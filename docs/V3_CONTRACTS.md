# Contratos de implementación v3

Estos contratos coordinan el trabajo. `omi.models` es la definición ejecutable;
proponer ampliaciones al principal antes de modificarla. Python >=3.11.

## Almacenamiento común (principal)

`Store(path: Path)` SQLite, thread-safe, transacciones; `get(key, default=None)`,
`put(key,value)`, `delete(key)`, `items(prefix='')`, `close()` para valores JSON.
Los nombres de claves separan dominios. `Library(store)`:

- `export() -> list[dict]`: todas las revisiones, incluyendo tombstones.
- `merge(revisions: list[dict]) -> dict`: valida el lote completo y une sin perder
  ni cambiar revisiones existentes; rechaza misma ID con contenido diferente.
- `digest() -> str`: hash estable de la biblioteca.
- `list() -> list[dict]`: `{preset_id,name,heads:[revisionID],conflict,deleted,
  revision:dict}`; revision es representativa, NO un ganador automático.
- `get(revision_id) -> Revision`, `heads(preset_id) -> list[Revision]`.
- `save(request: SavePreset, author='local') -> Revision`: padres explícitos;
  nueva creación sin preset_id. No sobrescribe ninguna revisión.
- `resolve(preset_id, chosen_revision, author='local') -> Revision`: nuevo merge
  con TODOS los heads como padres y contenido del elegido.
- `delete(preset_id, used_by: list[str]) -> Revision`: bloquea si usado; tombstone.
  El llamante obtiene used_by de ejecución/asignación conocida, incluso offline.

Toda API devuelve JSON. Errores de validación 422, conflicto/ocupado 409,
ausente 404, offline/no control local 503/423. Sin autenticación (LAN acordada).

## Servicio operativo (responsable servicios)

`omi.services.host.ServiceHost(data_dir: Path, simulated: bool=False,
satellite_dir: Path|None=None)` importa sin hardware. Métodos async:

- `start(config: PresetConfig) -> dict` espera readiness REAL o error acotado.
- `stop() -> None` detiene proceso y descendientes con plazos.
- `apply(config: PresetConfig) -> None` aplica cambios MIDI compatibles en caliente.
- `inputs() -> list[str]` entradas disponibles, no cambia selección.
- `open_editor(session_id: str, config: PresetConfig) -> None`: MIDI activo:
  silencia todo; standby: captura MIDI sin emitir OSC (no Satellite). El principal
  solo llama este método para editor de rutas MIDI. Se admite un solo editor.
- `close_editor(session_id: str) -> None`: libera mute/captura, sin replay.
- `arm_learn(session_id: str) -> None`: descarta preview anterior y captura primero.
- `inject_midi(message: dict) -> None`: solo simulación; la producción rechaza.

Métodos síncronos, no bloqueantes: `status() -> dict` con `health` (ready/waiting/
error/standby), `detail`, métricas opcionales; `learn_result(session_id) -> dict|None`.
Host mantiene estado de editor durante stop/start por guardar configuración:
el nuevo worker debe nacer silenciado si sigue abierto el editor. El cierre del
agente llama close_editor y stop. No importa servidor/cliente. Las tareas de
supervisión deben terminar al parar. Ninguna instalación/descarga en start.

## Agente HTTP (principal; 8081)

`create_app(runtime)`; fábrica de runtime y CLI principal. Endpoints de control
siempre activos. Interfaz humana estática compartida bajo `/`, deshabilitada
cuando central online y conforme, salvo sesión local pendiente.

- GET `/api/status`: `{role:'client', serial,hostname,state,local_control,
  connected,device_id,name,simulated}`.
- POST `/api/commands` (Command) -> Operation; GET `/api/operations/{id}`.
- GET `/api/presets`, GET `/api/presets/export`, POST `/api/presets/import`
  (`{revisions:[...]}`), POST `/api/presets` (SavePreset),
  POST `/api/presets/{id}/resolve` (`{revision:...}`), DELETE `/api/presets/{id}`.
- POST `/api/sync` (`{revisions:[...]}`) -> `{revisions:[...],digest}` control.
- GET `/api/midi/inputs` -> `{inputs:[...]}`.
- POST `/api/editors` (EditorRequest) -> `{id,expires_at,learn:null}`.
  POST `/api/editors/{id}/renew`, POST `.../learn`, GET `...`, DELETE `...`.
  Lease 30 s; UI renueva cada 8 s. GET incluye `{id,expires_at,learn}`.
