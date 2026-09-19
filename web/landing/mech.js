// mech.js — the shared machine vocabulary of the GITRL factory.
//
// Every new machine speaks snakeArms.js's language — knuckle pins, clevis forks,
// alternating box / cylinder links, pistons, cable runs, bolt heads, service hoses,
// warning collars — in snakeArms.js's EXACT materials, so under the manga pass
// nothing reads as a different object set. snakeArms.js itself is never edited
// (it is pomme's, copied verbatim); this is the same idiom, extracted for reuse.

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

// Let the loading gears paint between bounded construction/upload batches.
export const yieldBuild = () => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 0)));

// ---- materials: identical values to snakeArms.js MAT ---------------------------
// The look IS these five materials plus MangaPass({ bw: 1, grit: 1 }). Share the
// instances; clone only when one machine needs its own animated emissive.
export const MAT = {
  cable: new THREE.MeshBasicMaterial({ color: '#b5bdc7' }), // silver wire that survives the ink pass
  body: new THREE.MeshStandardMaterial({ color: '#d7d9de', roughness: 0.55, metalness: 0.15 }),
  dark: new THREE.MeshStandardMaterial({ color: '#75727e', roughness: 0.6, metalness: 0.2 }),
  joint: new THREE.MeshStandardMaterial({ color: '#f2a03c', roughness: 0.6, metalness: 0.1 }),
  lens: new THREE.MeshStandardMaterial({ color: '#25405e', roughness: 0.25, metalness: 0.4 }),
  glow: new THREE.MeshStandardMaterial({ color: '#d8362a', emissive: '#d8362a', emissiveIntensity: 0.8 }),
  // snakeArms' emissive-lifted head material (HB): heads and tools that face away
  // from the lights would otherwise fall into the crosshatch bands
  lifted: new THREE.MeshStandardMaterial({
    color: '#e8eaee', roughness: 0.5, metalness: 0.1,
    emissive: '#6a6e74', emissiveIntensity: 0.85 }),
};

// ---- Rigid: one rigid body, one mesh per material ------------------------------
// All parts parented to the same joint never move relative to each other, so merge
// them. A 16-joint tentacle is ~48 draw calls instead of ~300.
//   new Rigid().add(geo, MAT.body, x, y, z).add(...).into(jointGroup)
export class Rigid {
  constructor() { this.parts = new Map(); }

  add(geo, mat, x = 0, y = 0, z = 0) {
    geo.translate(x, y, z);
    if (!this.parts.has(mat)) this.parts.set(mat, []);
    this.parts.get(mat).push(geo);
    return this;
  }

  into(parent) {
    for (const [mat, geos] of this.parts) {
      parent.add(new THREE.Mesh(mergeGeometries(geos, false), mat));
      for (const g of geos) g.dispose();
    }
    this.parts.clear();
    return parent;
  }
}

