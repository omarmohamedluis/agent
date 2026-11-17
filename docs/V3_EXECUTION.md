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

## Propiedad de archivos durante implementación

| Responsable | Archivos |
| --- | --- |
| Principal | `omi/models.py`, `omi/store.py`, `omi/library.py`, `omi/client/`, `omi/network.py`, `omi/discovery.py`, `omi/cli.py`, `omi/logging.py`, `pyproject.toml`, pruebas correspondientes e integración |
| Servicios | `omi/services/`, `tests/test_midi*`, `tests/test_satellite*`, `tests/test_services*` |
| Servidor y web | `omi/server/`, `omi/web/`, `tests/test_server*`, `tests/test_web*` |
| Instalación y pantallas | `scripts/`, `deploy/`, `omi/display/`, `tests/test_install*`, `tests/test_display*`, README y guías de instalación/desarrollo |

No se edita un archivo ajeno sin acuerdo con su responsable. El principal realiza
los commits y la retirada final de archivos antiguos, después de rescatar drivers.

## Plan y evidencias de aceptación

- [ ] Contratos validados, persistencia atómica, biblioteca vacía, revisiones,
      conflictos, importación/exportación, renombrado y borrado protegido.
- [ ] Coordinador: exclusión, duplicados, fallos, timeout, proceso/red en orden,
      recuperación local, cierre y acciones de alimentación verificadas con fakes.
- [ ] NetworkManager real: gestión separada, VLAN propia, DHCP/IP fija comprobados,
      eliminación restringida, pruebas de errores sin tocar la red anfitriona.
- [ ] MIDI real: selección estable, arranque limitado, reconexión indefinida,
      conversión/canales/todas las rutas, editor/Learn y aplicación en caliente.
- [ ] Ensayos de ráfagas/carga: descartar eventos viejos, sin reintentos ni ráfagas
      posteriores, destinos conservados, métricas y control independientes.
- [ ] Satellite real: versión fijada, instalación preparada, supervisión limitada,
      hijos detenidos y pérdida de Companion sin reiniciar red/servicio.
- [ ] Descubrimiento, identidad, onboard, edición/intercambio de IDs, estado
      esperado/observado, aceptar/sincronizar y autonomía/reconexión.
- [ ] Replicación sin cambiar ejecución, conflictos offline conservados, aplicación
      selectiva, acciones de grupo y rechazo offline/ocupado.
- [ ] Web central/local completa: presets, rutas, captura remota, estado, consola,
      mantenimiento; comprobación práctica de recorridos en navegador simulado.
- [ ] Pantallas opcionales y fallos aislados, mismos datos, franja amarilla.
- [ ] Logs acotados, diagnóstico, limpieza/reset/backup y actualización recuperable.
- [ ] Instaladores por rol, systemd, desarrollo Git/SSH, dependencias reproducibles,
      arranque sin Internet, empaquetado y sintaxis Bash comprobados.
- [ ] Retirada de código/documentación obsoletos y actualización de guías.
- [ ] Revisión independiente, correcciones, suite final, publicación origin/v3,
      matriz de resultados y secuencia física preparada.

Las pruebas físicas en Pi/CT, USB/I2C/VLAN reales y duración de semanas quedan
para la validación posterior. Cualquier funcionalidad sin implementar impediría
marcar esta fase como terminada.

## Progreso

- 2026-11-17: leídos los tres documentos completos, verificados rama y cambios
  locales, definidas responsabilidades y arquitectura inicial.
- Implementados contratos Pydantic, SQLite transaccional y biblioteca DAG,
  coordinador, NetworkManager real, descubrimiento, runtime/API local y CLI.
  Primeras 19 pruebas de estos módulos pasan en Windows Python 3.12.10.
  Diagnóstico offline e interfaces CLI de ambos roles comprobados.
- Subagentes trabajando en servicios, servidor/web e instaladores/pantallas.
  Integración completa, pruebas de navegador, revisión independiente y retirada
  del legado todavía pendientes: este registro no constituye entrega terminada.

## Referencias técnicas y decisiones

- NetworkManager: perfiles y propiedades contrastados con su
  [documentación oficial](https://networkmanager.dev/docs/api/latest/nm-settings-nmcli.html).
  VLAN DHCP sin rutas/DNS automáticos ni ruta por defecto. Si el preset indica
  explícitamente gateway estático, se aplica con métrica 32000 para conservar la
  preferencia de la ruta normal de administración. Esta política debe comprobarse
  con las rutas concretas de la instalación durante la prueba física.
- Captura MIDI Linux por ALSA con timestamps de entrada y envíos UDP no bloqueantes
  en proceso independiente; la implementación y ensayos los desarrolla el agente
  de servicios. No se promete entrega OSC ni latencia física del USB/red.
