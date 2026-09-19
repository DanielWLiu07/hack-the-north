// replay.js — Robot Session Replay (docs/29-how-we-use-sentry.md ★). Sentry replays browser sessions;
// this replays a robot from logs that already exist. One clock (seconds from the shutter) drives three
// views at once: the robot (path from encoders, lean from pitch, doubt from odom_residual), the Sentry
// trace of the same moment, and the telemetry lanes. Drag anywhere on the lanes — or a span — to scrub.
//
// Rules: nothing is drawn that was not recorded. A missing lane says why. Derived things (the path, the
// tilt-spike marker) are labelled derived. One accent colour, and it only ever means "this is wrong".
//
//   import { mountReplay } from '/pages/replay.js';
//   mountReplay(host, { captureId: 'cap_0004' })   |   mountReplay(host, { from: iso, to: iso })

const NS = 'http://www.w3.org/2000/svg';
const WHEEL_R = 0.0825, WHEEL_BASE = 0.425, BODY_H = 0.5;        // metres; docs/02-hardware.md (body height is the drawing's, not a measurement)

function h(tag, attrs = {}, ...kids) {
  const el = attrs.ns ? document.createElementNS(NS, tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'ns' || v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'text') el.textContent = v;
    else el.setAttribute(k === 'className' ? 'class' : k, v === true ? '' : v);
  }
  for (const kid of kids.flat(3)) if (kid != null && kid !== false) el.append(kid);
  return el;
}
const s = (tag, attrs = {}, ...kids) => h(tag, { ...attrs, ns: 1 }, ...kids);
const fill = (el, ...kids) => el.replaceChildren(...kids.flat(3).filter((k) => k != null && k !== false));
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const lin = (d0, d1, r0, r1) => (v) => r0 + ((v - d0) * (r1 - r0)) / ((d1 - d0) || 1);
const signed = (v, n) => `${v < 0 ? '−' : v > 0 ? '+' : ''}${Math.abs(v).toFixed(n)}`;
const tLabel = (t) => (Math.abs(t) < 1 ? `${signed(t * 1000, 0)} ms` : `${signed(t, 2)} s`);

/** index of the sample nearest in time (series: [[t, …], …] sorted by t) */
function nearestIndex(series, t) {
  if (!series || !series.length) return -1;
  let lo = 0, hi = series.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (series[mid][0] <= t) lo = mid; else hi = mid; }
  return Math.abs(series[lo][0] - t) <= Math.abs(series[hi][0] - t) ? lo : hi;
}
const valueAt = (series, t) => { const i = nearestIndex(series, t); return i < 0 ? null : series[i][1]; };

const LANES = [
  { key: 'tilt_rate', label: 'tilt_rate', unit: 'rad/s', digits: 3, gate: true, symmetric: true },
  { key: 'pitch', label: 'pitch — the body’s lean', short: 'pitch', unit: 'rad', digits: 3, symmetric: true },
  { key: 'odom_residual', label: 'odom_residual — drift, measured', short: 'odom_residual', unit: 'm', digits: 4, zero: true },
  { key: 'motor_current_l', with: 'motor_current_r', label: 'motor current  L ─  R ┄', short: 'current L─ R┄', unit: 'A', digits: 2, zero: true },
  { key: 'balanced', label: 'balanced', unit: '', digits: 0, step: true, zero: true },
];

export async function mountReplay(host, opts = {}) {
  host.classList.add('replay');
  host.replaceChildren(h('p', { className: 'rp-msg', role: 'status', text: 'Loading the replay…' }));
  const url = opts.captureId ? `/api/replay/${encodeURIComponent(opts.captureId)}`
    : `/api/replay/window?from=${encodeURIComponent(opts.from)}&to=${encodeURIComponent(opts.to)}`;
  let D;
  try {
    const r = await fetch(url, { headers: { accept: 'application/json' } });
    D = await r.json();
    if (!r.ok) throw Object.assign(new Error(D.detail || r.statusText), D);
  } catch (e) {
    // embedded under a capture page that has ALREADY said "there is no capture cap_9999": do not say it twice
    if (opts.embedded && e.error === 'not_found') { (host.closest('.replay-embed') || host).remove(); return null; }
    host.replaceChildren(h('p', { className: 'rp-msg', role: 'alert', text: `The replay could not be loaded: ${e.error ? `${e.error} — ` : ''}${e.message}` }));
    return null;
  }
  return new Replay(host, D, opts);
}

