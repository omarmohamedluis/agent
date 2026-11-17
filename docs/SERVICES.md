# Servicios operativos de OMI v3

`omi.services.host.ServiceHost` es la interfaz común del coordinador. Importar
los módulos no abre MIDI, sockets, procesos ni dispositivos. Arranque y parada
son asíncronos; `status()` y `learn_result()` devuelven instantáneas en memoria.
No se instala ni se descarga software al arrancar un servicio.

## MIDI y OSC

El host ejecuta un proceso Python separado (`omi.services.worker`). Su hilo de
entrada posee el cliente ALSA; el agente, HTTP, SQLite, sincronización y logs no
participan en el recorrido de eventos. El canal stdin/stdout sirve únicamente
para configuración, estado y control. La inyección MIDI existe solo en modo
simulado y utiliza ese canal de pruebas.

La implementación Linux usa `libasound.so.2` mediante `ctypes`, sin cola de Mido
o RtMidi. Una suscripción ALSA añade marcas de tiempo reales desde una cola de
reloj iniciada antes de conectar la entrada. Esa cola mide el tiempo; OMI no
programa eventos futuros en ella. El buffer de entrada de la biblioteca tiene
1024 bytes y el pool de entrada del cliente tiene 64 eventos. Al desbordarse se
descarta la entrada pendiente. Estos buffers del controlador/biblioteca son
inevitables, pero sus eventos no reciben una marca nueva al leerlos.

La calibración entre tiempo de cola y reloj monotónico se mide de nuevo en cada
lote, con muestras monotónicas antes y después de consultar el reloj ALSA. Se
usa el límite inferior de ese intervalo para envejecer conservadoramente los
eventos. Si consultar el reloj tarda más de 1 ms, se descarta la entrada incierta;
el siguiente muestreo válido vuelve a funcionar sin conservar un desfase del
muestreo anterior. Cada evento se procesa inmediatamente o se descarta. Se
vuelve a comprobar el plazo antes de cada destino y ruta; el valor
por defecto es 10 ms, configurable entre 1 y 100 ms. Una ráfaga se drena en
tramos acotados de hasta 256 eventos o 2 ms para conservar el control del proceso.
Los contadores no guardan eventos ni generan trabajo pendiente para la web.

Las rutas se compilan por tipo, número y canal y se sustituyen en una operación.
Se ejecutan todas las coincidencias, incluso si comparten dirección OSC. Los
canales internos son 0–15. Se conserva la conversión anterior: enteros 0–127,
float valor/127, booleano de nota activo únicamente para note-on con velocidad
positiva, booleano CC positivo, y constante en cada evento coincidente. La
velocidad de liberación de note-off se conserva para int/float.

Cada destino tiene su propio socket UDP no bloqueante, con `SO_SNDBUF`
solicitado de 2048 bytes (Linux puede ajustar o duplicar ese presupuesto).
`SIOCOUTQ` rechaza un nuevo envío si el socket de ESE destino todavía mantiene
memoria de un paquete anterior. Un destino bloqueado no bloquea los demás. Un
fallo no retira el destino ni guarda el paquete: el siguiente evento nuevo
vuelve a intentar su propio envío. La resolución DNS usa un único hilo auxiliar y una
caché acotada a los destinos actuales; nunca bloquea captura ni control. Hasta
resolver un nombre, sus envíos se descartan. Los nombres se revisan cada 5 s y
el control incorpora resultados disponibles cada 0,5 s.

