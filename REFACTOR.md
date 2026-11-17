# Entrevista para el refactor de OMI Agent

Estado: **en preparación de requisitos; refactor pendiente de nuestra revisión conjunta**.

Fecha de preparación: 16 de noviembre de 2026.

Este documento es nuestra entrevista: tú decides qué producto necesitas y qué compromisos aceptas; yo traduzco esas decisiones a una implementación mantenible. Puedes corregir cualquier interpretación, descartar mis propuestas y añadir necesidades que el código actual no cubre.

## Cómo rellenarlo

- Escribe debajo de **Respuesta:**. Los identificadores permiten preguntarme, por ejemplo, «¿qué quieres decir en RED-06?».
- Las preguntas marcadas **CLAVE** condicionan varias partes del diseño. Puedes empezar por ellas y volver después al resto.
- Puedes responder **«a criterio del ingeniero»**, **«no aplica»**, **«para más adelante»** o **«pendiente de hablarlo»**. No necesitas elegir librerías ni saber todos los términos.
- Las propuestas son recomendaciones para discutir. Dejarlas sin responder **no significa aceptarlas**.
- Distingue, cuando puedas, entre **lo que ocurre hoy**, **lo que quieres que ocurra** y **lo que sería deseable después**.
- Los ejemplos concretos ayudan más que una especificación perfecta: «uso cuatro Pi, dos VLAN y un portátil sin Internet» es una buena respuesta.
- Usa nombres e IP de ejemplo. No escribas contraseñas, tokens ni otros secretos reales en este archivo.
- No hace falta contestar todo de una sentada. Puedes preguntarme mientras lo rellenas.

Cuando me digas que has terminado, revisaré las respuestas, señalaré contradicciones o huecos y prepararé el alcance y el plan. Empezaremos el refactor cuando hayamos acordado que esa información es suficiente. Completar este documento no pone en marcha cambios por sí solo.

## Índice de bloques

Hay 84 preguntas agrupadas en 21 bloques. El esquema de Markdown de VS Code también permite saltar a cada pregunta.

