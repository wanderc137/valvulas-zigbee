# Bitácora del proyecto

Registro de lo que salió mal, de lo que se descartó y de lo que se aprendió. Está
escrito para que un proyecto futuro —mío o de otro— no repita los mismos tres o cuatro
errores que costaron más tiempo.

---

## 1. Problemas encontrados y cómo se resolvieron

### P-01 · El converter dice `ON`/`OFF`, no `OPEN`/`CLOSE`

**Síntoma.** La especificación inicial describía comandos con `{"state": "OPEN"}` y
`{"state": "CLOSE"}`. Los primeros envíos no cambiaban nada y no había error visible.

**Causa.** La SONOFF SWV-NH se expone mediante el cluster `OnOff` de Zigbee: el
converter publica y acepta `ON`/`OFF` y `TOGGLE`. `OPEN`/`CLOSE` no existe en ese
vocabulario. El plan se había escrito desde la intuición ("una válvula se abre"), no
desde la documentación del dispositivo.

**Solución.** Constante `STATE_OPTIONS = {"ON", "OFF"}` y validación previa: un estado
inválido se rechaza antes de publicar nada. `open`/`close` de la CLI se traducen a
`ON`/`OFF` en `main()`; el usuario sigue hablando de abrir y cerrar, que es lo que
significa.

**Lección.** Con dispositivos Zigbee, el contrato no lo define el fabricante ni el
sentido común: lo define el *converter* de Zigbee2MQTT. Antes de escribir una línea,
publicar un `get` y mirar el JSON real.

---

### P-02 · Los comandos fallaban de forma intermitente (válvulas dormidas)

**Síntoma.** `open`/`close` funcionaban recién usados y fallaban tras un rato de
inactividad con `timeout esperando el estado real`. En los logs de Zigbee2MQTT se veía
el comando salir y caducar a los 10 s.

**Causa.** La SWV-NH es un **end device a batería**. Entra en sueño profundo y solo
despierta en sus ventanas de escucha. Un `set` enviado mientras duerme se queda en la
cola del coordinador hasta que expira el timeout ZCL. No es un fallo del script: es
física de bajo consumo.

**Solución.** **Despertar antes de comandar.** Secuencia implementada en `cmd_set` y en
`send_valve_command`:

1. `publish({"state": ""}, topic=<nombre>/get)`
2. Esperar la respuesta real (10-20 s)
3. **Descartar** ese payload (es el estado viejo, no la confirmación)
4. Publicar el `set`
5. Esperar el estado nuevo con el timeout del comando

Resultado: de "a veces sí, a veces no" a respuesta consistente en 2-8 s.

**Lección.** En cualquier dispositivo a batería, la primera operación tras un periodo
de calma es *despertarlo*, no comandarlo. Y hay que descartar la lectura de despertar
para no confundirla con la confirmación (un `OFF` leído antes del `set` haría creer que
el `close` funcionó al instante).

---

### P-03 · `permit_join` funcionaba… o quedaba abierto para siempre

**Síntoma.** Publicar `{"value": true}` en `bridge/request/permit_join` a veces no
abría la ventana y, en otras versiones, la dejaba abierta indefinidamente.

**Causa.** Zigbee2MQTT 2.x exige el campo `time` (segundos) en el payload. Sin él, el
comportamiento depende de la versión y de la configuración previa.

**Solución.** `cmd_pair` publica `{"value": true, "time": N}` y cierra con
`{"value": false, "time": 0}`.

**Deuda pendiente.** Un `Ctrl+C` durante el `sleep(N)` deja la ventana abierta. Se
documenta como limitación; la solución es envolver el `sleep` en `try/finally`.

**Lección.** Los payloads de `bridge/request/*` cambian entre versiones mayores. Fijar
la versión en la documentación y probar el camino completo, no solo la publicación.

---

### P-04 · `?` en el menú justo después de reiniciar

