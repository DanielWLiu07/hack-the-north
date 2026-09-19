// heads.js — the camera heads. Every machine in the GITRL factory that looks at you
// wears one of these.
//
// Same language as pomme's pod in snakeArms.js: at cutout scale, what reads as
// "camera" in black and white is a BOLD profile in emissive-lifted light metal
// (MAT.lifted, so camera-facing surfaces stay in the paper band) with MAT.dark
// accents. The gritty manga pass is close to binary, so every head is designed as
// big light shapes against big dark ones, and the EYE is built to survive that:
// dark hood ring > light iris ring > dark pupil > white catchlight, with two light
// eyelid shutters (quarter-sphere shells, like an animatronic eye) that close over it.
// A light lid over a dark lens is unmistakable at any size; it is where the
// personality comes from.
//
// THE INTERFACE (every kind honours it; watchers.js and the other modules rely on it):
//
//   const head = makeHead(kind, seed, scale)      unknown kind -> 'pod'
//   head.group        Object3D. Origin = the neck ball joint (the mount point: parent
//                     this to an arm tip). The lens looks along local +z, up is +y.
//   head.radius       bounding radius in world units (after `scale`), for crowding
//   head.lens         Vector3, (first) lens centre in head.group's local space
//   head.center       Vector3, centre of the head's visual mass in local space (scaled):
//                     it hangs forward of the neck — and, for a periscope, well above it
//   head.setIris(v)   pupil size: 1 = dilated, ~0.35 = pinpoint. At v < 0.3 the lids
//                     close as well, so setIris(0) is a blink for callers that know
//                     nothing about lids.
//   head.setBlink(v)  0 = open .. 1 = shut, on top of the lids; leaves the pupil alone.
//                     All three EASE toward what you set: nothing on a face is instant.
//   head.setLids(u,l) upper / lower eyelid, 0 = open .. 1 = shut. Half-lids read as
//                     sleepy (upper) or suspicious (both).
//   head.setTally(v)  REC light, 0..1
//   head.flash()      fire the flash if this kind has one (no-op otherwise)
//   head.update(dt,t) the head's own life: flash decay, film reels turning
//
// `seed` varies proportions deterministically (mech.hash) so two heads of one kind
// are siblings, not clones.

import * as THREE from 'three';
import { MAT, PackedRigid as Rigid, hash, lerp, clamp01, smooth } from './mech.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

export const HEAD_KINDS = ['pod', 'stereo', 'eyeball', 'cctv', 'dome', 'webcam', 'paparazzi', 'film', 'periscope'];

const WHITE = new THREE.MeshBasicMaterial({ color: '#ffffff' });      // catchlights, bulbs, flash
const LID = MAT.lifted.clone();
LID.side = THREE.DoubleSide;                                          // a shell, seen from inside when open

// cylinder along +z: r0 = FRONT radius, r1 = back radius
const cyl = (r0, r1, h, seg = 12) => new THREE.CylinderGeometry(r0, r1, h, Math.min(seg, 12)).rotateX(Math.PI / 2);
const cylX = (r, h, seg = 10) => new THREE.CylinderGeometry(r, r, h, Math.min(seg, 10)).rotateZ(Math.PI / 2);
const box = (x, y, z) => new THREE.BoxGeometry(x, y, z);
const pupilGeo = cyl(0.68, 0.68, 0.03, 24);
const upperGeo = new THREE.SphereGeometry(1, 10, 5, 0, Math.PI, 0, Math.PI / 2);
const lowerGeo = new THREE.SphereGeometry(1, 10, 5, 0, Math.PI, Math.PI / 2, Math.PI / 2);
const tallyGeo = new THREE.SphereGeometry(1, 6, 4);
const tallyMaterial = new THREE.MeshBasicMaterial({ color: '#ffffff' });

