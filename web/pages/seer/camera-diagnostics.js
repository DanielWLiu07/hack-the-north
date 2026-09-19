// Read-only detail for the camera preview and the external health watcher.
// Reuse the page's poll results: opening this panel adds no robot reads.
const make = (tag, text, cls) => {
  const el = document.createElement(tag);
  if (text != null) el.textContent = String(text);
  if (cls) el.className = cls;
  return el;
};
const value = (v, unit = '') => v == null ? 'not reported' : `${v}${unit}`;

export function createCameraDiagnostics(host) {
  const panel = make('details', null, 'camera-diagnostics');
  panel.append(make('summary', 'Camera diagnostics & incident history'));
  const body = make('div', null, 'camera-diagnostics-body');
  panel.append(body);
  host.append(panel);
  const style = make('style');
  style.textContent = `
    .camera-diagnostics { border-top:1px solid var(--glass-line,var(--line));margin-top:20px;padding-top:16px;min-width:0; }
    .camera-diagnostics summary { cursor:pointer;font:12px var(--mono);color:var(--ink); }
    .camera-diagnostics-body { display:grid;gap:20px;padding-top:18px; }
    .camera-diagnostics h3 { margin:0 0 12px;font:600 12px var(--mono); }
    .camera-diagnostics p,.camera-diagnostics li { font:12px/1.6 var(--mono);overflow-wrap:anywhere; }
    .camera-diagnostics dl { display:grid;grid-template-columns:minmax(100px,.7fr) minmax(0,1fr);gap:8px 16px;margin:0;font:12px/1.5 var(--mono); }
    .camera-diagnostics dt { color:var(--dim); }
    .camera-diagnostics dd { margin:0;overflow-wrap:anywhere; }
    .camera-diagnostics ol { padding-left:20px;margin:0; }
    .camera-diagnostics li { padding:8px 0;border-top:1px solid var(--glass-line,var(--line)); }
    .camera-diagnostics time { display:block;color:var(--dim);font-size:10px; }
    .camera-diagnostics .diag-bad { color:var(--wrong,#f2a03c); }
    .camera-diagnostics pre { white-space:pre-wrap;overflow-wrap:anywhere;font:11px/1.5 var(--mono);max-height:320px;overflow:auto; }
    @media(min-width:1000px) { .camera-diagnostics-body { grid-template-columns:1fr 1fr; } .camera-diagnostics .diag-history,.camera-diagnostics .diag-raw { grid-column:1/-1; } }
  `;
  panel.append(style);
  let latest = {}, fingerprint = '';
  const block = title => { const el = make('section'); el.append(make('h3', title)); return el; };
  const rows = (el, entries) => {
    const dl = make('dl');
    for (const [k, v] of entries) dl.append(make('dt', k), make('dd', v));
    el.append(dl);
  };
  function render() {
    if (!panel.open) return;
    const {view = {}, link = {}, fps = null, pollErrors = []} = latest;
    const hz = link.healthz || {}, watch = link.watch || {}, preview = hz.preview || {};
    const camera = block('Camera & frame delivery');
    rows(camera, [
      ['Preview camera', value(view.camera)],
      ['Available cameras', Array.isArray(hz.cameras) ? hz.cameras.join(', ') || 'none' : 'not reported'],
      ['Picture state', view.live === true ? 'live' : view.live === false ? (view.frame_kb ? 'stale' : 'no picture') : 'not reported'],
      ['Frame at laptop', value(view.frame_age_s, ' s old')],
      ['Frame at robot', value(view.robot_frame_age_ms, ' ms old at receipt')],
      ['Delivery / target', `${fps == null ? 'not reported' : fps.toFixed(1)} / ${value(view.target_fps)} fps`],
      ['Frame size', value(view.frame_kb, ' KB')],
      ['New frames / polls', `${value(view.frames)} / ${value(view.polls)}`],
      ['Preview viewers', value(view.viewers)],
      ['Preview reads / served', `${value(preview.reads)} / ${value(preview.served)}`],
      ['Preview cache age', value(preview.last_age_ms, ' ms')],
      ['Frame clients / drops', `${value(hz.frames_clients)} / ${value(hz.frames_dropped)}`],
      ['Camera boot', value(view.boot_id || null)],
    ]);
    if (view.error) camera.append(make('p', view.error, 'diag-bad'));
    for (const [name, reason] of Object.entries(hz.unavailable || {})) camera.append(make('p', `${name}: ${reason}`, 'diag-bad'));
    camera.append(make('p', 'Live preview is colour only. Depth, calibration and capture quality must be verified on a recorded capture.'));
    if (/^cap_[a-zA-Z0-9_-]+$/.test(hz.last_capture || '')) {
      const a = make('a', `Inspect ${hz.last_capture}`); a.href = `/capture/${encodeURIComponent(hz.last_capture)}`; camera.append(a);
    }
    const health = block('Reporting & data health');
    const reporting = watch.watching === true && watch.sentry_live === true && watch.dry_run === false;
    rows(health, [
      ['Watcher', watch.watching === true ? 'running' : watch.watching === false ? 'not running' : 'not reported'],
      ['Sentry reporting', reporting ? 'enabled on watcher' : watch.dry_run ? 'dry run; not sent' : 'not confirmed'],
      ['Last health check', value(watch.checked_s_ago, ' s ago')],
      ['Robot health', watch.healthy === true ? 'healthy at last check' : watch.healthy === false ? 'fault detected' : 'not reported'],
      ['Source errors', value(hz.telemetry?.source_errors)],
      ['Skipped samples', value(hz.telemetry?.overruns)],
      ['Telemetry drops', value(hz.telemetry?.dropped)],
      ['Event drops', value(hz.events?.dropped)],
      ['Event cursor', value(hz.events?.last_id)],
    ]);
    if (watch.reason) health.append(make('p', watch.reason, 'diag-bad'));
    for (const error of pollErrors) health.append(make('p', error, 'diag-bad'));
    for (const [kind, detail] of Object.entries(watch.open || {})) health.append(make('p', `OPEN · ${kind}: ${detail}`, 'diag-bad'));
    for (const [kind, seconds] of Object.entries(watch.pending || {})) health.append(make('p', `PENDING · ${kind}: observed for ${seconds} s; not yet filed`, 'diag-bad'));
    const history = block('Recent failures & recovery'); history.className = 'diag-history';
    history.append(make('p', 'Recent watcher records, newest first. A recovery record means the condition cleared; it does not confirm that its Sentry issue was resolved. Capture traces and logs are linked in Capture history below.'));
    const records = Array.isArray(watch.filed) ? watch.filed.slice(-12).reverse() : [];
    if (!records.length) history.append(make('p', 'No incident records returned. This is not a complete error archive.'));
    else {
      const list = make('ol');
      for (const record of records) {
        const li = make('li', null, record.level === 'error' || record.level === 'warning' ? 'diag-bad' : '');
        li.append(make('time', record.at), make('strong', `${value(record.level)} · ${value(record.kind)}`), make('div', value(record.detail)));
        list.append(li);
      }
      history.append(list);
    }
    const raw = make('details', null, 'diag-raw');
    raw.open = body.querySelector('.diag-raw')?.open || false;
    raw.append(make('summary', 'All returned diagnostic fields'), make('pre', JSON.stringify({view, link}, null, 2)));
    body.replaceChildren(camera, health, history, raw);
  }
  panel.addEventListener('toggle', render);
  return {
    update(snapshot) {
      latest = snapshot;
      const next = JSON.stringify(snapshot);
      if (next === fingerprint) return;
      fingerprint = next; render();
    },
    dispose() { panel.remove(); },
  };
}
