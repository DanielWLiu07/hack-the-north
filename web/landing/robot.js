// robot.js — the Bracket Bot, built from primitives: it pops up from the bottom of the frame
// after GITRL has dropped, and catches its balance.
//
// Why primitives and not the scan: see HANDOFF-SPLAT.md. The photogrammetry mesh is one
// welded lump and the splat is a cloud; neither has a wheel that can turn. A Bracket Bot is
// a tall mast on two wheels with a lift carriage, two arms and a camera head, which is a
// dozen RIGID parts. So it is pomme's buildChain idea again (snakeArms.js): nested
// THREE.Groups, one per joint, each part rotating about its own joint. Nothing is skinned,
// nothing deforms, nothing can tear.
//
//   root                    on the floor, between the wheels. +Y up, +Z is FORWARD.
//   ├ wheelL / wheelR       rotation.x = roll. They do NOT lean with the body.
//   └ body                  at the axle. rotation.x = lean (it is an inverted pendulum)
//       └ mast              at its socket. rotation.x = the whip of a long tube in a short clamp
//           ├ lift          the carriage on the belt: position.y slides (a prismatic joint)
//           │   └ armL / armR   shoulder.rotation.x -> elbow.rotation.x -> wrist.rotation.x
//           └ headPan       rotation.y  -> headTilt rotation.x (and .z: it can cock its head)
//
// PROPORTIONS are the real machine's. Known from Bracket Bot's own code (docs/02-hardware.md):
// 165 mm wheels, 425 mm between them. Everything else is measured off the capture frames in
// wheel diameters: it stands 9.4 of them tall (1.55 m), the shoulders sit at 0.82 of that, the
// mast is under half a wheel thick. Units here are METRES; buildBracketBot({ height }) scales
// the root so the landing can ask for a height in world units, as splat.js does.
//
// THE LOOK IS THE MATERIAL. paintRobot() puts pomme's painterly v2 on it (styles2.js,
// verbatim): MeshToon through a 3-step ramp, the stroke normal map sampled triplanar,
// posterised pigment, a back-face hull for the line. That shader samples its map SIX times a
// fragment and compiles one program PER MESH, so the rules here are: one merged,
// vertex-coloured mesh per rigid link (12 meshes), and low poly (~2.5k triangles).
// Two things are adjusted after apply(), without touching styles2.js:
//   - stroke scale: styles2 normalises strokes to each mesh's own bounds, which would give a
//     1.4 m mast and a 16 cm wheel the same number of strokes. One scale for the whole robot.
//   - the hull: an inverted hull pushed along HARD normals splits open at every box corner.
//     Each hull gets a welded copy of its geometry with averaged normals; the lit mesh keeps
//     its crisp faces.
//
// MOTION is a pure function of time (makePopUp(scale).state(t) -> poseRig / poseRigid): scrubbable
// (dev-robot.html?t=1.6), deterministic, no Math.random. The entrance is a pop-up from the
// bottom of the frame and a balance catch that is SIMULATED, not keyframed: see the comment
// above POP. The earlier roll-in from the left is parked in robot-rollin.js.

import * as THREE from 'three';
import { mergeGeometries, mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
import { makePainterlyStyle2 } from './styles2.js';

// ---- the real machine, in metres ------------------------------------------------------
export const BOT = {
  wheelR: 0.0825, track: 0.425, tyreW: 0.062,        // Bracket Bot's odometry constants
  height: 1.55,
  mastR: 0.036, mastTop: 1.44,                        // top of the tube, from the floor
  shoulderY: 1.27, shoulderX: 0.098,
  upper: 0.30, fore: 0.235,                           // shoulder -> elbow -> wrist
  liftTravel: [-0.16, 0.05],
};

// base pigments (the shader clamps to [0.10, 0.92]: never a pure white or a pure primary)
const INK = {
  white: new THREE.Color('#c6c2ba'), shade: new THREE.Color('#9d9992'), tyre: new THREE.Color('#2b2a31'),
  dark: new THREE.Color('#3a3841'), yellow: new THREE.Color('#e0ac2a'), lens: new THREE.Color('#1d2733'),
  glass: new THREE.Color('#5d88a8'), blue: new THREE.Color('#3f66c2'), red: new THREE.Color('#c4432f'),
};

// one geometry, one flat colour, placed; everything in a link is merged from these
function part(geo, color, x = 0, y = 0, z = 0) {
  const g = geo.index ? geo.toNonIndexed() : geo;      // hard normals survive the merge
  const n = g.attributes.position.count, c = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) { c[i * 3] = color.r; c[i * 3 + 1] = color.g; c[i * 3 + 2] = color.b; }
  g.setAttribute('color', new THREE.BufferAttribute(c, 3));
  g.deleteAttribute('uv');
  return g.translate(x, y, z);
}
const box = (w, h, d) => new THREE.BoxGeometry(w, h, d);
const cylY = (r0, r1, h, seg = 12) => new THREE.CylinderGeometry(r0, r1, h, seg, 1);
const cylX = (r, w, seg = 16) => new THREE.CylinderGeometry(r, r, w, seg, 1).rotateZ(Math.PI / 2);
const cylZ = (r, d, seg = 12) => new THREE.CylinderGeometry(r, r, d, seg, 1).rotateX(Math.PI / 2);

function link(name, parts, parent) {
  const geo = mergeGeometries(parts, false);
  for (const p of parts) p.dispose();
  const mesh = new THREE.Mesh(geo, LINK_MATERIAL);
  mesh.name = name;
  parent.add(mesh);
  return mesh;
}
// the un-styled look (dev-robot.html?style=0): what the painterly pass starts from
const LINK_MATERIAL = new THREE.MeshStandardMaterial({ color: '#ffffff', vertexColors: true, roughness: 0.7, metalness: 0.05 });

