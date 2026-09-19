// tentacles.js — the factory's mechanical tentacles: a WELDER that works the title
// letter by letter, a POKER that sneaks up from under the word to jab letters, an
// INSPECTOR that peeks over the title and watches the line (and you), and four camera
// tentacles far back in the fog. Each is fully rigged — a nested THREE.Group per
// vertebra, a real kinematic chain — solved every frame, and every behaviour is a
// function of world.t (plus the pointer), so the show reproduces.
//
// They share the house intro (layout.js INTRO): each enters from its nearest screen
// edge on the sine wave, snaking as it comes, everyone closes in around the title
// while GITRL drops, a flourish on the ta-da, then the work routines start.
//
// GITRL STAYS READABLE. Nothing here parks in front of a letter: the welder rests
// ABOVE the word and works only on letter tops and the outer strokes of G and L, with
// its body hanging from a hoist so it never drapes across the word; the poker lives
// BELOW the word and crosses a letter only for the jab itself.
//
// Look: the vertebrae speak snakeArms.js's language (orange knuckle, turned body,
// dark collars, cable runs, warning collars, bolt heads) in its exact materials,
// merged per joint (mech.js Rigid). Anything that looks at you wears a heads.js head
// and finds you with layout.js gazePoint.

import * as THREE from 'three';
import {
  MAT, Rigid, hash, clamp01, lerp, smooth, smoother, bump,
  Spring, Spring3, aimAt,
} from './mech.js';
import {
  WELDER_ROOT, INSPECTOR_ROOT, POKER_ROOT, BG_ROOTS, BELT, TITLE, TITLE_CLEAR, FRONT_RAIL,
  INTRO, introEnter, introConverge, introTada, introWorking, presentPoint, gazePoint,
} from './layout.js';
import { makeHead, HEAD_KINDS } from './heads.js';

const TAU = Math.PI * 2;
const UP = new THREE.Vector3(0, 1, 0);
const AX_X = new THREE.Vector3(1, 0, 0), AX_Y = new THREE.Vector3(0, 1, 0);
const IDENT = new THREE.Quaternion();
const ease = (dt, rate) => 1 - Math.exp(-dt * rate);   // frame-rate-independent slerp
const HALF_W = 5.44;                                   // frame half-width at z = 0

// first kind heads.js actually offers, else pomme's pod
const kindOf = (...prefs) => prefs.find((k) => HEAD_KINDS.includes(k)) || 'pod';

// scratch
const _d = new THREE.Vector3(), _b = new THREE.Vector3(), _v = new THREE.Vector3();
const _pq = new THREE.Quaternion(), _inv = new THREE.Quaternion(), _q = new THREE.Quaternion();

// ---- the chain -------------------------------------------------------------------
// Vertebra i: ball knuckle at the joint (a universal joint: this chain bends both
// ways, unlike snakeArms' single-axis clevis), a cast or turned body, two dark collars, three
// cable runs braided a step per vertebra, a warning collar every fourth, bolt heads.
// `thin` < 1 slims the last third hard, so a chain working near the title is a line,
// not a wall.
function buildChain({ segments, segLen, baseW, tipW, thin = 1, braid = 0.42 }) {
  const root = new THREE.Group();
  const joints = [];
  let parent = root;
  for (let i = 0; i < segments; i++) {
    const f = i / (segments - 1);
    let w = lerp(baseW, tipW, f);
    if (f > 0.66) w *= lerp(1, thin, smooth(0.66, 0.85, f));
    const j = new THREE.Group();
    j.position.y = i === 0 ? 0 : segLen;
    const r = new Rigid();
    r.add(new THREE.SphereGeometry(w * 0.5, 12, 9), MAT.joint);
    // link body: alternating box / cylinder profiles, as snakeArms does ("identical
    // links read as toy chain") — and the flat faces are what catch the key light and
    // print as paper under the pass; an all-round chain prints as a dark tube
    if (i % 2 === 0) {
      r.add(new THREE.BoxGeometry(w * 0.86, segLen * 0.62, w * 0.86).rotateY(i * braid),
        MAT.body, 0, segLen * 0.5, 0);
    } else {
      r.add(new THREE.CylinderGeometry(w * 0.4, w * 0.46, segLen * 0.62, 12),
        MAT.body, 0, segLen * 0.5, 0);
    }
    for (const y of [0.2, 0.8]) {
      r.add(new THREE.TorusGeometry(w * 0.45, w * 0.08, 6, 14).rotateX(Math.PI / 2),
        MAT.dark, 0, segLen * y, 0);
    }
    for (let k = 0; k < 3; k++) {
      const a = k * TAU / 3 + i * braid;
      r.add(new THREE.CylinderGeometry(w * 0.06, w * 0.06, segLen * 0.96, 6),
        MAT.dark, Math.cos(a) * w * 0.5, segLen * 0.5, Math.sin(a) * w * 0.5);
    }
    if (i % 4 === 3) {
      r.add(new THREE.CylinderGeometry(w * 0.52, w * 0.52, w * 0.2, 12),
        MAT.joint, 0, segLen * 0.5, 0);
    }
    for (const s of [-1, 1]) {
      r.add(new THREE.CylinderGeometry(w * 0.08, w * 0.08, w * 0.1, 6).rotateZ(Math.PI / 2),
        MAT.dark, s * w * 0.43, segLen * 0.36, 0);
    }
    r.into(j);
    parent.add(j);
    joints.push(j);
    parent = j;
  }
  const tip = new THREE.Group();
  tip.position.y = segLen;
  parent.add(tip);
  // the tool rides a ball on the tip (snakeArms' gimbal trick: any relative angle
  // between chain and tool reads as a joint, never as detachment)
  const ballR = tipW * thin * 0.62;
  new Rigid().add(new THREE.SphereGeometry(ballR, 12, 9), MAT.joint).into(tip);
  return { root, joints, tip, segLen, n: segments, ballR };
}

const _ch = new THREE.Vector3(), _bu = new THREE.Vector3(), _b2 = new THREE.Vector3();