- GET `/api/logs?level=&limit=200` -> `{lines:[...]}`; GET `/api/logs/download`.
- POST `/api/simulate/midi` (dict) solo si simulated.
- GET/PUT `/api/settings`: ajustes locales/manual server/interface/timings.
- Mantenimiento por API: concretar con principal al integrar; UI debe incluir
  limpieza logs, reset temporal, backup/factory (confirmación) y actualización.

Las llamadas proxy del servidor incluyen `X-OMI-Control: central` para distinguir
las solicitudes locales. No es autenticación. El principal controla restricciones
de edición/estado. Para guardar activo en una Pi se usa POST presets contextual:
crea revisión y aplica a ESA Pi; si standby conserva standby.

## Servidor (responsable servidor/web; 8080)

`omi.server.app.create_app(data_dir: Path, *, transport=None, ...)->FastAPI`:
admite inyección de transporte HTTP para pruebas. Usa Store/Library comunes.
El agente hace POST `/api/heartbeat` (Heartbeat) -> HeartbeatReply, NO órdenes.
POST `/api/sync` (`{serial,revisions:[...]}`) -> `{revisions,digest}`: el servidor
solo incorpora biblioteca de Pi onboarded (salvo importación explícita). Una
biblioteca vacía del servidor no ordena borrar la del cliente.

API humana central: GET `/api/status` role server; GET `/api/agents`; POST
`/api/agents/{serial}/onboard` `{name?,import_presets?:bool}`, PATCH agente
`{device_id?,name?}` (intercambia ID ocupado de forma atómica), DELETE agente;
POST `.../accept` y `.../synchronize`; POST `.../commands` Command; POST
`/api/groups/commands` `{serials,command}` devuelve resultado por Pi.
Mantener asignación esperada separada de observada, registrar pending antes de
enviar, rechazar offline/busy, reconciliar IDs/resultados por heartbeat. Sin cola.
Guardar expected ante orden apply/standby aceptada; errores preservan expected
para diagnóstico, actual puede estar en standby con error. Desconexión no cancela
un trabajo vivo ni autoriza nuevos trabajos; consultar estado antes de repetir.

Biblioteca central mismas rutas que cliente; save no aplica. UI Guardar y aplicar
crea revisión y envía apply a los seleccionados. Proxy remoto para editores,
entradas, logs y presets contextual bajo `/api/agents/{serial}/...` (p.ej.
`midi/inputs`, `editors`, `presets`). Sync de biblioteca antes de un apply remoto.

## Web compartida (responsable servidor/web)

HTML/CSS/JS locales en `omi/web/`, sin CDN ni iframes. `/api/status` decide rol.
Interfaz español, campos completos para ambos servicios/red, tabla rutas,
selección MIDI remota, editor/Learn con mute al abrir (sesión ligada a Pi),
revisiones/conflictos/import/export/duplicar/renombrar, tareas/grupos, onboard,
progreso/errores, aceptar/sincronizar, diagnóstico/mantenimiento. JS no debe
sobrescribir borradores durante polling. Separar elegir preset de ejecutar.

## Instalación/pantallas (responsable instalación)

Entradas CLI principal: `omi-agent`, `omi-server`, `omi-maintain`, todos en
`omi.cli`. Agent/server aceptan `--data-dir`, `--host`, `--port`, `--simulate`;
agente `--serial` solo para simulación; servidor `--advertise` y `--no-beacon`.
Configuración persistente `settings` en Store; sistema puede fijar vars OMI_*.
Producción datos `/var/lib/omi/{client,server}`; código `/opt/omi/releases/<id>`
y `/opt/omi/current` symlink. Satellite bajo `/opt/omi/satellite/<version>` con
symlink `/opt/omi/satellite/current`; comunicar host ubicación y comando real.

`omi.display.DisplayManager()` importa sin hardware, `start()` detecta, `render`
recibe dict del GET status, `close()` limpia. Render/sondeo fuera hilo principal.
Rescatar drivers antes de eliminar legado. Principal administra pyproject; enviar
lista de deps necesarias. Scripts instaladores cliente/servidor en scripts/.
No ejecutar instalaciones reales ni modificar red/power del anfitrión.