function wheelParts() {
  const r = BOT.wheelR, w = BOT.tyreW;
  // a tyre is a lathe: flat tread, rounded shoulders, 18 sides
  const profile = [[r * 0.58, -w / 2], [r * 0.9, -w / 2], [r, -w * 0.28], [r, w * 0.28], [r * 0.9, w / 2], [r * 0.58, w / 2]]
    .map(([x, y]) => new THREE.Vector2(x, y));
  const parts = [part(new THREE.LatheGeometry(profile, 18).rotateZ(Math.PI / 2), INK.tyre),
    part(cylX(r * 0.6, w * 0.9, 14), INK.white)];
  // what makes the spin READ at 40 px: five bolts, and ONE yellow valve cap, on both faces
  for (const side of [-1, 1]) {
    for (let i = 0; i < 5; i++) {
      const a = (i / 5) * Math.PI * 2;
      parts.push(part(cylX(0.0075, 0.012, 6), INK.dark, side * w * 0.47, Math.cos(a) * r * 0.36, Math.sin(a) * r * 0.36));
    }
    parts.push(part(box(0.012, 0.034, 0.016), INK.yellow, side * w * 0.47, r * 0.72, 0));
    parts.push(part(cylX(r * 0.17, 0.016, 8), INK.dark, side * w * 0.47, 0, 0));
  }
  return parts;
}

function armParts() {
  const upper = [                                         // origin = the shoulder axis
    part(box(0.074, 0.105, 0.088), INK.white, 0, -0.012, 0),          // shoulder servo block
    part(cylX(0.03, 0.082, 10), INK.shade, 0, 0, 0),                  // the axis it turns on
    part(cylY(0.031, 0.028, BOT.upper - 0.09, 10), INK.white, 0, -BOT.upper / 2 - 0.01, 0),
    part(box(0.066, 0.078, 0.074), INK.white, 0, -BOT.upper + 0.004, 0),   // elbow servo block
  ];
  const fore = [                                          // origin = the elbow axis; hangs along -Y
    part(cylX(0.026, 0.074, 10), INK.shade, 0, 0, 0),
    part(cylY(0.028, 0.025, BOT.fore - 0.05, 10), INK.white, 0, -BOT.fore / 2, 0),
    part(box(0.054, 0.06, 0.056), INK.white, 0, -BOT.fore + 0.012, 0),    // wrist block
    part(box(0.004, BOT.fore * 0.7, 0.004), INK.dark, 0.026, -BOT.fore * 0.5, 0.012),   // the servo cable
  ];
  const hand = [                                          // origin = the wrist axis
    part(box(0.05, 0.05, 0.044), INK.white, 0, -0.03, 0),
    part(box(0.014, 0.085, 0.026), INK.dark, -0.02, -0.095, 0),       // fixed finger
    part(box(0.014, 0.078, 0.026), INK.dark, 0.021, -0.09, 0),        // the jaw
    part(box(0.03, 0.02, 0.05), INK.shade, 0, -0.056, 0),
  ];
  return { upper, fore, hand };
}

/**
 * @param {object} [opts]
 * @param {number} [opts.height]  total height in world units (default: metres, 1.55)
 * @returns rig = { root, wheelL, wheelR, body, mast, lift, headPan, headTilt, arms: [{shoulder, elbow, wrist}], scale, wheelRadius }
 */
export function buildBracketBot(opts = {}) {
  const root = new THREE.Group(); root.name = 'bracketBot';
  const R = BOT.wheelR;

  const wheels = [-1, 1].map((side) => {
    const j = new THREE.Group(); j.name = side < 0 ? 'wheelL' : 'wheelR';
    j.position.set(side * BOT.track / 2, R, 0);
    link(j.name + '.mesh', wheelParts(), j);
    root.add(j);
    return j;
  });

  // the body leans about the AXLE, so its origin is the axle
  const body = new THREE.Group(); body.name = 'body'; body.position.y = R; root.add(body);
  const SOCKET = 0.15, mastLen = BOT.mastTop - R - SOCKET;
  link('base.mesh', [
    part(cylX(0.094, BOT.track - BOT.tyreW - 0.05, 16), INK.white),                   // the drum between the wheels
    part(cylX(0.03, BOT.track - BOT.tyreW + 0.01, 8), INK.dark),                      // the axle stubs
    part(box(0.17, 0.05, 0.15), INK.white, 0, 0.098, 0),                              // deck
    part(cylY(0.054, 0.062, 0.09, 12), INK.shade, 0, SOCKET, 0),                      // mast socket
    part(box(0.115, 0.078, 0.086), INK.yellow, 0, 0.165, -0.105),                     // the drill battery
    part(box(0.115, 0.03, 0.086), INK.dark, 0, 0.219, -0.105),
    part(box(0.09, 0.05, 0.02), INK.dark, 0, 0.06, 0.088),                            // the front panel
  ], body);
  // A 1.3 m tube clamped in a 9 cm socket is not rigid and the real one is not either: it rocks
  // in its mount. One more revolute joint, at the socket, is the whole "whip".
  const mast = new THREE.Group(); mast.name = 'mast'; mast.position.y = SOCKET; body.add(mast);
  link('mast.mesh', [
    part(cylY(BOT.mastR, BOT.mastR, mastLen, 12), INK.white, 0, mastLen / 2, 0),      // THE MAST
    part(box(0.026, mastLen * 0.8, 0.009), INK.dark, 0, 0.05 + mastLen * 0.4, BOT.mastR + 0.003),   // the lift belt
    part(box(0.04, 0.04, 0.02), INK.dark, 0, 0.06, BOT.mastR + 0.008),                // the belt's motor end
    part(new THREE.SphereGeometry(BOT.mastR * 1.06, 10, 6), INK.white, 0, mastLen, 0),
  ], mast);

  // the carriage rides the belt
  const lift = new THREE.Group(); lift.name = 'lift'; lift.position.y = BOT.shoulderY - R - SOCKET; mast.add(lift);
  lift.userData.home = lift.position.y;
  link('lift.mesh', [
    part(box(0.118, 0.19, 0.104), INK.white, 0, -0.03, 0),                            // the clamp round the mast
    part(box(BOT.shoulderX * 2 - 0.07, 0.07, 0.074), INK.shade, 0, 0.0, 0),           // the yoke to both shoulders
    part(box(0.07, 0.05, 0.012), INK.lens, 0, -0.02, 0.056),                          // status screen
    part(box(0.014, 0.014, 0.014), INK.red, 0.04, 0.045, 0.054),
    part(box(0.014, 0.014, 0.014), INK.blue, -0.04, 0.045, 0.054),
  ], lift);

  const arms = [-1, 1].map((side) => {
    const P = armParts();
    const shoulder = new THREE.Group(); shoulder.name = side < 0 ? 'armL' : 'armR';
    shoulder.position.set(side * BOT.shoulderX, 0, 0);
    link(shoulder.name + '.upper', P.upper, shoulder);
    const elbow = new THREE.Group(); elbow.position.y = -BOT.upper; shoulder.add(elbow);
    link(shoulder.name + '.fore', P.fore, elbow);
    const wrist = new THREE.Group(); wrist.position.y = -BOT.fore; elbow.add(wrist);
    link(shoulder.name + '.hand', P.hand, wrist);
    lift.add(shoulder);
    return { side, shoulder, elbow, wrist };
  });

  // the head: a camera pill on a neck, pan then tilt
  const headPan = new THREE.Group(); headPan.name = 'headPan'; headPan.position.y = mastLen; mast.add(headPan);
  const headTilt = new THREE.Group(); headTilt.name = 'headTilt'; headTilt.position.y = 0.05; headPan.add(headTilt);
  link('head.mesh', [
    part(cylY(0.03, 0.034, 0.07, 10), INK.shade, 0, -0.035, 0),
    part(new THREE.CapsuleGeometry(0.054, 0.1, 3, 12).rotateX(Math.PI / 2), INK.white, 0, 0.03, 0.02),
    part(cylZ(0.044, 0.016, 14), INK.lens, 0, 0.03, 0.118),                           // the visor
    part(cylZ(0.021, 0.012, 10), INK.glass, -0.012, 0.036, 0.126),                    // the lens in it
    part(box(0.012, 0.012, 0.008), INK.white, 0.018, 0.048, 0.128),                   // catchlight: it is LOOKING
    part(box(0.016, 0.03, 0.03), INK.blue, 0.055, 0.03, 0.0),
    part(box(0.016, 0.03, 0.03), INK.red, -0.055, 0.03, 0.0),
  ], headTilt);

  const scale = opts.height ? opts.height / BOT.height : 1;
  root.scale.setScalar(scale);
  const rig = { root, wheelL: wheels[0], wheelR: wheels[1], body, mast, lift, headPan, headTilt, arms, scale,
    wheelRadius: R * scale, halfTrack: (BOT.track / 2) * scale };
  restPose(rig);
  return rig;
}

