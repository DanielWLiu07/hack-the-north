// hall.js — the GITRL factory: a gritty, dark industrial interior, in 3D, printed by
// the same MangaPass({ bw: 1, grit: 1 }) as the arms.
//
// TONE PLAN. The pass bands LINEAR luminance: white > 0.74, halftone 0.39-0.74,
// hatch 0.18-0.39, solid ink < 0.18. Its crosshatch texture is dark lines on white
// and its halftone is dots on black, so BOTH of those bands read LIGHT — darkness can
// only come from the ink band. So the hall is built in ink: almost every albedo is
// low enough that the rig's three directionals leave it under 0.18, and structure is
// read as silhouette against the few lighter things — grimy clerestory glass, hazard
// stripes, bulbs, the beacon, thin lit EDGE strips, and the lamp pools on the floor
// behind the belt. The back wall behind the title (x +-6.1, y -1..5.2 at z -10) is
// kept in ink so the light letters read against it.
//
// Publishes world.hall = { mounts: [{ pos, normal }] } (CCTV plates, see cctvMounts).
// Returns { group, update(world), mounts, lights, beltSpeed(t), beltOffset(t) }.
//
// Everything is deterministic from world.t (hash(), never Math.random) and every
// static part is merged per material by Rigid.

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { Rigid, Spring, hash, hashS, clamp01, smoother } from './mech.js';
import {
  FLOOR_Y, CEILING_Y, BACK_WALL_Z, BELT, GANTRY, LAMPS, CRATE, INTRO,
} from './layout.js';

const BG = '#060608';

// Quiet recessed machine bay. The old factory remains parked below: no lamps,
// trusses or fixtures are built by this entry point. Fine machined edges provide
// depth without putting a bright surface behind the lettering.
export function buildBackdrop(world) {
  const group = new THREE.Group();
  group.name = 'machine-bay';
  const shell = new Rigid(), edges = [];
  const dark = new THREE.MeshBasicMaterial({ color: '#17171b' });
  const edge = new THREE.MeshBasicMaterial({ color: '#85858a' });
  const box = (x, y, z) => new THREE.BoxGeometry(x, y, z);
  shell.add(box(32, 24, 0.25), dark, 0, 2, -15);
  // Deep side cabinets, ceiling return and floor plinth continue beyond the frame.
  for (const side of [-1, 1]) {
    shell.add(box(2.5, 24, 7), dark, side * 11.5, 2, -11.5);
    for (const z of [-14, -11, -8]) {
      edges.push(box(0.025, 12, 0.025).translate(side * 10.23, 2, z));
      // Short fastener slots break the edge into a believable cabinet detail.
      for (let y = -3; y <= 8; y += 2.2)
        edges.push(box(0.16, 0.022, 0.035).translate(side * 10.12, y, z));
    }
  }
  shell.add(box(25, 1, 7), dark, 0, 10, -11.5);
  shell.add(box(25, 0.5, 7), dark, 0, -5.3, -11.5);
  // Back-wall/floor junction and two short receding floor seams.
  edges.push(box(20.5, 0.018, 0.025).translate(0, -5.02, -14.8));
  for (const x of [-8.5, 8.5]) edges.push(box(0.018, 0.02, 6.8).translate(x, -5.02, -11.4));
  // White enamel vent slats and worn bay markings live on the outer cabinets.
  // Broken, broad accents read through the ink pass without lighting the centre.
  for (const side of [-1, 1]) {
    for (const y of [-2.3, 1.6, 5.7]) {
      edges.push(box(0.055, 1.25, 0.04).translate(side * 9.15, y, -14.65));
      for (let k = 0; k < 5; k++)
        edges.push(box(0.72 - k * 0.06, 0.075, 0.045).translate(side * 9.65, y + (k - 2) * 0.21, -14.6));
    }
    // Short painted corner returns reveal the recess, rather than crossing it.
    edges.push(box(1.45, 0.08, 0.035).translate(side * 8.2, 7.75, -14.65));
    edges.push(box(0.06, 0.85, 0.035).translate(side * 7.5, 7.36, -14.65));
    for (let k = 0; k < 5; k++) edges.push(box(0.24, 0.035, 0.44).rotateY(-side * 0.45)
      .translate(side * (7.4 + k * 0.44), -5.01, -13.8));
  }
  // A high row of narrow clerestory slots, well above the lettering.
  for (let k = -5; k <= 5; k++) {
    if (Math.abs(k) < 2) continue;
    edges.push(box(0.46, 0.14, 0.035).translate(k * 1.25, 9.1, -14.6));
  }
  shell.into(group);
  const accents = new THREE.Mesh(mergeGeometries(edges, false), edge);
  // Reveal by vertex displacement: no transparency sorting or extra draws.
  const reveal = { value: 0 };
  edge.onBeforeCompile = shader => {
    shader.uniforms.uReveal = reveal;
    shader.vertexShader = shader.vertexShader.replace('#include <common>',
      '#include <common>\nuniform float uReveal;')
      .replace('#include <begin_vertex>', `#include <begin_vertex>
        float lane = abs(position.x) > 6.0 ? 0.0 : 0.12;
        float a = smoothstep(lane, lane + 0.65, uReveal);
        transformed.x += sign(position.x) * (1.0 - a) * 16.0;
        transformed.y += sign(position.y - 2.0) * (1.0 - a) * 12.0;`);
  };
  accents.frustumCulled = false;
  group.add(accents);
  edges.forEach(g => g.dispose());
  world.scene.add(group);
  let last = '';
  return { group, update() {
    const target = world.away.on ? 0 : Math.min(1, world.t / 0.7);
    reveal.value += (target - reveal.value) * (1 - Math.exp(-12 * world.dt));
    const camera = world.camera, key = `${camera.aspect}|${camera.position.z}`;
    if (key === last) return;
    last = key;
    // Match the outer bay to the viewport; its rear wall remains a large quiet field.
    group.scale.x = (camera.position.z + 10) * 0.364 * camera.aspect / 10.8;
  } };
}

const std = (color, roughness = 0.9, metalness = 0.05, extra = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness, metalness, ...extra });

