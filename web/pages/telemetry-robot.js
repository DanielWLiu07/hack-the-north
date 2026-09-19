import { createCameraDiagnostics } from './seer/camera-diagnostics.js';
import { createSpatialTelemetry } from './seer/spatial-telemetry.js';
// telemetry-robot.js — the "Robot · live" section at the top of /telemetry (link session, docs/33).
//
// Its own file on purpose: telemetry.js is another session's, and this section must not wait for it. The board
// is hidden until an Elasticsearch query answers; whether the robot is reachable, what its camera sees and
// whether Sentry is watching it are known in milliseconds and belong on screen first.
//
// Three sources, all of them the site's own:
//   /api/robot/view.mjpg      the head camera (web/robot_view_api.py — one shared poller, however many viewers)
//   /api/robot/link           address · reachable · rtt · the robot's /healthz · is Sentry watching it
//   /api/events               the hub's 2 Hz telemetry frames — every signal the robot sends, not a selection
// Nothing is invented: a value that is not arriving reads "not reported", and an offline robot says why.

const host = document.getElementById('robot-live');
if (host) start();

function start() {
  const h = (tag, attrs = {}, ...kids) => { const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) { if (v === false || v == null) continue; k === 'class' ? e.className = v : e.setAttribute(k, v === true ? '' : v); }
    for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(String(c))); return e; };
  const NS = 'http://www.w3.org/2000/svg';
  const s = (tag, attrs = {}, text) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text != null) e.textContent = text; return e; };
  const fix = (v, n) => (typeof v === 'number' && Number.isFinite(v) ? v.toFixed(n) : '–');

  // ── skeleton ────────────────────────────────────────────────────────────────────────────────────
  const img = h('img', { alt: 'Live frame from the robot’s head camera', decoding: 'async' });
  const badge = h('span', { class: 'rl-badge' }, h('i', {}), h('b', {}, 'CONNECTING'));
  const why = h('div', { class: 'rl-why' }, h('strong', {}, 'No live picture'), h('span', {}, 'asking the robot…'));
  const eye = h('div', { class: 'rl-eye rl-lenses', role: 'group', 'aria-label': 'Live camera lens' });
  let lens = 'left';
  for (const [key, label] of [['left', 'Left lens'], ['right', 'Right lens'], ['stereo', 'Stereo']]) {
    const button = h('button', { type: 'button', class: 'navbtn', 'aria-pressed': String(key === lens) }, label);
    button.onclick = () => { lens = key; stage.classList.toggle('stereo', key === 'stereo'); stage.classList.toggle('right-eye', key === 'right'); for (const b of eye.children) b.setAttribute('aria-pressed', String(b === button)); };
    eye.append(button);
  }
  const stage = h('div', { class: 'rl-stage offline' }, img, badge, why, eye);
  const facts = h('dl', { class: 'rl-facts' });
  const sig = h('div', { class: 'rl-signals' });
  const state = document.getElementById('robot-live-state');
  const cameraView = h('div', { class: 'rl-top' }, stage, facts);
  host.append(cameraView, sig);
  const diagnostics = createCameraDiagnostics(host);


  // ── camera: an <img> plays the multipart stream by itself. Only opened while the tab is visible, so a
  //    forgotten background tab is not a viewer and the robot is not read for nobody.
  let streaming = false;
  const stream = (on) => { if (on && !host.isConnected || on === streaming) return; streaming = on; if (on) img.src = '/api/robot/view.mjpg?t=' + Date.now(); else img.removeAttribute('src'); };
  const spatial = createSpatialTelemetry(host, cameraView, on => stream(on && !document.hidden));
  document.addEventListener('visibilitychange', () => stream(!document.hidden && spatial.cameraVisible));
  img.onerror = () => { if (streaming) setTimeout(() => { streaming = false; stream(!document.hidden && spatial.cameraVisible); }, 3000); };
  // Not before `load`: a multipart <img> never finishes, and a pending image holds the page's load event (and the
  // tab's spinner) open for as long as the stream runs.
  if (document.readyState === 'complete') stream(!document.hidden && spatial.cameraVisible); else window.addEventListener('load', () => stream(!document.hidden && spatial.cameraVisible), { once: true });

  // ── facts ───────────────────────────────────────────────────────────────────────────────────────
  const row = (k, v, cls) => [h('dt', {}, k), h('dd', { class: cls || '' }, v)];
  let view = {}, link = {}, lastFrames = null, lastAt = 0, lastBoot = null, fps = null, polling = false;
  function paintFacts() {
    const hz = link.healthz || {}, tel = hz.telemetry || {}, ev = hz.events || {}, w = link.watch || {};
    const live = !!view.live, age = view.frame_age_s;
    const st = live ? 'live' : (view.frame_kb ? 'stale' : 'offline');
    stage.className = `rl-stage ${st}${lens === 'stereo' ? ' stereo' : lens === 'right' ? ' right-eye' : ''}`;
    badge.lastChild.textContent = st === 'live' ? 'LIVE' : st === 'stale' ? `STALE · ${fix(age, 0)} s` : 'OFFLINE';
    why.firstChild.textContent = st === 'stale' ? 'The picture has stopped updating' : 'No live picture';
    why.lastChild.textContent = view.error || link.error || 'waiting for the first frame…';
    if (state) state.textContent = link.reachable ? (live ? 'receiving' : 'robot up · no picture') : 'robot unreachable';

    const openKinds = Object.keys(w.open || {}), pend = Object.keys(w.pending || {});
    const sentry = link.watch == null ? 'checking…' : !w.watching ? [h('b', { class: 'off' }, 'NOT watching the robot'), ` · ${w.reason || ''}`]
      : w.dry_run || !w.sentry_live ? [h('b', { class: 'off' }, 'watching, but NOT reporting'), ' · dry run, or no SENTRY_DSN']
      : openKinds.length ? [h('b', { class: 'off' }, `${openKinds.length} open: ${openKinds.join(', ')}`), ` · filed to Sentry · checked ${fix(w.checked_s_ago, 0)} s ago`]
      : ['watching the robot from outside', pend.length ? h('b', { class: 'off' }, ` · ${pend.join(', ')} (not yet an issue)`) : ' · nothing open', ` · checked ${fix(w.checked_s_ago, 0)} s ago`];
    const lastFiled = (w.filed || []).slice(-1)[0];

    facts.replaceChildren(...[
      row('link', link.reachable ? `${link.link} · ${link.robot} · ${fix(link.rtt_ms, 0)} ms`
        : link.reachable === false ? [h('b', { class: 'off' }, 'DOWN'), ` · ${link.link || '?'} · ${link.robot || 'no address'}`] : 'checking…'),
      row('robot', link.reachable ? `${hz.mode} · ${hz.fw} · boot ${String(hz.boot_id || '').slice(0, 8)}` : (link.error || '–'), link.reachable ? '' : 'dim'),
      row('camera', link.reachable ? [`${(hz.cameras || []).join(', ') || 'none'}`, Object.keys(hz.unavailable || {}).length ? h('b', { class: 'off' }, ` · unavailable: ${Object.keys(hz.unavailable).join(', ')}`) : '',
        live ? ` · frame ${fix(age, 2)} s old · ${fps == null ? '–' : fix(fps, 1)} fps · ${view.frame_kb} KB` : ' · no picture'] : '–'),
      row('telemetry tap', link.reachable ? [`50 Hz · skipped ticks ${tel.overruns ?? '–'} · `, (tel.source_errors ? h('b', { class: 'off' }, `source errors ${tel.source_errors}`) : `source errors ${tel.source_errors ?? 'not reported'}`), ` · dropped ${tel.dropped ?? '–'}`] : '–'),
      row('event log', link.reachable ? `#${String(ev.last_id || '').split(':').pop() || '–'} · ${ev.clients ?? 0} client${ev.clients === 1 ? '' : 's'} · dropped ${ev.dropped ?? 'not reported'}` : '–'),
      row('last capture', link.reachable ? (hz.last_capture ? h('a', { href: `/capture/${hz.last_capture}` }, hz.last_capture) : 'none since it started') : '–'),
      row('sentry', sentry),
      lastFiled ? row('last filed', `${lastFiled.kind} · ${lastFiled.at} — ${lastFiled.detail}`, 'dim') : [],
    ].flat());
  }
  let removed = false;
  function gone() { if (removed) return; removed = true; stream(false); clearInterval(timer); diagnostics.dispose(); spatial.dispose(); const sec = host.closest('section'); if (sec) sec.remove(); }
  async function poll() {
    if (removed || polling || document.hidden) return;
    polling = true;
    const pollErrors = [];
    // This route is local-only; a 403 removes the private camera panel.
    const get = async (u) => {
      try {
        const r = await fetch(u, { cache: 'no-store', signal: AbortSignal.timeout(6000) });
        if (r.status === 403) { gone(); return {}; }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return await r.json();
      } catch (e) {
        pollErrors.push(`${u.endsWith('/link') ? 'Robot health' : 'Camera status'} unavailable: ${e.message}`);
        return {};
      }
    };
    try {
      [view, link] = await Promise.all([get('/api/robot/view/status'), get('/api/robot/link')]);
      if (removed) return;
      const now = performance.now();
      fps = lastFrames != null && Number.isFinite(view.frames) && view.frames >= lastFrames &&
        view.boot_id === lastBoot && now > lastAt ? (view.frames - lastFrames) / ((now - lastAt) / 1000) : null;
      lastFrames = view.frames ?? null; lastAt = now; lastBoot = view.boot_id;
      paintFacts();
      diagnostics.update({view, link, fps, pollErrors});
      if (pollErrors.length && state) state.textContent = 'diagnostics unavailable';
    } finally { polling = false; }
  }
  paintFacts();                      // rows first, values when they arrive: the column is never blank
  const timer = setInterval(poll, 2000); poll();

  // ── every signal the robot sends, not a selection ───────────────────────────────────────────────
  // The hub posts the LATEST sample of each signal at 2 Hz (+ tilt_rate_peak, the peak since the last frame).
  // Encoders are cumulative turns — a ramp says nothing — so they are drawn as wheel speed, their difference.
  const KEEP = 120, GATE = 0.05, frames = [];
  const ROWS = [
    { key: 'tilt_rate_peak', label: 'tilt rate · peak', unit: 'rad/s', digits: 3, gate: GATE, zero: true },
    { key: 'tilt_rate', label: 'tilt rate · now', unit: 'rad/s', digits: 3 },
    { key: 'pitch', label: 'pitch', unit: '°', digits: 1, map: (v) => v * 180 / Math.PI },
    { key: 'left_speed', label: 'left wheel', unit: 'turn/s', digits: 2 },
    { key: 'right_speed', label: 'right wheel', unit: 'turn/s', digits: 2 },
    { key: 'motor_current_l', label: 'motor current L', unit: 'A', digits: 2, zero: true },
    { key: 'motor_current_r', label: 'motor current R', unit: 'A', digits: 2, zero: true },
    { key: 'odom_residual', label: 'odom residual', unit: 'm', digits: 4, zero: true },
  ];
  const num = (f, k) => (typeof f[k] === 'number' && Number.isFinite(f[k]) ? f[k] : null);
  function paintSignals() {
    if (!frames.length) {
      sig.replaceChildren(h('p', { class: 'slot big' }, 'No telemetry is arriving · the hub (telemetry/hub.py) posts the robot’s signals here at 2 Hz. ',
        link.reachable === false ? 'The robot is unreachable, so there is nothing to post.' : 'Is the hub running? tmux window `hub`.'));
      return;
    }
    const W = Math.max(320, sig.clientWidth || 900), L = 132, R = 96, RH = 34, off = KEEP - frames.length;
    const x = (i) => L + (i / (KEEP - 1)) * (W - L - R);
    const svg = s('svg', { viewBox: `0 0 ${W} ${ROWS.length * RH + 22}`, role: 'img', 'aria-label': 'every telemetry signal from the robot, last 60 seconds' });
    const silent = [];
    ROWS.forEach((r, ri) => {
      const vals = frames.map((f) => { const v = num(f, r.key); return v == null ? null : (r.map ? r.map(v) : v); }), nums = vals.filter((v) => v != null);
      const y0 = ri * RH + 5, y1 = (ri + 1) * RH - 5, mid = (y0 + y1) / 2 + 4;
      svg.append(s('text', { class: 't', x: 0, y: mid }, r.label));
      if (!nums.length) { silent.push(r.label); svg.append(s('text', { class: 't', x: L, y: mid }, 'not reported by the robot')); return; }
      const hi = Math.max(1e-6, ...nums.map(Math.abs), r.gate ? r.gate * 1.4 : 0), lo = r.zero ? 0 : -hi;
      const y = (v) => y1 - ((v - lo) / (hi - lo)) * (y1 - y0);
      svg.append(s('line', { x1: L, x2: W - R, y1: y(0), y2: y(0), stroke: '#26262e' }));
      if (r.gate) svg.append(s('line', { x1: L, x2: W - R, y1: y(r.gate), y2: y(r.gate), stroke: '#8a8a94', 'stroke-dasharray': '5 5' }),
        s('text', { class: 't', x: W - R - 4, y: y(r.gate) - 4, 'text-anchor': 'end' }, `gate ${r.gate}`));
      let d = '', pen = false;
      vals.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? 'L' : 'M'}${x(i + off).toFixed(1)} ${y(v).toFixed(1)}`; pen = true; });
      svg.append(s('path', { d, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.8, 'stroke-linejoin': 'round' }));
      if (r.gate) vals.forEach((v, i) => { if (v != null && v >= r.gate) svg.append(s('circle', { cx: x(i + off), cy: y(v), r: 4, class: 'over' })); });
      const last = nums[nums.length - 1];
      svg.append(s('text', { class: r.gate && last >= r.gate ? 'tw' : 'tl', x: W - R + 8, y: mid }, `${fix(last, r.digits)} ${r.unit}`));
    });
    const by = ROWS.length * RH + 4;
    svg.append(s('text', { class: 't', x: 0, y: by + 10 }, 'balanced'));
    frames.forEach((f, i) => { if (typeof f.balanced === 'boolean') svg.append(s('rect', { x: x(i + off) - 1, y: by, width: Math.max(2, (W - L - R) / KEEP), height: 11, class: f.balanced ? 'bal' : 'unbal' })); });
    const last = frames[frames.length - 1];
    sig.replaceChildren(h('div', { class: 'strip' }, svg), h('p', { class: 'slot' }, `${frames.length} frames · last ${last.ts || '–'} · balanced: `,
      h('b', { class: last.balanced === false ? 'off' : '' }, last.balanced === false ? '✕ NO' : last.balanced === true ? '✓ yes' : 'not reported'),
      silent.length ? ` · not reported by this robot: ${silent.join(', ')}` : ''));
  }
  if ('EventSource' in window) {
    const es = new EventSource('/api/events');
    es.addEventListener('telemetry', (e) => { let f; try { f = JSON.parse(e.data); } catch { return; }
      const p = frames[frames.length - 1], dt = p && f.ts && p.ts ? (Date.parse(f.ts) - Date.parse(p.ts)) / 1000 : 0;
      for (const side of ['left', 'right']) { const a = num(f, `${side}_enc`), b = p ? num(p, `${side}_enc`) : null;
        f[`${side}_speed`] = a != null && b != null && dt > 0 && dt < 5 ? (a - b) / dt : null; }
      frames.push(f); if (frames.length > KEEP) frames.shift(); paintSignals(); });
  }
  paintSignals(); setInterval(() => { if (frames.length && Date.now() - Date.parse(frames[frames.length - 1].ts || 0) > 6000) paintSignals(); }, 3000);
  window.addEventListener('resize', paintSignals);
}
