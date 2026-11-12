# Datasheet — Manual de operador

**Sistema:** `valvulas-zigbee`
**Ámbito:** operación diaria de dos válvulas SONOFF SWV-NH y un interruptor SONOFF
ZBMINIL2 mediante la CLI `valvulas.py` o el menú `menu_valvulas.py`.
**Audiencia:** quien riega. No requiere conocimientos de programación; sí ayuda
entender qué es un topic MQTT.

| Dato | Valor |
|---|---|
| Versión documentada | Zigbee2MQTT 2.13 · paho-mqtt 2.1 · Python 3.12 |
| Host de referencia | Raspberry Pi 4 (Debian 11, aarch64) |
| Coordinador | Sonoff Zigbee 3.0 USB Dongle Plus V2 (EFR32MG21, EZSP v13) |
| Dispositivos | 2 válvulas SWV-NH + 1 interruptor ZBMINIL2 |
| Conectividad requerida | Ninguna hacia internet. Solo la red local y el USB |

---

## 1. Qué controla este sistema

| Dispositivo | Qué hace | Cómo se manda |
|---|---|---|
| `valvula_riego` | Abre o cierra el paso de agua y mide caudal/volumen | `open` / `close` |
| `valvula_principal` | Idem, segundo circuito | `open` / `close` |
| `interruptor` | Enciende o apaga una carga (bomba, luz, otra válvula) | `on` / `off` |

No hay app, no hay cuenta, no hay nube. Todo ocurre entre el dongle USB, un
contenedor local y los scripts.

---

## 2. Principio físico

### 2.1 La válvula

La **SONOFF SWV-NH** es una **válvula de bola motorizada** montada en línea. Un
motor eléctrico gira la bola 90° para abrir o cerrar el paso. El cuerpo incorpora un
**caudalímetro** (turbina o efecto Hall según lote) que cuenta el agua que pasa y
permite reportar:

- **Caudal instantáneo** en m³/h.
- **Volumen** de la sesión actual y del día, en litros.
- **Duración** de la sesión, en segundos.

Se alimenta con pilas. Como es un dispositivo Zigbee a batería, **duerme** para
ahorrar energía: no mantiene una conexión permanente, sino que despierta, reporta y
vuelve a dormir. Eso condiciona toda la operación: un comando puede tardar segundos
y, tras un reinicio del coordinador, la válvula puede tardar minutos en "aparecer".

### 2.2 La radio

La comunicación es **Zigbee 3.0 sobre IEEE 802.15.4, banda de 2.4 GHz**, cifrada con
AES-128. La red la gobierna el **coordinador** (el dongle). No hay IP ni DNS: los
nodos se identifican por direcciones de 64 bits (IEEE) y 16 bits (NWK).

Alcance típico en interior: 10-20 m con paredes de por medio. Los dispositivos
alimentados a red (como el ZBMINIL2) **repeten** la señal y extienden la malla; las
válvulas a batería no.

### 2.3 La cadena de software

```
Válvula  ──Zigbee──▶  Dongle USB  ──serie──▶  Zigbee2MQTT  ──MQTT──▶  Scripts
                                                                      ├─ valvulas.py
                                                                      └─ menu_valvulas.py
```

Zigbee2MQTT traduce cada atributo Zigbee a un campo JSON. La CLI no habla Zigbee:
publica y lee JSON en el broker local.

---

## 3. Operación diaria

### 3.1 Antes de empezar

```bash
cd ~/valvulas-zigbee
docker compose ps            # el contenedor debe estar "Up"
systemctl is-active mosquitto
```

Si el contenedor no está arriba:

```bash
docker compose up -d
docker compose logs -f --tail=30
```

Espera a ver `Zigbee2MQTT started!`.

### 3.2 Menú interactivo (recomendado para el día a día)

```bash
python3 menu_valvulas.py
```

