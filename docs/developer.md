# Developer — Referencia técnica

Documentación del código, del contrato MQTT y de cómo extenderlo sin romper nada.
Para operar el sistema, ver [datasheet.md](datasheet.md); para el recorrido de
decisiones, [BITACORA.md](BITACORA.md).

| Dato | Valor |
|---|---|
| Lenguaje | Python 3.8+ (probado en 3.12) |
| Dependencia | `paho-mqtt >= 2.0` (probado 2.1.0) |
| Transporte | MQTT 3.1.1, QoS 1, sin TLS, loopback |
| Interlocutor | Zigbee2MQTT 2.13 (`zigbee2mqtt` como `base_topic`) |
| LOC | ~620 (`valvulas.py`) + ~236 (`menu_valvulas.py`) |

---

## 1. Mapa del proyecto

```
valvulas.py          CLI + capa MQTT + formateo. Es la única pieza que habla MQTT.
menu_valvulas.py     UI de terminal. Importa de valvulas.py; no abre conexiones propias.
docker-compose.yml   Zigbee2MQTT (host network, device USB, volumen ./config).
config/              Configuración y estado de Zigbee2MQTT (el estado no se versiona).
```

Dependencia entre módulos:

```
menu_valvulas.py ──importa──▶ valvulas.py
                                 ├── ValvulasMqtt
                                 ├── VALVULAS, INTERRUPTORES, Z2M_PREFIX
                                 └── _report_metrics
```

No hay paquetes, ni `__init__.py`, ni instalación: son dos archivos ejecutables en el
mismo directorio.

---

## 2. Arquitectura interna de `valvulas.py`

```
┌──────────────────────────────────────────────────────────┐
│ main(argv)                          argparse + despacho   │
├──────────────────────────────────────────────────────────┤
│ cmd_list · cmd_status · cmd_set · cmd_switch · cmd_read  │
│ cmd_monitor · cmd_pair              lógica de cada verbo │
├──────────────────────────────────────────────────────────┤
│ ValvulasMqtt      connect / publish_json / subscribe /   │
│                   wait_for / disconnect                   │
├──────────────────────────────────────────────────────────┤
│ paho.mqtt.client  (CallbackAPIVersion.VERSION2)          │
└──────────────────────────────────────────────────────────┘
```

Principio de diseño: **toda la complejidad está en una clase**. Los comandos son
funciones planas que orquestan llamadas a `ValvulasMqtt`; no hay estado global salvo
las constantes de configuración.

### 2.1 Constantes

| Constante | Valor | Uso |
|---|---|---|
| `MQTT_HOST` | `"127.0.0.1"` | Broker |
| `MQTT_PORT` | `1883` | Puerto |
| `MQTT_USER` / `MQTT_PASS` | `None` | Credenciales opcionales |
| `Z2M_PREFIX` | `"zigbee2mqtt"` | `base_topic` de Z2M |
| `VALVULAS` | `{alias: friendly_name}` | Válvulas controlables |
| `INTERRUPTORES` | `{alias: friendly_name}` | Interruptores ON/OFF |
| `TIMEOUT` | `15` | Espera de confirmación (s) |
| `PAIR_DEFAULT` | `60` | `permit_join` por defecto (s) |
| `STATE_FIELDS` | lista de tuplas | Campos y etiquetas de `status` |
| `STATE_OPTIONS` | `{"ON", "OFF"}` | Estados válidos |

```python
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
```

Cambiar `STATE_FIELDS` cambia a la vez las cabeceras y las filas de `status`: es la
única fuente de verdad de esa tabla.

---

## 3. API de `ValvulasMqtt`

```python
class ValvulasMqtt:
    def __init__(self, timeout=TIMEOUT)
    def connect(self, timeout=None) -> None            # lanza ConnectionError
    def disconnect(self) -> None
    def publish_json(self, topic: str, payload: dict) -> None
    def subscribe(self, topic: str) -> None
    def wait_for(self, topic: str, predicate, timeout=None)
```