| Para hablar de… | Bloques |
| --- | --- |
| Referencia y propósito | [1. Versión de referencia](#1-qué-versión-estamos-definiendo), [2. Objetivos](#2-objetivo-prioridades-y-alcance), [3. Entorno](#3-entorno-y-tamaño-del-sistema) |
| Comportamiento del agente | [4. Arranque y autonomía](#4-arranque-autonomía-y-reconexión), [5. Identidad](#5-identidad-de-los-dispositivos), [6. Servicios](#6-servicios-y-coordinación-de-operaciones) |
| Red y datos | [7. Red y VLAN](#7-red-vlan-y-acceso-a-los-equipos), [8. Comunicación](#8-descubrimiento-estado-y-comandos-remotos), [9. Presets](#9-configuraciones-y-presets) |
| Integraciones | [10. MIDI/OSC](#10-midi-y-osc), [11. Satellite](#11-companion-satellite), [12. Otros servicios](#12-oscnum-y-servicios-futuros) |
| Operación diaria | [13. Webs](#13-interfaces-web-y-experiencia-de-uso), [14. Pantallas](#14-pantallas-físicas), [15. Standby y apagado](#15-standby-parada-y-apagado) |
| Mantenimiento | [16. Instalación](#16-instalación-ejecución-y-desarrollo), [17. Logs](#17-logs-y-diagnóstico), [18. Acceso](#18-acceso-y-control) |
| Entregas y validación | [19. Actualizaciones](#19-actualizaciones-y-migración), [20. Aceptación](#20-comprobación-y-criterios-de-aceptación), [21. Plan propuesto](#21-propuesta-de-trabajo-para-discutir) |

## Punto de partida de la entrevista

La revisión corresponde a la copia local de la rama `v2-server-implementation`, commit `138da29`. He seguido los recorridos principales del cliente y el servidor y revisado servicios, instalación y actualizaciones. No he ejecutado el sistema en una Raspberry Pi ni validado el hardware.

Estas observaciones describen el código que he leído; todavía no son requisitos acordados:

| Observación | Dónde aparece | Qué necesitamos decidir |
| --- | --- | --- |
| El cliente coordina API web, procesos, red, comunicación, pantalla y apagado. | `client/client.py`, `client/src/` | Qué responsabilidades tendrá cada componente. |
| Arrancar y parar servicios repite coordinación entre las rutas locales y los comandos remotos. | `client/client.py`, `client/src/net_com_handler.py` | Un recorrido común y sus reglas ante fallos. |
| Hay estado en memoria, `structure.json`, `servicios.json`, `active_config.txt` y archivos del servidor. | Gestores del cliente y `server/storage.py` | Quién es responsable de cada dato y qué persiste. |
| El servidor entrega comandos como respuesta a heartbeats y los retira de su cola; el emisor inmediato del cliente no procesa esos comandos. | `server/app.py`, `client/src/net_com_handler.py` | Confirmaciones, reintentos y duración de las órdenes. |
| El gestor detiene otros servicios al arrancar uno. | `client/src/service_manager.py` | Si la exclusividad es una regla del producto. |
| La gestión de red usa NetworkManager y contiene supuestos sobre `eth0` y una VLAN activa. | `client/src/net_manager.py` | La topología real que hay que soportar. |
| MIDI tiene rutas, aprendizaje y su propia web; Satellite envuelve una aplicación externa y su instalación. | `client/servicios/` | Qué conservar y qué simplificar en cada integración. |
| OSCnum actualmente es un proceso de ejemplo que escribe periódicamente en un log. | `client/servicios/OSCnum/service.py` | Su función futura y si entra en el alcance. |
| La instalación ofrece arranque con cron; la actualización usa Git y reinicia el dispositivo. | `scripts/` | La forma de instalar, supervisar y actualizar que queremos mantener. |
| No he encontrado una suite automatizada de pruebas; hay un script de prueba de UI. | `docs/testers/Testui.py` | Qué recorridos verificaremos antes de sustituir piezas. |

También he leído `notas mentales.txt` y `docs/HELPERS/pensamientos.txt`. Ahí aparecen standby, IDs editables, cierre ordenado, fallos de pantalla, menos ruido y límites de logs, entre otras cosas. Los incorporo a las preguntas como antecedentes que hay que confirmar; algunas notas podrían estar ya resueltas o haber quedado obsoletas.

## 1. Qué versión estamos definiendo

### BASE-01 · CLAVE · ¿Esta rama es la referencia correcta?

El IDE menciona `client/agent/network/models.py`, que no aparece en esta copia. Además, `docs/HELPERS/NUEVO_RESUMEN_COMUNICACIONES.JSON` describe módulos, UDP y SQLite que no corresponden a la implementación HTTP actual. ¿Existe otra rama o copia más reciente que deba estudiar? ¿Esos documentos describen una versión anterior, una propuesta o el objetivo al que quieres llegar?

**Respuesta:**

> revisar github... no se ni lo que es model.py

### BASE-02 · ¿Qué documentación y notas siguen vigentes?

¿Hay algún documento, esquema o lista personal que deba tratar como referencia principal? De las notas antiguas, indica qué sigue pendiente, qué está arreglado y qué ya no quieres. Puedes irlo aclarando en los bloques correspondientes.

**Propuesta:** conservar una única especificación vigente y señalar claramente los documentos históricos.

**Respuesta:**

> habrá que hacer limpieza de archivos y docs... porque es un desastre la verdad, en esta refactorizacion usa las cosas como referencia, pero reah las cosas bien hechas.

### BASE-03 · ¿Qué herramientas o integraciones dependen de esta versión?

Además de sus propias webs, ¿hay scripts, Companion, programas externos, marcadores, automatizaciones o usuarios que dependan de rutas API, puertos, nombres de archivos o formatos JSON actuales? Lista lo que conozcas, aunque no recuerdes sus detalles.

**Respuesta:**

> ni idea....

## 2. Objetivo, prioridades y alcance

### OBJ-01 · CLAVE · ¿Para qué sirve OMI Agent en tu trabajo real?

Describe un uso completo: quién lo prepara, dónde se utiliza, qué conecta y qué resultado necesita. ¿Es una herramienta personal, para un equipo técnico, para instalaciones permanentes, para eventos o para distribuir a otras personas?

**Respuesta:**

> es una herramienta personal de momento. tengo varias raspberrys pis en el control, y quiero que hagan principalemnte midi to osc o de companion satellite, el resto de servicios ya no los usaré.

### OBJ-02 · CLAVE · ¿Qué cinco cosas te hacen perder más tiempo o confianza?

Ordénalas por importancia. Si puedes, añade cómo reproducirlas y qué debería pasar. Distingue los fallos actuales de funciones nuevas que todavía no existen.

**Respuesta:**

> 1. no sabría decirte el orden y tal. pero la aplicación falla de vez en cunado y es un espagueti de seguir, los iframes fallan un poco a veces porque google los bloquea.... me gustaria simplificarlo un poco todo.


### OBJ-03 · CLAVE · ¿Qué debe conseguir la primera versión del refactor?

Ordena tus prioridades: fiabilidad, facilidad de mantenimiento, simplificar instalación, claridad de la interfaz, rendimiento, incorporar funciones o eliminar funciones. ¿Hay una fecha, un evento o una disponibilidad de pruebas que condicione las entregas?

**Propuesta:** una primera entrega centrada en estado coherente y operaciones de servicio fiables, con resultados que podamos comprobar.

**Respuesta:**

> la propuesta me parece bien

### OBJ-04 · ¿Qué podemos simplificar o retirar y qué debe quedar fuera?

¿Hay opciones, pantallas, servicios, compatibilidades o formas de configuración que nunca uses? ¿Qué comportamiento funciona tan bien que prefieres conservarlo? Añade las funciones futuras que ya conozcas, separando «necesarias ahora» de «posibles después».

**Respuesta:**

> si, servicios van a ser solo dos: midi y companion satellite, el resto no lo necesito (lo de las pantallas y eso eso si, me refiero principalmente a los servicios)

## 3. Entorno y tamaño del sistema

### ENT-01 · CLAVE · ¿Qué dispositivos y periféricos debemos soportar?

Rellena lo que sepas; una fila por tipo de equipo es suficiente. Incluye adaptadores Ethernet/USB, pantallas y controladores MIDI que puedan cambiar el comportamiento.

| Uso o equipo | Modelo y RAM | Sistema operativo y arquitectura, si los sabes | Pantalla | MIDI / USB / otros periféricos | Cantidad |
| --- | --- | --- | --- | --- | --- |
| Ejemplo de formato: agente de escenario | Pi … | Raspberry Pi OS … | SSD1306 / UCTRONICS / ninguna | Controlador … | … |
| Tu equipo | Pendiente | Pendiente | Pendiente | Pendiente | Pendiente |

**Respuesta / matices:**

> raspberry pi 5 como cleinte, y usaremos un ct de proxmox como servidor.

### ENT-02 · ¿Dónde debe funcionar el servidor y dónde desarrollarás?

La guía habla de servidor Windows y cliente Linux. ¿Eso sigue siendo suficiente? ¿Necesitas servidor Linux, macOS, cliente simulado en Windows o ejecutar ambas partes en una misma máquina? Indica versiones de Python o de sistema que sea obligatorio conservar, si las hay.

**Propuesta:** separar las dependencias del hardware para poder desarrollar y probar la coordinación desde tu ordenador.

**Respuesta:**

> esto va a cambiar, el servidor sera un ct de proixmox

### ENT-03 · CLAVE · ¿Qué escala y duración de uso esperamos?

¿Cuántos agentes hay hoy y cuántos prevés como máximo? ¿Uno o varios operadores a la vez? ¿Funcionan unas horas, varios días o permanentemente? ¿Son instalaciones independientes con sus propios servidores?

**Respuesta:**

> Agentes hoy / máximo previsto: 5/20
> Operadores simultáneos: 1
> Duración habitual: una semana o dos de continuo idealmente
> Instalaciones independientes: 1

### ENT-04 · CLAVE · ¿Cómo es una red típica y qué acceso tiene a Internet?

Describe o dibuja con texto servidor, Pi, switches, routers, Wi-Fi, VLAN y equipos de destino. ¿El navegador está en el ordenador del servidor o en otro dispositivo? ¿Hay redes aisladas, rangos repetidos o instalaciones sin Internet? Basta un ejemplo representativo con datos ficticios.

**Respuesta:**

> uso netgear serie M (av) con un router pr460s y varios switches, con equipos de todo tipo, dante, algo de video over ip (alguna vez NDI) y sobre todo d3net (disguise) y control de equipos. KVMs IP....

## 4. Arranque, autonomía y reconexión

### ARR-01 · CLAVE · ¿Qué debe hacer una Pi al encenderse?

Opciones: restaurar el último servicio y preset, arrancar siempre en standby, usar una selección de arranque guardada por dispositivo o esperar instrucciones del servidor. ¿La elección debe ser configurable? ¿Hay que arrancar el servicio aunque todavía no haya red, MIDI o servidor disponibles?

**Propuesta:** guardar una política explícita de arranque y distinguirla del estado que había justo antes del apagado.

**Respuesta:**

> idealmente, esperar a ver que dice el servidor, si no lo encuentra, arrancar con el ultimo estado

### ARR-02 · CLAVE · ¿Cuánta autonomía debe tener sin servidor?

Si el servidor no existe al arrancar, o desaparece durante el uso, ¿deben seguir funcionando el servicio y la web local? ¿Puede el operador cambiar servicio, preset y red desde la Pi? ¿Hay alguna operación que dependa obligatoriamente del servidor?

**Propuesta:** mantener los servicios locales operativos al perder el servidor y mostrar la pérdida de conexión por separado.

**Respuesta:**

> justo tu propuesta. el servidor es un orquestador, pero una vez todo el mundo hace lo suyo, el se puede ir y que todo siga funcionando. si se pierde el servidor, el operador podrá conectarse a la pi y cambiar servicio o configurarlo. logicamente, cuando vuelva el servidor, el servidor deberia decir oye, acabo de despertar, y veo una discrepancia entre lo que la pi esta haciendo y lo que yo tengo apuntado que deberia estar haciendo.... y en la web del servidor se indicara con el icono de la pi en naranja, y te de la opcion de resincronizar (forzar a la pi a hacer lo que dice el servidor) o aceptar lo que hace la pi y actualizar l ainformacion en el servidor

### ARR-03 · CLAVE · ¿Qué debe pasar al recuperar el servidor?

Ejemplo: sin conexión cambiaste de MIDI/preset A a Satellite/preset B, mientras el servidor conservaba la selección anterior. Al reconectar, ¿debe respetarse lo que está funcionando, aplicarse lo último solicitado en el servidor o pedir una decisión al operador?

**Propuesta:** informar primero del estado real y aplicar después solo las órdenes que sigan siendo válidas; una reconexión por sí sola no debería reiniciar un servicio.

**Respuesta:**

> está contestado antes, pero en resumen, todo funciona, por cualquier motico se cae el serivodr, y reinnicia dos horas despues, el operador ha cambiado de servicio, el servidor la raspi 1 esta haciendo lo que debe? si la 2? tambien, AHA! la 3 no! y se "chive al usuario" poniendola naranja y con un warning, que al hacer hover diga, algo tipo, servicio no esperado, y dos opciones, resincronizar o aceptar servicio nuevo, si resincronizas, la pi volvera a hacer lo que el servidor tenia guardado, si aceptas, la raspberri sigue con lo suyo y el servidor actualiza su info.

### ARR-04 · CLAVE · ¿Quién manda si hay cambios locales y remotos a la vez?

Ejemplo: alguien pulsa «arrancar MIDI» en la Pi y otra persona pide Satellite desde el servidor. ¿Orden de llegada, prioridad local, prioridad remota o bloqueo durante la operación? ¿Quieres poder dejar una Pi temporalmente bajo control local?

**Respuesta:**

> la pi en realidad manda, porque es la que presta el servicio, pero el servidor informará de que la pi no esta haciendo lo que debe.

### ARR-05 · ¿Puede haber varios servidores y qué ocurre si se reinicia uno?

¿Cada agente pertenece a un servidor concreto? ¿Debe descubrir cualquiera, permitir selección manual o admitir uno de respaldo? Tras reiniciar el servidor, ¿qué debería recuperar sin intervención: agentes, IDs, presets y órdenes pendientes?

**Propuesta:** definir un servidor responsable por agente para evitar que dos servidores den instrucciones contradictorias.

**Respuesta:**

> nunca habrá más de un servidor. lo voy a usar yo, soy el ingeniero de sistemas de los shows que hago, hago el diseño de sistema y la mayoria tambien hago de operador (lo sé, soy un friki, mirame xD) so no tenemos que preocuparnos por usuarios inutiles. tal vez en el futuro...

## 5. Identidad de los dispositivos

### ID-01 · ¿Qué significan serial, ID numérico, nombre y hostname?

¿El número identifica una posición física, el orden de la lista o tiene significado para MIDI/Satellite? ¿El nombre visible puede ser independiente del hostname del sistema? ¿Qué debe seguir identificando al mismo equipo tras cambiar IP o reinstalarlo?

**Propuesta:** una identidad técnica estable y, por separado, un número y un nombre editables para el operador.

**Respuesta:**

> como los clientes son raspberrry pi, se le puede consultar el serial, el serial es el identificador unico (como si fuese su mac sabes? su id unico en el mundo) el ID es el index de la raspberry, por ejemplo, dentro de este proyecto eres la "1", el nombre, es el nombre que el operador quiera asignarle, por ejempllo, foh 1 o boh 1 o camerino 1... el hostname es el hostname de la raspberry pi.

### ID-02 · ¿Qué reglas deben cumplir los IDs editables?

Tus notas piden cambiarlos desde el servidor y reflejarlos en el cliente. ¿Deben ser únicos? ¿Empiezan en 0 o en 1? ¿Permitimos huecos? Si se cambia el ID de una Pi desconectada, ¿se aplica al reconectar? ¿Se puede intercambiar el número de dos Pi?

**Respuesta:**

>  empiean en uno, si permitimos huecos (alomejor hago la primera decena foh, la segunda decena boh...) y si, aunque el id es unico, se pueden intercambiar, ¿cuando se asigna el ID? en el broadcasting del servidor, si ve una raspberry nueva, se le da a onboard, en ese onboard se le da la siguiente id disponible, que luego se puede editar y cambiar.

### ID-03 · ¿Qué debe pasar al clonar una tarjeta o eliminar un agente?

¿Sueles preparar varias Pi clonando una tarjeta? ¿Una sustituta debe heredar el nombre, ID y presets del equipo antiguo? Si borras un agente del servidor y vuelve a aparecer, ¿se registra de nuevo, pide aceptación o se mantiene excluido?

**Respuesta:**

> si,  al final lo importante es el serial number, el hostname puede cambiar bla bla bla, pero el serial number es el que identifica a ESA raspberry py en la base de datos

## 6. Servicios y coordinación de operaciones

### SVC-01 · CLAVE · ¿Solo puede funcionar un servicio por Pi?

Ahora el gestor detiene los otros al arrancar uno. ¿Es una regla que debemos conservar o necesitas MIDI y Satellite simultáneamente? ¿El modo de configuración de otro servicio cuenta dentro de esa exclusividad?

**Propuesta:** conservar un único servicio operativo si cubre el uso real; soportar simultaneidad cambia también las reglas de red, pantalla y selección.

**Respuesta:**

> UN UNICO SERVICIO SIEMPRE, de hecho, si iniciamos un servicio diferente PRIMERO para el servicio anterior y despues ejecuta el nuevo (con las reglas de red pantalla bla bla bla porsupuesto)

### SVC-02 · ¿Qué permite exactamente el modo configuración?

¿Debe abrir solo la web, capturar MIDI para aprender, emitir mensajes de prueba, conectar con Companion o modificar VLAN? ¿Entrar a configurar debe parar el servicio activo? ¿Al salir se restaura lo anterior o se queda en standby?

**Propuesta:** separar claramente «editar una configuración» de «ponerla en funcionamiento» y hacer visibles los efectos que tenga el modo de prueba.

**Respuesta:**

> la configuracion (que solo se pueden configurar con ningun servicio activo) es para editar una configuracion concreta, ahoramismo por ejemplo, no se termina de entender del todo bien si estoy en play y en editar, porque es un poco confuso, pero si, en midi por ejemplo, tengo que poder capturar paquetes midi para aprenderlos y tal. pero sin mandar osc claor, y con companion, configurar el config.json antes de arrancar la aplicacion. 

### SVC-03 · CLAVE · ¿Cuándo consideramos que un servicio está listo?

¿Basta con que el proceso esté vivo, debe responder su web o debe tener MIDI/Companion conectado? ¿Qué estados intermedios necesitas ver? ¿Cuánto es razonable esperar al arrancar cada servicio?

**Propuesta:** distinguir proceso arrancado, interfaz disponible y conexión al equipo externo; un timeout no debe mostrarse como éxito silencioso.

**Respuesta:**

> tu propuesta es correcta

### SVC-04 · CLAVE · ¿Qué hacemos si falla un cambio de servicio o preset?

Ejemplo: MIDI funciona, pides Satellite, se detiene MIDI y Satellite no arranca. ¿Volvemos automáticamente al servicio y red anteriores, quedamos en standby con error o esperamos tu elección? ¿Es aceptable una breve interrupción para hacer el cambio?

**Respuesta:**

>  nos quedamos en estandby con el error, tambien reflejado en el servidor con su tarjeta en rojo. no resetamos la anterior. 

### SVC-05 · ¿Cómo se recupera un servicio que se cae durante el uso?

¿Reinicio automático, intervención manual o política por servicio? ¿Cuántos reintentos y cuánto tiempo de espera aceptarías? ¿Qué debe pasar si entra en un ciclo de caídas o desaparece su dispositivo externo?

**Propuesta:** reintentos limitados y visibles, con un estado de fallo cuando no haya recuperación; el agente y su web deben seguir disponibles.

**Respuesta:**

> tu propuesta es correcta

### SVC-06 · ¿Qué debe pasar al recibir otra operación mientras hay una en curso?

Arrancar, parar, aplicar red y cambiar preset pueden afectar a los mismos recursos. ¿Prefieres rechazar la segunda orden explicando el motivo, ponerla en cola o sustituir la anterior? ¿Parar o apagar debe tener prioridad? ¿Necesitas cancelar operaciones largas?

**Propuesta:** un único coordinador para las operaciones locales y remotas, con reglas explícitas de orden y cancelación. Pulsar dos veces no debería arrancar dos procesos.

**Respuesta:**

> tu propuesta es la correcta, de hecho, cunado mandemos una orden desde el servidor, este deberia "bloquear" la interactuacion con el cliente hasta que este haya completado la tarea requerida, por ejemplo, le decimos, que queremos que una pi arranque en midi, pues en cunato le demos a aceptar, su tarjeta se ponga como gris y no funcionen los botones, hasta que la pi haya respondido ok, estoy lista, entoncessu tarjeta se pone verde o roja o naranja si ha fallado o algo y nos permitirá volver a mandarle mensajes

## 7. Red, VLAN y acceso a los equipos

### RED-01 · CLAVE · ¿Qué red usamos para administrar y cuál para el servicio?

¿Servidor y web local van por Ethernet sin etiquetar, por Wi-Fi o por una VLAN de gestión? ¿MIDI/OSC y Satellite usan esa misma red u otra VLAN? ¿Debe mantenerse siempre un acceso de administración mientras cambia la red del servicio?

**Propuesta:** identificar por separado la conectividad de administración y la que necesita el servicio, incluso si físicamente comparten cable.

**Respuesta:**

> aqui: siempre le llegara una VLAN destagueada. a veces ademas taggeadas. si es destagueada, asumimos que por ahí le vendra internet, comunicacion con el servidor bla bla bla, con los equipos a comunicarse... si le añadimos vlan, está sera porque el servicio ha de conectarse con equipos en otras VLANS, pero el servidor, internet y tal siempre vendran por el mismo sitio, el trafico sin taggear.

### RED-02 · CLAVE · ¿Qué interfaces y perfiles puede gestionar OMI?

¿Solo `eth0` o también Wi-Fi, adaptadores USB y otros nombres de interfaz? ¿La Pi se dedica por completo a OMI o comparte configuración con otras aplicaciones? ¿Hay perfiles de NetworkManager o VLAN creados a mano que deban conservarse?

**Propuesta:** que OMI modifique y retire únicamente los perfiles que gestiona explícitamente, con una interfaz seleccionable.

**Respuesta:**

> wifi de momento no, adaptadores usb... por ahí conectaré los streamdeck o controladoras midi, pero nada raro.y la pi se dedica UNICAMENTE a omi

### RED-03 · CLAVE · ¿De dónde sale la VLAN y cuántas necesitamos a la vez?

Ahora hay VLAN global o tomada del servicio/preset. ¿Quieres mantener ambas opciones? ¿Cuál tiene prioridad? Al pasar a standby, ¿se elimina la VLAN del servicio? ¿Hay que soportar varias VLAN simultáneas o una sola basta?

**Respuesta:**

> la raspberry tiene siempre, trafico sin taggear por defefcto, por ese trafico irá la comunicacion con el servidor, su interfaz web, internet suponiendo que ese trafico tenga router y tal. entonces ¿para que las VLAN? muy sencillo, alomejor tengo un dispositivo al que quiero mandar OSC con midi, pero esta en una VLAN diferente a la que la pi usa para comunicarse con su servidor, entonces, habilitaremos una vlan 0.100 o lo que sea para poder comunicarnos con ese equipo. las VLAN SIEMPRE estan atadas a un servicio, si encendemos el servicio, su vlan si esta solicitada tiene que encenderse, si paramos el serivico, destruimos la configuracion de dicha VLAN. nunca habra trafico taggeado de multiples vlan que sean relevantes solo para la pi.

### RED-04 · ¿Qué debe ocurrir sin cable, con Wi-Fi o sin DHCP?

¿Puede arrancar un servicio por Wi-Fi? Si no llega una IP por DHCP, ¿esperamos, usamos una IP de respaldo o mostramos error manteniendo la web accesible por otra interfaz? ¿Se reconfigura automáticamente al conectar después el cable?

**Respuesta:**

> no compliquemos la parte de red, simepre por cable.

### RED-05 · CLAVE · ¿Qué reglas necesitamos para IP fija, gateway, DNS e Internet?

En notas antiguas aparece pérdida de Internet al fijar IP. Describe un caso deseado: IP/máscara de gestión, IP/máscara del servicio y qué red debe aportar la salida a Internet. ¿Las VLAN de servicio deben poder funcionar sin gateway? ¿Necesitamos nombres DNS, IPv6 o basta IPv4?

**Propuesta:** validar el perfil completo antes de aplicarlo y evitar que una VLAN de servicio cambie por accidente la salida de administración.

**Respuesta:**

> minimo dhcp / ip fija (ip y netmask, dns y gateway opcionales)

### RED-06 · CLAVE · ¿Cómo recuperamos el acceso tras un cambio de red fallido?

Si aplicas una IP incorrecta y pierdes la web, ¿quieres una reversión automática si no confirmas el cambio en cierto tiempo? ¿Dispones de acceso físico, teclado, consola serie o SSH por otra interfaz? ¿Qué interrupción máxima aceptarías al cambiar de perfil?

**Propuesta:** para cambios que afecten al acceso, conservar el perfil anterior y establecer un mecanismo comprobable de recuperación. Dime si eso encaja con tu uso.

**Respuesta:**

> de momento tendré cuidado xP si la lio tanto, conecto un monitor teclado y raton y lo arreglo a mano xD aunque es buena observación, pero complica la movida. esto de momento no, confiemos en el operador, y si se queda out, pos que conecte tecaldo y raton...

### RED-07 · ¿Cómo deben salir OSC y las conexiones de los servicios?

Tus notas dicen que el binding del servicio no hace falta. ¿La intención es dejar que las rutas del sistema elijan la interfaz? ¿Hay destinos con rangos de IP repetidos, broadcast o casos donde haya que forzar una salida concreta? Da un ejemplo de envío OSC y otro de Satellite.

**Propuesta:** centralizar las decisiones de red y evitar configuraciones duplicadas dentro de cada servicio; concretar el envío según tu topología real.

**Respuesta:**

> buena pregunta.... yo dejaria que el os se encargue honestamente, soy cuidadoso con las redes y nuevamente, lo voy a suar yo... tampoco tenemos que hacerlo a prueba de chimpances, nuestros usuarios son técnicos, estan acostumbrados a lidiar con ips, no va a haber una VLAN en la misma subnet que el trafico sin taggear u otra subnet

## 8. Descubrimiento, estado y comandos remotos

### COM-01 · ¿Hay alguna razón para mantener o cambiar el protocolo actual?

Esta rama usa descubrimiento UDP y comunicación HTTP. ¿Hay restricciones de firewall, red o compatibilidad que lo condicionen? ¿Algún sistema externo habla ya este protocolo? Puedes delegar la elección técnica si te importa únicamente el resultado.

**Propuesta:** conservar los mecanismos que cubran el uso y compartir un contrato de mensajes validado entre cliente y servidor. Cambiar de transporte requeriría una ventaja concreta.

**Respuesta:**

> me parece bien si te parece bien.

### COM-02 · ¿Cómo debe encontrarse el servidor?

¿Descubrimiento automático, dirección manual, nombre DNS o combinación? Si el portátil tiene Ethernet, Wi-Fi y VPN, ¿debe poder elegirse la interfaz anunciada? ¿Los agentes pueden estar en otra subred donde no llegue el broadcast?

**Propuesta:** descubrimiento automático con configuración manual de respaldo y una dirección de servidor que podamos verificar.

**Respuesta:**

> tu propuesta me gusta. ya te digo, mi idea original era un beacon broadcast del servidor, indicando su ip y lo que sea necesario (puerto o lo que tu consideres) y las pis se presentan, si estan el la base de datos, pos ala se comunican, el servidor comprueba el estado de la pi coincida con lo que el tiene de esa pi en su base de datos y lo de siempre, verde si bien amarillo si mal rojo si error. si la pi no estaba en la base de datos, que aparezca una seccion abado de discovered, y se le pueda hacer onboard.

### COM-03 · CLAVE · ¿Qué significan online, ocupado y offline y con qué tiempos?

Tus notas piden eliminar «away». ¿Seguimos con esos tres estados visibles? ¿En cuántos segundos quieres detectar una desconexión y con qué frecuencia actualizar la información? ¿Una instalación o actualización larga puede seguir enviando señales de vida? ¿Cómo quieres mostrar un servicio con error si la Pi está conectada?

**Propuesta:** separar conexión, operación en curso y salud del servicio. Un servicio averiado puede convivir con un agente online; ocupado debe seguir comunicándose.

**Respuesta:**

> no pasa nada, aunque lo que propones es correcto, podemos pausar la comunicacion y dejar que la pi haga lo suyo, y luego la pi vuelva con el resultado de la operación, con un timeout de uno o dos minutos alomejor.

### COM-04 · CLAVE · ¿Qué confirmación necesitas de una orden?

¿Debemos distinguir «enviada», «recibida», «ejecutándose», «terminada» y «fallida»? Si se corta la conexión después de recibir una orden, ¿quieres consultar su resultado al reconectar? ¿Cuánto tiempo esperar antes de mostrar que el resultado es desconocido?

**Propuesta:** cada operación con identificador, resultado y tratamiento de duplicados. Reenviar una petición por un fallo de red no debería repetir sus efectos cuando ya se ha aplicado.

**Respuesta:**

> tut propuesta me parece bien

### COM-05 · CLAVE · ¿Qué órdenes pueden esperar a una Pi desconectada?

¿Arrancar servicio, cambiar preset, standby, actualizar, reiniciar y apagar tienen las mismas reglas? ¿Cuánto tiempo siguen siendo válidas? Ejemplo: alguien pulsa «apagar todos» y una Pi desconectada vuelve dos días después; ¿debe apagarse entonces? ¿Las órdenes deben sobrevivir al reinicio del servidor?

**Propuesta:** definir caducidad y persistencia por tipo de orden; separar una selección de arranque duradera de una acción puntual.

**Respuesta:**

> ninguna, no quiero una base de datos o cola de espera de pis offline o algo así... se conecta, se comprueba lo que está haciendo y se dice si coincide con lo guardado y con lo no guardado, una pi que no responde a una orden, tras un timepo de timeout se pone en offline

### COM-06 · ¿Cómo se aplican órdenes sucesivas y acciones sobre todos los agentes?

Si pides MIDI, después Satellite y luego standby antes de que la Pi responda, ¿ejecutamos todo o solo el destino final? En una acción global, ¿incluimos desconectados, todos los registrados o un grupo seleccionado? ¿Quieres ver el resultado de cada Pi y reintentar únicamente las que fallen?

**Respuesta:**

> esta respondido antes, mando la orden la pi dice: me pongo con ello, el servidor la pone en ocupada, (lo que si podemos hacer almejor es que la pi mande mensajes de milestones del proceso en el que esta, tipo, midi parado, red limpiada, red configurada nueva, iniciando servicio, servicio iniciado) pero durante el proceso en el que la pi está ocupada, no se le pueden enviar más ordenes desde el servidor, en cunato el recibe el "me pongo con ello" bloquea el envio de nuevas ordenes hasta que la pi le diga, vale ya estoy, resultado ok, o advertencia o error. 

## 9. Configuraciones y presets

### CFG-01 · CLAVE · ¿Quién es responsable de los presets y cómo resolvemos conflictos?

Ejemplo: existe «Show A» en servidor y Pi, con contenidos distintos. ¿Prevalece servidor, cliente, versión más reciente o se conservan ambos para decidir? ¿Editar localmente debe publicar el cambio automáticamente? ¿Una sincronización puede sustituir un preset que se está usando?

**Propuesta:** sincronización explícita con revisión identificable y conflictos visibles, evitando sobrescrituras silenciosas.

**Respuesta:**

> generalmente la más reciente manda, evitar sobreescrituras sinlenciosas, perguntar en caso de duda ^^

### CFG-02 · ¿Los presets son compartidos por instalación o propios de cada Pi?

¿«Show A» debe significar lo mismo en todos los equipos? ¿Necesitas una biblioteca central, presets privados por dispositivo o ambas? ¿Se asigna una copia independiente al lanzar o se mantiene una referencia a un preset compartido?

**Respuesta:**

>Todas las Pi mantienen una copia local de la biblioteca central. Guardar un preset distribuye una nueva versión, pero actualizar los servicios que ya lo ejecutan requiere una acción explícita. Los cambios simultáneos u offline deben conservarse y mostrar el conflicto, sin sobrescrituras silenciosas.

### CFG-03 · CLAVE · ¿Qué datos viajan con un preset y cuáles pertenecen al dispositivo?

Ahora aparecen juntos rutas MIDI, entrada MIDI, destino OSC, puertos web e información de red. Al copiar un preset de Pi A a Pi B, ¿deben viajar también IP fija, VLAN, puerto web y nombre del controlador? ¿Qué valores deberían adaptarse a la Pi de destino?

**Propuesta:** separar contenido funcional, perfil de red y ajustes propios del dispositivo donde eso facilite reutilizar presets.

**Respuesta:**

> El preset contiene toda la configuración necesaria para ejecutar el servicio: entrada y rutas MIDI, destinos, puertos y configuración de la VLAN.

Si el servicio requiere una VLAN con IP fija, esa dirección, máscara y gateway se guardan en el preset y se aplican en la Pi que lo ejecute. Cada Pi conserva por separado su identidad y configuración de red local de administración.

La gestión de posibles conflictos al ejecutar simultáneamente un preset con la misma IP fija en varias Pi queda pendiente de definir más adelante.


### CFG-04 · ¿Qué significa guardar, seleccionar y aplicar?

¿Guardar modifica inmediatamente el servicio activo o debe existir un botón «aplicar»? ¿Seleccionar un preset parado puede afectar a la red? ¿Editar rutas MIDI, cambiar un destino OSC y cambiar VLAN deben seguir el mismo recorrido? ¿Qué cambios pueden causar una interrupción aceptable?

**Propuesta:** guardar y aplicar como acciones diferenciadas cuando haya efectos sobre un servicio en uso; recargar únicamente lo que haya cambiado.

**Respuesta:**

> tenog un servicio encendido lo edito, en el momento de guardar, guardo la info en el archivo, paro el servicio y lo renicio y ya cogera la configuracion nueva, seleccionar un preset es con que preset voy a iniciar el servicio, aplicar y guardan son lo mismo si no me equivoco

### CFG-05 · ¿Qué reglas deben tener los nombres y las operaciones sobre presets?

¿Quieres crear, duplicar, renombrar, exportar, importar y borrar desde ambas interfaces? ¿Puede borrarse el activo? ¿Debe existir siempre un «Default» protegido? ¿`default` y `Default` deben tratarse como el mismo nombre? ¿Los archivos tienen que seguir siendo editables a mano?

**Respuesta:**

> no se ditan a mano, que pereza todo con interfaz, el default es el primero que sale, pero no tiene que estar protegido, de hecho, podemos no tener un preset y al arrancar nos diga, oye, escoje un preset no? y si, es sensible a mayusculas y minusculas

### CFG-06 · ¿Qué debemos conservar y recuperar de los datos actuales?

¿Cuántos presets reales hay y qué formatos usan? ¿Los archivos de `docs/HELPERS/` son ejemplos válidos? ¿Necesitas importar instalaciones anteriores, exportar una instalación completa o recuperar un archivo corrupto? ¿Prefieres una migración automática con copia de seguridad o una exportación/importación revisada?

**Propuesta:** formatos con versión, validación antes de aplicar y conservación del original durante una migración. Un error al leer un archivo debe quedar visible.

**Respuesta:**

> tu propuesta me parece bien

## 10. MIDI y OSC

### MIDI-01 · CLAVE · ¿Qué comportamiento MIDI/OSC necesitamos preservar exactamente?

Describe tus controladores y receptores reales. El código revisado contempla notas y CC. ¿Necesitas además program change, pitch bend, aftertouch, SysEx, clock o salida MIDI? Separa compatibilidad imprescindible de ampliaciones futuras.

**Respuesta:**

> no, con notas cc y velocidad me vale, son comandos midi sencillos.

### MIDI-02 · ¿Qué debe enviar cada tipo de evento?

Ahora existen salidas float, int, bool y constante. Para pulsar y soltar una nota, `note_on` con velocidad cero y cambios CC, ¿qué valor y cuántos mensajes esperas? ¿Los floats van de 0 a 1 y los enteros de 0 a 127? ¿Una constante se envía también al soltar? ¿Necesitas disparo, pulsador momentáneo o alternancia?

**Propuesta:** acordar ejemplos de entrada y salida y usarlos como pruebas de compatibilidad, corrigiendo las diferencias que identifiquemos como errores.

**Respuesta / ejemplos:**

> comom estaba funcionando ahora me vale.

### MIDI-03 · ¿Cómo se combinan las rutas y cómo se muestran los canales?

Si varias rutas coinciden con un evento, ¿se ejecutan todas o solo la primera? ¿Puede haber varias rutas con la misma dirección OSC? ¿El orden tiene significado? ¿La interfaz debe mostrar canales 1–16 aunque internamente se representen como 0–15? ¿Canal vacío equivale a todos?

**Respuesta:**

> se envian todas, el orden no es del todo relevante, porque serán milisegundos, 1-16 + todos.

### MIDI-04 · ¿Cómo identificamos y recuperamos las entradas MIDI?

¿Una entrada por servicio o varias simultáneas? Si se desenchufa el controlador y vuelve con otro nombre o número de puerto, ¿debe reconectarse solo? Si hay dos iguales, ¿cómo distingue el operador cuál usar? ¿Debe seguir disponible la web mientras falta el controlador?

**Respuesta:**

> ahoramismo es un poco royo, porque como ejecute el servicio y sin querer no haya conectado la controladora midi, no solo no lo pilla, sino que cambia el preset a la controladora midi interna de la raspberry, por lo que si conecto no me basta con reiniciar el servicio tengo que editarlo para ponerlo como estaba antes, mi propuesta? que antes de arrancar el servicio, se compruebe si esta la controladora midi, si no esta, de un error "dispositivo midi no encontrado" y lo vuelva a intentar unas 5 o 6 veces, si no lo encuentra en ese periodo, pare el proceso y de un error. cunado conecte la controladora, le volveré a dar sabes? y listo.

### MIDI-05 · ¿Cómo debe funcionar el aprendizaje y la edición durante el uso?

¿Se captura el primer evento y se bloquea hasta confirmar, o se actualiza con cada evento? ¿Durante learn debe seguir enviándose OSC? ¿Se permite aprender mientras está otro servicio activo? ¿Guardar o reordenar rutas debe aplicarse en caliente? ¿Qué controles de prueba necesitas?

**Respuesta:**

> learn NO envia OSC, primer evento se registra, solo se puede hacer learn en dos situaciones: editando el preset o con el servicio activo en añadir o editar ruta, en ninguna de esas tres situaciones enviamos OSC.

### MIDI-06 · ¿Qué garantías y diagnósticos necesitas para los destinos OSC?

¿Varios destinos, broadcast, nombres DNS, diferentes puertos por destino o algún otro caso? Si falla un envío, ¿seguimos intentándolo, avisamos o desactivamos el destino? ¿El escaneo por ping se usa de verdad? ¿Qué carga máxima de eventos y latencia tolerable has observado o necesitas?

**Propuesta:** que los diagnósticos y la actualización de la web no bloqueen el envío de eventos. Un fallo puntual no debería borrar la configuración del destino; un ping positivo tampoco equivale a una confirmación de recepción OSC.

**Respuesta:**

> como está ahora...

## 11. Companion Satellite

### SAT-01 · ¿Qué parte de Satellite utilizas y qué debe configurar OMI?

¿Qué versión de Companion/Satellite y qué superficies usas? ¿Solo IP/puerto de Companion y plugins de superficies, o hay más opciones imprescindibles? ¿Qué aporta la web propia de OMI frente a configurar Satellite directamente?

**Respuesta:**

> la ultima, de hecho, tengo una movida implementada para actualizarla si fuese necesario. uso streamdecks solamente, que aporta omi? que lo puedo configurar desde el servidor, por ejemplo, si tengo 15 raspberrys pi's, con 15 streamdecks, configuro el preset en el servidor, y ejecuto satellite apuntando a la instacia de companion desde el servidor, no tengo que ir raspberry por raspberry. ¿cambia el puerto de escuhca de companion? lo aplico a las 15 a la vez 

### SAT-02 · ¿Cómo quieres instalar y versionar Satellite?

Ahora conviven instalación en el instalador general y preparación/compilación desde el servicio. ¿Debe venir preparado, instalarse bajo demanda o seguir compilándose en la Pi? ¿Necesitas actualizarlo independientemente de OMI? ¿Mantienes cambios propios en su código? ¿Debe arrancar sin Internet una vez instalado?

**Propuesta:** un único recorrido de instalación y una versión conocida por entrega; arrancar un servicio ya instalado debería poder hacerse sin descargar dependencias.

**Respuesta:**

> me parece bien tu propuesta.

### SAT-03 · ¿Qué significa que Satellite funciona y cómo debe recuperarse?

¿Mostramos por separado wrapper/web, proceso Satellite y conexión con Companion? Si Companion se desconecta o Satellite se cae, ¿qué debe reiniciarse? ¿Cuándo está justificado tocar VLAN o reiniciar todo el servicio? ¿Hay que conservar opciones específicas al actualizar?

**Respuesta:**

> si companion se cae, mala suerte, no tocamos nada, nos quedamos esperando a la conexion. si satellite se cae, intentamos levantarlo y si da fallo pues lo anotamos, en el futuro si esto fallase mucho o algo, ya nos pelearemos con ello.

## 12. OSCnum y servicios futuros

### EXT-01 · ¿Qué hacemos con OSCnum y cómo imaginas añadir servicios?

El código actual de OSCnum solo escribe mensajes periódicos. ¿Es una prueba prescindible o un servicio pendiente con una función concreta? ¿Qué futuros servicios prevés y quién los añadirá? ¿Te basta declarar cómo arrancarlos, pararlos, consultar su salud y localizar su web/configuración?

**Propuesta:** un contrato pequeño para integrar servicios como procesos independientes. Conservar un ejemplo mínimo solo si sirve para desarrollar o verificar ese contrato.

**Respuesta:**

> oscnum deprecated, dead, mort,

## 13. Interfaces web y experiencia de uso

### UI-01 · ¿Qué quieres unificar visual y funcionalmente?

¿Mantenemos el aspecto actual, lo pulimos o hacemos un rediseño? ¿Qué tareas corresponden al panel central y cuáles deben seguir en el local? ¿Quieres que términos, estados y acciones sean iguales en ambos? ¿Debemos soportar móvil/tablet y qué idioma prefieres?

**Propuesta:** primero vocabulario y comportamiento coherentes, conservando las tecnologías actuales si cubren las necesidades. El rediseño visual puede tener una entrega propia.

**Respuesta:**

> el frontend no me quita el sueño, me parece bien como esta o si consideras que se puede refinar, o hace falta usar algun framework, up to you, me fio :) he visto lo que ahces y siempre me sorprendes para bien en ese aspecto :)

### UI-02 · CLAVE · ¿Cómo son tus tres recorridos principales en la interfaz?

Por ejemplo: preparar todas las Pi para un evento, cambiar una Pi de servicio durante el uso, o diagnosticar una que no conecta. Para cada recorrido, indica desde qué pantalla partes y qué información necesitas para darlo por terminado. ¿Qué acciones sobre grupos echas en falta?

**Respuesta:**

> 1. preaparo las pis, les asigno tareas y dejo el serividor de fondo un poco como watchdog
> 2. si se rompe una pi en directo o necesito añadir una, ahí tengo el servidor para ayudarme, muchas veces llevo conmigo una o dos pis mas por si una se rombpe, rapidamente sacarla, conectarla, asignarle es servicio necesario (por eso la copia local de los presets) y a funcionar
> 3. si falla, lo investigo ya en casa o en la nave, en directo no suelo tener tiempo para debuguear salvo que este de ensayos o tranaquilo, pero no suele ser el caso xD

### UI-03 · ¿Desde dónde debe poder abrirse la web de cada servicio?

Ahora se usan puertos propios e interfaces embebidas. ¿El navegador del operador puede llegar directamente a las IP y puertos de cada Pi? ¿Quieres acceso a través del servidor, enlaces en otra pestaña o conservar el iframe? ¿Hay firewalls o VLAN que impidan alguna de esas opciones?

**Respuesta:**

> fuck iframes, son un dolor de cabeza con chrome... solo si es imprescindible (como companion) el resto no hace falta ifrannes, mi idea es si hay servidor, la pi no necesita levantar su interface, solo si se cae el server, sabes? editar presets, asiganrlos aprar iniciar servicios desde el servidor nada más, que se cae el servidor? todo el mundo lanza su interface por si acaso, que vuelve el servidor? si hay discrepancias no cierran su servidor propio hasta que el servidor diga que hacer, que esta todo bien? se cierra el servidor de la pi. ya no hace falta. (tal vez lo cambiemos en le futuro, de momento que se quede así)

### UI-04 · ¿Cómo mostramos progreso, errores y confirmaciones?

¿Quieres poder seguir consultando estado y logs mientras hay una operación? ¿Qué detalles necesita el operador cuando falla? ¿Qué acciones requieren confirmación: apagado global, cambio de red, actualización, borrar preset? El panel local tiene un desbloqueo de emergencia: ¿se usa y qué esperas que haga?

**Propuesta:** mostrar la operación real, su fase y el último resultado. Un desbloqueo visual no debe confundirse con cancelar o terminar el trabajo que sigue ejecutándose.

**Respuesta:**

> tu propuesta esta bien, pero sin ser extremadamente verbosos. 

## 14. Pantallas físicas

### OLED-01 · ¿Qué pantallas hay y cómo se elige el driver?

Confirma modelos SSD1306, UCTRONICS RM0004 u otros, resolución y conexión si las conoces. ¿Autodetección, selección manual o ambas? ¿Hay equipos sin pantalla? ¿Qué condiciones reproducen la pantalla negra que mencionabas en las notas?

**Respuesta:**

> como esta ahora funciona bien, tengo pis enracadas en chasis utronic con pantallas propias y otras en cajas impresas en 3d con pantallas de esas de 4 pines 2noseque noseque, como esta ahora, al menos la parte de LCD funciona (a veces funcionan a medias, pero los drivers y su comprobacion son correctos.)

### OLED-02 · ¿Qué información tiene prioridad y debe coincidir entre drivers?

Ordena: ID/nombre, conexión al servidor, servicio/preset, IP Ethernet, Wi-Fi, VLAN, CPU/temperatura, operación y error. Si hay varias IP, ¿mostramos gestión, servicio o alternamos? ¿Ambos drivers deben presentar la misma información, cambiando solo el dibujo?

**Propuesta:** preparar una única vista del estado y compartir las reglas de presentación entre pantallas.

**Respuesta:**

> tu propuesta me parece bien, lo unico, los 16 (0 al 15 )pixeles de arriba en la pantalla que no es la utronic son amarillos, asi que me gustaria maneter esa estetica de la franja amarilla en la utronic tambien. pero el diseño y tal up to you :)

### OLED-03 · ¿Qué debe ocurrir si falla o se apaga la pantalla?

¿El agente debe continuar y reintentar recuperarla? ¿Necesitas botones físicos, brillo, reposo, orientación o una pantalla final al apagar? ¿Hay funciones del hardware de UCTRONICS que dependan de otro proceso y debamos conservar?

**Propuesta:** que la pantalla sea opcional para el funcionamiento del agente y sus servicios, con fallos visibles en el diagnóstico.

**Respuesta:**

> la pantalla es opcional.

## 15. Standby, parada y apagado

### POW-01 · CLAVE · ¿Qué significa standby y cómo se selecciona?

Tus notas piden poder elegirlo como destino de arranque y desde el servidor, además del botón de parar. ¿Standby significa sin proceso de servicio, pero con agente, red de gestión, pantalla y web disponibles? ¿Qué ocurre con la VLAN del servicio y con la selección que se restaurará al arrancar?

**Respuesta:**

> si, es el estado en el que está una raspberry de backup por ejemplo. comunicada con el servidor, funcionando, pero sin ejecutar ningun servicio. en estandby, latente, esperante de su orden de unirse al frente :)

### POW-02 · CLAVE · ¿Cómo debe ser el cierre y qué hacemos si una parte no termina?

Para parar el agente, reiniciar o apagar la Pi: ¿cuánto puede durar el cierre? Si un servicio no responde, ¿lo forzamos después de un plazo, cancelamos el apagado o esperamos? Si no se puede avisar al servidor, ¿seguimos con el cierre? ¿Hay datos o periféricos que necesiten una secuencia especial?

**Propuesta:** un recorrido de cierre común para orden local, remota, Ctrl+C y terminación del proceso, con plazos y resultados definidos. Reinicio y apagado del sistema se ejecutan al finalizar ese recorrido.

**Respuesta:**

> tu propuesta es correcta

### POW-03 · ¿Qué debe recuperarse después de un corte de corriente o reinicio?

¿Debe seguir seleccionada la misma configuración? ¿Una operación a medias se reintenta o se marca como interrumpida? ¿Es habitual cortar alimentación sin apagar? ¿Necesitas distinguir «apagado solicitado» de «desconectado inesperadamente» en el servidor?

**Respuesta:**

> siempre el ultimo servicio, da igaul porque, siempre el ultimo servicio.

## 16. Instalación, ejecución y desarrollo

### INS-01 · ¿Cómo quieres instalar una Pi nueva y qué puede modificar el instalador?

¿Un comando desde una imagen limpia, asistente interactivo, imagen preconfigurada o clonación? ¿Se instala siempre todo o eliges servicios? ¿I2C, PCIe y ajustes de arranque son obligatorios para todas las Pi? ¿Debe comprobar primero NetworkManager y detenerse con instrucciones si falta algo?

**Propuesta:** instalación repetible, requisitos comprobados y modificaciones del sistema limitadas a lo que necesite ese equipo.

**Respuesta:**

> tanto la pi como el servidor me gustaria algo tipo, que en la documentacion del readme te diga:
apt update && apt install -y curl && curl -fsSL https://raw.githubusercontent.com/bitfocus/companion-pi/main/install.sh | bash

pero en vez de companion logicamente a scripts nuestros, que instalen el servidor y otro para el cliente. 

hardware destino:
raspberry pi cliente
proxmox CT debian 12 normal siempre servidor.

### INS-02 · ¿Cómo se inicia y supervisa OMI al arrancar el sistema?

Ahora hay arranque manual y cron de root. ¿Quieres conservarlos o usar un servicio del sistema con inicio, parada, reinicio y consulta de estado? ¿Debe recuperarse automáticamente si se cae el agente? ¿El servidor Windows también necesita arranque automático o basta ejecutarlo a mano?

**Propuesta:** un mecanismo de supervisión por instalación; en las Pi, valorar un servicio de systemd y evitar dos formas de autoarranque activas a la vez.

**Respuesta:**

> En las Pi, OMI se ejecutará como servicio de systemd, con arranque automático y recuperación ante fallos con reintentos limitados. Debe poder detenerse para mantenimiento y permitir ejecución manual durante el desarrollo, evitando instancias duplicadas. Se retirará el autoarranque mediante cron

### INS-03 · ¿Qué restricciones hay sobre privilegios y dependencias?

¿Las Pi son dedicadas y administradas por ti? ¿Debemos seguir ejecutando todo como root o podemos reservar privilegios para red/apagado y dar acceso específico a MIDI/I2C? ¿Aceptas dependencias de sistema adicionales? ¿Qué parte debe poder funcionar sin instalar MIDI, OLED o Satellite?

**Propuesta:** dependencias opcionales por función y una separación clara de las operaciones que requieren permisos especiales, dentro de la complejidad que aceptemos.

**Respuesta:**

> las pis son mias, compradas por mi, soy el root xD 

### INS-04 · ¿Cómo quieres desarrollar, depurar y limpiar una instalación?

¿Con VS Code desde Windows, SSH a la Pi o directamente en Linux? ¿Necesitas un modo simulado sin hardware? Los scripts `reset.sh` borran estado, presets y otros archivos: ¿qué debería significar «limpiar logs», «restablecer estado» y «restaurar de fábrica»? ¿Qué datos deben quedar protegidos en cada caso?

**Propuesta:** comandos distintos para esas tareas, con alcance claro; una limpieza rutinaria debe conservar la configuración de trabajo.

**Respuesta:**

> Desarrollaré con VS Code desde Windows y acceso SSH a las Pi y al servidor. Quiero poder editar, ejecutar y depurar directamente en una Pi de pruebas mediante Remote-SSH, guardar los cambios en una rama de GitHub y distribuir versiones probadas al resto de equipos. Los detalles del flujo quedan a criterio del ingeniero, con instrucciones documentadas.
La limpieza tendrá acciones separadas: limpiar logs eliminará solo registros; restablecer estado limpiará datos temporales conservando presets y ajustes; restaurar de fábrica restablecerá la configuración y los datos de usuario, con confirmación y posibilidad de copia de seguridad.

## 17. Logs y diagnóstico

### LOG-01 · ¿Qué sucesos quieres registrar y cuáles te sobran?

En tus notas pides menos mensajes de beacon/heartbeat. ¿Guardamos por defecto arranque, cierre, cambios de estado, operaciones y errores, dejando el tráfico repetitivo para diagnóstico? ¿Quieres registrar cada evento MIDI/OSC o solo activarlo cuando investigas un problema? ¿Necesitas colores en consola y marcas claras de inicio/final?

**Respuesta:**

> lo que tu consideres importante loguear, loguealo, mejor tener de mas que de menos.... seguramente, esas queja era porque paso algo concreto, revisate las logicas de log y lo que tu consideres :)

### LOG-02 · ¿Qué política de archivos y límites quieres?

Hay notas sobre un archivo por ejecución, un máximo de archivos y un límite de 200 MB. ¿Ese límite es total por Pi, por servicio, por ejecución o por archivo? ¿Cuántos días o ejecuciones necesitas conservar? ¿Mismo criterio en servidor y servicios?

**Propuesta:** rotación por tamaño con un presupuesto total y eliminación de lo más antiguo; especificar juntos el límite de espacio y la conservación deseada.

**Respuesta:**

> tu propuesta esta bien :) 

### LOG-03 · ¿Qué diagnóstico debe ofrecer el servidor?

Tus notas piden ver los logs desde él. ¿Últimas líneas, seguimiento en directo, filtros, descarga o paquete de soporte? ¿Necesitamos conservar errores de una Pi que ya no está accesible? ¿Qué datos te bastan para entender «no arranca», «no encuentra MIDI» y «no llega al servidor»?

**Propuesta:** mostrar causa, operación e identificador de correlación para seguir una misma acción entre servidor, agente y servicio, con límites para el tráfico de logs.

**Respuesta:**

> tu propuesta esta bien

## 18. Acceso y control

### ACC-01 · CLAVE · ¿Quién puede acceder a la red y a los paneles?

¿Es una LAN privada bajo tu control, una red compartida con otros equipos o habrá acceso remoto por VPN/Internet? ¿Necesitas credenciales en el servidor y en el panel local? Describe quién debe poder consultar y quién debe poder cambiar servicio, red, actualizar o apagar.

**Propuesta:** ajustar autenticación y exposición al entorno real, teniendo en cuenta que las APIs controlan procesos, red y alimentación.

**Respuesta:**

> una lan controladdisima, sin wifi, con puertos de siwtch en desuso capados, y con control extricto, no hay peligro. no hacen falta credenciales, internet estara puntualmente para acutalizar si fuera necesario y cosas así, nada más.

### ACC-02 · ¿Cómo entra un agente nuevo y cómo deja de estar autorizado?

¿Todo equipo descubierto se acepta automáticamente o prefieres una aprobación/emparejamiento inicial? ¿Qué debe ocurrir al mover una Pi a otra instalación, reinstalarla o perderla? ¿Necesitas renovar su acceso conservando su identidad visible?

**Respuesta:**

> un apartado de descubrimineto y onboard, normalmente, el servidor se le hara un factory default en cada instalacion, por lo que todas las pis serán nuevas.

### ACC-03 · ¿Necesitas varios usuarios, permisos e historial de acciones?

¿Un único operador es suficiente? ¿Hace falta distinguir consulta, operación y administración? ¿Necesitas saber quién cambió un preset, apagó un equipo o actualizó una instalación? ¿Qué datos pueden aparecer en exportaciones y logs?

**Propuesta:** empezar con el modelo de usuarios más pequeño que cubra tu uso, evitando añadir roles que nadie vaya a utilizar.

**Respuesta:**

> no.

## 19. Actualizaciones y migración

### UPD-01 · CLAVE · ¿Cómo quieres distribuir y seleccionar versiones?

Ahora se elige una rama Git. ¿Quieres mantener esa flexibilidad, usar versiones etiquetadas o separar estable/pruebas? ¿Necesitas cambiar de rama desde la web? ¿Hay instalaciones con cambios locales de código que deban conservarse? ¿Satellite sigue su propio calendario de versiones?

**Propuesta:** una versión identificable y comprobable por entrega, con un canal de pruebas si te resulta útil.

**Respuesta:**

> no lo se, basandote en lo que ya hemos hablado, lo que consiederes oportuno.

### UPD-02 · CLAVE · ¿Qué debe pasar si una actualización falla?

¿Podemos parar servicios para descargar/preparar o deben seguir trabajando hasta el cambio final? ¿Se permite reiniciar la Pi siempre? Si fallan Internet, dependencias, compilación o arranque, ¿quieres volver automáticamente a la versión anterior? ¿Quién decide cuándo es una ventana válida para actualizar?

**Propuesta:** preparar y comprobar todo lo posible antes de detener el servicio, conservar una vía de recuperación y mostrar un resultado verificable.

**Respuesta:**

> tu propuesta tiene sentido

### UPD-03 · CLAVE · ¿Qué compatibilidad necesitamos durante el refactor?

¿Podemos actualizar servidor y todas las Pi a la vez o convivirán versiones antiguas y nuevas? ¿Hay APIs/archivos/puertos que deban permanecer compatibles durante un tiempo? ¿Es aceptable migrar datos una vez, conservando una copia? ¿Necesitas poder volver a la versión anterior con sus datos?

**Respuesta:**

> ninguna. todo a la basura, si tiene buena pinta, mañana mismo formateo una pi y me genero un CT y probamos :)

