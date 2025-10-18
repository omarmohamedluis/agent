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

## Logs del agente y servicios
- `client/logger.py` centraliza la configuración. El agente escribe en `logs/agent.log`.
- Cada servicio agrega stdout/stderr a un único archivo rotativo `logs/services/<service>.log`.
- Los manifiestos de servicios (`servicios/sercivios.json`) pueden sobreescribir la ruta del log si necesitan un destino concreto.

## Stubs de hardware
El módulo `agent/hardware.py` define un stub para el botón físico de apagado. Durante el arranque (`AgentRuntime.bootstrap`) se registra un callback que, por ahora, solo deja constancia en los logs. Cuando exista la integración con GPIO se reutilizará la misma interfaz (`register_shutdown_callback`).
