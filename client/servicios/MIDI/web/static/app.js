(function(){
  function onReady(fn){
    if (document.readyState === 'loading'){
      document.addEventListener('DOMContentLoaded', fn);
    } else {
      fn();
    }
  }

  function formatValue(v){
    if (typeof v === 'number'){
      if (Math.abs(v) >= 1000) return v.toFixed(0);
      return Math.round(v * 1000) / 1000;
    }
    return String(v);
  }

  function applyValueByPath(path, value){
    if (!path) return;
    document.querySelectorAll('[data-osc="' + path + '"]').forEach(function(el){
      el.textContent = formatValue(value);
    });
  }

  function applyValueByRoute(idx, value){
    if (idx === undefined || idx === null) return 0;
    var id = String(idx);
    var nodes = document.querySelectorAll('[data-route="' + id + '"]');
    nodes.forEach(function(el){ el.textContent = formatValue(value); });
    return nodes.length;
  }

  function applyStatePayload(payload){
    if (!payload) return;
    var routeIdx = payload.route_idx ?? payload.routeIndex ?? payload.idx;
    var value = payload.value;
    var path = payload.path;
    var matched = applyValueByRoute(routeIdx, value);
    if (!matched){
      applyValueByPath(path, value);
    }
  }

  function initHome(){
    fetch('/state').then(function(r){ return r.json(); }).then(function(st){
      Object.entries(st || {}).forEach(function(entry){
        var key = entry[0];
        var obj = entry[1];
        if (obj && typeof obj === 'object' && 'path' in obj){
          var matched = applyValueByRoute(key, obj.value);
          if (!matched){ applyValueByPath(obj.path, obj.value); }
        } else if (obj && typeof obj === 'object' && 'value' in obj){
          applyValueByPath(key, obj.value);
        }
      });
    }).catch(function(){});

    (function setupWS(){
      var proto = (location.protocol === 'https:') ? 'wss' : 'ws';
      var ws = new WebSocket(proto + '://' + location.host + '/ws');
      ws.onmessage = function(ev){
        try {
          var msg = JSON.parse(ev.data);
          if (msg && (msg.route_idx !== undefined || msg.path !== undefined)){
            applyStatePayload(msg);
          }
        } catch(e){}
      };
      ws.onclose = function(){ setTimeout(setupWS, 1000); };
    })();
  }

  function initSettings(){
    var form = document.getElementById('settingsForm');
    if (form){
      form.addEventListener('submit', function(ev){
        if (!confirm('Se reiniciará el servicio, ¿desea continuar?')){
          ev.preventDefault();
        }
      });
    }

    var pingBtn = document.getElementById('pingBtn');
    var pingStatus = document.getElementById('pingStatus');
    if (pingBtn && pingStatus){
      pingBtn.addEventListener('click', function(){
        var original = pingBtn.textContent;
        pingBtn.disabled = true;
        pingBtn.textContent = 'Enviando…';
        fetch('/ping_osc', {method: 'POST'}).then(function(res){
          pingStatus.style.display = 'block';
          pingStatus.textContent = res.ok ? 'Ping enviado a los targets OSC.' : 'Error enviando ping OSC.';
        }).catch(function(){
          pingStatus.style.display = 'block';
          pingStatus.textContent = 'Error enviando ping OSC.';
        }).finally(function(){
          setTimeout(function(){ pingStatus.style.display = 'none'; }, 3500);
          pingBtn.disabled = false;
          pingBtn.textContent = original;
        });
      });
    }
  }

  function initManual(){
    var vSel = document.getElementById('manualVType');
    var constRow = document.getElementById('manualConstRow');
    if (!vSel || !constRow) return;
    var toggle = function(){ constRow.style.display = (vSel.value === 'const') ? 'block' : 'none'; };
    vSel.addEventListener('change', toggle);
    toggle();
  }

  function initLearn(){
    var vtypeSel = document.getElementById('vtypeInput');
    var constRow = document.getElementById('constRow');
    var form = document.getElementById('learnForm');
    var oscInput = document.getElementById('oscInput');
    var constInput = document.getElementById('constInput');
    var summaryOsc = document.getElementById('summaryOsc');
    var summaryType = document.getElementById('summaryType');
    var summaryKind = document.getElementById('summaryKind');
    var summaryCandidate = document.getElementById('summaryCandidate');
    var summaryDetails = document.getElementById('summaryDetails');
    var acceptBtn = document.getElementById('learnAccept');
    var cancelBtn = document.getElementById('learnCancel');
    var resultBox = document.getElementById('learnResult');
    var resultSummary = document.getElementById('resultSummary');
    var messageBox = document.getElementById('learnMessage');

    if (!form || !vtypeSel || !constRow || !oscInput || !constInput || !acceptBtn || !cancelBtn) return;

    var TYPE_LABELS = {
      "float": "float (0..1)",
      "int": "int (0..127)",
      "bool": "bool",
      "const": "const"
    };
    var defaultAcceptLabel = acceptBtn.textContent;
    var isSaving = false;
    var lastCandidateKey = null;

    function showMessage(text){
      if (!messageBox) return;
      if (text){
        messageBox.textContent = text;
        messageBox.style.display = 'block';
      } else {
        messageBox.textContent = '';
        messageBox.style.display = 'none';
      }
    }

    function setAcceptLoading(loading){
      if (loading){
        acceptBtn.textContent = 'Guardando…';
        acceptBtn.disabled = true;
      } else {
        acceptBtn.textContent = defaultAcceptLabel;
      }
    }

    function toggleConst(){
      var show = vtypeSel.value === 'const';
      constRow.style.display = show ? 'block' : 'none';
    }

    function updateSummary(){
      summaryOsc.textContent = oscInput.value || '/learn';
      var typeLabel = TYPE_LABELS[vtypeSel.value] || vtypeSel.value;
      summaryType.textContent = typeLabel;
      if (vtypeSel.value === 'const' && constInput.value !== ''){
        summaryType.textContent = typeLabel + ' = ' + constInput.value;
      }
      showMessage('');
    }

    async function pushConfig(){
      try {
        var fd = new FormData(form);
        if (vtypeSel.value !== 'const'){
          fd.delete('const');
        }
        await fetch('/arm_learn', {method: 'POST', body: fd});
      } catch (e) {
        console.warn('arm_learn error', e);
      }
    }

    function formatCandidate(candidate){
      if (!candidate) return '';
      if (candidate.type === 'note'){
        var noteVal = (typeof candidate.note === 'number') ? candidate.note : '?';
        var msgType = candidate.message_type === 'note_off' ? 'nota off' : 'nota';
        return msgType + ' ' + noteVal;
      }
      var ccVal = (typeof candidate.cc === 'number') ? candidate.cc : '?';
      var ch = candidate.channel;
      var chPart = (typeof ch === 'number') ? ' canal ' + ch : '';
      return 'cc ' + ccVal + chPart;
    }

    function formatCandidateDetails(candidate){
      if (!candidate) return '';
      var bits = [];
      if (candidate.type === 'note'){
        if (typeof candidate.channel === 'number') bits.push('canal ' + candidate.channel);
        if (typeof candidate.velocity === 'number') bits.push('velocidad ' + candidate.velocity);
      } else if (candidate.type === 'cc'){
        if (typeof candidate.channel === 'number') bits.push('canal ' + candidate.channel);
        if (typeof candidate.value === 'number') bits.push('valor ' + candidate.value);
      }
      return bits.join(' · ');
    }

    function formatResult(result){
      if (!result) return '';
      var route = result.route || {};
      var osc = route.osc || '';
      var vtype = route.vtype || '';
      var midiPart = result.label || '';
      if (!midiPart){
        if (route.type === 'note'){
          midiPart = 'nota ' + ((typeof route.note === 'number') ? route.note : '?');
        } else if (route.type === 'cc'){
          midiPart = 'cc ' + ((typeof route.cc === 'number') ? route.cc : '?');
          if (typeof route.channel === 'number') midiPart += ' canal ' + route.channel;
        } else {
          midiPart = 'MIDI';
        }
      }
      return midiPart + ' → ' + osc + ' (' + vtype + ')';
    }

    var initialised = false;

    async function refreshLearnUI(){
      try {
        var res = await fetch('/learn_state');
        var st = await res.json();
        if (!initialised){
          if (st.osc && !oscInput.value){ oscInput.value = st.osc; }
          if (st.vtype){ vtypeSel.value = st.vtype; }
          if (st.vtype === 'const' && typeof st.const !== 'undefined' && !constInput.value){ constInput.value = st.const; }
          toggleConst();
          updateSummary();
          initialised = true;
        }
        summaryOsc.textContent = st.osc || '/learn';
        var typeLabel = TYPE_LABELS[st.vtype] || st.vtype;
        summaryType.textContent = typeLabel;
        if (st.vtype === 'const' && typeof st.const !== 'undefined'){
          summaryType.textContent = typeLabel + ' = ' + st.const;
          if (!constInput.matches(':focus')){
            constInput.value = st.const;
          }
        }

        if (st.candidate){
          summaryCandidate.textContent = formatCandidate(st.candidate);
          summaryKind.textContent = (st.candidate.type === 'note') ? 'nota' : 'cc';
          var details = formatCandidateDetails(st.candidate);
          if (details){
            summaryDetails.textContent = details;
            summaryDetails.style.display = 'block';
          } else {
            summaryDetails.textContent = '';
            summaryDetails.style.display = 'none';
          }
          var candidateKey = JSON.stringify([st.candidate.type, st.candidate.note, st.candidate.cc, st.candidate.channel, st.candidate.message_type]);
          if (candidateKey !== lastCandidateKey){ showMessage(''); }
          lastCandidateKey = candidateKey;
          if (!isSaving){
            acceptBtn.disabled = false;
          }
        } else {
          summaryCandidate.textContent = 'Esperando evento MIDI…';
          summaryKind.textContent = 'Esperando…';
          summaryDetails.textContent = '';
          summaryDetails.style.display = 'none';
          acceptBtn.disabled = true;
          lastCandidateKey = null;
        }

        if (st.result && st.result.route){
          resultBox.style.display = 'block';
          resultSummary.textContent = formatResult(st.result);
        } else {
          resultBox.style.display = 'none';
          resultSummary.textContent = '';
        }
      } catch (e) {
        console.warn('refreshLearnUI error', e);
      }
    }

    async function commitCandidate(confirm){
      if (confirm === undefined) confirm = false;
      if (isSaving || acceptBtn.disabled) return;
      showMessage('');
      isSaving = true;
      setAcceptLoading(true);
      try {
        var fd = new FormData(form);
        if (vtypeSel.value !== 'const'){
          fd.delete('const');
        }
        if (confirm){ fd.append('confirm', '1'); }
        var res = await fetch('/commit_learn', {method: 'POST', body: fd});
        var data = null;
        try { data = await res.json(); } catch (_) { data = null; }
        if (res.ok && data && data.ok){
          window.location.href = data.redirect || '/';
          return;
        }
        if (res.status === 409 && data && data.reason === 'duplicate'){
          if (window.confirm('La ruta OSC "' + data.osc + '" ya existe. ¿Deseas continuar?')){
            isSaving = false;
            setAcceptLoading(false);
            acceptBtn.disabled = false;
            return commitCandidate(true);
          }
          showMessage('Ruta no guardada. Ajusta la ruta OSC si no quieres sobrescribir.');
        } else if (data && data.reason === 'no_candidate'){
          showMessage('Todavía no hay mensaje MIDI capturado.');
        } else {
          showMessage('No se pudo guardar la ruta. Vuelve a intentarlo.');
        }
      } catch (e) {
        showMessage('Error de red al guardar la ruta.');
      } finally {
        if (isSaving){
          isSaving = false;
          setAcceptLoading(false);
        }
        refreshLearnUI();
      }
    }

    form.addEventListener('submit', function(ev){ ev.preventDefault(); });
    acceptBtn.addEventListener('click', function(){ commitCandidate(false); });

    toggleConst();
    updateSummary();
    pushConfig();
    refreshLearnUI();
    setInterval(refreshLearnUI, 250);

    oscInput.addEventListener('input', updateSummary);
    oscInput.addEventListener('change', function(){ pushConfig(); });
    vtypeSel.addEventListener('change', function(){ toggleConst(); updateSummary(); pushConfig(); });
    constInput.addEventListener('input', updateSummary);
    constInput.addEventListener('change', function(){ pushConfig(); });

    cancelBtn.addEventListener('click', function(){
      window.location.href = '/cancel_learn';
    });
  }

  onReady(function(){
    var page = document.body.dataset.page || '';
    initCommon();
    if (page === 'home'){ initHome(); }
    else if (page === 'settings'){ initSettings(); }
    else if (page === 'add'){ initManual(); }
    else if (page === 'add/learn'){ initLearn(); }
  });

  function initCommon(){ /* placeholder for shared behaviours */ }
})();