// arms hang, elbows bent forward: how it actually stands in the capture frames
export function restPose(rig) {
  for (const a of rig.arms) { a.shoulder.rotation.set(0.12, 0, a.side * -0.05); a.elbow.rotation.x = -1.2; a.wrist.rotation.x = -0.15; }
  rig.headPan.rotation.y = 0; rig.headTilt.rotation.set(0.12, 0, 0);
  rig.lift.position.y = rig.lift.userData.home;
  rig.body.rotation.x = 0; rig.mast.rotation.x = 0;
}

// ---- the look ---------------------------------------------------------------------------

const POMME_CEL_KEY = 'vec3(0.73, 0.68, 0.06)';
// Upper right and a little in front. The shader's shadow threshold "sits IN the lit range"
// (0.28-0.52 of N.key) on purpose: a surface facing the camera should land NEAR it, so the
// stroke map pushes brush-shaped patches of shadow across it. Side-on (pomme's 0.06 in z) and
// everything facing the viewer is solid shadow; too frontal (0.6) and it is all flat light.
// The numbers: cel = N.key + ~0.22 (the stroke map's mean tilt) +- the dabs, shadow below
// 0.28, light above 0.52. z = 0.24 puts a plane that faces the camera at 0.46: inside the band,
// so the dabs decide, patch by patch. It also runs the terminator down the FRONT of every tube.
const KEY_DIR = new THREE.Vector3(0.78, 0.58, 0.24).normalize();

// welded copy with averaged normals: the hull's own geometry (see the header)
function hullGeometry(geo) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', geo.attributes.position.clone());
  const welded = mergeVertices(g, 1e-4);
  welded.computeVertexNormals();
  g.dispose();
  return welded;
}

/**
 * pomme's painterly v2 on the whole rig.
 * @param {number} [opts.strokes=0.6]  stroke density relative to pomme's one-object-is-two-units rule (bigger = finer)
 * @param {number} [opts.line=1.0]     outline weight
 */
export function paintRobot(rig, opts = {}) {
  // opts.strokeMap: another URL for the stroke normal map. styles2.js asks for the 2048 px PNG
  // (7 MB); at the sizes the robot is drawn a 1024 px WebP (0.2 MB) is indistinguishable, and a
  // dashboard that has to open on a phone cannot afford the difference. The redirect exists only
  // for the duration of the one call that starts the load, and styles2.js stays verbatim.
  // NOT for the hero: scene.js owns the loading manager's one URL modifier there.
  if (opts.strokeMap) THREE.DefaultLoadingManager.setURLModifier((u) => (u === './textures/watercolor_normal.png' ? opts.strokeMap : u));
  const style = makePainterlyStyle2(rig.root);
  if (opts.strokeMap) THREE.DefaultLoadingManager.setURLModifier(undefined);
  style.apply();
  // styles2: posScale = 2 / (the mesh's largest dimension). Same formula, the ROBOT's dimension.
  // ...times `strokes`. MEASURED on the map: one tile holds ~30 brush dabs, so a dab is
  // 0.62 m / 30 / strokes. At stage size the robot is ~210 px per metre: strokes = 3 made a dab
  // 1.4 px (it mip-averaged to nothing and every face went flat); 0.6 makes it ~7 px, the
  // smallest that still reads as a brush mark. Closer cameras can afford finer.
  const posScale = (2 / BOT.height) * (opts.strokes ?? 0.6);
  const line = opts.line ?? 1;
  const hulls = [];
  rig.root.traverse((o) => {
    if (!o.isMesh) return;
    if (o.userData.isOutline) {
      // width in the hull shader = noise * 0.5 / uPosScale: keep its own scale for the line
      o.material.uniforms.uPosScale.value = (2 / BOT.height) / line;
      o.geometry = hullGeometry(o.geometry);
      hulls.push(o);
    } else {
      const compile = o.material.onBeforeCompile;
      o.material.onBeforeCompile = (shader, renderer) => {
        compile(shader, renderer);
        shader.uniforms.uPosScale.value = posScale;
        // styles2 paints its shadow cel from a FIXED world key, "aligned with the physical key
        // light" of pomme's scene: (0.73, 0.68, 0.06), almost side-on. Seen from the landing
        // camera that puts every viewer-facing surface on the shadow threshold (a raised arm
        // went solid lavender). Same rule, this scene's key light.
        if (!shader.fragmentShader.includes(POMME_CEL_KEY)) console.warn('[robot] styles2 cel key not found: painted shadow keeps pomme\'s direction');
        shader.fragmentShader = shader.fragmentShader.replace(POMME_CEL_KEY, `vec3(${KEY_DIR.x.toFixed(4)}, ${KEY_DIR.y.toFixed(4)}, ${KEY_DIR.z.toFixed(4)})`);
      };
    }
  });
  return { style, hulls, setHull(on) { for (const h of hulls) h.visible = on; } };
}