// Shared moving surfaces draw once per geometry across the entire cast.
export function batchHeadEyes(heads, parent) {
  const batches = new Map();
  for (const head of heads) head.group.traverse(o => {
    if (!o.userData.eyeBatch) return;
    const key = o.geometry;
    if (!batches.has(key)) batches.set(key, { sources: [], material: o.userData.tally ? tallyMaterial : o.material });
    batches.get(key).sources.push({ mesh: o, head });
    o.visible = false;
  });
  for (const [geo, b] of batches) {
    b.mesh = new THREE.InstancedMesh(geo, b.material, b.sources.length);
    b.mesh.frustumCulled = false;
    b.mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    parent.add(b.mesh);
  }
  return () => {
    for (const b of batches.values()) {
      let count = 0;
      for (const { mesh, head } of b.sources) if (head.group.visible) {
        b.mesh.setMatrixAt(count, mesh.matrixWorld);
        if (mesh.userData.tally) b.mesh.setColorAt(count, mesh.material.color);
        count++;
      }
      b.mesh.count = count;
      b.mesh.instanceMatrix.needsUpdate = true;
      if (b.mesh.instanceColor) b.mesh.instanceColor.needsUpdate = true;
    }
  };
}

// ---- the eye ------------------------------------------------------------------------
// Static parts go into the head's Rigid (merged per material); the pupil and the two
// lids are the only moving meshes. (x, y, z) = centre of the lens FRONT plane.
function addEye(rig, group, r, x, y, z, lidR = r * 1.34) {
  rig.add(cyl(r * 1.28, r * 1.06, 0.2), MAT.dark, x, y, z - 0.1)                 // hood ring
    .add(cyl(r * 0.93, r * 0.93, 0.04, 24), MAT.lifted, x, y, z)                 // iris ring
    .add(cyl(r * 0.19, r * 0.19, 0.02, 10), WHITE, x + r * 0.27, y + r * 0.3, z + 0.055);   // catchlight
  // Focus grip, retaining screws and engraved index marks: readable machined optics.
  for (let k = 0; k < 12; k++) {
    const a = k * Math.PI / 6;
    rig.add(box(r * 0.13, r * 0.24, 0.10).rotateZ(-a), MAT.lifted,
      x + Math.sin(a) * r * 1.20, y + Math.cos(a) * r * 1.20, z - 0.15);
    if (k % 3 === 0) rig.add(cyl(r * 0.06, r * 0.06, 0.014, 6), MAT.lens,
      x + Math.sin(a) * r * 1.05, y + Math.cos(a) * r * 1.05, z + 0.025);
  }
  const pupil = new THREE.Mesh(pupilGeo, MAT.lens);
  pupil.userData.eyeBatch = true;
  pupil.position.set(x, y, z + 0.03);
  const lid = (upper) => {
    const m = new THREE.Mesh(
      upper ? upperGeo : lowerGeo, LID);
    m.scale.setScalar(lidR);
    m.userData.eyeBatch = true;
    m.position.set(x, y, z - 0.03);
    return m;
  };
  const up = lid(true), low = lid(false);
  group.add(pupil, up, low);
  return {
    center: new THREE.Vector3(x, y, z),
    iris(v) { const s = r * lerp(0.4, 1, v); pupil.scale.set(s, s, 1); },
    lids(u, l) { up.rotation.x = lerp(-1.46, 0, u); low.rotation.x = lerp(1.46, 0, l); },
  };
}

function addTally(group, x, y, z, r = 0.055) {
  const mat = MAT.glow.clone();
  const m = new THREE.Mesh(tallyGeo, mat);
  m.scale.setScalar(r);
  m.userData.eyeBatch = true;
  m.userData.tally = true;
  m.position.set(x, y, z);
  group.add(m);
  return mat;
}

// ---- the kinds ------------------------------------------------------------------------
// Each builder returns { eyes: [...], tally, radius, flash?, update? } and fills `group`.
// Bodies sit around z = 0.55: the head hangs FORWARD of the neck ball (pomme's mount).