Antes de admitir un envío un segundo hilo auxiliar consulta rutas y vecinos de
Linux mediante comandos `ip -j` de solo lectura, con timeout de 0,25 s. Las rutas
se guardan como máximo 2 s; una única instantánea de vecinos por familia se
renueva aproximadamente cada 0,1 s. Una observación de vecinos de más de 0,25 s
no autoriza envíos. Si se necesita gateway, se comprueba su dirección de enlace,
no la dirección de enlace del destino remoto. `INCOMPLETE`, `FAILED`, vecinos
ausentes y errores de inspección cierran la admisión para ese destino. Las
direcciones propias del equipo (rutas de tipo `local`), loopback, multicast y
broadcast no necesitan resolución de vecino.
Para resolver ARP/ND se permite como máximo una solicitud ICMP por segundo y
siguiente salto; nunca se usa un datagrama OSC ni otro payload UDP de prueba.
Esta preparación se realiza fuera del procesamiento de eventos, sin ejecutar
órdenes de añadir, borrar o modificar rutas, vecinos o configuración de red.
ICMP provoca la resolución ARP/ND normal del kernel. Si faltan `iproute2` o los
permisos para el socket ICMP, se mantiene el descarte hasta disponer de una
observación válida del siguiente salto; no se prueba enviando OSC.

El plazo observable comienza al entrar en ALSA y acaba en la admisión del
datagrama por el kernel. El sistema no puede garantizar cuándo un controlador
USB generó un evento antes de entregarlo al kernel, ni cuándo una red, vecino
ARP, switch o receptor entregará un paquete después de `sendto`. La inspección
evita enviar OSC cuando ya se sabe que falta resolver el vecino, pero es una
observación en caché: una ruta o vecino puede cambiar entre su comprobación y
el envío. Ese paquete podría entrar en una cola ARP/ND y aparecer tarde tras
recuperarse la red. `SIOCOUTQ` refleja memoria atribuida al socket; no demuestra
que todas las colas qdisc, del driver o de la NIC estén vacías. Un qdisc puede
desvincular el paquete de la contabilidad del socket y conservarlo pendiente.
OMI no puede retirar un paquete ya admitido por esas capas. Las pruebas físicas
deben medir esas fronteras además de los descartes de la aplicación. «Sin
replay» se refiere a eventos y reintentos de OMI; no promete ausencia absoluta
de paquetes tardíos del kernel o la red. No se afirma una garantía de tiempo
real duro ni de entrega UDP.

### Selección y reconexión

La entrada elegida se conserva exactamente. Su etiqueta usa cliente y puerto
ALSA, más identidad USB cuando existe. Una serie USB identifica el controlador
aunque cambie el número de tarjeta o cliente ALSA. Sin serie se usa el puerto
USB físico: hay que reconectar en ese mismo conector, o seleccionar de nuevo
explícitamente. Las entradas virtuales sin identidad física usan sus nombres;
si hay varios idénticos, el arranque rechaza la selección ambigua.

Un arranque comprueba seis veces la entrada, con 0,5 s entre intentos. No elige
otra entrada ni cambia el preset. Tras haber conectado correctamente, una
desconexión deja el servicio en `waiting` y vuelve a intentar la misma entrada
indefinidamente, sin alterar VLAN ni servicio. La detección consulta cada 0,5 s.

### Editor y Learn

Solo hay un editor de rutas. Abrirlo silencia toda la salida antes de confirmar
la apertura. Learn conserva el primer evento posterior a armarlo; los eventos
anteriores que sigan en buffers no se convierten en una captura nueva. Guardar
rutas mantiene el silencio. Tras cerrar, un corte por marca de tiempo descarta
también eventos recibidos durante el silencio que todavía estén en ALSA. No
hay replay. El estado de editor, la captura retenida y Learn armado se conservan
al parar/arrancar para aplicar un cambio de red o de entrada.

En standby se abre un worker únicamente para captura, siempre silenciado; al
cerrar el editor se detiene. El host no presenta esa captura como un servicio
operativo. La caducidad/renovación de la sesión la administra el agente HTTP.

## Companion Satellite

La instalación prepara Satellite **3.8.0**, build **804-8831637**, y su runtime
Node. El host exige `omi-satellite.json` con esa versión/build y ejecuta el
launcher preparado `companion-satellite <configuración.json>` desde
`/opt/omi/satellite/current` o `OMI_SATELLITE_DIR`. Los datos de ejecución están
fuera del código, bajo `<data-dir>/runtime/satellite.json`.

