# OMI Control Server

FastAPI application that orchestrates OMI agents on the network. It exposes the HTML dashboard and JSON APIs used by the Raspberry Pi clients.

## Requisitos

- Python 3.11+
- Dependencias del servidor (instalar desde la raíz del repositorio):

```bash
pip install -r requirements.txt
```

## Puesta en marcha

Desde la carpeta raíz del repositorio:

```bash
python -m server.omi_server
```

o, en modo desarrollo con recarga automática:

```bash
uvicorn server.omi_server:app --host 0.0.0.0 --port 6969 --reload
```

El panel HTML queda disponible en `http://<host>:6969/`.

## Estructura principal

- `server/app/settings.py` — configuración estática (puertos, TTL, rutas estáticas).
- `server/app/routes.py` — endpoints HTTP/JSON.
- `server/app/broadcast.py` — descubrimiento y comandos UDP hacia los agentes.
- `server/app/registry.py` — registro en memoria de agentes en línea.
- `server/app/network.py` — stub para gestión de perfiles de red por dispositivo.
- `server/app/web.py` — utilidades de renderizado HTML.
- `server/db.py` — capa de persistencia SQLite (dispositivos y presets).

Los assets estáticos y plantillas del panel viven en `server/web/`.

## API destacada

- `GET /api/devices` — estado de los agentes conocidos.
- `PUT /api/devices/{serial}` — actualizar servicio/configuración deseada y perfil de red.
- `POST /api/devices/{serial}/network/ack` — stub para que el agente confirme que aplicó la configuración de red.
- `GET/POST/DELETE /api/configs/{service}` — CRUD de presets por servicio.
- `POST /api/devices/{serial}/service` — solicitar cambio de servicio activo.
- `POST /api/devices/{serial}/power` — solicitar apagado o reinicio del agente.

Los errores se devuelven con una carga JSON consistente (`{"error": "...", "context": {...}}`) y quedan registrados en los logs (`server/logs/server.log`).

## Notas de desarrollo

- Ejecuta `python -m compileall server` para validar sintaxis.
- La base de datos SQLite (`server/omi.db`) se migra en caliente: nuevas columnas se añaden en `init_db()`.
- El `NetworkManager` actual solo persiste perfiles; servirá de punto de integración cuando se implemente la orquestación de VLAN/DHCP.
