# 🧭 SETUP – Instalación completa de omiControl

Guía paso a paso para desplegar el sistema **omiControl**, compuesto por:
- **omi-Agent:** software que corre en cada Raspberry Pi.
- **omi-Fleet-Config:** repositorio central con configuraciones de shows.
- **PC Central:** equipo que gestiona y monitoriza la flota.

---

## 🧩 1. Requisitos

### Hardware
- 1 PC o NUC de control (Windows o Linux)
- 1 o más Raspberry Pi (recomendado modelo 4 o 5)
- Red local con DHCP (IP fija opcional)
- Conectividad por **Ethernet** Y **POE** Para las raspberry pi's es recomendable 

### Software necesario
| En PC central | En Raspberry Pi |
|----------------|------------------|
| Git | Git |
| Visual Studio Code | Python 3.11+ |
| PowerShell o Terminal | pip / venv |
| (Opcional) Apple Bonjour | Avahi-daemon |

En el PC central:
- Git, VSC, apple bonjour

En la raspberry:
Git, python 3.11+, pip / venv, Avahi-Daemon

---

## ⚙️ 2. Preparar el PC central

1. Instalar **Git** y **VS Code**
2. Clonar los repositorios:

```bash
git clone https://github.com/omarmohamedluis/omi-fleet-config.git
git clone https://github.com/omarmohamedluis/agent.git omi-agent
(Opcional) Configurar Bonjour en Windows
Esto permite descubrir las Raspberry Pi automáticamente por nombre (omipi-01.local, etc.).

🍓 3. Preparar una Raspberry Pi
3.1 Instalar el sistema
Instalar Raspberry Pi OS Lite (64-bit) en la tarjeta SD.

Conectarla por SSH o teclado.

Actualizar el sistema:

bash

sudo apt update && sudo apt full-upgrade -y
3.2 Instalar dependencias
bash

sudo apt -y install python3 python3-pip python3-venv git avahi-daemon
sudo systemctl enable --now avahi-daemon
3.3 Clonar e instalar el agente
bash

mkdir -p ~/omi && cd ~/omi
git clone https://github.com/omarmohamedluis/agent.git
cd agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
🧱 4. Crear el servicio systemd
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
Verificar que está activo:

bash

sudo systemctl status omiagent
🔐 5. Permisos sudo
Permitir que el agente ejecute acciones de administración sin pedir contraseña:

bash

echo "pi ALL=(ALL) NOPASSWD: /bin/systemctl, /usr/bin/journalctl, /usr/sbin/shutdown, /usr/sbin/reboot, /usr/bin/hostnamectl" | sudo tee /etc/sudoers.d/omiagent
🔎 6. Configurar mDNS (Avahi)
Crea el archivo /etc/avahi/services/omiagent.service:

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
Ahora podrás descubrir la Raspberry por nombre:
ping omipi-01.local

🧠 7. Prueba de funcionamiento
7.1 Desde la Raspberry
bash

curl http://localhost:9000/v1/health
7.2 Desde el PC central
powershell

Invoke-RestMethod http://omipi-01.local:9000/v1/health
Si responde con un JSON mostrando CPU, RAM, rol, etc., ¡funciona correctamente! 🎉

🧭 8. Asignar identidad y rol
Asignar índice y nombre
bash

curl -X PUT -H "Content-Type: application/json" \
  -d '{"index":1,"hostname":"omipi-01"}' \
  http://localhost:9000/v1/identity
Cambiar rol
bash

curl -X PUT -H "Content-Type: application/json" \
  -d '{"role":"omimidi"}' \
  http://localhost:9000/v1/role
🧩 9. Cargar configuración desde omi-Fleet-Config
Desde el PC central, descarga o clona el repo de configuraciones:

bash

git clone https://github.com/omarmohamedluis/omi-fleet-config.git
Abre el perfil del show (por ejemplo profiles/DEMO_SHOW/).

Envía los archivos de configuración a las Raspberry correspondientes:

bash

curl -X PUT -H "Content-Type: application/json" \
  -d @profiles/DEMO_SHOW/roles/omimidi/map.json \
  http://omipi-01.local:9000/v1/configs/omimidi
🧾 10. Endpoints útiles
Ruta	Descripción
/v1/health	Estado y métricas
/v1/identity	Cambiar index y hostname
/v1/role	Cambiar rol activo
/v1/services	Listar servicios
/v1/power/reboot	Reiniciar
/v1/power/shutdown	Apagar

🧰 11. Mantenimiento y actualización
Ver logs
bash

curl http://localhost:9000/v1/logs/omiagent
Actualizar el agente
bash

cd ~/omi/agent
git pull
sudo systemctl restart omiagent
Actualizar configuraciones del show
bash

cd ~/omi-fleet-config
git pull
🎬 12. Siguiente fase
Una vez que todas las Raspberry estén en red y funcionando,
podrás construir el panel de control web (en el PC central)
para descubrir automáticamente cada omiAgent y asignar roles o configuraciones
de forma visual y en tiempo real.

📜 Licencia
MIT © 2025 Omar Mohamed Luis

yaml


---

### 📍 Dónde colocarlo
Guárdalo en tu repo del **omi-agent**, en la raíz, junto a tu `README.md`, así:

omi-agent/
├── app.py
├── requirements.txt
├── README.md
├── SETUP.md ← aquí
└── ...