const M = {
  wall: std('#2e2d34'),
  wallB: std('#3a3941'),          // alternate corrugated panel, a shade up
  block: std('#27262c'),
  steel: std('#6f6d78', 0.6, 0.25),
  // the only way to draw structure without big white masses: thin strips of a
  // material bright enough to clear the pass's 0.176 threshold, used as edges
  edge: std('#cbc9d2', 0.5, 0.15),
  steelDark: std('#33323a', 0.7, 0.3),
  floor: std('#3e3d45', 0.95, 0),
  grate: std('#3f3e47', 0.7, 0.3),
  slat: std('#ffffff', 0.7, 0.3),          // tinted per instance (see slats())
  hazard: std('#f2c230', 0.6, 0.05),
  ink: std('#121216', 0.8, 0.05),
  rubber: std('#191820', 0.9, 0),
  door: std('#44434c', 0.7, 0.2),
  pipe: std('#5e5c68', 0.5, 0.35),
  orange: std('#f2a03c', 0.6, 0.1),
  curtain: std('#7e808a', 0.8, 0.05),
  // unlit: daylight through filthy glass. Three tones so the band reads as panes,
  // not as one slab. Values chosen for AFTER fog at the back wall (~0.32 to black).
  // only the panes ABOVE the title are daylit; the ones over the corners, where the
  // pomme arms hang, are boarded so the white arms keep a black background
  paneA: new THREE.MeshBasicMaterial({ color: '#b6b8b9', fog: true }),
  paneB: new THREE.MeshBasicMaterial({ color: '#8d8f91', fog: true }),
  paneC: new THREE.MeshBasicMaterial({ color: '#232327', fog: true }),
  vent: new THREE.MeshBasicMaterial({ color: '#9a9ea0', fog: true }),
  dial: new THREE.MeshBasicMaterial({ color: '#c9cbc8', fog: true }),
  steam: new THREE.MeshBasicMaterial({ color: '#ffffff', fog: true }),
};

const Y = new THREE.Vector3(0, 1, 0);
const V = (x, y, z) => new THREE.Vector3(x, y, z);

const box = (r, mat, w, h, d, x, y, z) => r.add(new THREE.BoxGeometry(w, h, d), mat, x, y, z);
const cylY = (r, mat, rt, rb, h, seg, x, y, z) =>
  r.add(new THREE.CylinderGeometry(rt, rb, h, seg), mat, x, y, z);
const cylZ = (r, mat, rt, rb, h, seg, x, y, z) =>
  r.add(new THREE.CylinderGeometry(rt, rb, h, seg).rotateX(Math.PI / 2), mat, x, y, z);
const cylX = (r, mat, rt, rb, h, seg, x, y, z) =>
  r.add(new THREE.CylinderGeometry(rt, rb, h, seg).rotateZ(Math.PI / 2), mat, x, y, z);

