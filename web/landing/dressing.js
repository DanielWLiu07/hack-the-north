// dressing.js — the machinery that fills the frame's empty edges: meshing gear trains
// sunk in the floor either side of the ENTER button, a pumping crank-and-piston, a
// chain drive, belt-driven flywheels on lattice girders across the top corners,
// pipe runs with valve wheels, a bank of pressure gauges, riveted I-beams cropping the
// bottom corners. A dense machined FRAME around a calm dark middle: nothing here comes
// inside TITLE_CLEAR (plus margin) or ENTER_CLEAR, and nothing sits behind the title.
//
// TONE. The manga pass at grit 1 is close to binary (lit surfaces print white), and the
// crowd of watchers in front is solid white — so the dressing is drawn like LINE ART:
// ink faces, thin light rims, spokes you can see through, hazard stripes. It reads as
// structure and stays recessive behind the cameras.
//
// COST. Every static cluster is ONE mesh (merged, vertex-coloured with mech.js MAT's
// own colours) and every moving part is one mesh, all sharing one material: ~30 draws.
// Gears mesh exactly: each gear's angle is derived from its driver's by tooth count
// every frame, so ratios, counter-rotation and tooth/gap alignment hold even while the
// right-hand train jams and judders. Deterministic from world.t (hash, no Math.random).

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { MAT, hash, clamp01, smoother } from './mech.js';
import { FLOOR_Y, INTRO } from './layout.js';

const TAN_HALF_FOV = 0.364;                       // the rig's camera: vertical fov 40
const C = {
  body: MAT.body.color, dark: MAT.dark.color, joint: MAT.joint.color,
  ink: new THREE.Color('#15151a'), hazard: new THREE.Color('#f2c230'),
};
const Z_TRAIN = -1.15, Z_CORNER = -2.6, Z_FORE = 1.8;
const MODULE = 0.125;                             // gear module: pitch radius = MODULE * N / 2

const bx = (w, h, d) => new THREE.BoxGeometry(w, h, d);
const cyZ = (r0, r1, h, seg = 16) => new THREE.CylinderGeometry(r0, r1, h, seg).rotateX(Math.PI / 2);
const cyY = (r0, r1, h, seg = 14) => new THREE.CylinderGeometry(r0, r1, h, seg);
const cyX = (r, h, seg = 14) => new THREE.CylinderGeometry(r, r, h, seg).rotateZ(Math.PI / 2);
// a bar from (ax, ay) to (bx, by) in the xy-plane
function strut(ax, ay, bxx, byy, t, d) {
  const dx = bxx - ax, dy = byy - ay;
  return bx(t, Math.hypot(dx, dy), d).rotateZ(Math.atan2(dy, dx) - Math.PI / 2)
    .translate((ax + bxx) / 2, (ay + byy) / 2, 0);
}

// ---- Kit: merge everything into one vertex-coloured geometry ------------------------
class Kit {
  constructor() { this.g = []; this.xf = null; }
  add(geo, color, x = 0, y = 0, z = 0) {
    geo.translate(x, y, z);
    if (this.xf) geo.applyMatrix4(this.xf);
    const g = geo.index ? geo.toNonIndexed() : geo;          // Extrude is non-indexed; match it
    if (g !== geo) geo.dispose();
    const n = g.attributes.position.count, a = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) { a[i * 3] = color.r; a[i * 3 + 1] = color.g; a[i * 3 + 2] = color.b; }
    g.setAttribute('color', new THREE.BufferAttribute(a, 3));
    this.g.push(g);
    return this;
  }
  geometry() {
    const out = mergeGeometries(this.g, false);
    for (const g of this.g) g.dispose();
    this.g.length = 0;
    return out;
  }
  mesh(mat, parent) {
    const m = new THREE.Mesh(this.geometry(), mat);
    m.frustumCulled = false;
    if (parent) parent.add(m);
    return m;
  }
}