function pod(g, seed) {                         // pomme's camcorder
  const long = lerp(0.85, 1.05, hash(seed, 1)), tall = lerp(0.46, 0.58, hash(seed, 2));
  const r = lerp(0.25, 0.31, hash(seed, 3)), Z = 0.55, front = Z + long / 2;
  const rig = new Rigid()
    .add(cyl(0.07, 0.09, 0.42, 10), MAT.dark, 0, 0, 0.2)                          // yoke post
    .add(box(0.66, tall, long), MAT.lifted, 0, 0, Z)
    .add(cyl(r * 0.95, r * 1.02, 0.42), MAT.lifted, 0, 0.02, front + 0.2)         // barrel
    .add(cyl(r * 1.1, r * 1.1, 0.08), MAT.dark, 0, 0.02, front + 0.08)            // focus ring
    .add(box(0.16, 0.11, long * 0.7), MAT.lifted, 0, tall / 2 + 0.16, Z)          // top handle
    .add(box(0.1, 0.15, 0.1), MAT.dark, 0, tall / 2 + 0.07, Z + 0.28)
    .add(box(0.1, 0.15, 0.1), MAT.dark, 0, tall / 2 + 0.07, Z - 0.26)
    .add(box(0.5, 0.16, long * 0.75), MAT.dark, 0, -tall / 2 - 0.04, Z - 0.06)
    .add(cyl(0.09, 0.1, 0.38, 12), MAT.lifted, 0.4, 0.2, Z - 0.1);                // viewfinder
  const eye = addEye(rig, g, r, 0, 0.02, front + 0.5);
  rig.into(g);
  return { eyes: [eye], tally: addTally(g, -0.3, tall / 2 + 0.06, front - 0.05), radius: 0.8 };
}

function stereo(g, seed) {                      // the project's own sensor: a twin-lens stereo bar
  const wide = lerp(1.25, 1.5, hash(seed, 1)), r = lerp(0.2, 0.24, hash(seed, 2)), Z = 0.5;
  const ex = wide / 2 - r * 1.5;
  const rig = new Rigid()
    .add(cyl(0.07, 0.09, 0.4, 10), MAT.dark, 0, 0, 0.2)
    .add(box(wide, 0.42, 0.42), MAT.lifted, 0, 0, Z)
    .add(box(wide + 0.08, 0.1, 0.46), MAT.dark, 0, -0.26, Z)                      // heatsink rail
    .add(box(0.1, 0.46, 0.46), MAT.joint, -wide / 2 - 0.05, 0, Z)                 // end caps
    .add(box(0.1, 0.46, 0.46), MAT.joint, wide / 2 + 0.05, 0, Z)
    .add(cyl(0.05, 0.05, 0.04, 10), MAT.dark, 0, 0.02, Z + 0.23)                  // IR projector
    .add(cyl(r * 1.1, r * 1.15, 0.16), MAT.lifted, -ex, 0, Z + 0.27)
    .add(cyl(r * 1.1, r * 1.15, 0.16), MAT.lifted, ex, 0, Z + 0.27);
  const eyes = [addEye(rig, g, r, -ex, 0, Z + 0.44), addEye(rig, g, r, ex, 0, Z + 0.44)];
  rig.into(g);
  return { eyes, tally: addTally(g, 0, 0.27, Z + 0.1, 0.045), radius: 0.85 };
}

function eyeball(g, seed) {                     // nearly all lens
  const R = lerp(0.46, 0.54, hash(seed, 1)), Z = 0.5;
  const rig = new Rigid()
    .add(cyl(0.08, 0.1, 0.3, 10), MAT.dark, 0, 0, 0.12)
    .add(new THREE.SphereGeometry(R, 12, 8), MAT.lifted, 0, 0, Z)
    .add(new THREE.TorusGeometry(R * 1.01, 0.035, 4, 16), MAT.dark, 0, 0, Z - R * 0.15)           // seam, behind the eye
    .add(new THREE.CylinderGeometry(0.02, 0.02, 0.34, 6), MAT.dark, 0, R + 0.15, Z - 0.1);          // antenna
  const eye = addEye(rig, g, R * 0.7, 0, 0, Z + R * 0.8, R * 1.1);
  rig.into(g);
  return { eyes: [eye], tally: addTally(g, 0, R + 0.34, Z - 0.1, 0.05), radius: 0.7 };
}