// a member from world point a to world point b (thin box section)
const _d = new THREE.Vector3(), _q = new THREE.Quaternion();
function beam(r, mat, a, b, t = 0.09, t2 = null) {
  _d.subVectors(b, a);
  const len = _d.length();
  const g = new THREE.BoxGeometry(t, len, t2 ?? t);
  g.applyQuaternion(_q.setFromUnitVectors(Y, _d.divideScalar(len)));
  r.add(g, mat, (a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2);
}

// alternating hazard / ink blocks along x (the single most legible thing in B&W)
function stripesX(r, x0, x1, y, z, h, d, block = 0.34) {
  const n = Math.max(2, Math.round((x1 - x0) / block));
  for (let i = 0; i < n; i++) {
    const w = (x1 - x0) / n;
    box(r, i % 2 ? M.ink : M.hazard, w * 0.98, h, d, x0 + w * (i + 0.5), y, z);
  }
}
function stripesY(r, y0, y1, x, z, w, d, block = 0.3) {
  const n = Math.max(2, Math.round((y1 - y0) / block));
  for (let i = 0; i < n; i++) {
    const h = (y1 - y0) / n;
    box(r, i % 2 ? M.ink : M.hazard, w, h * 0.98, d, x, y0 + h * (i + 0.5), z);
  }
}

// ---------------------------------------------------------------------------------

export function buildHall(world) {
  const { scene } = world;
  scene.background = new THREE.Color(BG);
  scene.fog = new THREE.Fog(BG, 13, 30);

  const root = new THREE.Group();
  root.name = 'hall';
  scene.add(root);

  const r = new Rigid();
  shell(r);
  backWall(r);
  structure(r);
  gantry(r);
  conveyorStatic(r);
  props(r);
  const mounts = cctvMounts(r);
  r.into(root);
  world.hall = { mounts };

  const anim = {};
  // POWER: every hall material bright enough to print white is scaled by the lamps'
  // combined level, so the hall starts black and comes up strike by strike. The pass
  // is near-binary (white above lum 0.176), so the reveal is staged for free: the lit
  // edges clear the threshold after the first lamp, the hazard stripes after the
  // second, the grimy glass only once all three have caught.
  anim.dimmable = [M.edge, M.hazard, M.orange, M.paneA, M.paneB, M.vent, M.dial, M.curtain, M.slat]
    .map((mat) => ({ mat, base: mat.color.clone() }));
  anim.power = -1;
  fan(root, anim);
  beacon(root, anim);
  lamps(root, anim);
  slats(root, anim);
  curtains(root, anim);
  steam(root, anim);
  gauge(root, anim);

  // ---- POWER ON (INTRO.powerOn) ------------------------------------------------
  // Lamps strike one by one left to right: two stutters, then the tube catches with
  // a small overshoot. Level is a pure function of t, so it reproduces exactly.
  const strikeAt = (i) => INTRO.powerOn[0] + 0.06 + i * 0.22;
  function strike(i, t) {
    const u = t - strikeAt(i);
    if (u < 0) return 0;
    if (u < 0.05) return 0.55;                 // first stutter
    if (u < 0.12) return 0.04;
    if (u < 0.19) return 0.85;                 // second
    if (u < 0.24) return 0.06;
    const c = clamp01((u - 0.24) / 0.42);      // catches, overshoots, settles at 1
    return smoother(0, 0.3, c) * (1 + 0.32 * Math.sin(Math.PI * c) * (1 - c));
  }
  // ambient sputter AFTER power on: bursts on one lamp, deterministic schedule
  const SPUTTER = 2;                            // which lamp misbehaves
  function sputter(t) {
    let start = INTRO.work + 3;
    for (let k = 0; k < 64; k++) {
      const dur = 0.5 + 0.8 * hash(k, 11);
      if (t < start) return 1;
      if (t < start + dur) {
        const step = Math.floor((t - start) * 22);
        const on = hash(step, k + 3) > 0.42;
        return on ? 0.85 + 0.35 * hash(step, 7) : 0.05;
      }
      if (t < start + dur + 0.35) {             // catches again
        const c = clamp01((t - start - dur) / 0.35);
        return 1 + 0.35 * Math.sin(Math.PI * c) * (1 - c);
      }
      start += dur + 0.35 + 7 + 9 * hash(k, 12);
    }
    return 1;
  }

  // belt spins up from a standstill to BELT.speed by INTRO.work, then holds
  const T0 = INTRO.powerOn[1], T1 = INTRO.work;
  const beltSpeed = (t) => BELT.speed * Math.min(1, Math.max(0, (t - T0) / (T1 - T0))) ** 2;
  const beltOffset = (t) => {                   // exact integral of beltSpeed
    const u = clamp01((t - T0) / (T1 - T0));
    const ramp = BELT.speed * (T1 - T0) * u * u * u / 3;
    return t > T1 ? ramp + BELT.speed * (t - T1) : ramp;
  };

  const _p = new THREE.Vector3();

  function update(w) {
    const t = w.t, dt = w.dt;

    // lamps: power-on strike, then ambient sputter on one of them
    let power = 0;
    for (let i = 0; i < anim.lamps.length; i++) {
      const L = anim.lamps[i];
      const striking = t < strikeAt(i) + 0.7;
      const lv = Math.max(0, striking ? strike(i, t) : (i === SPUTTER ? sputter(t) : 1));
      power += clamp01(striking ? lv : 1) / anim.lamps.length;   // the sputter stays local
      L.light.intensity = L.base * lv;
      L.bulb.material.color.setScalar(clamp01(lv) * 0.95 + 0.05);
      L.reflector.material.color.setScalar(0.18 + 0.62 * clamp01(lv));
      // starburst: pops past its size on the catch, flinches with every stutter
      L.burst.scale.setScalar(lv < 0.1 ? 0 : 0.35 + 0.65 * lv);
      L.burst.rotation.z = 0.2 * Math.sin(t * 0.6 + i * 2.1);
    }
    if (power !== anim.power) {
      for (const d of anim.dimmable) d.mat.color.copy(d.base).multiplyScalar(power);
      anim.power = power;
    }

    // the beacon eases into its spin and keeps turning
    const bt = Math.max(0, t - 0.45);
    const tau = 0.4;
    anim.beacon.rot.rotation.y = 2.6 * (bt - tau * (1 - Math.exp(-bt / tau)));
    const lit = clamp01(bt / 0.5);
    anim.beacon.dome.material.emissiveIntensity = 1.6 * lit;
    anim.beacon.light.intensity = 16 * lit;
    anim.beacon.light.target.position.set(
      anim.beacon.pos.x + Math.cos(anim.beacon.rot.rotation.y) * 4,
      anim.beacon.pos.y + 2.4,
      anim.beacon.pos.z + Math.sin(anim.beacon.rot.rotation.y) * 4);
    anim.beacon.light.target.updateMatrixWorld();

    // extractor fan: slow, and it wobbles slightly (a tired bearing)
    anim.fan.rotation.z = -0.85 * t + 0.05 * Math.sin(t * 2.3);

    // belt slats
    const off = beltOffset(t);
    for (let i = 0; i < anim.slat.count; i++) {
      const x = BELT.x0 + ((i * anim.slat.gap + off) % anim.slat.span);
      anim.slat.dummy.position.set(x, BELT.topY - 0.013, BELT.z);
      anim.slat.dummy.updateMatrix();
      anim.slat.mesh.setMatrixAt(i, anim.slat.dummy.matrix);
    }
    anim.slat.mesh.instanceMatrix.needsUpdate = true;

    // strip curtains: crates shove them aside and they swing back
    const crates = w.crates?.list || [];
    for (const s of anim.curtain.strips) {
      let target = 0;
      for (const c of crates) {
        if (!c.object3D || c.state === 'gone') continue;
        c.object3D.getWorldPosition(_p);
        const h = CRATE / 2;
        if (Math.abs(_p.z - s.z) > h + 0.06) continue;
        if (Math.abs(_p.y - BELT.topY) > 0.9) continue;
        const d = _p.x + h - s.x;                       // how far past the curtain
        if (d > -0.05 && _p.x - h < s.x + 0.45) {
          target = Math.max(target, Math.min(0.85, Math.asin(clamp01(d / s.len))));
        }
      }
      // neighbours get dragged a little, and there is always a draught
      s.spring.step(target + 0.02 * Math.sin(t * 1.7 + s.phase * 6.3), dt,
        target > 0 ? 120 : 45, target > 0 ? 0.8 : 0.16);
      anim.curtain.dummy.position.set(s.x, s.yTop, s.z);
      anim.curtain.dummy.rotation.set(0, 0, s.spring.x);
      anim.curtain.dummy.updateMatrix();
      anim.curtain.mesh.setMatrixAt(s.i, anim.curtain.dummy.matrix);
    }
    anim.curtain.mesh.instanceMatrix.needsUpdate = true;

    // relief valve: a puff every few seconds, and the gauge needle drops when it goes
    let sinceBlow = 1e9;
    {
      let start = INTRO.work + 1.5;
      for (let k = 0; k < 64; k++) {
        if (t < start) break;
        if (t - start < 6) { sinceBlow = t - start; break; }
        start += 5.5 + 5 * hash(k, 21);
      }
    }
    const puffing = sinceBlow < 0.45;
    for (let i = 0; i < anim.steam.n; i++) {
      const p = anim.steam.parts[i];
      if (puffing && p.age >= p.life && hash(Math.floor(t * 60), i) > 0.55) {
        p.age = 0;
        p.life = 1.6 + 0.9 * hash(i, 31);
        p.p.copy(anim.steam.origin);
        p.v.set(0.35 + 0.5 * hash(i, 32), 0.7 + 0.5 * hash(i, 33), 0.1 * hashS(i, 34));
      }
      if (p.age < p.life) {
        p.age += dt;
        p.v.y += 0.22 * dt;
        p.v.multiplyScalar(1 - 0.45 * dt);
        p.p.addScaledVector(p.v, dt);
        const u = clamp01(p.age / p.life);
        anim.steam.dummy.position.copy(p.p);
        anim.steam.dummy.scale.setScalar(0.1 + 0.62 * u);
        anim.steam.dummy.updateMatrix();
        anim.steam.mesh.setMatrixAt(i, anim.steam.dummy.matrix);
        anim.steam.mesh.setColorAt(i, anim.steam.col.setScalar((1 - u) * (1 - u) * 0.85));
      } else {
        anim.steam.dummy.scale.setScalar(0);
        anim.steam.dummy.updateMatrix();
        anim.steam.mesh.setMatrixAt(i, anim.steam.dummy.matrix);
      }
    }
    anim.steam.mesh.instanceMatrix.needsUpdate = true;
    if (anim.steam.mesh.instanceColor) anim.steam.mesh.instanceColor.needsUpdate = true;

    // gauge: creeps up with line pressure, twitches, and dumps on a blow-off
    const creep = 0.55 + 0.25 * Math.sin(t * 0.21) + 0.08 * Math.sin(t * 3.1) * hash(Math.floor(t * 2), 41);
    const dump = sinceBlow < 1.2 ? -0.5 * Math.exp(-sinceBlow * 2.5) : 0;
    anim.gauge.spring.step(creep + dump, dt, 140, 0.35);
    anim.gauge.needle.rotation.z = -2.3 * anim.gauge.spring.x + 1.15;
  }

  return { group: root, update, mounts, lights: anim.lights, beltSpeed, beltOffset };
}

// ---- static geometry -------------------------------------------------------------

function shell(r) {
  const H = CEILING_Y - FLOOR_Y;
  box(r, M.floor, 46, 0.4, 30, 0, FLOOR_Y - 0.2, -2);          // floor slab
  box(r, M.ink, 46, 0.3, 30, 0, CEILING_Y + 0.15, -2);         // ceiling
  box(r, M.wall, 0.4, H + 2, 30, -19, FLOOR_Y + H / 2, -2);    // side walls
  box(r, M.wall, 0.4, H + 2, 30, 19, FLOOR_Y + H / 2, -2);
}

function backWall(r) {
  const z = BACK_WALL_Z, H = CEILING_Y - FLOOR_Y;
  box(r, M.wall, 42, H, 0.3, 0, FLOOR_Y + H / 2, z - 0.15);
  box(r, M.block, 42, 2.3, 0.12, 0, FLOOR_Y + 1.15, z + 0.06);  // block course

  // corrugated panels between the block course and the window sill
  for (let i = 0; i < 34; i++) {
    const x = -21 + 1.25 * i + 0.625;
    const grubby = hash(i, 3);
    box(r, grubby > 0.55 ? M.wallB : M.wall, 1.18, 4.9, 0.07, x, 3.55, z + 0.08);
    box(r, M.steelDark, 0.07, 4.9, 0.13, x + 0.625, 3.55, z + 0.11);   // rib
  }

  // clerestory band: mullion grid + panes (a few dark, a few boarded)
  const y0 = 6.4, y1 = 8.4;
  box(r, M.steelDark, 42, 0.18, 0.3, 0, y0 - 0.09, z + 0.1);
  box(r, M.steelDark, 42, 0.18, 0.3, 0, y1 + 0.09, z + 0.1);
  for (let i = 0; i < 40; i++) {
    for (let j = 0; j < 2; j++) {
      const x = -20 + i * 1.0 + 0.5, y = y0 + 0.5 + j * 1.0;
      const h = hash(i * 2 + j, 5);
      const daylit = Math.abs(x) < 4.7;          // dark glass behind the arms and the gantry cameras
      box(r, !daylit || h > 0.86 ? M.paneC : h > 0.62 ? M.paneB : M.paneA,
        0.9, 0.9, 0.05, x, y, z + 0.12);
    }
    box(r, M.steelDark, 0.1, 2.0, 0.14, -20 + i * 1.0, y0 + 1.0, z + 0.15);
  }
  box(r, M.steelDark, 42, 0.1, 0.14, 0, y0 + 1.0, z + 0.15);

  // bay door, right of the title's shadow, with a hazard-striped frame
  const dx = 8.9, dw = 4.2, dh = 5.0;
  box(r, M.ink, dw + 0.5, dh + 0.3, 0.1, dx, FLOOR_Y + dh / 2, z + 0.1);
  for (let i = 0; i < 18; i++) {                                   // roller slats
    box(r, i % 2 ? M.door : M.steelDark, dw, 0.24, 0.12, dx, FLOOR_Y + 0.2 + i * 0.27, z + 0.16);
  }
  stripesY(r, FLOOR_Y, FLOOR_Y + dh, dx - dw / 2 - 0.22, z + 0.2, 0.3, 0.14);
  stripesY(r, FLOOR_Y, FLOOR_Y + dh, dx + dw / 2 + 0.22, z + 0.2, 0.3, 0.14);
  stripesX(r, dx - dw / 2 - 0.36, dx + dw / 2 + 0.36, FLOOR_Y + dh + 0.2, z + 0.2, 0.3, 0.14);
  for (const s of [-1, 1]) {                                        // bollards
    cylY(r, M.hazard, 0.11, 0.11, 1.0, 10, dx + s * (dw / 2 + 0.75), FLOOR_Y + 0.5, z + 0.9);
    box(r, M.ink, 0.23, 0.16, 0.23, dx + s * (dw / 2 + 0.75), FLOOR_Y + 0.62, z + 0.9);
  }

  // extractor fan housing (blades are animated separately)
  const fx = -9.4, fy = 4.4;
  box(r, M.steelDark, 2.9, 2.9, 0.22, fx, fy, z + 0.16);
  r.add(new THREE.CircleGeometry(1.2, 28), M.vent, fx, fy, z + 0.26);
  r.add(new THREE.TorusGeometry(1.24, 0.07, 6, 28), M.steelDark, fx, fy, z + 0.3);
  for (let i = 0; i < 8; i++) {                                     // guard bars
    const a = (i / 8) * Math.PI;
    const g = new THREE.BoxGeometry(0.05, 2.45, 0.05);
    g.applyQuaternion(_q.setFromAxisAngle(new THREE.Vector3(0, 0, 1), a));
    r.add(g, M.steelDark, fx, fy, z + 0.33);
  }

  // pipe rack under the windows, with flanges and drops
  for (const [py, pr] of [[5.72, 0.17], [6.06, 0.12]]) {
    cylX(r, M.pipe, pr, pr, 42, 12, 0, py, z + 0.55);
    for (let i = 0; i < 20; i++) cylX(r, M.steelDark, pr * 1.3, pr * 1.3, 0.1, 12, -20 + i * 2.1, py, z + 0.55);
  }
  for (const s of [-1, 1]) {
    cylY(r, M.pipe, 0.15, 0.15, 6.9, 12, s * 7.4, 2.3, z + 0.55);
    r.add(new THREE.TorusGeometry(0.28, 0.14, 8, 12, Math.PI / 2)
      .rotateY(s > 0 ? 0 : Math.PI), M.pipe, s * 7.4 - s * 0.28, 5.72, z + 0.55);
    cylY(r, M.steelDark, 0.2, 0.2, 0.12, 12, s * 7.4, 1.2, z + 0.55);
  }
}

function structure(r) {
  // I-beam columns with X-bracing, and trusses under the ceiling
  const cz = BACK_WALL_Z + 1.2;
  for (const cx of [-13.6, -6.8, 6.8, 13.6]) {
    const top = 7.1;
    box(r, M.steel, 0.52, top - FLOOR_Y, 0.07, cx, (top + FLOOR_Y) / 2, cz + 0.25);
    box(r, M.steelDark, 0.52, top - FLOOR_Y, 0.07, cx, (top + FLOOR_Y) / 2, cz - 0.25);
    box(r, M.steelDark, 0.08, top - FLOOR_Y, 0.5, cx, (top + FLOOR_Y) / 2, cz);
    box(r, M.steelDark, 0.9, 0.12, 0.9, cx, FLOOR_Y + 0.06, cz);          // base plate
    for (const s2 of [-1, 1])                                              // catch-light edges
      box(r, M.edge, 0.05, top - FLOOR_Y, 0.04, cx + s2 * 0.235, (top + FLOOR_Y) / 2, cz + 0.29);
  }
  for (const s of [-1, 1]) {                                               // X-bracing
    for (const [ya, yb] of [[FLOOR_Y + 0.2, 3.0], [3.0, 6.6]]) {
      beam(r, M.steelDark, V(s * 6.8, ya, cz), V(s * 13.6, yb, cz), 0.09);
      beam(r, M.steelDark, V(s * 6.8, yb, cz), V(s * 13.6, ya, cz), 0.09);
    }
  }
  // Warren trusses running back-to-front (strong perspective at the top of frame)
  for (const tx of [-9.2, -3.1, 3.1, 9.2]) {
    const zb = BACK_WALL_Z + 0.4, zf = 2.0, yb = 7.15, yt = 8.5;
    box(r, M.steelDark, 0.12, 0.12, zf - zb, tx, yb, (zb + zf) / 2);
    box(r, M.steelDark, 0.12, 0.12, zf - zb, tx, yt, (zb + zf) / 2);
    const n = Math.round((zf - zb) / 1.15);
    for (let i = 0; i < n; i++) {
      const z0 = zb + (zf - zb) * (i / n), z1 = zb + (zf - zb) * ((i + 1) / n);
      beam(r, M.steelDark, V(tx, yb, z0), V(tx, yt, z1), 0.07);
      beam(r, M.steelDark, V(tx, yt, z0), V(tx, yb, z1), 0.07);
    }
  }
  // cross trusses tying them together, at the back where they are visible
  for (const tz of [-8.6, -6.0]) {
    box(r, M.steelDark, 26, 0.12, 0.12, 0, 7.15, tz);
    box(r, M.steelDark, 26, 0.12, 0.12, 0, 8.5, tz);
    for (let i = 0; i < 22; i++) {
      const x0 = -13 + i * 1.18, x1 = x0 + 1.18;
      beam(r, M.steelDark, V(x0, 7.15, tz), V(x1, 8.5, tz), 0.06);
    }
  }
  // side catwalks at mid height: only their edges catch light, so they read as
  // lines receding into the dark rather than as lit surfaces
  for (const s2 of [-1, 1]) {
    const x0 = s2 * 6.4, x1 = s2 * 13.4, cy = 3.2, cz2 = -7.5;
    box(r, M.steelDark, Math.abs(x1 - x0), 0.1, 1.5, (x0 + x1) / 2, cy, cz2);
    box(r, M.edge, Math.abs(x1 - x0), 0.05, 0.06, (x0 + x1) / 2, cy + 0.04, cz2 + 0.75);
    box(r, M.edge, Math.abs(x1 - x0), 0.04, 0.05, (x0 + x1) / 2, cy + 1.0, cz2 + 0.75);   // handrail
    for (let i = 0; i < 6; i++) {
      const px = x0 + (x1 - x0) * (i / 5);
      box(r, M.steelDark, 0.06, 1.0, 0.06, px, cy + 0.5, cz2 + 0.75);
      beam(r, M.steelDark, V(px, cy, cz2 - 0.6), V(px + s2 * 0.9, cy - 1.4, cz2 - 0.6), 0.07);
    }
  }

  // a big round duct receding along the left
  cylZ(r, M.pipe, 0.46, 0.46, 12, 14, -10.8, 6.9, -4);
  for (let i = 0; i < 7; i++) cylZ(r, M.steelDark, 0.52, 0.52, 0.1, 14, -10.8, 6.9, -9.6 + i * 1.7);

  // floor: grating bays behind the belt, walkway lines, chevrons at the front
  for (let bx = 0; bx < 16; bx++) {
    for (let bz = 0; bz < 3; bz++) {
      const x0 = -12 + bx * 1.5, z0 = -9.4 + bz * 1.5;
      box(r, M.steelDark, 1.44, 0.06, 1.44, x0 + 0.72, FLOOR_Y + 0.03, z0 + 0.72);
      for (let i = 0; i < 7; i++) {
        box(r, M.grate, 1.4, 0.05, 0.06, x0 + 0.72, FLOOR_Y + 0.07, z0 + 0.18 + i * 0.2);
      }
    }
  }
  box(r, M.hazard, 26, 0.02, 0.07, 0, FLOOR_Y + 0.02, -5.9);   // one walkway line only
}

function gantry(r) {
  const { y, z, x0, x1 } = GANTRY;
  box(r, M.steelDark, x1 - x0, 0.07, 0.36, 0, y + 0.03, z);      // bottom flange (the rail)
  box(r, M.edge, x1 - x0, 0.03, 0.08, 0, y + 0.01, z + 0.15);    // its lit edge
  box(r, M.steelDark, x1 - x0, 0.33, 0.09, 0, y + 0.23, z);       // web
  box(r, M.steelDark, x1 - x0, 0.07, 0.36, 0, y + 0.43, z);       // top flange
  stripesX(r, x0, x1, y + 0.23, z + 0.06, 0.3, 0.03, 0.42);       // hazard on the web
  // festoon cable looping between trolleys behind the beam
  for (let i = 0; i < 20; i++) {
    const g = new THREE.TorusGeometry(0.62, 0.028, 4, 12, Math.PI);
    g.rotateZ(Math.PI);
    g.scale(1, 0.55, 1);
    r.add(g, M.ink, x0 + 0.8 + i * 1.5, y + 0.02, z - 0.28);
    box(r, M.steelDark, 0.12, 0.16, 0.12, x0 + 0.2 + i * 1.5, y - 0.02, z - 0.28);
  }
}

function conveyorStatic(r) {
  const { z, topY, width, x0, x1 } = BELT;
  box(r, M.rubber, x1 - x0, 0.1, width, 0, topY - 0.06, z);        // belt body
  for (const s of [-1, 1]) {                                        // side rails
    box(r, M.steelDark, x1 - x0, 0.26, 0.07, 0, topY - 0.04, z + s * (width / 2 + 0.06));
    box(r, M.edge, x1 - x0, 0.035, 0.085, 0, topY + 0.09, z + s * (width / 2 + 0.06));
    box(r, M.steelDark, x1 - x0, 0.1, 0.12, 0, topY - 0.2, z + s * (width / 2 + 0.06));
  }
  for (const s2 of [-1, 1])                                        // guard stripe, outer thirds
    stripesX(r, s2 < 0 ? -6.1 : 4.1, s2 < 0 ? -4.1 : 6.1, topY - 0.16, z + width / 2 + 0.12, 0.13, 0.03, 0.4);
  for (let i = 0; i < 17; i++) {                                    // legs
    const x = -12 + i * 1.5;
    for (const s of [-1, 1]) {
      box(r, M.steelDark, 0.09, topY - FLOOR_Y - 0.12, 0.09, x, (topY + FLOOR_Y) / 2 - 0.06, z + s * 0.4);
      box(r, M.steelDark, 0.22, 0.05, 0.22, x, FLOOR_Y + 0.03, z + s * 0.4);
    }
    beam(r, M.steelDark, V(x, FLOOR_Y + 0.1, z - 0.4), V(x, topY - 0.2, z + 0.4), 0.05);
  }
  for (let i = 0; i < 32; i++) cylZ(r, M.steelDark, 0.06, 0.06, width, 8, -12 + i * 0.75, topY - 0.22, z);

  // drive motor and gearbox
  box(r, M.steelDark, 0.5, 0.42, 0.34, 5.35, topY - 0.45, z + 0.5);
  cylZ(r, M.orange, 0.17, 0.17, 0.4, 12, 5.35, topY - 0.45, z + 0.72);
  cylZ(r, M.steelDark, 0.26, 0.26, 0.12, 14, 5.05, topY - 0.2, z + 0.5);

  // tunnel hoods: crates come out of the dark and go back into it
  for (const s of [-1, 1]) {
    const xi = s * 6.1, xo = s * 12.6;
    const cx = (xi + xo) / 2, len = Math.abs(xo - xi);
    box(r, M.steelDark, len, 0.12, 1.9, cx, 1.02, z);              // roof
    box(r, M.wall, len, 2.4, 0.1, cx, -0.2, z + 0.9);              // front wall
    box(r, M.wall, len, 2.4, 0.1, cx, -0.2, z - 0.9);              // back wall
    box(r, M.ink, 0.12, 2.3, 1.85, xo, -0.2, z);                   // far end cap
    stripesY(r, topY - 0.2, 1.0, xi - s * 0.08, z + 0.92, 0.16, 0.04, 0.26);
    stripesX(r, Math.min(xi, xi - s * 0.9), Math.max(xi, xi - s * 0.9), 1.02, z + 0.92, 0.16, 0.04, 0.3);
    box(r, M.steelDark, 0.16, 0.3, 1.9, xi, 1.05, z);              // curtain rail
    cylY(r, M.orange, 0.09, 0.09, 0.12, 10, xi - s * 0.5, 1.2, z + 0.5);   // hood lamp
  }
}

function props(r) {
  // pallet stacks and barrels: silhouettes with something to say about the place
  for (const [px, pz, n] of [[-8.6, -7.6, 4], [10.4, -6.6, 3], [-11.5, -5.2, 2]]) {
    for (let i = 0; i < n; i++) {
      box(r, M.steelDark, 1.2, 0.09, 1.0, px, FLOOR_Y + 0.05 + i * 0.62, pz);
      box(r, M.wallB, 1.05, 0.5, 0.9, px, FLOOR_Y + 0.35 + i * 0.62, pz);
      box(r, M.ink, 1.07, 0.06, 0.92, px, FLOOR_Y + 0.55 + i * 0.62, pz);
    }
  }
  for (const [bx, bz] of [[7.9, -7.4], [8.5, -7.9], [-5.2, -8.8]]) {
    cylY(r, M.steelDark, 0.34, 0.34, 0.95, 14, bx, FLOOR_Y + 0.48, bz);
    for (const ry of [0.25, 0.7]) r.add(new THREE.TorusGeometry(0.35, 0.035, 5, 14).rotateX(Math.PI / 2),
      M.steelDark, bx, FLOOR_Y + ry, bz);
  }
  // riser pipe with the relief valve and gauge (the steam comes off this)
  cylY(r, M.pipe, 0.17, 0.17, 5.4, 12, -7.6, FLOOR_Y + 2.7, -7.0);
  cylY(r, M.steelDark, 0.24, 0.24, 0.16, 12, -7.6, 1.3, -7.0);
  r.add(new THREE.TorusGeometry(0.3, 0.05, 6, 14).rotateX(Math.PI / 2), M.orange, -7.6, 1.9, -7.0);
  cylZ(r, M.pipe, 0.08, 0.08, 0.5, 10, -7.6, 2.55, -6.75);          // valve spout
}

// Places a bracket-mounted CCTV camera can bolt on (watchers.js populates them): a dark
// plate with four orange bolts at each. `pos` is the centre of the plate's OUTER face,
// `normal` points away from the surface. All of them sit outside the dark zone kept
// behind the title, against ink-band surfaces so a light camera body reads.
function cctvMounts(r) {
  const cz = BACK_WALL_Z + 1.2, N = { z: V(0, 0, 1), up: V(0, 1, 0), down: V(0, -1, 0) };
  const T = 0.06;                                   // plate thickness
  const mounts = [
    { pos: V(-6.8, 3.3, cz + 0.285 + T), normal: N.z },                   // column L
    { pos: V(6.8, 4.6, cz + 0.285 + T), normal: N.z },                    // column R
    { pos: V(-4.7, GANTRY.y - 0.005 - T, GANTRY.z), normal: N.down },     // gantry underside
    { pos: V(-3.2, GANTRY.y - 0.005 - T, GANTRY.z), normal: N.down },
    { pos: V(3.3, GANTRY.y - 0.005 - T, GANTRY.z), normal: N.down },
    { pos: V(8.9, FLOOR_Y + 5.6, BACK_WALL_Z + 0.115 + T), normal: N.z }, // above the bay door
    { pos: V(-6.3, 1.08 + T, BELT.z + 0.5), normal: N.up },               // on the tunnel hoods
    { pos: V(6.3, 1.08 + T, BELT.z + 0.5), normal: N.up },
    { pos: V(-8.6, 2.98, -6.69 + T), normal: N.z },                       // catwalk drop plates
    { pos: V(8.8, 2.98, -6.69 + T), normal: N.z },
  ];
  const Z = V(0, 0, 1), q = new THREE.Quaternion();
  for (const m of mounts) {
    q.setFromUnitVectors(Z, m.normal);
    const place = (geo, mat, lx, ly, lz) => {
      geo.translate(lx, ly, lz);
      geo.applyQuaternion(q);
      r.add(geo, mat, m.pos.x, m.pos.y, m.pos.z);
    };
    place(new THREE.BoxGeometry(0.36, 0.36, T), M.steelDark, 0, 0, -T / 2);
    for (const bx of [-1, 1]) for (const by of [-1, 1]) {
      place(new THREE.CylinderGeometry(0.035, 0.035, 0.05, 8).rotateX(Math.PI / 2),
        M.orange, bx * 0.125, by * 0.125, 0.01);
    }
  }
  // the catwalk fascia is too shallow for a plate: hang a drop plate under its edge
  for (const x of [-8.6, 8.8]) box(r, M.steelDark, 0.5, 0.56, 0.06, x, 2.95, -6.72);
  return mounts;
}

// ---- animated parts ---------------------------------------------------------------

function fan(root, anim) {
  const g = new THREE.Group();
  g.position.set(-9.4, 4.4, BACK_WALL_Z + 0.3);
  root.add(g);
  const r = new Rigid();
  cylZ(r, M.steelDark, 0.2, 0.2, 0.3, 12, 0, 0, 0);
  for (let i = 0; i < 5; i++) {
    const b = new THREE.BoxGeometry(0.38, 2.1, 0.05);
    b.rotateX(0.38);
    b.translate(0, 0.95, 0);
    b.rotateZ((i / 5) * Math.PI * 2);
    r.add(b, M.steelDark, 0, 0, 0);
  }
  r.into(g);
  anim.fan = g;
}

function beacon(root, anim) {
  const pos = V(-1.6, GANTRY.y - 0.22, GANTRY.z + 0.05);
  const g = new THREE.Group();
  g.position.copy(pos);
  root.add(g);
  new Rigid()
    .add(new THREE.CylinderGeometry(0.13, 0.15, 0.1, 12), M.steelDark, 0, 0.06, 0)
    .into(g);
  const dome = new THREE.Mesh(
    new THREE.SphereGeometry(0.15, 14, 10, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2),
    new THREE.MeshStandardMaterial({ color: '#f2a03c', emissive: '#f2a03c', emissiveIntensity: 1.6,
      transparent: true, opacity: 0.85 }));
  g.add(dome);
  const rot = new THREE.Group();
  g.add(rot);
  new Rigid()
    .add(new THREE.BoxGeometry(0.1, 0.12, 0.2), new THREE.MeshBasicMaterial({ color: '#ffffff' }), 0, -0.07, 0.07)
    .into(rot);
  const light = new THREE.SpotLight('#ff9a3c', 0, 11, 0.42, 0.55, 1.3);
  light.position.copy(pos);
  root.add(light);
  root.add(light.target);
  // PERF: a SpotLight is evaluated per fragment by EVERY lit material in the scene (the
  // whole crowd of arms), to tint a patch of back wall. Invisible lights are skipped by
  // the renderer; the beacon's dome still turns.
  light.visible = false;
  anim.beacon = { dome, rot, light, pos };
  anim.lights = anim.lights || [];
  anim.lights.push(light);
}

function lamps(root, anim) {
  anim.lamps = [];
  anim.lights = anim.lights || [];
  for (let i = 0; i < LAMPS.length; i++) {
    const p = LAMPS[i];
    const g = new THREE.Group();
    g.position.copy(p);
    root.add(g);
    const r = new Rigid();
    // chain up to the ceiling
    const links = Math.floor((CEILING_Y - p.y - 0.2) / 0.11);
    for (let k = 0; k < links; k++) {
      const t = new THREE.TorusGeometry(0.045, 0.013, 4, 8);
      if (k % 2) t.rotateY(Math.PI / 2);
      t.rotateX(Math.PI / 2);
      r.add(t, M.steelDark, 0, 0.22 + k * 0.11, 0);
    }
    cylY(r, M.steelDark, 0.11, 0.56, 0.44, 20, 0, 0, 0);            // shade
    r.add(new THREE.TorusGeometry(0.56, 0.035, 6, 20).rotateX(Math.PI / 2), M.ink, 0, -0.22, 0);
    r.into(g);
    const reflector = new THREE.Mesh(new THREE.CircleGeometry(0.5, 20).rotateX(Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: '#8d8d8d' }));
    reflector.position.y = -0.16;
    g.add(reflector);
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.13, 12, 10),
      new THREE.MeshBasicMaterial({ color: '#ffffff' }));
    bulb.position.y = -0.24;
    g.add(bulb);
    const light = new THREE.SpotLight('#fff1dc', 0, 0, 0.44, 0.75, 2);
    light.position.copy(p).setY(p.y - 0.24);
    // no shadows here, so a pool that reached the belt would shine THROUGH it onto the
    // floor in front: aim far enough back that the whole pool lands behind the belt
    light.target.position.set(p.x, FLOOR_Y, p.z - 1.3);
    root.add(light);
    root.add(light.target);
    // manga "light on" glyph: eight thin rays round the bulb, facing the camera
    const burst = new THREE.Group();
    burst.position.y = -0.24;
    const rays = new Rigid(), white = bulb.material;
    for (let k = 0; k < 8; k++) {
      const len = k % 2 ? 0.2 : 0.38;
      const ray = new THREE.BoxGeometry(0.03, len, 0.01);
      ray.translate(0, 0.24 + len / 2, 0);
      ray.rotateZ((k / 8) * Math.PI * 2);
      rays.add(ray, white, 0, 0, 0.2);
    }
    rays.into(burst);
    burst.scale.setScalar(0);
    g.add(burst);
    // The hanging lamps read as stray "backstage lights" behind the title and their three
    // SpotLights cost every lit fragment on the page. They still exist as the POWER ON
    // clock (their strike levels stage the hall's reveal) but are neither drawn nor lit.
    g.visible = false;
    light.visible = false;
    anim.lamps.push({ light, bulb, reflector, burst, base: 560 });
    anim.lights.push(light);
  }
}

