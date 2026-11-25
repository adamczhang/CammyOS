'use strict';
const el = id => document.getElementById(id);
let token = '', agent = '', busy = false, generation = 0;
let probeCatalogue = null, probeJob = null;
let visionCaps = null, snapshotJob = null, imageURL = null, imageTimer = null, photoEpoch = 0;
let state = null, caps = null, connected = false, simulation = false, audioURL = null;
function buttons() {
  const online = Boolean(token && connected && state && !busy);
  const owner = online && state.owner === agent;
  el('acquire').disabled = !online || Boolean(state.owner && state.owner !== agent);
  el('release').disabled = !owner;
  el('stop').disabled = !(token && connected);
  el('preview-start').disabled = !(owner && caps && caps.preview && caps.preview.available);
  el('preview-stop').disabled = !owner;
  el('preview-check').disabled = !(owner && state.preview_active && caps?.preview?.status_available);
  const probeOnline = online && caps?.metadata_probes === true;
  el('list-probes').disabled = !probeOnline;
  el('probe-name').disabled = !(probeOnline && probeCatalogue);
  el('run-probe').disabled = !(probeOnline && probeCatalogue && el('probe-name').value && probeJob?.status !== 'running');
  el('cancel-probe').disabled = !(probeOnline && probeJob?.status === 'running' && probeJob.agent === agent);
  for (const [id, capability] of [['look-left', 'gimbal'], ['look-center', 'gimbal'], ['look-right', 'gimbal'],
      ['set-face', 'face'], ['sim-speak', 'speech'], ['sim-audio', 'audio_capture']]) {
    el(id).disabled = !(simulation && owner && caps && caps[capability] && caps[capability].available);
  }
  for (const id of ['sim-disconnect', 'sim-reconnect', 'sim-stale', 'sim-fresh', 'set-latency']) {
    el(id).disabled = !(simulation && token && !busy);
  }
  el('read-diagnostics').disabled = !(token && connected && !busy);
  el('snapshot').disabled = !(owner && visionCaps?.available && !snapshotJob?.active && state.lease_remaining_seconds >= 25);
  el('agent').disabled = Boolean(token);
}
async function api(path, method = 'GET', body = null, credential = token, label = agent) {
  const response = await fetch(path, {method, cache: 'no-store', signal: AbortSignal.timeout(5000),
    headers: {'Authorization': `Bearer ${credential}`, 'X-Agent-ID': label, 'Content-Type': 'application/json'},
    body: body === null ? null : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(`${response.status}: ${data.error || 'Request failed'}`);
  return data;
}
function resetBody() {
  visionCaps = null; snapshotJob = null; clearPhoto();
  state = null; caps = null; probeCatalogue = null; probeJob = null;
  el('probe-name').replaceChildren();
  el('probe-result').textContent = 'No current probe data.';
  el('preview-readiness').textContent = 'No current readiness data.';
  for (const id of ['endpoint', 'owner', 'lease', 'preview']) el(id).textContent = '—';
  el('capabilities').replaceChildren(); el('events').replaceChildren();
  el('diagnostics').textContent = 'No current body data.';
  el('body-diagnostics').textContent = 'No current body diagnostics.';
  el('sim-pose').textContent = 'Virtual gaze: —';
  el('sim-speech').textContent = 'No current simulated reply.';
  clearCanvas('sim-face'); clearCanvas('sim-frame');
  el('frame-status').textContent = 'No fresh synthetic frame.';
  if (audioURL) { URL.revokeObjectURL(audioURL); audioURL = null; }
  el('sim-audio-player').removeAttribute?.('src');
}
async function refresh() {
  if (busy) return;
  busy = true; buttons();
  const revision = generation;
  try {
    const health = await fetch('/health', {cache: 'no-store', signal: AbortSignal.timeout(5000)}).then(r => r.json());
    if (revision !== generation) return;
    connected = health.connected; simulation = health.simulation === true;
    el('simulation').hidden = !simulation;
    el('connection').textContent = health.offline ? 'Offline development — camera requests disabled' :
      simulation ? (connected ? 'SIMULATION — virtual camera connected' : 'SIMULATION — virtual camera disconnected') :
      connected ? 'Gateway connected to body' : 'Gateway running — body disconnected';
    if (!token || !connected) { resetBody(); return; }
    const credential = token, label = agent;
    const rows = await Promise.all(['/v1/gateway', '/v1/state', '/v1/capabilities', '/v1/events'].map(p => api(p, 'GET', null, credential, label)));
    if (revision !== generation) return;
    const [gateway, body, capabilities, events] = rows;
    state = body; caps = capabilities;
    await refreshVision(credential, label, revision);
    if (revision !== generation) return;
    el('endpoint').textContent = gateway.endpoint || '—';
    el('owner').textContent = body.owner || 'Unclaimed';
    el('lease').textContent = `${Math.max(0, body.lease_remaining_seconds).toFixed(1)} seconds (at last refresh)`;
    el('preview').textContent = body.preview_active ? 'Started; media freshness unverified' : 'Stopped';
    el('capabilities').replaceChildren(...['preview', 'audio_capture', 'speech', 'face', 'gimbal'].map(name => {
      const li = document.createElement('li');
      li.textContent = `${name}: ${caps[name] && caps[name].available ? (simulation ? 'simulated only' : 'backend advertised; hardware check required') : 'unavailable'}`;
      return li;
    }));
    el('events').replaceChildren(...events.events.slice(-12).map(event => {
      const li = document.createElement('li'); li.textContent = `${event.sequence}: ${event.type}`; return li;
    }));
    el('diagnostics').textContent = JSON.stringify({gateway, state, capabilities}, null, 2);
    if (caps.preview?.status_available) {
      try {
        const readiness = await api('/v1/preview/status', 'GET', null, credential, label);
        if (revision !== generation) return;
        el('preview-readiness').textContent = JSON.stringify(readiness, null, 2);
      } catch (error) {
        if (revision !== generation) return;
        el('preview-readiness').textContent = `Readiness unavailable: ${error.message}`;
      }
    } else el('preview-readiness').textContent = 'Readiness API unavailable on this body.';
    if (caps.metadata_probes && probeJob?.status === 'running') {
      try {
        const result = await api(`/v1/probes/${probeJob.job_id}`, 'GET', null, credential, label);
        if (revision !== generation) return;
        validateProbeJob(result); probeJob = result;
        el('probe-result').textContent = JSON.stringify(result, null, 2);
      } catch (error) {
        if (revision !== generation) return;
        probeJob = null; el('probe-result').textContent = `Probe result unavailable or evicted: ${error.message}`;
      }
    }
    if (simulation) {
      el('sim-pose').textContent = `Commanded virtual pan: ${state.commanded_pan_degrees}° (not measured)`;
      el('sim-speech').textContent = state.last_speech_text || 'No simulated reply.';
      drawFace(state.face, state.commanded_pan_degrees);
      if (state.preview_active) {
        try {
          const frame = await api('/v1/simulation/frame', 'GET', null, credential, label);
          if (revision === generation) drawFrame(frame);
        } catch (error) {
          if (revision === generation) { clearCanvas('sim-frame'); el('frame-status').textContent = `No fresh frame: ${error.message}`; }
        }
      } else { clearCanvas('sim-frame'); el('frame-status').textContent = 'Start simulated preview to generate frames.'; }
    }
  } catch (error) {
    if (revision === generation) { connected = false; resetBody(); el('message').textContent = error.message; }
  } finally { busy = false; buttons(); }
}
el('connect').addEventListener('submit', event => {
  event.preventDefault(); generation++;
  token = el('token').value.trim(); agent = el('agent').value;
  el('token').value = ''; el('message').textContent = ''; resetBody(); refresh();
});
el('forget').addEventListener('click', () => {
  generation++; token = ''; agent = ''; el('token').value = ''; resetBody(); buttons();
  el('message').textContent = 'Token forgotten. Any held lease expires naturally; forgetting does not send stop.';
});
async function action(path, body) {
  if (busy) return;
  busy = true; buttons(); const revision = generation;
  try {
    const result = await api(path, 'POST', body);
    if (revision === generation) {
      if (result.wav_base64) {
        if (audioURL) URL.revokeObjectURL(audioURL);
        const bytes = Uint8Array.from(atob(result.wav_base64), char => char.charCodeAt(0));
        audioURL = URL.createObjectURL(new Blob([bytes], {type: 'audio/wav'}));
        el('sim-audio-player').src = audioURL;
        el('audio-status').textContent = result.source;
        el('message').textContent = 'Synthetic sample ready. Play it explicitly using the audio control.';
      } else el('message').textContent = JSON.stringify(result);
    }
  } catch (error) { if (revision === generation) el('message').textContent = error.message; }
  finally { busy = false; buttons(); await refresh(); }
}
el('acquire').addEventListener('click', () => action('/v1/session', {agent, ttl: 30}));
el('release').addEventListener('click', () => { clearPhoto(); return action('/v1/session/release', {}); });
el('stop').addEventListener('click', async () => {
  clearPhoto();
  // Stop remains available while an ordinary refresh/action is awaiting a reply.
  const revision = generation;
  try {
    const result = await api('/v1/stop', 'POST', {});
    if (revision === generation) el('message').textContent = JSON.stringify(result);
  } catch (error) { if (revision === generation) el('message').textContent = error.message; }
  if (revision === generation) await refresh();
});
function clearPhoto() {
  photoEpoch++;
  if (imageTimer) clearTimeout(imageTimer);
  imageTimer = null;
  if (imageURL) URL.revokeObjectURL(imageURL);
  imageURL = null;
  el('snapshot-image').removeAttribute?.('src');
  el('snapshot-image').hidden = true;
}
async function refreshVision(credential, label, revision) {
  const photoRevision = photoEpoch, started = Date.now();
  try {
    const available = await api('/v1/vision/capabilities', 'GET', null, credential, label);
    if (revision !== generation) return;
    visionCaps = available;
    if (state.owner !== label) {
      clearPhoto(); snapshotJob = null;
      el('snapshot-status').textContent = 'Acquire control before requesting a photo.';
      return;
    }
    if (!snapshotJob) {
      el('snapshot-status').textContent = available.available ? 'Ready for one explicitly requested photo.' :
        `Snapshots unavailable${available.fault ? ': ' + available.fault : '; desktop feature disabled'}.`;
      return;
    }
    const result = await api(`/v1/vision/${snapshotJob.id}`, 'GET', null, credential, label);
    if (revision !== generation) return;
    snapshotJob = result;
    el('snapshot-status').textContent = result.active ? `Photo: ${result.phase}…` :
      result.phase === 'completed' ? `${result.synthetic ? 'SYNTHETIC TEST' : 'Camera still'}: ${result.width} × ${result.height}; original mode restored; age ${result.age_seconds}s.` :
      `Photo ${result.phase}: ${result.error || 'No image available'}`;
    if (!result.image_available) { clearPhoto(); return; }
    if (imageURL) return;
    const response = await fetch(`/v1/vision/${result.id}/image`, {
      cache: 'no-store', signal: AbortSignal.timeout(5000),
      headers: {'Authorization': `Bearer ${credential}`, 'X-Agent-ID': label}});
    if (!response.ok) throw new Error(`Image unavailable (${response.status})`);
    const blob = await response.blob();
    if (revision !== generation || photoRevision !== photoEpoch || state?.owner !== label) return;
    const remaining = 10000 - result.age_seconds * 1000 - (Date.now() - started);
    if (remaining <= 0) { clearPhoto(); return; }
    clearPhoto(); imageURL = URL.createObjectURL(blob);
    el('snapshot-image').src = imageURL; el('snapshot-image').hidden = false;
    imageTimer = setTimeout(() => {
      clearPhoto(); el('snapshot-status').textContent = 'Photo expired. Request another photo explicitly.';
    }, remaining);
  } catch (error) {
    if (revision === generation) {
      clearPhoto(); el('snapshot-status').textContent = `Snapshot unavailable: ${error.message}`;
    }
  }
}
el('snapshot').addEventListener('click', async () => {
  if (busy || !token || !visionCaps?.available || snapshotJob?.active) return;
  const revision = generation; busy = true; clearPhoto(); buttons();
  try {
    const result = await api('/v1/vision/capture', 'POST', {});
    if (revision === generation) {
      snapshotJob = result;
      el('snapshot-status').textContent = `Photo: ${result.phase}…`;
    }
  } catch (error) {
    if (revision === generation) el('snapshot-status').textContent = error.message;
  } finally { busy = false; buttons(); }
});
el('preview-start').addEventListener('click', () => action('/v1/preview/start', {}));
el('preview-check').addEventListener('click', () => action('/v1/preview/check', {}));
el('preview-stop').addEventListener('click', () => action('/v1/preview/stop', {}));
function clearCanvas(id) {
  const canvas = el(id), ctx = canvas.getContext?.('2d');
  if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
}
function drawFace(expression, pan) {
  const canvas = el('sim-face'), ctx = canvas.getContext?.('2d'); if (!ctx) return;
  ctx.fillStyle = '#101b21'; ctx.fillRect(0, 0, 356, 400);
  ctx.fillStyle = expression === 'muted' ? '#f1b7a6' : '#b5e9dc';
  const offset = Math.max(-45, Math.min(45, Number(pan) || 0)) / 2;
  for (const x of [110, 246]) {
    ctx.beginPath(); ctx.ellipse(x + offset, 170, 27, expression === 'sleep' || Date.now() % 5000 < 150 ? 3 : 38, 0, 0, 2 * Math.PI); ctx.fill();
  }
  ctx.font = '20px system-ui'; ctx.textAlign = 'center'; ctx.fillText(expression || 'idle', 178, 300);
  ctx.font = '16px system-ui'; ctx.fillText('SIMULATED SCREEN', 178, 365);
}
function drawFrame(frame) {
  if (!frame.fresh || frame.age_ms > 1000) { clearCanvas('sim-frame'); el('frame-status').textContent = 'Stale synthetic frame rejected'; return; }
  el('frame-status').textContent = `Synthetic frame ${frame.sequence}, age ${frame.age_ms}ms — ${frame.captured_utc}`;
  const ctx = el('sim-frame').getContext?.('2d'); if (!ctx) return;
  ctx.fillStyle = '#10242f'; ctx.fillRect(0, 0, 320, 180); ctx.strokeStyle = '#31535c';
  for (let x = 0; x < 320; x += 20) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 180); ctx.stroke(); }
  ctx.fillStyle = '#b5e9dc'; ctx.fillRect((frame.sequence * 13) % 280, 55, 40, 40);
  ctx.font = '16px system-ui'; ctx.fillText('SYNTHETIC CAMERA', 12, 155);
}
for (const [id, pan] of [['look-left', -20], ['look-center', 0], ['look-right', 20]])
  el(id).addEventListener('click', () => action('/v1/look', {pan_degrees: pan}));
