# Segunda revisión de requisitos: OMI Agent v3

Fecha: 17 de noviembre de 2026.

Estado: **entrevista revisada, sin decisiones de producto bloqueantes pendientes; refactor todavía sin iniciar**.

Las preguntas y propuestas originales se conservan como contexto. **Las resoluciones de la sección 6 prevalecen sobre las propuestas anteriores cuando difieren**, especialmente en edición, reinicios, arranque y pérdida del controlador MIDI. Tus respuestas originales se mantienen.

He leído las 84 respuestas de `REFACTOR.md` y las aclaraciones que hemos hablado. La idea del producto está definida. Quedan unos cruces entre respuestas y algunos comportamientos concretos que pueden implementarse de varias maneras.

No necesitas repetir la entrevista. Aquí hay **10 aclaraciones**, cada una con contexto, ejemplo y propuesta. Puedes responder «acepto la propuesta», cambiar una parte o delegarla. Las propuestas sin respuesta siguen siendo propuestas.

`REFACTOR.md` conserva tus respuestas originales. Este documento lo complementa; si acordamos aquí algo distinto, dejaremos registrada esa resolución antes de implementar.

## Lo que ya entiendo y no te vuelvo a preguntar

| Tema | Decisión expresada en tus respuestas |
| --- | --- |
| Uso | Herramienta personal para bolos; un operador, una instalación y un servidor. |
| Escala | Cinco Pi hoy, hasta unas veinte; funcionamiento continuo durante una o dos semanas. |
| Plataformas | Clientes Raspberry Pi 5; servidor en un CT Proxmox con Debian 12. Windows queda como ordenador de desarrollo. |
| Servicios | Únicamente MIDI a OSC y Companion Satellite; OSCnum y los demás quedan fuera. |
| Exclusividad | Un único servicio operativo por Pi. Antes de cambiar, se detiene el anterior. |
| Red | Administración, servidor y eventual Internet por tráfico Ethernet sin etiquetar. Sin Wi-Fi en esta versión. |
| VLAN | Como máximo una VLAN de servicio relevante por Pi; pertenece al preset. Se prepara antes de arrancar el servicio y se elimina al pararlo. No hace falta VLAN global. |
| Configuración de red | DHCP o IP fija; máscara, gateway y DNS según corresponda. La red de administración pertenece a la Pi. |
| IP fija en presets | Se almacena y se aplica tal cual. La gestión de conflictos entre varias Pi usando esa IP queda aplazada expresamente. |
| Recuperación de red | No se pide reversión automática de una IP incorrecta. El operador puede recuperar el equipo con acceso físico. |
| Selección de interfaz de salida | En el entorno descrito no hay subredes solapadas; las rutas del sistema deciden la salida. |
| Autonomía | Perder el servidor no detiene el servicio; debe habilitarse el control local. |
| Reconexión | Una discrepancia se muestra en naranja y requiere elegir entre aceptar el estado real o aplicar lo que esperaba el servidor. |
| Operaciones remotas | Sin cola de órdenes para Pi offline. Una operación aceptada se sigue hasta su resultado y bloquea nuevas operaciones sobre esa Pi. |
| Identidad | Serial físico como identidad estable; ID positivo y único, editable y con huecos; nombre visible y hostname separados. |
| Alta | Dispositivos descubiertos en una sección propia; incorporación manual mediante onboard. |
| Biblioteca | Todos los agentes mantienen una copia local de los presets; las revisiones activas y las disponibles se distinguen. |
| Conflictos de edición | Conservar cambios concurrentes/offline y consultar al operador; evitar sobrescrituras silenciosas. |
| Fallo de cambio de servicio | Standby con error y tarjeta roja; no recuperar automáticamente el servicio anterior. |
| MIDI | Notas, CC y velocidad; todas las rutas coincidentes; canales visibles 1–16 y «Todos». |
| MIDI ausente al arrancar | Comprobar el dispositivo elegido, reintentar unas cinco o seis veces y fallar si no aparece. No cambiar el preset ni sustituirlo por otro dispositivo. |
| Satellite | Stream Deck; configurar Companion centralmente. La pérdida de Companion no provoca cambios de red ni reinicios del servicio; la caída de Satellite admite recuperación limitada. |
| Pantallas | Opcionales; conservar drivers y detección que ya sirven. Estado compartido y estética de franja superior amarilla también en UCTRONICS. |
| Acceso | LAN controlada; sin autenticación ni modelo de usuarios/roles en esta versión. |
| Instalación | Un instalador propio de cliente y otro de servidor, con una instrucción sencilla en el README. |
| Supervisión | systemd en las Pi, autoarranque, recuperación limitada y ejecución manual para desarrollo sin duplicar instancias. |
| Desarrollo | VS Code desde Windows y Remote-SSH a los equipos; cambios guardados en Git y distribución de versiones probadas. |
| Limpieza | Separar limpiar logs, restablecer estado temporal y restaurar de fábrica. |
| Rendimiento | Preparación ordenada y comprobada; durante el bolo, latencia MIDI/OSC baja y predecible. |
| Alcance de v3 | Refactor completo con pruebas automatizadas. Sin obligación de conservar APIs, estructura o instalaciones antiguas; pruebas físicas posteriores en Pi y CT nuevos. |
| Git | Llevar la base actual a `main` y crear desde ahí `v3`, conforme a UPD-04. El historial ya revisado permite avanzar `main` hasta la rama actual. |

