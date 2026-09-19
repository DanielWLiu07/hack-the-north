
// WHY this page is showing fixture data, in words. /api/health makes no Elasticsearch call while the
// key is parked or missing, so asking costs nothing. "Unreachable" would be a lie when it is parked.
const FIXTURE_WHY = {
  elastic_paused: 'Elasticsearch is PARKED to save quota (no calls until it is unparked)',
  elastic_unconfigured: 'Elasticsearch is not configured on this server',
  elastic_unreachable: 'Elasticsearch is unreachable', elastic_timeout: 'Elasticsearch timed out',
};
async function explainFixture(tagEl) {
  try {
    const health = await (await fetch('/api/health', { cache: 'no-store' })).json();
    const why = FIXTURE_WHY[health && health.elastic && health.elastic.error];
    if (why) tagEl.textContent = `fixture data — ${why}; read from fake/out/demo.ndjson`;
  } catch { /* the neutral wording stays */ }
}
// capture.js — /capture/<capture_id>: "why was this diff wrong?" on one screen.
//
// Everything drawn here comes from GET /api/capture/<id>. A value the pipeline did not
// record is shown as "not recorded" — this page is evidence, it never fills a gap with
// a number. Orange marks the thing that is wrong and nothing else; the three cameras
// are told apart by SHAPE and label (circle / square / triangle), never by colour.

const SVG = 'http://www.w3.org/2000/svg';
const INK = '#efece6', DIM = '#aaa69f', FAINT = '#77736d', WRONG = '#f2a03c', PANEL = '#0e0e12';

function h(tag, attrs = {}, ...kids) {
  const el = tag === 'svg' || attrs.ns ? document.createElementNS(SVG, tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'ns' || v == null || v === false) continue;
    if (k === 'class') el.setAttribute('class', v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : String(kid));
  return el;
}
const s = (tag, attrs = {}, ...kids) => h(tag, { ...attrs, ns: 1 }, ...kids);
const $ = (id) => document.getElementById(id);
const fill = (el, ...kids) => el.replaceChildren(...kids.flat(3).filter((k) => k != null && k !== false));
const fix = (v, n) => (v == null ? '—' : Number(v).toFixed(n));
const mm = (v) => (v == null ? '—' : `${Number(v).toFixed(Math.abs(v) < 100 ? 1 : 0)} mm`);
const short = (sha) => (sha ? sha.slice(0, 7) : null);

// camera identity = a shape, in the order the capture lists its cameras
function marker(i, cx, cy, r, fill, ring = PANEL) {
  const a = { fill, stroke: ring, 'stroke-width': 2, 'paint-order': 'stroke' };
  if (i % 4 === 0) return s('circle', { cx, cy, r, ...a });
  if (i % 4 === 1) return s('rect', { x: cx - r * 0.9, y: cy - r * 0.9, width: r * 1.8, height: r * 1.8, ...a });
  if (i % 4 === 2) return s('path', { d: `M${cx} ${cy - r * 1.15}L${cx + r * 1.1} ${cy + r * 0.85}L${cx - r * 1.1} ${cy + r * 0.85}Z`, ...a });
  return s('path', { d: `M${cx} ${cy - r * 1.2}L${cx + r * 1.2} ${cy}L${cx} ${cy + r * 1.2}L${cx - r * 1.2} ${cy}Z`, ...a });
}
const glyph = (i, colour = INK) => s('svg', { viewBox: '0 0 18 18', width: 16, height: 16, 'aria-hidden': 'true' }, marker(i, 9, 9, 6, colour, 'none'));

const capId = decodeURIComponent(location.pathname.split('/').filter(Boolean).pop() || '');
let DATA = null, CAM_ORDER = [], selected = null, lastTelWidth = 0;

