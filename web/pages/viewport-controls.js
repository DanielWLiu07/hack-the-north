// viewport-controls.js — Blender's 3D viewport, for a three.js camera the page already owns.
//
// WHY. The room's camera was a free tumbler: every drag orbited, the up vector drifted, Q/E rolled the horizon,
// and there was no way to say "show me the room from the front". Anyone who lives in Blender fought it. This is
// Blender's 3D-viewport navigation instead, written from scratch — three.js is vendored here WITHOUT
// OrbitControls and nothing may come from a CDN — and it adds no renderer and no second WebGL context.
//
// WHAT IT BINDS. Mouse: middle-drag tumbles, shift+middle pans, ctrl+middle and the wheel dolly toward the
// cursor. Left- and right-drag keep doing what this page always did (orbit / pan) so a visitor who has never
// opened Blender is not stranded, and alt+left is Blender's own "Emulate 3 Button Mouse". Trackpad, which is
// what a Mac laptop demo actually is: two-finger scroll tumbles, shift+two-finger pans, pinch zooms — the three
// gestures Blender's default keymap puts on TRACKPADPAN / TRACKPADZOOM. Keys, numpad or the number row
// (Blender's "Emulate Numpad"): 1 front, 3 right, 7 top, ctrl+those for back / left / bottom, 9 orbit-opposite,
// 5 ortho ⇄ persp, 2/4/6/8 orbit 15°, 0 or Home frame everything, . frames the robot, +/- zoom.
//
// WHERE IT ORBITS. About a pivot, never the world origin. Zooming walks the pivot toward whatever is under the
// cursor, framing puts it on what you framed, alt+click drops it on the surface you clicked, and pressing the
// middle button runs Blender's Auto Depth: the pivot jumps to the point under the cursor while the camera
// stays exactly where it is.
//
// WHAT IT REFUSES TO DO. No damping on a drag and no momentum: the view stops when your hand does, and macOS's
// inertial scroll tail is dropped (see coasting). No roll, ever. No gimbal flip: elevation stops 0.1° short of
// each pole. The only animation is Blender's Smooth View, the 220 ms ease a view key jumps you through, and the
// page's "Smooth orbit" setting (or prefers-reduced-motion) turns even that off.
//
// FRAMES. The scene is y-up and a room map is z-up; this page maps map (x, y, z) onto scene (x, z, -y), so the
// room's up is scene +Y and the room's +Y is scene -Z. Every view below is named for the ROOM's axes, which is
// what the gizmo's letters mean and what a Blender user expects numpad 1 to show.
import * as THREE from 'three';

const UP = new THREE.Vector3(0, 1, 0);
const AXES = { x: new THREE.Vector3(1, 0, 0), y: new THREE.Vector3(0, 0, -1), z: new THREE.Vector3(0, 1, 0) };
const MAX_EL = THREE.MathUtils.degToRad(89.9);
const MIN_D = 0.02, MAX_D = 4000;
const SMOOTH_MS = 220;
const STEP = THREE.MathUtils.degToRad(15);
const CLICK_PX = 3;          // a drag shorter than this is a click, so alt+click can recentre
const PICK_PX = 26;          // how near the cursor a point must land to count as "what you are looking at"
const PICK_MAX = 32000;      // every cloud is sampled down to this, so picking a 600k-point room costs ~1 ms

// az is measured from the room's -Y (the front view) turning toward +X; el is the angle above the floor.
const VIEWS = {
  front: [0, 0], back: [Math.PI, 0], right: [Math.PI / 2, 0], left: [-Math.PI / 2, 0],
  top: [0, MAX_EL], bottom: [0, -MAX_EL],
};
const GIZMO = [['x', 1, 'X', '#e2606a'], ['x', -1, '', '#e2606a'], ['y', 1, 'Y', '#9ec93b'], ['y', -1, '', '#9ec93b'], ['z', 1, 'Z', '#5b86ea'], ['z', -1, '', '#5b86ea']];
const GIZMO_VIEW = { x1: 'right', 'x-1': 'left', y1: 'back', 'y-1': 'front', z1: 'top', 'z-1': 'bottom' };
const SVG = 'http://www.w3.org/2000/svg';