### UPD-04 · ¿Cómo probamos y desplegamos la transición?

¿Hay una Pi de laboratorio y una instalación que podamos usar primero? ¿Prefieres validar servicio por servicio, una Pi completa o una instalación pequeña? ¿Qué se respalda, dónde y quién comprueba que puede restaurarse? ¿Qué caída de servicio es aceptable durante la migración?

**Propuesta:** desarrollar en una rama de trabajo y validar primero en un equipo de prueba, con datos preservados y un procedimiento de vuelta documentado.

**Respuesta:**

> de momento tambien necesitamos hacer housekeeping en git, por lo que tal y como este ahora, metelo en main, y desde main, sacacmos el refactor como v3. con la rama v3, instalamos en la pi de prueba y en el CT y vamos viendo :) 

## 20. Comprobación y criterios de aceptación

### VAL-01 · ¿Con qué equipos, ejemplos y pruebas reales podremos contar?

¿Puedes facilitar configuraciones de ejemplo sin secretos, controladores MIDI, acceso de desarrollo a una Pi o resultados de pruebas que ejecutarás tú? ¿Qué acciones se pueden probar en laboratorio y cuáles deben quedar fuera de cualquier prueba automática, como apagar equipos en uso o cambiar la red de producción?

**Propuesta:** pruebas de coordinación con sustitutos de procesos/red/hardware y pruebas reales separadas para dispositivos y conectividad. Las simulaciones complementan la validación en Pi.