| Miembro | Contrato |
|---|---|
| `received` | `dict[topic] = último payload` (dict si era JSON, `str` si no) |
| `connected` | `True` desde `on_connect` hasta que se pare el loop |
| `connect()` | Crea el socket, arranca `loop_start()` y espera el callback hasta 10 s. Si no conecta: `ConnectionError` con mensaje accionable y `loop_stop()` |
| `publish_json()` | Serializa a JSON UTF-8, publica **QoS 1**, `wait_for_publish(timeout)`. `rc != 0` → `ConnectionError` |
| `wait_for()` | Sondeo cada 100 ms hasta que `predicate(received[topic])` sea verdadero o expire el timeout. Devuelve el payload o `None` |

Notas de implementación:

- `mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="valvulas-cli")`: la API
  v2 es obligatoria en paho 2.x.
- `_on_message` intenta `json.loads`; si el payload no es JSON guarda el texto. Así
  `monitor` y los diagnósticos no revientan con mensajes planos.
- `publish_json` **no** marca el mensaje como retenido: un comando caducado no debe
  reejecutarse cuando un cliente se reconecte.
- `wait_for` no consume ni borra el mensaje: quien necesite "solo lo nuevo" debe hacer
  `mq.received.pop(topic, None)` antes de la acción. Todos los comandos lo hacen.

### 3.1 Helpers de presentación

| Función | Firma | Salida |
|---|---|---|
| `_fmt` | `(value, unit="", decimals=None)` | `-` si no hay dato; entero sin decimales si aplica |
| `_fmt_value` | `(value)` | Celda de tabla como texto |
| `_print_table` | `(rows, headers)` | Tabla alineada calculando anchos |
| `_state_equals` | `(target)` | Predicado `payload["state"] == target` |
| `_report_metrics` | `(payload, title="Medición")` | Batería, caudal (m³/h y L/min), volumen, duración y diario |

`_report_metrics` convierte con `lpm = m³/h × 1000 / 60` y tolera `flow` ausente
(sin agua circulando) mostrando `- (sin agua circulando)`.

---

## 4. Contrato MQTT

### 4.1 Topics

| Topic | Dirección | Retenido | QoS | Payload |
|---|---|---|---|---|
| `zigbee2mqtt/bridge/devices` | Z2M → script | Sí | 1 | Array de dispositivos |
| `zigbee2mqtt/bridge/request/permit_join` | script → Z2M | No | 1 | `{"value": true, "time": 60}` |
| `zigbee2mqtt/<nombre>` | Z2M → script | No | 1 | Estado del dispositivo |
| `zigbee2mqtt/<nombre>/set` | script → Z2M | No | 1 | `{"state": "ON"}` |
| `zigbee2mqtt/<nombre>/get` | script → Z2M | No | 1 | `{"state": ""}` |
| `zigbee2mqtt/#` | Z2M → script | — | 1 | Todo (monitor) |

`<nombre>` es el `friendly_name`, no el IEEE.

### 4.2 Payload de estado (válvula)

```json
{
  "state": "ON",
  "flow": 0.45,
  "real_time_irrigation_volume": 12,
  "real_time_irrigation_duration": 95,
  "daily_irrigation_volume": 12,
  "valve_work_state": "working",
  "current_device_status": "normal_state",
  "battery": 98,
  "linkquality": 255
}
```

`flow` **no aparece** cuando no hay circulación. El código lo trata como opcional;
cualquier consumidor debe hacer lo mismo.

### 4.3 Payload de `bridge/devices`

Cada elemento relevante:

```json
{
  "friendly_name": "valvula_riego",
  "ieee_address": "0x0000000000000001",
  "type": "EndDevice",
  "supported": true,
  "definition": {"model": "SWV", "vendor": "SONOFF"}
}
```