const CSS = `
.vpc-hud{position:absolute;left:22px;right:22px;bottom:50px;z-index:6;display:flex;align-items:flex-end;gap:10px;font:9px/1.5 monospace;letter-spacing:.08em;color:#8a8680;pointer-events:none}
.vpc-hud>*{flex:0 0 auto}
.vpc-gizmo{display:block;width:64px;height:64px;overflow:visible;pointer-events:auto}
.vpc-gizmo text{font:7px monospace;fill:#0c0c0c;text-anchor:middle;dominant-baseline:central;pointer-events:none;letter-spacing:0}
.vpc-gizmo g{cursor:pointer}
.voxel-active .vpc-hud{display:none}
.camera-current .vpc-hud{visibility:hidden}
@media(max-width:760px){.vpc-hud{left:18px;bottom:44px}}`;

const shortest = (a) => Math.atan2(Math.sin(a), Math.cos(a));
const ease = (t) => t * t * (3 - 2 * t);
const reduced = matchMedia('(prefers-reduced-motion: reduce)');

// The one thing on screen that is not a mesh: a cloud. room-cloud.js draws THREE.Points; scene-model.js's Layer
// draws the SAME array as instanced cubes, whose per-cell positions live in an `offset` attribute, not in
// `position`. Both answer here, and both are read live — those arrays are reused as the room reloads.
function cloudOf(obj) {
  const g = obj.geometry;
  if (!g) return null;
  let attr = null, count = 0;
  if (obj.isPoints) {
    attr = g.attributes.position;
    count = attr ? Math.min(attr.count, g.drawRange.count === Infinity ? attr.count : g.drawRange.start + g.drawRange.count) : 0;
  } else if (g.isInstancedBufferGeometry && g.attributes.offset) {
    attr = g.attributes.offset;
    count = Math.min(attr.count, g.instanceCount == null || g.instanceCount === Infinity ? attr.count : g.instanceCount);
  }
  if (!attr || !count || attr.itemSize !== 3 || attr.isInterleavedBufferAttribute) return null;
  return { array: attr.array, count };
}

export class ViewportControls extends THREE.EventDispatcher {
  constructor(camera, canvas, scene = null) {
    super();
    this.camera = camera; this.canvas = canvas; this.scene = scene;
    this.host = canvas.parentElement || document.body;
    this.target = new THREE.Vector3();
    this.distance = 6; this.azimuth = 0; this.elevation = 0;
    this.ortho = false;
    this.enabled = true;
    this.zoomSpeed = 1;
    this.orbitSpeed = 1;
    this.trackpadScale = 0.5;      // two-finger travel arrives scrolled, not dragged: half a drag reads as 1:1
    this.autoDepth = true;         // Blender's Auto Depth: tumble about the surface under the cursor
    this.enableDamping = true;     // the page's "Smooth orbit" setting; here it means Blender's Smooth View
    this.dampingFactor = 0.12;     // kept only so the page's settings code can still write it
    this.synced = false;
    this.pointers = new Map();
    this.drag = null;
    this.anim = null;
    this.wheelKind = '';           // '' | 'mouse' | 'trackpad', latched from the deltas Chrome reports
    this.wheelAt = 0; this.wheelPeak = 0; this.wheelLast = 0; this.wheelShrink = 0; this.wheelCoast = false;
    this.hovering = false;
    this.ray = new THREE.Raycaster();
    this.scratch = new Float32Array(PICK_MAX * 3);
    this.v = new THREE.Vector3(); this.w = new THREE.Vector3(); this.q = new THREE.Quaternion();
    this.box = new THREE.Box3(); this.tmpBox = new THREE.Box3(); this.mvp = new THREE.Matrix4(); this.vp = new THREE.Matrix4();
    this.gizmoKey = ''; this.gizmoQ = new THREE.Quaternion(0, 0, 0, 0);

    const base = camera.updateProjectionMatrix.bind(camera);     // numpad 5 without swapping the camera object out:
    camera.updateProjectionMatrix = () => {                      // room-map.js and room-voxels.js hold this instance
      if (!this.ortho) return base();
      const h = Math.max(this.distance, MIN_D) * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2);
      const w = h * camera.aspect, far = Math.max(this.distance * 50, 500);
      camera.projectionMatrix.makeOrthographic(-w, w, h, -h, -far, far, camera.coordinateSystem);
      camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
    };