// ---- the solver ------------------------------------------------------------------
// Where does a tentacle put its spare length? That is the whole problem, and an
// iterative IK answers it badly (FABRIK folds the slack into a hook at the tip). So
// the SHAPE is analytic: the chord from root to goal plus a half-sine bulge whose
// amplitude is solved so the arc length is exactly the chain's, bulging in a direction
// the caller chooses — which is also how a body gets routed AROUND the word rather
// than across it. A travelling writhe (and the intro's snake) ride on that curve.
//
// The vertebrae do not snap to the curve. Each keeps most of last frame's velocity
// (WHIP: the body swings wide when the carriage moves and the tail is still arriving
// after the head has stopped) and is drawn toward its place on the curve. A few passes
// of FABRIK then restore exact link lengths and pin the tip to the goal.
//
// The curve needs a bend of about 2 sqrt(slack / chord) (pi / chord) segLen per joint.
// Keep that under maxBend BY DESIGN (hoists, chain lengths): past it the pose simply
// falls short of the goal, gracefully — it is never solved for.
//
// Orientation is rebuilt root->tip in each parent's frame — setFromUnitVectors on the
// parent-local direction, so a hanging chain (local dir ~ +y) never meets the
// anti-parallel singularity.
const SHAPE_N = 40;
class Solver {
  constructor(chain, { maxBend = 0.5, rootBend = 1.45, iters = 4 } = {}) {
    this.c = chain;
    this.maxBend = maxBend;
    this.rootBend = rootBend;
    this.iters = iters;
    const mk = () => Array.from({ length: chain.n + 1 }, () => new THREE.Vector3());
    this.p = mk(); this.prev = mk(); this.cv = mk();
    this.cum = new Float32Array(SHAPE_N + 1);
    this.warm = false;
  }

  // fills this.cv: the ideal centreline, sampled at the joints (equal arc length)
  shape(rootP, goal, o) {
    const { n, segLen: L } = this.c, cv = this.cv, cum = this.cum, len = n * L;
    _ch.subVectors(goal, rootP);
    const d = Math.max(_ch.length(), 1e-4);
    _ch.divideScalar(d);
    // bulge direction: the caller's preference (plus gravity), made perpendicular
    _bu.copy(o.bulge).addScaledVector(UP, -o.sag).addScaledVector(_ch, -_bu.dot(_ch));
    if (_bu.lengthSq() < 1e-6) _bu.set(_ch.y, -_ch.x, 0.3).addScaledVector(_ch, -_bu.dot(_ch));
    _bu.normalize();
    _b2.crossVectors(_ch, _bu);
    // amplitude A with  integral sqrt(d^2 + (pi A cos(pi u))^2) du = len   (bisection)
    let A = 0;
    if (d < len * 0.999) {
      let lo = 0, hi = len;
      for (let it = 0; it < 14; it++) {
        A = (lo + hi) / 2;
        let s = 0;
        for (let k = 0; k < 16; k++) s += Math.hypot(d, Math.PI * A * Math.cos(Math.PI * (k + 0.5) / 16));
        if (s / 16 < len) lo = A; else hi = A;
      }
    }
    this.A = A; this.d = d;
    cum[0] = 0;
    for (let k = 0; k < SHAPE_N; k++) {
      cum[k + 1] = cum[k] + Math.hypot(d, Math.PI * A * Math.cos(Math.PI * (k + 0.5) / SHAPE_N)) / SHAPE_N;
    }
    const total = cum[SHAPE_N];
    let k = 0;
    for (let i = 0; i <= n; i++) {
      const want = Math.min(i * L, total);
      while (k < SHAPE_N - 1 && cum[k + 1] < want) k++;
      const u = d >= len * 0.999 ? (i * L) / d          // out of reach: a straight line
        : (k + (want - cum[k]) / Math.max(cum[k + 1] - cum[k], 1e-9)) / SHAPE_N;
      const env = Math.sin(Math.PI * Math.min(u, 1));
      const ph = o.t * o.writheSpeed - i * 0.55 + o.phase;
      cv[i].copy(rootP).addScaledVector(_ch, d * u)
        .addScaledVector(_bu, A * env + o.writhe * env * Math.sin(ph))
        .addScaledVector(_b2, o.writhe * 0.8 * env * Math.cos(ph * 0.83 + 1.1));
      if (o.snakeAmp) cv[i].addScaledVector(o.snakeAxis, o.snakeAmp * env * Math.sin(o.t * 7.5 - i * 0.95));
    }
  }

  solve(goal, o) {
    const { root, joints, tip, n, segLen: L } = this.c;
    const p = this.p, prev = this.prev, cv = this.cv, dt = o.dt;
    root.parent.updateMatrixWorld(true);         // the mount moved this frame
    const keep = this.warm ? o.whip * Math.exp(-dt * 5) : 0;
    for (let i = 0; i <= n; i++) {
      _v.setFromMatrixPosition((i < n ? joints[i] : tip).matrixWorld);   // where it IS
      p[i].copy(_v);
      if (i > 0) p[i].add(_d.subVectors(_v, prev[i]).clampLength(0, 0.3).multiplyScalar(keep));
      prev[i].copy(_v);
    }
    this.shape(p[0], goal, o);
    for (let i = 1; i <= n; i++) {
      p[i].lerp(cv[i], this.warm ? ease(dt, o.follow) : 1);
    }
    this.warm = true;

    // a few FABRIK passes restore exact link lengths and pin the tip. The seed is
    // already the right SHAPE, so this only fine-tunes (FABRIK alone, asked to find a
    // shape for a slack chain, folds the slack into a hook at the tip).
    _b.copy(p[0]);
    for (let it = 0; it < this.iters; it++) {
      p[n].copy(goal);
      for (let i = n - 1; i >= 0; i--) {
        _d.subVectors(p[i], p[i + 1]);
        p[i].copy(p[i + 1]).addScaledVector(_d, L / (_d.length() || 1e-6));
      }
      p[0].copy(_b);
      for (let i = 1; i <= n; i++) {
        _d.subVectors(p[i], p[i - 1]);
        p[i].copy(p[i - 1]).addScaledVector(_d, L / (_d.length() || 1e-6));
      }
      if (p[n].distanceToSquared(goal) < 1e-6) break;
    }

    // joints: bend-limited, eased, and SPIN-CAPPED — no vertebra may turn faster than
    // maxSpin, so whatever the solve does, nothing on screen can teleport
    root.getWorldQuaternion(_pq);
    const cap = o.maxSpin * dt;
    for (let i = 0; i < n; i++) {
      _d.subVectors(p[i + 1], p[i]).normalize();
      _d.applyQuaternion(_inv.copy(_pq).invert());
      _q.setFromUnitVectors(UP, _d);
      const lim = i === 0 ? this.rootBend : this.maxBend;
      const ang = 2 * Math.acos(Math.min(1, Math.abs(_q.w)));
      // (not slerpQuaternions(IDENT, _q, ..): it copies its first argument into `this`
      // before reading the second, so aliasing _q silently yields identity)
      if (ang > lim) _q.slerp(IDENT, 1 - lim / ang);
      const turn = joints[i].quaternion.angleTo(_q);
      joints[i].quaternion.slerp(_q, Math.min(ease(dt, o.lag), turn > 1e-5 ? cap / turn : 1));
      _pq.multiply(joints[i].quaternion);
    }
  }