**Respuesta:**

> lo que necesites baby, tu pide por esa boquita.

### VAL-02 · ¿Qué límites medibles importan de verdad?

Da cifras orientativas o «a criterio del ingeniero»: tiempo de arranque, cambio de servicio, detección de desconexión, reconexión, cierre, latencia MIDI/OSC, consumo de CPU/RAM y espacio de disco. ¿Qué límite convierte una versión en inaceptable? ¿Qué errores actuales son bloqueantes?

**Respuesta:**

> Los cambios de servicio se realizan principalmente durante la preparación, normalmente al principio del día. Priorizo una secuencia ordenada y comprobada: detener el servicio anterior, retirar su VLAN si existe, preparar y activar la nueva VLAN, esperar a obtener IP mediante DHCP o verificar la IP fija y, finalmente, arrancar el nuevo servicio.

Cada paso debe completarse antes de continuar. Las esperas deben depender de comprobaciones reales, con plazos razonables y errores claros si algo falla. Acepto que la preparación tarde lo necesario para hacerse correctamente.

Durante el funcionamiento, el servicio se utiliza en directo y debe ofrecer una latencia baja y predecible, especialmente en MIDI/OSC. El procesamiento de eventos tendrá prioridad; la web, los logs, las métricas y la sincronización deberán evitar interferir con él. Buscamos la mayor rapidez compatible con un funcionamiento estable y correcto, con objetivos concretos que se fijarán midiendo el hardware y la carga reales.

