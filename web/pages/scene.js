// scene.js — /scene: the room's 3D model, one capture at a time, turning in a tab (web/scene_api.py serves it, to this
// laptop only). A model is the PLY scripts/room_live.py writes beside a room instance: ~500,000 points with the colour of
// the pixel each came from, in the ROOM frame — x forward from the robot, y left, z up, floor at z = 0, metres.
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
  ramp: $('ramp'), size: $('size'), sizeOut: $('size-out'), crop: $('crop'), cropOut: $('crop-out'), frameMs: $('frame-ms') };

const POLL_MS = 5000;
const MAX_TILT_RATE = 0.05;                   // rad/s — the robot's own gate (robot/config.py): above it the cloud is smeared
const PLY_COLUMNS = ['float x', 'float y', 'float z', 'uchar red', 'uchar green', 'uchar blue'];
const POINT_BYTES = 15;
const RAMP = [[0.20, 0.35, 0.62], [0.13, 0.62, 0.60], [0.40, 0.80, 0.40], [0.98, 0.88, 0.20], [0.95, 0.45, 0.23]];   // scene.css .ramp
const RAMP_Z = [0, 2.5];
const MOUNT = { height_m: 1.59, pitch_down_deg: 38, yaw_left_deg: 0 };       // used when a capture.json records no mount

// ── what the person chose last time ─────────────────────────────────────────────
const prefs = { size: 8, crop: 4, mode: 'camera', follow: true };
try { Object.assign(prefs, JSON.parse(localStorage.getItem('scene.prefs') || '{}')); } catch (e) { /* private mode: defaults */ }
const remember = () => { try { localStorage.setItem('scene.prefs', JSON.stringify(prefs)); } catch (e) { /* same */ } };

// ── saying what happened, in words ──────────────────────────────────────────────
function why(title, text, wrong = true) {
  el.why.hidden = !title;
  el.why.classList.toggle('wrong', wrong);
  el.whyTitle.textContent = title || '';
  el.whyText.textContent = text || '';
}
function progress(frac) {                     // null hides the bar
  el.bar.hidden = frac === null;
  if (frac !== null) el.fill.style.width = `${Math.round(100 * Math.min(1, frac))}%`;
}
function readout(...parts) {                  // strings, or [text] for the emphasised ones
  el.readout.replaceChildren(...parts.map((p) => {
    if (!Array.isArray(p)) return p;
    const b = document.createElement('b'); b.textContent = p[0]; return b;
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

// floor grid: GridHelper lies in three's xz plane; this room's floor is xy. 12 m across, 12 divisions = 1 m cells on whole metres
const grid = new THREE.GridHelper(12, 12, 0x6c6c7a, 0x33333e);
grid.rotation.x = Math.PI / 2;
grid.material.transparent = true; grid.material.opacity = 0.75; grid.material.depthWrite = false;
scene.add(grid);

function label(text, color) {                 // a word that stays the same size on screen and is never hidden by points
  const c = document.createElement('canvas'), g = c.getContext('2d'), font = '600 44px ui-monospace, Menlo, monospace';
  g.font = font;
  c.width = Math.ceil(g.measureText(text).width) + 16; c.height = 64;
  g.font = font; g.fillStyle = color; g.textBaseline = 'middle'; g.fillText(text, 8, 34);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, sizeAttenuation: false, transparent: true }));
  s.scale.set(0.032 * c.width / c.height, 0.032, 1); s.center.set(0, 0.5); s.renderOrder = 3;
  return s;
}
{ // axes at the room's origin, 1 m each, named for what they mean to the robot. Rods, not GL lines: a line is 1 px wide
  // whatever you ask for, and 1 px disappears against half a million points.
  const AXES = [['x forward', '#ec6a62', [1, 0, 0]], ['y left', '#6fce7a', [0, 1, 0]], ['z up', '#6aa2f0', [0, 0, 1]]];
  const rod = new THREE.CylinderGeometry(0.008, 0.008, 1, 8).translate(0, 0.5, 0), tip = new THREE.ConeGeometry(0.03, 0.1, 12).translate(0, 1, 0);
  const yAxis = new THREE.Vector3(0, 1, 0);           // both are built along three's y and turned onto their axis
  for (const [text, hex, d] of AXES) {
    const m = new THREE.MeshBasicMaterial({ color: hex }), arm = new THREE.Group();
    arm.add(new THREE.Mesh(rod, m), new THREE.Mesh(tip, m));
    arm.quaternion.setFromUnitVectors(yAxis, new THREE.Vector3(...d));
    const s = label(text, hex); s.position.set(d[0] * 1.12, d[1] * 1.12, d[2] * 1.12);
    scene.add(arm, s);
  }
}

// the robot: a mast from the floor to its camera, the camera as a small frustum, a short line along where it looks, and
// — faint — the rest of that line down to the floor. Rebuilt in place when a capture records another pose or mount.
const robot = new THREE.Group();
const robotLines = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0xefece6 }));
const robotRay = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0xefece6, transparent: true, opacity: 0.3 }));
robotLines.geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(10 * 2 * 3), 3));
robotRay.geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(3 * 2 * 3), 3));
const robotHead = new THREE.Mesh(new THREE.SphereGeometry(0.035, 16, 10), new THREE.MeshBasicMaterial({ color: 0xefece6 }));
const cropRing = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(
  Array.from({ length: 128 }, (_, i) => new THREE.Vector3(Math.cos(i / 128 * 2 * Math.PI), Math.sin(i / 128 * 2 * Math.PI), 0))),
  new THREE.LineBasicMaterial({ color: 0xefece6, transparent: true, opacity: 0.22, depthWrite: false }));