// ---- wheels ---------------------------------------------------------------------------
// Tooth 0 points along the mesh's local +x, so rotation.z IS the gear angle.
function ringShape(N, m, toothed) {
  const r = (m * N) / 2, ro = r + m, rr = r - 1.25 * m, p = (2 * Math.PI) / N, s = new THREE.Shape();
  if (!toothed) { s.absarc(0, 0, ro, 0, Math.PI * 2, false); return { s, r, ro, rr: ro }; }
  for (let k = 0; k < N; k++) {
    const a = k * p;
    const pts = [[rr, a - 0.29 * p], [ro, a - 0.13 * p], [ro, a + 0.13 * p], [rr, a + 0.29 * p]];
    for (const [rad, ang] of pts) (k === 0 && rad === rr && ang < a ? s.moveTo : s.lineTo)
      .call(s, rad * Math.cos(ang), rad * Math.sin(ang));
  }
  s.closePath();
  return { s, r, ro, rr };
}

function wheelKit(kit, N, m, { toothed = true, depth = 0.16, spokes = 5, hub = 0.2, rim = 0.13 } = {}) {
  const { s, r, ro, rr } = ringShape(N, m, toothed);
  const ri = rr - rim;
  const open = ri > hub + 0.14;                     // room for spokes? else a solid web
  if (open) { const h = new THREE.Path(); h.absarc(0, 0, ri, 0, Math.PI * 2, true); s.holes.push(h); }
  kit.add(new THREE.ExtrudeGeometry(s, { depth, bevelEnabled: false, curveSegments: 20 }), C.body, 0, 0, -depth / 2);
  if (open) {
    for (let k = 0; k < spokes; k++) {
      const a = (k / spokes) * Math.PI * 2 + 0.3;
      kit.add(strut(hub * 0.7 * Math.cos(a), hub * 0.7 * Math.sin(a), (ri + 0.03) * Math.cos(a), (ri + 0.03) * Math.sin(a),
        0.085, depth * 0.55), C.body);
    }
  } else {
    kit.add(cyZ(rr * 0.8, rr * 0.8, 0.02, 20), C.ink, 0, 0, depth / 2 + 0.005);      // recessed web
  }
  kit.add(cyZ(hub, hub, depth * 1.5, 16), C.joint);                                   // hub
  kit.add(cyZ(hub * 0.5, hub * 0.5, depth * 1.9, 10), C.ink);                         // shaft end
  for (let k = 0; k < 5; k++) {                                                       // hub bolts
    const a = (k / 5) * Math.PI * 2;
    kit.add(cyZ(0.022, 0.022, 0.03, 6), C.ink, hub * 0.75 * Math.cos(a), hub * 0.75 * Math.sin(a), depth * 0.76);
  }
  return { r, ro };
}

// ---- structure ------------------------------------------------------------------------
function ibeam(kit, len, h, flange) {             // along +x from 0, web facing the camera
  kit.add(bx(len, h, 0.05), C.ink, len / 2, 0, 0);
  for (const s of [-1, 1]) kit.add(bx(len, 0.045, flange), C.body, len / 2, s * h / 2, 0);
  for (let x = 0.5; x < len; x += 1.05) kit.add(bx(0.055, h - 0.1, flange * 0.8), C.body, x, 0, 0);
  for (let x = 0.12; x < len; x += 0.21) for (const s of [-1, 1]) {
    kit.add(new THREE.SphereGeometry(0.03, 6, 4), C.body, x, s * (h / 2 - 0.13), 0.035);   // rivets
  }
}

function lattice(kit, len, sp) {                  // along +x from 0: two chords and a zigzag
  for (const s of [-1, 1]) kit.add(bx(len, 0.09, 0.09), C.body, len / 2, s * sp / 2, 0);
  const n = Math.max(2, Math.round(len / (sp * 1.15)));
  for (let i = 0; i < n; i++) {
    const x0 = (len * i) / n, x1 = (len * (i + 1)) / n, up = i % 2 ? 1 : -1;
    kit.add(strut(x0, -up * sp / 2, x1, up * sp / 2, 0.05, 0.05), C.body);
    kit.add(bx(0.2, 0.2, 0.03), C.ink, x0, -up * sp / 2, 0.05);                         // gusset plate
    kit.add(cyZ(0.025, 0.025, 0.03, 6), C.body, x0, -up * sp / 2, 0.075);
  }
}

function hazardBar(kit, x0, x1, y, z, h, d) {
  const n = Math.max(2, Math.round(Math.abs(x1 - x0) / 0.3)), w = (x1 - x0) / n;
  for (let i = 0; i < n; i++) kit.add(bx(Math.abs(w) * 0.98, h, d), i % 2 ? C.ink : C.hazard, x0 + w * (i + 0.5), y, z);
}