// ---- header ---------------------------------------------------------------------
function renderHeader(d) {
  document.title = `${d.capture_id} — capture — GITRL`;
  $('cap-id').textContent = d.capture_id;
  const when = new Date(d.ts);
  fill($('meta'), 
    h('span', {}, h('b', {}, d.ts.replace('T', ' ').replace(/\.000Z$/, 'Z'))),
    h('span', {}, `local ${when.toLocaleTimeString()}`),
    h('span', {}, 'commit ', h('b', {}, short(d.commit_sha) || 'none — not committed')),
    d.diff && d.diff.branch ? h('span', {}, 'branch ', h('b', {}, d.diff.branch)) : null,
    h('span', {}, `${d.cameras.length} cameras · ${d.objects.length} objects · ${d.rejected.length} rejected clusters`),
  );
  const link = (id, label) => (id ? h('a', { href: `/capture/${id}`, rel: label.includes('←') ? 'prev' : 'next' }, label) : h('span', {}, label));
  fill($('nav'), link(d.nav.prev, d.nav.prev ? `← ${d.nav.prev}` : '← first'), link(d.nav.next, d.nav.next ? `${d.nav.next} →` : 'latest →'));
  const tags = [];
  if (d.source === 'fixture') tags.push('fixture data — read from fake/out/demo.ndjson');
  const pv = d.provenance || { synthetic: d.synthetic, why: d.synthetic ? 'text scripted by fake/scene_gen' : null };
  if (pv.synthetic) tags.push({ synthetic: `SYNTHETIC — ${pv.why}` });
  fill($('tags'), ...tags.map((t) => (t.synthetic ? h('span', { class: 'tag synthetic', title: 'Real: the git commit and the pipeline that read this. Generated: descriptions, poses, per-camera noise, cloud numbers.' }, t.synthetic) : h('span', { class: 'tag' }, t))));
  if (d.source === 'fixture') explainFixture($('tags').firstElementChild);

  const g = d.gate, v = $('verdict');
  let word, because, bad = false;
  if (g.pass === false) {
    bad = true; word = 'REJECTED';
    because = g.failing.map((n) => [h('b', {}, `${n} ${fmtGate(n, g.values[n])}`), ` — the gate needs ${g.thresholds[n].op} ${g.thresholds[n].value}`]);
  } else if (g.pass === true) {
    word = 'PASSED';
    because = ['all three gate values inside their limits'];
    const noisy = d.suspect.reasons.filter((r) => r.kind === 'disagreement').length;
    if (noisy) { bad = true; because.push(' — but ', h('b', {}, `${noisy} committed move${noisy > 1 ? 's are' : ' is'} inside the camera noise`)); }
  } else {
    word = 'INCOMPLETE';
    because = [`${g.missing.join(', ')} not recorded — the gate cannot be evaluated`];
  }
  v.className = `verdict${bad ? ' bad' : ''}`;
  fill(v, h('span', { class: 'word' }, word), h('span', { class: 'because' }, ...because.flat()),
    d.nav.retry ? h('a', { href: `/capture/${d.nav.retry}` }, `retried as ${d.nav.retry} →`) : null,
    d.nav.retry_of ? h('a', { href: `/capture/${d.nav.retry_of}` }, `← retry of ${d.nav.retry_of} (rejected)`) : null);
  v.hidden = false;
}
function fmtGate(name, v) {
  if (v == null) return 'not recorded';
  return name === 'skew_ms' ? `${fix(v, 2)} ms` : name === 'tilt_rate_max' ? `${fix(v, 3)} rad/s` : `${fix(v * 100, 1)} %`;
}

