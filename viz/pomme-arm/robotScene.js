// robotScene.js — the manga robotics scene as an importable module.
// Procedural rigged arm (base/shoulder/elbow/wrist joint chain), 3-finger
// claw RIGGED for grabbing, wrist camera pod (pan/tilt), and an observer
// stalk camera that tracks the claw — with a real THREE camera mounted on
// it (rig.obsCam) usable as a render view for the video.
// Choreography: reach -> claw closes on the apple -> lift -> release, loop.

import * as THREE from 'three';
import { buildApple } from './apple.js';

// opts.overlay: CUTOUT mode for the divide — just the arm + rigged cameras
// on a transparent background (no floor plate, no pedestal, no own apple);
// the manga pass alpha then composites them INTO the painterly scene.
export function buildRobotScene({ overlay = false } = {}) {
  const scene = new THREE.Scene();
  scene.background = overlay ? null : new THREE.Color('#b9b5ad');

  const camera = new THREE.PerspectiveCamera(40, 2, 0.1, 100);
  camera.position.set(4.6, 3.4, 6.2);
  camera.lookAt(0, 1.9, 0);

  const key = new THREE.DirectionalLight('#ffffff', 3.0);
  key.position.set(4, 6, 3);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.camera.left = key.shadow.camera.bottom = -5;
  key.shadow.camera.right = key.shadow.camera.top = 5;
  scene.add(key);
  const fill = new THREE.DirectionalLight('#8fa8ff', 0.8);
  fill.position.set(-4, 2, -2);
  scene.add(fill);
  scene.add(new THREE.AmbientLight('#46424e', 1.1));

  if (!overlay) {
    const plate = new THREE.Mesh(
      new THREE.CylinderGeometry(5, 5, 0.12, 48),
      new THREE.MeshStandardMaterial({ color: '#8d8a94', roughness: 0.85, metalness: 0.1 }),
    );
    plate.position.y = -0.06;
    plate.receiveShadow = true;
    scene.add(plate);
  }

  const M = {
    body:  new THREE.MeshStandardMaterial({ color: '#d7d9de', roughness: 0.55, metalness: 0.15 }),
    dark:  new THREE.MeshStandardMaterial({ color: '#75727e', roughness: 0.6, metalness: 0.2 }),
    joint: new THREE.MeshStandardMaterial({ color: '#f2a03c', roughness: 0.6, metalness: 0.1 }),
    lens:  new THREE.MeshStandardMaterial({ color: '#25405e', roughness: 0.25, metalness: 0.4 }),
    glow:  new THREE.MeshStandardMaterial({ color: '#d8362a', emissive: '#d8362a', emissiveIntensity: 0.8 }),
  };
  function part(geo, mat, parent, x = 0, y = 0, z = 0) {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(x, y, z);
    m.castShadow = true;
    parent.add(m);
    return m;
  }

  // ---- rig: SIMPLE industrial — slab base, plain box beams, clevis-pin
  // joints like an excavator. Clean silhouettes for the manga pass. ----
  const rig = {};
  const base = new THREE.Group();
  scene.add(base);
  rig.base = base;
  part(new THREE.BoxGeometry(1.7, 0.22, 1.15), M.dark, base, 0, 0.11, 0);
  part(new THREE.CylinderGeometry(0.6, 0.72, 0.32, 20), M.body, base, 0, 0.38, 0);
  // clevis plates the shoulder pin sits between
  part(new THREE.BoxGeometry(0.08, 0.6, 0.52), M.body, base, -0.31, 0.72, 0);
  part(new THREE.BoxGeometry(0.08, 0.6, 0.52), M.body, base, 0.31, 0.72, 0);

  const shoulder = new THREE.Group();
  shoulder.position.set(0, 0.8, 0);
  base.add(shoulder);
  rig.shoulder = shoulder;
  part(new THREE.CylinderGeometry(0.19, 0.19, 0.76, 16).rotateZ(Math.PI / 2),
    M.joint, shoulder);

  const upperArm = new THREE.Group();
  shoulder.add(upperArm);
  part(new THREE.BoxGeometry(0.42, 1.78, 0.5), M.body, upperArm, 0, 0.8, 0);
  part(new THREE.BoxGeometry(0.44, 0.28, 0.52), M.dark, upperArm, 0, 1.4, 0);

  const elbow = new THREE.Group();
  elbow.position.set(0, 1.7, 0);
  upperArm.add(elbow);
  rig.elbow = elbow;
  part(new THREE.CylinderGeometry(0.16, 0.16, 0.6, 16).rotateZ(Math.PI / 2),
    M.joint, elbow);

  const foreArm = new THREE.Group();
  elbow.add(foreArm);
  part(new THREE.BoxGeometry(0.3, 1.5, 0.38), M.body, foreArm, 0, 0.68, 0);

  const wrist = new THREE.Group();
  wrist.position.set(0, 1.42, 0);
  foreArm.add(wrist);
  rig.wrist = wrist;
  part(new THREE.CylinderGeometry(0.12, 0.12, 0.46, 14).rotateZ(Math.PI / 2),
    M.joint, wrist);

  // CLAMP: industrial two-jaw parallel gripper, HANGING DOWNWARD like a
  // crane-game claw — it positions above the target, descends, and the two
  // jaws close horizontally to clamp it.
  const claw = new THREE.Group();
  claw.position.set(0, 0.24, 0);
  claw.rotation.x = Math.PI;         // assembly points DOWN from the wrist
  claw.scale.setScalar(1.35);        // beefy industrial clamp
  wrist.add(claw);
  rig.claw = claw;
  // housing: boxy actuator block + crossbar rail the jaws slide on
  part(new THREE.BoxGeometry(0.5, 0.28, 0.34), M.body, claw, 0, 0.14, 0);
  part(new THREE.BoxGeometry(0.86, 0.10, 0.16), M.dark, claw, 0, 0.31, 0);
  part(new THREE.CylinderGeometry(0.05, 0.05, 0.24, 10), M.joint, claw, 0, 0.30, 0.14);
  part(new THREE.SphereGeometry(0.03, 8, 8), M.glow, claw, 0.2, 0.05, 0.18);
  // grab socket: between the jaws
  const socket = new THREE.Group();
  socket.position.set(0, 0.72, 0);
  claw.add(socket);
  rig.socket = socket;
  // two opposing jaws: L-shaped plates that slide along the rail
  rig.jaws = [];
  for (const side of [-1, 1]) {
    const jaw = new THREE.Group();
    jaw.position.set(side * 0.42, 0.31, 0);   // open position on the rail
    claw.add(jaw);
    const arm1 = part(new THREE.BoxGeometry(0.12, 0.55, 0.26), M.body,
      jaw, 0, 0.28, 0);
    arm1.rotation.z = side * 0.06;
    // inward-facing pad with grip teeth
    part(new THREE.BoxGeometry(0.06, 0.34, 0.22), M.dark,
      jaw, -side * 0.10, 0.52, 0);
    part(new THREE.BoxGeometry(0.05, 0.05, 0.24), M.joint,
      jaw, -side * 0.13, 0.44, 0);
    part(new THREE.BoxGeometry(0.05, 0.05, 0.24), M.joint,
      jaw, -side * 0.13, 0.60, 0);
    rig.jaws.push({ jaw, side, open: 0.42, closed: 0.16 });
  }

  // wrist camera pod (pan/tilt)
  const pod = new THREE.Group();
  pod.position.set(0, 0.05, 0.34);
  wrist.add(pod);
  rig.pod = pod;
  part(new THREE.BoxGeometry(0.26, 0.2, 0.3), M.dark, pod);
  const podCam = part(new THREE.CylinderGeometry(0.09, 0.11, 0.16, 14)
    .rotateX(Math.PI / 2), M.body, pod, 0, 0, 0.2);
  part(new THREE.CylinderGeometry(0.07, 0.07, 0.05, 14).rotateX(Math.PI / 2),
    M.lens, podCam, 0, 0, 0.09);
  part(new THREE.SphereGeometry(0.03, 8, 8), M.glow, pod, 0.09, 0.13, 0.1);

  // observer stalk camera — tracks the claw; carries a REAL render camera
  const stalk = new THREE.Group();
  stalk.position.set(2.3, 0, -1.6);
  scene.add(stalk);
  part(new THREE.CylinderGeometry(0.08, 0.12, 2.6, 12), M.dark, stalk, 0, 1.3, 0);
  const obsHead = new THREE.Group();
  obsHead.position.set(0, 2.6, 0);
  stalk.add(obsHead);
  rig.obsHead = obsHead;
  part(new THREE.BoxGeometry(0.34, 0.26, 0.44), M.body, obsHead);
  part(new THREE.CylinderGeometry(0.1, 0.13, 0.2, 14).rotateX(Math.PI / 2),
    M.lens, obsHead, 0, 0, 0.3);
  part(new THREE.SphereGeometry(0.035, 8, 8), M.glow, obsHead, 0.12, 0.16, 0.12);
  const obsCam = new THREE.PerspectiveCamera(45, 2, 0.1, 100);
  obsCam.position.set(0, 0, 0.32);
  obsCam.rotation.y = Math.PI;   // camera looks down -z; head "faces" +z
  obsHead.add(obsCam);
  rig.obsCam = obsCam;

  // ---- the apple to grab (standalone page only; in overlay mode the
  // robot steals the PAINTED apple from the nature layer instead) ----
  // calibrated: plumb clamp socket lands here at descend+grip
  const APPLE_POS = new THREE.Vector3(2.10, 1.22, 1.23);
  let apple = null;
  const appleRest = new THREE.Vector3().copy(APPLE_POS);
  if (!overlay) {
    apple = buildApple();
    apple.group.scale.setScalar(0.42);
    scene.add(apple.group);
    const pedestal = new THREE.Mesh(
      new THREE.CylinderGeometry(0.4, 0.55, 0.85, 18), M.dark);
    pedestal.castShadow = pedestal.receiveShadow = true;
    pedestal.position.set(APPLE_POS.x, 0.42, APPLE_POS.z);
    scene.add(pedestal);
    apple.group.position.copy(appleRest);
  }

  // ---- choreography: reach -> grip -> lift -> release, 10s loop ----
  const clawTip = new THREE.Vector3();
  const socketW = new THREE.Vector3();
  const yawToApple = Math.atan2(APPLE_POS.x, APPLE_POS.z);
  // crane rule: the wrist counter-rotates the arm's pitches so the clamp
  // stays PLUMB — it always hangs straight down and descends onto targets
  function applyPose(reach, grip, lift, descend, yaw, t) {
    rig.base.rotation.y = yaw;
    rig.shoulder.rotation.x = -0.12 + reach * 1.32 - lift * 0.85
                            + descend * 0.30;
    rig.elbow.rotation.x = 0.3 + reach * 1.05 - lift * 0.45 + descend * 0.16;
    rig.wrist.rotation.x = -(rig.shoulder.rotation.x + rig.elbow.rotation.x);
    rig.wrist.rotation.y = (1 - grip) * t * 0.25;
    for (const j of rig.jaws) {
      const x = j.open + (j.closed - j.open) * grip;
      j.jaw.position.x = j.side * x;
    }
  }

  function pose(t) {
    const loop = t % 10.0;
    const e = (a, b, x) => THREE.MathUtils.smoothstep(x, a, b);
    const reach = e(1.0, 2.6, loop) - e(7.0, 8.6, loop);
    const descend = e(2.6, 3.2, loop) - e(6.2, 6.9, loop);
    const grip = e(3.3, 3.8, loop) - e(6.2, 6.9, loop);
    const lift = e(4.2, 5.4, loop) - e(6.2, 7.2, loop);

    applyPose(reach, grip, lift, descend,
      (1 - reach) * 0.4 * Math.sin(t * 0.3) + reach * yawToApple, t);
    rig.pod.rotation.y = Math.sin(t * 0.9) * 0.5;
    rig.pod.rotation.x = Math.sin(t * 0.6 + 1.0) * 0.25;
    rig.claw.getWorldPosition(clawTip);
    rig.obsHead.lookAt(clawTip);
    if (grip > 0.9 && !rig._calLogged) {   // calibration: where the claw is
      rig._calLogged = true;
      rig.socket.getWorldPosition(socketW);
      console.log('[robot] socket@grip', socketW.x.toFixed(2),
        socketW.y.toFixed(2), socketW.z.toFixed(2));
    }

    // apple: rides the claw socket while gripped, else rests on pedestal
    if (apple) {
      if (grip > 0.55) {
        rig.socket.getWorldPosition(socketW);
        apple.group.position.lerp(socketW, 0.35);
      } else {
        apple.group.position.lerp(appleRest, 0.18);
      }
    }
  }

  // explicit pose driver for choreographed sequences (the divide heist):
  // phases are 0..1, yaw aims the whole arm
  function poseExplicit({ reach = 0, grip = 0, lift = 0, descend = 0,
                          yaw = 0, t = 0 }) {
    applyPose(reach, grip, lift, descend, yaw, t);
    rig.pod.rotation.y = Math.sin(t * 0.9) * 0.5;
    rig.pod.rotation.x = Math.sin(t * 0.6 + 1.0) * 0.25;
    rig.claw.getWorldPosition(clawTip);
    rig.obsHead.lookAt(clawTip);
  }

  return { scene, camera, update: pose, poseExplicit, rig,
           appleGroup: apple ? apple.group : null };
}
