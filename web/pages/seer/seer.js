// seer.js — Seer, embodied (docs/26-seer-embodied.md is the authority).
//
// A WATCHER in the telemetry page's hero band: one large lens on a body, and a posture that
// changes with what it is doing. Not a mascot — every motion here MEANS a state:
//
//   idle       slow sweep, lens dim               nothing to look at
//   summoned   turns toward the failure           an issue was selected: lens up, a hand points, the magnifier comes out
//   thinking   lens brightens, tighter arcs       the Seer call is in flight: beam scanning, hands working
//   verdict    HOLDS STILL, lens steady           the answer renders beside it: one open hand presents it. Stillness is the point.
//   stumped    lens dims, turns AWAY              Seer returned nothing useful: arms droop, a small shrug, and it does
//                                                 NOT perk back up on its own. Built first; a system that visibly
//                                                 gives up is one you can trust.
// There is no celebration in this vocabulary: no thumbs-up, no "found it". Never fake a verdict — not even in acting.
//
// The character is Sentry's own Seer, kept recognisable: a flat-faced PAPER PYRAMID, ONE almond
// eye (the lens: cream sclera, dark purple iris, white catchlight, heavy upper lid) with a starburst
// behind it, eight boneless NOODLE ARMS, four-digit GLOVE HANDS, a keyboard, a magnifying glass, a
// beam of light from the eye. It is drawn in THIS site's vocabulary (landing/watchers.js, heads.js —
// reused as ideas, not imports: those files are being edited): arms are solved curves, the eye
// tracks / blinks / squints with everything eased, and it all goes through pomme's manga pass.
// Reference-led purple/magenta illustration shading; deliberately NOT the landing
// page's manga treatment. Seer is the only purple focal point on the data page.
//
// PROCEDURAL vs MESHY. Meshy cannot rig this (its rigger is for bipeds) and was unreliable on hand
// POSE (its "thumbs up" raised a FINGER; rejected twice). The pyramid, eye, arms, props and every
// hand whose fingers must MOVE (typing, grip) are procedural capsules whose curls EASE between
// gestures. Approved Meshy gloves remain opt-in references; the default rig keeps every
// finger articulated. Nothing is ever loaded from models/_rejected/.
//
// STAGE. Seer stays put in a HERO BAND (~42vh, full width; ~30vh on phones): the canvas IS the band
// (it scrolls away with the page; the loop stops while it is off screen or the tab is hidden). It
// never reaches over the page. `el` only says WHERE the thing is: eye, beam and hands turn that way.
//
// HOST PAGE: an import map for "three" and "three/addons/" and a canvas that fills the band:
//   <div style="position:relative;height:42vh"><canvas id="seer" style="position:absolute;inset:0;width:100%;height:100%"></canvas></div>
//   const seer = await mountSeer(canvas)
//   seer.react(state, el?)          'idle' | 'summoned' | 'thinking' | 'verdict' | 'stumped'
//   seer.lookAt(clientX, clientY)   optional: where the idle sweep should rest instead
//   seer.dispose()
// This module makes no network calls beyond loading its own models.
//
// World units are the canvas's CSS pixels (x right, y UP, so y = -localY), orthographic.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

// ---- palette (linear, > 1 on purpose: the pass bands by luminance; > 0.74 prints as paper) ----
const rgb = hex => new THREE.Color(hex).toArray();
const FACE = rgb('#f725cc'), FACE_TOP = rgb('#fa36db');
const SIDE_L = rgb('#991dac'), SIDE_R = rgb('#c422c4'), UNDER = rgb('#701185');
const RAY = rgb('#fff0d3');
const CREAM = rgb('#fff3e1'), IRIS = rgb('#7745af'), PUPIL = rgb('#7745af'), WHITE = rgb('#c4a6e4');
const INKY = rgb('#351340'), HOLO_BG = rgb('#19161d'), HOLO = rgb('#e8e5ec');
const HANDLE = rgb('#ae92ce');

const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const lerp = (a, b, u) => a + (b - a) * u;
const smooth = (a, b, x) => { const u = clamp((x - a) / (b - a), 0, 1); return u * u * (3 - 2 * u); };
function hash(i, c = 0) {                 // deterministic variation: never Math.random
  let h = (Math.imul(i | 0, 0x27d4eb2d) ^ Math.imul((c | 0) + 0x165667b1, 0x9e3779b1)) >>> 0;
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b) >>> 0; h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35) >>> 0;
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}
class Spring {                            // semi-implicit, two substeps: stable at any real dt
  constructor(x = 0, k = 60, z = 0.8) { this.x = x; this.v = 0; this.k = k; this.z = z; }
  step(t, dt) { const c = 2 * this.z * Math.sqrt(this.k), h = Math.min(dt, 1 / 30) / 2;
    for (let s = 0; s < 2; s++) { this.v += (this.k * (t - this.x) - c * this.v) * h; this.x += this.v * h; } return this.x; }
}
class Spring3 {
  constructor(p, k = 40, z = 0.82) { this.x = p.clone(); this.v = new THREE.Vector3(); this.k = k; this.z = z; }
  step(t, dt) { const c = 2 * this.z * Math.sqrt(this.k), h = Math.min(dt, 1 / 30) / 2;
    for (let s = 0; s < 2; s++) { this.v.x += (this.k * (t.x - this.x.x) - c * this.v.x) * h; this.v.y += (this.k * (t.y - this.x.y) - c * this.v.y) * h;
      this.v.z += (this.k * (t.z - this.x.z) - c * this.v.z) * h; this.x.addScaledVector(this.v, h); } return this.x; }
}
function paint(geo, c) {
  const n = geo.attributes.position.count, a = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) { a[i * 3] = c[0]; a[i * 3 + 1] = c[1]; a[i * 3 + 2] = c[2]; }
  geo.setAttribute('color', new THREE.BufferAttribute(a, 3));
  return geo;
}
const FLAT = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide });
// arms and hands: lit, so a noodle reads as a volume (paper on top, halftone underneath)
const SKIN = new THREE.MeshStandardMaterial({ color: '#f325ca', roughness: 0.92, metalness: 0,
  emissive: '#de16b5', emissiveIntensity: 0.25 });

// Fine stippled pigment and broad violet shadow bands from the original artwork.
// One native material pass: no outline/threshold postprocessing or texture fetches.
function illustration(material, sculpted = false) {
  material.onBeforeCompile = shader => {
    shader.vertexShader = shader.vertexShader.replace('#include <common>',
      '#include <common>\nvarying vec3 vPigment;')
      .replace('#include <project_vertex>', '#include <project_vertex>\nvPigment = mvPosition.xyz;');
    shader.fragmentShader = shader.fragmentShader.replace('#include <common>', `#include <common>
      varying vec3 vPigment;
      float pigment(vec2 p) { return fract(sin(dot(p, vec2(12.9898,78.233))) * 43758.5453); }`)
      .replace('#include <opaque_fragment>', `${sculpted ? `
        float illustrationLight = smoothstep(-0.35, 0.9, dot(normal, normalize(vec3(-0.45, 0.65, 0.7))));
        outgoingLight = mix(vec3(0.29, 0.008, 0.37), vec3(0.93, 0.018, 0.64), illustrationLight);
      ` : ''}
      float fleck = pigment(floor(vPigment.xy * 1.5));
      outgoingLight *= 0.88 + 0.20 * fleck;
      outgoingLight = mix(outgoingLight, vec3(0.16, 0.015, 0.23), step(0.97, fleck) * 0.22);
      #include <opaque_fragment>`);
  };
  material.customProgramCacheKey = () => `seer-original-pigment-${sculpted}`;
  return material;
}
illustration(FLAT); illustration(SKIN, true);