// seconds of full-speed running by time t: still at t = 0, eased in through the intro
// (the integral of a smootherstep, so velocity starts at exactly zero), plus a surge on
// the ta-da (the integral of introTada's sin^2 bump)
function spinClock(t) {
  const a = 0.25, T = 2.45, u = clamp01((t - a) / T);
  const v = clamp01((t - INTRO.tada) / 0.7);
  return T * (u ** 6 - 3 * u ** 5 + 2.5 * u ** 4) + Math.max(0, t - a - T)
    + 1.7 * 0.7 * (v / 2 - Math.sin(2 * Math.PI * v) / (4 * Math.PI));
}

export function buildDressing(world) {
  const { scene, camera } = world;
  const root = new THREE.Group();
  root.name = 'dressing';
  scene.add(root);
  const VC = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.57, metalness: 0.15 });
  VC.color.setScalar(0);                          // dark until POWER ON
  const _m = new THREE.Matrix4(), _v = new THREE.Vector3(), _dir = new THREE.Vector3();

  // ---- the two floor gear trains ---------------------------------------------------------
  // spec: [teeth, direction to the NEXT gear in degrees from the outward horizontal]
  const SPEC = [[13, 9.6], [9, 25], [16, -35], [10, 0]];
  function buildTrain(side) {
    const g = new THREE.Group();
    g.position.set(0, 0, Z_TRAIN);
    root.add(g);
    const stat = new Kit(), gears = [];
    let X = (MODULE * SPEC[0][0]) / 2 + MODULE, y = -0.78;          // inner rim sits on the anchor
    SPEC.forEach(([N, deg], i) => {
      const kit = new Kit(), r = (MODULE * N) / 2;
      wheelKit(kit, N, MODULE, { spokes: N > 12 ? 6 : 4, hub: N > 12 ? 0.22 : 0.16 });
      if (i === 2 && side < 0) {                                      // crank pin for the piston
        kit.add(cyZ(0.07, 0.07, 0.3, 10), C.joint, 0.4, 0, 0.2);
      }
      if (i === 2 && side > 0) {                                      // coaxial chain sprocket
        const sk = new Kit(); wheelKit(sk, 12, 0.05, { depth: 0.08, hub: 0.12, rim: 0.05 });
        kit.add(sk.geometry(), C.body, 0, 0, 0.17);
      }
      const mesh = kit.mesh(VC, g);
      mesh.position.set(side * X, y, 0);
      gears.push({ N, r, mesh, x: side * X, y, contact: 0, theta: 0 });
      // bearing stand: an ink A-frame from the floor, light edges, behind the gear
      const foot = Math.max(0.35, r * 0.7);
      for (const s of [-1, 1]) {
        stat.add(strut(side * X + s * foot, FLOOR_Y, side * X, y, 0.12, 0.08), C.ink, 0, 0, -0.16);
        stat.add(strut(side * X + s * foot, FLOOR_Y, side * X, y, 0.035, 0.03), C.body, 0, 0, -0.11);
      }
      stat.add(bx(foot * 2.4, 0.07, 0.4), C.body, side * X, FLOOR_Y + 0.035, -0.1);
      if (i < SPEC.length - 1) {
        const a = THREE.MathUtils.degToRad(deg), d = r + (MODULE * SPEC[i + 1][0]) / 2;
        gears[i].contact = side > 0 ? a : Math.PI - a;                // world-plane direction to the next gear
        X += Math.cos(a) * d; y += Math.sin(a) * d;
      }
    });
    // floor plinth, a hazard-striped kerb in front, two pipe runs with flanges
    const xa = side * 0.05, xb = side * 9.5;
    stat.add(bx(9.5, 0.05, 0.5), C.body, side * 4.75, FLOOR_Y + 0.025, 0);
    hazardBar(stat, xa, side * 6.6, FLOOR_Y + 0.09, 0.42, 0.14, 0.05);
    stat.add(cyX(0.075, 9.4, 10), C.body, (xa + xb) / 2, FLOOR_Y + 0.32, 0.62);
    stat.add(cyX(0.045, 9.4, 8), C.body, (xa + xb) / 2, FLOOR_Y + 0.52, 0.62);
    for (let k = 0; k < 9; k++) {
      stat.add(cyX(0.115, 0.06, 10), C.ink, side * (0.5 + k * 1.1), FLOOR_Y + 0.32, 0.62);
      stat.add(cyX(0.12, 0.02, 10), C.body, side * (0.5 + k * 1.1), FLOOR_Y + 0.32, 0.62);
    }
    return { g, gears, stat, side };
  }
  const L = buildTrain(-1), R = buildTrain(1);

  // left: the big gear cranks a vertical piston pump, with a gauge bank beside it
  const crank = L.gears[2], ROD = 1.15, RC = 0.4;
  {
    const s = L.stat, x = crank.x, top = crank.y + ROD + RC;
    for (const e of [-1, 1]) {                                       // crosshead guide rails
      s.add(bx(0.05, 1.25, 0.06), C.body, x + e * 0.2, crank.y + 1.15, 0.2);
    }
    s.add(cyY(0.27, 0.27, 0.95, 16), C.body, x, top + 0.55, 0.2);    // cylinder
    for (const yy of [0.12, 0.5, 0.88]) s.add(cyY(0.3, 0.3, 0.07, 16), C.ink, x, top + 0.08 + yy, 0.2);
    s.add(cyY(0.33, 0.33, 0.09, 16), C.joint, x, top + 1.06, 0.2);   // cylinder head
    const gx = x + 0.82, gy = FLOOR_Y + 2.55;
    s.add(strut(x + 0.3, top + 0.7, gx, top + 0.7, 0.06, 0.06), C.body, 0, 0, 0.2);        // feed pipe
    s.add(strut(gx, top + 0.73, gx, gy + 0.3, 0.06, 0.06), C.body, 0, 0, 0.2);
    // gauge bank on a post
    s.add(bx(0.07, 2.4, 0.07), C.body, gx, FLOOR_Y + 1.2, 0.12);
    s.add(bx(0.84, 0.5, 0.05), C.ink, gx, gy, 0.2);
    for (const [w, h, ox, oy] of [[0.88, 0.035, 0, 0.26], [0.88, 0.035, 0, -0.26], [0.035, 0.55, 0.44, 0], [0.035, 0.55, -0.44, 0]]) {
      s.add(bx(w, h, 0.07), C.body, gx + ox, gy + oy, 0.2);
    }
    L.gauges = [-0.27, 0, 0.27].map((ox, k) => {
      const r = k === 1 ? 0.13 : 0.105;
      s.add(cyZ(r, r, 0.03, 18), C.body, gx + ox, gy, 0.24);
      s.add(new THREE.TorusGeometry(r, 0.018, 5, 18), C.ink, gx + ox, gy, 0.26);
      return { x: gx + ox, y: gy, r, seed: k };
    });
  }
  const rod = (() => { const k = new Kit();
    k.add(bx(0.075, ROD, 0.05), C.body, 0, ROD / 2, 0);
    k.add(cyZ(0.1, 0.1, 0.07, 12), C.body); k.add(cyZ(0.1, 0.1, 0.07, 12), C.body, 0, ROD, 0);
    const m = k.mesh(VC, L.g); m.position.z = 0.24; return m; })();
  const cross = (() => { const k = new Kit();
    k.add(bx(0.36, 0.2, 0.12), C.joint); k.add(cyZ(0.05, 0.05, 0.16, 8), C.ink);
    k.add(cyY(0.045, 0.045, 1.0, 8), C.body, 0, 0.55, 0);             // piston rod, up into the cylinder
    const m = k.mesh(VC, L.g); m.position.z = 0.2; return m; })();

  // right: a chain drive climbs from the big gear's sprocket to an idler on a post
  const drive = R.gears[2], SPR = 0.3, cB = new THREE.Vector2(drive.x + 0.3, 2.3);
  {
    const s = R.stat;
    s.add(bx(0.09, cB.y - FLOOR_Y + 0.3, 0.09), C.body, cB.x + 0.22, (cB.y + FLOOR_Y) / 2 + 0.15, -0.05);
    s.add(bx(0.4, 0.09, 0.09), C.body, cB.x + 0.05, cB.y, -0.05);
    // vent louvre on the post
    const vx = cB.x - 0.85, vy = FLOOR_Y + 2.35;
    s.add(bx(0.8, 0.62, 0.05), C.ink, vx, vy, 0.1);
    for (let k = 0; k < 5; k++) s.add(bx(0.7, 0.05, 0.1).rotateX(0.6), C.body, vx, vy - 0.22 + k * 0.11, 0.14);
    for (const e of [-1, 1]) s.add(bx(0.04, 0.66, 0.08), C.body, vx + e * 0.4, vy, 0.12);
    s.add(bx(0.07, 2.1, 0.07), C.body, vx, FLOOR_Y + 1.05, 0.04);
  }
  const idler = (() => { const k = new Kit(); wheelKit(k, 12, 0.05, { depth: 0.08, hub: 0.12, rim: 0.05 });
    const m = k.mesh(VC, R.g); m.position.set(cB.x, cB.y, 0.17); return m; })();
  const cA = new THREE.Vector2(drive.x, drive.y), chainU = cB.clone().sub(cA), CH_D = chainU.length();
  chainU.divideScalar(CH_D);
  const chainN = new THREE.Vector2(-chainU.y, chainU.x), CH_LEN = 2 * CH_D + 2 * Math.PI * SPR;
  const LINKS = Math.round(CH_LEN / 0.15);
  const chain = new THREE.InstancedMesh((() => { const k = new Kit();
    k.add(bx(0.1, 0.05, 0.04), C.body); k.add(cyZ(0.022, 0.022, 0.06, 6), C.ink, 0.045, 0, 0); return k.geometry(); })(), VC, LINKS);
  chain.frustumCulled = false; chain.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  R.g.add(chain);
  function chainPoint(s, out) {                   // -> x, y, tangent angle on the loop A -> B (+n side) -> back
    s = ((s % CH_LEN) + CH_LEN) % CH_LEN;
    const base = Math.atan2(chainN.y, chainN.x), arc = Math.PI * SPR;
    if (s < CH_D) return out.set(cA.x + chainN.x * SPR + chainU.x * s, cA.y + chainN.y * SPR + chainU.y * s, base - Math.PI / 2);
    s -= CH_D;
    if (s < arc) { const a = base - s / SPR; return out.set(cB.x + Math.cos(a) * SPR, cB.y + Math.sin(a) * SPR, a - Math.PI / 2); }
    s -= arc;
    if (s < CH_D) return out.set(cB.x - chainN.x * SPR - chainU.x * s, cB.y - chainN.y * SPR - chainU.y * s, base + Math.PI / 2);
    s -= CH_D;
    const a = base - Math.PI - s / SPR;
    return out.set(cA.x + Math.cos(a) * SPR, cA.y + Math.sin(a) * SPR, a - Math.PI / 2);
  }
  L.stat.mesh(VC, L.g); R.stat.mesh(VC, R.g);

  // gauge needles: one instanced mesh
  const needles = new THREE.InstancedMesh(new Kit().add(bx(0.02, 0.13, 0.012), C.ink, 0, 0.045, 0).geometry(), VC, L.gauges.length);
  needles.frustumCulled = false; needles.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  L.g.add(needles);

  // valve wheels on the pipe runs: they get a quarter turn now and then
  const valves = [L, R].map((T, k) => {
    const kit = new Kit();
    kit.add(new THREE.TorusGeometry(0.2, 0.03, 6, 18), C.joint);
    for (let i = 0; i < 4; i++) kit.add(strut(0, 0, 0.2 * Math.cos(i * 1.571), 0.2 * Math.sin(i * 1.571), 0.035, 0.03), C.joint);
    kit.add(cyZ(0.05, 0.05, 0.16, 8), C.ink, 0, 0, -0.06);
    const m = kit.mesh(VC, T.g);
    m.position.set(T.side * (1.05 + k * 1.1), FLOOR_Y + 0.32, 0.78);
    return m;
  });

  // ---- top corners: a lattice girder, a belt-driven flywheel, slack cables ------------------
  const corners = [-1, 1].map((side) => {
    const g = new THREE.Group();
    g.position.z = Z_CORNER;
    root.add(g);
    const stat = new Kit(), ang = Math.atan2(3.45, 4.6), len = 6.2;
    const at = (d, off = 0) => new THREE.Vector2(-side * (-0.5 + Math.cos(ang) * d - Math.sin(ang) * off), 2.9 + Math.sin(ang) * d + Math.cos(ang) * off);
    stat.xf = new THREE.Matrix4().makeRotationZ(side > 0 ? Math.PI - ang : ang).setPosition(side * 0.5, 2.9, 0);
    lattice(stat, len, 0.5);
    stat.xf = null;
    const cF = at(1.75, -0.95), cP = at(3.6, -0.1), RF = 0.88, RP = 0.3;
    for (const c of [cF, cP]) {                                     // hangers from the girder
      const foot = at(c === cF ? 1.75 : 3.6, -0.25);
      stat.add(strut(foot.x, foot.y, c.x, c.y, 0.07, 0.07), C.body, 0, 0, -0.12);
    }
    // open flat belt: the two outer tangents of the wheels
    const phi = Math.atan2(cP.y - cF.y, cP.x - cF.x), d = cF.distanceTo(cP), off = Math.acos((RF - RP) / d);
    for (const s of [-1, 1]) {
      const a = phi + s * off;
      stat.add(strut(cF.x + RF * Math.cos(a), cF.y + RF * Math.sin(a), cP.x + RP * Math.cos(a), cP.y + RP * Math.sin(a), 0.035, 0.1), C.body);
    }
    for (let k = 0; k < 2; k++) {                                   // slack cables over the corner
      const a = at(5.6 - k * 1.2, 0.3), b = new THREE.Vector2(side * (1.2 + k * 0.2), 4.7 - k * 0.9), pts = [];
      for (let i = 0; i <= 8; i++) { const u = i / 8;
        pts.push(new THREE.Vector3(a.x + (b.x - a.x) * u, a.y + (b.y - a.y) * u - Math.sin(Math.PI * u) * (0.7 + k * 0.3), 0.1 + k * 0.1)); }
      stat.add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 18, 0.022, 5), C.body);
    }
    stat.mesh(VC, g);
    const fk = new Kit(); wheelKit(fk, 14, 0.12, { toothed: false, depth: 0.14, spokes: 6, hub: 0.17, rim: 0.12 });
    fk.add(bx(0.3, 0.2, 0.16), C.ink, RF * 0.62, 0, 0);              // counterweight: makes the turn visible
    const fly = fk.mesh(VC, g); fly.position.set(cF.x, cF.y, 0);
    const pk = new Kit(); wheelKit(pk, 8, 0.06, { toothed: false, depth: 0.14, spokes: 3, hub: 0.1, rim: 0.06 });
    const pul = pk.mesh(VC, g); pul.position.set(cP.x, cP.y, 0);
    return { g, side, fly, pul, ratio: RF / RP };
  });

  // ---- foreground: riveted I-beams cropping the bottom corners -------------------------------
  const fore = [-1, 1].map((side) => {
    const g = new THREE.Group();
    g.position.z = Z_FORE;
    root.add(g);
    const kit = new Kit(), a = Math.atan2(-0.95, 2.4);
    kit.xf = new THREE.Matrix4().makeRotationZ(side > 0 ? Math.PI - a : a).setPosition(side * 0.5, 0.36, 0);
    ibeam(kit, 3.4, 0.52, 0.14);
    kit.mesh(VC, g);
    return { g, side };
  });

  // ---- screen-space layout: x follows the live frame, y and z are the room's ---------------
  let layoutKey = '';
  const halfW = (z) => (camera.position.z - z) * TAN_HALF_FOV * camera.aspect;
  function relayout() {
    // trains start just outside ENTER_CLEAR — but never so far in that the gauge bank
    // (2.2 outboard of the anchor, at title height) crosses the title's margin on an
    // ultrawide screen; likewise the flywheel's inner rim (2.47 in from the corner) on
    // a narrow one, where the corner cluster slides out past the edge instead
    const hwT = halfW(Z_TRAIN), hwC = halfW(Z_CORNER);
    const anchor = Math.max(0.28 * hwT, 0.64 * hwT - 2.2);
    L.g.position.x = -anchor; R.g.position.x = anchor;
    for (const c of corners) c.g.position.x = c.side * Math.max(hwC + 0.05, 0.64 * hwC + 2.47);
    for (const f of fore) f.g.position.x = f.side * halfW(Z_FORE);
  }

  // ---- per frame ------------------------------------------------------------------------------
  const W0 = 0.55;                                // inner gear, rad/s at full speed
  let lastJam = -1, lastClick = -Infinity;
  const _p = new THREE.Vector3();
  function runTrain(T, theta0) {
    const G = T.gears;
    G[0].theta = theta0;
    for (let i = 1; i < G.length; i++) {          // exact meshing: a tooth of i meets a gap of i+1
      const a = G[i - 1].contact, Ni = G[i - 1].N, Nj = G[i].N;
      G[i].theta = a + Math.PI - Math.PI / Nj - (Ni / Nj) * (G[i - 1].theta - a);
    }
    for (const gear of G) gear.mesh.rotation.z = gear.theta;
  }

  function update(world) {
    const { t } = world;
    const key = camera.aspect.toFixed(3) + '|' + camera.position.z.toFixed(2);
    if (key !== layoutKey) { layoutKey = key; relayout(); }
    VC.color.setScalar(smoother(INTRO.powerOn[0] + 0.15, INTRO.powerOn[1] + 0.2, t));

    const S = spinClock(t);
    // the right-hand train jams now and then: it falls behind (all but stalls), judders,
    // then catches up. k and its slope are zero at both ends, so nothing steps.
    const jt = t - INTRO.work - 4, jn = Math.floor(jt / 11.5), tau = jt - jn * 11.5 - hash(jn, 71) * 3;
    let jam = 0;
    if (jn >= 0 && tau > 0 && tau < 1.5) {
      const k = Math.sin((Math.PI * tau) / 1.5) ** 2;
      jam = -W0 * 1.5 * 0.3 * k + Math.sin(tau * 52) * 0.03 * k;
      if (tau > 0.3 && lastJam !== jn) {
        lastJam = jn;
        const a = R.gears[0], c = a.contact;
        _p.set(a.x + Math.cos(c) * a.r, a.y + Math.sin(c) * a.r, 0.1).applyMatrix4(R.g.matrixWorld);
        if (world.sparks) world.sparks.emit(_p, _dir.set(0.3, 1, 0.6), 14, 2.2, 0.9);
      }
    }
    runTrain(L, -W0 * S);
    runTrain(R, W0 * S + jam);

    // crank -> connecting rod -> crosshead
    const px = crank.x + RC * Math.cos(crank.theta), py = crank.y + RC * Math.sin(crank.theta);
    const hy = py + Math.sqrt(ROD * ROD - (px - crank.x) ** 2);
    rod.position.set(px, py, rod.position.z);
    rod.rotation.z = Math.atan2(hy - py, crank.x - px) - Math.PI / 2;
    cross.position.set(crank.x, hy, cross.position.z);

    // chain: links ride the loop at the sprocket's rim speed
    idler.rotation.z = drive.theta;
    const s0 = -SPR * drive.theta, pitch = CH_LEN / LINKS;
    for (let i = 0; i < LINKS; i++) {
      chainPoint(s0 + i * pitch, _v);
      chain.setMatrixAt(i, _m.makeRotationZ(_v.z).setPosition(_v.x, _v.y, 0.2));
    }
    chain.instanceMatrix.needsUpdate = true;

    for (const c of corners) {
      c.fly.rotation.z = c.side * 0.5 * S;
      c.pul.rotation.z = c.side * 0.5 * S * c.ratio;
    }
    valves.forEach((m, k) => {
      const n = (t + k * 3.1) / 6.3, i = Math.floor(n);
      m.rotation.z = (i + smoother(0, 0.16, n - i)) * (Math.PI / 2) * (hash(k, 9) > 0.5 ? 1 : -1);
    });

    // gauges: a slow wander, a nervous twitch, the piston's pulse — and a spike when you click
    if (world.click.t !== lastClick) lastClick = world.click.t;
    const since = t - lastClick, spike = since >= 0 ? Math.exp(-since * 2.2) * (1 - Math.exp(-since * 30)) : 0;
    L.gauges.forEach((gg, k) => {
      const tw = Math.floor(t * 3 + k * 1.7);
      const a = 0.9 - 0.5 * Math.sin(t * 0.31 + k * 2.1) - 0.12 * hash(tw, k + 80) * (hash(tw, k + 81) > 0.6 ? 1 : 0)
        - (k === 1 ? 0.25 * Math.sin(crank.theta) : 0) - 1.7 * spike * Math.cos(since * 14 + k) ** 2;
      needles.setMatrixAt(k, _m.makeRotationZ(a * clamp01(S)).setPosition(gg.x, gg.y, 0.275));
    });
    needles.instanceMatrix.needsUpdate = true;
  }

  return { update, root };
}