function cctv(g, seed) {                        // bullet security camera with a long sunshade
  const long = lerp(1.0, 1.25, hash(seed, 1)), r = 0.24, Z = 0.55, front = Z + long / 2;
  const rig = new Rigid()
    .add(cyl(0.07, 0.09, 0.4, 10), MAT.dark, 0, -0.1, 0.2)
    .add(box(0.2, 0.3, 0.2), MAT.dark, 0, -0.3, Z - 0.1)                          // bracket knuckle
    .add(cyl(0.3, 0.3, long, 20), MAT.lifted, 0, 0, Z)
    .add(cyl(0.31, 0.31, 0.1, 20), MAT.dark, 0, 0, Z - long / 2 + 0.05)           // back cap
    .add(box(0.74, 0.06, long + 0.5), MAT.lifted, 0, 0.34, Z + 0.22)              // visor
    .add(box(0.06, 0.2, long + 0.5), MAT.lifted, -0.37, 0.25, Z + 0.22)
    .add(box(0.06, 0.2, long + 0.5), MAT.lifted, 0.37, 0.25, Z + 0.22)
    .add(new THREE.TorusGeometry(0.2, 0.03, 4, 10, Math.PI * 1.2).rotateY(Math.PI / 2), MAT.dark,
      0, -0.22, Z - long / 2);                                                    // cable pigtail
  const eye = addEye(rig, g, r, 0, 0, front + 0.03, r * 1.22);
  rig.into(g);
  return { eyes: [eye], tally: addTally(g, 0.2, -0.2, front - 0.02, 0.04), radius: 0.8 };
}

function dome(g, seed) {                        // PTZ dome under a saucer
  const R = lerp(0.42, 0.5, hash(seed, 1)), Z = 0.45;
  const rig = new Rigid()
    .add(cyl(0.08, 0.1, 0.3, 10), MAT.dark, 0, 0.2, 0.12)
    .add(new THREE.CylinderGeometry(R * 1.35, R * 1.2, 0.16, 12), MAT.lifted, 0, R * 0.55, Z)
    .add(new THREE.CylinderGeometry(R * 0.7, R * 1.1, 0.2, 12), MAT.lifted, 0, R * 0.55 + 0.18, Z)
    .add(new THREE.SphereGeometry(R, 12, 8), MAT.dark, 0, 0, Z)
    .add(new THREE.TorusGeometry(R * 1.02, 0.03, 4, 16).rotateX(Math.PI / 2), MAT.joint, 0, R * 0.42, Z);
  const eye = addEye(rig, g, R * 0.55, 0, -R * 0.12, Z + R * 0.86, R * 0.82);
  rig.into(g);
  return { eyes: [eye], tally: addTally(g, R * 0.9, R * 0.62, Z + R * 0.6, 0.04), radius: 0.7 };
}

function webcam(g, seed) {                      // small, cute, with a ring light
  const wide = lerp(0.7, 0.9, hash(seed, 1)), r = 0.17, Z = 0.4;
  const rig = new Rigid()
    .add(cyl(0.05, 0.06, 0.34, 8), MAT.dark, 0, -0.08, 0.16)
    .add(cylX(0.22, wide, 18), MAT.lifted, 0, 0, Z)
    .add(new THREE.SphereGeometry(0.22, 10, 6), MAT.lifted, -wide / 2, 0, Z)
    .add(new THREE.SphereGeometry(0.22, 10, 6), MAT.lifted, wide / 2, 0, Z)
    .add(box(0.3, 0.08, 0.3), MAT.dark, 0, -0.26, Z - 0.05)                       // clip foot
    .add(new THREE.TorusGeometry(r * 1.55, 0.035, 4, 16), WHITE, 0, 0, Z + 0.2);  // ring light
  const eye = addEye(rig, g, r, 0, 0, Z + 0.24, r * 1.3);
  rig.into(g);
  return { eyes: [eye], tally: addTally(g, wide / 2 - 0.05, 0.1, Z + 0.2, 0.03), radius: 0.55 };
}

