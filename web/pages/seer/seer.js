// seer.js — Seer, embodied (docs/26-seer-embodied.md is the authority).
//
// A WATCHER in the telemetry page's hero band: one large lens on a body, and a posture that
// changes with what it is doing. Entrance/idle scans are decorative; host states mean:
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
// eye (pale sclera, low purple pupil, fine upper lid) and a curved lower silhouette,
// behind it, eight boneless NOODLE ARMS, four-digit GLOVE HANDS, a keyboard, a magnifying glass, a
// beam of light from the eye. Arms are solved curves and the eye tracks, blinks and squints
// with everything eased. Art direction follows the main illustration linked by
// the user's saved Downloads/sentry product page:
// https://sentry.io/astro-assets/images/products/Adjustred-Ratio_Seer-Illustration.jpg
// This reference differs from the earlier blog artwork: it has no starburst.
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
// has a pointer-transparent scan overlay. `el` only says WHERE the thing is: eye, beam and hands turn that way.
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
import { createScanOverlay } from './scan-overlay.js';
import { createIntroStage } from './intro-stage.js';
import { createIntroBugs } from './intro-bugs.js';
import { createLettering } from './lettering.js';
import { createDecor } from './decor.js';
import { createIntroEffects } from './intro-effects.js';

// ---- original illustration palette, converted from sRGB to linear ----------------------------
const rgb = hex => new THREE.Color(hex).toArray();
const FACE = rgb('#ef42c6'), FACE_TOP = rgb('#d918cb');
const SIDE_L = rgb('#991dac'), SIDE_R = rgb('#c422c4'), UNDER = rgb('#701185');
const RAY = rgb('#fff0d3');
const CREAM = rgb('#f3ffd2'), IRIS = rgb('#352047'), PUPIL = rgb('#352047'), WHITE = rgb('#806391');
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
function illustration(material, sculpted = false, bodyPigment = false) {
  material.onBeforeCompile = shader => {
    shader.vertexShader = shader.vertexShader.replace('#include <common>',
      '#include <common>\nvarying vec3 vPigment;\nvarying vec3 vPaper;')
      .replace('#include <project_vertex>', '#include <project_vertex>\nvPigment = mvPosition.xyz;\nvPaper = position;');
    shader.fragmentShader = shader.fragmentShader.replace('#include <common>', `#include <common>
      varying vec3 vPigment;
      varying vec3 vPaper;
      float pigment(vec2 p) { return fract(sin(dot(p, vec2(12.9898,78.233))) * 43758.5453); }`)
      .replace('#include <opaque_fragment>', `${sculpted ? `
        float illustrationLight = smoothstep(-0.35, 0.9, dot(normal, normalize(vec3(-0.45, 0.65, 0.7))));
        // Printed gradients, not shiny plastic or sharply separated cel bands.
        vec3 shadowInk = vec3(0.47, 0.015, 0.42);
        vec3 pinkInk = vec3(0.86, 0.055, 0.565);
        vec3 lightInk = vec3(0.94, 0.17, 0.65);
        outgoingLight = mix(shadowInk, pinkInk, smoothstep(0.12, 0.75, illustrationLight));
        outgoingLight = mix(outgoingLight, lightInk, smoothstep(0.65, 1.0, illustrationLight) * 0.4);
      ` : ''}
      // Shared screen-space pigment scale keeps the arm/palm join invisible.
      float fleck = pigment(floor(vPigment.xy * 1.8));
      float grainWeight = 0.22;
      ${sculpted ? `
        float skinLight = smoothstep(0.25, 0.92, dot(normal, normalize(vec3(-0.45, 0.65, 0.7))));
        float rim = smoothstep(0.12, 0.82, 1.0 - abs(normal.z));
        grainWeight = clamp(0.12 + rim * 0.88 + (1.0 - skinLight) * 0.22, 0.0, 1.0);
        float lightDots = step(1.0 - skinLight * 0.075 * grainWeight, fleck);
        float darkDots = step(fleck, (0.012 + (1.0 - skinLight) * 0.055) * grainWeight);
        outgoingLight = mix(outgoingLight, vec3(0.94, 0.34, 0.64), lightDots * 0.32);
        outgoingLight = mix(outgoingLight, vec3(0.28, 0.025, 0.32), darkDots * 0.38);
      ` : ''}
      ${bodyPigment ? `
        float foot = 1.0 - smoothstep(-87.0, 20.0, vPaper.y);
        float crown = smoothstep(25.0, 110.0, vPaper.y);
        float halfWidth = max(1.0, (110.0 - vPaper.y) * 132.0 / 177.0 + 5.0);
        float sideDistance = (halfWidth - abs(vPaper.x)) * 0.8;
        float bottomDistance = vPaper.y + 87.0 - 20.0 * pow(vPaper.x / 132.0, 2.0);
        float edge = 1.0 - smoothstep(4.0, 48.0, min(sideDistance, bottomDistance));
        grainWeight = clamp(0.10 + edge * 0.75 + crown * 0.25 + foot * 0.22, 0.0, 1.0);
        // Grain density, not a blurred overlay, blends the printed pigments.
        float lightDots = step(1.0 - foot * 0.12 * grainWeight, fleck);
        float darkDots = step(fleck, (0.01 + crown * 0.045 + edge * 0.025) * grainWeight);
        outgoingLight = mix(outgoingLight, vec3(0.64, 0.37, 0.28), foot * 0.18 * smoothstep(0.0, 40.0, vPaper.z));
        outgoingLight = mix(outgoingLight, vec3(0.94, 0.34, 0.64), lightDots * 0.36);
        outgoingLight = mix(outgoingLight, vec3(0.28, 0.025, 0.32), darkDots * 0.42);
      ` : ''}
      outgoingLight *= 1.0 + (fleck - 0.5) * 0.10 * grainWeight;
      outgoingLight = mix(outgoingLight, vec3(0.52, 0.06, 0.43), step(1.0 - 0.008 * grainWeight, fleck) * 0.22);
      #include <opaque_fragment>`);
  };
  material.customProgramCacheKey = () => `seer-original-pigment-${sculpted}-${bodyPigment}`;
  return material;
}
illustration(FLAT); illustration(SKIN, true);
const BODY = illustration(new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide }), false, true);

