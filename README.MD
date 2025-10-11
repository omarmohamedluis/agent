# omi-agent

Agente ligero para Raspberry Pi que expone una API **REST** para gestionar roles y servicios (`omiMIDI`, `omiOSC`, `Companion` / `Companion Satellite`) y sincronizar configuraciones. Pensado para un **control central** que orquesta una flota de RPi idénticas.

- Backend: **FastAPI** (ASGI)
- Server: **Uvicorn**
- SO recomendado: Raspberry Pi OS Lite (64-bit)
- Orquestación en Pi: **systemd**

## ✨ Qué hace
- `/v1/health` — estado de la Pi (CPU, RAM, temp, rol, servicios…)
- `/v1/identity` — asigna `index` y (opcional) `hostname`
- `/v1/role` — cambia el rol con transición segura (stop→apply→start→verify)
- `/v1/services` y control `start/stop/restart` por servicio
- `/v1/configs/{service}` — guarda configs (JSON) en `/etc/omi/roles/...`
- `/v1/logs/{name}` — tail de journalctl
- `/v1/power/reboot|shutdown` — reinicio/apagado limpio

> **Roles soportados**: `standby`, `omimidi`, `omiosc`, `satellite` (Companion Satellite), `companion` (instancia completa).

---

## 📦 Requisitos en la Raspberry
```bash
sudo apt update && sudo apt -y full-upgrade
sudo apt -y install python3 python3-pip python3-venv git jq avahi-daemon i2c-tools python3-smbus
sudo raspi-config nonint do_i2c 0
sudo systemctl enable --now avahi-daemon


instalacion:
# Carpeta del proyecto
mkdir -p ~/omi
cd ~/omi

# Clona este repo
git clone https://github.com/TU_USUARIO/omi-agent.git agent
cd agent

# Entorno virtual e instalación de dependencias
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt


ejecutar en desarollo:

source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 9000


prueba desde otro equipo:

Invoke-RestMethod http://omipi-01.local:9000/v1/health


para crear el servicio:

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

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now omiagent
sudo systemctl status omiagent --no-pager


