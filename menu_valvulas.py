#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
menu_valvulas.py — Menú interactivo para controlar los dispositivos Zigbee
(válvulas SONOFF SWV-NH y el interruptor ON/OFF) reutilizando la lógica MQTT
de valvulas.py (mismo directorio).

Ejecución:
    python3 menu_valvulas.py

Cada acción espera confirmación REAL del dispositivo (gracias a optimistic: false
en la configuración de Zigbee2MQTT), no asume éxito.
"""

import sys
import time

from valvulas import (
    ValvulasMqtt,
    VALVULAS,
    INTERRUPTORES,
    Z2M_PREFIX,
    _report_metrics,
)

CONFIRM_TIMEOUT = 20   # segundos para esperar confirmación real del dispositivo
READ_TIMEOUT = 15      # segundos para esperar respuesta a una lectura (get)


def _state_of(mq, topic_name):
    """Estado cacheado ('ON'/'OFF'/'?') sin forzar lectura."""
    topic = f"{Z2M_PREFIX}/{topic_name}"
    cached = mq.received.get(topic)
    if isinstance(cached, dict) and "state" in cached:
        return cached["state"]
    return "?"


def refresh_state(mq, topic_name):
    """Fuerza una lectura (get) y devuelve el estado real ('ON'/'OFF'/'?')."""
    topic = f"{Z2M_PREFIX}/{topic_name}"
    # Reintenta una vez: la SWV-NH es un end device a batería que puede estar
    # en sueño profundo y no responder a la primera lectura.
    for _ in range(2):
        mq.received.pop(topic, None)
        try:
            mq.publish_json(f"{topic}/get", {"state": ""})
        except ConnectionError:
            return "?"
        payload = mq.wait_for(topic, lambda v: v is not None, timeout=READ_TIMEOUT)
        if isinstance(payload, dict) and "state" in payload:
            return payload["state"]
    return "?"


def send_valve_command(mq, alias, new_state):
    """Envía ON/OFF a una válvula y espera confirmación real. Devuelve (ok, mensaje, payload)."""
    topic = f"{Z2M_PREFIX}/{VALVULAS[alias]}"
    mq.received.pop(topic, None)  # descartar estado previo cacheado

    # Despertar la válvula (end device a batería) con un "get" antes del comando,
    # para que el "set" no caduque por timeout mientras duerme.
    try:
        mq.publish_json(f"{topic}/get", {"state": ""})
        mq.wait_for(topic, lambda v: v is not None, timeout=READ_TIMEOUT)
    except ConnectionError:
        pass
    mq.received.pop(topic, None)  # descartar el estado recién leído

    try:
        mq.publish_json(f"{topic}/set", {"state": new_state})
    except ConnectionError as e:
        return False, str(e), None

    confirmed = mq.wait_for(
        topic,
        lambda v: isinstance(v, dict) and v.get("state") == new_state,
        timeout=CONFIRM_TIMEOUT,
    )
    if confirmed is None:
        return False, (f"Sin confirmación real de '{alias}' en {CONFIRM_TIMEOUT}s "
                       "(¿reinicio reciente? las válvulas tardan 2-5 min en despertar)"), None
    return True, f"'{alias}' quedó en {new_state} (confirmado por la válvula)", confirmed


def send_switch_command(mq, alias, new_state):
    """Envía ON/OFF a un interruptor y espera confirmación real."""
    topic = f"{Z2M_PREFIX}/{INTERRUPTORES[alias]}"
    mq.received.pop(topic, None)

    try:
        mq.publish_json(f"{topic}/set", {"state": new_state})
    except ConnectionError as e:
        return False, str(e), None

    confirmed = mq.wait_for(
        topic,
        lambda v: isinstance(v, dict) and v.get("state") == new_state,
        timeout=CONFIRM_TIMEOUT,
    )
    if confirmed is None:
        return False, f"Sin confirmación real de '{alias}' en {CONFIRM_TIMEOUT}s", None
    return True, f"'{alias}' quedó en {new_state} (confirmado por el dispositivo)", confirmed


def main():
    mq = ValvulasMqtt()
    try:
        mq.connect()
    except ConnectionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    valve_aliases = list(VALVULAS.keys())
    switch_aliases = list(INTERRUPTORES.keys())

    for topic_name in list(VALVULAS.values()) + list(INTERRUPTORES.values()):
        mq.subscribe(f"{Z2M_PREFIX}/{topic_name}")

    # Cargar estado inicial de todos los dispositivos
    print("Cargando estado inicial...")
    for alias in valve_aliases:
        refresh_state(mq, VALVULAS[alias])
    for alias in switch_aliases:
        refresh_state(mq, INTERRUPTORES[alias])

    # Avisar si alguna válvula sigue dormida (típico tras un reinicio reciente)
    if any(_state_of(mq, VALVULAS[a]) == "?" for a in valve_aliases):
        print()
        print("   NOTA: hay válvulas sin responder. Tras un reinicio de la Raspberry")
        print("   tardan 2-5 min en despertar (son end devices a batería).")
        print("   Espera un poco y pulsa 'R' para reintentar.")
        print()

    n_valve_opts = 2 * len(valve_aliases)
    n_total_opts = n_valve_opts + 2 * len(switch_aliases)

    try:
        while True:
            print()
            print("=" * 46)
            print("      CONTROL DE DISPOSITIVOS ZIGBEE")
            print("=" * 46)

            if valve_aliases:
                print("   Válvulas:")
                for alias in valve_aliases:
                    print(f"     {alias:<20} : {_state_of(mq, VALVULAS[alias])}")
                print("     (ON = abierta, OFF = cerrada)")

            if switch_aliases:
                print()
                print("   Interruptores:")
                for alias in switch_aliases:
                    print(f"     {alias:<20} : {_state_of(mq, INTERRUPTORES[alias])}")
                print("     (ON = encendido, OFF = apagado)")

            print("-" * 46)
            i = 1
            for alias in valve_aliases:
                print(f"   [{i}]   Abrir  {alias}")
                print(f"   [{i + 1}]   Cerrar {alias}")
                i += 2
            for alias in switch_aliases:
                print(f"   [{i}]   Encender {alias}")
                print(f"   [{i + 1}]   Apagar  {alias}")
                i += 2
            print("   [R]   Actualizar estado")
            print("   [Q]   Salir")
            print("-" * 46)

            choice = input("   Elige una opción: ").strip().lower()

            if choice in ("q", "0"):
                print("Saliendo...")
                break

            if choice == "r":
                print("Leyendo estado actual...")
                for alias in valve_aliases:
                    refresh_state(mq, VALVULAS[alias])
                for alias in switch_aliases:
                    refresh_state(mq, INTERRUPTORES[alias])
                continue

            try:
                idx = int(choice)
            except ValueError:
                print("   Opción no válida.")
                continue

            if 1 <= idx <= n_total_opts:
                if idx <= n_valve_opts:
                    # Válvula: abrir/cerrar
                    valve_idx = (idx - 1) // 2
                    is_open = (idx - 1) % 2 == 0
                    alias = valve_aliases[valve_idx]
                    new_state = "ON" if is_open else "OFF"
                    action = "Abriendo" if is_open else "Cerrando"
                    print(f"\n   {action} '{alias}'...")
                    ok, msg, payload = send_valve_command(mq, alias, new_state)
                    if ok:
                        print(f"   OK: {msg}")
                        if new_state == "ON":
                            print("   Abierta: empieza a medir caudal y volumen.")
                        else:
                            print("   Cerrada: sesión de riego finalizada.")
                        _report_metrics(payload)
                    else:
                        print(f"   ERROR: {msg}", file=sys.stderr)
                else:
                    # Interruptor: encender/apagar
                    sw_idx = (idx - n_valve_opts - 1) // 2
                    is_on = (idx - n_valve_opts - 1) % 2 == 0
                    alias = switch_aliases[sw_idx]
                    new_state = "ON" if is_on else "OFF"
                    action = "Encendiendo" if is_on else "Apagando"
                    print(f"\n   {action} '{alias}'...")
                    ok, msg, payload = send_switch_command(mq, alias, new_state)
                    if ok:
                        print(f"   OK: {msg}")
                    else:
                        print(f"   ERROR: {msg}", file=sys.stderr)
                time.sleep(1.0)
            else:
                print("   Opción no válida.")

    except KeyboardInterrupt:
        print("\n   Interrumpido. Saliendo...")

    mq.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
