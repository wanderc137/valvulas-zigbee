# Referencias

Fuentes consultadas para construir y documentar este proyecto, con una nota de **qué
aporta cada una**. Los enlaces pueden cambiar de ruta con el tiempo; el nombre del
documento suele seguir siendo válido.

---

## 1. Zigbee2MQTT (la pieza central)

| Recurso | Para qué sirve |
|---|---|
| [Documentación oficial](https://www.zigbee2mqtt.io/) | Punto de entrada: instalación, configuración, integración |
| [Configuración (`configuration.yaml`)](https://www.zigbee2mqtt.io/guide/configuration/) | Referencia de todas las claves usadas: `mqtt`, `serial`, `advanced`, `device_options`, `frontend`, `availability` |
| [Dispositivo SONOFF SWV](https://www.zigbee2mqtt.io/devices/SWV.html) | Lista exacta de atributos y comandos de la válvula: `state`, `flow`, `real_time_irrigation_volume`, `cyclic_timed_irrigation`, etc. |
| [Dispositivo SONOFF ZBMINIL2](https://www.zigbee2mqtt.io/devices/ZBMINIL2.html) | Atributos del interruptor: `state`, `power_on_behavior`, `linkquality` |
| [Adaptadores EZSP / ember](https://www.zigbee2mqtt.io/guide/adapters/emberznet.html) | Por qué `adapter: ember` y qué firmware necesita el EFR32 |
| [Topics MQTT](https://www.zigbee2mqtt.io/guide/usage/mqtt_topics_and_messages.html) | Contrato de `bridge/devices`, `bridge/request/*`, `<nombre>/set`, `<nombre>/get` |
| [`permit_join`](https://www.zigbee2mqtt.io/guide/usage/mqtt_topics_and_messages.html#zigbee2mqtt-bridge-request-permit-join) | Formato del payload y el campo `time` en la serie 2.x |
| [Solución de problemas](https://www.zigbee2mqtt.io/guide/faq/) | Entrevistas fallidas, `node descriptor`, dispositivos que duermen |

**Cómo se usó:** el contrato MQTT del proyecto (topics, payloads, QoS, retención) sale
de esta documentación, no de la intuición. Los nombres de los campos de la válvula son
los del *converter* oficial; si Zigbee2MQTT los cambia, cambia `STATE_FIELDS`.

---

## 2. MQTT

| Recurso | Para qué sirve |
|---|---|
| [Especificación MQTT 3.1.1 (OASIS)](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/mqtt-v3.1.1.html) | Semántica de QoS, retención, sesiones limpias |
| [MQTT Essentials (HiveMQ)](https://www.hivemq.com/mqtt-essentials/) | Introducción práctica a topics y wildcards |
| [Eclipse Mosquitto — documentación](https://mosquitto.org/documentation/) | `mosquitto.conf`, `listener`, `allow_anonymous`, clientes de línea de comandos |
| [mosquitto.conf(5)](https://mosquitto.org/man/mosquitto-conf-5.html) | Sintaxis exacta de `listener 1883 127.0.0.1` |

**Decisiones derivadas:** QoS 1 para comandos y estados; comandos **no retenidos**
(un `set` retenido se reejecutaría al reconectar y podría abrir una válvula sola);
`bridge/devices` sí es retenido, por eso `list` responde al instante.

---

## 3. Python y paho-mqtt

| Recurso | Para qué sirve |
|---|---|
| [paho-mqtt en PyPI](https://pypi.org/project/paho-mqtt/) | Versión y notas de compatibilidad |
| [paho-mqtt — documentación](https://eclipse.dev/paho/files/paho.mqtt.python/html/index.html) | `CallbackAPIVersion.VERSION2`, `loop_start`, `wait_for_publish` |
| [Migración a paho 2.x](https://eclipse.dev/paho/files/paho.mqtt.python/html/migrations.html) | Por qué los callbacks requieren la nueva firma (`reason_code`, `properties`) |
| [argparse](https://docs.python.org/3/library/argparse.html) | Subcomandos, `required=True`, exit code 2 |

**Cómo se usó:** el cliente se crea con `mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
client_id="valvulas-cli")`. Sin la API v2, paho 2.x emite avisos y rompe la firma de
`on_connect`.

---

## 4. Zigbee, radio y hardware

| Recurso | Para qué sirve |
|---|---|
| [IEEE 802.15.4](https://standards.ieee.org/ieee/802.15.4/7029/) | Capa física y MAC sobre la que corre Zigbee |
| [Zigbee Cluster Library (CSA)](https://csa-iot.org/all-solutions/zigbee/) | De dónde vienen clusters como `OnOff` |
| [SONOFF — SWV-NH](https://sonoff.tech/) | Ficha del producto: tipo de válvula, baterías, caudalímetro |
| [SONOFF — ZBMINIL2](https://sonoff.tech/) | Ficha del interruptor sin neutro |

**Advertencia de uso:** las fichas comerciales sirven para conocer el hardware, pero
los **atributos reales** que expone el dispositivo son los que publica Zigbee2MQTT.
Cuando hay discrepancia, manda el converter.

---

## 5. Contenedores y sistema

| Recurso | Para qué sirve |
|---|---|
| [Docker Compose — referencia](https://docs.docker.com/compose/compose-file/) | `network_mode`, `devices`, `volumes`, `restart` |
| [koenkk/zigbee2mqtt (Docker Hub)](https://hub.docker.com/r/koenkk/zigbee2mqtt) | Etiquetas de imagen y variables de entorno |
| [systemd.service](https://www.freedesktop.org/software/systemd/man/systemd.service.html) | `Type=oneshot`, `RemainAfterExit` |
| [systemd.timer](https://www.freedesktop.org/software/systemd/man/systemd.timer.html) | `OnCalendar`, `Persistent` |
| [Debian — `dialout` y permisos serie](https://wiki.debian.org/SystemGroups) | Por qué el usuario necesita `dialout` |

---

## 6. Fuentes que se decidió **no** usar

| Fuente | Motivo |
|---|---|
| Documentación de la app del fabricante / eWeLink | Camino cloud, incompatible con el objetivo local |
| Guías de "flashear la válvula con firmware alternativo" | Innecesario: la SWV-NH es Zigbee estándar y funciona tal cual |
| Foros con configuraciones completas de terceros | Riesgo de copiar `network_key` y IEEE ajenos; se partió de la plantilla oficial |
| Repos con `configuration.yaml` reales | Mismo motivo: son secretos de red |

---

## 7. Cómo verificar lo que dice esta documentación

| Afirmación | Cómo comprobarla |
|---|---|
| Los topics y campos existen | `python3 valvulas.py monitor` mientras abres una válvula |
| La confirmación es real | Comparar `open` con `optimistic: false` y luego con `true`: el segundo "confirma" antes |
| El check-in tarda 2-5 min | Reiniciar el contenedor y cronometrar el primer `status` exitoso |
| El dongle es EFR32MG21 | `docker compose logs` (versión de firmware) o `lsusb` |
| El contenedor consume ~100 MB | `docker stats --no-stream zigbee2mqtt` |
| No hay tráfico a internet | `ss -tupn` o un cortafuegos con log |

---

## 8. Convenciones de esta documentación

- Los **IEEE**, IPs, rutas de usuario y credenciales son siempre placeholders
  (`0x00124b0000000001`, `<IP_DEL_HOST>`, `<TU_USUARIO>`).
- La configuración real de Zigbee2MQTT nunca se publica: se documenta a partir de
  `config/configuration.example.yaml`.
- Los mensajes de error citados son **literales** del programa, no reconstrucciones.
- Los valores de rendimiento provienen de un despliegue doméstico con 2 válvulas y 1
  interruptor; no son un *benchmark*.