// its own light: the robot is drawn RAW (never through the ink pass), so it cannot borrow the
// stage's. No tone mapping on the landing renderer, so these stay low enough not to clip.
export function robotLights() {
  const g = new THREE.Group(); g.name = 'robotLights';
  const key = new THREE.DirectionalLight('#ffffff', 2.25); key.position.copy(KEY_DIR).multiplyScalar(8);   // = the painted cel key
  const fill = new THREE.DirectionalLight('#9fb4ff', 0.35); fill.position.set(-4, 2, -2);
  const front = new THREE.DirectionalLight('#ffffff', 0.4); front.position.set(0.5, 2.5, 10);
  g.add(key, fill, front, new THREE.AmbientLight('#6a6472', 0.9));
  return g;
}

// Pay for the look BEFORE the entrance. styles2 compiles one program per mesh, and three only
// compiles what it actually draws: a robot waiting below the frame compiles nothing, then drops
// frames on the very frame it appears. Measured in the real page (tools/dev/robot-in-scene.mjs):
// worst frame at the entrance 86 ms cold, 18 ms after this. It compiles in parallel
// (KHR_parallel_shader_compile), uploads the stroke map, and resolves when both are done;
// as a scene.js module that happens behind the loading screen.
export async function warmUp(rig, renderer, camera, scene) {
  let map = null;
  rig.root.traverse((o) => { if (o.isMesh && o.material.normalMap) map = o.material.normalMap; });
  if (map) {
    for (let i = 0; i < 400 && !(map.image && map.image.width); i++) await new Promise((r) => setTimeout(r, 25));
    if (map.image && map.image.width) renderer.initTexture(map);
  }
  const was = rig.root.visible; rig.root.visible = true;
  await renderer.compileAsync(rig.root, camera, scene);
  rig.root.visible = was;
}

// ---- the entrance: it POPS UP from the bottom of the frame, and catches its balance -------
//
// A Bracket Bot is a self-balancing two-wheeler, so the personality is free: thrown up onto its
// wheels it lands pitched forward, the base darts forward to get back under the mast, the mast
// swings past vertical, and it takes a few decaying counter-leans to come to rest. None of that
// is keyframed. makePopUp() integrates the actual thing once, at build time:
//
//   a cart and an inverted pendulum        x'' = a,   theta'' = (g*theta - a) / L
//   under a balance controller             a = k1*theta + k2*theta' + k3*x + k4*x'
//
// with the gains placed from the response wanted (a lightly damped pair: the visible
// counter-leans; a well damped slow pair: the base drifting back to its mark). Driven by that
// base acceleration, three lighter things ride along: the mast rocking in its socket (the
// "whip", stiff and barely damped, so it shivers AGAINST each reversal), the arms as hanging
// pendulums, and the carriage bouncing on its belt from the landing. The result is a table
// sampled at 240 Hz, and pose(t) only reads it: still a pure function of time, scrubbable
// (dev-robot.html?t=1.6), deterministic, nothing per-frame but a lerp.
//
// Around the physics, the acting: a PEEK over the edge first (head up, a look each way), a DIP
// (anticipation), the POP (ballistic, overshooting its standing height by `hop`), the landing,
// the catch; and once it is still, two beats with the head: it finds GITRL, then it looks at
// YOU and cocks its head.

const c01 = (u) => Math.min(1, Math.max(0, u));
const ease = (a, b, x) => { const u = c01((x - a) / (b - a)); return u * u * (3 - 2 * u); };   // pomme's `ease`
const easeOutBack = (u, k = 1.6) => { u = c01(u) - 1; return 1 + u * u * ((k + 1) * u + k); };   // arrives, overshoots a touch, returns

export const POP = {
  x: -3.75, z: 0.6,        // where it stands: left of GITRL and of ENTER's clear box, in front of the crowd
  edgeY: -0.72,            // world y of the BOTTOM EDGE of the frame at that depth (buildRobot measures it)
  clearance: 0.1,          // wheels rest this far above the edge
  yaw: 0.95,               // three-quarter, facing the title: a fore-aft lean has to read in silhouette
  peek: 0.34, hold: 0.30, dip: 0.17,   // seconds: head rises over the edge, looks about, sinks to spring
  peekShow: 0.4,           // how much of it shows during the peek (world units: the head)
  up: 0.34, hop: 0.2,      // seconds to the apex; how far ABOVE standing height it is thrown
  // the machine (metres, radians)
  L: 0.95,                 // effective pendulum length: most of the mass is low, the mast is long
  wobbleHz: 1.3, wobbleDamp: 0.2,      // the counter-leans: how fast, how quickly they die
  returnRate: 2.5, returnDamp: 0.9,    // the base finding its mark again
  land: { theta: 0.11, omega: 0.1, v: 0.03, x: -0.06 },     // how it comes down: pitched 6 degrees forward, still rotating
                           // (tuned on the numbers: lands +6, swings back to -9, then +4, -2, +1; darts 25 cm; still in 1.8 s)
  whip: { hz: 3.4, damp: 0.11, gain: 0.0042, kick: -0.55 },  // the mast in its socket
  arms: { hz: 1.35, damp: 0.2, gain: 0.05, kick: 1.6 },
  carriage: { hz: 3.1, damp: 0.33, kick: -0.75 },           // m/s into the belt at touchdown
  lookTitle: 1.35,         // how long it looks at GITRL before it looks at you
  title: [0, 2.2, -1.2], viewer: [0, 2.3, 8.4],
  reduced: false,          // prefers-reduced-motion: it simply rises; no throw, no wobble
  twos: 0,                 // 12 = pomme's stepped look. Off: a 1.2 Hz wobble needs every frame
};