    canvas.style.touchAction = 'none';
    canvas.addEventListener('contextmenu', (e) => e.preventDefault());
    canvas.addEventListener('pointerdown', (e) => this.onDown(e));
    canvas.addEventListener('pointermove', (e) => this.onMove(e));
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) canvas.addEventListener(name, (e) => this.onUp(e));
    canvas.addEventListener('wheel', (e) => this.onWheel(e), { passive: false });
    this.host.addEventListener('pointerenter', () => { this.hovering = this.everHovered = true; });
    this.host.addEventListener('pointerleave', () => { this.hovering = false; });
    document.addEventListener('keydown', (e) => this.onKey(e));
    this.buildHud();
  }

  // ── where the camera ends up ───────────────────────────────────────────────
  sync() {                                                       // the page moved the camera itself: read it back
    this.anim = null;
    const off = this.v.copy(this.camera.position).sub(this.target), len = off.length();
    this.distance = THREE.MathUtils.clamp(len, MIN_D, MAX_D);
    this.elevation = len > 1e-9 ? THREE.MathUtils.clamp(Math.asin(THREE.MathUtils.clamp(off.y / len, -1, 1)), -MAX_EL, MAX_EL) : 0;
    this.azimuth = len > 1e-9 ? Math.atan2(off.x, off.z) : this.azimuth;
    this.synced = true;
    this.place();
  }

  place() {
    const c = this.camera, d = this.distance, ce = Math.cos(this.elevation);
    c.position.set(this.target.x + d * ce * Math.sin(this.azimuth), this.target.y + d * Math.sin(this.elevation), this.target.z + d * ce * Math.cos(this.azimuth));
    c.up.copy(UP);
    c.lookAt(this.target);
    const near = Math.max(d / 10000, 1e-7), far = Math.max(d * 100, 1000);
    if (near !== c.near || far !== c.far || d !== this.projD) {   // this runs 60 times a second: only rebuild the
      c.near = near; c.far = far; this.projD = d;                 // projection when the clip planes actually moved
      c.updateProjectionMatrix();
    }
    c.updateMatrixWorld();
  }

  update() {
    if (!this.synced) this.sync();
    let moving = false;
    if (this.anim) {
      const a = this.anim, t = Math.min(1, (performance.now() - a.t0) / a.ms), k = ease(t);
      this.target.copy(a.from.target).lerp(a.to.target, k);
      this.distance = a.from.distance * Math.pow(a.to.distance / a.from.distance, k);
      this.azimuth = a.from.azimuth + a.spin * k;
      this.elevation = a.from.elevation + (a.to.elevation - a.from.elevation) * k;
      if (t >= 1) this.anim = null; else moving = true;
    }
    this.place();
    this.paintGizmo();
    return moving;
  }

  goto(to, { animate = true } = {}) {                            // Blender's Smooth View
    this.ready();
    const want = {
      target: to.target ? to.target.clone() : this.target.clone(),
      distance: THREE.MathUtils.clamp(to.distance === undefined ? this.distance : to.distance, MIN_D, MAX_D),
      azimuth: to.azimuth === undefined ? this.azimuth : to.azimuth,
      elevation: THREE.MathUtils.clamp(to.elevation === undefined ? this.elevation : to.elevation, -MAX_EL, MAX_EL),
    };
    if (!animate || !this.enableDamping || reduced.matches) {
      this.target.copy(want.target); this.distance = want.distance; this.azimuth = want.azimuth; this.elevation = want.elevation;
      this.anim = null;
    } else {
      this.anim = { t0: performance.now(), ms: SMOOTH_MS, spin: shortest(want.azimuth - this.azimuth), to: want,
        from: { target: this.target.clone(), distance: this.distance, azimuth: this.azimuth, elevation: this.elevation } };
    }
    this.changed();
  }

  ready() { if (!this.synced) this.sync(); }
  changed() { this.synced = true; this.dispatchEvent({ type: 'change' }); }

  // ── the three moves ────────────────────────────────────────────────────────
  rotate(dx, dy) {                                               // turntable: yaw about the room's up, then pitch
    this.ready();
    const rate = (2 * Math.PI / Math.max(this.canvas.clientHeight, 1)) * this.orbitSpeed;
    this.azimuth -= dx * rate;
    this.elevation = THREE.MathUtils.clamp(this.elevation + dy * rate, -MAX_EL, MAX_EL);
    this.anim = null;
    this.changed();
  }

  pan(dx, dy) {
    this.ready();
    const perPx = 2 * Math.max(this.distance, MIN_D) * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2) / Math.max(this.canvas.clientHeight, 1);
    const m = this.camera.matrixWorld.elements;                  // columns 0 and 1 are the camera's right and up
    this.target.x += (-dx * m[0] + dy * m[4]) * perPx;
    this.target.y += (-dx * m[1] + dy * m[5]) * perPx;
    this.target.z += (-dx * m[2] + dy * m[6]) * perPx;
    this.anim = null;
    this.changed();
  }

  // Scaling the whole view about the point under the cursor keeps that point on the same pixel, which is what
  // "zoom towards the mouse" means — and it is why the pivot ends up on what you zoomed into.
  dolly(factor, event = null) {
    this.ready();
    const next = THREE.MathUtils.clamp(this.distance * THREE.MathUtils.clamp(factor, 0.2, 5), MIN_D, MAX_D);
    const k = next / this.distance;
    if (event) {
      const r = this.canvas.getBoundingClientRect();
      const nx = ((event.clientX - r.left) / Math.max(r.width, 1)) * 2 - 1, ny = 1 - ((event.clientY - r.top) / Math.max(r.height, 1)) * 2;
      const halfH = this.distance * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2), halfW = halfH * this.camera.aspect;
      const m = this.camera.matrixWorld.elements, ox = nx * halfW * (1 - k), oy = ny * halfH * (1 - k);
      this.target.x += m[0] * ox + m[4] * oy;
      this.target.y += m[1] * ox + m[5] * oy;
      this.target.z += m[2] * ox + m[6] * oy;
    }
    this.distance = next;
    this.anim = null;
    this.changed();
  }

  // ── pointers ───────────────────────────────────────────────────────────────
  modeFor(e) {
    if (e.button === 2) return 'pan';
    if (e.ctrlKey) return 'dolly';
    if (e.shiftKey || e.metaKey) return 'pan';
    return 'orbit';
  }

  onDown(e) {
    if (!this.enabled) return;
    this.canvas.focus({ preventScroll: true });
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    try { this.canvas.setPointerCapture(e.pointerId); } catch { /* the pointer is already gone */ }
    if (this.pointers.size > 1) { this.drag = null; return; }
    this.drag = { id: e.pointerId, mode: this.modeFor(e), moved: 0, alt: e.altKey };
    if (this.drag.mode === 'orbit' && this.autoDepth && !e.altKey) this.focusUnder(e, true);
  }

  onMove(e) {
    if (!this.enabled) return;
    const was = this.pointers.get(e.pointerId);
    if (!was) return;
    const dx = e.clientX - was.x, dy = e.clientY - was.y;
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (this.pointers.size === 2) {                              // touchscreen: two fingers pan and pinch
      const other = [...this.pointers.entries()].find(([id]) => id !== e.pointerId);
      if (other) {
        const before = Math.hypot(was.x - other[1].x, was.y - other[1].y), after = Math.hypot(e.clientX - other[1].x, e.clientY - other[1].y);
        this.pan(dx * 0.5, dy * 0.5);
        if (before > 1 && after > 1) this.dolly(before / after);
      }
      return;
    }
    const d = this.drag;
    if (!d || d.id !== e.pointerId) return;
    d.moved += Math.abs(dx) + Math.abs(dy);
    if (d.alt && d.moved < CLICK_PX) return;
    if (d.mode === 'orbit') this.rotate(dx, dy);
    else if (d.mode === 'pan') this.pan(dx, dy);
    else this.dolly(Math.exp(dy * 0.006 * this.zoomSpeed));
  }

  onUp(e) {
    this.pointers.delete(e.pointerId);
    const d = this.drag;
    if (!d || d.id !== e.pointerId) return;
    if (d.alt && d.moved < CLICK_PX) this.focusUnder(e, false);  // Blender's alt+MMB, "Center View to Mouse"
    this.drag = null;
  }

  // macOS hands Chrome the trackpad's inertial tail as more wheel events, and left alone the room keeps turning
  // for a second after your fingers stop. A coasting delta only ever decays; a hand on glass speeds up as often
  // as it slows. So: three shrinking events running, well off the gesture's peak, and the rest of the tail is
  // dropped. Nothing but a real 8% push (or a 140 ms pause, which starts a new gesture) turns it back on. About
  // three frames of a flick still land before the cut — the coast after that, which is the part you would call
  // drift, does not.
  coasting(mag) {
    const now = performance.now();
    if (now - this.wheelAt > 140) { this.wheelPeak = 0; this.wheelShrink = 0; this.wheelCoast = false; this.wheelLast = 0; }
    this.wheelAt = now;
    if (mag > this.wheelLast * 1.08 + 0.05) { this.wheelShrink = 0; this.wheelCoast = false; }
    else if (mag < this.wheelLast) this.wheelShrink++;
    this.wheelLast = mag;
    this.wheelPeak = Math.max(this.wheelPeak, mag);
    if (this.wheelShrink >= 3 && mag < this.wheelPeak * 0.8) this.wheelCoast = true;
    return this.wheelCoast;
  }

  classify(e) {                                                  // a wheel notch is a big round number with no x
    const dy = Math.abs(e.deltaY);
    if (e.deltaMode !== 0 || (e.deltaX === 0 && dy >= 100 && Number.isInteger(e.deltaY))) this.wheelKind = 'mouse';
    else if (e.deltaX !== 0 || dy < 40 || !Number.isInteger(e.deltaY)) this.wheelKind = 'trackpad';
    return this.wheelKind || 'mouse';
  }

  onWheel(e) {
    if (!this.enabled) return;
    e.preventDefault();
    if (e.ctrlKey) {                                             // Chrome reports a trackpad pinch as ctrl + wheel
      this.wheelKind = 'trackpad';
      this.dolly(Math.exp(THREE.MathUtils.clamp(e.deltaY, -60, 60) * 0.012 * this.zoomSpeed), e);
      return;
    }
    // shift+wheel axes are swapped by the OS: never classify on them
    const kind = e.shiftKey ? (this.wheelKind || 'mouse') : this.classify(e);
    if (e.shiftKey) {                                            // Blender puts shift+wheel on pan, too
      if (kind === 'trackpad' && this.coasting(Math.hypot(e.deltaX, e.deltaY))) return;
      const s = kind === 'mouse' ? 0.4 : 1;
      this.pan(-e.deltaX * s, -e.deltaY * s);
      return;
    }
    if (kind === 'mouse') { this.dolly(Math.pow(1.2, Math.sign(e.deltaY) * this.zoomSpeed), e); return; }
    const dx = -e.deltaX, dy = -e.deltaY;                        // fingers move the view, so the scroll is inverted
    if (this.coasting(Math.hypot(dx, dy))) return;
    this.rotate(dx * this.trackpadScale, dy * this.trackpadScale);
  }

  // ── keys ───────────────────────────────────────────────────────────────────
  // Blender's viewport is hover-focused, and so is this: the keys are the viewport's only while the pointer is
  // over it or the canvas holds focus, so they never fight the agent panel or Settings. Before the pointer has
  // touched anything at all — a fresh page nobody has moved the mouse on yet — the viewport takes them.
  listening(e) {
    const t = e.target;
    if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement || (t && t.isContentEditable)) return false;
    return this.hovering || document.activeElement === this.canvas || !this.everHovered;
  }

  onKey(e) {
    if (!this.enabled || e.metaKey || e.altKey || !this.listening(e)) return;
    const digit = /^(?:Numpad|Digit)([0-9])$/.exec(e.code), n = digit ? digit[1] : '';
    const ctrl = e.ctrlKey;
    let done = true;
    if (n === '1') this.axisView(ctrl ? 'back' : 'front');
    else if (n === '3') this.axisView(ctrl ? 'left' : 'right');
    else if (n === '7') this.axisView(ctrl ? 'bottom' : 'top');
    else if (n === '9') this.goto({ azimuth: this.azimuth + Math.PI, elevation: -this.elevation });
    else if (n === '5') this.setOrtho(!this.ortho);
    else if (n === '0' || e.code === 'Home') this.frameAll();
    else if (n === '4') this.goto({ azimuth: this.azimuth - STEP });
    else if (n === '6') this.goto({ azimuth: this.azimuth + STEP });
    else if (n === '8') this.goto({ elevation: this.elevation + STEP });
    else if (n === '2') this.goto({ elevation: this.elevation - STEP });
    else if (e.code === 'NumpadDecimal' || e.code === 'Period') this.frameRobot();
    else if (e.code === 'NumpadAdd' || e.key === '+' || e.key === '=') this.dolly(1 / 1.2);
    else if (e.code === 'NumpadSubtract' || e.key === '-') this.dolly(1.2);
    else if (ctrl) done = false;                                 // every other ctrl chord is the browser's
    else if (e.key === 'a') this.pan(40, 0);
    else if (e.key === 'd') this.pan(-40, 0);
    else if (e.key === 'w') this.pan(0, 40);
    else if (e.key === 's') this.pan(0, -40);
    else if (e.key === 'ArrowLeft') this.goto({ azimuth: this.azimuth - STEP });
    else if (e.key === 'ArrowRight') this.goto({ azimuth: this.azimuth + STEP });
    else if (e.key === 'ArrowUp') this.goto({ elevation: this.elevation + STEP });
    else if (e.key === 'ArrowDown') this.goto({ elevation: this.elevation - STEP });
    else done = false;
    if (done) e.preventDefault();
  }

  axisView(name) {
    const [az, el] = VIEWS[name] || VIEWS.front;
    this.goto({ azimuth: az, elevation: el });
  }

  setOrtho(on) {
    this.ortho = Boolean(on);
    this.camera.updateProjectionMatrix();
    this.changed();
  }

  // ── what you are looking at ────────────────────────────────────────────────
  sample(obj) {                                                  // up to PICK_MAX local positions into this.scratch
    const cloud = cloudOf(obj);
    if (!cloud) return 0;
    const { array, count } = cloud, stride = Math.max(1, Math.ceil(count / PICK_MAX)), out = this.scratch;
    let k = 0;
    for (let i = 0; i < count; i += stride) {
      const j = i * 3;
      out[k++] = array[j]; out[k++] = array[j + 1]; out[k++] = array[j + 2];
    }
    return k / 3;
  }

  parts() {
    const clouds = [], meshes = [];
    if (!this.scene) return { clouds, meshes };
    this.scene.updateMatrixWorld();
    const walk = (o) => {
      if (o.visible === false || o.name === 'robotSplat' || o.type === 'GridHelper' || o.type === 'Box3Helper') return;
      if (cloudOf(o)) clouds.push(o);
      else if (o.isMesh) meshes.push(o);                         // lines and labels are decoration, never a target
      for (const c of o.children) walk(c);
    };
    for (const c of this.scene.children) walk(c);
    return { clouds, meshes };
  }

  pick(clientX, clientY) {
    const { clouds, meshes } = this.parts();
    if (!clouds.length && !meshes.length) return null;
    const r = this.canvas.getBoundingClientRect();
    const nx = ((clientX - r.left) / Math.max(r.width, 1)) * 2 - 1, ny = 1 - ((clientY - r.top) / Math.max(r.height, 1)) * 2;
    this.camera.updateMatrixWorld();
    const near = this.v.set(nx, ny, -1).unproject(this.camera).clone();       // unproject reads the matrix installed
    const far = this.w.set(nx, ny, 1).unproject(this.camera);                 // above, so this is right in ortho too
    this.ray.ray.origin.copy(near);
    this.ray.ray.direction.copy(far).sub(near).normalize();
    this.ray.near = 0; this.ray.far = Infinity;
    let best = null, bestDepth = Infinity;
    for (const hit of this.ray.intersectObjects(meshes, false)) { best = hit.point.clone(); bestDepth = best.distanceTo(this.camera.position); break; }
    const halfW = Math.max(r.width, 1) / 2, halfH = Math.max(r.height, 1) / 2;
    this.vp.multiplyMatrices(this.camera.projectionMatrix, this.camera.matrixWorldInverse);
    for (const cloud of clouds) {
      const n = this.sample(cloud);
      if (!n) continue;
      const m = this.mvp.multiplyMatrices(this.vp, cloud.matrixWorld).elements, pts = this.scratch;
      let hit = -1, hitPx = PICK_PX, hitZ = Infinity;
      for (let i = 0; i < n * 3; i += 3) {
        const px = pts[i], py = pts[i + 1], pz = pts[i + 2];
        const w = m[3] * px + m[7] * py + m[11] * pz + m[15];
        if (w <= 1e-6) continue;
        const sx = (m[0] * px + m[4] * py + m[8] * pz + m[12]) / w, sy = (m[1] * px + m[5] * py + m[9] * pz + m[13]) / w;
        const dpx = Math.hypot((sx - nx) * halfW, (sy - ny) * halfH);
        if (dpx > hitPx) continue;
        const z = (m[2] * px + m[6] * py + m[10] * pz + m[14]) / w;
        if (z >= hitZ) continue;                                 // nearest to the camera wins, like a depth buffer
        hit = i; hitZ = z;
      }
      if (hit < 0) continue;
      const p = new THREE.Vector3(pts[hit], pts[hit + 1], pts[hit + 2]).applyMatrix4(cloud.matrixWorld);
      const depth = p.distanceTo(this.camera.position);
      if (depth < bestDepth) { best = p; bestDepth = depth; }
    }
    return best;
  }

  focusUnder(e, keepCamera) {
    const p = this.pick(e.clientX, e.clientY);
    if (!p) return false;
    if (!keepCamera) { this.goto({ target: p }); return true; }
    const d = p.distanceTo(this.camera.position);                // Auto Depth: the pivot moves, the camera does not
    if (!(d > 0.25) || d > MAX_D) return false;
    this.target.copy(p);
    this.sync();
    return true;
  }

  // ── framing ────────────────────────────────────────────────────────────────
  contentBox() {
    const box = this.box.makeEmpty();
    if (!this.scene) return box;
    this.scene.updateMatrixWorld();
    const walk = (o) => {
      if (o.visible === false || o.type === 'GridHelper' || o.type === 'Box3Helper' || o.type === 'AxesHelper') return;
      if (o.name === 'robotSplat') {                             // a splat has no geometry to measure: use its stance
        const p = o.getWorldPosition(this.v).clone();
        box.expandByPoint(this.v.set(p.x - 0.4, p.y, p.z - 0.4)); box.expandByPoint(this.v.set(p.x + 0.4, p.y + 1.6, p.z + 0.4));
        return;
      }
      const n = this.sample(o);
      if (n) {
        const pts = this.scratch, m = o.matrixWorld;
        for (let i = 0; i < n * 3; i += 3) box.expandByPoint(this.v.set(pts[i], pts[i + 1], pts[i + 2]).applyMatrix4(m));
      } else if (o.geometry) {
        const g = o.geometry;
        if (!g.boundingBox) g.computeBoundingBox();
        if (g.boundingBox && Number.isFinite(g.boundingBox.min.x)) box.union(this.tmpBox.copy(g.boundingBox).applyMatrix4(o.matrixWorld));
      }
      for (const c of o.children) walk(c);
    };
    for (const c of this.scene.children) walk(c);
    return box;
  }

  frameBox(box, animate = true) {
    if (!box || box.isEmpty()) return false;
    const center = box.getCenter(new THREE.Vector3()), radius = Math.max(box.getSize(this.v).length() / 2, 0.15);
    const vfov = THREE.MathUtils.degToRad(this.camera.fov), hfov = 2 * Math.atan(Math.tan(vfov / 2) * Math.max(this.camera.aspect, 0.2));
    const distance = Math.max(radius / Math.sin(vfov / 2), radius / Math.sin(hfov / 2)) * 1.04;
    this.goto({ target: center, distance }, { animate });        // Blender's View All keeps the angle you were at
    return true;
  }

  frameAll(animate = true) {
    this.frameBox(this.contentBox(), animate);
  }

  frameRobot(animate = true) {                                   // Blender's numpad . — the robot is the selection
    let at = null;
    if (this.scene) {
      this.scene.updateMatrixWorld();
      this.scene.traverse((o) => {
        if (at || o.visible === false) return;
        const marker = o.isGroup && !o.name && o.children.some((k) => k.geometry && k.geometry.type === 'CircleGeometry');
        if (o.name === 'robotSplat' || marker) at = o.getWorldPosition(new THREE.Vector3());
      });
    }
    if (!at) { this.frameAll(animate); return; }
    this.frameBox(new THREE.Box3(new THREE.Vector3(at.x - 0.6, at.y - 0.1, at.z - 0.6), new THREE.Vector3(at.x + 0.6, at.y + 1.7, at.z + 0.6)), animate);
  }

  // ── the corner gizmo ───────────────────────────────────────────────────────
  buildHud() {
    if (!document.getElementById('vpc-style')) {
      const style = document.createElement('style');
      style.id = 'vpc-style'; style.textContent = CSS;
      document.head.append(style);
    }
    const hud = document.createElement('div');
    hud.className = 'vpc-hud';
    const svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('class', 'vpc-gizmo');
    svg.setAttribute('viewBox', '-34 -34 68 68');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Axis gizmo. Click an axis ball to look down that axis.');
    this.balls = GIZMO.map(([axis, sign, letter, color]) => {
      const g = document.createElementNS(SVG, 'g');
      const line = document.createElementNS(SVG, 'line');
      line.setAttribute('stroke', color); line.setAttribute('stroke-width', '1.7');
      line.setAttribute('x1', '0'); line.setAttribute('y1', '0');
      const ball = document.createElementNS(SVG, 'circle');
      ball.setAttribute('r', sign > 0 ? '7' : '5.4');
      ball.setAttribute('fill', sign > 0 ? color : '#101010');
      ball.setAttribute('stroke', color); ball.setAttribute('stroke-width', '1.6');
      const text = document.createElementNS(SVG, 'text');
      text.textContent = letter;
      g.append(line, ball, text);
      g.addEventListener('pointerdown', (e) => { e.preventDefault(); this.axisView(GIZMO_VIEW[`${axis}${sign}`]); });
      svg.append(g);
      return { axis, sign, g, line, ball, text, positive: sign > 0 };
    });
    hud.append(svg);
    this.host.append(hud);
    this.hud = hud;
  }

  paintGizmo() {
    if (!this.balls) return;
    const q = this.camera.quaternion;
    if (Math.abs(this.gizmoQ.dot(q)) > 0.999995) return;         // nothing turned: leave the DOM alone
    this.gizmoQ.copy(q);
    const inv = this.q.copy(q).invert(), R = 24;
    const seen = this.balls.map((b) => {
      const v = this.v.copy(AXES[b.axis]).multiplyScalar(b.sign).applyQuaternion(inv);
      return { b, x: v.x * R, y: -v.y * R, z: v.z };
    });
    seen.sort((a, c) => a.z - c.z);
    const order = seen.map((s) => `${s.b.axis}${s.b.sign}`).join(' ');
    const reorder = order !== this.gizmoKey;
    for (const { b, x, y, z } of seen) {
      b.ball.setAttribute('cx', x.toFixed(2)); b.ball.setAttribute('cy', y.toFixed(2));
      b.text.setAttribute('x', x.toFixed(2)); b.text.setAttribute('y', y.toFixed(2));
      b.line.setAttribute('x2', x.toFixed(2)); b.line.setAttribute('y2', y.toFixed(2));
      b.line.setAttribute('opacity', b.positive ? '0.9' : '0');
      b.g.setAttribute('opacity', (0.45 + 0.55 * (z + 1) / 2).toFixed(3));
      if (reorder) b.g.parentNode.append(b.g);                   // painter's order, only when it actually changed
    }
    this.gizmoKey = order;
  }

  dispose() {
    this.enabled = false;
    if (this.hud) this.hud.remove();
  }
}

export { ViewportControls as FreeCameraControls };