// ---- the eye's almond: two parabolas ---------------------------------------------------------
const EYE = { w: 48, hT: 27, hB: 21, cx: 0, cy: 8 };
const yTop = (x) => EYE.hT * (1 - (x / EYE.w) ** 2), yBot = (x) => -EYE.hB * (1 - (x / EYE.w) ** 2);

function buildBody() {
  const body = new THREE.Group();
  const A = new THREE.Vector2(0, 122), L = new THREE.Vector2(-132, -94), R = new THREE.Vector2(132, -94), D = 40;
  // front face with the almond cut OUT of it: the eye sits behind, so the face itself masks the
  // iris and the lids (no stencil, and a few px of real depth when the body turns)
  const shape = new THREE.Shape([L, R, A]);
  const hole = new THREE.Path();
  hole.moveTo(EYE.cx - EYE.w, EYE.cy); hole.quadraticCurveTo(EYE.cx, EYE.cy + 2 * EYE.hT, EYE.cx + EYE.w, EYE.cy);
  hole.quadraticCurveTo(EYE.cx, EYE.cy - 2 * EYE.hB, EYE.cx - EYE.w, EYE.cy);
  shape.holes.push(hole);
  const front = paint(new THREE.ShapeGeometry(shape, 28), FACE).translate(0, 0, D);
  { const pos = front.attributes.position, col = front.attributes.color;   // colour is linear in y: exact on any triangulation
    for (let i = 0; i < pos.count; i++) { const u = smooth(-94, 122, pos.getY(i)); for (let k = 0; k < 3; k++) col.array[i * 3 + k] = lerp(FACE[k], FACE_TOP[k], u * 0.8); } }
  // the rest of the pyramid: two flanks and an underside meeting at a back apex
  const back = [0, -30, -150], tri = (p, q, r, c) => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute([...p, ...q, ...r], 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1], 2));
    g.setIndex([0, 1, 2]); g.computeVertexNormals();
    return paint(g, c);
  };
  const a3 = [A.x, A.y, D], l3 = [L.x, L.y, D], r3 = [R.x, R.y, D];
  // pale starburst behind the eye, clipped to stay inside the face
  const rays = [];
  const edges = [[L, R], [R, A], [A, L]], c0 = new THREE.Vector2(EYE.cx, EYE.cy);
  for (let i = 0; i < 14; i++) {
    const ang = (i / 14) * Math.PI * 2 + 0.22, dir = new THREE.Vector2(Math.cos(ang), Math.sin(ang));
    let reach = 1e9;
    for (const [p, q] of edges) {         // distance from the eye to the face's edge along this ray
      const e = q.clone().sub(p), den = dir.x * e.y - dir.y * e.x; if (Math.abs(den) < 1e-6) continue;
      const w = p.clone().sub(c0), t = (w.x * e.y - w.y * e.x) / den, s = (w.x * dir.y - w.y * dir.x) / den;
      if (t > 0 && s >= 0 && s <= 1) reach = Math.min(reach, t);
    }
    const r0 = 50, r1 = Math.min(reach - 9, r0 + (i % 2 ? 22 : 40)); if (r1 < r0 + 5) continue;
    const n = new THREE.Vector2(-dir.y, dir.x), wd = 6.5;
    const p = c0.clone().addScaledVector(dir, r0), g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute([p.x + n.x * wd, p.y + n.y * wd, D + 0.6, p.x - n.x * wd, p.y - n.y * wd, D + 0.6,
      c0.x + dir.x * r1, c0.y + dir.y * r1, D + 0.6], 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1], 2)); g.setIndex([0, 1, 2]); g.computeVertexNormals();
    rays.push(g);
  }
  body.add(new THREE.Mesh(mergeGeometries([front, tri(a3, l3, back, SIDE_L), tri(r3, a3, back, SIDE_R), tri(l3, r3, back, UNDER)], false), FLAT));
  // the starburst and the sclera carry the LENS'S BRIGHTNESS: dim when there is nothing to look at, bright when it thinks
  const rayMat = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }), scleraMat = new THREE.MeshBasicMaterial();
  illustration(rayMat); illustration(scleraMat);
  const star = new THREE.Shape(), spikes = 12, step = Math.PI * 2 / spikes;
  const radial = (angle, radius) => [EYE.cx + Math.cos(angle) * radius, EYE.cy + Math.sin(angle) * radius];
  star.moveTo(...radial(0, 87));
  for (let i = 0; i < spikes; i++) {
    const a = i * step;
    star.quadraticCurveTo(...radial(a + step * 0.18, 43), ...radial(a + step * 0.5, 43));
    star.quadraticCurveTo(...radial(a + step * 0.82, 43), ...radial(a + step, (i % 2 ? 87 : 79)));
  }
  star.closePath(); star.holes.push(hole.clone());
  const burst = new THREE.Mesh(new THREE.ShapeGeometry(star, 8).translate(0, 0, D + 0.6), rayMat);
  body.add(burst); rays.forEach(g => g.dispose());

  // the eye, a few px behind the face
  const eye = new THREE.Group(); eye.position.set(EYE.cx, EYE.cy, D);
  eye.add(new THREE.Mesh(new THREE.ShapeGeometry(new THREE.Shape(hole.getPoints(48)), 1)
    .translate(-EYE.cx, -EYE.cy, -6), scleraMat));
  const irisGeo = new THREE.CircleGeometry(27, 48);
  paint(irisGeo, IRIS);
  const irisColors = irisGeo.attributes.color, irisPos = irisGeo.attributes.position;
  for (let i = 0; i < irisPos.count; i++) {
    const u = clamp((irisPos.getY(i) + 27) / 54, 0, 1);
    irisColors.setXYZ(i, lerp(IRIS[0] * 0.55, WHITE[0], u), lerp(IRIS[1] * 0.55, WHITE[1], u), lerp(IRIS[2] * 0.55, WHITE[2], u));
  }
  const iris = new THREE.Mesh(irisGeo, FLAT);
  iris.position.z = -5;
  eye.add(iris);
  // lids: strips from far outside the almond to a moving edge; the face hides whatever overshoots
  const K = 22, lidGeo = new THREE.BufferGeometry(), nv = (K + 1) * 6;
  lidGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(nv * 3), 3));
  const col = new Float32Array(nv * 3), idx = [];
  for (let k = 0; k <= K; k++) for (let s = 0; s < 6; s++) { const c = s === 2 || s === 3 ? INKY : FACE; col.set(c, (k * 6 + s) * 3); }
  for (let k = 0; k < K; k++) for (const s of [0, 2, 4]) { const a = k * 6 + s, b = a + 1, c = a + 6, d = a + 7; idx.push(a, c, b, b, c, d); }
  lidGeo.setAttribute('color', new THREE.BufferAttribute(col, 3)); lidGeo.setIndex(idx);
  const lids = new THREE.Mesh(lidGeo, FLAT); lids.position.z = -3; lids.frustumCulled = false;
  eye.add(lids);
  body.add(eye);
  function setLids(u, l) {                 // 0 = open .. 1 = shut, upper and lower
    const p = lidGeo.attributes.position.array;
    for (let k = 0; k <= K; k++) {
      const x = lerp(-EYE.w - 8, EYE.w + 8, k / K), xc = clamp(x, -EYE.w, EYE.w);
      const yu = lerp(yTop(xc), yBot(xc), u), yl = lerp(yBot(xc), yTop(xc), l), o = k * 18;
      const put = (s, y, z) => { p[o + s * 3] = x; p[o + s * 3 + 1] = y; p[o + s * 3 + 2] = z; };
      const triangleTop = 122 - Math.abs(x + EYE.cx) / 132 * 216 - EYE.cy;
      put(0, Math.min(EYE.hT + 34, triangleTop), 0); put(1, yu, 0);
      put(2, yu + 0.5, 0.4); put(3, yu - 3.4, 0.4);       // the heavy lash line riding its edge
      put(4, Math.min(yl, yu - 0.5), 0); put(5, -EYE.hB - 34, 0);   // lower lid (never crosses the upper)
    }
    lidGeo.attributes.position.needsUpdate = true;
  }
  function setGlow(g) {                    // 0 = dim lens .. 1 = bright
    scleraMat.color.setRGB(...CREAM);
    rayMat.color.setRGB(...RAY);
  }
  return { body, iris, burst, setLids, setGlow };
}