const _t = new THREE.Vector3(), _m = new THREE.Matrix4();
// pan / tilt that points the head at a world point, from the frame the head is mounted in (so
// it keeps its gaze while everything under it leans)
export function aimAngles(rig, target) {
  _t.fromArray(target).applyMatrix4(_m.copy(rig.headPan.parent.matrixWorld).invert()).sub(rig.headPan.position);
  _t.y -= rig.headTilt.position.y;
  return { pan: Math.atan2(_t.x, _t.z), tilt: -Math.atan2(_t.y, Math.hypot(_t.x, _t.z)) };
}

function simulateCatch(cfg) {
  const g = 9.81, L = cfg.L, HZ = 240, N = Math.ceil(4.5 * HZ), dt = 1 / HZ / 2;      // two substeps a sample
  // gains from the poles: (s^2 + a1 s + a0)(s^2 + b1 s + b0) against the loop's characteristic
  // polynomial  L s^4 + (k2 - L k4) s^3 + (k1 - g - L k3) s^2 + g k4 s + g k3
  const w1 = 2 * Math.PI * cfg.wobbleHz, a1 = 2 * cfg.wobbleDamp * w1, a0 = w1 * w1;
  const b1 = 2 * cfg.returnDamp * cfg.returnRate, b0 = cfg.returnRate * cfg.returnRate;
  const k3 = (a0 * b0 * L) / g, k4 = ((a1 * b0 + a0 * b1) * L) / g;
  const k1 = (a0 + b0 + a1 * b1) * L + g + L * k3, k2 = (a1 + b1) * L + L * k4;
  const osc = (o) => ({ w: 2 * Math.PI * o.hz, z: o.damp });
  const W = osc(cfg.whip), A = osc(cfg.arms), C = osc(cfg.carriage), H = BOT.shoulderY - BOT.wheelR;
  let x = cfg.land.x, v = cfg.land.v, th = cfg.land.theta, om = cfg.land.omega;
  let ph = 0, phv = cfg.whip.kick, ps = 0, psv = cfg.arms.kick, d = 0, dv = cfg.carriage.kick;
  const T = { hz: HZ, n: N, x: new Float32Array(N), th: new Float32Array(N), ph: new Float32Array(N), ps: new Float32Array(N), d: new Float32Array(N) };
  for (let i = 0; i < N; i++) {
    T.x[i] = x; T.th[i] = th; T.ph[i] = ph; T.ps[i] = ps; T.d[i] = d;
    for (let k = 0; k < 2; k++) {
      const a = k1 * th + k2 * om + k3 * x + k4 * v, al = (g * th - a) / L;
      const top = a + H * al;                          // what the shoulders feel
      v += a * dt; x += v * dt; om += al * dt; th += om * dt;
      phv += (-2 * W.z * W.w * phv - W.w * W.w * ph - cfg.whip.gain * W.w * W.w * top) * dt; ph += phv * dt;
      psv += (-2 * A.z * A.w * psv - A.w * A.w * ps - cfg.arms.gain * A.w * A.w * top) * dt; ps += psv * dt;
      dv += (-2 * C.z * C.w * dv - C.w * C.w * d) * dt; d += dv * dt;
    }
  }
  // when is it STILL? the last moment the lean is over 0.6 degrees: the head beats wait for that
  let last = 0; for (let i = 0; i < N; i++) if (Math.abs(T.th[i]) > 0.0105) last = i;
  T.settle = last / HZ + 0.12;
  return T;
}
const sample = (T, col, tau) => {
  const f = Math.max(0, tau) * T.hz, i = Math.min(T.n - 2, Math.floor(f)), u = Math.min(1, f - i);
  return T[col][i] * (1 - u) + T[col][i + 1] * u;
};

/**
 * The entrance as DATA: state(t) says where the machine is and how it is leaning at t seconds
 * since it began, and knows nothing about what is being posed. poseRig() puts it on the
 * jointed primitives robot; poseRigid() puts it on anything that can only move as ONE piece
 * (the splat). Pure: same t, same state.
 * @param {number} scale  world units per metre of robot (rig.scale, or height / BOT.height)
 */
