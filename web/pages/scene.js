// scene.js — /scene: the room's 3D model, turning in a tab (web/scene_api.py serves it, to this laptop only). Two kinds
// of model sit beside a room instance (scripts/room_live.py), both a PLY of float x y z + uchar r g b, z up, floor at 0:
//   MAP      `room_live.py add` — the robot's own fused map: ~50,000 voxels of 3 cm in the SLAM world frame (the origin is
//            where SLAM started, not under the robot), with a .json sidecar: where the robot stood and faced, whether SLAM
//            was localized, and every object and wall it separated, as a box. This is the page's main content: it opens
//            on the newest map and follows new ones, so typing `add` fills the scene within a poll (5 s).
//   CAPTURE  one capture's single-view cloud: ~500,000 points with their pixel's colour, in the ROBOT's frame — x forward,
//            y left. No sidecar, no boxes.
// The two frames differ, so the camera is re-framed whenever the kind (or the instance) changes, and kept otherwise.
//
// WHAT KEEPS HALF A MILLION POINTS SMOOTH
//   one THREE.Points, one BufferGeometry, Float32 positions + normalized Uint8 colours (7.5 MB on the GPU, the file's size);
//   the buffers are REUSED from capture to capture (a new one is only allocated when a cloud is bigger than any before it);
//   colour mode, point size and the range crop are UNIFORMS of one small shader — moving a slider uploads nothing;
//   frames are drawn on demand (an orbit, a slider, a new cloud), so an idle tab following `room_live.py watch` costs nothing;
//   nothing in the frame path allocates: the orbit works in its own scratch vectors.
//
// Only three's core is vendored (no PLYLoader, no OrbitControls) and this page fetches nothing from outside, so both are
// here, cut to what this page needs: readPly() reads exactly the header room_live.write_scene writes and refuses any
// other with the reason; Orbit is a turntable about +z, because in this frame z is up and three's own assume y.
import * as THREE from 'three';

const $ = (id) => document.getElementById(id);
const el = { viewport: $('viewport'), canvas: $('cloud'), bar: $('bar'), fill: $('bar-fill'), why: $('why'), whyTitle: $('why-title'),
  whyText: $('why-text'), readout: $('readout'), instance: $('instance'), instanceMeta: $('instance-meta'), follow: $('follow'),
  followState: $('follow-state'), caps: $('captures'), capsNote: $('captures-note'), camera: $('c-camera'), height: $('c-height'),
  ramp: $('ramp'), rampMax: $('ramp-max'), size: $('size'), sizeOut: $('size-out'), crop: $('crop'), cropOut: $('crop-out'),
  frameMs: $('frame-ms'), things: $('things'), boxes: $('boxes'), objects: $('objects'), objectsHead: $('objects-head'),
  drawCubes: $('d-cubes'), drawPoints: $('d-points'), dense: $('dense'), denseLabel: $('dense-label') };

const POLL_MS = 5000;
const MAX_TILT_RATE = 0.05;                   // rad/s — the robot's own gate (robot/config.py): above it the cloud is smeared
// the model itself — layers, boxes, the robot, the PLY reader — is scene-model.js, shared with the Room page (room-map.js)
import { Layer, Boxes, Robot, readPly, thingsOf, makeShared, fitShared, label, cm, triple, RAMP_Z } from './scene-model.js';

// ── what the person chose last time ─────────────────────────────────────────────
// "follow latest" is not one of them: it is ON for every visit, unless the address names a capture (?capture=cap_0008 is
// a link to THAT model, and following would replace it five seconds later). The address only names one while follow is off.
// Size and crop are kept PER KIND: a 3 cm voxel wants a ~3 cm point and the whole map; a 500k cloud wants 16 mm and 4 m.
const prefs = { size: 16, crop: 4, mapSize: 33, mapCrop: 10, mode: 'camera', boxes: true, dense: true, mapDraw: 'cubes', capDraw: 'points' };
try { Object.assign(prefs, JSON.parse(localStorage.getItem('scene.prefs') || '{}')); } catch (e) { /* private mode: defaults */ }
const remember = () => { try { localStorage.setItem('scene.prefs', JSON.stringify(prefs)); } catch (e) { /* same */ } };
const url = new URL(location.href);
let follow = !url.searchParams.get('capture');

// ── saying what happened, in words ──────────────────────────────────────────────
let onScreen = false;                         // is a cloud being shown (why() is needed before the cloud object exists)
function why(title, text, wrong = true) {
  el.why.hidden = !title;
  el.why.classList.toggle('wrong', wrong);
  el.why.classList.toggle('aside', onScreen);            // a cloud is showing: the notice sits above it, not on it
  el.whyTitle.textContent = title || '';
  el.whyText.textContent = text || '';
}
function progress(frac) {                     // null hides the bar
  el.bar.hidden = frac === null;
  if (frac !== null) el.fill.style.width = `${Math.round(100 * Math.min(1, frac))}%`;
}
function readout(...parts) {                  // strings · [text] emphasised · {wrong: text} in the one colour that means wrong
  el.readout.replaceChildren(...parts.map((p) => {
    if (typeof p === 'string') return p;
    const b = document.createElement(Array.isArray(p) ? 'b' : 'span'); b.textContent = Array.isArray(p) ? p[0] : p.wrong;
    if (!Array.isArray(p)) b.className = 'wrong';
    return b;
  }));
}

