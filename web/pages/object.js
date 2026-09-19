
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
// object.js — /object/<object_id>: the full life of one thing.
//
// Everything drawn here comes from GET /api/object-life/<id>. This is where "find the
// hammer" lands when the hammer is gone: last seen where, gone after which commit, and a
// VERDICT read from the recorded observations — with the rule and its numbers on screen.
// A value that was not recorded is shown as "not recorded"; nothing here is filled in.
// Orange marks the one thing that matters (it is ABSENT / where it was last seen) and
// nothing else; cameras are told apart by SHAPE and label, never by colour.
//
// "Drive there and point" follows the site's rule: PREVIEW before execute, always. The
// first control only shows where the robot would go. Running it is a second, separate
// control that arms itself 600 ms later, is never focused for you, and ignores the
// second click of a double-click.

const SVG = 'http://www.w3.org/2000/svg';
const INK = '#efece6', DIM = '#aaa69f', FAINT = '#77736d', WRONG = '#f2a03c', PANEL = '#0e0e12', LINE = '#26262e';
const ARM_MS = 600;

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
const short = (sha) => (sha ? sha.slice(0, 7) : '—');
const fix = (v, n) => (v == null ? '—' : Number(v).toFixed(n));
const cm = (m) => (m == null ? '—' : m < 0.005 ? '0 cm' : `${(m * 100).toFixed(m < 0.1 ? 1 : 0)} cm`);
const clock = (ts) => (ts ? new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—');
const day = (ts) => (ts ? new Date(ts).toLocaleDateString([], { month: 'short', day: 'numeric' }) : '');
const when = (ts) => (ts ? `${day(ts)} ${clock(ts)}` : 'not recorded');
const pose = (p) => (p ? `x ${fix(p.x, 2)} · y ${fix(p.y, 2)} · z ${fix(p.z, 2)} m${p.yaw != null ? ` · yaw ${fix(p.yaw, 0)}°` : ''}` : 'not recorded');
const capLink = (id) => (id ? h('a', { class: 'mono', href: `/capture/${id}` }, id) : 'no capture recorded');
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

function marker(i, cx, cy, r, fillC, ring = PANEL) {
  const a = { fill: fillC, stroke: ring, 'stroke-width': 2, 'paint-order': 'stroke' };
  if (i % 3 === 0) return s('circle', { cx, cy, r, ...a });
  if (i % 3 === 1) return s('rect', { x: cx - r * 0.9, y: cy - r * 0.9, width: r * 1.8, height: r * 1.8, ...a });
  return s('path', { d: `M${cx} ${cy - r * 1.15}L${cx + r * 1.1} ${cy + r * 0.85}L${cx - r * 1.1} ${cy + r * 0.85}Z`, ...a });
}
const glyph = (i, colour = INK) => s('svg', { viewBox: '0 0 18 18', width: 16, height: 16, 'aria-hidden': 'true' }, marker(i, 9, 9, 6, colour, 'none'));

const objectId = decodeURIComponent(location.pathname.split('/').filter(Boolean).pop() || '');
let DATA = null, CAMS = [];
const camIndex = (c) => Math.max(0, CAMS.indexOf(c));

// ---- header ---------------------------------------------------------------------------
function renderHeader(d) {
  document.title = `${d.object_id} — object — GITRL`;
  $('obj-id').textContent = d.object_id;
  const a = d.appearances;
  fill($('meta'),
    h('span', {}, 'class ', h('b', {}, d.class || 'not recorded')),
    h('span', {}, 'first seen ', h('b', {}, when(d.first_seen))),
    h('span', {}, 'in ', h('b', {}, a.of ? `${a.commits} of ${a.of} commits` : plural(a.commits, 'commit'))),
    h('span', {}, h('b', {}, `${a.sightings}${a.truncated ? '+' : ''}`), ' camera sightings'),
    h('span', {}, d.zones.length ? ['zones ', h('b', {}, d.zones.join(' → '))] : 'zone not recorded'),
    d.branches.length > 1 ? h('span', {}, 'branches ', h('b', {}, d.branches.join(' · '))) : null);
  const tags = [];
  if (d.source === 'fixture') tags.push('fixture data — read from fake/out/demo.ndjson');
  if (d.head.from === 'index') tags.push('HEAD taken from the index — no room.git on this server');
  if (d.head.sha && !d.head.indexed) tags.push(`HEAD ${short(d.head.sha)} is not indexed yet — presence unknown`);
  const pv = d.provenance;
  if (!pv || pv.synthetic) tags.push({ synthetic: `SYNTHETIC — ${(pv && pv.why) || 'no provenance recorded'}` });
  fill($('tags'), ...tags.map((t) => (t.synthetic ? h('span', { class: 'tag synthetic', title: pv && pv.legend ? `Real: ${pv.legend.real}. Generated: ${pv.legend.generated}.` : '' }, t.synthetic) : h('span', { class: 'tag' }, t))));
  if (d.source === 'fixture') explainFixture($('tags').firstElementChild);

  const p = $('presence'), ls = d.last_seen;
  const seen = ls ? ['last seen on the ', h('b', {}, ls.zone || 'unknown zone'), ` · ${when(ls.ts)} · commit `,
    h('a', { class: 'mono', href: '/?info#history' }, short(ls.commit_sha)), ls.branch && ls.branch !== d.head.branch ? ` on ${ls.branch}` : ''] : ['never committed'];
  let word, bad = false;
  if (d.present_now === true) word = 'PRESENT NOW';
  else if (d.present_now === false) { word = 'ABSENT'; bad = true; }
  else word = 'PRESENCE UNKNOWN';
  p.className = `verdict${bad ? ' bad' : ''}`;
  fill(p, h('span', { class: 'word' }, word),
    h('span', { class: 'because' }, d.present_now === true ? [`at HEAD ${short(d.head.sha)}${d.head.branch ? ` (${d.head.branch})` : ''} — `, ...seen] : seen),
    bad && d.verdict ? h('a', { href: '#verdict-card' }, `${d.verdict.headline} ↓`) : null);
  p.hidden = false;
}

// ---- the verdict: the five steps of "find the hammer", with their numbers -------------------
function renderVerdict(d) {
  const v = d.verdict, card = $('verdict-card');
  if (!v) { card.hidden = true; return; }
  card.hidden = false;
  const ls = d.last_seen, g = v.gone_after, hist = v.history, c = hist.confidence;
  const steps = [];
  steps.push(['Last committed', [h('b', {}, `on the ${ls.zone || 'unknown zone'}`), ` at ${pose(ls.pose)} — commit `,
    h('a', { class: 'mono', href: '/?info#history' }, short(ls.commit_sha)), ls.subject ? ` “${ls.subject}”` : '', ` · ${when(ls.ts)} · `, capLink(ls.capture_id)]]);
  if (v.last_sighting) {
    steps.push(['Last seen by a camera', [h('b', {}, when(v.last_sighting.ts)), ` — ${v.last_sighting.camera}, confidence ${fix(v.last_sighting.confidence, 2)}`,
      v.last_sighting.occluded ? ', occluded' : '', ' · ', capLink(v.last_sighting.capture_id),
      hist.sightings ? ` · ${plural(hist.sightings, 'sighting')} in all` : '']]);
  } else steps.push(['Last seen by a camera', ['not recorded']]);
  if (v.kind === 'other_branch') steps.push(['Gone after', ['nothing removed it — it was never on this branch']]);
  else if (g) {
    steps.push(['Gone after', [h('b', {}, 'commit '), h('a', { class: 'mono', href: '/?info#history' }, short(g.commit_sha)), g.subject ? ` “${g.subject}”` : '',
      ` · ${when(g.ts)} · `, capLink(g.capture_id), g.recorded_removal ? ' — the commit records its removal' : ' — the next commit without it']]);
  } else steps.push(['Gone after', ['no later commit recorded']]);

  // the evidence, per camera
  let evidence;
  if (v.after) {
    evidence = h('div', { class: 'tablewrap' }, h('table', { class: 'ev' },
      h('thead', {}, h('tr', {}, h('th', {}, 'camera'), h('th', { class: 'num' }, 'saw it'), h('th', { class: 'num' }, 'mean conf.'),
        h('th', {}, `in ${v.after.capture_id}, within ${Math.round(v.near_m * 100)} cm of the spot`))),
      h('tbody', {}, v.after.cameras.map((cam) => {
        const stat = hist.cameras[cam.camera] || {};
        let saw;
        if (cam.state === 'clear') saw = [h('b', {}, 'clear view'), ` — saw ${cam.nearest.object_id}, unoccluded, ${cm(cam.nearest.distance_m)} away (conf. ${fix(cam.nearest.confidence, 2)})`];
        else if (cam.state === 'occluded_nearby') saw = [h('b', { class: 'off' }, 'occluded'), ` — ${cam.occluded_nearby.map((n) => n.object_id || 'an unmatched cluster').join(', ')} occluded there`];
        else saw = [h('b', {}, 'not recorded'), ` — reported ${plural(cam.reported, 'thing')}, none near the spot`];
        return h('tr', {}, h('td', {}, h('span', { class: 'camname' }, glyph(camIndex(cam.camera)), cam.camera)),
          h('td', { class: 'num' }, stat.sightings != null ? `${stat.sightings}×` : '—'),
          h('td', { class: 'num' }, fix(stat.mean_confidence, 2)), h('td', {}, saw));
      }))));
  } else evidence = h('p', { class: 'none' }, 'No capture after its last appearance is recorded, so the cameras’ view of the spot is unknown.');
  const extra = [];
  if (v.watch && v.watch.captures) {
    extra.push(h('p', { class: 'why' }, `Between that last sighting and ${v.after ? v.after.capture_id : 'the next capture'} the watch loop (${v.watch.cameras.join(', ')}) ran ` +
      `${plural(v.watch.captures, 'time')}: it saw the spot, unoccluded, in ${v.watch.saw_the_spot} of them and the object in ${v.watch.saw_the_object}.`));
  }
  if (c) extra.push(h('p', { class: 'why' }, `Its own sightings: confidence ${fix(c.min, 2)}–${fix(c.max, 2)}, mean ${fix(c.mean, 2)}, spread ${fix(c.stdev, 2)}; ` +
    `${hist.occluded} of ${hist.sightings} occluded.`));
  steps.push(['The evidence', [evidence, ...extra]]);
  steps.push(['Verdict', [h('p', { class: `concl${v.kind === 'unknown' || v.kind === 'flaky' ? '' : ''}` }, h('b', { class: 'wrong' }, v.headline)),
    h('p', { class: 'rule' }, v.rule || 'not enough evidence recorded'), h('p', { class: 'why' }, v.method)]]);

  fill($('verdict-body'), h('ol', { class: 'steps' }, steps.map(([name, body], i) => h('li', {},
    h('span', { class: 'n', 'aria-hidden': 'true' }, i + 1), h('div', {}, h('h3', {}, name), h('div', { class: 'stepbody' }, body))))));
}

// ---- every description ---------------------------------------------------------------------
function markWords(text, unique) {
  const set = new Set(unique || []);
  return text.split(/([A-Za-z0-9']+)/).map((tok) => (set.has(tok.toLowerCase()) ? h('mark', {}, tok) : tok));
}
function renderDescriptions(d) {
  const n = d.descriptions.reduce((a, c) => a + c.cameras.reduce((b, cam) => b + cam.descriptions.length, 0), 0);
  const labels = [...new Set(d.descriptions.flatMap((c) => c.labels))];
  fill($('said-lede'), n ? [`${plural(n, 'description')} across ${plural(d.descriptions.length, 'capture')}, none reconciled. `,
    labels.length > 1 ? ['The cameras have called it ', h('b', {}, labels.join(' · ')), '. '] : null,
    'Underlined words are the ones only that camera used in that capture.'] : 'No description of this object is recorded.');
  fill($('said-body'), d.descriptions.map((cap) => h('div', { class: 'capblock' },
    h('div', { class: 'caphead' }, capLink(cap.capture_id), h('span', {}, when(cap.ts)),
      cap.commit_sha ? h('span', {}, 'commit ', h('a', { class: 'mono', href: '/?info#history' }, short(cap.commit_sha))) : h('span', {}, 'not committed'),
      cap.gate_pass === false ? h('span', { class: 'chip bad' }, 'capture rejected') : null,
      cap.labels_disagree ? h('span', { class: 'chip' }, `labels: ${cap.labels.join(' · ')}`) : null),
    h('div', { class: 'cams' }, CAMS.map((name, i) => {
      const cam = cap.cameras.find((x) => x.camera === name);
      return h('article', { class: 'cam' },
        h('header', {}, glyph(i), name, cam ? h('span', { class: 'stats' }, `conf ${fix(cam.confidence, 2)}${cam.occluded ? ' · occluded' : ''}`) : null),
        h('div', { class: 'says' }, cam ? cam.descriptions.map((desc) => [h('blockquote', {}, markWords(desc.text, desc.unique)),
          h('div', { class: 'chips' }, desc.label ? h('span', { class: 'chip' }, `label: ${desc.label}`) : null,
            desc.attempt > 1 ? h('span', { class: 'chip' }, `attempt ${desc.attempt}`) : null)])
          : h('p', { class: 'none' }, 'This camera recorded no description.')));
    })))));
}

// ---- drive there and point: preview first, a separate armed control to run --------------------
function renderPoint(d) {
  const body = $('point-body'), pt = d.point;
  if (!pt) { fill(body, h('p', { class: 'none' }, 'No pose is recorded for this object, so there is nowhere to point.')); return; }
  const absent = d.present_now === false;
  const out = h('div', { class: 'pt-out', role: 'status', 'aria-live': 'polite' });
  const panel = h('div', { class: 'pt-preview', hidden: true });
  let armTimer = 0;
  const open = h('button', { class: 'btn ghost', type: 'button', 'aria-expanded': 'false', 'aria-controls': 'pt-preview',
    onclick: (e) => {
      if (e.detail > 1) return;                               // a double-click opens it once and runs nothing
      const showing = !panel.hidden;
      panel.hidden = showing; open.setAttribute('aria-expanded', String(!showing));
      clearTimeout(armTimer);
      run.disabled = true; run.setAttribute('data-arming', '');
      if (showing) return;
      // armed only after the preview has been on screen a moment: the second click of a
      // double-click, or a palm on the trackpad, can never land on a live control
      armTimer = setTimeout(() => { run.disabled = false; run.removeAttribute('data-arming'); }, ARM_MS);
      document.dispatchEvent(new CustomEvent('gitrl:preview-point', { detail: true }));
    } }, absent ? 'Preview: where would it point?' : 'Preview: where would it go?');
  const run = h('button', { class: 'btn run', type: 'button', disabled: true, 'data-arming': '', tabindex: '0',
    onclick: async (e) => {
      if (e.detail > 1 || run.disabled) return;               // never on a double-click
      run.disabled = true;
      fill(out, 'Asking the server to plan it…');
      try {
        const r = await fetch(`/api/object-life/${encodeURIComponent(d.object_id)}/point`, { method: 'POST' });
        const job = await r.json();
        if (!r.ok) throw job;
        fill(out, h('b', {}, `${job.job_id} — ${job.state}. `),
          job.executor === 'not_connected'
            ? 'No robot is connected to this server yet, so nothing moved and nothing was written. The plan: drive to the pose above and point — about '
              + `${job.estimated_s} s.` : `About ${job.estimated_s} s.`,
          job.allow_listed === false ? h('span', { class: 'why' }, ' “point” is not on this server’s command allow-list (WEB_ALLOWED_COMMANDS); a connected executor must refuse it until it is added.') : null);
      } catch (err) {
        fill(out, h('span', { class: 'off' }, `${err.error || 'error'}: ${err.detail || 'the request failed'}`));
      }
      setTimeout(() => { run.disabled = false; }, 1500);
    } }, 'Drive there and point');
  panel.id = 'pt-preview';
  panel.append(h('p', { class: 'previewonly' }, 'Preview only — nothing has moved.'),
    h('p', { class: 'concl' }, 'The robot would drive to the ', h('b', {}, pt.zone || 'last known zone'), ' and point at ', h('b', {}, pt.at), '.'),
    h('p', { class: 'pos' }, 'target ', h('b', {}, pose(pt.target_pose)), ' · from commit ', h('a', { class: 'mono', href: '/?info#history' }, short(pt.commit_sha))),
    run, out);
  fill(body, h('p', { class: 'lede' }, absent ? 'It is not here — but the room remembers where it was. The robot can go there and point at the empty space.'
    : 'The robot can drive over and point at it.'), open, panel);
}

// ---- where it has stood: a plan-view track, x / y in metres, equal scale ---------------------------
function renderTrack(d) {
  const rows = [...d.timeline].reverse().filter((t) => t.pose && t.pose.x != null && t.pose.y != null);   // oldest first
  const host = $('track-body');
  if (!rows.length) { fill(host, h('p', { class: 'none' }, 'No pose is recorded for this object.')); return; }
  // commits at the same spot share one mark
  const spots = [], spotOf = new Map();
  for (const t of rows) {
    let spot = spots.find((p) => Math.hypot(p.x - t.pose.x, p.y - t.pose.y) < 0.004);
    if (!spot) { spot = { x: t.pose.x, y: t.pose.y, commits: [] }; spots.push(spot); }
    spot.commits.push(t); spotOf.set(t.commit_sha, spot);
  }
  const W = 520, H = 250, PAD = 34;
  const xs = spots.map((p) => p.x), ys = spots.map((p) => p.y);
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 0.3);
  const cx = (Math.max(...xs) + Math.min(...xs)) / 2, cy = (Math.max(...ys) + Math.min(...ys)) / 2;
  const k = Math.min((W - PAD * 2) / span, (H - PAD * 2) / span);
  const X = (x) => W / 2 + (x - cx) * k, Y = (y) => H / 2 - (y - cy) * k;
  const absent = d.present_now === false;
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img',
    'aria-label': `Plan view of where ${d.object_id} has stood: ${plural(spots.length, 'position')} over ${plural(rows.length, 'commit')}. The table below lists every one.` });
  // a recessive 10 cm grid, and a scale bar
  const step = 0.1;
  for (let gx = Math.ceil((cx - span) / step) * step; gx <= cx + span; gx += step) svg.append(s('line', { x1: X(gx), x2: X(gx), y1: 0, y2: H, stroke: LINE, 'stroke-width': 1 }));
  for (let gy = Math.ceil((cy - span) / step) * step; gy <= cy + span; gy += step) svg.append(s('line', { x1: 0, x2: W, y1: Y(gy), y2: Y(gy), stroke: LINE, 'stroke-width': 1 }));
  svg.append(s('line', { x1: 14, x2: 14 + step * k, y1: H - 12, y2: H - 12, stroke: DIM, 'stroke-width': 2 }),
    s('text', { x: 14 + step * k + 8, y: H - 8, class: 't' }, '10 cm · plan view (x →, y ↑)'));
  // the path follows ANCESTRY, not the clock: a leg runs from the pose in a commit's parent to the
  // pose in that commit (two branches fork from one spot). A leg into another branch is dashed.
  for (const t of rows) {
    const a = spotOf.get(t.parent_sha), b = spotOf.get(t.commit_sha);
    if (!a || a === b) continue;
    const off = !t.in_head_history;
    svg.append(s('line', { x1: X(a.x), y1: Y(a.y), x2: X(b.x), y2: Y(b.y), stroke: off ? FAINT : DIM, 'stroke-width': 2,
      'stroke-dasharray': off ? '5 5' : null, 'stroke-linecap': 'round' }));
  }
  const tip = h('div', { class: 'tip', hidden: true });
  const lastHere = [...spots].reverse().find((p) => p.commits.some((t) => t.in_head_history)) || spots[spots.length - 1];
  spots.forEach((p, i) => {
    const isLast = p === lastHere, newest = p.commits[p.commits.length - 1];
    const g = s('g', { tabindex: 0, role: 'img', 'aria-label': `${p.commits.map((t) => short(t.commit_sha)).join(', ')}: x ${fix(p.x, 2)}, y ${fix(p.y, 2)}` });
    if (isLast && absent) g.append(s('circle', { cx: X(p.x), cy: Y(p.y), r: 14, fill: 'none', stroke: WRONG, 'stroke-width': 2, 'stroke-dasharray': '4 4' }));
    g.append(s('circle', { cx: X(p.x), cy: Y(p.y), r: isLast ? 7 : 5, fill: isLast ? (absent ? WRONG : INK) : PANEL, stroke: isLast ? PANEL : DIM, 'stroke-width': 2 }));
    g.append(s('circle', { cx: X(p.x), cy: Y(p.y), r: 18, fill: 'transparent' }));              // the hit target is bigger than the mark
    const show = () => {
      fill(tip, ...p.commits.map((t) => h('div', {}, h('b', {}, short(t.commit_sha)), ` ${t.branch || ''} · ${when(t.ts)}`)),
        h('div', {}, `x ${fix(p.x, 3)} · y ${fix(p.y, 3)} m · ${newest.zone || 'zone —'}`));
      tip.style.left = `${(X(p.x) / W) * 100}%`; tip.style.top = `${(Y(p.y) / H) * 100}%`; tip.hidden = false;
    };
    g.addEventListener('pointerenter', show); g.addEventListener('focus', show);
    g.addEventListener('pointerleave', () => { tip.hidden = true; }); g.addEventListener('blur', () => { tip.hidden = true; });
    svg.append(g);
    // selective direct labels: the first position and the last one in HEAD's history
    if (i === 0 || isLast) {
      const label = isLast ? (absent ? `last seen · ${short(newest.commit_sha)}` : `now · ${short(newest.commit_sha)}`) : `first · ${short(p.commits[0].commit_sha)}`;
      // the current / last-seen position is labelled above its mark, the first one below: they never share a line
      svg.append(s('text', { x: Math.min(W - 70, Math.max(70, X(p.x))), y: Y(p.y) + (isLast ? -16 : 24),
        'text-anchor': 'middle', class: isLast && absent ? 'tw' : 'tl' }, label));
    }
  });
  const moves = d.timeline.filter((t) => t.in_head_history && t.moved_m && t.moved_m >= 0.005);
  const total = moves.reduce((a, t) => a + t.moved_m, 0);
  fill(host, h('p', { class: 'lede' }, spots.length === 1 ? ['It has only ever stood in ', h('span', { class: 'mono' }, 'one place'), '.']
    : [`Moved ${plural(moves.length, 'time')} in this branch's history — ${cm(total)} in all.`,
      rows.some((t) => !t.in_head_history) ? ' Dashed: a position on another branch.' : '']),
    h('div', { class: 'strip' }, svg, tip));
}

// ---- sightings: confidence over time ---------------------------------------------------------------
function renderSightings(d) {
  const host = $('seen-body'), pts = d.sightings.filter((p) => p.confidence != null && p.ts);
  if (!pts.length) { fill(host, h('p', { class: 'none' }, 'No camera sighting of this object is recorded.')); return; }
  const W = 520, H = 150, L = 34, R = 10, T = 12, B = 26;
  const t0 = Date.parse(pts[0].ts), headTs = (d.timeline.find((t) => t.commit_sha === d.head.sha) || {}).ts;
  const allTs = d.timeline.map((t) => Date.parse(t.ts)).concat(pts.map((p) => Date.parse(p.ts)));
  const gone = d.verdict && d.verdict.gone_after ? Date.parse(d.verdict.gone_after.ts) : null;
  const t1 = Math.max(...allTs, gone || 0, headTs ? Date.parse(headTs) : 0, t0 + 60000);
  const X = (t) => L + ((t - t0) / (t1 - t0)) * (W - L - R), Y = (v) => T + (1 - v) * (H - T - B);
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img',
    'aria-label': `Camera confidence for ${d.object_id}: ${pts.length} sightings from ${when(pts[0].ts)} to ${when(pts[pts.length - 1].ts)}.` });
  for (const v of [0, 0.5, 1]) {
    svg.append(s('line', { x1: L, x2: W - R, y1: Y(v), y2: Y(v), stroke: LINE, 'stroke-width': 1 }), s('text', { x: L - 6, y: Y(v) + 4, 'text-anchor': 'end', class: 't' }, v.toFixed(1)));
  }
  svg.append(s('text', { x: L, y: H - 6, class: 't' }, clock(pts[0].ts)), s('text', { x: W - R, y: H - 6, 'text-anchor': 'end', class: 't' }, clock(new Date(t1).toISOString())));
  const last = pts[pts.length - 1], lastX = X(Date.parse(last.ts));
  if (d.present_now === false && lastX < W - R - 2) {
    svg.append(s('rect', { x: lastX, y: T, width: W - R - lastX, height: H - T - B, fill: 'rgba(242,160,60,.09)' }),
      s('text', { x: Math.min(lastX + 8, W - R - 96), y: T + 14, class: 'tw' }, 'no sighting since'));
  }
  // one thin line through every sighting; commit-path captures (three cameras + VLM) get their camera's shape
  svg.append(s('path', { d: pts.map((p, i) => `${i ? 'L' : 'M'}${X(Date.parse(p.ts)).toFixed(1)} ${Y(p.confidence).toFixed(1)}`).join(''),
    fill: 'none', stroke: DIM, 'stroke-width': 2, 'stroke-linejoin': 'round' }));
  for (const p of pts) if (String(p.capture_id).startsWith('cap_')) svg.append(marker(camIndex(p.camera), X(Date.parse(p.ts)), Y(p.confidence), 4.5, p.occluded ? PANEL : INK));
  if (d.present_now === false) svg.append(s('circle', { cx: lastX, cy: Y(last.confidence), r: 6, fill: WRONG, stroke: PANEL, 'stroke-width': 2 }));
  // crosshair + tooltip: the nearest REAL sighting, never an interpolation
  const cross = s('line', { y1: T, y2: H - B, stroke: INK, 'stroke-width': 1, visibility: 'hidden' });
  const dot = s('circle', { r: 5, fill: INK, stroke: PANEL, 'stroke-width': 2, visibility: 'hidden' });
  svg.append(cross, dot, s('rect', { x: L, y: T, width: W - L - R, height: H - T - B, fill: 'transparent' }));
  const tip = h('div', { class: 'tip', hidden: true });
  const move = (e) => {
    const box = svg.getBoundingClientRect(), x = ((e.clientX - box.left) / box.width) * W;
    let best = pts[0], bd = Infinity;
    for (const p of pts) { const dx = Math.abs(X(Date.parse(p.ts)) - x); if (dx < bd) { bd = dx; best = p; } }
    const bx = X(Date.parse(best.ts)), by = Y(best.confidence);
    cross.setAttribute('x1', bx); cross.setAttribute('x2', bx); cross.setAttribute('visibility', 'visible');
    dot.setAttribute('cx', bx); dot.setAttribute('cy', by); dot.setAttribute('visibility', 'visible');
    fill(tip, h('div', {}, h('b', {}, fix(best.confidence, 2)), ` · ${best.camera}${best.occluded ? ' · occluded' : ''}`), h('div', {}, `${when(best.ts)} · ${best.capture_id}`));
    tip.style.left = `${Math.min(84, Math.max(16, (bx / W) * 100))}%`; tip.style.top = `${(by / H) * 100 - 4}%`; tip.hidden = false;
  };
  svg.addEventListener('pointermove', move);
  svg.addEventListener('pointerleave', () => { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); });
  const caps = pts.filter((p) => String(p.capture_id).startsWith('cap_'));
  fill(host, h('div', { class: 'strip' }, svg, tip),
    h('div', { class: 'legend' }, CAMS.map((c, i) => h('span', {}, glyph(i), c)), h('span', {}, 'shapes: three-camera captures · line: every sighting, watch loop included')),
    h('details', {}, h('summary', {}, `Show the ${caps.length} three-camera sightings as a table`),
      h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'when'), h('th', {}, 'capture'), h('th', {}, 'camera'), h('th', { class: 'num' }, 'confidence'))),
        h('tbody', {}, caps.map((p) => h('tr', {}, h('td', {}, when(p.ts)), h('td', {}, capLink(p.capture_id)), h('td', {}, p.camera),
          h('td', { class: 'num' }, `${fix(p.confidence, 2)}${p.occluded ? ' occl.' : ''}`)))))));
}

