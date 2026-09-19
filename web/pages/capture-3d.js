// capture-3d.js — what the robot saw, inside the telemetry row itself.
//
// One panel per capture in the ledger. The bytes are the same PLY scripts/room_live.py writes, read by
// scene-model.js's readPly (the one parser on this site; nothing here re-implements it), thinned to a
// thumbnail's worth of points and drawn by ONE renderer shared by every row.
//
// WHY ONE RENDERER. A browser keeps about 16 WebGL contexts alive and drops the oldest without asking,
// so a canvas+renderer per row breaks the page at row 17 and wastes memory long before that. Instead:
// a single off-screen WebGLRenderer draws one row at a time and the frame is copied (drawImage) into
// that row's own 2D canvas. Twenty rows on screen cost exactly one context.
//
// WHAT IS NEVER DONE: nothing renders on a timer. A row is drawn when it appears, when it is resized,
// and while it is being dragged — never otherwise. A cloud is fetched only once its row is near the
// viewport, at most two at a time, and the fetch is aborted if the row leaves again before it lands.
//
// HONESTY. A capture with no point cloud says so in one line and draws nothing: no empty black box.
// The reason printed is the reason /api/scene/find gave, never a guess.
import * as THREE from 'three';
import { readPly } from '/pages/scene-model.js';

const MAX_POINTS = 40000;         // a thumbnail's worth: a 460k-point capture is drawn 1 in 12
const FOV = 50;
const CONCURRENCY = 2;            // PLYs are 5–7 MB each; twenty rows must not ask for all of them at once
const CACHE_MAX = 32;             // clouds kept on the GPU (~600 kB each once thinned)
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const period = (s) => (/[.!?]$/.test(s || '') ? s : `${s}.`);

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(2)) if (kid != null && kid !== false) n.append(kid.nodeType ? kid : String(kid));
  return n;
}
const sentence = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

// ── the ONE renderer ──────────────────────────────────────────────────────────────
// Created on the first row that actually has a cloud, so a board with no 3D at all starts no context.
let stage;                        // undefined = not asked yet; { failed: true, why } = WebGL said no

function makeStage() {
  const canvas = document.createElement('canvas');
  let renderer;
  try {
    // preserveDrawingBuffer: the frame is read back with drawImage; low-power: these are thumbnails.
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, preserveDrawingBuffer: true, powerPreference: 'low-power' });
  } catch (e) {
    return { failed: true, why: 'this browser could not start WebGL, so the point cloud cannot be drawn' };
  }
  renderer.setClearColor(0x0b0b0f, 1);
  const scene = new THREE.Scene();
  const grid = new THREE.GridHelper(12, 12, 0x555561, 0x2b2b34);      // the floor, z = 0, so a room reads as a room
  grid.rotation.x = Math.PI / 2;
  grid.material.transparent = true; grid.material.opacity = 0.5; grid.material.depthWrite = false;
  scene.add(grid);
  const holder = new THREE.Group();                                   // whichever row is being drawn lives here
  scene.add(holder);
  const camera = new THREE.PerspectiveCamera(FOV, 1, 0.05, 120);
  camera.up.set(0, 0, 1);                                             // the models are z-up, floor at 0
  // A point is a size in the ROOM clamped to a few pixels — the same trick scene-model.js uses, minus the
  // ramp, crop and height uniforms a thumbnail has no controls for.
  const material = new THREE.ShaderMaterial({
    uniforms: { uSize: { value: 0.032 }, uScale: { value: 420 }, uMaxPx: { value: 9 } },
    vertexShader: `uniform float uSize, uScale, uMaxPx; attribute vec3 rgb; varying vec3 vColor;
      void main() {
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_Position = projectionMatrix * mv;
        gl_PointSize = clamp(uSize * uScale / max(-mv.z, 0.05), 1.0, uMaxPx);
        vColor = rgb;
      }`,
    fragmentShader: `varying vec3 vColor; void main() {
      vec2 d = gl_PointCoord - 0.5; if (dot(d, d) > 0.25) discard;
      gl_FragColor = vec4(vColor, 1.0);
    }`,
  });
  canvas.addEventListener('webglcontextlost', (e) => {
    e.preventDefault();                                               // say it out loud rather than freeze the last frame
    stage = { failed: true, why: 'the browser dropped this page’s WebGL context; reload to draw the clouds again' };
    for (const v of views) if (v.state === 'ready' || v.state === 'loading') fail(v, stage.why);
  });
  return { canvas, renderer, scene, holder, camera, material, w: 0, h: 0, failed: false };
}
function getStage() {
  if (stage === undefined) stage = makeStage();
  return stage;
}