robot.add(robotLines, robotRay, robotHead, cropRing, label('robot', '#efece6'));
robot.children[4].position.set(0, 0, 0);      // placed with the head in placeRobot
scene.add(robot);
const pose = { x: 0, y: 0, yaw: 0, h: MOUNT.height_m, pitch: MOUNT.pitch_down_deg * Math.PI / 180 };

function placeRobot(where, mount) {
  const m = { ...MOUNT, ...(mount || {}) }, r = where || {};
  pose.x = +r.x || 0; pose.y = +r.y || 0; pose.h = +m.height_m || MOUNT.height_m;
  pose.yaw = (+r.yaw || 0) + (+m.yaw_left_deg || 0) * Math.PI / 180;
  pose.pitch = (Number.isFinite(+m.pitch_down_deg) ? +m.pitch_down_deg : MOUNT.pitch_down_deg) * Math.PI / 180;
  robot.position.set(pose.x, pose.y, 0); robot.rotation.z = pose.yaw;
  const h = pose.h, d = [Math.cos(pose.pitch), 0, -Math.sin(pose.pitch)], u = [Math.sin(pose.pitch), 0, Math.cos(pose.pitch)];
  const at = (f, l, v) => [d[0] * f + u[0] * v, l, h + d[2] * f + u[2] * v];      // f along the look, l to the left, v up the image
  const c = [at(0.3, 0.17, 0.12), at(0.3, -0.17, 0.12), at(0.3, -0.17, -0.12), at(0.3, 0.17, -0.12)], eye = [0, 0, h];
  robotLines.geometry.attributes.position.array.set([0, 0, 0, ...eye, ...eye, ...at(0.6, 0, 0),
    ...eye, ...c[0], ...eye, ...c[1], ...eye, ...c[2], ...eye, ...c[3], ...c[0], ...c[1], ...c[1], ...c[2], ...c[2], ...c[3], ...c[3], ...c[0]]);
  robotLines.geometry.attributes.position.needsUpdate = true;
  // the look line carried on to the floor, and a small cross where it lands. A camera looking level or up never lands.
  const reach = pose.pitch > 0.05 ? h / Math.sin(pose.pitch) : 0, fx = reach * d[0];
  robotRay.visible = reach > 0.6 && reach < 12;
  robotRay.geometry.attributes.position.array.set([...at(0.6, 0, 0), fx, 0, 0.003, fx - 0.12, 0, 0.003, fx + 0.12, 0, 0.003, fx, -0.12, 0.003, fx, 0.12, 0.003]);
  robotRay.geometry.attributes.position.needsUpdate = true;
  robotHead.position.set(0, 0, h);
  robot.children[4].position.set(0, 0, h + 0.12);
  cloudMaterial.uniforms.uRobot.value.set(pose.x, pose.y);
  invalidate();
}

