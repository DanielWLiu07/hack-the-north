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
const MAX_CACHE = 32;                                     // thumbnails AND the larger view, per session
// The captures' recorded bounds are the whole hallway, and the cloud only fills part of it. Fitting the
// bare box leaves the room a stamp in the middle of an empty card, so the frame is pulled in and the
// far corners are allowed to clip — the same crop for every node, which is what keeps them comparable.
const ZOOM = 1.8;

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
function frameFor(box, w, h, splat, fixedK) {
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
  // a fixed k is how the bigger head card stays comparable: same metres per pixel, more room around it
  const k = fixedK || Math.min((w - pad * 2) / (u1 - u0 || 1), (h - pad * 2) / (v1 - v0 || 1)) * ZOOM;
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
      if (ev.data.ok) job.done({ ...ev.data, key: job.key }); else if (!ev.data.aborted) job.fail(ev.data);
      this.pump();
      if (!this.busy && this.onIdle) this.onIdle();
    };
    this.worker.onerror = () => { const job = this.busy; this.busy = null; if (job) job.fail({ reason: 'the thumbnail worker stopped' }); };
    return this.worker;
  }
  // `first` puts a job at the head of the line: what somebody just clicked beats what is merely on screen.
  ask({ key, url, w, h, cam, first }, done, fail) {
    const cached = this.cached(key);
    if (cached) { done({ cached, key }); return; }
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
  // Only the CARD thumbnails come through here — a few tens of kB each. The larger view is never
  // stored: it would be most of the quota for a picture somebody looked at once.
  keep(key, dataUrl) {
    if (!this.store) return;
    const mine = () => { try { return Object.keys(this.store).filter((k) => k.startsWith('cl:')); } catch { return []; } };
    const evict = () => { const k = mine()[0]; if (!k) return false; try { this.store.removeItem(k); return true; } catch { return false; } };
    while (mine().length >= MAX_CACHE) if (!evict()) return;
    for (let i = 0; i < 4; i++) {                         // a full store is not a reason to stop: make room and retry
      try { this.store.setItem(key, dataUrl); return; } catch { if (!evict()) return; }
    }
  }
}
function safeStorage() { try { const s = sessionStorage; s.setItem('cl:probe', '1'); s.removeItem('cl:probe'); return s; } catch { return null; } }

