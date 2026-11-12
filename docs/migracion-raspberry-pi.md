# Runbook — Migración a Raspberry Pi (centro de mando Zigbee)

Procedimiento reproducible para mover `valvulas-zigbee` desde un equipo de trabajo a
una **Raspberry Pi dedicada** que queda como centro de mando estable de la red Zigbee.

> **Qué se conserva:** la red Zigbee completa, sin re-emparejar ninguna válvula. Se
> copian el mismo dongle y la misma configuración (`network_key`, `pan_id`, `channel`,
> `database.db`, `coordinator_backup.json`).
>
> **Qué se gana:** un host que arranca solo, sin escritorio, con menos de 250 MB de RAM
> en uso y control verificado de extremo a extremo.

Todos los valores de este documento son **placeholders**: `<IP_DEL_HOST>`,
`<TU_USUARIO>`, `<IEEE_DE_LA_VALVULA>`. Sustitúyelos por los de tu instalación y no
los commitees.

---

## 1. Contexto y arquitectura

```
[Válvulas SWV-NH] --(Zigbee 2.4 GHz)--> [Dongle Sonoff USB] --(USB)-->
   [Raspberry Pi: contenedor Zigbee2MQTT] --(MQTT 127.0.0.1:1883)--> [scripts Python]
```

### Qué es un servicio y qué no

| Componente | Tipo | Arranque al boot |
|---|---|---|
| `docker` | servicio systemd | `enabled` |
| `mosquitto` | servicio systemd | `enabled` |
| `zigbee2mqtt` (docker compose) | `oneshot` systemd | `enabled` |
| `valvulas.py` / `menu_valvulas.py` | scripts bajo demanda | no |

---

## 2. Prerrequisitos

- Dongle **Sonoff Zigbee 3.0 USB Dongle Plus V2** (EFR32MG21, adapter `ember`).
- Raspberry Pi 3/4/5 con Raspberry Pi OS o Debian (probado en Pi 4, Debian 11
  bullseye, aarch64).
- Acceso SSH con un usuario con `sudo`.
- El proyecto completo, **incluida la carpeta `config/`** (es la red Zigbee).
- Red local con DHCP o IP fija reservada.

---

## 3. Procedimiento

### Paso 0 — Relevamiento del equipo

```bash
ssh <TU_USUARIO>@<IP_DEL_HOST> '
  uname -a; uname -m; head -3 /etc/os-release;
  docker --version 2>/dev/null; systemctl is-active mosquitto;
  python3 --version; lsusb
'
```

Anota: arquitectura (`aarch64`/`armv7l`), si Docker existe, si Mosquitto corre y si el
dongle aparece en `lsusb`.

### Paso 1 — Actualizar el sistema

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt autoremove -y
```

Si la Pi venía con escritorio, la limpieza va en el paso 8.

### Paso 2 — Instalar la infraestructura

```bash
# Docker + plugin compose
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker,dialout "$USER"

# Broker MQTT
sudo apt install -y mosquitto mosquitto-clients

# Dependencia Python
sudo apt install -y python3-paho-mqtt        # o un venv con pip
```

Configurar Mosquitto **solo en loopback**:

```bash
sudo tee /etc/mosquitto/conf.d/local.conf > /dev/null <<'EOF'
listener 1883 127.0.0.1
allow_anonymous true
EOF
sudo systemctl enable --now mosquitto
```

Cierra y reabre la sesión SSH para que los grupos `docker` y `dialout` apliquen.

### Paso 3 — Copiar el proyecto

Copia **solo lo necesario**. No copies `.git/`, `__pycache__/` ni notas internas de
trabajo.

```bash
rsync -av --exclude '.git' --exclude '__pycache__' \
      ./valvulas-zigbee/ <TU_USUARIO>@<IP_DEL_HOST>:/home/<TU_USUARIO>/valvulas-zigbee/
