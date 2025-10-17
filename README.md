# OMI Agent Monorepo

Este repositorio agrupa el cliente embebido de OMI y el futuro código del servidor bajo un mismo árbol.

## Estructura
- `client/`: código y recursos del agente que corre en el dispositivo (incluye servicios, UI y utilidades).
- `server/`: espacio reservado para el backend; añade aquí el código del servidor cuando lo tengas listo.
- `.vscode/`, `.venv/`: configuración de desarrollo local y entorno virtual (opcional).

## Flujos habituales
- Ejecutar el agente: `python client/client.py`
- Levantar la Web UI MIDI: `python client/servicios/MIDI/midiwebui.py`

Cada subproyecto mantiene sus dependencias y scripts dentro de su carpeta. Añade un `README.md` específico (por ejemplo `client/README.md` y `server/README.md`) con instrucciones detalladas según vayas completando cada lado del sistema.
