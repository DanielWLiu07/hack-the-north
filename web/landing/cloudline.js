// cloudline.js — HISTORY, horizontally: the room's commits as the room itself, left to right.
//
// The old graph in #history drew a commit as a dot on a vertical rail. A dot is a poor picture of a
// room. Here every node IS the room at that commit — the point cloud that was committed in that
// tree — rendered once into an ordinary 2D canvas and laid out as a filmstrip: oldest on the left,
// the room now on the right.
//
// ONE RENDERER, AND IT IS NOT A GPU. The dashboard is allowed a single WebGL context and the hero
// spends it, so none of this may touch WebGL — not even hidden, which tools/dev/framewatch.mjs
// proves by hooking getContext before any page script. plyshot.worker.js projects each cloud into
// pixels in a worker and posts the pixels back; the cards are 2D canvases. Nothing here blocks a
// frame and nothing here allocates a GPU object.
//
// ONE CAMERA, FOR EVERY NODE. Two frames of a film are only comparable if the camera did not move
// between them, so the basis is computed ONCE from the union of the captures' own bounds and then
// used for every node: same angle, same metres per pixel, same centre.
//
// WHERE THE DATA COMES FROM. GET /api/scene/{instance}/history — the instance's real `git log`,
// parents and refs included — and the cloud committed beside it. That is the only place in this
// project where a commit and a point cloud come out of the same tree, which is why the timeline is
// drawn from it and not from room.git: room.git's commits carry object records, not clouds, and
// dressing them in a cloud captured in a different room would be a lie.

const NS = 'http://www.w3.org/2000/svg';
const CARD = { w: 214, h: 132 }, HEAD_CARD = { w: 268, h: 164 };   // CSS px; the newest node is the biggest
const DETAIL = { w: 640, h: 340 };
const DASH = ['', '7 5', '2 5', '11 4 2 4'];              // lanes differ by stroke, not colour: the page has one accent
const CAM_V = 3;                                          // bump when the basis changes, so cached thumbnails are not reused
const AZ = 0.40, EL = 0.36;                               // radians: from behind the robot, a little to its left, tilted down
const MAX_CACHE = 14;

