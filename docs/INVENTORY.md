# Repository inventory (refactor baseline)

## Directories
- `client/`
  - `client.py`: agent runtime (to be modularised).
  - `AppHandler.py`: service launcher/manager.
  - `agent/hardware.py`: stub para futuros triggers físicos (apagar/reiniciar).
  - `jsonconfig.py`: structure/config helpers.
  - `logger.py`: logging utilities (agent + services).
  - `servicios/`: per-service packages (MIDI, OSCnum, etc.).
    - `MIDI/web/static/`: activos CSS/JS servidos por FastAPI (sin scripts inline).
  - `logs/`: runtime logs (ignored by git).
  - `agent_pi/data/`: persistent device state (structure.json, etc.).
- `server/`
  - `app/`: FastAPI application modules (settings, broadcast, routes, registry, web utilities).
    - `network.py`: stub de gestor de perfiles de red (persistencia + ACK).
  - `web/`: HTML + static assets for server UI.
  - `db.py`: SQLite persistence helpers.
  - `omi_server.py`: entry point (imports server.app.create_app).
  - `logs/`: runtime server logs.
- `docs/`: documentation (inventory/checklists).

## Entry points / scripts
- `client/client.py` — executed on the Raspberry Pi agent.
- `client/servicios/*/service.py` — individual service launchers.
- `server/omi_server.py` — FastAPI server entry.
- `client/servicios/MIDI/omimidi_core.py` — MIDI core loop, invoked by MIDI service.

## Generated data / config
- `client/agent_pi/data/structure.json` — device identity + enabled services.
- `client/agent_pi/data/server.json` — last known server info (API URL, serial, host).
- `client/servicios/MIDI/OMIMIDI_*.json` — runtime state for MIDI learn/state.
- `client/logs/`, `server/logs/` — runtime logs (rotating).

## Logging overview
- `logger.get_agent_logger()` → `client/logs/agent.log` + consola.
- Servicios agregan stdout/stderr en un único `logs/services/<service>.log` rotativo (configurable por manifiesto).
- `omimidi_core` y la Web UI registran advertencias adicionales en `logs/services/midi.log`.
- El servidor central escribe en `server/logs/server.log` mediante `logger.get_server_logger()`.

## HTTP API summary (server)
- `GET /` — server dashboard (FastAPI + HTML template).
- `GET /api/devices` — runtime devices + desired settings.
- `GET /api/clients` — persisted desired device entries.
- `POST /api/devices/{serial}/service` — request service change.
- `POST /api/devices/{serial}/power` — agent reboot/shutdown.
- `GET/POST/DELETE /api/configs/<service>` — manage stored service configs.
- `PUT/DELETE /api/devices/{serial}` — update desired config metadata.
- `POST /api/devices/{serial}/network/ack` — stub de confirmación de perfiles de red aplicados por el agente.

## Deployment assumptions
- Pi agent runs standalone; server is optional for orchestration.
- FastAPI serves UIs directly (no external web server).
- Future roadmap: per-service VLAN/DHCP configuration, hardware triggers (shutdown button).
- Manual test matrix disponible en `docs/TEST_MATRIX.md`.