**Síntoma.** Tras reiniciar la Raspberry, `valvulas.py status` devolvía `NO DISPONIBLE`
y el menú mostraba `?` durante varios minutos. Después, todo normal.

**Causa.** Los end devices a batería no mantienen una sesión permanente: tras un
reinicio del coordinador, cada uno debe hacer **check-in** en su siguiente ventana de
escucha. Eso tarda **2-5 minutos** con la configuración de *polling* por defecto.

**Solución.** No se "arregla": se **documenta y se avisa**. La CLI imprime la pista en
el error de timeout y el menú muestra una nota al arrancar si detecta dispositivos sin
responder. Pulsar el botón físico de la válvula fuerza un despertar inmediato.

**Lección.** Hay estados del sistema que no son fallos sino latencias del hardware. Si
no se documentan, el usuario concluye que el software está roto.

---

### P-05 · El interruptor no emparejaba a distancia

**Síntoma.** El ZBMINIL2, instalado en otra planta, no completaba la entrevista:
`can not get node descriptor` / `Interview failed`.

**Causa.** Señal insuficiente. La entrevista intercambia decenas de mensajes seguidos
(descriptor de nodo, endpoints, clusters, binding, configuración de reportes). Es mucho
más exigente que un comando puntual.

**Solución.** Emparejar **junto al dongle** y devolver el dispositivo a su sitio
después. La red lo conserva; el enlace se renegocia solo.

**Lección.** "Funciona para operar" no implica "funciona para emparejar". Trata el
emparejamiento como una operación de proximidad.

---

### P-06 · `water_shortage` en una válvula que funcionaba

**Síntoma.** Una válvula reportaba `current_device_status: water_shortage` con la
batería al 100 % y el estado `OFF`.

**Causa.** El circuito no tenía presión en ese momento (llave general cerrada). El
dispositivo lo informa, pero **no impide abrir**.

**Solución.** Ninguna: es informativo. Se documenta para que no se confunda con un
fallo, y se recomienda mirar `flow` para saber si realmente circula agua.

**Lección.** Distinguir entre "el dispositivo no obedece" y "el dispositivo informa de
una condición del mundo físico". Mezclarlos genera diagnósticos equivocados.

---

### P-07 · El dongle cambiaba de `/dev/ttyUSB*`

**Síntoma.** Tras conectar otro dispositivo USB, el contenedor arrancaba sin radio.

**Causa.** `/dev/ttyUSB0` depende del orden de enumeración. No es un nombre estable.

**Solución.** Usar `/dev/serial/by-id/usb-Itead_Sonoff_...-if00-port0` en el mapeo de
`docker-compose.yml`, manteniendo `/dev/ttyUSB0` dentro del contenedor.

**Lección.** Cualquier dispositivo USB que importe debe referenciarse por `by-id`. Es
gratis y elimina una clase entera de fallos intermitentes.

---

### P-08 · "¿Por qué hay un `node index.js` consumiendo 100 MB?"

**Síntoma.** Al limpiar la Raspberry, un proceso `node index.js` parecía sospechoso.

**Causa.** Es **Zigbee2MQTT**: la aplicación está escrita en Node.js. Matarlo tumba el
control.

**Solución.** Documentarlo en el runbook de limpieza con una advertencia explícita.

**Lección.** Antes de "limpiar procesos huérfanos", identificar qué corre dentro de los
contenedores. Un proceso con nombre genérico no es un proceso perdido.

---

### P-09 · `--timeout` no hacía nada (auditoría, ciclo 1)

**Síntoma.** `open valvula_riego --timeout 3` seguía esperando 15 s.

**Causa.** `main()` leía el argumento pero llamaba a `cmd_set(alias, "ON")` sin pasarlo;
la función tampoco aceptaba el parámetro.

**Solución.** `cmd_set(alias, new_state, timeout=TIMEOUT)`, `ValvulasMqtt(timeout=...)`
y despacho con `args.timeout`. Verificado con `--timeout 3`.

