// watchers.js — THE CROWD. The page's whole idea in one module.
//
// A crowd of camera-headed arms and tentacles SWINGS IN from all four edges of the
// frame, at three depths — each one a gate swinging about its off-screen root, rippling
// left to right — and every one of them looks at GITRL as it drops into the space they
// are watching. Hover ENTER and they look at it; press it and they swing back out.
// (FOLLOW_CURSOR below restores the earlier behaviour: everything watching the visitor.)
//
// RIG. Every watcher is a real kinematic chain: nested Object3D joints, each offset
// along its parent's +y, posed only by local quaternions (pomme's buildChain idea).
// Links taper by uniform scale, so nothing distorts.
// DRAWING. ~250 joints would be ~1500 meshes built pomme's way, so the chains are
// drawn by six InstancedMeshes instead: each kit piece is merged ONCE into a single
// geometry with vertex colours taken from mech.js MAT (the same values as
// snakeArms.js), and every frame instanceMatrix = joint.matrixWorld * taper.
// POSE. The chain follows a space curve from its off-frame root to the head: a cubic
// Hermite plus a slack bulge solved so the arc length equals the chain's length —
// smooth by construction, no kinks, no IK iteration. Joints take their orientation
// from the curve by parallel transport. The head position rides a spring, so every
// move overshoots and settles, and the neck's end tangent bends toward the gaze so
// the ARM points the head at you (pomme's aim-assist), not just the gimbal.

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { MAT, hash, hashS, clamp01, lerp, smooth, bump, Spring, Spring3, yieldBuild } from './mech.js';
import { TITLE, TITLE_CLEAR, ENTER, ENTER_CLEAR, INTRO, introEnter, introConverge, introTada, presentPoint } from './layout.js';
import { makeHead, HEAD_KINDS, batchHeadEyes } from './heads.js';

const TAN_HALF_FOV = 0.364;             // the rig's camera: vertical fov 40
const UP = new THREE.Vector3(0, 1, 0);
const LEN_ARM = 1.34, LEN_VERT = 0.86;  // joint spacing clears the neighbouring clevis housings

// ---- the cast ---------------------------------------------------------------------
// Perches are authored in SCREEN space (ndc x, y) plus a world depth z, so the crowd
// frames the title at any aspect ratio; roots are pushed past the named edge.
// GITRL's own box (|x| < 0.56, -0.46 < y < 0.54 in ndc) stays empty at rest.
// ENTER hangs under the title, so nobody perches at the bottom centre. Density is deliberate: ~2 heads per edge per depth.
//        edge      ndcX   ndcY     z    size  style      kind (optional: cast to type)
const CAST = [
  // midground: the ring around the title
  ['left',   -0.86, -0.26, -0.4, 0.95, 'arm'],
  ['left',   -0.70, -0.58, -1.6, 0.80, 'tentacle'],
  ['top',    -0.42,  0.80, -2.0, 0.85, 'arm'],
  ['top',    -0.06,  0.76, -2.4, 0.80, 'tentacle', 'periscope'],
  ['top',     0.32,  0.80, -0.4, 0.90, 'arm'],
  ['top',     0.72,  0.40, -1.0, 1.30, 'arm', 'pod'],      // the two big ones, flanking the title
  ['top',    -0.72,  0.38, -1.0, 1.25, 'arm', 'stereo'],
  ['right',   0.88, -0.12, -0.3, 0.95, 'arm'],
  ['right',   0.72, -0.50, -1.4, 0.80, 'tentacle'],
  ['bottom', -0.44, -0.76, -0.2, 0.90, 'arm'],
  ['bottom',  0.46, -0.76,  0.2, 0.90, 'tentacle'],
  // foreground: BIG, cropped by the frame — a lens looming in a corner
  ['left',   -0.88, -0.70,  2.2, 0.90, 'arm'],
  ['right',   0.90, -0.68,  2.0, 0.88, 'arm'],
  ['top',    -0.68,  0.88,  1.8, 0.82, 'tentacle'],
  // background: small, in the fog, long necks from the trusses and the walls
  ['top',    -0.64,  0.16, -5.5, 1.15, 'tentacle'],
  ['top',     0.66,  0.20, -5.2, 1.15, 'tentacle'],
  ['bottom', -0.68, -0.86, -5.0, 1.00, 'tentacle', 'cctv'],
  ['bottom',  0.70, -0.87, -5.6, 1.05, 'tentacle', 'paparazzi'],
  // Fill the upper outer pockets behind the large diagonal necks, instead of
  // stacking another pair of small heads along the already crowded middle sides.
  ['left',   -0.88,  0.73, -4.6, 1.12, 'arm'],
  ['right',   0.73,  0.72, -4.4, 1.05, 'arm'],
  ['bottom', -0.22, -0.94, -3.3, 0.62, 'arm', 'webcam'],
  ['bottom',  0.24, -0.96, -3.8, 0.65, 'tentacle', 'film'],
];
const ENTRY = { top: [0, -1], bottom: [0, 1], left: [1, 0], right: [-1, 0] };
const TEMPERS = ['bold', 'shy', 'sleepy', 'nosy', 'plain', 'plain', 'plain'];
const FOLLOW_CURSOR = false;            // false: they look at GITRL. true: they look at you.
const SWING_FROM = 1.95;                // rad: how far round its root an arm is folded while off stage

// ---- the kit: one merged, vertex-coloured geometry per piece -------------------------
function paint(geo, color) {
  const n = geo.attributes.position.count, a = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) { a[i * 3] = color.r; a[i * 3 + 1] = color.g; a[i * 3 + 2] = color.b; }
  geo.setAttribute('color', new THREE.BufferAttribute(a, 3));
  return geo;
}
const part = (list, geo, mat, x = 0, y = 0, z = 0) => list.push(paint(geo.translate(x, y, z), mat.color));
const cylX = (r, h, seg) => new THREE.CylinderGeometry(r, r, h, seg).rotateZ(Math.PI / 2);

