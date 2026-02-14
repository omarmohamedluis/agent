#!/bin/bash

# Script Global de Cierre OMI Agent
# Detiene tanto el Servidor como el Cliente.

echo "🛑 Iniciando cierre GLOBAL de OMI Agent..."

# 1. Detener Servidor
if [ -f "./server/close.sh" ]; then
    bash ./server/close.sh
else
    echo "⚠️  No se encontró server/close.sh"
fi

# 2. Detener Cliente
if [ -f "./client/close.sh" ]; then
    bash ./client/close.sh
else
    echo "⚠️  No se encontró client/close.sh"
fi

echo "✅ Sistema de OMI Agent detenido."