### VAL-03 · CLAVE · ¿Qué pruebas deben cumplirse para darlo por bueno?

Completa o corrige los resultados de esta tabla. Son escenarios candidatos, no resultados ya acordados. Puedes remitir a otra respuesta para evitar repetirla.

| Escenario | Resultado deseado | Prioridad: imprescindible / deseable / fuera |
| --- | --- | --- |
| Encender sin servidor y sin Internet. | Pendiente. | Pendiente. |
| Encender con el controlador MIDI ausente y conectarlo después. | Pendiente. | Pendiente. |
| Perder y recuperar el servidor con un servicio activo. | Pendiente. | Pendiente. |
| Reiniciar el servidor con operaciones pendientes. | Pendiente. | Pendiente. |
| Pedir el mismo arranque dos veces o perder la respuesta a una orden. | Pendiente. | Pendiente. |
| Dar órdenes locales y remotas casi a la vez. | Pendiente. | Pendiente. |
| Cambiar de servicio o preset y fallar durante el cambio. | Pendiente. | Pendiente. |
| Cambiar IP/VLAN y perder acceso o no recibir DHCP. | Pendiente. | Pendiente. |
| Desconectar cable y conservar Wi-Fi, o volver a conectar cable. | Pendiente. | Pendiente. |
| Encontrar dos versiones distintas del mismo preset al reconectar. | Pendiente. | Pendiente. |
| Desenchufar/reconectar MIDI y perder/recuperar destinos externos. | Pendiente. | Pendiente. |
| Probar note-on, note-off, velocidad cero, CC y rutas coincidentes. | Pendiente. | Pendiente. |
| Perder Companion o caer el proceso Satellite. | Pendiente. | Pendiente. |
| Fallar o no existir la pantalla física. | Pendiente. | Pendiente. |
| Llegar al límite de logs o encontrar un JSON inválido. | Pendiente. | Pendiente. |
| Entrar en standby y reiniciar según la política de arranque. | Pendiente. | Pendiente. |
| Apagar/reiniciar individualmente y en grupo, con un servicio bloqueado. | Pendiente. | Pendiente. |
| Cortar la alimentación durante una operación. | Pendiente. | Pendiente. |
| Actualizar con éxito, fallar una actualización y recuperar la versión anterior. | Pendiente. | Pendiente. |
| Ejecutar agentes antiguos y nuevos durante la transición, si aplica. | Pendiente. | Pendiente. |
| Mantener la instalación funcionando durante el tiempo habitual de uso. | Pendiente. | Pendiente. |