// One draw for mixed rigid surfaces, preserving each part's linear albedo,
// emissive lift, roughness and metalness (including unlit catchlights).
const packedMaterial = new THREE.MeshStandardMaterial({ vertexColors: true });
packedMaterial.onBeforeCompile = (s) => {
  s.vertexShader = s.vertexShader.replace('#include <common>', '#include <common>\nattribute vec3 partGlow; attribute vec2 partSurface; varying vec3 vPartGlow; varying vec2 vPartSurface;')
    .replace('#include <begin_vertex>', '#include <begin_vertex>\nvPartGlow = partGlow; vPartSurface = partSurface;');
  s.fragmentShader = s.fragmentShader.replace('#include <common>', '#include <common>\nvarying vec3 vPartGlow; varying vec2 vPartSurface;')
    .replace('#include <roughnessmap_fragment>', 'float roughnessFactor = vPartSurface.x;')
    .replace('#include <metalnessmap_fragment>', 'float metalnessFactor = vPartSurface.y;')
    .replace('#include <emissivemap_fragment>', 'totalEmissiveRadiance = vPartGlow;');
};
export class PackedRigid extends Rigid {
  into(parent) {
    const parts = [];
    for (const [mat, geos] of this.parts) for (const g of geos) {
      const n = g.attributes.position.count;
      const colors = new Float32Array(n * 3), glow = new Float32Array(n * 3), surface = new Float32Array(n * 2);
      const unlit = mat.isMeshBasicMaterial;
      const emission = unlit ? mat.color : mat.emissive?.clone().multiplyScalar(mat.emissiveIntensity);
      for (let i = 0; i < n; i++) {
        mat.color.toArray(colors, i * 3);
        if (unlit) colors.fill(0, i * 3, i * 3 + 3);
        if (emission) emission.toArray(glow, i * 3);
        surface[i * 2] = mat.roughness ?? 1; surface[i * 2 + 1] = mat.metalness ?? 0;
      }
      g.setAttribute('color', new THREE.BufferAttribute(colors, 3));
      g.setAttribute('partGlow', new THREE.BufferAttribute(glow, 3));
      g.setAttribute('partSurface', new THREE.BufferAttribute(surface, 2));
      parts.push(g);
    }
    parent.add(new THREE.Mesh(mergeGeometries(parts, false), packedMaterial));
    parts.forEach(g => g.dispose());
    this.parts.clear();
    return parent;
  }
}

// ---- deterministic variation ----------------------------------------------------
// Field note 43: index-modulo is not variation, and scene motion must reproduce, so
// never Math.random. hash(i, channel) -> [0, 1).
export function hash(i, c = 0) {
  let h = (Math.imul(i | 0, 0x27d4eb2d) ^ Math.imul((c | 0) + 0x165667b1, 0x9e3779b1)) >>> 0;
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b) >>> 0;
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35) >>> 0;
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}
export const hashS = (i, c = 0) => hash(i, c) * 2 - 1;   // [-1, 1)

// ---- easing ---------------------------------------------------------------------
export const clamp01 = (x) => Math.min(1, Math.max(0, x));
export const lerp = (a, b, u) => a + (b - a) * u;
// smoothstep / smootherstep of x across [a, b]: zero velocity at both ends (note 51)
export const smooth = (a, b, x) => { const u = clamp01((x - a) / (b - a)); return u * u * (3 - 2 * u); };
export const smoother = (a, b, x) => {
  const u = clamp01((x - a) / (b - a));
  return u * u * u * (u * (u * 6 - 15) + 10);
};
// 0 -> 1 -> 0 bump over [a, b] (smooth at both joins)
export const bump = (a, b, x) => Math.sin(Math.PI * clamp01((x - a) / (b - a))) ** 2;

// ---- springs: momentum is where the personality comes from -----------------------
// Semi-implicit Euler in two substeps (snakeArms' poseChain does the same): stable at
// any real dt. k = stiffness, zeta = damping ratio (<1 overshoots and rings).
export class Spring {
  constructor(x = 0, k = 60, zeta = 0.6) { this.x = x; this.v = 0; this.k = k; this.zeta = zeta; }
  step(target, dt, k = this.k, zeta = this.zeta) {
    const c = 2 * zeta * Math.sqrt(k), h = Math.min(dt, 1 / 30) / 2;
    for (let s = 0; s < 2; s++) {
      this.v += (k * (target - this.x) - c * this.v) * h;
      this.x += this.v * h;
    }
    return this.x;
  }
  kick(dv) { this.v += dv; return this; }
  snap(x) { this.x = x; this.v = 0; return this; }
}

export class Spring3 {
  constructor(x = new THREE.Vector3(), k = 60, zeta = 0.6) {
    this.x = x.clone(); this.v = new THREE.Vector3(); this.k = k; this.zeta = zeta;
  }
  step(target, dt, k = this.k, zeta = this.zeta) {
    const c = 2 * zeta * Math.sqrt(k), h = Math.min(dt, 1 / 30) / 2;
    for (let s = 0; s < 2; s++) {
      this.v.x += (k * (target.x - this.x.x) - c * this.v.x) * h;
      this.v.y += (k * (target.y - this.x.y) - c * this.v.y) * h;
      this.v.z += (k * (target.z - this.x.z) - c * this.v.z) * h;
      this.x.addScaledVector(this.v, h);
    }
    return this.x;
  }
  kick(dv) { this.v.add(dv); return this; }
  snap(x) { this.x.copy(x); this.v.set(0, 0, 0); return this; }
}

