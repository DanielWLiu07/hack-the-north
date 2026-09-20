// The room as a 3D point cloud, with the scanned robot splat standing at its map pose.
import * as THREE from 'three';
import { ViewportControls as OrbitControls } from './viewport-controls.js';
import { loadRobotSplat } from '/splat.js';

const host = document.querySelector('.viewer');
const label = document.querySelector('#view-label');
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const MAP = '/live/robot-map.json';
const ROBOT_HEIGHT = 1.55; // Bracket Bot, metres — same as landing/robot.js BOT.height
const POINT_BYTES = 15; // float x y z + uchar rgb — the layout room_live.write_ply writes
const MAX_POINTS = 600000; // stereo captures top out ~500k; only thin anything bigger
const COMMIT_RE = /^[0-9a-f]{7,40}$/;
const color = new THREE.Color();
const frameHooks = [];

const canvas = document.createElement('canvas');
canvas.className = 'cloud-canvas';
canvas.tabIndex = 0;
canvas.setAttribute('aria-label', 'Hallway point cloud with the robot at its recorded pose. Drag to orbit, right-drag or shift-drag to pan, wheel to zoom.');
const state = document.createElement('p');
state.className = 'cloud-state';
state.textContent = 'loading hallway';
state.dataset.ready = 'false';
// ── the room's history, as a VERTICAL commit graph — the History tab ──────────────────
// One node is one COMMIT, and a commit of this repo is one point cloud AND the objects found in
// it (cloud/current.ply + zones/<zone>/<id>.yaml), so the same pair of nodes answers "what did
// the room look like" and "what actually moved". Time runs TOP -> BOTTOM, newest at the top,
// lanes across x — the orientation a `git log --graph` has always had.
//
// It lives in ONE place: the History tab's right-hand panel (#room-history), where the Health tab
// used to be. It no longer floats over the 3D canvas as well — two copies of one graph on one page
// is the confusion this replaced.
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const svgEl = (tag, attrs) => { const n = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const k in attrs) if (attrs[k] != null) n.setAttribute(k, String(attrs[k])); return n; };

const historyHost = document.getElementById('room-history');
const log = document.createElement('nav');
log.className = 'git-log';
log.hidden = true;
log.setAttribute('aria-label', 'The room\u2019s point clouds as a commit graph');
const logBar = el('div', 'git-bar');
const strip = el('div', 'git-strip');
strip.tabIndex = 0;
strip.setAttribute('role', 'group');
strip.setAttribute('aria-label', 'Commits of the room, newest at the top. Up and down arrows step commit to commit; home jumps to the newest and end to the first scan. Square brackets do the same from anywhere on the page.');
const track = el('div', 'git-track');
const rails = svgEl('svg', { class: 'git-rails', 'aria-hidden': 'true', focusable: 'false' });
const nodeList = document.createElement('ol');
track.append(rails, nodeList);
strip.append(track);
log.append(logBar, strip);
const panel = document.createElement('aside');
panel.className = 'git-panel';
panel.hidden = true;
panel.setAttribute('aria-live', 'polite');
panel.setAttribute('aria-label', 'The selected commit, and what changed');
log.append(panel);
host.append(canvas, state);
(historyHost || host).append(log);
host.classList.add('cloud-active');

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
renderer.setClearColor('#090909', 1);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(48, 1, 0.05, 200);
const controls = new OrbitControls(camera, canvas, scene);
controls.dampingFactor = 0.12;
controls.addEventListener('change', () => wake());
const shared = { uSize: { value: 0.034 }, uScale: { value: 800 }, uMaxPx: { value: 18 } };
const material = new THREE.ShaderMaterial({
  uniforms: shared,
  vertexShader: `
    uniform float uSize, uScale, uMaxPx;
    attribute vec3 rgb; varying vec3 vColor;
    void main() {
      vec4 mv = modelViewMatrix * vec4(position, 1.0);
      gl_Position = projectionMatrix * mv;
      gl_PointSize = clamp(uSize * uScale / max(-mv.z, 0.08), 1.4, uMaxPx);
      vColor = rgb;
    }`,
  fragmentShader: `
    varying vec3 vColor;
    void main() {
      vec2 d = gl_PointCoord - 0.5;
      if (dot(d, d) > 0.25) discard;
      gl_FragColor = vec4(vColor, 1.0);
    }`,
});

let points = null, grid = null, box = null, robotMark = null, robotSplat = null, robotPose = null;
let raf = 0, frames = 0, count = 0, source = '';
let topView = false, fitted = false, instanceName = '', selectedSha = '', commits = [];
let follow = true, adding = false, pollTimer = 0;
let hist = null, compareId = null, openCommand = false;   // hist: the whole /history payload — branches, refs, dirty
const POLL_MS = 5000;

