# Client

Código del agente que se ejecuta en el dispositivo.

## Requisitos
- Python 3.11+ (sugerido uso de entorno virtual)
- Dependencias listadas en `client/requirements.txt` (si existe) o instala manualmente:
  - `fastapi`, `uvicorn`, `mido`, `python-osc`, `psutil`, `netifaces`, etc.

## Comandos útiles
- Arrancar el agente principal: `python client.py`
- Ejecutar la Web UI MIDI: `python servicios/MIDI/midiwebui.py`
- Probar la UI local: `python Testui.py`

Todos los comandos se ejecutan desde esta carpeta (`client/`) a menos que se indique lo contrario.