// ── reading a PLY into a thumbnail ────────────────────────────────────────────────
// scene-model.js's readPly fills a Layer's arrays; all it wants of one is buffers(n) and filled(n). This
// is that, with ONE pair of arrays reused by every capture on the page — the big allocation happens once,
// and what each row keeps is only the thinned copy.
const scratch = {
  cap: 0, xyz: null, rgb: null, n: 0,
  buffers(n) {
    if (n > this.cap) { this.cap = Math.ceil(n * 1.1); this.xyz = new Float32Array(this.cap * 3); this.rgb = new Uint8Array(this.cap * 3); }
    return [this.xyz, this.rgb];
  },
  filled(n) { this.n = n; },
};

function thin(n) {
  // every step-th point, so the sample covers the whole room (a prefix would be whatever the scan wrote first)
  const step = Math.max(1, Math.ceil(n / MAX_POINTS)), m = Math.ceil(n / step);
  const xyz = new Float32Array(m * 3), rgb = new Uint8Array(m * 3);
  const sx = scratch.xyz, sr = scratch.rgb;
  let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity], k = 0;
  for (let i = 0; i < n; i += step, k += 3) {
    const p = i * 3;
    for (let a = 0; a < 3; a++) {
      const v = sx[p + a];
      xyz[k + a] = v; rgb[k + a] = sr[p + a];
      if (v < lo[a]) lo[a] = v; if (v > hi[a]) hi[a] = v;
    }
  }
  const drawn = k / 3;
  return { xyz, rgb, drawn, step, bounds: drawn && hi[0] >= lo[0] ? { min: lo, max: hi } : null };
}

// ── the clouds already on the GPU ─────────────────────────────────────────────────
// Keyed by capture_id and owned here, not by a row: the board rebuilds every card on a resize or when a
// new capture lands over SSE, and a rebuild must not re-download 6 MB.
const clouds = new Map();
function remember(id, cloud) {
  const had = clouds.get(id);
  if (had && had !== cloud) had.points.geometry.dispose();
  clouds.set(id, cloud);
  while (clouds.size > CACHE_MAX) {
    const inUse = new Set([...views].map((v) => v.cloud).filter(Boolean));
    const spare = [...clouds].find(([, c]) => !inUse.has(c));
    if (!spare) break;                              // every cloud is on screen: keeping them costs less than redrawing nothing
    clouds.delete(spare[0]);
    spare[1].points.geometry.dispose();
  }
}
function buildCloud(id, t, meta) {
  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(t.xyz, 3));
  geom.setAttribute('rgb', new THREE.BufferAttribute(t.rgb, 3, true));
  const points = new THREE.Points(geom, getStage().material);
  points.frustumCulled = false;
  const b = t.bounds;
  const target = new THREE.Vector3(0, 0, 0.5);
  let radius = 4;
  if (b) {
    target.set((b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2, Math.min(1.1, Math.max(0.3, (b.min[2] + b.max[2]) / 2)));
    radius = Math.max(1.8, Math.max(b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]) * 1.15);
  }
  const cloud = { points, target, radius, drawn: t.drawn, step: t.step, total: meta.total, tilt: meta.tilt };
  remember(id, cloud);
  return cloud;
}

// ── drawing: one shared renderer, one row at a time, on demand only ───────────────
const views = new Set();
const byBox = new Map();          // the observed element -> its view
const queued = new Set();
let raf = 0;

function invalidate(v) {
  if (v.state !== 'ready' || !v.canvas) return;
  queued.add(v);
  if (!raf) raf = requestAnimationFrame(flush);
}
function flush() {
  raf = 0;
  const batch = [...queued];
  queued.clear();
  for (const v of batch) draw(v);
}
function draw(v) {
  const st = getStage();
  if (st.failed || !v.cloud || !v.canvas) return;
  const w = v.canvas.width, h = v.canvas.height;
  if (!w || !h) return;
  if (st.w !== w || st.h !== h) { st.renderer.setSize(w, h, false); st.w = w; st.h = h; }
  st.camera.aspect = w / h;
  st.camera.updateProjectionMatrix();
  st.material.uniforms.uScale.value = h / (2 * Math.tan((FOV * Math.PI) / 360));
  const { target, radius } = v.cloud, s = Math.sin(v.phi);
  st.camera.position.set(
    target.x + radius * s * Math.sin(v.theta),
    target.y + radius * s * Math.cos(v.theta),
    target.z + radius * Math.cos(v.phi));
  st.camera.lookAt(target);
  st.holder.clear();
  st.holder.add(v.cloud.points);
  st.renderer.render(st.scene, st.camera);
  v.ctx.drawImage(st.canvas, 0, 0, w, h, 0, 0, w, h);     // same tick as the render: the frame is still there
}