// ── the cloud ───────────────────────────────────────────────────────────────────
// One shader instead of PointsMaterial, for three things it cannot do: a point is a size in the ROOM (millimetres, so a
// surface stays closed as you fly in) clamped to 1 px..uMaxPx; the crop hides points by ground range from the robot
// without touching a buffer; height colouring is a ramp on z, shaded by the pixel's own brightness so edges survive.
// The camera's colours are sRGB bytes and go to the screen as they are — no colour-space pass is included on purpose.
const glsl = (rgb) => `vec3(${rgb.map((v) => v.toFixed(3)).join(', ')})`;
const cloudMaterial = new THREE.ShaderMaterial({
  uniforms: { uSize: { value: 0.008 }, uScale: { value: 800 }, uMaxPx: { value: 32 }, uCrop: { value: 4 }, uHeight: { value: 0 },
    uRobot: { value: new THREE.Vector2() }, uZ: { value: new THREE.Vector2(RAMP_Z[0], RAMP_Z[1]) } },
  vertexShader: `
    uniform float uSize, uScale, uMaxPx, uCrop, uHeight; uniform vec2 uRobot, uZ;
    attribute vec3 rgb; varying vec3 vColor;
    vec3 ramp(float t) {
      float s = t * 4.0;
      vec3 c = mix(${glsl(RAMP[0])}, ${glsl(RAMP[1])}, clamp(s, 0.0, 1.0));
      c = mix(c, ${glsl(RAMP[2])}, clamp(s - 1.0, 0.0, 1.0));
      c = mix(c, ${glsl(RAMP[3])}, clamp(s - 2.0, 0.0, 1.0));
      return mix(c, ${glsl(RAMP[4])}, clamp(s - 3.0, 0.0, 1.0));
    }
    void main() {
      vec4 mv = modelViewMatrix * vec4(position, 1.0);
      gl_Position = projectionMatrix * mv;
      gl_PointSize = clamp(uSize * uScale / max(-mv.z, 0.001), 1.0, uMaxPx);
      if (distance(position.xy, uRobot) > uCrop) gl_Position = vec4(2.0, 2.0, 2.0, 1.0);       // outside the clip volume: not drawn
      float luma = dot(rgb, vec3(0.299, 0.587, 0.114));
      vColor = mix(rgb, ramp(clamp((position.z - uZ.x) / (uZ.y - uZ.x), 0.0, 1.0)) * (0.5 + 0.5 * luma), uHeight);
    }`,
  fragmentShader: 'varying vec3 vColor; void main() { gl_FragColor = vec4(vColor, 1.0); }',
});
const cloudGeometry = new THREE.BufferGeometry();
const cloud = new THREE.Points(cloudGeometry, cloudMaterial);
cloud.frustumCulled = false;                  // one object, always wanted: a bounding sphere over 500k points buys nothing
cloud.visible = false;
scene.add(cloud);
let capacity = 0;

function buffersFor(n) {
  // Big enough already: write into the arrays that are on the GPU. Otherwise free them FIRST (dispose releases the
  // attributes the geometry holds NOW; ones replaced before a dispose are never released) and allocate with headroom,
  // so a run of captures of 460k..500k points allocates once.
  if (n > capacity) {
    cloudGeometry.dispose();
    capacity = Math.ceil(n * 1.15);
    cloudGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(capacity * 3), 3));
    cloudGeometry.setAttribute('rgb', new THREE.BufferAttribute(new Uint8Array(capacity * 3), 3, true));
  }
  return [cloudGeometry.attributes.position, cloudGeometry.attributes.rgb];
}

