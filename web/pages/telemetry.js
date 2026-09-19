// telemetry.js — /telemetry. One question per row: can this capture be trusted, and if not, why?
//
// Layout: Seer's band on top (a three.js character, pages/seer/seer.js — strictly optional), then a
// black-and-white ledger. Ink on dark, hairline rules, hatch and halftone for bands; the ONE accent
// (orange) appears only on what is wrong. PASS / REJECTED always carry a glyph and a word.
//
// Everything drawn comes from GET /api/telemetry/board. A value the pipeline did not record is
// "not recorded"; the live strip never animates a fake line; a Sentry link exists only when the
// document carries a real trace. Each row is a doorway into Sentry's products, keyed by the tags
// obs.capture_scope() puts on every span, log and issue: capture_id (and commit_sha).

const SVG = 'http://www.w3.org/2000/svg';
const INK = '#efece6', DIM = '#aaa69f', FAINT = '#77736d', WRONG = '#f2a03c', GROUND = '#060608';

function h(tag, attrs = {}, ...kids) {
  const el = tag === 'svg' || attrs.ns ? document.createElementNS(SVG, tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'ns' || v == null || v === false) continue;
    if (k === 'class') el.setAttribute('class', v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(3)) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : String(kid));
  return el;
}
const s = (tag, attrs = {}, ...kids) => h(tag, { ...attrs, ns: 1 }, ...kids);
const $ = (id) => document.getElementById(id);
const fill = (el, ...kids) => el.replaceChildren(...kids.flat(3).filter((k) => k != null && k !== false));
const fix = (v, n) => (v == null ? '—' : Number(v).toFixed(n));
const short = (sha) => (sha ? sha.slice(0, 7) : null);
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

function makeTip(host) {
  const el = h('div', { class: 'tip', hidden: true, role: 'tooltip' });
  host.append(el);
  return {
    show(target, lines) {
      el.replaceChildren(...lines.map((l, i) => h('div', i ? {} : { style: 'font-weight:600' }, l)));
      const a = target.getBoundingClientRect(), b = host.getBoundingClientRect();
      el.hidden = false;
      const half = el.offsetWidth / 2, cx = a.left + a.width / 2 - b.left;
      el.style.left = `${Math.min(Math.max(cx, half + 2), b.width - half - 2)}px`;
      el.style.top = `${a.top - b.top - 6}px`;
    },
    hide() { el.hidden = true; },
  };
}

