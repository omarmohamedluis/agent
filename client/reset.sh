#!/bin/bash

# Script de Reinicio Profundo del Cliente OMI Agent
# Limpia todos los archivos no esenciales, generados e ignorados por git.

echo "🚀 Iniciando limpieza profunda del Cliente OMI Agent..."

# Verificar si se ejecuta como root para ciertas operaciones
if [ "$EUID" -ne 0 ]; then
    echo "⚠️  Nota: No se está ejecutando como root. Algunos archivos del sistema (como pycache propiedad de root) podrían no limpiarse."
    echo "   Si ves errores de 'Permiso denegado', intenta: sudo ./reset.sh"
fi

# 0. Detener Servicios en Ejecución
echo "🛑 Deteniendo servicios en ejecución..."
sudo pkill -f "service.py" 2>/dev/null
sudo pkill -f "midiwebui.py" 2>/dev/null
sudo pkill -f "uvicorn" 2>/dev/null
sudo pkill -f "client.py" 2>/dev/null

# 1. Limpiar Logs
echo "📁 Limpiando logs..."
find . -name "*.log" -type f -delete 2>/dev/null
mkdir -p logs/services logs/components

# 2. Limpiar Pycache y artefactos de Python
echo "🐍 Limpiando artefactos de Python (__pycache__, .pyc, .pyo)..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find . -name "*.pyc" -type f -delete 2>/dev/null
find . -name "*.pyo" -type f -delete 2>/dev/null

# 3. Limpiar archivos de bloqueo (Lock) y PID
echo "🔒 Limpiando archivos lock y PID..."
find . -name "*.lock" -type f -delete 2>/dev/null
find . -name "*.pid" -type f -delete 2>/dev/null

# 4. Reiniciar Estado de Servicio Activo
echo "🔄 Reiniciando estado de servicio activo..."
if [ -f "data/active_service.txt" ]; then
    echo "STANDBY" > data/active_service.txt
fi

# 5. Limpiar configuraciones JSON generadas/locales
echo "📄 Limpiando configuraciones JSON generadas y locales..."
rm -f data/structure.json
rm -f data/server.json
rm -f servicios/servicios.json
rm -f servicios/MIDI/OMIMIDI_map.json
find . -name "*_TEMP.json" -type f -delete 2>/dev/null
rm -f servicios/MIDI/OMIMIDI_state.json
rm -f servicios/MIDI/OMIMIDI_learn_request.json
rm -f servicios/MIDI/OMIMIDI_restart.flag


echo "✨ ¡Listo! El estado de la aplicación está completamente limpio."
echo "💡 Nota: Las configuraciones críticas (servicios.json, OMIMIDI_map.json) han sido respaldadas como archivos .template."