function toWorld(x, y, z, into, i) {
  into[i] = x; into[i + 1] = z; into[i + 2] = -y;
}
function hexColor(hex, into, i, lift = 1.18) {
  color.set(/^#[0-9a-f]{6}$/i.test(hex) ? hex : '#8a8680');
  into[i] = Math.min(1, color.r * lift + 0.04);
  into[i + 1] = Math.min(1, color.g * lift + 0.04);
  into[i + 2] = Math.min(1, color.b * lift + 0.04);
}
function hash(i) {
  const t = Math.sin(i * 12.9898) * 43758.5453;
  return t - Math.floor(t);
}

function hallwayCloud() {
  // Straight corridor, metres, z-up: 10 m long, 1.5 m wide, 2.4 m tall. Jittered so it reads as a scan.
  const xyz = [], rgb = [];
  let n = 0;
  const put = (x, y, z, hex) => {
    const j = n * 3, s = 0.012;
    toWorld(x + (hash(n) - 0.5) * s, y + (hash(n + 17) - 0.5) * s, Math.max(0, z + (hash(n + 31) - 0.5) * s), xyz, j);
    hexColor(hex, rgb, j, 1);
    n++;
  };
  for (let x = -0.2; x <= 10.2; x += 0.03) {
    for (let y = -0.75; y <= 0.75; y += 0.03) {
      if (hash(x * 80 + y * 13) > 0.18) put(x, y, 0.01, y * y > 0.42 ? '#6a6762' : '#9a958c');
    }
  }
  for (let x = 0; x <= 10; x += 0.03) {
    for (let z = 0.04; z <= 2.35; z += 0.03) {
      if (hash(x * 40 + z * 9) > 0.22) put(x, -0.74, z, z > 1.8 ? '#5c5a56' : '#8d8880');
      if (hash(x * 41 + z * 11 + 3) > 0.22) put(x, 0.74, z, z > 1.8 ? '#55534f' : '#7f7b74');
    }
  }
  for (let x = 0; x <= 10; x += 0.04) {
    for (let y = -0.72; y <= 0.72; y += 0.04) {
      if (hash(x * 21 + y * 8) > 0.45) put(x, y, 2.38, '#4a4946');
    }
  }
  for (let y = -0.7; y <= 0.7; y += 0.03) {
    for (let z = 0.04; z <= 2.35; z += 0.03) {
      const open = Math.abs(y) < 0.38 && z < 2.05;
      if (!open && hash(y * 30 + z * 7) > 0.2) put(10.05, y, z, '#6e6a64');
      if (hash(y * 18 + z * 5 + 2) > 0.35) put(-0.05, y, z, '#5a5854');
    }
  }
  for (const [x, y, z, hex] of [[3.2, -0.52, 0.18, '#c4b39a'], [3.35, -0.48, 0.42, '#b9a48a'], [6.8, 0.4, 0.12, '#3b3a38'], [6.85, 0.38, 0.28, '#2e2d2b']]) {
    for (let i = 0; i < 80; i++) put(x + hash(i) * 0.18, y + hash(i + 4) * 0.14, z + hash(i + 9) * 0.22, hex);
  }
  return { xyz: new Float32Array(xyz), rgb: new Float32Array(rgb), n, source: 'example hallway', robot: { x: 0.4, y: 0, heading_rad: 0 } };
}

function mapCloud(data) {
  const cells = Array.isArray(data.cells) ? data.cells : [];
  const xyz = new Float32Array(cells.length * 3), rgb = new Float32Array(cells.length * 3);
  let n = 0;
  for (const c of cells) {
    const p = c.center;
    if (!Array.isArray(p) || p.length < 3 || !p.every(Number.isFinite)) continue;
    const i = n * 3;
    toWorld(p[0], p[1], p[2], xyz, i);
    hexColor(c.color, rgb, i);
    n++;
  }
  if (!n) throw Error('Hallway map had no usable points.');
  return {
    xyz: xyz.subarray(0, n * 3), rgb: rgb.subarray(0, n * 3), n,
    source: data.source === 'bbos mapping.voxels + slam.pose' ? 'robot hallway map' : 'hallway map',
    robot: data.robot, total: data.total, voxel: data.voxel_m,
  };
}

function caption() {
  const pose = robotPose && Number.isFinite(+robotPose.x) ? ' · robot at map pose' : '';
  const body = robotSplat ? ' · scanned splat' : '';
  // with ?cloud=0 the scan is loaded (the camera and the guides are measured from it) but not drawn, so the
  // caption must not advertise points nobody can see — it says what IS on screen, and that the scan is hidden
  if (!showCloud) { state.textContent = `${source} · scan hidden (?cloud=0) · room bounds only${pose}`; return; }
  state.textContent = `${source} · ${count.toLocaleString()} points${pose}${body}`;
}

function clearGuide() {
  for (const obj of [grid, box, robotMark]) {
    if (!obj) continue;
    scene.remove(obj);
    obj.traverse?.((o) => { o.geometry?.dispose(); o.material?.dispose?.(); });
    obj.geometry?.dispose(); obj.material?.dispose?.();
  }
  grid = box = robotMark = null;
}

function placeRobot() {
  const r = robotPose || {};
  if (!Number.isFinite(+r.x) || !Number.isFinite(+r.y)) return;
  const x = +r.x, z = -(+r.y), yaw = Number.isFinite(+r.yaw) ? +r.yaw : (+r.heading_rad || 0) + Math.PI / 2;   // heading 0 faces +y (measured)
  if (robotSplat) {
    robotSplat.position.set(x, 0, z);
    // Canonical splat faces +Z (= map -y); yaw is CCW from +x in the map, so the turn about y is yaw + 90°.
    robotSplat.rotation.set(0, Math.PI / 2 + yaw, 0);
    robotSplat.visible = true;
  }
  if (robotMark) {
    robotMark.position.set(x, 0, z);
    robotMark.rotation.y = yaw;
    for (const child of robotMark.children) child.visible = child.userData.keep === true || !robotSplat;
  }
}

function guides(cloud) {
  clearGuide();
  robotPose = cloud.robot || null;
  const b = points.geometry.boundingBox, size = b.getSize(new THREE.Vector3()), center = b.getCenter(new THREE.Vector3());
  const span = Math.max(size.x, size.z, 1);
  grid = new THREE.GridHelper(span * 1.25, Math.max(8, Math.round(span)), 0x4a4842, 0x1c1b19);
  grid.position.set(center.x, b.min.y, center.z);
  grid.material.transparent = true; grid.material.opacity = 0.55;
  grid.visible = document.querySelector('#show-grid')?.checked !== false;
  scene.add(grid);
  box = new THREE.Box3Helper(b, 0x6a675f);
  box.visible = document.querySelector('#show-bounds')?.checked !== false;
  scene.add(box);
  if (Number.isFinite(+robotPose?.x) && Number.isFinite(+robotPose?.y)) {
    robotMark = new THREE.Group();
    const disc = new THREE.Mesh(new THREE.CircleGeometry(0.22, 28), new THREE.MeshBasicMaterial({ color: 0xefece6, transparent: true, opacity: 0.28, depthTest: false }));
    disc.rotation.x = -Math.PI / 2; disc.position.y = 0.012; disc.userData.keep = true;
    const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.03, 1.1, 8), new THREE.MeshBasicMaterial({ color: 0xefece6 }));
    mast.position.y = 0.55;
    const arrow = new THREE.Mesh(new THREE.ConeGeometry(0.06, 0.2, 10), new THREE.MeshBasicMaterial({ color: 0xefece6, depthTest: false }));
    arrow.rotation.z = -Math.PI / 2; arrow.position.set(0.28, 0.04, 0);
    robotMark.add(disc, mast, arrow);
    scene.add(robotMark);
  }
  placeRobot();
}

// ?cloud=0 hides the SCANNED point cloud, and nothing else — opt-in, symmetric with room-voxels' ?octree=1, and
// never a default: dense layers stay off unless the URL asks. It is for the one screen that shows the Elastic
// octree on its own, because the only cloud that exists to draw is a different room's (the story commits carry no
// .ply at all), and two rooms on one floor is nonsense. The cloud is still LOADED: the camera fit, the guides and
// the caption are all measured from it, so skipping the fetch would frame the octree against nothing.
const showCloud = new URL(location.href).searchParams.get('cloud') !== '0';

function placeInMap(xyz, robot) {
  // A capture's PLY is in the ROBOT's frame (x forward, y left). With the SLAM pose at the shutter (robot.placed) it is
  // moved to where it was taken: map = R(yaw) · p + (x, y), yaw = heading + pi/2 (the convention the API's `yaw` carries).
  // In this scene's y-up layout a point is (X, Y, Z) = (x, z, -y), so the turn is applied to (X, -Z).
  const c = Math.cos(robot.yaw), sn = Math.sin(robot.yaw), tx = +robot.x, ty = +robot.y;
  for (let i = 0; i < xyz.length; i += 3) {
    const px = xyz[i], py = -xyz[i + 2];
    xyz[i] = px * c - py * sn + tx;
    xyz[i + 2] = -(px * sn + py * c + ty);
  }
}

function mount(cloud, { refit = false } = {}) {
  if (points) { scene.remove(points); points.geometry.dispose(); }
  if (cloud.robot && cloud.robot.placed && Number.isFinite(+cloud.robot.yaw) && cloud.kind === 'capture') placeInMap(cloud.xyz, cloud.robot);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(cloud.xyz, 3));
  geometry.setAttribute('rgb', new THREE.BufferAttribute(cloud.rgb, 3));
  geometry.computeBoundingBox(); geometry.computeBoundingSphere();
  points = new THREE.Points(geometry, material);
  points.frustumCulled = false;
  points.visible = showCloud;
  scene.add(points);
  count = cloud.n; source = cloud.source;
  if (Number.isFinite(+cloud.size)) shared.uSize.value = +cloud.size;
  guides(cloud);
  state.dataset.ready = 'true';
  caption();
  if (refit || !fitted) { fitted = true; fit(false); } else wake();
  loadSplat();
}

function visible() {
  return !document.hidden && !host.classList.contains('voxel-active') && !document.body.classList.contains('camera-current');
}
function render() {
  raf = 0;
  if (!visible()) return;
  const moving = controls.update();
  renderer.render(scene, camera);
  frames++;
  for (const fn of frameHooks) fn();
  if (moving || robotSplat) wake();
}
function wake() { if (visible() && !raf) raf = requestAnimationFrame(render); }

function resize() {
  const w = Math.max(1, host.clientWidth), h = Math.max(1, host.clientHeight);
  const quality = +document.querySelector('#render-quality')?.value || 1;
  renderer.setPixelRatio(Math.min(devicePixelRatio, quality));
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  shared.uScale.value = h / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2));
  const viewer = robotSplat?.userData.splat?.viewer?.viewer;
  if (viewer) {
    viewer.devicePixelRatio = renderer.getPixelRatio();
    if (viewer.splatMesh) viewer.splatMesh.devicePixelRatio = renderer.getPixelRatio();
  }
  wake();
}
new ResizeObserver(resize).observe(host);
resize();