// ---- hands ----------------------------------------------------------------------------------
// Hand frame: +X wrist -> fingers, +Y the thumb side, +Z the back of the hand (fingers curl to -Z).
// Every part is an instance of ONE unit capsule: all procedural hands cost a single draw call.
const GESTURES = {   // per finger [mcp, pip] curl, spread about z, thumb [swing from +X toward +Y, curl1, curl2]
  open:  { f: [[0.10, 0.12], [0.05, 0.10], [0.12, 0.16]], s: [0.2, 0, -0.2], t: [0.95, 0.10, 0.12] },
  splay: { f: [[-0.08, 0.04], [-0.1, 0.02], [-0.06, 0.06]], s: [0.34, 0, -0.34], t: [1.2, 0.0, 0.0] },       // the shrug: "nothing"
  limp:  { f: [[0.5, 0.6], [0.58, 0.66], [0.66, 0.74]], s: [0.06, 0, -0.06], t: [0.7, 0.4, 0.35] },          // arms drooped
  point: { f: [[0.0, 0.03], [1.5, 1.55], [1.52, 1.55]], s: [0.04, 0, 0], t: [0.3, 0.85, 0.7] },
  peace: { f: [[0.02, 0.04], [0.02, 0.04], [1.5, 1.5]], s: [0.22, -0.18, 0], t: [0.35, 0.9, 0.8] },
  grip:  { f: [[1.0, 1.2], [1.0, 1.2], [1.0, 1.2]], s: [0, 0, 0], t: [0.55, 1.0, 0.75] },
};
const HAND_PARTS = 10;
function buildHand() {
  const root = new THREE.Group(), parts = [];
  const cap = (node, len, thick, wide, x = 0) => parts.push({ node,
    local: new THREE.Matrix4().compose(new THREE.Vector3(x, 0, 0), new THREE.Quaternion(), new THREE.Vector3(len / 2, wide, thick)) });
  cap(root, 0.48, 0.65, 0.72, 0.12);                      // smooth wrist, not a mechanical cuff
  cap(root, 1.28, 0.72, 1.22, 0.72);                      // inflated glove palm
  const fingers = [0.34, 0, -0.34].map((y, i) => {
    const mcp = new THREE.Group(); mcp.position.set(1.14, y, 0); root.add(mcp);
    const len = i === 1 ? 0.56 : 0.5;
    cap(mcp, len, 0.4, 0.4, len / 2 - 0.08);
    const pip = new THREE.Group(); pip.position.set(len - 0.17, 0, 0); mcp.add(pip);
    cap(pip, 0.46, 0.37, 0.37, 0.15);
    return { mcp, pip };
  });
  const tb = new THREE.Group(); tb.position.set(0.5, 0.46, -0.04); root.add(tb);
  cap(tb, 0.5, 0.46, 0.46, 0.17);
  const tt = new THREE.Group(); tt.position.set(0.34, 0, 0); tb.add(tt);
  cap(tt, 0.44, 0.42, 0.42, 0.14);
  const cur = JSON.parse(JSON.stringify(GESTURES.open));
  function pose(name, dt, tap = 0) {       // curls EASE toward the gesture: a hand changes its mind, it does not pop
    const g = GESTURES[name] || GESTURES.open, k = 1 - Math.exp(-dt * 13);
    for (let i = 0; i < 3; i++) {
      for (let j = 0; j < 2; j++) cur.f[i][j] += (g.f[i][j] - cur.f[i][j]) * k;
      cur.s[i] += (g.s[i] - cur.s[i]) * k;
      const extra = tap ? Math.max(0, Math.sin(tap + i * 2.1)) ** 3 * 0.55 : 0;  // typing: fingers drum in turn
      fingers[i].mcp.rotation.set(0, cur.f[i][0] + extra, cur.s[i]);
      fingers[i].pip.rotation.y = cur.f[i][1] + extra * 0.6;
    }
    for (let j = 0; j < 3; j++) cur.t[j] += (g.t[j] - cur.t[j]) * k;
    tb.rotation.set(0, 0, cur.t[0]); tb.rotateY(cur.t[1]); tt.rotation.y = cur.t[2];
  }
  return { root, parts, pose };
}

// ---- props ------------------------------------------------------------------------------------
function buildMagnifier() {
  const g = new THREE.Group(), ring = new THREE.Group(), handle = new THREE.Group();
  ring.add(new THREE.Mesh(mergeGeometries([
    paint(new THREE.TorusGeometry(1, 0.13, 10, 56), CREAM),
    paint(new THREE.TorusGeometry(0.74, 0.03, 6, 14, 0.9).rotateZ(0.5), WHITE),          // two glints: the lens is
    paint(new THREE.TorusGeometry(0.6, 0.025, 6, 10, 0.5).rotateZ(0.75), WHITE)], false), FLAT));   // otherwise see-through
  handle.add(new THREE.Mesh(mergeGeometries([
    paint(new THREE.CylinderGeometry(5.5, 6.5, 1, 12).translate(0, -0.5, 0), HANDLE),
    paint(new THREE.CylinderGeometry(7, 7, 0.12, 12).translate(0, -0.06, 0), CREAM)], false), FLAT));
  g.add(ring, handle);
  return { g, ring, handle };
}
function buildPanel() {
  const parts = [paint(new THREE.PlaneGeometry(92, 60), HOLO_BG), paint(new THREE.PlaneGeometry(96, 64), HOLO).translate(0, 0, -0.4)];
  for (let i = 0; i < 3; i++) parts.push(paint(new THREE.PlaneGeometry(i === 1 ? 40 : 56, 4), HOLO).translate(i === 1 ? -18 : -10, 16 - i * 11, 0.4));
  const panel = new THREE.Mesh(mergeGeometries(parts, false), FLAT);
  const traceGeo = new THREE.BufferGeometry();
  traceGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(33 * 3), 3));
  const trace = new THREE.Line(traceGeo, new THREE.LineBasicMaterial({ color: '#ffffff' }));
  trace.frustumCulled = false; panel.add(trace);
  const marker = new THREE.Mesh(new THREE.CircleGeometry(2.8, 10), new THREE.MeshBasicMaterial({ color: '#ffffff' }));
  panel.add(marker);
  // An illustrative signal study, not a fabricated live telemetry reading.
  panel.userData.trace = traceGeo; panel.userData.marker = marker;
  return panel;
}

function buildKeyboard() {                 // Seer's desk: a slab of ink with paper keys
  const parts = [paint(new THREE.PlaneGeometry(292, 30), HOLO_BG), paint(new THREE.PlaneGeometry(298, 36), CREAM).translate(0, 0, -0.4)];
  for (let r = 0; r < 2; r++) for (let k = 0; k < 13; k++) {
    const c = hash(k, r + 7) > 0.78 ? HOLO : CREAM;
    parts.push(paint(new THREE.PlaneGeometry(17, 9), c).translate(-132 + k * 22, 6.5 - r * 13, 0.4));
  }
  return new THREE.Mesh(mergeGeometries(parts, false), FLAT);
}