// ---- the three cameras --------------------------------------------------------------
function markWords(text, unique) {
  const set = new Set(unique || []);
  return text.split(/([A-Za-z0-9']+)/).map((tok) => (set.has(tok.toLowerCase()) ? h('mark', {}, tok) : tok));
}
function renderCameras() {
  const d = DATA, o = d.objects.find((x) => x.object_id === selected);
  if (!o) { fill($('cams'), h('p', { class: 'none' }, 'No associated objects in this capture.')); return; }
  const dis = o.disagreement;
  const lede = [h('span', { class: 'mono' }, o.object_id), o.class ? ` — committed as “${o.class}”${o.zone ? ` on the ${o.zone}` : ''}. `
    : o.known_class ? ` — known from other commits as “${o.known_class}”${o.known_zone ? ` on the ${o.known_zone}` : ''}. ` : ' — never committed. '];
  if (o.labels_disagree) lede.push('The cameras could not even agree what it is: ', h('b', {}, o.labels.join(' · ')), '. ');
  lede.push('Underlined words are the ones only that camera used.');
  fill($('cams-lede'), ...lede);

  fill($('cams'), ...d.cameras.map((cam, i) => {
    const row = o.cameras.find((c) => c.camera === cam.camera);
    const isOut = dis && dis.outlier_camera === cam.camera;
    const frame = cam.frame_uri && /^(https?:\/\/|\/)/.test(cam.frame_uri)
      ? h('img', { src: cam.frame_uri, alt: `${cam.camera} frame`, loading: 'lazy' })
      : h('span', {}, 'no frame stored for this capture', h('br'), cam.frame_uri ? cam.frame_uri : '(frames are not indexed)');
    let body;
    if (!row) body = h('p', { class: 'none' }, 'This camera did not report this object.');
    else {
      const off = dis ? dis.offsets_mm[cam.camera] : null;
      body = [
        ...(row.descriptions.length ? row.descriptions : [{ text: '', label: null }]).map((ds) => [
          ds.text ? h('blockquote', {}, ...markWords(ds.text, ds.unique)) : h('p', { class: 'none' }, 'no description recorded'),
          h('div', { class: 'chips' },
            ds.label ? h('span', { class: 'chip' }, `label: ${ds.label}`) : null,
            ds.attempt > 1 ? h('span', { class: 'chip' }, `attempt ${ds.attempt}`) : null,
            ds.model ? h('span', { class: 'chip' }, ds.model) : null),
        ]),
        h('div', { class: 'pos' },
          'x ', h('b', {}, fix(row.raw_x, 4)), '  y ', h('b', {}, fix(row.raw_y, 4)), '  z ', h('b', {}, fix(row.raw_z, 4)), ' m', h('br'),
          `confidence ${fix(row.confidence, 2)} · ${row.point_count ?? '—'} points${row.occluded ? ' · OCCLUDED' : ''}`,
          off != null ? [h('br'), h('span', { class: isOut ? 'off' : '' }, `${off > 0 ? '+' : ''}${fix(off, 1)} mm on ${dis.axis} from the median${isOut ? ' — the outlier' : ''}`)] : null),
      ];
    }
    return h('article', { class: `cam${isOut ? ' outlier' : ''}`, 'aria-label': cam.camera },
      h('header', {}, glyph(i, isOut ? WRONG : INK), cam.camera,
        h('span', { class: 'stats' }, `${cam.detections} seen · ${cam.rejected} rejected`, h('br'), `mean conf ${fix(cam.mean_confidence, 2)}`)),
      h('div', { class: `frame${cam.frame_uri ? '' : ' empty'}` }, frame),
      h('div', { class: 'says' }, ...[body].flat(3)));
  }));
}

// ---- per-camera disagreement --------------------------------------------------------
function niceCeil(v) { const p = 10 ** Math.floor(Math.log10(v)); for (const m of [1, 1.5, 2, 3, 5, 7.5, 10]) if (v <= m * p) return m * p; return 10 * p; }
function dotPlot(o, D, q, tip) {
  const W = 260, H = 30, mid = W / 2, half = 114, x = (v) => mid + (v / D) * half;
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `${o.object_id}: camera estimates on ${o.disagreement.axis}, millimetres from the median` });
  svg.append(s('rect', { x: x(-q / 2), y: 5, width: x(q / 2) - x(-q / 2), height: H - 10, fill: 'rgba(239,236,230,.16)', rx: 2 }),
    s('line', { x1: x(-D), x2: x(D), y1: H / 2, y2: H / 2, stroke: '#33333c', 'stroke-width': 1 }),
    s('line', { x1: mid, x2: mid, y1: 3, y2: H - 3, stroke: FAINT, 'stroke-width': 1 }));
  const d = o.disagreement;
  CAM_ORDER.forEach((cam, i) => {
    const off = d.offsets_mm[cam];
    if (off == null) return;
    const out = d.outlier_camera === cam, row = o.cameras.find((c) => c.camera === cam);
    const m = marker(i, x(off), H / 2, 6.5, out ? WRONG : INK);
    const hit = s('circle', { cx: x(off), cy: H / 2, r: 13, fill: 'transparent', tabindex: 0,
      'aria-label': `${cam}: ${d.axis} ${fix(row[`raw_${d.axis}`], 4)} m, ${off > 0 ? '+' : ''}${fix(off, 1)} mm from the median` });
    const show = () => tip.show(hit, [`${cam}${out ? ' — outlier' : ''}`, `${d.axis} = ${fix(row[`raw_${d.axis}`], 4)} m`, `${off > 0 ? '+' : ''}${fix(off, 1)} mm from the median`]);
    hit.addEventListener('pointerenter', show); hit.addEventListener('focus', show);
    hit.addEventListener('pointerleave', tip.hide); hit.addEventListener('blur', tip.hide);
    svg.append(m, hit);
  });
  return svg;
}
function makeTip(host) {
  const el = h('div', { class: 'tip', hidden: true, role: 'tooltip' });
  host.style.position = 'relative';
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
function renderTable() {
  const d = DATA, q = d.quantum_mm, table = $('dis-table');
  const withD = d.objects.filter((o) => o.disagreement);
  if (!withD.length) { table.replaceWith(h('p', { class: 'none' }, 'No object was seen by two cameras, so there is nothing to compare.')); return; }
  const top = withD[0], over = withD.filter((o) => o.disagreement.exceeds_quantum).length;
  fill($('dis-lede'), 'Largest: ', h('span', { class: 'mono' }, top.object_id), ' — ',
    h('b', {}, `${fix(top.disagreement.max_spread_mm, 1)} mm spread`), ` on ${top.disagreement.axis}. The quantum is ${q} mm; `,
    `${over} of ${withD.length} objects are beyond it.`);
  const D = niceCeil(Math.max(q, ...withD.flatMap((o) => Object.values(o.disagreement.offsets_mm).map(Math.abs))));
  fill($('legend'), ...CAM_ORDER.map((c, i) => h('span', {}, glyph(i), c)),
    h('span', {}, h('i'), `the ${q} mm quantum`), h('span', {}, glyph(0, WRONG), 'outlier camera'),
    h('span', { class: 'scale' }, `plots: ±${D} mm from the cameras’ median`));

  const tip = makeTip(table.parentElement);
  const tx = (x, anchor, text) => s('text', { x, y: 12, 'text-anchor': anchor, fill: DIM, 'font-size': 12, 'font-family': 'ui-monospace,Menlo,monospace' }, text);
  const axis = s('svg', { viewBox: '0 0 260 16', 'aria-hidden': 'true' }, tx(16, 'start', `−${D}`), tx(130, 'middle', '0'), tx(244, 'end', `+${D} mm`));
  fill(table,
    h('caption', { class: 'sr' }, 'Where each camera put each object, and how far apart they are'),
    h('thead', {}, h('tr', {},
      h('th', { scope: 'col', class: 'obj' }, 'object'),
      h('th', { scope: 'col', class: 'vals hide-s num' }, CAM_ORDER.join(' · '), h('br'), 'worst axis, m'),
      h('th', { scope: 'col', class: 'num sp' }, 'spread', h('br'), '× quantum'),
      h('th', { scope: 'col', class: 'plot' }, axis),
      )),
    h('tbody', {}, ...d.objects.map((o) => {
      const dis = o.disagreement;
      const tr = h('tr', { 'aria-selected': String(o.object_id === selected), onclick: () => select(o.object_id) },
        h('td', { class: 'obj' }, h('button', { type: 'button', 'aria-label': `show ${o.object_id} in the camera views` }, o.object_id),
          h('small', {}, [o.class || o.known_class, o.zone || o.known_zone].filter(Boolean).join(' · ') || 'unknown object'),
          o.moved_mm != null ? h('small', { class: 'mv' }, 'read as moved ', h('b', {}, mm(o.moved_mm)),
            o.phantom ? h('span', { class: 'phantom' }, 'phantom') : null) : null),
        h('td', { class: 'num vals hide-s' }, dis ? [`${dis.axis}  `, ...CAM_ORDER.flatMap((c, i) => {
          const row = o.cameras.find((r) => r.camera === c);
          const v = row ? fix(row[`raw_${dis.axis}`], 3) : '—';
          return [i ? ' · ' : '', dis.outlier_camera === c ? h('span', { style: `color:${WRONG}` }, v) : v];
        })] : 'one camera only'),
        h('td', { class: 'num sp' }, dis ? h('span', { class: `spread${dis.exceeds_quantum ? ' bad' : ''}` }, fix(dis.max_spread_mm, 1), h('small', {}, ' mm'),
          h('small', { class: 'times' }, `  ${fix(dis.ratio, 1)}×`)) : '—'),
        h('td', { class: 'plot' }, dis ? dotPlot(o, D, q, tip) : null));
      return tr;
    })));

  const rej = $('rejected');
  if (!d.rejected.length) rej.hidden = true;
  else fill(rej, h('summary', {}, `${d.rejected.length} clusters were rejected before association — the discard pile`),
    h('table', {}, h('thead', {}, h('tr', {}, ...['camera', 'why', 'what the VLM said', 'conf', 'points'].map((t) => h('th', { scope: 'col' }, t)))),
      h('tbody', {}, ...d.rejected.map((r) => h('tr', { style: 'cursor:default' }, h('td', { class: 'mono' }, r.camera), h('td', { class: 'mono' }, r.reason || '—'),
        h('td', {}, r.description || '—'), h('td', { class: 'num' }, fix(r.confidence, 2)), h('td', { class: 'num' }, r.point_count ?? '—'))))));
}
function select(id) {
  selected = id;
  $('pick').value = id;
  for (const tr of $('dis-table').querySelectorAll('tbody tr')) tr.setAttribute('aria-selected', String(tr.querySelector('button').textContent === id));
  renderCameras();
}

// ---- the quality gate ----------------------------------------------------------------
function renderGate(g) {
  const names = { skew_ms: 'skew_ms — cameras latched together', tilt_rate_max: 'tilt_rate_max — robot settled', coverage: 'coverage — valid depth' };
  fill($('gate-cells'), ...Object.keys(g.values).map((n) => {
    const v = g.values[n], rule = g.thresholds[n], bad = g.failing.includes(n);
    const shown = v == null ? null : n === 'skew_ms' ? [fix(v, 2), 'ms'] : n === 'tilt_rate_max' ? [fix(v, 3), 'rad/s'] : [fix(v * 100, 1), '%'];
    const limit = n === 'coverage' ? `${rule.value * 100} %` : `${rule.value} ${rule.unit}`;
    return h('div', { class: `g${bad ? ' bad' : ''}${v == null ? ' na' : ''}` },
      h('div', { class: 'name' }, names[n]),
      h('div', { class: 'val' }, shown ? [shown[0], h('small', {}, shown[1])] : 'not recorded for this capture'),
      h('div', { class: 'rule' }, `must be ${rule.op} ${limit}`),
      h('div', { class: 'state' }, v == null ? '— NO VALUE' : bad ? '✕ FAILED' : '✓ OK'));
  }));
  if (g.recorded_ok != null && g.pass != null && g.recorded_ok !== g.pass) {
    $('gate').append(h('p', { class: 'why' }, `Note: the pipeline recorded quality_ok = ${g.recorded_ok}, which disagrees with these numbers.`));
  }
}

// ---- telemetry: two small multiples sharing one time axis (never a dual axis) -----------
function strip(name, unit, pts, t, opts) {
  const W = opts.width, H = 132, L = 50, R = 12, T = 12, B = 26, win = t.window_s * 1000;
  const vals = pts.map((p) => p[1]);
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  if (opts.threshold) { hi = Math.max(hi, opts.threshold * 1.25); if (lo < 0) lo = Math.min(lo, -opts.threshold * 1.25); }
  const pad = (hi - lo) * 0.12 || 1; hi += pad; if (lo < 0) lo -= pad;
  const x = (ms) => L + ((ms + win) / (2 * win)) * (W - L - R), y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `${name} from ${-win} to +${win} milliseconds around the shutter` });
  svg.append(s('rect', { x: x(-100), y: T, width: x(100) - x(-100), height: H - T - B, fill: 'rgba(239,236,230,.07)' }));   // latch window
  // ticks are values that MEAN something: zero, the gate, and the data's own extreme
  const fmt = (v) => String(Number(v.toPrecision(2)));
  const peak = vals.reduce((a, b) => (Math.abs(b) > Math.abs(a) ? b : a), 0);
  const ticks = new Set([0]);
  if (opts.threshold) { ticks.add(opts.threshold); if (lo < 0) ticks.add(-opts.threshold); }
  if (!opts.spike && (!opts.threshold || Math.abs(peak) > opts.threshold * 1.4)) ticks.add(peak);
  if (!opts.threshold) ticks.add(Math.max(...vals));
  for (const v of ticks) {
    svg.append(s('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: v === 0 ? '#3a3a44' : '#1f1f26', 'stroke-width': 1 }),
      s('text', { class: 't', x: L - 6, y: y(v) + 4, 'text-anchor': 'end' }, fmt(v)));
  }
  if (opts.threshold) for (const sign of lo < 0 ? [1, -1] : [1]) {
    svg.append(s('line', { x1: L, x2: W - R, y1: y(sign * opts.threshold), y2: y(sign * opts.threshold), stroke: DIM, 'stroke-width': 1, 'stroke-dasharray': '5 5' }));
    if (sign === 1) svg.append(s('text', { class: 't', x: W - R, y: y(opts.threshold) - 5, 'text-anchor': 'end' }, `gate ${opts.threshold} ${unit}`));
  }
  for (const ms of [-2000, -1000, 1000, 2000].filter((m) => Math.abs(m) <= win)) {
    svg.append(s('text', { class: 't', x: x(ms), y: H - 8, 'text-anchor': ms === -win ? 'start' : ms === win ? 'end' : 'middle' }, `${ms > 0 ? '+' : ''}${ms / 1000} s`));
  }
  svg.append(s('line', { x1: x(0), x2: x(0), y1: T - 4, y2: H - B + 4, stroke: INK, 'stroke-width': 1.5 }),
    s('text', { class: 'tl', x: x(0), y: H - 8, 'text-anchor': 'middle' }, 'shutter'),
    s('path', { d: pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join(''), fill: 'none', stroke: INK, 'stroke-width': 2, 'stroke-linejoin': 'round' }));
  const sp = opts.spike;
  if (sp) {
    const left = x(sp.at_ms) > W * 0.42, when = sp.at_ms < 0 ? 'before' : 'after';
    svg.append(s('circle', { cx: x(sp.at_ms), cy: y(sp.value), r: 6, fill: WRONG, stroke: PANEL, 'stroke-width': 2 }),
      s('text', { class: 'tw', x: x(sp.at_ms) + (left ? -11 : 11), y: y(sp.value) + 4, 'text-anchor': left ? 'end' : 'start' },
        W < 420 ? `${fix(sp.value, 3)} · ${Math.abs(sp.at_ms)} ms ${when}` : `${fix(sp.value, 3)} ${unit} · ${Math.abs(sp.at_ms)} ms ${when}`));
  }
  const cross = s('line', { y1: T, y2: H - B, stroke: DIM, 'stroke-width': 1, visibility: 'hidden' });
  const dot = s('circle', { r: 4.5, fill: INK, stroke: PANEL, 'stroke-width': 2, visibility: 'hidden' });
  svg.append(cross, dot);
  return { svg, x, y, cross, dot, L, R, W, pts };
}
function renderTelemetry(t) {
  const body = $('tel-body');
  if (!t.recorded) { fill(body, h('p', { class: 'none' }, `Telemetry was not recorded for this capture: robot-telemetry has no samples within ±${t.window_s} s of the shutter.`)); return; }
  const strips = [], kids = [], width = Math.max(300, Math.round(body.clientWidth || 520));
  lastTelWidth = width;
  for (const [name, threshold] of [['tilt_rate', DATA.gate.thresholds.tilt_rate_max.value], ['odom_residual', null]]) {
    const pts = t.signals[name];
    if (!pts || !pts.length) { kids.push(h('p', { class: 'none' }, `${name} was not recorded in this window.`)); continue; }
    const unit = t.units[name] || '', st = strip(name, unit, pts, t, { width, threshold, spike: t.spike && t.spike.signal === name ? t.spike : null });
    const wrap = h('div', { class: 'strip' }, h('h3', {}, `${name} · ${unit}`), st.svg), tip = makeTip(wrap);
    strips.push({ ...st, tip, name, unit });
    kids.push(wrap);
  }
  // one crosshair, synced across both strips; the nearest real sample, never an interpolation
  const move = (ev, st) => {
    const b = st.svg.getBoundingClientRect(), vx = ((ev.clientX - b.left) / b.width) * st.W;
    const ms = ((vx - st.L) / (st.W - st.L - st.R)) * 2 * t.window_s * 1000 - t.window_s * 1000;
    for (const o of strips) {
      const p = o.pts.reduce((a, c) => (Math.abs(c[0] - ms) < Math.abs(a[0] - ms) ? c : a));
      o.cross.setAttribute('x1', o.x(p[0])); o.cross.setAttribute('x2', o.x(p[0])); o.cross.setAttribute('visibility', 'visible');
      o.dot.setAttribute('cx', o.x(p[0])); o.dot.setAttribute('cy', o.y(p[1])); o.dot.setAttribute('visibility', 'visible');
      o.tip.show(o.dot, [`${p[0] > 0 ? '+' : ''}${p[0]} ms`, `${o.name} ${fix(p[1], 4)} ${o.unit}`]);
    }
  };
  const leave = () => strips.forEach((o) => { o.cross.setAttribute('visibility', 'hidden'); o.dot.setAttribute('visibility', 'hidden'); o.tip.hide(); });
  strips.forEach((st) => { st.svg.addEventListener('pointermove', (e) => move(e, st)); st.svg.addEventListener('pointerleave', leave); });

  const every = (pts) => pts.filter((p, i) => i % 10 === 0 || (t.spike && p[0] === t.spike.at_ms));
  kids.push(h('p', { class: 'why' }, 'Shaded: the ±100 ms latch window the gate reads. ',
    t.spike ? `Marked: |tilt_rate| crossing the ${t.spike.threshold} rad/s gate.` : 'No sample crosses the gate.'),
  h('details', {}, h('summary', {}, 'Show the samples as a table'),
    h('table', {}, h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'ms from shutter'), ...strips.map((o) => h('th', { scope: 'col', class: 'num' }, `${o.name} (${o.unit})`)))),
      h('tbody', {}, ...every(strips[0].pts).map((p) => h('tr', { style: 'cursor:default' }, h('td', { class: 'mono' }, p[0]),
        ...strips.map((o) => { const q = o.pts.find((r) => r[0] === p[0]); return h('td', { class: 'num' }, q ? fix(q[1], 4) : '—'); })))))));
  fill(body, kids);
}

