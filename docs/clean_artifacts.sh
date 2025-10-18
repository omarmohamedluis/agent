#!/usr/bin/env bash
#
# clean_artifacts.sh
# -------------------
# Elimina los artefactos generados durante la ejecución (bases de datos,
# logs, JSON de estado, __pycache__). Puede ejecutarse en cualquier momento
# para dejar el repositorio listo para un arranque en limpio.
#
# Uso:
#   ./docs/clean_artifacts.sh
#

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

info() {
  printf '[ clean ] %s\n' "$1"
}

remove_path() {
  local target="$1"
  if [ -e "$target" ]; then
    rm -rf "$target"
    info "Eliminado $target"
  else
    info "No existe $target (ok)"
  fi
}

# Artefactos de servidor
remove_path "$REPO_ROOT/server/omi.db"
remove_path "$REPO_ROOT/server/logs"

# Artefactos del agente / cliente
remove_path "$REPO_ROOT/client/logs"
remove_path "$REPO_ROOT/client/agent_pi/data/structure.json"
remove_path "$REPO_ROOT/client/agent_pi/data/server.json"

# Artefactos del servicio MIDI
remove_path "$REPO_ROOT/client/servicios/MIDI/OMIMIDI_map.json"
remove_path "$REPO_ROOT/client/servicios/MIDI/OMIMIDI_state.json"
remove_path "$REPO_ROOT/client/servicios/MIDI/OMIMIDI_last_event.json"
remove_path "$REPO_ROOT/client/servicios/MIDI/OMIMIDI_learn_request.json"
remove_path "$REPO_ROOT/client/servicios/MIDI/OMIMIDI_restart.flag"
remove_path "$REPO_ROOT/client/servicios/MIDI/OMIMIDI_webui.pid"

# __pycache__ dispersos
info "Eliminando directorios __pycache__"
find "$REPO_ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} +

info "Limpieza completada."