async function serverWords(r) {
  try { const doc = await r.json(); return String(doc.detail || doc.error || `HTTP ${r.status}`); } catch (e) { return `HTTP ${r.status}`; }
}
async function request(url, signal) {
  let r;
  try { r = await fetch(url, { cache: 'no-store', signal }); } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new Error('the web server did not answer — is web/server.py still running on this laptop?');
  }
  if (r.status === 403) throw new Error(`${await serverWords(r)}. Open it as http://localhost:8000/scene on the machine that runs the server — through the tunnel it is refused on purpose.`);
  if (!r.ok) throw new Error(r.status === 404 ? 'the server has no such instance or capture any more (deleted, or another ROOM_LIVE_DIR)' : await serverWords(r));
  return r;
}

// ── three: renderer, camera, and the things that are not the cloud ──────────────
let renderer;
try {
  renderer = new THREE.WebGLRenderer({ canvas: el.canvas, antialias: true, powerPreference: 'high-performance' });
} catch (e) {
  why('This browser could not start WebGL', 'The point cloud is drawn on the GPU. Try Chrome or Safari with hardware acceleration on. The captures list still works; each has a .png.');
}
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x08080b);
const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 200);
camera.up.set(0, 0, 1);

// floor grid: GridHelper lies in three's xz plane; this room's floor is xy. 24 m across, 24 divisions = 1 m cells on whole
// metres — a map's origin is wherever SLAM started, so the room can lie anywhere around it
const grid = new THREE.GridHelper(24, 24, 0x6c6c7a, 0x33333e);
grid.rotation.x = Math.PI / 2;
grid.material.transparent = true; grid.material.opacity = 0.75; grid.material.depthWrite = false;
scene.add(grid);

const axisNames = { capture: [], map: [] };   // a capture's axes mean something to the robot; a map's are only the SLAM frame's
{ // axes at the origin, 1 m each. Rods, not GL lines: a line is 1 px wide whatever you ask for, and 1 px disappears
  // against half a million points.
  const AXES = [['x forward', 'map x', '#ec6a62', [1, 0, 0]], ['y left', 'map y', '#6fce7a', [0, 1, 0]], ['z up', 'z up', '#6aa2f0', [0, 0, 1]]];
  const rod = new THREE.CylinderGeometry(0.008, 0.008, 1, 8).translate(0, 0.5, 0), tip = new THREE.ConeGeometry(0.03, 0.1, 12).translate(0, 1, 0);
  const yAxis = new THREE.Vector3(0, 1, 0);           // both are built along three's y and turned onto their axis
  for (const [inCapture, inMap, hex, d] of AXES) {
    const m = new THREE.MeshBasicMaterial({ color: hex, depthTest: false }), arm = new THREE.Group();      // a floor point must not bury the x axis
    arm.add(new THREE.Mesh(rod, m), new THREE.Mesh(tip, m)); arm.children.forEach((c) => { c.renderOrder = 2; });
    arm.quaternion.setFromUnitVectors(yAxis, new THREE.Vector3(...d));
    scene.add(arm);
    for (const [kind, text] of [['capture', inCapture], ['map', inMap]]) {
      const s = label(text, hex); s.position.set(d[0] * 1.08, d[1] * 1.08, d[2] * 1.08 + 0.03);
      axisNames[kind].push(s); scene.add(s);
    }
  }
}

// the robot (scene-model.js Robot), plus the crop ring around where it stands
const robot = new Robot();
const cropRing = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(
  Array.from({ length: 128 }, (_, i) => new THREE.Vector3(Math.cos(i / 128 * 2 * Math.PI), Math.sin(i / 128 * 2 * Math.PI), 0))),
  new THREE.LineBasicMaterial({ color: 0xefece6, transparent: true, opacity: 0.22, depthWrite: false }));
robot.group.add(cropRing);
scene.add(robot.group);
const pose = robot.pose;

function placeRobot(where, mount, localOrigin = false) {
  robot.place(where, mount);
  // the crop is measured in the CLOUD's own frame: a map's cloud is the map, a capture's is the robot's — for a capture placed
  // into the map the layer is moved, not its points, so the robot is at the layer's origin
  if (localOrigin) shared.uRobot.value.set(0, 0); else shared.uRobot.value.set(pose.x, pose.y);
  invalidate();
}
function placeCloud(where) {                  // a capture with a SLAM pose: its cloud turned and moved to where it was taken, in the map
  if (where) { cloud.group.position.set(where.x, where.y, 0); cloud.group.rotation.z = where.yaw; }
  else { cloud.group.position.set(0, 0, 0); cloud.group.rotation.z = 0; }
}

// ── the clouds (scene-model.js Layer): uniforms every layer shares, then the model and a map's dense layer ──────────────
const shared = makeShared();
const cloud = new Layer(shared, 0.016, 0.03);       // the model: a map's voxels, or a capture's points
const dense = new Layer(shared, 0.010, 0.010);      // a map's dense layer: the camera's own points at 1 cm, drawn as 10 mm points over the cells
scene.add(cloud.group, dense.group);

// ── a map's objects, walls and floor finds (scene-model.js Boxes) ───────────────
const boxes = new Boxes();
boxes.group.visible = false;
scene.add(boxes.group);