// ---- the trace, and what the diff concluded -----------------------------------------------
function renderSentry(sn) {
  fill($('sentry-body'), 
    sn.url ? h('a', { class: 'btn', href: sn.url, target: '_blank', rel: 'noopener' }, 'Open in Sentry', h('span', { 'aria-hidden': 'true' }, '→'))
      : h('span', { class: 'btn off', role: 'link', 'aria-disabled': 'true' }, 'Open in Sentry'),
    sn.url ? null : h('p', { class: 'why' }, `No link: ${sn.why_no_link}.`),
    h('p', { class: 'trace' }, sn.trace_id ? ['sentry_trace_id ', h('b', {}, sn.trace_id), sn.span_id ? ` · span ${sn.span_id}` : '',
      h('br'), 'Every document of this capture carries it — the way back from a bad diff to the waterfall that produced it.'] : 'This capture’s documents carry no sentry_trace_id.'));
}
const idChips = (ids, bad) => h('div', { class: 'ids' }, ...ids.map((id) => h('span', { class: `chip mono${bad && bad.has(id) ? ' bad' : ''}` }, id)));
function renderDiff(d) {
  const df = d.diff, kids = [];
  if (!df) kids.push(h('p', { class: 'none' }, 'No room-events document references this capture, so what it concluded was not recorded.'));
  else {
    const phantom = new Set(df.phantom_moves || []);
    const counts = `${df.objects_moved.length} moved · ${df.objects_added.length} added · ${df.objects_removed.length} removed`;
    if (df.rejected) {
      kids.push(h('p', { class: 'concl' }, h('span', { class: 'wrong' }, 'Not committed. '), 'It would have concluded: ', h('b', {}, counts), '.'));
      if (df.objects_moved.length) kids.push(h('h3', {}, 'would have moved'), idChips(df.objects_moved, phantom));
      if (df.retry) {
        const r = df.retry;
        kids.push(h('p', { class: 'concl' }, 'The retry ', h('a', { href: `/capture/${r.capture_id}` }, r.capture_id), ' found: ',
          h('b', {}, `${r.objects_moved.length} moved · ${r.objects_added.length} added · ${r.objects_removed.length} removed`),
          r.commit_sha ? [' — committed as ', h('span', { class: 'mono' }, short(r.commit_sha)), '.'] : '.'));
        if (phantom.size) kids.push(h('p', { class: 'why' }, `Outlined: the ${phantom.size} moves the retry did not find — the robot moved, not the room.`));
      }
    } else {
      kids.push(h('p', { class: 'concl' }, 'Committed ', h('span', { class: 'mono' }, short(df.commit_sha) || '—'), df.branch ? ` on ${df.branch}` : '', ': ', h('b', {}, counts), '.'),
        df.message ? h('p', { class: 'why' }, `“${df.message}”`) : null);
      for (const [label, ids] of [['moved', df.objects_moved], ['added', df.objects_added], ['removed', df.objects_removed]]) if (ids.length) kids.push(h('h3', {}, label), idChips(ids));
    }
    if (df.outcome && df.outcome !== 'ok') kids.push(h('p', { class: 'why mono' }, `outcome: ${df.outcome}`));
  }
  if (d.suspect.reasons.length) kids.push(h('h3', {}, df && df.rejected ? 'why it was wrong' : 'why it may be wrong'),
    h('ul', { class: 'reasons' }, ...d.suspect.reasons.map((r) => h('li', {}, r.text))));
  else if (df) kids.push(h('p', { class: 'why' }, 'Nothing recorded for this capture contradicts that conclusion.'));
  fill($('diff-body'), ...kids);
}

