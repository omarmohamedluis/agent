# Evidencias de validación v3

Esta fase entrega implementación e integración para empezar la validación física.
No equivale a una instalación probada en Pi 5/CT ni a una prueba de duración.
Código de referencia: `9be663077ed80e90ed93dc6159a6191c31a93133`; los commits
posteriores de documentación no cambian ese código.

## Ejecuciones realizadas

| Entorno | Comprobación | Resultado |
| --- | --- | --- |
| Windows, Python 3.12.10 | `python -m pytest -q` con Chromium y procesos de servicio sobre adaptadores simulados | **167 passed, 2 skipped**, 81.16 s |
| Ubuntu 24.04, Python 3.11 y 3.12 | Suite completa, Chromium, dependencias y wheel/sdist | Ambos trabajos de GitHub Actions aprobados |
| Ubuntu 24.04 | Preparar Satellite oficial amd64 y ejecutar `scripts/smoke-satellite.py` | Arranque, espera sin Companion, parada, segundo arranque y limpieza correctos |
| Windows | `python -m build --no-isolation`, `pip check`, importaciones/recursos del wheel | Correctos |
| Git Bash / Linux Bash | Sintaxis y transacciones de instaladores con systemctl sustituido y HTTP de prueba real | Correctos; incluidos en pytest |
| Windows | `node --check omi/web/app.js` | Correcto |
| Revisión independiente | Servicios, coordinación, integración, instalación, rollback y factory reset | Sin defectos bloqueantes abiertos tras corregir y repetir las pruebas afectadas |

Las dos omisiones Windows son las pruebas de grupos de procesos POSIX y
TIME_WAIT de Linux; se ejecutan en los trabajos Linux. Los cuatro recorridos
de navegador forman parte de la suite, no son revisiones manuales supuestas.

Ejecuciones publicadas, todas aprobadas:

