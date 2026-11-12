#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
valvulas.py — Control local/offline de válvulas de agua Zigbee SONOFF SWV-NH.

Arquitectura:
  [Válvulas SWV-NH] --(Zigbee)--> [Dongle Sonoff /dev/ttyUSB0]
    --(USB)--> [Zigbee2MQTT en Docker] --(MQTT localhost:1883)--> [este script]

Este script NO habla Zigbee directamente: usa MQTT (paho-mqtt) para hablar
con Zigbee2MQTT, que es quien maneja el dongle.

Requiere:
  - Python 3.8+
  - paho-mqtt (pip install paho-mqtt)
  - Zigbee2MQTT levantado (docker compose up -d) y broker Mosquitto local.

Comandos:
  python3 valvulas.py list
  python3 valvulas.py status
  python3 valvulas.py open NOMBRE
  python3 valvulas.py close NOMBRE
  python3 valvulas.py on NOMBRE
  python3 valvulas.py off NOMBRE
  python3 valvulas.py read NOMBRE
  python3 valvulas.py monitor
  python3 valvulas.py pair [SEGUNDOS]
"""

import argparse
import json
import sys
import time

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# CONFIGURACIÓN (editar aquí si cambia el entorno)
# ---------------------------------------------------------------------------

MQTT_HOST = "127.0.0.1"          # Broker Mosquitto local
MQTT_PORT = 1883                 # Puerto estándar MQTT
MQTT_USER = None                 # Sin credenciales (allow_anonymous true)
MQTT_PASS = None

Z2M_PREFIX = "zigbee2mqtt"       # base_topic de Zigbee2MQTT

# Alias -> nombre MQTT de la válvula (tal como queda tras el emparejamiento).
# Si el usuario renombra los dispositivos en Zigbee2MQTT, ajustar aquí.
VALVULAS = {
    "valvula_riego": "valvula_riego",
    "valvula_principal": "valvula_principal",
}

# Alias -> nombre MQTT de los interruptores ON/OFF alimentados a red (p. ej. SONOFF
# ZBMINIL2). A diferencia de las válvulas, no exponen caudal/volumen/batería y no
# duermen (alimentación de red), por lo que no necesitan el "despertar" previo.
INTERRUPTORES = {
    "interruptor": "interruptor",
}

TIMEOUT = 15                     # segundos a esperar confirmación real de la válvula
PAIR_DEFAULT = 60                # segundos de permit_join por defecto en `pair`

# Campos de estado reales expuestos por la SONOFF SWV-NH (Zigbee2MQTT 2.x).
# state usa ON/OFF (ON=válvula abierta, OFF=válvula cerrada).
STATE_FIELDS = [
    ("state", "Estado"),
    ("flow", "Caudal (m³/h)"),
    ("real_time_irrigation_volume", "Vol. riego (L)"),
    ("real_time_irrigation_duration", "Dur. riego (s)"),
    ("daily_irrigation_volume", "Vol. diario (L)"),
    ("valve_work_state", "Modo válvula"),
    ("battery", "Batería (%)"),
    ("linkquality", "Señal"),
]

# La SWV-NH comanda/conforma con ON (abrir) y OFF (cerrar).
STATE_OPTIONS = {"ON", "OFF"}


# ---------------------------------------------------------------------------
# Cliente MQTT con helpers de publicación/suscripción y espera de mensajes
# ---------------------------------------------------------------------------

class ValvulasMqtt:
    """Wrapper sobre paho-mqtt para las operaciones de la CLI."""

    def __init__(self, timeout=TIMEOUT):
        self.timeout = timeout
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="valvulas-cli")
        self.received = {}          # topic -> último payload JSON (dict o crudo)
        self.connected = False

        if MQTT_USER is not None:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    # -- callbacks ----------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = True

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = msg.payload.decode("utf-8", errors="replace")
        self.received[msg.topic] = payload

    # -- helpers ------------------------------------------------------------
    def connect(self, timeout=None):
        try:
            self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        except (OSError, TimeoutError) as e:
            raise ConnectionError(
                f"No se pudo conectar al broker MQTT {MQTT_HOST}:{MQTT_PORT}: {e}. "
                "¿Está Mosquitto activo? (systemctl status mosquitto)"
            ) from e
        self.client.loop_start()
        deadline = time.time() + (timeout or 10)
        while not self.connected and time.time() < deadline:
            time.sleep(0.05)
        if not self.connected:
            self.client.loop_stop()
            raise ConnectionError(
                f"No se pudo conectar al broker MQTT {MQTT_HOST}:{MQTT_PORT}. "
                "¿Está Mosquitto activo? (systemctl status mosquitto)"
            )

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def publish_json(self, topic, payload):
        """Publica un dict como JSON retenido=False (comando efímero)."""
        raw = json.dumps(payload).encode("utf-8")
        info = self.client.publish(topic, raw, qos=1)
        info.wait_for_publish(timeout=self.timeout)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"Fallo al publicar en {topic} (rc={info.rc})")

    def subscribe(self, topic):
        self.client.subscribe(topic, qos=1)

    def wait_for(self, topic, predicate, timeout=None):
        """Espera hasta que el topic tenga un payload que cumpla predicate."""
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            if topic in self.received and predicate(self.received[topic]):
                return self.received[topic]
            time.sleep(0.1)
        return None


# ---------------------------------------------------------------------------
# Formateo y helpers de presentación
# ---------------------------------------------------------------------------

def _fmt_value(value):
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{value}"
    return str(value)


def _print_table(rows, headers):
    """Imprime una tabla simple alineada por columnas."""
    rows = [[_fmt_value(c) for c in row] for row in rows]
    widths = [max(len(r[i]) for r in rows + [headers]) for i in range(len(headers))]
    fmt = "  ".join(f"{{{i}:<{widths[i]}}}" for i in range(len(headers)))
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*row))


def _state_equals(target):
    """Predicado: True cuando el payload dict reporta el estado deseado."""
    def check(value):
        return isinstance(value, dict) and value.get("state") == target
    return check


def _fmt(value, unit="", decimals=None):
    """Formatea un número con unidad; devuelve '-' si no hay dato."""
    if value is None or value == "":
        return "-"
    try:
        f = float(value)
        if decimals is None:
            text = f"{int(f)}" if f == int(f) else f"{f:.2f}"
        else:
            text = f"{f:.{decimals}f}"
        return f"{text}{unit}"
    except (TypeError, ValueError):
        return f"{value}{unit}"


def _report_metrics(payload, title="Medición"):
    """Imprime caudal, volumen y batería a partir de un payload de la SWV-NH."""
    if not isinstance(payload, dict):
        return
    battery = payload.get("battery")
    flow = payload.get("flow")
    session_vol = payload.get("real_time_irrigation_volume")
    duration = payload.get("real_time_irrigation_duration")
    daily_vol = payload.get("daily_irrigation_volume")

    print(f"   {title}:")
    print(f"     Batería      : {_fmt(battery, ' %')}")
    if flow is not None:
        try:
            lpm = float(flow) * 1000 / 60
            print(f"     Caudal       : {_fmt(flow, ' m³/h', 3)}  ({_fmt(lpm, ' L/min', 1)})")
        except (TypeError, ValueError):
            print(f"     Caudal       : {flow} m³/h")
    else:
        print("     Caudal       : - (sin agua circulando)")
    print(f"     Vol. sesión  : {_fmt(session_vol, ' L')}")
    print(f"     Duración     : {_fmt(duration, ' s')}")
    print(f"     Vol. de hoy  : {_fmt(daily_vol, ' L')}")


# ---------------------------------------------------------------------------
# Comandos CLI
# ---------------------------------------------------------------------------

def cmd_list():
    """Lista los dispositivos descubiertos por Zigbee2MQTT."""
    mq = ValvulasMqtt()
    try:
        mq.connect()
    except ConnectionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # La lista de dispositivos se publica retenida en bridge/devices
    mq.subscribe(f"{Z2M_PREFIX}/bridge/devices")
    devices = mq.wait_for(f"{Z2M_PREFIX}/bridge/devices", lambda v: v is not None, timeout=8)

    if devices is None:
        print("ERROR: Zigbee2MQTT no respondió al consultar la lista de dispositivos.", file=sys.stderr)
        print("Asegúrate de que el contenedor esté corriendo (docker compose up -d) y de que", file=sys.stderr)
        print("el bridge haya publicado su configuración (reinicia el contenedor si es necesario).", file=sys.stderr)
        mq.disconnect()
        return 1

    if devices == []:
        print("Sin dispositivos emparejados todavía. Ejecuta: python3 valvulas.py pair 60")
        mq.disconnect()
        return 0

    rows = []
    for d in devices:
        name = d.get("friendly_name", "?")
        rows.append((
            name,
            d.get("ieee_address", "-"),
            d.get("type", "-"),
            d.get("definition", {}).get("model", "-"),
            d.get("supported", False),
        ))

    if not rows:
        print("Sin dispositivos emparejados todavía. Ejecuta: python3 valvulas.py pair 60")
        mq.disconnect()
        return 0

    print(f"Dispositivos descubiertos por Zigbee2MQTT ({len(rows)}):")
    _print_table(
        rows,
        ["Nombre", "IEEE", "Tipo", "Modelo", "Soportado"],
    )

    our = [r for r in rows if r[0] in VALVULAS.values()]
    if not our:
        print("\nAVISO: No se encontraron tus válvulas por su nombre configurado.")
        print("Empareja cada válvula (botón físico) y renómbrala en Zigbee2MQTT a:")
        print("  " + ", ".join(VALVULAS.values()))

    mq.disconnect()
    return 0


def cmd_status():
    """Lee el estado actual de cada válvula, forzando una lectura (get)."""
    mq = ValvulasMqtt()
    try:
        mq.connect()
    except ConnectionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    rows = []
    any_missing = False
    for alias, topic_name in VALVULAS.items():
        topic = f"{Z2M_PREFIX}/{topic_name}"
        mq.subscribe(topic)
        mq.received.pop(topic, None)
        # La SWV-NH es un end device a batería que duerme: solo publica al cambiar
        # su estado. Enviamos un "get" para forzar que responda con su estado actual.
        try:
            mq.publish_json(f"{topic}/get", {"state": ""})
        except ConnectionError:
            pass
        payload = mq.wait_for(topic, lambda v: v is not None, timeout=20)
        if payload is None:
            rows.append((alias, "NO DISPONIBLE") + ("-",) * (len(STATE_FIELDS) - 1))
            any_missing = True
        elif isinstance(payload, dict):
            rows.append((alias,) + tuple(payload.get(key, "-") for key, _ in STATE_FIELDS))
        else:
            rows.append((alias, str(payload)) + ("-",) * (len(STATE_FIELDS) - 1))

    headers = ["Válvula"] + [label for _, label in STATE_FIELDS]
    _print_table(rows, headers)

    if any_missing:
        print("\nAVISO: Hay válvulas que no responden. Comprueba que estén emparejadas,")
        print("dentro del rango y que Zigbee2MQTT esté corriendo.")
        mq.disconnect()
        return 1
    mq.disconnect()
    return 0


def cmd_set(alias, new_state, timeout=TIMEOUT):
    """Abre/cierra una válvula y espera confirmación real publicada por el dispositivo."""
    if alias not in VALVULAS:
        print(f"ERROR: válvula desconocida '{alias}'. Válvulas configuradas: {', '.join(VALVULAS)}",
              file=sys.stderr)
        return 1

    if new_state not in STATE_OPTIONS:
        print(f"ERROR: estado inválido '{new_state}'. Usa ON (abrir) u OFF (cerrar).", file=sys.stderr)
        return 1

    topic_name = VALVULAS[alias]
    state_topic = f"{Z2M_PREFIX}/{topic_name}"
    set_topic = f"{state_topic}/set"

    mq = ValvulasMqtt(timeout=timeout)
    try:
        mq.connect()
    except ConnectionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    mq.subscribe(state_topic)
    # Descartar valores previos retenidos para no confundir confirmaciones antiguas
    mq.received.pop(state_topic, None)

    # La SWV-NH es un end device a batería que entra en sueño profundo tras un rato
    # de inactividad. Un "set" directo mientras duerme caduca (timeout ZCL 10 s).
    # La despertamos primero con un "get" (lectura) y esperamos su respuesta real.
    wake_timeout = min(20, max(10, timeout))
    try:
        mq.publish_json(f"{state_topic}/get", {"state": ""})
        woke = mq.wait_for(state_topic, lambda v: v is not None, timeout=wake_timeout)
        if woke is None:
            print(f"Aviso: '{alias}' no respondió al despertado; se intenta el comando igualmente.")
    except ConnectionError:
        pass

    # Descartar el estado recién leído para evaluar solo la confirmación del comando
    mq.received.pop(state_topic, None)

    try:
        mq.publish_json(set_topic, {"state": new_state})
    except ConnectionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        mq.disconnect()
        return 1

    print(f"Comando {new_state} enviado a '{alias}' ({topic_name}). Esperando confirmación real...")
    confirmed = mq.wait_for(state_topic, _state_equals(new_state), timeout=mq.timeout)

    if confirmed is None:
        print(f"ERROR: timeout de {mq.timeout}s esperando el estado real de '{alias}'.", file=sys.stderr)
        print("Comprueba que la válvula esté encendida, dentro del rango y que el comando llegó", file=sys.stderr)
        print("(logs de Zigbee2MQTT: docker compose logs -f).", file=sys.stderr)
        print("Si acabas de reiniciar la Raspberry, espera 2-5 min a que las válvulas", file=sys.stderr)
        print("(end devices a batería) hagan check-in y vuelve a intentar.", file=sys.stderr)
        mq.disconnect()
        return 1

    actual = confirmed.get("state") if isinstance(confirmed, dict) else confirmed
    if actual != new_state:
        print(f"ERROR: la válvula respondió con estado '{actual}' en vez de '{new_state}'.",
              file=sys.stderr)
        mq.disconnect()
        return 1

    verb = "abierta" if new_state == "ON" else "cerrada"
    print(f"OK: '{alias}' quedó {verb} (confirmado por el dispositivo).")
    if new_state == "ON":
        print("   Abierta: empieza a medir caudal y volumen.")
    else:
        print("   Cerrada: sesión de riego finalizada.")
    _report_metrics(confirmed)
    mq.disconnect()
    return 0


def cmd_switch(alias, new_state, timeout=TIMEOUT):
    """Enciende/apaga un interruptor ON/OFF y espera confirmación real."""
    if alias not in INTERRUPTORES:
        print(f"ERROR: interruptor desconocido '{alias}'. Interruptores configurados: {', '.join(INTERRUPTORES)}",
              file=sys.stderr)
        return 1

    if new_state not in STATE_OPTIONS:
        print(f"ERROR: estado inválido '{new_state}'. Usa ON (encender) u OFF (apagar).", file=sys.stderr)
        return 1

    topic_name = INTERRUPTORES[alias]
    state_topic = f"{Z2M_PREFIX}/{topic_name}"
    set_topic = f"{state_topic}/set"

    mq = ValvulasMqtt(timeout=timeout)
    try:
        mq.connect()
    except ConnectionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    mq.subscribe(state_topic)
    mq.received.pop(state_topic, None)

    try:
        mq.publish_json(set_topic, {"state": new_state})
    except ConnectionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        mq.disconnect()
        return 1

    print(f"Comando {new_state} enviado a '{alias}' ({topic_name}). Esperando confirmación real...")
    confirmed = mq.wait_for(state_topic, _state_equals(new_state), timeout=mq.timeout)

    if confirmed is None:
        print(f"ERROR: timeout de {mq.timeout}s esperando el estado real de '{alias}'.", file=sys.stderr)
        mq.disconnect()
        return 1

    verb = "encendido" if new_state == "ON" else "apagado"
    print(f"OK: '{alias}' quedó {verb} (confirmado por el dispositivo).")
    mq.disconnect()
    return 0


def cmd_read(alias):
    """Lee en vivo caudal, volumen y batería de una válvula (sin cambiar su estado)."""
    if alias not in VALVULAS:
        print(f"ERROR: válvula desconocida '{alias}'. Válvulas configuradas: {', '.join(VALVULAS)}",
              file=sys.stderr)
        return 1

    topic = f"{Z2M_PREFIX}/{VALVULAS[alias]}"
    mq = ValvulasMqtt()
    try:
        mq.connect()
    except ConnectionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    mq.subscribe(topic)
    mq.received.pop(topic, None)
    # Forzar lectura: la SWV-NH es un end device a batería que duerme.
    try:
        mq.publish_json(f"{topic}/get", {"state": ""})
    except ConnectionError:
        pass
    payload = mq.wait_for(topic, lambda v: v is not None, timeout=20)
    mq.disconnect()

    if payload is None:
        print(f"ERROR: '{alias}' no respondió a la lectura.", file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        print(f"'{alias}': {payload}")
        return 0

    state = payload.get("state", "-")
    print(f"\n  Válvula: {alias}")
    print(f"  Estado : {state}")
    _report_metrics(payload, title="Lectura en vivo")
    return 0


def cmd_monitor():
    """Monitorea en vivo todos los topics de Zigbee2MQTT."""
    mq = ValvulasMqtt()
    try:
        mq.connect()
    except ConnectionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Reemplazamos el callback para imprimir todo en vivo
    def on_live(client, userdata, msg):
        ts = time.strftime("%H:%M:%S")
        payload = msg.payload.decode("utf-8", errors="replace")
        print(f"[{ts}] {msg.topic}: {payload}")

    mq.client.on_message = on_live
    mq.subscribe(f"{Z2M_PREFIX}/#")
    print(f"Monitoreando {Z2M_PREFIX}/# ... Ctrl+C para salir.")
    print("-" * 60)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nMonitor finalizado.")
        mq.disconnect()
        return 0


def cmd_pair(seconds):
    """Habilita permit_join durante `seconds` segundos para emparejar válvulas."""
    mq = ValvulasMqtt()
    try:
        mq.connect()
    except ConnectionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    request_topic = f"{Z2M_PREFIX}/bridge/request/permit_join"
    try:
        # Zigbee2MQTT 2.x exige el campo "time" (segundos) en el payload.
        mq.publish_json(request_topic, {"value": True, "time": seconds})
    except ConnectionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        mq.disconnect()
        return 1

    print(f"permit_join habilitado durante {seconds} s.")
    print("Ahora mantén pulsado el botón de la válvula SONOFF SWV-NH hasta que parpadee.")
    time.sleep(seconds)

    try:
        mq.publish_json(request_topic, {"value": False, "time": 0})
    except ConnectionError as e:
        print(f"ERROR al desactivar permit_join: {e}", file=sys.stderr)
        mq.disconnect()
        return 1

    print("permit_join deshabilitado. Revisa 'python3 valvulas.py list' para ver el dispositivo.")
    print("Renombra el dispositivo a valvula_riego o valvula_principal en Zigbee2MQTT.")
    mq.disconnect()
    return 0


# ---------------------------------------------------------------------------
# main / argparse
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="valvulas.py",
        description="Control local/offline de válvulas Zigbee SONOFF SWV-NH vía MQTT/Zigbee2MQTT.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Lista los dispositivos Zigbee descubiertos")
    sub.add_parser("status", help="Muestra estado, flujo/volumen y batería de cada válvula")

    p_open = sub.add_parser("open", help="Abre una válvula (espera confirmación real)")
    p_open.add_argument("nombre", help="Alias de la válvula (valvula_riego, valvula_principal)")
    p_open.add_argument("--timeout", type=int, default=TIMEOUT, help="Timeout de confirmación (s)")

    p_close = sub.add_parser("close", help="Cierra una válvula (espera confirmación real)")
    p_close.add_argument("nombre", help="Alias de la válvula (valvula_riego, valvula_principal)")
    p_close.add_argument("--timeout", type=int, default=TIMEOUT, help="Timeout de confirmación (s)")

    p_on = sub.add_parser("on", help="Enciende un interruptor (espera confirmación real)")
    p_on.add_argument("nombre", help="Alias del interruptor (interruptor)")
    p_on.add_argument("--timeout", type=int, default=TIMEOUT, help="Timeout de confirmación (s)")

    p_off = sub.add_parser("off", help="Apaga un interruptor (espera confirmación real)")
    p_off.add_argument("nombre", help="Alias del interruptor (interruptor)")
    p_off.add_argument("--timeout", type=int, default=TIMEOUT, help="Timeout de confirmación (s)")

    sub.add_parser("monitor", help="Monitorea en vivo todo lo que publican los dispositivos")

    p_read = sub.add_parser("read", help="Lee caudal, volumen y batería de una válvula")
    p_read.add_argument("nombre", help="Alias de la válvula (valvula_riego, valvula_principal)")

    p_pair = sub.add_parser("pair", help="Habilita permit_join para emparejar dispositivos")
    p_pair.add_argument("segundos", nargs="?", type=int, default=PAIR_DEFAULT,
                        help="Duración de permit_join en segundos")

    args = parser.parse_args(argv)

    if args.command == "list":
        return cmd_list()
    if args.command == "status":
        return cmd_status()
    if args.command == "open":
        return cmd_set(args.nombre, "ON", args.timeout)
    if args.command == "close":
        return cmd_set(args.nombre, "OFF", args.timeout)
    if args.command == "on":
        return cmd_switch(args.nombre, "ON", args.timeout)
    if args.command == "off":
        return cmd_switch(args.nombre, "OFF", args.timeout)
    if args.command == "monitor":
        return cmd_monitor()
    if args.command == "read":
        return cmd_read(args.nombre)
    if args.command == "pair":
        return cmd_pair(args.segundos)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
