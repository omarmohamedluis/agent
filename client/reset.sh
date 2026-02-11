#!/bin/bash

# Asegurar que se ejecuta desde el directorio del script
cd "$(dirname "$0")"

# Script de Reinicio Profundo del Cliente OMI Agent
# Limpia todos los archivos no esenciales, generados e ignorados por git.

echo "🚀 Iniciando limpieza profunda del Cliente OMI Agent..."

# Verificar si se ejecuta como root para ciertas operaciones
if [ "$EUID" -ne 0 ]; then
    echo "⚠️  Nota: No se está ejecutando como root. Algunos archivos del sistema (como pycache propiedad de root) podrían no limpiarse."
    echo "   Si ves errores de 'Permiso denegado', intenta: sudo ./reset.sh"
fi

# 0. Detener Servicios en Ejecución
# 0. Detener Servicios en Ejecución
echo "🛑 Deteniendo servicios en ejecución..."
# Usar close.sh para asegurar cierre elegante y consistente
if [ -f "./close.sh" ]; then
    ./close.sh
else
    echo "⚠️  No se encontró close.sh, usando método forzoso..."
    sudo pkill -f "service.py" 2>/dev/null
    sudo pkill -f "midiwebui.py" 2>/dev/null
    sudo pkill -f "uvicorn" 2>/dev/null
    sudo pkill -9 -f "client.py" 2>/dev/null
fi

# 1. Limpiar Logs
echo "📁 Limpiando logs..."
sudo find . -name "*.log" -type f -delete 2>/dev/null
sudo mkdir -p logs/services logs/components
sudo chmod -R 777 logs 2>/dev/null

# 2. Limpiar Pycache y artefactos de Python
echo "🐍 Limpiando artefactos de Python (__pycache__, .pyc, .pyo)..."
sudo find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
sudo find . -name "*.pyc" -type f -delete 2>/dev/null
sudo find . -name "*.pyo" -type f -delete 2>/dev/null

# 3. Limpiar archivos de bloqueo (Lock) y PID
echo "🔒 Limpiando archivos lock y PID..."
sudo find . -name "*.lock" -type f -delete 2>/dev/null
sudo find . -name "*.pid" -type f -delete 2>/dev/null

# 4. Reiniciar Estado de Servicio Activo
echo "🔄 Reiniciando estado de servicio activo..."
if [ -f "data/active_service.txt" ]; then
    echo "STANDBY" | sudo tee data/active_service.txt > /dev/null
fi

# 5. Limpiar configuraciones JSON generadas/locales
echo "📄 Limpiando configuraciones JSON generadas y locales..."
sudo rm -f data/structure.json
sudo rm -f data/structure.json.lock
sudo rm -f data/server.json
sudo rm -f servicios/servicios.json
sudo rm -f servicios/MIDI/OMIMIDI_map.json
sudo find . -name "*_TEMP.json" -type f -delete 2>/dev/null
sudo rm -f servicios/MIDI/OMIMIDI_state.json
sudo rm -f servicios/MIDI/OMIMIDI_learn_request.json
sudo rm -f servicios/MIDI/OMIMIDI_restart.flag
sudo rm -f servicios/MIDI/active_config.txt

# 6. Limpiar Satellite Service (basurita y binarios)
echo "🛰️  Limpiando archivos de Satellite (binarios, código y estados)..."
sudo rm -rf servicios/satellite/satellite_code
sudo rm -rf servicios/satellite/fnm_data
sudo rm -rf servicios/satellite/bin
sudo rm -rf servicios/satellite/logs
sudo rm -f servicios/satellite/runtime_config.json
sudo rm -f servicios/satellite/service_state.json
sudo rm -f servicios/satellite/install.flag
sudo rm -f servicios/satellite/restart_satellite.flag
sudo rm -f servicios/satellite/active_config.txt

# Limpiar configuraciones de servicios (MIDI y Satellite)
echo "🎹 Limpiando configuraciones de MIDI y Satellite (manteniendo templates)..."
sudo find servicios/MIDI/configs -name "*.json" ! -name "*.template.json" -type f -delete 2>/dev/null
sudo find servicios/satellite/configs -name "*.json" ! -name "*.template.json" -type f -delete 2>/dev/null

# Limpieza adicional detallada
echo "🧹 Limpiando cachés y configuraciones activas remanentes..."
sudo rm -f servicios/MIDI/active_config.txt
sudo rm -rf servicios/MIDI/src/__pycache__
sudo rm -rf src/__pycache__
sudo rm -rf src/displays/__pycache__




echo "✨ ¡Listo! El estado de la aplicación está completamente limpio."
echo "💡 Nota: Las configuraciones críticas (servicios.json, OMIMIDI_map.json) han sido respaldadas como archivos .template."

# Restaurar configuración de terminal
stty sane