// ── loading, at most two at a time, cancelled when the row scrolls away ───────────
const finds = new Map();          // capture_id -> promise of /api/scene/find's answer
function find(id) {
  if (!finds.has(id)) {
    finds.set(id, fetch(`/api/scene/find/${encodeURIComponent(id)}`, { headers: { accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : { available: false, reason: `the server answered HTTP ${r.status} when asked where this capture's model is` }))
      .catch((e) => ({ available: false, reason: `this page could not reach its own server (${e.message})` })));
  }
  return finds.get(id);
}

let running = 0;
const jobs = [];
function pump() {
  while (running < CONCURRENCY && jobs.length) {
    const job = jobs.shift();
    if (stale(job.v, job.gen)) continue;                // it scrolled away while it waited its turn
    running += 1;
    job.run().catch(() => {}).finally(() => { running -= 1; pump(); });
  }
}
// A row that scrolls away and comes back starts a NEW attempt; the old one must not also finish, or the
// same capture is fetched twice and the loser's geometry is stranded on the GPU.
const stale = (v, gen) => v.state !== 'loading' || v.gen !== gen;

// The scratch arrays are shared, so only one capture may be in readPly at a time. It is: everything from
// the arrayBuffer() onwards is synchronous, so two of these can never interleave over the parse.
async function readCloud(v, sc) {
  const r = await fetch(sc.ply, { cache: 'no-store', signal: v.ac.signal });
  if (r.status === 403) return { why: 'a coloured cloud of a real room is served on this laptop only; open the page as http://localhost:8000 here' };
  if (!r.ok) return { why: r.status === 404 ? 'the point cloud file is gone (deleted, or another ROOM_LIVE_DIR)' : `the point cloud did not load (HTTP ${r.status})` };
  const buf = new Uint8Array(await r.arrayBuffer());
  if (v.ac.signal.aborted) return { cancelled: true };
  try { readPly(buf, scratch); } catch (e) { return { why: e.message }; }
  return { cloud: buildCloud(v.id, thin(scratch.n), { total: scratch.n, tilt: sc.tilt_rate_max }) };
}

function ensure(v) {
  if (v.state !== 'idle') return;
  const cached = clouds.get(v.id);
  if (cached) { show(v, cached); return; }
  v.state = 'loading';
  v.gen = (v.gen || 0) + 1;
  v.ac = new AbortController();
  v.box.dataset.state = 'wait';
  v.box.replaceChildren(el('p', { class: 'cap3d-line' }, 'looking for this capture’s 3D model…'));
  const gen = v.gen;
  const run = async () => {
    const sc = await find(v.id);
    if (stale(v, gen)) return;
    if (sc.ply && !getStage().failed) {
      v.box.replaceChildren(el('p', { class: 'cap3d-line' }, `reading ${(sc.size_mb || 0).toFixed(1)} MB of points…`));
      const got = await readCloud(v, sc);
      if (got.cancelled || stale(v, gen)) return;
      if (got.cloud) { show(v, got.cloud); return; }
      if (sc.png) { showPng(v, sc.png, got.why); return; }             // the picture rendered beside it is still true
      fail(v, got.why);
      return;
    }
    if (sc.png) { showPng(v, sc.png, sc.ply ? getStage().why : sc.reason); return; }
    fail(v, sc.ply ? getStage().why : sc.reason || 'no 3D was recorded for this capture');
  };
  jobs.push({ v, gen, run });
  pump();
}
function cancel(v) {
  if (v.state !== 'loading') return;
  v.ac.abort();
  v.state = 'idle';
  v.box.dataset.state = 'wait';
  v.box.replaceChildren(el('p', { class: 'cap3d-line' }, 'scroll here to load it'));
}

// ── one panel ─────────────────────────────────────────────────────────────────────
function show(v, cloud) {
  v.state = 'ready';
  v.cloud = cloud;
  v.theta = -Math.PI / 2; v.phi = 1.12;               // behind the robot, a little above: roughly where its head was
  const canvas = el('canvas', { class: 'cap3d-canvas', tabindex: '0', role: 'img',
    'aria-label': `${v.id} as a point cloud: ${cloud.drawn.toLocaleString()} of its ${cloud.total.toLocaleString()} points. Drag, or use the arrow keys, to turn it.` });
  v.canvas = canvas;
  v.ctx = canvas.getContext('2d');
  v.box.replaceChildren(canvas);
  v.box.dataset.state = 'cloud';
  turnable(v, canvas);
  sizeCanvas(v);
  v.cap.replaceChildren(
    `${cloud.total.toLocaleString()} points`,
    cloud.step > 1 ? ` · 1 in ${cloud.step} drawn` : ' · all drawn',
    typeof cloud.tilt === 'number' && cloud.tilt >= 0.05 ? el('span', { class: 'cap3d-wrong' }, ` · tilt ${cloud.tilt.toFixed(3)} rad/s`) : '',
    el('span', { class: 'cap3d-hint' }, '\ndrag to turn'));    // its own line, and its own word when read aloud
  invalidate(v);
}
function showPng(v, png, why) {
  v.state = 'png';
  v.box.dataset.state = 'png';
  v.box.replaceChildren(el('img', { class: 'cap3d-img', src: png, alt: `${v.id}, rendered from two views by the scene server`, loading: 'lazy', decoding: 'async' }));
  v.cap.replaceChildren(why ? `the rendered picture · ${why}` : 'the rendered picture of this capture');
}
function fail(v, why) {
  v.state = 'none';
  v.cloud = null; v.canvas = null; v.ctx = null;
  v.box.dataset.state = 'none';
  v.box.replaceChildren(el('p', { class: 'cap3d-line' }, period(sentence(why || 'no 3D was recorded for this capture'))));
  v.cap.replaceChildren();
}

// A drag costs one frame per pointermove and nothing at all when the pointer is still: no loop, no inertia.
function turnable(v, canvas) {
  let id = null, lx = 0, ly = 0;
  const turn = (dx, dy) => {
    const k = (2 * Math.PI) / Math.max(1, canvas.clientHeight);
    v.theta -= dx * k;
    v.phi = Math.min(Math.PI - 0.1, Math.max(0.1, v.phi - dy * k));
    invalidate(v);
  };
  canvas.addEventListener('pointerdown', (e) => {
    if (id !== null) return;
    id = e.pointerId; lx = e.clientX; ly = e.clientY;
    canvas.setPointerCapture(id);
    canvas.classList.add('turning');
    e.preventDefault();
  });
  canvas.addEventListener('pointermove', (e) => {
    if (e.pointerId !== id) return;
    turn(e.clientX - lx, e.clientY - ly);
    lx = e.clientX; ly = e.clientY;
  });
  for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    canvas.addEventListener(name, (e) => { if (e.pointerId === id) { id = null; canvas.classList.remove('turning'); } });
  }
  canvas.addEventListener('keydown', (e) => {
    const step = REDUCED ? 40 : 22;
    const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
    if (!d) return;
    e.preventDefault();
    turn(d[0], d[1]);
  });
}