// ---- one strip: a single series, one y axis, x = ms from the shutter ---------------------------
// tilt_rate and odom_residual have different units, so they are two small multiples sharing the
// x axis — never one chart with two y scales. Bands are printed, not coloured: the gate's safe
// band is a halftone, the ±100 ms latch window a hatch.
let uid = 0;
function strip(name, unit, pts, tel, opts) {
  const W = opts.width, H = opts.last ? 112 : 92, L = 46, R = 10, T = 10, B = opts.last ? 26 : 8, win = tel.window_s * 1000, id = ++uid;
  const vals = pts.map((p) => p[1]);
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  if (opts.threshold) { hi = Math.max(hi, opts.threshold * 1.25); if (lo < 0) lo = Math.min(lo, -opts.threshold * 1.25); }
  const pad = (hi - lo) * 0.12 || 1; hi += pad; if (lo < 0) lo -= pad;
  const x = (ms) => L + ((ms + win) / (2 * win)) * (W - L - R), y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img',
    'aria-label': `${name}, ${unit}, from ${-win} to +${win} milliseconds around the shutter${opts.spike ? `; peak ${fix(opts.spike.value, 3)} at ${opts.spike.at_ms} ms` : ''}` });
  svg.append(s('defs', {},
    s('pattern', { id: `hatch${id}`, width: 6, height: 6, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' }, s('line', { x1: 0, y1: 0, x2: 0, y2: 6, stroke: INK, 'stroke-width': 1, opacity: 0.28 })),
    s('pattern', { id: `dots${id}`, width: 5, height: 5, patternUnits: 'userSpaceOnUse' }, s('circle', { cx: 1.2, cy: 1.2, r: 0.7, fill: INK, opacity: 0.22 }))));
  if (opts.threshold) {                                    // the gate's safe band: |v| < threshold
    const top = y(opts.threshold), bot = y(lo < 0 ? -opts.threshold : 0);
    svg.append(s('rect', { x: L, y: top, width: W - L - R, height: bot - top, fill: `url(#dots${id})` }));
  }
  svg.append(s('rect', { x: x(-tel.latch_ms), y: T, width: x(tel.latch_ms) - x(-tel.latch_ms), height: H - T - B, fill: `url(#hatch${id})` }));   // the latch window
  const fmt = (v) => String(Number(v.toPrecision(2)));
  const ticks = new Set([0]);
  if (opts.threshold) { ticks.add(opts.threshold); if (lo < 0) ticks.add(-opts.threshold); } else ticks.add(Math.max(...vals));
  for (const v of ticks) {
    svg.append(s('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: v === 0 ? '#3a3a44' : '#26262e', 'stroke-width': 1 }),
      s('text', { class: 't', x: L - 6, y: y(v) + 4, 'text-anchor': 'end' }, fmt(v)));
  }
  if (opts.threshold) for (const sign of lo < 0 ? [1, -1] : [1]) {
    svg.append(s('line', { x1: L, x2: W - R, y1: y(sign * opts.threshold), y2: y(sign * opts.threshold), stroke: DIM, 'stroke-width': 1, 'stroke-dasharray': '5 5' }));
  }
  if (opts.last) {
    for (const ms of [-2000, -1000, 1000, 2000].filter((m) => Math.abs(m) <= win)) {
      svg.append(s('text', { class: 't', x: x(ms), y: H - 7, 'text-anchor': ms === -win ? 'start' : ms === win ? 'end' : 'middle' }, `${ms > 0 ? '+' : ''}${ms / 1000} s`));
    }
    svg.append(s('text', { class: 'tl', x: x(0), y: H - 7, 'text-anchor': 'middle' }, 'shutter'));
  }
  svg.append(s('line', { x1: x(0), x2: x(0), y1: T - 3, y2: H - B + 3, stroke: INK, 'stroke-width': 1.5 }),
    s('path', { d: pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join(''), fill: 'none', stroke: INK, 'stroke-width': 2, 'stroke-linejoin': 'round' }));
  let spikeEl = null;
  const sp = opts.spike;
  if (sp) {
    const left = x(sp.at_ms) > W * 0.42, when = sp.at_ms < 0 ? 'before' : 'after';
    spikeEl = s('circle', { class: 'spike', cx: x(sp.at_ms), cy: y(sp.value), r: 6, fill: WRONG, stroke: GROUND, 'stroke-width': 2 });
    svg.append(spikeEl, s('text', { class: 'tw', x: x(sp.at_ms) + (left ? -11 : 11), y: y(sp.value) + 4, 'text-anchor': left ? 'end' : 'start' },
      `${fix(sp.value, 3)} · ${Math.abs(sp.at_ms)} ms ${when}`));
  }
  const cross = s('line', { y1: T, y2: H - B, stroke: DIM, 'stroke-width': 1, visibility: 'hidden' });
  const dot = s('circle', { r: 4.5, fill: INK, stroke: GROUND, 'stroke-width': 2, visibility: 'hidden' });
  svg.append(cross, dot);
  return { svg, x, y, cross, dot, L, R, W, pts, name, unit, spikeEl };
}

function charts(c, width) {
  const t = c.telemetry;
  if (!t.recorded) {
    return { node: h('p', { class: 'none' }, `Telemetry was not recorded for this capture: robot-telemetry has no samples within ±${t.window_s} s of the shutter.`), spikeEl: null };
  }
  const wrap = h('div', { class: 'strips' }), tip = makeTip(wrap), strips = [];
  const order = [['tilt_rate', c.gate.thresholds.tilt_rate_max.value], ['odom_residual', null]].filter(([n]) => (t.signals[n] || []).length);
  order.forEach(([name, threshold], i) => {
    const unit = t.units[name] || '';
    const st = strip(name, unit, t.signals[name], t, { width, threshold, last: i === order.length - 1, spike: t.spike && t.spike.signal === name ? t.spike : null });
    strips.push(st);
    wrap.append(h('div', { class: 'strip' }, h('h3', {}, `${name} · ${unit}`), st.svg));
  });
  // one crosshair across both strips, snapped to the nearest REAL sample (never interpolated)
  const move = (ev, st) => {
    const b = st.svg.getBoundingClientRect(), vx = ((ev.clientX - b.left) / b.width) * st.W;
    const ms = ((vx - st.L) / (st.W - st.L - st.R)) * 2 * t.window_s * 1000 - t.window_s * 1000;
    const lines = [];
    for (const o of strips) {
      const p = o.pts.reduce((a, q) => (Math.abs(q[0] - ms) < Math.abs(a[0] - ms) ? q : a));
      o.cross.setAttribute('x1', o.x(p[0])); o.cross.setAttribute('x2', o.x(p[0])); o.cross.setAttribute('visibility', 'visible');
      o.dot.setAttribute('cx', o.x(p[0])); o.dot.setAttribute('cy', o.y(p[1])); o.dot.setAttribute('visibility', 'visible');
      if (!lines.length) lines.push(`${p[0] > 0 ? '+' : ''}${p[0]} ms`);
      lines.push(`${o.name} ${fix(p[1], 4)} ${o.unit}`);
    }
    tip.show(strips[0].dot, lines);
  };
  const leave = () => { strips.forEach((o) => { o.cross.setAttribute('visibility', 'hidden'); o.dot.setAttribute('visibility', 'hidden'); }); tip.hide(); };
  strips.forEach((st) => { st.svg.addEventListener('pointermove', (e) => move(e, st)); st.svg.addEventListener('pointerleave', leave); });

  const rows = (strips[0] ? strips[0].pts : []).filter((p, i) => i % 10 === 0 || (t.spike && p[0] === t.spike.at_ms));
  wrap.append(h('details', {}, h('summary', {}, 'samples as a table'),
    h('div', { class: 'tablewrap' }, h('table', {},
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'ms from shutter'), ...strips.map((o) => h('th', { scope: 'col', class: 'num' }, `${o.name} (${o.unit})`)))),
      h('tbody', { class: 'plain' }, ...rows.map((p) => h('tr', {}, h('td', { class: 'mono' }, p[0]),
        ...strips.map((o) => { const q = o.pts.find((r) => r[0] === p[0]); return h('td', { class: 'num' }, q ? fix(q[1], 4) : '—'); }))))))));
  return { node: wrap, spikeEl: (strips.find((o) => o.spikeEl) || {}).spikeEl || null };
}