// ---- tiny DOM helpers (text only ever enters the DOM as text) ------------------------------
function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'text') n.textContent = v; else if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v === true ? '' : v);
  }
  n.append(...kids.filter((c) => c != null && c !== false));
  return n;
}
const svg = (tag, attrs) => { const n = document.createElementNS(NS, tag); for (const k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]); return n; };
const short = (id) => (/^[0-9a-f]{40}$/.test(id || '') ? id.slice(0, 7) : id || '—');
const num = (n) => (typeof n === 'number' && isFinite(n) ? n.toLocaleString() : null);
const thousands = (n) => (Math.abs(n) >= 1000 ? `${n < 0 ? '−' : '+'}${Math.round(Math.abs(n) / 1000)}k` : `${n < 0 ? '−' : '+'}${Math.abs(n)}`);
function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!isFinite(s)) return '';
  const a = Math.abs(s), w = a < 90 ? [Math.round(a), 's'] : a < 5400 ? [Math.round(a / 60), ' min']
    : a < 129600 ? [Math.round(a / 3600), ' h'] : [Math.round(a / 86400), ' d'];
  return s < 0 ? `in ${w[0]}${w[1]}` : `${w[0]}${w[1]} ago`;
}
async function getJSON(url) {
  const r = await fetch(url, { headers: { accept: 'application/json' } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(body.detail || r.statusText), { status: r.status });
  return body;
}

// ---- the railroad walk, transposed ---------------------------------------------------------
// Straight out of graph.js:57-75, which is correct and twenty lines long. There, rows were time and
// lanes stacked on x; here time advances along x and lanes stack on y. Nodes arrive newest-first
// (git's --date-order guarantees a parent never comes before its child), and the column each one is
// drawn in is simply mirrored at the end so that the oldest is on the left.
function layout(nodes) {
  const lanes = [], place = new Map(), edges = [];
  nodes.forEach((n, row) => {
    let lane = lanes.indexOf(n.id);
    if (lane < 0) { lane = lanes.indexOf(null); if (lane < 0) lane = lanes.length; }
    lanes.forEach((waiting, i) => { if (waiting === n.id && i !== lane) lanes[i] = null; });
    place.set(n.id, { lane, row });
    lanes[lane] = null;
    (n.parents || []).forEach((p, k) => {
      let via = lane;                                     // the first parent continues MY lane, always
      if (k > 0) { via = lanes.indexOf(p); if (via < 0) { via = lanes.indexOf(null); if (via < 0) via = lanes.length; } }
      lanes[via] = p;
      edges.push({ child: n.id, parent: p, via });
    });
  });
  return { place, edges, laneCount: Math.max(1, ...[...place.values()].map((p) => p.lane + 1)) };
}

// ---- the one camera ---------------------------------------------------------------------------
function unit(v) { const m = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / m, v[1] / m, v[2] / m]; }
function basis() {
  const d = unit([Math.cos(EL) * Math.cos(AZ), Math.cos(EL) * Math.sin(AZ), -Math.sin(EL)]);
  const r = unit([d[1], -d[0], 0]);                       // cross(d, z-up): the robot's right, on screen
  const u = [r[1] * d[2] - r[2] * d[1], r[2] * d[0] - r[0] * d[2], r[0] * d[1] - r[1] * d[0]];
  return { d, r, u };
}
// Fit the union of every capture's own bounds into a w x h frame, once. Every node then gets the
// same k, the same centre and the same angle — which is the whole point of a filmstrip.
function frameFor(box, w, h, splat) {
  const { d, r, u } = basis();
  const c = [(box.min[0] + box.max[0]) / 2, (box.min[1] + box.max[1]) / 2, (box.min[2] + box.max[2]) / 2];
  let u0 = Infinity, u1 = -Infinity, v0 = Infinity, v1 = -Infinity, d0 = Infinity, d1 = -Infinity;
  for (let i = 0; i < 8; i++) {
    const p = [i & 1 ? box.max[0] : box.min[0], i & 2 ? box.max[1] : box.min[1], i & 4 ? box.max[2] : box.min[2]];
    const q = [p[0] - c[0], p[1] - c[1], p[2] - c[2]];
    const uu = q[0] * r[0] + q[1] * r[1] + q[2] * r[2], vv = q[0] * u[0] + q[1] * u[1] + q[2] * u[2],
      dd = q[0] * d[0] + q[1] * d[1] + q[2] * d[2];
    u0 = Math.min(u0, uu); u1 = Math.max(u1, uu); v0 = Math.min(v0, vv); v1 = Math.max(v1, vv);
    d0 = Math.min(d0, dd); d1 = Math.max(d1, dd);
  }
  const pad = 6;
  const k = Math.min((w - pad * 2) / (u1 - u0 || 1), (h - pad * 2) / (v1 - v0 || 1));
  return { c, r, u, d, k, ox: w / 2 - ((u0 + u1) / 2) * k, oy: h / 2 + ((v0 + v1) / 2) * k, d0, d1, splat };
}

// ---- the renderer: one worker, one job at a time, newest first ---------------------------------
class Shots {
  constructor() {
    this.worker = null; this.queue = []; this.busy = null; this.seq = 0;
    this.store = safeStorage();
  }
  start() {
    if (this.worker) return this.worker;
    this.worker = new Worker(new URL('./plyshot.worker.js', import.meta.url));
    this.worker.onmessage = (ev) => {
      const job = this.busy;
      if (!job || ev.data.id !== job.id) return;
      this.busy = null;
      if (ev.data.ok) job.done(ev.data); else if (!ev.data.aborted) job.fail(ev.data);
      this.pump();
    };
    this.worker.onerror = () => { const job = this.busy; this.busy = null; if (job) job.fail({ reason: 'the thumbnail worker stopped' }); };
    return this.worker;
  }
  // `first` puts a job at the head of the line: what somebody just clicked beats what is merely on screen.
  ask({ key, url, w, h, cam, first }, done, fail) {
    const cached = this.cached(key);
    if (cached) { done({ cached }); return; }
    if (this.queue.some((j) => j.key === key) || (this.busy && this.busy.key === key)) return;
    const job = { id: ++this.seq, key, url, w, h, cam, done, fail };
    if (first) this.queue.unshift(job); else this.queue.push(job);
    this.pump();
  }
  pump() {
    if (this.busy || !this.queue.length) return;
    const job = this.queue.shift();
    this.busy = job;
    this.start().postMessage({ id: job.id, url: job.url, w: job.w, h: job.h, cam: job.cam });
  }
  cached(key) { try { return this.store && this.store.getItem(key); } catch { return null; } }
  keep(key, dataUrl) {
    if (!this.store) return;
    try {
      const mine = Object.keys(this.store).filter((k) => k.startsWith('cl:'));
      while (mine.length >= MAX_CACHE) this.store.removeItem(mine.shift());
      this.store.setItem(key, dataUrl);
    } catch { /* a full or blocked sessionStorage only costs a re-render next visit */ }
  }
}
function safeStorage() { try { const s = sessionStorage; s.setItem('cl:probe', '1'); s.removeItem('cl:probe'); return s; } catch { return null; } }

