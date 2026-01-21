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
echo "🛑 Iniciando secuencia de apagado..."

# Intentar cierre elegante de client.py
CLIENT_PID=$(pgrep -f "client.py")
if [ ! -z "$CLIENT_PID" ]; then
    echo "⏳ Enviando señal de terminación a client.py (PID $CLIENT_PID)..."
    sudo kill -15 $CLIENT_PID 2>/dev/null
    
    # Esperar hasta 10 segundos
    for i in {1..10}; do
        if ! pgrep -f "client.py" > /dev/null; then
            echo "✅ client.py se cerró correctamente."
            break
        fi
        sleep 1
    done
fi

# Limpieza forzosa de remanentes
echo "🧹 Asegurando cierre de procesos restantes..."
sudo pkill -f "service.py" 2>/dev/null
sudo pkill -f "midiwebui.py" 2>/dev/null
sudo pkill -f "uvicorn" 2>/dev/null
sudo pkill -9 -f "client.py" 2>/dev/null

echo "✅ Todos los procesos detenidos."