function slats(root, anim) {
  const gap = 0.24, span = BELT.x1 - BELT.x0;
  const count = Math.floor(span / gap);
  const mesh = new THREE.InstancedMesh(
    new THREE.BoxGeometry(0.075, 0.028, BELT.width * 0.96), M.slat, count);
  mesh.frustumCulled = false;
  // the belt sits outside the lamp pools, so dark slats on dark rubber would hide the
  // motion entirely: every 4th slat is a raised bright cleat, a white dash riding along
  const dark = new THREE.Color('#403f48'), cleat = new THREE.Color('#d2d0d8');
  for (let i = 0; i < count; i++) mesh.setColorAt(i, i % 4 === 0 ? cleat : dark);
  root.add(mesh);
  anim.slat = { mesh, count, gap, span, dummy: new THREE.Object3D() };
}

function curtains(root, anim) {
  const yTop = 1.0, len = yTop - BELT.topY - 0.02, nz = 7;
  const strips = [];
  const geo = new THREE.BoxGeometry(0.02, len, 0.17);
  geo.translate(0, -len / 2, 0);
  const mesh = new THREE.InstancedMesh(geo, M.curtain, nz * 2);
  mesh.frustumCulled = false;
  root.add(mesh);
  let i = 0;
  for (const s of [-1, 1]) {
    for (let k = 0; k < nz; k++) {
      strips.push({
        i: i++, x: s * 6.1, z: BELT.z - 0.6 + k * 0.2, yTop, len,
        phase: hash(k, s > 0 ? 1 : 2), spring: new Spring(0),
      });
    }
  }
  anim.curtain = { mesh, strips, dummy: new THREE.Object3D() };
}

