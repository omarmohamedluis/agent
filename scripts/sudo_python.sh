#!/bin/bash

if [ -f "$PROJECT_ROOT/client/close.sh" ]; then
    echo "🧹 Limpiando procesos del cliente previos..."
    bash "$PROJECT_ROOT/client/close.sh"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"
exec sudo -E "$PROJECT_ROOT/.venv/bin/python" "$@"