const ROOT_X = [-98, -70, -42, -14, 14, 42, 70, 98];
const ROLE = { outerL: 0, lens: 1, point: 2, typeL: 3, typeR: 4, present: 5, restR: 6, outerR: 7 };
const ARMS = ROOT_X.length, RINGS = 40, SIDES = 8;
// what each state looks like, as numbers: how much everything moves, how bright the lens is, how open the lids are,
// the beam, and how hard the typists work
const ACT = {
  idle:     { mv: 0.65, rate: 1.0, glow: 0.45, lids: [0.20, 0.04], beam: 0,    typing: 3.8 },
  summoned: { mv: 0.65, rate: 1.0, glow: 0.8,  lids: [0.08, 0.0],  beam: 0.16, typing: 0 },
  thinking: { mv: 1.2,  rate: 2.1, glow: 1.0,  lids: [0.16, 0.02], beam: 0.34, typing: 11 },
  verdict:  { mv: 0.0,  rate: 0.0, glow: 0.9,  lids: [0.2, 0.04],  beam: 0.1,  typing: 0 },
  stumped:  { mv: 0.1,  rate: 0.4, glow: 0.0,  lids: [0.58, 0.1],  beam: 0,    typing: 0 },
};
const STATES = Object.keys(ACT);
const bump = (a, b, x) => Math.sin(Math.PI * clamp((x - a) / (b - a), 0, 1)) ** 2;   // 0 -> 1 -> 0, zero value AND velocity at both ends

