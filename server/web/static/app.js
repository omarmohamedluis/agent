(function(){
  var configCache = {};
  var currentDevices = [];
  var currentClients = [];
  var NO_CONFIG_OPTION = '__no_config__';
  var configEditorState = {
    editing: false,
    service: null,
    originalName: null,
  };
  var configEditorOverlay = document.getElementById('configEditorOverlay');
  var configEditorForm = document.getElementById('configEditorForm');
  var configEditorTitle = document.getElementById('configEditorTitle');
  var configEditorCloseBtn = document.getElementById('configEditorCloseBtn');
  var configEditorCancelBtn = document.getElementById('configEditorCancelBtn');
  var configEditorSaveBtn = document.getElementById('configEditorSaveBtn');
  var configServiceInput = document.getElementById('configService');
  var configServiceSuggestions = document.getElementById('serviceSuggestions');
  var configNameInput = document.getElementById('configName');
  var configSourceInput = document.getElementById('configSource');
  var midiSection = document.getElementById('midiConfigSection');
  var genericSection = document.getElementById('genericConfigSection');
  var midiInputField = document.getElementById('midiInput');
  var midiOscPortField = document.getElementById('midiOscPort');
  var midiOscIpsField = document.getElementById('midiOscIps');
  var midiUiPortField = document.getElementById('midiUiPort');
  var midiRoutesTable = document.getElementById('midiRoutesTable');
  var addMidiRouteBtn = document.getElementById('addMidiRouteBtn');
  var genericConfigTextarea = document.getElementById('genericConfigData');

  function escapeHtml(str){
    var map = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'};
    var value = (str === undefined || str === null) ? '' : String(str);
    return value.replace(/[&<>"']/g, function(c){ return map[c] || c; });
  }

  function toArray(list){ return Array.prototype.slice.call(list || []); }

  function addServiceCandidate(targetSet, name){
    if(!name) return;
    var value = String(name).trim();
    if(!value || value.toLowerCase() === 'standby') return;
    targetSet.add(value);
  }

  function gatherKnownServices(){
    var services = new Set();
    Object.keys(configCache || {}).forEach(function(service){
      addServiceCandidate(services, service);
    });
    currentDevices.forEach(function(dev){
      (dev.available_services || []).forEach(function(service){
        addServiceCandidate(services, service);
      });
      (dev.services || []).forEach(function(entry){
        if(entry && typeof entry.name === 'string'){
          addServiceCandidate(services, entry.name);
        }
      });
      if(dev.desired_service){
        addServiceCandidate(services, dev.desired_service);
      }
    });
    currentClients.forEach(function(client){
      if(client.desired_service){
        addServiceCandidate(services, client.desired_service);
      }
    });
    services.add('MIDI');
    services.delete('');
    return Array.from(services).sort();
  }

  function resolveHostLabel(identifier){
    if(!identifier) return '';
    var serial = String(identifier);
    var client = currentClients.find(function(item){ return item && item.serial === serial; });
    if(client && client.host){
      return client.host;
    }
    var device = currentDevices.find(function(item){ return item && item.serial === serial; });
    if(device && device.host){
      return device.host;
    }
    return serial;
  }

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
    if(!service || service === 'standby'){
      return '';
    }
    var configs = configCache[service] || [];
    var normalizedActive = active || NO_CONFIG_OPTION;
    var html = '<option value="' + NO_CONFIG_OPTION + '"' + (normalizedActive === NO_CONFIG_OPTION ? ' selected' : '') + '>No enviar configuración</option>';
    var hasActive = normalizedActive === NO_CONFIG_OPTION;
    configs.forEach(function(cfg){
      var name = cfg && typeof cfg.name === 'string' ? cfg.name : '';
      if(!name){
        return;
      }
      var selected = name === normalizedActive;
      if(selected){
        hasActive = true;
      }
      html += '<option value="' + escapeHtml(name) + '"' + (selected ? ' selected' : '') + '>' + escapeHtml(name) + '</option>';
    });
    if(!hasActive && normalizedActive !== NO_CONFIG_OPTION){
      html += '<option value="' + escapeHtml(normalizedActive) + '" selected>(actual: ' + escapeHtml(normalizedActive) + ')</option>';
    }
    return html;
  }

  function updateConfigSection(card, service, options){
    if(!card) return Promise.resolve();
    var configSelect = card.querySelector('select[data-config-for]');
    var wrapper = card.querySelector('.config-wrapper');
    if(!configSelect || !wrapper) return Promise.resolve();

    var serviceSelect = card.querySelector('select[data-service-select]');
    var serviceDisabled = serviceSelect ? serviceSelect.disabled : false;
    var desiredConfig;
    if(options && options.desiredConfig !== undefined && options.desiredConfig !== null){
      desiredConfig = options.desiredConfig;
    } else if(configSelect.dataset.selectedConfig){
      desiredConfig = configSelect.dataset.selectedConfig;
    } else if(configSelect.getAttribute('data-active-config')){
      desiredConfig = configSelect.getAttribute('data-active-config');
    } else {
      desiredConfig = NO_CONFIG_OPTION;
    }
    if(desiredConfig === ''){
      desiredConfig = NO_CONFIG_OPTION;
    }
    if(!desiredConfig){
      desiredConfig = NO_CONFIG_OPTION;
    }

    var existingHint = wrapper.querySelector('.config-hint');

    if(!service || service === 'standby'){
      wrapper.classList.add('hidden');
      if(existingHint){
        existingHint.remove();
      }
      configSelect.innerHTML = '';
      configSelect.disabled = true;
      configSelect.value = NO_CONFIG_OPTION;
      configSelect.dataset.currentService = 'standby';
      configSelect.dataset.selectedConfig = NO_CONFIG_OPTION;
      return Promise.resolve();
    }

    configSelect.dataset.currentService = service;
    configSelect.dataset.selectedConfig = desiredConfig;

    return ensureConfigs(service).then(function(){
      var html = renderConfigOptions(service, desiredConfig);
      configSelect.innerHTML = html;
      configSelect.disabled = serviceDisabled;
      if(configSelect.value !== desiredConfig){
        configSelect.value = desiredConfig;
      }
      wrapper.classList.remove('hidden');
      var hintNode = wrapper.querySelector('.config-hint');
      var hasConfigs = (configCache[service] || []).length > 0;
      if(hasConfigs){
        if(hintNode){
          hintNode.remove();
        }
      } else if(!hintNode){
        wrapper.insertAdjacentHTML('beforeend', '<div class="small config-hint">No hay configuraciones guardadas en el servidor.</div>');
      }
    }).catch(function(err){
      console.error(err);
    });
  }

  function renderDevice(dev, overrides){
    var serial = dev.serial || '';
    var online = !!dev.online;
    var state = dev.service_state || {};
    var heartbeat = dev.heartbeat || {};
    var services = dev.services || [];
    var available = dev.available_services || [];
    var activeEntry = services.find(function(s){ return s.enabled; });
    var active = state.expected || (activeEntry ? activeEntry.name : 'standby');
    var selectedService = (overrides && overrides.service) ? overrides.service : active;
    if(!selectedService){
      selectedService = active || 'standby';
    }
    var transition = !!state.transition;
    var progressValue = (typeof state.progress === 'number') ? Math.max(0, Math.min(100, Number(state.progress))) : null;
    var stageText = state.stage || (transition ? 'Sincronizando' : '');
    if(stageText){
      stageText = stageText.charAt(0).toUpperCase() + stageText.slice(1);
    }
    var ledClass = 'status-led ' + (transition ? 'syncing' : (online ? 'online' : 'offline'));
    var statusLabel = transition ? 'Synking' : (online ? 'Online' : 'Offline');
    var cpu = (heartbeat.cpu != null) ? Number(heartbeat.cpu).toFixed(0) + '%' : '--';
    var temp = (heartbeat.temp != null) ? Number(heartbeat.temp).toFixed(0) + '°C' : '--';
    var serviceReturn = state.returncode != null ? state.returncode : '—';
    var serviceError = state.error || state.last_error || '';
    var serviceConfig = state.config_name || '—';
    var desiredService = dev.desired_service || '—';
    var desiredConfig = dev.desired_config || '—';
    var ip = dev.ip || '-';
    var lastSeen = formatDate(dev.last_seen);
    var indexLabel = (dev.index !== undefined && dev.index !== null) ? ('#' + dev.index) : '#--';
    var nameHeader = '<span class="index-label">' + escapeHtml(indexLabel) + '</span>' + escapeHtml(dev.host || dev.serial || 'Agente');

    if(selectedService && available.indexOf(selectedService) === -1){
      available = available.concat([selectedService]);
    }
    var uniqueServices = Array.from(new Set(available));
    var serviceOptions = uniqueServices.map(function(name){
      return '<option value="' + escapeHtml(name) + '" ' + (name === selectedService ? 'selected' : '') + '>' + escapeHtml(name) + '</option>';
    }).join('');

    var transitionHtml = '—';
    if(transition){
      var pct = (progressValue != null ? progressValue : 0);
      transitionHtml = '<div>' + escapeHtml(stageText || 'Sincronizando') + '</div>' +
        '<div class="progress' + (pct >= 100 ? ' done' : '') + '"><div class="progress-inner" style="width:' + pct + '%;"></div></div>';
    } else if(stageText){
      transitionHtml = escapeHtml(stageText);
    }

    var selectedConfig = (overrides && overrides.config !== undefined && overrides.config !== null)
      ? overrides.config
      : (state.config_name || NO_CONFIG_OPTION);
    if(!selectedConfig){
      selectedConfig = NO_CONFIG_OPTION;
    }
    var shouldShowConfig = selectedService && selectedService !== 'standby';
    var configSelectDisabled = !shouldShowConfig || !online || transition;
    var configOptionsHtml = shouldShowConfig ? renderConfigOptions(selectedService, selectedConfig) : '';
    var configHintHtml = '';
    var configsHtml =
      '<div class="config-wrapper' + (shouldShowConfig ? '' : ' hidden') + '" data-config-wrapper="' + escapeHtml(serial) + '">' +
        '<select class="config-select" data-config-for="' + escapeHtml(serial) + '" data-active-config="' + escapeHtml(state.config_name || '') + '" data-current-service="' + escapeHtml(selectedService) + '" data-selected-config="' + escapeHtml(selectedConfig) + '" ' +
          (configSelectDisabled ? 'disabled' : '') + '>' +
          configOptionsHtml +
        '</select>' +
        configHintHtml +
      '</div>';

    var rows = [];
    rows.push(['Estado', '<td class="' + (online ? 'status-ok' : 'status-bad') + '">' + escapeHtml(statusLabel) + '</td>']);
    rows.push(['Transición', '<td>' + transitionHtml + '</td>']);
    rows.push(['Servicio activo', '<td>' + escapeHtml(active) + '</td>']);
    rows.push(['Config actual', '<td>' + escapeHtml(serviceConfig) + '</td>']);
    rows.push(['Return code', '<td>' + escapeHtml(serviceReturn) + '</td>']);
    rows.push(['Error servicio', '<td>' + (serviceError ? escapeHtml(serviceError) : '—') + '</td>']);
    rows.push(['CPU', '<td>' + cpu + '</td>']);
    rows.push(['Temperatura', '<td>' + temp + '</td>']);
    rows.push(['Deseado', '<td>' + escapeHtml(desiredService) + ' / ' + escapeHtml(desiredConfig) + '</td>']);

    var serviceActions = [];
    if(shouldShowConfig){
      serviceActions.push('<button class="btn" data-open-editor="' + escapeHtml(selectedService) + '" data-service-context="' + escapeHtml(selectedService) + '" ' + (!online ? 'disabled' : '') + '>Configurar</button>');
    }
    if(state.web_url){
      serviceActions.unshift('<button class="btn" data-service-ui="' + escapeHtml(state.web_url) + '" data-service-ui-serial="' + escapeHtml(serial) + '" ' + (!online ? 'disabled' : '') + '>Abrir UI</button>');
    }
    if(online && !transition && shouldShowConfig){
      serviceActions.push('<button class="btn" data-restart-service="' + escapeHtml(serial) + '" data-service="' + escapeHtml(active) + '" data-config="' + escapeHtml(state.config_name || '') + '">Reiniciar servicio</button>');
    }

    var serviceCell =
      '<td>' +
        (serviceActions.length ? '<div class="card-actions service-actions">' + serviceActions.join('') + '</div>' : '') +
        '<div class="service-selectors">' +
          '<select data-service-select data-serial="' + escapeHtml(serial) + '" data-active-service="' + escapeHtml(active) + '" data-selected-service="' + escapeHtml(selectedService) + '" ' + (!online || transition ? 'disabled' : '') + '>' +
            serviceOptions +
          '</select>' +
          configsHtml +
        '</div>' +
        '<div style="margin-top:8px; display:flex; gap:8px; flex-wrap:wrap;">' +
          '<button class="btn" data-apply-service="' + escapeHtml(serial) + '" ' + (!online || transition ? 'disabled' : '') + '>Aplicar</button>' +
        '</div>' +
      '</td>';
    rows.push(['Servicio', serviceCell]);

    var tableHtml = ['<table class="table">'];
    rows.forEach(function(row){
      tableHtml.push('<tr><th>' + escapeHtml(row[0]) + '</th>' + row[1] + '</tr>');
    });
    tableHtml.push('</table>');

    var powerButtons = '<div class="card-actions">' +
      '<button class="btn warning" data-power="reboot" data-serial="' + escapeHtml(serial) + '" ' + (!online || transition ? 'disabled' : '') + '>Reiniciar</button>' +
      '<button class="btn danger-solid" data-power="shutdown" data-serial="' + escapeHtml(serial) + '" ' + (!online || transition ? 'disabled' : '') + '>Apagar</button>' +
    '</div>';

    var cardParts = [];
    cardParts.push('<div class="card" data-serial="' + escapeHtml(serial) + '">');
    cardParts.push('<div class="card-headline"><h2><span class="' + ledClass + '"></span>' + nameHeader + (online ? '' : ' <span class="tag">Offline</span>') + '</h2>' + powerButtons + '</div>');
    cardParts.push('<div class="small">Serial: ' + escapeHtml(dev.serial || '?') + '</div>');
    cardParts.push('<div class="small">IP: ' + escapeHtml(ip) + '</div>');
    cardParts.push('<div class="small">Último contacto: ' + escapeHtml(lastSeen) + '</div>');
    cardParts = cardParts.concat(tableHtml);
    cardParts.push('</div>');

    return cardParts.join('');
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
    container.innerHTML = devices.map(function(dev){
      var overrides = selectionSnapshot[dev.serial] || {};
      return renderDevice(dev, overrides);
    }).join('');

    toArray(container.querySelectorAll('.card[data-serial]')).forEach(function(card){
      var serial = card.dataset.serial;
      if(!serial) return;
      var snapshot = selectionSnapshot[serial] || {};
      var serviceSel = card.querySelector('select[data-service-select]');
      var serviceValue = serviceSel ? serviceSel.value : null;
      updateConfigSection(card, serviceValue, { desiredConfig: snapshot.config });
    });

    toArray(container.querySelectorAll('select[data-service-select]')).forEach(function(sel){
      sel.addEventListener('change', function(){
        var service = sel.value;
        sel.dataset.selectedService = service;
        var card = sel.closest('.card');
        updateConfigSection(card, service);
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

    toArray(container.querySelectorAll('select[data-config-for]')).forEach(function(sel){
      sel.addEventListener('change', function(){
        sel.dataset.selectedConfig = sel.value || NO_CONFIG_OPTION;
      });
    });

    toArray(container.querySelectorAll('button[data-open-editor]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var service = btn.dataset.openEditor || '';
        if(!service || service === 'standby'){
          alert('Selecciona primero un servicio para abrir el editor.');
          return;
        }
        openConfigEditor(service);
      });
    });

    toArray(container.querySelectorAll('button[data-restart-service]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var serial = btn.dataset.restartService;
        var service = btn.dataset.service;
        var config = btn.dataset.config || '';
        if(!serial || !service || service === 'standby'){
          alert('Solo puedes reiniciar un servicio activo.');
          return;
        }
        if(!confirm('¿Reiniciar el servicio ' + service + ' en ' + serial + '?')) return;
        sendServiceChange(serial, service, config);
      });
    });

    toArray(container.querySelectorAll('button[data-service-ui]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        if(btn.disabled){
          return;
        }
        var url = btn.dataset.serviceUi;
        if(!url){
          return;
        }
        window.open(url, '_blank', 'noopener');
      });
    });
  }

  function renderServicesView(){
    var container = document.getElementById('servicesView');
    if(!container) return;
    var services = gatherKnownServices();
    if(!services.length){
      container.innerHTML = '<div class="card"><h2>Servicios</h2><div class="small">No hay configuraciones registradas todavía.</div><div class="card-actions" style="margin-top:12px;"><button class="btn" data-new-config="MIDI">Nueva configuración MIDI</button></div></div>';
      populateServiceSuggestions(null, false);
      attachServiceButtons(container);
      return;
    }

    var html = services.map(function(service){
      var configs = configCache[service] || [];
      var summary = '<span class="pill">' + configs.length + ' config' + (configs.length === 1 ? '' : 's') + '</span>';
      var card = '<div class="card service-card" data-service="' + escapeHtml(service) + '">';
      card += '<div class="service-card-header"><h3>' + escapeHtml(service) + ' ' + summary + '</h3><div class="card-actions"><button class="btn" data-new-config="' + escapeHtml(service) + '">Nueva configuración</button></div></div>';
      if(!configs.length){
        card += '<div class="small">Todavía no hay configuraciones guardadas.</div>';
      } else {
        card += '<table class="service-config-list"><tr><th>Nombre</th><th>Actualizado</th><th>Resumen</th><th class="actions">Acciones</th></tr>';
        configs.forEach(function(cfg){
          var updatedAt = cfg.updated_at ? formatDate(cfg.updated_at) : '—';
          var updaterLabel = cfg.updated_by ? resolveHostLabel(cfg.updated_by) : '';
          var updatedBy = updaterLabel ? ('por ' + escapeHtml(updaterLabel)) : '';
          var data = cfg.data || {};
          var summaryText = '';
          if(isMidiService(service)){
            var routesCount = Array.isArray(data.routes) ? data.routes.length : 0;
            summaryText = routesCount + ' ruta' + (routesCount === 1 ? '' : 's');
          } else {
            summaryText = Object.keys(data || {}).length + ' claves';
          }
          card += '<tr data-config-entry="' + escapeHtml(service) + '::' + escapeHtml(cfg.name) + '">' +
            '<td>' + escapeHtml(cfg.name) + '</td>' +
            '<td>' + escapeHtml(updatedAt) + '<br><span class="small">' + updatedBy + '</span></td>' +
            '<td>' + escapeHtml(summaryText) + '</td>' +
            '<td class="actions"><div class="card-actions">' +
              '<button class="btn" data-edit-config="' + escapeHtml(service) + '::' + escapeHtml(cfg.name) + '">Editar</button>' +
              '<button class="btn danger-solid" data-delete-config="' + escapeHtml(service) + '::' + escapeHtml(cfg.name) + '">Eliminar</button>' +
            '</div></td>' +
          '</tr>';
        });
        card += '</table>';
      }
      card += '</div>';
      return card;
    }).join('');

    container.innerHTML = html;
    populateServiceSuggestions(configEditorState.service, configEditorState.editing);
    attachServiceButtons(container);
  }

  function attachServiceButtons(container){
    toArray(container.querySelectorAll('button[data-new-config]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var service = btn.dataset.newConfig || gatherKnownServices()[0] || 'MIDI';
        openConfigEditor(service);
      });
    });

    toArray(container.querySelectorAll('button[data-edit-config]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var parts = (btn.dataset.editConfig || '').split('::');
        if(parts.length !== 2) return;
        var service = parts[0];
        var name = parts[1];
        var configs = configCache[service] || [];
        var cfg = configs.find(function(item){ return item && item.name === name; });
        if(!cfg){
          alert('No se encontró la configuración seleccionada.');
          return;
        }
        openConfigEditor(service, cfg);
      });
    });

    toArray(container.querySelectorAll('button[data-delete-config]')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var parts = (btn.dataset.deleteConfig || '').split('::');
        if(parts.length !== 2) return;
        var service = parts[0];
        var name = parts[1];
        if(!confirm('¿Eliminar la configuración "' + name + '" del servicio ' + service + '?')) return;
        fetch('/api/configs/' + encodeURIComponent(service) + '/' + encodeURIComponent(name), { method:'DELETE' })
          .then(function(res){
            if(!res.ok){
              throw new Error(res.status);
            }
            return ensureConfigs(service, true).then(function(){
              renderServicesView();
              loadDevices();
            });
          })
          .catch(function(err){
            alert('No se pudo eliminar la configuración: ' + err);
          });
      });
    });
  }

  function isMidiService(service){
    return String(service || '').trim().toUpperCase() === 'MIDI';
  }

  function populateServiceSuggestions(selectedService, lock){
    if(!configServiceSuggestions || !configServiceInput) return;
    var services = gatherKnownServices();
    if(selectedService && services.indexOf(selectedService) === -1){
      services.push(selectedService);
    }
    services = Array.from(new Set(services.filter(Boolean))).sort();
    if(!services.length){
      services = ['MIDI'];
    }
    configServiceSuggestions.innerHTML = services.map(function(service){
      return '<option value="' + escapeHtml(service) + '"></option>';
    }).join('');
    var desiredValue = selectedService || configServiceInput.value || services[0];
    configServiceInput.value = desiredValue || '';
    configServiceInput.readOnly = !!lock;
  }

  function toggleConfigSections(service){
    var midi = isMidiService(service);
    if(midiSection){
      midiSection.style.display = midi ? 'block' : 'none';
    }
    if(genericSection){
      genericSection.style.display = midi ? 'none' : 'block';
    }
    if(addMidiRouteBtn){
      addMidiRouteBtn.disabled = !midi;
    }
  }

  function resetConfigEditor(){
    configEditorState = { editing: false, service: null, originalName: null, routes: [] };
    if(configEditorForm){
      configEditorForm.reset();
    }
    if(configServiceInput){
      configServiceInput.value = '';
      configServiceInput.readOnly = false;
    }
    if(configNameInput){
      configNameInput.value = '';
      configNameInput.readOnly = false;
      configNameInput.disabled = false;
    }
    if(configSourceInput){
      configSourceInput.value = 'server-ui';
    }
    if(genericConfigTextarea){
      genericConfigTextarea.value = JSON.stringify({}, null, 2);
    }
    renderMidiRoutes([]);
    toggleConfigSections('');
  }

  function mapConfigRoutesToEditor(routes){
    if(!Array.isArray(routes)) return [];
    return routes.map(function(route){
      var type = (route && route.type) || (route && route.note !== undefined ? 'note' : 'cc');
      var number = (type === 'note') ? route.note : route.cc;
      return {
        type: (type || 'note').toLowerCase(),
        number: number !== undefined && number !== null ? String(number) : '',
        channel: route && route.channel !== undefined && route.channel !== null ? String(route.channel) : '',
        osc: (route && route.osc) || '',
        vtype: (route && route.vtype) || 'float',
        const: route && route.const !== undefined && route.const !== null ? String(route.const) : ''
      };
    });
  }

  function renderMidiRoutes(routes){
    if(!midiRoutesTable) return;
    var tbody = midiRoutesTable.querySelector('tbody');
    if(!tbody) return;
    var normalized = Array.isArray(routes) ? routes : [];
    configEditorState.routes = normalized.slice();
    if(!normalized.length){
      tbody.innerHTML = '<tr><td colspan="7" class="small">Añade rutas MIDI con el botón “Añadir ruta”.</td></tr>';
      return;
    }
    var rowsHtml = normalized.map(function(route, index){
      var type = (route.type || 'note').toLowerCase();
      var number = route.number !== undefined && route.number !== null ? route.number : '';
      var channel = route.channel !== undefined && route.channel !== null ? route.channel : '';
      var osc = route.osc || '';
      var vtype = route.vtype || 'float';
      var constVal = route.const !== undefined && route.const !== null ? route.const : '';
      return (
        '<tr data-route-index="' + index + '">' +
          '<td><select data-field="type">' +
            '<option value="note"' + (type === 'note' ? ' selected' : '') + '>note</option>' +
            '<option value="cc"' + (type === 'cc' ? ' selected' : '') + '>cc</option>' +
          '</select></td>' +
          '<td><input type="number" min="0" max="127" data-field="number" value="' + escapeHtml(number) + '" placeholder="0..127"></td>' +
          '<td><input type="number" min="0" max="15" data-field="channel" value="' + escapeHtml(channel) + '" placeholder="(any)"></td>' +
          '<td><input type="text" data-field="osc" value="' + escapeHtml(osc) + '" placeholder="/ruta"></td>' +
          '<td><select data-field="vtype">' +
            '<option value="float"' + (vtype === 'float' ? ' selected' : '') + '>float (0..1)</option>' +
            '<option value="int"' + (vtype === 'int' ? ' selected' : '') + '>int (0..127)</option>' +
            '<option value="bool"' + (vtype === 'bool' ? ' selected' : '') + '>bool</option>' +
            '<option value="const"' + (vtype === 'const' ? ' selected' : '') + '>const</option>' +
          '</select></td>' +
          '<td><input type="text" data-field="const" value="' + escapeHtml(constVal) + '" placeholder="1.0"></td>' +
          '<td class="actions"><button type="button" class="btn icon" data-remove-route="true">&times;</button></td>' +
        '</tr>'
      );
    }).join('');
    tbody.innerHTML = rowsHtml;

    toArray(tbody.querySelectorAll('tr')).forEach(function(tr){
      var vtypeSelect = tr.querySelector('[data-field="vtype"]');
      var constInput = tr.querySelector('[data-field="const"]');
      var removeBtn = tr.querySelector('[data-remove-route]');
      if(vtypeSelect && constInput){
        var toggleConst = function(){
          if(vtypeSelect.value === 'const'){
            constInput.disabled = false;
          } else {
            constInput.disabled = true;
            constInput.value = '';
          }
        };
        toggleConst();
        vtypeSelect.addEventListener('change', toggleConst);
      }
      if(removeBtn){
        removeBtn.addEventListener('click', function(){
          var snapshot = readRouteRows(false);
          var index = parseInt(tr.dataset.routeIndex, 10);
          if(!isNaN(index)){
            snapshot.splice(index, 1);
          }
          renderMidiRoutes(snapshot);
        });
      }
    });
  }

  function readRouteRows(strict){
    if(!midiRoutesTable) return [];
    var tbody = midiRoutesTable.querySelector('tbody');
    if(!tbody) return [];
    var rows = [];
    toArray(tbody.querySelectorAll('tr')).forEach(function(tr){
      var typeField = tr.querySelector('[data-field="type"]');
      if(!typeField) return;
      rows.push({
        type: (typeField.value || 'note').toLowerCase(),
        number: (tr.querySelector('[data-field="number"]') || {}).value || '',
        channel: (tr.querySelector('[data-field="channel"]') || {}).value || '',
        osc: (tr.querySelector('[data-field="osc"]') || {}).value || '',
        vtype: (tr.querySelector('[data-field="vtype"]') || {}).value || 'float',
        const: (tr.querySelector('[data-field="const"]') || {}).value || ''
      });
    });
    if(strict && !rows.length){
      throw new Error('Añade al menos una ruta MIDI.');
    }
    return rows;
  }

  function collectMidiRoutes(){
    var raw = readRouteRows(true);
    var routes = [];
    var allowedVtypes = ['float', 'int', 'bool', 'const'];
    raw.forEach(function(item, idx){
      var type = (item.type || 'note').toLowerCase();
      if(type !== 'note' && type !== 'cc'){
        throw new Error('Ruta #' + (idx + 1) + ': tipo inválido (uso "note" o "cc").');
      }
      var number = parseInt(item.number, 10);
      if(isNaN(number) || number < 0 || number > 127){
        throw new Error('Ruta #' + (idx + 1) + ': especifica un valor 0..127.');
      }
      var osc = (item.osc || '').trim();
      if(!osc){
        throw new Error('Ruta #' + (idx + 1) + ': indica la ruta OSC.');
      }
      if(osc.charAt(0) !== '/'){
        osc = '/' + osc;
      }
      var vtype = (item.vtype || 'float').toLowerCase();
      if(allowedVtypes.indexOf(vtype) === -1){
        throw new Error('Ruta #' + (idx + 1) + ': tipo de valor no soportado.');
      }
      var route = { type: type, osc: osc, vtype: vtype };
      if(type === 'note'){
        route.note = number;
      } else {
        route.cc = number;
      }
      var channelRaw = (item.channel || '').trim();
      if(channelRaw){
        var channel = parseInt(channelRaw, 10);
        if(isNaN(channel) || channel < 0 || channel > 15){
          throw new Error('Ruta #' + (idx + 1) + ': canal fuera de rango (0-15).');
        }
        route.channel = channel;
      }
      if(vtype === 'const'){
        var constRaw = item.const;
        if(constRaw === '' || constRaw === null || constRaw === undefined){
          throw new Error('Ruta #' + (idx + 1) + ': especifica el valor constante.');
        }
        var constVal = Number(constRaw);
        if(!isFinite(constVal)){
          throw new Error('Ruta #' + (idx + 1) + ': valor constante inválido.');
        }
        route.const = constVal;
      }
      routes.push(route);
    });
    return routes;
  }

  function collectGenericPayload(service){
    if(!genericConfigTextarea) return {};
    var raw = genericConfigTextarea.value || '';
    var trimmed = raw.trim();
    if(!trimmed){
      return {};
    }
    try{
      var parsed = JSON.parse(trimmed);
      if(parsed && typeof parsed === 'object'){
        return parsed;
      }
      throw new Error('Debe ser un objeto JSON.');
    } catch (err){
      throw new Error('JSON inválido para el servicio ' + service + ': ' + err.message);
    }
  }

  function openConfigEditor(service, config){
    if(!configEditorOverlay) return;
    resetConfigEditor();
    var svc = (service || '').trim() || 'MIDI';
    configEditorState.editing = !!config;
    configEditorState.service = svc;
    configEditorState.originalName = config ? config.name : null;

    populateServiceSuggestions(svc, configEditorState.editing);
    toggleConfigSections(svc);

    if(configEditorTitle){
      configEditorTitle.textContent = config ? 'Editar configuración' : 'Nueva configuración';
    }
    if(configServiceInput){
      configServiceInput.value = svc;
      configServiceInput.readOnly = configEditorState.editing;
    }
    if(configNameInput){
      configNameInput.value = config ? (config.name || '') : '';
      configNameInput.readOnly = configEditorState.editing;
      configNameInput.disabled = configEditorState.editing;
    }
    if(configSourceInput){
      configSourceInput.value = (config && config.updated_by) ? String(config.updated_by) : 'server-ui';
    }

    if(isMidiService(svc)){
      var data = (config && config.data) ? config.data : {};
      midiInputField.value = data.midi_input || '';
      midiOscPortField.value = data.osc_port != null ? data.osc_port : 1024;
      midiOscIpsField.value = Array.isArray(data.osc_ips) ? data.osc_ips.join(', ') : '127.0.0.1';
      midiUiPortField.value = data.ui_port != null ? data.ui_port : 9001;
      renderMidiRoutes(mapConfigRoutesToEditor(data.routes || []));
    } else {
      var payload = (config && config.data) ? config.data : {};
      genericConfigTextarea.value = JSON.stringify(payload, null, 2);
      renderMidiRoutes([]);
    }

    configEditorOverlay.classList.add('active');
    if(!config){
      (configNameInput || configServiceInput).focus();
    } else {
      configEditorSaveBtn && configEditorSaveBtn.focus();
    }
  }

  function closeConfigEditor(){
    if(!configEditorOverlay) return;
    configEditorOverlay.classList.remove('active');
    resetConfigEditor();
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
    if(config === NO_CONFIG_OPTION){
      config = '';
    }
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
      loadServiceConfigs(true);
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

  if(configEditorCloseBtn){
    configEditorCloseBtn.addEventListener('click', function(){
      closeConfigEditor();
    });
  }

  if(configEditorCancelBtn){
    configEditorCancelBtn.addEventListener('click', function(){
      closeConfigEditor();
    });
  }

  if(configEditorOverlay){
    configEditorOverlay.addEventListener('click', function(ev){
      if(ev.target === configEditorOverlay){
        closeConfigEditor();
      }
    });
  }

  if(configServiceInput){
    configServiceInput.addEventListener('input', function(){
      toggleConfigSections(configServiceInput.value);
    });
  }

  if(addMidiRouteBtn){
    addMidiRouteBtn.addEventListener('click', function(){
      try{
        var snapshot = readRouteRows(false);
        snapshot.push({ type: 'note', number: '60', channel: '', osc: '/ruta', vtype: 'float', const: '' });
        renderMidiRoutes(snapshot);
      } catch (err){
        alert(err.message || err);
      }
    });
  }

  if(configEditorForm){
    configEditorForm.addEventListener('submit', function(ev){
      ev.preventDefault();
      var service = (configServiceInput ? configServiceInput.value : '').trim();
      if(!service){
        alert('Selecciona el servicio al que pertenece la configuración.');
        return;
      }
      var name = (configNameInput ? configNameInput.value : '').trim();
      if(!name){
        alert('Introduce un nombre para la configuración.');
        return;
      }
      var overwrite = !!configEditorState.editing;
      var serialSource = configSourceInput ? (configSourceInput.value || 'server-ui') : 'server-ui';
      var data;
      try{
        if(isMidiService(service)){
          var routes = collectMidiRoutes();
          var oscPort = parseInt(midiOscPortField.value, 10);
          if(isNaN(oscPort) || oscPort < 1 || oscPort > 65535){
            throw new Error('Puerto OSC inválido (1-65535).');
          }
          var uiPort = parseInt(midiUiPortField.value, 10);
          if(isNaN(uiPort) || uiPort < 1 || uiPort > 65535){
            throw new Error('Puerto WebUI inválido (1-65535).');
          }
          var ips = (midiOscIpsField.value || '127.0.0.1').split(',').map(function(ip){ return ip.trim(); }).filter(Boolean);
          if(!ips.length){
            ips = ['127.0.0.1'];
          }
          data = {
            midi_input: midiInputField.value || '',
            osc_port: oscPort,
            osc_ips: ips,
            ui_port: uiPort,
            routes: routes,
            config_name: name
          };
        } else {
          data = collectGenericPayload(service);
          data.config_name = name;
        }
      } catch (err){
        alert(err.message || err);
        return;
      }

      var payload = {
        name: name,
        data: data,
        overwrite: overwrite,
        serial: serialSource || 'server-ui'
      };

      fetch('/api/configs/' + encodeURIComponent(service), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      }).then(function(res){
        if(!res.ok){
          return res.json().catch(function(){ return {}; }).then(function(detail){
            var message = (detail && detail.error) || detail.detail || ('HTTP ' + res.status);
            throw new Error(message);
          });
        }
        return ensureConfigs(service, true).then(function(){
          renderServicesView();
          loadDevices();
          closeConfigEditor();
        });
      }).catch(function(err){
        alert('No se pudo guardar la configuración: ' + (err.message || err));
      });
    });
  }

  document.addEventListener('keydown', function(evt){
    if(evt.key === 'Escape' && configEditorOverlay && configEditorOverlay.classList.contains('active')){
      closeConfigEditor();
    }
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

  function loadServiceConfigs(force){
    var services = gatherKnownServices();
    if(!services.length){
      services = ['MIDI'];
    }
    Promise.all(services.map(function(service){
      return ensureConfigs(service, force);
    }))
      .then(function(){
        renderServicesView();
      })
      .catch(console.error);
  }

  function loadClients(){
    fetchClients().then(renderClientsView).catch(console.error);
  }

  setInterval(loadDevices, 4000);
  setInterval(function(){ loadServiceConfigs(false); }, 15000);
  setInterval(loadClients, 15000);
  loadDevices();
  loadServiceConfigs(true);
  loadClients();
})();