// ---- world-space aim (snakeArms' head trick) --------------------------------------
// lookAt keeps world-up, so an aimed head never inherits roll from its chain; the
// slerp gives it a servo's lag instead of a UI element's snap. +z is "forward".
const _qa = new THREE.Quaternion(), _qb = new THREE.Quaternion();
export function aimAt(obj, target, rate = 1) {
  _qa.copy(obj.quaternion);
  obj.lookAt(target);
  _qb.copy(obj.quaternion);
  obj.quaternion.copy(_qa).slerp(_qb, rate);
}

// ---- a straight member between two WORLD points ------------------------------------
// For cables, chains, hoses. `mesh` must be a child of an identity-transformed parent
// (the scene) and built as a unit-height cylinder along +y centred at the origin.
const _up = new THREE.Vector3(0, 1, 0), _d = new THREE.Vector3();
export function stretchBetween(mesh, a, b) {
  _d.subVectors(b, a);
  const len = _d.length() || 1e-6;
  mesh.position.addVectors(a, b).multiplyScalar(0.5);
  mesh.quaternion.setFromUnitVectors(_up, _d.divideScalar(len));
  mesh.scale.set(1, len, 1);
}

// ---- sparks: shared by anything that welds, grinds or crashes ----------------------
// White-hot streaks (unlit white prints as paper under the pass, with ink outlines).
// One InstancedMesh, fixed pool, deterministic spray.
export function createSparks(scene, max = 220) {
  const mesh = new THREE.InstancedMesh(
    new THREE.BoxGeometry(0.02, 0.02, 0.11),
    new THREE.MeshBasicMaterial({ color: '#ffffff' }), max);
  mesh.frustumCulled = false;
  mesh.count = max;
  scene.add(mesh);
  const pool = Array.from({ length: max }, () => ({
    age: 1, life: 0, p: new THREE.Vector3(), v: new THREE.Vector3() }));
  const dummy = new THREE.Object3D(), _t = new THREE.Vector3(), _r = new THREE.Vector3();
  let head = 0, seq = 0;
  const hidden = new THREE.Matrix4().makeScale(0, 0, 0);
  for (let i = 0; i < max; i++) mesh.setMatrixAt(i, hidden);

  // pos: world point; dir: preferred direction (need not be unit); n: count
  function emit(pos, dir, n = 6, speed = 2.4, spread = 1.1) {
    for (let k = 0; k < n; k++) {
      const s = pool[head];
      head = (head + 1) % max;
      seq++;
      _r.set(hashS(seq, 1), hashS(seq, 2), hashS(seq, 3)).multiplyScalar(spread);
      s.v.copy(dir).normalize().add(_r).normalize()
        .multiplyScalar(speed * (0.45 + 0.9 * hash(seq, 4)));
      s.p.copy(pos);
      s.age = 0;
      s.life = 0.25 + 0.45 * hash(seq, 5);
    }
  }

  function update(dt) {
    for (let i = 0; i < max; i++) {
      const s = pool[i];
      if (s.age >= s.life) { mesh.setMatrixAt(i, hidden); continue; }
      s.age += dt;
      s.v.y -= 9.8 * dt;
      s.v.multiplyScalar(1 - 0.8 * dt);
      s.p.addScaledVector(s.v, dt);
      dummy.position.copy(s.p);
      dummy.lookAt(_t.copy(s.p).add(s.v));
      dummy.scale.setScalar(Math.max(0, 1 - s.age / s.life));
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    }
    mesh.instanceMatrix.needsUpdate = true;
  }

  return { mesh, emit, update };
}
