(function(){
  var configCache = {};
  var currentDevices = [];
  var currentClients = [];

  function escapeHtml(str){
    var map = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'};
    var value = (str === undefined || str === null) ? '' : String(str);
    return value.replace(/[&<>"']/g, function(c){ return map[c] || c; });
  }

  function toArray(list){ return Array.prototype.slice.call(list || []); }

  function formatDate(value){
    if(!value) return '—';
    var date = new Date(value);
    if(isNaN(date.getTime())) return escapeHtml(value);
    return date.toLocaleString();
  }

  function getJson(url){
    return fetch(url).then(function(res){
      if(!res.ok) throw new Error(url + ' => ' + res.status);
      return res.json();
    });
  }

  function fetchDevices(){
    return getJson('/api/devices').then(function(data){ return data.devices || []; });
  }

  function fetchClients(){
    return getJson('/api/clients').then(function(data){ return data.clients || []; });
  }

  function ensureConfigs(service, force){
    if(!service || service === 'standby') return Promise.resolve([]);
    if(!force && configCache[service]) return Promise.resolve(configCache[service]);
    return getJson('/api/configs/' + encodeURIComponent(service)).then(function(data){
      configCache[service] = data.configs || [];
      return configCache[service];
    });
  }

  function renderConfigOptions(service, active){
    var configs = configCache[service] || [];
    var html = '<option value="">(actual)</option>';
    configs.forEach(function(cfg){
      var selected = cfg.name === active ? 'selected' : '';
      html += '<option value="' + escapeHtml(cfg.name) + '" ' + selected + '>' + escapeHtml(cfg.name) + '</option>';
    });
    return html;
  }

  function renderDevice(dev){
    var online = !!dev.online;
    var state = dev.service_state || {};
    var heartbeat = dev.heartbeat || {};
    var services = dev.services || [];
    var available = dev.available_services || [];
    var activeEntry = services.find(function(s){ return s.enabled; });
    var active = state.expected || (activeEntry ? activeEntry.name : 'standby');
    var transition = !!state.transition;
    var progressValue = (typeof state.progress === 'number') ? Math.max(0, Math.min(100, Number(state.progress))) : null;
    var stageText = state.stage || (transition ? 'Sincronizando' : '');
    if(stageText){ stageText = stageText.charAt(0).toUpperCase() + stageText.slice(1); }
    var ledClass = 'status-led ' + (transition ? 'syncing' : (online ? 'online' : 'offline'));
    var statusLabel = transition ? 'Synking' : (online ? 'Online' : 'Offline');
    var cpu = (heartbeat.cpu != null) ? Number(heartbeat.cpu).toFixed(0) + '%' : '--';
    var temp = (heartbeat.temp != null) ? Number(heartbeat.temp).toFixed(0) + '°C' : '--';
    var serviceReturn = state.returncode != null ? state.returncode : '—';
    var serviceError = state.error || state.last_error || '';
    var serviceConfig = state.config_name || '—';
    var webUrl = state.web_url || '';
    var desiredService = dev.desired_service || '—';
    var desiredConfig = dev.desired_config || '—';
    var ip = dev.ip || '-';
    var lastSeen = formatDate(dev.last_seen);
    var indexLabel = (dev.index !== undefined && dev.index !== null) ? ('#' + dev.index) : '#--';
    var nameHeader = '<span class="index-label">' + escapeHtml(indexLabel) + '</span>' + escapeHtml(dev.host || dev.serial || 'Agente');
    var availableOptions = available.map(function(name){
      return '<option value="' + escapeHtml(name) + '" ' + (name===active ? 'selected' : '') + '>' + escapeHtml(name) + '</option>';
    }).join('');
    var configsHtml = (active !== 'standby')
      ? '<select class="config-select" data-config-for="' + escapeHtml(dev.serial || '') + '" data-active-config="' + escapeHtml(state.config_name || '') + '" ' + (!online || transition ? 'disabled' : '') + '>' + renderConfigOptions(active, state.config_name) + '</select>'
      : '<div class="small">Sin opciones de configuración.</div>';
    var configBtn = webUrl
      ? '<button class="btn" data-config-url="' + escapeHtml(webUrl) + '" data-config-title="' + escapeHtml(dev.host || dev.serial || 'Configuración') + '" ' + (!online || transition ? 'disabled' : '') + '>Configurar</button>'
      : '<button class="btn" disabled>Configurar</button>';
    var powerButtons = '<div class="card-actions">' +
      '<button class="btn warning" data-power="reboot" data-serial="' + escapeHtml(dev.serial || '') + '" ' + (!online || transition ? 'disabled' : '') + '>Reiniciar</button>' +
      '<button class="btn danger-solid" data-power="shutdown" data-serial="' + escapeHtml(dev.serial || '') + '" ' + (!online || transition ? 'disabled' : '') + '>Apagar</button>' +
    '</div>';
    var transitionHtml = '—';
    if(transition){
      var pct = (progressValue != null ? progressValue : 0);
      transitionHtml = '<div>' + escapeHtml(stageText || 'Sincronizando') + '</div>' +
        '<div class="progress' + (pct >= 100 ? ' done' : '') + '"><div class="progress-inner" style="width:' + pct + '%;"></div></div>';
    } else if(stageText){
      transitionHtml = escapeHtml(stageText);
    }

    return (
      '<div class="card" data-serial="' + escapeHtml(dev.serial || '') + '">' +
      '<div class="card-headline"><h2><span class="' + ledClass + '"></span>' + nameHeader + (online ? '' : ' <span class="tag">Offline</span>') + '</h2>' + powerButtons + '</div>' +
      '<div class="small">Serial: ' + escapeHtml(dev.serial || '?') + '</div>' +
      '<div class="small">IP: ' + escapeHtml(ip) + '</div>' +
      '<div class="small">Último contacto: ' + escapeHtml(lastSeen) + '</div>' +
      '<table class="table">' +
        '<tr><th>Estado</th><td class="' + (online ? 'status-ok' : 'status-bad') + '">' + escapeHtml(statusLabel) + '</td></tr>' +
        '<tr><th>Transición</th><td>' + transitionHtml + '</td></tr>' +
        '<tr><th>Servicio activo</th><td>' + escapeHtml(active) + '</td></tr>' +
        '<tr><th>Config actual</th><td>' + escapeHtml(serviceConfig) + '</td></tr>' +
        '<tr><th>Return code</th><td>' + escapeHtml(serviceReturn) + '</td></tr>' +
        '<tr><th>Error servicio</th><td>' + (serviceError ? escapeHtml(serviceError) : '—') + '</td></tr>' +
        '<tr><th>CPU</th><td>' + cpu + '</td></tr>' +
        '<tr><th>Temperatura</th><td>' + temp + '</td></tr>' +
        '<tr><th>Deseado</th><td>' + escapeHtml(desiredService) + ' / ' + escapeHtml(desiredConfig) + '</td></tr>' +
        '<tr><th>Servicio</th><td>' +
          '<select data-service-select data-serial="' + escapeHtml(dev.serial || '') + '" data-active-service="' + escapeHtml(active) + '" ' + (!online || transition ? 'disabled' : '') + '>' +
            availableOptions +
          '</select>' +
          configsHtml +
          '<div style="margin-top:8px; display:flex; gap:8px; flex-wrap:wrap;">' +
            '<button class="btn" data-apply-service="' + escapeHtml(dev.serial || '') + '" ' + (!online || transition ? 'disabled' : '') + '>Aplicar</button>' +
            configBtn +
          '</div>' +
        '</td></tr>' +
      '</table>' +
      '</div>'
    );
  }

  function renderDevices(devices){
    currentDevices = devices;
    var container = document.getElementById('devicesView');
    var selectionSnapshot = {};
    toArray(container.querySelectorAll('.card[data-serial]')).forEach(function(card){
      var serial = card.dataset.serial;
      if(!serial) return;
      var serviceSel = card.querySelector('select[data-service-select]');
      var configSel = card.querySelector('select[data-config-for]');
      selectionSnapshot[serial] = {
        service: serviceSel ? serviceSel.value : null,
        config: configSel ? configSel.value : null
      };
    });
    if(!devices.length){
      container.innerHTML = '<div class="card">No se detectaron agentes.</div>';
      return;
    }
    container.innerHTML = devices.map(renderDevice).join('');

    toArray(container.querySelectorAll('select[data-service-select]')).forEach(function(sel){
      var serial = sel.dataset.serial;
      var active = sel.getAttribute('data-active-service');
      var snapshot = selectionSnapshot[serial];
      if(snapshot && snapshot.service !== null && snapshot.service !== undefined && snapshot.service !== active && !sel.disabled){
        sel.value = snapshot.service;
      }
    });

    toArray(container.querySelectorAll('select[data-config-for]')).forEach(function(sel){
      var serial = sel.dataset.configFor;
      var activeConfig = sel.getAttribute('data-active-config') || '';
      var snapshot = selectionSnapshot[serial];
      if(snapshot && snapshot.config !== null && snapshot.config !== undefined && snapshot.config !== activeConfig && !sel.disabled){
        sel.value = snapshot.config;
      }
    });

    toArray(container.querySelectorAll('select[data-service-select]')).forEach(function(sel){
      sel.addEventListener('change', function(){
        var service = sel.value;
        ensureConfigs(service).then(function(){
          var card = sel.closest('.card');
          var configSelect = card ? card.querySelector('select[data-config-for]') : null;
          if(configSelect){
            configSelect.innerHTML = renderConfigOptions(service, null);
            configSelect.disabled = (service === 'standby' || sel.disabled);
          }
        }).catch(console.error);
      });
    });

    toArray(container.querySelectorAll('button[data-power]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var serial = btn.dataset.serial;
        var action = btn.dataset.power;
        sendPowerCommand(serial, action, btn);
      });
    });

    toArray(container.querySelectorAll('button[data-apply-service]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var serial = btn.dataset.applyService;
        var card = btn.closest('.card');
        if(!card) return;
        var serviceSel = card.querySelector('select[data-service-select]');
        var configSel = card.querySelector('select[data-config-for]');
        var service = serviceSel ? serviceSel.value : '';
        var config = configSel ? configSel.value : '';
        sendServiceChange(serial, service, config);
      });
    });

    toArray(container.querySelectorAll('button[data-config-url]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var url = btn.dataset.configUrl;
        var title = btn.dataset.configTitle || 'Configuración';
        openConfig(url, title);
      });
    });
  }

  function renderServicesView(){
    var container = document.getElementById('servicesView');
    var midi = configCache['MIDI'] || [];
    var html = '<div class="card"><h2>Configuraciones MIDI</h2>';
    if(!midi.length){
      html += '<div class="small">Todavía no hay configuraciones guardadas.</div>';
    } else {
      html += '<table class="table"><tr><th>Nombre</th><th>Última actualización</th><th></th></tr>';
      midi.forEach(function(cfg){
        html += '<tr><td>' + escapeHtml(cfg.name) + '</td><td>' + escapeHtml(cfg.updated_at || '') + '</td><td><button class="btn" data-delete-config="MIDI::' + escapeHtml(cfg.name) + '">Eliminar</button></td></tr>';
      });
      html += '</table>';
    }
    html += '</div>';
    container.innerHTML = html;

    toArray(container.querySelectorAll('button[data-delete-config]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var parts = (btn.dataset.deleteConfig || '').split('::');
        if(parts.length !== 2) return;
        var service = parts[0];
        var name = parts[1];
        if(!confirm('¿Eliminar la configuración ' + name + '?')) return;
        fetch('/api/configs/' + encodeURIComponent(service) + '/' + encodeURIComponent(name), { method:'DELETE' })
          .then(function(){ ensureConfigs(service, true).then(renderServicesView).catch(console.error); })
          .catch(function(err){ alert('No se pudo eliminar la configuración: ' + err); });
      });
    });
  }

  function renderClientsView(clients){
    currentClients = clients;
    var container = document.getElementById('clientsView');
    if(!clients.length){
      container.innerHTML = '<div class="card">No hay clientes registrados.</div>';
      return;
    }
    var html = '<div class="card"><h2>Clientes registrados</h2>';
    html += '<table class="table"><tr><th>Serial</th><th>Host</th><th>Servicio deseado</th><th>Configuración</th><th>Actualizado</th><th></th></tr>';
    clients.forEach(function(client){
      html += '<tr>' +
        '<td>' + escapeHtml(client.serial || '') + '</td>' +
        '<td>' + escapeHtml(client.host || '—') + '</td>' +
        '<td>' + escapeHtml(client.desired_service || '—') + '</td>' +
        '<td>' + escapeHtml(client.desired_config || '—') + '</td>' +
        '<td>' + escapeHtml(client.updated_at || '') + '</td>' +
        '<td><button class="btn" data-remove-client="' + escapeHtml(client.serial || '') + '">Eliminar</button></td>' +
      '</tr>';
    });
    html += '</table></div>';
    container.innerHTML = html;

    toArray(container.querySelectorAll('button[data-remove-client]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var serial = btn.dataset.removeClient;
        if(!serial || !confirm('¿Eliminar el cliente ' + serial + '?')) return;
        fetch('/api/devices/' + encodeURIComponent(serial), { method:'DELETE' })
          .then(function(){ return Promise.all([loadClients(), loadDevices()]); })
          .catch(function(err){ alert('No se pudo eliminar el cliente: ' + err); });
      });
    });
  }

  function sendServiceChange(serial, service, config){
    if(!service){
      alert('Selecciona un servicio.');
      return;
    }
    var body = { service: service };
    if(config) body.config = config;
    fetch('/api/devices/' + encodeURIComponent(serial) + '/service', {
      method:'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function(res){
      if(!res.ok){
        return res.json().catch(function(){ return { detail:'error' }; }).then(function(detail){
          throw new Error(detail.detail || res.status);
        });
      }
    }).catch(function(err){
      alert('Error cambiando servicio: ' + err);
    }).finally(function(){
      ensureConfigs(service, true).finally(function(){ setTimeout(loadDevices, 500); });
    });
  }

  function sendPowerCommand(serial, action, button){
    if(!serial || !action) return;
    var confirmMsg = action === 'shutdown'
      ? '¿Apagar la Raspberry ' + serial + '?'
      : '¿Reiniciar la Raspberry ' + serial + '?';
    if(!confirm(confirmMsg)) return;
    if(button) button.disabled = true;
    fetch('/api/devices/' + encodeURIComponent(serial) + '/power', {
      method:'POST',
      headers:{ 'Content-Type':'application/json' },
      body: JSON.stringify({ action: action })
    }).then(function(res){
      if(!res.ok){
        return res.json().catch(function(){ return { detail:'error' }; }).then(function(detail){
          throw new Error(detail.detail || res.status);
        });
      }
    }).catch(function(err){
      alert('Error enviando comando: ' + err);
    }).finally(function(){
      if(button){ button.disabled = false; }
      setTimeout(loadDevices, 1200);
    });
  }

  function showView(view){
    toArray(document.querySelectorAll('[data-view-btn]')).forEach(function(btn){
      btn.classList.toggle('active', btn.dataset.viewBtn === view);
    });
    document.getElementById('devicesView').classList.toggle('hidden', view !== 'devices');
    document.getElementById('clientsView').classList.toggle('hidden', view !== 'clients');
    document.getElementById('servicesView').classList.toggle('hidden', view !== 'services');
    if(view === 'services'){
      loadServiceConfigs();
    } else if(view === 'clients'){
      loadClients();
    }
  }

  function openConfig(url, title){
    if(!url) return;
    var overlay = document.getElementById('configOverlay');
    document.getElementById('configFrame').src = url;
    document.getElementById('overlayTitle').textContent = title || 'Configuración';
    overlay.classList.add('active');
  }

  function closeConfig(){
    var overlay = document.getElementById('configOverlay');
    overlay.classList.remove('active');
    document.getElementById('configFrame').src = 'about:blank';
  }

  var closeBtn = document.getElementById('closeOverlayBtn');
  if(closeBtn){
    closeBtn.addEventListener('click', function(){
      closeConfig();
      showView('devices');
    });
  }

  toArray(document.querySelectorAll('[data-view-btn]')).forEach(function(btn){
    btn.addEventListener('click', function(){ showView(btn.dataset.viewBtn); });
  });

  function loadDevices(){
    fetchDevices().then(function(devices){
      var services = new Set();
      devices.forEach(function(dev){ (dev.available_services || []).forEach(function(s){ services.add(s); }); });
      return Promise.all(Array.from(services).map(function(service){ return ensureConfigs(service); })).then(function(){
        renderDevices(devices);
      });
    }).catch(console.error);
  }

  function loadServiceConfigs(){
    ensureConfigs('MIDI', true).then(renderServicesView).catch(console.error);
  }

  function loadClients(){
    fetchClients().then(renderClientsView).catch(console.error);
  }

  setInterval(loadDevices, 4000);
  setInterval(loadServiceConfigs, 15000);
  setInterval(loadClients, 15000);
  loadDevices();
  loadServiceConfigs();
  loadClients();
})();
