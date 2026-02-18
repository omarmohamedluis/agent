# Guía de Instalación OMI Agent

Este documento detalla los pasos para instalar el Cliente (Raspberry Pi) y el Servidor (Windows) del sistema OMI Agent.

---

## 🛰️ Cliente (Raspberry Pi)

Sigue estos pasos en una Raspberry Pi con el sistema operativo (Raspberry Pi OS Lite 64-bit recomendado) recién instalado.

### 1. Actualizar el sistema
```bash
sudo apt update && sudo apt upgrade -y
```

### 2. Instalar el repositorio y submódulos
```bash
# Instalar git si no está presente
sudo apt install git -y

# Clonar repositorio
git clone https://github.com/omarmohamedluis/agent.git omi-agent
cd omi-agent

# Inicializar submódulos (Satellite)
git submodule update --init --recursive
```

### 3. Instalar dependencias del Cliente
```bash
# Instalar dependencias de sistema y Python (incluye librerías para compilar python-rtmidi)
sudo apt install -y python3-pip python3-venv network-manager libasound2-dev libjack-jackd2-dev

# Crear y activar entorno virtual
python3 -m venv .venv
source .venv/bin/activate

# Instalar librerías de Python desde el archivo de requerimientos
pip install -r client/requirements.txt
```

### 4. Compilar y preparar Satellite
Satellite requiere Node.js v24 y Yarn. Aunque el servicio lo intenta configurar automáticamente, puedes prepararlo manualmente:

```bash
cd client/servicios/satellite/satellite_code

# Habilitar Yarn vía Corepack
sudo corepack enable

# Instalar y compilar
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