export function makePopUp(scale, cfg = POP) {
  const T = simulateCatch(cfg), s = scale, H = BOT.height * s;
  const floorY = cfg.edgeY + cfg.clearance, hiddenY = cfg.edgeY - H - 0.15, peekY = cfg.edgeY - H + cfg.peekShow;
  const tHold = cfg.peek, tDip = tHold + cfg.hold, tLaunch = tDip + cfg.dip, dipY = peekY - 0.09;
  // the throw: ballistic from the dip to an apex `hop` above standing height, then down onto the wheels
  const rise = floorY + cfg.hop - dipY, G = (2 * rise) / (cfg.up * cfg.up), v0 = G * cfg.up;
  const tApex = tLaunch + cfg.up, tLand = cfg.reduced ? 0.9 : tApex + Math.sqrt((2 * cfg.hop) / G);
  const settle = cfg.reduced ? 0.3 : T.settle, settleAt = tLand + settle, titleAt = settleAt + 0.15, viewerAt = titleAt + cfg.lookTitle;
  const Rm = BOT.wheelR, airSpin = 16, air = tLand - tLaunch;                      // rad/s the wheels turn as it leaves the ground

  function state(t) {
    if (cfg.twos) t = Math.floor(t * cfg.twos + 1e-6) / cfg.twos;
    let y, x = cfg.land.x - 0.1, lean = 0, whip = 0, swing = 0, sink = 0, roll = 0, float = 0, phase;
    const tau = t - tLand;
    if (cfg.reduced) {                                                              // just rise, gently
      y = hiddenY + (floorY - hiddenY) * ease(0, 0.9, t); x = 0; phase = t < 0.9 ? 'rise' : 'ground';
    } else if (t < tLaunch) {
      phase = t < tHold ? 'peek' : t < tDip ? 'hold' : 'dip';
      const d = ease(tDip, tLaunch, t);
      y = hiddenY + (peekY - hiddenY) * easeOutBack(t / cfg.peek, 1.2) + (dipY - peekY) * d;
      lean = -0.05 * d; sink = -0.05 * d;                                           // leans back and sinks on its belt, to spring
    } else if (t < tLand) {
      phase = 'air';
      const a = t - tLaunch; y = dipY + v0 * a - 0.5 * G * a * a;
      // thrown a little forward and rotating: a Hermite from the crouch to exactly the state
      // the catch starts from, so lean and its RATE are continuous across the landing
      const u = c01(a / air), h1 = u * u * (3 - 2 * u), h3 = u * u * (u - 1);
      lean = -0.05 * (1 - h1) + cfg.land.theta * h1 + cfg.land.omega * air * h3;
      x = (cfg.land.x - 0.1) * (1 - h1) + cfg.land.x * h1 + cfg.land.v * air * h3;
      roll = airSpin * a * (1 - 0.35 * u);                                          // wheels free-spinning, slowing
      float = Math.sin(Math.PI * c01(u * 1.15)); sink = -0.05 * (1 - ease(0, 0.12, a));   // arms lift, weightless
    } else {
      phase = tau < settle ? 'catch' : 'ground';
      y = floorY; x = sample(T, 'x', tau); lean = sample(T, 'th', tau);
      whip = sample(T, 'ph', tau); swing = sample(T, 'ps', tau); sink = sample(T, 'd', tau);
      roll = airSpin * air * 0.65 + (x - cfg.land.x) / Rm;                          // on the ground: distance / radius, exactly
    }
    // it never stops balancing: a slow few-millimetre hunt, faded in once the catch has died
    const hunting = cfg.reduced ? ease(1.0, 2.2, t) : ease(settle - 0.4, settle + 1.0, tau);
    const huntX = -0.012 * Math.sin(t * 1.7) * hunting;
    lean += 0.011 * Math.sin(t * 1.7 + 0.5) * hunting;
    if (phase === 'ground' || phase === 'catch' || cfg.reduced) { x += huntX; roll += huntX / Rm; }
    // the acting: a look each way during the peek; after the settle, GITRL, then YOU
    const glance = cfg.reduced ? 0 : -0.75 * (ease(cfg.peek * 0.6, cfg.peek + 0.06, t) - 1.8 * ease(tHold + 0.1, tHold + 0.2, t)
      + 0.8 * ease(tDip - 0.04, tDip + 0.08, t));
    const toTitle = t > titleAt ? easeOutBack((t - titleAt) / 0.42, 1.3) : 0, toViewer = t > viewerAt ? easeOutBack((t - viewerAt) / 0.5, 1.1) : 0;
    return { t, y, x, lean, whip, swing, sink, roll, float, glance, toTitle, toViewer, phase, airborne: phase === 'air',
      gaze: toViewer > 0.5 ? 'viewer' : toTitle > 0.5 ? 'title' : 'ahead' };
  }
  return { state, scale: s, cfg, landAt: tLand, launchAt: tLaunch, apexAt: tApex, settleAt, titleAt, viewerAt, floorY, table: T };
}

const _fwd = new THREE.Vector3();
/** state -> the jointed robot: every joint gets its own share of the motion */
export function poseRig(rig, st, cfg = POP) {
  const s = rig.scale; _fwd.set(Math.sin(cfg.yaw), 0, Math.cos(cfg.yaw));
  rig.root.position.set(cfg.x + _fwd.x * st.x * s, st.y, cfg.z + _fwd.z * st.x * s);
  rig.root.rotation.set(0, cfg.yaw, 0);
  rig.wheelL.rotation.x = rig.wheelR.rotation.x = st.roll;
  rig.body.rotation.x = st.lean;
  rig.mast.rotation.x = st.whip;
  rig.lift.position.y = rig.lift.userData.home + Math.max(BOT.liftTravel[0], Math.min(BOT.liftTravel[1], st.sink));
  for (const a of rig.arms) {
    a.shoulder.rotation.order = 'YXZ';
    a.shoulder.rotation.set(0.12 + st.swing - st.lean * 0.5 - 0.75 * st.float, 0, a.side * (-0.05 - 0.28 * st.float));
    a.elbow.rotation.x = -1.2 + 0.55 * st.float - 0.6 * st.swing;
    a.wrist.rotation.x = -0.15 - 0.5 * st.swing;
  }
  // the head is a stabilised camera: through the throw and the catch it holds its gaze level
  // while everything under it swings. Then the two beats, aimed at the real targets.
  rig.root.updateMatrixWorld(true);
  const aT = aimAngles(rig, cfg.title), aV = aimAngles(rig, cfg.viewer), clampPan = (a) => Math.max(-1.45, Math.min(1.45, a));
  const pan = st.glance + (clampPan(aT.pan) - st.glance) * st.toTitle, tilt = 0.06 + (aT.tilt - 0.06) * st.toTitle;
  rig.headPan.rotation.y = pan + (clampPan(aV.pan) - pan) * st.toViewer;
  rig.headTilt.rotation.set(-(st.lean + st.whip) + tilt + (aV.tilt - tilt) * st.toViewer, 0, 0.2 * st.toViewer);   // .z: it cocks its head at you
  return st;
}

