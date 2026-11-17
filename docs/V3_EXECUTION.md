# Ejecución de v3

Encargo vigente: `PROMPT_V3.txt`, `REFACTOR.md` y sección 6 de `refactor2.md`.
Rama exclusiva: `v3`. Base preservada en `main` (31851af). El archivo `TODO`
contiene anotaciones locales del usuario y no se modifica durante este trabajo.

## Arquitectura y contratos

Un paquete Python `omi`, instalable en Python >=3.11, con FastAPI para ambos
roles. No hay efectos de hardware al importar módulos. Los datos se guardan fuera
del código, en SQLite con transacciones. Las revisiones de presets son inmutables,
forman un DAG y se replican por unión; los conflictos son varias cabezas. El estado
local de ejecución guarda un objetivo (preset_id, revision) o standby.

El control del agente usa HTTP permanente (puerto 8081); la interfaz humana se
habilita condicionalmente en ese mismo proceso, sin cerrar el canal de control.
El servidor usa 8080 y anuncia su dirección mediante UDP 18080. Heartbeats cada
2 s, offline tras 8 s, espera inicial de descubrimiento 6 s (configurables).
Las órdenes llevan ID y caducidad, se envían directamente a agentes online; no
existe cola offline. Las operaciones de procesos/red se serializan en la Pi.

Los servicios operativos se ejecutan en procesos hijos; configuración y control
por IPC local. MIDI utiliza una ruta de eventos no bloqueante sin cola de trabajo,
independiente de HTTP, persistencia y logs. Las pruebas sustituyen adaptadores,
no las implementaciones Linux de producción.

## Responsabilidades durante implementación

| Responsable | Archivos |
| --- | --- |
| Principal | `omi/models.py`, `omi/store.py`, `omi/library.py`, `omi/client/`, `omi/network.py`, `omi/discovery.py`, `omi/cli.py`, `omi/logging.py`, `pyproject.toml`, pruebas correspondientes e integración |
| Servicios | `omi/services/`, `tests/test_midi*`, `tests/test_satellite*`, `tests/test_services*` |
| Servidor y web | `omi/server/`, `omi/web/`, `tests/test_server*`, `tests/test_web*` |
| Instalación y pantallas | `scripts/`, `deploy/`, `omi/display/`, `tests/test_install*`, `tests/test_display*`, README y guías de instalación/desarrollo |

Los agentes trabajaron con archivos separados. El principal integró los cambios,
realizó los commits y retiró el legado después de rescatar sus drivers útiles.

## Plan y evidencias de aceptación

- [x] Contratos validados, persistencia atómica, biblioteca vacía, revisiones,
      conflictos, importación/exportación, renombrado y borrado protegido.
- [x] Coordinador: exclusión, duplicados, fallos, timeout, proceso/red en orden,
      recuperación local, cierre y acciones de alimentación verificadas con fakes.
- [x] NetworkManager real: gestión separada, VLAN propia, DHCP/IP fija comprobados,
      eliminación restringida, pruebas de errores sin tocar la red anfitriona.
- [x] MIDI real: selección estable, arranque limitado, reconexión indefinida,
      conversión/canales/todas las rutas, editor/Learn y aplicación en caliente.
- [x] Ensayos de ráfagas/carga: descartar eventos viejos, sin reintentos ni ráfagas
      posteriores, destinos conservados, métricas y control independientes.
- [x] Satellite real: versión fijada, instalación preparada, supervisión limitada,
      hijos detenidos y pérdida de Companion sin reiniciar red/servicio.
- [x] Descubrimiento, identidad, onboard, edición/intercambio de IDs, estado
      esperado/observado, aceptar/sincronizar y autonomía/reconexión.
- [x] Replicación sin cambiar ejecución, conflictos offline conservados, aplicación
      selectiva, acciones de grupo y rechazo offline/ocupado.
- [x] Web central/local completa: presets, rutas, captura remota, estado, consola,
      mantenimiento; comprobación práctica de recorridos en navegador simulado.