export async function mountSeer(canvas, { models = '/pages/seer/models/', generatedHands = false } = {}) {
  const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(1);              // the pass works in CSS px; a retina screen must not cost 4x on a data page
  renderer.setClearColor(0x000000, 0);
  renderer.autoClear = false;
  renderer.info.autoReset = false;

  const scene = new THREE.Scene(), camera = new THREE.OrthographicCamera(0, 1, 0, -1, -2000, 2000);
  camera.position.z = 600;
  {                                       // the site's four-light rig; no SpotLights (they tax every lit fragment)
    const key = new THREE.DirectionalLight('#ffffff', 3.0); key.position.set(4, 6, 3);
    const fill = new THREE.DirectionalLight('#8fa8ff', 0.8); fill.position.set(-4, 2, -2);
    const front = new THREE.DirectionalLight('#ffffff', 1.5); front.position.set(0.5, 2.5, 10);
    scene.add(key, fill, front, new THREE.AmbientLight('#46424e', 1.1));
  }
  // Render the original character palette directly: no manga threshold or hue wash.

  // the beam is drawn straight to the canvas, UNDER Seer: light at low alpha would print as ink in the pass
  const beamGeo = new THREE.BufferGeometry();
  beamGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(12), 3));
  beamGeo.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 0, 1, 1, 0, 1, 1], 2)); beamGeo.setIndex([0, 2, 1, 1, 2, 3]);
  const beamMat = new THREE.ShaderMaterial({ transparent: true, depthTest: false, depthWrite: false, side: THREE.DoubleSide, uniforms: { uA: { value: 0 } },
    vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: `varying vec2 vUv; uniform float uA;
      void main(){ float across = 1.0 - pow(abs(vUv.y * 2.0 - 1.0), 1.6);
        vec2 g = gl_FragCoord.xy / 7.0; vec2 c = fract(vec2(g.x + floor(g.y) * 0.5, g.y)) - 0.5;      // halftone dots, like the page
        float dots = smoothstep(0.34, 0.2, length(c)) * 0.65 + 0.35;
        float a = uA * across * smoothstep(0.0, 0.06, vUv.x) * (1.0 - 0.35 * vUv.x) * dots;
        gl_FragColor = vec4(vec3(0.73, 0.42, 1.0) * a, a); }`, blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneMinusSrcAlphaFactor });
  const beam = new THREE.Mesh(beamGeo, beamMat); beam.frustumCulled = false;
  const beamScene = new THREE.Scene(); beamScene.add(beam);

  // ---- the character -------------------------------------------------------------------------
  const rig = new THREE.Group(); scene.add(rig);           // scaled by S, placed at the body centre
  const { body, iris, burst, setLids, setGlow } = buildBody(); rig.add(body);
  const roots = ROOT_X.map((x) => new THREE.Vector3(x, -90, -18));
  const panels = [buildPanel(), buildPanel()]; rig.add(...panels);
  const keyboard = buildKeyboard(); rig.add(keyboard);
  const armGeo = new THREE.BufferGeometry(), AV = (RINGS + 1) * SIDES;
  armGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(ARMS * AV * 3), 3));
  armGeo.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(ARMS * AV * 3), 3));
  { const ix = []; for (let a = 0; a < ARMS; a++) for (let r = 0; r < RINGS; r++) for (let s = 0; s < SIDES; s++) {
      const p = a * AV + r * SIDES + s, q = a * AV + r * SIDES + (s + 1) % SIDES; ix.push(p, q, p + SIDES, q, q + SIDES, p + SIDES); }
    armGeo.setIndex(ix); }
  const armMesh = new THREE.Mesh(armGeo, SKIN); armMesh.frustumCulled = false; scene.add(armMesh);
  const capsule = new THREE.CapsuleGeometry(0.5, 1, 5, 12).rotateZ(-Math.PI / 2);
  const handMesh = new THREE.InstancedMesh(capsule, SKIN, ARMS * HAND_PARTS); handMesh.frustumCulled = false;
  handMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage); scene.add(handMesh);
  const palmMesh = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 16, 10), SKIN, ARMS);
  palmMesh.frustumCulled = false; palmMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage); scene.add(palmMesh);
  const lens = buildMagnifier(); scene.add(lens.g);

  const arms = roots.map((rootLocal, i) => {
    const hand = buildHand(); scene.add(hand.root);
    return { i, rootLocal, hand, wrist: null, quat: new THREE.Quaternion(), qT: new THREE.Quaternion(), target: new THREE.Vector3(),
      gesture: 'open', tap: 0, size: 1, sizeT: 1, sizeS: new Spring(1, 120, 0.7), glb: {}, shown: null, squash: new Spring(1, 320, 0.45), squashTo: 1, z: -50 + i * 6 };
  });

  // Meshy gloves: ONLY open / point, and only when hands.json says "OK…". Hands whose fingers must MOVE stay procedural.
  const GLB_ARMS = { [ROLE.outerL]: ['open'], [ROLE.outerR]: ['open'], [ROLE.present]: ['open'], [ROLE.point]: ['open', 'point'] };
  const glbKey = (g) => (g === 'point' ? 'point' : g === 'grip' ? null : 'open');       // open / splay / limp all wear the open glove
  let disposed = false;
  (async () => {
    if (!generatedHands) return; // rigid generated gloves cannot articulate their fingers
    let log; try { log = await (await fetch(models + 'hands.json', { cache: 'no-cache' })).json(); } catch { return; }
    const loader = new GLTFLoader();
    for (const pose of ['open', 'point']) {
      if (!log[pose] || !String(log[pose].verdict || '').startsWith('OK')) continue;
      loader.load(models + `hand_${pose}.glb`, (gltf) => {
        let geo = null; gltf.scene.traverse((o) => { if (o.isMesh && !geo) geo = o.geometry.clone(); });
        if (!geo || disposed) return;
        geo.computeBoundingBox(); const b = geo.boundingBox, size = b.getSize(new THREE.Vector3());
        // Meshy: +Y up the fingers, cuff at the bottom, palm toward +Z  ->  ours: +X fingers, palm -Z, wrist at the origin
        geo.translate(-(b.min.x + b.max.x) / 2, -b.min.y, -(b.min.z + b.max.z) / 2).scale(2.35 / size.y, 2.35 / size.y, 2.35 / size.y);
        geo.applyMatrix4(new THREE.Matrix4().makeBasis(new THREE.Vector3(0, 1, 0), new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, -1)));
        for (const name of Object.keys(geo.attributes)) if (name !== 'position') geo.deleteAttribute(name);
        geo.computeVertexNormals();
        for (const a of arms) if (GLB_ARMS[a.i] && GLB_ARMS[a.i].includes(pose)) {
          const m = new THREE.Mesh(geo, SKIN); m.visible = false; m.frustumCulled = false; a.hand.root.add(m); a.glb[pose] = m;
        }
      }, undefined, () => {});
    }
  })();

  // ---- state ---------------------------------------------------------------------------------
  let W = 1, H = 1, S = 1, xr = 400, onScreen = true, paused = false, rigReady = false, loaded = false;
  let state = 'idle', target = null, since = 0, clock = 0, ph = 0, last = performance.now() / 1000, lastDraw = 0, raf = 0, awaySide = -1;
  const pointer = new THREE.Vector2(NaN, NaN), rest = new THREE.Vector2(NaN, NaN);
  const bodyPos = new Spring3(new THREE.Vector3(), 60, 0.8), hop = new Spring(0, 170, 0.34), squashB = new Spring(0, 260, 0.4);
  const yawS = new Spring(0, 34, 0.8), pitchS = new Spring(0, 34, 0.85), rollS = new Spring(0, 40, 0.7), sagS = new Spring(0, 14, 0.9);
  const mvS = new Spring(ACT.idle.mv, 20, 1), rateS = new Spring(ACT.idle.rate, 20, 1), glowS = new Spring(ACT.idle.glow, 22, 1), typeS = new Spring(ACT.idle.typing, 20, 1);
  const gaze = new Spring3(new THREE.Vector3(), 90, 0.9), lidU = new Spring(0.36, 300, 1), lidL = new Spring(0.06, 300, 1);
  const dirS = new Spring3(new THREE.Vector3(0, -1, 0), 50, 0.85);      // where the thing is, eased
  const lensPos = new Spring3(new THREE.Vector3(), 34, 0.8), lensR = new Spring(34, 40, 0.9), lensH = new Spring3(new THREE.Vector3(0.45, -0.89, 0), 30, 0.85), beamA = new Spring(0, 40, 1);
  const tmp = new THREE.Vector3(), X = new THREE.Vector3(), Y = new THREE.Vector3(), Z = new THREE.Vector3(), mtx = new THREE.Matrix4();
  const ZERO = new THREE.Matrix4().makeScale(0, 0, 0), debug = { calls: 0, tris: 0 };
  const V2 = (x, y) => new THREE.Vector2(x, y);
  const onMove = (e) => pointer.set(e.clientX, e.clientY);
  addEventListener('pointermove', onMove, { passive: true });

  function resize() {
    W = Math.max(2, canvas.clientWidth); H = Math.max(2, canvas.clientHeight);
    renderer.setSize(W, H, false);
    camera.right = W; camera.bottom = -H; camera.updateProjectionMatrix();
    const textReserve = W < 700 && canvas.closest('.seerband') ? 110 : 30;
    S = clamp(Math.min((H - textReserve) / 400, W / 720), 0.25, 1.25);
    xr = Math.min(W / 2 - 60 * S, 600 * S) / S;                        // how far out the hands may go, in S units
    if (!rigReady) bodyPos.x.set(W / 2, -(22 + 124 * S), 0);
  }
  const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(resize) : null;
  if (ro) ro.observe(canvas); addEventListener('resize', resize); resize();
  const io = typeof IntersectionObserver === 'function' ? new IntersectionObserver(([e]) => { onScreen = e.isIntersecting; syncRunning(); }) : null;
  if (io) io.observe(canvas);

  const HU = () => 40 * S;                 // px per hand unit: OG's hands are BIG
  const blinkAmount = (t) => {             // deterministic schedule; lids close in ~80 ms, open in ~140
    const n = Math.floor(t / 3.6), t0 = n * 3.6 + 1.2 + hash(n, 3) * 1.8, p = t - t0;
    const once = (q) => (q < 0 || q > 0.26 ? 0 : q < 0.08 ? smooth(0, 0.08, q) : q < 0.12 ? 1 : 1 - smooth(0.12, 0.26, q));
    return Math.max(once(p), hash(n, 4) > 0.72 ? once(p - 0.36) : 0);
  };
  // place a hand: fingers along f; the thumb side toward `up` — or exactly along `exactY` when it holds something
  function aimHand(a, palm, f, up, exactY = null) {
    X.set(f.x, f.y, 0).normalize();
    if (exactY) { Y.set(exactY.x, exactY.y, 0); Y.addScaledVector(X, -Y.dot(X)).normalize(); Z.crossVectors(X, Y).normalize(); }
    else { Z.set(0, 0, 1); Y.crossVectors(Z, X); if (Y.x * up.x + Y.y * up.y < 0) { Z.set(0, 0, -1); Y.crossVectors(Z, X); } }
    a.qT.setFromRotationMatrix(mtx.makeBasis(X, Y, Z));
    a.target.set(palm.x, palm.y, a.z).addScaledVector(X, -0.74 * HU() * a.size);
  }

  const p0 = new THREE.Vector3(), c1 = new THREE.Vector3(), c2 = new THREE.Vector3(), p3v = new THREE.Vector3(), pt = new THREE.Vector3(), tan = new THREE.Vector3(), bin = new THREE.Vector3();
  function frame() {
    raf = requestAnimationFrame(frame);
    if (document.hidden || !onScreen || paused) return;
    const now = performance.now() / 1000, age0 = clock - since;
    const settled = (state === 'verdict' || state === 'stumped' || reduced) && age0 > 3.5;
    if (now - lastDraw < (settled ? 1 / 32 : 1 / 60) - 0.002) return;
    lastDraw = now;
    const dt = Math.min(0.06, Math.max(0.001, now - last)); last = now; clock += dt;
    const act = ACT[state], age = clock - since, calm = reduced ? 0 : 1;
    const play = clock % 9;
    const inspect = state === 'idle' ? bump(0.6, 4.8, play) * calm : 0;
    const presentSignal = state === 'idle' ? bump(4.5, 8.7, play) * calm : 0;
    const mv = mvS.step(act.mv, dt) * calm;                             // how much everything sways: 0 in a verdict — it HOLDS STILL
    ph += dt * rateS.step(act.rate, dt);                                // one phase for all the idle motion: "tighter, faster arcs" is a rate
    renderer.info.reset();
    const cr = canvas.getBoundingClientRect();
    const local = (cx, cy) => V2(cx - cr.left, -(cy - cr.top));
    const stumped = state === 'stumped', engaged = state === 'summoned' || state === 'thinking' || state === 'verdict';

    // ---- body: centre stage, and it STAYS there -------------------------------------------------
    const sag = sagS.step(stumped ? 1 : 0, dt);
    const home = tmp.set(W / 2, -(22 + 124 * S) + Math.sin(ph * 1.25) * 5 * S * mv - sag * 16 * S + hop.step(0, dt) + (stumped ? Math.sin(age * 0.9) * 2.2 * S * calm : 0), 0);
    const B = bodyPos.step(home, dt);
    const sq = squashB.step(0, dt) + sag * 0.05;
    rig.position.copy(B); rig.scale.set(S * (1 + sq * 0.5), S * (1 - sq), S);
    const eye = V2(B.x + EYE.cx * S, B.y + EYE.cy * S);

    // ---- where is the thing? (a DIRECTION: Seer never leaves the band) ---------------------------
    let dir = null;
    if (target && target.isConnected) {
      const r = target.getBoundingClientRect();
      if (r.width || r.height) { dir = local(r.left + r.width / 2, r.top + r.height / 2).sub(eye); dir = dir.lengthSq() < 1 ? null : dir.normalize(); }
    }
    if (!dir && (engaged || stumped)) dir = V2(0, -1);                  // the panel is below the band
    if (dir) dirS.step(tmp.set(dir.x, dir.y, 0), dt);
    const D = V2(dirS.x.x, dirS.x.y).normalize(), Dp = V2(-D.y, D.x), side = D.x >= 0 ? 1 : -1;

    // ---- posture: toward it, or — stumped — AWAY from it -----------------------------------------
    let yawT = 0, pitchT = 0, rollT = 0, look = V2(0, 0);
    if (stumped) {
      yawT = awaySide * 0.82 * smooth(0.25, 1.5, age) + Math.sin((age - 0.45) * Math.PI * 3.2) * 0.17 * bump(0.45, 2.0, age) * calm;   // turns away, shaking its head
      pitchT = 0.2 * smooth(0.2, 1.4, age); rollT = awaySide * -0.07 * smooth(0.3, 1.6, age);
      look = V2(awaySide * 0.95, -0.8);                                 // and looks down, away
    } else if (engaged) {
      yawT = D.x * 0.5; pitchT = -D.y * 0.24; look = V2(D.x, D.y * 1.2);
      if (state === 'thinking') look.add(V2(Math.sin(ph * 2.3) * 0.34, Math.sin(ph * 3.1) * 0.22)); // the eye hunts in tight arcs
    } else {
      const pp = !isNaN(rest.x) ? local(rest.x, rest.y) : (!isNaN(pointer.x) && pointer.y >= cr.top && pointer.y <= cr.bottom ? local(pointer.x, pointer.y) : null);
      if (pp) look = V2(clamp((pp.x - eye.x) / 300, -1, 1), clamp((pp.y - eye.y) / 240, -1, 1));
      else look = V2(Math.sin(ph * 0.62) * 0.85 * (reduced ? 0 : 1), -0.12);  // nothing to look at: a slow sweep
      yawT = look.x * 0.3; pitchT = -look.y * 0.1;
      look.lerp(V2(-0.95, 0.5), inspect * 0.8);
      yawT -= inspect * 0.18; rollT = inspect * 0.035 - presentSignal * 0.04;
    }
    gaze.k = state === 'summoned' ? 170 : stumped ? 22 : 60;            // onto a failure: fast (still eased). Giving up: slow.
    gaze.step(tmp.set(clamp(look.x, -1, 1), clamp(look.y, -1, 1), 0), dt);
    iris.position.set(gaze.x.x * (EYE.w - 19), gaze.x.y * 6.5, -5);
    body.rotation.set(pitchS.step(pitchT, dt), yawS.step(yawT, dt), rollS.step(rollT, dt) + Math.sin(ph * 0.9) * 0.025 * mv);
    // the lens: brightness, lids, starburst
    const glow = glowS.step(act.glow, dt);
    setGlow(glow);
    burst.visible = true; // the cream sunburst is part of the original face
    const giveUp = stumped && age > 0.12 && age < 0.8;                  // a long slow blink as it gives up...
    const startle = state === 'summoned' && age < 0.5;                  // ...and eyes WIDE when it is summoned
    const blink = blinkAmount(clock * (stumped ? 0.6 : 1));
    setLids(Math.max(lidU.step(giveUp ? 0.97 : startle ? 0.02 : act.lids[0], dt), blink), Math.max(lidL.step(giveUp ? 0.5 : act.lids[1], dt), blink * 0.5));
    panels[0].position.set(-xr * 0.62 + Math.sin(ph * 0.8) * 5 * mv, 96 + Math.sin(ph * 1.1 + 1) * 7 * mv, -60);
    panels[1].position.set(xr * 0.6 + Math.sin(ph * 0.7 + 2) * 5 * mv, 104 + Math.sin(ph * 0.9) * 6 * mv, -60); panels[1].scale.set(-0.86, 0.86, 1);
    panels[0].visible = panels[1].visible = W > 640;
    panels.forEach((panel, index) => {
      const p = panel.userData.trace.attributes.position;
      const wave = u => -16 + Math.sin(u * 10 - ph * 2 + index) * 3 +
        Math.exp(-(((u - (0.5 + Math.sin(ph + index) * 0.28)) / 0.075) ** 2)) * 13;
      for (let i = 0; i < p.count; i++) { const u = i / (p.count - 1); p.setXYZ(i, -36 + u * 72, wave(u), 1); }
      p.needsUpdate = true;
      const u = 0.5 + Math.sin(ph * 1.4 + index) * 0.46;
      panel.userData.marker.position.set(-36 + u * 72, wave(u), 1.2);
    });
    keyboard.position.set(0, -236, -30); keyboard.rotation.x = -0.5;

    // ---- where every hand goes --------------------------------------------------------------------
    const at = (dx, dy) => V2(B.x + dx * S, B.y + dy * S);              // in S units from the body centre
    const drift = (i, ax, ay) => V2(Math.sin(ph * 0.55 + i * 1.9) * ax * S * mv, Math.sin(ph * 0.43 + i * 2.7) * ay * S * mv);
    const UPV = V2(0, 1), floorY = (-H + 8 - B.y) / S + 62;              // the band's floor, in S units below the body, less a hanging hand
    const droopY = Math.max(floorY, -262);
    for (const a of arms) { a.gesture = 'open'; a.sizeT = 1; a.tap = 0; }
    const typing = typeS.step(act.typing, dt);

    // the typists (procedural: their fingers move). Working = fast; idle = the odd key; stumped = slumped on the keys
    for (const [role, sx] of [[ROLE.typeL, -1], [ROLE.typeR, 1]]) {
      const a = arms[role];
      if (stumped) { aimHand(a, at(sx * 62, -212), V2(sx * 0.42, -0.9), V2(-sx, 0)); a.gesture = 'limp'; continue; }
      const up = engaged && state !== 'thinking' ? 26 : 0;              // hands off the keys while it attends
      aimHand(a, at(sx * 74, -204 + up + Math.abs(Math.sin(ph * 2.2 + sx)) * 4 * mv), V2(sx * -0.16, -1), V2(-sx, 0));
      a.tap = typing > 0.05 ? clock * typing + sx * 1.7 : 0;
    }

    // the magnifier
    let ringC = at(-xr * 0.52, -70).add(drift(1, 10, 8)), ringR = 36 * S, hT = V2(0.45, -0.89);
    if (W > 640) {
      ringC.lerp(at(-xr * 0.62 + Math.sin(ph * 1.4) * 16, 78), inspect);
      ringR += inspect * 7 * S;
    }
    if (state === 'summoned' || state === 'thinking') {                  // it comes OUT: held on the eye-line, the eye looks THROUGH it
      const orbit = state === 'thinking' ? V2(Math.cos(ph * 2.2), Math.sin(ph * 2.2)).multiplyScalar(22 * S * mv) : V2(0, 0);
      ringC = at(side * 196, -34).add(V2(D.x * 46 * S, D.y * 46 * S)).add(orbit); ringR = 46 * S;
      hT = V2(side * 0.62, -0.78);
      if (state === 'thinking') hT.rotateAround(V2(0, 0), Math.sin(ph * 1.3) * 0.45 * mv);                           // turning it over
    } else if (stumped) { ringC = at(-176, Math.max(droopY + 44, -250)); hT = V2(0.02, 1); }                         // dangling from a limp hand
    else if (state === 'verdict') ringC = at(-side * xr * 0.5, -96);                                                 // put away, on the far side
    ringC.y = clamp(ringC.y, -H + ringR + 6, -ringR - 6);
    if (!rigReady) { lensPos.x.set(ringC.x, ringC.y, 30); lensR.x = ringR; lensH.x.set(hT.x, hT.y, 0); }
    lensPos.step(tmp.set(ringC.x, ringC.y, 30), dt); lensR.step(ringR, dt); lensH.step(tmp.set(hT.x, hT.y, 0), dt);
    const hdir = V2(lensH.x.x, lensH.x.y).normalize(), HL = 66 * S, M = lensPos.x;
    const palmL = V2(M.x + hdir.x * (lensR.x + HL * 0.66), M.y + hdir.y * (lensR.x + HL * 0.66));
    const px = V2(hdir.y, -hdir.x); if (px.dot(V2(B.x - palmL.x, B.y - 60 * S - palmL.y)) > 0) px.negate();          // the forearm comes from the body's side
    aimHand(arms[ROLE.lens], palmL, px, null, V2(-hdir.x, -hdir.y)); arms[ROLE.lens].gesture = 'grip';
    lens.ring.position.set(M.x, M.y, 30); lens.ring.scale.setScalar(lensR.x);
    lens.handle.position.set(M.x + hdir.x * lensR.x, M.y + hdir.y * lensR.x, 29);
    lens.handle.rotation.z = Math.atan2(hdir.y, hdir.x) + Math.PI / 2; lens.handle.scale.set(S, HL, S);

    // the pointer: on a summons it POINTS at the failure (Meshy's pointing glove), and keeps pointing while it thinks
    { const a = arms[ROLE.point];
      if (state === 'summoned' || state === 'thinking') {
        const p = at(side * Math.min(xr * 0.62, 360), -96).add(V2(D.x * 30 * S, D.y * 30 * S)); p.y = clamp(p.y, -H + 96 * S, -40 * S);
        aimHand(a, p, D, UPV); a.gesture = 'point'; a.sizeT = 1.3;
      } else if (stumped) { aimHand(a, at(-112, droopY + 8), V2(-0.08, -1), V2(-1, 0)); a.gesture = 'limp'; }
      else if (state === 'idle') {
        aimHand(a, at(-xr * 0.30, 54).add(drift(2, 5, 5)), V2(-0.1, 1), UPV); a.gesture = 'peace';
      } else aimHand(a, at(-xr * 0.28, -172).add(drift(2, 9, 7)), V2(-0.35, -0.94), UPV); }

    // the presenter: in a verdict ONE open hand, held out toward where the answer renders — and nothing else moves
    { const a = arms[ROLE.present];
      if (state === 'verdict') { aimHand(a, at(side * Math.min(xr * 0.46, 300), -118), V2(D.x * 0.55 + side * 0.62, D.y * 0.55 + 0.12), UPV); a.sizeT = 1.35; }
      else if (stumped) { aimHand(a, at(112, droopY + 8), V2(0.08, -1), V2(1, 0)); a.gesture = 'limp'; }
      else {
        const palm = at(xr * 0.28, -172).add(drift(5, 9, 7));
        palm.lerp(at(xr * 0.6, W > 640 ? 42 : -112), presentSignal);
        aimHand(a, palm, V2(0.35, lerp(-0.94, 0.8, presentSignal)), UPV);
      } }
    { const a = arms[ROLE.restR];                                        // the spare hand keeps out of the acting hands' way
      if (stumped) { aimHand(a, at(178, droopY + 26), V2(0.05, -1), V2(1, 0)); a.gesture = 'limp'; }
      else if (engaged) aimHand(a, at(-side * xr * 0.3, -176), V2(-side * 0.3, -0.95), UPV);
      else aimHand(a, at(xr * 0.54, -96).add(drift(6, 10, 8)), V2(0.45, -0.89), UPV); }

    // the outer hands: at rest they hang out wide; stumped they do the SHRUG — up, out, palms open — then sink and stay out
    { // up and out on the shrug's lift, then they SINK and stay low and open: a held "I've got nothing", not a ta-da
      const sink = smooth(1.6, 3.0, age), lift = stumped ? 96 * smooth(0.3, 0.95, age) - 92 * sink : 0, spread = stumped ? 1 : 0;
      for (const [role, sx] of [[ROLE.outerL, -1], [ROLE.outerR, 1]]) {
        const a = arms[role];
        if (stumped) { aimHand(a, at(sx * Math.min(xr * 0.62, 330), -122 + lift), V2(sx * lerp(0.8, 0.95, sink), lerp(0.6, 0.3, sink)), UPV); a.gesture = 'splay'; a.sizeT = 1.2; }
        else {
          aimHand(a, at(sx * xr * 0.76, 40).add(drift(role, 8, 8)), V2(sx * 0.72, 0.7), UPV);
          a.gesture = sx < 0 ? 'point' : 'peace';
        }
      }
      void spread; }

    // ---- solve and skin the arms; pose and place the hands -------------------------------------
    const P = armGeo.attributes.position.array, Nn = armGeo.attributes.normal.array, rad = 9.5 * S;
    rig.updateMatrixWorld(true); // update the parent transform before root attachments
    arms.forEach((a, i) => {
      if (!a.wrist) { a.wrist = new Spring3(a.target, lerp(44, 30, hash(i, 1)), 0.74); a.quat.copy(a.qT); }
      a.wrist.k = stumped ? 16 : lerp(44, 30, hash(i, 1));              // giving up is slow and heavy
      const wpos = a.wrist.step(a.target, dt);
      a.quat.slerp(a.qT, 1 - Math.exp(-dt * (stumped ? 4.5 : 10)));
      a.size = a.sizeS.step(a.sizeT, dt);
      // cubic from the root under the body to the wrist, arriving ALONG the forearm; the control points wander so it stays boneless
      p0.copy(a.rootLocal).applyMatrix4(body.matrixWorld);
      p3v.copy(wpos); p3v.z = a.z;
      const dist = p0.distanceTo(p3v), k1 = clamp(dist * 0.55, 60 * S, 380 * S), k2 = clamp(dist * 0.45, 46 * S, 320 * S);
      const fan = a.rootLocal.x / 120, sway = Math.sin(ph * 0.7 + i * 1.3) * 30 * S * mv;
      c1.set(p0.x + fan * k1 * 0.85 + sway, p0.y - k1 * (1 - Math.abs(fan) * 0.3), lerp(p0.z, a.z, 0.3));
      X.set(1, 0, 0).applyQuaternion(a.quat);
      c2.set(p3v.x - X.x * k2 - sway * 0.5, p3v.y - X.y * k2 + Math.cos(ph * 0.6 + i) * 16 * S * mv, lerp(p0.z, a.z, 0.75));
      for (let r = 0; r <= RINGS; r++) {
        const u = r / RINGS, m = 1 - u, b0 = m * m * m, b1 = 3 * m * m * u, b2 = 3 * m * u * u, b3 = u * u * u;
        pt.set(b0 * p0.x + b1 * c1.x + b2 * c2.x + b3 * p3v.x, b0 * p0.y + b1 * c1.y + b2 * c2.y + b3 * p3v.y,
          b0 * p0.z + b1 * c1.z + b2 * c2.z + b3 * p3v.z);
        tan.set(3 * m * m * (c1.x - p0.x) + 6 * m * u * (c2.x - c1.x) + 3 * u * u * (p3v.x - c2.x),
          3 * m * m * (c1.y - p0.y) + 6 * m * u * (c2.y - c1.y) + 3 * u * u * (p3v.y - c2.y),
          3 * m * m * (c1.z - p0.z) + 6 * m * u * (c2.z - c1.z) + 3 * u * u * (p3v.z - c2.z)).normalize();
        bin.set(tan.y, -tan.x, 0).normalize();
        Z.crossVectors(bin, tan).normalize();
        const o = (i * AV + r * SIDES) * 3;
        for (let s = 0; s < SIDES; s++) {
          const ang = (s / SIDES) * Math.PI * 2, cs = Math.cos(ang), sn = Math.sin(ang), j = o + s * 3;
          Nn[j] = bin.x * cs + Z.x * sn; Nn[j + 1] = bin.y * cs + Z.y * sn; Nn[j + 2] = bin.z * cs + Z.z * sn;
          P[j] = pt.x + Nn[j] * rad; P[j + 1] = pt.y + Nn[j + 1] * rad; P[j + 2] = pt.z + Nn[j + 2] * rad;
        }
      }
      // the hand: procedural fingers ease between gestures; a Meshy glove swaps in with a squash, never a pop
      a.hand.pose(a.gesture, dt, a.tap);
      const key = glbKey(a.gesture), want = key && a.glb[key] ? key : null;
      if (want !== a.shown && a.pending === undefined) { a.pending = want; a.squashTo = 0.1; }
      if (a.pending !== undefined && a.squash.x < 0.22) { a.shown = a.pending; a.pending = undefined; a.squashTo = 1;
        for (const [k, m] of Object.entries(a.glb)) m.visible = k === a.shown; }
      const q = clamp(a.squash.step(a.squashTo, dt), 0.05, 1.4), hu = HU() * a.size;
      a.hand.root.position.copy(wpos).setZ(a.z); a.hand.root.quaternion.copy(a.quat); a.hand.root.scale.set(hu * (0.55 + 0.45 * q), hu * q, hu * q);
      a.hand.root.updateMatrixWorld(true);
      a.hand.parts.forEach((part, k) => {
        mtx.multiplyMatrices(part.node.matrixWorld, part.local);
        if (k === 1) palmMesh.setMatrixAt(i, a.shown ? ZERO : mtx);
        handMesh.setMatrixAt(i * HAND_PARTS + k, a.shown || k === 1 ? ZERO : mtx);
      });
    });
    armGeo.attributes.position.needsUpdate = true; armGeo.attributes.normal.needsUpdate = true; handMesh.instanceMatrix.needsUpdate = true;
    palmMesh.instanceMatrix.needsUpdate = true;

    // The tool follows the actual posed palm, not its spring's target: no slipping
    // grip while a wrist catches up during a state change.
    const grip = arms[ROLE.lens].hand.root;
    tmp.set(0.74, 0, 0).applyMatrix4(grip.matrixWorld);
    Y.set(0, -1, 0).applyQuaternion(arms[ROLE.lens].quat).normalize();
    lens.ring.position.copy(tmp).addScaledVector(Y, -(lensR.x + HL * 0.66));
    lens.ring.position.z = arms[ROLE.lens].z - 1;
    lens.handle.position.copy(lens.ring.position).addScaledVector(Y, lensR.x);
    lens.handle.rotation.z = Math.atan2(Y.y, Y.x) + Math.PI / 2;
    rigReady = true;

    // ---- the beam: from the eye, through the magnifier, out of the band toward the failure ---------
    beamMat.uniforms.uA.value = beamA.step(act.beam, dt);
    { const scan = state === 'thinking' ? Math.sin(ph * 1.7) * 0.17 * mv : 0, bd = D.clone().rotateAround(V2(0, 0), scan);
      const bp = beamGeo.attributes.position.array, ex = eye.x + gaze.x.x * 18 * S, ey = eye.y, len = Math.hypot(W, H), n = V2(-bd.y, bd.x), w1 = 110 * S + len * 0.05;
      bp.set([ex + n.x * 7, ey + n.y * 7, 0, ex - n.x * 7, ey - n.y * 7, 0, ex + bd.x * len + n.x * w1, ey + bd.y * len + n.y * w1, 0, ex + bd.x * len - n.x * w1, ey + bd.y * len - n.y * w1, 0]);
      beamGeo.attributes.position.needsUpdate = true; }

    // ---- draw: scene -> pomme's pass -> hue wash, over the beam ---------------------------------
    renderer.setRenderTarget(null); renderer.clear();
    if (beamMat.uniforms.uA.value > 0.004) renderer.render(beamScene, camera);
    renderer.render(scene, camera);
    debug.calls = renderer.info.render.calls; debug.tris = renderer.info.render.triangles;
    debug.frames = (debug.frames || 0) + 1;
    debug.attachError = 0;
    debug.rootError = 0;
    arms.forEach((a, i) => {
      const j = (i * AV + RINGS * SIDES) * 3, h = a.hand.root.position;
      debug.attachError = Math.max(debug.attachError, Math.hypot(P[j] - Nn[j] * rad - h.x,
        P[j + 1] - Nn[j + 1] * rad - h.y, P[j + 2] - Nn[j + 2] * rad - h.z));
      const rootOffset = i * AV * 3;
      tmp.copy(a.rootLocal).applyMatrix4(body.matrixWorld);
      debug.rootError = Math.max(debug.rootError, Math.hypot(P[rootOffset] - Nn[rootOffset] * rad - tmp.x,
        P[rootOffset + 1] - Nn[rootOffset + 1] * rad - tmp.y, P[rootOffset + 2] - Nn[rootOffset + 2] * rad - tmp.z));
    });
  }

  // the pass prints black until its three textures arrive: stay invisible until they have
  canvas.style.opacity = '0'; canvas.style.transition = 'opacity .5s';
  function syncRunning() {
    cancelAnimationFrame(raf); raf = 0;
    last = performance.now() / 1000;
    if (loaded && !disposed && !paused && onScreen && !document.hidden) raf = requestAnimationFrame(frame);
  }
  document.addEventListener('visibilitychange', syncRunning);
  loaded = true; syncRunning();
  requestAnimationFrame(() => { canvas.style.opacity = '1'; });

  return {
    react(next, el = null) {
      if (!STATES.includes(next)) next = 'idle';
      if (next !== state) {
        const calm = reduced ? 0 : 1;
        squashB.v += (next === 'summoned' ? 1.7 : next === 'stumped' ? 1.1 : next === 'verdict' ? 0.5 : 0.7) * calm;  // every change of mind begins with a squash
        if (next === 'summoned') hop.v += 150 * S * calm;                // the snap to attention
        if (next === 'stumped') {                                        // the shrug's little lift; and it turns AWAY from where the failure is
          hop.v += 70 * S * calm;
          awaySide = dirS.x.x > 0.12 ? -1 : dirS.x.x < -0.12 ? 1 : -1;
        }
        since = clock;
      }
      state = next; target = el;
    },
    lookAt(x, y) { rest.set(x, y); },
    pause() { paused = true; syncRunning(); },
    resume() { paused = false; syncRunning(); },
    dispose() {
      disposed = true; cancelAnimationFrame(raf); removeEventListener('pointermove', onMove); removeEventListener('resize', resize);
      document.removeEventListener('visibilitychange', syncRunning);
      if (ro) ro.disconnect(); if (io) io.disconnect();
      scene.traverse((o) => { if (o.geometry) o.geometry.dispose(); }); renderer.dispose();
    },
    // for harnesses and captures only
    _debug: () => ({ ...debug, state, S, W, H, age: clock - since, iris: [iris.position.x, iris.position.y], body: bodyPos.x.toArray(), yaw: yawS.x,
      hands: arms.map((a) => (a.wrist ? [a.wrist.x.x, a.wrist.x.y] : null)), gestures: arms.map((a) => a.gesture), glb: arms.map((a) => a.shown),
      lens: [lensPos.x.x, lensPos.x.y, lensR.x], lids: [lidU.x, lidL.x], glow: glowS.x, mv: mvS.x }),
  };
}