// ---- its life, commit by commit ----------------------------------------------------------------------
function renderLife(d) {
  fill($('life-table'),
    h('thead', {}, h('tr', {}, h('th', {}, 'commit'), h('th', {}, 'when'), h('th', {}, 'zone · pose'), h('th', { class: 'num' }, 'moved'), h('th', {}, 'capture'))),
    h('tbody', { class: 'plain' }, d.timeline.map((t) => h('tr', { class: t.in_head_history ? null : 'offbranch' },
      h('td', {}, h('a', { class: 'mono', href: '/?info#history' }, short(t.commit_sha)), t.branch ? h('small', {}, ` ${t.branch}`) : null,
        h('div', { class: 'subj' }, t.subject || 'subject not recorded'),
        t.in_head_history ? null : h('span', { class: 'chip' }, 'not in HEAD’s history')),
      h('td', {}, when(t.ts)),
      h('td', {}, h('b', {}, t.zone || '—'), h('div', { class: 'pos axes' }, t.pose ? ['x', 'y', 'z'].map((a) => h('span', {}, `${a} ${fix(t.pose[a], 2)}`)).concat(t.pose.yaw != null ? [h('span', {}, `yaw ${fix(t.pose.yaw, 0)}°`)] : []) : 'not recorded')),
      h('td', { class: 'num' }, t.change === 'first appearance' ? 'first' : t.change === 'unchanged' ? '—' : cm(t.moved_m)),
      h('td', {}, capLink(t.capture_id))))));
}

