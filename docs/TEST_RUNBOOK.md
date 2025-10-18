# Manual Test Runbook

This runbook strings together the scenarios listed in `docs/TEST_MATRIX.md` so you can execute a full manual regression in one go.

## 0. Preparación previa
- **Entorno limpio**: clona el repositorio o limpia artefactos (`server/omi.db`, `client/agent_pi/data/*.json`, logs).
- **Python 3.11+** y dependencias instaladas (servidor y agente).
- **Red local**: el agente debe poder alcanzar la IP del servidor.

## 1. Arranque inicial
1. En una terminal, desde la raíz: `python -m server.omi_server`.
2. Verifica en consola que FastAPI expone `http://0.0.0.0:6969`.
3. En otra terminal: `python client/client.py`.
4. Observa en el servidor el log de descubrimiento (`Broadcast manager iniciado`) y que el agente anuncie su estado.

## 2. Validar descubrimiento
1. Abre `http://<ip-servidor>:6969/`.
2. Cambia a la pestaña **Clientes** y confirma que el agente aparece con `online=true`, IP y `device_index` asignado.

## 3. Cambio de servicio (MIDI)
1. Desde la vista **Dispositivos**, selecciona el agente y solicita el servicio `MIDI`.
2. Espera los mensajes `SERVICE_ACK` en el log del servidor.
3. En el agente, comprueba que `logs/services/midi.log` registre la transición y que el servicio se inicie sin errores.

## 4. Reasignación de índices
1. Usa la API para registrar el dispositivo:  
   `curl -X PUT http://<server>:6969/api/devices/<serial> -H 'Content-Type: application/json' -d '{"desired_service": null}'`.
2. Lanza `curl -X DELETE http://<server>:6969/api/devices/<serial>`.
3. Observa el log del servidor y confirma que se envía `SET_INDEX` a los agentes restantes y reciben `INDEX_ACK`.

## 5. Perfil de red y handshake
1. Envía `curl -X PUT http://<server>:6969/api/devices/<serial> -H 'Content-Type: application/json' -d '{"network":{"vlan_id":20,"dhcp":true}}'`.
2. Revisa `server/logs/server.log` para comprobar que se almacena el perfil.
3. Simula un ACK:  
   `curl -X POST http://<server>:6969/api/devices/<serial>/network/ack -H 'Content-Type: application/json' -d '{"applied":true,"message":"vlan set"}'`.
4. Verifica el log `omi.server.network` indicando el ACK recibido.

## 6. MIDI Web UI – LEARN
1. Desde el agente: `python client/servicios/MIDI/midiwebui.py`.
2. Abre `http://<ip-agente>:9001/add/learn`.
3. Mueve un control MIDI; comprueba que se actualiza la previsualización.
4. Acepta la ruta y confirma que `OMIMIDI_map.json` contiene la nueva entrada y que se sincroniza con el servidor (lo verás en `midi.log`).

## 7. Ajustes MIDI y reinicio
1. En la Web UI, ve a **Ajustes**.
2. Cambia `osc_port` o `osc_ips` y guarda.
3. Observa la pantalla de reinicio; tras unos segundos, la UI debe reconectar.
4. En `midi.log`, confirma que el core detectó el reinicio (`Reiniciando proceso…`) y que aplica los nuevos valores al reiniciarse.

## 8. Ping OSC
1. En la Web UI, pulsa “Enviar ping OSC”.
2. Verifica en `midi.log` el mensaje `Ping OSC enviado…` y comprueba en el destino (si existe) la recepción del `/omimidi/ping`.

## 9. Persistencia tras reinicio forzado
1. Termina el proceso `omimidi_core` (Ctrl+C o `pkill -f omimidi_core`).
2. Reinícialo con `python client/servicios/MIDI/service.py`.
3. Revisa que los archivos `OMIMIDI_state.json` y `OMIMIDI_learn_request.json` se limpian correctamente y que el core vuelve a cargar el mapa sin inconsistencias.

## 10. Cierre
1. Detén el agente (Ctrl+C).
2. Detén el servidor.
3. Si procede, borra los artefactos generados para dejar el entorno listo para la siguiente ejecución.

Consulta `docs/TEST_MATRIX.md` cuando necesites revisar los detalles de cada escenario (inputs, resultados esperados o notas adicionales).