function steam(root, anim) {
  const n = 26;
  const mesh = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(0.5, 1), M.steam, n);
  mesh.frustumCulled = false;
  root.add(mesh);
  mesh.setColorAt(0, new THREE.Color(0, 0, 0));
  root.add(mesh);
  anim.steam = {
    mesh, n, dummy: new THREE.Object3D(), col: new THREE.Color(),
    origin: V(-7.6, 2.62, -6.55),
    parts: Array.from({ length: n }, () => ({ age: 9, life: 1, p: V(0, 0, 0), v: V(0, 0, 0) })),
  };
}

function gauge(root, anim) {
  const g = new THREE.Group();
  g.position.set(-7.6, 1.65, -6.72);
  root.add(g);
  new Rigid()
    .add(new THREE.CylinderGeometry(0.3, 0.3, 0.07, 16).rotateX(Math.PI / 2), M.steelDark, 0, 0, 0)
    .add(new THREE.CircleGeometry(0.26, 18), M.dial, 0, 0, 0.05)
    .into(g);
  const needle = new THREE.Mesh(new THREE.BoxGeometry(0.03, 0.22, 0.01),
    new THREE.MeshBasicMaterial({ color: '#111114' }));
  needle.geometry.translate(0, 0.09, 0);
  needle.position.z = 0.07;
  g.add(needle);
  anim.gauge = { needle, spring: new Spring(0.5) };
}