**Otros escenarios y criterio final de aceptación:**

> todas las pruebas han de cunmplirse.

## 21. Propuesta de trabajo para discutir

### PLAN-01 · CLAVE · ¿Encaja esta división de responsabilidades con el producto que quieres?

La estructura exacta de carpetas saldrá después. Mi propuesta inicial es:

| Pieza | Responsabilidad propuesta |
| --- | --- |
| Contratos compartidos | Definir mensajes, validaciones y vocabulario común entre cliente y servidor. |
| Coordinador del agente | Recibir operaciones locales/remotas y ejecutar una sola secuencia con sus estados, errores y cancelaciones. |
| Gestor de procesos | Iniciar, detener y observar servicios y sus procesos hijos. |
| Gestión de red | Traducir perfiles acordados a cambios del sistema y comunicar su resultado. |
| Configuración persistente | Guardar preferencias, presets y selección de arranque mediante un responsable claro por dato. |
| Estado observado | Informar de lo que está ocurriendo: procesos, conexiones, métricas y operaciones actuales. |
| Integraciones de servicio | Encapsular lo específico de MIDI, Satellite y futuros servicios. |
| Webs y pantallas | Mostrar el estado común y solicitar operaciones al coordinador. |
| Servidor central | Registrar equipos, distribuir configuraciones y seguir las operaciones remotas. |