el('set-face').addEventListener('click', () => action('/v1/face', {expression: el('face-expression').value}));
el('sim-speak').addEventListener('click', () => action('/v1/speak', {text: el('speech-text').value}));
el('sim-audio').addEventListener('click', () => action('/v1/audio/capture', {}));
for (const [id, faults] of [['sim-disconnect', {disconnected: true}], ['sim-reconnect', {disconnected: false}],
    ['sim-stale', {stale_frame: true}], ['sim-fresh', {stale_frame: false}]])
  el(id).addEventListener('click', () => action('/v1/simulation', faults));
el('set-latency').addEventListener('click', () => action('/v1/simulation', {latency_ms: Number(el('sim-latency').value)}));
refresh(); setInterval(refresh, 3000);

// Animate only the virtual screen; the real camera display is never addressed.
setInterval(() => {
  if (simulation && connected && state) drawFace(state.face, state.commanded_pan_degrees);
}, 100);

el('read-diagnostics').addEventListener('click', async () => {
  const revision = generation;
  try {
    const result = await api('/v1/diagnostics');
    if (revision === generation) el('body-diagnostics').textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    if (revision === generation) el('body-diagnostics').textContent = `Diagnostics unavailable (requires body v0.2+): ${error.message}`;
  }
});

function validateProbeJob(result) {
  if (!/^job-[1-9][0-9]{0,19}$/.test(result.job_id) ||
      !['running', 'completed', 'failed', 'timed_out', 'cancelled'].includes(result.status)) {
    throw new Error('Invalid probe job reply');
  }
}
async function probeAction(kind) {
  if (busy || !token || !connected) return;
  const revision = generation; busy = true; buttons();
  try {
    if (kind === 'catalogue') {
      const result = await api('/v1/probes');
      if (revision !== generation) return;
      probeCatalogue = result;
      const available = result.probes.filter(probe => probe.available);
      el('probe-name').replaceChildren(...available.map(probe => {
        const option = document.createElement('option'); option.value = probe.id;
        option.textContent = `${probe.id} — ${probe.effect}`; return option;
      }));
      el('probe-name').value = available[0]?.id || '';
      el('probe-result').textContent = JSON.stringify(result, null, 2);
    } else {
      const result = kind === 'start' ? await api('/v1/probes', 'POST', {probe: el('probe-name').value}) :
        await api(`/v1/probes/${probeJob.job_id}/cancel`, 'POST', {});
      if (revision !== generation) return;
      validateProbeJob(result); probeJob = result; el('probe-result').textContent = JSON.stringify(result, null, 2);
    }
  } catch (error) {
    if (revision === generation) el('probe-result').textContent = `Probe unavailable: ${error.message}`;
  } finally { busy = false; buttons(); }
}
el('list-probes').addEventListener('click', () => probeAction('catalogue'));
el('run-probe').addEventListener('click', () => probeAction('start'));
el('cancel-probe').addEventListener('click', () => probeAction('cancel'));