// ---- the timeline ------------------------------------------------------------------------------
export function mount(host) {
  const shots = new Shots();
  let data = null, nodes = [], place = null, edges = [], laneCount = 1, cam = null, camDetail = null;
  let selected = null, drawn = new Set(), pending = new Map(), watcher = null, instance = null;

  const kicker = el('span', { class: 'cl-k mono', text: 'the room, commit by commit' });
  const where = el('span', { class: 'cl-inst mono', text: 'reading the room’s history…' });
  const bar = el('div', { class: 'cl-bar' }, kicker, where);
  const rails = svg('svg', { class: 'cl-rails', 'aria-hidden': 'true', focusable: 'false' });
  const strip = el('ol', { class: 'cl-strip' });
  const inner = el('div', { class: 'cl-inner' }, rails, strip);      // the rails sit UNDER the cards, in their own layer
  const scroller = el('div', { class: 'cl-scroll', tabindex: '0', role: 'group',
    'aria-label': 'The room at each commit, oldest on the left. Left and right arrows step through time.' }, inner);
  const axis = el('div', { class: 'cl-axis mono' }, el('span', { text: 'older' }), el('span', { class: 'cl-axis-line' }), el('span', { text: 'the room now' }));
  const detail = el('aside', { class: 'cl-detail', 'aria-live': 'polite' });
  const note = el('p', { class: 'cl-note', hidden: true });
  const wrap = el('div', { class: 'cl' }, bar, scroller, axis, detail, note);
  host.replaceChildren(wrap);

  // ---- cards ------------------------------------------------------------------------------------
  function cardSize(n) { return n.head ? HEAD_CARD : CARD; }

  function refChips(n) {
    return (n.refs || []).filter((r) => r.kind !== 'remote').map((r) => el('span', {
      class: 'cl-ref', 'data-kind': r.kind, 'data-head': r.head ? '' : null,
      text: (r.head && r.kind === 'branch' ? 'HEAD → ' : '') + r.name }));
  }

  function badges(n) {
    const out = [];
    if ((n.parents || []).length > 1) out.push(el('span', { class: 'cl-badge', 'data-kind': 'merge', text: 'merge' }));
    // an approved decision in this project IS a merge commit, and roomctl names its refs pr/<n>
    for (const r of n.refs || []) if (/^pr[/-]/i.test(r.name)) out.push(el('span', { class: 'cl-badge', 'data-kind': 'pr', text: r.name }));
    return out;
  }

  function factLine(n) {
    const bits = [];
    if (n.at) bits.push(ago(n.at));
    if (num(n.points)) bits.push(`${num(n.points)} pts`);
    const parent = nodes.find((x) => x.id === (n.parents || [])[0]);
    if (parent && typeof parent.points === 'number' && typeof n.points === 'number' && parent.points !== n.points) {
      bits.push(`${thousands(n.points - parent.points)} vs ${short(parent.id)}`);
    }
    return bits.join(' · ');
  }

  function makeCard(n) {
    const size = cardSize(n);
    const canvas = el('canvas', { class: 'cl-canvas', width: String(size.w), height: String(size.h), 'aria-hidden': 'true' });
    const fallback = el('span', { class: 'cl-fallback mono', text: n.cloud ? 'point cloud not drawn yet' : 'no point cloud in this commit' });
    const frame = el('span', { class: 'cl-frame' }, canvas, fallback);
    const btn = el('button', {
      class: 'cl-card', type: 'button', 'data-id': n.id, 'data-head': n.head ? '' : null,
      'data-cloud': n.cloud ? '' : null, 'aria-pressed': 'false',
      'aria-label': `${short(n.id)} ${n.subject}${n.head ? ' — the room now' : ''}: show this commit’s point cloud larger`,
      onclick: () => choose(n.id) },
      frame,
      el('span', { class: 'cl-meta' },
        el('span', { class: 'cl-line1' }, n.head ? el('span', { class: 'cl-badge', 'data-kind': 'now', text: 'the room now' }) : null,
          ...refChips(n), ...badges(n), el('span', { class: 'cl-sha mono', text: short(n.id) })),
        el('span', { class: 'cl-subject', text: n.subject || short(n.id) }),
        el('span', { class: 'cl-facts mono', text: factLine(n) })));
    const li = el('li', { class: 'cl-node', 'data-id': n.id, style: `--cw:${size.w}px;--ch:${size.h}px` }, btn);
    li._canvas = canvas; li._fallback = fallback; li._node = n;
    return li;
  }

  // ---- rendering a node into its card ------------------------------------------------------------
  function plyUrl(n) {
    if (!n.cloud) return null;
    // a node that is a real commit carries the cloud IN its tree; a capture that has not been
    // committed yet is only a file beside the repo, and is fetched as one
    return n.commit_sha ? `/api/scene/${encodeURIComponent(instance)}/history/${encodeURIComponent(n.commit_sha)}.ply`
      : n.capture_id ? `/api/scene/${encodeURIComponent(instance)}/${encodeURIComponent(n.capture_id)}.ply` : null;
  }

  function paintPixels(canvas, msg) {
    const g = canvas.getContext('2d');                    // 2D, never webgl: the page's one context belongs to the hero
    if (!g) return false;
    g.putImageData(new ImageData(msg.pixels, msg.w, msg.h), 0, 0);
    return true;
  }

  function drawInto(li, first) {
    const n = li._node, url = plyUrl(n), lens = li._cam || cam;    // the head card is bigger, so it has its own fit
    if (!url || !lens) { li._fallback.textContent = n.cloud ? 'this cloud has no address on this server' : 'no point cloud in this commit'; return; }
    if (drawn.has(n.id) || pending.has(n.id)) return;
    pending.set(n.id, true);
    li.setAttribute('data-loading', '');
    li._fallback.textContent = 'drawing the room at this commit…';
    const size = cardSize(n), key = `cl:${instance}:${n.id}:${size.w}x${size.h}:v${CAM_V}`;
    shots.ask({ key, url, w: size.w, h: size.h, cam: lens, first }, (out) => {
      pending.delete(n.id);
      li.removeAttribute('data-loading');
      if (out.cached) {
        const img = new Image();
        img.onload = () => { li._canvas.getContext('2d')?.drawImage(img, 0, 0); li.setAttribute('data-drawn', ''); };
        img.onerror = () => { drawn.delete(n.id); li._fallback.textContent = 'the cached thumbnail was unreadable'; };
        img.src = out.cached;
        drawn.add(n.id);
        return;
      }
      if (!paintPixels(li._canvas, out)) { li._fallback.textContent = 'this browser gave no 2D canvas'; return; }
      drawn.add(n.id);
      li.setAttribute('data-drawn', '');
      li.dataset.points = String(out.n);
      if (out.coverage < 0.004) li._fallback.textContent = 'the cloud is there but almost empty at this angle';
      // the PNG costs ~5 ms and saves the whole download next time: do it when nothing else is waiting
      const keep = () => { try { shots.keep(key, li._canvas.toDataURL('image/png')); } catch { /* tainted or out of room: skip */ } };
      (window.requestIdleCallback || ((f) => setTimeout(f, 400)))(keep);
    }, (bad) => {
      pending.delete(n.id);
      li.removeAttribute('data-loading');
      li.setAttribute('data-failed', '');
      li._fallback.textContent = bad.reason || 'this cloud could not be read';
    });
  }

  // Lazily, and newest first: the strip is a long way down a long page, and seven captures are
  // twenty megabytes. Nothing is fetched until the section is nearly on screen.
  function watch() {
    if (watcher) watcher.disconnect();
    watcher = new IntersectionObserver((entries) => {
      const seen = entries.filter((e) => e.isIntersecting).map((e) => e.target);
      if (!seen.length) return;
      for (const li of seen) watcher.unobserve(li);
      // newest first, whatever order the observer reported them in
      seen.sort((a, b) => nodes.indexOf(a._node) - nodes.indexOf(b._node));
      for (const li of seen) drawInto(li, false);
    }, { root: null, rootMargin: '400px 0px', threshold: 0.01 });
    for (const li of strip.children) watcher.observe(li);
  }

  // ---- the rails behind the cards -----------------------------------------------------------------
  function drawRails() {
    if (!place || !strip.children.length) return;
    const base = inner.getBoundingClientRect();
    const W = Math.round(base.width), H = Math.round(base.height);
    if (!W || !H) return;
    rails.setAttribute('width', W); rails.setAttribute('height', H); rails.setAttribute('viewBox', `0 0 ${W} ${H}`);
    rails.replaceChildren();
    // rects, not offsetLeft: the strip is inside a scroller and the two disagree about where zero is
    const at = new Map();
    for (const li of strip.children) {
      const frame = li.querySelector('.cl-frame');
      if (!frame) continue;
      const f = frame.getBoundingClientRect();
      at.set(li.dataset.id, { l: f.left - base.left, r: f.right - base.left, y: f.top - base.top + f.height / 2 });
    }
    const K = 26;
    for (const e of edges) {
      const c = at.get(e.child), p = at.get(e.parent);
      if (!c) continue;
      if (!p) { rails.append(svg('path', { class: 'cl-edge', 'stroke-dasharray': DASH[e.via % 4] || null, d: `M${c.l} ${c.y} H0` })); continue; }
      // parents are to the LEFT: every step of the walk advances by -x
      let d = `M${c.l} ${c.y}`;
      if (Math.abs(c.y - p.y) > 1) d += ` C${c.l - K} ${c.y} ${p.r + K} ${p.y} ${p.r} ${p.y}`;
      else d += ` H${p.r}`;
      rails.append(svg('path', { class: 'cl-edge', 'stroke-dasharray': DASH[e.via % 4] || null, d }));
    }
  }

  // ---- selection: show the room at that commit, larger --------------------------------------------
  function choose(id) {
    selected = selected === id ? null : id;
    for (const li of strip.children) {
      const on = li.dataset.id === selected;
      li.toggleAttribute('data-selected', on);
      li.querySelector('.cl-card')?.setAttribute('aria-pressed', String(on));
    }
    renderDetail();
  }

  function renderDetail() {
    if (!selected) { detail.replaceChildren(); detail.hidden = true; return; }
    const n = nodes.find((x) => x.id === selected);
    if (!n) { detail.replaceChildren(); detail.hidden = true; return; }
    detail.hidden = false;
    const canvas = el('canvas', { class: 'cl-big', width: String(DETAIL.w), height: String(DETAIL.h), 'aria-hidden': 'true' });
    const state = el('p', { class: 'cl-big-state mono', text: n.cloud ? 'drawing this commit’s point cloud…' : 'this commit committed no point cloud' });
    const parent = nodes.find((x) => x.id === (n.parents || [])[0]);
    const facts = [
      ['commit', n.commit_sha ? short(n.commit_sha) : 'not committed yet — a capture beside the repo'],
      ['capture', n.capture_id || '—'],
      ['when', n.at ? `${new Date(n.at).toLocaleString()} · ${ago(n.at)}` : '—'],
      ['points', num(n.points) || '—'],
      ['parent', parent ? `${short(parent.id)} · ${parent.subject}` : (n.parents || []).length ? short(n.parents[0]) : 'the first scan — nothing before it'],
      ['robot', n.robot && typeof n.robot.x === 'number' ? `x ${n.robot.x.toFixed(2)} m · y ${n.robot.y.toFixed(2)} m · yaw ${((n.robot.yaw || 0) * 180 / Math.PI).toFixed(0)}°` : 'not recorded'],
    ];
    const links = el('p', { class: 'cl-links mono' },
      el('a', { href: `/robot?instance=${encodeURIComponent(instance)}`, text: 'open this room →' }),
      n.capture_id ? el('a', { href: `/scene?instance=${encodeURIComponent(instance)}&capture=${encodeURIComponent(n.capture_id)}`, text: 'open the cloud in the viewer →' }) : null,
      n.capture_id ? el('a', { href: `/capture/${encodeURIComponent(n.capture_id)}`, text: 'the capture’s quality gate →' }) : null);
    detail.replaceChildren(
      el('div', { class: 'cl-big-head' },
        el('span', { class: 'cl-kicker mono', text: n.head ? 'the room now' : 'the room then' }),
        el('button', { class: 'cl-close', type: 'button', 'aria-label': 'Close this commit', text: 'esc ✕', onclick: () => choose(selected) })),
      el('p', { class: 'cl-big-title' }, el('span', { class: 'mono', text: short(n.id) }), ' ', n.subject || ''),
      el('div', { class: 'cl-big-grid' },
        el('div', { class: 'cl-big-frame' }, canvas, state),
        el('dl', { class: 'cl-facts-list mono' }, ...facts.flatMap(([k, v]) => [el('dt', { text: k }), el('dd', { text: v })]))),
      links);

    const url = plyUrl(n);
    if (!url || !camDetail) return;
    shots.ask({ key: `cl:${instance}:${n.id}:${DETAIL.w}x${DETAIL.h}:v${CAM_V}`, url, w: DETAIL.w, h: DETAIL.h, cam: camDetail, first: true },
      (out) => {
        if (selected !== n.id) return;
        if (out.cached) { const img = new Image(); img.onload = () => canvas.getContext('2d')?.drawImage(img, 0, 0); img.src = out.cached; state.remove(); return; }
        if (paintPixels(canvas, out)) { state.textContent = `${num(out.n)} points, as they were committed`; state.dataset.done = ''; }
      },
      (bad) => { if (selected === n.id) state.textContent = bad.reason || 'this cloud could not be read'; });
  }

  // ---- stepping through time ------------------------------------------------------------------------
  const ordered = () => [...strip.children];               // DOM order IS time order, oldest first
  function step(delta) {
    const list = ordered();
    if (!list.length) return;
    const i = selected ? list.findIndex((li) => li.dataset.id === selected) : list.length - 1;
    const next = list[Math.min(list.length - 1, Math.max(0, (i < 0 ? list.length - 1 : i) + delta))];
    if (!next || next.dataset.id === selected) return;
    choose(next.dataset.id);
    next.querySelector('.cl-card')?.focus({ preventScroll: true });
    next.scrollIntoView({ block: 'nearest', inline: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    drawInto(next, true);
  }
  scroller.addEventListener('keydown', (e) => {
    if (e.metaKey || e.altKey || e.ctrlKey) return;
    if (e.key === 'ArrowRight' || e.key === ']') { e.preventDefault(); step(1); }
    else if (e.key === 'ArrowLeft' || e.key === '[') { e.preventDefault(); step(-1); }
    else if (e.key === 'Home') { e.preventDefault(); const f = ordered()[0]; if (f) { choose(f.dataset.id); f.scrollIntoView({ inline: 'start' }); } }
    else if (e.key === 'End') { e.preventDefault(); const l = ordered().pop(); if (l) { choose(l.dataset.id); l.scrollIntoView({ inline: 'end' }); } }
    else if (e.key === 'Escape' && selected) { e.preventDefault(); choose(selected); }
  });
  // a vertical trackpad flick over a horizontal strip should walk the strip, not jump the page past it
  scroller.addEventListener('wheel', (e) => {
    if (e.deltaX !== 0 || e.shiftKey) return;              // the browser already does those
    const max = scroller.scrollWidth - scroller.clientWidth;
    if (max <= 1) return;
    const at = scroller.scrollLeft;
    if ((e.deltaY > 0 && at >= max - 1) || (e.deltaY < 0 && at <= 0)) return;     // at the end: let the page scroll on
    e.preventDefault();
    scroller.scrollLeft = at + e.deltaY;
  }, { passive: false });

  // ---- load -------------------------------------------------------------------------------------------
  function say(words) { note.hidden = !words; note.textContent = words || ''; }

  async function boundsFor(list) {
    // the captures' own recorded extents, from the sidecars that exist; a few hundred bytes each
    const sidecars = await Promise.all(list.filter((n) => n.cloud && n.commit_sha).slice(0, 8).map((n) =>
      getJSON(`/api/scene/${encodeURIComponent(instance)}/history/${encodeURIComponent(n.commit_sha)}.json`).catch(() => null)));
    let box = null;
    for (const s of sidecars) {
      const b = s && s.bounds_m;
      if (!b || !Array.isArray(b.min) || !Array.isArray(b.max)) continue;
      box = box ? { min: box.min.map((v, i) => Math.min(v, b.min[i])), max: box.max.map((v, i) => Math.max(v, b.max[i])) }
        : { min: [...b.min], max: [...b.max] };
    }
    // no sidecar anywhere: a room-sized box in the capture frame (x forward, y left, z up, floor at 0)
    if (!box) box = { min: [-0.5, -3.5, -0.2], max: [5, 3.5, 2.6] };
    const pad = 0.06, span = box.max.map((v, i) => (v - box.min[i]) * pad);
    return { min: box.min.map((v, i) => v - span[i]), max: box.max.map((v, i) => v + span[i]) };
  }

  async function load() {
    let list;
    try {
      list = await getJSON('/api/scene/instances');
    } catch (e) {
      say(e.status === 404 ? 'GET /api/scene/instances is not live on this server yet, so there are no clouds to draw.' : `the room’s instances could not be read: ${e.message}`);
      where.textContent = 'no instance';
      return;
    }
    const all = (list.instances || []).filter((i) => i.commits > 0);
    const pick = all.find((i) => i.name === list.current) || all.slice().sort((a, b) => b.commits - a.commits)[0];
    if (!pick) { say('No room instance on this machine has a commit yet, so there is no history to draw.'); where.textContent = 'no instance'; return; }
    instance = pick.name;

    let doc;
    try { doc = await getJSON(`/api/scene/${encodeURIComponent(instance)}/history`); }
    catch (e) { say(`the history of ${instance} could not be read: ${e.message}`); return; }
    data = doc;
    nodes = (doc.commits || []).map((c) => ({ ...c, id: c.id || c.sha }));
    if (!nodes.length) { say(`${instance} has no commits yet.`); where.textContent = instance; return; }

    const walked = layout(nodes);
    place = walked.place; edges = walked.edges; laneCount = walked.laneCount;

    const withCloud = nodes.filter((n) => n.cloud).length;
    where.textContent = `${instance} · ${nodes.length} commit${nodes.length === 1 ? '' : 's'} · ${withCloud} with a point cloud`
      + (laneCount > 1 ? ` · ${laneCount} lanes` : '');

    strip.style.setProperty('--lanes', String(laneCount));
    const total = nodes.length;
    strip.replaceChildren(...nodes.map((n) => {
      const li = makeCard(n);
      const p = place.get(n.id);
      li.style.gridColumn = String(total - p.row);        // mirror the walk: oldest on the left
      li.style.gridRow = String(p.lane + 1);
      return li;
    }).sort((a, b) => Number(a.style.gridColumn) - Number(b.style.gridColumn)));

    const box = await boundsFor(nodes);
    cam = frameFor(box, CARD.w, CARD.h, 2);
    camDetail = frameFor(box, DETAIL.w, DETAIL.h, 2);
    // the head card is bigger, so it needs its own fit at that size
    const camHead = frameFor(box, HEAD_CARD.w, HEAD_CARD.h, 2);
    for (const li of strip.children) if (li._node.head) li._cam = camHead;

    drawRails();
    watch();
    scroller.scrollLeft = scroller.scrollWidth;            // open on the room now
  }

  new ResizeObserver(() => drawRails()).observe(strip);
  window.addEventListener('resize', drawRails, { passive: true });
  load();

  return { reload: load, get instance() { return instance; } };
}
