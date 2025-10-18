(function () {
  const form = document.getElementById('learnForm');
  if (!form) {
    return;
  }

  const vtypeSel = document.getElementById('vtypeInput');
  const constRow = document.getElementById('constRow');
  const oscInput = document.getElementById('oscInput');
  const constInput = document.getElementById('constInput');
  const summaryOsc = document.getElementById('summaryOsc');
  const summaryType = document.getElementById('summaryType');
  const summaryKind = document.getElementById('summaryKind');
  const summaryCandidate = document.getElementById('summaryCandidate');
  const summaryDetails = document.getElementById('summaryDetails');
  const acceptBtn = document.getElementById('learnAccept');
  const cancelBtn = document.getElementById('learnCancel');
  const resultBox = document.getElementById('learnResult');
  const resultSummary = document.getElementById('resultSummary');
  const messageBox = document.getElementById('learnMessage');

  const TYPE_LABELS = {
    float: 'float (0..1)',
    int: 'int (0..127)',
    bool: 'bool',
    const: 'const'
  };

  const DEFAULT_ACCEPT_LABEL = acceptBtn.textContent;
  let isSaving = false;
  let lastCandidateKey = null;
  let initialised = false;

  function showMessage(text) {
    if (!messageBox) {
      return;
    }
    if (text) {
      messageBox.textContent = text;
      messageBox.style.display = 'block';
    } else {
      messageBox.textContent = '';
      messageBox.style.display = 'none';
    }
  }

  function setAcceptLoading(loading) {
    if (loading) {
      acceptBtn.textContent = 'Guardando…';
      acceptBtn.disabled = true;
    } else {
      acceptBtn.textContent = DEFAULT_ACCEPT_LABEL;
    }
  }

  function toggleConst() {
    if (!constRow) {
      return;
    }
    const show = vtypeSel.value === 'const';
    constRow.style.display = show ? 'block' : 'none';
  }

  function updateSummaryFromInputs() {
    summaryOsc.textContent = oscInput.value || '/learn';
    const typeLabel = TYPE_LABELS[vtypeSel.value] || vtypeSel.value;
    summaryType.textContent = typeLabel;
    if (vtypeSel.value === 'const' && constInput.value !== '') {
      summaryType.textContent = `${typeLabel} = ${constInput.value}`;
    }
    showMessage('');
  }

  async function pushConfig() {
    try {
      const fd = new FormData(form);
      if (vtypeSel.value !== 'const') {
        fd.delete('const');
      }
      await fetch('/arm_learn', { method: 'POST', body: fd });
    } catch (error) {
      console.warn('arm_learn error', error);
    }
  }

  function formatCandidate(candidate) {
    if (!candidate) {
      return '';
    }
    if (candidate.type === 'note') {
      const noteVal = typeof candidate.note === 'number' ? candidate.note : '?';
      const msgType = candidate.message_type === 'note_off' ? 'nota off' : 'nota';
      return `${msgType} ${noteVal}`;
    }
    const ccVal = typeof candidate.cc === 'number' ? candidate.cc : '?';
    const ch = candidate.channel;
    const chPart = typeof ch === 'number' ? ` canal ${ch}` : '';
    return `cc ${ccVal}${chPart}`;
  }

  function formatCandidateDetails(candidate) {
    if (!candidate) {
      return '';
    }
    const bits = [];
    if (candidate.type === 'note') {
      if (typeof candidate.channel === 'number') {
        bits.push(`canal ${candidate.channel}`);
      }
      if (typeof candidate.velocity === 'number') {
        bits.push(`velocidad ${candidate.velocity}`);
      }
    } else if (candidate.type === 'cc') {
      if (typeof candidate.channel === 'number') {
        bits.push(`canal ${candidate.channel}`);
      }
      if (typeof candidate.value === 'number') {
        bits.push(`valor ${candidate.value}`);
      }
    }
    return bits.join(' · ');
  }

  function formatResult(result) {
    if (!result) {
      return '';
    }
    const route = result.route || {};
    const osc = route.osc || '';
    const vtype = route.vtype || '';
    let midiPart = result.label || '';
    if (!midiPart) {
      if (route.type === 'note') {
        const noteVal = typeof route.note === 'number' ? route.note : '?';
        midiPart = `nota ${noteVal}`;
      } else if (route.type === 'cc') {
        const ccVal = typeof route.cc === 'number' ? route.cc : '?';
        midiPart = `cc ${ccVal}`;
        if (typeof route.channel === 'number') {
          midiPart += ` canal ${route.channel}`;
        }
      } else {
        midiPart = 'MIDI';
      }
    }
    return `${midiPart} → ${osc} (${vtype})`;
  }

  async function refreshLearnUI() {
    try {
      const res = await fetch('/learn_state');
      const st = await res.json();
      if (!initialised) {
        if (st.osc && !oscInput.value) {
          oscInput.value = st.osc;
        }
        if (st.vtype) {
          vtypeSel.value = st.vtype;
        }
        if (st.vtype === 'const' && typeof st.const !== 'undefined' && !constInput.value) {
          constInput.value = st.const;
        }
        toggleConst();
        updateSummaryFromInputs();
        initialised = true;
      }
      summaryOsc.textContent = st.osc || '/learn';
      const typeLabel = TYPE_LABELS[st.vtype] || st.vtype;
      summaryType.textContent = typeLabel;
      if (st.vtype === 'const' && typeof st.const !== 'undefined') {
        summaryType.textContent = `${typeLabel} = ${st.const}`;
        if (!constInput.matches(':focus')) {
          constInput.value = st.const;
        }
      }

      if (st.candidate) {
        summaryCandidate.textContent = formatCandidate(st.candidate);
        summaryKind.textContent = st.candidate.type === 'note' ? 'nota' : 'cc';
        const details = formatCandidateDetails(st.candidate);
        if (details) {
          summaryDetails.textContent = details;
          summaryDetails.style.display = 'block';
        } else {
          summaryDetails.textContent = '';
          summaryDetails.style.display = 'none';
        }
        const candidateKey = JSON.stringify([
          st.candidate.type,
          st.candidate.note,
          st.candidate.cc,
          st.candidate.channel,
          st.candidate.message_type
        ]);
        if (candidateKey !== lastCandidateKey) {
          showMessage('');
        }
        lastCandidateKey = candidateKey;
        if (!isSaving) {
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

      if (st.result && st.result.route) {
        resultBox.style.display = 'block';
        resultSummary.textContent = formatResult(st.result);
      } else {
        resultBox.style.display = 'none';
        resultSummary.textContent = '';
      }
    } catch (error) {
      console.warn('learn_state polling error', error);
    }
  }

  async function commitCandidate(confirm) {
    if (isSaving || acceptBtn.disabled) {
      return;
    }
    showMessage('');
    isSaving = true;
    setAcceptLoading(true);
    try {
      const fd = new FormData(form);
      if (vtypeSel.value !== 'const') {
        fd.delete('const');
      }
      if (confirm) {
        fd.append('confirm', '1');
      }
      const res = await fetch('/commit_learn', { method: 'POST', body: fd });
      let data = null;
      try {
        data = await res.json();
      } catch (error) {
        data = null;
      }
      if (res.ok && data && data.ok) {
        window.location.href = data.redirect || '/';
        return;
      }
      if (res.status === 409 && data && data.reason === 'duplicate') {
        const proceed = window.confirm(`La ruta OSC "${data.osc}" ya existe. ¿Deseas continuar?`);
        if (proceed) {
          isSaving = false;
          setAcceptLoading(false);
          acceptBtn.disabled = false;
          return commitCandidate(true);
        }
        showMessage('Ruta no guardada. Ajusta la ruta OSC si no quieres sobrescribir.');
      } else if (data && data.reason === 'no_candidate') {
        showMessage('Todavía no hay mensaje MIDI capturado.');
      } else {
        showMessage('No se pudo guardar la ruta. Vuelve a intentarlo.');
      }
    } catch (error) {
      showMessage('Error de red al guardar la ruta.');
    } finally {
      if (isSaving) {
        isSaving = false;
        setAcceptLoading(false);
      }
      refreshLearnUI();
    }
  }

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
  });
  acceptBtn.addEventListener('click', function () {
    commitCandidate(false);
  });

  toggleConst();
  updateSummaryFromInputs();
  pushConfig();
  refreshLearnUI();
  setInterval(refreshLearnUI, 250);

  oscInput.addEventListener('input', updateSummaryFromInputs);
  oscInput.addEventListener('change', pushConfig);
  vtypeSel.addEventListener('change', function () {
    toggleConst();
    updateSummaryFromInputs();
    pushConfig();
  });
  constInput.addEventListener('input', updateSummaryFromInputs);
  constInput.addEventListener('change', pushConfig);

  cancelBtn.addEventListener('click', function () {
    window.location.href = '/cancel_learn';
  });
})();
