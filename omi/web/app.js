'use strict';
// Polling updates operational state only. Draft fields are owned by the editor.
const $ = id => document.getElementById(id);
const state = {status:null, agents:[], presets:[], revisions:[], selected:new Set(), draft:null,
  context:null, session:null, sessionBase:'', routeIndex:null, routeOpen:false, learning:false,
  pollBusy:false, maintenanceLoaded:false, dirty:false, noticeTimer:null, confirmResolve:null, inflight:new Set()};
const clone = value => JSON.parse(JSON.stringify(value));
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const short = value => value ? String(value).slice(0,8) : '—';
const server = () => state.status?.role === 'server';
const contextBase = serial => serial ? `/api/agents/${encodeURIComponent(serial)}` : '/api';
const maintenanceBase = () => contextBase(server() ? $('maintenance-target').value : null);
const active = item => item?.state?.target;
const currentAgent = serial => state.agents.find(item => item.serial === serial);

async function api(path, options={}) {
  const response = await fetch(path, {cache:'no-store', ...options,
    headers:{'Content-Type':'application/json', ...(options.headers || {})},
    body: options.body === undefined ? undefined : JSON.stringify(options.body)});
  const content = response.headers.get('content-type') || '';
  const result = content.includes('json') ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = result?.detail || result || `HTTP ${response.status}`;
    const error=new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));error.status=response.status;throw error;
  }
  return result;
}
function notice(message, error=false) {
  $('notice').textContent = message;
  $('notice').className = `visible${error ? ' error' : ''}`;
  if($('notice').showPopover&&!$('notice').matches(':popover-open'))$('notice').showPopover();
  clearTimeout(state.noticeTimer);
  state.noticeTimer = setTimeout(() => {$('notice').className='';if($('notice').hidePopover&&$('notice').matches(':popover-open'))$('notice').hidePopover();}, error ? 12000 : 6000);
}
async function run(work) { try { return await work(); } catch(error) { notice(error.message,true); return null; } }
function button(label, action, disabled=false, extra='') {
  return `<button data-action="${action}" ${disabled?'disabled':''} ${extra}>${label}</button>`;
}
function presetLabel(target) {
  if (!target) return 'Standby';
  const revision = state.revisions.find(item => item.id === target.revision);
  const catalog = state.presets.find(item => item.preset_id === target.preset_id);
  const name = catalog && !catalog.conflict ? catalog.name : revision?.name;
  return `${name || short(target.preset_id)} · ${short(target.revision)}`;
}
function healthLabel(health) {
  return {standby:'Standby', starting:'Preparando', ready:'Listo', waiting:'Esperando dispositivo', error:'Error', stopping:'Deteniendo'}[health] || health;
}
function networkLabel(agent) {
  const network = agent.state?.network || {};
  const parts = [];
  if (network.vlan) parts.push(`VLAN ${network.vlan}`);
  if (network.addresses?.length) parts.push(network.addresses.join(', '));
  else {
    let management = agent.management_ip || '';
    try { management ||= new URL(agent.endpoint).hostname; } catch {}
    parts.push(management ? `Administración: ${management}` : 'Administración Ethernet');
  }
  return parts.join(' · ');
}
function revisionNotice(target) {
  if (!target) return '';
  const catalog = state.presets.find(item => item.preset_id === target.preset_id);
  return catalog && !catalog.conflict && !catalog.deleted && catalog.revision.id !== target.revision
    ? '<span class="muted"> · Revisión nueva disponible</span>' : '';
}
function presetOptions(selected='') {
  return '<option value="">Elegir preset…</option>' + state.presets.filter(p=>!p.deleted&&!p.conflict).map(p =>
    `<option value="${escapeHTML(p.revision.id)}" ${p.revision.id===selected?'selected':''}>${escapeHTML(p.name)} · ${p.revision.config.service==='midi'?'MIDI → OSC':'Satellite'}</option>`).join('');
}
function replaceOptions(element, html) { const value=element.value; element.innerHTML=html; if ([...element.options].some(o=>o.value===value)) element.value=value; }
function onlineAgentOptions(includeCentral=false) {
  return (includeCentral?'<option value="">Servidor central</option>':'<option value="">Sin captura · edición manual</option>') +
    state.agents.filter(a=>a.onboarded&&a.online).map(a=>`<option value="${escapeHTML(a.serial)}">${a.device_id} · ${escapeHTML(a.name)}</option>`).join('');
}
function operationText(item) {
  const pending = item.pending;
  const op = pending?.operation || item.state?.operation;
  const lines = [pending ? `${pending.command?.action || ''} · ${pending.status} · ${short(pending.id)}` : '', op ? `${op.phase} · ${op.status}` : '',
    ...(op?.progress || []).slice(-7), op?.error || pending?.error || ''].filter(Boolean);
  return lines.join('\n');
}
function renderAgents() {
  const selections = new Map([...$('agent-list').querySelectorAll('[data-serial]')].map(card=>[card.dataset.serial,card.querySelector('[data-preset]')?.value]));
  const agents = server() ? state.agents.filter(a=>a.onboarded) : [{...state.status,onboarded:true,online:true,busy:state.status?.busy||state.status?.state?.busy||['accepted','running'].includes(state.status?.state?.operation?.status),observed:state.status?.state?.target}];
  $('devices-title').textContent = server() ? 'Equipos de la instalación' : state.status?.name || state.status?.hostname || 'Esta Pi';
  $('devices-summary').textContent = server() ? `${agents.filter(a=>a.online).length} conectados / ${agents.length} incorporados · sin cola de órdenes offline` : `${state.status?.connected?'Servidor conectado':'Modo autónomo'} · ${state.status?.local_control?'Control local disponible':'Control central'}`;
  $('group-controls').hidden = !server();
  $('discovered-title').hidden = !server();
  $('discovered').hidden = !server();
  $('agent-list').innerHTML = agents.map(a => {
    const health=a.state?.health||'standby';
    const busy=!!a.busy||state.inflight.has(a.serial), disabled=!a.online||busy||(!server()&&!state.status.local_control);
    const color=!a.online?'offline':busy?'busy':health==='error'?'error':a.discrepancy?'discrepancy':'';
    const connection=!a.online?'Offline':busy?'Ocupada':health==='error'?'Error':a.discrepancy?'Discrepancia':health==='waiting'?'Esperando equipo':'Online';
    const observed=a.observed || a.state?.target;
    return `<article class="card ${color}" data-serial="${escapeHTML(a.serial)}"><div class="card-top"><div><h2>${server()?`<label class="inline"><input type="checkbox" data-select="${escapeHTML(a.serial)}" ${state.selected.has(a.serial)?'checked':''}>` : ''}${escapeHTML(a.device_id ? `${a.device_id} · ${a.name}` : a.name||a.hostname||'Pi')}${server()?'</label>':''}</h2><span class="muted">${escapeHTML(a.hostname)} · ${escapeHTML(a.serial)}</span></div><span class="badge ${color==='error'?'red':color==='discrepancy'?'orange':'green'}">${connection}</span></div><dl><dt>Ejecuta</dt><dd>${escapeHTML(presetLabel(observed))}${revisionNotice(observed)}</dd>${server()?`<dt>Esperado</dt><dd>${escapeHTML(presetLabel(a.expected))}</dd>`:''}<dt>Salud</dt><dd>${escapeHTML(healthLabel(health))} ${escapeHTML(a.state?.detail||'')}</dd><dt>Red</dt><dd>${escapeHTML(networkLabel(a))}</dd></dl>${busy?`<pre class="console">${escapeHTML(operationText(a))}</pre>${button('Consultar operación','operation',!a.online)}`:`<div class="toolbar"><select data-preset aria-label="Preset para ${escapeHTML(a.name||a.hostname)}" ${disabled?'disabled':''}>${presetOptions()}</select>${button('Aplicar','apply',disabled)}${button('Standby','standby',disabled)}${button('Editar actual','edit-current',disabled||!observed)}${button('Editar desde esta Pi','edit-context',disabled)}${server()&&a.discrepancy?button('Aceptar estado','accept',disabled)+button('Sincronizar','synchronize',disabled):''}</div><div class="toolbar">${button('Logs','logs',!a.online)}${button('Reiniciar','reboot',disabled)}${button('Apagar','poweroff',disabled)}${server()?button('ID / nombre','identity')+button('Retirar','forget'):''}</div>`}${!busy&&a.last_command?.error?`<p class="warning">${escapeHTML(a.last_command.error)}</p>`:''}${!busy&&a.state?.operation?.error?`<pre class="console">${escapeHTML(operationText(a))}</pre>`:''}</article>`;
  }).join('') || '<p class="empty">Todavía no hay Pi incorporadas. Encienda un agente y use Onboard en Descubiertos.</p>';
  $('agent-list').querySelectorAll('[data-serial]').forEach(card=>{const select=card.querySelector('[data-preset]');if(select&&selections.get(card.dataset.serial))select.value=selections.get(card.dataset.serial);});
  $('discovered').innerHTML = state.agents.filter(a=>!a.onboarded).map(a=>`<article class="card ${a.online?'':'offline'}" data-serial="${escapeHTML(a.serial)}"><h2>${escapeHTML(a.hostname)}</h2><p class="muted">Serial ${escapeHTML(a.serial)}<br>${escapeHTML(a.endpoint)} · ${a.online?'Online':'Offline'}</p><p>Estado local: ${escapeHTML(presetLabel(a.state?.target))}</p>${a.import_available?'<p class="warning">Biblioteca central vacía: puede importar los presets de esta Pi al incorporarla.</p>':''}${button('Onboard','onboard',!a.online)}</article>`).join('') || '<p class="empty">No hay equipos nuevos descubiertos.</p>';
  $('selection-count').textContent = `${state.selected.size} seleccionados`;
  replaceOptions($('group-preset'),presetOptions());
  replaceOptions($('maintenance-target'),server()?onlineAgentOptions(true):'<option value="">Esta Pi</option>');
  $('reset-installation').hidden=!server()||!!$('maintenance-target').value;
  $('management-network').hidden=server()&&!$('maintenance-target').value;
}
function renderPresets() {
  const visible=state.presets.filter(p=>$('show-deleted').checked||!p.deleted);
  $('preset-list').innerHTML = visible.map(p=>`<article class="card ${p.conflict?'conflict':''}" data-preset-id="${escapeHTML(p.preset_id)}"><div class="card-top"><h2>${escapeHTML(p.name)}</h2><span class="badge ${p.conflict?'orange':''}">${p.deleted?'Borrado':p.conflict?'Conflicto':p.revision.config.service==='midi'?'MIDI → OSC':'Satellite'}</span></div><p class="muted">Preset ${short(p.preset_id)} · Revisión ${short(p.revision.id)}<br>${p.heads.length} ${p.heads.length===1?'rama':'ramas'} · ${escapeHTML(p.revision.author)}</p><div class="toolbar">${button('Editar','edit',p.deleted||p.conflict)}${button('Duplicar','duplicate',p.deleted)}${button('Renombrar','rename',p.deleted||p.conflict)}${p.conflict?button('Resolver conflicto','resolve'):''}${button('Exportar','export')}${button('Borrar','delete',p.deleted||p.conflict)}</div></article>`).join('') || '<p class="empty">Biblioteca vacía. Cree un preset o importe una biblioteca existente. Standby sigue disponible.</p>';
}
async function poll() {
  if(state.pollBusy)return;
  state.pollBusy=true;
  try {
    state.status=await api('/api/status');
    if(!server()&&!state.status.local_control&&!state.draft){
      $('role').textContent='Control local';$('connection').textContent='Control central disponible';
      $('devices-summary').textContent='La Pi sigue funcionando. Use el servidor central. La interfaz local se habilitará si se pierde la comunicación.';
      $('agent-list').innerHTML='<p class="empty">Control central disponible. El estado y el servicio de esta Pi se mantienen.</p>';
      $('group-controls').hidden=true;$('new-preset').disabled=true;return;
    }
    const results=await Promise.all([server()?api('/api/agents'):Promise.resolve([]),api('/api/presets'),api('/api/presets/export')]);
    state.agents=results[0];state.presets=results[1];state.revisions=Array.isArray(results[2])?results[2]:results[2].revisions;
    $('role').textContent=server()?'Control central':'Control local';
    $('connection').textContent=server()?`${state.status.online} Pi conectadas`:state.status.connected?'Servidor conectado':'Sin servidor · autónomo';
    $('version').textContent=state.status.version||'v3';
    renderAgents();renderPresets();
    if(state.session&&state.learning&&state.routeOpen){try{await readLearn();}catch(error){state.learning=false;notice(`No se ha podido consultar la captura: ${error.message}. El borrador se conserva.`,true);}}
    // Do not refresh controls, routes or configuration while a draft is open.
    if(!state.draft&&!server()&&!state.status.local_control){$('new-preset').disabled=true;}
    else $('new-preset').disabled=false;
  }catch(error){$('connection').textContent='Sin comunicación';notice(error.message,true);}
  finally{state.pollBusy=false;}
}
function tab(name){document.querySelectorAll('.view').forEach(e=>e.hidden=e.id!==name);document.querySelectorAll('.tab').forEach(e=>e.classList.toggle('active',e.dataset.tab===name));}
function defaultConfig(){return {service:'midi',network:{vlan:null,mode:'dhcp',address:'',gateway:'',dns:[]},midi:{input:'',destinations:[],routes:[],deadline_ms:10},satellite:{host:'127.0.0.1',port:16622,rest_port:9999}};}
function selectedCapture(){return server()?$('capture-agent').value:null;}
function captureBase(){return server()?(selectedCapture()?contextBase(selectedCapture()):null):'/api';}
async function endSession(){
  const session=state.session, base=state.sessionBase;state.session=null;state.learning=false;
  if(session){try{await api(`${base}/editors/${encodeURIComponent(session.id)}`,{method:'DELETE'});}catch(error){if(error.status!==404)notice('No se ha confirmado el cierre de captura. La Pi liberará el silencio al caducar la sesión.',true);}}
}
async function beginSession(routeEditor=false){
  const base=captureBase();
  if(!base||!state.draft){await endSession();return null;}
  const reuse=state.session&&state.sessionBase===base?state.session.id:null;
  if(!reuse)await endSession();
  state.session=await api(`${base}/editors`,{method:'POST',body:{preset_id:state.draft.preset_id||'',revision:state.draft.id||'',route_editor:routeEditor,input:$('midi-input').value||null,session_id:reuse,config:routeEditor&&!state.draft.id?readDraft().config:undefined}});
  state.sessionBase=base;
  return state.session;
}
async function renewSession(){
  if(!state.session)return;
  const id=state.session.id;
  try{const result=await api(`${state.sessionBase}/editors/${encodeURIComponent(id)}/renew`,{method:'POST'});if(state.session?.id===id)state.session={...state.session,...result};}
  catch(error){if(state.session?.id!==id)return;state.session=null;state.learning=false;$('mute-status').textContent='Se perdió la sesión de la Pi. El borrador se conserva; cierre y vuelva a abrir la ruta para restablecer el silencio.';notice(`Sesión perdida: ${error.message}. El borrador sigue abierto.`,true);}
}
async function openEditor(revision=null, context=null, duplicate=false){
  if(state.draft){notice('Cierre el borrador abierto antes de abrir otro.',true);return;}
  const localTarget=server()?active(currentAgent(context)):state.status.state?.target;
  if(duplicate&&localTarget)throw new Error('Pase esta Pi a standby antes de crear una copia de otro preset.');
  if((context||!server())&&localTarget&&(!revision||revision.preset_id!==localTarget.preset_id))throw new Error('Con un servicio activo solo se puede editar el preset que está ejecutando. Pase a standby para editar otro.');
  state.context=context;state.draft=revision?clone(revision):{name:'',config:defaultConfig()};state.dirty=false;
  if(duplicate){delete state.draft.id;delete state.draft.preset_id;state.draft.name+=' (copia)';}
  const d=state.draft,c=d.config,n=c.network,m=c.midi,s=c.satellite;
  $('preset-name').value=d.name;$('preset-service').value=c.service;$('network-vlan').value=n.vlan??'';$('network-mode').value=n.mode;$('network-address').value=n.address;$('network-gateway').value=n.gateway;$('network-dns').value=n.dns.join(', ');
  $('midi-input').value=m.input;$('midi-deadline').value=m.deadline_ms;$('satellite-host').value=s.host;$('satellite-port').value=s.port;$('satellite-rest').value=s.rest_port;
  $('editor-title').textContent=d.id?'Editar preset':'Nuevo preset';$('editor-context-label').textContent=context?`CONTEXTO PI ${currentAgent(context)?.device_id||context}`:server()?'BIBLIOTECA CENTRAL':'CONTEXTO DE ESTA PI';
  $('draft-status').textContent=context||!server()?(localTarget?'Guardar aplica a esta Pi y conserva las revisiones de las demás.':'Esta Pi está en standby. Guardar no inicia el servicio.'):'Guardar conserva las ejecuciones actuales. Seleccione equipos para guardar y aplicar.';
  $('capture-agent').innerHTML=onlineAgentOptions();if(context)$('capture-agent').value=context;
  $('capture-agent').disabled=!!context;$('capture-label').hidden=!server();
  $('save-apply').hidden=!server()||!!context;$('apply-targets').hidden=!server()||!!context;
  $('apply-targets').innerHTML=state.agents.filter(a=>a.onboarded).map(a=>`<label class="inline"><input type="checkbox" data-apply-target="${escapeHTML(a.serial)}" ${state.selected.has(a.serial)?'checked':''} ${!a.online||a.busy?'disabled':''}>${a.device_id} · ${escapeHTML(a.name)} ${!a.online?'(offline)':a.busy?'(ocupada)':''}</label>`).join('');
  $('revision-detail').textContent=d.id?`Base: ${d.id}. Guardar crea una revisión nueva sin borrar esta base.`:'Preset nuevo, sin revisión aplicada.';
  renderDestinations();renderRoutes();serviceFields();$('preset-editor').showModal();
  try{if(!server()||context)await beginSession(false);}catch(error){notice(error.message,true);}
}
function serviceFields(){const midi=$('preset-service').value==='midi';$('midi-fields').hidden=!midi;$('satellite-fields').hidden=midi;$('network-address').required=$('network-mode').value==='static';}
function renderDestinations(){
  $('destinations').innerHTML=state.draft.config.midi.destinations.map((d,i)=>`<div class="destination-row"><input data-destination-host="${i}" aria-label="Host OSC ${i+1}" value="${escapeHTML(d.host)}" placeholder="192.168.30.20" required><input data-destination-port="${i}" aria-label="Puerto OSC ${i+1}" type="number" min="1" max="65535" value="${d.port}" required><button type="button" data-remove-destination="${i}" aria-label="Quitar destino ${i+1}">Quitar</button></div>`).join('');
}
function renderRoutes(){
  $('routes').innerHTML=state.draft.config.midi.routes.map((r,i)=>`<tr><td>${r.kind==='note'?'Nota':'CC'}</td><td>${r.number}</td><td>${r.channel===null?'Todos':r.channel+1}</td><td>${escapeHTML(r.address)}</td><td>${escapeHTML(r.value_type==='const'?String(r.constant):r.value_type)}</td><td><button type="button" data-route-edit="${i}">Editar</button> <button type="button" data-route-remove="${i}">Quitar</button></td></tr>`).join('')||'<tr><td colspan="6" class="muted">Sin rutas. Añada una ruta manual o capture el primer evento MIDI.</td></tr>';
}
function readDraft(){
  const c=state.draft.config;c.service=$('preset-service').value;state.draft.name=$('preset-name').value.trim();
  c.network={vlan:$('network-vlan').value?Number($('network-vlan').value):null,mode:$('network-mode').value,address:$('network-address').value.trim(),gateway:$('network-gateway').value.trim(),dns:$('network-dns').value.split(',').map(s=>s.trim()).filter(Boolean)};
  c.midi.input=$('midi-input').value;c.midi.deadline_ms=Number($('midi-deadline').value);
  c.midi.destinations=[...$('destinations').querySelectorAll('[data-destination-host]')].map(e=>({host:e.value.trim(),port:Number($('destinations').querySelector(`[data-destination-port="${e.dataset.destinationHost}"]`).value)}));
  c.satellite={host:$('satellite-host').value.trim(),port:Number($('satellite-port').value),rest_port:Number($('satellite-rest').value)};
  return {preset_id:state.draft.preset_id||null,parents:state.draft.id?[state.draft.id]:[],name:state.draft.name,config:c,session_id:state.session?.id||null};
}
async function savePreset(apply=false){
  if(!$('preset-form').reportValidity())return;
  const targets=[...document.querySelectorAll('[data-apply-target]:checked')].map(e=>e.dataset.applyTarget);
  if(apply&&!targets.length)throw new Error('Seleccione al menos una Pi para aplicar.');
  $('save-preset').disabled=true;$('save-apply').disabled=true;
  try{
    const body=readDraft();
    const result=await api(`${contextBase(state.context)}/presets`,{method:'POST',body});
    state.draft=clone(result);state.dirty=false;
    $('draft-status').textContent=`Guardado · revisión ${short(result.id)}. Puede seguir editando.`;
    $('revision-detail').textContent=`Base: ${result.id}`;
    // Keep the editor session alive while the form remains open. It may refer to the previous immutable base.
    if(apply){const results=await api('/api/groups/commands',{method:'POST',body:{serials:targets,command:{action:'apply',target:{preset_id:result.preset_id,revision:result.id},arguments:state.session?{session_id:state.session.id}:{}}}});showGroupResults(results);}
    notice(apply?'Preset guardado; consulte el resultado de cada Pi.':'Preset guardado.');await poll();
  }finally{$('save-preset').disabled=false;$('save-apply').disabled=false;}
}
async function closeEditor(){
  if(state.routeOpen)await closeRoute(false);
  if(state.dirty&&!await confirmAction('Descartar borrador','Hay cambios sin guardar. ¿Cerrar el editor y descartar esos cambios?'))return;
  await endSession();state.draft=null;state.context=null;state.dirty=false;$('preset-editor').close();await poll();
}
async function openRoute(index=null){
  state.routeIndex=index;state.learning=false;
  const r=index===null?{kind:'note',number:60,channel:null,address:'/go',value_type:'int',constant:1}:state.draft.config.midi.routes[index];
  $('route-kind').value=r.kind;$('route-number').value=r.number;$('route-channel').value=r.channel??'';$('route-address').value=r.address;$('route-type').value=r.value_type;$('route-constant').value=typeof r.constant==='string'?JSON.stringify(r.constant):String(r.constant);$('learn-result').textContent='';
  await beginSession(true);state.routeOpen=true;
  $('mute-status').textContent=state.session?'Toda la salida OSC de esta Pi está silenciada mientras este editor permanezca abierto.':'Edición manual, sin Pi de captura. Ningún equipo se ha silenciado.';
  $('learn').disabled=!state.session;$('simulate-midi').hidden=!state.status.simulated&&!currentAgent(selectedCapture())?.simulated;
  $('route-editor').showModal();
}
async function closeRoute(commit){
  if(commit){
    if(!commitRoute())return;
  }
  try{if(state.draft&&(!server()||state.context||selectedCapture()))await beginSession(false);else await endSession();}
  catch(error){await endSession();notice(`El cierre se verificará al caducar la sesión: ${error.message}`,true);}
  finally{state.routeOpen=false;$('route-editor').close();}
}
function commitRoute(){
  if(!$('route-form').reportValidity())return false;
  let constant;try{constant=JSON.parse($('route-constant').value);}catch{constant=$('route-constant').value;}
  if(!['string','number','boolean'].includes(typeof constant))throw new Error('La constante debe ser número, texto o booleano.');
  const previous=state.routeIndex===null?null:state.draft.config.midi.routes[state.routeIndex];
  const route={id:previous?.id||(globalThis.crypto?.randomUUID?.()||`route-${Date.now()}-${Math.random().toString(36).slice(2)}`),kind:$('route-kind').value,number:Number($('route-number').value),channel:$('route-channel').value===''?null:Number($('route-channel').value),address:$('route-address').value.trim(),value_type:$('route-type').value,constant};
  if(state.routeIndex===null){state.draft.config.midi.routes.push(route);state.routeIndex=state.draft.config.midi.routes.length-1;}else state.draft.config.midi.routes[state.routeIndex]=route;
  state.dirty=true;renderRoutes();return true;
}
async function learn(){
  if(!state.session)throw new Error('La sesión no está disponible. Cierre y vuelva a abrir la ruta.');
  await api(`${state.sessionBase}/editors/${encodeURIComponent(state.session.id)}/learn`,{method:'POST'});state.learning=true;$('learn-result').textContent='Esperando el primer evento…';
}
async function readLearn(){
  const result=await api(`${state.sessionBase}/editors/${encodeURIComponent(state.session.id)}`);
  const event=result.learn;if(!event)return;
  state.learning=false;
  $('route-kind').value=event.kind||(event.type==='control_change'?'cc':'note');
  $('route-number').value=event.number??event.note??event.control??60;
  $('route-channel').value=event.channel??'';
  $('learn-result').textContent=`Capturado: ${$('route-kind').value==='note'?'nota':'CC'} ${$('route-number').value}, canal ${event.channel===null||event.channel===undefined?'Todos':event.channel+1}. Retenido hasta confirmar o descartar.`;
}
async function refreshInputs(){
  const base=captureBase();if(!base)throw new Error('Elija la Pi desde la que quiere consultar las entradas MIDI.');
  const result=await api(`${base}/midi/inputs`);$('midi-inputs').innerHTML=result.inputs.map(p=>`<option value="${escapeHTML(p)}"></option>`).join('');notice(`${result.inputs.length} entradas disponibles. La selección actual se conserva.`);
}
async function conflict(preset){
  const heads=preset.heads.map(id=>state.revisions.find(r=>r.id===id)).filter(Boolean);
  $('conflict-options').innerHTML=heads.map(r=>`<article class="panel"><h3>${escapeHTML(r.name)} · ${short(r.id)}</h3><p class="muted">${escapeHTML(r.author)} · ${new Date(r.created_at*1000).toLocaleString()} ${r.deleted?'· Borrado':''}</p><details><summary>Ver contenido</summary><pre class="console">${escapeHTML(JSON.stringify(r.config,null,2))}</pre></details><button data-resolve-id="${escapeHTML(r.id)}" data-resolve-preset="${escapeHTML(preset.preset_id)}">Usar este contenido y fusionar</button></article>`).join('');$('conflict-editor').showModal();
}
async function confirmAction(title,message,phrase=null,backup=false){
  $('confirm-title').textContent=title;$('confirm-message').textContent=message;$('confirm-phrase').value='';$('confirm-phrase-label').hidden=!phrase;$('confirm-phrase-label').firstChild.textContent=phrase?`Escriba «${phrase}» para confirmar`:'';$('confirm-backup-label').hidden=!backup;$('confirm-backup').checked=true;
  $('confirm-dialog').dataset.phrase=phrase||'';$('confirm-dialog').showModal();
  return new Promise(resolve=>state.confirmResolve=resolve);
}
function finishConfirm(result){$('confirm-dialog').close();const resolve=state.confirmResolve;state.confirmResolve=null;resolve?.(result);}
function showGroupResults(result){
  $('group-results').hidden=false;$('group-results').textContent=(result.results||[]).map(r=>`${currentAgent(r.serial)?.name||r.serial}: ${r.skipped?'Omitida · '+r.error:r.result.status+(r.result.error?' · '+r.result.error:'')}`).join('\n');
}
async function sendCommand(serial,action,target=null){
  if(['reboot','poweroff','update'].includes(action)&&!await confirmAction(action==='poweroff'?'Apagar equipo':action==='reboot'?'Reiniciar equipo':'Actualizar OMI',`Se solicita ${action} a esta Pi. El servicio se cerrará de forma ordenada cuando corresponda.`))return;
  state.inflight.add(serial);renderAgents();
  try{const result=await api(`${contextBase(server()?serial:null)}/commands`,{method:'POST',body:{action,...(target?{target}:{})}});
    notice(result.error||`Orden ${action}: ${result.status}`,!!result.error);
  }finally{state.inflight.delete(serial);await poll();}
}
function findRevision(id){const r=state.revisions.find(r=>r.id===id);if(!r)throw new Error('Seleccione un preset sin conflicto.');return r;}
async function agentAction(event){
  const node=event.target.closest('[data-action]');if(!node)return;
  const card=node.closest('[data-serial]');if(!card)return;
  const serial=card.dataset.serial,action=node.dataset.action,a=currentAgent(serial)||state.status;
  if(action==='apply'){const r=findRevision(card.querySelector('[data-preset]').value);return sendCommand(serial,'apply',{preset_id:r.preset_id,revision:r.id});}
  if(['standby','reboot','poweroff'].includes(action))return sendCommand(serial,action);
  if(action==='operation'){const id=a.pending?.id||a.state?.operation?.id;if(id){const result=await api(`${contextBase(server()?serial:null)}/operations/${encodeURIComponent(id)}`);notice(JSON.stringify(result.operation||result));}return poll();}
  if(action==='edit-current'){const r=state.revisions.find(r=>r.id===a.state?.target?.revision);if(!r)throw new Error('La revisión ejecutada aún no está en la biblioteca; sincronice o importe los presets de la Pi.');return openEditor(r,server()?serial:null);}
  if(action==='edit-context'){
    if(a.state?.target)return openEditor(findRevision(a.state.target.revision),server()?serial:null);
    const selected=card.querySelector('[data-preset]')?.value;
    if(!selected){tab('library');notice('Seleccione un preset en la tarjeta y pulse Editar desde esta Pi.');return;}
    return openEditor(findRevision(selected),server()?serial:null);
  }
  if(action==='logs'){tab('maintenance');$('maintenance-target').value=server()?serial:'';await loadLogs();return;}
  if(action==='onboard'){
    const imported=a.import_available&&await confirmAction('Importar biblioteca de la Pi','La biblioteca central está vacía. ¿Importar las revisiones locales de esta Pi? Cancelar incorpora la Pi conservando la biblioteca central vacía.');
    await api(`/api/agents/${encodeURIComponent(serial)}/onboard`,{method:'POST',body:{import_presets:!!imported}});return poll();
  }
  if(action==='identity'){
    const id=window.prompt('ID positivo. Si está ocupado se intercambian los IDs.',a.device_id);if(id===null)return;
    const name=window.prompt('Nombre visible del equipo',a.name);if(name===null)return;
    await api(`/api/agents/${encodeURIComponent(serial)}`,{method:'PATCH',body:{device_id:Number(id),name}});return poll();
  }
  if(action==='forget'){if(await confirmAction('Retirar Pi','Se eliminan su ID y asignación del servidor. La Pi y sus presets siguen funcionando.'))await api(`/api/agents/${encodeURIComponent(serial)}`,{method:'DELETE'});return poll();}
  if(['accept','synchronize'].includes(action)){await api(`/api/agents/${encodeURIComponent(serial)}/${action}`,{method:'POST'});return poll();}
}
async function presetAction(event){
  const node=event.target.closest('[data-action]');if(!node)return;
  const card=node.closest('[data-preset-id]');if(!card)return;
  const preset=state.presets.find(p=>p.preset_id===card.dataset.presetId),action=node.dataset.action;
  if(action==='edit')return openEditor(preset.revision);
  if(action==='duplicate')return openEditor(preset.revision,null,true);
  if(action==='resolve')return conflict(preset);
  if(action==='export'){download(`${preset.name}.json`,{schema_version:1,revisions:state.revisions.filter(r=>r.preset_id===preset.preset_id)});return;}
  if(action==='rename'){
    const name=window.prompt('Nombre nuevo (distingue mayúsculas y minúsculas)',preset.name);if(!name||name===preset.name)return;
    await api('/api/presets',{method:'POST',body:{preset_id:preset.preset_id,parents:[preset.revision.id],name,config:preset.revision.config}});return poll();
  }
  if(action==='delete'){if(await confirmAction('Borrar preset',`¿Borrar «${preset.name}»? Se bloqueará si figura activo o asignado, incluso a una Pi offline.`))await api(`/api/presets/${encodeURIComponent(preset.preset_id)}`,{method:'DELETE'});return poll();}
}
function download(name,data){
  const blob=new Blob([typeof data==='string'?data:JSON.stringify(data,null,2)],{type:typeof data==='string'?'text/plain':'application/json'});const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(link.href),2000);
}
async function downloadBackup(){
  const response=await fetch(`${maintenanceBase()}/backup`,{cache:'no-store'});
  if(!response.ok)throw new Error(`Error descargando copia de seguridad: ${response.status}`);
  const link=document.createElement('a');link.href=URL.createObjectURL(await response.blob());link.download='omi-backup.sqlite3';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),2000);
}
async function loadLogs(){const result=await api(`${maintenanceBase()}/logs?level=${encodeURIComponent($('log-level').value)}&limit=200`);$('logs').textContent=result.lines.join('\n')||'No hay registros para este filtro.';}
async function loadSettings(){const result=await api(`${maintenanceBase()}/settings`);$('settings-json').value=JSON.stringify(result,null,2);}
async function applyManagement(){
  if(!$('management-form').reportValidity())return;
  if(!await confirmAction('Cambiar red de administración','Se aplicará el perfil Ethernet de esta Pi. Su dirección de acceso puede cambiar; no hay reversión automática. Confirme que podrá localizarla con la nueva dirección.'))return;
  const base=maintenanceBase();
  await api(`${base}/settings`,{method:'PUT',body:{interface:$('management-interface').value.trim()||null}});
  const result=await api(`${base}/commands`,{method:'POST',body:{action:'management',arguments:{network:{vlan:null,mode:$('management-mode').value,address:$('management-address').value.trim(),gateway:$('management-gateway').value.trim(),dns:$('management-dns').value.split(',').map(v=>v.trim()).filter(Boolean)}}}});
  $('maintenance-result').hidden=false;$('maintenance-result').textContent=JSON.stringify(result,null,2);notice('Cambio de red aceptado. Consulte su progreso en la tarjeta del equipo.');
}
async function maintenance(action){
  const destructive=['reset-state','reset-installation','factory-reset'].includes(action);
  if(action!=='clear-logs'&&!await confirmAction(`Mantenimiento · ${action}`,action==='factory-reset'?'Se borrarán los datos de usuario y los presets del equipo elegido. Las bibliotecas de otros equipos no se borran.':action==='reset-installation'?'Se borrarán incorporaciones y asignaciones del servidor. La biblioteca se conserva.':action==='reset-state'?'Se limpian datos temporales conservando presets y ajustes.':'Se preparará una versión de OMI y se comprobará antes del cambio. El resultado se mostrará en diagnóstico.',destructive?action:null,destructive))return;
  const result=await api(`${maintenanceBase()}/maintenance/${action}`,{method:'POST',body:{confirm:destructive?true:undefined,backup:$('confirm-backup').checked}});$('maintenance-result').hidden=false;$('maintenance-result').textContent=JSON.stringify(result,null,2);await poll();
}
function bind(){
  document.querySelectorAll('.tab').forEach(e=>e.onclick=()=>tab(e.dataset.tab));
  $('refresh').onclick=()=>run(poll);$('agent-list').onclick=e=>run(()=>agentAction(e));$('discovered').onclick=e=>run(()=>agentAction(e));$('preset-list').onclick=e=>run(()=>presetAction(e));
  $('agent-list').onchange=e=>{if(e.target.dataset.select){if(e.target.checked)state.selected.add(e.target.dataset.select);else state.selected.delete(e.target.dataset.select);$('selection-count').textContent=`${state.selected.size} seleccionados`;}};
  document.querySelectorAll('[data-group]').forEach(e=>e.onclick=()=>run(async()=>{
    if(!state.selected.size)throw new Error('Seleccione al menos una Pi.');
    if(['poweroff','reboot','update'].includes(e.dataset.group)&&!await confirmAction('Acción de grupo',`¿Solicitar ${e.dataset.group} a ${state.selected.size} Pi? Los equipos offline u ocupados se omitirán.`))return;
    showGroupResults(await api('/api/groups/commands',{method:'POST',body:{serials:[...state.selected],command:{action:e.dataset.group}}}));await poll();
  }));
  $('group-apply').onclick=()=>run(async()=>{if(!state.selected.size)throw new Error('Seleccione al menos una Pi.');const r=findRevision($('group-preset').value);showGroupResults(await api('/api/groups/commands',{method:'POST',body:{serials:[...state.selected],command:{action:'apply',target:{preset_id:r.preset_id,revision:r.id}}}}));await poll();});
  $('new-preset').onclick=()=>run(()=>openEditor());$('show-deleted').onchange=renderPresets;
  $('export-library').onclick=()=>run(async()=>download('omi-biblioteca.json',await api('/api/presets/export')));
  $('import-library').onchange=e=>run(async()=>{const file=e.target.files[0];if(!file)return;const parsed=JSON.parse(await file.text());const revisions=Array.isArray(parsed)?parsed:parsed.revisions;if(!Array.isArray(revisions))throw new Error('El archivo debe contener revisiones de OMI v3.');const result=await api('/api/presets/import',{method:'POST',body:{revisions}});notice(`Importación: ${result.added} revisiones nuevas`);e.target.value='';await poll();});
  $('preset-form').onsubmit=e=>{e.preventDefault();run(()=>savePreset(false));};$('save-apply').onclick=()=>run(()=>savePreset(true));
  $('preset-form').oninput=()=>{state.dirty=true;};$('preset-service').onchange=serviceFields;$('network-mode').onchange=serviceFields;
  $('close-editor').onclick=$('cancel-editor').onclick=()=>run(closeEditor);$('preset-editor').oncancel=e=>{e.preventDefault();run(closeEditor);};
  $('capture-agent').onchange=()=>run(async()=>{await beginSession(false);notice('Pi de captura seleccionada. Elija su entrada MIDI sin sustituir la selección automáticamente.');});
  $('refresh-inputs').onclick=()=>run(refreshInputs);
  $('add-destination').onclick=()=>{readDraft();state.draft.config.midi.destinations.push({host:'127.0.0.1',port:7001});state.dirty=true;renderDestinations();};
  $('destinations').onclick=e=>{const index=e.target.dataset.removeDestination;if(index!==undefined){readDraft();state.draft.config.midi.destinations.splice(Number(index),1);state.dirty=true;renderDestinations();}};
  $('add-route').onclick=()=>run(()=>openRoute());$('routes').onclick=e=>run(async()=>{if(e.target.dataset.routeEdit!==undefined)await openRoute(Number(e.target.dataset.routeEdit));if(e.target.dataset.routeRemove!==undefined){state.draft.config.midi.routes.splice(Number(e.target.dataset.routeRemove),1);state.dirty=true;renderRoutes();}});
  $('route-form').onsubmit=e=>{e.preventDefault();run(()=>closeRoute(true));};$('close-route').onclick=$('cancel-route').onclick=()=>run(()=>closeRoute(false));$('route-editor').oncancel=e=>{e.preventDefault();run(()=>closeRoute(false));};$('learn').onclick=()=>run(learn);
  $('save-route-preset').onclick=()=>run(async()=>{if(commitRoute())await savePreset(false);});
  $('simulate-midi').onclick=()=>run(async()=>{await api(`${state.sessionBase}/simulate/midi`,{method:'POST',body:{type:'note_on',note:60,velocity:100,channel:0}});await readLearn();});
  for(let i=0;i<16;i++)$('route-channel').add(new Option(String(i+1),String(i)));
  $('duplicate-draft').onclick=()=>run(async()=>{readDraft();await endSession();delete state.draft.id;delete state.draft.preset_id;state.draft.name+=' (copia)';$('preset-name').value=state.draft.name;state.context=null;state.dirty=true;$('editor-context-label').textContent='NUEVO PRESET';$('draft-status').textContent='Copia independiente. Guarde para crear una identidad nueva.';});
  $('close-conflict').onclick=()=>$('conflict-editor').close();$('conflict-options').onclick=e=>run(async()=>{const id=e.target.dataset.resolveId;if(!id)return;await api(`/api/presets/${encodeURIComponent(e.target.dataset.resolvePreset)}/resolve`,{method:'POST',body:{revision:id}});$('conflict-editor').close();await poll();notice('Conflicto resuelto en una revisión nueva.');});
  $('confirm-form').onsubmit=e=>{e.preventDefault();if($('confirm-dialog').dataset.phrase&&$('confirm-phrase').value!==$('confirm-dialog').dataset.phrase){notice('La frase de confirmación no coincide.',true);return;}finishConfirm(true);};$('confirm-cancel').onclick=()=>finishConfirm(false);$('confirm-dialog').oncancel=e=>{e.preventDefault();finishConfirm(false);};
  $('read-logs').onclick=()=>run(loadLogs);$('download-logs').onclick=()=>run(async()=>download('omi.log',await api(`${maintenanceBase()}/logs/download`)));$('load-settings').onclick=()=>run(loadSettings);$('save-settings').onclick=()=>run(async()=>{await api(`${maintenanceBase()}/settings`,{method:'PUT',body:JSON.parse($('settings-json').value)});notice('Ajustes guardados.');});
  $('maintenance-target').onchange=()=>{state.maintenanceLoaded=false;$('settings-json').value='';$('logs').textContent='Seleccione Actualizar logs para leer este equipo.';$('reset-installation').hidden=!server()||!!$('maintenance-target').value;$('management-network').hidden=server()&&!$('maintenance-target').value;};
  $('management-mode').onchange=()=>{$('management-address').required=$('management-mode').value==='static';};$('management-form').onsubmit=e=>{e.preventDefault();run(applyManagement);};
  $('backup').onclick=()=>run(downloadBackup);document.querySelectorAll('[data-maintenance]').forEach(e=>e.onclick=()=>run(()=>maintenance(e.dataset.maintenance)));
  window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
  window.addEventListener('pagehide',()=>{if(state.session)fetch(`${state.sessionBase}/editors/${encodeURIComponent(state.session.id)}`,{method:'DELETE',keepalive:true}).catch(()=>{});});
}
bind();run(poll);setInterval(()=>run(poll),2000);setInterval(()=>run(renewSession),8000);