¿Cambiarías alguna responsabilidad? ¿Qué parte quieres comprender o controlar especialmente? Mi intención es reutilizar FastAPI, las interfaces y las integraciones útiles, sustituyendo gradualmente la coordinación y retirando el código reemplazado en cada entrega.

**Respuesta:**

> refactorizacion de codigo completa, luego lidiaremos con bugs (logicamente haz tus test y lo que consideres necesario, tienes toda la noche, yo me voy a dormir xD)

### PLAN-02 · ¿Qué delegas y qué decisiones quieres revisar conmigo?

Puedes separar decisiones de producto de detalles de implementación: estructura de paquetes, nombres, sincronización interna, almacenamiento JSON/SQLite, utilidades de pruebas o pequeñas dependencias. ¿Hay tecnologías que prefieras o quieras evitar? ¿Qué nivel de documentación y explicación necesitas para mantenerlo tú después?

**Propuesta:** tú fijas comportamiento, restricciones y prioridades; yo elijo los detalles internos dentro del alcance acordado y te explico las decisiones que afecten a mantenimiento, instalación o compatibilidad.

**Respuesta:**

> tu propuesta me parece bien

### PLAN-03 · ¿Te encaja este orden tentativo de entregas?

1. Consolidar tus respuestas, resolver contradicciones y fijar alcance y pruebas de aceptación.
2. Preparar una base de pruebas y ejecución sin hardware que permita verificar los recorridos acordados.
3. Migrar un recorrido completo de arranque/parada de servicio, accesible desde web local y órdenes remotas.
4. Consolidar estado, presets, coordinación de red y recuperación conforme a las reglas acordadas.
5. Completar protocolo, reconexión, cierre y supervisión; adaptar MIDI/Satellite donde sea necesario.
6. Unificar la presentación del estado, diagnóstico e instalación/actualización según las prioridades.
7. Migrar datos, validar en Pi y retirar las piezas antiguas y documentación obsoleta.

