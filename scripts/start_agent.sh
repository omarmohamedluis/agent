#!/bin/bash
# OMI Agent - Script de arranque automático

# Obtener la ruta del directorio del script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# El directorio raíz del agente es el padre de scripts/
AGENT_DIR="$( dirname "$SCRIPT_DIR" )"

cd "$AGENT_DIR"

# Activar el entorno virtual si existe
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Ejecutar el cliente
# Nota: Si se ejecuta desde crontab de root, no necesita sudo
python3 client/client.py
