(function () {
    /**
     * Helper to run code when DOM is ready
     */
    function onReady(fn) {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', fn);
        } else {
            fn();
        }
    }

    /**
     * Formatting helpers
     */
    function formatValue(v) {
        if (typeof v === 'number') {
            if (Math.abs(v) >= 1000) return v.toFixed(0);
            return Math.round(v * 1000) / 1000;
        }
        return String(v);
    }

    /**
     * UI Update helpers
     */
    function applyValueByPath(path, value) {
        if (!path) return;
        document.querySelectorAll('[data-osc="' + path + '"]').forEach(function (el) {
            el.textContent = formatValue(value);
        });
    }

    function applyValueByRoute(idx, value) {
        if (idx === undefined || idx === null) return 0;
        var id = String(idx);
        var nodes = document.querySelectorAll('[data-route="' + id + '"]');
        nodes.forEach(function (el) { el.textContent = formatValue(value); });
        return nodes.length;
    }

    /**
     * HOME PAGE LOGIC
     */
    /**
     * Shared Logger
     */
    function logToConsole(msg, type) {
        var cb = document.getElementById('consoleBody');
        if (!cb) return;
        var div = document.createElement('div');
        div.className = 'log-entry' + (type ? ' log-' + type : '');
        div.textContent = '[' + new Date().toLocaleTimeString() + '] ' + msg;
        cb.appendChild(div);
        cb.scrollTop = cb.scrollHeight;
    }
    // Expose globally for inline scripts if needed
    window.logToConsole = logToConsole;

    /**
     * WebSocket Setup
     */
    function setupWS() {
        var proto = (location.protocol === 'https:') ? 'wss' : 'ws';
        var ws = new WebSocket(proto + '://' + location.host + '/ws');
        ws.onmessage = function (ev) {
            try {
                var data = JSON.parse(ev.data);

                // Manejo de logs de Ping
                if (data.type === 'ping_log' && data.data) {
                    var res = data.data;
                    var consoleBody = document.getElementById('consoleBody');

                    // Asegurar que el overlay sea visible si llega un log importante?
                    // No forzamos display aquí para no ser intrusivos, 
                    // pero el botón de ping ya lo abre.

                    if (res.summary) {
                        if (res.success > 0) {
                            logToConsole('Resumen: ' + res.success + '/' + res.total + ' IPs alcanzadas.', 'success');
                        } else {
                            logToConsole('Resumen: Fallo total. Ninguna IP respondió.', 'error');
                        }
                        var pingBtn = document.getElementById('pingBtn');
                        if (pingBtn) {
                            pingBtn.disabled = false;
                            pingBtn.textContent = 'Ping OSC (IGMP)';
                        }
                    } else if (res.error) {
                        logToConsole('ERROR: ' + res.error, 'error');
                        var pingBtn = document.getElementById('pingBtn');
                        if (pingBtn) {
                            pingBtn.disabled = false;
                            pingBtn.textContent = 'Ping OSC (IGMP)';
                        }
                    } else {
                        // Resultado individual
                        if (res.type === 'broadcast') {
                            logToConsole('BROADCAST: ' + res.ip + ' - ' + res.info, 'info');
                        } else if (res.ok) {
                            var prefix = res.discovered ? 'DESCUBIERTO: ' : 'EXITO: ';
                            logToConsole(prefix + res.ip + ' respondió correctamente.', 'success');
                        } else {
                            var prefix = res.discovered ? 'DESCUBIERTO (Fallo): ' : 'ERROR: ';
                            logToConsole(prefix + res.ip + ' falló. ' + (res.error || ''), 'error');
                        }
                    }
                    return;
                }

                var batch = data.batch || [data];
                batch.forEach(function (msg) {
                    if (msg && (msg.route_idx !== undefined || msg.path !== undefined)) {
                        var routeIdx = msg.route_idx ?? msg.routeIndex ?? msg.idx;
                        var value = msg.value;
                        var path = msg.path;
                        var matched = applyValueByRoute(routeIdx, value);
                        if (!matched) {
                            applyValueByPath(path, value);
                        }
                    }
                });
            } catch (e) { }
        };
        ws.onclose = function () { setTimeout(setupWS, 1000); };
    }

    /**
     * HOME PAGE LOGIC
     */
    function initHome() {
        fetch('/state').then(function (r) { return r.json(); }).then(function (st) {
            Object.entries(st || {}).forEach(function (entry) {
                var key = entry[0];
                var obj = entry[1];
                if (obj && typeof obj === 'object' && 'path' in obj) {
                    var matched = applyValueByRoute(key, obj.value);
                    if (!matched) { applyValueByPath(obj.path, obj.value); }
                } else if (obj && typeof obj === 'object' && 'value' in obj) {
                    applyValueByPath(key, obj.value);
                }
            });
        }).catch(function () { });
    }

    /**
     * CONFIGURATION PAGE LOGIC
     */
    function initConfig() {
        // Ping OSC button
        var pingBtn = document.getElementById('pingBtn');
        var consoleOverlay = document.getElementById('consoleOverlay');
        var consoleBody = document.getElementById('consoleBody');
        var consoleClose = document.getElementById('consoleClose');

        // Exponer logToConsole globalmente para que el WS pueda usarlo (chapuza rápida)
        // YA NO ES NECESARIO: logToConsole es compartido arriba.

        if (consoleClose) {
            consoleClose.addEventListener('click', function () {
                consoleOverlay.style.display = 'none';
            });
        }

        if (consoleClose) {
            consoleClose.addEventListener('click', function () {
                consoleOverlay.style.display = 'none';
            });
        }

        if (pingBtn && consoleOverlay && consoleBody) {
            pingBtn.addEventListener('click', function () {
                consoleOverlay.style.display = 'flex';
                consoleBody.innerHTML = '';
                window.logToConsole('Iniciando Ping OSC (IGMP)...', 'info');

                pingBtn.disabled = true;
                pingBtn.textContent = 'Enviando…';

                fetch('/ping_osc', { method: 'POST' })
                    .then(function (res) { return res.json(); })
                    .then(function (data) {
                        if (data.ok) {
                            window.logToConsole('Solicitud enviada. Esperando resultados...', 'info');
                        } else {
                            window.logToConsole('Error al iniciar ping: ' + (data.err || 'Desconocido'), 'error');
                            pingBtn.disabled = false;
                            pingBtn.textContent = 'Ping OSC (IGMP)';
                        }
                    })
                    .catch(function (err) {
                        window.logToConsole('Error de red: ' + err.message, 'error');
                        pingBtn.disabled = false;
                        pingBtn.textContent = 'Ping OSC (IGMP)';
                    });
            });
        }

        // Restart button
        var restartBtn = document.getElementById('reiniciarBtn');
        if (restartBtn) {
            restartBtn.addEventListener('click', function () {
                if (confirm('Se reiniciará el servicio completo (Parada -> Red -> Inicio). Esto tomará unos segundos.\n¿Desea continuar?')) {
                    // Crear un form oculto para hacer POST y navegar
                    var form = document.createElement('form');
                    form.method = 'POST';
                    form.action = '/restart_service';
                    document.body.appendChild(form);
                    form.submit();
                    // VLAN Logic removed (always visible)
                }
            });
        }
    }

    /**
     * Actividad MIDI global (en el header)
     */
    function initGlobalActivity() {
        var el = document.getElementById('midiActivity');
        var text = document.getElementById('midiActivityText');
        if (!el || !text) return;

        setInterval(function () {
            fetch('/learn_state')
                .then(function (res) { return res.json(); })
                .then(function (data) {
                    if (data.last_event && data.last_event.ts) {
                        el.style.display = 'inline-flex';
                        var ev = data.last_event;
                        var label = ev.type === 'control_change' ? 'CC ' + ev.cc : 'Nota ' + ev.note;
                        text.textContent = label + ' (Ch ' + ev.channel + ')';

                        // Efecto de parpadeo
                        el.style.borderColor = 'var(--accent)';
                        setTimeout(function () { el.style.borderColor = 'var(--line)'; }, 100);
                    }
                }).catch(function () { });
        }, 1000);
    }

    /**
     * Formulario unificado (Añadir/Editar) con Learn integrado
     */
    function initUnifiedForm() {
        var vtypeSel = document.getElementById('vtypeInput');
        var constRow = document.getElementById('constRow');
        var btnToggle = document.getElementById('btnLearnToggle');
        var btnClose = document.getElementById('btnLearnClose');
        var previewArea = document.getElementById('learnPreviewArea');
        var btnApply = document.getElementById('btnApplyLearn');

        var summaryKind = document.getElementById('summaryKind');
        var summaryCandidate = document.getElementById('summaryCandidate');

        var isLearning = false;
        var pollInterval = null;

        if (vtypeSel && constRow) {
            vtypeSel.addEventListener('change', function () {
                constRow.style.display = (vtypeSel.value === 'const') ? 'block' : 'none';
            });
            constRow.style.display = (vtypeSel.value === 'const') ? 'block' : 'none';
        }

        if (!btnToggle) return;

        function stopLearning() {
            isLearning = false;
            btnToggle.textContent = '✨ Learn';
            btnToggle.classList.remove('primary');
            if (previewArea) previewArea.style.display = 'none';
            if (pollInterval) clearInterval(pollInterval);
            pollInterval = null;
        }

        btnToggle.addEventListener('click', function () {
            if (isLearning) {
                stopLearning();
            } else {
                isLearning = true;
                btnToggle.textContent = 'Detener';
                btnToggle.classList.add('primary');
                if (previewArea) previewArea.style.display = 'block';
                var fd = new FormData(document.getElementById('manualForm'));
                fetch('/arm_learn', { method: 'POST', body: fd });
                startPolling();
            }
        });

        if (btnClose) {
            btnClose.addEventListener('click', stopLearning);
        }

        function startPolling() {
            if (pollInterval) clearInterval(pollInterval);
            pollInterval = setInterval(function () {
                fetch('/learn_state')
                    .then(function (res) { return res.json(); })
                    .then(function (data) {
                        // Prioridad al candidato (evento capturado tras armar)
                        // o al último evento global si está armado
                        var ev = data.candidate || data.last_event;

                        if (ev && ev.type) {
                            var isCC = (ev.type === 'control_change' || ev.type === 'cc');
                            var label = isCC ? 'CC ' + (ev.cc || ev.control) : 'Nota ' + (ev.note);
                            if (ev.channel !== undefined) label += ' (Canal ' + ev.channel + ')';

                            if (summaryKind) summaryKind.textContent = (isCC ? 'CC' : 'Nota');
                            if (summaryCandidate) summaryCandidate.textContent = label;

                            if (btnApply) {
                                btnApply.disabled = false;
                                btnApply.onclick = function () {
                                    var rtype = isCC ? 'cc' : 'note';
                                    var num = isCC ? (ev.cc || ev.control) : ev.note;

                                    var rtypeEl = document.getElementById('rtypeInput');
                                    var numEl = document.getElementById('numInput');
                                    var chanEl = document.getElementById('channelInput');

                                    if (rtypeEl) rtypeEl.value = rtype;
                                    if (numEl) numEl.value = num;
                                    if (chanEl && ev.channel !== undefined) chanEl.value = ev.channel;

                                    btnApply.textContent = '¡Aplicado!';
                                    setTimeout(function () { btnApply.textContent = 'Aplicar valores'; }, 1000);
                                };
                            }
                        }
                    });
            }, 500);
        }
    }

    /**
     * RESTART PAGE LOGIC
     */
    function initRestart() {
        function retry() {
            fetch('/')
                .then(function (res) {
                    if (res.ok) {
                        window.location.href = '/';
                    } else {
                        setTimeout(retry, 1000);
                    }
                })
                .catch(function () {
                    setTimeout(retry, 1000);
                });
        }
        // Esperar un poco antes del primer intento para dar tiempo al cierre
        setTimeout(retry, 2000);
    }

    /**
     * MAIN INITIALIZATION
     */
    onReady(function () {
        var page = document.body.getAttribute('data-page') || '';

        initGlobalActivity();
        setupWS();

        if (page === 'home') initHome();
        else if (page === 'config') initConfig();
        else if (page === 'add' || page === 'edit') initUnifiedForm();
        else if (page === 'restart') initRestart();
    });

})();