## 1. Los cruces que necesitamos resolver

### R2-01 · ¿Cuándo podemos editar y qué se silencia durante el aprendizaje?

**Referencias:** SVC-02, CFG-04 y MIDI-05.

Hay dos reglas que, tal como están escritas, se pisan:

- SVC-02 permite configurar únicamente cuando no hay ningún servicio activo.
- CFG-04 describe editar un servicio encendido y reiniciarlo al guardar; MIDI-05 permite añadir/editar una ruta y hacer learn con MIDI activo.

**Propuesta para encajarlas:**

1. El editor completo de un preset se abre desde standby. Puede capturar MIDI, pero no emite OSC ni arranca Satellite.
2. Con MIDI funcionando se permite una excepción explícita: añadir/editar rutas y hacer learn. Al entrar en ese modo, se pausa **todo el envío OSC de esa Pi**, manteniendo la captura MIDI.
3. Learn conserva el primer evento capturado hasta confirmarlo o descartarlo.
4. Si guardas cambios en esa Pi activa, se aplica el recorrido de reinicio que acordemos en R2-02. Si cancelas, vuelve a funcionar la revisión anterior y se reanuda OSC.
5. En Satellite, editar la configuración requiere pasar a standby; no se cambia la aplicación externa mientras está trabajando.

¿Quieres esta excepción para MIDI, o prefieres una regla más sencilla: pasar siempre a standby antes de editar cualquier cosa? ¿Durante learn quieres silenciar toda la salida, como propongo, o solo la ruta que estás editando?

**Respuesta:**

> apunte, editar solo desde estandby, PERO si puedo modificar el PRESET QUE ESTA EJECUTADO, es decir, como MIDI ejecutado, no puedo editar un preset de companion o un preset diferente de midi, PERO, si puedo entrar en la configuracion del preset actual y modificar , ip, vlan... bla bla bla, cunado le doy a guardar paro el servicio y lo reinicio. pero solo si es un cambio que requiera reinicio, una ruta no necesita reinicio, pero una VLAN si... por eso las opciones gordas del preset las tengo en el menu de configuracion del preset. "tu has dhico: - Desde standby se puede editar cualquier preset. Con un servicio funcionando solo se permite editar el preset que está ejecutando, tanto sus rutas como sus opciones generales. Al guardar, los cambios compatibles se aplican en caliente; los que requieren reinicio ejecutan la secuencia ordenada de parada, preparación de red y arranque"

### R2-02 · ¿A qué Pi afecta guardar un preset?

**Referencias:** CFG-02, CFG-04 y SAT-01.

Podemos hacer compatibles tus respuestas: guardar desde una Pi puede reiniciar su servicio sin reiniciar los servicios de las demás que comparten el preset. Falta fijar qué hace el editor central.

**Propuesta:**

| Acción | Biblioteca | Servicios en marcha |
| --- | --- | --- |
| Guardar un preset desde el editor central. | Nueva revisión; se distribuye a las Pi conectadas. | Siguen con la revisión que ya ejecutaban. |
| «Guardar y aplicar» desde el servidor, con una o varias Pi seleccionadas. | Nueva revisión; se distribuye. | Reinicia únicamente los servicios de las Pi seleccionadas, con la secuencia completa de servicio/red. |
| Guardar desde la edición de una Pi con MIDI activo, si admitimos R2-01. | Nueva revisión local, sincronizada con el servidor cuando haya conexión. | Reinicia el servicio de esa Pi; las demás continúan con su revisión anterior. |
| Guardar mientras una Pi está en standby. | Se guarda y sincroniza. | Continúa en standby hasta que se solicite arrancar. |

Ejemplo: quince Satellite usan «Show A». Cambia el puerto de Companion. Editas el preset y eliges aplicar a las quince; cada una informa de su resultado. La mera llegada de una revisión nueva a la biblioteca no reinicia ninguna.

Una Pi que sigue ejecutando una revisión anterior **pero sigue cumpliendo lo que se le había asignado** puede estar verde y mostrar «actualización disponible». La discrepancia naranja aparece cuando difiere de la asignación esperada, no simplemente porque exista una revisión más nueva.

¿Te encaja esta tabla o quieres que guardar en el servidor aplique automáticamente a todas las Pi que utilizan ese preset?

**Respuesta:**

> propuesta me parece bien

### R2-03 · ¿Qué significa exactamente «recuperar el último servicio»?