OMI escribe IP y puerto TCP de Companion, puerto REST, nombre de instalación y
solo el plugin `elgato-stream-deck` activado. Conserva `machineId` y nombre si
ya existen; desactiva estadísticas detalladas y mDNS. Satellite upstream puede
consultar información de actualización en segundo plano, pero no se descargan
ni instalan dependencias ni versiones al arrancar OMI.

La disponibilidad se comprueba durante un máximo de 15 s mediante la API REST
real: `/api/config` debe reflejar el destino solicitado y `/api/status` debe
contener estado de conexión válido. Un proceso vivo por sí solo no basta. La
incompatibilidad conocida de API Companion rechaza el arranque explícitamente.
La preparación termina aunque Companion esté ausente: `ready: true` indica que
Satellite está preparado, mientras `health: waiting` indica la dependencia
externa pendiente. La pérdida de Companion o de su diagnóstico REST no
reinicia un proceso vivo ni modifica la red.

Una caída del proceso admite tres recuperaciones durante esa ejecución, con
esperas de 0,5/1/1,5 s. Al agotarlas aparece `error`. Un fallo de preparación del
nuevo proceso termina la recuperación con error explícito. La salida se drena
en fragmentos y se conserva un máximo de 50 líneas de 4096 bytes en memoria;
el logger común aplica su presupuesto de disco.

Un guardián conserva un pipe de vida al agente. Si el agente desaparece o se
cierra el pipe, envía TERM al grupo de Satellite y sus hijos, espera hasta 4 s
y fuerza KILL. El cierre normal también limpia el grupo aun si su líder ya
murió. La unidad systemd con `KillMode=control-group` añade el límite del cgroup
para cualquier descendiente que upstream pudiera separar del grupo. El worker
MIDI termina al recibir EOF del canal de control.

## Comprobaciones reproducibles

```powershell
.venv/Scripts/python.exe -m pytest tests/test_midi.py tests/test_midi_backend.py tests/test_midi_neighbours.py tests/test_services_host.py tests/test_services_processes.py tests/test_satellite.py -q
```

En Linux, usar `python -m pytest` desde el entorno instalado. Las pruebas cubren
conversión/canales, todas las rutas, 20 000 eventos caducados, carga entre
destinos, DNS lento, descartes, buffers previos a Learn/cierre, entradas
ausentes/reconexión, ABI ALSA, proceso MIDI con UDP real y supervisión Satellite
con un proceso REST de prueba. Los sustitutos de pruebas no se seleccionan en
producción. El ensayo de cierre de hijo y nieto por grupo/EOF requiere Linux y
queda omitido explícitamente en Windows.

Pendiente de ejecución física: libasound y USB de la Pi, marcas de entrada de
controladores reales, faders bajo carga concurrente con medición en el receptor,
reconexión en el mismo puerto, Stream Deck y Companion reales, cierres de sus
helpers nativos, VLAN/rutas/broadcast y funcionamiento prolongado. Windows no
valida esas condiciones.

Referencias de implementación: [ALSA sequencer y timestamps](https://www.alsa-project.org/alsa-doc/alsa-lib/seq.html),
[API de eventos y propiedad de buffers ALSA](https://www.alsa-project.org/alsa-doc/alsa-lib/group___seq_event.html),
[identidad de tarjeta ALSA](https://www.alsa-project.org/alsa-doc/alsa-lib/group___seq_client.html),
[Satellite v3.8.0](https://github.com/bitfocus/companion-satellite/tree/v3.8.0/satellite/src),
[reloj sequencer Linux](https://github.com/torvalds/linux/blob/v6.12/sound/core/seq/seq_clientmgr.c),
[contabilidad UDP Linux](https://github.com/torvalds/linux/blob/v6.12/net/ipv4/udp.c) y
[ejemplo de qdisc que desacopla paquetes del socket](https://github.com/torvalds/linux/blob/v6.12/net/sched/sch_netem.c).
