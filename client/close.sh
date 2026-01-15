#!/bin/bash

# Script de Cierre del Cliente OMI Agent
# Detiene todos los procesos relacionados con el cliente y sus servicios.

echo "🛑 Deteniendo Cliente OMI Agent y servicios..."

# Verificar si se ejecuta como root para asegurar que podemos matar todos los procesos
if [ "$EUID" -ne 0 ]; then
    echo "⚠️  Nota: No se está ejecutando como root. Es posible que no se puedan detener todos los procesos."
    echo "   Si los procesos persisten, intenta: sudo ./close.sh"
fi

# 0. Detener Servicios en Ejecución
echo "🛑 Enviando señal de terminación a los procesos..."
sudo pkill -f "service.py" 2>/dev/null
sudo pkill -f "midiwebui.py" 2>/dev/null
sudo pkill -f "uvicorn" 2>/dev/null
sudo pkill -f "client.py" 2>/dev/null

echo "✅ Procesos detenidos."