**Referencias:** ARR-01, ARR-02/03, POW-01/03 y SVC-04.

ARR-01 dice esperar primero al servidor. POW-03 dice «siempre el último servicio». Standby también es una elección válida, y has pedido no recuperar el servicio anterior si un cambio falla.

Propongo estos resultados concretos. Puedes aceptar la tabla o corregir filas:

| Situación al arrancar el agente | Resultado propuesto |
| --- | --- |
| El servidor aparece durante una espera inicial limitada y la Pi está incorporada con una asignación válida. | Arrancar la asignación que tiene el servidor. La espera exacta será configurable y la fijaré como detalle técnico. |
| No aparece el servidor. | Recuperar el último estado de ejecución guardado localmente, que puede ser un servicio/preset o standby. |
| El operador dejó la Pi expresamente en standby antes de apagar. | Recuperar standby; no resucitar un servicio que había parado. |
| MIDI funcionaba, se intentó pasar a Satellite y el cambio falló dejando standby con error. | Conservar standby como estado local recuperable, con el último error disponible; un arranque posterior sin servidor no recupera MIDI ni reintenta Satellite por su cuenta. |
| La Pi ejecutaba «Show A», revisión 3, aunque su biblioteca ya tenía la 4. | Sin otra instrucción, recuperar la revisión 3 que estaba ejecutando. |
| Hay servidor, pero la Pi todavía no está incorporada o no tiene asignación. | Esperar en standby a la incorporación/selección. Si no tiene preset válido, indicar que necesita uno. |

La regla de arranque no cambia la de reconexión: si el servicio ya está funcionando cuando vuelve el servidor, se mantiene y se muestra cualquier discrepancia para que decidas.

¿Es esto lo que quieres? Especialmente: ¿standby cuenta como «último estado» y una nueva revisión del preset debe esperar a una aplicación explícita también después de reiniciar?

**Respuesta:**

> apago la pi, o se va la corriente o la reinicio o lo que sea, la secuencia de arranque de la pi es la misma, espera el beacon, si lo recibe, le indica al server lo ultimo que estaba haciendo, y lo ejecuta, el servidor puede avisar al usuario, en el interfaz del servidor de oye, esta pi no esta haciendo lo que yo pensaba. y dices, esta bien o sincronizar. "Sí. La Pi siempre recupera su estado local; el servidor compara y avisa. Recibir el beacon sirve para establecer comunicación.

La secuencia sería:
1. La Pi arranca y espera el beacon durante un plazo limitado.
2. Si encuentra el servidor, le comunica el estado que tiene guardado y va a recuperar.
3. Recupera ese estado local. Si no encuentra servidor, también lo recupera.
4. Una vez arrancado el servicio, informa del resultado real.
5. El servidor compara con su asignación esperada. Si hay discrepancia, muestra la advertencia y ofrece:
   - Aceptar: actualizar lo que esperaba el servidor.
   - Sincronizar: ordenar a la Pi que aplique la asignación del servidor.
Esto corrige mi tabla: encontrar el servidor al arrancar no hace que la Pi ejecute automáticamente lo que tenga guardado el servidor.

Y el procedimiento es el mismo tras apagado, reinicio, corte de corriente o reinicio del agente."

### R2-04 · ¿Bloqueamos órdenes o dejamos de saber si la Pi sigue viva?

**Referencias:** COM-03/05/06, SVC-06 y UI-03.

COM-03 permite pausar la comunicación durante uno o dos minutos; COM-06 plantea recibir hitos del proceso. Además, la ausencia del servidor debe activar la web local. Hay que distinguir una operación lenta de una pérdida real de comunicación.

**Propuesta:**

- La tarjeta se bloquea en cuanto solicitas la operación y mantiene el bloqueo mientras se acepta y ejecuta. El agente también rechaza nuevas operaciones incompatibles; el bloqueo de botones no es la única protección.
- El coordinador trabaja mientras un canal ligero e independiente sigue notificando salud y, cuando cambia la fase, progreso: servicio parado, VLAN retirada, IP obtenida, servicio listo.
- Que venza el plazo de una operación con señales de vida presentes produce un resultado de error/timeout; la Pi continúa identificada como conectada.
- Si desaparecen también las señales de vida, pasa a offline y el resultado de la operación puede quedar desconocido hasta recuperar contacto. No se reenvía automáticamente la orden para ejecutarla otra vez.
- Un timeout de la interfaz no da permiso para ejecutar dos operaciones simultáneas: primero se consulta o recupera el estado real del agente.

Esto conserva tu bloqueo de órdenes durante el trabajo y permite seguir viendo lo que pasa. Los intervalos y plazos concretos quedan a mi criterio dentro de los objetivos de rendimiento.

¿Aceptas este funcionamiento, o quieres silencio completo durante la operación aunque el servidor tarde más en distinguir un bloqueo de una desconexión?

**Respuesta:**

