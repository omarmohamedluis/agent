(function () {
    const state = {
        view: 'home',
        agents: {},
        configs: {},
        currentService: null,
        editingConfig: null,
        selections: {}, // {serial: {service: '', preset: ''}}
        lastEventId: 0,
        pillCount: 0
    };

    // --- DOM Elements ---
    const agentGrid = document.getElementById('agent-grid');
    const agentsList = document.getElementById('agents-list');
    const serviceSelector = document.getElementById('service-selector');
    const configList = document.getElementById('config-list');
    const editorPane = document.getElementById('editor-pane');
    const configJson = document.getElementById('config-json');
    const serviceTitle = document.getElementById('current-service-title');

    // --- Networking ---
    async function apiFetch(url, options = {}) {
        try {
            const res = await fetch(url, options);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (err) {
            console.error(`API Error (${url}):`, err);
            return null;
        }
    }

    async function loadAgents() {
        const data = await apiFetch('/api/agents');
        if (data && data.agents) {
            // 1. Remove agents from local state that aren't in the remote data
            // (Unless they are optimistic/launching)
            for (const serial in state.agents) {
                if (!data.agents[serial] && !state.agents[serial].is_optimistic) {
                    delete state.agents[serial];
                }
            }

            // 2. Add/Update remaining agents
            for (const serial in data.agents) {
                const remote = data.agents[serial];
                const local = state.agents[serial];

                if (local && local.is_optimistic && remote.status !== 'loading' && remote.status !== 'stalled') {
                    continue;
                }
                remote.is_optimistic = false;
                state.agents[serial] = remote;
            }

            // Handle Events (Pills)
            if (data.events) {
                data.events.forEach(event => {
                    if (event.id > state.lastEventId) {
                        spawnPill(event);
                        state.lastEventId = event.id;
                    }
                });
            }

            renderAgents();
            renderHome();
        }
    }

    const spawnPill = (event) => {
        // User requested to remove pop-up notifications.
        // Logic removed but function kept to avoid breaking calls.
        if (event.type === 'discovery') {
            // Still refresh configs on discovery silently
            loadConfigs();
        }
    };

    const poll = loadAgents; // Alias for compatibility with command handlers

    async function loadConfigs() {
        const data = await apiFetch('/api/configs');
        if (data) {
            state.configs = data.configs;
            renderHome();
        }
    }

    // --- Rendering ---
    function renderHome() {
        const currentSerials = Object.keys(state.agents);

        // 1. Remove cards for agents that are no longer present
        Array.from(agentGrid.querySelectorAll('.agent-card')).forEach(card => {
            const serial = card.dataset.serial;
            if (!currentSerials.includes(serial)) {
                card.remove();
            }
        });

        // 2. Add or Update cards
        currentSerials.forEach(serial => {
            const agent = state.agents[serial];
            let card = agentGrid.querySelector(`.agent-card[data-serial="${serial}"]`);

            const currentSelection = state.selections[serial] || {
                service: agent.active_service || 'Standby',
                preset: agent.active_config || ''
            };

            const availableServices = ['Standby', ...Object.keys(state.configs)];
            const availablePresets = state.configs[currentSelection.service] ? Object.keys(state.configs[currentSelection.service]) : [];
            const isDifferent = (currentSelection.service === 'Standby' ? !!agent.active_service : currentSelection.service !== agent.active_service) || (currentSelection.service !== 'Standby' && currentSelection.preset !== agent.active_config);
            const hasActive = !!agent.active_service;
            const isLocked = agent.status === 'loading' || agent.status === 'stalled';
            const statusText = agent.status === 'loading' ? (agent.busy_message || 'loading...') : agent.status;

            if (!card) {
                // Initial creation
                const cardHtml = `
                    <div class="agent-card status-${agent.status} ${isLocked ? 'is-locked' : ''}" data-serial="${serial}">
                        <div class="status-indicator">
                            <span class="dot"></span>
                            <span class="status-label">${statusText}</span>
                        </div>
                        <h3 class="agent-title">#${agent.id || '?'} - ${agent.host || serial}</h3>
                        <div class="info">
                            <p><strong>IP:</strong> <span class="ip-text">${agent.ip || '---'}</span></p>
                            <p><strong>Serial:</strong> <span class="serial-text">${serial}</span></p>
                        </div>
                        <div class="stats">
                            <span class="cpu-text">CPU: ${agent.cpu ? agent.cpu.toFixed(1) + '%' : '---'}</span>
                            <span class="temp-text">🌡️ ${agent.temp ? agent.temp.toFixed(1) + '°C' : '---'}</span>
                        </div>
                        <div class="current-state ${agent.active_service ? 'active' : ''}">
                            <p class="state-text"><strong>Activo:</strong> ${agent.active_service ? `${agent.active_service} (${agent.active_config || 'Default'})` : 'Standby'}</p>
                        </div>
                        <div class="controls">
                            <div class="configure-button-container">
                                ${(agent.status === 'online' && agent.active_service && agent.active_service_port) ? `
                                    <button class="btn btn-primary" onclick="configureAgent('${serial}', ${agent.active_service_port})" style="margin-bottom: 0.8rem; width: 100%;">⚙️ CONFIGURAR</button>
                                ` : ''}
                            </div>
                            <div class="selector-group">
                                <label>Srv:</label>
                                <select class="srv-select" onchange="updateSelection('${serial}', 'service', this.value)" ${isLocked ? 'disabled' : ''}>
                                    <option value="">---</option>
                                    ${availableServices.map(s => `<option value="${s}" ${currentSelection.service === s ? 'selected' : ''}>${s}</option>`).join('')}
                                </select>
                            </div>
                            <div class="selector-group">
                                <label>Cfg:</label>
                                <select class="cfg-select" onchange="updateSelection('${serial}', 'preset', this.value)" ${isLocked ? 'disabled' : ''}>
                                    <option value="">---</option>
                                    ${availablePresets.map(p => `<option value="${p}" ${currentSelection.preset === p ? 'selected' : ''}>${p}</option>`).join('')}
                                </select>
                            </div>
                             <div class="button-group-row">
                                <div class="start-button-container">
                                    ${(!hasActive || isDifferent) ? `
                                        <button class="btn btn-primary btn-start ${isLocked ? 'disabled' : ''}" 
                                                onclick="startService('${serial}')" ${isLocked ? 'disabled' : ''}>
                                            ${agent.status === 'offline' ? '📝 PROGRAMAR ARRANQUE' : (hasActive && isDifferent ? '🔄 ACTUALIZAR' : '🚀 INICIAR')}
                                        </button>
                                    ` : ''}
                                </div>
                                <div class="stop-button-container">
                                    ${hasActive && agent.status !== 'offline' ? `
                                        <button class="btn btn-danger btn-stop ${isLocked ? 'disabled' : ''}" 
                                                onclick="stopService('${serial}')" ${isLocked ? 'disabled' : ''}>
                                            ⏹️ PARAR
                                        </button>
                                    ` : ''}
                                </div>
                            </div>
                        </div>
                    </div>
                `;
                agentGrid.insertAdjacentHTML('beforeend', cardHtml);
                return;
            }

            // Update existing card
            card.className = `agent-card status-${agent.status} ${isLocked ? 'is-locked' : ''}`;
            card.querySelector('.status-label').textContent = statusText;
            card.querySelector('.agent-title').textContent = `#${agent.id || '?'} - ${agent.host || serial}`;
            card.querySelector('.ip-text').textContent = agent.ip || '---';
            card.querySelector('.cpu-text').textContent = `CPU: ${agent.cpu ? agent.cpu.toFixed(1) + '%' : '---'}`;
            card.querySelector('.temp-text').textContent = `🌡️ ${agent.temp ? agent.temp.toFixed(1) + '°C' : '---'}`;

            const stateTextEl = card.querySelector('.state-text');
            stateTextEl.innerHTML = `<strong>Activo:</strong> ${agent.active_service ? `${agent.active_service} (${agent.active_config || 'Default'})` : 'Standby'}`;
            card.querySelector('.current-state').classList.toggle('active', !!agent.active_service);

            // Update Configure Button for existing cards
            const configContainer = card.querySelector('.configure-button-container');
            if (configContainer) {
                const showButton = agent.status === 'online' && agent.active_service && agent.active_service_port;
                const existingBtn = configContainer.querySelector('button');

                if (showButton) {
                    const btnHtml = `<button class="btn btn-primary" onclick="configureAgent('${serial}', ${agent.active_service_port})" style="margin-bottom: 0.8rem; width: 100%;">⚙️ CONFIGURAR</button>`;
                    if (!existingBtn) {
                        configContainer.innerHTML = btnHtml;
                    } else {
                        // Update existing button if parameters changed
                        // Using setAttribute is safer than innerHTML replacement for existing elements to preserve state if any
                        existingBtn.setAttribute('onclick', `configureAgent('${serial}', ${agent.active_service_port})`);
                    }
                } else if (!showButton && existingBtn) {
                    configContainer.innerHTML = '';
                }
            }

            // Update Selectors (ONLY if not focused and options changed)
            const srvSelect = card.querySelector('.srv-select');
            const cfgSelect = card.querySelector('.cfg-select');

            if (document.activeElement !== srvSelect) {
                const srvHtml = `<option value="">---</option>${availableServices.map(s => `<option value="${s}" ${currentSelection.service === s ? 'selected' : ''}>${s}</option>`).join('')}`;
                if (srvSelect.innerHTML !== srvHtml) srvSelect.innerHTML = srvHtml;
                srvSelect.disabled = isLocked;
            }

            if (document.activeElement !== cfgSelect) {
                const cfgHtml = `<option value="">---</option>${availablePresets.map(p => `<option value="${p}" ${currentSelection.preset === p ? 'selected' : ''}>${p}</option>`).join('')}`;
                if (cfgSelect.innerHTML !== cfgHtml) cfgSelect.innerHTML = cfgHtml;
                cfgSelect.disabled = isLocked;
            }

            // Update Start/Update Button
            const startContainer = card.querySelector('.start-button-container');
            const shouldHaveStart = !hasActive || isDifferent;
            if (shouldHaveStart) {
                const label = agent.status === 'offline' ? '📝 PROGRAMAR ARRANQUE' : (hasActive && isDifferent ? '🔄 ACTUALIZAR' : '🚀 INICIAR');
                if (!startContainer.querySelector('.btn-start')) {
                    startContainer.innerHTML = `
                        <button class="btn btn-primary btn-start ${isLocked ? 'disabled' : ''}" 
                                onclick="startService('${serial}')" ${isLocked ? 'disabled' : ''}>
                            ${label}
                        </button>
                    `;
                } else {
                    const btnStart = startContainer.querySelector('.btn-start');
                    btnStart.textContent = label;
                    btnStart.disabled = isLocked;
                    btnStart.classList.toggle('disabled', isLocked);
                }
            } else {
                startContainer.innerHTML = '';
            }

            const stopContainer = card.querySelector('.stop-button-container');
            const shouldHaveStop = hasActive && agent.status !== 'offline';
            if (shouldHaveStop) {
                if (!stopContainer.querySelector('.btn-stop')) {
                    stopContainer.innerHTML = `
                        <button class="btn btn-danger btn-stop ${isLocked ? 'disabled' : ''}" 
                                onclick="stopService('${serial}')" ${isLocked ? 'disabled' : ''}>
                            ⏹️ PARAR
                        </button>
                    `;
                } else {
                    const btnStop = stopContainer.querySelector('.btn-stop');
                    btnStop.disabled = isLocked;
                    btnStop.classList.toggle('disabled', isLocked);
                }
            } else {
                stopContainer.innerHTML = '';
            }
        });
    }

    function renderAgents() {
        const html = Object.entries(state.agents).map(([serial, agent]) => `
            <tr>
                <td>${agent.id || '--'} <button class="btn-icon" onclick="updateAgentId('${serial}', ${agent.id})">✏️</button></td>
                <td>${serial}</td>
                <td>${agent.host}</td>
                <td>${agent.ip}</td>
                <td>
                    <span class="status-indicator status-${agent.status}">
                        <span class="dot"></span> ${agent.status}
                    </span>
                </td>
                <td>
                    ${agent.status === 'offline' ? `<button class="btn btn-sm btn-danger" onclick="deleteAgent('${serial}')">Eliminar</button>` : ''}
                    <button class="btn btn-sm btn-warning" onclick="sendPower('${serial}', 'reboot')">R</button>
                    <button class="btn btn-sm btn-danger" onclick="sendPower('${serial}', 'shutdown')">O</button>
                </td>
            </tr>
        `).join('');
        agentsList.innerHTML = html;
    }

    window.configureAgent = (serial, port) => {
        const agent = state.agents[serial];
        if (!agent || !agent.ip) return;

        const url = `http://${agent.ip}:${port}`;
        window.open(url, '_blank');
    };

    window.updateAgentId = async (serial, currentId) => {
        const newId = prompt(`Asignar nuevo ID numérico para ${serial}:`, currentId);
        if (newId === null || newId === "" || isNaN(newId)) return;

        await apiFetch(`/api/agents/${serial}/id`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: parseInt(newId) })
        });
        loadAgents();
    };

    window.deleteAgent = async (serial) => {
        if (!confirm(`¿Eliminar definitivamente el agente ${serial} del registro?`)) return;

        // Remove locally first for immediate UI feedback
        delete state.agents[serial];
        renderAgents();
        renderHome();

        await apiFetch(`/api/agents/${serial}`, { method: 'DELETE' });
        loadAgents();
    };

    function switchView(view) {
        state.view = view;
        document.querySelectorAll('.view').forEach(el => el.classList.add('hidden'));
        document.getElementById(`view-${view}`).classList.remove('hidden');
        document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.view === view));
    }

    // --- Interactivity ---
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => switchView(btn.dataset.view));
    });

    serviceSelector.addEventListener('click', (e) => {
        const li = e.target.closest('li');
        if (!li) return;

        document.querySelectorAll('#service-selector li').forEach(el => el.classList.remove('active'));
        li.classList.add('active');

        state.currentService = li.dataset.service;
        serviceTitle.textContent = state.currentService;
        document.getElementById('btn-new-config').classList.remove('hidden');
        renderServiceConfigs();
    });

    function renderServiceConfigs() {
        const configs = state.configs[state.currentService] || {};
        const html = Object.keys(configs).map(name => `
            <li onclick="editConfig('${name}')">
                <span>${name}</span>
                <button class="btn-icon" onclick="event.stopPropagation(); deleteConfig('${name}')">🗑️</button>
            </li>
        `).join('');
        configList.innerHTML = html;
        editorPane.classList.add('hidden');
        configList.classList.remove('hidden');
    }

    window.updateSelection = function (serial, key, value) {
        if (!state.selections[serial]) {
            const agent = state.agents[serial];
            state.selections[serial] = {
                service: agent.active_service || 'Standby',
                preset: agent.active_config || ''
            };
        }
        state.selections[serial][key] = value;
        // If service changes, clear preset if not compatible
        if (key === 'service' && value !== 'Standby') {
            const firstPreset = state.configs[value] ? Object.keys(state.configs[value])[0] : '';
            state.selections[serial].preset = firstPreset;
        } else if (key === 'service' && value === 'Standby') {
            state.selections[serial].preset = '';
        }
        renderHome();
    };

    window.startService = async (serial) => {
        const sel = state.selections[serial];
        if (!sel || !sel.service) return;

        if (sel.service === 'Standby') {
            return stopService(serial);
        }

        // Optimistic update
        if (state.agents[serial]) {
            state.agents[serial].status = 'loading';
            state.agents[serial].busy_message = 'Launching...';
            state.agents[serial].is_optimistic = true; // Mark as local update
            renderHome();
        }

        await apiFetch(`/api/agents/${serial}/command`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                action: 'start_service',
                service_id: sel.service,
                config_name: sel.preset
            })
        });

        // Force immediate poll to sync with server's receipt of command
        setTimeout(poll, 200);
    };

    window.stopService = async (serial) => {
        if (!confirm(`¿Detener servicio en ${serial}?`)) return;

        // Optimistic update
        if (state.agents[serial]) {
            state.agents[serial].status = 'loading';
            state.agents[serial].busy_message = 'Stopping...';
            state.agents[serial].is_optimistic = true; // Mark as local update
            renderHome();
        }

        await apiFetch(`/api/agents/${serial}/command`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                action: 'stop_service',
                service_id: state.agents[serial].active_service
            })
        });

        // Force immediate poll
        setTimeout(poll, 200);
    };

    window.editConfig = function (name) {
        const config = state.configs[state.currentService][name];
        state.editingConfig = name;
        configJson.value = JSON.stringify(config.data, null, 2);
        configList.classList.add('hidden');
        editorPane.classList.remove('hidden');
    };

    window.sendPower = async (serial, action) => {
        if (!confirm(`¿Confirmar ${action} para ${serial}?`)) return;
        await apiFetch(`/api/agents/${serial}/command`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action })
        });
    };

    document.getElementById('btn-restart-all').addEventListener('click', async () => {
        if (!confirm('¿Reiniciar TODOS los agentes?')) return;
        await apiFetch('/api/agents/global-command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'reboot' })
        });
    });

    document.getElementById('btn-shutdown-all').addEventListener('click', async () => {
        if (!confirm('¿Apagar TODOS los agentes?')) return;
        await apiFetch('/api/agents/global-command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'shutdown' })
        });
    });

    // Initialize Sortable
    new Sortable(agentGrid, {
        animation: 150,
        ghostClass: 'blue-background-class'
    });

    // Poll for updates
    setInterval(loadAgents, 3000);
    setInterval(loadConfigs, 10000);

    // Initial Load
    loadAgents();
    loadConfigs();
})();