// ---- the gate: three numbers, each with its limit and a pass / fail mark ----------------------------
function gateCells(g) {
  return h('div', { class: 'gate3' }, ...Object.keys(g.values).map((n) => {
    const v = g.values[n], rule = g.thresholds[n], bad = g.failing.includes(n);
    const shown = v == null ? null : n === 'skew_ms' ? [fix(v, 2), 'ms'] : n === 'tilt_rate_max' ? [fix(v, 3), 'rad/s'] : [fix(v * 100, 1), '%'];
    const limit = n === 'coverage' ? `${rule.value * 100} %` : `${rule.value} ${rule.unit}`;
    return h('div', { class: `gv${bad ? ' bad' : ''}${v == null ? ' na' : ''}`, 'data-gate': n },
      h('div', { class: 'name' }, n),
      h('div', { class: 'val' }, shown ? [shown[0], h('small', {}, shown[1])] : 'not recorded'),
      h('div', { class: 'rule' }, h('span', { class: 'mk', 'aria-hidden': 'true' }, v == null ? '—' : bad ? '✕' : '✓'), ` ${v == null ? 'no value' : bad ? 'FAILED' : 'ok'} · needs ${rule.op} ${limit}`));
  }));
}

// ---- the Sentry stack, per capture ---------------------------------------------------------------
const sentryCache = new Map();
function copyButton(text) {
  const b = h('button', { type: 'button', class: 'copy', 'aria-label': `Copy ${text}` }, 'copy');
  b.addEventListener('click', async (e) => {
    e.stopPropagation();
    try { await navigator.clipboard.writeText(text); b.textContent = 'copied'; } catch { b.textContent = 'select it'; }
    setTimeout(() => { b.textContent = 'copy'; }, 1400);
  });
  return b;
}
function waterfall(w) {
  if (!w || !w.stages.length) return h('p', { class: 'slot' }, 'The trace carries no timed spans.');
  const total = w.total_ms || Math.max(...w.stages.map((st) => st.start_ms + st.ms));
  return h('div', { class: 'wf', role: 'img', 'aria-label': `stage waterfall, ${fix(total / 1000, 1)} seconds in total` },
    ...w.stages.map((st) => h('div', { class: 'wfrow' },
      h('span', { class: 'wfname' }, st.stage),
      h('span', { class: 'wftrack' }, h('span', { class: 'wfbar', style: `left:${(st.start_ms / total) * 100}%;width:${Math.max(0.8, (st.ms / total) * 100)}%` })),
      h('span', { class: 'wfms' }, st.ms >= 1000 ? `${fix(st.ms / 1000, 1)} s` : `${fix(st.ms, 0)} ms`))),
    h('p', { class: 'slot' }, `${w.spans} spans · ${w.transactions} transaction${w.transactions === 1 ? '' : 's'} · ${fix(total / 1000, 1)} s end to end`));
}
function sentryColumn(c) {
  const sn = c.sentry, stack = DATA.sentry_stack;
  const wfSlot = h('div', { class: 'slotbox' }), issueSlot = h('div', { class: 'slotbox' });
  const paused = stack.paused ? `Sentry is paused${stack.until ? ` until ${stack.until}` : ''} — ` : null;
  const setSlots = (state) => {
    if (state && state.available) {
      fill(wfSlot, waterfall(state.waterfall));
      fill(issueSlot, state.issues.length ? h('ul', { class: 'issues' }, ...state.issues.map((i) => h('li', {},
        i.permalink ? h('a', { href: i.permalink, target: '_blank', rel: 'noopener' }, i.short_id || i.id) : (i.short_id || i.id), ' ', i.title || '', i.count ? h('small', {}, ` ×${i.count}`) : null)))
        : h('p', { class: 'slot' }, 'No issue is tagged with this capture — no robot failure was raised during it.'));
      return;
    }
    const why = state && state.error ? `${state.error}: ${state.detail}` : sn.trace
      ? (paused ? `${paused}loads from the trace when it is back` : (stack.configured ? 'select this row to load it' : stack.reason))
      : (sn.trace_id ? 'synthetic capture — nothing in Sentry carries its tags' : 'no sentry_trace_id on its documents');
    fill(wfSlot, h('p', { class: 'slot' }, why));
    fill(issueSlot, h('p', { class: 'slot' }, sn.trace ? (paused ? `${paused}issues tagged ${sn.search} list here` : 'none loaded yet') : '—'));
  };
  setSlots(sentryCache.get(c.capture_id));
  const el = h('div', { class: 'sentrycol' },
    h('div', { class: 'srow' }, h('span', { class: 'sk' }, 'trace'),
      sn.trace ? h('a', { class: 'slink', href: sn.trace, target: '_blank', rel: 'noopener' }, 'open the waterfall →') : h('span', { class: 'snone' }, sn.trace_id ? 'synthetic trace — no link' : 'no trace id'),
      sn.trace_id ? h('code', { class: 'tid' }, `${sn.trace_id.slice(0, 12)}…`) : null),
    h('div', { class: 'srow' }, h('span', { class: 'sk' }, 'find it'), h('code', { class: 'q' }, sn.search), copyButton(sn.search),
      sn.tags.commit_sha ? [h('code', { class: 'q' }, `commit_sha:${short(sn.tags.commit_sha)}`)] : null),
    h('div', { class: 'srow col' }, h('span', { class: 'sk' }, 'stages'), wfSlot),
    h('div', { class: 'srow col' }, h('span', { class: 'sk' }, 'issues'), issueSlot),
    h('div', { class: 'srow' }, h('span', { class: 'sk' }, 'next'),
      sn.logs ? h('a', { class: 'slink', href: sn.logs, target: '_blank', rel: 'noopener' }, 'Logs') : h('span', { class: 'snone' }, 'Logs'),
      sn.issues ? h('a', { class: 'slink', href: sn.issues, target: '_blank', rel: 'noopener' }, 'Issues') : h('span', { class: 'snone' }, 'Issues'),
      sn.replays ? h('a', { class: 'slink', href: sn.replays, target: '_blank', rel: 'noopener' }, 'Session Replay') : h('span', { class: 'snone' }, 'Session Replay')));
  return { el, setSlots };
}
async function loadSentry(k) {
  const c = k.c, stack = DATA.sentry_stack;
  if (!c.sentry.trace || !stack.configured || sentryCache.has(c.capture_id)) return;
  sentryCache.set(c.capture_id, { loading: true });
  let state;
  try {
    const r = await fetch(`/api/telemetry/sentry/${encodeURIComponent(c.capture_id)}`, { headers: { accept: 'application/json' } });
    state = await r.json();
  } catch (e) { state = { error: 'unreachable', detail: e.message }; }
  sentryCache.set(c.capture_id, state);
  const now = cardsById.get(c.capture_id);
  if (now) now.setSlots(state);
}