function paparazzi(g, seed) {                   // press camera with a flash gun
  const r = lerp(0.24, 0.29, hash(seed, 1)), Z = 0.5, side = hash(seed, 2) > 0.5 ? 1 : -1;
  const fx = side * 0.66, fy = 0.34, fz = Z + 0.18;
  const rig = new Rigid()
    .add(cyl(0.07, 0.09, 0.4, 10), MAT.dark, 0, 0, 0.2)
    .add(box(0.78, 0.54, 0.44), MAT.lifted, 0, 0, Z)
    .add(box(0.8, 0.12, 0.46), MAT.dark, 0, -0.1, Z)                              // leatherette band
    .add(box(0.24, 0.16, 0.3), MAT.lifted, -side * 0.2, 0.35, Z)                  // viewfinder
    .add(cyl(0.06, 0.06, 0.05, 10), MAT.joint, side * 0.28, 0.3, Z + 0.1)         // shutter button
    .add(cyl(r, r * 1.05, 0.34), MAT.lifted, 0, -0.02, Z + 0.38)
    .add(cylX(0.04, 0.5, 8), MAT.dark, side * 0.5, fy - 0.12, Z)                  // flash arm
    .add(cyl(0.3, 0.1, 0.18, 20), MAT.lifted, fx, fy, fz)                         // reflector dish
    .add(new THREE.TorusGeometry(0.3, 0.03, 4, 14), MAT.dark, fx, fy, fz + 0.09)  // rim
    .add(new THREE.TorusGeometry(0.19, 0.016, 4, 12), MAT.dark, fx, fy, fz + 0.1) // ribs
    .add(new THREE.TorusGeometry(0.12, 0.016, 4, 10), MAT.dark, fx, fy, fz + 0.1)
    .add(cyl(0.07, 0.07, 0.05, 12), WHITE, fx, fy, fz + 0.1);                     // bulb
  const eye = addEye(rig, g, r, 0, -0.02, Z + 0.58);
  rig.into(g);
  const burst = new THREE.Mesh(new THREE.SphereGeometry(0.34, 8, 6), WHITE);
  burst.position.set(fx, fy, fz + 0.15);
  burst.visible = false;
  g.add(burst);
  let lit = 0;
  return {
    eyes: [eye], tally: addTally(g, -side * 0.3, 0.2, Z + 0.23, 0.04), radius: 0.9,
    flash() { lit = 1; },
    update(dt) {
      lit *= Math.exp(-dt * 13);
      burst.visible = lit > 0.03;
      burst.scale.setScalar(0.4 + 2.4 * lit);
    },
  };
}

function film(g, seed) {                        // old film camera: two reels up top, a matte box
  const r = 0.24, Z = 0.55, spin = lerp(1.6, 2.6, hash(seed, 1));
  const rig = new Rigid()
    .add(cyl(0.07, 0.09, 0.4, 10), MAT.dark, 0, 0, 0.2)
    .add(box(0.56, 0.62, 0.9), MAT.lifted, 0, 0, Z)
    .add(box(0.62, 0.1, 0.94), MAT.dark, 0, -0.3, Z)
    .add(cyl(r, r * 1.05, 0.3), MAT.lifted, 0, 0, Z + 0.58)
    .add(box(0.74, 0.06, 0.3), MAT.dark, 0, 0.36, Z + 0.78)                       // matte box
    .add(box(0.74, 0.06, 0.3), MAT.dark, 0, -0.36, Z + 0.78)
    .add(box(0.06, 0.72, 0.3), MAT.dark, -0.36, 0, Z + 0.78)
    .add(box(0.06, 0.72, 0.3), MAT.dark, 0.36, 0, Z + 0.78)
    .add(cylX(0.03, 0.2, 8), MAT.dark, 0.38, -0.05, Z - 0.1)                      // crank
    .add(box(0.05, 0.22, 0.05), MAT.joint, 0.47, -0.14, Z - 0.1);
  const eye = addEye(rig, g, r, 0, 0, Z + 0.72, r * 1.2);
  rig.into(g);
  const reels = [Z + 0.2, Z - 0.34].map((z, k) => {
    const reel = new THREE.Group();
    reel.position.set(0, 0.62, z);
    new Rigid()
      .add(cylX(0.3 - k * 0.04, 0.12, 20), MAT.lifted)
      .add(cylX(0.08, 0.16, 10), MAT.dark)
      .add(cylX(0.06, 0.14, 8), MAT.dark, 0, 0.17, 0)
      .add(cylX(0.06, 0.14, 8), MAT.dark, 0, -0.085, 0.147)
      .add(cylX(0.06, 0.14, 8), MAT.dark, 0, -0.085, -0.147)
      .into(reel);
    g.add(reel);
    return reel;
  });
  return {
    eyes: [eye], tally: addTally(g, -0.2, 0.34, Z + 0.46, 0.04), radius: 0.95,
    update(dt) { reels[0].rotation.x -= dt * spin; reels[1].rotation.x -= dt * spin * 1.18; },
  };
}