**Lección.** Un argumento aceptado por `argparse` y no usado es peor que no tenerlo:
genera confianza falsa. Los tests de integración deben comprobar el efecto, no la
ayuda.

---

### P-10 · Tracebacks de socket al arrancar sin broker

**Síntoma.** Con Mosquitto caído, la CLI escupía un `ConnectionRefusedError` con traza.

**Causa.** `client.connect()` puede lanzar `OSError`/`TimeoutError` antes de que el
callback `on_connect` exista, y el código no lo capturaba.

**Solución.** `try/except (OSError, TimeoutError)` → `ConnectionError` con mensaje
accionable (`¿Está Mosquitto activo? (systemctl status mosquitto)`), `loop_stop()` en el
fallo de handshake, y `except ConnectionError` en todos los `cmd_*`.

**Lección.** En una CLI, un traceback es un mensaje de error mal escrito. El usuario
final necesita saber qué hacer, no dónde falló el intérprete.

---

### P-11 · La Raspberry venía con demasiada vida encima

**Síntoma.** Antes de dedicarla al riego, la Pi usaba ~420 MB de RAM y ~6.5 GB de disco
con escritorio, impresión, Bluetooth, Kubernetes e InfluxDB.

**Solución.** Runbook de limpieza: deshabilitar y purgar escritorio y servicios
heredados. Resultado: ~225 MB de RAM y ~4.3 GB de disco, con solo `docker`,
`mosquitto`, `zigbee2mqtt` y lo imprescindible.

**Lección.** Un host dedicado es más fácil de razonar, de respaldar y de reiniciar. La
limpieza se documenta porque es irreversible y hay que saber qué NO tocar.

---

## 2. Enfoques descartados y por qué

| Enfoque | Por qué se descartó |
|---|---|
| **Hablar Zigbee directo** con `zigpy` + `bellows` (radio serie propia) | Hay que competir con el coordinador por el dongle, implementar el *converter* de la SWV-NH y mantener una base de datos de red. Zigbee2MQTT ya lo hace y expone JSON estable |
| **Flashear la válvula** con firmware alternativo | No hacía falta: es Zigbee 3.0 estándar. Flashear añade riesgo de ladrillo y quita soporte del converter |
| **Usar la nube del fabricante** o su app | Es exactamente el problema que el proyecto quiere resolver: dependencia, privacidad y caducidad del servicio |
| **Home Assistant como capa obligatoria** | Añade un servicio pesado y un modelo de configuración propio para una necesidad que se resuelve con dos scripts. Se deja la puerta abierta (`homeassistant.enabled`), no como requisito |
| **MQTT con usuario/contraseña y TLS en loopback** | El broker escucha solo en `127.0.0.1`. Añadir TLS entre dos procesos del mismo host no aporta seguridad y complica el arranque. Se documenta qué hacer si el broker sale de la máquina |
| **`optimistic: true`** | Publica el estado esperado antes de que el dispositivo confirme: convierte la "confirmación real" en teatro. Se rechaza por diseño |
| **Comandos retenidos** | Un `set` retenido se reejecuta al reconectar cualquier cliente. En una válvula de agua eso es abrir sola. Nunca |
| **API REST propia** (`FastAPI` + endpoints) | El consumidor natural es un humano con una terminal o un `cron`. Un servidor HTTP añade puertos, autenticación y dependencias para nada |
| **Scheduler dentro del proyecto** | Duplicaría `cron`/`systemd` y crearía estado que hay que persistir, migrar y depurar. La CLI es la API; el "cuándo" lo pone el sistema operativo |
| **Un `requirements.txt` de una línea** | Se documenta el `pip install` directamente. Si el proyecto creciera, el archivo aparecería sin drama |
| **Publicar la `configuration.yaml` real** | Contiene `network_key`, `pan_id` e IEEE. Se sustituye por `configuration.example.yaml` con placeholders |

---

## 3. Decisiones técnicas y su justificación