`definition` puede faltar en dispositivos no soportados; el código usa
`d.get("definition", {}).get("model", "-")`.

### 4.4 `permit_join` en Zigbee2MQTT 2.x

El campo `time` (segundos) es **obligatorio** en la práctica. Enviar solo
`{"value": true}` deja la ventana abierta indefinidamente o falla según versión:

```json
{"value": true,  "time": 60}
{"value": false, "time": 0}
```

### 4.5 Topics que el proyecto **no** usa

| Topic | Por qué no |
|---|---|
| `.../bridge/config` | Contiene la `network_key`: no se publica ni se lee desde la CLI |
| `.../bridge/info` | Útil para diagnóstico manual, no necesario |
| `.../availability` | Se resuelve con `linkquality` y el estado real |
| `homeassistant/*` | Integración desactivada por diseño |

---

## 5. Flujos de ejecución

### 5.1 `open` / `close` (`cmd_set`)

```
validar alias y estado
crear ValvulasMqtt(timeout)
connect()                          → ConnectionError si falla
subscribe(<estado>)
received.pop(<estado>)             # descartar caché
publish_json(<estado>/get, {"state": ""})
wait_for(<estado>, any, wake_timeout = min(20, max(10, timeout)))
received.pop(<estado>)             # descartar la lectura de despertar
publish_json(<estado>/set, {"state": new_state})
wait_for(<estado>, _state_equals(new_state), timeout)
  None            → error + exit 1
  estado distinto → error + exit 1
  correcto        → OK + _report_metrics + exit 0
```

Tiempos implicados:

| Fase | Espera máxima |
|---|---|
| Conexión al broker | 10 s |
| Despertar (`get`) | `min(20, max(10, timeout))` |
| Confirmación (`set`) | `timeout` (15 s por defecto) |
| `wait_for_publish` | `timeout` |

Peor caso teórico con `--timeout 15`: ~45 s. En la práctica, despierta en 1-3 s.

### 5.2 `on` / `off` (`cmd_switch`)

Idéntico pero **sin** `get` previo: un interruptor alimentado a red no duerme. Sin
`_report_metrics` (no expone caudal ni batería).

### 5.3 `status` (`cmd_status`)

Por cada válvula, en serie: `subscribe` → `pop` → `get` → `wait_for(20 s)`. Si no hay
respuesta, rellena la fila con `NO DISPONIBLE` y marca `any_missing` para devolver 1
al final. Nunca aborta en la primera válvula: informa de todas.

### 5.4 `read` (`cmd_read`)

`subscribe` → `pop` → `get` → `wait_for(20 s)` → imprime `state` y `_report_metrics`.
No requiere el alias en `VALVULAS`... salvo para resolver el topic: **sí lo requiere**.

### 5.5 `list` (`cmd_list`)

`subscribe(bridge/devices)` → `wait_for(8 s)`. Tres desenlaces:

| Resultado | Mensaje | Exit |
|---|---|---|
| `None` | "Zigbee2MQTT no respondió…" + 2 pistas | 1 |
| `[]` | "Sin dispositivos emparejados todavía…" | 0 |
| Lista | Tabla + aviso si faltan los alias configurados | 0 |

### 5.6 `pair` (`cmd_pair`)

`publish_json(permit_join, {"value": True, "time": N})` → `time.sleep(N)` →
`publish_json(..., {"value": False, "time": 0})`. El `sleep` bloquea: la ventana es
deliberadamente interactiva. `Ctrl+C` durante el `sleep` aborta **sin** cerrar la
ventana: es una limitación conocida (ver §10).

### 5.7 `monitor` (`cmd_monitor`)

Reemplaza `client.on_message` por un closure que imprime `HH:MM:SS topic payload`,
se suscribe a `zigbee2mqtt/#` y queda en un bucle con `sleep(0.5)` hasta `Ctrl+C`.

---

## 6. Códigos de salida