- [Integración inicial ad3f091](https://github.com/omarmohamedluis/agent/actions/runs/43996842896).
- [Rollback y Satellite real 160aecf](https://github.com/omarmohamedluis/agent/actions/runs/43997773010).
- [Revisión final de código 9be6630](https://github.com/omarmohamedluis/agent/actions/runs/43997958292).

Los archivos upstream de Satellite arm64 y amd64 se descargaron, contrastaron
con los SHA-256 de `deploy/satellite-lock.json` y extrajeron verificando integridad
ASAR. La ejecución nativa automatizada corresponde a **amd64**. La ejecución
arm64, su USB y la compatibilidad con el Stream Deck concreto se verifican en Pi.

## Matriz de requisitos y pruebas

Los archivos citados están en `tests/`. Los sustitutos ejercitan errores y
secuencias sin cambiar la red, alimentación ni instalaciones del anfitrión.

| Requisito / caso de aceptación | Evidencia automatizada |
| --- | --- |
| Persistencia, biblioteca vacía, nombres sensibles a mayúsculas y revisiones inmutables | `test_library.py`: guardados paralelos, DAG y rechazo atómico de lotes inválidos |
| Corrupción identificable sin destruir la base recuperable | `test_library.py`: archivo conservado y handle liberado |
| Ediciones offline concurrentes y resolución explícita | `test_library.py`, `test_server.py`, `test_client_integration.py`: ambas ramas sobreviven y convergen |
| Guardar sin cambiar ejecuciones; aplicar selectivamente; revisión fijada al reiniciar | `test_coordinator.py`, `test_client_integration.py`, `test_web.py` |
| Borrado bloqueado por uso/asignación, también offline y por import/sync | `test_server.py`: tombstones y resolución de cabeza borrada; `test_library.py` |
| Servidor ausente/recuperado, discrepancia y control local condicional | `test_client_integration.py`: arranque, reconexión y sesión local conservada; `test_server.py`: aceptar/sincronizar |
| Onboard manual, identidad persistente, nombre/ID e intercambio atómico | `test_server.py`, `test_runtime_cli.py` |
| Exclusión, comandos duplicados/caducados y resultados desconocidos | `test_coordinator.py`, `test_server.py`: deduplicación, timeout y recuperación sin reenvío |
| Heartbeat durante cambios; grupos no encolan equipos offline | `test_client_integration.py`, `test_server.py` |
| Parar → retirar VLAN → preparar/verificar IP → arrancar; fallo deja standby | `test_coordinator.py`, `test_network.py`: orden y persistencia, sin vuelta al servicio anterior |
| NetworkManager real; DHCP ausente, IP incorrecta y gestión separada | `test_network.py`: argumentos nmcli, IP, perfiles ajenos preservados y timeout de proceso real |
| Canales, notas/CC, note-off/velocidad cero, tipos y todas las rutas coincidentes | `test_midi.py`, `test_midi_backend.py`: conversiones, filtros y ABI ALSA |
| Editor silencia todo, primer Learn retenido, guardar no libera mute | `test_midi.py`, `test_services_host.py`, `test_client_integration.py`, `test_web.py` |
| Lease caducado y cierre liberan editor sin reproducir eventos | `test_coordinator.py`, `test_midi.py`, `test_services_host.py` |
| Controlador ausente al arrancar: seis comprobaciones; desconexión en uso: esperar al mismo | `test_midi_backend.py`, `test_midi.py`: identidad USB estable y rechazo de sustitución ambigua |
| Ráfagas/carga: descartar tarde, no acumular ni reintentar; conservar destinos | `test_midi.py`: ráfaga, deadline entre rutas/destinos, carga y DNS lento; `test_midi_backend.py`: recuperación de reloj |
| Vecino no resuelto/destino ocupado no retiene OSC ni bloquea otros destinos | `test_midi_neighbours.py`: admisión, gateway/IPv6, expiración y sockets independientes |
| Satellite preparado, configuración central, Companion ausente y caídas limitadas | `test_satellite.py`: REST/config/readiness y recuperación; smoke nativo en CI |
| Parada de hijos/nietos, EOF del agente y reinicio con TIME_WAIT | `test_services_processes.py`, `test_satellite.py`, `test_services_host.py` |
| Web: contextos, borradores, biblioteca, captura remota y mantenimiento | `test_web.py`: cuatro recorridos Chromium con servidor y dos agentes simulados |
| Pantalla opcional, autodetección, vista común y fallo aislado | `test_display.py`: buses sustituidos, formatos de ambos drivers y render bloqueado sin bloquear estado |
| Logs acotados, ajustes persistentes, limpieza/reset y copias recuperables | `test_runtime_cli.py`, `test_library.py`, `test_server.py`; factory no acepta omitir copia |
| Instalación identificada, sin descargas al arrancar, rollback con datos y verificación | `test_installation.py`: bootstrap, hashes/ASAR, systemd, parada fallida y readiness HTTP por commit/objetivo |
| Actualización conserva exclusión a través del reinicio y timeout del instalador | `test_coordinator.py`, `test_update_readiness.py`: estado faltante, unidad viva y resultado original |

## Límites y siguiente fase

No se han ejecutado instalaciones completas en la Pi 5 ni en un CT Debian 12,
acciones de alimentación reales, pruebas eléctricas I2C, DHCP/VLAN reales,
controladores USB, Stream Deck físico ni una o dos semanas de funcionamiento.
Las implementaciones reales están preparadas; lo pendiente es comprobarlas con
esos equipos siguiendo [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md).

El plazo MIDI de 10 ms controla admisión y descartes en la aplicación. Linux,
USB y red pueden tener buffers posteriores al envío que la aplicación no puede
retirar. No se promete latencia física máxima ni entrega garantizada por UDP;
las mediciones del banco deben determinar la latencia y estabilidad reales.
Una IP fija se aplica como figura en el preset; detectar conflictos entre Pi
se aplazó expresamente. Wi-Fi, autenticación/roles y migración v2 quedan fuera
del alcance acordado. No se ha publicado una release estable ni modificado main.
