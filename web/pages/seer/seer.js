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
  function setGlow(g, hot = 0) {           // 0 = dim lens .. 1 = bright; `hot` is charge gathering behind it
    const k = clamp(hot, 0, 1) ** 1.4;     // overdriven past white: the lens blows out as it fills
    scleraMat.color.setRGB(lerp(CREAM[0], 1.75, k), lerp(CREAM[1], 1.30, k), lerp(CREAM[2], 1.95, k));
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
// THE SHOT. Only a real Seer call fires it — `thinking` — never idle, which stays calm.
// charge: energy converges into the lens.  hold: everything stops. The pause is what sells the size.
// Under reduced motion the same beats run short and flat: no tremble, no strobe, but it still reads as a shot.
const SHOT = { charge: 0.68, hold: 0.18, travel: 0.05, fall: 0.14 };
const SHOT_REDUCED = { charge: 0.24, hold: 0.06, travel: 0.001, fall: 0.26 };
const bump = (a, b, x) => Math.sin(Math.PI * clamp((x - a) / (b - a), 0, 1)) ** 2;   // 0 -> 1 -> 0, zero value AND velocity at both ends

export async function mountSeer(canvas, { models = '/pages/seer/models/', generatedHands = false, reducedMotion = false } = {}) {
  const motionQuery = matchMedia('(prefers-reduced-motion: reduce)');
  let reduced = reducedMotion || motionQuery.matches;
  const overlay = createScanOverlay();
  // A LATE entrance is worse than no entrance. telemetry.html covers the page while this module loads, but
  // caps how long it may be blank; past the cap it shows the board and marks <html data-seer-late>. Arriving
  // after that and playing the entrance would hide a page the reader has already started using — the exact
  // flicker the cover exists to prevent, just later. So when we are late we skip straight to the end state.
  // Passing it as `reduced` to the stage is deliberate: the stage adds and removes body.seer-intro in one
  // synchronous block, so the board is never taken away for even a frame.
  const lateBoot = document.documentElement.hasAttribute('data-seer-late');
  let entrance = reduced || lateBoot ? INTRO_END : 0;
  const motionChanged = () => { reduced = reducedMotion || motionQuery.matches; if (reduced) { entrance = INTRO_END; overlay.hide(); } };
  motionQuery.addEventListener('change', motionChanged);
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(1);              // the pass works in CSS px; a retina screen must not cost 4x on a data page
  renderer.setClearColor(0x000000, 0);
  renderer.autoClear = false;
  renderer.info.autoReset = false;
  const introStage = createIntroStage(canvas, reduced || lateBoot);
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
  // The wedge's cross-section is solved from world position against the beam's own axis, NOT from the
  // quad's uv: a trapezoid is not affine, so linear uv interpolation across its two triangles puts a
  // visible KINK down the middle of a beam whose ends differ in width.
  const BEAM_SECTION = `varying vec2 vPos;
      uniform float uA, uT, uFront, uBlast, uHeat, uLen, uW0, uW1, uAim;
      uniform vec2 uOrigin, uAxis;`;
  const BEAM_VERT = 'varying vec2 vPos; void main(){ vPos = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }';
  const BEAM_SOLVE = `vec2 rel = vPos - uOrigin;
        float px = dot(rel, uAxis);                            // px from the lens, so the look is scale-free
        float x = px / uLen;
        float halfW = max(1.0, uW0 + (uW1 - uW0) * clamp(px / uAim, 0.0, 1.0));
        float y0 = dot(rel, vec2(-uAxis.y, uAxis.x)) / halfW;  // -1 .. 1 across the beam`;
  const BEAM_AMP = `step(0.0, px) * (1.0 - smoothstep(uFront - 46.0 / uLen, uFront, x)) * smoothstep(0.0, 26.0, px) * exp(-px / 4200.0)`;

  // One quad carries every layer — blown-out core, saturated body, soft bloom, running caustics and
  // edge shimmer are all analytic here, so a spectacular beam still costs exactly one draw call.
  const beamMat = new THREE.ShaderMaterial({ transparent: true, depthTest: false, depthWrite: false, side: THREE.DoubleSide,
    uniforms: { uA: { value: 0 }, uT: { value: 0 }, uFront: { value: 1 }, uBlast: { value: 0 }, uHeat: { value: 0 }, uLen: { value: 1000 },
      uOrigin: { value: new THREE.Vector2() }, uAxis: { value: new THREE.Vector2(0, -1) }, uW0: { value: 1 }, uW1: { value: 1 }, uAim: { value: 1000 } },
    vertexShader: BEAM_VERT,
    fragmentShader: `${BEAM_SECTION}
      void main(){
        ${BEAM_SOLVE}
        // heat shimmer: the EDGES boil, the core is held straight, and the boil grows with distance
        float wob = sin(px / 17.0 - uT * 11.0) + sin(px / 9.0 + uT * 7.3) * 0.6;
        float across = y0 + wob * 0.05 * uHeat * smoothstep(20.0, 260.0, px) * smoothstep(0.04, 0.45, abs(y0));
        float c = abs(across);
        float edge = 1.0 - smoothstep(0.22, 1.0, c);           // fade to nothing INSIDE the quad: no polygon edge
        float core = exp(-c * c * 300.0);                      // blown-out white, a hair wide
        float body = exp(-pow(c * 1.75, 3.0));                 // saturated inner body: flat-topped, so it reads as a COLUMN
        float halo = exp(-c * c * 1.6) * edge;                 // soft outer bloom
        // fringe/caustics running out along its length
        float fringe = (0.5 + 0.5 * sin(px / 26.0 - uT * 15.0 + sin(px / 140.0) * 2.2))
                     * (0.7 + 0.3 * sin(px / 11.0 - uT * 26.0));
        body *= 0.86 + 0.28 * fringe;          // the caustics ride the BODY; the core stays one clean line
        // the front lances out: a bright leading edge, and nothing at all beyond it
        float lead = exp(-pow((uFront - x) * uLen / 46.0, 2.0)) * (0.35 + uBlast);
        vec2 g = gl_FragCoord.xy / 7.0; vec2 hd = fract(vec2(g.x + floor(g.y) * 0.5, g.y)) - 0.5;  // halftone dots, like the page
        float dots = smoothstep(0.34, 0.2, length(hd)) * 0.55 + 0.45;
        float amp = uA * ${BEAM_AMP};
        body = (body + lead * 0.50) * amp; halo *= dots * amp;
        vec3 light = vec3(0.95, 0.30, 1.0) * body * 0.72
                   + vec3(0.42, 0.14, 1.0) * halo * 0.72;
        gl_FragColor = vec4(light, clamp(body * 0.42 + halo * 0.38, 0.0, 1.0));
      }`, blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneMinusSrcAlphaFactor });
  const beam = new THREE.Mesh(beamGeo, beamMat); beam.frustumCulled = false;
  const beamScene = new THREE.Scene(); beamScene.add(beam);
  // The windup, also under the character: arcs and motes spiral IN from the surrounding air and are
  // swallowed at the silhouette, with a ring that tightens onto the lens as the charge fills.
  const chargeMat = new THREE.ShaderMaterial({ transparent: true, depthTest: false, depthWrite: false,
    uniforms: { uC: { value: 0 }, uT: { value: 0 } },
    vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: `varying vec2 vUv; uniform float uC, uT;
      void main(){
        vec2 p = (vUv - 0.5) * 2.0; float r = length(p);
        float turn = atan(p.y, p.x);
        // comets: one per lane, each riding inward on a curved path and swallowed at the lens
        float mote = 0.0;
        for (int k = 0; k < 2; k++) {
          float n = 11.0 + float(k) * 7.0, off = float(k) * 0.37;
          float s = (turn + r * 2.4 + 3.14159265) / 6.2831853 * n;
          float sect = floor(s), lane = fract(s) - 0.5;
          float phase = fract(uT * (1.25 + off) + fract(sin(sect * 12.9898 + off) * 43758.5453));
          float dr = r - (1.1 - phase * 1.1);                  // <0 ahead of the head, >0 in its tail
          mote += (exp(-dr * dr * 190.0) + 0.45 * exp(-max(dr, 0.0) * 7.0) * step(0.0, dr))
                * exp(-lane * lane * 34.0) * smoothstep(0.0, 0.14, phase) * smoothstep(1.0, 0.82, phase);
        }
        // logarithmic-spiral filaments, sweeping in behind them
        float arc = pow(0.5 + 0.5 * sin(turn * 5.0 - log(max(r, 0.05)) * 5.0 + uT * 3.4), 16.0)
                  * smoothstep(1.0, 0.25, r) * smoothstep(0.04, 0.26, r);
        float ring = exp(-pow((r - (1.02 - 0.88 * uC)) * 16.0, 2.0));   // the iris of light, tightening
        float e = (mote * 0.95 + arc * 0.5 + ring * 0.5) * pow(uC, 1.5) * smoothstep(1.0, 0.86, r);
        vec3 col = mix(vec3(0.5, 0.16, 1.0), vec3(1.0, 0.6, 0.95), smoothstep(0.6, 0.08, r));
        gl_FragColor = vec4(col * e, clamp(e * 0.85, 0.0, 1.0));
      }`, blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneMinusSrcAlphaFactor });
  const chargeRing = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), chargeMat);
  chargeRing.frustumCulled = false; chargeRing.visible = false; beamScene.add(chargeRing);
  // THE BRIGHT HALF OF THE BEAM LIVES ABOVE THE PAGE, not in the canvas. The band's heading, the nav and
  // the board all paint over #seer, so anything drawn inside it is occluded by the page no matter what the
  // render order is. The soft wide bloom stays in the canvas UNDER the character (a low-alpha wash over the
  // flat illustration prints as ink); the hot core is stroked onto a fixed layer that nothing covers, and
  // composited with 'lighter', which can only ADD light and so can never grey anything out.
  const coreLayer = document.createElement('canvas');
  coreLayer.setAttribute('aria-hidden', 'true'); coreLayer.dataset.seerBeam = '';
  coreLayer.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:9;display:none';
  document.body.append(coreLayer);
  const coreCtx = coreLayer.getContext('2d');
  // half-width as a fraction of the beam's, alpha, colour — widest and faintest first. This carries the
  // FULL beam now, wide bloom included: the bloom was the part the board was still swallowing, and up
  // here 'lighter' can only add light, so it cannot grey the illustration the way an under-canvas wash would.
  const CORE_LAYERS = [[1.30, 0.09, '138,42,214'], [1.00, 0.15, '168,58,255'], [0.70, 0.22, '168,58,255'],
    [0.30, 0.36, '236,104,255'], [0.13, 0.55, '255,168,255'], [0.058, 0.85, '255,226,255'], [0.024, 1.0, '255,255,255']];
  // ...and a solid BODY, not a filament. Drawn source-over so it genuinely occludes: additive alone washes
  // out to transparent over bright pixels, and at these fractions the beam blocks the page it crosses
  // rather than tinting it. 0.86 of the half-width is opaque, so what you see is a solid object.
  const SOLID_CORE = [[0.86, '214,74,255'], [0.58, '255,190,255'], [0.32, '255,255,255']];
  let coreW = 0, coreH = 0, coreShown = false;
  function drawBeamCore(sx, sy, dx, dy, w0, w1, aim, len, amp, hot, time) {
    if (amp <= 0.004) { if (coreShown) { coreLayer.style.display = 'none'; coreShown = false; } return; }
    const vw = innerWidth, vh = innerHeight;
    if (coreW !== vw || coreH !== vh) { coreW = coreLayer.width = vw; coreH = coreLayer.height = vh; }
    if (!coreShown) { coreLayer.style.display = 'block'; coreShown = true; }
    coreCtx.setTransform(1, 0, 0, 1, 0, 0);
    coreCtx.clearRect(0, 0, vw, vh);
    coreCtx.globalCompositeOperation = 'lighter';
    const nx = -dy, ny = dx, x0 = sx + dx * 26, y0 = sy + dy * 26;      // starts clear of the lens
    const x1 = sx + dx * len, y1 = sy + dy * len;                       // and on, off the screen
    // EXPONENTIAL FLARE. A straight wedge puts most of its spread past the target, where nobody sees it —
    // it arrives narrow and widens off screen. This grows the half-width geometrically instead, so it
    // leaves the lens tight and is wider than the whole viewport BY the time it reaches the failure.
    // Growth stops just past the aim and runs parallel after that: left uncapped the exponent reaches
    // millions of pixels a few multiples out and the rasteriser gives up.
    const reach = Math.max(1, aim);
    const cover = Math.hypot(vw, vh) * 0.85;                            // past the failure it must exceed the screen
    const grow = Math.log(Math.max(1.2, cover / Math.max(1, w0)));
    // The beam OPENS and COLLAPSES; it does not switch on and off. Width follows its own envelope, eased,
    // so the shot flings the wedge open and then draws it back down to a thread as the light dies --
    // fading a full-screen slab at constant width just reads as a layer being turned off. Squared so the
    // collapse leads the fade: the beam is already narrowing while it is still bright.
    // Smootherstep, not smoothstep: it holds nearer full while the beam is live and then lets go faster,
    // so the collapse is a whip rather than a slow deflation.
    const e = Math.min(1, amp / 0.62), env = e * e * e * (e * (e * 6 - 15) + 10);
    // THE NECK. As the beam dies its width passes through a narrow band; brightening exactly there reads as
    // the energy concentrating into the last of it, so the shot goes out with a snap instead of a fade.
    const neck = Math.max(0, 1 - Math.abs(e - 0.3) / 0.26) ** 1.4;
    // ALIVE AT PEAK. A beam held at a fixed silhouette reads as a drawn shape. Two travelling waves plus a
    // slow swell, phase-shifted per layer so the layers do not move in lockstep and the thing has volume.
    // `time` is 0 under reduced motion, which freezes all of it.
    const halfAt = (u, phase) => w0 * Math.exp(grow * Math.min(u, 1.06)) * (0.05 + 0.95 * env)
      * (1 + 0.17 * Math.sin(u * 6.3 - time * 7.5 + phase)
           + 0.10 * Math.sin(u * 12.1 + time * 12 - phase * 1.7)
           + 0.06 * Math.sin(time * 3.1 + phase));
    const uEnd = Math.max(1.06, len / reach), STEPS = 26;   // an exponential edge facets badly below ~20
    const wedge = (f) => {
      const phase = f * 3.1;
      coreCtx.beginPath();
      for (let i = 0; i <= STEPS; i++) {
        const u = (i / STEPS) * uEnd, d = 26 + u * reach, hw = halfAt(u, phase) * f;
        const cx = sx + dx * d, cy = sy + dy * d;
        if (i === 0) coreCtx.moveTo(cx + nx * hw, cy + ny * hw); else coreCtx.lineTo(cx + nx * hw, cy + ny * hw);
      }
      for (let i = STEPS; i >= 0; i--) {
        const u = (i / STEPS) * uEnd, d = 26 + u * reach, hw = halfAt(u, phase) * f;
        const cx = sx + dx * d, cy = sy + dy * d;
        coreCtx.lineTo(cx - nx * hw, cy - ny * hw);
      }
      coreCtx.closePath(); coreCtx.fill(); };
    for (const [f, a, rgbv] of CORE_LAYERS) {
      const alpha = Math.min(1, a * amp * (1 + hot * 0.7 + neck * 1.3));
      const g = coreCtx.createLinearGradient(x0, y0, x1, y1);
      g.addColorStop(0, `rgba(${rgbv},${alpha})`);
      g.addColorStop(0.45, `rgba(${rgbv},${alpha * 0.72})`);
      g.addColorStop(1, `rgba(${rgbv},0)`);
      coreCtx.fillStyle = g; wedge(f);
    }
    // The solid centre. Fades in with amp so a dying beam thins out instead of snapping off.
    coreCtx.globalCompositeOperation = 'source-over';
    const solid = Math.min(1, amp * 2.8 + neck * 0.55);   // a HELD beam sits near amp 0.34; it still has to be opaque
    for (const [f, rgbv] of SOLID_CORE) {
      const g = coreCtx.createLinearGradient(x0, y0, x1, y1);
      g.addColorStop(0, `rgba(${rgbv},${solid})`);
      g.addColorStop(0.82, `rgba(${rgbv},${solid * 0.92})`);
      g.addColorStop(1, `rgba(${rgbv},0)`);
      coreCtx.fillStyle = g; wedge(f);
    }
  }
  const hideBeamCore = () => { if (coreShown) { coreLayer.style.display = 'none'; coreShown = false; } };

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
  // The emitter itself. The beam stays UNDER the character (low-alpha light would print as ink), but the
  // light gathering in the lens and the flash on the frame it fires are bright and small, and belong in front.
  const muzzleMat = new THREE.ShaderMaterial({ transparent: true, depthTest: false, depthWrite: false,
    uniforms: { uG: { value: 0 }, uF: { value: 0 }, uDir: { value: new THREE.Vector2(0, -1) } },
    vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: `varying vec2 vUv; uniform float uG, uF; uniform vec2 uDir;
      void main(){
        vec2 p = (vUv - 0.5) * 2.0; float r = length(p);
        float gather = exp(-r * r * (150.0 - 95.0 * uG)) * uG * uG * 1.25    // a point of light, tightening as it fills
                     + exp(-r * r * 30.0) * uG * 0.5;                        // and the glow it throws on the face
        float ball = exp(-r * r * 90.0) * uF;                              // the flash itself: small and white-hot
        float ring = exp(-pow((r - (0.1 + (1.0 - uF) * 0.8)) * 13.0, 2.0)) * uF * 0.28;   // its shock front, running out
        float along = dot(p, uDir), across = dot(p, vec2(-uDir.y, uDir.x));
        float bar = exp(-across * across * 900.0) * exp(-along * along * 2.4) * (uF * 0.55 + uG * uG * 0.18);
        float cross = exp(-along * along * 700.0) * exp(-across * across * 9.0) * uF * 0.3;
        float e = (gather + ball + ring + bar + cross) * smoothstep(1.0, 0.7, r);
        vec3 col = mix(vec3(1.0, 0.52, 0.98), vec3(1.0), clamp(e * 1.6, 0.0, 1.0));
        gl_FragColor = vec4(col * e, clamp(e, 0.0, 1.0));
      }`, blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneFactor });
  const muzzle = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), muzzleMat);
  muzzle.frustumCulled = false; muzzle.visible = false; muzzle.renderOrder = 999; scene.add(muzzle);
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
    // Loaded HERE, not at the top: the gloves are opt-in and off by default, so a static import put 108 kB
    // of loader on the critical path of an entrance that never uses it.
    const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
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
  let lastEye = null;           // the pupil in CLIENT pixels, for a host that wants to draw from it (see eye())
  let state = 'idle', target = null, since = 0, clock = 0, ph = 0, last = performance.now() / 1000, lastDraw = 0, raf = 0, awaySide = -1;
  const pointer = new THREE.Vector2(NaN, NaN), rest = new THREE.Vector2(NaN, NaN);
  const bodyPos = new Spring3(new THREE.Vector3(), 60, 0.8), hop = new Spring(0, 170, 0.34), squashB = new Spring(0, 260, 0.4);
  const yawS = new Spring(0, 34, 0.8), pitchS = new Spring(0, 34, 0.85), rollS = new Spring(0, 40, 0.7), sagS = new Spring(0, 14, 0.9);
  const mvS = new Spring(ACT.idle.mv, 20, 1), rateS = new Spring(ACT.idle.rate, 20, 1), glowS = new Spring(ACT.idle.glow, 22, 1), typeS = new Spring(ACT.idle.typing, 20, 1);
  const gaze = new Spring3(new THREE.Vector3(), 90, 0.9), lidU = new Spring(0.36, 300, 1), lidL = new Spring(0.06, 300, 1);
  const dirS = new Spring3(new THREE.Vector3(0, -1, 0), 50, 0.85);      // where the thing is, eased
  const lensPos = new Spring3(new THREE.Vector3(), 34, 0.8), lensR = new Spring(34, 40, 0.9), lensH = new Spring3(new THREE.Vector3(0.45, -0.89, 0), 30, 0.85), beamA = new Spring(0, 40, 1);
  // the shot: seconds since a real Seer call, -1 when nothing is firing
  let shot = -1, shotFired = false, beamHot = 0, warmed = false;
  const kick = new Spring3(new THREE.Vector3(), 150, 0.42);   // recoil back along the beam axis, then settle
  const kickRoll = new Spring(0, 120, 0.45), kickPitch = new Spring(0, 130, 0.45);
  const ORIGIN = new THREE.Vector3(), bugAim = { land: null, bolt: false, power: 0 };
  const beamFrom = new THREE.Vector2(), beamAxis = new THREE.Vector2(0, -1);   // reported for harnesses
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
    // ---- the shot: charge, a held beat, release ------------------------------------------------
    const T = reduced ? SHOT_REDUCED : SHOT, REL = T.charge + T.hold;
    if (shot >= 0) { shot += dt; if (state !== 'thinking') shot = -1; }
    const live = shot >= 0;
    const charge = live ? smooth(0, T.charge, shot) : 0;                // energy converging into the lens
    const out = live ? shot - REL : -1;                                 // seconds since the release, <0 while winding up
    const held = live && shot >= T.charge && out < 0 ? 1 : 0;           // the stillness: everything stops before it goes
    const blast = out < 0 ? 0 : Math.exp(-out / T.fall);                // the release transient
    const flash = out < 0 ? 0 : Math.exp(-out / (reduced ? 0.14 : 0.07));   // the muzzle: gone before it can hide the beam
    const winding = live && out < 0 ? charge : 0;
    const front = out < 0 ? 0 : clamp(out / T.travel, 0, 1);            // the beam FRONT lancing out from the lens
    beamHot = Math.max(out < 0 ? 0 : 0.68 + blast * 0.42, beamHot * Math.exp(-dt * 2.4));  // snaps on, releases slowly
    const mv = mvS.step(act.mv, dt) * calm * (1 - held * 0.92);         // how much everything sways: 0 in a verdict — it HOLDS STILL
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
      +anticipation*.07-Math.sin(dock*Math.PI)*.035+winding*.06;
    // One shared body transform; arm roots are solved from its actual matrix below.
    const arrivalY = -(1 - emerge) * (H * .6) + bump(.8, 2, entrance) * 20 * S;
    const stageB = B.clone(); stageB.y += arrivalY + zapKick * 8 * S;
    // The gun shakes as it fills and goes DEAD STILL on the held beat; then the shot shoves the body back.
    const tremble = held ? 0 : winding ** 3 * calm;
    const kickP = kick.step(ORIGIN, dt);
    stageB.x += Math.sin(clock * 47) * 1.7 * S * tremble + kickP.x;
    stageB.y += Math.cos(clock * 39) * 1.3 * S * tremble + kickP.y;
    rig.position.copy(stageB); rig.scale.set(S * (1 + sq * 0.5), S * (1 - sq), S);
    aura.position.set(stageB.x, stageB.y - 45 * S, -700);
    aura.scale.setScalar(780 * S);
    auraMaterial.uniforms.energy.value = stumped ? 0 : opening * (stageFocus * 1.8 + zapKick * .8 + (state === 'thinking' ? .9 : .25) + winding * .45 + blast * .5);
    auraMaterial.uniforms.phase.value = ph * 1.6;
    const eye = V2(B.x + EYE.cx * S, B.y + EYE.cy * S);

    // ---- direction toward the real host target ------------------------------------------------
    let dir = null;
    // Aim at the overlay's own anchor when there is one, so the in-band beam and the scan line that
    // carries it down the page are exactly collinear and read as a single shot.
    const aimAt = target ? overlay.anchor(target) : null;
    // Aim from where the beam actually LEAVES (last frame's emitter), not the body centre: the pupil sits
    // well above it, and over a short throw that offset is a few degrees of miss.
    const from = rigReady ? beamFrom : eye;
    if (aimAt) { dir = local(aimAt.x, aimAt.y).sub(from); dir = dir.lengthSq() < 1 ? null : dir.normalize(); }
    else if (target && target.isConnected) {
      const r = target.getBoundingClientRect();
      if (r.width || r.height) { dir = local(r.left + r.width / 2, r.top + r.height / 2).sub(from); dir = dir.lengthSq() < 1 ? null : dir.normalize(); }
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
      // The pupil follows the pointer ANYWHERE on the page, not only while it happens to be inside the
      // band's own box: once the entrance is over the band is a short strip at the top, and gating on it
      // meant the eye went dead the moment you moved down to the thing you were actually reading. The
      // reach is tied to the viewport so a pointer at the bottom of a tall page still reads as a look
      // rather than pinning the eye at full deflection.
      const pp = !isNaN(rest.x) ? local(rest.x, rest.y) : (!isNaN(pointer.x) ? local(pointer.x, pointer.y) : null);
      if (pp) look = V2(clamp((pp.x - eye.x) / Math.max(300, W * 0.30), -1, 1),
                        clamp((pp.y - eye.y) / Math.max(240, innerHeight * 0.40), -1, 1));
      else look = V2(0, -.12); // At rest, meet the viewer rather than hunting nonexistent failures.
      // The EYE follows the pointer anywhere on the page. The BODY only leans while the pointer is over
      // Seer's own band, or at a target it was actually given. Leaning after a pointer that has moved
      // down the page made the character sink away to the side and stay there -- nothing brought it back
      // upright, because the pointer never returned to the band. yawS/pitchS spring the target, so
      // dropping the lean is a settle rather than a snap.
      const leaning = !isNaN(rest.x) || (!isNaN(pointer.x) && pointer.y >= cr.top && pointer.y <= cr.bottom) ? 1 : 0;
      yawT = look.x * 0.3 * leaning; pitchT = -look.y * 0.1 * leaning;
      rollT = Math.sin(ph*.7)*.015*mv;
      const scan = bump(1.55, 3.3, entrance);
      look.x = lerp(look.x, Math.sin((entrance - 1.6) * 3) * .95, scan);
      const aim = scanTarget && overlay.anchor(scanTarget);
      if (aim) look.lerp(V2(clamp((aim.x - cr.left - eye.x) / 240, -1, 1), -1), scanStrength);
      const bug = !reduced && stageFocus > 0 ? introBugs.target(entrance) : null;
      if (bug) look = V2(clamp((bug.x - eye.x) / (150 * S), -1, 1), clamp((-bug.y - eye.y) / (100 * S), -1, 1));
      else if(introStage.fullscreen)look.lerp(V2(-1,.15),bump(3.02,4.45,entrance));
      // Recomputed because the scan / bug / dock terms above have moved `look` since. The lean gate still
      // applies, but a target Seer was actually GIVEN -- the entrance scan, a scan target, a bug -- always
      // turns the body, whatever the pointer is doing.
      yawT = look.x * .3 * (aim || bug || scan > .001 ? 1 : leaning); rollT += (1 - emerge) * -.18;
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
    body.rotation.z += kickRoll.step(0, dt); body.rotation.x += kickPitch.step(0, dt);   // the shot's kick, on top of the pose
    // the lens: brightness, lids, starburst
    const glow = glowS.step(act.glow, dt);
    // the lens stays lit for as long as the beam is coming out of it, not just on the release frame
    const emitHot = Math.max(winding * winding, blast, Math.min(1, beamHot) * 0.45);
    setGlow(glow, emitHot);
    // the iris stops down as it charges, then flares wide open on the shot
    iris.scale.setScalar(1 - 0.34 * winding + blast * 0.26);
    burst.visible = false; // saved Sentry product-page illustration has no starburst
    const giveUp = stumped && age > 0.12 && age < 0.8;                  // a long slow blink as it gives up...
    const startle = state === 'summoned' && age < 0.5;                  // ...and eyes WIDE when it is summoned
    const blink = reduced || live ? 0 : blinkAmount(clock * (stumped ? 0.6 : 1));   // it does not blink mid-shot
    const lidShot = live ? (out < 0 ? 0.34 * charge : -0.9 * blast) : 0;            // squints down, then snaps wide
    setLids(Math.max(1 - opening, lidU.step(giveUp ? 0.97 : startle ? 0.02 : clamp(act.lids[0] + lidShot, 0, 1), dt), entrance < 3.3 ? 0 : blink), Math.max(lidL.step(giveUp ? 0.5 : act.lids[1], dt), entrance < 3.3 ? 0 : blink * 0.5));
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

    // The shot reaches the limbs last: they gather in toward the lens as it charges, and the recoil
    // travels out to the wrists a beat behind the body — the magnifier rides its own hand, so it follows.
    if (live || kickP.lengthSq() > 0.25) {
      const gx = B.x + D.x * 150 * S, gy = B.y + D.y * 150 * S;
      for (const a of arms) {
        const lag = 0.5 + 0.5 * hash(a.i, 9), pull = winding * 0.17 * lag;
        if (pull > 0.001) { a.target.x = lerp(a.target.x, gx, pull); a.target.y = lerp(a.target.y, gy, pull); }
        a.target.x += kickP.x * (0.8 + 0.7 * lag); a.target.y += kickP.y * (0.8 + 0.7 * lag);
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
    lastEye = { dx: eyeWorld.x, dy: -eyeWorld.y };   // kept relative to the canvas: eye() stays right after a scroll
    bugAim.power = winding;                       // the intro bolt stays the bug hunt's own; idle never winds up
    introBugs.draw(entrance, source, !reduced && state === 'idle' && stageFocus > 0, bugAim);
    const realTarget = aimAt ? target : null;
    const realPulse = !reduced && (state === 'summoned' || state === 'thinking') ? bump(.15, 1.3, age % 3.5) * .7 : 0;
    overlay.draw(source, state === 'idle' ? scanTarget : realTarget, state === 'idle' ? scanStrength : realPulse,
      state === 'idle' ? bump(.48, .95, scanTime) : 0, clock);
    const ambient = beamA.step(reduced || realTarget ? 0 : act.beam, dt);      // the old soft wedge, unchanged
    beamMat.uniforms.uA.value = Math.max(ambient, beamHot) * opening;
    // While it only watches, the beam hunts in wide arcs. A fired shot holds its aim: the wobble drops to a
    // drift, so the in-band beam and the scan line that carries it down the page stay on one axis.
    { const scan = state === 'thinking' ? Math.sin(ph * 1.7) * 0.17 * mv * (1 - Math.min(1, beamHot) * 0.95) : 0, bd = D.clone().rotateAround(V2(0, 0), scan);
      const bp = beamGeo.attributes.position.array, ex = eyeWorld.x, ey = eyeWorld.y, n = V2(-bd.y, bd.x);
      // It STOPS where it is aimed. Running on to the far corner of the screen reads as a miss, and the
      // landing point is where the impact is drawn.
      const span = Math.hypot(W, H);
      // It does not stop at the target — it runs on off the screen. The AIM distance still governs the
      // shape: the beam fans out to full width by the time it reaches the failure, then carries on at that
      // width, so the landing point still reads without the beam fanning out forever behind it.
      const aim = aimAt ? clamp(Math.hypot(aimAt.x - source.x, aimAt.y - source.y), 60, span) : span;
      const len = aimAt ? Math.min(span * 1.8, aim + span) : span;
      // Thick at the lens, and a divergence gentle enough that a long throw across the room layout does not
      // simply flood the viewport: most of the size should be present the moment it leaves the emitter.
      const w0 = 230 * S + blast * 70 * S;     // thick at the lens: a short throw must not make it a thread
      const w1 = w0 + ((186 * S + span * 0.062) * (1 + beamHot * 0.3 + blast * 0.5) - w0) * (aim / span);
      bp.set([ex + n.x * w0, ey + n.y * w0, 0, ex - n.x * w0, ey - n.y * w0, 0, ex + bd.x * len + n.x * w1, ey + bd.y * len + n.y * w1, 0, ex + bd.x * len - n.x * w1, ey + bd.y * len - n.y * w1, 0]);
      beamGeo.attributes.position.needsUpdate = true;
      beamFrom.set(ex, ey); beamAxis.copy(bd);
      beamMat.uniforms.uOrigin.value.set(ex, ey); beamMat.uniforms.uAxis.value.set(bd.x, bd.y);
      beamMat.uniforms.uW0.value = w0; beamMat.uniforms.uW1.value = w1; beamMat.uniforms.uAim.value = aim;
      beamMat.uniforms.uT.value = reduced ? 0 : clock;                          // reduced motion: no crawling caustics
      beamMat.uniforms.uFront.value = live ? front : 1;
      beamMat.uniforms.uBlast.value = blast;
      beamMat.uniforms.uHeat.value = reduced ? 0 : 0.55 + blast;
      beamMat.uniforms.uLen.value = len;
      // RELEASE. One frame: the beam exists, the muzzle flashes, and the body takes the shove.
      if (live && out >= 0 && !shotFired) {
        shotFired = true;
        const soft = reduced ? 0.16 : 1;
        kick.v.addScaledVector(tmp.set(-bd.x, -bd.y, 0), 260 * S * soft);
        kickRoll.v += -bd.x * 1.25 * soft; kickPitch.v += bd.y * 1.0 * soft;
        squashB.v += 1.35 * soft;
      }
      // the windup's converging arcs, and the light gathering at the emitter
      const gatherE = out < 0 ? winding : winding * Math.exp(-out / 0.05);
      chargeRing.visible = gatherE > 0.012;
      if (chargeRing.visible) {
        chargeRing.position.set(ex, ey, 0); chargeRing.scale.setScalar(Math.min(980 * S, H * 1.9));
        chargeMat.uniforms.uC.value = gatherE; chargeMat.uniforms.uT.value = reduced ? 0.35 : clock;
      }
      // A FIRED shot is carried entirely by the overlay above the page, so the in-canvas wedge stands down:
      // drawing both double-counted the bloom, and clipped its halftone dead at the canvas edge while the
      // overlay's smooth falloff carried on past it — a visible seam in the band layout. The soft ambient
      // wedge (summoned/verdict with nowhere to point) still renders here, under the character, as before.
      beam.visible = beamHot <= 0.004 && ambient * opening > 0.004;
      // the hot core, in client px, above every element on the page
      // `front` is the lance ramping OUT of the lens, so it only applies while a shot is in flight. Using
      // it unconditionally killed the overlay the instant the shot timeline reset on a state change --
      // front snapped to 0 and the beam vanished in a frame, however slowly beamHot was still decaying.
      // Off a shot, uA alone carries it, so the release actually gets to be seen.
      const coreAmp = Math.min(1.6, beamMat.uniforms.uA.value) * (out >= 0 ? front : 1);
      drawBeamCore(source.x, source.y, bd.x, -bd.y, w0, w1, aim, len, coreAmp, blast, reduced ? 0 : clock);
      const emitE = Math.max(gatherE, Math.min(1, beamHot) * 0.55);   // a live source while it fires
      muzzle.visible = emitE > 0.012 || flash > 0.012;
      if (muzzle.visible) {
        muzzle.position.set(ex, ey, 200); muzzle.scale.setScalar(250 * S * (1 + flash * 1.1));
        muzzleMat.uniforms.uG.value = emitE; muzzleMat.uniforms.uF.value = flash;
        muzzleMat.uniforms.uDir.value.set(bd.x, bd.y);
      }
    }

    // Build the beam's shader programs on the first drawn frame, at zero energy and so invisible: a
    // program compiled here costs a frame nobody is watching, instead of the frame the shot goes off.
    if (!warmed && rigReady) {
      warmed = true; chargeRing.visible = true; muzzle.visible = true; beam.visible = true;
      chargeMat.uniforms.uC.value = 0; muzzleMat.uniforms.uG.value = 0; muzzleMat.uniforms.uF.value = 0;
    }

    // ---- draw: original illustration palette, over the in-band beam ---------------------------
    lettering.update(W,H,entrance,reduced);
    introEffects.update(W,H,entrance,reduced,stageFocus);
    decor.update(W,H,stageFocus,clock,reduced,state);
    renderer.setRenderTarget(null); renderer.clear();
    if (beam.visible || chargeRing.visible) renderer.render(beamScene, camera);
    renderer.render(scene, camera);
    debug.shot = live ? shot : -1; debug.charge = charge; debug.blast = blast;
    debug.beamFrom = [beamFrom.x, beamFrom.y]; debug.beamAxis = [beamAxis.x, beamAxis.y];   // exactly what the quad was built from
    debug.beam = beamMat.uniforms.uA.value; debug.fired = live && shotFired;
    debug.kick = Math.hypot(kickP.x, kickP.y);
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
    overlay.hide(); hideBeamCore();
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
        // a real Seer call is in flight: wind the lens up and fire. Only here — idle stays calm.
        if (next === 'thinking') { shot = 0; shotFired = false; beamHot = 0; }
        since = clock;
      }
      state = next; target = el;
      introStage.react(state);
      overlay.hide();
    },
    lookAt(x, y) { rest.set(x, y); },
    // Where the beam comes OUT, in client pixels. Null until one frame has been drawn; afterwards it
    // is recomputed from the canvas's CURRENT box, so it stays right while the loop is stopped (the
    // band scrolls away and the loop pauses). /telemetry's laser fires from exactly here.
    eye() {
      if (!lastEye) return null;
      const r = canvas.getBoundingClientRect();
      return { x: r.left + lastEye.dx, y: r.top + lastEye.dy };
    },
    pause() { paused = true; introStage.finish(); syncRunning(); },
    resume() { paused = false; syncRunning(); },
    dispose() {
      disposed = true; cancelAnimationFrame(raf); removeEventListener('pointermove', onMove); removeEventListener('resize', resize);
      document.removeEventListener('visibilitychange', syncRunning);
      motionQuery.removeEventListener('change', motionChanged); coreLayer.remove(); overlay.dispose(); introBugs.dispose(); introEffects.dispose(); lettering.dispose(); decor.dispose(); introStage.dispose();
      if (ro) ro.disconnect(); if (io) io.disconnect();
      for (const s of [scene, beamScene]) s.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
      renderer.dispose();
    },
    // for harnesses and captures only
    _debug: () => ({ ...debug, state, S, W, H, entrance, stageFocus, decorativeAssets:decor.debug(), introShapes:introEffects.debug(), roll:body.rotation.z, propsVisible: keyboard.visible || lens.g.visible || panels.some(p => p.visible), scan: overlay.debug(), age: clock - since, iris: [iris.position.x, iris.position.y], body: bodyPos.x.toArray(), yaw: yawS.x,
      hands: arms.map((a) => (a.wrist ? [a.wrist.x.x, a.wrist.x.y] : null)), gestures: arms.map((a) => a.gesture), glb: arms.map((a) => a.shown),
      lens: [lensPos.x.x, lensPos.x.y, lensR.x], lids: [lidU.x, lidL.x], glow: glowS.x, mv: mvS.x }),
  };
}