// ---- a ledger row per capture -------------------------------------------------------------------------
let DATA = null, selectedId = null, seer = null, cardsById = new Map(), lastWidth = 0;

function ago(ts) {
  const sec = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
  if (sec < 90) return `${Math.round(sec)} s ago`;
  if (sec < 5400) return `${Math.round(sec / 60)} min ago`;
  if (sec < 129600) return `${Math.round(sec / 3600)} h ago`;
  return `${Math.round(sec / 86400)} d ago`;
}

function card(c, width) {
  const pass = c.gate.pass, word = pass === false ? 'REJECTED' : pass === true ? 'PASSED' : 'INCOMPLETE';
  const glyph = pass === false ? '✕' : pass === true ? '✓' : '—';
  const e = c.event;
  const verdict = pass === false
    ? (e && e.event_type === 'capture_rejected' ? `not committed — it would have moved ${e.moved} object${e.moved === 1 ? '' : 's'}` : 'rejected by the quality gate')
    : pass === true ? (c.commit_sha ? `committed ${short(c.commit_sha)}` : 'passed the gate') : `not recorded: ${c.gate.missing.join(', ')}`;
  const ch = charts(c, width), sc = sentryColumn(c);
  const el = h('article', { class: `row${pass === false ? ' bad' : ''}`, tabindex: 0, id: `card-${c.capture_id}`, 'aria-labelledby': `t-${c.capture_id}`,
    onpointerenter: () => select(c.capture_id), onfocusin: () => select(c.capture_id) },
    h('header', {},
      h('a', { class: 'capid mono', id: `t-${c.capture_id}`, href: `/capture/${encodeURIComponent(c.capture_id)}` }, c.capture_id),
      h('span', { class: 'verd' }, h('span', { 'aria-hidden': 'true' }, `${glyph} `), word),
      h('span', { class: 'because' }, verdict),
      c.retry ? h('a', { class: 'chip bad', href: `#card-${c.retry}` }, `retried as ${c.retry} ↑`) : null,
      c.retry_of ? h('a', { class: 'chip', href: `#card-${c.retry_of}` }, `retry of ${c.retry_of} ↓`) : null,
      c.synthetic ? h('span', { class: 'chip' }, 'synthetic') : null,
      h('span', { class: 'when mono', title: c.ts }, ago(c.ts)),
      h('a', { class: 'more', href: `/capture/${encodeURIComponent(c.capture_id)}` }, 'the evidence →')),
    h('div', { class: 'rowbody' },
      h('div', { class: 'c1' }, gateCells(c.gate)),
      h('div', { class: 'c2' }, ch.node),
      h('div', { class: 'c3' }, sc.el)));
  const failing = c.gate.failing[0] && el.querySelector(`[data-gate="${c.gate.failing[0]}"]`);
  return { el, spikeEl: ch.spikeEl, gateEl: el.querySelector('.gate3'), failEl: failing || null, c, setSlots: sc.setSlots };
}

function chartWidth() {
  const w = $('cards').clientWidth || 1100;
  return Math.max(280, Math.round(w >= 1180 ? w * 0.40 : w >= 760 ? w * 0.58 : w - 8));
}
function renderBoard() {
  const width = chartWidth(); lastWidth = width;
  cardsById = new Map();
  fill($('cards'), DATA.captures.map((c) => { const k = card(c, width); cardsById.set(c.capture_id, k); return k.el; }));
  if (selectedId && cardsById.has(selectedId)) mark(selectedId);
}
function mark(id) { for (const [cid, k] of cardsById) k.el.classList.toggle('sel', cid === id); }

