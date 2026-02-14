#!/bin/bash

# Asegurar que se ejecuta desde el directorio del script
cd "$(dirname "$0")"

# Script de Reinicio Profundo del Servidor OMI Agent
# Limpia todos los archivos no esenciales y generados del servidor.

echo "🚀 Iniciando limpieza profunda del Servidor OMI Agent..."

# Verificar si se ejecuta como root
if [ "$EUID" -ne 0 ]; then
    echo "⚠️  Nota: No se está ejecutando como root. Es posible que algunos archivos no se puedan limpiar."
fi

# 1. Detener el servidor
echo "🛑 Deteniendo el servidor..."
if [ -f "./close.sh" ]; then
    bash ./close.sh
else
    echo "⚠️  No se encontró close.sh, usando pkill..."
    sudo pkill -f "server/app.py" 2>/dev/null
fi

# 2. Limpiar Logs
echo "📁 Limpiando logs del servidor..."
sudo rm -rf logs
mkdir -p logs/server logs/storage logs/services
sudo chmod -R 777 logs 2>/dev/null

# 3. Limpiar Datos Generados
echo "📄 Limpiando datos del servidor (agents.json, configs.json)..."
sudo rm -rf data
mkdir -p data
# Opcional: Reinstalar archivos base si existieran plantillas en el servidor
# pero aquí los manejamos por código al arrancar.

# 4. Limpiar Pycache
echo "🐍 Limpiando artefactos de Python (__pycache__)..."
sudo find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null

echo "✨ ¡Listo! El Servidor OMI Agent está completamente limpio."