| Código | Cuándo |
|---|---|
| `0` | Operación confirmada, o consulta sin error |
| `1` | Error controlado: broker caído, timeout, alias inválido, dispositivo ausente |
| `2` | Error de `argparse`: subcomando o argumento faltante |
| `130` | `Ctrl+C` no capturado en la CLI (comportamiento del intérprete) |

`main()` devuelve el código de cada `cmd_*` y `sys.exit(main())` lo propaga. Esto hace
la CLI usable en `&&`, `cron` y `systemd` sin parsear texto.

---

## 7. `menu_valvulas.py`

| Elemento | Detalle |
|---|---|
| `CONFIRM_TIMEOUT` | 20 s para la confirmación del comando |
| `READ_TIMEOUT` | 15 s por intento de lectura |
| `_state_of(mq, topic)` | Estado cacheado o `"?"`, sin forzar lectura |
| `refresh_state(mq, topic)` | `get` + espera, **dos intentos**; devuelve `ON`/`OFF`/`"?"` |
| `send_valve_command(mq, alias, state)` | Despierta, comanda, confirma; devuelve `(ok, msg, payload)` |
| `send_switch_command(mq, alias, state)` | Idem sin despertar |
| `main()` | Conexión única, suscripción a todos los topics, bucle de menú |

Detalles de diseño:

- **Una sola conexión** para toda la sesión: el arranque es lo caro (handshake y
  suscripciones); después cada acción es inmediata.
- El menú se construye a partir de `len(VALVULAS)` y `len(INTERRUPTORES)`: las
  opciones de válvulas ocupan `1..2n` y las de interruptores `2n+1..`.
- Las acciones de válvula muestran `_report_metrics(payload)` con el payload
  confirmado: no hay una lectura extra.
- `KeyboardInterrupt` se captura en el bucle para desconectar limpiamente.

---

## 8. Cómo extender

### 8.1 Añadir una válvula

```python
VALVULAS = {
    "valvula_riego": "valvula_riego",
    "valvula_principal": "valvula_principal",
    "valvula_huerto": "valvula_huerto",       # nueva
}
```

Empareja, renombra a `valvula_huerto` en Z2M y listo. `status`, `open`, `close`,
`read` y el menú la incluyen automáticamente.

### 8.2 Añadir un interruptor

```python
INTERRUPTORES = {"interruptor": "interruptor", "bomba": "bomba_patio"}
```

Aparece en el menú como *Encender/Apagar bomba*.

### 8.3 Cambiar el broker o la base de topics

```python
MQTT_HOST = "192.0.2.10"     # ejemplo; no uses una IP real en documentación
MQTT_PORT = 1883
MQTT_USER = "valvulas"
MQTT_PASS = "..."            # no lo commitees: usa variables de entorno
Z2M_PREFIX = "zigbee2mqtt"
```

Si activas credenciales, `username_pw_set` ya está condicionado a `MQTT_USER is not
None`.

### 8.4 Añadir un comando nuevo

1. Escribe `cmd_algo()` siguiendo el patrón: crear `ValvulasMqtt`, `connect()` con
   `try/except ConnectionError`, operar, `disconnect()` y devolver `0`/`1`.
2. Añade el subparser en `main()` y su rama de despacho.
3. Documenta el exit code.

### 8.5 Riego por volumen objetivo

No está implementado, pero el material necesario existe:

```python
def regar_hasta(alias, litros, intervalo=15, max_iter=240):
    """Cierra la válvula al alcanzar el volumen pedido. Devuelve litros medidos."""
    import valvulas as v
    mq = v.ValvulasMqtt()
    mq.connect()
    topic = f"{v.Z2M_PREFIX}/{v.VALVULAS[alias]}"
    mq.subscribe(topic)
    inicial = 0.0
    try:
        for _ in range(max_iter):
            mq.received.pop(topic, None)
            mq.publish_json(f"{topic}/get", {"state": ""})
            payload = mq.wait_for(topic, lambda x: isinstance(x, dict), timeout=20)
            if payload and payload.get("real_time_irrigation_volume") is not None:
                actual = float(payload["real_time_irrigation_volume"])
                inicial = inicial or actual
                if actual - inicial >= litros:
                    break
            import time; time.sleep(intervalo)
    finally:
        mq.disconnect()
    return inicial
```