function sizeCanvas(v) {
  if (!v.canvas) return;
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const w = Math.max(1, Math.round(v.box.clientWidth * dpr)), h = Math.max(1, Math.round(v.box.clientHeight * dpr));
  if (v.canvas.width !== w || v.canvas.height !== h) { v.canvas.width = w; v.canvas.height = h; }
}

let io = null, ro = null;
function observe(v) {
  if (!io) {
    io = new IntersectionObserver((ents) => {
      for (const e of ents) {
        const view = byBox.get(e.target);
        if (!view) continue;
        view.near = e.isIntersecting;
        if (e.isIntersecting) { ensure(view); invalidate(view); } else cancel(view);
      }
    }, { rootMargin: '400px 0px' });
    ro = new ResizeObserver((ents) => {
      for (const e of ents) {
        const view = byBox.get(e.target);
        if (!view) continue;
        sizeCanvas(view);
        invalidate(view);
      }
    });
  }
  io.observe(v.box);
  ro.observe(v.box);
}

/**
 * A capture's 3D panel, ready to drop into its ledger row. Nothing is fetched until the row is near the
 * viewport; nothing is drawn until there is something real to draw.
 * @param {string} captureId
 * @returns {{ el: HTMLElement, dispose: () => void }}
 */
export function captureView(captureId) {
  const box = el('div', { class: 'cap3d-box', 'data-state': 'wait' }, el('p', { class: 'cap3d-line' }, 'scroll here to load it'));
  const cap = el('p', { class: 'cap3d-cap' });
  const root = el('div', { class: 'cap3d' }, box, cap);
  const v = { id: captureId, box, cap, root, state: 'idle', gen: 0, cloud: null, canvas: null, ctx: null, theta: -Math.PI / 2, phi: 1.12, ac: null, near: false };
  views.add(v);
  byBox.set(box, v);
  observe(v);
  return {
    el: root,
    dispose() {
      cancel(v);
      views.delete(v);
      byBox.delete(box);
      queued.delete(v);
      if (io) io.unobserve(box);
      if (ro) ro.unobserve(box);
      v.cloud = null; v.canvas = null; v.ctx = null;      // the geometry belongs to the cache, not to this row
    },
  };
}

/** For a test or a console: how many WebGL contexts this module has taken. It is 0 or 1, never per row. */
export function contextsUsed() { return stage === undefined || stage.failed ? 0 : 1; }