function fit(top = false) {
  topView = top;
  if (!points) return;
  const b = points.geometry.boundingBox, center = b.getCenter(new THREE.Vector3()), size = b.getSize(new THREE.Vector3());
  const floor = Math.max(size.x, size.z, 0.8) / 2;
  const radius = top ? floor : Math.hypot(floor, size.y * 0.7);
  const distance = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) / Math.min(Math.max(camera.aspect, 0.5), 1.35) * (top ? 1.02 : 1.08);
  const along = size.x >= size.z ? new THREE.Vector3(-0.9, 0.42, 0.28) : new THREE.Vector3(0.28, 0.42, 0.9);
  controls.target.copy(center);
  camera.up.set(0, 1, 0);
  camera.position.copy(center).add((top ? new THREE.Vector3(0.001, 1, 0) : along).normalize().multiplyScalar(distance));
  camera.lookAt(center);
  controls.sync();
  document.querySelector('#top').setAttribute('aria-pressed', String(top));
  document.querySelector('#perspective').setAttribute('aria-pressed', String(!top));
  label.textContent = top ? 'Top / hallway metres' : 'Hallway / drag to orbit';
  wake();
}

function applySettings() {
  controls.enableDamping = !reduced.matches && document.querySelector('#camera-smoothing')?.checked !== false;
  controls.zoomSpeed = +document.querySelector('#zoom-speed')?.value || 1;
  if (grid) grid.visible = document.querySelector('#show-grid')?.checked !== false;
  if (box) box.visible = document.querySelector('#show-bounds')?.checked !== false;
  resize();
}
window.addEventListener('room:settings', applySettings);
for (const id of ['show-grid', 'show-bounds']) document.getElementById(id)?.addEventListener('change', applySettings);
for (const id of ['perspective', 'reset', 'top']) {
  document.getElementById(id)?.addEventListener('click', () => fit(id === 'top'));
}
document.addEventListener('visibilitychange', () => { if (document.hidden) { cancelAnimationFrame(raf); raf = 0; } else wake(); });
new MutationObserver(() => { if (visible()) wake(); }).observe(host, { attributes: true, attributeFilter: ['class'] });
new MutationObserver(() => { if (visible()) wake(); }).observe(document.body, { attributes: true, attributeFilter: ['class'] });
applySettings();

function readPly(bytes) {
  const head = new TextDecoder('latin1').decode(bytes.subarray(0, Math.min(bytes.length, 4096)));
  const end = head.indexOf('end_header\n');
  if (!head.startsWith('ply\n') || end < 0) throw Error('not a PLY');
  if (!head.includes('format binary_little_endian 1.0')) throw Error('PLY must be binary little-endian');
  const named = /^element vertex (\d+)$/m.exec(head.slice(0, end));
  if (!named) throw Error('PLY header names no vertex count');
  const n = +named[1], start = end + 'end_header\n'.length;
  if (bytes.length < start + n * POINT_BYTES) throw Error('PLY is cut short');
  const view = new DataView(bytes.buffer, bytes.byteOffset + start, n * POINT_BYTES);
  const take = Math.min(n, MAX_POINTS), step = n / take;
  const xyz = new Float32Array(take * 3), rgb = new Float32Array(take * 3);
  for (let k = 0; k < take; k++) {
    const i = Math.min(n - 1, Math.floor(k * step)), o = i * POINT_BYTES, j = k * 3;
    toWorld(view.getFloat32(o, true), view.getFloat32(o + 4, true), view.getFloat32(o + 8, true), xyz, j);
    rgb[j] = Math.min(1, bytes[start + o + 12] / 255 * 1.12 + 0.02);
    rgb[j + 1] = Math.min(1, bytes[start + o + 13] / 255 * 1.12 + 0.02);
    rgb[j + 2] = Math.min(1, bytes[start + o + 14] / 255 * 1.12 + 0.02);
  }
  return { xyz, rgb, n: take, total: n };
}

function robotFromMeta(doc) {
  const r = doc?.robot;
  if (r && Number.isFinite(+r.x)) return { x: r.x, y: r.y, heading_rad: r.heading_rad };
  const p = doc?.pose;
  if (p && Number.isFinite(+p.x)) return { x: p.x, y: p.z, heading_rad: Number.isFinite(+p.yaw) ? +p.yaw - Math.PI / 2 : 0 };
  return null;
}

function nodeId(c) {
  return c?.id || c?.capture_id || c?.sha || '';
}
// the COMMIT behind a node, when there is one. A capture .ply that was never committed has none,
// and then there is no tree to diff — the panel says so rather than inventing one.
function shaOf(c) {
  return c?.commit_sha || (COMMIT_RE.test(c?.sha || '') ? c.sha : '');
}
const nodeById = (id) => commits.find((c) => nodeId(c) === id) || null;
const shortId = (c) => c?.capture_id || (shaOf(c) || nodeId(c) || '').slice(0, 7);

function ago(iso) {
  const sec = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!Number.isFinite(sec)) return '';
  const a = Math.abs(sec);
  const w = a < 90 ? [Math.round(a), 's'] : a < 5400 ? [Math.round(a / 60), ' min'] : a < 129600 ? [Math.round(a / 3600), ' h'] : [Math.round(a / 86400), ' d'];
  return sec < 0 ? `in ${w[0]}${w[1]}` : `${w[0]}${w[1]} ago`;
}
function stamp(iso) {
  const d = new Date(iso);
  return Number.isFinite(d.getTime()) ? d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : null;
}
const plural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;

// ── the railroad lane walk ────────────────────────────────────────────────────────────
// Ported verbatim from web/landing/graph.js layout() (the dashboard graph's), so there is ONE
// implementation of this in the codebase. `row` is the index into commits, newest first; the
// server sends --date-order, which is the one thing this relies on: a parent never comes before
// a child. lanes[i] holds the id lane i is waiting for. A commit takes the lane waiting for it
// (its first child's) or the first free one; its first parent continues its lane and any further
// parent opens or joins another.
//
// Only the PROJECTION is transposed: row is drawn along x (time), lane along y.
function layout(nodes) {
  const lanes = [], place = new Map(), edges = [];
  nodes.forEach((n, row) => {
    const id = nodeId(n);
    let lane = lanes.indexOf(id);
    if (lane < 0) { lane = lanes.indexOf(null); if (lane < 0) lane = lanes.length; }
    lanes.forEach((waiting, i) => { if (waiting === id && i !== lane) lanes[i] = null; });
    place.set(id, { lane, row });
    lanes[lane] = null;
    (n.parents || []).forEach((parent, k) => {
      let via = lane;
      if (k > 0) {
        via = lanes.indexOf(parent);
        if (via < 0) { via = lanes.indexOf(null); if (via < 0) via = lanes.length; }
      }
      lanes[via] = parent;
      edges.push({ child: id, parent, via });
    });
  });
  return { place, edges, width: Math.max(1, ...[...place.values()].map((pl) => pl.lane + 1)) };
}
const DASH = ['', '7 5', '2 5', '11 4 2 4'];   // lanes differ by stroke, not colour: this page has one accent
const LANE_W = () => (log.clientWidth < 300 ? 18 : 22);   // how far apart two branches sit, across
const NODE_Y = 15;                                        // the dot's height inside a row
const LANE_K = 26;                                        // how long a lane change takes, along time

