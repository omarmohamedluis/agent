# Guía de Instalación OMI Agent

Este documento detalla los pasos para instalar el Cliente (Raspberry Pi) y el Servidor (Windows) del sistema OMI Agent.

---

## 🛰️ Cliente (Raspberry Pi)

Sigue estos pasos en una Raspberry Pi con el sistema operativo (Raspberry Pi OS Lite 64-bit recomendado) recién instalado.

### Opción A: Instalación Automática (Recomendada)

#### Si YA has clonado el repositorio:
Si ya estás dentro de la carpeta `omi-agent`, simplemente ejecuta:
```bash
./scripts/install_pi.sh
```

#### Si es una instalación desde CERO (sin clonar):*
```bash
# Descargar y ejecutar el instalador
curl -fsSL https://raw.githubusercontent.com/omarmohamedluis/agent/v2-server-implementation/scripts/install_pi.sh -o install_pi.sh
chmod +x install_pi.sh
./install_pi.sh
```
*El script te preguntará qué rama instalar y configurará todo automáticamente.*

### Opción B: Instalación Manual
Si prefieres hacerlo paso a paso:

#### 1. Actualizar e instalar dependencias base
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3-pip python3-venv network-manager libasound2-dev libjack-jackd2-dev
```

#### 2. Clonar repositorio (Interactivo)
```bash
read -p "Introduce la rama a descargar (v2, main, etc.) [default: v2]: " BRANCH
BRANCH=${BRANCH:-v2}
git clone -b $BRANCH --recursive https://github.com/omarmohamedluis/agent.git omi-agent
cd omi-agent
```

#### 3. Configurar Python
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r client/requirements.txt
```

#### 4. Compilar Satellite (Node.js)
```bash
# Instalar Node.js v24
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt install -y nodejs
sudo corepack enable

# Compilar
cd client/servicios/satellite/satellite_code
yarn install
yarn build
```

---

## 💻 Servidor (Windows)

El servidor central se ejecuta en Windows para gestionar los agentes.

### 1. Instalar Python
Descarga e instala la última versión de **Python 3.10+** desde [python.org](https://www.python.org/). Asegúrate de marcar la casilla **"Add Python to PATH"** durante la instalación.

### 2. Instalar dependencias
Abre una terminal (PowerShell o CMD) en la carpeta del proyecto y ejecuta:
```powershell
pip install -r server/requirements.txt
```

### 3. Ejecutar el Servidor
```powershell
python server/app.py
```
El servidor estará disponible en `http://localhost:9000`.

---

## 🚀 Inicio Rápido (Cliente)

Para iniciar el cliente tras la instalación:
```bash
cd omi-agent
source .venv/bin/activate
sudo python3 client/client.py
```
*(Nota: Se requiere `sudo` para que el gestor de red pueda configurar las interfaces).*