```
==============================================
      CONTROL DE DISPOSITIVOS ZIGBEE
==============================================
   Válvulas:
     valvula_riego        : OFF
     valvula_principal    : OFF
     (ON = abierta, OFF = cerrada)

   Interruptores:
     interruptor          : OFF
     (ON = encendido, OFF = apagado)
----------------------------------------------
   [1]   Abrir  valvula_riego
   [2]   Cerrar valvula_riego
   [3]   Abrir  valvula_principal
   [4]   Cerrar valvula_principal
   [5]   Encender interruptor
   [6]   Apagar  interruptor
   [R]   Actualizar estado
   [Q]   Salir
----------------------------------------------
   Elige una opción:
```

| Tecla | Efecto |
|---|---|
| `1` … `6` | Ejecuta la acción indicada |
| `R` | Relee el estado real de todos los dispositivos |
| `Q` o `0` | Sale |
| `Ctrl+C` | Sale cerrando la conexión MQTT |

Cada acción **espera la confirmación del dispositivo** (hasta 20 s) y luego muestra
batería, caudal y litros.

### 3.3 CLI (para scripts y consultas)

| Acción | Comando |
|---|---|
| Ver dispositivos de la red | `python3 valvulas.py list` |
| Estado de todas las válvulas | `python3 valvulas.py status` |
| Abrir / cerrar válvula | `python3 valvulas.py open valvula_riego` · `close` |
| Encender / apagar interruptor | `python3 valvulas.py on interruptor` · `off` |
| Leer caudal y batería | `python3 valvulas.py read valvula_riego` |
| Ver todo en vivo | `python3 valvulas.py monitor` |
| Emparejar dispositivo nuevo | `python3 valvulas.py pair 60` |

### 3.4 Qué significa "confirmación real"

El script no dice `OK` cuando envía el comando, sino cuando **la válvula reporta el
nuevo estado**. Es la diferencia entre "lo mandé" y "pasó". Si no llega la
confirmación en el timeout (15 s por defecto, `--timeout` para cambiarlo), el
comando **falla** y devuelve un código de salida distinto de cero.

---

## 4. Lectura e interpretación del estado

### 4.1 Tabla de campos

| Campo | Unidad | Lectura |
|---|---|---|
| `state` | ON/OFF | `ON` = abierta (pasa agua), `OFF` = cerrada |
| `flow` | m³/h | Caudal instantáneo. Si no hay agua, no aparece |
| `real_time_irrigation_volume` | L | Litros de la sesión actual o de la última |
| `real_time_irrigation_duration` | s | Segundos de esa sesión |
| `daily_irrigation_volume` | L | Litros de hoy (se reinicia cada día) |
| `valve_work_state` | enum | `idle` reposo · `working` programa interno en marcha |
| `current_device_status` | enum | `normal_state` · `water_shortage` · `water_leakage` |
| `battery` | % | Carga de las pilas |
| `linkquality` | 0-255 | Calidad de enlace (LQI) |

### 4.2 Conversión de caudal

La válvula reporta **m³/h**; el script añade **L/min**:

```
L/min = (m³/h × 1000) / 60
```

| m³/h | L/min | Se parece a |
|---|---|---|
| 0.05 | 0.8 | Un grifo que gotea |
| 0.12 | 2.0 | Goteo de un circuito pequeño |
| 0.45 | 7.5 | Aspersión doméstica |
| 1.20 | 20.0 | Manguera abierta |

### 4.3 Los tres volúmenes

- **Sesión** (`real_time_irrigation_volume`): se reinicia al empezar un riego nuevo.
- **Duración** (`real_time_irrigation_duration`): acompaña a la sesión.
- **Diario** (`daily_irrigation_volume`): acumula todo el día.

Ejemplo práctico: si abres a las 7:00 y cierras a las 7:20, el volumen de sesión y
la duración describen ese riego; el diario suma todos los riegos del día.

### 4.4 Cómo saber que cerró de verdad

Es la pregunta más importante del manual. Al cerrar, la válvula apenas hace ruido y
el LED se apaga; el silencio no prueba nada.

**Procedimiento:**