// What Seer is told (docs/26-seer-embodied.md): idle | summoned | thinking | verdict | stumped. It is
// driven by the FAILURE ROWS, not by the ledger: a watcher earns its place by being pointed at a real
// failure and answering — or visibly giving up. The element only tells it which way to look.
let seerNow = { state: 'idle', el: null };
function tellSeer(state, el) {
  if (state) seerNow = { state, el: el || null };
  if (!seer) return;
  try { seer.react(seerNow.state, seerNow.el || undefined); } catch (e) { console.warn('[telemetry] Seer stopped reacting:', e); seer = null; }
}
function select(id) {
  if (id === selectedId) return;
  selectedId = id;
  mark(id);
  const k = cardsById.get(id);
  if (k) loadSentry(k);
}

function renderTop() {
  const sm = DATA.summary, th = sm.thresholds, stack = DATA.sentry_stack;
  fill($('tags'), DATA.source === 'fixture' ? h('span', { class: 'tag' }, 'fixture data — Elasticsearch is not being read; from fake/out/demo.ndjson') : null);
  const w = sm.worst_tilt_rate_max;
  const tile = (label, value, unit, note, bad, href) => h('div', { class: `tile${bad ? ' bad' : ''}` },
    h('div', { class: 'name' }, label), h('div', { class: 'val' }, value, unit ? h('small', {}, unit) : null),
    h('div', { class: 'rule' }, href ? h('a', { href }, note) : note));
  fill($('tiles'),
    tile('captures', String(sm.captures), null, `${sm.passed} passed the gate`),
    tile('rejected', String(sm.rejected), null, sm.rejected ? 'diffs that were NOT committed' : 'none so far', sm.rejected > 0),
    tile('worst tilt_rate_max', w ? fix(w.value, 3) : '—', w ? 'rad/s' : null, w ? `${w.capture_id} · needs < ${th.tilt_rate_max.value}` : 'not recorded', !!w && w.value >= th.tilt_rate_max.value, w ? `#card-${w.capture_id}` : null),
    tile('p95 skew_ms', sm.p95_skew_ms == null ? '—' : fix(sm.p95_skew_ms, 2), sm.p95_skew_ms == null ? null : 'ms', `${sm.skew_samples} captures · needs < ${th.skew_ms.value} ms`, sm.p95_skew_ms != null && sm.p95_skew_ms >= th.skew_ms.value));
  fill($('stack'),
    h('p', { class: `stackstate${stack.paused ? ' paused' : ''}` }, stack.paused
      ? `Sentry is paused${stack.until ? ` until ${stack.until}` : ''}: no call is made. Each row's stage waterfall and issues load from its trace when Sentry is back.`
      : stack.configured ? 'Sentry is connected: select a row to load its stage waterfall and the issues tagged with it.' : `${stack.reason}.`),
    h('dl', { class: 'products' },
      ...[['Tracing', 'the capture as one waterfall across the Pi and the laptop — sentry_trace_id is on every document'],
        ['Issues', 'a robot failure arrives as an issue with the last 2 s of tilt as breadcrumbs (obs.robot_failure)'],
        ['Logs', 'structured, tagged capture_id and camera — where to look when a stage is slow'],
        ['Session Replay', 'this dashboard, recorded — watch where a judge hesitated']].map(([k, v]) => [h('dt', {}, k), h('dd', {}, v)])));
  fill($('board-key'),
    h('span', { class: 'kitem' }, h('span', { class: 'sw hatch', 'aria-hidden': 'true' }), '±100 ms latch window the gate reads'),
    h('span', { class: 'kitem' }, h('span', { class: 'sw dots', 'aria-hidden': 'true' }), 'inside the gate (|tilt_rate| < 0.05 rad/s)'),
    h('span', { class: 'kitem' }, h('span', { class: 'sw shutter', 'aria-hidden': 'true' }), 'shutter'),
    h('span', { class: 'kitem' }, h('span', { class: 'sw spk', 'aria-hidden': 'true' }), 'tilt crossing the gate'));
}