> me has ledio la mente, ademas, podemos hacer que la tarjeta de la pi en el servidor cambien de botones a una especie de consola con los logs que van entrando o algo así un poco friki :) y cunado termina cambiamos el diseño de tarjeta a uno normal.


## 2. Casos de uso que faltan por concretar

### R2-05 · ¿Qué Pi escucha MIDI cuando haces learn desde el servidor?

**Referencias:** SAT-01, UI-03 y MIDI-05.

El servidor será un CT sin el controlador USB conectado. Para editar toda la biblioteca desde él y capturar un botón real, tenemos que usar la entrada MIDI de una Pi concreta.

**Propuesta:** en el editor central eliges «capturar desde Pi X», seleccionas su entrada MIDI y haces learn. Esa Pi aplica las reglas de edición/silencio de R2-01; el servidor recibe el evento y lo incorpora al borrador. La captura acaba al confirmar, cancelar o perder la sesión, liberando el dispositivo y recuperando el estado que corresponda. El primer evento queda retenido hasta decidir qué hacer con él.

¿Quieres este flujo? Si editas un preset destinado a varias Pi, ¿te basta capturar desde una Pi representativa y guardar el controlador elegido en el preset, como ya has pedido?

**Respuesta:**

> tu propuesta me parece bien :)

### R2-06 · ¿El reset de cada instalación borra también los presets?

**Referencias:** ACC-02, CFG-02 e INS-04.

Dices que normalmente harás factory default del servidor en cada instalación, de modo que todas las Pi volverán a ser nuevas. También quieres una biblioteca central replicada en los agentes para reutilizarla y cubrir contingencias.

¿Al preparar otro bolo quieres borrar **solo el registro de Pi, IDs, nombres y tareas asignadas**, o también **la biblioteca central de presets**?

**Propuesta:**

- «Restablecer instalación» limpia las incorporaciones y asignaciones del servidor, conservando la biblioteca de presets.
- «Restaurar de fábrica» ofrece borrar todos sus datos de usuario, con confirmación y copia de seguridad.
- Ninguna de esas acciones borra a distancia las copias locales de las Pi.
- Si incorporas una Pi con presets a un servidor cuya biblioteca está vacía, se ofrece importar sus presets; los conflictos siguen las reglas de revisiones acordadas. Un servidor vacío no se interpreta automáticamente como una orden de borrar las bibliotecas locales.

¿Te encaja esta separación o quieres empezar cada instalación con la biblioteca central vacía? ¿La importación al incorporar la Pi debe ser automática si no hay conflictos, o quieres seleccionarla?

**Respuesta:**

> me parece bien tu propueta

### R2-07 · ¿Qué hacemos si el controlador MIDI se desconecta durante el bolo?

**Referencia:** MIDI-04 y SVC-05.

Está resuelto el arranque con el controlador ausente: reintentos limitados y error, conservando exactamente la selección del preset. Falta concretar la desconexión de un controlador que ya estaba funcionando.

**Propuesta:** aplicar una recuperación limitada del dispositivo elegido. Si vuelve dentro del plazo de reintentos, reabrirlo y continuar; si no vuelve, detener el servicio, retirar su VLAN y quedar en standby con error. Después de conectar el controlador, el operador solicita arrancar de nuevo. Nunca se escoge la entrada MIDI interna ni otra controladora como sustituta.

¿Quieres esta recuperación limitada también durante el uso, o prefieres que el servicio permanezca esperando indefinidamente al mismo controlador y se recupere en cuanto lo conectes?

**Respuesta:**

> que se quede esperando. 

### R2-08 · ¿Se puede borrar un preset que está en uso o asignado?

**Referencias:** CFG-02 y CFG-05.

Has definido nombres sensibles a mayúsculas, ausencia de un Default protegido y posibilidad de biblioteca vacía. Falta decidir qué ocurre si se borra un preset que una Pi usa o que el servidor tiene asignado.

**Propuesta:** bloquear el borrado si figura como activo o asignado a alguna Pi registrada e indicar cuáles son. Primero se cambia su asignación o se la pone en standby. Si una Pi está offline, el servidor conserva su última asignación conocida para esta comprobación. Renombrar un preset conserva su identidad y no reinicia los servicios.

La alternativa es quitar el preset de la biblioteca, conservando las revisiones necesarias para las ejecuciones actuales y obligando a elegir otro al próximo arranque.

¿Qué comportamiento prefieres? La limitación del servidor es la información disponible: si una Pi cambió su ejecución estando offline, no podremos conocer ese cambio hasta que vuelva a comunicarlo.

**Respuesta:**

> me parece bien tu sujerencia.

### R2-09 · Dos detalles de OSC donde «como está ahora» deja resultados distintos

**Referencias:** MIDI-02/03/06.

Al revisar la implementación he encontrado estos dos casos:

| Caso | Implementación actual | Propuesta para v3 |
| --- | --- | --- |
| Dos rutas usan la misma dirección OSC, por ejemplo dos notas distintas que disparan `/go`. | La validación rechaza direcciones OSC repetidas, aunque el motor puede recorrer todas las rutas coincidentes. | Permitirlo. Cada ruta que coincida envía su mensaje; si un evento coincide con dos rutas hacia la misma dirección, salen los dos mensajes. |
| Un envío OSC lanza una excepción o timeout. | Se retira ese destino de la lista de clientes activa, por lo que los siguientes eventos pueden dejar de enviársele hasta reconstruirla. | Conservar el destino y seguir intentando los envíos posteriores, con avisos limitados para evitar saturar logs o afectar al servicio. |

¿Aceptas ambas propuestas o quieres alguna regla diferente?

El resto de la conversión existente se toma como referencia, conforme a MIDI-02: int mantiene el valor 0–127, float lo divide entre 127, bool representa activación y const envía la constante configurada. Dejaremos casos concretos de note-on, note-off, velocidad cero y CC en las pruebas. También se aplicará el filtro de canal a notas y CC; el motor actual lo consulta en CC y lo omite en notas.

**Respuesta:**

> MIDI/OSC prioriza la actualidad del evento. Los eventos que no puedan procesarse o enviarse a tiempo se descartan, sin acumularlos ni reintentarlos posteriormente. Se probará específicamente con movimientos rápidos de faders y picos de carga para comprobar que no aparecen ráfagas de eventos atrasados.

### R2-10 · Un dato práctico para preparar los instaladores y las pruebas

**Referencias:** ENT-01/02/04 e INS-01.

El servidor está definido: CT Debian 12. Para el cliente solo consta Pi 5; falta la imagen del sistema que instalarás en la Pi de prueba.

- ¿Qué sistema e imagen vas a usar: nombre, versión y 32/64 bits, si lo sabes? Si prefieres que lo elija yo, indícalo y documentaré una imagen de referencia antes de las pruebas físicas.
- ¿El CT del servidor estará conectado al mismo segmento de administración sin etiquetar que las Pi? Si hay un router entre ellos, usaremos la dirección manual de respaldo que ya has aceptado, salvo que preparemos otra forma de descubrimiento.

No necesito ahora las IP reales, credenciales ni el inventario completo de switches. Se trata de definir el entorno al que deben apuntar los instaladores.

**Respuesta:**

> Sistema de la Pi: rasbperry pi os 64, el normal del instalador.
> CT y Pi en el mismo segmento de administración:si, misma vlan misma subnet siempre. sin taguear. ellos se entienden por una vlan dedicada para ellos.

## 3. Interpretaciones técnicas que puedo resolver con lo que ya has delegado

Estas son las decisiones de implementación que planteo a partir de tus respuestas. Puedes corregirlas aquí si alguna no refleja tu intención; no requieren otra entrevista de librerías o carpetas.

1. **Web local y comunicación del agente se separan.** Cerrar la interfaz humana de la Pi cuando el servidor funciona no detiene el canal de control, el proceso del agente ni el servicio. Los comandos locales y remotos usan el mismo coordinador, evitando que módulos del mismo agente dependan de llamar por HTTP a su propia web.
2. **La web local se activa en modo autónomo.** Al volver el servidor, una discrepancia la mantiene disponible. Cuando todo coincide, podrá cerrarse sin interrumpir el servicio. Si hay un editor o una captura en curso, se resolverá la sesión antes de cerrarla para evitar perder trabajo.
3. **«Más reciente» se basa en revisiones y su origen.** Una revisión derivada de otra puede actualizar la biblioteca. Dos ediciones que parten de la misma base y divergen se conservan como conflicto; no se decide únicamente por la hora de los equipos.
4. **Guardar en disco y ejecutar una revisión son estados distintos.** Conservaremos la información necesaria para informar de qué revisión está usando cada Pi, aunque exista una posterior en su biblioteca. La política concreta de aplicación y arranque depende de R2-02/03.
5. **Sin servidor se conserva la autonomía y la copia local.** La sincronización con las demás Pi se recupera cuando vuelve el servidor; no se incorpora un sistema de replicación directa entre Pi en esta versión.
6. **No habrá cola de órdenes offline.** Sí se guardarán identidad, biblioteca y asignación esperada: esa persistencia es necesaria para mostrar las discrepancias que has descrito. Actualizar la biblioteca de una Pi al reconectar no equivale a ejecutar una orden antigua.
7. **Coordinación serializada por Pi.** Habrá una sola operación que altere procesos/red a la vez. Las acciones globales se aplicarán a las Pi conectadas y disponibles, mostrando resultados y equipos omitidos; no dejarán acciones programadas para las offline.
8. **Identidad y captura no modifican presets por sorpresa.** El serial identifica al equipo aunque se clone la tarjeta. La selección MIDI se conserva y solo se modifica mediante edición explícita; diferencias del identificador temporal del puerto se tratarán sin sustituir el dispositivo por otro arbitrario.
9. **Supervisión y permisos quedan dentro de los instaladores.** Prepararé systemd también para el servidor Linux, además del cliente. La ejecución manual de desarrollo y la supervisada tendrán un procedimiento que evite duplicados. Resolveré los permisos de red, alimentación, MIDI e I2C de forma documentada.
10. **El diagnóstico tendrá límites.** Elegiré niveles, rotación y un presupuesto de logs configurable, priorizando la investigación posterior y la estabilidad durante el bolo. Las cifras precisas de reintentos, tiempos y recursos se documentarán como parámetros, no como esperas arbitrarias dispersas.
11. **La validación sin hardware cubre la lógica.** Procesos, red y dispositivos tendrán sustitutos para las pruebas automatizadas. La detección física de pantallas, USB, NetworkManager y Stream Deck se comprobará después en la Pi/CT de prueba.
12. **El trabajo de Git ya tiene dirección.** La base revisada es `v2-server-implementation`, no el antiguo `main`. Al comenzar la fase acordada se conservará esa base en `main` y se trabajará la reconstrucción en `v3`, manteniendo tus documentos de requisitos.