// ── orbit: a turntable about +z ─────────────────────────────────────────────────
class Orbit {
  constructor(cam, dom, changed) {
    this.cam = cam; this.dom = dom; this.changed = changed;
    this.target = new THREE.Vector3(); this.theta = Math.PI; this.phi = 1; this.radius = 5;
    this.dTheta = 0; this.dPhi = 0;           // what is left of the last drag; damping bleeds it off over a few frames
    this.flight = null;                       // a glide to an object (fly); a drag does not cancel it, it turns around it
    this.damping = matchMedia('(prefers-reduced-motion: reduce)').matches ? 1 : 0.22;
    this.right = new THREE.Vector3(); this.up = new THREE.Vector3(); this.offset = new THREE.Vector3();
    this.down = new Map();                    // pointerId -> {x, y, button}, mutated in place while it moves
    dom.addEventListener('contextmenu', (e) => e.preventDefault());
    dom.addEventListener('pointerdown', (e) => { dom.setPointerCapture(e.pointerId); this.down.set(e.pointerId, { x: e.clientX, y: e.clientY, button: e.button }); });
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) dom.addEventListener(name, (e) => this.down.delete(e.pointerId));
    dom.addEventListener('pointermove', (e) => this.moved(e));
    dom.addEventListener('wheel', (e) => { e.preventDefault(); this.dolly(Math.exp(Math.max(-1, Math.min(1, e.deltaY * (e.deltaMode === 1 ? 16 : 1) * 0.0015)))); }, { passive: false });
    dom.addEventListener('keydown', (e) => this.key(e));
  }

  moved(e) {
    const p = this.down.get(e.pointerId);
    if (!p) return;
    const dx = e.clientX - p.x, dy = e.clientY - p.y;
    if (this.down.size === 2) {               // two fingers: the spread zooms, the drift pans
      let q; for (const [id, v] of this.down) if (id !== e.pointerId) q = v;
      const before = Math.hypot(p.x - q.x, p.y - q.y), after = Math.hypot(e.clientX - q.x, e.clientY - q.y);
      if (before > 1 && after > 1) this.dolly(before / after);
      this.pan(dx / 2, dy / 2);
    } else if (p.button === 2 || p.button === 1 || e.shiftKey || e.ctrlKey || e.metaKey) this.pan(dx, dy);
    else { const k = 2 * Math.PI / this.dom.clientHeight; this.dTheta -= dx * k; this.dPhi -= dy * k; }
    p.x = e.clientX; p.y = e.clientY;
    this.changed();
  }

  pan(dx, dy) {                               // the target slides in the picture plane, one screen pixel per pixel at its depth
    const perPx = 2 * Math.tan(this.cam.fov * Math.PI / 360) * this.radius / this.dom.clientHeight;
    this.right.setFromMatrixColumn(this.cam.matrix, 0); this.up.setFromMatrixColumn(this.cam.matrix, 1);
    this.target.addScaledVector(this.right, -dx * perPx).addScaledVector(this.up, dy * perPx);
    this.changed();
  }

  dolly(k) { if (this.flight) this.flight.r1 *= k; this.radius = Math.max(0.15, Math.min(60, this.radius * k)); this.changed(); }

  key(e) {
    const step = { ArrowLeft: [0.08, 0], ArrowRight: [-0.08, 0], ArrowUp: [0, -0.06], ArrowDown: [0, 0.06] }[e.key];
    if (step) { this.dTheta += step[0]; this.dPhi += step[1]; } else if (e.key === '+' || e.key === '=') this.dolly(0.87); else if (e.key === '-') this.dolly(1.15); else return;
    e.preventDefault(); this.changed();
  }

  fly(to, radius) {                           // glide the target to `to` and the distance to `radius`, keeping the angle you chose
    if (this.damping === 1) { this.target.copy(to); this.radius = radius; this.flight = null; return this.changed(); }     // reduced motion: arrive
    this.flight = { t0: performance.now(), from: this.target.clone(), to: to.clone(), r0: this.radius, r1: radius };
    this.changed();
  }

  look(from, at) {                            // put the camera at `from`, looking at `at`, and stop any coasting
    this.flight = null;
    this.target.copy(at); this.offset.copy(from).sub(at);
    this.radius = this.offset.length(); this.theta = Math.atan2(this.offset.y, this.offset.x);
    this.phi = Math.acos(Math.max(-1, Math.min(1, this.offset.z / this.radius)));
    this.dTheta = this.dPhi = 0; this.changed();
  }

  update() {                                  // once per drawn frame. True while a drag is still coasting or a flight is under way.
    if (this.flight) {
      const f = this.flight, t = Math.min(1, (performance.now() - f.t0) / 600), k = t * t * (3 - 2 * t);
      this.target.lerpVectors(f.from, f.to, k); this.radius = f.r0 + (f.r1 - f.r0) * k;
      if (t === 1) this.flight = null;
    }
    this.theta += this.dTheta * this.damping; this.phi += this.dPhi * this.damping;
    this.dTheta *= 1 - this.damping; this.dPhi *= 1 - this.damping;
    if (Math.abs(this.dTheta) < 1e-4) this.dTheta = 0;
    if (Math.abs(this.dPhi) < 1e-4) this.dPhi = 0;
    this.phi = Math.max(0.01, Math.min(Math.PI - 0.01, this.phi));           // never exactly along z: lookAt has no "up" there
    const s = Math.sin(this.phi) * this.radius;
    this.cam.position.set(this.target.x + s * Math.cos(this.theta), this.target.y + s * Math.sin(this.theta), this.target.z + this.radius * Math.cos(this.phi));
    this.cam.lookAt(this.target);
    return this.dTheta !== 0 || this.dPhi !== 0 || this.flight !== null;
  }
}

// ── drawing, on demand ──────────────────────────────────────────────────────────
let queued = false, lastFrame = 0, busyMs = 0, busyFrames = 0, spin = null;
function invalidate() { if (!queued && renderer) { queued = true; requestAnimationFrame(frame); } }
const orbit = new Orbit(camera, el.canvas, invalidate);

function frame(now) {
  queued = false;
  if (spin) spin.before(now);
  const coasting = orbit.update(), t = spin ? performance.now() : 0;
  renderer.render(scene, camera);
  if (spin) { gpuWait(); spin.after(performance.now() - t); }
  // how long a frame takes WHILE the picture is moving: only back-to-back frames count, an idle gap is not a slow frame
  const dt = now - lastFrame; lastFrame = now;
  if (dt < 250) { busyMs += dt; busyFrames++; }
  if (busyMs > 500) { el.frameMs.textContent = `${(busyMs / busyFrames).toFixed(1)} ms / frame while moving`; busyMs = busyFrames = 0; }
  if (coasting || spin) invalidate();
}