- [x] Pantallas opcionales y fallos aislados, mismos datos, franja amarilla.
- [x] Logs acotados, diagnóstico, limpieza/reset/backup y actualización recuperable.
- [x] Instaladores por rol, systemd, desarrollo Git/SSH, dependencias reproducibles,
      arranque sin Internet, empaquetado y sintaxis Bash comprobados.
- [x] Retirada de código/documentación obsoletos y actualización de guías.
- [x] Revisión independiente, correcciones, suite final, publicación origin/v3,
      matriz de resultados y secuencia física preparada.

Las marcas significan implementación y comprobación automatizada de esta fase,
no homologación física. Las evidencias y sus límites figuran en la
[matriz de validación](VALIDATION.md). Las pruebas en Pi/CT, USB/I2C/VLAN reales
y duración de semanas siguen la [secuencia física](HARDWARE_VALIDATION.md).
Los adaptadores de producción están implementados; no quedan sustituidos por
simulaciones ni interfaces pendientes de conectar.

## Progreso

- 2026-11-17: leídos los tres documentos completos, verificados rama y cambios
  locales, definidas responsabilidades y arquitectura inicial.
- Implementados contratos Pydantic, SQLite transaccional y biblioteca DAG,
  coordinador, NetworkManager real, descubrimiento, runtime/API local y CLI.
  Primeras 19 pruebas de estos módulos pasan en Windows Python 3.12.10.
  Diagnóstico offline e interfaces CLI de ambos roles comprobados.
- Integrados servicios, servidor/web e instaladores/pantallas. Retirados los
  antiguos `client/`, `server/`, scripts y submódulo Satellite reemplazados.
  Los assets de pantalla rescatados viven en `omi/display/assets/`.
- Pruebas de navegador Chromium con servidor y dos Pi simuladas: edición,
  aplicación selectiva, Learn remoto, silencio completo y borradores conservados.
  Empaquetado, CLI, dependencias y Bash comprobados en Windows y Linux.
- Revisión independiente terminada y correcciones cubiertas por regresiones:
  reloj ALSA tras carga, sockets independientes, readiness de Satellite,
  TIME_WAIT, operaciones desconocidas, persistencia de ajustes, sesiones de
  edición y protección de borrado. También se revisaron exclusión durante
  actualización, verificación de rollback y copias obligatorias de factory reset.
- GitHub Actions ejecutó suites completas en Linux con Python 3.11 y 3.12.
  Un trabajo adicional preparó y arrancó el Satellite real fijado, lo paró y lo
  arrancó de nuevo sin Companion. No quedó un defecto bloqueante conocido en la
  revisión; el resultado no sustituye las pruebas físicas posteriores.

## Referencias técnicas y decisiones

- NetworkManager: perfiles y propiedades contrastados con su
  [documentación oficial](https://networkmanager.dev/docs/api/latest/nm-settings-nmcli.html).
  VLAN DHCP sin rutas/DNS automáticos ni ruta por defecto. Si el preset indica
  explícitamente gateway estático, se aplica con métrica 32000 para conservar la
  preferencia de la ruta normal de administración. Esta política debe comprobarse
  con las rutas concretas de la instalación durante la prueba física.
- Captura MIDI Linux por ALSA con timestamps de entrada y envíos UDP no bloqueantes
  en proceso independiente. Plazo útil inicial de 10 ms, configurable; no hay
  cola de trabajo ni reintento de eventos. DNS y consulta de vecinos quedan fuera
  del recorrido de eventos. No se promete entrega OSC ni tiempo real estricto
  del USB/kernel/red; límites precisos en [SERVICES.md](SERVICES.md).
- Satellite 3.8.0, build 804-8831637 y Node 24.22.2 preparados al instalar.
  Arrancar servicios no descarga dependencias. Si falta Companion se espera;
  una caída del hijo tiene recuperación limitada y limpieza de descendientes.
- Actualización preparada antes de parar; verificación de commit, recuperación
  terminada y objetivo exacto. El rollback también comprueba esos datos y aborta
  antes de restaurar si falla la parada. El bloqueo de actualización sobrevive
  al reinicio intencional y no expira mientras siga vivo el instalador externo.
