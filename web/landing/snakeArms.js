// snakeArms.js — TWO many-jointed serpentine robot arms for the divide.
// A SPY-CAM arm snakes DOWN FROM THE TOP of frame and stares at the apple
// (real THREE camera in the pod: rig.cam.povCam), and a CLAW arm slides in
// FROM THE LEFT for the grab. Each arm is a chain of bendable segments —
// procedural S-curve posing, unfurling from a coil on entry, with idle
// breathing — rendered on a transparent overlay for the manga cutout.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

export function buildSpyRig() {
  const scene = new THREE.Scene();
  scene.background = null;                    // cutout overlay

  const camera = new THREE.PerspectiveCamera(40, 2, 0.1, 100);
  camera.position.set(0, 2.3, 8.4);
  camera.lookAt(0, 2.1, 0);

  const key = new THREE.DirectionalLight('#ffffff', 3.0);
  key.position.set(4, 6, 3);
  scene.add(key);
  const fill = new THREE.DirectionalLight('#8fa8ff', 0.8);
  fill.position.set(-4, 2, -2);
  scene.add(fill);
  // FRONT fill: without it, camera-facing surfaces crush to black under
  // the gritty manga pass and models read as silhouette blobs
  const front = new THREE.DirectionalLight('#ffffff', 1.5);
  front.position.set(0.5, 2.5, 10);
  scene.add(front);
  scene.add(new THREE.AmbientLight('#46424e', 1.1));

  const MAT = {
    body: new THREE.MeshStandardMaterial({ color: '#d7d9de', roughness: 0.55, metalness: 0.15 }),
    dark: new THREE.MeshStandardMaterial({ color: '#75727e', roughness: 0.6, metalness: 0.2 }),
    joint: new THREE.MeshStandardMaterial({ color: '#f2a03c', roughness: 0.6, metalness: 0.1 }),
    lens: new THREE.MeshStandardMaterial({ color: '#25405e', roughness: 0.25, metalness: 0.4 }),
    glow: new THREE.MeshStandardMaterial({ color: '#d8362a', emissive: '#d8362a', emissiveIntensity: 0.8 }),
  };
  function part(geo, mat, parent, x = 0, y = 0, z = 0) {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(x, y, z);
    parent.add(m);
    return m;
  }

  // ---- serpentine chain builder: N bendable segments, tapering, with
  // knuckle rings, rib plates and a cable run — decently complicated ----
  function buildChain({ segments, segLen, baseW }) {
    const root = new THREE.Group();
    const joints = [];
    let parent = root;
    for (let i = 0; i < segments; i++) {
      const j = new THREE.Group();
      j.position.y = i === 0 ? 0 : segLen;
      const f = i / (segments - 1);
      const w = baseW * (1 - f * 0.45);           // taper toward the tip
      // knuckle pin (the bendable joint) + CLEVIS FORK flanking it — the
      // industrial articulation every real robot arm has
      part(new THREE.CylinderGeometry(w * 0.55, w * 0.55, w * 1.6, 14)
        .rotateZ(Math.PI / 2), MAT.joint, j);
      part(new THREE.BoxGeometry(w * 0.18, w * 1.1, w * 1.15), MAT.body,
        j, -w * 0.72, 0, 0);
      part(new THREE.BoxGeometry(w * 0.18, w * 1.1, w * 1.15), MAT.body,
        j, w * 0.72, 0, 0);
      // link body: alternating box / cylinder profiles (real arms mix
      // castings and turned sections, identical links read as toy chain)
      if (i % 2 === 0) {
        part(new THREE.BoxGeometry(w * 0.95, segLen * 0.8, w * 1.05),
          MAT.body, j, 0, segLen * 0.52, 0);
      } else {
        part(new THREE.CylinderGeometry(w * 0.5, w * 0.56, segLen * 0.8, 14),
          MAT.body, j, 0, segLen * 0.52, 0);
      }
      // hydraulic piston: sleeve + rod reaching toward the next knuckle
      part(new THREE.CylinderGeometry(w * 0.16, w * 0.16, segLen * 0.42, 10)
        .rotateX(0.38), MAT.dark, j, 0, segLen * 0.3, w * 0.72);
      part(new THREE.CylinderGeometry(w * 0.09, w * 0.09, segLen * 0.5, 8)
        .rotateX(0.38), MAT.body, j, 0, segLen * 0.62, w * 0.55);
      // cable run along the back
      part(new THREE.CylinderGeometry(w * 0.11, w * 0.11, segLen * 0.9, 8),
        MAT.dark, j, 0, segLen * 0.5, -w * 0.62);
      // bolt heads on the clevis plates (both sides)
      for (const s of [-1, 1]) {
        part(new THREE.CylinderGeometry(w * 0.12, w * 0.12, w * 0.1, 8)
          .rotateZ(Math.PI / 2), MAT.dark, j, s * w * 0.84, 0, w * 0.34);
        part(new THREE.CylinderGeometry(w * 0.12, w * 0.12, w * 0.1, 8)
          .rotateZ(Math.PI / 2), MAT.dark, j, s * w * 0.84, 0, -w * 0.34);
      }
      // hose arc looping over alternating sides (slack service loop)
      const hose = new THREE.Mesh(
        new THREE.TorusGeometry(segLen * 0.3, w * 0.09, 6, 12, Math.PI),
        MAT.dark);
      hose.position.set((i % 2 ? 1 : -1) * w * 0.58, segLen * 0.5, 0);
      hose.rotation.y = Math.PI / 2;
      hose.rotation.z = (i % 2 ? 1 : -1) * 0.3;
      j.add(hose);
      // warning collar every third segment
      if (i % 3 === 2) part(new THREE.BoxGeometry(w * 1.05, w * 0.26, w * 1.1),
        MAT.joint, j, 0, segLen * 0.84, 0);
      // ROOT: a proper shoulder housing where the arm enters frame
      if (i === 0) {
        part(new THREE.BoxGeometry(w * 1.7, segLen * 0.7, w * 1.7),
          MAT.body, j, 0, segLen * 0.1, 0);
        part(new THREE.BoxGeometry(w * 1.85, segLen * 0.22, w * 1.85),
          MAT.dark, j, 0, segLen * 0.34, 0);
      }
      parent.add(j);
      joints.push(j);
      parent = j;
    }
    const tip = new THREE.Group();
    tip.position.y = segLen;
    parent.add(tip);
    return { root, joints, tip, segLen };
  }

  // ---- SPY-CAM arm: enters HIGH from the RIGHT edge and loops around
  // in a shepherd's-crook arc — the head arrives upright (the old
  // straight-down hang flipped the whole frame and the pod read
  // upside-down). Pulled toward the viewer (z+) for screen presence. ----
  const cam = buildChain({ segments: 10, segLen: 0.52, baseW: 0.44 });
  // base ABOVE the top edge (top of frame at this depth is y~6), slightly
  // right of center; BACK in depth so the pod shows its front. The head
  // is world-aimed + ball-jointed, so top entry can't flip it anymore.
  cam.root.position.set(5.9, 7.0, -1.8);   // user-tuned
  cam.root.rotation.z = Math.PI;               // chain grows DOWNWARD
  cam.anchor = { x: 5.9, y: 7.0 };             // LIVE-tunable (user-tuned)
  scene.add(cam.root);
  {
    // ICONIC camera silhouette — at cutout scale, what reads as "camera"
    // in B&W is a bold profile: boxy body, BIG lens disc with hood, top
    // handle, side viewfinder, tally light. (Meshy's head was dense
    // geometry that mushed into an unrecognizable blob.)
    // BALL-JOINT mount: the arm tip ends in a ball (on the TIP, so it
    // stays welded to the arm), and the pod pivots around it — any
    // relative rotation reads as a gimbal, never as detachment
    part(new THREE.SphereGeometry(0.2, 14, 12), MAT.joint, cam.tip, 0, 0.28, 0);
    const head = new THREE.Group();
    head.scale.setScalar(1.45);                // a touch bigger
    head.position.y = 0.28;                    // pivot AT the ball
    cam.tip.add(head);
    cam.head = head;
    // yoke post from the ball back to the pod body
    part(new THREE.CylinderGeometry(0.07, 0.09, 0.42, 10).rotateX(Math.PI / 2),
      MAT.dark, head, 0, 0, -0.35);
    // emissive-lifted body: the tilted pod faces away from both lights,
    // and lit-only midtones fall straight into the crosshatch bands —
    // the lift keeps its surfaces in the white band, dark stays accent
    const HB = new THREE.MeshStandardMaterial({
      color: '#e8eaee', roughness: 0.5, metalness: 0.1,
      emissive: '#6a6e74', emissiveIntensity: 0.85 });
    const podG = new THREE.Group();
    podG.name = 'podvis';
    podG.position.z = 0.55;                    // pod hangs FORWARD of ball
    head.add(podG);
    part(new THREE.BoxGeometry(0.66, 0.52, 0.95), HB, podG, 0, 0, -0.02);
    part(new THREE.BoxGeometry(0.5, 0.16, 0.72), MAT.dark, podG, 0, -0.3, -0.08);
    part(new THREE.BoxGeometry(0.68, 0.1, 0.3), MAT.dark, podG, 0, 0.1, -0.4);
    // lens assembly: LIGHT barrel, DARK hood ring, glass disc (the eye)
    const barrel = part(new THREE.CylinderGeometry(0.24, 0.27, 0.5, 18)
      .rotateX(Math.PI / 2), HB, podG, 0, 0.02, 0.66);
    part(new THREE.CylinderGeometry(0.35, 0.27, 0.18, 18).rotateX(Math.PI / 2),
      MAT.dark, barrel, 0, 0, 0.3);
    part(new THREE.CylinderGeometry(0.21, 0.21, 0.06, 18).rotateX(Math.PI / 2),
      MAT.lens, barrel, 0, 0, 0.37);
    part(new THREE.CylinderGeometry(0.08, 0.08, 0.05, 12).rotateX(Math.PI / 2),
      HB, barrel, 0, 0, 0.4);                     // specular pupil
    // focus ring detail on the barrel
    part(new THREE.CylinderGeometry(0.285, 0.285, 0.08, 18).rotateX(Math.PI / 2),
      MAT.dark, barrel, 0, 0, 0.02);
    // top handle (classic camcorder grip), light with dark posts
    part(new THREE.BoxGeometry(0.16, 0.11, 0.66), HB, podG, 0, 0.42, 0.02);
    part(new THREE.BoxGeometry(0.1, 0.15, 0.1), MAT.dark, podG, 0, 0.33, 0.3);
    part(new THREE.BoxGeometry(0.1, 0.15, 0.1), MAT.dark, podG, 0, 0.33, -0.24);
    // side viewfinder tube + tally light
    part(new THREE.CylinderGeometry(0.09, 0.1, 0.38, 12).rotateX(Math.PI / 2),
      HB, podG, 0.4, 0.2, -0.18);
    part(new THREE.CylinderGeometry(0.055, 0.055, 0.06, 10).rotateX(Math.PI / 2),
      MAT.dark, podG, 0.4, 0.2, -0.4);
    part(new THREE.SphereGeometry(0.055, 8, 8), MAT.glow, podG, -0.3, 0.32, 0.44);
    // the REAL camera inside the pod (usable as a render view)
    const povCam = new THREE.PerspectiveCamera(50, 2, 0.1, 100);
    povCam.position.set(0, 0, 0.5);
    povCam.rotation.y = Math.PI;               // looks out along +z
    podG.add(povCam);
    cam.povCam = povCam;
  }

  // ---- CLAW arm: slides in from the left edge ----
  // mirrors the camera arm's design: crook entry from the TOP-LEFT
  const claw = buildChain({ segments: 10, segLen: 0.52, baseW: 0.58 });
  // MIRROR TWIN of the camera arm: base OFF-FRAME at the TOP-LEFT,
  // same J-shape hang with the signs flipped. During the reach the
  // root slides ALONG the top edge (still off-frame) until it sits
  // over the apple — the base is never on screen.
  claw.root.position.set(-5.9, 7.0, -1.8);
  claw.anchor = { x: -5.9, y: 7.0 };           // LIVE-tunable
  // grab end-state, LIVE-tunable: where the reach lands (dx/dy), how
  // large the gripper is, and the angle the jaws STOP at on the fruit
  claw.gripCtl = { dx: 0, dy: 0, size: 1.35, open: 0.05, wide: 0.12,
                   spread: 1.15, grip: -0.2 };  // user-tuned closed-on-apple angle
  claw.poseOff = new Array(10).fill(0);   // per-BONE grab-pose sculpting
  claw.root.rotation.z = Math.PI;              // chain grows DOWNWARD
  scene.add(claw.root);
  {
    const hand = new THREE.Group();
    claw.tip.add(hand);
    claw.hand = hand;
    part(new THREE.BoxGeometry(0.44, 0.26, 0.34), MAT.body, hand, 0, 0.05, 0);
    part(new THREE.BoxGeometry(0.78, 0.10, 0.16), MAT.dark, hand, 0, 0.24, 0);
    part(new THREE.SphereGeometry(0.035, 8, 8), MAT.glow, hand, 0.2, 0.1, 0.16);
    claw.jaws = [];
    for (const side of [-1, 1]) {
      const jaw = new THREE.Group();
      jaw.position.set(side * 0.34, 0.24, 0);
      hand.add(jaw);
      part(new THREE.BoxGeometry(0.11, 0.5, 0.24), MAT.body, jaw, 0, 0.25, 0);
      part(new THREE.BoxGeometry(0.06, 0.3, 0.2), MAT.dark,
        jaw, -side * 0.085, 0.46, 0);
      part(new THREE.BoxGeometry(0.05, 0.05, 0.22), MAT.joint,
        jaw, -side * 0.11, 0.38, 0);
      claw.jaws.push({ jaw, side, open: 0.34, closed: 0.13 });
    }
    const socket = new THREE.Group();
    socket.position.set(0, 0.55, 0);
    hand.add(socket);
    claw.socket = socket;
  }

  // ---- posing: unfurl from a coil into an explicit bend PROFILE (a
  // function of position along the chain), with idle breathing ----
  const cl = (x) => Math.min(1, Math.max(0, x));
  // ---- SPRING-DRIVEN POSING ----
  // The old parametric elastic painted the swing onto the entrance
  // clock: when the clock ended, motion stopped dead — the awkward
  // too-fast settle. Now each joint is a real damped harmonic
  // oscillator (angle + angular velocity) chasing a moving target.
  // The entrance just moves the TARGET (a smooth root->tip wave); the
  // overshoot, the swing past, and the slow ring-down all emerge from
  // momentum, and the settle continues for as long as the physics
  // says, blending into the idle breathing with no seam.
  //   base: stiff + near-critically damped  (planted shoulder)
  //   tip:  soft + underdamped              (free end swings widest)
  function poseChain(rig, enter, t, opts) {
    const N = rig.joints.length;
    if (!rig.spr) {
      rig.spr = { th: new Array(N).fill(null),
                  om: new Array(N).fill(0), lastT: t };
    }
    const dt = Math.min(Math.max(t - rig.spr.lastT, 0), 1 / 30);
    rig.spr.lastT = t;
    const eAll = cl(enter);
    const springy = opts.springy ?? 1;
    for (let i = 1; i < N; i++) {
      const f = i / (N - 1);
      // the target drive: plain smooth unfurl wave, root arrives first
      const local = cl((eAll - f * 0.42) / 0.58);
      const eJ = local * local * (3 - 2 * local);
      const coil = 0.4;                             // curled, not balled
      const S = opts.profile(f);
      const idle = Math.sin(t * 1.1 + i * 0.8 + opts.phase) * 0.028
                 + Math.sin(t * 0.53 + i * 1.7) * 0.014;
      let target = coil * (1 - eJ) + (S + idle * opts.idleAmp) * eJ;
      if (opts.poseOff) target += (opts.poseOff[i] || 0) * (opts.poseAmt ?? 1);
      // spring constants graded along the arm
      const k = 30 - 21 * f * springy;              // stiffness
      const zeta = 0.95 - 0.52 * f * springy;       // damping ratio
      const cD = 2 * zeta * Math.sqrt(k);
      let th = rig.spr.th[i];
      if (th === null) th = target;                 // first frame: snap
      let om = rig.spr.om[i];
      // two semi-implicit Euler substeps: stable at any real dt
      for (let s = 0; s < 2; s++) {
        const h = dt / 2;
        om += (k * (target - th) - cD * om) * h;
        th += om * h;
      }
      rig.spr.th[i] = th;
      rig.spr.om[i] = om;
      rig.joints[i].rotation.z = th;
    }
    // root: SPRING-DRIVEN translation — the swoop carries momentum,
    // overshoots its mark a touch and swings back, instead of riding a
    // rigid rail (this is most of the 'dynamic' in the claw's dive)
    const l0 = cl(eAll / 0.58);
    const eR = l0 * l0 * (3 - 2 * l0);
    const tx = opts.anchor.x + opts.slide.x * (1 - eR);
    const ty = opts.anchor.y + opts.slide.y * (1 - eR);
    if (!rig.sprR) rig.sprR = { x: null, y: 0, vx: 0, vy: 0 };
    if (rig.sprR.x === null) { rig.sprR.x = tx; rig.sprR.y = ty; }
    const kR = 22, cR = 2 * 0.55 * Math.sqrt(22);
    for (let s = 0; s < 2; s++) {
      const h = dt / 2;
      rig.sprR.vx += (kR * (tx - rig.sprR.x) - cR * rig.sprR.vx) * h;
      rig.sprR.x += rig.sprR.vx * h;
      rig.sprR.vy += (kR * (ty - rig.sprR.y) - cR * rig.sprR.vy) * h;
      rig.sprR.y += rig.sprR.vy * h;
    }
    rig.root.position.x = rig.sprR.x;
    rig.root.position.y = rig.sprR.y;
  }

  // ---- THE MANGA WORLD ABOVE: the painted scene is a small sunken
  // garden in a bigger B&W machine-world. Everything lives at y >= 10.2
  // — the resting frame top is ~6 and the parked arms reach ~9, so NONE
  // of it can appear until the ascent lifts the camera 7 units. The two
  // arms' cable trunks run down from this machinery: the heist rig is
  // OF that world. ----
  const mangaWorld = new THREE.Group();
  {
    const world = mangaWorld;
    // THE ROOM: the painted world turns out to be a SCREEN mounted in
    // a manga interior. The screen opening matches the comp shader's
    // zoomed rect exactly (center of view at z=0: half 1.63 x 0.92 for
    // a 0.30 zoom at 16:9). Walls surround the opening so the painting
    // shows THROUGH it; everything else is B&W set dressing.
    const HW = 1.63, HH = 0.92, CY = 2.1;
    // UNLIT wall material: lit walls picked up a diagonal light
    // gradient that the manga pass quantized into fake panel seams
    const WALL = new THREE.MeshBasicMaterial({ color: '#e4e5e2' });
    const INK = new THREE.MeshBasicMaterial({ color: '#191a1e' });
    // wall segments around the opening (never covering it)
    part(new THREE.BoxGeometry(44, 10, 0.3), WALL, world,
      0, CY + HH + 5.06, -0.85);                    // above
    part(new THREE.BoxGeometry(44, 10, 0.3), WALL, world,
      0, CY - HH - 5.06, -0.85);                    // below
    part(new THREE.BoxGeometry(19, 44, 0.3), WALL, world,
      -(HW + 9.56), CY, -0.85);                     // left
    part(new THREE.BoxGeometry(19, 44, 0.3), WALL, world,
      HW + 9.56, CY, -0.85);                        // right
    // bezel: one uniform dark frame, nothing else attached to it
    part(new THREE.BoxGeometry(HW * 2 + 0.72, 0.36, 0.2), INK,
      world, 0, CY + HH + 0.18, 0.02);
    part(new THREE.BoxGeometry(HW * 2 + 0.72, 0.36, 0.2), INK,
      world, 0, CY - HH - 0.18, 0.02);
    part(new THREE.BoxGeometry(0.36, HH * 2 + 0.72, 0.2), INK,
      world, -HW - 0.18, CY, 0.02);
    part(new THREE.BoxGeometry(0.36, HH * 2 + 0.72, 0.2), INK,
      world, HW + 0.18, CY, 0.02);
    // small orange corner screws ON the frame
    for (const sx of [-1, 1]) for (const sy of [-1, 1]) {
      part(new THREE.CylinderGeometry(0.055, 0.055, 0.08, 8)
        .rotateX(Math.PI / 2), MAT.joint, world,
        sx * (HW + 0.18), CY + sy * (HH + 0.18), 0.14);
    }
    // stand: post + feet planting the screen in the room
    part(new THREE.BoxGeometry(0.5, 1.3, 0.4), INK, world,
      0, CY - HH - 0.85, -0.12);
    part(new THREE.BoxGeometry(2.6, 0.22, 0.9), WALL, world,
      0, CY - HH - 1.55, -0.1);
    // floor line: a darker skirting across the lower wall
    part(new THREE.BoxGeometry(44, 0.35, 0.32), INK, world,
      0, CY - HH - 1.72, -0.68);
    // CABLES from the screen's top edge up out of frame — the arms'
    // feed lines: the heist rig is plugged into this screen's world
    for (const cx of [-0.55, 0.55]) {
      part(new THREE.CylinderGeometry(0.06, 0.08, 9, 8), INK,
        world, cx, CY + HH + 4.85, -0.05);
    }
    // wall dressing: a vent + a small hung frame
    part(new THREE.BoxGeometry(1.5, 0.9, 0.12), INK, world,
      -4.6, 4.4, -0.68);
    part(new THREE.BoxGeometry(1.15, 1.5, 0.1), INK, world,
      4.4, 3.4, -0.7);
    part(new THREE.BoxGeometry(0.85, 1.2, 0.12), WALL, world,
      4.4, 3.4, -0.62);
    scene.add(world);
    world.traverse((o) => {
      o.frustumCulled = false;
      // skip the triplanar wash patch: its object-space blotches render
      // as giant phantom panels on room-scale surfaces
      o.userData.noStyle = true;
    });
    world.visible = false;   // gated by the zoom — NEVER visible at rest
  }

  // The overlay scene is only the two arms — draw everything always.
  // Frustum culling was deferring the Meshy heads' texture upload and
  // shader compile to the exact frame the arm entered the view, which
  // hitched the entrance.
  function noCull(root) {
    root.traverse((o) => { o.frustumCulled = false; });
  }
  noCull(scene);

  // update(t, phases): cam/claw = entry 0..1, grab closes the jaws,
  // lookTarget = point (overlay-scene coords) the pod should stare at
  const _qa = new THREE.Quaternion(), _qb = new THREE.Quaternion();
  const _qp = new THREE.Quaternion(), _qd = new THREE.Quaternion();
  const _qL = new THREE.Quaternion();
  const _eul = new THREE.Euler();
  const _tgt = new THREE.Vector3(), _tipP = new THREE.Vector3();
  const _hp = new THREE.Vector3(), _fwd = new THREE.Vector3();
  const _m4 = new THREE.Matrix4();
  const clampV = (v, a, b) => Math.min(b, Math.max(a, v));
  let aimBend = 0;      // cam arm aim-assist state
  let aimBendClaw = 0;  // claw arm aim-assist state
  function update(t, { cam: camE = 0, claw: clawE = 0, grab = 0,
                       reach = 0, lookTarget = null, rise = 0,
                       camExit = 0, exit = 0 } = {}) {
    // J-SHAPE from the right side: straight stem in, strong downward
    // curve through the middle, tip hooks back UP — the head rides the
    // hook upright
    // J from the top: straight stem down, curl LEFT toward the scene,
    // tip hooks back — negative bends curl a descending chain leftward
    poseChain(cam, Math.max(0, camE - camExit * 0.85), t, {
      phase: 0.45, idleAmp: 0.8, springy: 1,
      profile: (f) => f < 0.3 ? -0.04 : f < 0.72 ? -0.30 : 0.26,
      anchor: { x: cam.anchor.x + camExit * 6.5,
                y: cam.anchor.y + rise + camExit * 1.5 },
      slide: { x: 0, y: 1.8 },
    });
    const e = cl(camE);
    // resolve the gaze target (mouse steals attention upstream)
    if (lookTarget) _tgt.copy(lookTarget);
    else {
      cam.head.getWorldPosition(_tgt);
      _tgt.x -= 3; _tgt.y -= 2.5;       // default gaze: down-left
    }
    _tgt.x += Math.sin(t * 0.6) * 0.5;
    _tgt.y += Math.sin(t * 0.83 + 1.2) * 0.35;

    // ARM-ASSISTED AIM: the last three joints bend toward the target —
    // the ARM tracks the mouse/apple, so the head never needs to twist
    // beyond its mount to reach a gaze
    cam.tip.getWorldPosition(_tipP);
    cam.tip.getWorldQuaternion(_qp);
    _fwd.set(0, 1, 0).applyQuaternion(_qp);
    const thCur = Math.atan2(_fwd.y, _fwd.x);
    const thTgt = Math.atan2(_tgt.y - _tipP.y, _tgt.x - _tipP.x);
    let err = thTgt - thCur;
    err = Math.atan2(Math.sin(err), Math.cos(err));
    aimBend += (clampV(err, -1.0, 1.0) * 0.5 - aimBend)
             * (0.1 + 0.18 * cl((1 - e) * 6));   // continuous ramp
    const N = cam.joints.length;
    for (let k = N - 3; k < N; k++) {
      cam.joints[k].rotation.z += (aimBend / 3) * e;
    }

    // HEAD: upright WORLD aim (lookAt keeps world-up, so no inherited
    // roll from the arm), with the gaze yaw CLAMPED to the leftward
    // half-space — it can never aim back into its own arm. The ball
    // joint makes whatever relative angle remains read as a gimbal.
    if (e > 0.03) {
      cam.head.getWorldPosition(_hp);
      _fwd.copy(_tgt).sub(_hp);
      // TOP-mount constraint: the head may gaze anywhere except back UP
      // into its own chain — clamp elevation, leave yaw free (the old
      // leftward-fan clamp was for the side entry and blocked honest
      // rightward gazes, which is why the mouse-aim felt wrong)
      const horiz = Math.max(Math.hypot(_fwd.x, _fwd.z), 1e-4);
      const elev = Math.atan2(_fwd.y, horiz);
      const elevC = Math.min(elev, 0.15);
      const scale = Math.tan(elevC) * horiz;
      _tgt.y = _hp.y + scale;
      _qa.copy(cam.head.quaternion);
      cam.head.lookAt(_tgt);
      cam.head.rotateY(0.1);                   // whisper of profile
      _qb.copy(cam.head.quaternion);
      cam.head.quaternion.copy(_qa)
        .slerp(_qb, 0.18 + 0.37 * cl((1 - e) * 6));  // continuous ramp
    }

    // mirrored J at rest; the REACH blends it toward a plumb drop while
    // the root rides the (off-frame) top edge until it hangs over the
    // apple — swoop without ever showing the base
    claw.root.visible = clawE > 0.001;
    const rch = cl(reach);
    poseChain(claw, Math.max(0, clawE), t, {
      phase: -0.3, idleAmp: 0.7, springy: 1.35,
      poseOff: claw.poseOff, poseAmt: rch,
      profile: (f) => {
        const j = f < 0.3 ? 0.04 : f < 0.72 ? 0.30 : -0.26;
        const r = f < 0.5 ? 0.10 : 0.02;
        return j + (r - j) * rch;
      },
      // exit lift: during the carry-out the root climbs well above the
      // rising camera, so claw + apple leave through the top edge for
      // real before anything is hidden
      anchor: { x: claw.anchor.x + (3.4 + claw.gripCtl.dx) * rch,
                y: claw.anchor.y + claw.gripCtl.dy * rch + rise
                   + exit * 5.5 },
      slide: { x: 0, y: 1.8 },
    });
    // FOLLOW logic (same as the camera arm): the claw's last joints
    // lean toward the gaze target — it tracks the mouse / apple too
    {
      claw.tip.getWorldPosition(_tipP);
      claw.tip.getWorldQuaternion(_qp);
      _fwd.set(0, 1, 0).applyQuaternion(_qp);
      const thC = Math.atan2(_fwd.y, _fwd.x);
      const thT = lookTarget
        ? Math.atan2(lookTarget.y - _tipP.y, lookTarget.x - _tipP.x) : thC;
      let err2 = Math.atan2(Math.sin(thT - thC), Math.cos(thT - thC));
      aimBendClaw += (clampV(err2, -1, 1) * 0.4 - aimBendClaw) * 0.12;
      const e2 = cl(clawE);
      const N2 = claw.joints.length;
      for (let k = N2 - 3; k < N2; k++) {
        claw.joints[k].rotation.z += (aimBendClaw / 3) * e2;
      }
    }

    // hand is WORLD-driven like the head: jaws hang down but LEAN toward
    // the gaze target (mouse/apple) — a crane grab that points where it
    // intends to grab, decoupled from the chain's roll
    claw.hand.getWorldPosition(_hp);
    let lean = 0;
    if (lookTarget) {
      // angle from straight-down to the hand->target direction, about Z
      lean = Math.atan2(lookTarget.x - _hp.x, -(lookTarget.y - _hp.y));
      lean = clampV(lean, -0.55, 0.55);
    }
    // the room exists only while the zoom-out is under way
    mangaWorld.visible = rise > 0.01;

    // NO turn: yawing the clamp mid-dive made the C-jaws overlap on
    // screen and read as FULLY CLOSED until the grab — face-on always
    const showYaw = 0;
    claw.hand.parent.getWorldQuaternion(_qp);
    _qd.setFromEuler(
      _eul.set(Math.PI, showYaw, Math.sin(t * 0.7) * 0.06 - lean));
    _qL.copy(claw.hand.quaternion);
    claw.hand.quaternion.copy(_qp.invert()).multiply(_qd);
    // TIGHT world-lock: at 0.2/frame the hand lagged the fast-diving
    // arm and twisted through EDGE-ON angles — the stacked C-jaws read
    // as a fully-closed clamp mid-descent
    claw.hand.quaternion.copy(_qL.slerp(claw.hand.quaternion, 0.65));
    for (const j of claw.jaws) {
      j.jaw.position.x = j.side * (j.open + (j.closed - j.open) * cl(grab));
    }
    if (claw.model) {
      // spread stretches the cradle HORIZONTALLY — visibly wider mouth
      claw.model.scale.set(claw.gripCtl.size * claw.gripCtl.spread,
                           claw.gripCtl.size, claw.gripCtl.size);
    }
    if (claw.pick) {
      // START WIDE, CLOSE TO THE OG FIT: the dive yawns the jaws wider
      // (reach-driven); the grab closes them back to the resting value
      // that fits the apple perfectly — never past it
      const gq = cl(grab);
      const rest = claw.pick.base + claw.gripCtl.open + claw.gripCtl.wide;
      const yawn = 0.45 * cl((rch - 0.1) / 0.75);
      // grab closes past the resting width down to gripCtl.grip —
      // the actual on-the-apple angle, live-tunable with ,/.
      const open = claw.gripCtl.grip
                 + (rest + yawn - claw.gripCtl.grip) * (1 - gq);
      // NEGATED: the hand frame's flip mirrors z-rotations, so the
      // on-screen 'open' direction is the opposite of model space —
      // the old sign made the yawn CLOSE the tips mid-dive
      claw.pick.jawA.rotation[claw.pick.rotAxis] =
        -open * claw.pick.jawA.userData.dir;
      claw.pick.jawB.rotation[claw.pick.rotAxis] =
        -open * claw.pick.jawB.userData.dir;
    } else if (claw.model) {
      claw.model.scale.set(1 - 0.18 * cl(grab), 1, 1);
    }
  }

  // ---- Meshy model upgrades (graceful: procedural stays if absent) ----
  // normalize an arbitrary GLB: longest axis -> Y, scaled to `len`,
  // centered, returned wrapped so callers can place it cleanly
  function fitToY(root, len) {
    const box = new THREE.Box3().setFromObject(root);
    const size = box.getSize(new THREE.Vector3());
    if (size.x >= size.y && size.x >= size.z) root.rotation.z = Math.PI / 2;
    else if (size.z >= size.y && size.z >= size.x) root.rotation.x = Math.PI / 2;
    const wrap = new THREE.Group();
    wrap.add(root);
    const b2 = new THREE.Box3().setFromObject(wrap);
    const s2 = b2.getSize(new THREE.Vector3());
    wrap.scale.setScalar(len / Math.max(s2.y, 1e-6));
    const b3 = new THREE.Box3().setFromObject(wrap);
    const c3 = b3.getCenter(new THREE.Vector3());
    const inner = new THREE.Group();
    inner.add(wrap);
    wrap.position.sub(c3);
    return inner;
  }
  const loader = new GLTFLoader();
  {
    // ---- PROCEDURAL FRUIT CLAMP (user-picked design): a chunky hinge
    // block and two C-shaped jaws that CRADLE the apple, rubber pads on
    // the inner faces, real hinge articulation. Built in hand space:
    // the hand frame is flipped, so local +y renders DOWNWARD. ----
    for (const m2 of [...claw.hand.children]) if (m2.isMesh) claw.hand.remove(m2);
    for (const j of claw.jaws) claw.hand.remove(j.jaw);
    claw.jaws.length = 0;
    const model = new THREE.Group();
    // stem up to the arm tip
    part(new THREE.CylinderGeometry(0.12, 0.1, 0.4, 10), MAT.dark,
      model, 0, -0.3, 0);
    // hinge block + pin (echoes the arm's clevis-and-knuckle language)
    part(new THREE.BoxGeometry(0.6, 0.42, 0.44), MAT.body, model, 0, -0.02, 0);
    part(new THREE.CylinderGeometry(0.15, 0.15, 0.74, 12).rotateX(Math.PI / 2),
      MAT.joint, model, 0, 0.1, 0);
    part(new THREE.BoxGeometry(0.34, 0.18, 0.5), MAT.dark, model, 0, -0.2, 0);
    const CC = new THREE.Vector3(0, 1.0, 0);    // cradle (apple) center
    const RJ = 1.0;                             // jaw radius — WIDE grip
    const mkJaw = (side) => {
      const pivot = new THREE.Group();
      pivot.position.set(side * 0.2, 0.12, side * 0.07);
      // C-arc wrapping the cradle: starts near the hinge, curls under.
      // Local frame: hinge is BELOW the cradle center (y 0.12 < 0.85),
      // i.e. at angle 270 deg on the circle. Left jaw sweeps 270->150,
      // right jaw 270->390 (mirrored).
      // SHORTER arcs: tips stop at the fruit's sides, so the mouth
      // spans the whole bottom — a wide catch the apple nests into
      const arc = new THREE.Mesh(
        new THREE.TorusGeometry(RJ, 0.13, 10, 26,
          1.8), MAT.body);
      arc.rotation.z = side > 0 ? -Math.PI / 2 : 2.912;
      arc.position.copy(CC).sub(pivot.position);
      pivot.add(arc);
      // rubber pad lining on the inner face
      const pad = new THREE.Mesh(
        new THREE.TorusGeometry(RJ - 0.14, 0.055, 8, 22, 1.45), MAT.dark);
      pad.rotation.z = side > 0 ? -Math.PI / 2 + 0.18 : 2.95;
      pad.position.copy(arc.position);
      pivot.add(pad);
      // fingertip cap at the far end of the C
      const tipA = side > 0 ? -Math.PI / 2 + 1.8 : 2.912;
      const cap = new THREE.Mesh(new THREE.SphereGeometry(0.16, 12, 10),
        MAT.dark);
      cap.position.set(
        arc.position.x + RJ * Math.cos(tipA),
        arc.position.y + RJ * Math.sin(tipA),
        arc.position.z);
      pivot.add(cap);
      // hinge cheek plate
      part(new THREE.BoxGeometry(0.16, 0.3, 0.3), MAT.body, pivot,
        0, 0.02, 0);
      pivot.userData.dir = side;
      model.add(pivot);
      return pivot;
    };
    claw.pick = { rotAxis: 'z', jawA: mkJaw(-1), jawB: mkJaw(1),
                  base: 0 };      // at size 2.15 the closed cradle
                                  // already wraps the apple
    // the steal socket IS the cradle hole: reparent it into the model
    // at the cradle center so the apple rides the hole exactly
    claw.socket.position.copy(CC);
    model.add(claw.socket);
    model.position.y = 0.35;
    claw.hand.add(model);
    claw.model = model;
    noCull(model);
  }
  // camhead2 = the HOODED-EYE model (chunky, one huge lens). NOT
  // camhead.glb — that first dense model kept async-replacing the
  // procedural pod with an unreadable blob. Emissive-lifted light metal
  // so the manga pass keeps its surfaces out of the crosshatch bands.
  loader.load('./models/camhead2.glb', (g) => {
    const podG = cam.head.getObjectByName('podvis');
    for (const m of [...podG.children]) if (m.isMesh) podG.remove(m);
    const lifted = new THREE.MeshStandardMaterial({
      color: '#e6e8ec', roughness: 0.5, metalness: 0.1,
      emissive: '#63676d', emissiveIntensity: 0.85 });
    g.scene.traverse((o) => { if (o.isMesh) o.material = lifted; });
    const model = fitToY(g.scene, 1.15);
    // -x90 with NO y-flip: same lens direction as the old (+x90 + y-flip)
    // combo, but the model's VERTICAL stays upright — the old combo put
    // the mount stub skyward, which read as an upside-down camera
    model.rotation.x = -Math.PI / 2;
    cam.head.getObjectByName('podvis').add(model);
    noCull(model);
    console.log('[snakeArms] hooded-eye camera head installed');
  }, undefined, () => console.log('[snakeArms] no camhead2.glb — procedural pod'));
  // NOTE: the Meshy arm-segment model is intentionally NOT used — at
  // segment scale it read as black beads on a string, not an arm. The
  // procedural links (thick plates + knuckle rings + cables) read right.

  return { scene, camera, update, cam, claw };


}
