// Minimal DOM harness: tests page logic offline without a browser or camera.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: id === 'agent' ? 'desktop' : '', textContent: '', disabled: false,
    handlers: {}, children: [], addEventListener(name, fn) { this.handlers[name] = fn; },
    replaceChildren(...children) { this.children = children; }
  });
  return elements.get(id);
}
const calls = [];
let probes = false, jobStatus = 'running', jobMissing = false;
let vision = false, snapshotActive = true;
let connected = false, owner = null, advertised = false, offline = true, simulation = false, stale = false;
const sandbox = {document: {getElementById: element, createElement: () => ({textContent: ''})},
  AbortSignal, setInterval: () => {}, setTimeout: () => 1, clearTimeout: () => {},
  URL: {createObjectURL: () => 'blob:synthetic-test', revokeObjectURL: () => {}},
  fetch: async (route, options) => {
    calls.push([route, options]);
    if (route.endsWith('/image')) return {ok: true, blob: async () => ({type:'image/jpeg'})};
    if (route === '/v1/probes/job-1' && jobMissing) return {ok:false,status:404,json:async()=>({error:'expired job'})};
    const rows = {'/health': {connected, offline, simulation}, '/v1/gateway': {endpoint: 'loopback-test'},
      '/v1/state': {owner, lease_remaining_seconds: 30, preview_active: simulation, commanded_pan_degrees: 20, face: 'listening', last_speech_text: 'simulated'},
      '/v1/capabilities': {metadata_probes: probes, preview: {available: advertised, status_available: probes}, gimbal: {available: advertised}, face: {available: advertised}},
      '/v1/simulation/frame': {fresh: !stale, age_ms: stale ? 2000 : 0, sequence: 1, captured_utc: 'synthetic'},
      '/v1/preview/status': {phase: 'sdp-ready', frame_fresh: null},
      '/v1/probes': options.method === 'POST' ? {job_id:'job-1',agent:'desktop',status:'running'} : {probes:[{id:'runtime',available:true,effect:'read-only metadata'}]},
      '/v1/probes/job-1': {job_id:'job-1',agent:'desktop',status:jobStatus},
      '/v1/probes/job-1/cancel': {job_id:'job-1',agent:'desktop',status:'cancelled'},
      '/v1/events': {events: [{sequence: 1, type: '<script>untrusted</script>'}]}};
    rows['/v1/vision/capabilities'] = {available:vision};
    rows['/v1/vision/capture'] = {id:'frame-1',active:true,phase:'validating'};
    rows['/v1/vision/frame-1'] = {id:'frame-1',active:snapshotActive,phase:snapshotActive?'capturing':'completed',
      image_available:!snapshotActive,width:48,height:32,age_seconds:0,synthetic:true};
    return {ok: true, json: async () => rows[route] || {stopped: true}};
  }};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../host/web/app.js'), 'utf8'), sandbox);
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  await settle();
  assert.match(element('connection').textContent, /camera requests disabled/);
  assert.equal(element('stop').disabled, true);
  element('token').value = 'test-token';
  element('connect').handlers.submit({preventDefault() {}});
  await settle();
  assert.equal(element('token').value, '');
  assert.equal(calls.some(([route]) => route.startsWith('/v1/')), false);
  connected = true; offline = false;
  await vm.runInContext('refresh()', sandbox);
  assert.equal(element('acquire').disabled, false);
  assert.equal(element('preview-start').disabled, true);
  owner = 'another-agent';
  await vm.runInContext('refresh()', sandbox);
  assert.equal(element('acquire').disabled, true);
  assert.equal(element('release').disabled, true);
  assert.equal(element('stop').disabled, false);
  owner = 'desktop'; advertised = true;
  await vm.runInContext('refresh()', sandbox);
  assert.equal(element('release').disabled, false);
  assert.equal(element('preview-start').disabled, false);
  assert.equal(element('snapshot').disabled, true);
  vision = true;
  await vm.runInContext('refresh()', sandbox);
  assert.equal(element('snapshot').disabled, false);
  assert.equal(calls.some(([route]) => route === '/v1/vision/capture'), false);
  await element('snapshot').handlers.click();
  assert.equal(element('snapshot').disabled, true);
  snapshotActive = false;
  await vm.runInContext('refresh()', sandbox);
  assert.equal(element('snapshot-image').hidden, false);
  assert.match(element('snapshot-status').textContent, /SYNTHETIC TEST/);
  owner = 'another-agent';
  await vm.runInContext('refresh()', sandbox);
  assert.equal(element('snapshot-image').hidden, true);
  owner = 'desktop';
  assert.equal(element('events').children[0].textContent, '1: <script>untrusted</script>');
  await element('stop').handlers.click();
  const post = calls.filter(([route, options]) => route === '/v1/stop' && options.method === 'POST');
  assert.equal(post.length, 1); assert.equal(post[0][0], '/v1/stop');
  assert.equal(post[0][1].headers.Authorization, 'Bearer test-token');
  simulation = true;
  await vm.runInContext('refresh()', sandbox);
  assert.match(element('connection').textContent, /SIMULATION/);
  assert.equal(element('look-left').disabled, false);
  assert.match(element('frame-status').textContent, /Synthetic frame/);
  stale = true;
  await vm.runInContext('refresh()', sandbox);
  assert.match(element('frame-status').textContent, /Stale synthetic frame rejected/);
  probes = true;
  await vm.runInContext('refresh()', sandbox);
  assert.equal(element('list-probes').disabled, false);
  assert.equal(element('run-probe').disabled, true);
  assert.match(element('preview-readiness').textContent, /sdp-ready/);
  await element('list-probes').handlers.click();
  assert.equal(element('probe-name').value, 'runtime');
  assert.equal(element('run-probe').disabled, false);
  await element('run-probe').handlers.click();
  assert.equal(element('cancel-probe').disabled, false);
  assert.equal(element('run-probe').disabled, true);
  await element('cancel-probe').handlers.click();
  assert.match(element('probe-result').textContent, /cancelled/);
  await element('run-probe').handlers.click(); jobStatus = 'completed';
  await vm.runInContext('refresh()', sandbox);
  assert.match(element('probe-result').textContent, /completed/);
  jobStatus = 'running'; await element('run-probe').handlers.click(); jobMissing = true;
  await vm.runInContext('refresh()', sandbox);
  assert.match(element('probe-result').textContent, /evicted/);
  assert.equal(element('stop').disabled, false);
  jobMissing = false;
  vm.runInContext('busy = true; buttons()', sandbox);
  assert.equal(element('stop').disabled, false);
  await element('stop').handlers.click();
  vm.runInContext('busy = false; buttons()', sandbox);
  connected = false;
  await vm.runInContext('refresh()', sandbox);
  assert.equal(element('look-left').disabled, true);
  assert.equal(element('sim-reconnect').disabled, false);
  connected = true;
  element('forget').handlers.click();
  assert.equal(element('stop').disabled, true);
  connected = false;
  await vm.runInContext('refresh()', sandbox);
  assert.equal(element('owner').textContent, '—');
  assert.equal(element('preview-start').disabled, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