// ---- the timeline ------------------------------------------------------------------------------
export function mount(host) {
  const shots = new Shots();
  shots.onIdle = () => flush();       // nothing left to draw: the PNG cache may have the idle time now
  let nodes = [], place = null, edges = [], laneCount = 1, cam = null, camDetail = null;
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
  const detail = el('aside', { class: 'cl-detail', hidden: true, 'aria-live': 'polite' });
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
  // A node that is a real commit carries the cloud IN its tree; a capture that has not been committed
  // is only a file beside the repo. Both are tried, in that order, because `cloud: true` can be true
  // of the capture FILE while the commit itself holds none — cap_0007 is exactly that today: the
  // first scan committed room.yaml and the anchors, and the .ply only ever sat in <instance>.scene/.
  function plyUrls(n) {
    if (!n.cloud) return [];
    const inst = encodeURIComponent(instance);
    return [n.commit_sha && `/api/scene/${inst}/history/${encodeURIComponent(n.commit_sha)}.ply`,
      n.capture_id && `/api/scene/${inst}/${encodeURIComponent(n.capture_id)}.ply`].filter(Boolean);
  }
  // walk the candidates: a 404 on the first is an answer about THAT address, not about the cloud
  function askCloud(n, size, lens, first, done, fail) {
    const urls = plyUrls(n);
    const tryAt = (i) => {
      if (i >= urls.length) { fail({ reason: urls.length ? 'no address on this server holds this cloud' : 'no point cloud in this commit' }); return; }
      shots.ask({ key: `cl:${instance}:${n.id}:${i}:${size.w}x${size.h}:v${CAM_V}`, url: urls[i], w: size.w, h: size.h, cam: lens, first },
        done, (bad) => (bad.soft && i + 1 < urls.length ? tryAt(i + 1) : fail(bad)));
    };
    tryAt(0);
  }

  function paintPixels(canvas, msg) {
    const g = canvas.getContext('2d');                    // 2D, never webgl: the page's one context belongs to the hero
    if (!g) return false;
    g.putImageData(new ImageData(msg.pixels, msg.w, msg.h), 0, 0);
    return true;
  }

  function drawInto(li, first) {
    const n = li._node, lens = li._cam || cam;             // the head card is bigger, so it has its own fit
    if (!lens || !plyUrls(n).length) { li._fallback.textContent = 'no point cloud in this commit'; return; }
    if (drawn.has(n.id) || pending.has(n.id)) return;
    pending.set(n.id, true);
    li.setAttribute('data-loading', '');
    li._fallback.textContent = 'drawing the room at this commit…';
    askCloud(n, cardSize(n), lens, first, (out) => {
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
      if (out.coverage < 0.004) li._fallback.textContent = 'the cloud is there but almost empty at this angle';
      keepLater(out.key, li._canvas);
    }, (bad) => {
      pending.delete(n.id);
      li.removeAttribute('data-loading');
      li.setAttribute('data-failed', '');
      li._fallback.textContent = bad.reason || 'this cloud could not be read';
    });
  }

  // Encoding a PNG is the one piece of this that costs the main thread anything (a few ms a card), and
  // it buys only a faster SECOND visit. So it waits until every cloud has been drawn and then goes one
  // card per idle callback: it must never compete with a frame while the strip is still filling in.
  const queued = [];
  let flushing = false;
  function keepLater(key, canvas) {
    if (!key || !shots.store) return;
    queued.push([key, canvas]);
    flush();
  }
  function flush() {
    if (flushing || !queued.length || shots.busy || shots.queue.length) return;
    flushing = true;
    (window.requestIdleCallback || ((f) => setTimeout(f, 500)))(() => {
      flushing = false;
      const next = queued.shift();
      if (next) { try { shots.keep(next[0], next[1].toDataURL('image/png')); } catch { /* full, blocked or tainted: skip */ } }
      flush();
    });
  }

  // The section is a long way down a long page and seven captures are twenty megabytes, so nothing
  // is fetched until the strip is nearly on screen. Then all of them are queued NEWEST FIRST, one at
  // a time: a card scrolled out sideways never intersects on its own, and the oldest commits would
  // sit blank until somebody dragged the strip.
  function watch() {
    if (watcher) watcher.disconnect();
    watcher = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      watcher.disconnect(); watcher = null;
      for (const n of nodes) {                              // `nodes` is newest first
        const li = [...strip.children].find((x) => x.dataset.id === n.id);
        if (li) drawInto(li, false);
      }
    }, { root: null, rootMargin: '900px 0px', threshold: 0.01 });
    watcher.observe(scroller);
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

  // What CHANGED at this commit, in objects rather than points. One commit is one point cloud AND the
  // object records found in it, so the same two shas answer both questions: GET .../diff?a=&b= is the
  // object diff room.git's own graph uses (web/objdiff.py), pointed at this instance's repo. A stable
  // object_id across two commits already MEANS the same physical thing — perception decided that, and
  // `first_seen` is what tells a genuinely new object from an old one that only moved.
  const cm = (m) => (typeof m === 'number' ? `${(m * 100).toFixed(m < 0.1 ? 1 : 0)} cm` : null);
  // `first_seen` is carried forward by roomctl and never re-derived, so an object that appears here
  // and was first seen here is genuinely new; one that appears with an OLDER first_seen left and came
  // back. Saying "new" for both would be the one lie this panel must not tell.
  const RETURN_MS = 90_000;
  function newHere(o, at) {
    if (!o.first_seen || !at) return null;
    const dt = new Date(at).getTime() - new Date(o.first_seen).getTime();
    return isFinite(dt) ? dt <= RETURN_MS : null;
  }
  function opRow(o, at) {
    const fresh = o.op === 'added' ? newHere(o, at) : null;
    const what = o.op === 'moved'
      ? ['moved', cm(o.delta_m), o.delta_yaw_deg ? `turned ${Math.abs(Math.round(o.delta_yaw_deg))}°` : null,
        o.from_zone && o.from_zone !== o.zone ? `${o.from_zone} → ${o.zone}` : null].filter(Boolean).join(' · ')
      : o.op === 'added' ? (fresh === null ? 'appeared; the room does not record when it was first seen'
        : fresh ? 'new to the room' : `returned — first seen ${ago(o.first_seen)}`)
      : o.op === 'removed' ? 'gone from the room' : 'its record changed; it did not move';
    return el('li', { class: 'cl-op', 'data-op': o.op },
      el('span', { class: 'cl-swatch', style: `background:${/^#[0-9a-f]{3,8}$/i.test(o.color || '') ? o.color : 'transparent'}`, 'aria-hidden': 'true' }),
      el('a', { class: 'mono', href: `/object/${encodeURIComponent(o.object_id)}`, text: `${o.object_id}${o.class && o.class !== 'unknown' ? ` · ${o.class}` : ''}` }),
      el('span', { class: 'cl-op-what', text: what }),
      el('span', { class: 'cl-op-zone mono', text: o.zone ? `zones/${o.zone}` : '' }));
  }
  async function renderOps(box, n, parent) {
    if (!n.commit_sha || !parent || !parent.commit_sha) {
      box.replaceChildren(el('p', { class: 'cl-dim', text: !parent ? 'The first scan: there is nothing before it to compare.'
        : 'One of these two nodes is a capture beside the repo, not a commit, so git holds no object records to diff.' }));
      return;
    }
    box.replaceChildren(el('p', { class: 'cl-dim', text: 'reading what changed…' }));
    let d;
    try { d = await getJSON(`/api/scene/${encodeURIComponent(instance)}/diff?a=${encodeURIComponent(parent.commit_sha)}&b=${encodeURIComponent(n.commit_sha)}`); }
    catch (e) { box.replaceChildren(el('p', { class: 'cl-dim', text: `the object diff could not be read: ${e.message}` })); return; }
    if (selected !== n.id) return;
    const s = d.summary || {}, rows = (d.ops || []).filter((o) => o.object_id);
    const counted = [['moved', s.moved], ['added', s.added], ['removed', s.removed], ['record changed', s.changed]]
      .filter(([, k]) => k).map(([w, k]) => `${k} ${w}`).join(' · ');
    box.replaceChildren(...[                              // replaceChildren does NOT drop a null: it prints one
      el('p', { class: 'cl-ops-k mono', text: `what changed, in objects  ·  ${short(parent.id)} → ${short(n.id)}` }),
      el('p', { class: 'cl-dim', text: counted
        ? `${counted}. The room holds ${d.objects ? d.objects.b : '—'} object${d.objects && d.objects.b === 1 ? '' : 's'} here.`
        : `No object record changed. Both commits hold ${d.objects ? d.objects.b : '—'} object${d.objects && d.objects.b === 1 ? '' : 's'}.` }),
      rows.length ? el('ul', { class: 'cl-op-list' }, ...rows.slice(0, 8).map((o) => opRow(o, (d.at && d.at.b) || n.at))) : null,
      rows.length > 8 ? el('p', { class: 'cl-dim', text: `…and ${rows.length - 8} more.` }) : null,
    ].filter(Boolean));
  }

  function renderDetail() {
    if (!selected) { detail.replaceChildren(); detail.hidden = true; return; }
    const n = nodes.find((x) => x.id === selected);
    if (!n) { detail.replaceChildren(); detail.hidden = true; return; }
    detail.hidden = false;
    const canvas = el('canvas', { class: 'cl-big', width: String(DETAIL.w), height: String(DETAIL.h), 'aria-hidden': 'true' });
    const state = el('p', { class: 'cl-big-state mono', text: n.cloud ? 'drawing this commit’s point cloud…' : 'this commit committed no point cloud' });
    // the recorded count and the committed cloud can differ: `add` writes a 2 cm cell grid, so a
    // 465,173-point capture is committed as 110,062 points. Say which number is on screen.
    const parent = nodes.find((x) => x.id === (n.parents || [])[0]);
    const facts = [
      ['commit', n.commit_sha ? short(n.commit_sha) : 'not committed yet — a capture beside the repo'],
      ['capture', n.capture_id || '—'],
      ['when', n.at ? `${new Date(n.at).toLocaleString()} · ${ago(n.at)}` : '—'],
      ['points recorded', num(n.points) || '—'],
      ['parent', parent ? `${short(parent.id)} · ${parent.subject}` : (n.parents || []).length ? short(n.parents[0]) : 'the first scan — nothing before it'],
      ['robot', n.robot && typeof n.robot.x === 'number' ? `x ${n.robot.x.toFixed(2)} m · y ${n.robot.y.toFixed(2)} m · yaw ${((n.robot.yaw || 0) * 180 / Math.PI).toFixed(0)}°` : 'not recorded'],
    ];
    const links = el('p', { class: 'cl-links mono' },
      el('a', { href: `/robot?instance=${encodeURIComponent(instance)}${n.capture_id ? `&capture=${encodeURIComponent(n.capture_id)}` : ''}`,
        text: 'open this commit in the room →' }),
      n.capture_id ? el('a', { href: `/scene?instance=${encodeURIComponent(instance)}&capture=${encodeURIComponent(n.capture_id)}`, text: 'open the cloud in the viewer →' }) : null,
      n.capture_id ? el('a', { href: `/capture/${encodeURIComponent(n.capture_id)}`, text: 'the capture’s quality gate →' }) : null);
    const ops = el('div', { class: 'cl-ops' });
    detail.replaceChildren(
      el('div', { class: 'cl-big-head' },
        el('span', { class: 'cl-kicker mono', text: n.head ? 'the room now' : 'the room then' }),
        el('button', { class: 'cl-close', type: 'button', 'aria-label': 'Close this commit', text: 'esc ✕', onclick: () => choose(selected) })),
      el('p', { class: 'cl-big-title' }, el('span', { class: 'mono', text: short(n.id) }), ' ', n.subject || ''),
      el('div', { class: 'cl-big-grid' },
        el('div', { class: 'cl-big-frame' }, canvas, state),
        el('div', { class: 'cl-aside' },
          el('dl', { class: 'cl-facts-list mono' }, ...facts.flatMap(([k, v]) => [el('dt', { text: k }), el('dd', { text: v })])),
          ops)),
      links);
    renderOps(ops, n, parent);

    if (!camDetail || !plyUrls(n).length) return;
    askCloud(n, DETAIL, camDetail, true,
      (out) => {
        if (selected !== n.id) return;
        if (out.cached) { const img = new Image(); img.onload = () => canvas.getContext('2d')?.drawImage(img, 0, 0); img.src = out.cached; state.remove(); return; }
        if (paintPixels(canvas, out)) { state.textContent = `${num(out.n)} points drawn, as this node holds them`; state.dataset.done = ''; }
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
    nodes = (doc.commits || []).map((c) => ({ ...c, id: c.id || c.sha }));
    if (!nodes.length) { say(`${instance} has no commits yet.`); where.textContent = instance; return; }

    const walked = layout(nodes);
    place = walked.place; edges = walked.edges; laneCount = walked.laneCount;

    const withCloud = nodes.filter((n) => n.cloud).length;
    const on = doc.detached ? `detached at ${short(doc.head || '')}` : (doc.branch || null);
    where.textContent = [instance, on, `${nodes.length} commit${nodes.length === 1 ? '' : 's'}`,
      `${withCloud} with a point cloud`, laneCount > 1 ? `${laneCount} lanes` : null,
      'one camera, from behind the robot'].filter(Boolean).join(' · ');

    // a rebuild must forget what it had drawn: the cards are new elements with blank canvases
    drawn.clear(); pending.clear();
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
    camDetail = frameFor(box, DETAIL.w, DETAIL.h, 3);
    // the head card is bigger but NOT closer: same metres per pixel, so the newest frame of the film
    // can still be laid beside the one before it
    const camHead = frameFor(box, HEAD_CARD.w, HEAD_CARD.h, 2, cam.k);
    for (const li of strip.children) if (li._node.head) li._cam = camHead;

    drawRails();
    watch();
    scroller.scrollLeft = scroller.scrollWidth;            // open on the room now
  }

  // a commit landing anywhere (roomctl, `git add · current` on /robot) puts a new frame on the film
  function listen() {
    if (!('EventSource' in window)) return;
    const es = window.gitrlEvents || new EventSource('/api/events');
    let timer = 0;
    es.addEventListener('capture', () => { clearTimeout(timer); timer = setTimeout(() => load().catch(() => {}), 400); });
  }

  new ResizeObserver(() => drawRails()).observe(strip);
  window.addEventListener('resize', drawRails, { passive: true });
  load();
  listen();

  return { reload: load, get instance() { return instance; } };
}