**Correcciones opcionales a estas interpretaciones:**

> Ninguna / indicar el número y la corrección.

## 4. Qué hacer con la tabla de pruebas de la primera entrevista

Tus 84 preguntas tienen respuesta. Los «Pendiente» que quedan en la tabla VAL-03 son celdas del formulario; no necesitas rellenarlas todas de nuevo. Tu respuesta «todas las pruebas han de cumplirse» sirve para construir una matriz con resultados verificables a partir de los acuerdos.

Hay dos escenarios que deben adaptarse al alcance que has elegido:

- **Wi-Fi:** queda fuera de v3; la prueba se centra en pérdida y recuperación del cable/red Ethernet.
- **Convivencia con versiones antiguas:** no es requisito de esta reconstrucción, porque has aceptado instalaciones nuevas sin compatibilidad anterior.

Esta es una primera síntesis de los demás casos; las filas señaladas se cerrarán con las respuestas anteriores:

| Prueba | Resultado que ya se puede exigir o decisión pendiente |
| --- | --- |
| El servidor desaparece durante el funcionamiento. | El servicio sigue funcionando; el control local se habilita y queda indicada la pérdida de conexión. |
| El servidor vuelve y todo coincide. | Se conserva la ejecución existente y se recupera el control central. |
| El servidor vuelve y hay discrepancias. | Advertencia naranja y elección del operador entre aceptar o resincronizar. |
| Falta el MIDI elegido antes de arrancar. | Reintentos limitados, selección conservada y error si no aparece. |
| El MIDI se desconecta durante el uso. | Según R2-07, sin cambiar silenciosamente el dispositivo configurado. |
| Se pide un arranque duplicado. | No se duplican procesos ni efectos; resultado ligado a la operación. |
| Coinciden órdenes locales y remotas. | Un único coordinador aplica el bloqueo y comunica qué operación ha aceptado. |
| Falla el cambio de servicio o la preparación de su red. | Standby con error; no se recupera automáticamente el servicio anterior. |
| Se cambia servicio correctamente. | Parada, retirada de VLAN, preparación de nueva red, obtención/verificación de IP y arranque, en ese orden. |
| Se pasa a standby. | Sin servicio operativo ni VLAN de servicio; administración y agente siguen disponibles. |
| Se guarda un preset utilizado por varias Pi. | Según R2-02; conservar revisiones y aplicar únicamente a los destinos que acordemos. |
| Aparecen cambios concurrentes u offline. | Se conservan y se ofrece resolver el conflicto sin sobrescritura silenciosa. |
| Se pierde Companion. | Satellite espera la reconexión; no se altera la red ni se reinicia todo por ese motivo. |
| Cae Satellite. | Recuperación limitada y error visible si no se logra. |
| No hay pantalla o falla su driver. | El agente y los servicios continúan; el fallo queda diagnosticado. |
| Hay un timeout de operación o una desconexión. | Según R2-04; no dar por terminado un trabajo que siga ejecutándose ni repetirlo a ciegas. |
| Se reinicia el agente o se corta alimentación. | Según la tabla de R2-03; persistencia coherente y recuperación del estado acordado. |
| Se solicita apagado o reinicio del dispositivo. | Cierre común y ordenado; parada de procesos y limpieza antes de ejecutar la acción del sistema. |
| Los logs alcanzan su presupuesto o un archivo de datos es inválido. | Rotación acotada; error identificable y conservación de los datos recuperables. |
| Falla una actualización. | Preparación previa, resultado visible y vía documentada de recuperación. |
| Funcionamiento prolongado durante una o dos semanas. | Objetivo de validación en hardware; las mediciones y pruebas automatizadas previas no sustituyen el tiempo real de funcionamiento. |

## 5. Cierre de esta ronda