// ---- the eye's almond: two parabolas ---------------------------------------------------------
const EYE = { w: 66, hT: 43, hB: 8, cx: 0, cy: -3 };
const yTop = (x) => EYE.hT * (1 - (x / EYE.w) ** 2), yBot = (x) => -EYE.hB * (1 - (x / EYE.w) ** 2);

function buildBody() {
  const body = new THREE.Group();
  // Reference body: about 455 x 340 px, eye about 235 x 89 px.
  // 264 x 197 model units preserves its 1.34 width/height silhouette ratio.
  const A = new THREE.Vector2(0, 110), L = new THREE.Vector2(-132, -67), R = new THREE.Vector2(132, -67), D = 40;
  // front face with the almond cut OUT of it: the eye sits behind, so the face itself masks the
  // iris and the lids (no stencil, and a few px of real depth when the body turns)
  const shape = new THREE.Shape(), corners = [L, R, A], inset = 13;
  // Round the actual silhouette, including its side walls, not just a painted edge.
  for (let i = 0; i < corners.length; i++) {
    const c = corners[i], prev = corners[(i + 2) % 3], next = corners[(i + 1) % 3];
    const enter = c.clone().add(prev.clone().sub(c).normalize().multiplyScalar(inset));
    const leave = c.clone().add(next.clone().sub(c).normalize().multiplyScalar(inset));
    if (i === 0) shape.moveTo(enter.x, enter.y);
    else if (i === 1) shape.quadraticCurveTo(0, -107, enter.x, enter.y);
    else shape.quadraticCurveTo(88, 35, enter.x, enter.y);
    shape.quadraticCurveTo(c.x, c.y, leave.x, leave.y);
  }
  const first = L.clone().add(A.clone().sub(L).normalize().multiplyScalar(inset));
  shape.quadraticCurveTo(-88, 35, first.x, first.y);
  shape.closePath();
  const rim = shape.getPoints(8);
  const hole = new THREE.Path();
  hole.moveTo(EYE.cx - EYE.w, EYE.cy); hole.quadraticCurveTo(EYE.cx, EYE.cy + 2 * EYE.hT, EYE.cx + EYE.w, EYE.cy);
  hole.quadraticCurveTo(EYE.cx, EYE.cy - 2 * EYE.hB, EYE.cx - EYE.w, EYE.cy);
  shape.holes.push(hole);
  const front = paint(new THREE.ShapeGeometry(shape, 28), FACE).translate(0, 0, D);
  { const pos = front.attributes.position, col = front.attributes.color;   // colour is linear in y: exact on any triangulation
    for (let i = 0; i < pos.count; i++) { const u = smooth(-94, 122, pos.getY(i)); for (let k = 0; k < 3; k++) col.array[i * 3 + k] = lerp(FACE[k], FACE_TOP[k], u * 0.8); } }
  // the rest of the pyramid: two flanks and an underside meeting at a back apex
  const back = [0, -67, -250], tri = (p, q, r, c) => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute([...p, ...q, ...r], 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1], 2));
    g.setIndex([0, 1, 2]); g.computeVertexNormals();
    return paint(g, c);
  };
  const flanks = rim.slice(1).map((p, i) => {
    const q = rim[i], c = p.y < -90 && q.y < -90 ? UNDER : p.x + q.x < 0 ? SIDE_L : SIDE_R;
    return tri([q.x, q.y, D], [p.x, p.y, D], back, c);
  });
  body.add(new THREE.Mesh(mergeGeometries([front, ...flanks], false), BODY));
  // the starburst and the sclera carry the LENS'S BRIGHTNESS: dim when there is nothing to look at, bright when it thinks
  const rayMat = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }), scleraMat = new THREE.MeshBasicMaterial();
  illustration(rayMat); illustration(scleraMat);
  const star = new THREE.Shape(), spikes = 12, step = Math.PI * 2 / spikes;
  const radial = (angle, radius) => [EYE.cx + Math.cos(angle) * radius, EYE.cy + Math.sin(angle) * radius];
  star.moveTo(...radial(0, 87));
  for (let i = 0; i < spikes; i++) {
    const a = i * step;
    star.quadraticCurveTo(...radial(a + step * 0.18, 58), ...radial(a + step * 0.5, 58));
    star.quadraticCurveTo(...radial(a + step * 0.82, 58), ...radial(a + step, (i % 2 ? 87 : 79)));
  }
  star.closePath(); star.holes.push(hole.clone());
  const burst = new THREE.Mesh(new THREE.ShapeGeometry(star, 8).translate(0, 0, D + 0.6), rayMat);
  body.add(burst);

  // the eye, a few px behind the face
  const eye = new THREE.Group(); eye.position.set(EYE.cx, EYE.cy, D);
  eye.add(new THREE.Mesh(new THREE.ShapeGeometry(new THREE.Shape(hole.getPoints(48)), 1)
    .translate(-EYE.cx, -EYE.cy, -6), scleraMat));
  const irisGeo = new THREE.CircleGeometry(34, 48);
  paint(irisGeo, IRIS);
  const irisColors = irisGeo.attributes.color, irisPos = irisGeo.attributes.position;
  for (let i = 0; i < irisPos.count; i++) {
    const u = clamp((irisPos.getY(i) + 34) / 68, 0, 1);
    irisColors.setXYZ(i, lerp(IRIS[0] * 0.55, WHITE[0], u), lerp(IRIS[1] * 0.55, WHITE[1], u), lerp(IRIS[2] * 0.55, WHITE[2], u));
  }
  const iris = new THREE.Mesh(irisGeo, FLAT);
  iris.position.z = -5;
  const glint = new THREE.Mesh(paint(new THREE.CircleGeometry(5, 16), rgb('#a18aac')), FLAT);
  glint.position.set(11, 19, 0.3); glint.scale.set(1.5, 0.55, 1); glint.rotation.z = -0.5;
  iris.add(glint);
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
function buildPalmGeometry() {
  // One continuous skin from the noodle's radius into the hand. No separate
  // cuff and no balloon-shaped glove palm. The first ring overlaps the arm.
  const sections = [
    [-0.18, 0.2375, 0.2375], [0, 0.2375, 0.2375], [0.22, 0.29, 0.25],
    [0.5, 0.40, 0.29], [0.82, 0.49, 0.31], [1.08, 0.47, 0.29],
    [1.3, 0.32, 0.22], [1.42, 0, 0],
  ];
  const positions = [], indices = [], sides = 16;
  sections.forEach(([x, width, depth], r) => {
    for (let s = 0; s < sides; s++) {
      const a = s / sides * Math.PI * 2;
      positions.push(x, Math.cos(a) * width, Math.sin(a) * depth);
      if (r) { const p = (r - 1) * sides + s, n = (r - 1) * sides + (s + 1) % sides;
        indices.push(p, n, p + sides, n, n + sides, p + sides); }
    }
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(indices); geometry.computeVertexNormals();
  return geometry;
}
function buildHand() {
  const root = new THREE.Group(), parts = [];
  const cap = (node, len, thick, wide, x = 0) => parts.push({ node,
    local: new THREE.Matrix4().compose(new THREE.Vector3(x, 0, 0), new THREE.Quaternion(), new THREE.Vector3(len / 2, wide, thick)) });
  cap(root, 0.72, 0.49, 0.49, 0.15);                      // narrow continuous wrist, no glove cuff
  // The palm uses a unit-radius sphere, unlike the half-radius finger capsules.
  // Its transverse scales are radii: doubling them hides the finger articulation.
  cap(root, 1.28, 0.36, 0.61, 0.72);
  const fingers = [0.34, 0, -0.34].map((y, i) => {
    const mcp = new THREE.Group(); mcp.position.set(1.14, y, 0); root.add(mcp);
    const len = i === 1 ? 0.74 : 0.66;
    cap(mcp, len, 0.4, 0.4, len / 2 - 0.08);
    const pip = new THREE.Group(); pip.position.set(len - 0.17, 0, 0); mcp.add(pip);
    cap(pip, 0.62, 0.37, 0.37, 0.23);
    return { mcp, pip };
  });
  const tb = new THREE.Group(); tb.position.set(0.5, 0.46, -0.04); root.add(tb);
  cap(tb, 0.60, 0.46, 0.46, 0.22);
  const tt = new THREE.Group(); tt.position.set(0.44, 0, 0); tb.add(tt);
  cap(tt, 0.54, 0.42, 0.42, 0.19);
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
  return { root, parts, pose, digits: [
    ...fingers.map(({ mcp, pip }) => ({ base: mcp, tip: pip, length: 0.54, radius: 0.20 })),
    { base: tb, tip: tt, length: 0.46, radius: 0.23 },
  ] };
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
  idle:     { mv: 0.65, rate: 1.0, glow: 0.45, lids: [0.06, 0.02], beam: 0,    typing: 3.8 },
  summoned: { mv: 0.65, rate: 1.0, glow: 0.8,  lids: [0.08, 0.0],  beam: 0.16, typing: 0 },
  thinking: { mv: 1.2,  rate: 2.1, glow: 1.0,  lids: [0.16, 0.02], beam: 0.34, typing: 11 },
  verdict:  { mv: 0.0,  rate: 0.0, glow: 0.9,  lids: [0.2, 0.04],  beam: 0.1,  typing: 0 },
  stumped:  { mv: 0.1,  rate: 0.4, glow: 0.0,  lids: [0.58, 0.1],  beam: 0,    typing: 0 },
};
const STATES = Object.keys(ACT);
const INTRO_END = 6.4;
const bump = (a, b, x) => Math.sin(Math.PI * clamp((x - a) / (b - a), 0, 1)) ** 2;   // 0 -> 1 -> 0, zero value AND velocity at both ends

export async function mountSeer(canvas, { models = '/pages/seer/models/', generatedHands = false, reducedMotion = false } = {}) {
  const motionQuery = matchMedia('(prefers-reduced-motion: reduce)');
  let reduced = reducedMotion || motionQuery.matches;
  const overlay = createScanOverlay();
  let entrance = reduced ? INTRO_END : 0;
  const motionChanged = () => { reduced = reducedMotion || motionQuery.matches; if (reduced) { entrance = INTRO_END; overlay.hide(); } };
  motionQuery.addEventListener('change', motionChanged);
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(1);              // the pass works in CSS px; a retina screen must not cost 4x on a data page
  renderer.setClearColor(0x000000, 0);
  renderer.autoClear = false;
  renderer.info.autoReset = false;
  const introStage = createIntroStage(canvas, reduced);
  const introBugs = createIntroBugs(introStage.fullscreen && !reduced);
  let stageFocus = introStage.update(0, reduced);

  const scene = new THREE.Scene(), camera = new THREE.OrthographicCamera(0, 1, 0, -1, -2000, 2000);
  const lettering = createLettering(scene, canvas);
  const introEffects = createIntroEffects(scene, introStage.fullscreen);
  const decor = createDecor(scene, introStage.fullscreen, SKIN, buildPalmGeometry);
  camera.position.z = 600;
  const outlineMaterial = new THREE.ShaderMaterial({ side: THREE.BackSide, depthWrite: false,
    uniforms: { viewport: { value: new THREE.Vector2(1, 1) }, lineWidth: { value: 0.7 } },
    vertexShader: `
      #include <common>
      uniform vec2 viewport;
      uniform float lineWidth;
      void main() {
        #include <beginnormal_vertex>
        #include <defaultnormal_vertex>
        #include <begin_vertex>
        #include <project_vertex>
        vec2 edge = transformedNormal.xy;
        gl_Position.xy += edge / max(length(edge), 0.001) * lineWidth * 2.0 / viewport * gl_Position.w;
      }`,
    fragmentShader: 'void main() { gl_FragColor = vec4(0.012, 0.006, 0.016, 1.0); }',
  });
  const outline = mesh => {
    const ink = mesh.isInstancedMesh ? new THREE.InstancedMesh(mesh.geometry, outlineMaterial, mesh.count)
      : new THREE.Mesh(mesh.geometry, outlineMaterial);
    if (mesh.isInstancedMesh) ink.instanceMatrix = mesh.instanceMatrix;
    ink.frustumCulled = false; ink.renderOrder = -1;
    mesh.add(ink);
  };
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
  const auraMaterial = new THREE.ShaderMaterial({ transparent: true, depthWrite: false,
    uniforms: { energy: { value: 0 }, phase: { value: 0 } },
    vertexShader: 'varying vec2 vUv; void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
    fragmentShader: `varying vec2 vUv; uniform float energy; uniform float phase;
      void main(){ vec2 p=(vUv-.5)*2.; float r=length(p);
        float halo=exp(-r*r*6.)*.23;
        float orbit=exp(-pow((r-(.51+sin(phase)*.018))*100.,2.))*.075;
        float a=(halo+orbit)*energy*smoothstep(1.,.75,r);
        gl_FragColor=vec4(mix(vec3(.38,.12,.85),vec3(.95,.22,.67),vUv.y),a); }`,
  });
  const aura = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), auraMaterial); scene.add(aura);
  outline(body.children[0]);
  const roots = ROOT_X.map((x) => new THREE.Vector3(x * 0.9, -65 + 8 * (x / 132) ** 2, 28));
  const panels = [buildPanel(), buildPanel()]; rig.add(...panels);
  const keyboard = buildKeyboard(); rig.add(keyboard);
  const armGeo = new THREE.BufferGeometry(), AV = (RINGS + 1) * SIDES;
  armGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(ARMS * AV * 3), 3));
  armGeo.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(ARMS * AV * 3), 3));
  { const ix = []; for (let a = 0; a < ARMS; a++) for (let r = 0; r < RINGS; r++) for (let s = 0; s < SIDES; s++) {
      const p = a * AV + r * SIDES + s, q = a * AV + r * SIDES + (s + 1) % SIDES; ix.push(p, p + SIDES, q, q, p + SIDES, q + SIDES); }
    armGeo.setIndex(ix); }
  const armMesh = new THREE.Mesh(armGeo, SKIN); armMesh.frustumCulled = false; scene.add(armMesh);
  const capsule = new THREE.CapsuleGeometry(0.5, 1, 5, 12).rotateZ(-Math.PI / 2);
  const handMesh = new THREE.InstancedMesh(capsule, SKIN, ARMS * HAND_PARTS); handMesh.frustumCulled = false;
  handMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage); scene.add(handMesh);
  const palmMesh = new THREE.InstancedMesh(buildPalmGeometry(), SKIN, ARMS);
  palmMesh.frustumCulled = false; palmMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage); scene.add(palmMesh);
  outline(armMesh); outline(handMesh); outline(palmMesh);
  // Each finger is one deforming skin, not two overlapping capsule surfaces.
  handMesh.visible = false;
  const fingerRings = 14, fingerSides = 10, fingerStride = (fingerRings + 1) * fingerSides;
  const fingerGeometry = new THREE.BufferGeometry();
  fingerGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(ARMS * 4 * fingerStride * 3), 3));
  fingerGeometry.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(ARMS * 4 * fingerStride * 3), 3));
  { const indices = [];
    for (let d = 0; d < ARMS * 4; d++) for (let r = 0; r < fingerRings; r++) for (let s = 0; s < fingerSides; s++) {
      const p = d * fingerStride + r * fingerSides + s, n = d * fingerStride + r * fingerSides + (s + 1) % fingerSides;
      indices.push(p, p + fingerSides, n, n, p + fingerSides, n + fingerSides);
    }
    fingerGeometry.setIndex(indices);
  }
  const fingerMesh = new THREE.Mesh(fingerGeometry, SKIN); fingerMesh.frustumCulled = false;
  scene.add(fingerMesh); outline(fingerMesh);
  const f0 = new THREE.Vector3(), f1 = new THREE.Vector3(), f2 = new THREE.Vector3(), f3 = new THREE.Vector3();
  const fc = new THREE.Vector3(), ft = new THREE.Vector3(), fn = new THREE.Vector3(), fb = new THREE.Vector3();
  const fingerP = fingerGeometry.attributes.position, fingerN = fingerGeometry.attributes.normal;
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
    outlineMaterial.uniforms.viewport.value.set(W, H);
    camera.right = W; camera.bottom = -H; camera.updateProjectionMatrix();
    const textReserve = W < 700 && canvas.closest('.seerband') ? 110 : 30;
    const bandScale = clamp(Math.min((H - textReserve) / 400, W / 720), 0.25, 1.25);
    const fullScale = Math.min(H / 490, W / (W < 700 ? 580 : 980));
    S = introStage.fullscreen ? fullScale : bandScale;
    xr = Math.min(W / 2 - 60 * S, 600 * S) / S;                        // how far out the hands may go, in S units
    if (!rigReady) bodyPos.x.set(introStage.fullscreen ? lerp(14 * S, W / 2, stageFocus) : W / 2,
      -(introStage.fullscreen ? H * lerp(W < 760 ? .32 : .43, .37, stageFocus) : 22 + 124 * S), 0);
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
    entrance = Math.min(INTRO_END, entrance + dt * (state === 'idle' ? 1.35 : 4));
    const previousFocus = stageFocus;
    stageFocus = introStage.update(entrance, reduced);
    if (previousFocus > 0 && stageFocus === 0 && entrance < 5.65) entrance = INTRO_END;
    if (stageFocus !== previousFocus && !introStage.fullscreen) resize();
    const emerge = smooth(.18, 1.45, entrance), opening = smooth(.95, 1.65, entrance);
    const play = Math.max(0, clock - 5.2) % 9;
    const settledIntro = smooth(5.4, INTRO_END, entrance);
    const inspect = state === 'idle' ? bump(0.6, 4.8, play) * calm * settledIntro : 0;
    const presentSignal = state === 'idle' ? bump(4.5, 8.7, play) * calm * settledIntro : 0;
    const scanTime = entrance < 5.2 ? entrance - 3.15 : play - 6.5;
    const scanStrength = 0; // Idle watches; beams require host interaction or the intro bug hunt.
    const scanTarget = scanStrength > 0 && stageFocus === 0 ? overlay.pick() : null;
    const mv = mvS.step(act.mv, dt) * calm;                             // how much everything sways: 0 in a verdict — it HOLDS STILL
    ph += dt * rateS.step(act.rate, dt) * calm;                         // reduced motion also freezes decorative traces and fingers
    renderer.info.reset();
    const cr = canvas.getBoundingClientRect();
    const local = (cx, cy) => V2(cx - cr.left, -(cy - cr.top));
    const stumped = state === 'stumped', engaged = state === 'summoned' || state === 'thinking' || state === 'verdict';

    // ---- body: anticipate, arc toward the edge, then settle ------------------------------------
    const sag = sagS.step(stumped ? 1 : 0, dt);
    const dock = introStage.fullscreen ? 1 - stageFocus : 0;
    const peek = state === 'thinking' ? 12 * S * (reduced ? 1 : .7 + Math.sin(ph * 1.8) * .3) : state === 'summoned' ? 8 * S : 0;
    const sideX = 14 * S + peek;
    const anticipation = introStage.fullscreen ? bump(2.9,3.65,entrance)*calm : 0;
    const settle = introStage.fullscreen ? bump(5.3,INTRO_END,entrance)*calm : 0;
    const centerX = introStage.fullscreen ? lerp(sideX,W/2,stageFocus)+anticipation*16*S-settle*7*S : W/2;
    const centerY = introStage.fullscreen ? H*lerp(W<760?.32:.43,.37,stageFocus)-Math.sin(dock*Math.PI)*48*S : 22+124*S;
    const home = tmp.set(centerX, -centerY + Math.sin(ph * 1.25) * 5 * S * mv - sag * 16 * S + hop.step(0, dt) + (stumped ? Math.sin(age * 0.9) * 2.2 * S * calm : 0), 0);
    bodyPos.k=introStage.fullscreen&&entrance<INTRO_END?85:60;
    const B = bodyPos.step(home, dt);
    const zapKick = introStage.fullscreen && state === 'idle' ? [1.75, 2.35, 2.95].reduce((a, t) => a + bump(t, t + .24, entrance), 0) : 0;
    const sq = squashB.step(0,dt)+sag*.05+bump(0,.65,entrance)*.18-bump(.55,1.55,entrance)*.10+zapKick*.06
      +anticipation*.07-Math.sin(dock*Math.PI)*.035;
    // One shared body transform; arm roots are solved from its actual matrix below.
    const arrivalY = -(1 - emerge) * (H * .6) + bump(.8, 2, entrance) * 20 * S;
    const stageB = B.clone(); stageB.y += arrivalY + zapKick * 8 * S;
    rig.position.copy(stageB); rig.scale.set(S * (1 + sq * 0.5), S * (1 - sq), S);
    aura.position.set(stageB.x, stageB.y - 45 * S, -700);
    aura.scale.setScalar(780 * S);
    auraMaterial.uniforms.energy.value = stumped ? 0 : opening * (stageFocus * 1.8 + zapKick * .8 + (state === 'thinking' ? .9 : .25));
    auraMaterial.uniforms.phase.value = ph * 1.6;
    const eye = V2(B.x + EYE.cx * S, B.y + EYE.cy * S);

    // ---- direction toward the real host target ------------------------------------------------
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
      else look = V2(0, -.12); // At rest, meet the viewer rather than hunting nonexistent failures.
      yawT = look.x * 0.3; pitchT = -look.y * 0.1;
      rollT = Math.sin(ph*.7)*.015*mv;
      const scan = bump(1.55, 3.3, entrance);
      look.x = lerp(look.x, Math.sin((entrance - 1.6) * 3) * .95, scan);
      const aim = scanTarget && overlay.anchor(scanTarget);
      if (aim) look.lerp(V2(clamp((aim.x - cr.left - eye.x) / 240, -1, 1), -1), scanStrength);
      const bug = !reduced && stageFocus > 0 ? introBugs.target(entrance) : null;
      if (bug) look = V2(clamp((bug.x - eye.x) / (150 * S), -1, 1), clamp((-bug.y - eye.y) / (100 * S), -1, 1));
      else if(introStage.fullscreen)look.lerp(V2(-1,.15),bump(3.02,4.45,entrance));
      yawT = look.x * .3; rollT += (1 - emerge) * -.18;
    }
    gaze.k = state === 'summoned' ? 170 : stumped ? 22 : 60;            // onto a failure: fast (still eased). Giving up: slow.
    const eyeTurn=-body.rotation.z,eyeCos=Math.cos(eyeTurn),eyeSin=Math.sin(eyeTurn);
    gaze.step(tmp.set(clamp(look.x*eyeCos-look.y*eyeSin,-1,1),clamp(look.x*eyeSin+look.y*eyeCos,-1,1),0),dt);
    iris.position.set(gaze.x.x * 16, -8 + gaze.x.y * 4, -5);
    // A mild three-quarter resting pose exposes the pyramid flank and underside.
    rollS.k=introStage.fullscreen&&entrance<INTRO_END?85:40;
    const turn=smooth(.08,.93,dock),settleRock=Math.sin((entrance-5.3)*9)*settle*.055;
    body.rotation.set(pitchS.step(pitchT * (1 - dock * .7) + .10, dt),
      yawS.step(lerp(yawT + .36, stumped ? -.4 : .08 + look.x * .08, dock), dt),
      rollS.step(rollT-turn*Math.PI/2+anticipation*.10+settleRock,dt)+Math.sin(ph*.9)*.025*mv);
    // the lens: brightness, lids, starburst
    const glow = glowS.step(act.glow, dt);
    setGlow(glow);
    burst.visible = false; // saved Sentry product-page illustration has no starburst
    const giveUp = stumped && age > 0.12 && age < 0.8;                  // a long slow blink as it gives up...
    const startle = state === 'summoned' && age < 0.5;                  // ...and eyes WIDE when it is summoned
    const blink = reduced ? 0 : blinkAmount(clock * (stumped ? 0.6 : 1));
    setLids(Math.max(1 - opening, lidU.step(giveUp ? 0.97 : startle ? 0.02 : act.lids[0], dt), entrance < 3.3 ? 0 : blink), Math.max(lidL.step(giveUp ? 0.5 : act.lids[1], dt), entrance < 3.3 ? 0 : blink * 0.5));
    panels[0].position.set(-xr * 0.62 + Math.sin(ph * 0.8) * 5 * mv, 96 + Math.sin(ph * 1.1 + 1) * 7 * mv, -60);
    panels[1].position.set(xr * 0.6 + Math.sin(ph * 0.7 + 2) * 5 * mv, 104 + Math.sin(ph * 0.9) * 6 * mv, -60); panels[1].scale.set(-0.86, 0.86, 1);
    panels[0].visible = panels[1].visible = false;
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
    keyboard.visible = false; lens.g.visible = false;
    keyboard.scale.setScalar(.65 + .35 * smooth(.9, 2.1, entrance));

    // ---- where every hand goes --------------------------------------------------------------------
    const at = (dx, dy) => V2(B.x + dx * S, B.y + dy * S);              // in S units from the body centre
    const drift = (i, ax, ay) => V2(Math.sin(ph * 0.55 + i * 1.9) * ax * S * mv, Math.sin(ph * 0.43 + i * 2.7) * ay * S * mv);
    const UPV = V2(0, 1), floorY = (-H + 8 - B.y) / S + 62;              // the band's floor, in S units below the body, less a hanging hand
    const droopY = Math.max(floorY, -262);
    for (const a of arms) { a.gesture = 'open'; a.sizeT = 1; a.tap = 0; }
    const typing = typeS.step(act.typing, dt) * calm;

    // the typists (procedural: their fingers move). Working = fast; idle = the odd key; stumped = slumped on the keys
    for (const [role, sx] of [[ROLE.typeL, -1], [ROLE.typeR, 1]]) {
      const a = arms[role]; a.sizeT = 1.4;
      if (stumped) { aimHand(a, at(sx * 62, -212), V2(sx * 0.42, -0.9), V2(-sx, 0)); a.gesture = 'limp'; continue; }
      const up = engaged && state !== 'thinking' ? 26 : 0;              // hands off the keys while it attends
      aimHand(a, at(sx * 78, -190 + up + Math.abs(Math.sin(ph * 2.2 + sx)) * 4 * mv), V2(sx * -0.16, -1), V2(-sx, 0));
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

    // With the props removed, the limbs form a breathing fan around the eye.
    // Staggered waves travel outward rather than pantomiming a keyboard or lens.
    if (state === 'idle' || state === 'thinking') {
      for (const a of arms) {
        const sx = a.i < 4 ? -1 : 1, rank = a.i < 4 ? a.i : 7 - a.i;
        const wave = Math.sin(ph * (state === 'thinking' ? 1.5 : .8) - rank * .8) * calm;
        const spread = [ .77, .60, .40, .18 ][rank];
        const height = [ 38, -55, -145, -206 ][rank];
        const palm = at(sx * xr * spread, height + wave * (18 + stageFocus * 20));
        aimHand(a, palm, V2(sx * (.85 - rank * .16), .5 - rank * .46 + wave * .14), UPV);
        a.gesture = rank === 0 ? 'splay' : 'open'; a.sizeT = rank === 3 ? 1.12 : 1; a.tap = 0;
      }
    }
    arms[ROLE.lens].gesture = stumped ? 'limp' : 'open';
    if (dock > 0 || (introStage.fullscreen && entrance > 3.05)) {
      for (const a of arms) {
        // Two separated edge gestures; the other arms trail out of view.
        const reach = a.i === ROLE.point && (state === 'thinking' || state === 'summoned');
        const border = a.i === ROLE.outerL || a.i === ROLE.outerR;
        const edgeSide = a.i === ROLE.outerL ? 1 : -1;
        // Alternating reach, open palm, and recoil. The lower hand follows later.
        const beat=ph*.95+(edgeSide>0?0:2.4);
        const extension=(.5+.5*Math.sin(beat))**2;
        const room=(W<760?12:Math.min(140,W*.13-55))*(edgeSide>0&&W>=760?.72:1);   // the upper hand stops short of the page title
        const tx = reach ? room*.8 : border ? room*(.52+.48*extension*mv)+Math.sin(beat*2)*3*mv : -(210+(a.i%3)*24)*S;
        const ty = border ? -clamp(-B.y-edgeSide*210*S,150*S,H-160*S)+(Math.sin(beat-.6)*15+extension*9)*S*mv
          : B.y+(a.i-3.5)*34*S+Math.sin(ph*1.3+a.i)*6*S*mv;
        const pull=border?Math.max(dock,smooth(3.05,4.4,entrance)):dock;
        a.target.lerp(tmp.set(tx,ty,a.z),pull);
        if(border){
          a.qT.setFromAxisAngle(Z.set(0,0,1),.12+Math.sin(beat-.65)*.24*mv);
          a.gesture=stumped?'limp':dock>.1&&dock<.85?'grip':extension>.65?'splay':'open';
        }
        if (reach) a.gesture = 'point';
      }
    }

    // ---- solve and skin the arms; pose and place the hands -------------------------------------
    const P = armGeo.attributes.position.array, Nn = armGeo.attributes.normal.array, rad = 9.5 * S;
    rig.updateMatrixWorld(true); // update the parent transform before root attachments
    arms.forEach((a, i) => {
      const unfurl = smooth(.35 + i * .095, 1.35 + i * .095, entrance);
      tmp.copy(a.rootLocal).applyMatrix4(body.matrixWorld);
      a.target.lerp(tmp.set(tmp.x + (i - 3.5) * 8 * S, tmp.y - 48 * S, a.z), 1 - unfurl);
      if (unfurl < .75) { a.gesture = 'limp'; a.tap = 0; }
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
      // Match the palm's +X axis in all three dimensions at the wrist.
      // Moving the last control point sideways created a visible elbow/seam.
      c2.copy(p3v).addScaledVector(X, -k2);
      if(dock>0){
        const border=i===ROLE.outerL||i===ROLE.outerR;
        // One clean sweep out of the edge, with delayed wrist follow-through.
        c1.lerp(tmp.set(-120*S,border?p3v.y-75*S:p0.y,p0.z),dock);
        c2.lerp(tmp.copy(p3v).addScaledVector(X,-(border?95:65)*S),dock);
      }
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
          const radius = rad * lerp(1, a.size, smooth(0.55, 1, u));
          P[j] = pt.x + Nn[j] * radius; P[j + 1] = pt.y + Nn[j + 1] * radius; P[j + 2] = pt.z + Nn[j + 2] * radius;
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
      a.hand.digits.forEach((digit, d) => {
        f0.set(-0.15, 0, 0).applyMatrix4(digit.base.matrixWorld);
        f1.set(0.28, 0, 0).applyMatrix4(digit.base.matrixWorld);
        f2.set(0.13, 0, 0).applyMatrix4(digit.tip.matrixWorld);
        f3.set(digit.length, 0, 0).applyMatrix4(digit.tip.matrixWorld);
        for (let r = 0; r <= fingerRings; r++) {
          const u = r / fingerRings, v = 1 - u;
          fc.copy(f0).multiplyScalar(v * v * v).addScaledVector(f1, 3 * v * v * u)
            .addScaledVector(f2, 3 * v * u * u).addScaledVector(f3, u * u * u);
          ft.copy(f1).sub(f0).multiplyScalar(3 * v * v);
          fn.copy(f2).sub(f1); ft.addScaledVector(fn, 6 * v * u);
          fn.copy(f3).sub(f2); ft.addScaledVector(fn, 3 * u * u).normalize();
          fn.set(0, 0, 1); if (Math.abs(ft.z) > 0.9) fn.set(0, 1, 0);
          fb.crossVectors(ft, fn).normalize(); fn.crossVectors(fb, ft).normalize();
          const cap = Math.sqrt(Math.max(0, 1 - Math.pow(Math.max(0, (u - 0.72) / 0.28), 2)));
          const radius = digit.radius * hu * cap;
          for (let s = 0; s < fingerSides; s++) {
            const angle = s / fingerSides * Math.PI * 2, cs = Math.cos(angle), sn = Math.sin(angle);
            const nx = fb.x * cs + fn.x * sn, ny = fb.y * cs + fn.y * sn, nz = fb.z * cs + fn.z * sn;
            const j = (i * 4 + d) * fingerStride + r * fingerSides + s;
            fingerP.setXYZ(j, fc.x + nx * radius, fc.y + ny * radius, fc.z + nz * radius);
            fingerN.setXYZ(j, nx, ny, nz);
          }
        }
      });
      a.hand.parts.forEach((part, k) => {
        mtx.multiplyMatrices(part.node.matrixWorld, part.local);
        if (k === 1) palmMesh.setMatrixAt(i, a.shown ? ZERO : a.hand.root.matrixWorld);
        handMesh.setMatrixAt(i * HAND_PARTS + k, a.shown || k <= 1 ? ZERO : mtx);
      });
    });
    armGeo.attributes.position.needsUpdate = true; armGeo.attributes.normal.needsUpdate = true; handMesh.instanceMatrix.needsUpdate = true;
    palmMesh.instanceMatrix.needsUpdate = true;
    fingerP.needsUpdate = true; fingerN.needsUpdate = true;

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
    // The pupil's centre sits below the almond mask; emit from its visible upper half.
    tmp.set(0, 18, 1).applyMatrix4(iris.matrixWorld);
    const eyeWorld = tmp.clone();
    const source = { x: cr.left + eyeWorld.x, y: cr.top - eyeWorld.y };
    introBugs.draw(entrance, source, !reduced && state === 'idle' && stageFocus > 0);
    const realTarget = target && overlay.anchor(target) ? target : null;
    const realPulse = !reduced && (state === 'summoned' || state === 'thinking') ? bump(.15, 1.3, age % 3.5) * .7 : 0;
    overlay.draw(source, state === 'idle' ? scanTarget : realTarget, state === 'idle' ? scanStrength : realPulse,
      state === 'idle' ? bump(.48, .95, scanTime) : 0, clock);
    beamMat.uniforms.uA.value = beamA.step(reduced || realTarget ? 0 : act.beam, dt) * opening;
    { const scan = state === 'thinking' ? Math.sin(ph * 1.7) * 0.17 * mv : 0, bd = D.clone().rotateAround(V2(0, 0), scan);
      const bp = beamGeo.attributes.position.array, ex = eyeWorld.x, ey = eyeWorld.y, len = Math.hypot(W, H), n = V2(-bd.y, bd.x), w1 = 110 * S + len * 0.05;
      bp.set([ex + n.x * 7, ey + n.y * 7, 0, ex - n.x * 7, ey - n.y * 7, 0, ex + bd.x * len + n.x * w1, ey + bd.y * len + n.y * w1, 0, ex + bd.x * len - n.x * w1, ey + bd.y * len - n.y * w1, 0]);
      beamGeo.attributes.position.needsUpdate = true; }

    // ---- draw: original illustration palette, over the in-band beam ---------------------------
    lettering.update(W,H,entrance,reduced);
    introEffects.update(W,H,entrance,reduced,stageFocus);
    decor.update(W,H,stageFocus,clock,reduced,state);
    renderer.setRenderTarget(null); renderer.clear();
    if (beamMat.uniforms.uA.value > 0.004) renderer.render(beamScene, camera);
    renderer.render(scene, camera);
    debug.calls = renderer.info.render.calls; debug.tris = renderer.info.render.triangles;
    debug.frames = (debug.frames || 0) + 1;
    debug.attachError = 0;
    debug.rootError = 0;
    arms.forEach((a, i) => {
      const j = (i * AV + RINGS * SIDES) * 3, h = a.hand.root.position;
      debug.attachError = Math.max(debug.attachError, Math.hypot(P[j] - Nn[j] * rad * a.size - h.x,
        P[j + 1] - Nn[j + 1] * rad * a.size - h.y, P[j + 2] - Nn[j + 2] * rad * a.size - h.z));
      const rootOffset = i * AV * 3;
      tmp.copy(a.rootLocal).applyMatrix4(body.matrixWorld);
      debug.rootError = Math.max(debug.rootError, Math.hypot(P[rootOffset] - Nn[rootOffset] * rad - tmp.x,
        P[rootOffset + 1] - Nn[rootOffset + 1] * rad - tmp.y, P[rootOffset + 2] - Nn[rootOffset + 2] * rad - tmp.z));
    });
  }

  canvas.style.opacity = '1';
  function syncRunning() {
    cancelAnimationFrame(raf); raf = 0;
    last = performance.now() / 1000;
    overlay.hide();
    introBugs.hide(); introEffects.hide();
    if (loaded && !disposed && !paused && onScreen && !document.hidden) raf = requestAnimationFrame(frame);
  }
  document.addEventListener('visibilitychange', syncRunning);
  loaded = true; syncRunning();

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
      introStage.react(state);
      overlay.hide();
    },
    lookAt(x, y) { rest.set(x, y); },
    pause() { paused = true; introStage.finish(); syncRunning(); },
    resume() { paused = false; syncRunning(); },
    dispose() {
      disposed = true; cancelAnimationFrame(raf); removeEventListener('pointermove', onMove); removeEventListener('resize', resize);
      document.removeEventListener('visibilitychange', syncRunning);
      motionQuery.removeEventListener('change', motionChanged); overlay.dispose(); introBugs.dispose(); introEffects.dispose(); lettering.dispose(); decor.dispose(); introStage.dispose();
      if (ro) ro.disconnect(); if (io) io.disconnect();
      scene.traverse((o) => { if (o.geometry) o.geometry.dispose(); }); renderer.dispose();
    },
    // for harnesses and captures only
    _debug: () => ({ ...debug, state, S, W, H, entrance, stageFocus, decorativeAssets:decor.debug(), introShapes:introEffects.debug(), roll:body.rotation.z, propsVisible: keyboard.visible || lens.g.visible || panels.some(p => p.visible), scan: overlay.debug(), age: clock - since, iris: [iris.position.x, iris.position.y], body: bodyPos.x.toArray(), yaw: yawS.x,
      hands: arms.map((a) => (a.wrist ? [a.wrist.x.x, a.wrist.x.y] : null)), gestures: arms.map((a) => a.gesture), glb: arms.map((a) => a.shown),
      lens: [lensPos.x.x, lensPos.x.y, lensR.x], lids: [lidU.x, lidL.x], glow: glowS.x, mv: mvS.x }),
  };
}