function periscope(g, seed) {                   // peeks over things
  const tall = lerp(0.7, 1.0, hash(seed, 1)), r = 0.21;
  const rig = new Rigid()
    .add(new THREE.CylinderGeometry(0.17, 0.2, tall, 14), MAT.lifted, 0, tall / 2, 0.1)
    .add(new THREE.CylinderGeometry(0.22, 0.22, 0.08, 14), MAT.dark, 0, tall * 0.3, 0.1)
    .add(new THREE.CylinderGeometry(0.21, 0.21, 0.08, 14), MAT.joint, 0, tall * 0.62, 0.1)
    .add(box(0.46, 0.46, 0.5), MAT.lifted, 0, tall + 0.18, 0.18)                  // mirror box
    .add(box(0.5, 0.08, 0.54), MAT.dark, 0, tall + 0.44, 0.18)
    .add(cyl(r * 1.1, r * 1.2, 0.4), MAT.lifted, 0, tall + 0.16, 0.6);
  const eye = addEye(rig, g, r, 0, tall + 0.16, 0.83, r * 1.28);
  rig.into(g);
  return { eyes: [eye], tally: addTally(g, 0.18, tall + 0.5, 0.3, 0.04), radius: 0.75,
    center: new THREE.Vector3(0, tall * 0.75, 0.35) };
}

const BUILD = { pod, stereo, eyeball, cctv, dome, webcam, paparazzi, film, periscope };

function serviceDetails(group, kind, seed) {
  const rig = new Rigid();
  const panel = (side, x, y, z, height, length) => {
    rig.add(box(0.028, height, length), MAT.dark, side * x, y, z)
      .add(box(0.034, height * 0.72, length * 0.78), MAT.body, side * (x + 0.018), y, z);
    for (const sy of [-1, 1]) for (const sz of [-1, 1]) {
      rig.add(cylX(0.035, 0.046, 6), MAT.lens, side * (x + 0.045), y + sy * height * 0.38, z + sz * length * 0.4);
    }
    for (let k = 0; k < 5; k++) rig.add(box(0.04, height * 0.35, 0.025), MAT.lens,
      side * (x + 0.04), y, z + (k - 2) * length * 0.10);
    // Cast identification tab, with raised binary tooling marks.
    rig.add(box(0.046, 0.075, length * 0.38), MAT.lens, side * (x + 0.04), y - height * 0.30, z);
    for (let k = 0; k < 4; k++) rig.add(box(0.05, 0.037, 0.012 + hash(seed, 80 + k) * 0.016), MAT.lifted,
      side * (x + 0.045), y - height * 0.30, z + (k - 1.5) * 0.04);
  };
  if (['pod', 'paparazzi', 'film', 'stereo', 'periscope'].includes(kind)) {
    const x = kind === 'stereo' ? 0.79 : kind === 'paparazzi' ? 0.40 : kind === 'film' ? 0.29 : kind === 'periscope' ? 0.24 : 0.34;
    const y = kind === 'periscope' ? lerp(0.7, 1, hash(seed, 1)) + 0.18 : 0;
    const z = kind === 'periscope' ? 0.18 : 0.55;
    for (const side of [-1, 1]) panel(side, x, y, z, kind === 'stereo' ? 0.32 : 0.40, kind === 'pod' || kind === 'film' ? 0.66 : 0.32);
  } else {
    const R = kind === 'eyeball' ? lerp(0.46, 0.54, hash(seed, 1)) : kind === 'dome' ? 0.43 : kind === 'cctv' ? 0.30 : 0.23;
    for (let k = 0; k < 8; k++) {
      const a = k * Math.PI / 4;
      rig.add(box(0.11, 0.06, 0.30).rotateZ(-a), MAT.dark, Math.sin(a) * R, Math.cos(a) * R, 0.45);
      rig.add(cyl(0.026, 0.026, 0.02, 6), MAT.lifted, Math.sin(a) * R, Math.cos(a) * R, 0.61);
    }
  }
  // Rear connector and strain relief, common across the family.
  rig.add(cyl(0.105, 0.105, 0.09, 8), MAT.joint, 0, -0.13, 0.04)
    .add(cyl(0.065, 0.065, 0.14, 8), MAT.lens, 0, -0.13, -0.05);
  rig.into(group);
}

