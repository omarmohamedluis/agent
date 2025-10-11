# omi-Agent

Agente REST ligero para Raspberry Pi que permite gestionar roles, servicios y configuraciones de forma centralizada dentro del ecosistema **omiControl**.

---

## 🚀 Características principales

- API REST basada en **FastAPI**
- Métricas del sistema: CPU, RAM, temperatura, uptime
- Control de servicios gestionados por **systemd**
- Asignación dinámica de roles: `omimidi`, `omiosc`, `satellite`, `companion`, `standby`
- Gestión de configuraciones locales en `/etc/omi/roles/`
- Encendido, reinicio y apagado remoto
- Descubrimiento automático mediante **mDNS (Avahi)**
- Integración con el repositorio [`omi-fleet-config`](https://github.com/omarmohamedluis/omi-fleet-config)

---

## 🧰 Instalación en Raspberry Pi

```bash
sudo apt update && sudo apt -y install python3 python3-pip python3-venv git avahi-daemon
mkdir -p ~/omi && cd ~/omi
git clone https://github.com/omarmohamedluis/agent.git
cd agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
⚙️ Crear servicio systemd
bash

sudo tee /etc/systemd/system/omiagent.service >/dev/null <<'EOF'
[Unit]
Description=omiAgent REST API
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/home/pi/omi/agent
ExecStart=/home/pi/omi/agent/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 9000
User=pi
Restart=always
RestartSec=2
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now omiagent
🧩 Endpoints principales
Método	Ruta	Descripción
GET	/v1/health	Estado general y métricas del sistema
PUT	/v1/identity	Asigna index y hostname a la Raspberry
PUT	/v1/role	Cambia el rol activo (omimidi, omiosc, etc.)
GET	/v1/services	Lista los servicios y su estado
POST	/v1/services/{name}/{action}	Inicia, detiene o reinicia servicios
PUT	/v1/configs/{service}	Guarda configuración local en /etc/omi/roles/
GET	/v1/logs/{name}	Muestra los logs de un servicio
POST	/v1/power/{action}	Reinicia o apaga la Raspberry (reboot / shutdown)

📘 Documentación interactiva disponible en:
http://<raspberry_ip>:9000/docs

🧠 Permisos sudo
Permitir que el agente maneje servicios y energía sin pedir contraseña:

bash

echo "pi ALL=(ALL) NOPASSWD: /bin/systemctl, /usr/bin/journalctl, /usr/sbin/shutdown, /usr/sbin/reboot, /usr/bin/hostnamectl" | sudo tee /etc/sudoers.d/omiagent
🔍 Descubrimiento mDNS (Avahi)
Permite encontrar automáticamente cada Raspberry Pi por su nombre (omipi-01.local, etc.)

Crea /etc/avahi/services/omiagent.service:

xml

<?xml version="1.0" standalone='no'?><!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">%h _omiagent</name>
  <service>
    <type>_omiagent._tcp</type>
    <port>9000</port>
    <txt-record>api=/v1</txt-record>
  </service>
</service-group>
Y reinicia el servicio:

bash

sudo systemctl restart avahi-daemon
🧪 Prueba rápida
Localmente:
bash

curl http://localhost:9000/v1/health
Desde el PC central:
powershell

Invoke-RestMethod http://omipi-01.local:9000/v1/health
🧭 Roles disponibles
Rol	Servicio(s) activado(s)	Descripción
standby	Ninguno	Estado inactivo esperando asignación
omimidi	omimidi.service	Convierte mensajes MIDI a OSC
omiosc	omiosc.service	Controlador de señales OSC
satellite	companion-satellite.service	Modo Companion Satellite
companion	companion.service	Instancia principal de Companion

🧩 Ejemplos de uso
Cambiar rol
bash

curl -X PUT -H "Content-Type: application/json" \
  -d '{"role":"omimidi"}' \
  http://localhost:9000/v1/role
Ver logs
bash

curl http://localhost:9000/v1/logs/omimidi
Apagar la Raspberry
bash

curl -X POST http://localhost:9000/v1/power/shutdown
🔗 Integración con omi-Fleet-Config
El agente puede sincronizar configuraciones directamente desde el repositorio
omi-fleet-config
para aplicar parámetros de show y roles definidos.

📜 Licencia
MIT © 2025 Omar Mohamed Luis