// ---- the live strip: real frames or an honest nothing ---------------------------------------------
// The producer is telemetry/hub.py's SSESink: 2 Hz frames holding the LATEST sample of every signal
// plus `tilt_rate_peak`, the peak |tilt_rate| since the previous frame. At 2 Hz the latest sample
// almost never lands on a 20 ms knock, so the strip draws the PEAK against the gate line and shows
// the latest tilt_rate only as a number. Extra or missing keys are tolerated.
const live = { frames: [], lastAt: 0 };
const LIVE_KEEP = 120;                                     // 60 s at 2 Hz
const LIVE_GATE = 0.05;                                    // rad/s, the quality gate's tilt limit
function renderLive() {
  const host = $('live'), n = live.frames.length;
  if (!n) {
    fill(host, h('p', { class: 'slot big' }, 'No live telemetry source connected — the laptop’s telemetry hub (telemetry/hub.py) posts 2 Hz frames here. Nothing is simulated.'));
    return;
  }
  const num = (f, k) => (typeof f[k] === 'number' && Number.isFinite(f[k]) ? f[k] : null);
  const W = Math.max(300, host.clientWidth || 900), L = 118, R = 84, off = LIVE_KEEP - n;
  const x = (i) => L + (i / Math.max(1, LIVE_KEEP - 1)) * (W - L - R);
  const rows = [
    { key: 'tilt_rate_peak', label: 'tilt_rate peak', unit: 'rad/s', h: 58, gate: LIVE_GATE, zeroBased: true },
    { key: 'pitch', label: 'pitch', unit: 'rad', h: 36 },
    { key: 'odom_residual', label: 'odom_residual', unit: 'm', h: 36, zeroBased: true },
  ].filter((r) => live.frames.some((f) => num(f, r.key) != null));
  const H = rows.reduce((a, r) => a + r.h, 0) + 24;
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `live telemetry, last ${n} frames at 2 hertz` });
  let top = 0, crossed = 0;
  for (const r of rows) {
    const vals = live.frames.map((f) => num(f, r.key)), nums = vals.filter((v) => v != null);
    const hi = Math.max(1e-6, ...nums.map(Math.abs), r.gate ? r.gate * 1.4 : 0), lo = r.zeroBased ? 0 : -hi;
    const y0 = top + 6, y1 = top + r.h - 4, y = (v) => y1 - ((v - lo) / (hi - lo)) * (y1 - y0);
    const lastV = nums.length ? nums[nums.length - 1] : null;
    svg.append(s('text', { class: 't', x: 0, y: (y0 + y1) / 2 + 4 }, r.label), s('line', { x1: L, x2: W - R, y1: y(0), y2: y(0), stroke: '#26262e' }));
    if (r.gate) {                                          // the gate line, labelled inside the plot so it never meets the row label
      svg.append(s('line', { x1: L, x2: W - R, y1: y(r.gate), y2: y(r.gate), stroke: DIM, 'stroke-dasharray': '5 5' }),
        s('text', { class: 't', x: L + 4, y: y(r.gate) - 5 }, `gate ${r.gate} ${r.unit}`));
    }
    let d = '', pen = false;
    vals.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? 'L' : 'M'}${x(i + off).toFixed(1)} ${y(v).toFixed(1)}`; pen = true; });
    svg.append(s('path', { d, fill: 'none', stroke: INK, 'stroke-width': 2, 'stroke-linejoin': 'round' }));
    if (r.gate) vals.forEach((v, i) => { if (v != null && v >= r.gate) { crossed++; svg.append(s('circle', { cx: x(i + off), cy: y(v), r: 4.5, fill: WRONG, stroke: GROUND, 'stroke-width': 2 })); } });
    const over = r.gate && lastV != null && lastV >= r.gate;
    svg.append(s('text', { class: over ? 'tw' : 'tl', x: W - R + 8, y: (y0 + y1) / 2 + 4 }, lastV == null ? '—' : `${fix(lastV, r.key === 'odom_residual' ? 4 : 3)} ${r.unit}`));
    top += r.h;
  }
  const by = top + 4;                                       // balanced: ink while upright, the accent where it is not
  svg.append(s('text', { class: 't', x: 0, y: by + 10 }, 'balanced'));
  live.frames.forEach((f, i) => {
    if (typeof f.balanced !== 'boolean') return;
    svg.append(s('rect', { x: x(i + off) - 1, y: by, width: Math.max(2, (W - L - R) / LIVE_KEEP), height: 11, fill: f.balanced ? FAINT : WRONG }));
  });
  const last = live.frames[n - 1], tr = num(last, 'tilt_rate');
  fill(host, h('div', { class: 'strip' }, svg),
    h('p', { class: 'slot' }, `${n} frames · last ${last.ts || '—'} · tilt_rate now `, h('b', {}, tr == null ? 'not reported' : `${fix(tr, 3)} rad/s`),
      ' (the line is the PEAK between frames — a 20 ms knock falls between 2 Hz samples) · balanced: ',
      h('b', { class: last.balanced === false ? 'off' : '' }, last.balanced === false ? '✕ NO' : last.balanced === true ? '✓ yes' : 'not reported'),
      crossed ? h('b', { class: 'off' }, ` · ${crossed} frame${crossed === 1 ? '' : 's'} crossed the gate`) : null));
}
const liveState = (text) => { $('live-state').textContent = text; };
function listen() {
  if (!('EventSource' in window)) { liveState('live updates unsupported in this browser'); return; }
  const es = new EventSource('/api/events');
  es.onopen = () => liveState(live.frames.length ? 'receiving' : 'connected — waiting for a telemetry source');
  es.onerror = () => liveState('reconnecting…');
  es.addEventListener('telemetry', (ev) => {
    let f; try { f = JSON.parse(ev.data); } catch { return; }
    live.frames.push(f); if (live.frames.length > LIVE_KEEP) live.frames.shift();
    live.lastAt = Date.now();
    liveState('receiving');
    renderLive();
  });
  es.addEventListener('capture', () => load(false));       // a commit landed: the board has a new row
  setInterval(() => { if (live.lastAt && Date.now() - live.lastAt > 5000) liveState('the telemetry source went quiet'); }, 2000);
}

// ---- FAILURE ROWS: the Elastic evidence, the Sentry trace, the AI verdict — one row ---------------------
// [open capture] and [open trace] work with no Seer at all. [ask Seer] is wired end to end and never
// fakes a verdict: whatever comes back that is not an answer is printed verbatim as "stumped".
const answers = new Map();                                  // failure id -> outcome | { thinking: true }
const seenFailures = new Set();
const CFG_KEY = 'gitrl.seer.config';
function config() {
  let c = {};
  try { c = JSON.parse(localStorage.getItem(CFG_KEY) || '{}') || {}; } catch { /* defaults */ }
  return { auto: c.auto === true, severity: c.severity === 'all' ? 'all' : 'ops', depth: [10, 20, 40].includes(c.depth) ? c.depth : 40 };
}
function saveConfig(next) { try { localStorage.setItem(CFG_KEY, JSON.stringify(next)); } catch { /* this visit only */ } }
const severe = (f, cfg) => (cfg.severity === 'all' ? true : f.kind !== 'capture_rejected');

function answerBlock(f) {
  const a = answers.get(f.id);
  if (!a) return null;
  if (a.thinking) return h('p', { class: 'fans thinking' }, 'Seer is looking into it…');
  if (a.state === 'verdict') {
    return h('div', { class: 'fans verdict' }, h('b', {}, 'Seer: '), h('span', { class: 'vtext' }, a.verdict),
      a.issue && a.issue.permalink ? [' ', h('a', { href: a.issue.permalink, target: '_blank', rel: 'noopener' }, `${a.issue.short_id || 'the issue'} →`)] : null,
      a.verified === false ? h('small', {}, ' · read through an API shape not yet verified against the live Sentry') : null);
  }
  return h('p', { class: 'fans stumped' }, h('b', {}, 'Seer is stumped: '), a.reason || 'no reason given', a.error ? ` (${a.error})` : '');
}

async function askSeer(f, rowEl) {
  if ((answers.get(f.id) || {}).thinking) return;
  answers.set(f.id, { thinking: true });
  renderFailures(false);
  tellSeer('thinking', document.getElementById(`fail-${cssId(f.id)}`) || rowEl);
  let out;
  try {
    const r = await fetch('/api/seer/ask', { method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ capture_id: f.capture_id, context_depth: config().depth }) });
    out = await r.json();
    if (!r.ok) out = { state: 'stumped', reason: out.detail || r.statusText, error: out.error };
  } catch (e) { out = { state: 'stumped', reason: `this page could not reach its own server (${e.message})` }; }
  if (out.state !== 'verdict') out.state = 'stumped';      // anything that is not an answer is said out loud as not an answer
  answers.set(f.id, out);
  renderFailures(false);
  tellSeer(out.state, document.getElementById(`fail-${cssId(f.id)}`));
}
const cssId = (id) => id.replace(/[^a-zA-Z0-9_-]/g, '_');

function renderFailures(first) {
  const host = $('failures'), list = (DATA && DATA.failures) || [], seerInfo = (DATA && DATA.seer) || {}, cfg = config();
  if (!list.length) { fill(host, h('p', { class: 'slot' }, 'No rejected capture or failed operation on record.')); return; }
  fill(host, list.map((f) => {
    const tr = f.trace || {}, id = `fail-${cssId(f.id)}`;
    const pre = seerInfo.available ? (seerInfo.verified ? null : 'unverified endpoint') : `will answer: ${seerInfo.reason || 'unavailable'}`;
    const row = h('article', { class: 'fail', id, tabindex: 0,
      onpointerenter: (e) => { if (!answers.has(f.id)) tellSeer('summoned', e.currentTarget); },
      onfocusin: (e) => { if (!answers.has(f.id)) tellSeer('summoned', e.currentTarget); },
      onpointerleave: () => { if (seerNow.state === 'summoned') tellSeer('idle'); } },
      h('p', { class: 'fline' }, h('span', { class: 'warn', 'aria-hidden': 'true' }, '⚠ '), h('b', { class: 'kind' }, f.kind),
        f.capture_id ? [' · ', h('span', { class: 'mono' }, f.capture_id)] : null, f.ts ? [' · ', h('span', { title: f.ts }, ago(f.ts))] : null,
        f.detail ? h('span', { class: 'fdetail' }, ` — ${f.detail}`) : null),
      h('div', { class: 'fbtns' },
        f.capture_url ? h('a', { class: 'fbtn', href: f.capture_url }, 'open capture') : h('span', { class: 'fbtn off', 'aria-disabled': 'true' }, 'open capture'),
        tr.url ? h('a', { class: 'fbtn', href: tr.url, target: '_blank', rel: 'noopener' }, 'open trace') : h('span', { class: 'fbtn off', 'aria-disabled': 'true', title: tr.why_no_link || '' }, 'open trace'),
        f.capture_id ? h('button', { type: 'button', class: 'fbtn ask', onclick: (e) => askSeer(f, e.currentTarget.closest('.fail')) }, 'ask Seer') : null),
      h('p', { class: 'fnote' }, tr.url ? null : `no trace link: ${tr.why_no_link || 'not recorded'}`, tr.url || !pre ? null : ' · ', pre ? `Seer ${pre}` : null),
      h('div', { 'aria-live': 'polite' }, answerBlock(f)));
    return row;
  }));
  // auto-summon (default OFF): only for failures that ARRIVE while the page is open, never the backlog
  for (const f of list) {
    const fresh = !seenFailures.has(f.id);
    seenFailures.add(f.id);
    if (!first && fresh && cfg.auto && severe(f, cfg) && f.capture_id) askSeer(f, null);
  }
}

// config comes last, and invents nothing: no number is shown that Sentry did not give us
function renderConfig() {
  const cfg = config(), seerInfo = (DATA && DATA.seer) || {};
  const set = (patch) => { saveConfig({ ...config(), ...patch }); renderConfig(); };
  fill($('seer-config-body'),
    h('label', { class: 'cfg' }, h('input', { type: 'checkbox', checked: cfg.auto, onchange: (e) => set({ auto: e.target.checked }) }),
      h('span', {}, h('b', {}, 'auto-summon on failure'), ' — off by default: every summon spends Seer credits')),
    h('label', { class: 'cfg' }, h('span', {}, h('b', {}, 'severity threshold')),
      h('select', { onchange: (e) => set({ severity: e.target.value }) },
        h('option', { value: 'ops', selected: cfg.severity === 'ops' }, 'failed operations only (failed_op)'),
        h('option', { value: 'all', selected: cfg.severity === 'all' }, 'failed operations and rejected captures'))),
    h('label', { class: 'cfg' }, h('span', {}, h('b', {}, 'context depth'), ' — telemetry breadcrumbs attached to the question'),
      h('select', { onchange: (e) => set({ depth: Number(e.target.value) }) }, ...[10, 20, 40].map((n) => h('option', { value: n, selected: cfg.depth === n }, `${n} samples`)))),
    h('p', { class: 'cfg' }, h('b', {}, 'credits remaining: '), seerInfo.credits != null ? String(seerInfo.credits) : (seerInfo.credits_reason || 'unknown')));
}

// ---- Seer: optional, lives in its band ---------------------------------------------------------------
const SEER_KEY = 'gitrl.seer.hidden';
const canvas = $('seer'), toggle = $('seer-toggle'), band = $('stage');
let seerLoading = null;
function seerHidden() { try { return localStorage.getItem(SEER_KEY) === '1'; } catch { return false; } }
function paintToggle() {
  const hidden = seerHidden();
  toggle.setAttribute('aria-pressed', String(!hidden));
  toggle.textContent = hidden ? 'Seer: off' : 'Seer: on';
  band.classList.toggle('noseer', hidden || band.dataset.seer === 'missing');
}
async function mountSeerOnce() {
  if (seer || seerLoading || seerHidden()) return;
  seerLoading = (async () => {
    try {
      const mod = await import('/pages/seer/seer.js');
      seer = await mod.mountSeer(canvas, { reducedMotion: reduced });
      tellSeer();
    } catch (e) {
      console.info('[telemetry] Seer is not available; the board works without it.', e && e.message);
      band.dataset.seer = 'missing';
      toggle.hidden = true;
      paintToggle();
    }
  })();
  await seerLoading;
}
toggle.addEventListener('click', () => {
  const hide = !seerHidden();
  try { localStorage.setItem(SEER_KEY, hide ? '1' : '0'); } catch { /* private mode: the toggle still works for this visit */ }
  paintToggle();
  if (hide) { if (seer) { try { seer.react('idle'); if (seer.pause) seer.pause(); } catch { /* optional */ } } return; }
  if (seer && seer.resume) { try { seer.resume(); } catch { /* optional */ } }
  mountSeerOnce().then(() => tellSeer());
});

// ---- load ----------------------------------------------------------------------------------------
async function load(first) {
  try {
    const r = await fetch('/api/telemetry/board?limit=12', { headers: { accept: 'application/json' } });
    const body = await r.json();
    if (!r.ok) throw Object.assign(new Error(body.detail || r.statusText), body);
    DATA = body;
  } catch (e) {
    if (first) $('state').textContent = `The board could not be loaded: ${e.error ? `${e.error} — ` : ''}${e.message}`;
    return;
  }
  $('state').hidden = true; $('main').hidden = false;
  renderTop();
  renderBoard();
  if (first) renderLive();
  renderFailures(first);
  renderConfig();
  if (!DATA.captures.length) { fill($('cards'), h('p', { class: 'slot big' }, 'No captures recorded yet.')); return; }
  if (first || !cardsById.has(selectedId)) {
    // open on the story: the most recent REJECTED capture whose telemetry shows the spike; else the
    // most recent rejected; else the newest
    const caps = DATA.captures, fromHash = location.hash.startsWith('#card-') && location.hash.slice(6);
    const pick = (fromHash && caps.find((c) => c.capture_id === fromHash)) || caps.find((c) => c.gate.pass === false && c.telemetry.spike)
      || caps.find((c) => c.gate.pass === false) || caps[0];
    selectedId = null;
    select(pick.capture_id);
  }
}

addEventListener('resize', () => {
  if (!DATA) return;
  if (Math.abs(chartWidth() - lastWidth) > 24) renderBoard();
  if (live.frames.length) renderLive();
});

paintToggle();
mountSeerOnce();
load(true);
listen();