```

Verifica integridad de los archivos críticos (deben coincidir):

```bash
md5sum config/database.db config/coordinator_backup.json config/configuration.yaml
```

> **Crítico:** `database.db` y `coordinator_backup.json` contienen el estado de la red.
> Si se pierden, hay que re-emparejar cada válvula físicamente.

### Paso 4 — Fijar el puerto del dongle

```bash
ls -l /dev/serial/by-id/ /dev/ttyUSB* 2>/dev/null
```

Usa el **symlink by-id**, no `/dev/ttyUSB0`, para que el mapeo sobreviva a cambios de
enumeración USB:

```
/dev/serial/by-id/usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_<SERIAL>-if00-port0
```

En `docker-compose.yml`:

```yaml
services:
  zigbee2mqtt:
    image: koenkk/zigbee2mqtt:latest
    container_name: zigbee2mqtt
    restart: unless-stopped
    network_mode: host
    devices:
      - /dev/serial/by-id/usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_<SERIAL>-if00-port0:/dev/ttyUSB0
    volumes:
      - ./config:/app/data
    environment:
      - TZ=America/Santiago
```

`config/configuration.yaml` sigue apuntando a `/dev/ttyUSB0` dentro del contenedor.

### Paso 5 — Levantar y verificar la red

```bash
cd ~/valvulas-zigbee
docker compose up -d
docker compose logs -f --tail=50
```

Espera:

```
z2m: Adapter network matches config.
z2m: Currently 2 devices are joined.
z2m: Zigbee2MQTT started!
```

Si aparecen las válvulas **sin re-emparejar**, la red se conservó. Si el dongle no
arranca (`Coordinator failed to start`), revisa permisos (`dialout`, el dispositivo en
`docker-compose.yml`) o el firmware EFR32.

### Paso 6 — Persistencia al arranque

```bash
sudo systemctl enable docker mosquitto

sudo tee /etc/systemd/system/zigbee2mqtt.service > /dev/null <<'EOF'
[Unit]
Description=Zigbee2MQTT docker compose stack (valvulas-zigbee)
Requires=docker.service
After=docker.service mosquitto.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=<TU_USUARIO>
WorkingDirectory=/home/<TU_USUARIO>/valvulas-zigbee
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
ExecReload=/usr/bin/docker compose up -d

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable zigbee2mqtt
```

### Paso 7 — Verificación de extremo a extremo

```bash
cd ~/valvulas-zigbee
python3 valvulas.py list                  # Coordinator + 2 válvulas + interruptor
python3 valvulas.py status                # ambas OFF, batería y señal
python3 valvulas.py open valvula_riego    # confirmación real
python3 valvulas.py close valvula_riego
python3 valvulas.py read valvula_riego
python3 menu_valvulas.py                  # menú (Q para salir)
curl -I http://localhost:8099             # frontend Z2M (HTTP 200)
```

### Paso 8 — Dejar la Pi dedicada (opcional)

Libera RAM y disco eliminando escritorio y servicios heredados. **Conserva** `docker`,
`containerd`, `mosquitto`, `ssh`, la red y lo que uses para acceso remoto.

```bash
sudo systemctl disable --now lightdm colord cups cups-browsed bluetooth hciuart \
  ModemManager avahi-daemon triggerhappy rtkit-daemon udisks2 packagekit

sudo apt purge -y lxde* lxpanel* lxplug-* openbox pcmanfm lightdm pi-greeter \
  pulseaudio* raspberrypi-ui-mods xserver-xorg* colord* avahi-daemon bluez* \
  cups* modemmanager packagekit libpulse0
sudo apt autoremove -y && sudo apt clean