```bash
python3 valvulas.py read valvula_riego    # anota Estado, Duración y Vol. de hoy
sleep 60
python3 valvulas.py read valvula_riego
```

| Resultado | Diagnóstico |
|---|---|
| `Estado: OFF` y duración **igual** | Cerrada correctamente |
| `Estado: OFF` y duración **subiendo** | El programa interno sigue regando (`working`) |
| `Estado: OFF` y caudal **> 0** | Fuga, válvula atascada o programa cíclico |
| `Estado: ON` | Sigue abierta; repite `close --timeout 30` |
| Sin respuesta | Dormida o fuera de alcance: pulsa su botón o espera el check-in |

### 4.5 Batería y señal

| Batería | Acción |
|---|---|
| 100-60 % | Nada |
| 60-30 % | Ten pilas de repuesto |
| < 30 % | Cámbialas: el cierre y el reporte empiezan a fallar |

| LQI | Interpretación |
|---|---|
| > 150 | Excelente |
| 50-150 | Correcto |
| < 50 | Marginal: comandos que caducan, entrevistas que fallan |

---

## 5. Emparejamiento

1. `python3 valvulas.py pair 60`.
2. Pulsa el botón del dispositivo hasta que el LED parpadee (SWV-NH) o el botón de
   reset (ZBMINIL2).
3. Espera a que termine la entrevista (10-30 s).
4. Renombra a `valvula_riego`, `valvula_principal` o `interruptor` en
   `http://localhost:8099`.
5. Verifica con `python3 valvulas.py list`.

Si el dispositivo está lejos, **empareja cerca del dongle** y luego muévelo: la
entrevista necesita mejor señal que la operación normal.

---

## 6. Calibración y ajustes

| Ajuste | Dónde | Cómo |
|---|---|---|
| Nombre de válvulas | `valvulas.py` → `VALVULAS` | Alias → `friendly_name` |
| Nombre de interruptores | `valvulas.py` → `INTERRUPTORES` | Idem |
| Broker MQTT | `valvulas.py` → `MQTT_HOST` / `MQTT_PORT` | `127.0.0.1:1883` |
| Base de topics | `valvulas.py` → `Z2M_PREFIX` | Debe coincidir con `mqtt.base_topic` |
| Timeout de confirmación | `--timeout N` | 15 s por defecto |
| Canal Zigbee | `configuration.yaml` → `advanced.channel` | 11/15/20/25 |
| Estado optimista | `configuration.yaml` → `optimistic` | **Siempre `false`** |

### Sobre la precisión del caudalímetro

La SWV-NH no es un instrumento de laboratorio: es un medidor doméstico. En caudales
muy bajos (goteo mínimo) puede no detectar paso de agua; en caudales altos tiende a
ser estable. Para detectar fugas sirve de sobra; para facturar, no.

### Sobre el sentido de instalación

Respeta la flecha del cuerpo de la válvula. Instalada al revés, el caudal puede
medirse mal o no medirse.

---

## 7. Mantenimiento

| Periodicidad | Tarea |
|---|---|
| Semanal | `python3 valvulas.py status` y revisar batería y LQI |
| Mensual | Revisar `daily_irrigation_volume` buscando consumos anómalos |
| Trimestral | Copiar `config/` (respaldos) a otra máquina |
| Semestral | `docker compose pull && docker compose up -d` (actualizar Z2M) |
| Anual / al fallar | Cambiar pilas de las válvulas |

### Respaldo mínimo

```bash
tar czf ~/respaldo-zigbee-$(date +%F).tar.gz \
    -C ~/valvulas-zigbee config/configuration.yaml \
    config/database.db config/coordinator_backup.json
```

Sin `database.db` hay que re-emparejar cada válvula físicamente. Guarda ese archivo
como si fuera la llave de casa.

---

## 8. Catálogo de mensajes