// Block until the GPU has really drawn. gl.finish() returns early in Chrome (the GL calls live in another process);
// reading one pixel back cannot. Only the bench and the once-per-load timing call this, never an ordinary frame.
const onePixel = new Uint8Array(4);
function gpuWait() { const gl = renderer.getContext(); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, onePixel); }

function resize() {                           // also the devicePixelRatio path: a window dragged to another screen lands here
  if (!renderer) return;
  const w = Math.max(1, el.viewport.clientWidth), h = Math.max(1, el.viewport.clientHeight), dpr = Math.min(window.devicePixelRatio || 1, 2);
  renderer.setPixelRatio(dpr); renderer.setSize(w, h, false);
  camera.aspect = w / h; camera.updateProjectionMatrix();
  fitShared(shared, h, camera.fov, dpr);
  invalidate();
}
new ResizeObserver(resize).observe(el.viewport);
(function watchDpr() { matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`).addEventListener('change', () => { resize(); watchDpr(); }, { once: true }); })();
el.canvas.addEventListener('webglcontextlost', (e) => { e.preventDefault(); why('The GPU dropped this tab', 'The browser took the WebGL context away (sleep, or too many 3D tabs). It usually comes back by itself; reload if it does not.'); });
el.canvas.addEventListener('webglcontextrestored', () => { why(''); invalidate(); });

// ── where to look from ──────────────────────────────────────────────────────────
// `extent` is the floor plan of the MAP on screen (its sidecar's bounds, else the points' own). A capture is not framed by
// its extent: it is always the few metres in front of the robot, and the robot is its origin.
const viewFrom = new THREE.Vector3(), viewAt = new THREE.Vector3(), flyTo = new THREE.Vector3();
let kind = 'map', extent = null;

function view(name) {
  const c = Math.cos(pose.yaw), s = Math.sin(pose.yaw);
  const carry = (v, p) => v.set(pose.x + c * p[0] - s * p[1], pose.y + s * p[0] + c * p[1], p[2]);        // robot frame -> the model's
  if (name === 'eye') {                       // from the robot's own camera, along its look
    carry(viewFrom, [0, 0, pose.h]); carry(viewAt, [Math.cos(pose.pitch) * 2.5, 0, pose.h - Math.sin(pose.pitch) * 2.5]);
  } else if (kind === 'map' && extent) {
    // the whole map: look at the middle of its bounds from above and BEHIND THE ROBOT — on the line from the middle through
    // where it stands, so the picture is the room as the robot looks into it. (The origin is no help: SLAM put it anywhere.)
    const mx = (extent.min[0] + extent.max[0]) / 2, my = (extent.min[1] + extent.max[1]) / 2;
    const r = Math.hypot(extent.max[0] - extent.min[0], extent.max[1] - extent.min[1]) / 2;
    let dx = pose.x - mx, dy = pose.y - my, d = Math.hypot(dx, dy);
    if (d < 0.3) { dx = -c; dy = -s; d = 1; }                  // standing in the middle: "behind" is opposite to where it faces
    const back = Math.max(d + 1.5, 1.25 * r);
    viewAt.set(mx, my, 0.3);
    if (name === 'top') viewFrom.set(mx + dx / d * 0.05, my + dy / d * 0.05, 0.3 + 2.4 * r);
    else viewFrom.set(mx + dx / d * back, my + dy / d * back, 0.3 + 1.05 * back);       // ~45 degrees down: a floor plan, still with walls
  } else if (name === 'top') { carry(viewFrom, [1.9, 0, 9]); carry(viewAt, [2, 0, 0]); }
  else { carry(viewFrom, [-2.5, -0.6, 2.5]); carry(viewAt, [1.5, 0, 0.3]); }        // a little off its axis: from exactly behind, x and z are one line
  orbit.look(viewFrom, viewAt);
}

// ── instances and models ────────────────────────────────────────────────────────
const state = { instance: '', pinned: false, captures: [], shown: '', framed: '', summary: null, refused: new Set(), loading: null,
  current: null, declined: null, pollTimer: 0, polling: false, polls: 0, deaf: false };
// ids restart when the robot reboots, so a rewritten file is a new model; and a map whose sidecar arrived late is one too
const keyOf = (c) => `${c.capture_id}@${c.written_ms}${c.sidecar ? '+json' : ''}${c.dense_points ? '+dense' : ''}`;      // a dense layer that lands late is a new model too
// what "latest" means: the newest finished MAP if the instance has one — `add` is the headline — else the newest capture
const latestOf = (caps) => { const ok = caps.filter((c) => c.complete && !state.refused.has(keyOf(c))); return ok.find((c) => c.kind === 'map') || ok[0]; };
function address(captureId) {                 // the address names an instance only if a person chose it, a model only while not following
  if (state.pinned) url.searchParams.set('instance', state.instance); else url.searchParams.delete('instance');
  if (captureId && !follow) url.searchParams.set('capture', captureId); else url.searchParams.delete('capture');
  history.replaceState(null, '', url);
}

const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour12: false });
// every count on this page goes through num(): a value that is not a finite number prints as "?", never as "[object Object]"
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString() : '?');
const count = (n, one, many = `${one}s`) => `${num(n)} ${n === 1 ? one : many}`;
function ago(iso) {
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (!Number.isFinite(s)) return '';
  return s < 90 ? `${Math.max(0, Math.round(s))} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
}

async function loadInstances() {
  const doc = await (await request('/api/scene/instances')).json();
  // which one opens: the one already open, else the one in the address, else room_live.py's current — unless that has no
  // model yet and another does (a `new` that is still taking its first capture should not greet you with an empty floor)
  const modelled = doc.instances.filter((i) => i.models).sort((a, b) => b.newest_ms - a.newest_ms)[0];
  const want = state.instance || url.searchParams.get('instance') || (modelled && !(doc.instances.find((i) => i.current) || {}).models ? modelled.name : doc.current) || '';
  // grouped by PLACE — which room — with the current instance's room first; an instance without one is under "place not set"
  const rooms = new Map();
  for (const i of doc.instances) { const k = i.place || 'place not set'; if (!rooms.has(k)) rooms.set(k, []); rooms.get(k).push(i); }
  const first = (doc.instances.find((i) => i.current) || {}).place || '';
  el.instance.replaceChildren(...[...rooms.entries()].sort((a, b) => (b[0] === first) - (a[0] === first) || a[0].localeCompare(b[0])).map(([place, list]) => {
    const g = document.createElement('optgroup'); g.label = place;
    g.append(...list.map((i) => {
      const o = document.createElement('option'); o.value = i.name;
      o.textContent = `${i.name} · ${[i.maps && count(i.maps, 'map'), i.captures && count(i.captures, 'capture')].filter(Boolean).join(', ') || 'empty'}`; return o;
    }));
    return g;
  }));
  if (!doc.instances.length) {
    why('No room instance yet', `Nothing under ${doc.rooms_dir}. Make one:  python scripts/room_live.py add`, false);
    readout('no instances'); return null;
  }
  const chosen = doc.instances.find((i) => i.name === want) || doc.instances[0];
  el.instance.value = chosen.name;
  const lc = chosen.last_commit;
  el.instanceMeta.textContent = `${chosen.place || 'place not set'} · ${chosen.current ? 'room_live.py\'s current · ' : ''}${count(chosen.commits, 'commit')}${lc ? ` · ${lc.when} · ${lc.subject}` : ''}`;
  document.title = `${chosen.name} · ${chosen.place || 'place not set'} · scene · GITIRL`;
  return chosen.name;
}

function drawCaptures() {
  el.caps.replaceChildren(...state.captures.map((c) => {
    const li = document.createElement('li'), b = document.createElement('button'), map = c.kind === 'map';
    const shaky = !map && typeof c.tilt_rate_max === 'number' && c.tilt_rate_max >= MAX_TILT_RATE, lost = (map && c.localized === false) || (!map && !c.placed);
    b.type = 'button'; b.setAttribute('aria-current', String(keyOf(c) === state.shown)); b.disabled = !c.complete;
    const id = document.createElement('b'), when = document.createElement('span'), facts = document.createElement('span');
    id.textContent = map ? 'map' : c.capture_id; when.className = 'when'; facts.className = `facts${shaky || lost ? ' wrong' : ''}`;
    when.textContent = c.at ? `${clock(c.at)} · ${ago(c.at)}` : map ? c.capture_id.slice(4) : 'time not recorded';
    facts.textContent = !c.complete ? 'being written…'
      : map ? [`${num(c.points)} voxels`, ...(c.dense_points ? [`+ ${num(c.dense_points)} dense`] : []), `${num(c.size_mb)} MB`, ...(c.sidecar ? [count(c.objects, 'object'),
        ...(c.floor_objects ? [count(c.floor_objects, 'floor object')] : []), count(c.walls, 'wall'),
        c.localized === null ? 'SLAM ?' : c.localized ? 'SLAM localized' : 'SLAM NOT localized'] : ['no sidecar: points only'])].join(' · ')
      : [`${num(c.points)} pts`, `${num(c.size_mb)} MB`, c.placed && c.robot ? `placed by SLAM (${c.robot.x.toFixed(2)}, ${c.robot.y.toFixed(2)})` : 'pose not recorded',
        typeof c.tilt_rate_max !== 'number' ? 'tilt ?' : `tilt ${c.tilt_rate_max.toFixed(3)}${shaky ? ' rad/s — head was moving' : ''}`].join(' · ');
    b.append(id, when, facts); b.addEventListener('click', () => { setFollow(false); show(c); });
    li.append(b);
    if (c.has_png) { const a = document.createElement('a'); a.href = `/api/scene/${state.instance}/${c.capture_id}.png`; a.target = '_blank'; a.rel = 'noopener'; a.textContent = 'png'; a.title = 'the 2D picture room_live.py drew of this model'; li.append(a); }
    return li;
  }));
}

async function loadCaptures() {
  const doc = await (await request(`/api/scene/${state.instance}/captures`)).json();
  state.captures = doc.captures; state.current = doc.current;
  el.capsNote.textContent = doc.without_model.length ? `${doc.without_model.join(', ')}: recorded, but no model beside ${doc.without_model.length === 1 ? 'it' : 'them'} (--no-scene, or the depth step failed)` : '';
  drawCaptures();
  return doc.captures;
}

// ── a map's objects, as a list ──────────────────────────────────────────────────
function drawObjects(things) {
  const near = [...things].sort((a, b) => (a.kind === 'wall') - (b.kind === 'wall') || (a.from_robot_m ?? 1e9) - (b.from_robot_m ?? 1e9));      // walls last, the rest nearest first
  el.objectsHead.textContent = things.length ? 'nearest first' : 'none in this map';
  el.objects.replaceChildren(...near.map((t) => {
    const li = document.createElement('li'), b = document.createElement('button'), sw = document.createElement('i'), size = document.createElement('span'), far = document.createElement('span');
    b.type = 'button'; b.className = t.box; b.setAttribute('aria-pressed', 'false');
    size.textContent = t.kind === 'floor' ? `floor object${t.box === 'large' ? ' (large)' : ''} · ${cm(t.height_m ?? t.size_m[2])} cm tall` : `${t.kind === 'wall' ? 'wall ' : ''}${t.size_m.map(cm).join('×')} cm`;
    far.textContent = Number.isFinite(t.from_robot_m) ? `${t.from_robot_m.toFixed(1)} m` : '';
    b.title = t.kind === 'floor' ? `${t.size_m.map(cm).join('×')} cm at ${t.centre_m.map((v) => v.toFixed(2)).join(', ')} m · ${far.textContent} from the robot${t.flags && t.flags.length ? ` · ${t.flags.join(', ')}` : ''}`
      : `centre ${t.centre_m.map((v) => v.toFixed(2)).join(', ')} m · top ${t.top_m ?? '?'} m · ${t.voxels ?? '?'} voxels · ${far.textContent} from the robot`;
    b.append(sw, size, far); li.append(b);
    b.addEventListener('click', () => {         // fly to it: the target glides to its centre, the distance to a few times its size
      for (const o of el.objects.querySelectorAll('button')) o.setAttribute('aria-pressed', String(o === b));
      boxes.choose(t);
      if (!prefs.boxes) setBoxes(true);
      orbit.fly(flyTo.set(...t.centre_m), Math.max(1.4, 2.4 * Math.max(...t.size_m)));
    });
    return li;
  }));
}

async function show(c) {
  // one load at a time; asking for another drops the one in flight. The model on screen stays until the new one is READY,
  // so a failed load never leaves a black picture — it leaves the last good one, and says why the new one is not there.
  if (state.loading) state.loading.abort();
  const mine = state.loading = new AbortController(), t0 = performance.now(), base = `/api/scene/${state.instance}/${c.capture_id}`;
  const name = c.kind === 'map' ? `map ${c.at ? clock(c.at) : c.capture_id.slice(4)}` : c.capture_id;
  try {
    progress(0); readout('loading ', [name], ' …');
    // a map's sidecar (2 KB) is asked for alongside its points. Without it the map is still a picture — just one with no boxes.
    const sidecar = c.kind === 'map' && c.sidecar ? request(`${base}.json`, mine.signal).then((r) => r.json()).catch(() => null) : null;
    // and its dense layer, if the listing says there is one. A failure there costs the layer, not the map: the words go in the status.
    const denseBytes = c.kind === 'map' && c.dense_points ? request(`${base}.dense.ply`, mine.signal).then((r) => r.arrayBuffer()).catch((e) => e) : null;
    const r = await request(`${base}.ply`, mine.signal), total = +r.headers.get('content-length') || c.size_bytes || 0;
    let bytes = new Uint8Array(total || 1 << 23), got = 0;
    for (const reader = r.body.getReader(); ;) {              // straight into one buffer the size of the file: progress is real, nothing is joined later
      const { done, value } = await reader.read();
      if (done) break;
      if (got + value.length > bytes.length) { const more = new Uint8Array(Math.max(bytes.length * 2, got + value.length)); more.set(bytes.subarray(0, got)); bytes = more; }
      bytes.set(value, got); got += value.length;
      progress(total ? got / total : 0.5); readout('loading ', [name], ` … ${(got / 1e6).toFixed(1)}${total ? ` of ${(total / 1e6).toFixed(1)}` : ''} MB`);
    }
    const meta = await sidecar, denseIn = await denseBytes;
    if (mine.signal.aborted) return;          // a newer load took over: do not write into the buffers it is about to fill
    const t1 = performance.now(), { n, bounds } = readPly(bytes.subarray(0, got), cloud);
    let denseN = 0, denseWhy = '';
    if (denseIn instanceof ArrayBuffer) { try { denseN = readPly(new Uint8Array(denseIn), dense).n; } catch (e) { denseWhy = e.message; } } else if (denseIn) denseWhy = denseIn.message;
    dense.n = denseN; dense.show('points', denseN > 0 && prefs.dense);
    el.denseLabel.textContent = denseN ? `dense layer · ${num(denseN)} points` : 'dense layer'; el.dense.disabled = !denseN;
    const t2 = performance.now();
    onScreen = true;
    if (c.kind === 'map') cloud.cell = meta && Number.isFinite(meta.voxel_m) && meta.voxel_m > 0 ? meta.voxel_m : (c.voxel_m || 0.03);
    const placed = c.kind === 'capture' && Boolean(c.placed && c.robot);
    setKind(c.kind, placed);
    placeCloud(placed ? c.robot : null);
    placeRobot(placed || c.kind === 'map' ? c.robot : null, c.mount, placed);      // an unplaced capture: the robot at ITS origin, not at "(0, 0)" as if that were known
    const things = thingsOf(meta);
    boxes.draw(things); drawObjects(things);
    const stated = meta && meta.bounds_m && triple(meta.bounds_m.min) && triple(meta.bounds_m.max) ? meta.bounds_m : null;
    extent = c.kind === 'map' ? stated || bounds : null;
    // the camera is yours once you have it — a new map of the same room arrives under the view you chose. It is only
    // re-framed when the FRAME changes: another instance, or map <-> capture (SLAM's world vs the robot's own axes).
    const frameKey = `${state.instance}/${c.kind === 'map' || placed ? 'map' : 'robot'}`;      // a placed capture shares the map's frame: no re-framing between them
    if (state.framed !== frameKey) { state.framed = frameKey; view('behind'); }
    if (renderer) { orbit.update(); renderer.render(scene, camera); gpuWait(); }      // once per load: so "first draw" below includes the upload and the GPU's own time
    const t3 = performance.now();
    state.shown = keyOf(c); why(''); drawCaptures();
    address(c.capture_id);
    sceneView.last = { capture_id: c.capture_id, kind: c.kind, points: n, dense: denseN, bytes: got, objects: things.length, fetch_ms: t1 - t0, parse_ms: t2 - t1, first_draw_ms: t3 - t2, total_ms: t3 - t0 };
    const timing = [' · ready in ', [`${Math.round(t3 - t0)} ms`], ` (fetch ${Math.round(t1 - t0)} · read ${Math.round(t2 - t1)} · first draw ${Math.round(t3 - t2)})`];
    const slam = meta && meta.slam ? meta.slam.localized : null, cell = meta && Number.isFinite(meta.voxel_m) ? ` of ${cm(meta.voxel_m)} cm` : '';
    state.summary = c.kind === 'map'
      ? [[name], c.at ? ` · ${ago(c.at)}` : '', ` · ${num(n)} voxels${cell}`, denseN ? ` + ${num(denseN)} dense` : '', denseWhy ? { wrong: ` (dense layer failed: ${denseWhy})` } : '', ' · ',
        ...(meta ? [[count(c.objects, 'object')], things.some((t) => t.kind === 'floor') ? ` · ${count(things.filter((t) => t.kind === 'floor').length, 'floor object')}` : '', ` · ${count(c.walls, 'wall')} · SLAM `,
          slam === null ? 'state not recorded' : slam ? ['localized'] : { wrong: 'NOT localized — the map may have slipped' }] : ['no sidecar: points only']), ...timing]
      : [[name], ` · ${num(n)} points · ${(got / 1e6).toFixed(2)} MB · `,
        placed ? [`placed by SLAM at (${c.robot.x.toFixed(2)}, ${c.robot.y.toFixed(2)}) facing ${(c.robot.yaw * 57.3).toFixed(0)}°`] : { wrong: 'pose not recorded (robot frame)' }, ...timing];
    readout(...state.summary);
  } catch (e) {
    if (e.name === 'AbortError') return;
    if (e.unreadable) state.refused.add(keyOf(c));
    why(`${name} did not load`, `${e.message}${/[.?!]$/.test(e.message) ? '' : '.'}${state.shown ? ' The picture is still the last model that did.' : ''}`);
    readout([name], ' failed — ', e.message);
  } finally {
    if (state.loading === mine) { state.loading = null; progress(null); }
  }
}

async function poll() {
  // every 5 s, visible tab only: the list is one directory listing and a few 2 KB reads on the server. With "follow latest"
  // on, the newest finished map (latestOf) that is not the one on screen gets loaded — THIS is how `room_live.py add` fills
  // the scene — and if `add` went to another instance than the one open (it moves .current), the page goes there too,
  // unless a person chose this instance in the picker or the address.
  if (state.polling) return;
  state.polling = true; clearTimeout(state.pollTimer);
  try {
    if (!document.hidden && (!state.instance || ++state.polls % 6 === 0) && document.activeElement !== el.instance) {
      const name = await loadInstances();     // every 30 s, and until there is one at all: a first `add` shows up by itself
      if (name && name !== state.instance) { why(''); await openInstance(name); }      // the first ever, or the open one was deleted
    }
    if (!document.hidden && state.instance) {
      let caps = await loadCaptures();
      if (follow && !state.pinned && state.current && state.current !== state.instance && state.current !== state.declined) {
        const was = state.instance; state.instance = '';       // .current moved: let loadInstances choose again — the current one, if it has a model
        const name = await loadInstances().finally(() => { state.instance = was; });
        if (name && name !== was) { why(''); await openInstance(name); caps = state.captures; } else state.declined = state.current;      // nothing to show there yet: do not ask again until it moves
      }
      const newest = latestOf(caps);
      el.followState.textContent = follow ? `checked ${clock(Date.now())}` : '';
      if (follow && newest && keyOf(newest) !== state.shown && !state.loading) {
        await show(newest);
        if (document.activeElement !== el.instance) await loadInstances();      // the picker's counts and the last commit moved with it
      }
      if (el.why.hidden === false && !el.why.classList.contains('wrong')) why('');
      if (!caps.length) why('Nothing in this instance yet', `python scripts/room_live.py add ${state.instance}  snapshots the robot's map; it will appear here by itself.`, false);
      if (state.deaf && state.summary) readout(...state.summary);          // the server is answering again: put back what the picture is
      state.deaf = false;
    }
  } catch (e) {
    if (e.name !== 'AbortError') { state.deaf = true; el.followState.textContent = 'not answering'; readout('the list of models did not load — ', e.message); }
  }
  state.polling = false; state.pollTimer = setTimeout(poll, POLL_MS);
}

async function openInstance(name, captureId) {
  state.instance = name; state.shown = ''; state.summary = null; state.captures = [];
  address();
  const caps = await loadCaptures();
  const pick = caps.find((c) => c.capture_id === captureId && c.complete) || latestOf(caps);
  if (pick) await show(pick);
  else { cloud.show(cloud.mode, false); dense.show('points', false); onScreen = boxes.group.visible = false; el.things.hidden = true; invalidate(); readout('nothing in ', [name], ' yet'); }
}

// ── controls ────────────────────────────────────────────────────────────────────
function setKind(k, placed = false) {         // which FRAME is on screen: it decides the axes' names, the height ramp, and whose size and crop apply
  kind = k === 'map' ? 'map' : 'capture';
  const axes = kind === 'map' || placed ? 'map' : 'capture';      // a placed capture sits in the map: the axes are the map's
  for (const [name, sprites] of Object.entries(axisNames)) for (const sp of sprites) sp.visible = name === axes;
  shared.uZ.value.set(...RAMP_Z[kind]); el.rampMax.textContent = `${RAMP_Z[kind][1]} m`;
  el.things.hidden = kind !== 'map'; boxes.group.visible = kind === 'map' && prefs.boxes;
  robot.arrow.visible = kind === 'map';          // in a capture's frame forward IS the x axis, already drawn and named
  if (kind !== 'map') dense.show('points', false);
  setSize(kind === 'map' ? prefs.mapSize : prefs.size); setCrop(kind === 'map' ? prefs.mapCrop : prefs.crop);
  setDraw(kind === 'map' ? prefs.mapDraw : prefs.capDraw);
}
function setFollow(on) { follow = el.follow.checked = on; if (!on) el.followState.textContent = ''; }
function setDraw(mode) {                      // cells or points. A map's cell is its voxel (3 cm, from the sidecar); a capture's is the point size
  mode = mode === 'cubes' ? 'cubes' : 'points';
  prefs[kind === 'map' ? 'mapDraw' : 'capDraw'] = mode;
  el.drawCubes.setAttribute('aria-pressed', String(mode === 'cubes')); el.drawPoints.setAttribute('aria-pressed', String(mode === 'points'));
  const fixed = mode === 'cubes' && kind === 'map';
  el.size.disabled = fixed; if (fixed) el.sizeOut.textContent = `${cm(cloud.cell)} cm cells`; else setSize(+el.size.value);
  cloud.show(mode, onScreen); remember(); invalidate();
}
function setDense(on) { prefs.dense = el.dense.checked = on; dense.show('points', on && kind === 'map' && dense.n > 0); remember(); invalidate(); }
function setBoxes(on) { prefs.boxes = el.boxes.checked = on; boxes.group.visible = kind === 'map' && on; remember(); invalidate(); }
function setMode(mode) {
  prefs.mode = mode === 'height' ? 'height' : 'camera';
  el.camera.setAttribute('aria-pressed', String(prefs.mode === 'camera')); el.height.setAttribute('aria-pressed', String(prefs.mode === 'height'));
  el.ramp.hidden = prefs.mode !== 'height';
  shared.uHeight.value = prefs.mode === 'height' ? 1 : 0;
  remember(); invalidate();
}
function setSize(mm) {
  prefs[kind === 'map' ? 'mapSize' : 'size'] = mm; el.size.value = mm; el.sizeOut.textContent = `${Math.round(mm)} mm`;
  cloud.size = mm / 1000; if (kind !== 'map') cloud.cell = mm / 1000; remember(); invalidate();
}
function setCrop(m) {                         // the slider's far end is "no crop": a map is wanted whole, and it is 6 m across
  const all = m >= +el.crop.max;
  prefs[kind === 'map' ? 'mapCrop' : 'crop'] = m; el.crop.value = m; el.cropOut.textContent = all ? 'everything' : `${(+m).toFixed(1)} m`;
  shared.uCrop.value = all ? 1e9 : m; cropRing.visible = !all; cropRing.scale.set(m, m, 1); remember(); invalidate();
}
el.camera.addEventListener('click', () => setMode('camera'));
el.height.addEventListener('click', () => setMode('height'));
el.size.addEventListener('input', () => setSize(+el.size.value));
el.crop.addEventListener('input', () => setCrop(+el.crop.value));
el.boxes.addEventListener('change', () => setBoxes(el.boxes.checked));
el.dense.addEventListener('change', () => setDense(el.dense.checked));
el.drawCubes.addEventListener('click', () => setDraw('cubes'));
el.drawPoints.addEventListener('click', () => setDraw('points'));
el.follow.addEventListener('change', () => { setFollow(el.follow.checked); if (follow) { address(); poll(); } });
el.instance.addEventListener('change', () => { state.pinned = true; openInstance(el.instance.value).catch((e) => why('That instance did not open', e.message)); });
for (const b of document.querySelectorAll('[data-view]')) b.addEventListener('click', () => view(b.dataset.view));
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });

// For the console: `await sceneView.bench()` turns the cloud one full circle and reports what a frame cost. `interval`
// is frame-to-frame (what the eye gets, capped by the display); `gpu` is render + a one-pixel readback (what a frame really costs).
const sceneView = window.sceneView = { last: null, bench(frames = 240) {
  return new Promise((done) => {
    const interval = new Float32Array(frames), gpu = new Float32Array(frames), theta0 = orbit.theta;
    const stat = (a) => { const s = Array.from(a).sort((x, y) => x - y), mid = s[s.length >> 1]; return { mean: +(s.reduce((x, y) => x + y, 0) / s.length).toFixed(2), p95: +s[Math.floor(s.length * 0.95)].toFixed(2), max: +s[s.length - 1].toFixed(2), late: s.filter((v) => v > 1.5 * mid).length }; };      // late: frames that took over 1.5x the median
    let i = -1, prev = 0;
    spin = {
      before(now) { if (i >= 0) interval[i] = now - prev; prev = now; i++; orbit.theta = theta0 + (Math.min(i, frames) / frames) * 2 * Math.PI; },
      after(ms) {
        if (i < frames) { gpu[i] = ms; return; }
        spin = null;
        done({ frames, points: cloud.n, drawn_as: cloud.mode, canvas: [el.canvas.width, el.canvas.height], interval_ms: stat(interval), gpu_ms: stat(gpu) });
      },
    };
    invalidate();
  });
} };

// ── go. Nothing here is awaited at the top level: the module finishes at once, so the page's load event is not held ──
state.pinned = Boolean(url.searchParams.get('instance'));
setMode(prefs.mode); setBoxes(prefs.boxes !== false); setDense(prefs.dense !== false); setKind('map'); setFollow(follow);
placeRobot(null, null); view('behind'); resize();
loadInstances()
  .then((name) => (name ? openInstance(name, url.searchParams.get('capture')) : null))
  .catch((e) => { why('The scene did not open', e.message); readout('not loaded — ', e.message); })
  .finally(() => { state.pollTimer = setTimeout(poll, POLL_MS); });