function buildKit() {
  const L = LEN_ARM, V = LEN_VERT, kit = {};
  // pomme's link, part for part (snakeArms.js buildChain with w = 1, segLen = L)
  for (const kind of ['linkA', 'linkB']) {
    const g = [], side = kind === 'linkA' ? -1 : 1;
    part(g, cylX(0.55, 1.6, 10), MAT.joint);                                   // knuckle pin
    part(g, new THREE.BoxGeometry(0.18, 1.1, 1.15), MAT.body, -0.72, 0, 0);    // clevis fork
    part(g, new THREE.BoxGeometry(0.18, 1.1, 1.15), MAT.body, 0.72, 0, 0);
    part(g, kind === 'linkA' ? new THREE.BoxGeometry(0.95, L * 0.8, 1.05)      // casting /
      : new THREE.CylinderGeometry(0.5, 0.56, L * 0.8, 10), MAT.body, 0, L * 0.52, 0);   // turned
    part(g, new THREE.CylinderGeometry(0.16, 0.16, L * 0.42, 10).rotateX(0.38), MAT.dark, 0, L * 0.3, 0.72);
    part(g, new THREE.CylinderGeometry(0.09, 0.09, L * 0.5, 8).rotateX(0.38), MAT.body, 0, L * 0.62, 0.55);
    part(g, new THREE.CylinderGeometry(0.11, 0.11, L * 0.9, 8), MAT.dark, 0, L * 0.5, -0.62);  // cable run
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) part(g, cylX(0.12, 0.1, 8), MAT.dark, sx * 0.84, 0, sz * 0.34);
    part(g, new THREE.TorusGeometry(L * 0.3, 0.09, 6, 12, Math.PI).rotateZ(side * 0.3).rotateY(Math.PI / 2),
      MAT.dark, side * 0.58, L * 0.5, 0);                                      // service hose
    // Raised inspection cover and diagonal safety cuts on the outer casting.
    part(g, new THREE.BoxGeometry(0.62, L * 0.43, 0.035), MAT.dark, 0, L * 0.53, 0.55);
    part(g, new THREE.BoxGeometry(0.48, L * 0.31, 0.045), MAT.body, 0, L * 0.53, 0.57);
    for (let n = -1; n <= 1; n++) part(g, new THREE.BoxGeometry(0.055, 0.23, 0.048).rotateZ(-0.5),
      MAT.dark, n * 0.13, L * 0.53, 0.60);
    for (const sx of [-1, 1]) part(g, cylX(0.22, 0.06, 6), MAT.body, sx * 0.87, 0, 0);
    kit[kind] = mergeGeometries(g, false);
  }
  { const g = []; part(g, new THREE.BoxGeometry(1.05, 0.26, 1.1), MAT.joint, 0, L * 0.84, 0);
    kit.collar = mergeGeometries(g, false); }
  { const g = [];                                                              // shoulder housing
    part(g, new THREE.BoxGeometry(1.7, L * 0.7, 1.7), MAT.body, 0, L * 0.1, 0);
    part(g, new THREE.BoxGeometry(1.85, L * 0.22, 1.85), MAT.dark, 0, L * 0.34, 0);
    part(g, new THREE.BoxGeometry(2.3, 0.3, 2.3), MAT.dark, 0, -L * 0.3, 0);
    kit.shoulder = mergeGeometries(g, false); }
  { const g = [];                                                              // tentacle vertebra
    part(g, new THREE.SphereGeometry(0.5, 8, 6), MAT.joint);
    part(g, new THREE.CylinderGeometry(0.36, 0.42, V * 0.72, 8), MAT.body, 0, V * 0.55, 0);
    part(g, new THREE.CylinderGeometry(0.47, 0.47, 0.08, 8), MAT.dark, 0, V * 0.25, 0);
    part(g, new THREE.CylinderGeometry(0.45, 0.45, 0.08, 8), MAT.dark, 0, V * 0.85, 0);
    for (let k = 0; k < 3; k++) {
      const a = k * 2.094;
      part(g, new THREE.CylinderGeometry(0.06, 0.06, V * 0.9, 6), MAT.dark, Math.cos(a) * 0.5, V * 0.5, Math.sin(a) * 0.5);
      part(g, new THREE.BoxGeometry(0.13, V * 0.36, 0.10).rotateY(-a), MAT.body,
        Math.cos(a) * 0.43, V * 0.52, Math.sin(a) * 0.43);
    }
    kit.vertebra = mergeGeometries(g, false); }
  { const g = []; part(g, new THREE.SphereGeometry(1, 8, 6), MAT.joint); kit.ball = mergeGeometries(g, false); }
  return kit;
}

// ---- scratch ------------------------------------------------------------------------
const _a = new THREE.Vector3(), _b = new THREE.Vector3(), _c = new THREE.Vector3(), _d = new THREE.Vector3();
const _gaze = new THREE.Vector3(), _tgt = new THREE.Vector3(), _ndc = new THREE.Vector3();
const _q = new THREE.Quaternion(), _qi = new THREE.Quaternion(), _qT = new THREE.Quaternion();
const _m = new THREE.Matrix4(), _roll = new THREE.Quaternion(), _yaw = new THREE.Quaternion();
const Z_AXIS = new THREE.Vector3(0, 0, 1);
const SAMPLES = 33;
const _pts = new Float32Array(SAMPLES * 3), _cum = new Float32Array(SAMPLES);
const curveBasis = new Map([25, SAMPLES].map(n => [n, Array.from({ length: n }, (_, k) => {
  const s = k / (n - 1), s2 = s * s, s3 = s2 * s;
  return [2 * s3 - 3 * s2 + 1, s3 - 2 * s2 + s, -2 * s3 + 3 * s2, s3 - s2,
    Math.sin(Math.PI * s), Math.sin(s * 9.4), Math.cos(s * 9.4)];
})]));