// The projection: row is drawn down y (time), lane across x. Measured from the rows AFTER they
// exist, so a subject that wraps to two lines never misplaces its dot.
function drawRails() {
  if (!commits.length || nodeList.children.length !== commits.length) return;
  const { place, edges, width } = layout(commits);
  const LW = LANE_W(), lis = [...nodeList.children];      // lis is NEWEST first, like commits
  const railW = width * LW + 12;
  track.style.setProperty('--rail', `${railW}px`);   // .git-track declares the fallback: set it HERE or it wins
  const X = (lane) => 8 + lane * LW + LW / 2;
  const Y = (row) => { const li = lis[row]; return li ? li.offsetTop + NODE_Y : 0; };
  const H = Math.max(1, nodeList.offsetHeight);
  rails.setAttribute('width', railW); rails.setAttribute('height', H);
  rails.setAttribute('viewBox', `0 0 ${railW} ${H}`);
  rails.replaceChildren();
  for (const e of edges) {
    const c = place.get(e.child), par = place.get(e.parent);
    if (!c) continue;
    if (!par) {                                 // the parent is older than -80, or is not a node here
      rails.append(svgEl('path', { class: 'git-edge', 'stroke-dasharray': DASH[c.lane % 4] || null, d: `M${X(c.lane)} ${Y(c.row)} V${H}` }));
      continue;
    }
    let d = `M${X(c.lane)} ${Y(c.row)}`, cx = X(c.lane), cy = Y(c.row);
    if (e.via !== c.lane) { d += ` C${cx} ${cy + LANE_K * 0.6} ${X(e.via)} ${cy + LANE_K * 0.4} ${X(e.via)} ${cy + LANE_K}`; cx = X(e.via); cy += LANE_K; }
    if (e.via !== par.lane) d += ` V${Y(par.row) - LANE_K} C${cx} ${Y(par.row) - LANE_K * 0.4} ${X(par.lane)} ${Y(par.row) - LANE_K * 0.6} ${X(par.lane)} ${Y(par.row)}`;
    else d += ` V${Y(par.row)}`;
    rails.append(svgEl('path', { class: 'git-edge', 'stroke-dasharray': DASH[e.via % 4] || null, d }));
  }
  commits.forEach((c, row) => {
    const at = place.get(nodeId(c));
    if (!at) return;
    const cx = X(at.lane), cy = Y(row);
    const g = svgEl('g', { class: 'git-dot-g', 'data-sha': nodeId(c), 'data-cloud': String(!!c.cloud), 'data-head': String(!!c.head) });
    g.append(svgEl('circle', { class: 'git-dot', cx, cy, r: c.head ? 7 : 5.5 }));
    if (c.head) g.append(svgEl('circle', { class: 'git-headdot', cx, cy, r: 2.6 }));
    rails.append(g);
  });
  paintSelection();
}

function paintSelection() {
  for (const b of nodeList.querySelectorAll('button[data-sha]')) {
    const id = b.dataset.sha;
    b.toggleAttribute('data-compare', id === compareId);
    if (id === selectedSha) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
  }
  for (const g of rails.querySelectorAll('.git-dot-g')) {
    g.toggleAttribute('data-current', g.dataset.sha === selectedSha);
    g.toggleAttribute('data-compare', g.dataset.sha === compareId);
  }
}

// ── the bar, and the columns ──────────────────────────────────────────────────────────
function paintBar() {
  logBar.replaceChildren();
  const name = el('span', 'git-log-name', instanceName || 'room.git');
  const branch = el('span', 'git-branch', hist?.detached ? `detached @ ${(hist.head_sha || '').slice(0, 7)}` : (hist?.branch || ''));
  const counts = el('span', 'git-log-hint', `${plural(commits.length, 'node')} · click one · shift-click a second to diff · [ ] or ↑ ↓`);
  // NOTHING ON THE RAIL WRITES. Every button up here only opens a preview; the single control
  // that changes the room or a ref is the armed one inside the panel, which is disabled for the
  // first 600 ms and ignores the second click of a double-click. `git add` used to commit on one
  // unarmed click, which was the one hole in that rule.
  const add = document.createElement('button');
  add.type = 'button';
  add.className = 'git-add';
  add.textContent = adding ? 'capturing…' : 'git add · current…';
  add.disabled = adding || !instanceName;
  add.title = 'Preview what capturing the room as it is now would commit. Running it is a second, armed click.';
  add.addEventListener('click', () => openCommandWith('add'));
  const cmd = document.createElement('button');
  cmd.type = 'button';
  cmd.className = 'git-cmdbtn';
  cmd.textContent = 'branch / checkout';
  cmd.title = 'Type a command: branch <name>, checkout <ref>. There is no merge here.';
  cmd.addEventListener('click', () => { if (/^\s*add\b/i.test(commandText || '')) commandText = ''; openCommandWith(null); });   // this button is not the add button
  const acts = el('div', 'git-acts');
  acts.append(add, cmd);
  logBar.append(name, branch || '', counts, acts);
  if (hist?.dirty) logBar.append(el('span', 'git-dirty', 'uncommitted work in the room'));
}

// the rail's buttons all end here: the panel, with a preview and a control that is not yet armed
function openCommandWith(text) {
  openCommand = true;
  if (text) commandText = text;
  if (!selectedSha && commits[0]) selectedSha = nodeId(commits[0]);
  renderPanel();
  queueMicrotask(() => panel.querySelector('.git-cmd-in')?.focus());
}

function row(c) {
  const id = nodeId(c), sha = shaOf(c);
  const li = document.createElement('li');
  li.dataset.sha = id;
  const b = document.createElement('button');
  b.type = 'button';
  b.dataset.sha = id;
  if (!c.cloud) b.disabled = true;
  if (c.head) b.dataset.head = 'true';
  if (c.cloud) b.dataset.cloud = 'true';
  const refs = el('span', 'git-refs');
  for (const r of (c.refs || [])) {
    if (r.kind === 'remote') continue;
    const chip = el('span', 'git-ref', (r.head && r.kind === 'branch' ? 'HEAD → ' : '') + r.name);
    chip.dataset.kind = r.kind;
    if (r.head) chip.dataset.head = 'true';
    refs.append(chip);
  }
  const idEl = el('span', 'git-id', shortId(c));
  // `head` is the node the page FOLLOWS: the newest complete capture in the captures graph (the robot can
  // be ahead of the repo), git HEAD in the commits graph. Git HEAD always shows as its own "HEAD -> branch"
  // ref chip, so the two are never confused.
  if (c.head) idEl.append(el('span', 'git-tip', hist?.kind === 'captures' ? 'NEWEST' : 'HEAD'));
  const msg = el('span', 'git-msg', (c.subject || '') + (c.kind === 'capture' ? (c.robot && c.robot.placed ? ' · placed by SLAM' : ' · pose not recorded') : ''));
  const when = el('span', 'git-when', [c.at ? ago(c.at) : null, Number.isFinite(+c.points) ? `${Number(c.points).toLocaleString()} pts` : null].filter(Boolean).join(' · '));
  b.append(refs, idEl, msg, when);
  b.title = `${shortId(c)} ${c.subject || ''}${c.cloud ? '' : ' — no cloud in this node'}. Click to load it, shift-click a second to diff.`;
  b.addEventListener('click', (ev) => {
    if (ev.detail > 1) return;                                     // a double-click selects once
    select(c, ev.shiftKey);
  });
  li.append(b);
  return li;
}

function paintLog(sha) {
  selectedSha = sha || selectedSha;
  if (!commits.length && !instanceName) { log.hidden = true; return; }
  log.hidden = false;
  paintBar();
  nodeList.replaceChildren(...commits.map(row));     // DOM order IS commits order: newest first, top to bottom
  drawRails();
  requestAnimationFrame(drawRails);                  // again once the rows have wrapped to their real height
}

