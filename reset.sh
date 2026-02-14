#!/bin/bash

# Script Global de Reinicio OMI Agent
# Limpia profundamente tanto el Servidor como el Cliente.

echo "🚀 Iniciando REINICIO GLOBAL de OMI Agent..."

# 1. Reiniciar Servidor
if [ -f "./server/reset.sh" ]; then
    bash ./server/reset.sh
else
    echo "⚠️  No se encontró server/reset.sh"
    # Fallback si el script existiera pero fallara la ruta relativa
    if [ -f "server/close.sh" ]; then
        bash server/close.sh
        sudo rm -rf server/logs server/data
    fi
fi

# 2. Reiniciar Cliente
if [ -f "./client/reset.sh" ]; then
    bash ./client/reset.sh
else
    echo "⚠️  No se encontró client/reset.sh"
fi

echo "✨ Sistema de OMI Agent completamente reseteado."
