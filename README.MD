# omi-Agent

Agente REST ligero para Raspberry Pi que permite gestionar roles, servicios y configuraciones de forma centralizada.

---

## 🚀 Características principales
- API REST en **FastAPI**
- Métricas del sistema (CPU, RAM, temperatura, uptime)
- Control de servicios (`systemd`)
- Gestión de roles (`omimidi`, `omiosc`, `satellite`, `companion`, `standby`)
- Guardado de configuraciones en `/etc/omi/roles/`
- Reboot / Shutdown remoto
- Descubrimiento automático por **mDNS**

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
🔁 Crear servicio systemd
bash
Copiar código
sudo tee /etc/systemd/system/omiagent.service >/dev/null <<'EOF'
[Unit]
Description=omiAgent REST
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
GET	/v1/health	Estado y métricas del dispositivo
PUT	/v1/identity	Asignar index y hostname
PUT	/v1/role	Cambiar rol activo
GET	/v1/services	Listar servicios
POST	/v1/services/{name}/{action}	Start/Stop/Restart de servicios
PUT	/v1/configs/{service}	Guardar configuración local
GET	/v1/logs/{name}	Ver logs del servicio
POST	/v1/power/{action}	reboot o shutdown

Documentación interactiva disponible en
🧩 http://<raspberry_ip>:9000/docs

🧠 Permisos sudo
Permitir que el agente maneje servicios y energía sin pedir contraseña:

bash
Copiar código
echo "pi ALL=(ALL) NOPASSWD: /bin/systemctl, /usr/bin/journalctl, /usr/sbin/shutdown, /usr/sbin/reboot, /usr/bin/hostnamectl" | sudo tee /etc/sudoers.d/omiagent
🔍 Descubrimiento mDNS (Avahi)
Crea el archivo /etc/avahi/services/omiagent.service:

xml
Copiar código
<?xml version="1.0" standalone='no'?><!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">%h _omiagent</name>
  <service>
    <type>_omiagent._tcp</type>
    <port>9000</port>
    <txt-record>api=/v1</txt-record>
  </service>
</service-group>
Y reinicia Avahi:

bash
Copiar código
sudo systemctl restart avahi-daemon
🧪 Prueba rápida
bash
Copiar código
curl http://localhost:9000/v1/health
O desde PowerShell (PC central):

powershell
Copiar código
Invoke-RestMethod http://omipi-01.local:9000/v1/health
🧭 Roles disponibles
Rol	Servicio(s) activado(s)	Función
standby	ninguno	Espera asignación
omimidi	omimidi.service	Convierte MIDI → OSC
omiosc	omiosc.service	Controlador OSC
satellite	companion-satellite.service	Companion Satellite
companion	companion.service	Instancia principal de Companion

🧩 Ejemplos de uso de la API
Cambiar rol
bash
Copiar código
curl -X PUT -H "Content-Type: application/json" \
  -d '{"role":"omimidi"}' \
  http://localhost:9000/v1/role
Ver logs de un servicio
bash
Copiar código
curl http://localhost:9000/v1/logs/omimidi
Apagar la Raspberry
bash
Copiar código
curl -X POST http://localhost:9000/v1/power/shutdown
📜 Licencia
MIT © 2025 Omar Mohamed Luis