function readPly(bytes) {
  // header: ASCII lines up to "end_header\n"; after it, n records of <f4 x y z, u1 r g b — 15 bytes, so NOT 4-byte aligned:
  // a Float32Array cannot be laid over them, hence the DataView. ~10 ms for half a million points.
  const head = new TextDecoder('latin1').decode(bytes.subarray(0, Math.min(bytes.length, 4096)));
  const end = head.indexOf('end_header\n');
  if (!head.startsWith('ply\n') || end < 0) throw new Error('the file does not begin with a PLY header');
  const lines = head.slice(0, end).split('\n').map((l) => l.trim());
  if (!lines.includes('format binary_little_endian 1.0')) throw new Error(`the PLY is "${lines[1] || '?'}"; this page reads binary_little_endian 1.0`);
  const columns = lines.filter((l) => l.startsWith('property ')).map((l) => l.slice(9));
  if (columns.join('|') !== PLY_COLUMNS.join('|')) throw new Error(`the PLY's columns are "${columns.join(', ')}"; this page reads exactly "${PLY_COLUMNS.join(', ')}"`);
  const count = lines.map((l) => /^element vertex (\d+)$/.exec(l)).find(Boolean);
  if (!count) throw new Error('the PLY header names no "element vertex"');
  const n = +count[1], start = end + 'end_header\n'.length;
  if (bytes.length < start + n * POINT_BYTES) throw new Error(`the file is cut short: ${n.toLocaleString()} points need ${(start + n * POINT_BYTES).toLocaleString()} bytes and ${bytes.length.toLocaleString()} arrived — it was probably still being written`);
  const [position, rgb] = buffersFor(n), xyz = position.array, col = rgb.array;
  const view = new DataView(bytes.buffer, bytes.byteOffset + start, n * POINT_BYTES);
  for (let i = 0, o = 0, p = 0, c = start + 12; i < n; i++, o += POINT_BYTES, p += 3, c += POINT_BYTES) {
    xyz[p] = view.getFloat32(o, true); xyz[p + 1] = view.getFloat32(o + 4, true); xyz[p + 2] = view.getFloat32(o + 8, true);
    col[p] = bytes[c]; col[p + 1] = bytes[c + 1]; col[p + 2] = bytes[c + 2];
  }
  for (const a of [position, rgb]) { a.clearUpdateRanges(); a.addUpdateRange(0, n * 3); a.needsUpdate = true; }     // upload n points, not the capacity
  cloudGeometry.setDrawRange(0, n);
  return n;
}