// ── travelling a long history ─────────────────────────────────────────────────────────
// The History tab is one ordinary vertical scroller (#room-history), so the wheel, a trackpad and
// a touch swipe are all the browser's own and nothing here intercepts them. Keeping a node in view
// means scrolling that panel, not the page.
function scroller() {
  return log.closest('#room-history') || log.parentElement || document.scrollingElement;
}
function keepInView(id, smooth) {
  const li = [...nodeList.children].find((x) => x.dataset.sha === id);
  const box = scroller();
  if (!li || !box || box.scrollHeight <= box.clientHeight) return;
  const pad = 24;
  const top = li.offsetTop - box.offsetTop - pad, bottom = top + li.offsetHeight + pad * 2;
  const to = top < box.scrollTop ? top : bottom > box.scrollTop + box.clientHeight ? bottom - box.clientHeight : null;
  if (to != null) box.scrollTo({ top: Math.max(0, to), behavior: smooth && !reduced.matches ? 'smooth' : 'auto' });
}
const toTip = (smooth) => { const box = scroller(); if (box) box.scrollTo({ top: 0, behavior: smooth && !reduced.matches ? 'smooth' : 'auto' }); };

// The arrow keys belong to the CANVAS (room-camera.js orbits with them), so up/down are bound to
// the rail and only act when the graph has focus. `[` and `]` stay document-wide — muscle memory,
// and they work from anywhere on the page. Newest is at the TOP, so: up = newer, down = older,
// Home = the tip, End = the first scan.
strip.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const order = [...nodeList.children].map((li) => li.dataset.sha);   // newest -> oldest
  if (!order.length) return;
  const i = order.indexOf(selectedSha), last = order.length - 1;
  const next = e.key === 'ArrowDown' ? order[i < 0 ? 0 : Math.min(last, i + 1)]
    : e.key === 'ArrowUp' ? order[i < 0 ? 0 : Math.max(0, i - 1)]
    : e.key === 'Home' ? order[0] : e.key === 'End' ? order[last] : null;
  if (!next) return;
  e.preventDefault();
  stepTo(next);
});

function stepTo(id) {
  const c = nodeById(id);
  if (!c) return;
  select(c, false);
  [...nodeList.children].find((x) => x.dataset.sha === id)?.querySelector('button')?.focus({ preventScroll: true });
}

// Selecting is a PREVIEW: it loads the cloud and opens the panel. It never writes anything —
// the two commands that move a ref need a second, armed click inside the panel.
function select(c, second) {
  const id = nodeId(c);
  if (second && selectedSha && id !== selectedSha) compareId = id;
  else {
    const already = id === selectedSha && !compareId;      // half a million points are not re-fetched to re-select what is already up
    compareId = null;
    selectedSha = id;
    follow = !!c.head;
    if (c.cloud && !already) loadCommit(c).catch((error) => { state.textContent = `cloud failed · ${error.message}`; state.dataset.ready = 'false'; });
  }
  paintSelection();
  keepInView(id, true);
  renderPanel();
}
function clearSelection() { compareId = null; openCommand = false; renderPanel(); }

// ── the panel: what this node is, what changed, and the two commands ──────────────────
// IDENTITY IS NOT DECIDED HERE. perception/associate.py matched this scan's boxes to the objects
// already in the tree, and roomctl/state.py carried class, colour and first_seen forward from the
// first sight of each one. So a stable object_id across two commits MEANS the same physical
// object, a new id means the association pass minted one, and a first_seen older than the
// left-hand commit is associate.py's `returned` row — it left and came back. This only says so.
const HEXCOLOR = /^#[0-9a-fA-F]{3,8}$/;
function swatch(o) {
  const n = el('span', 'git-sw');
  n.setAttribute('aria-hidden', 'true');
  if (HEXCOLOR.test(o.color || '')) n.style.background = o.color; else n.dataset.unknown = 'true';
  return n;
}
const poseText = (p) => (p ? `(${['x', 'y', 'z'].map((k) => Number(p[k]).toFixed(2)).join(', ')})${Number.isFinite(+p.yaw) ? ` · ${Math.round(p.yaw)}°` : ''}` : '—');

function verdict(o, aAt) {
  const first = o.first_seen ? Date.parse(o.first_seen) : NaN, at = aAt ? Date.parse(aAt) : NaN;
  const since = stamp(o.first_seen);
  if (!Number.isFinite(first)) {
    return { badge: o.op.toUpperCase(), known: null, line: 'This record carries no first_seen, so the room cannot say how long it has known it. Not guessed.' };
  }
  const knew = Number.isFinite(at) ? first < at : null;
  if (o.op === 'added') {
    return knew
      ? { badge: 'RETURNED', known: true, line: `We have seen this before: in the room since ${since} (${ago(o.first_seen)}). It left and came back, and the association pass gave it its old id back.` }
      : { badge: 'NEW', known: false, line: `New to the room. First seen ${since} — no earlier commit knows this id, so this is a new object, not a move.` };
  }
  if (o.op === 'removed') return { badge: 'GONE', known: true, line: `It had been in the room since ${since} (${ago(o.first_seen)}); at the right-hand commit it is not there.` };
  if (o.op === 'changed') return { badge: 'RECORD', known: true, line: `Same object, in the room since ${since}. Its record changed; the object did not move.` };
  return { badge: 'MOVED', known: true, line: `The same physical object — same id at both commits, so this is a move. In the room since ${since} (${ago(o.first_seen)}).` };
}

function objectRow(o, aAt) {
  const v = verdict(o, aAt), zoned = o.from_zone && o.from_zone !== o.zone;
  const far = Number.isFinite(+o.delta_m) ? +o.delta_m : null;
  const how = o.op === 'moved'
    ? [far === 0 ? null : far != null ? `moved ${far.toFixed(2)} m` : 'moved',
       o.delta_yaw_deg ? `turned ${Math.abs(o.delta_yaw_deg).toFixed(0)}°${far === 0 ? ' in place' : ''}` : null,
       zoned ? `${o.from_zone} → ${o.zone}` : null].filter(Boolean).join(' · ')
    : o.op === 'added' ? `put in the ${o.zone || 'room'}`
    : o.op === 'removed' ? `taken off the ${o.zone || 'room'}`
    : 'record changed, pose identical';
  const li = document.createElement('li');
  li.className = 'git-obj';
  li.dataset.op = o.op;
  if (v.known != null) li.dataset.known = String(v.known);
  const head = el('span', 'git-obj-head');
  const badge = el('span', 'git-badge', v.badge);
  badge.dataset.badge = v.badge;
  head.append(swatch(o), el('span', 'git-obj-class', o.class || o.object_id), el('span', 'git-obj-id', o.object_id), badge);
  li.append(head, el('span', 'git-obj-how', how));
  if (o.from || o.to) li.append(el('span', 'git-obj-pose', `${poseText(o.from)} → ${poseText(o.to)}`));
  li.append(el('span', 'git-obj-line', v.line));
  return li;
}

// _ops already sorts by op, then by the biggest move: grouping keeps that order, so the zone
// where the most actually happened comes first, and inside it the biggest move is first.
function zoneGroups(ops) {
  const by = new Map();
  for (const o of ops) {
    const z = o.zone || '—';
    if (!by.has(z)) by.set(z, []);
    by.get(z).push(o);
  }
  return [...by.entries()];
}

function cloudLine(a, b, diff) {
  const pa = Number(a?.points), pb = Number(b?.points);
  const line = el('p', 'git-cloud');
  if (!Number.isFinite(pa) || !Number.isFinite(pb)) {
    line.textContent = 'Point counts are not recorded for both of these nodes.';
    return line;
  }
  const d = pb - pa;
  line.textContent = `Clouds: ${pa.toLocaleString()} → ${pb.toLocaleString()} points (${d >= 0 ? '+' : ''}${d.toLocaleString()}). `
    + 'A point count is not a change: which points APPEARED or went away is free-space geometry (perception/difference.py), and it is not computed here.';
  return line;
}

function nothingChanged(a, b, total) {
  const box = el('div', 'git-same');
  box.append(el('p', 'git-same-title', 'Nothing changed.'),
    el('p', 'git-same-line', `All ${plural(total ?? 0, 'object')} are in the same zone at the same pose at both nodes. Not "no data": the two trees under zones/ are identical, byte for byte.`),
    el('p', 'git-mono', `git diff ${shortId(a)} ${shortId(b)} -- zones   →   (no output)`));
  return box;
}

