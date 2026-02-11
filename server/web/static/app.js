(function () {
    const state = {
        view: 'home',
        agents: {},
        configs: {},
        currentService: null,
        editingConfig: null,
        selections: {} // {serial: {service: '', preset: ''}}
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
        if (data) {
            state.agents = data.agents;
            renderAgents();
            renderHome();
        }
    }

    async function loadConfigs() {
        const data = await apiFetch('/api/configs');
        if (data) {
            state.configs = data.configs;
            renderHome();
        }
    }

    // --- Rendering ---
    function renderHome() {
        const html = Object.entries(state.agents).map(([serial, agent]) => {
            const currentSelection = state.selections[serial] || {
                service: agent.active_service || '',
                preset: agent.active_config || ''
            };

            // Available services (could be dynamic from configs later)
            const availableServices = Object.keys(state.configs);
            const availablePresets = state.configs[currentSelection.service] ? Object.keys(state.configs[currentSelection.service]) : [];

            const isDifferent = currentSelection.service !== agent.active_service || currentSelection.preset !== agent.active_config;

            return `
                <div class="agent-card status-${agent.status}" data-serial="${serial}">
                    <div class="status-indicator">
                        <span class="dot"></span>
                        <span>${agent.status}</span>
                    </div>
                    <h3>${agent.host || serial}</h3>
                    <div class="info">
                        <p><strong>IP:</strong> ${agent.ip || '---'}</p>
                        <p><strong>Serial:</strong> <span class="serial-text">${serial}</span></p>
                    </div>
                    <div class="stats">
                        <span>CPU: ${agent.cpu ? agent.cpu.toFixed(1) + '%' : '---'}</span>
                        <span>🌡️ ${agent.temp ? agent.temp.toFixed(1) + '°C' : '---'}</span>
                    </div>
                    <div class="current-state ${agent.active_service ? 'active' : ''}">
                        <p><strong>Activo:</strong> ${agent.active_service ? `${agent.active_service} (${agent.active_config || 'Default'})` : 'Standby'}</p>
                    </div>
                    <div class="controls">
                        <div class="selector-group">
                            <label>Srv:</label>
                            <select onchange="updateSelection('${serial}', 'service', this.value)">
                                <option value="">---</option>
                                ${availableServices.map(s => `<option value="${s}" ${currentSelection.service === s ? 'selected' : ''}>${s}</option>`).join('')}
                            </select>
                        </div>
                        <div class="selector-group">
                            <label>Cfg:</label>
                            <select onchange="updateSelection('${serial}', 'preset', this.value)">
                                <option value="">---</option>
                                ${availablePresets.map(p => `<option value="${p}" ${currentSelection.preset === p ? 'selected' : ''}>${p}</option>`).join('')}
                            </select>
                        </div>
                        <button class="btn btn-primary btn-block ${isDifferent ? '' : 'disabled'}" 
                                onclick="startService('${serial}')" ${isDifferent ? '' : 'disabled'}>
                            🚀 INICIAR
                        </button>
                    </div>
                </div>
            `;
        }).join('');
        agentGrid.innerHTML = html;
    }

    function renderAgents() {
        const html = Object.entries(state.agents).map(([serial, agent]) => `
            <tr>
                <td>${serial}</td>
                <td>${agent.host}</td>
                <td>${agent.ip}</td>
                <td>
                    <span class="status-indicator status-${agent.status}">
                        <span class="dot"></span> ${agent.status}
                    </span>
                </td>
                <td>
                    <button class="btn btn-sm btn-danger" onclick="deleteAgent('${serial}')">Eliminar</button>
                    <button class="btn btn-sm btn-warning" onclick="sendPower('${serial}', 'reboot')">R</button>
                    <button class="btn btn-sm btn-danger" onclick="sendPower('${serial}', 'shutdown')">O</button>
                </td>
            </tr>
        `).join('');
        agentsList.innerHTML = html;
    }

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
                service: agent.active_service || '',
                preset: agent.active_config || ''
            };
        }
        state.selections[serial][key] = value;
        // If service changes, clear preset if not compatible
        if (key === 'service') {
            const firstPreset = state.configs[value] ? Object.keys(state.configs[value])[0] : '';
            state.selections[serial].preset = firstPreset;
        }
        renderHome();
    };

    window.startService = async (serial) => {
        const sel = state.selections[serial];
        if (!sel || !sel.service) return;

        await apiFetch(`/api/agents/${serial}/command`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                action: 'start_service',
                service_id: sel.service,
                config_name: sel.preset
            })
        });

        // Optimistic update or just wait for poll
        renderHome();
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