export async function buildWatchers(world) {
  const { scene, camera } = world;
  const crowd = new THREE.Group();
  crowd.name = 'watchers';
  crowd.matrixWorldAutoUpdate = false;    // posed and updated once per frame, below — not twice
  scene.add(crowd);
  const kit = buildKit();
  const kitMat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.57, metalness: 0.15 });

  // screen (ndc) + depth -> world, with the live camera
  function toWorld(nx, ny, z, out) {
    _ndc.set(nx, ny, 0.5).unproject(camera).sub(camera.position);
    return out.copy(_ndc).multiplyScalar((z - camera.position.z) / _ndc.z).add(camera.position);
  }
  const halfH = (z) => (camera.position.z - z) * TAN_HALF_FOV;

  // ---- build the cast ---------------------------------------------------------------
  camera.updateMatrixWorld();
  const kinds = HEAD_KINDS.length ? HEAD_KINDS : ['pod'];
  const counts = { linkA: 0, linkB: 0, collar: 0, shoulder: 0, vertebra: 0, ball: 0 };
  const watchers = [];
  for (const [i, [edge, nx, ny, z, size, style, castKind]] of CAST.entries()) {
    await yieldBuild();
    const [ex, ey] = ENTRY[edge];
    const entry = new THREE.Vector3(ex, ey, 0);
    const lateral = new THREE.Vector3(Math.abs(ey), Math.abs(ex), 0);          // across the entry axis
    const w = {
      i, edge, nx, ny, z, size, style, entry, lateral,
      depth: z > 1.5 ? 'fore' : z < -4 ? 'back' : 'mid',
      temper: TEMPERS[Math.floor(hash(i, 11) * TEMPERS.length)],
      delay: lerp(0.06, 0.42, hash(i, 12)) * (0.6 + 0.5 * size),               // reaction time
      perch: new THREE.Vector3(), rootHome: new THREE.Vector3(), headPos: new THREE.Vector3(), bodyPos: new THREE.Vector3(),
      headQ: new THREE.Quaternion(), bulge: new THREE.Vector3(), sep: new THREE.Vector3(),
      roll: new Spring(0, 40, 0.5), swing: null, offStage: true, leanW: 0, hideW: 0, awakeUntil: -1, lastKick: -1,
      glanceUntil: -1, glanceAt: new THREE.Vector3(), spectateUntil: -1, gagGaze: null, gagPush: new THREE.Vector3(),
      shake: 0, startleAt: -Infinity, clickAt: -Infinity, inited: false,
    };
    if (w.depth === 'fore' && (w.temper === 'shy' || w.temper === 'sleepy')) w.temper = 'bold';   // the big ones never hide or doze
    if (w.depth === 'back' && w.temper === 'bold') w.temper = 'sleepy';
    toWorld(nx, ny, z, w.perch);
    // root: past the named edge, slid sideways a little so necks arrive on a diagonal
    const lat = hashS(i, 13) * 0.22;
    toWorld(ex ? -ex * 1.38 : nx + lat, ey ? -ey * 1.4 : ny + lat, z - 0.4 * hash(i, 14), w.rootHome);
    w.T0 = entry.clone().addScaledVector(lateral, -lat * 1.5).normalize();
    // the chain: long enough to reach the perch with slack, and to lean in from there
    // Reach changes the number of vertebrae, never the wrist/head proportion.
    const unit = style === 'arm' ? LEN_ARM : LEN_VERT, AVG = 0.775;
    const want = w.rootHome.distanceTo(w.perch) * 1.10 + 0.45;
    let baseW = (style === 'arm' ? 0.4 : 0.34) * size;
    const count = Math.max(6, Math.ceil(want / (unit * baseW * AVG)));
    baseW = want / (count * unit * AVG); // rounding must not add a whole link's worth of slack
    w.lens = []; w.scales = [];
    for (let k = 0; k < count; k++) {
      const sc = baseW * (1 - 0.45 * k / (count - 1));
      w.scales.push(sc); w.lens.push(unit * sc);
    }
    w.N = w.lens.length;
    w.L = w.lens.reduce((a, b) => a + b, 0);
    w.spring = new Spring3(w.perch, lerp(24, 11, clamp01((size - 0.8) / 0.4)), lerp(0.8, 0.92, hash(i, 15)));
    // nested joints — the rig
    w.root = new THREE.Object3D();
    w.rootQ = new THREE.Quaternion().setFromUnitVectors(UP, w.T0)
      .multiply(new THREE.Quaternion().setFromAxisAngle(UP, hash(i, 16) * 6.283));
    w.root.quaternion.copy(w.rootQ);
    crowd.add(w.root);
    w.joints = []; w.pieces = [];
    let parent = w.root;
    for (let k = 0; k < w.N; k++) {
      const j = new THREE.Object3D();
      j.position.y = k === 0 ? 0 : w.lens[k - 1];
      parent.add(j); parent = j; w.joints.push(j);
      const piece = style === 'arm' ? (k % 2 ? 'linkB' : 'linkA') : 'vertebra';
      const local = new THREE.Matrix4().makeRotationY(style === 'arm' ? 0 : k * 0.52)
        .scale(new THREE.Vector3(w.scales[k], w.scales[k], w.scales[k]));
      w.pieces.push({ piece, slot: counts[piece]++, local, joint: j });
      if (style === 'arm' && k % 3 === 2) w.pieces.push({ piece: 'collar', slot: counts.collar++, local, joint: j });
    }
    const sh = baseW * 1.15;
    w.pieces.push({ piece: 'shoulder', slot: counts.shoulder++, joint: w.root,
      local: new THREE.Matrix4().makeScale(sh, sh, sh) });
    // ball-joint mount, then the head: the head pivots around the ball, so whatever
    // angle is left between neck and gaze reads as a gimbal (pomme's pod mount)
    w.tip = new THREE.Object3D();
    w.tip.position.y = w.lens[w.N - 1] + 0.24 * size;
    parent.add(w.tip);
    w.pieces.push({ piece: 'ball', slot: counts.ball++, joint: w.tip,
      local: new THREE.Matrix4().makeScale(0.15 * size, 0.15 * size, 0.15 * size) });
    // casting: bold, readable faces up close; simple silhouettes in the fog
    const casting = w.depth === 'fore' ? ['stereo', 'eyeball', 'paparazzi', 'pod']
      : w.depth === 'back' ? ['eyeball', 'stereo', 'dome', 'cctv'] : kinds;
    const pool = casting.filter((k) => kinds.includes(k));
    const from = pool.length ? pool : kinds;
    w.head = makeHead(castKind && kinds.includes(castKind) ? castKind : from[Math.floor(hash(i, 17) * from.length)], i, size * 0.66);
    w.tip.add(w.head.group);
    w.blinkPeriod = lerp(2.4, 6.2, hash(i, 18));
    w.letter = Math.floor(hash(i, 27) * TITLE.text.length);
    // folded round its root, off stage: top and bottom arms fold outward along their edge
    // (away from the centre), side arms fold up or down along theirs
    const outward = edge === 'top' ? Math.sign(nx || 1) : edge === 'bottom' ? -Math.sign(nx || 1)
      : edge === 'left' ? (ny >= 0 ? 1 : -1) : (ny >= 0 ? -1 : 1);
    w.phi0 = outward * SWING_FROM;
    w.swing = new Spring(w.phi0, lerp(32, 24, clamp01((size - 0.8) / 0.5)), 0.88);
    w.outDelay = 0.025 + 0.18 * hash(i, 26);
    w.turnAt = INTRO.tada + 0.06 + 0.26 * Math.abs(nx) + 0.12 * hash(i, 19);   // the punchline ripples outward
    watchers.push(w);
  }

  const meshes = {};
  const liveCounts = {};
  for (const [name, n] of Object.entries(counts)) {
    if (!n) continue;
    const mesh = new THREE.InstancedMesh(kit[name], kitMat, n);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    mesh.frustumCulled = false;
    crowd.add(mesh);
    meshes[name] = mesh;
  }
  const byPointer = [...watchers];
  let frameNo = 0;
  const mids = watchers.filter((w) => w.depth === 'mid');
  for (const w of mids) {                       // each one's nearest neighbour, for the jostle gag
    w.neighbour = mids.filter((o) => o !== w).sort((p, q) => p.perch.distanceTo(w.perch) - q.perch.distanceTo(w.perch))[0];
  }
  world.heads = watchers.map((w) => ({ pos: w.headPos, radius: w.head.radius }));
  const updateEyes = batchHeadEyes(watchers.map(w => w.head), crowd);

  // ---- shared attention: a short history of where the pointer was --------------------
  const HIST = 96, hist = Array.from({ length: HIST }, () => ({ t: -1, x: 0, y: 0, present: true }));
  let hi = 0;
  function cursorAt(t) {
    for (let k = 0; k < HIST; k++) {
      const h = hist[(hi - 1 - k + HIST * 2) % HIST];
      if (h.t >= 0 && h.t <= t) return h;
    }
    return hist[(hi - 1 + HIST) % HIST];
  }
  // where a head at `pos` looks to look at a (delayed) pointer: layout.gazePoint's rule
  function gazeFrom(h, pos, out, ahead = 3) {
    if (!h.present) return out.copy(camera.position);
    _ndc.set(h.x, h.y, 0.5).unproject(camera).sub(camera.position);
    const zp = Math.min(pos.z + ahead, camera.position.z - 0.6);
    return out.copy(_ndc).multiplyScalar((zp - camera.position.z) / _ndc.z).add(camera.position);
  }
  const rayAtZ = (h, z, out) => toWorld(h.x, h.y, z, out);

  // ---- events from the rest of the factory --------------------------------------------
  let lastFast = 0, startleAt = -Infinity, lastClickT = -Infinity, layoutKey = '', seenAt = -1;
  const startleFrom = new THREE.Vector3(), clickPoint = new THREE.Vector3();
  world.on('throw', (e) => {
    const until = (e && e.tCatch ? e.tCatch : world.t + 2.4) + 0.45;
    for (const w of watchers) if (hash(w.i, 31) < 0.4) w.spectateUntil = until;
  });
  world.on('poke', (e) => {
    if (!world.title || !e || e.letter == null) return;
    world.title.center(e.letter, _a);
    [...mids].sort((p, q) => p.headPos.distanceTo(_a) - q.headPos.distanceTo(_a)).slice(0, 4)
      .forEach((w, k) => { w.glanceUntil = world.t + 0.75 + k * 0.08; w.glanceAt.copy(_a); });
  });

  // ---- the curve --------------------------------------------------------------------
  // C(s) = Hermite(root, T0 k, H, T1 k1) + sin(pi s) (b B + wave W). Fills _pts with n
  // samples and returns the polyline's length.
  function sample(n, p0, m0, p1, m1, B, b, W, waveAmp, wavePhase) {
    let len = 0, px = 0, py = 0, pz = 0;
    const basis = curveBasis.get(n), sin = Math.sin(wavePhase), cos = Math.cos(wavePhase);
    for (let k = 0; k < n; k++) {
      const [h00, h10, h01, h11, env, ws, wc] = basis[k];
      const wv = env * waveAmp * (ws * cos - wc * sin);
      const x = h00 * p0.x + h10 * m0.x + h01 * p1.x + h11 * m1.x + env * b * B.x + wv * W.x;
      const y = h00 * p0.y + h10 * m0.y + h01 * p1.y + h11 * m1.y + env * b * B.y + wv * W.y;
      const z = h00 * p0.z + h10 * m0.z + h01 * p1.z + h11 * m1.z + env * b * B.z + wv * W.z;
      if (k) len += Math.hypot(x - px, y - py, z - pz);
      _pts[k * 3] = x; _pts[k * 3 + 1] = y; _pts[k * 3 + 2] = z; _cum[k] = len;
      px = x; py = y; pz = z;
    }
    return len;
  }

  const m0 = new THREE.Vector3(), m1 = new THREE.Vector3(), root = new THREE.Vector3(), H = new THREE.Vector3();
  const chordDir = new THREE.Vector3(), actualTip = new THREE.Vector3();
  const qSwing = new THREE.Quaternion(), T0s = new THREE.Vector3(), rootQs = new THREE.Quaternion(), latS = new THREE.Vector3();
  const dPrev = new THREE.Vector3(), dCur = new THREE.Vector3(), Qprev = new THREE.Quaternion(), Qcur = new THREE.Quaternion();
  const pA = new THREE.Vector3(), pB = new THREE.Vector3(), titleLo = new THREE.Vector3(), titleHi = new THREE.Vector3(),
    enterLo = new THREE.Vector3(), enterHi = new THREE.Vector3();

  function pose(w, t, dt, phi, waveAmp) {
    // the swing: everything that defines the curve turns about the root, round the view axis
    qSwing.setFromAxisAngle(Z_AXIS, phi);
    root.copy(w.rootHome);
    H.copy(w.spring.x).sub(root).applyQuaternion(qSwing).add(root);
    T0s.copy(w.T0).applyQuaternion(qSwing);
    rootQs.copy(qSwing).multiply(w.rootQ);
    latS.copy(w.lateral).applyQuaternion(qSwing);
    // never ask for more than the chain has
    _a.subVectors(H, root);
    const reach = w.L * 0.965, dist = _a.length();
    if (dist > reach) H.copy(root).addScaledVector(_a, reach / dist);
    const chord = Math.min(dist, reach);
    chordDir.copy(_a).normalize();
    // end tangent: mostly the gaze, so the neck hooks round and presents the head
    _b.copy(Z_AXIS).applyQuaternion(w.headQ);   // smoothed: a gaze change never snaps the whole neck
    // Keep the exit curve regular even when the wrist looks against the swing.
    // The gimbal handles the remaining turn; a near-cancelling tangent made kinks.
    m1.copy(_a).normalize().multiplyScalar(1.4).add(_b).normalize();
    // slack direction: the watcher's preferred sag, dragged behind the head's motion
    // Smooth the sag in the arm's rotating frame. Projecting last frame's world
    // sag onto a rapidly rotating chord could flip its sign during entry.
    _c.copy(w.sag).addScaledVector(w.spring.v, -0.12);
    _d.copy(chordDir).applyQuaternion(_qi.copy(qSwing).invert());
    _c.addScaledVector(_d, -_c.dot(_d));
    if (_c.lengthSq() < 1e-4) _c.copy(w.lateral);
    w.localBulge ??= _c.clone().normalize();
    w.localBulge.lerp(_c.normalize(), w.inited ? 1 - Math.exp(-6 * dt) : 1).normalize();
    w.bulge.copy(w.localBulge).applyQuaternion(qSwing);
    // All departures from the chord are transverse. Longitudinal progress is
    // strictly positive, so a chain cannot double back and cross itself.
    w.bulge.addScaledVector(chordDir, -w.bulge.dot(chordDir)).normalize();
    latS.addScaledVector(chordDir, -latS.dot(chordDir)).normalize();
    T0s.addScaledVector(chordDir, -T0s.dot(chordDir)).multiplyScalar(0.3).add(chordDir);
    m1.addScaledVector(chordDir, -m1.dot(chordDir)).multiplyScalar(0.3).add(chordDir);

    // arc length = chain length: shrink the tangents if even b = 0 is too long,
    // otherwise grow the bulge until the slack is used up (both monotonic -> bisect)
    let k = chord, b = 0;
    const fit = (kk, bb) => { m0.copy(T0s).multiplyScalar(kk); _tgt.copy(m1).multiplyScalar(kk);
      return sample(25, root, m0, H, _tgt, w.bulge, bb, latS, waveAmp, t * 7 + w.i); };
    if (fit(k, 0) > w.L) {
      let lo = 0, hi = k;
      for (let it = 0; it < 7; it++) { k = (lo + hi) / 2; if (fit(k, 0) > w.L) hi = k; else lo = k; }
      k = lo;
    } else {
      let lo = 0, hi = w.L;
      b = w.fitB ?? w.L * 0.3;
      // Warm-start smooth motion. Safeguarded Newton steps usually need just two
      // length probes; a bracketed fallback handles entrance and resize changes.
      for (let it = 0; it < 7; it++) {
        const length = fit(k, b), error = length - w.L;
        if (Math.abs(error) < w.L * 0.0005) break;
        if (error > 0) hi = b; else lo = b;
        const eps = w.L * 0.008, slope = (fit(k, b + eps) - length) / eps;
        const next = b - error / Math.max(0.01, slope);
        b = next > lo && next < hi ? next : (lo + hi) / 2;
      }
      w.fitB = b;
    }
    m0.copy(T0s).multiplyScalar(k); _tgt.copy(m1).multiplyScalar(k);
    const total = sample(SAMPLES, root, m0, H, _tgt, w.bulge, b, latS, waveAmp, t * 7 + w.i);

    // joints from the curve, by parallel transport (consecutive directions differ by
    // small angles, so setFromUnitVectors never meets its anti-parallel singularity)
    w.root.position.copy(root);
    w.root.quaternion.copy(rootQs);
    dPrev.copy(w.T0).applyQuaternion(qSwing); Qprev.copy(rootQs);
    pA.copy(root);
    actualTip.copy(root);
    let along = 0, seg = 1;
    for (let j = 0; j < w.N; j++) {
      along += w.lens[j] * (total / w.L);
      while (seg < SAMPLES - 1 && _cum[seg] < along) seg++;
      const u = clamp01((along - _cum[seg - 1]) / Math.max(1e-6, _cum[seg] - _cum[seg - 1]));
      pB.set(lerp(_pts[seg * 3 - 3], _pts[seg * 3], u), lerp(_pts[seg * 3 - 2], _pts[seg * 3 + 1], u),
        lerp(_pts[seg * 3 - 1], _pts[seg * 3 + 2], u));
      dCur.subVectors(pB, pA).normalize();
      actualTip.addScaledVector(dCur, w.lens[j]);
      _q.setFromUnitVectors(dPrev, dCur);
      Qcur.multiplyQuaternions(_q, Qprev);
      w.joints[j].quaternion.copy(_qi.copy(Qprev).invert()).multiply(Qcur);
      Qprev.copy(Qcur); dPrev.copy(dCur); pA.copy(pB);
    }
    w.headPos.copy(actualTip).addScaledVector(dCur, 0.24 * w.size);

    // the head: aimed in WORLD space (keeps world-up: no roll inherited from the neck),
    // with a servo's lag, then tilted and — when glared at — shaken
    _b.subVectors(w.gazeNow, w.headPos).normalize();
    const backTurn = _b.dot(dCur);
    if (backTurn < 0) _b.addScaledVector(dCur, -backTurn).normalize();
    _m.lookAt(_b.add(w.headPos), w.headPos, UP);
    _qT.setFromRotationMatrix(_m);
    if (!w.inited) w.headQ.copy(_qT);
    w.headQ.slerp(_qT, 1 - Math.exp(-dt * w.headRate));
    _roll.setFromAxisAngle(Z_AXIS, w.roll.x);
    _yaw.setFromAxisAngle(UP, w.shake);
    w.head.group.quaternion.copy(_qi.copy(Qprev).invert()).multiply(_yaw).multiply(w.headQ).multiply(_roll);
    // the head's MASS hangs forward of the neck ball, toward the gaze: that, not the
    // ball, is what must stay off the title and off the other heads
    w.bodyPos.copy(w.head.center).applyQuaternion(w.headQ).add(w.headPos);
    w.inited = true;
  }

  // ---- bracket-mounted CCTV, on whatever mounts the hall publishes ---------------------
  const fixtures = [];
  function buildFixtures() {
    const mounts = world.hall && world.hall.mounts;
    if (!mounts || !mounts.length || fixtures.length) return;
    const geos = [];
    const kind = ['cctv', 'dome', 'pod'].find((k) => kinds.includes(k)) || 'pod';
    mounts.forEach((mnt, k) => {
      const n = _a.copy(mnt.normal).normalize().clone(), q = new THREE.Quaternion().setFromUnitVectors(Z_AXIS, n);
      const place = (geo, mat, x, y, z) => { geo.translate(x, y, z).applyQuaternion(q).translate(mnt.pos.x, mnt.pos.y, mnt.pos.z);
        geos.push(paint(geo, mat.color)); };
      place(new THREE.BoxGeometry(0.34, 0.34, 0.06), MAT.dark, 0, 0, 0.03);
      place(new THREE.CylinderGeometry(0.05, 0.06, 0.5, 8).rotateX(Math.PI / 2), MAT.body, 0, 0, 0.3);
      place(new THREE.SphereGeometry(0.1, 10, 8), MAT.joint, 0, 0, 0.56);
      const head = makeHead(kind, 100 + k, 0.5);
      head.group.position.copy(mnt.pos).addScaledVector(n, 0.56);
      crowd.add(head.group);
      const f = { head, pos: head.group.position, q: new THREE.Quaternion(), delay: lerp(0.15, 0.6, hash(k, 41)),
        blinkPeriod: lerp(3, 7, hash(k, 42)), inited: false };
      fixtures.push(f);
      world.heads.push({ pos: f.pos, radius: head.radius });
    });
    const mesh = new THREE.Mesh(mergeGeometries(geos, false), kitMat);
    mesh.frustumCulled = false;
    crowd.add(mesh);
  }

  // 0 = open .. 1 = shut. One blink: close over 80 ms, hold 40, open over 140; heads.js
  // eases the lids on top of this, so a blink is a movement, never a switch.
  const lidCurve = (ph) => ph < 0 || ph > 0.26 ? 0 : ph < 0.08 ? smooth(0, 0.08, ph) : ph < 0.12 ? 1 : 1 - smooth(0.12, 0.26, ph);
  const blink = (period, seed, t) => {
    const n = Math.floor((t + seed * 7.3) / period), ph = (t + seed * 7.3) - n * period;
    return Math.max(lidCurve(ph), hash(n, seed + 50) > 0.7 ? lidCurve(ph - 0.34) : 0);
  };

  // ---- per frame ----------------------------------------------------------------------
  function update(world) {
    const { t, dt } = world, cur = world.cursor;
    const h = hist[hi]; h.t = t; h.x = cur.ndc.x; h.y = cur.ndc.y; h.present = cur.present;
    hi = (hi + 1) % HIST;
    buildFixtures();

    // re-frame the crowd when the screen changes shape
    const key = camera.aspect.toFixed(3) + '|' + camera.position.z.toFixed(2);
    if (key !== layoutKey) {
      layoutKey = key;
      camera.updateMatrixWorld();
      for (const w of watchers) {
        // A portrait frame has room for three overhead characters, not ten.
        // Use the same eased exit for the others, including during live resize.
        w.compactHidden = camera.aspect < 0.8 && w.edge === 'top' && ![2, 3, 4].includes(w.i);
        toWorld(w.nx, w.ny, w.z, w.perch);
        const [ex, ey] = ENTRY[w.edge], lat = hashS(w.i, 13) * 0.22;
        toWorld(ex ? -ex * 1.38 : w.nx + lat, ey ? -ey * 1.4 : w.ny + lat, w.z - 0.4 * hash(w.i, 14), w.rootHome);
        // roots stay off-screen, always: if the screen got wider than the chain can
        // span, it is the PERCH that gives way (the head sits nearer its own edge)
        _a.subVectors(w.perch, w.rootHome);
        if (_a.length() > w.L * 0.82) w.perch.copy(w.rootHome).addScaledVector(_a.normalize(), w.L * 0.82);
        w.sag = w.sag || new THREE.Vector3();
        if (w.edge === 'bottom') w.sag.set(hashS(w.i, 20) * 0.6, 0.2, 0.8);      // floor risers arch toward you
        else if (w.edge === 'top') w.sag.set(hashS(w.i, 20), -0.3, 0.5);
        else w.sag.set(0, -1, 0.35);                                              // side arms sag
      }
    }
    // GITRL's box on screen, to keep it readable
    titleLo.set(-TITLE_CLEAR.x, TITLE_CLEAR.y0, TITLE.center.z).project(camera);
    titleHi.set(TITLE_CLEAR.x, TITLE_CLEAR.y1, TITLE.center.z).project(camera);
    enterLo.set(-ENTER_CLEAR.x, ENTER_CLEAR.y0, ENTER.center.z).project(camera);
    enterHi.set(ENTER_CLEAR.x, ENTER_CLEAR.y1, ENTER.center.z).project(camera);

    // crowd-level state
    if (cur.speed > 0.7 || !cur.seen) lastFast = t;
    const working = t > INTRO.work + 0.8;
    if (cur.seen && seenAt < 0) seenAt = t;
    // (the first frames after the pointer appears or returns are a teleport, not a whip)
    const settled = t - seenAt > 0.7 && t - cur.returnedAt > 0.7;
    if (FOLLOW_CURSOR && working && settled && cur.speed > 48 && t - startleAt > 3.5) { startleAt = t; startleFrom.copy(cur.point); }
    if (world.click.t !== lastClickT) { lastClickT = world.click.t; clickPoint.copy(world.click.point); }

    // the jostle gag: A shoves its neighbour B; B glares, shakes its head; A plays innocent
    for (const w of mids) { w.gagGaze = null; w.gagPush.set(0, 0, 0); w.shake = 0; }
    const gi = Math.floor((t - INTRO.work - 5) / 13);
    if (gi >= 0 && mids.length > 1) {
      const tau = t - (INTRO.work + 5 + gi * 13 + hash(gi, 61) * 4);
      const A = mids[Math.floor(hash(gi, 62) * mids.length)], B = A.neighbour;
      if (tau >= 0 && tau < 2.0 && B) {
        _a.subVectors(B.perch, A.perch).normalize();
        A.gagPush.copy(_a).multiplyScalar(0.5 * bump(0, 0.7, tau));
        if (tau > 0.28 && B.lastKick !== gi) { B.lastKick = gi; B.spring.kick(_b.copy(_a).multiplyScalar(1.8)); B.roll.kick(1.5); }
        if (tau > 0.4 && tau < 1.15) B.gagGaze = A.headPos;
        if (tau > 1.15 && tau < 1.8) B.shake = Math.sin((tau - 1.15) * 22) * 0.2 * (1 - (tau - 1.15) / 0.65);
        if (tau > 0.45 && tau < 1.35) A.gagGaze = _c.copy(A.headPos).addScaledVector(_a, -2).add(_d.set(0, 2.2, 2.5)).clone();
      }
    }

    // heads keep out of each other's way on screen (matters when they crowd the cursor)
    const asp = camera.aspect;
    for (const w of watchers) { _ndc.copy(w.bodyPos).project(camera); w.sx = _ndc.x * asp; w.sy = _ndc.y;
      w.sr = w.head.radius / halfH(w.bodyPos.z) * 0.9; w.pushX = 0; w.pushY = 0; }
    // the pomme camera and claw are the heroes: the crowd gives them room, never the reverse
    for (const hero of world.hero || []) {
      _ndc.copy(hero.pos).project(camera);
      const hx = _ndc.x * asp, hy = _ndc.y, hr = hero.radius / halfH(hero.pos.z);
      for (const w of watchers) {
        const dx = w.sx - hx, dy = w.sy - hy, d = Math.hypot(dx, dy) || 1e-4, over = w.sr + hr - d;
        if (over > 0 && w.inited) { w.pushX += dx / d * over; w.pushY += dy / d * over; }
      }
    }
    for (let p = 0; p < watchers.length; p++) for (let q = p + 1; q < watchers.length; q++) {
      const a = watchers[p], b = watchers[q], dx = a.sx - b.sx, dy = a.sy - b.sy, d = Math.hypot(dx, dy) || 1e-4;
      const over = a.sr + b.sr - d;
      if (over > 0 && a.inited && b.inited) { const f = over * 0.5 / d; a.pushX += dx * f; a.pushY += dy * f; b.pushX -= dx * f; b.pushY -= dy * f; }
    }

    if (frameNo++ % 12 === 0) {           // who is nearest the pointer changes slowly: rank 5x a second
      for (const w of watchers) w.dPtr = Math.hypot((w.nx - cur.ndc.x) * asp, w.ny - cur.ndc.y);
      byPointer.sort((p, q) => p.dPtr - q.dPtr);
      byPointer.forEach((w, rank) => { w.mayLean = rank < 6 && w.depth !== 'back'; });
    }

    for (const w of watchers) {
      const seen = FOLLOW_CURSOR ? cursorAt(t - w.delay) : h;
      const ent = introEnter(t, w.nx);
      const beat = t - w.delay * 0.75;
      const conv = introConverge(beat), tada = introTada(beat);
      const carry = 1 - smooth(INTRO.release, INTRO.release + 2.4, beat);
      const flow = t * 1.05 / (w.temper === 'sleepy' ? 1.3 : 1) + w.i * 1.7;

      // ---- where the head wants to be --------------------------------------------------
      _tgt.copy(w.perch);
      const lazy = w.temper === 'sleepy' ? 1.7 : w.temper === 'nosy' ? 1.5 : 1;
      _tgt.x += Math.sin(flow) * (0.13 + 0.09 * carry) * lazy * w.size;
      _tgt.y += Math.cos(flow * 0.87 + w.i * 0.6) * (0.16 + 0.10 * carry) * w.size;
      _tgt.z += Math.sin(flow * 0.63) * (0.12 + 0.08 * carry);

      // still cursor -> they lean in and ring it; any real movement lets go
      const still = FOLLOW_CURSOR && working && w.mayLean && cur.seen && seen.present && t - lastFast > 1.5;
      w.leanW += ((still ? 1 : 0) - w.leanW) * (1 - Math.exp(-dt * (still ? 0.8 : 4.5)));
      rayAtZ(seen, w.perch.z, _a);
      const near = FOLLOW_CURSOR ? clamp01(1 - _a.distanceTo(w.headPos) / 4) : 0.6;   // (pose() reuses _a: read it now)
      _b.subVectors(_a, _tgt); _b.z = 0;
      const dist = _b.length() || 1e-4;
      const reachMax = w.temper === 'bold' ? 2.6 : w.temper === 'shy' ? 0.5 : w.depth === 'back' ? 0.9 : 1.5;
      const ring = (w.temper === 'bold' ? 1.0 : 1.35) + w.head.radius;
      _tgt.addScaledVector(_b, Math.min(reachMax, Math.max(0, dist - ring)) / dist * w.leanW);
      if (w.temper === 'bold') _tgt.z += 0.7 * w.leanW;

      // shy: caught being pointed at -> ducks back toward its own arm, peeks out later
      if (w.temper === 'shy') {
        const caught = FOLLOW_CURSOR && working && seen.present && _a.distanceTo(w.headPos) < 1.0;
        w.hideW += ((caught ? 1 : 0) - w.hideW) * (1 - Math.exp(-dt * (caught ? 9 : 1.1)));
        _tgt.lerp(w.rootHome, 0.3 * w.hideW);
      }
      // startle: flinch away from a fast cursor, after this one's own reaction time
      if (startleAt + w.delay < t && w.startleAt < startleAt) {
        w.startleAt = startleAt; w.awakeUntil = t + 4;
        w.spring.kick(_c.subVectors(w.headPos, startleFrom).setZ(0).normalize().multiplyScalar(2.2 / w.size));
      }
      // click: recoil a beat, then push in closer than before
      if (lastClickT + w.delay < t && w.clickAt < lastClickT) {
        w.clickAt = lastClickT; w.awakeUntil = t + 5;
        w.spring.kick(_c.subVectors(w.headPos, clickPoint).setZ(-1.2).normalize().multiplyScalar(2.8 / w.size));
        w.head.flash();
      }
      const sinceClick = t - w.clickAt;
      if (sinceClick > 0.3 && sinceClick < 2.2) {
        _c.subVectors(clickPoint, _tgt); _c.z = 1.2;
        _tgt.addScaledVector(_c.normalize(), 0.7 * bump(0.3, 2.2, sinceClick));
      }
      // gone: after the stare, shoulders drop
      _tgt.add(w.gagPush);

      // intro: pull back, then close in on the title's ring from wherever we stand; hop on the ta-da
      if (conv !== 0) {
        presentPoint(w.perch, _c);
        const k = (camera.position.z - w.perch.z) / (camera.position.z - TITLE.center.z);
        _c.set(lerp(camera.position.x, _c.x, k), lerp(camera.position.y, _c.y, k), w.perch.z);
        _tgt.lerp(_c, (w.depth === 'fore' ? 0.16 : 0.5) * conv);
      }
      _tgt.y += 0.16 * tada * w.size;

      // Separation is applied before the text guards, so it cannot push a head
      // back through the reserved lettering space.
      const hh = halfH(_tgt.z);
      w.sep.lerp(_c.set(w.pushX * hh, w.pushY * hh, 0), 1 - Math.exp(-dt * 4));
      _tgt.add(w.sep);

      // keep GITRL and the ENTER button readable: push the head's MASS out of their boxes
      // on screen (a curious one may peek a little way over the title's edge)
      _d.subVectors(w.bodyPos, w.headPos);                  // neck -> body, as posed last frame
      for (const [lo, hi, peek] of [[titleLo, titleHi, 0.03 * w.leanW], [enterLo, enterHi, 0]]) {
        _ndc.copy(_tgt).add(_d).project(camera);
        const rx = w.sr / asp, ry = w.sr;
        const inL = _ndc.x + rx - (lo.x + peek), inR = (hi.x - peek) - (_ndc.x - rx);
        const inB = _ndc.y + ry - (lo.y + peek), inT = (hi.y - peek) - (_ndc.y - ry);
        if (inL > 0 && inR > 0 && inB > 0 && inT > 0) {
          const mn = Math.min(inL, inR, inB, inT);
          if (mn === inL) _ndc.x -= inL; else if (mn === inR) _ndc.x += inR; else if (mn === inB) _ndc.y -= inB; else _ndc.y += inT;
          toWorld(_ndc.x, _ndc.y, _tgt.z + _d.z, _tgt).sub(_d);
        }
      }
      if (!w.inited) w.spring.snap(_tgt);
      w.spring.step(_tgt, dt);

      // ---- where it looks -------------------------------------------------------------
      // the watcher's own delayed view of the pointer; then whatever outranks you
      w.gazeNow = w.gazeNow || new THREE.Vector3();
      const controlTarget = world.info?.hovered ? world.info : world.enter?.hovered ? world.enter : null;
      if (FOLLOW_CURSOR) gazeFrom(seen, w.headPos, _gaze);
      else if (controlTarget) _gaze.copy(controlTarget.pos);
      else if (world.title) world.title.center(w.letter, _gaze);
      else _gaze.copy(TITLE.center).setX(TITLE.center.x + (w.letter - (TITLE.text.length - 1) / 2) * TITLE.width / TITLE.text.length);
      // Look inward in three-quarter view: the lens remains readable on the big heads.
      if (!FOLLOW_CURSOR) _gaze.z = Math.max(_gaze.z, w.headPos.z + 2.8);
      // when you hold still the eye makes tiny fixation jumps (it is alive); never while
      // you move, where it would read as looking at random places
      const sn = Math.floor(t / lerp(0.7, 1.4, hash(w.i, 23)) + w.i);
      _gaze.x += hashS(sn, w.i + 3) * 0.05 * w.leanW; _gaze.y += hashS(sn, w.i + 4) * 0.04 * w.leanW;
      let rate = lerp(10, 5, clamp01((w.size - 0.8) / 0.4));
      if (w.temper === 'sleepy' && t > w.awakeUntil) { _gaze.y -= 1.3; rate *= 0.5; }
      if (w.temper === 'shy' && w.hideW > 0.3) _gaze.copy(w.headPos).addScaledVector(w.entry, -3).add(_c.set(0, -0.8, 1.5));
      // pointer off the page: they look at YOU (gazeFrom returns the viewer), with a slow
      // drift so the stare is alive. No glancing about — that read as random.
      if (FOLLOW_CURSOR && !seen.present) { _gaze.x += Math.sin(t * 0.4 + w.i) * 0.5; _gaze.y += Math.sin(t * 0.31 + w.i * 1.7) * 0.3; }
      if (t < w.spectateUntil && world.crates && world.crates.flying && world.crates.flying()) {
        world.crates.flying().object3D.getWorldPosition(_gaze);
      }
      if (t < w.glanceUntil) _gaze.copy(w.glanceAt);
      if (w.gagGaze) _gaze.copy(w.gagGaze);
      if (controlTarget) _gaze.copy(controlTarget.pos).setZ(Math.max(controlTarget.pos.z, w.headPos.z + 2.8));
      // Each camera anticipates its own letter, then follows it down. Blend the
      // attention continuously instead of all cameras switching targets together
      // and chasing a new letter every 120 ms.
      if (!FOLLOW_CURSOR && !controlTarget) {
        const dropAt = INTRO.titleDrop + w.letter * INTRO.titleStagger;
        _a.copy(TITLE.center).setX(TITLE.center.x + (w.letter - (TITLE.text.length - 1) / 2) * TITLE.width / TITLE.text.length);
        _a.y += 1.1 * (1 - smooth(dropAt - 0.2, dropAt + 0.7, t));
        _gaze.lerp(_a, 1 - smooth(dropAt - 0.15, dropAt + 0.55, t));
        _a.copy(w.headPos).addScaledVector(w.entry, 1.8).add(_c.set(0, 0.4, 3));
        _gaze.lerp(_a, 1 - smooth(ent.delay + 0.25, dropAt + 0.15, t));
        _gaze.z = Math.max(_gaze.z, w.headPos.z + 2.8);
      }
      // the look itself is eased: switching target (title -> you, you -> a neighbour's
      // shove, back again) is a turn of the head, never a jump cut
      if (!w.inited) w.gazeNow.copy(_gaze);
      else w.gazeNow.lerp(_gaze, 1 - Math.exp(-dt * (controlTarget ? 45 :
        w.temper === 'sleepy' ? 3 : lerp(8, 4.5, clamp01((w.size - 0.8) / 0.4)))));
      w.headRate = controlTarget ? 35 : rate;

      // tilt: a slow lazy roll, a curious cock of the head when you hold still
      const tiltTo = Math.sin(flow * 0.72 + 0.5) * (0.12 + 0.045 * carry)
        + hashS(w.i, 24) * 0.34 * w.leanW + Math.sin(beat * 5) * 0.09 * tada;
      w.roll.step(tiltTo, dt);

      // THE SWING. Off stage an arm is folded round its root, outside the frame. Its cue
      // comes on the intro's left-to-right ripple; it swings in like a gate, past its mark
      // and back (the spring), with a wave running down its body while it moves. ENTER
      // sends it back out the way it came; scrolling back to the hero brings it in again.
      const away = world.away, cue = away.on ? Infinity : Math.max(ent.delay, away.since + 0.15 + w.outDelay * 1.6);
      const out = w.compactHidden || t < cue || (away.on && t > away.since + w.outDelay);
      if (away.on && t > away.since + w.outDelay && !w.offStage) w.swing.kick(-Math.sign(w.phi0) * 2.2);   // a wind-up first
      w.offStage = out;
      // Keep a small pendular tail after entry, flowing into the idle orbit.
      const afloat = Math.sin(flow * 0.8) * (0.012 + 0.035 * carry);
      w.swing.step(out ? w.phi0 : afloat, dt);
      const moving = Math.min(1, Math.abs(w.swing.v) / 3);
      const hidden = out && Math.abs(w.swing.x - w.phi0) < 0.06 && Math.abs(w.swing.v) < 0.2;
      if (!hidden || !w.hidden || !w.inited) pose(w, t, dt, w.swing.x,
        (0.06 * moving + 0.018 + 0.022 * carry) * w.size);
      w.hidden = hidden;
      w.head.group.visible = !w.hidden;

      // eyes: blink, dilate when close to the pointer, pinpoint when startled
      let iris = lerp(0.62, 1, near);
      const shut = blink(w.blinkPeriod, w.i, t);
      if (w.head.setBlink) w.head.setBlink(shut); else iris *= 1 - shut;
      if (t - w.startleAt < 0.55) iris = Math.min(iris, 0.3);
      if (w.hideW > 0.3) iris = Math.min(iris, 0.4);
      const dozing = w.temper === 'sleepy' && t > w.awakeUntil;
      if (w.head.setLids) w.head.setLids(dozing ? 0.45 : w.leanW * 0.1, dozing ? 0.1 : 0);
      else if (dozing) iris = Math.min(iris, 0.45);
      w.head.setIris(iris);
      w.head.setTally(sinceClick < 1.6 ? +(Math.sin(t * 38) > 0) : +(Math.sin(t * 3.1 + w.i) > -0.2));
      w.head.update(dt, t);
    }

    for (const f of fixtures) {
      const controlTarget = world.info?.hovered ? world.info : world.enter?.hovered ? world.enter : null;
      if (controlTarget) _gaze.copy(controlTarget.pos);
      else gazeFrom(cursorAt(t - f.delay), f.pos, _gaze, 4);
      _m.lookAt(_gaze, f.pos, UP); _qT.setFromRotationMatrix(_m);
      if (!f.inited) { f.q.copy(_qT); f.inited = true; }
      f.head.group.quaternion.copy(f.q.slerp(_qT, 1 - Math.exp(-dt * (controlTarget ? 35 : 5))));
      f.head.setIris(0.9);
      if (f.head.setBlink) f.head.setBlink(blink(f.blinkPeriod, 200 + fixtures.indexOf(f), t));
      f.head.setTally(+(Math.sin(t * 2.2 + f.delay * 20) > 0));
      f.head.update(dt, t);
    }

    // one matrix pass for the whole crowd, then hand the joints to the instanced kit
    crowd.updateMatrixWorld(true);
    updateEyes();
    for (const name in meshes) liveCounts[name] = 0;
    for (const w of watchers) {
      if (w.hidden) continue;
      for (const p of w.pieces)
        meshes[p.piece].setMatrixAt(liveCounts[p.piece]++, _m.multiplyMatrices(p.joint.matrixWorld, p.local));
    }
    for (const name in meshes) {
      meshes[name].count = liveCounts[name];
      meshes[name].instanceMatrix.needsUpdate = true;
    }
  }

  return { update, watchers, fixtures };
}