// ---- go --------------------------------------------------------------------------------
async function main() {
  const state = $('state');
  let res;
  try { res = await fetch(`/api/capture/${encodeURIComponent(capId)}`, { headers: { accept: 'application/json' } }); }
  catch { state.replaceChildren('The server did not answer. ', h('a', { href: location.href }, 'Retry')); return; }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    $('cap-id').textContent = capId;
    state.replaceChildren(res.status === 404 ? `There is no capture “${capId}”. ` : `${body.error || res.status}: ${body.detail || 'request failed'}. `,
      h('a', { href: '/capture' }, 'Open the latest capture'));
    return;
  }
  DATA = body;
  CAM_ORDER = DATA.cameras.map((c) => c.camera);
  selected = DATA.objects.length ? DATA.objects[0].object_id : null;
  renderHeader(DATA);
  fill($('pick'), ...DATA.objects.map((o) => h('option', { value: o.object_id },
    `${o.object_id}${o.disagreement ? ` — ${fix(o.disagreement.max_spread_mm, 1)} mm` : ''}${o.phantom ? ' · phantom move' : ''}`)));
  $('pick').addEventListener('change', (e) => select(e.target.value));
  renderTable();
  renderCameras();
  renderGate(DATA.gate);
  renderTelemetry(DATA.telemetry);
  renderSentry(DATA.sentry);
  renderDiff(DATA);
  state.hidden = true;
  $('main').hidden = false;
  renderTelemetry(DATA.telemetry);            // again, now that the column has a width
  let timer;
  addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(() => {
    if (Math.abs(($('tel-body').clientWidth || 0) - lastTelWidth) > 24) renderTelemetry(DATA.telemetry);
  }, 150); });
}
main();