Las diez aclaraciones tienen respuesta. Se han contrastado con `REFACTOR.md` y con las precisiones de la conversación. La última precisión recibida es: **se silencia toda la salida OSC de la Pi mientras esté abierto el editor de añadir o editar rutas**, aunque Learn no esté armado.

No quedan decisiones de producto que impidan preparar la implementación. Los detalles técnicos delegados se elegirán y documentarán durante el trabajo. No hace falta una tercera entrevista.

Esta revisión únicamente consolida requisitos. Todavía no se ha modificado la aplicación ni reorganizado las ramas.

## 6. Resoluciones consolidadas para implementar v3

Esta sección cierra los cruces de las propuestas anteriores. El resto de requisitos de la primera entrevista y de esta revisión sigue vigente.

### 6.1. Edición, guardado y aprendizaje

- En el contexto de una Pi en standby se puede editar cualquier preset de MIDI o Satellite.
- Con un servicio activo se puede editar únicamente el preset que esa Pi está ejecutando, incluidas sus opciones generales. Esto también permite editar el preset activo de Satellite.
- Los cambios del borrador se aplican al guardar. Cambios compatibles, como las rutas MIDI, se aplican en caliente; los que exigen reinicio siguen la secuencia de parada, preparación de red y arranque. Las referencias genéricas a «reiniciar al guardar» de R2-02 no obligan a reiniciar por un cambio de rutas.
- Mientras esté abierto el editor de añadir/editar rutas se silencia **toda** la salida OSC de esa Pi. Esto incluye el tiempo anterior a armar Learn y el posterior a capturar su primer evento, mientras el editor siga abierto.
- Learn retiene el primer evento como dato del borrador hasta confirmar o descartar. Ese dato no se convierte en un envío OSC posterior.
- Cerrar o cancelar el editor libera el silencio y continúa con la última revisión aplicada. Si se pierde la sesión de edición, debe liberarse también ese modo de captura/silencio de forma controlada.
- Guardar sin cerrar el editor no reanuda OSC mientras el editor de rutas siga abierto. Los eventos recibidos durante el silencio se descartan y no se reproducen al salir.
- Desde el servidor se elige la Pi que captura MIDI para Learn; el CT no necesita tener el controlador USB conectado. Se aplican las mismas restricciones y reglas de silencio que en la edición local.

### 6.2. Biblioteca compartida y aplicación a equipos

- Guardar en el editor central crea una revisión y distribuye la biblioteca a las Pi conectadas. Por sí solo no cambia las ejecuciones actuales.
- «Guardar y aplicar» actúa únicamente sobre las Pi seleccionadas. La clase de cambio determina si se aplica en caliente o requiere reiniciar el servicio.
- Guardar desde el contexto de una Pi activa aplica el cambio a esa Pi; las demás que usan el mismo preset mantienen su revisión hasta una aplicación explícita.
- Guardar desde standby conserva standby; no arranca el servicio automáticamente.
- Se distingue la revisión de la biblioteca de la que está ejecutando cada equipo. Una revisión nueva disponible no convierte por sí sola una asignación cumplida en discrepancia naranja.
- Se conservan ediciones concurrentes u offline para resolver conflictos; no se elige un ganador únicamente por la hora de los equipos.
- Se permite que distintas rutas compartan dirección OSC. Cada ruta coincidente genera su envío, sin deduplicación automática de las acciones configuradas.
- El borrado de presets activos o asignados se bloquea según la información conocida por el servidor, indicando los equipos que los utilizan. Renombrar conserva la identidad del preset y no reinicia sus servicios.

### 6.3. Arranque y discrepancias con el servidor

- El procedimiento es el mismo tras apagado, corte de corriente, reinicio de la Pi o reinicio del agente.
- Se espera el beacon durante un plazo limitado y configurable. Encontrarlo establece comunicación: la Pi anuncia el estado local que va a recuperar y después informa del resultado real.
- **La Pi recupera su estado local tanto si encuentra servidor como si no.** No aplica automáticamente la asignación del servidor durante el arranque.
- El estado local incluye servicio, preset y revisión aplicada, o standby si ese era su estado. Una biblioteca con revisiones más nuevas no sustituye automáticamente la revisión recuperada.
- Que una Pi aún no esté incorporada al servidor no impide recuperar un estado local válido. Una Pi sin estado o preset válido queda en standby e informa de que necesita configuración.
- El servidor compara la ejecución observada con lo que esperaba. Si difieren, muestra advertencia naranja: «Aceptar» actualiza la asignación esperada; «Sincronizar» solicita aplicar la asignación del servidor.
- La misma comparación se hace al recuperar la comunicación durante el uso. Una reconexión no provoca por sí sola un cambio de servicio.
- Un cambio de servicio fallido termina en standby con error y no restaura el servicio anterior. Ese estado resultante se conserva de forma coherente para la recuperación local.

### 6.4. Operaciones, red y seguimiento

