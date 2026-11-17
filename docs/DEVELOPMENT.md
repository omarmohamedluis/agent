# Desarrollo con Git, SSH y simulación

La instalación normal descarga versiones identificadas, sin claves GitHub ni
árbol de trabajo. Para desarrollar se usa una copia Git separada en una Pi de
pruebas o en el CT. Los datos de producción siguen fuera del repositorio y no se
guardan en Git. `v3` es la rama de integración para esta validación.

## Windows sin hardware

Python 3.11 o posterior y Git. Desde PowerShell:

```powershell
git clone --branch v3 git@github.com:omarmohamedluis/agent.git
cd agent
py -3 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.lock
.venv/Scripts/python.exe -m pip install --no-deps --no-build-isolation -e .
.venv/Scripts/omi-server.exe --simulate --data-dir .omi-dev/server --host 127.0.0.1 --port 8080 --no-beacon
```

En una segunda terminal:

```powershell
.venv/Scripts/omi-agent.exe --simulate --serial pi-laboratorio --data-dir .omi-dev/client --host 127.0.0.1 --port 8081 --server http://127.0.0.1:8080 --endpoint http://127.0.0.1:8081
```

Abrir `http://127.0.0.1:8080`, incorporar `pi-laboratorio` y probar la biblioteca,
asignaciones, rutas y Learn. La simulación usa adaptadores explícitos y no cambia
la red ni ejecuta acciones de alimentación del anfitrión. Solo en ese modo se
permite inyectar MIDI por `/api/simulate/midi`. No demuestra que ALSA, USB,
NetworkManager, I2C o Stream Deck funcionen físicamente.

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m build
```

Las pruebas de instaladores detectan Git Bash en
`C:/Program Files/Git/bin/bash.exe`. Ejecutan sintaxis y transacciones en carpetas
temporales con `systemctl` sustituido, sin instalar paquetes ni detener servicios.
Las pruebas de pantalla necesitan Pillow y smbus2, disponibles con
`pip install -r requirements-client.lock` o el extra `.[display]`.

## Remote-SSH a la Pi o al CT

Activar SSH al preparar la imagen y conectar desde VS Code Remote-SSH con la
cuenta administradora. Crear una clave de desarrollo si hace falta, añadir su
parte pública a GitHub y verificar el acceso:

```bash
ssh-keygen -t ed25519 -C omi-desarrollo
cat ~/.ssh/id_ed25519.pub
ssh -T git@github.com
git clone --branch v3 git@github.com:omarmohamedluis/agent.git ~/src/omi
cd ~/src/omi
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
```

Aceptar una huella de SSH únicamente tras contrastarla con la documentación del
servicio. No copiar la clave privada a otras Pi: solo la máquina donde se hace
desarrollo necesita acceso Git por SSH.

Para trabajar sobre hardware real de la Pi, detener primero la instancia
supervisada. El cliente también mantiene un bloqueo global que impide dos
instancias reales aunque usen distintos directorios de datos.

```bash
sudo systemctl stop omi-client
.venv/bin/python -m pip install -r requirements-client.lock
sudo .venv/bin/omi-agent --data-dir /var/lib/omi/client --host 0.0.0.0 --port 8081
```

Se puede usar `/var/lib/omi/client-lab` para pruebas que no deban editar la
biblioteca de la instalación; mantener parada la unidad igualmente. La Pi real
necesita los paquetes de sistema y Satellite preparados por el instalador. La
ejecución manual no descarga dependencias. Salir con Ctrl+C para detener servicios
y limpiar su red antes de volver a activar systemd:

```bash
sudo systemctl start omi-client
```

Para el servidor de desarrollo detener `omi-server`, usar una carpeta de datos
propia de la cuenta de desarrollo y ejecutar `.venv/bin/omi-server`. No arrancar
dos procesos que anuncien el mismo CT/puerto. Para depuración de control y webs,
usar la simulación sin privilegios antes de pasar al adaptador real.

## Distribuir un cambio probado

Crear una rama de trabajo, ejecutar las pruebas afectadas y la suite pertinente,
revisar el diff y publicar cambios normales. No editar `main`, no hacer
force-push y no publicar una release estable antes de la validación física.
Conservar las anotaciones locales del usuario en `TODO`.

```bash
git switch -c mi-cambio v3
git diff --check
.venv/bin/python -m pytest -q
git add ARCHIVOS_REVISADOS
git commit -m "Describe el cambio comprobado"
git push -u origin mi-cambio
```

Tras integrar y publicar en `v3`, actualizar primero una Pi y el CT de laboratorio
con los comandos de [INSTALL.md](../INSTALL.md#actualizar). Para reproducir una
prueba se puede seleccionar el SHA completo con `--ref`. El resto de los equipos
recibe esa misma versión después de comprobar los recorridos de hardware.

Los archivos `requirements-*.lock` fijan las dependencias de ejecución/desarrollo;
`deploy/satellite-lock.json` fija la descarga externa. Cambiar un pin exige
reconstruir y comprobar la instalación correspondiente. Las unidades systemd no
contienen `pip`, Git, Yarn ni descargas al arrancar.