class Replay {
  constructor(host, D, opts) {
    this.host = host; this.D = D; this.opts = opts;
    const sig = D.signals || {};
    this.recorded = Object.keys(sig).filter((k) => sig[k].length);
    // the clock: what was actually recorded, plus the trace, plus the shutter — not the empty seconds asked for
    const ts = [];
    for (const k of this.recorded) ts.push(sig[k][0][0], sig[k][sig[k].length - 1][0]);
    for (const sp of D.spans) ts.push(sp.t0, sp.t1);
    if (D.capture_id) ts.push(0);
    this.has = ts.length > 0;
    const lo = this.has ? Math.min(...ts) : -D.window.before_s, hi = this.has ? Math.max(...ts) : D.window.after_s;
    const pad = Math.max(0.02, (hi - lo) * 0.015);
    this.d0 = lo - pad; this.d1 = hi + pad;
    this.gate = (D.spike && D.spike.threshold) || 0.05;
    const fromHash = /[#&]t=(-?[\d.]+)/.exec(location.hash);
    this.t = clamp(fromHash ? Number(fromHash[1]) : D.spike ? D.spike.t : D.capture_id ? 0 : lo, lo, hi);
    this.lo = lo; this.hi = hi;
    this.playing = false; this.speed = 1; this.gain = 1;
    this.build();
    this.ro = new ResizeObserver(() => { if (Math.abs(this.host.clientWidth - this.w) > 8) this.layout(); });
    this.ro.observe(host);
  }

  // ── static structure ───────────────────────────────────────────────────────────────────────
  build() {
    const D = this.D, el = (this.el = {});
    const verdict = D.gate && D.gate.pass === false ? h('b', { className: 'rp-bad', text: 'REJECTED' }) : D.gate && D.gate.pass ? h('b', { text: 'PASSED' }) : null;
    el.head = h('header', { className: 'rp-head' },
      h('div', {},
        h('p', { className: 'rp-kicker', text: 'Robot session replay' }),
        h('h2', { className: 'rp-title' }, D.capture_id ? [h('span', { className: 'mono', text: D.capture_id }), ' ', verdict] : h('span', { className: 'mono', text: `${D.window.from.slice(11, 19)}Z → ${D.window.to.slice(11, 19)}Z` })),
        // who wrote what is on screen — a capture with a REAL Sentry trace can still carry scripted numbers
        D.capture_id && (!D.provenance || D.provenance.synthetic) ? h('p', { className: 'rp-synth' }, h('b', { text: 'SYNTHETIC' }),
          ` ${(D.provenance && D.provenance.why) || 'this server did not report who wrote this capture — treated as scripted'}${D.trace_url ? ' · its Sentry trace is real' : ''}`) : null,
        h('p', { className: 'rp-sub', text: 'Sentry replays a browser session. This replays the robot — rebuilt from telemetry, encoders and the trace, with no new instrumentation.' })),
      h('div', { className: 'rp-links' },
        D.capture_id && !this.opts.embedded ? h('a', { className: 'rp-btn', href: `/capture/${D.capture_id}`, text: 'open capture' }) : null,
        D.capture_id && this.opts.embedded ? h('a', { className: 'rp-btn', href: `/replay/${D.capture_id}`, text: 'full replay' }) : null,
        D.trace_url ? h('a', { className: 'rp-btn', href: D.trace_url, target: '_blank', rel: 'noopener', text: 'open in Sentry →' }) : null));

    el.stage = h('section', { className: 'rp-panel rp-stage', 'aria-label': 'The robot, replayed' });
    el.trace = h('section', { className: 'rp-panel rp-trace', 'aria-label': 'The Sentry trace of the same moment' });

    el.play = h('button', { type: 'button', className: 'rp-btn rp-play', 'aria-pressed': 'false', onclick: () => this.toggle(), text: '▶ play' });
    el.speed = h('button', { type: 'button', className: 'rp-btn', onclick: () => { this.speed = this.speed === 1 ? 0.25 : 1; el.speed.textContent = this.speed === 1 ? '1×' : '¼×'; }, text: '1×', title: 'playback speed' });
    el.clock = h('output', { className: 'rp-clock mono', 'aria-live': 'off' });
    el.controls = h('div', { className: 'rp-controls' }, el.play, el.speed,
      D.spike ? h('button', { type: 'button', className: 'rp-btn rp-warn', onclick: () => this.seek(D.spike.t, true), text: '⇤ the spike' }) : null,
      D.capture_id ? h('button', { type: 'button', className: 'rp-btn', onclick: () => this.seek(0, true), text: 'shutter' }) : null,
      el.clock);
    el.readout = h('dl', { className: 'rp-readout' });

    el.lanes = h('div', { className: 'rp-lanes' });
    el.range = h('input', { type: 'range', className: 'rp-range', min: this.lo, max: this.hi, step: 0.02, value: this.t,
      'aria-label': `Replay time, seconds from ${D.t0_is}`, oninput: (e) => this.seek(Number(e.target.value)) });
    el.notes = h('div', { className: 'rp-notes' });

    this.host.replaceChildren(el.head, h('div', { className: 'rp-cols' }, el.stage, el.trace), el.controls, el.readout, el.lanes, el.range, el.notes);
    this.notes();
    this.layout();
  }

  notes() {
    const D = this.D, missing = Object.entries(D.recorded).filter(([, n]) => !n).map(([k]) => k);
    const after = D.events.filter((e) => e.t > this.hi + 0.05);
    fill(this.el.notes,
      D.downsampled ? h('p', { className: 'rp-note rp-bad' }, h('b', { className: 'rp-bad', text: 'Downsampled: ' }), `${D.downsampled}.`) : null,
      missing.length ? h('p', { className: 'rp-note' }, h('b', { text: 'Not recorded in this window: ' }), missing.join(', '), '.') : null,
      after.length ? h('p', { className: 'rp-note' }, h('b', { text: 'After the window: ' }), after.map((e) => `${e.type}${e.capture_id ? ` ${e.capture_id}` : ''} at ${tLabel(e.t)}`).join(' · ')) : null,
      h('p', { className: 'rp-note' }, h('b', { text: 'What is measured, what is derived: ' }),
        'pitch, tilt_rate, currents and odom_residual are samples as recorded. The path is derived — wheel encoders integrated as differential-drive odometry — so it drifts; odom_residual is the robot’s own measure of that drift and is drawn as the circle of doubt, to scale.',
        D.anchors.length ? ` The path is ${D.path_info.origin}; any other capture pose in the window shows how far the integrated path had wandered from it.` : ` Not pinned to the room: ${D.anchors_reason}.`),
      h('p', { className: 'rp-note rp-src', text: `source: ${D.source} · ${D.t0_is} = ${D.t0_ts}${D.synthetic ? ' · synthetic capture' : ''}` }));
  }

  // ── everything that depends on width ───────────────────────────────────────────────────────
  layout() {
    this.w = this.host.clientWidth || 800;
    this.phone = this.w < 640;
    this.drawStage(); this.drawTrace(); this.drawLanes(); this.update();
  }

  drawStage() {
    const D = this.D, el = this.el, path = D.path;
    const box = el.stage; box.replaceChildren(h('h3', { className: 'rp-h', text: path ? 'The robot — path from the wheel encoders' : 'The robot — lean from pitch' }));
    const W = Math.max(260, (this.phone ? this.w : box.clientWidth || this.w * 0.56) - 2), sideW = path ? (this.phone ? W : Math.min(230, W * 0.36)) : this.phone ? W : Math.min(340, W * 0.45);
    const topW = path ? (this.phone ? W : W - sideW - 10) : 0, H = this.phone ? 210 : 300;
    const row = h('div', { className: 'rp-stagerow' });
    this.top = null;
    if (path) {
      const xs = path.map((p) => p[1]), ys = path.map((p) => p[2]), m = 0.32;          // margin = half a robot
      const x0 = Math.min(...xs) - m, x1 = Math.max(...xs) + m, y0 = Math.min(...ys) - m, y1 = Math.max(...ys) + m;
      const k = Math.min(topW / (x1 - x0), H / (y1 - y0));
      const ox = (topW - (x1 - x0) * k) / 2, oy = (H - (y1 - y0) * k) / 2;
      const X = (x) => ox + (x - x0) * k, Y = (y) => H - oy - (y - y0) * k;            // +y is up / to the robot’s left
      const svg = s('svg', { viewBox: `0 0 ${topW} ${H}`, width: topW, height: H, className: 'rp-top', role: 'img', 'aria-label': `Top-down path, ${D.path_info.distance_m} m travelled` });
      const step = [0.05, 0.1, 0.25, 0.5, 1, 2, 5].find((g) => g * k >= 34) || 5;
      for (let gx = Math.ceil(x0 / step) * step; gx <= x1; gx += step) svg.append(s('line', { x1: X(gx), x2: X(gx), y1: 0, y2: H, className: 'rp-grid' }));
      for (let gy = Math.ceil(y0 / step) * step; gy <= y1; gy += step) svg.append(s('line', { y1: Y(gy), y2: Y(gy), x1: 0, x2: topW, className: 'rp-grid' }));
      svg.append(s('line', { x1: 10, x2: 10 + step * k, y1: H - 10, y2: H - 10, className: 'rp-scale' }), s('text', { x: 10, y: H - 16, className: 'rp-tick', text: `${step} m` }));
      const pts = path.map((p) => `${X(p[1]).toFixed(1)},${Y(p[2]).toFixed(1)}`);
      const future = s('polyline', { points: pts.join(' '), className: 'rp-future' }), past = s('polyline', { className: 'rp-past' });
      svg.append(s('circle', { cx: X(path[0][1]), cy: Y(path[0][2]), r: 3, className: 'rp-origin' }), s('text', { x: X(path[0][1]) + 6, y: Y(path[0][2]) + 14, className: 'rp-tick', text: D.path_info.frame === 'room' ? 'start' : 'start (0, 0)' }), future, past);
      for (const a of D.anchors) {                          // where the robot REALLY was, per the stored capture pose
        svg.append(s('path', { d: `M${X(a.x) - 6} ${Y(a.y)}h12M${X(a.x)} ${Y(a.y) - 6}v12`, className: 'rp-anchor' }),
          s('text', { x: X(a.x) + 8, y: Y(a.y) - 8, className: 'rp-tick', text: a.pinned_here ? `${a.capture_id} · pinned here` : `${a.capture_id}${a.gap_m == null ? '' : ` · path is ${(a.gap_m * 1000).toFixed(0)} mm off`}` }));
      }
      const doubt = s('circle', { className: 'rp-doubt', r: 0 });
      const hw = (WHEEL_BASE / 2) * k, wl = WHEEL_R * k, bd = 0.09 * k;                 // body drawn 18 cm deep
      const bot = s('g', { className: 'rp-bot' },
        s('rect', { x: -bd, y: -hw, width: bd * 2, height: hw * 2, rx: 2 }),
        s('line', { x1: -wl, x2: wl, y1: -hw, y2: -hw, className: 'rp-wheel' }), s('line', { x1: -wl, x2: wl, y1: hw, y2: hw, className: 'rp-wheel' }),
        s('path', { d: `M${bd} 0 L${bd + Math.max(8, 0.12 * k)} 0`, className: 'rp-heading' }));
      svg.append(doubt, bot);
      this.top = { X, Y, k, pts, past, doubt, bot };
      row.append(svg);
    }
    // side view: a two-wheeled balancer seen from its left; the body line leans by pitch
    const sH = path && !this.phone ? H : this.phone ? 170 : 300, kk = (sH - 46) / (BODY_H + WHEEL_R * 2);
    const side = s('svg', { viewBox: `0 0 ${sideW} ${sH}`, width: sideW, height: sH, className: 'rp-side', role: 'img', 'aria-label': 'Side view: the body’s lean' });
    const cx = sideW / 2, gy = sH - 18, ay = gy - WHEEL_R * kk;
    const ghosts = s('g', {});
    const spoke = s('line', { className: 'rp-spoke' }), body = s('line', { className: 'rp-body' }), head = s('circle', { r: 5, className: 'rp-headdot' });
    side.append(s('line', { x1: 8, x2: sideW - 8, y1: gy, y2: gy, className: 'rp-ground' }), s('line', { x1: cx, x2: cx, y1: ay, y2: ay - BODY_H * kk, className: 'rp-plumb' }),
      ghosts, s('circle', { cx, cy: ay, r: WHEEL_R * kk, className: 'rp-wheelc' }), spoke, body, head);
    const gainBtn = h('button', { type: 'button', className: 'rp-btn rp-gain', onclick: () => { this.gain = this.gain === 1 ? 5 : 1; gainBtn.textContent = `lean drawn ×${this.gain}`; this.update(); }, text: `lean drawn ×${this.gain}`, title: 'exaggerate the drawn lean; the number stays true' });
    this.side = { cx, ay, kk, spoke, body, head, ghosts };
    row.append(h('div', { className: 'rp-sidebox' }, side, this.recorded.includes('pitch') ? gainBtn : h('p', { className: 'rp-why', text: 'pitch was not recorded in this window' })));
    box.append(row);
    if (!path) {                                         // the reason sits where the path would have been
      const near = D.path_info.nearest || [];
      row.append(h('div', { className: 'rp-nopath' }, h('p', { className: 'rp-why' }, h('b', { text: 'No path drawn. ' }), `${D.path_info.reason}.`),
        h('p', { className: 'rp-why', text: 'A path is wheel encoders integrated over time. Without them there is nothing to integrate, and this page does not draw a line it cannot derive.' }),
        near.length ? h('a', { className: 'rp-btn', href: near[0].href, text: `replay the nearest stretch with encoders →` }) : null));
    } else {
      el.pathline = h('p', { className: 'rp-why' }, `${D.path_info.distance_m} m travelled, turned ${signed(D.path_info.turned_rad * 180 / Math.PI, 1)}° · derived by ${D.path_info.method}${D.path_info.resets ? ` · ${D.path_info.resets} encoder reset(s) skipped` : ''}`);
      box.append(el.pathline);
    }
  }

  drawTrace() {
    const D = this.D, box = this.el.trace, spans = D.spans;
    box.replaceChildren(h('h3', { className: 'rp-h', text: 'Sentry trace — the same instant' }));
    this.tr = null;
    if (!spans.length) {
      box.append(h('p', { className: 'rp-why' }, h('b', { text: 'No spans: ' }), `${D.spans_reason}.`),
        h('p', { className: 'rp-why', text: 'With a real trace, the span that was running at the scrubber’s instant lights up here — one event, two views.' }));
      return;
    }
    const W = Math.max(240, (this.phone ? this.w : box.clientWidth || this.w * 0.42) - 2), rowH = this.phone ? 30 : 26, labelW = Math.min(170, W * 0.42);
    const a = Math.min(...spans.map((x) => x.t0)), b = Math.max(...spans.map((x) => x.t1)), x = lin(a, b, labelW, W - 6), H = spans.length * rowH + 22;
    const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, className: 'rp-wf', role: 'img', 'aria-label': `Trace waterfall, ${spans.length} spans` });
    const rows = spans.map((sp, i) => {
      const y = i * rowH + 4, g = s('g', { className: 'rp-span', tabindex: 0, role: 'button', 'aria-label': `${sp.op}, ${((sp.t1 - sp.t0) * 1000).toFixed(0)} ms — scrub to it`,
        onclick: () => this.seek(sp.t0 + (sp.t1 - sp.t0) / 2, true), onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this.seek(sp.t0 + (sp.t1 - sp.t0) / 2, true); } } });
      g.append(s('rect', { x: 0, y, width: W, height: rowH - 2, className: 'rp-hit' }),
        s('text', { x: 4 + sp.depth * 10, y: y + rowH / 2 + 4, className: 'rp-spanlabel', text: sp.op || sp.description || 'span' }),
        s('rect', { x: x(sp.t0), y: y + 4, width: Math.max(2, x(sp.t1) - x(sp.t0)), height: rowH - 10, className: 'rp-bar' }),
        s('text', { x: Math.min(W - 4, x(sp.t1) + 5), y: y + rowH / 2 + 4, className: 'rp-ms', 'text-anchor': x(sp.t1) + 60 > W ? 'end' : 'start', dx: x(sp.t1) + 60 > W ? -(x(sp.t1) - x(sp.t0)) - 10 : 0, text: `${((sp.t1 - sp.t0) * 1000).toFixed(0)} ms` }));
      svg.append(g);
      return g;
    });
    const cursor = s('line', { y1: 0, y2: H - 18, className: 'rp-cursor' });
    svg.append(cursor, s('text', { x: labelW, y: H - 4, className: 'rp-tick', text: tLabel(a) }), s('text', { x: W - 6, y: H - 4, className: 'rp-tick', 'text-anchor': 'end', text: tLabel(b) }));
    const now = h('p', { className: 'rp-now', 'aria-live': 'polite' });
    box.append(now, svg);
    if (!D.arm_spans.length) box.append(h('p', { className: 'rp-why', text: `Arm and drive: ${D.arm_spans_reason}.` }));
    this.tr = { a, b, x, rows, cursor, now };
  }

  drawLanes() {
    const D = this.D, host = this.el.lanes, W = this.w, x = (this.x = lin(this.d0, this.d1, 6, W - 6));
    const lanes = LANES.filter((l) => this.recorded.includes(l.key)), laneH = this.phone ? 56 : 68, trH = D.spans.length ? 34 : 0;
    // events: a labelled flag each, placed greedily on rows so no two labels collide; the accent only for what went wrong
    const evs = D.events.filter((e) => e.t >= this.d0 && e.t <= this.d1).map((e) => {
      const label = `${e.type}${e.derived ? ' (derived)' : ''}`, ex = x(e.t), wpx = label.length * 6.7 + 10, right = ex + wpx > W - 4;
      return { e, label, ex, right, x0: right ? ex - wpx : ex, x1: right ? ex : ex + wpx };
    });
    const rowsUsed = [];
    for (const f of evs) {
      let r = 0;
      while ((rowsUsed[r] || []).some((g) => f.x0 < g.x1 && g.x0 < f.x1)) r++;
      (rowsUsed[r] = rowsUsed[r] || []).push(f); f.row = r;
    }
    const evH = Math.max(1, rowsUsed.length) * 14 + 12;
    const H = evH + trH + lanes.length * laneH + 20;
    const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, className: 'rp-tl', role: 'img', 'aria-label': 'Telemetry lanes on the replay clock. Drag to scrub.' });
    // time grid
    const span = this.d1 - this.d0, step = [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10].find((g) => (span / g) * (this.phone ? 64 : 80) <= W) || 10;
    for (let t = Math.ceil(this.d0 / step) * step; t <= this.d1 + 1e-9; t += step) {
      const tt = Math.round(t * 1000) / 1000;
      svg.append(s('line', { x1: x(tt), x2: x(tt), y1: evH, y2: H - 18, className: 'rp-grid' }), s('text', { x: x(tt), y: H - 4, 'text-anchor': 'middle', className: 'rp-tick', text: tt === 0 && D.capture_id ? 'shutter' : `${signed(tt, step < 1 ? 2 : 0)} s` }));
    }
    for (const f of evs) {
      const e = f.e, bad = e.type === 'tilt_spike' || e.outcome === 'rejected' || /fail|reject|fell|slip/.test(e.type || '');
      svg.append(s('g', { className: `rp-ev${bad ? ' bad' : ''}` }, s('title', { text: `${f.label} · ${tLabel(e.t)}${e.message ? ` — ${e.message}` : ''}` }),
        s('line', { x1: f.ex, x2: f.ex, y1: f.row * 14 + 3, y2: H - 18 }), s('text', { x: f.ex + (f.right ? -5 : 5), y: f.row * 14 + 12, 'text-anchor': f.right ? 'end' : 'start', text: f.label })));
    }
    let y = evH;
    if (trH) {                                           // the trace, on the lanes’ clock: tiny, and that is the truth of it
      for (const sp of D.spans) svg.append(s('rect', { x: x(sp.t0), y: y + 16 + Math.min(sp.depth, 1) * 8, width: Math.max(1.5, x(sp.t1) - x(sp.t0)), height: 6, className: sp.depth ? 'rp-tbar' : 'rp-tbar root' }));
      svg.append(s('line', { x1: 0, x2: W, y1: y + 0.5, y2: y + 0.5, className: 'rp-lanerule' }), s('text', { x: 8, y: y + 12, className: 'rp-lanelabel', text: 'sentry trace — every span, on this clock' }));
      y += trH;
    }
    this.dots = [];
    for (const lane of lanes) {
      const a = D.signals[lane.key], b = lane.with ? D.signals[lane.with] || [] : [], all = a.concat(b).map((p) => p[1]);
      let lo = Math.min(...all), hi = Math.max(...all);
      const mags = all.map(Math.abs).sort((p, q) => p - q), p99 = mags[Math.floor(mags.length * 0.99)] * 1.25;
      const clipAt = mags.length > 50 && mags[mags.length - 1] > p99 * 1.6 ? p99 : null;      // off-scale samples get a marker, not the whole lane
      if (clipAt != null) { lo = Math.max(lo, -clipAt); hi = Math.min(hi, clipAt); }
      if (lane.gate) { lo = Math.min(lo, -this.gate * 1.4); hi = Math.max(hi, this.gate * 1.4); }
      if (lane.symmetric) { const m = Math.max(Math.abs(lo), Math.abs(hi)); lo = -m; hi = m; }
      if (lane.zero) lo = Math.min(lo, 0);
      if (hi - lo < 1e-9) hi = lo + 1;
      const yTop = y + 16, yBot = y + laneH - 8, raw = lin(lo, hi, yBot, yTop), Y = (v) => clamp(raw(v), yTop, yBot), g = s('g', { className: 'rp-lane' });
      g.append(s('line', { x1: 0, x2: W, y1: y + 0.5, y2: y + 0.5, className: 'rp-lanerule' }));
      if (lo < 0) g.append(s('line', { x1: 6, x2: W - 6, y1: Y(0), y2: Y(0), className: 'rp-zero' }));
      if (lane.gate) {
        g.append(s('rect', { x: 6, width: W - 12, y: Y(this.gate), height: Y(-this.gate) - Y(this.gate), className: 'rp-gateband' }),
          s('text', { x: W - 8, y: Y(this.gate) - 3, 'text-anchor': 'end', className: 'rp-tick', text: `gate ±${this.gate} rad/s` }));
      }
      const line = (pts, cls) => s('polyline', { className: cls, points: pts.map((p, i) => (lane.step && i ? `${x(p[0]).toFixed(1)},${Y(pts[i - 1][1]).toFixed(1)} ` : '') + `${x(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(' ') });
      g.append(line(a, 'rp-line'));
      if (b.length) g.append(line(b, 'rp-line two'));
      if (lane.gate) {                                   // what crossed the gate, in the accent
        let run = [];
        const flush = () => { if (run.length) g.append(s('polyline', { className: 'rp-line over', points: run.map((p) => `${x(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(' ') })); run = []; };
        a.forEach((p, i) => { if (Math.abs(p[1]) >= this.gate) { if (!run.length && i) run.push(a[i - 1]); run.push(p); } else if (run.length) { run.push(p); flush(); } });
        flush();
      }
      if (clipAt != null) {                              // every clipped sample gets a marker; labels only where they fit
        const off = a.concat(b).filter((p) => Math.abs(p[1]) > clipAt).sort((p, q) => Math.abs(q[1]) - Math.abs(p[1])), taken = [];
        for (const p of off) {
          const up = p[1] > 0, px = x(p[0]), py = up ? yTop : yBot, right = px > W - 150, cls = lane.gate ? 'rp-off bad' : 'rp-off';
          g.append(s('path', { d: up ? `M${px - 4} ${py + 6}L${px} ${py - 1}L${px + 4} ${py + 6}z` : `M${px - 4} ${py - 6}L${px} ${py + 1}L${px + 4} ${py - 6}z`, className: cls }));
          if (taken.some((q) => Math.abs(q - px) < 150)) continue;
          taken.push(px);
          g.append(s('text', { x: px + (right ? -7 : 7), y: up ? py + 8 : py - 2, 'text-anchor': right ? 'end' : 'start', className: `${cls} rp-offlabel`, text: `off scale ${signed(p[1], lane.digits)} ${lane.unit}` }));
        }
      }
      const dot = s('circle', { r: 3.5, className: 'rp-dot' }), val = s('text', { className: 'rp-val', y: y + 12 });
      g.append(s('text', { x: 8, y: y + 12, className: 'rp-lanelabel', text: this.phone && lane.short ? lane.short : lane.label }), dot, val);
      svg.append(g);
      this.dots.push({ lane, series: a, Y, dot, val });
      y += laneH;
    }
    this.cursor = s('g', { className: 'rp-scrub' }, s('line', { y1: 0, y2: H - 18 }), s('path', { d: 'M-6 0h12l-6 8z' }));
    svg.append(this.cursor);
    // drag anywhere; vertical swipes still scroll the page on a phone (touch-action: pan-y in the css)
    const at = (e) => { const r = svg.getBoundingClientRect(); return clamp(this.d0 + ((e.clientX - r.left) / r.width) * (this.d1 - this.d0), this.lo, this.hi); };
    svg.addEventListener('pointerdown', (e) => { this.pause(); svg.setPointerCapture(e.pointerId); this.dragging = true; this.seek(at(e)); });
    svg.addEventListener('pointermove', (e) => { if (this.dragging) this.seek(at(e)); });
    const end = () => { if (this.dragging) { this.dragging = false; this.remember(); } };
    svg.addEventListener('pointerup', end); svg.addEventListener('pointercancel', end);
    host.replaceChildren(this.has ? svg : h('p', { className: 'rp-why', text: 'No telemetry was recorded in this window, so there is nothing to scrub.' }));
  }

  // ── the clock ──────────────────────────────────────────────────────────────────────────────
  seek(t, remember) {
    this.t = clamp(t, this.lo, this.hi);
    if (!this.raf) this.raf = requestAnimationFrame(() => { this.raf = 0; this.update(); });
    if (remember) this.remember();
  }
  remember() { try { history.replaceState(null, '', `#t=${this.t.toFixed(2)}`); } catch { /* sandboxed frame */ } }
  toggle() { if (this.playing) this.pause(); else this.start(); }
  pause() { this.playing = false; this.el.play.textContent = '▶ play'; this.el.play.setAttribute('aria-pressed', 'false'); }
  start() {
    if (this.t >= this.hi - 0.01) this.t = this.lo;
    this.playing = true; this.el.play.textContent = '❚❚ pause'; this.el.play.setAttribute('aria-pressed', 'true');
    let last = performance.now();
    const tick = (now) => {
      if (!this.playing) return;
      this.t += ((now - last) / 1000) * this.speed; last = now;
      if (this.t >= this.hi) { this.t = this.hi; this.update(); this.pause(); this.remember(); return; }
      this.update(); requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  update() {
    const D = this.D, t = this.t, el = this.el, sig = D.signals;
    if (this.cursor && this.x) this.cursor.setAttribute('transform', `translate(${this.x(t).toFixed(1)} 0)`);
    el.range.value = t;
    for (const d of this.dots || []) {
      const i = nearestIndex(d.series, t), p = d.series[i], over = Boolean(d.lane.gate && Math.abs(p[1]) >= this.gate), px = this.x(p[0]);
      d.dot.setAttribute('cx', px); d.dot.setAttribute('cy', d.Y(p[1])); d.dot.classList.toggle('bad', over);
      d.val.textContent = `${signed(p[1], d.lane.digits)} ${d.lane.unit}`; d.val.classList.toggle('bad', over);
      d.val.setAttribute('x', this.w - 8); d.val.setAttribute('text-anchor', 'end');
    }
    // the robot, from above
    const pitch = valueAt(sig.pitch, t), drift = valueAt(D.drift, t);
    let pose = null;
    if (this.top) {
      const i = nearestIndex(D.path, t), T = this.top; pose = D.path[i];
      T.past.setAttribute('points', T.pts.slice(0, i + 1).join(' '));
      T.bot.setAttribute('transform', `translate(${T.X(pose[1]).toFixed(1)} ${T.Y(pose[2]).toFixed(1)}) rotate(${(-pose[3] * 180 / Math.PI).toFixed(2)})`);
      T.doubt.setAttribute('cx', T.X(pose[1])); T.doubt.setAttribute('cy', T.Y(pose[2])); T.doubt.setAttribute('r', drift == null ? 0 : Math.max(0, drift * T.k));
    }
    // …and from the side
    const S = this.side, lean = (pitch || 0) * this.gain, tip = (a) => [S.cx + Math.sin(a) * BODY_H * S.kk, S.ay - Math.cos(a) * BODY_H * S.kk];
    const [hx, hy] = tip(lean);
    for (const [k, v] of Object.entries({ x1: S.cx, y1: S.ay, x2: hx, y2: hy })) S.body.setAttribute(k, v);
    S.head.setAttribute('cx', hx); S.head.setAttribute('cy', hy);
    S.body.style.visibility = S.head.style.visibility = pitch == null ? 'hidden' : 'visible';
    const tilt = valueAt(sig.tilt_rate, t), over = tilt != null && Math.abs(tilt) >= this.gate;
    S.body.classList.toggle('bad', over); S.head.classList.toggle('bad', over);
    S.ghosts.replaceChildren();
    if (sig.pitch && sig.pitch.length) {                 // the last half second of lean, fading: motion you can see while paused
      const i = nearestIndex(sig.pitch, t);
      for (let n = 1; n <= 8; n++) { const p = sig.pitch[i - n * 3]; if (!p) break; const [gx, gy] = tip(p[1] * this.gain); S.ghosts.append(s('line', { x1: S.cx, y1: S.ay, x2: gx, y2: gy, className: 'rp-ghost', opacity: (0.5 - n * 0.055).toFixed(2) })); }
    }
    const turns = sig.left_enc && sig.right_enc ? (valueAt(sig.left_enc, t) + valueAt(sig.right_enc, t)) / 2 : null;
    S.spoke.style.visibility = turns == null ? 'hidden' : 'visible';
    if (turns != null) { const a = turns * 2 * Math.PI, r = WHEEL_R * S.kk; for (const [k, v] of Object.entries({ x1: S.cx, y1: S.ay, x2: S.cx + Math.sin(a) * r, y2: S.ay - Math.cos(a) * r })) S.spoke.setAttribute(k, v); }
    // the trace, at this instant
    if (this.tr) {
      const T = this.tr, live = D.spans.filter((sp) => t >= sp.t0 && t <= sp.t1);
      T.rows.forEach((g, i) => g.classList.toggle('on', live.includes(D.spans[i])));
      const inside = t >= T.a && t <= T.b;
      T.cursor.style.visibility = inside ? 'visible' : 'hidden';
      if (inside) { T.cursor.setAttribute('x1', T.x(t)); T.cursor.setAttribute('x2', T.x(t)); }
      const deepest = live.length ? live.reduce((m, sp) => (sp.depth >= m.depth ? sp : m)) : null;
      fill(T.now, h('span', { className: 'mono', text: `at ${tLabel(t)}: ` }), deepest ? [h('b', { text: deepest.op || 'span' }), deepest.description ? ` — ${deepest.description}` : '']
        : t < T.a ? `the trace has not begun (it starts ${tLabel(T.a)}; ${((T.a - t)).toFixed(2)} s from here)` : t > T.b ? `the trace has ended (at ${tLabel(T.b)})` : 'between spans');
    }
    // the numbers
    const wall = new Date(new Date(D.t0_ts).getTime() + t * 1000).toISOString().slice(11, 23);
    fill(el.clock, h('b', { text: tLabel(t) }), ` from ${D.t0_is} · ${wall}Z`);
    const cell = (k, v, bad, wide) => h('div', { className: wide ? 'rp-cell wide' : 'rp-cell' }, h('dt', { text: k }), h('dd', { className: bad ? 'mono rp-bad' : 'mono', text: v }));
    fill(el.readout, [
      pitch == null ? null : cell('pitch', `${signed(pitch * 180 / Math.PI, 2)}° · ${signed(pitch, 3)} rad`),
      tilt == null ? null : cell('tilt_rate', `${signed(tilt, 3)} rad/s${over ? ' — over the gate' : ''}`, over),
      drift == null ? null : cell('circle of doubt', `${(drift * 1000).toFixed(1)} mm`),
      pose ? cell('pose (derived)', `x ${signed(pose[1], 3)} m · y ${signed(pose[2], 3)} m · yaw ${signed(pose[3] * 180 / Math.PI, 1)}°`, false, true) : null,
      sig.balanced && sig.balanced.length ? cell('balanced', valueAt(sig.balanced, t) ? 'yes' : 'NO', !valueAt(sig.balanced, t)) : null,
    ]);
  }

  dispose() { this.pause(); this.ro.disconnect(); this.host.replaceChildren(); }
}