Advertencias: el contador de sesión puede reiniciarse si la válvula arranca su propio
programa; y **nunca** dejes este bucle sin un cierre garantizado en `finally`.

### 8.6 Integración con Home Assistant

`homeassistant.enabled` está en `false`. Si lo activas, Z2M publica en
`homeassistant/*` y HA descubre los dispositivos. Es una decisión de arquitectura, no
una limitación técnica: el proyecto se diseñó para no depender de HA.

---

## 9. Pruebas

No hay suite automatizada; este es el plan manual reproducible:

```bash
# 1. Sintaxis
python3 -m py_compile valvulas.py menu_valvulas.py

# 2. Ayuda y errores de argparse (exit 2)
python3 valvulas.py
python3 valvulas.py open

# 3. Alias inválido (exit 1, sin tocar la radio)
python3 valvulas.py open inexistente; echo "exit=$?"

# 4. Broker caído (exit 1, mensaje limpio, sin traceback)
sudo systemctl stop mosquitto
python3 valvulas.py list; echo "exit=$?"
sudo systemctl start mosquitto

# 5. Integración con Z2M
python3 valvulas.py list
python3 valvulas.py status

# 6. Comando + confirmación
python3 valvulas.py open valvula_riego
python3 valvulas.py read valvula_riego
python3 valvulas.py close valvula_riego --timeout 30

# 7. Interruptor
python3 valvulas.py on interruptor
python3 valvulas.py off interruptor

# 8. Menú (arranque y salida limpia)
printf "q\n" | python3 menu_valvulas.py

# 9. Monitor (corta a los 5 s)
timeout 5 python3 valvulas.py monitor
```

Criterio de éxito: exit codes correctos, mensajes sin tracebacks y confirmación real
en los comandos de válvula.

---

## 10. Deuda técnica conocida

| # | Tema | Impacto | Idea de solución |
|---|---|---|---|
| 1 | `pair` no cierra `permit_join` ante `Ctrl+C` | Ventana abierta hasta el reinicio | `try/finally` con publicación de cierre |
| 2 | Configuración en constantes | Cambiar broker exige editar código | Leer de variables de entorno con *fallback* |
| 3 | Sin credenciales MQTT | Inaceptable fuera de loopback | `MQTT_USER/PASS` desde entorno + TLS |
| 4 | Sin tests | Regresiones silenciosas | `pytest` con un broker en Docker y *fakes* de paho |
| 5 | `status` secuencial | Lento con muchas válvulas | `get` en paralelo y espera conjunta |
| 6 | Sin reintento del `set` | Un paquete perdido = timeout | Reintentar una vez tras el `get` fallido |
| 7 | `monitor` sin filtro | Ruido con muchas devices | Aceptar un patrón de topic por argumento |
| 8 | Sin métricas históricas | No hay series temporales | Publicar a InfluxDB/SQLite desde un `cron` |

---

## 11. Notas de seguridad para quien modifique

- No imprimas `configuration.yaml` en logs ni en mensajes de error: contiene la
  `network_key`.
- No añadas `retain=True` a comandos: un `set` retenido se reejecuta al reconectar y
  puede abrir una válvula sola.
- Cualquier comando nuevo debe **validar el alias** antes de publicar.
- Mantén `optimistic: false`. Si alguien lo pone en `true`, la confirmación real deja
  de ser real y este proyecto pierde su única promesa fuerte.
- No escribas rutas absolutas de usuario ni IPs en el código; usa placeholders en la
  documentación.