- Un único servicio operativo por Pi y una única operación incompatible sobre procesos/red a la vez.
- Cambiar de servicio sigue este orden: detener el anterior, retirar su VLAN si existe, preparar la nueva VLAN si corresponde, obtener/verificar IP y arrancar el nuevo servicio.
- Los pasos usan comprobaciones reales, plazos definidos y errores explícitos. Fallar un paso no se presenta como éxito ni activa automáticamente el servicio anterior.
- La red de administración Ethernet sin etiquetar se mantiene separada de la VLAN del servicio. La IP fija del preset se guarda y aplica como está definida; el tratamiento de conflictos entre varias Pi queda aplazado.
- Durante una operación se bloquean nuevas órdenes incompatibles, pero siguen las señales de vida y las notificaciones de fase por un canal independiente.
- La tarjeta del equipo puede mostrar una pequeña consola de progreso/logs durante la operación y recuperar su presentación normal al terminar, sin bloquear el procesamiento MIDI/OSC.
- Un timeout de operación con señales de vida presentes se muestra como error de operación, no como desconexión. Si desaparece la comunicación, el resultado puede quedar desconocido hasta recuperar contacto.
- El bloqueo se aplica también en el agente: un timeout visual no permite lanzar otra operación sobre un trabajo que todavía siga ejecutándose.
- No se guardan órdenes para ejecutarlas cuando vuelva una Pi offline. Las acciones globales muestran los resultados de los equipos disponibles y los omitidos.

### 6.5. MIDI/OSC durante el bolo

- Se prioriza la actualidad de los eventos y una latencia baja y predecible.
- Los eventos que no puedan procesarse o enviarse dentro del plazo útil se descartan. No se almacenan colas de eventos pendientes, no se reintentan disparos antiguos ni se reproducen valores atrasados al desaparecer un pico de carga.
- Un fallo de envío no elimina el destino configurado: podrá recibir los eventos nuevos posteriores. Conservar el destino no supone reenviar el evento fallido.
- Logs, actualización de interfaz, métricas, sincronización de presets y seguimiento de operaciones deben evitar bloquear este recorrido.
- Se conserva la conversión funcional de valores existente y se aplica el filtro de canal tanto a notas como a CC. La interfaz usa canales 1–16 y «Todos».
- Si falta el controlador elegido **antes de arrancar**, se comprueba con reintentos limitados; si no aparece, se informa de error conservando el preset.
- Si el controlador se desconecta **durante el uso**, el servicio permanece esperando a ese controlador y se recupera cuando vuelve. No se aplica el cierre tras reintentos de la propuesta original de R2-07, no se retira la VLAN por ese motivo ni se selecciona otro dispositivo.
- Durante esa espera deben seguir disponibles el diagnóstico y el control; no se presentará el servicio como plenamente operativo si falta su entrada.
- Las pruebas incluirán movimientos rápidos de faders, carga simultánea y comprobación de descartes para detectar ráfagas atrasadas. Las mediciones sobre el hardware real fijarán y validarán los objetivos concretos.

### 6.6. Instalación, reinicios de datos y validación

- Cliente: Raspberry Pi 5 con Raspberry Pi OS de 64 bits, usando la imagen normal del instalador. Servidor: CT Debian 12 de Proxmox.
- CT y Pi están siempre en la misma VLAN/subred de administración, presentada sin etiquetar a los equipos. Se mantiene la dirección manual como respaldo del descubrimiento.
- «Restablecer instalación» borra incorporaciones y asignaciones del servidor conservando la biblioteca. «Restaurar de fábrica» permite borrar sus datos de usuario con confirmación y copia de seguridad.
- Un reset del servidor no borra a distancia las bibliotecas de las Pi. Al incorporar una Pi a una biblioteca vacía se ofrece importar sus presets, tratando los conflictos por revisión.
- Se conserva el alcance acordado: dos servicios, pantallas opcionales, instalación y supervisión Linux, sin Wi-Fi, usuarios/roles ni compatibilidad con versiones antiguas.
- La base actual se conservará en `main` y la reconstrucción se hará en `v3`. Los requisitos y las pruebas automatizadas acompañarán esa rama.
- Las pruebas automatizadas comprobarán la coordinación y los casos de fallo mediante sustitutos de hardware y red. La Pi/CT nuevos se usarán después para verificar USB, pantallas, VLAN/DHCP, Satellite y funcionamiento prolongado real.

### Resultado de la revisión

- Aclaraciones R2-01 a R2-10: **respondidas y consolidadas**.
- Silencio de OSC durante la edición de rutas: **aclarado en la conversación**.
- Decisiones de producto bloqueantes pendientes: **ninguna identificada**.
- Detalles técnicos de implementación, límites y organización interna: **delegados al ingeniero y sujetos a documentación y pruebas**.
- Aplicación y ramas: **todavía sin modificar; el trabajo realizado hasta aquí es la entrevista y su revisión**.