/**
 * state -> something that can only move as ONE rigid piece. `body` = { root, pivot, axleY }:
 * root stands on the floor (its +Z is FORWARD), pivot is a child at the axle height and the
 * thing itself hangs under the pivot, so rotation.x on the pivot is a lean ABOUT THE AXLE.
 * No joints, so the character has to come from the curve alone: the lean carries the whip as
 * well (a rigid shiver), and the head beats become what a rigid balancer can really do: it
 * turns on the spot toward GITRL with a small nod, then toward you.
 */
export function poseRigid(body, st, cfg = POP) {
  const s = body.scale; _fwd.set(Math.sin(cfg.yaw), 0, Math.cos(cfg.yaw));
  const face = (target) => Math.atan2(target[0] - cfg.x, target[2] - cfg.z);
  const toT = (face(cfg.title) - cfg.yaw) * 0.55, toV = (face(cfg.viewer) - cfg.yaw) * 0.8;   // it turns PART of the way: a look, not a march
  const yaw = cfg.yaw + st.glance * 0.12 + toT * st.toTitle + (toV - toT) * st.toViewer;
  body.root.position.set(cfg.x + _fwd.x * st.x * s, st.y, cfg.z + _fwd.z * st.x * s);
  body.root.rotation.set(0, yaw, 0);
  body.pivot.rotation.set(st.lean + st.whip * 1.6 + 0.045 * (st.toTitle - st.toViewer), 0, 0.05 * st.toViewer);
  return st;
}

// ---- the caretaker's gesture: it POINTS at the thing you lost -------------------------------
//
// "Where are my keys?" ends with the robot driving over and pointing (PLAN.md, demo ladder 1).
// Only a jointed robot can do that; a splat cannot. pointAt() is inverse kinematics for what the
// arm really is: a shoulder that pans then lifts (rotation order YXZ) carrying a straight-ish
// arm along its own -Y. Solve  Ry(pan) * Rx(lift) * (0,-1,0) = d  for the direction d to the
// target in the carriage's frame:   lift = -acos(-d.y),   pan = atan2(d.x, d.z).
// It layers ON TOP of whatever pose is already there, by `w` (0..1), so any choreography can
// ease it in and out. The near arm is used; the head looks where the hand points; and the body
// leans back a touch, because a balancer with an arm out in front has to.

const _p = new THREE.Vector3(), _q = new THREE.Vector3();
export function pointAt(rig, target, w = 1, opts = {}) {
  w = c01(w); if (w <= 0) return null;
  rig.root.updateMatrixWorld(true);
  // which arm: the one on the target's side of the robot
  _p.fromArray(target).applyMatrix4(_m.copy(rig.root.matrixWorld).invert());
  const arm = rig.arms[(opts.side ?? (_p.x >= 0 ? 1 : -1)) > 0 ? 1 : 0];
  // direction to the target from that shoulder, in the frame the shoulder is mounted in
  _q.fromArray(target).applyMatrix4(_m.copy(arm.shoulder.parent.matrixWorld).invert()).sub(arm.shoulder.position);
  const reach = _q.length(); _q.normalize();
  const bend = opts.bend ?? 0.22;                                   // a locked-straight arm reads as a salute, not a point
  const lift = -Math.acos(Math.max(-1, Math.min(1, -_q.y))) + bend * 0.45, pan = Math.atan2(_q.x, _q.z);
  const r = arm.shoulder.rotation; r.order = 'YXZ';
  r.set(r.x + (lift - r.x) * w, r.y + (Math.max(-1.9, Math.min(1.9, pan)) - r.y) * w, r.z * (1 - w));
  arm.elbow.rotation.x += (-bend - arm.elbow.rotation.x) * w;
  arm.wrist.rotation.x += (-0.05 - arm.wrist.rotation.x) * w;
  rig.body.rotation.x -= 0.03 * w;                                   // the counter-lean
  const a = aimAngles(rig, target);
  rig.headPan.rotation.y += (Math.max(-1.45, Math.min(1.45, a.pan)) - rig.headPan.rotation.y) * w;
  rig.headTilt.rotation.x += (a.tilt - rig.headTilt.rotation.x) * w;
  rig.headTilt.rotation.z *= 1 - w;
  return { side: arm.side, reach: reach / rig.scale, pan, lift };
}

/**
 * The whole beat as a pure function of time, for a robot already standing at cfg.x/z facing
 * cfg.yaw: it NOTICES (head first), TURNS on the spot toward the target with its wheels running
 * opposite ways, RAISES the arm, holds, and glances back at you: "there".
 * @returns {{ yaw, w, glance }}
 */
export function pointBeat(rig, t, target, cfg = POP) {
  const face = Math.atan2(target[0] - rig.root.position.x, target[2] - rig.root.position.z);
  let turn = face - cfg.yaw; turn = Math.atan2(Math.sin(turn), Math.cos(turn));
  const keep = Math.sign(turn) * Math.max(0, Math.abs(turn) - 0.5);  // it turns until the target is a comfortable reach off its nose
  const notice = ease(0.0, 0.35, t), turning = ease(0.3, 1.1, t), raise = easeOutBack((t - 0.85) / 0.55, 1.4) * (t > 0.85 ? 1 : 0);
  const yaw = cfg.yaw + keep * turning, spin = keep * turning * rig.halfTrack / rig.wheelRadius;
  rig.root.rotation.y = yaw;
  rig.wheelL.rotation.x += spin; rig.wheelR.rotation.x -= spin;      // turning on the spot: no slip
  rig.body.rotation.x += 0.02 * Math.sin(Math.PI * turning);         // and the little lean that comes with it
  pointAt(rig, target, raise);
  // before the arm is up, the head is already there
  if (raise < 1) { const a = aimAngles(rig, target), k = notice * (1 - raise);
    rig.headPan.rotation.y += (Math.max(-1.45, Math.min(1.45, a.pan)) - rig.headPan.rotation.y) * k;
    rig.headTilt.rotation.x += (a.tilt - rig.headTilt.rotation.x) * k; }
  // "there": a look back at the viewer, arm still out
  const glance = ease(2.2, 2.6, t) - ease(3.5, 3.9, t);
  if (glance > 0) { const v = aimAngles(rig, cfg.viewer);
    rig.headPan.rotation.y += (Math.max(-1.45, Math.min(1.45, v.pan)) - rig.headPan.rotation.y) * glance;
    rig.headTilt.rotation.x += (v.tilt - rig.headTilt.rotation.x) * glance; rig.headTilt.rotation.z = 0.16 * glance; }
  return { yaw, w: raise, glance };
}