sudo pkill -f gvfs; sudo pkill pulseaudio; sudo pkill -f packagekitd
```

> **Ojo con `node index.js`:** Zigbee2MQTT es una app Node.js. En `ps` aparece como
> `node index.js` y consume ~100 MB. **No lo mates**: es el propio Zigbee2MQTT.

Resultado típico: RAM usada de ~420 MB a ~225 MB y disco de ~6.5 GB a ~4.3 GB.

### Paso 9 — Reinicio limpio

```bash
sudo systemctl reboot
# espera 40-60 s y reconecta
systemctl is-active docker mosquitto zigbee2mqtt   # active active active
docker ps                                          # zigbee2mqtt Up
```

> **Check-in:** tras cada reinicio del coordinador, las válvulas a batería tardan
> **2-5 minutos** en reconectarse. Durante ese lapso muestran `?` y los comandos caducan.
> Es normal: espera, o pulsa el botón físico de cada válvula.

---

## 4. El fix del sueño de las válvulas

Las SWV-NH entran en sueño profundo tras un periodo de inactividad. Un `set` directo
mientras duermen caduca (timeout ZCL de 10 s) y la lectura inicial puede dar `?`.

**Solución implementada:** despertar antes de comandar. `cmd_set` (CLI) y
`send_valve_command` / `refresh_state` (menú) envían primero un `get`, esperan la
respuesta real y solo entonces envían el `set`. El menú además reintenta la lectura
inicial una vez.

Síntoma sin este fix: `Sin confirmación real ... en XXs` de forma esporádica.

---

## 5. Respaldo y restauración

### Respaldo

```bash
tar czf ~/respaldo-zigbee-$(date +%F).tar.gz \
    -C ~/valvulas-zigbee config/configuration.yaml \
    config/database.db config/coordinator_backup.json
```

Guárdalo fuera de la Pi. Contiene la clave de red: trátalo como un secreto.

### Restauración en un host nuevo

1. Instala Docker, Mosquitto y paho-mqtt (paso 2).
2. Copia el proyecto y restaura `config/` desde el respaldo.
3. Conecta **el mismo dongle** y fija el puerto con by-id.
4. `docker compose up -d` y comprueba `Currently N devices are joined`.
5. Verifica `list` y `status`.

### Volver atrás (rollback)

Mientras el host viejo conserve su `config/`, basta con apagar el contenedor en la Pi
y levantarlo en el host original. **Nunca tengas los dos coordinadores encendidos a la
vez**: dos redes Zigbee con la misma clave y distinto coordinador confunden a los
dispositivos.

---

## 6. Checklist de migración

- [ ] `md5sum` de `database.db` y `coordinator_backup.json` idénticos a la fuente.
- [ ] Dongle mapeado por `by-id`, no por `/dev/ttyUSB0`.
- [ ] `docker`, `mosquitto`, `zigbee2mqtt` → `enabled`.
- [ ] Logs: `Currently N devices are joined` (sin re-emparejar).
- [ ] `valvulas.py list` muestra válvulas + coordinador.
- [ ] `open` / `close` devuelven confirmación real.
- [ ] Frontend en `http://<IP_DEL_HOST>:8099` responde.
- [ ] Reinicio limpio: todo vuelve solo y las válvulas hacen check-in en 2-5 min.
- [ ] Respaldo de `config/` guardado fuera de la Pi.

---

## 7. Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `Sin confirmación real ... en XXs` | Válvula dormida | Fix del sueño (§4); esperar y reintentar |
| `status` o menú muestran `?` | Dormida o boot reciente | Esperar 2-5 min (check-in) o pulsar el botón |
| `?` justo tras reiniciar | End devices aún sin reconectar | Esperar; tecla `R` en el menú |
| `Coordinator failed to start` | Dongle sin firmware correcto o permisos | Revisar `dialout`, `ls -l /dev/ttyUSB0`; flashear EZSP |
| El contenedor no arranca al boot | Orden de servicios | `Requires=docker.service`, `After=...` |
| MQTT no responde | Mosquitto caído o expuesto en otra interfaz | `systemctl status mosquitto`; confirmar `listener 1883 127.0.0.1` |
| `docker compose` pide `sudo` | Sesión sin grupo `docker` | Reabrir sesión SSH |
| Válvula reporta `water_shortage` | Sin presión en esa línea | Informativo, no bloquea |
| Dos hosts con el mismo `config/` | Conflicto de coordinador | Dejar solo uno activo |

---

## 8. Notas de seguridad del despliegue

- El broker escucha **solo en `127.0.0.1`**: no lo cambies sin añadir autenticación.
- El frontend de Zigbee2MQTT queda accesible en la LAN **sin login**. Si no lo usas,
  pon `frontend.enabled: false`.
- No copies `config/` a repositorios ni a la nube: contiene `network_key`, `pan_id` e
  identificadores IEEE.
- No dejes `permit_join` activo tras emparejar.
- Si el host se administra en remoto, usa claves SSH y desactiva el acceso por
  contraseña.