  // largest local bend past the root joint (radians) — for the probe
  maxLocalBend() {
    let m = 0;
    for (let i = 1; i < this.c.n; i++) {
      m = Math.max(m, 2 * Math.acos(Math.min(1, Math.abs(this.c.joints[i].quaternion.w))));
    }
    return m;
  }
}

// ---- a tentacle: mount (carriage frame) + chain + solver + head --------------------
function makeTentacle(scene, { at, hang, segments, segLen, baseW, tipW, thin, maxBend, lag = 14 }) {
  const mount = new THREE.Group();
  mount.position.copy(at);
  scene.add(mount);
  const chain = buildChain({ segments, segLen, baseW, tipW, thin });
  if (hang) chain.root.rotation.z = Math.PI;       // grow downward, as snakeArms does
  mount.add(chain.root);
  const solver = new Solver(chain, { maxBend });
  const head = new THREE.Group();                  // world-aimed (+z = forward)
  chain.tip.add(head);
  head.position.y = chain.ballR;
  const inner = new THREE.Group();                 // roll / nod on top of the aim
  head.add(inner);
  mount.traverse((o) => { o.frustumCulled = false; });
  mount.updateMatrixWorld(true);
  const tipPos = chain.tip.getWorldPosition(new THREE.Vector3());
  return {
    mount, chain, solver, head, inner, lag, reach: segLen * segments,
    goal: new Spring3(tipPos, 20, 0.7),
    target: tipPos.clone(), look: tipPos.clone().add(new THREE.Vector3(0, 0, 3)),
    headPos: new THREE.Vector3(),
  };
}

// bulge: which way the spare length bows (world; made perpendicular to the chord);
// sag: how much gravity pulls that bow down; writhe: sway amplitude (u); whip: 0..1
// momentum the body keeps; follow: how hard (1/s) it is drawn to its ideal curve
const BULGE_BACK = new THREE.Vector3(0, 0, -1);
function drive(A, world, { k, zeta, bulge = BULGE_BACK, sag = 0.3, writhe = 0.1,
  writheSpeed = 1.3, whip = 0.85, follow = 14, phase = 0, aimRate = 9, lag = A.lag,
  maxSpin = 7, snakeAmp = 0, snakeAxis = AX_X }) {
  A.goal.step(A.target, world.dt, k, zeta);
  A.solver.solve(A.goal.x, { t: world.t, dt: world.dt, bulge, sag, writhe, writheSpeed,
    whip, follow, phase, lag, maxSpin, snakeAmp, snakeAxis });
  aimAt(A.head, A.look, ease(world.dt, aimRate));
  A.head.getWorldPosition(A.headPos);
}

// A hoist: the mount rides up and down (off-frame) so the chain stays ~92% extended
// whatever the tip is doing. A slack chain has to put its spare length somewhere, and
// the only somewhere near the title is in front of the word.
function hoistY(A, dir, limitY) {
  const D = 0.92 * A.reach;
  const dx = A.goal.x.x - A.mount.position.x, dz = A.goal.x.z - A.mount.position.z;
  const h = Math.sqrt(Math.max(D * D - dx * dx - dz * dz, 1));
  return dir > 0 ? Math.max(limitY, A.goal.x.y + h) : Math.min(limitY, A.goal.x.y - h);
}

// The close-in: every machine reaches for its own point on the ring around the title
// and looks at the letters. introConverge dips NEGATIVE first, which is the pull-back
// — so a negative weight pushes the same point outward instead of blending to it.
const _p = new THREE.Vector3();
function closeIn(A, t) {
  const conv = introConverge(t);
  if (conv === 0) return 0;
  presentPoint(A.goal.x, _p);
  if (conv < 0) _p.sub(TITLE.center).multiplyScalar(1.3).add(TITLE.center);
  A.target.lerp(_p, clamp01(Math.abs(conv)));
  A.look.lerp(TITLE.center, clamp01(Math.abs(conv)) * 0.9);
  return conv;
}

// pointer distance to a world point, on screen, in frame half-heights
const _proj = new THREE.Vector3();
function screenDist(w, pos) {
  _proj.copy(pos).project(w.camera);
  return Math.hypot((_proj.x - w.cursor.ndc.x) * w.camera.aspect, _proj.y - w.cursor.ndc.y);
}

function blinker(seed) {
  const P = 3.4 + 2.2 * hash(seed, 1), off = P * hash(seed, 2);
  return (t) => {
    const c = Math.floor((t + off) / P), u = (t + off) - c * P;
    let shut = bump(0, 0.17, u);
    if (hash(c, seed + 9) > 0.7) shut = Math.max(shut, bump(0.26, 0.43, u));   // double blink
    return 1 - shut;
  };
}

// ---- the welder's torch (a tool, not a face; built facing +z) ----------------------
function torchHead(parent) {
  const g = new THREE.Group();
  parent.add(g);
  const r = new Rigid();
  r.add(new THREE.CylinderGeometry(0.12, 0.12, 0.06, 14).rotateX(Math.PI / 2), MAT.joint, 0, 0, -0.04);
  r.add(new THREE.CylinderGeometry(0.1, 0.12, 0.32, 14).rotateX(Math.PI / 2), MAT.lifted, 0, 0, 0.13);
  r.add(new THREE.TorusGeometry(0.115, 0.026, 6, 16), MAT.dark, 0, 0, 0.28);
  r.add(new THREE.CylinderGeometry(0.036, 0.07, 0.22, 10).rotateX(Math.PI / 2), MAT.dark, 0, 0, 0.39);
  r.add(new THREE.BoxGeometry(0.045, 0.09, 0.2), MAT.dark, 0.115, 0, 0.08);     // gas valve
  r.add(new THREE.TorusGeometry(0.1, 0.024, 6, 10, Math.PI).rotateY(Math.PI / 2),
    MAT.dark, -0.1, 0, -0.02);                                                   // hose loop
  r.into(g);
  const hot = new THREE.MeshStandardMaterial({
    color: '#ffe9c8', emissive: '#ffd08a', emissiveIntensity: 0.1, roughness: 0.4 });
  const flame = new THREE.Mesh(new THREE.SphereGeometry(0.045, 10, 8), hot);
  flame.position.z = 0.51;
  g.add(flame);
  // welding visor on a hinge above the nozzle: up when idle, DOWN to weld
  const visor = new THREE.Group();
  visor.position.set(0, 0.125, 0.18);
  g.add(visor);
  new Rigid()
    .add(new THREE.BoxGeometry(0.25, 0.03, 0.3), MAT.dark, 0, 0, 0.13)
    .add(new THREE.BoxGeometry(0.18, 0.02, 0.07), MAT.lens, 0, 0.018, 0.18)
    .add(new THREE.CylinderGeometry(0.026, 0.026, 0.29, 8).rotateZ(Math.PI / 2), MAT.joint)
    .into(visor);
  g.traverse((o) => { o.frustumCulled = false; });
  return { g, hot, flame, visor, visorS: new Spring(-0.9, 70, 0.45) };
}