const diffCache = new Map();
async function objectDiff(aSha, bSha) {
  const key = `${aSha}..${bSha}`;
  if (!diffCache.has(key)) {
    diffCache.set(key, (async () => {
      const r = await fetch(`/api/scene/${instanceName}/diff?a=${aSha}&b=${bSha}`, { cache: 'no-store' });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw Error(body.detail || `diff HTTP ${r.status}`);
      return body;
    })().catch((e) => { diffCache.delete(key); throw e; }));
  }
  return diffCache.get(key);
}

function diffBlock(diff, a, b) {
  const out = [];
  const ops = diff.ops || [], sm = diff.summary || {}, aAt = diff.at?.a || a?.at;
  const counts = [[sm.moved, 'moved'], [sm.added, 'added'], [sm.removed, 'removed'], [sm.changed, 'record only']]
    .filter(([n]) => n).map(([n, w]) => { const x = el('span', 'git-count'); x.append(el('b', null, String(n)), document.createTextNode(` ${w}`)); return x; });
  if (!ops.length) { out.push(nothingChanged(a, b, diff.objects?.b)); out.push(cloudLine(a, b, diff)); return out; }
  const sum = el('p', 'git-sum');
  sum.append(...counts);
  out.push(sum);
  const travel = ops.reduce((t, o) => t + (Number.isFinite(+o.delta_m) ? +o.delta_m : 0), 0);
  const touched = (sm.moved || 0) + (sm.added || 0) + (sm.changed || 0);
  const untouched = Number.isFinite(+diff.objects?.b) ? Math.max(0, diff.objects.b - touched) : null;
  const fresh = ops.filter((o) => o.op === 'added' && verdict(o, aAt).known === false).length;
  const back = ops.filter((o) => o.op === 'added' && verdict(o, aAt).known === true).length;
  out.push(el('p', 'git-sum-2', [
    travel > 0 ? `${travel.toFixed(2)} m of travel in total` : null,
    untouched != null ? `${plural(untouched, 'object')} untouched of ${diff.objects.b}` : null,
    fresh ? `${plural(fresh, 'object')} the room had never seen` : null,
    back ? `${plural(back, 'object')} back after being away` : null,
  ].filter(Boolean).join(' · ')));
  for (const [zone, list] of zoneGroups(ops)) {
    const h = el('p', 'git-zone');
    h.append(el('span', 'git-mono', `zones/${zone}`), el('span', 'git-zone-n', plural(list.length, 'change')));
    out.push(h);
    const ul = document.createElement('ul');
    ul.className = 'git-objs';
    ul.append(...list.map((o) => objectRow(o, aAt)));
    out.push(ul);
  }
  out.push(cloudLine(a, b, diff));
  return out;
}

// ── the two commands. branch / checkout / add — and there is no merge, here or anywhere ──
// roomctl puts merge, cherry-pick and stash in WRITE_VERBS and exits 2; this page agrees with
// the CLI. Typing one is answered, not sent.
const ARM_MS = 600;
const CMD = /^(?:room\s+|git\s+)?([a-z-]+)(?:\s+(\S+))?\s*$/i;
const NO_MERGE = new Set(['merge', 'cherry-pick', 'cherrypick', 'stash', 'rebase']);
let commandText = '';
let lastCommand = null;      // what the last run actually did: the panel re-renders under it, and the answer must survive that