| Decisión | Justificación |
|---|---|
| Zigbee2MQTT en **Docker con `network_mode: host`** | Alcanza `127.0.0.1:1883` sin `host.docker.internal` y expone el frontend en `:8099` sin mapeos. Menos configuración, menos piezas |
| **Mosquitto del sistema**, no en contenedor | Ya estaba instalado y confinado a loopback. Un contenedor más es un servicio más que puede no arrancar |
| **Un único contenedor** | El sistema completo es una app Node.js. Microservicios aquí serían sobreingeniería |
| **Configuración en constantes** al inicio de `valvulas.py` | Un archivo, cero indirección. Cambiar el broker es editar una línea y se ve en el `diff` |
| **`ValvulasMqtt` como única clase** | Concentra el ciclo de vida del cliente y los tres verbos MQTT. Los comandos quedan planos y legibles |
| **`wait_for` con sondeo cada 100 ms** | Sin hilos ni `asyncio`: el caso de uso es secuencial y el coste es despreciable. Menos superficie de error |
| **Descartar el payload cacheado antes de cada comando** | Evita confirmar con un `OFF` de hace tres horas. Es la línea que hace fiable toda la lógica |
| **`exit code 1` en cualquier fallo** | Permite `&&`, `cron` y `systemd` sin parsear texto |
| **Mensajes de error con pistas** | El usuario de una CLI no lee el código: lee el error. Cada uno incluye el siguiente paso |
| **Dos interfaces sobre el mismo núcleo** | El menú importa de la CLI. Cero duplicación de la lógica MQTT |
| **Documentación separada** (operador / desarrollador / runbook / bitácora) | Cada lector busca algo distinto. Mezclarlo obliga a todos a leer de más |

---

## 4. Lecciones aplicables a proyectos futuros

1. **El hardware define el ritmo.** Un dispositivo a batería no es un servidor: duerme,
   hace check-in tarde y caduca comandos. Diseña para eso desde el principio, no como
   parche.
2. **Confirma contra el dispositivo, nunca contra tu propia expectativa.** Si el
   *middleware* puede "adelantar" el estado, ya no estás midiendo la realidad.
3. **La documentación del converter manda.** Los nombres de los campos y los valores
   válidos se leen publicando un `get`, no suponiendo.
4. **Los secretos se diseñaron para filtrarse.** `network_key`, `pan_id`, IEEE, IPs y
   rutas acaban en capturas y en `git log`. Plantilla con placeholders desde el primer
   commit.
5. **Un `rm` equivocado cuesta trabajo físico.** Si un archivo (`database.db`) exige
   pulsar botones en el jardín para recuperarlo, va en `.gitignore`, en el respaldo y en
   la primera página del manual.
6. **Los estados intermedios son parte de la UX.** `?` y `NO DISPONIBLE` no son ruido:
   son información. Explícalos o el usuario creerá que rompiste algo.
7. **No mates procesos que no entiendes.** `node index.js` era la aplicación.
8. **Los argumentos aceptados deben tener efecto.** Un `--timeout` que no hace nada es
   peor que su ausencia.
9. **Automatizar agua exige un cierre garantizado.** Abrir es fácil; el diseño está en
   el `finally`, en los reintentos y en el `logger`.
10. **Separar lo comprobado de lo supuesto.** En la documentación, "verificado en el
    despliegue" y "según la especificación" no son lo mismo, y mezclarlo destruye la
    confianza.

---

## 5. Cronología resumida

| Fecha | Hito |
|---|---|
| 2026-08-12 | Especificación, implementación inicial y auditoría ciclo 1 (5 incidencias) |
| 2026-08-12 | Auditoría ciclo 2: aprobado sin incidencias |
| 2026-08-12 | Red Zigbee formada, válvulas e interruptor emparejados |
| 2026-08-13 | Migración a Raspberry Pi conservando la red; servicios persistentes; limpieza del host |
| 2026-08-17 | Interruptor ZBMINIL2 integrado en CLI y menú; fix de sueño consolidado |
| 2026 | Documentación de portfolio, plantilla de configuración y diagramas |