// ---- go ---------------------------------------------------------------------------------------------
async function main() {
  const state = $('state');
  try {
    const r = await fetch(`/api/object-life/${encodeURIComponent(objectId)}`, { headers: { accept: 'application/json' } });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(body.detail || r.statusText), { status: r.status, body });
    DATA = body;
  } catch (e) {
    $('obj-id').textContent = objectId || 'object';
    fill(state, e.status === 404 ? ((e.body && /Elasticsearch/.test(e.body.detail || '')) ? e.body.detail : `No object called ${objectId} has ever been recorded in this room.`)
      : e.status === 422 ? `${objectId} is not an object id (they look like mug_a1b2).`
        : `Could not load this object: ${(e.body && e.body.error) || 'error'} — ${(e.body && e.body.detail) || e.message}`,
    h('br'), h('a', { href: '/?info#search' }, '← back to search'));
    return;
  }
  const d = DATA;
  CAMS = [...new Set([...d.sightings.map((p) => p.camera), ...d.descriptions.flatMap((c) => c.cameras.map((x) => x.camera))].filter(Boolean))].sort();
  renderHeader(d); renderVerdict(d); renderDescriptions(d); renderPoint(d); renderTrack(d); renderSightings(d); renderLife(d);
  state.hidden = true; $('main').hidden = false;
}
main();