// ── orbit: a turntable about +z ─────────────────────────────────────────────────
class Orbit {
  constructor(cam, dom, changed) {
    this.cam = cam; this.dom = dom; this.changed = changed;
    this.target = new THREE.Vector3(); this.theta = Math.PI; this.phi = 1; this.radius = 5;
    this.dTheta = 0; this.dPhi = 0;           // what is left of the last drag; damping bleeds it off over a few frames
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

  dolly(k) { this.radius = Math.max(0.15, Math.min(60, this.radius * k)); this.changed(); }

  key(e) {
    const step = { ArrowLeft: [0.08, 0], ArrowRight: [-0.08, 0], ArrowUp: [0, -0.06], ArrowDown: [0, 0.06] }[e.key];
    if (step) { this.dTheta += step[0]; this.dPhi += step[1]; } else if (e.key === '+' || e.key === '=') this.dolly(0.87); else if (e.key === '-') this.dolly(1.15); else return;
    e.preventDefault(); this.changed();
  }

  look(from, at) {                            // put the camera at `from`, looking at `at`, and stop any coasting
    this.target.copy(at); this.offset.copy(from).sub(at);
    this.radius = this.offset.length(); this.theta = Math.atan2(this.offset.y, this.offset.x);
    this.phi = Math.acos(Math.max(-1, Math.min(1, this.offset.z / this.radius)));
    this.dTheta = this.dPhi = 0; this.changed();
  }

  update() {                                  // once per drawn frame. True while a drag is still coasting.
    this.theta += this.dTheta * this.damping; this.phi += this.dPhi * this.damping;
    this.dTheta *= 1 - this.damping; this.dPhi *= 1 - this.damping;
    if (Math.abs(this.dTheta) < 1e-4) this.dTheta = 0;
    if (Math.abs(this.dPhi) < 1e-4) this.dPhi = 0;
    this.phi = Math.max(0.01, Math.min(Math.PI - 0.01, this.phi));           // never exactly along z: lookAt has no "up" there
    const s = Math.sin(this.phi) * this.radius;
    this.cam.position.set(this.target.x + s * Math.cos(this.theta), this.target.y + s * Math.sin(this.theta), this.target.z + this.radius * Math.cos(this.phi));
    this.cam.lookAt(this.target);
    return this.dTheta !== 0 || this.dPhi !== 0;
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
  if (spin) { renderer.getContext().finish(); spin.after(performance.now() - t); }
  // how long a frame takes WHILE the picture is moving: only back-to-back frames count, an idle gap is not a slow frame
  const dt = now - lastFrame; lastFrame = now;
  if (dt < 250) { busyMs += dt; busyFrames++; }
  if (busyMs > 500) { el.frameMs.textContent = `${(busyMs / busyFrames).toFixed(1)} ms / frame while moving`; busyMs = busyFrames = 0; }
  if (coasting || spin) invalidate();
}

function resize() {                           // also the devicePixelRatio path: a window dragged to another screen lands here
  if (!renderer) return;
  const w = Math.max(1, el.viewport.clientWidth), h = Math.max(1, el.viewport.clientHeight), dpr = Math.min(window.devicePixelRatio || 1, 2);
  renderer.setPixelRatio(dpr); renderer.setSize(w, h, false);
  camera.aspect = w / h; camera.updateProjectionMatrix();
  cloudMaterial.uniforms.uScale.value = h * dpr / (2 * Math.tan(camera.fov * Math.PI / 360));      // device px per metre, 1 m away
  cloudMaterial.uniforms.uMaxPx.value = 32 * dpr;
  invalidate();
}
new ResizeObserver(resize).observe(el.viewport);
(function watchDpr() { matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`).addEventListener('change', () => { resize(); watchDpr(); }, { once: true }); })();
el.canvas.addEventListener('webglcontextlost', (e) => { e.preventDefault(); why('The GPU dropped this tab', 'The browser took the WebGL context away (sleep, or too many 3D tabs). It usually comes back by itself; reload if it does not.'); });
el.canvas.addEventListener('webglcontextrestored', () => { why(''); invalidate(); });

const viewFrom = new THREE.Vector3(), viewAt = new THREE.Vector3();
function view(name) {                         // all three are said in the ROBOT's frame, then carried to where it stood
  const eyeDir = [Math.cos(pose.pitch), -Math.sin(pose.pitch)];
  const v = { behind: [[-2.5, 0, 2.5], [1.5, 0, 0.3]], top: [[1.9, 0, 9], [2, 0, 0]],
    eye: [[0, 0, pose.h], [eyeDir[0] * 2.5, 0, pose.h + eyeDir[1] * 2.5]] }[name] || [];
  if (!v.length) return;
  const c = Math.cos(pose.yaw), s = Math.sin(pose.yaw);
  viewFrom.set(pose.x + c * v[0][0] - s * v[0][1], pose.y + s * v[0][0] + c * v[0][1], v[0][2]);
  viewAt.set(pose.x + c * v[1][0] - s * v[1][1], pose.y + s * v[1][0] + c * v[1][1], v[1][2]);
  orbit.look(viewFrom, viewAt);
}

// ── instances and captures ──────────────────────────────────────────────────────
const state = { instance: '', captures: [], shown: '', loading: null, first: true, pollTimer: 0, polling: false, polls: 0 };
const keyOf = (c) => `${c.capture_id}@${c.written_ms}`;      // ids restart when the robot reboots; a rewritten file is a new model
const url = new URL(location.href);

function ago(iso) {
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (!Number.isFinite(s)) return '';
  return s < 90 ? `${Math.max(0, Math.round(s))} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
}

async function loadInstances() {
  const doc = await (await request('/api/scene/instances')).json();
  const want = state.instance || url.searchParams.get('instance') || doc.current || (doc.instances[0] || {}).name || '';
  el.instance.replaceChildren(...doc.instances.map((i) => {
    const o = document.createElement('option'); o.value = i.name;
    o.textContent = `${i.name} · ${i.captures} model${i.captures === 1 ? '' : 's'}`; return o;
  }));
  if (!doc.instances.length) {
    why('No room instance yet', `Nothing under ${doc.rooms_dir}. Make one:  python scripts/room_live.py new <name>`, false);
    readout('no instances'); return null;
  }
  const chosen = doc.instances.find((i) => i.name === want) || doc.instances[0];
  el.instance.value = chosen.name;
  const lc = chosen.last_commit;
  el.instanceMeta.textContent = `${chosen.current ? 'room_live.py\'s current · ' : ''}${chosen.commits} commit${chosen.commits === 1 ? '' : 's'}${lc ? ` · ${lc.when} · ${lc.subject}` : ''}`;
  return chosen.name;
}

function drawCaptures() {
  el.caps.replaceChildren(...state.captures.map((c) => {
    const li = document.createElement('li'), b = document.createElement('button'), shaky = c.tilt_rate_max !== null && c.tilt_rate_max >= MAX_TILT_RATE;
    b.type = 'button'; b.setAttribute('aria-current', String(keyOf(c) === state.shown)); b.disabled = !c.complete;
    const id = document.createElement('b'), when = document.createElement('span'), facts = document.createElement('span');
    id.textContent = c.capture_id; when.className = 'when'; facts.className = `facts${shaky ? ' shaky' : ''}`;
    when.textContent = c.at ? `${new Date(c.at).toLocaleTimeString([], { hour12: false })} · ${ago(c.at)}` : 'time not recorded';
    facts.textContent = !c.complete ? 'being written…' : [`${c.points === null ? '?' : c.points.toLocaleString()} pts`, `${c.size_mb} MB`, `pose ${c.pose_source ?? '?'}`,
      c.tilt_rate_max === null ? 'tilt ?' : `tilt ${c.tilt_rate_max.toFixed(3)}${shaky ? ' rad/s — head was moving' : ''}`].join(' · ');
    b.append(id, when, facts); b.addEventListener('click', () => { setFollow(false); show(c); });
    li.append(b);
    if (c.has_png) { const a = document.createElement('a'); a.href = `/api/scene/${state.instance}/${c.capture_id}.png`; a.target = '_blank'; a.rel = 'noopener'; a.textContent = 'png'; a.title = 'the two-view picture room_live.py drew of this capture'; li.append(a); }
    return li;
  }));
}

async function loadCaptures() {
  const doc = await (await request(`/api/scene/${state.instance}/captures`)).json();
  state.captures = doc.captures;
  el.capsNote.textContent = doc.without_model.length ? `${doc.without_model.join(', ')}: recorded, but no model beside ${doc.without_model.length === 1 ? 'it' : 'them'} (--no-scene, or the depth step failed)` : '';
  drawCaptures();
  return doc.captures;
}

async function show(c) {
  // one load at a time; asking for another drops the one in flight. The cloud on screen stays until the new one is READY,
  // so a failed load never leaves a black picture — it leaves the last good one, and says why the new one is not there.
  if (state.loading) state.loading.abort();
  const mine = state.loading = new AbortController(), t0 = performance.now(), path = `/api/scene/${state.instance}/${c.capture_id}.ply`;
  try {
    progress(0); readout('loading ', [c.capture_id], ' …');
    const r = await request(path, mine.signal), total = +r.headers.get('content-length') || c.size_bytes || 0;
    let bytes = new Uint8Array(total || 1 << 23), got = 0;
    for (const reader = r.body.getReader(); ;) {              // straight into one buffer the size of the file: progress is real, nothing is joined later
      const { done, value } = await reader.read();
      if (done) break;
      if (got + value.length > bytes.length) { const more = new Uint8Array(Math.max(bytes.length * 2, got + value.length)); more.set(bytes.subarray(0, got)); bytes = more; }
      bytes.set(value, got); got += value.length;
      progress(total ? got / total : 0.5); readout('loading ', [c.capture_id], ` … ${(got / 1e6).toFixed(1)}${total ? ` of ${(total / 1e6).toFixed(1)}` : ''} MB`);
    }
    if (mine.signal.aborted) return;          // a newer load took over: do not write into the buffers it is about to fill
    const t1 = performance.now(), n = readPly(bytes.subarray(0, got)), t2 = performance.now();
    cloud.visible = true; placeRobot(c.robot, c.mount);
    if (state.first) { state.first = false; view('behind'); }
    if (renderer) { orbit.update(); renderer.render(scene, camera); renderer.getContext().finish(); }      // once per load: so "drawn" below is the GPU's time too
    const t3 = performance.now();
    state.shown = keyOf(c); why(''); drawCaptures();
    url.searchParams.set('instance', state.instance); url.searchParams.set('capture', c.capture_id); history.replaceState(null, '', url);
    sceneView.last = { capture_id: c.capture_id, points: n, bytes: got, fetch_ms: t1 - t0, parse_ms: t2 - t1, first_draw_ms: t3 - t2, total_ms: t3 - t0 };
    readout([c.capture_id], ` · ${n.toLocaleString()} points · ${(got / 1e6).toFixed(2)} MB · ready in `, [`${Math.round(t3 - t0)} ms`],
      ` (fetch ${Math.round(t1 - t0)} · read ${Math.round(t2 - t1)} · first draw ${Math.round(t3 - t2)})`);
  } catch (e) {
    if (e.name === 'AbortError') return;
    why(`${c.capture_id} did not load`, `${e.message}.${state.shown ? ' The picture is still the last capture that did.' : ''}`);
    readout([c.capture_id], ' failed — ', e.message);
  } finally {
    if (state.loading === mine) { state.loading = null; progress(null); }
  }
}

async function poll() {
  // every 5 s, visible tab only: the list is one directory listing and a few 2 KB header reads on the server. With
  // "follow latest" on, the newest COMPLETE model that is not the one on screen gets loaded — this is the live update.
  if (state.polling) return;
  state.polling = true; clearTimeout(state.pollTimer);
  try {
    if (!document.hidden && (!state.instance || ++state.polls % 6 === 0) && document.activeElement !== el.instance) {
      const name = await loadInstances();     // every 30 s, and until there is one at all: `room_live.py new` shows up by itself
      if (name && !state.instance) { why(''); await openInstance(name); }
    }
    if (!document.hidden && state.instance) {
      const caps = await loadCaptures(), newest = caps.find((c) => c.complete);
      el.followState.textContent = prefs.follow ? `checked ${new Date().toLocaleTimeString([], { hour12: false })}` : '';
      if (prefs.follow && newest && keyOf(newest) !== state.shown && !state.loading) await show(newest);
      if (el.why.hidden === false && !el.why.classList.contains('wrong')) why('');
      if (!caps.length) why('No model in this instance yet', `python scripts/room_live.py status ${state.instance}  takes a capture and writes its model; it will appear here by itself.`, false);
    }
  } catch (e) {
    if (e.name !== 'AbortError') { el.followState.textContent = 'not answering'; readout('the captures list did not load — ', e.message); }
  }
  state.polling = false; state.pollTimer = setTimeout(poll, POLL_MS);
}

async function openInstance(name, captureId) {
  state.instance = name; state.shown = ''; state.captures = [];
  const caps = await loadCaptures();
  const pick = caps.find((c) => c.capture_id === captureId && c.complete) || caps.find((c) => c.complete);
  if (pick) await show(pick);
  else { cloud.visible = false; invalidate(); readout('no model in ', [name], ' yet'); }
}

// ── controls ────────────────────────────────────────────────────────────────────
function setFollow(on) { prefs.follow = on; el.follow.checked = on; if (!on) el.followState.textContent = ''; remember(); }
function setMode(mode) {
  prefs.mode = mode === 'height' ? 'height' : 'camera';
  el.camera.setAttribute('aria-pressed', String(prefs.mode === 'camera')); el.height.setAttribute('aria-pressed', String(prefs.mode === 'height'));
  el.ramp.hidden = prefs.mode !== 'height';
  cloudMaterial.uniforms.uHeight.value = prefs.mode === 'height' ? 1 : 0;
  remember(); invalidate();
}
function setSize(mm) { prefs.size = mm; el.size.value = mm; el.sizeOut.textContent = `${(+mm).toFixed(1)} mm`; cloudMaterial.uniforms.uSize.value = mm / 1000; remember(); invalidate(); }
function setCrop(m) {
  prefs.crop = m; el.crop.value = m; el.cropOut.textContent = `${(+m).toFixed(1)} m`;
  cloudMaterial.uniforms.uCrop.value = m; cropRing.scale.set(m, m, 1); remember(); invalidate();
}
el.camera.addEventListener('click', () => setMode('camera'));
el.height.addEventListener('click', () => setMode('height'));
el.size.addEventListener('input', () => setSize(+el.size.value));
el.crop.addEventListener('input', () => setCrop(+el.crop.value));
el.follow.addEventListener('change', () => { setFollow(el.follow.checked); if (prefs.follow) poll(); });
el.instance.addEventListener('change', () => { url.searchParams.delete('capture'); openInstance(el.instance.value).catch((e) => why('That instance did not open', e.message)); });
for (const b of document.querySelectorAll('[data-view]')) b.addEventListener('click', () => view(b.dataset.view));
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });

// For the console: `await sceneView.bench()` turns the cloud one full circle and reports what a frame cost. `interval`
// is frame-to-frame (what the eye gets, capped by the display); `gpu` is render + gl.finish() (what the frame really costs).
const sceneView = window.sceneView = { last: null, bench(frames = 240) {
  return new Promise((done) => {
    const interval = new Float32Array(frames), gpu = new Float32Array(frames), theta0 = orbit.theta;
    const stat = (a) => { const s = Array.from(a).sort((x, y) => x - y); return { mean: +(s.reduce((x, y) => x + y, 0) / s.length).toFixed(2), p95: +s[Math.floor(s.length * 0.95)].toFixed(2), max: +s[s.length - 1].toFixed(2) }; };
    let i = -1, prev = 0;
    spin = {
      before(now) { if (i >= 0) interval[i] = now - prev; prev = now; i++; orbit.theta = theta0 + (Math.min(i, frames) / frames) * 2 * Math.PI; },
      after(ms) {
        if (i < frames) { gpu[i] = ms; return; }
        spin = null;
        done({ frames, points: cloudGeometry.drawRange.count, canvas: [el.canvas.width, el.canvas.height], interval_ms: stat(interval), gpu_ms: stat(gpu) });
      },
    };
    invalidate();
  });
} };

// ── go. Nothing here is awaited at the top level: the module finishes at once, so the page's load event is not held ──
setMode(prefs.mode); setSize(prefs.size); setCrop(prefs.crop); setFollow(prefs.follow !== false);
placeRobot(null, null); view('behind'); resize();
loadInstances()
  .then((name) => (name ? openInstance(name, url.searchParams.get('capture')) : null))
  .catch((e) => { why('The scene did not open', e.message); readout('not loaded — ', e.message); })
  .finally(() => { state.pollTimer = setTimeout(poll, POLL_MS); });