// Straddles a rail of ~0.35 x 0.4 section whose centre sits 0.2 above the mount.
function trolley(mount) {
  const r = new Rigid();
  r.add(new THREE.BoxGeometry(0.46, 0.2, 0.46), MAT.dark, 0, 0.02, 0);
  r.add(new THREE.CylinderGeometry(0.2, 0.2, 0.1, 14), MAT.joint, 0, -0.1, 0);
  for (const s of [-1, 1]) {
    r.add(new THREE.BoxGeometry(0.92, 0.64, 0.07), MAT.body, 0, 0.36, s * 0.26);
    for (const wx of [-0.28, 0.28]) {
      r.add(new THREE.CylinderGeometry(0.09, 0.09, 0.08, 12).rotateX(Math.PI / 2),
        MAT.dark, wx, 0.48, s * 0.12);
      r.add(new THREE.CylinderGeometry(0.04, 0.04, 0.09, 8).rotateX(Math.PI / 2),
        MAT.joint, wx, 0.48, s * 0.3);
    }
  }
  r.add(new THREE.BoxGeometry(0.96, 0.07, 0.62), MAT.body, 0, 0.7, 0);
  r.add(new THREE.BoxGeometry(0.3, 0.16, 0.2), MAT.dark, 0.2, 0.8, 0);      // drive motor
  r.add(new THREE.SphereGeometry(0.04, 8, 6), MAT.glow, -0.36, 0.76, 0.2);
  r.into(mount);
}

