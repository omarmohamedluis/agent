# OMI Agent v3

Control central de Raspberry Pi dedicadas a MIDI → OSC o Companion Satellite.
Cada Pi mantiene su biblioteca y recupera su último estado local aunque falten
el servidor o Internet. El servidor compara ese estado con la asignación esperada;
una diferencia ofrece **Aceptar** o **Sincronizar**.

La rama `v3` es la versión de pruebas. La validación física en Pi 5 y CT Debian 12
se realiza después de las comprobaciones automatizadas; todavía no es una release
estable. Los requisitos están en [REFACTOR.md](REFACTOR.md) y en la
[sección 6 de refactor2.md](refactor2.md#6-resoluciones-consolidadas-para-implementar-v3).

## Instalar

Cliente: Raspberry Pi 5, Raspberry Pi OS **64 bits**, Ethernet administrado por
NetworkManager. Desde la cuenta administradora de una imagen limpia:

```bash
sudo bash -o pipefail -c 'apt-get update && apt-get install -y curl && curl -fsSL https://raw.githubusercontent.com/omarmohamedluis/agent/v3/scripts/install.sh | bash -s -- client'
```

Servidor: CT Proxmox con Debian 12, misma subred de administración sin etiquetar.
Ejecutar como **root** dentro del CT:

```bash
bash -o pipefail -c 'apt-get update && apt-get install -y curl && curl -fsSL https://raw.githubusercontent.com/omarmohamedluis/agent/v3/scripts/install.sh | bash -s -- server'
```

Abrir `http://IP_DEL_CT:8080`. La web autónoma de la Pi está en
`http://IP_DE_LA_PI:8081`; el canal de control sigue disponible cuando el servidor
asume el control y cierra esa interfaz humana. El CT no necesita USB MIDI.

[Instalación y recuperación](INSTALL.md) · [Desarrollo con Git/SSH](docs/DEVELOPMENT.md)
· [Pruebas físicas](docs/HARDWARE_VALIDATION.md) ·
[Arquitectura y contratos](docs/V3_CONTRACTS.md) · [Evidencias](docs/V3_EXECUTION.md)

## Operar

Incorporar cada Pi desde **Descubiertos**, asignarle nombre/ID y crear o importar
presets. Seleccionar un preset no lo arranca: **Aplicar** ejecuta el servicio;
**Standby** lo detiene y retira su VLAN conservando la administración Ethernet.
Solo hay un servicio operativo por Pi.

Guardar en la biblioteca central distribuye una revisión sin cambiar servicios
en marcha. **Guardar y aplicar** actúa sobre los equipos seleccionados. Editar el
preset de una Pi activa aplica a esa Pi; editar en standby conserva standby.
Abrir el editor de rutas MIDI silencia toda su salida OSC hasta cerrarlo, incluso
antes de armar Learn. Los eventos descartados no se reproducen después.

Satellite **3.8.0, build 804-8831637** se prepara junto con sus runtimes durante la
instalación. OMI genera su configuración y supervisa su proceso; la pantalla
opcional no condiciona el funcionamiento. Una instalación preparada arranca sin
Internet. Actualizar necesita acceso a los repositorios de descarga.

## Código y datos

- Código inmutable por instalación: `/opt/omi/releases/<commit>-<rol>`;
  `/opt/omi/current` apunta a la versión activa.
- Datos persistentes: `/var/lib/omi/client` o `/var/lib/omi/server`.
- Arranque y supervisión: `omi-client.service` / `omi-server.service`.
- Entradas: `omi-agent`, `omi-server`, `omi-maintain`, dentro del entorno virtual
  de la versión instalada. No se utiliza cron para arrancar OMI.

Las interfaces están diseñadas para la LAN privada de administración acordada.
