#!/bin/bash
# OMI Agent - Raspberry Pi Interactive Installer

REPO_URL="https://github.com/omarmohamedluis/agent.git"
INSTALL_DIR="omi-agent"

echo "==========================================="
echo "   OMI Agent - Instalador Interactivo Pi   "
echo "==========================================="

# 1. Obtener ramas disponibles
echo "🔍 Obteniendo lista de ramas..."
RAMAS=$(git ls-remote --heads $REPO_URL | sed 's?.*refs/heads/??')

if [ -z "$RAMAS" ]; then
    echo "❌ Error: No se pudieron obtener las ramas del repositorio."
    exit 1
fi

# 2. Mostrar menú de selección
echo "Selecciona la rama que deseas instalar:"
options=($RAMAS)
for i in "${!options[@]}"; do
    echo "$i) ${options[$i]}"
done

read -p "Introduce el número [default: 0]: " opt
opt=${opt:-0}

SELECTED_BRANCH=${options[$opt]}

if [ -z "$SELECTED_BRANCH" ]; then
    echo "❌ Opción inválida."
    exit 1
fi

echo "🚀 Instalando rama: $SELECTED_BRANCH"

# 3. Clonar repositorio
if [ -d "$INSTALL_DIR" ]; then
    read -p "⚠️ La carpeta $INSTALL_DIR ya existe. ¿Eliminar y re-instalar? (s/n): " confirm
    if [[ $confirm == [sS] ]]; then
        sudo rm -rf "$INSTALL_DIR"
    else
        echo "Abortando instalación."
        exit 0
    fi
fi

git clone -b $SELECTED_BRANCH --recursive $REPO_URL $INSTALL_DIR
cd $INSTALL_DIR

# 4. Instalar dependencias de sistema
echo "📦 Instalando dependencias de sistema (requiere sudo)..."
sudo apt update
sudo apt install -y python3-pip python3-venv network-manager libasound2-dev libjack-jackd2-dev nodejs

# 5. Configurar Python
echo "🐍 Configurando entorno virtual Python..."
python3 -m venv .venv
source .venv/bin/activate
pip install -r client/requirements.txt

# 5.5. Configurar I2C para LCD
echo "📟 Configurando I2C para pantalla LCD..."
CONFIG_FILE="/boot/firmware/config.txt"
if [ ! -f "$CONFIG_FILE" ]; then CONFIG_FILE="/boot/config.txt"; fi

if grep -q "^#dtparam=i2c_arm=on" "$CONFIG_FILE"; then
    sudo sed -i 's/^#dtparam=i2c_arm=on/dtparam=i2c_arm=on/' "$CONFIG_FILE"
elif ! grep -q "^dtparam=i2c_arm=on" "$CONFIG_FILE"; then
    echo "dtparam=i2c_arm=on" | sudo tee -a "$CONFIG_FILE"
fi

if ! grep -q "dtparam=i2c_arm_baudrate=400000" "$CONFIG_FILE"; then
    sudo sed -i "/dtparam=i2c_arm=on/a dtparam=i2c_arm_baudrate=400000" "$CONFIG_FILE"
fi

# 6. Configurar Node.js v24 y compilar Satellite
echo "🤖 Configurando Node.js y compilando Satellite..."
if ! command -v node &> /dev/null || [[ $(node -v) != v24* ]]; then
    echo "📥 Instalando Node.js v24..."
    curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
    sudo apt install -y nodejs
fi

sudo corepack enable
cd client/servicios/satellite/satellite_code
yarn install
yarn build
cd ../../../..

echo "==========================================="
echo "✅ Instalación completada con éxito."
echo "sudo python3 client/client.py"
echo "==========================================="

read -p "🔄 Instalación completada. ¿Deseas reiniciar ahora para aplicar cambios de hardware? (y/n): " reboot_now
if [[ $reboot_now == [yY]* ]]; then
    echo "Reiniciando..."
    sudo reboot
fi