| Mensaje | Significado | Qué hacer |
|---|---|---|
| `No se pudo conectar al broker MQTT` | Mosquitto caído o en otro puerto | `sudo systemctl start mosquitto` |
| `Zigbee2MQTT no respondió al consultar la lista` | Contenedor caído o arrancando | `docker compose up -d`, esperar 30 s |
| `Sin dispositivos emparejados todavía` | Red vacía | `pair` + botón físico |
| `AVISO: No se encontraron tus válvulas por su nombre` | Nombres distintos a los configurados | Renombrar en Z2M o editar `VALVULAS` |
| `NO DISPONIBLE` | Válvula sin responder | Esperar check-in, acercar, revisar pilas |
| `timeout de Ns esperando el estado real` | No llegó confirmación | Ver logs, revisar rango y batería |
| `válvula desconocida 'X'` | Alias mal escrito | Usar uno de los alias configurados |
| `Aviso: 'X' no respondió al despertado` | Sigue dormida; se intentó igual | Repetir en unos minutos |
| `permit_join habilitado durante N s` | Ventana abierta | Pulsar el botón del dispositivo |

---

## 9. Preguntas frecuentes

**¿Funciona sin internet?**
Sí, completamente. Nada del sistema consulta servicios externos.

**¿Puedo controlarlo desde el móvil?**
Solo dentro de tu red local, usando el frontend de Zigbee2MQTT en el puerto 8099.
Exponerlo a internet queda fuera del alcance y es responsabilidad de quien lo haga.

**¿Qué pasa si se va la luz?**
El host se reinicia; al volver, los servicios arrancan solos (si están `enabled`) y
las válvulas tardan 2-5 minutos en hacer check-in. Mientras tanto los comandos
fallan con timeout: es normal.

**¿Qué pasa si se va internet?**
Nada. El riego programado con `cron` sigue funcionando.

**¿Se descarga la batería si consulto mucho?**
Cada `status`/`read` despierta la válvula y consume energía. Consultar cada minuto
todo el día acorta la vida de las pilas; cada 30 minutos es razonable.

**¿Puedo tener más de dos válvulas?**
Sí. Añade la entrada en `VALVULAS` y empareja la nueva. El menú se adapta solo.

**¿Puedo regar 20 litros exactos y que se cierre?**
No viene implementado. Se puede programar leyendo `real_time_irrigation_volume` en un
bucle; el manual de desarrollador explica cómo.

**Oí un clic al abrir pero nada al cerrar, ¿está rota?**
No. El cierre es asistido por muelle y suena mucho menos. Compruébalo con el contador
de duración.

---

## 10. Seguridad

- Este sistema **abre agua real**. No lo dejes programado sin un cierre garantizado.
- Mantén `permit_join` desactivado en reposo.
- No publiques `configuration.yaml`, `state.json` ni `database.db`: contienen la clave
  de red Zigbee y los identificadores de tus dispositivos.
- El frontend de Zigbee2MQTT no tiene autenticación.
- Si detectas caudal con la válvula cerrada, cierra el paso manual y revisa la
  instalación: puede ser una fuga.

---

## 11. Glosario

| Término | Significado |
|---|---|
| **Zigbee** | Protocolo de radio de bajo consumo sobre IEEE 802.15.4 |
| **Coordinador** | Nodo que crea y gobierna la red Zigbee (el dongle) |
| **End device** | Nodo final, normalmente a batería, que duerme |
| **Router** | Nodo alimentado a red que retransmite para otros |
| **LQI** | Link Quality Indicator, calidad del enlace (0-255) |
| **IEEE** | Dirección física de 64 bits de un dispositivo Zigbee |
| **MQTT** | Protocolo publish/subscribe usado entre Z2M y los scripts |
| **Topic** | "Canal" con nombre jerárquico donde se publican mensajes |
| **Broker** | Servidor MQTT que enruta los mensajes (Mosquitto) |
| **`permit_join`** | Ventana durante la cual la red acepta dispositivos nuevos |
| **`optimistic`** | Si Z2M publica el estado esperado o solo el real |
| **Check-in** | Reconexión periódica de un end device tras un reinicio |
| **ZCL** | Zigbee Cluster Library, el "vocabulario" de comandos Zigbee |