El orden puede cambiar por tus respuestas: si la gestión de red es el principal riesgo o la compatibilidad obliga, la abordaremos antes. ¿Prefieres entregas pequeñas que puedas probar entre medias o una transición más amplia en laboratorio? ¿Hay alguna tarea que deba adelantarse o quedar fuera?

**Respuesta:**

> mi idea es punto uno, cunado estemos contentos, lo hagas tu todo esta noche. mañana con lo que tu tengas hecho y probado, ya lo metemos en hardware y empezamos con baterias de pruebas en hardware.

## Espacio libre

### Cosas que no te he preguntado y debería saber

> Pendiente.

### Ejemplos de una sesión real y de fallos difíciles de reproducir

> Pendiente.

### Decisiones que quieres aplazar y dudas que quieres que te explique

> Pendiente.

## Nuestra revisión cuando termines

Este bloque se rellenará después de leer tus respuestas. Por ahora no contiene acuerdos.

- Requisitos confirmados: pendiente.
- Suposiciones descartadas y comportamientos antiguos que retiraremos: pendiente.
- Contradicciones y dudas abiertas: pendiente.
- Alcance de la primera entrega y trabajo que queda fuera: pendiente.
- Reglas de compatibilidad y migración: pendiente.
- Criterios de aceptación y entorno de pruebas: pendiente.
- Decisiones técnicas delegadas: pendiente.
- Plan de trabajo acordado: pendiente.
- Estado del inicio del refactor: **pendiente de nuestra revisión conjunta**.