function commandBlock(node) {
  const box = el('div', 'git-cmd');
  const out = el('div', 'git-cmd-out');
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'git-cmd-in';
  input.spellcheck = false;
  input.autocomplete = 'off';
  input.setAttribute('aria-label', 'A command for this graph');
  input.value = commandText || `branch from-${shortId(node)}`;
  input.addEventListener('input', () => { commandText = input.value; });
  const form = document.createElement('form');
  form.className = 'git-cmd-line';
  const go = document.createElement('button');
  go.type = 'submit';
  go.className = 'git-cmd-go';
  go.textContent = 'preview';
  form.append(el('span', 'git-mono git-prompt', 'room>'), input, go);
  form.addEventListener('submit', (e) => { e.preventDefault(); preview(); });
  const chips = el('div', 'git-cmd-chips');
  const elsewhere = (hist?.branches || []).find((b) => b !== hist?.branch);   // no chip for checking out the branch you are on
  for (const text of [`branch from-${shortId(node)}`, elsewhere ? `checkout ${elsewhere}` : null, 'add'].filter(Boolean)) {
    const c = document.createElement('button');
    c.type = 'button';
    c.className = 'git-chip';
    c.textContent = text;
    c.addEventListener('click', () => { input.value = text; commandText = text; preview(); });
    chips.append(c);
  }

  function say(...kids) { out.replaceChildren(...kids.filter(Boolean)); }
  function armed(label, run, { loadHead = false } = {}) {
    // PREVIEW BEFORE EXECUTE. The run control arms itself only after the preview has been on
    // screen a moment, and the second click of a double-click lands on a disabled button.
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'git-run';
    b.textContent = label;
    b.disabled = true;
    b.dataset.arming = 'true';
    setTimeout(() => { b.disabled = false; delete b.dataset.arming; }, ARM_MS);
    b.addEventListener('click', async (e) => {
      if (e.detail > 1 || b.disabled) return;
      b.disabled = true;
      const note = el('p', 'git-cmd-note', 'asking the server…');
      b.after(note);
      try {
        const answer = await run();
        lastCommand = { ok: true, text: answer.detail || 'done' };
        note.textContent = lastCommand.text;
        note.className = 'git-cmd-ok';
        diffCache.clear();
        await refreshHistory({ loadHead });          // a re-render replaces this note; lastCommand is what survives it
      } catch (error) {
        lastCommand = { ok: false, text: error.message };
        note.textContent = error.message;
        note.className = 'git-cmd-err';
        b.disabled = false;
      }
    });
    return b;
  }
  async function post(path, body) {
    const r = await fetch(`/api/scene/${instanceName}/${path}`, { method: 'POST', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const doc = await r.json().catch(() => ({}));
    if (!r.ok) throw Error(doc.detail || `${path} HTTP ${r.status}`);
    return doc;
  }

  function preview() {
    const m = CMD.exec((input.value || '').trim());
    const verb = m ? m[1].toLowerCase() : '', arg = m ? m[2] : undefined;
    if (NO_MERGE.has(verb)) {
      say(el('p', 'git-cmd-err', `There is no ${verb} here, and there is not going to be one.`),
        el('p', 'git-cmd-note', 'A room is physical: two answers to "where does this belong" are a decision, so they go through a pull request. roomctl agrees — merge, cherry-pick and stash sit in WRITE_VERBS and exit 2. This graph branches and checks out; it never merges.'));
      return;
    }
    if (verb === 'branch') {
      const name = arg || `from-${shortId(node)}`;
      const taken = (hist?.branches || []).includes(name);
      const at = shaOf(node);
      // a capture that was never committed is not somewhere a ref can point: say that, never
      // quietly branch at HEAD instead and call it this node
      say(el('p', 'git-cmd-head', !at ? `${shortId(node)} is not a commit` : taken ? `${name} already exists` : `A new branch, ${name}, at ${shortId(node)}`),
        el('p', 'git-cmd-note', !at
          ? 'It is a capture point cloud in .scene/ that was never committed, so there is no node here for a branch to name. Pick one that carries a commit.'
          : taken
            ? 'It is already a branch in this room. Pick another name — nothing was changed.'
            : `${node.subject || ''} — HEAD stays on ${hist?.branch || 'this branch'} and no file changes: a branch is a second name for this node, somewhere to take the room's story on from.`),
        at ? el('p', 'git-mono', `git branch ${name} ${shortId(node)}`) : null,
        at && !taken ? armed(`Make the branch ${name}`, () => post('branch', { name, at })) : null);
      return;
    }
    if (verb === 'checkout') {
      const ref = arg || '';
      if (!ref) { say(el('p', 'git-cmd-err', 'checkout needs a branch name or a commit.')); return; }
      const toBranch = (hist?.branches || []).includes(ref);
      say(el('p', 'git-cmd-head', `HEAD moves to ${ref}`),
        el('p', 'git-cmd-note', 'The room’s working tree becomes that node’s: cloud/current.ply and every zones/*.yaml. Nothing is merged, and no earlier node is lost — they are all still in this graph.'),
        toBranch ? null : el('p', 'git-cmd-err', `${ref} is not a branch here, so HEAD would be detached: the next “git add · current” would commit onto no branch. Make a branch at this node first.`),
        hist?.dirty ? el('p', 'git-cmd-err', 'This room has uncommitted work right now, so the server will refuse: a checkout would throw it away.') : null,
        el('p', 'git-mono', `git checkout ${ref}`),
        armed(`Move HEAD to ${ref}`, () => post('checkout', { ref }), { loadHead: true }));
      return;
    }
    if (verb === 'add') {
      say(el('p', 'git-cmd-head', `Capture the room as it is now, on ${hist?.branch || 'this branch'}`),
        el('p', 'git-cmd-note', 'The robot is asked for its current fused map; if it has changed, it becomes the next node — a new point cloud AND the objects found in it.'),
        el('p', 'git-mono', 'room add · POST /api/scene/' + instanceName + '/add'),
        armed('Capture and commit', async () => { await addCurrent(); return { detail: 'capture finished' }; }));
      return;
    }
    say(el('p', 'git-cmd-err', `Not a command here: ${input.value.trim() || '(nothing)'}`),
      el('p', 'git-cmd-note', 'This graph takes three: branch <name>, checkout <ref>, add. Never merge.'));
  }

  box.append(el('p', 'git-p-kicker', 'COMMAND'), chips, form, out);
  if (lastCommand) box.append(el('p', lastCommand.ok ? 'git-cmd-ok' : 'git-cmd-err', `✓ ${lastCommand.text}`));
  queueMicrotask(preview);
  return box;
}

let panelToken = 0;
function renderPanel() {
  const token = ++panelToken;
  const node = nodeById(selectedSha), other = compareId ? nodeById(compareId) : null;
  if (!node) { panel.hidden = true; return; }
  panel.hidden = false;
  const head = el('div', 'git-p-head');
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'git-p-close';
  close.textContent = '✕';
  close.setAttribute('aria-label', 'Close this panel');
  close.addEventListener('click', () => { selectedSha = selectedSha; compareId = null; openCommand = false; panel.hidden = true; paintSelection(); });
  head.append(el('span', 'git-p-kicker', other ? 'DIFF' : 'THIS NODE'), close);
  const title = el('p', 'git-p-title', other ? `${shortId(node)} → ${shortId(other)}` : `${shortId(node)} · ${node.subject || ''}`);
  const body = el('div', 'git-p-body');
  body.append(el('p', 'git-dim', 'reading the two trees…'));
  panel.replaceChildren(head, title, body);
  panel.scrollTop = 0;

  const aSha = other ? shaOf(node) : shaOf(nodeById((node.parents || [])[0]));
  const bSha = other ? shaOf(other) : shaOf(node);
  const aNode = other ? node : nodeById((node.parents || [])[0]);
  const bNode = other ? other : node;

  const facts = el('div', 'git-facts');
  const factLine = (k, v) => { const d = el('div', 'git-fact'); d.append(el('span', 'git-fact-k', k), el('span', 'git-fact-v', v)); return d; };
  if (!other) {
    facts.append(factLine('when', node.at ? `${stamp(node.at)} · ${ago(node.at)}` : 'not recorded'));
    facts.append(factLine('points', Number.isFinite(+node.points) ? Number(node.points).toLocaleString() : 'not recorded'));
    facts.append(factLine('commit', shaOf(node) ? shaOf(node).slice(0, 10) : 'this capture was never committed'));
    if (node.robot && Number.isFinite(+node.robot.x)) {
      const yaw = Number.isFinite(+node.robot.yaw) ? `${Math.round((node.robot.yaw * 180) / Math.PI)}°` : '—';
      facts.append(factLine('robot', `x ${(+node.robot.x).toFixed(2)} · y ${(+node.robot.y).toFixed(2)} · facing ${yaw}`));
    }
    facts.append(factLine('on', (node.refs || []).filter((r) => r.kind !== 'remote').map((r) => r.name).join(', ') || '—'));
  } else {
    facts.append(factLine('left', `${shortId(node)} · ${node.at ? ago(node.at) : ''}`));
    facts.append(factLine('right', `${shortId(other)} · ${other.at ? ago(other.at) : ''}`));
    facts.append(factLine('note', 'Shift-click a node to move the right-hand side; click one on its own to go back.'));
    facts.append(factLine('frame', 'world_z_up · positions in metres · yaw in degrees'));
  }

  (async () => {
    const kids = [facts];
    if (!aSha || !bSha) {
      // Three different nothings, and they must not be confused: a capture .ply that was never
      // committed has no tree of objects at all; a root commit has no earlier node; and the node
      // on the other side of a comparison can be either.
      const orphan = (n) => `${shortId(n)} is a capture file that was never committed, so it has no zones/ tree — only a point cloud. Pick a node that carries a commit.`;
      kids.push(el('p', 'git-dim',
        !bSha ? orphan(bNode)
          : other ? orphan(aNode)
          : !aNode ? 'This is the first scan: there is no earlier node to compare it with. Every object in it was recorded here for the first time.'
          : orphan(aNode)));
    } else {
      try {
        const diff = await objectDiff(aSha, bSha);
        if (token !== panelToken) return;
        if (!other) kids.push(el('p', 'git-p-sub', `what this node changed — against ${shortId(aNode)}`));
        kids.push(...diffBlock(diff, aNode, bNode));
      } catch (error) {
        kids.push(el('p', 'git-cmd-err', `diff: ${error.message}`));
      }
    }
    if (token !== panelToken) return;
    if (openCommand || !other) kids.push(commandBlock(node));
    body.replaceChildren(...kids);
  })();
}

async function refreshHistory({ loadHead = false } = {}) {
  if (!instanceName) return;
  const res = await fetch(`/api/scene/${instanceName}/history`, { cache: 'no-store' });
  if (!res.ok) throw Error(`history HTTP ${res.status}`);
  const data = await res.json();
  const prevId = nodeId(commits.find((c) => c.head));
  const same = !loadHead && data.head === prevId && (data.commits || []).length === commits.length
    && data.branch === hist?.branch && data.head_sha === hist?.head_sha && data.dirty === hist?.dirty
    && (data.branches || []).join() === (hist?.branches || []).join();
  if (same) return;
  hist = data;
  commits = Array.isArray(data.commits) ? data.commits : [];
  if (compareId && !nodeById(compareId)) compareId = null;
  const head = commits.find((c) => c.head && c.cloud) || commits.find((c) => c.cloud);
  const box = scroller(), atTip = !box || box.scrollTop <= 4;   // sitting on the newest: follow the new node up
  paintLog(follow && head ? nodeId(head) : selectedSha);
  if (atTip) requestAnimationFrame(() => toTip(false));
  if (!panel.hidden) renderPanel();
  if (head && (loadHead || (follow && nodeId(head) !== prevId))) await loadCommit(head);
}

async function addCurrent() {
  if (adding || !instanceName) return;
  adding = true; follow = true;
  paintLog(selectedSha);
  state.textContent = 'capturing current';
  state.dataset.ready = 'false';
  try {
    const r = await fetch(`/api/scene/${instanceName}/add`, { method: 'POST', cache: 'no-store' });
    let doc = {};
    try { doc = await r.json(); } catch { /* not json */ }
    if (!r.ok) throw Error(doc.detail || `add HTTP ${r.status}`);
    diffCache.clear();
    await refreshHistory({ loadHead: true });
    if (!doc.committed) {
      state.textContent = `${source} · already current`;
      state.dataset.ready = 'true';
    }
  } catch (error) {
    console.warn('[room] git add failed:', error);
    state.textContent = `add failed · ${error.message}`;
    state.dataset.ready = 'false';
    throw error;
  } finally {
    adding = false;
    paintLog(selectedSha);
  }
}

function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(pollHistory, POLL_MS);
}
async function pollHistory() {
  if (!document.hidden && !adding && instanceName) {
    try { await refreshHistory(); } catch (error) { console.warn('[room] history poll:', error); }
  }
  schedulePoll();
}

function address(commit) {
  const url = new URL(location.href);
  if (instanceName) url.searchParams.set('instance', instanceName);
  if (commit?.capture_id) url.searchParams.set('capture', commit.capture_id);
  else url.searchParams.delete('capture');
  history.replaceState(null, '', url);
}

function plyUrl(commit) {
  if (commit.file) return `/api/scene/${instanceName}/${commit.file}`;
  if (commit.kind === 'capture' && commit.capture_id) return `/api/scene/${instanceName}/${commit.capture_id}.ply`;
  return `/api/scene/${instanceName}/history/${commit.commit_sha || commit.sha}.ply`;
}

// Loading half a million points must never hold up the scrub: the rail and the panel are painted
// first, and a newer pick simply wins — an older fetch that comes back late is dropped on the floor.
let loadToken = 0;
async function loadCommit(commit, { refit = false } = {}) {
  const mine = ++loadToken;
  address(commit);
  state.textContent = `loading ${shortId(commit)}`;
  state.dataset.ready = 'false';
  const plyRes = await fetch(plyUrl(commit), { cache: 'no-store' });
  if (!plyRes.ok) throw Error(`cloud HTTP ${plyRes.status}`);
  const bytes = new Uint8Array(await plyRes.arrayBuffer());
  if (mine !== loadToken) return;
  const parsed = readPly(bytes);
  let meta = {};
  const sha = shaOf(commit);
  if (sha) {
    try {
      const jsonRes = await fetch(`/api/scene/${instanceName}/history/${sha}.json`, { cache: 'no-store' });
      if (jsonRes.ok) meta = await jsonRes.json();
    } catch { /* sidecar is optional */ }
  }
  if (mine !== loadToken) return;
  const cap = commit.capture_id || meta.capture_id || shortId(commit);
  const capture = commit.kind === 'capture' || String(commit.capture_id || '').startsWith('cap_');
  mount({
    ...parsed,
    kind: capture ? 'capture' : 'map',
    source: `${instanceName} · ${cap}${capture ? (commit.robot && commit.robot.placed ? ' · placed by SLAM' : ' · pose not recorded, robot frame') : ''}`,
    robot: commit.robot || robotFromMeta(meta),
    size: capture ? 0.016 : (meta.voxel_m || meta.cell_m || 0.03),
  }, { refit });
}

function step(delta) {
  let i = commits.findIndex((c) => nodeId(c) === selectedSha);
  if (i < 0) return;
  for (i += delta; commits[i]; i += delta) {
    if (!commits[i].cloud) continue;
    stepTo(nodeId(commits[i]));
    return;
  }
}

async function loadGit() {
  const inst = await fetch('/api/scene/instances', { cache: 'no-store' });
  if (!inst.ok) throw Error(`instances HTTP ${inst.status}`);
  const doc = await inst.json();
  const list = Array.isArray(doc.instances) ? doc.instances : [];
  const want = new URL(location.href).searchParams;
  const named = list.find((i) => i.name === want.get('instance'));
  const richest = [...list].filter((i) => i.captures).sort((a, b) => b.captures - a.captures)[0];
  const chosen = named || richest || list.find((i) => i.current && i.commits) || list.find((i) => i.commits) || list[0];
  if (!chosen?.name) throw Error('no room instance');
  instanceName = chosen.name;
  const res = await fetch(`/api/scene/${instanceName}/history`, { cache: 'no-store' });
  if (!res.ok) throw Error(`history HTTP ${res.status}`);
  const data = await res.json();
  hist = data;
  commits = Array.isArray(data.commits) ? data.commits : [];
  const wantCap = want.get('capture');
  const head = commits.find((c) => c.head && c.cloud) || commits.find((c) => c.cloud);
  // A capture id names a SCAN, and several commits can hold the same one — a room whose branches
  // all argue about cap_0018 has one per branch. The page writes that id into its own address, so
  // "?capture=cap_0018" is usually just a reload, and the reload has to come back to where the
  // room is: HEAD, then anything reachable from it, and only then some branch tip that happens to
  // sort first. A node id (a sha) still names exactly one node and wins outright.
  const same = wantCap ? commits.filter((c) => c.cloud && (nodeId(c) === wantCap || c.capture_id === wantCap)) : [];
  const picked = same.find((c) => nodeId(c) === wantCap) || same.find((c) => c.head)
    || same.find((c) => c.on_head) || same[0] || null;
  const start = picked || head;
  if (!start) throw Error('no capture with a cloud');
  paintLog(nodeId(start));
  const atTip = start === head;                  // asked for HEAD, or not asked at all: keep following it
  follow = atTip;
  requestAnimationFrame(() => { drawRails(); if (atTip) toTip(false); else keepInView(nodeId(start), false); });
  await loadCommit(start, { refit: true });
  schedulePoll();
}

async function load() {
  try {
    await loadGit();
  } catch (error) {
    console.warn('[room] git captures unavailable, using hallway map:', error);
    try {
      const response = await fetch(MAP, { cache: 'no-store' });
      if (!response.ok) throw Error(`map HTTP ${response.status}`);
      mount(mapCloud(await response.json()), { refit: true });
    } catch (mapError) {
      console.warn('[room] hallway map unavailable, using example corridor:', mapError);
      mount(hallwayCloud(), { refit: true });
    }
  }
}

async function loadSplat() {
  if (robotSplat) { placeRobot(); caption(); return; }
  try {
    robotSplat = await loadRobotSplat({ height: ROBOT_HEIGHT, pixelRatio: renderer.getPixelRatio() });
    scene.add(robotSplat);
    placeRobot();
    caption();
    wake();
  } catch (error) {
    console.warn('[room] robot splat unavailable; map pose marker stays up:', error);
  }
}

addEventListener('pagehide', () => {
  robotSplat?.userData.splat?.dispose();
  robotSplat = null;
}, { once: true });

document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement || t.isContentEditable) return;
  if (e.key === '[') { e.preventDefault(); step(1); }        // older, i.e. leftwards
  if (e.key === ']') { e.preventDefault(); step(-1); }       // newer, i.e. rightwards
  if (e.key === 'Escape' && !panel.hidden) { panel.hidden = true; compareId = null; openCommand = false; paintSelection(); }
});

// a column's width is fixed in CSS, but the viewer is resizable and the lane height is not:
// re-measure and redraw rather than trusting a number computed at a different size
new ResizeObserver(() => drawRails()).observe(nodeList);   // the panel is resizable and rows wrap: re-measure, never trust a cached y

load();
window.roomCloud = {
  get state() { return { source, count, frames, splat: !!robotSplat, pose: robotPose, instance: instanceName, sha: selectedSha, compare: compareId, commits: commits.length, branches: hist?.branches || [], branch: hist?.branch || null, lanes: commits.length ? layout(commits).width : 0, follow, adding, camera: camera.position.toArray(), target: controls.target.toArray() }; },
  scene, camera, renderer, canvas, controls, wake,
  get cloudVisible() { return showCloud; },     // so another module can ask instead of reaching into the scene
  onFrame(fn) { if (fn && !frameHooks.includes(fn)) frameHooks.push(fn); },
  offFrame(fn) { const i = frameHooks.indexOf(fn); if (i >= 0) frameHooks.splice(i, 1); },
};