export function makeHead(kind = 'pod', seed = 0, scale = 1) {
  const group = new THREE.Group();
  const h = (BUILD[kind] || pod)(group, seed);
  serviceDetails(group, kind, seed);
  // Common socket, twin cheeks and pivot pin: every body uses the same wrist.
  new Rigid()
    .add(cyl(0.22, 0.25, 0.14, 12), MAT.dark, 0, 0, 0.04)
    .add(box(0.09, 0.32, 0.38), MAT.lifted, -0.22, 0, 0.2)
    .add(box(0.09, 0.32, 0.38), MAT.lifted, 0.22, 0, 0.2)
    .add(cylX(0.12, 0.56, 12), MAT.joint, 0, 0, 0.32)
    .into(group);
  // The mount and body are rigid in the same space; combine their packed meshes.
  const bodies = group.children.filter(o => o.isMesh && o.geometry.hasAttribute('partGlow'));
  if (bodies.length > 1) {
    const geo = mergeGeometries(bodies.map(o => o.geometry), false);
    bodies[0].geometry.dispose();
    bodies[0].geometry = geo;
    for (const o of bodies.slice(1)) { group.remove(o); o.geometry.dispose(); }
  }
  group.scale.setScalar(scale);
  group.traverse((o) => { o.frustumCulled = false; });
  // Callers set TARGETS; the eye eases toward them, so nothing about a face is ever
  // instant: lids travel in ~50 ms, the pupil dilates over ~150 ms. Integrate once
  // in update(dt), on the same deterministic, pausable clock as the arm.
  const goal = { iris: 1, lidU: 0, lidL: 0, blink: 0 }, cur = { iris: 1, lidU: 0, lidL: 0 };
  const ease = (dt) => {
    const shut = Math.max(goal.blink, 1 - smooth(0.02, 0.3, goal.iris));   // setIris(0) still blinks
    const kLid = 1 - Math.exp(-dt * 24), kIris = 1 - Math.exp(-dt * 8);
    cur.lidU += (Math.max(goal.lidU, shut) - cur.lidU) * kLid;
    cur.lidL += (Math.max(goal.lidL, shut * 0.9) - cur.lidL) * kLid;
    cur.iris += (Math.max(goal.iris, 0.3) - cur.iris) * kIris;
    for (const e of h.eyes) { e.iris(cur.iris); e.lids(cur.lidU, cur.lidL); }
  };
  ease(1 / 60);
  let tally = 0, tallyGoal = 0;
  return {
    kind: BUILD[kind] ? kind : 'pod', group, lens: h.eyes[0].center, radius: h.radius * scale,
    center: (h.center || new THREE.Vector3(0, 0, 0.75)).multiplyScalar(scale),
    setIris(v) { goal.iris = clamp01(v); },
    setLids(u, l = 0) { goal.lidU = clamp01(u); goal.lidL = clamp01(l); },
    setBlink(v) { goal.blink = clamp01(v); },
    setTally(v) { tallyGoal = v; },
    flash: h.flash || (() => {}),
    update(dt, t) { ease(dt); tally += (tallyGoal - tally) * (1 - Math.exp(-dt * 12));
      h.tally.emissiveIntensity = 0.15 + 1.6 * tally;
      h.tally.color.setRGB(0.08 + tally * 0.65, 0.025 + tally * 0.22, 0.015 + tally * 0.12);
      if (h.update) h.update(dt, t); },
  };
}