/**
 * The entrance on the landing, for ANY actor (the jointed robot here, the splat in
 * robot-splat.js). actor = { root, scale, pose(state, cfg), heroAt(vec3), lookAt?(point, weight), keepWarm? }.
 *
 * ORDER: the title drops, THEN the robot rises. It does not guess when that is: title.js
 * publishes world.title.letters[i].landed, so the entrance starts `beat` seconds after the LAST
 * letter has landed and the ta-da has played (INTRO.tada), whatever the title's owner does to
 * its timing. Without a title (?only=...) it falls back to the intro clock.
 *
 * It lists itself in world.hero, which the crowd already keeps clear of. ENTER pressed
 * (`away`): a small hop, then back out through the bottom it came from.
 */
export async function stageEntrance(world, actor, opts = {}) {
  const { INTRO, TITLE, ENTER } = await import('./layout.js');
  const cam = world.camera, reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const cfg = { ...POP, reduced, title: TITLE.center.toArray(), viewer: cam.position.toArray(), ...(opts.pop || {}) };
  let act = null, aspectBuilt = 0, startAt = opts.startAt ?? null, landedAt = null, awayU = 0, enterU = 0;
  const hero = { pos: new THREE.Vector3(0, -50, 0), radius: 0 };
  if (Array.isArray(world.hero)) world.hero.push(hero);
  const edge = new THREE.Vector3(), BEAT = opts.beat ?? 0.3, tall = BOT.height * actor.scale;
  function layout() {                        // the bottom edge of the frame at the robot's depth, from the real camera
    cam.updateMatrixWorld(); edge.set(0, -1, 0.5).unproject(cam).sub(cam.position);
    edge.multiplyScalar((cfg.z - cam.position.z) / edge.z).add(cam.position);
    // narrow screens: stay inside the frame, left of the title
    const halfW = Math.tan(THREE.MathUtils.degToRad(cam.fov / 2)) * (cam.position.z - cfg.z) * cam.aspect;
    act = makePopUp(actor.scale, { ...cfg, edgeY: edge.y, x: Math.max(cfg.x, -halfW + 0.75) });
    aspectBuilt = cam.aspect;
  }
  return { actor, get startAt() { return startAt; }, get act() { return act; },
    update(world) {
      if (!act || Math.abs(cam.aspect - aspectBuilt) > 1e-3) layout();
      if (startAt == null) {
        const L = world.title && world.title.letters;
        if (L && L.length) {
          if (landedAt == null && L.every((l) => l.landed)) landedAt = world.t;
          if (landedAt != null && world.t >= landedAt + BEAT && world.t >= INTRO.tada + 0.45) startAt = world.t;
        } else if (world.t >= INTRO.work + 0.3) startAt = world.t;
        if (startAt == null) {                 // waiting below the frame; drawn there if it has to stay warm
          actor.pose(act.state(0), act.cfg); actor.root.visible = !!actor.keepWarm;
          return;
        }
      }
      const t = world.t - startAt, away = !!(world.away && world.away.on);
      const st = actor.pose(act.state(t), act.cfg);
      // ENTER hovered: look at it, like every other camera on the page; the pose underneath stays the pure function
      const want = actor.lookAt && world.enter && world.enter.hovered && t > act.titleAt ? 1 : 0;
      enterU += (want - enterU) * (1 - Math.exp(-7 * world.dt));
      if (enterU > 1e-3) actor.lookAt((world.enter.pos || ENTER.center).toArray(), enterU);
      awayU += ((away ? 1 : 0) - awayU) * (1 - Math.exp(-4.5 * world.dt));
      const drop = awayU * awayU * (3 - 2 * awayU);
      actor.root.position.y += 0.18 * Math.sin(Math.PI * c01(awayU * 2.2)) - drop * (tall + 0.6);
      actor.root.visible = awayU < 0.995;
      actor.heroAt(hero.pos);
      hero.radius = actor.root.visible && (st.phase === 'air' || st.phase === 'catch' || st.phase === 'ground') ? 0.8 * (1 - drop) : 0;
    },
    dispose() { actor.root.removeFromParent(); const i = (world.hero || []).indexOf(hero); if (i >= 0) world.hero.splice(i, 1); } };
}

/**
 * The primitives robot as a scene.js module (one line in MODULES: ['./robot.js', 'buildRobot']).
 * It draws in world.controlsScene, the RAW overlay scene.js renders after the ink pass, so the
 * painterly material reaches the screen as itself.
 */
export async function buildRobot(world, opts = {}) {
  const rig = buildBracketBot({ height: opts.height ?? 2.7 });
  paintRobot(rig);
  const host = world.controlsScene || world.scene;
  host.add(rig.root, robotLights());
  rig.root.visible = false;
  // scene.js does not publish its renderer on `world`; its debug handle does
  const renderer = opts.renderer || world.renderer || (globalThis.gitrl && globalThis.gitrl.renderer);
  if (renderer && opts.warmUp !== false) await warmUp(rig, renderer, world.camera, host);
  return stageEntrance(world, {
    root: rig.root, scale: rig.scale, rig,
    pose: (st, cfg) => poseRig(rig, st, cfg),
    heroAt: (v) => rig.lift.getWorldPosition(v),
    lookAt(point, w) {
      const a = aimAngles(rig, point);
      rig.headPan.rotation.y += (Math.max(-1.45, Math.min(1.45, a.pan)) - rig.headPan.rotation.y) * w;
      rig.headTilt.rotation.x += (a.tilt - rig.headTilt.rotation.x) * w;
      rig.headTilt.rotation.z *= 1 - w;
    },
  }, opts);
}
