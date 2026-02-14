#!/bin/bash

# Script de Cierre del Servidor OMI Agent
# Detiene todos los procesos relacionados con el servidor.

echo "🛑 Deteniendo Servidor OMI Agent..."

# Verificar si se ejecuta como root
if [ "$EUID" -ne 0 ]; then
    echo "⚠️  Nota: No se está ejecutando como root. Es posible que no se puedan detener todos los procesos."
fi

# 1. Intentar cierre elegante de app.py
SERVER_PID=$(pgrep -f "server/app.py")
if [ ! -z "$SERVER_PID" ]; then
    echo "⏳ Enviando señal de terminación al Servidor (PID $SERVER_PID)..."
    sudo kill -15 $SERVER_PID 2>/dev/null
    
    # Esperar hasta 5 segundos
    for i in {1..5}; do
        if ! pgrep -f "server/app.py" > /dev/null; then
            echo "✅ El servidor se cerró correctamente."
            break
        fi
        sleep 1
    done
fi

# 2. Limpieza forzosa de remanentes
echo "🧹 Asegurando cierre de procesos del servidor restantes..."
sudo pkill -f "server/app.py" 2>/dev/null
sudo pkill -f "uvicorn" 2>/dev/null

echo "✅ Todos los procesos del servidor detenidos."
