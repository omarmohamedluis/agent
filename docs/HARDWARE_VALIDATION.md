# Validación física v3: Pi 5 y CT Debian 12

Esta secuencia se ejecuta en los equipos de prueba. Las pruebas automatizadas con
sustitutos verifican la lógica, pero no sustituyen USB, I2C, VLAN, DHCP ni la
duración real de funcionamiento. Guardar commit, modelo de controlador/Stream
Deck, versión de Companion, imagen del SO y resultados de cada paso.

## 1. Instalación limpia

1. Grabar Raspberry Pi OS 64 bits normal en una tarjeta nueva, activar SSH y
   conectar Ethernet. Crear CT Debian 12 con systemd y Ethernet en la misma VLAN
   de administración presentada sin etiquetar.
2. Ejecutar los dos comandos publicados de [README.md](../README.md#instalar).
   Anotar el commit que figura en `/opt/omi/current` y consultar `systemctl status`
   y `omi-maintain ... doctor --role ... --offline` según [INSTALL.md](../INSTALL.md).
3. Abrir el CT en 8080. La Pi aparece como descubierta; incorporarla, cambiar
   nombre/ID y comprobar persistencia tras reiniciar únicamente el servicio.
   Verificar que ninguna línea de cron ni otro Satellite la arranca en paralelo.
4. Cortar el acceso a Internet de la red de pruebas y reiniciar ambos equipos de
   forma controlada. Deben arrancar sin descargar dependencias. Restaurar Internet
   únicamente cuando se pruebe una actualización.

## 2. Standby, MIDI y Learn

1. Con biblioteca vacía, verificar standby y mensaje de configuración necesaria.
   Conectar el controlador MIDI, crear preset y seleccionar su nombre exacto.
2. Configurar destinos OSC de laboratorio y rutas para nota, CC, canal concreto,
   Todos, int, float, bool y constante. Comprobar pulsar/soltar, note-on con
   velocidad cero y dos rutas coincidentes hacia la misma dirección OSC.
3. Aplicar el preset. Desde el CT abrir el editor de rutas sin armar Learn: toda
   salida OSC de esa Pi debe quedar silenciada. Armar Learn y mover un control:
   solo el primer evento se retiene. Guardar manteniendo abierto el editor sigue
   en silencio; cerrar o cancelar recupera la salida sin reproducir eventos viejos.
4. Repetir la captura desde standby. Guardar no debe arrancar el servicio.
5. Con MIDI activo, quitar el USB y volverlo a conectar. Debe esperar a ese
   controlador, mantener la VLAN y recuperarlo, sin elegir otro dispositivo.
   Si falta antes del arranque, debe terminar el intento limitado con error.
6. Mover faders rápidamente durante varios minutos, con polling web, logs y sync
   simultáneos. Medir latencia con las marcas del emisor/receptor y contar los
   descartes. Provocar carga controlada: se aceptan descartes, no una ráfaga de
   valores antiguos al finalizar la carga. Fijar el objetivo medido para esta Pi.

## 3. Red de servicio

1. Usar una VLAN de laboratorio etiquetada que no sea la administración. Probar
   DHCP y luego IP/prefijo fijos, gateway/DNS opcionales. Verificar con
   `nmcli connection show --active` e `ip -4 address` la IP real antes de readiness.
2. Cambiar de MIDI a Satellite y observar el progreso: parar servicio, retirar
   VLAN anterior, preparar/verificar IP nueva y arrancar. Probar doble clic y
   órdenes locales/remotas simultáneas: no deben aparecer procesos/VLAN duplicados.
3. Pasar a standby: servicio y VLAN desaparecen; HTTP/administración continúan.
4. Desactivar el DHCP de esa VLAN de pruebas. Aplicar el preset debe fallar con
   causa y plazo definidos, dejar standby con error y conservar Ethernet de
   administración. No debe volver al servicio anterior.

## 4. Satellite y Stream Deck

1. Comprobar `/opt/omi/satellite/current/omi-satellite.json`: 3.8.0 y build
   804-8831637. Conectar un Stream Deck físico y configurar IP/puerto Companion
   desde el preset OMI. Verificar REST y superficie en Companion.
2. Probar botones, imágenes, brillo y ausencia de otra aplicación que abra el
   mismo Stream Deck. Anotar versiones y modelo exactos.
3. Cerrar Companion: Satellite debe esperar y reconectar sin recrear su red.
4. Terminar únicamente el proceso Satellite del equipo de pruebas; comprobar
   recuperación limitada y diagnóstico visible al agotar intentos. Parar OMI y
   verificar que no quedan hijos ni dispositivos retenidos.

## 5. Autonomía, revisiones y discrepancias

1. Guardar una revisión central sin aplicar: las Pi actuales mantienen su revisión.
   Guardar y aplicar solo a una Pi: las demás conservan la anterior.
2. Apagar el servidor de pruebas. El MIDI/OSC continúa y se habilita la web local.
   Editar un preset offline; hacer otra edición central desde la misma base.
   Al reconectar deben conservarse ambas revisiones como conflicto.
3. Dejar la Pi con una revisión diferente de la esperada. Reiniciar su agente:
   recupera la revisión local incluso con CT presente y muestra discrepancia.
   Probar Aceptar y Sincronizar por separado. Una revisión más nueva solo en la
   biblioteca no debe producir naranja si la asignación sigue cumplida.
4. Repetir el arranque con CT ausente, desde standby y después de un cambio
   fallido. La Pi recupera su último estado válido; no resucita el servicio anterior
   al fallo. No se ejecutan órdenes antiguas por volver a estar online.

## 6. Pantallas y mantenimiento

1. Sin pantalla, confirmar funcionamiento completo. Habilitar I2C y probar cada
   modelo disponible: autodetección, estado común, ID, conexión, operación/error
   y franja superior amarilla. Desconectar la pantalla solo de forma eléctricamente
   segura en el banco; el fallo no debe afectar MIDI ni bloquear el agente.
2. Exportar biblioteca, generar backup y limpiar logs. `reset-state` conserva
   presets/ajustes. En CT `reset-installation` conserva biblioteca y elimina
   incorporaciones/asignaciones. Factory reset exige confirmación y deja backup.
   Reincorporar una Pi a CT vacío ofrece importar su biblioteca sin borrarla.
3. Actualizar por SSH y por web. Interrumpir una descarga de prueba: la versión
   anterior continúa. Provocar un fallo de arranque del nuevo paquete en laboratorio
   y comprobar la vuelta de código/datos. Ensayar `rollback.sh --snapshot` offline.
4. Consultar journal y logs de la aplicación: tamaño acotado, errores explícitos y
   ninguna necesidad de descargar paquetes en el siguiente arranque sin Internet.

## 7. Duración

Mantener la instalación de pruebas una o dos semanas con trabajo MIDI y Satellite
representativo, cortes de servidor/cable y reinicios controlados. Registrar RAM,
CPU, temperatura, almacenamiento, latencias y errores diarios. La prueba de
duración solo se considera pasada cuando ese tiempo se haya completado.
