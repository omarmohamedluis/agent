# Instalación, actualización y recuperación v3

## Equipos de referencia

Cliente: Pi 5 con Raspberry Pi OS de 64 bits, instalación normal del Raspberry Pi
Imager. También funciona con la imagen Lite de 64 bits recomendada por Satellite.
La Ethernet de administración debe estar gestionada por NetworkManager. Servidor:
CT Proxmox Debian 12 con systemd y arquitectura amd64 o arm64. Una instalación
normal necesita Internet, espacio para dos versiones de código y una copia de sus
datos. Los equipos ya preparados arrancan sin descargar nada.

El tráfico de administración llega sin etiqueta, en la misma VLAN/subred para CT
y Pi. El switch entrega además las VLAN etiquetadas que necesiten los presets.
OMI crea únicamente la VLAN del servicio activo. No cambia automáticamente la
red del CT. En un CT normal no hacen falta privilegios USB ni NetworkManager.

## Instalación limpia

Los dos comandos de [README.md](README.md#instalar) descargan el instalador de la
rama publicada `v3`. Este resuelve la rama a un commit concreto de GitHub y
extrae ese archivo de código; no necesita clonar ni configurar SSH. No instala
una release estable. Para repetir exactamente una prueba se puede añadir
`--ref SHA_COMPLETO` después de `client` o `server`.

El instalador instala paquetes Debian y dependencias Python fijadas en
`requirements-client.lock` o `requirements-server.lock`, crea el entorno virtual,
prepara Satellite en la Pi, ejecuta `pip check`, comprueba la CLI y `doctor`
sin tocar la red, y finalmente instala/habilita la unidad systemd. Los datos
quedan fuera del código. La Pi ejecuta OMI como root para NetworkManager,
alimentación, USB e I2C en este equipo dedicado; el CT usa el usuario de sistema
`omi`, sin acceso de shell.

En la Pi comprobar primero:

```bash
uname -m
nmcli general status
nmcli device status
```

Debe ser `aarch64` y Ethernet debe estar administrada. El instalador se detiene
si falta NetworkManager; no convierte ni reinicia una conexión de administración
por sorpresa. Quitar cualquier autoarranque OMI anterior de `crontab -e` y
`sudo crontab -e`. Si existía una instalación independiente de Satellite, detener
y deshabilitar su `satellite.service` antes de que OMI gestione el Stream Deck.
El instalador rechaza esos mecanismos en competencia. No se migra el formato v2:
esta reconstrucción parte de instalaciones nuevas.

Las pantallas son opcionales. Para habilitar I2C en la Pi que lleva una pantalla:

```bash
sudo raspi-config nonint do_i2c 0
sudo modprobe i2c-dev
sudo systemctl restart omi-client
```

Se detecta UCTRONICS RM0004 en I2C 1, `0x18`, o SSD1306 128×64 en `0x3c`.
`OMI_DISPLAY=auto` es el valor predeterminado de `/etc/omi/omi.env`; se puede fijar
`uctronics`, `ssd1306` u `off`. Ambos usan la misma vista y franja superior amarilla
(física en el OLED). No detectar pantalla o fallar una escritura no detiene OMI.

## Estado y primera configuración

```bash
sudo systemctl status omi-client --no-pager
sudo journalctl -u omi-client -n 80 --no-pager
sudo /opt/omi/current/venv/bin/omi-maintain --data-dir /var/lib/omi/client doctor --role client --offline
```

En el CT sustituir `client` por `server`. Puertos de administración: HTTP 8080
(CT), HTTP 8081 (Pi), UDP 18080 (beacon del CT). Satellite conecta hacia Companion
por TCP 16622 de forma predeterminada y ofrece su REST en 9999 mientras se ejecuta.
El editor central configura Satellite a través de OMI; no depende de un iframe.
Ajustar `OMI_ADVERTISE=http://IP_DEL_CT:8080` en `/etc/omi/omi.env` si la selección
automática de dirección del CT no coincide con la interfaz de administración,
y reiniciar `omi-server`. La Pi admite una URL manual de servidor como respaldo
al descubrimiento desde sus ajustes.

La unidad reintenta fallos del proceso como máximo tres arranques en 120 s, con
5 s entre intentos. Tras resolver un fallo persistente:

```bash
sudo systemctl reset-failed omi-client
sudo systemctl start omi-client
```

Un `systemctl stop` permite mantenimiento sin que la supervisión lo resucite.
La parada termina el grupo de procesos, incluidos los hijos de Satellite.

## Actualizar

Desde SSH en una ventana de mantenimiento:

```bash
sudo bash /opt/omi/current/scripts/update.sh client --ref v3
```

En el CT como root: `bash /opt/omi/current/scripts/update.sh server --ref v3`.
También se puede solicitar la actualización desde la web. El servidor usa un
wrapper privilegiado, `/usr/local/sbin/omi-update`, que valida rol, referencia e
ID y abre una unidad transitoria systemd; la actualización sobrevive a la parada
del proceso OMI. El permiso sudo del usuario `omi` se limita a ese wrapper para
el rol servidor. Los archivos del wrapper y del código pertenecen a root.

La descarga, instalación de dependencias, preparación de Satellite y comprobación
del nuevo código ocurren antes de parar el proceso existente. Después se detiene
OMI, se guarda una copia coherente de todo su directorio de datos en
`/var/backups/omi/<rol>/`, se intercambian los enlaces y se comprueba HTTP durante
hasta 180 s. Se exige el commit instalado y la recuperación terminada de la misma
revisión local, sin operación pendiente ni error; esperar un equipo externo es
válido. Fallar el nuevo arranque restaura el código, la unidad y los datos
anteriores. No se reinicia la Pi ni se borran presets para actualizar.
`update-status.json` en el directorio de datos contiene ID, fase, resultado y
error; los detalles del proceso están en `journalctl -u omi-update-ID` si la
actualización se lanzó desde la web. Conservar las versiones anteriores hasta
terminar la prueba física.

Si la preparación falla, el proceso actual permanece activo. Puede quedar un
directorio de versión sin `.complete`: inspeccionarlo y moverlo aparte antes de
reintentar. No ejecutar desarrollo dentro de `/opt/omi/releases` ni modificar
el entorno virtual de una versión instalada.

## Recuperar sin Internet

La recuperación usa los archivos ya instalados y no necesita Git ni descargas.
Consultar las versiones y las copias antes de elegir:

```bash
ls -l /opt/omi/current
ls /opt/omi/releases
ls /var/backups/omi/client
```

Cambiar a una versión completa conservando los datos actuales:

```bash
sudo bash /opt/omi/current/scripts/rollback.sh client /opt/omi/releases/COMMIT-client
```

Si una actualización migró los datos, recuperar también su copia anterior:

```bash
sudo bash /opt/omi/current/scripts/rollback.sh client /opt/omi/releases/COMMIT-client --snapshot /var/backups/omi/client/FECHA-ID.tar.gz
```

El script guarda otra copia antes del cambio. Los datos que fallaron quedan
aparte como `client.failed-ID` y no se eliminan. Cambiar el rol y las rutas para
el CT. Si `current` está roto, ejecutar `rollback.sh` desde el directorio de una
versión completa. Un error de recuperación deja un resultado explícito; consultar
el journal y la consola local antes de repetir una orden.

## Limpieza, copias y resets

`omi-maintain` requiere `--data-dir` explícito. Las copias de SQLite son coherentes
incluso con el proceso activo. Las acciones destructivas exigen parar primero la
unidad; el bloqueo de instancia impide que compitan con el proceso.

```bash
sudo /opt/omi/current/venv/bin/omi-maintain --data-dir /var/lib/omi/client backup --output /var/backups/omi/client/manual.sqlite3
sudo /opt/omi/current/venv/bin/omi-maintain --data-dir /var/lib/omi/client clear-logs
sudo systemctl stop omi-client
sudo /opt/omi/current/venv/bin/omi-maintain --data-dir /var/lib/omi/client reset-state
sudo systemctl start omi-client
```

`clear-logs` elimina solo los registros propios de la aplicación; `reset-state`
limpia el estado temporal conservando presets y ajustes. En el CT,
`reset-installation --confirm` borra incorporaciones/asignaciones conservando la
biblioteca. `factory-reset --confirm --backup DIRECTORIO` borra los datos de
usuario tras guardar la copia. Ningún reset del CT borra a distancia las
bibliotecas de las Pi. La web ofrece las acciones equivalentes con su confirmación.

## Satellite fijado

Se utiliza **3.8.0, build 804-8831637**, commit upstream
`8831637d4048ae176c7d25f2c16dbd2f31da8729`. La instalación sigue la extracción de
recursos del [instalador oficial](https://github.com/bitfocus/companion-satellite/blob/v3.8.0/pi-image/update.sh),
con archivos y hashes fijados en `deploy/satellite-lock.json`. Se prepara Node
24.22.2 y los runtimes de módulos incluidos en el archivo publicado; no se usa
un actualizador independiente ni se recompila al arrancar.

`/opt/omi/satellite/current/companion-satellite CONFIG_JSON` ejecuta Node con
`satellite/dist/main.js`; OMI escribe `runtime/satellite.json` en sus datos. El
[main oficial](https://github.com/bitfocus/companion-satellite/blob/v3.8.0/satellite/src/main.ts)
acepta esa ruta como único argumento. El manifiesto `omi-satellite.json` permite
comprobar el pin. La compatibilidad declarada por el
[README upstream](https://github.com/bitfocus/companion-satellite/blob/v3.8.0/README.md)
es Companion 3.4 o posterior; comprobar la combinación concreta de Companion y
Stream Deck en la Pi antes de usarla durante un bolo.