// =====================================================================================
export function buildTentacles(world) {
  const scene = world.scene;
  const _l = new THREE.Vector3(), _n = new THREE.Vector3();
  const _s = new THREE.Vector3(), _c = new THREE.Vector3(), _k = new THREE.Vector3();
  const _gz = new THREE.Vector3();

  let throwT = -Infinity;
  world.on('throw', () => { throwT = world.t; });

  const cursorRecent = (w, s) => w.cursor.seen && w.t - w.cursor.movedAt < s;
  const REST_Y = TITLE_CLEAR.y1 + 0.6;               // above the word, inside the frame

  // ---------------------------------------------------------------- WELDER
  // Hangs from a hoist on the front rail (both above the frame, never seen). Works
  // letter TOPS and the OUTER strokes of G and L; rests above the word between jobs.
  const W = makeTentacle(scene, { at: WELDER_ROOT, hang: true, segments: 18,
    segLen: 0.3, baseW: 0.3, tipW: 0.17, thin: 0.55, maxBend: 0.42, lag: 42 });
  W.tool = torchHead(W.inner);
  W.carX = new Spring(WELDER_ROOT.x, 6, 0.9);
  W.hoist = new Spring(FRONT_RAIL.y + 2.6, 140, 1);
  W.sparkAcc = 0;
  W.bulge = new THREE.Vector3(-1, 0, 0.35);
  W.visit = -1; W.welded = -1; W.tinged = -1;
  W.job = { i: 0, u: 0, du: 0.05, side: 0 };        // side: 0 = from above, +-1 = outer
  const WELD_D = 7.4, WELD_ORDER = [0, 2, 4, 1, 3];

  // the best stretch of letter i's outline to weld this visit: high on the letter, or
  // (first / last letter) on its outer stroke — never the face of a main stroke
  function chooseWeld(title, v) {
    const J = W.job, last = title.count - 1;
    J.i = WELD_ORDER[v % WELD_ORDER.length] % title.count;
    title.center(J.i, _c);
    let best = -Infinity;
    for (let k = 0; k < 64; k++) {
      title.seam(J.i, k / 64, _s);
      const top = _s.y - _c.y;
      const out = J.i === 0 ? _c.x - _s.x : J.i === last ? _s.x - _c.x : -9;
      const sOut = _s.y > 0.9 && _s.y < 3.2 ? out * 1.3 : -9;
      const score = Math.max(top, sOut) + 0.35 * hash(v * 64 + k, 11);
      if (score > best) {
        best = score;
        J.u = k / 64;
        J.side = sOut > top ? (J.i === 0 ? -1 : 1) : 0;
      }
    }
    J.du = (hash(v, 8) > 0.5 ? 1 : -1) * (0.03 + 0.03 * hash(v, 9));
  }

  function welder(w) {
    const t = w.t, dt = w.dt, title = w.title, A = W, T = A.tool, J = A.job;
    const { e, snake } = introEnter(t, WELDER_ROOT.x / HALF_W);
    let k = 16, zeta = 0.72, weld = false, roll = 0, nod = 0, carTo = WELDER_ROOT.x;
    const working = introWorking(t) && title;

    // off duty: hover above the word, torch pointed idly at the letters below
    A.target.set(A.carX.x + 0.5 * Math.sin(t * 0.37), REST_Y + 0.2 * Math.sin(t * 0.53), -0.1);
    A.look.set(A.target.x + 1.2 * Math.sin(t * 0.31), 2.6, TITLE.center.z);
    roll = 0.25 * Math.sin(t * 0.9);

    if (working) {
      const cyc = (t - INTRO.work) / WELD_D, v = Math.floor(cyc), tau = (cyc - v) * WELD_D;
      if (A.visit !== v) { A.visit = v; chooseWeld(title, v); }
      title.normal(J.i, _n);
      title.seam(J.i, J.u, _s);
      // where the torch works from: just off the face, ABOVE a top seam or OUTSIDE an
      // outer one — so neither the head nor the body behind it covers the stroke
      const off = _k.set(J.side * 0.42, J.side ? 0.12 : 0.4, 0).addScaledVector(_n, 0.48);
      carTo = _s.x + J.side * 1.25;
      if (tau < 1.4) {                                         // travel (above the word)
        A.target.copy(_s).add(off).add(_l.set(J.side * 0.7, J.side ? 0.5 : 0.85, 0.2));
        A.look.copy(_s);
        roll = 0.2 * Math.sin(t * 1.3);
      } else if (tau < 2.0) {                                  // inspect: curious tilt
        A.target.copy(_s).add(off).add(_l.set(J.side * 0.25, 0.22, 0.12));
        A.look.copy(_s);
        roll = 0.5 * bump(1.4, 2.0, tau);
        k = 22;
      } else if (tau < 4.6) {                                  // weld the seam
        const s = smooth(2.0, 4.6, tau);
        title.seam(J.i, J.u + J.du * s + 0.004 * Math.sin(t * 9), _s);
        A.target.copy(_s).add(off)
          .add(_l.set(0.01 * Math.sin(t * 61), 0.01 * Math.sin(t * 73 + 1), 0));
        A.look.copy(_s);
        weld = true;
        roll = 0.07 * Math.sin(t * 57);
        k = 70; zeta = 0.85;
        if (A.welded !== v) { A.welded = v; w.emit('weld', { letter: J.i, point: _s.clone() }); }
      } else if (tau < 6.75) {                                 // back off, UP and away
        title.seam(J.i, J.u + J.du, _s);
        A.target.set(_s.x + J.side * 1.0, REST_Y + 0.15, 0);
        k = 28; zeta = 0.5;                                    // ...with overshoot
        A.look.copy(_s);
        if (tau > 5.3 && tau < 6.1) {                          // two proud nods
          nod = 0.42 * Math.sin(TAU * (tau - 5.3) / 0.4) * bump(5.3, 6.1, tau);
          if (A.tinged !== v && tau > 5.36) {
            A.tinged = v;
            title.poke(J.i, 0.25, _k.copy(_n).negate());       // 'ting'
          }
        } else if (tau >= 6.1) {                               // flourish: spin + hop
          roll = TAU * smoother(6.1, 6.75, tau);
          A.target.y += 0.3 * bump(6.1, 6.75, tau);
        }
      } else {                                                 // glance at the next job
        title.center(WELD_ORDER[(v + 1) % WELD_ORDER.length] % title.count, A.look);
        carTo = A.look.x;
      }

      // "watch it!": a pointer that comes close gets the torch pointed at it and a
      // scolding wag; the weld stops while it tells you off
      if (cursorRecent(w, 1.6)) {
        const near = smooth(0.3, 0.12, screenDist(w, A.headPos));
        if (near > 0) {
          A.look.lerp(gazePoint(w, A.headPos, _gz), near);
          roll += 0.32 * Math.sin(t * 13) * near;
          if (near > 0.4) weld = false;
        }
      }
    }

    closeIn(A, t);                                             // the ring around the title
    const tada = introTada(t);
    if (tada > 0) { roll += TAU * smoother(INTRO.tada, INTRO.tada + 0.7, t); A.target.y += 0.3 * tada; }

    A.carX.step(Math.max(-5.2, Math.min(5.2, carTo)), dt);
    A.mount.position.x = A.carX.x;
    // ENTER: drops in from the top on its slot in the sine wave; then the hoist rules
    A.mount.position.y = A.hoist.step(hoistY(A, 1, FRONT_RAIL.y), dt) + 7.5 * (1 - e);
    // the body bows OUTWARD, away from the middle of the word, and a little toward you
    A.bulge.set(A.goal.x.x >= TITLE.center.x ? 1 : -1, 0, 0.35);
    drive(A, w, { k, zeta, bulge: A.bulge, sag: 0, writhe: weld ? 0.015 : 0.1, writheSpeed: 1.1,
      phase: 0.4, aimRate: weld ? 16 : 8, snakeAmp: 0.5 * snake, snakeAxis: AX_X });
    A.inner.rotation.set(nod, 0, roll);
    const flick = 0.75 + 0.25 * Math.sin(t * 83) * Math.sin(t * 47);
    T.hot.emissiveIntensity = lerp(T.hot.emissiveIntensity,
      weld ? 3.2 * flick : 0.1 + 2.5 * tada, ease(dt, 20));
    T.flame.scale.setScalar(weld ? 1 + 0.5 * flick : 0.6 + tada);
    T.visor.rotation.x = T.visorS.step(weld ? 0.05 : -0.95, dt);
    if (weld && w.sparks) {
      A.sparkAcc += 95 * dt;
      const n = Math.floor(A.sparkAcc);
      A.sparkAcc -= n;
      if (n) w.sparks.emit(_s, _k.copy(_n).add(_c.set(J.side * 0.6, 0.8, 0)), n, 2.8, 1.05);
    }
  }

  // ---------------------------------------------------------------- POKER
  // Lives under the word. A little camera head with a poking finger slung beneath
  // the lens. The only time it crosses a letter is the jab itself — from BELOW, so
  // the letter hops.
  const P = makeTentacle(scene, { at: POKER_ROOT, hang: false, segments: 14,
    segLen: 0.3, baseW: 0.3, tipW: 0.15, thin: 0.8, maxBend: 0.45, lag: 48 });
  // the finger is the chain's last link (it points wherever the body is going, which
  // is what a jab is); the little camera rides in front of its base, free to look at
  // the victim — or round at you
  new Rigid()
    .add(new THREE.CylinderGeometry(0.03, 0.045, 0.6, 8), MAT.dark, 0, 0.36, 0)
    .add(new THREE.SphereGeometry(0.075, 12, 9), MAT.joint, 0, 0.7, 0)
    .add(new THREE.CylinderGeometry(0.07, 0.07, 0.06, 10), MAT.body, 0, 0.1, 0)
    .into(P.chain.tip);
  P.face = makeHead(kindOf('webcam', 'eyeball'), 31, 0.62);
  P.inner.add(P.face.group);
  P.chain.tip.traverse((o) => { o.frustumCulled = false; });
  P.tipDir = new THREE.Vector3(0, 1, 0);
  P.blink = blinker(31);
  P.hoist = new Spring(POKER_ROOT.y - 2, 140, 1);
  P.cyc = -1; P.poked = -1; P.startleT = -Infinity; P.pokeI = 0;
  P.pokePt = new THREE.Vector3();
  P.puppy = new Spring(0, 12, 0.9);
  const POKE_T0 = INTRO.work + 1.8, POKE_D = 10.5, POKE_ORDER = [3, 2, 4, 3, 1, 4];
  const LURK_Y = Math.min(TITLE_CLEAR.y0 - 0.15, 0.3);   // never above this at rest
  const JAB_DIR = new THREE.Vector3(0, 0.75, -0.66).normalize(), FINGER = 0.78;
  const POKER_BULGE = new THREE.Vector3(0.5, 0, 1);      // spare length bows toward you, not the word

  // the lowest point of the letter's outline the poker can reach comfortably
  function choosePoke(w, v) {
    const title = w.title;
    P.pokeI = POKE_ORDER[v % POKE_ORDER.length] % title.count;
    let best = Infinity;
    for (let s = 0; s < 48; s++) {
      title.seam(P.pokeI, s / 48, _s);
      const cost = _s.y + 0.3 * Math.abs(_s.x - POKER_ROOT.x)
                 + (_s.y < 0.75 ? 4 : 0)                       // not G's deep descender
                 + 0.15 * hash(v * 48 + s, 3);
      if (cost < best) { best = cost; P.pokePt.copy(_s); }
    }
  }

  function poker(w) {
    const t = w.t, dt = w.dt, title = w.title, A = P, H = A.face;
    const { e, snake } = introEnter(t, POKER_ROOT.x / HALF_W);
    // lurking spot: the head just above the bottom edge of frame, under the word
    _c.set(POKER_ROOT.x + 0.55 * Math.sin(t * 0.61), -0.16 + 0.08 * Math.sin(t * 1.3),
      POKER_ROOT.z - 0.15);
    let k = 10, zeta = 0.62, tilt = 0.18 * Math.sin(t * 0.7), iris = 0.8, lurking = true;
    A.target.copy(_c);
    gazePoint(w, A.headPos, A.look);                           // it is watching YOU

    if (title && t >= POKE_T0) {
      const cyc = (t - POKE_T0) / POKE_D, v = Math.floor(cyc), tau = (cyc - v) * POKE_D;
      if (A.cyc !== v) { A.cyc = v; choosePoke(w, v); }
      const pt = A.pokePt;
      // the approach: below and in front of the letter's foot, under the word
      _l.set(pt.x, Math.min(pt.y - 1.0, LURK_Y), pt.z + 0.9);
      if (tau < 2.0) {                                          // lurk; eye the victim
        if (tau > 1.2) { A.look.lerp(pt, smooth(1.2, 1.5, tau)); tilt = 0.3; }
      } else if (tau < 4.2) {                                   // sneak, tiptoe
        lurking = false;
        const s = smoother(2.0, 4.2, tau);
        A.target.lerpVectors(_c, _l, s);
        A.target.y += 0.06 * Math.abs(Math.sin(tau * 9)) * s - 0.06 * s;
        A.look.copy(pt);
        tilt = -0.2 * s;
        k = 14; zeta = 0.85;
      } else if (tau < 4.6) {                                   // wind up: coil DOWN
        lurking = false;
        A.target.copy(_l).add(_k.set(0, -0.4, 0.3));
        A.look.copy(pt);
        iris = 0.42;                                            // squint
        k = 40; zeta = 0.8;
      } else if (tau < 4.82) {                                  // JAB (the one crossing)
        lurking = false;
        A.target.copy(pt).addScaledVector(A.tipDir, -FINGER);   // finger END on the letter
        A.look.copy(pt);
        k = 340; zeta = 0.55;
        if (A.poked !== v && tau > 4.7) {
          A.poked = v;
          title.poke(A.pokeI, 1.2, JAB_DIR);
        }
      } else if (tau < 5.4) {                                   // recoil
        lurking = false;
        A.target.copy(_l).add(_k.set(0.3, -0.7, 0.4));
        A.look.copy(pt);
        iris = 1;                                               // "uh oh"
        k = 130; zeta = 0.45;
      } else if (tau < 6.6) {                                   // hide
        lurking = false;
        A.target.set(POKER_ROOT.x, -1.5, POKER_ROOT.z - 0.1);
        A.look.set(POKER_ROOT.x - 1, -2, 2);
        k = 45; zeta = 0.7;
      } else if (tau < 7.6) {                                   // peek back at its work
        A.target.set(POKER_ROOT.x - 0.35, -0.25, POKER_ROOT.z - 0.1);
        title.center(A.pokeI, A.look);
        k = 12; zeta = 0.7;
        tilt = 0.3;
      } else if (tau < 8.9) {                                   // GIGGLE, at you
        const g = bump(7.6, 8.9, tau);
        A.target.set(POKER_ROOT.x - 0.35 + 0.035 * Math.sin(t * 47) * g,
          -0.2 + 0.05 * Math.abs(Math.sin(t * 23)) * g, POKER_ROOT.z - 0.1);
        A.look.y += 0.25 * Math.sin(t * 29) * g;
        tilt = 0.3 + 0.25 * Math.sin(t * 31) * g;
        k = 60; zeta = 0.5;
      }
    }

    // a still pointer nearby: curious puppy (but it stays under the word — it looks
    // UP at you rather than climbing over the letters). A fast one: startle and duck.
    const cur = w.cursor;
    if (introWorking(t)) {
      const sd = screenDist(w, A.headPos);
      if (cur.seen && cur.speed > 7 && sd < 0.35 && t - A.startleT > 1.2) A.startleT = t;
      if (t - w.click.t < 0.05 && sd < 0.3) A.startleT = t;
      const still = cur.seen ? t - cur.movedAt : 0;
      const want = lurking && cur.seen && cur.present && still > 0.7 && still < 14
        && cur.point.distanceTo(POKER_ROOT) < A.reach + 1.5 ? 1 : 0;
      const pup = clamp01(A.puppy.step(want, dt));
      if (pup > 0.01) {
        _l.copy(cur.point).add(_k.set(0, -0.35, 0.3));
        if (Math.abs(_l.x) < TITLE_CLEAR.x + 0.2) _l.y = Math.min(_l.y, LURK_Y);
        _l.sub(POKER_ROOT).clampLength(0, A.reach - 0.4).add(POKER_ROOT);
        A.target.lerp(_l, pup);
        tilt = lerp(tilt, 0.38 * Math.sin(t * 1.9), pup);      // head cocked, like a dog
        k = lerp(k, 9, pup);
      }
      const st = t - A.startleT;
      if (st < 1.1) {
        const s = 1 - smooth(0.7, 1.1, st);
        A.target.lerp(_k.set(POKER_ROOT.x + 0.2, -1.5, POKER_ROOT.z), s);
        k = lerp(k, 170, s); zeta = lerp(zeta, 0.55, s);
        iris = lerp(iris, 1, s);
      }
    }

    closeIn(A, t);
    const tada = introTada(t);
    if (tada > 0) {                                             // happy wiggle
      tilt += 0.55 * Math.sin(t * 22) * tada;
      A.target.y += 0.3 * tada;
      iris = lerp(iris, 1, tada);
    }

    // ENTER: rises from under the floor, snaking; the hoist keeps the body a riser
    A.mount.position.y = A.hoist.step(hoistY(A, -1, POKER_ROOT.y), dt) - 4.5 * (1 - e);
    // staging: even when it eyes a letter it keeps a three-quarter face to the audience
    A.look.lerp(gazePoint(w, A.headPos, _gz), 0.35);
    // the head rides in FRONT of the finger's base (toward the viewer), whatever the
    // chain is doing
    A.chain.tip.getWorldPosition(_k);
    A.head.position.copy(A.chain.tip.worldToLocal(_k.add(_l.set(0, 0.02, 0.2))));
    A.tipDir.set(0, 1, 0).transformDirection(A.chain.tip.matrixWorld);
    if (A.tipDir.dot(JAB_DIR) < 0.5) A.tipDir.lerp(JAB_DIR, 0.5).normalize();
    drive(A, w, { k, zeta, bulge: POKER_BULGE, sag: 0, writhe: 0.07, writheSpeed: 1.7, phase: 2.1,
      aimRate: 11, snakeAmp: 0.45 * snake, snakeAxis: AX_X });
    A.inner.rotation.set(0, 0, tilt);
    H.setIris(iris * A.blink(t));
    H.setTally(lurking ? 0.2 : 1);
    H.update(dt, t);
  }

  // ---------------------------------------------------------------- INSPECTOR
  // On the gantry BEHIND the word: a big eye that peeks over the letter tops. Watches
  // you; watches the line when there is something on it; ducks flying crates.
  const E = makeTentacle(scene, { at: INSPECTOR_ROOT, hang: true, segments: 12,
    segLen: 0.19, baseW: 0.3, tipW: 0.17, maxBend: 0.5, lag: 40 });
  trolley(E.mount);
  E.face = makeHead(kindOf('eyeball', 'stereo'), 47, 0.72);
  E.inner.add(E.face.group);
  E.blink = blinker(47);
  E.carX = new Spring(INSPECTOR_ROOT.x, 2.6, 0.9);
  E.duck = new Spring(0, 90, 0.5);
  E.prevMove = -Infinity; E.onsetT = -Infinity; E.kicked = -Infinity;
  E.tadaDone = false; E.flashed = -Infinity;

  function interest(w, out) {
    const crates = w.crates;
    if (!crates) return null;
    const f = crates.flying?.();
    if (f) return f.object3D.getWorldPosition(out);
    // a crate going by under it is worth a look, for the first part of each pass
    let best = null, bd = 2.6;
    for (const c of crates.list || []) {
      if (c.state !== 'belt' && c.state !== 'held') continue;
      c.object3D.getWorldPosition(_v);
      const d = Math.abs(_v.x - E.headPos.x);
      if (d < bd) { bd = d; best = c; out.copy(_v); }
    }
    return best ? out : null;
  }

  function inspector(w) {
    const t = w.t, dt = w.dt, A = E, H = A.face;
    // ENTER: the carriage stays on the gantry and the chain REELS DOWN, snaking
    const { e, snake } = introEnter(t, INSPECTOR_ROOT.x / HALF_W);
    let carTo = INSPECTOR_ROOT.x, aimRate = 7, iris = 0.82, k = 14, zeta = 0.62;
    const working = introWorking(t);

    gazePoint(w, A.headPos, A.look);                           // default: YOU
    const got = working ? interest(w, _s) : null;
    if (got) {
      A.look.copy(_s);
      carTo = INSPECTOR_ROOT.x + Math.max(-2.2, Math.min(2.2, (_s.x - INSPECTOR_ROOT.x) * 0.35));
    }

    // reeled up at e = 0, hanging at its watch post (over the letter tops) at e = 1
    A.target.set(A.mount.position.x + 0.55 * (1 - e) + 0.12 * Math.sin(t * 0.7),
      INSPECTOR_ROOT.y - lerp(0.3, 1.9, e) + 0.06 * Math.sin(t * 1.1),
      INSPECTOR_ROOT.z + 0.35 * e);
    if (got) {
      _k.subVectors(_s, A.target);
      A.target.x += Math.max(-0.5, Math.min(0.5, _k.x * 0.12));
      A.target.y += Math.max(-0.1, Math.min(0.15, _k.y * 0.08));
    }

    // double-take when the pointer starts moving after a rest: glance, look away,
    // SNAP back, stare
    const cur = w.cursor;
    if (cur.movedAt > A.prevMove) {
      if (cur.movedAt - A.prevMove > 1.2) A.onsetT = t;
      A.prevMove = cur.movedAt;
    }
    const dtk = t - A.onsetT;
    if (working && cur.seen && dtk < 2.6) {
      gazePoint(w, A.headPos, _gz);
      if (dtk < 0.28) { A.look.lerp(_gz, smooth(0, 0.12, dtk)); aimRate = 10; }
      else if (dtk < 0.78) {                                    // "...nothing." looks away
        A.look.set(A.headPos.x - 2.5, BELT.topY, BELT.z);
      } else {
        A.look.copy(_gz);
        aimRate = dtk < 0.95 ? 60 : 14;
        iris = 1;
        if (A.kicked !== A.onsetT) { A.kicked = A.onsetT; A.goal.kick(_k.set(0, 1.4, 0.5)); }
      }
    }

    // duck when a flying crate comes close, then "phew"
    const f = working ? w.crates?.flying?.() : null;
    let near = 0;
    if (f) near = smooth(2.0, 0.9, f.object3D.getWorldPosition(_c).distanceTo(A.headPos));
    const duck = A.duck.step(near, dt);
    A.target.add(_k.set(0, -0.3 * duck, -0.35 * duck));          // down behind the letters
    iris *= 1 - 0.5 * clamp01(duck);
    if (f && t - throwT < 3) aimRate = Math.max(aimRate, 12);

    closeIn(A, t);
    const tada = introTada(t);
    if (tada > 0) {
      iris = 1;
      A.target.y += 0.4 * tada;
      aimRate = Math.max(aimRate, 14);
      if (!A.tadaDone) { A.tadaDone = true; A.goal.kick(_k.set(0, 2.2, 0.4)); H.flash(); }
    }
    if (w.click.t > A.flashed) { A.flashed = w.click.t; H.flash(); A.goal.kick(_k.set(0, 0.9, 0)); }

    A.carX.step(carTo, dt);
    A.mount.position.x = A.carX.x;
    // reeled up, the spare length hangs as a loop under the rail; at its post it bows
    // BACK, behind the word
    drive(A, w, { k, zeta, bulge: BULGE_BACK, sag: lerp(3, 0.25, e), writhe: 0.06, writheSpeed: 0.9,
      phase: 4.2, aimRate, snakeAmp: 0.4 * snake, snakeAxis: AX_X });
    A.inner.rotation.set(0, 0, 0.12 * Math.sin(t * 0.47) - 0.25 * clamp01(duck));
    const click = Math.exp(-Math.max(0, t - w.click.t) * 5);
    H.setIris(Math.min(1, iris + 0.3 * click) * A.blink(t));
    H.setTally(Math.max(tada, click, got ? 0.3 : 0.8));
    H.update(dt, t);
  }

  // ---------------------------------------------------------------- BACKGROUND
  // Four camera tentacles far back in the fog. They mostly watch the viewer; now and
  // then one leans in for a closer look, or follows a crate through the air.
  const BG_KINDS = [['cctv', 'pod'], ['film', 'periscope'], ['paparazzi', 'stereo'], ['dome', 'cctv']];
  const BG = BG_ROOTS.map((at, i) => {
    // the outer pair hang beside the word and can come down to eye level; the inner
    // pair are behind it, so they stay up in the rafters
    const outer = Math.abs(at.x) > TITLE_CLEAR.x + 1.5;
    const A = makeTentacle(scene, { at, hang: true, segments: 12, segLen: outer ? 0.58 : 0.42,
      baseW: 0.5, tipW: 0.24, maxBend: 0.4, lag: 26 });
    A.drop = outer ? 5.9 : 4.1;
    A.face = makeHead(kindOf(...BG_KINDS[i % 4]), 60 + i, 1.0 + 0.25 * hash(i, 6));
    A.inner.add(A.face.group);
    A.blink = blinker(60 + i);
    A.i = i;
    A.home = at.clone();
    A.side = at.x < 0 ? -1 : 1;                  // which screen edge it swings in from
    A.tadaDone = false;
    A.bulge = new THREE.Vector3(0, 0, 1);
    return A;
  });

  function crawler(w, A) {
    const t = w.t, dt = w.dt, i = A.i, root = A.home, H = A.face;
    // ENTER: swung far out past its own screen edge, then pendulums in (the spring's
    // low damping IS the pendulum; the snake runs vertically, across the swing)
    const { e, snake } = introEnter(t, root.x / HALF_W);
    const om = 0.75 + 0.5 * hash(i, 1), ph = TAU * hash(i, 2);
    A.target.set(root.x + 1.3 * Math.sin(t * 0.29 * om + ph),
      root.y - A.drop + 0.45 * Math.sin(t * 0.21 * om + ph * 1.3),
      root.z + 0.9 * Math.cos(t * 0.25 * om + ph));
    let k = 4, zeta = 0.8, iris = 0.8, tilt = 0.2 * Math.sin(t * 0.4 * om + ph), aimRate = 3.5;
    if (e < 1) {
      const out = (1 - e) ** 1.4;
      A.target.x += A.side * 8.5 * out;
      A.target.y += 1.6 * out;
      zeta = 0.42;                                // swings past and settles
      k = 6;
    }
    gazePoint(w, A.headPos, A.look, 4);
    if (introWorking(t)) {
      // lean in for a closer look: gather back (anticipation), then crane toward you
      const Pd = 10 + 7 * hash(i, 3), T0 = Pd * hash(i, 4);
      const u = (t + T0) % Pd;
      if (u < 3.6) {
        const back = bump(0, 0.9, u), out = smooth(0.7, 1.5, u) * (1 - smooth(2.7, 3.6, u));
        A.target.y += 0.7 * back;
        A.target.z -= 0.5 * back;
        _l.copy(A.look).sub(A.home).setLength(A.reach * 0.96).add(A.home);
        A.target.lerp(_l, out * 0.85);
        iris = lerp(iris, 1, out);
        tilt += 0.45 * Math.sin(u * 2.2) * out;   // cocks its head at you
        k = lerp(4, 9, out);
      }
      // a crate in the air steals everyone's attention, each after its own beat
      const f = w.crates?.flying?.();
      if (f && t - throwT > 0.12 + 0.35 * hash(i, 12)) {
        f.object3D.getWorldPosition(A.look);
        aimRate = 9;
        iris = 1;
      }
    }
    closeIn(A, t);
    const tada = introTada(t);
    if (tada > 0) {
      A.target.y += 0.7 * tada;
      tilt += 0.5 * Math.sin(t * 19 + i) * tada;
      iris = 1;
      if (!A.tadaDone && tada > 0.5 + 0.3 * hash(i, 13)) { A.tadaDone = true; H.flash(); }
    }
    A.bulge.set(Math.sin(t * 0.17 * om + ph), 0, Math.cos(t * 0.17 * om + ph));   // slow roll
    drive(A, w, { k, zeta, bulge: A.bulge, sag: 0.5, writhe: 0.3, writheSpeed: 0.6 + 0.3 * om,
      follow: 7, phase: ph, aimRate, snakeAmp: 0.8 * snake, snakeAxis: AX_Y });
    A.inner.rotation.set(0, 0, tilt);
    H.setIris(iris * A.blink(t));
    H.setTally(0.5 + 0.5 * Math.sin(t * 2.2 + ph) > 0.5 ? 1 : 0.15);   // REC blink
    H.update(dt, t);
  }

  const actors = { welder: W, poker: P, inspector: E, background: BG };

  function update(w) {
    welder(w);
    poker(w);
    inspector(w);
    for (const A of BG) crawler(w, A);
  }

  // probe: tips, goals, worst bend, head positions — for motion sampling in dev
  function probe() {
    const out = {};
    const all = { welder: W, poker: P, inspector: E,
      bg0: BG[0], bg1: BG[1], bg2: BG[2], bg3: BG[3] };
    for (const [name, A] of Object.entries(all)) {
      out[name] = {
        tip: A.chain.tip.getWorldPosition(new THREE.Vector3()).toArray(),
        goal: A.goal.x.toArray(),
        head: A.headPos.toArray(),
        bend: A.solver.maxLocalBend(),
        A: A.solver.A, d: A.solver.d, mountY: A.mount.position.y,
      };
    }
    return out;
  }

  return { update, actors, probe };
}
