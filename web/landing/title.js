// title.js — GITIRL, in Katie Roze, as hanging 3D metal.
//
// The letterforms are the font's own watercolour glyph art, traced into outlines by
// tools/build_gitrl.py (Katie Roze's glyf table holds only empty placeholders),
// extruded here and hung from the factory ceiling on two cables each. Every motion
// goes through springs: the drop-in on a bungee-catch cable, the domino knock between
// neighbours, pokes, idle breathing, the occasional gag. Every animated term starts
// at zero value and zero velocity (field note 51).
//
// buildTitle(world) publishes world.title = { count, poke, center, seam, normal, pick }
// (see layout.js) and returns { update(world), letters }.

import * as THREE from 'three';
import { MAT, Spring, hash, hashS, stretchBetween, smooth, clamp01, yieldBuild, Rigid } from './mech.js';
import { TITLE, INTRO, introTada } from './layout.js';

// geometry, font units
const DEPTH = 55;
const BEVEL_T = 8;
const BEVEL_S = 4;

// drop-in: each letter is released just above the frame already moving down (off
// screen, so the release is invisible), falls ballistically — speed rising all the
// way — until its cables go taut, and a stiff lossy "bungee" catches it: a CLANG,
// a stretch, a quick ring-down. The fall time is solved so the LAST letter lands
// exactly on INTRO.tada.
const DROP_H = 5.2;            // world units above rest at release (fully off-frame)
const G = 30;                  // fall acceleration (heavier than 9.8: reads as metal)
const K_CABLE = 600;           // cable stiffness once taut
const Z_CABLE = 0.4;           // cable damping ratio
const SLACK = G / K_CABLE;     // cable goes taut this far above rest -> rests at 0

export async function buildTitle(world, {
  url = './title/',
  tracking = 64,                                   // font units between letters
  stagger = [0.0, -0.3, 0.16, -0.14, 0.3, -0.08], // z per letter: overlaps LAYER, never touch
  heights = [0, -0.13, 0.10, -0.10, 0.03, 0],      // separate I/T crossbars without breaking the word
  start = INTRO.titleDrop,                         // first letter released (s)
  gap = INTRO.titleStagger,                        // release stagger (s)
  land = INTRO.tada,                               // last letter lands (s)
} = {}) {
  const meta = await (await fetch(url + 'gitirl.json')).json();
  const texLoader = new THREE.TextureLoader();
  const uniqueMaps = new Map();
  for (const L of meta.letters) if (!uniqueMaps.has(L.ch)) uniqueMaps.set(L.ch, texLoader.loadAsync(`${url}${L.ch}.webp`));
  const maps = await Promise.all(meta.letters.map(L => uniqueMaps.get(L.ch)));

  // ---- word layout in font units --------------------------------------------------
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  meta.letters.forEach((L, i) => {
    const pen = L.pen + tracking * i;
    for (const part of L.parts) for (const [x, y] of part.outer) {
      minX = Math.min(minX, x + pen); maxX = Math.max(maxX, x + pen);
      minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    }
  });
  const s = TITLE.width / (maxX - minX);             // font units -> world
  const wordCX = (minX + maxX) / 2, wordCY = (minY + maxY) / 2;

  const cableGeo = new THREE.CylinderGeometry(0.022, 0.022, 1, 5);
  const cableBatch = new THREE.InstancedMesh(cableGeo, MAT.cable, meta.letters.length * 2);
  cableBatch.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  cableBatch.frustumCulled = false;
  world.scene.add(cableBatch);
  const letters = [];

  for (const [i, L] of meta.letters.entries()) {
    await yieldBuild();
    const pen = L.pen + tracking * i;

    // ---- outlines -> shapes ------------------------------------------------------
    const shapes = L.parts.map((part) => {
      const shape = new THREE.Shape(part.outer.map(([x, y]) => new THREE.Vector2(x, y)));
      for (const h of part.holes) shape.holes.push(new THREE.Path(h.map(([x, y]) => new THREE.Vector2(x, y))));
      return shape;
    });
    // the welder walks the biggest stroke's outline
    const areaOf = (pts) => {
      let a = 0;
      for (let k = 0; k < pts.length; k++) {
        const [x0, y0] = pts[k], [x1, y1] = pts[(k + 1) % pts.length];
        a += x0 * y1 - x1 * y0;
      }
      return Math.abs(a) / 2;
    };
    const seamPts = L.parts.reduce((best, p) => (areaOf(p.outer) > areaOf(best) ? p.outer : best),
      L.parts[0].outer);

    // letter ink bounds (letter-local font units)
    let lx0 = Infinity, lx1 = -Infinity, ly0 = Infinity, ly1 = -Infinity;
    for (const part of L.parts) for (const [x, y] of part.outer) {
      lx0 = Math.min(lx0, x); lx1 = Math.max(lx1, x); ly0 = Math.min(ly0, y); ly1 = Math.max(ly1, y);
    }
    const midX = (lx0 + lx1) / 2;
    // cable attach: the highest outline point in each half, sunk into the stroke
    const top = (keep) => {
      let best = null;
      for (const part of L.parts) for (const p of part.outer) {
        if (keep(p[0]) && (!best || p[1] > best[1])) best = p;
      }
      return best;
    };
    const aL = top((x) => x < midX) || [lx0, ly1];
    const aR = top((x) => x >= midX) || [lx1, ly1];
    const attach = [new THREE.Vector3(aL[0], aL[1] - 20, 0), new THREE.Vector3(aR[0], aR[1] - 20, 0)];
    const pivot = attach[0].clone().add(attach[1]).multiplyScalar(0.5);

    // ---- mesh --------------------------------------------------------------------
    const geo = new THREE.ExtrudeGeometry(shapes, {
      depth: DEPTH, bevelEnabled: true, bevelThickness: BEVEL_T, bevelSize: BEVEL_S,
      bevelSegments: 2, curveSegments: 1,
    });
    geo.translate(-pivot.x, -pivot.y, -DEPTH / 2);   // origin = the hang point
    const map = maps[i];
    map.colorSpace = THREE.SRGBColorSpace;
    map.anisotropy = 4;
    // cap UVs are shape coordinates (font units): fit the art box onto them
    map.repeat.set(1 / L.box.w, 1 / L.box.h);
    map.offset.set(-L.box.x / L.box.w, -L.box.y / L.box.h);
    // light metal, lifted like snakeArms' pod (HB) so it prints paper-white; the
    // brush's ink density comes through as mottling -> halftone dots under the pass
    const face = new THREE.MeshStandardMaterial({
      color: '#eef0f3', map, roughness: 0.5, metalness: 0.1,
      emissive: '#6a6e74', emissiveIntensity: 0.85, emissiveMap: map });
    const mesh = new THREE.Mesh(geo, [face, MAT.dark]);
    mesh.scale.setScalar(s);
    mesh.userData.letter = i;

    // eyelets where the cables bite (orange, the arms' joint colour)
    const eyelets = new Rigid();
    for (const a of attach) eyelets.add(new THREE.TorusGeometry(15, 4.5, 5, 10), MAT.joint,
      a.x - pivot.x, a.y - pivot.y + 8, DEPTH / 2 + BEVEL_T - 2);
    eyelets.into(mesh);
    const cableEnd = attach.map((a) => new THREE.Vector3(a.x - pivot.x, a.y - pivot.y + 23, 0));

    const body = new THREE.Group();     // squash & stretch, about the hang point
    body.add(mesh);
    const hang = new THREE.Group();     // swing, twist, drop
    hang.add(body);
    const rest = new THREE.Vector3(
      TITLE.center.x + (pen + pivot.x - wordCX) * s,
      TITLE.center.y + (pivot.y - wordCY) * s + (heights[i] ?? 0),
      TITLE.center.z + (stagger[i] ?? 0));
    hang.position.copy(rest);
    hang.position.y += DROP_H;
    world.scene.add(hang);

    // cables up to the ceiling: anchors fixed at the rest attach points' x/z
    hang.updateMatrixWorld(true);
    const cables = cableEnd.map((c) => {
      const cable = new THREE.Object3D(); // transform only; one shared cable draw
      const w = mesh.localToWorld(c.clone());
      return { mesh: cable, local: c, anchor: new THREE.Vector3(w.x, TITLE.cableTopY, w.z),
               end: new THREE.Vector3() };
    });

    // seam walk: cumulative arc length along the stroke outline (hang-local units)
    const seam = seamPts.map(([x, y]) => new THREE.Vector2(x - pivot.x, y - pivot.y));
    const cum = [0];
    for (let k = 1; k <= seam.length; k++) cum.push(cum[k - 1] + seam[k % seam.length].distanceTo(seam[k - 1]));

    letters.push({
      i, ch: L.ch, hang, body, mesh, rest, cables,
      center: new THREE.Vector3((lx0 + lx1) / 2 - pivot.x, (ly0 + ly1) / 2 - pivot.y, 0),
      seam, cum,
      // physics
      y: DROP_H, vy: 0, taut: false, landed: false, released: false,
      tStart: start + gap * i,
      sx: new Spring(0, 14, 0.3),      // swing about x (forward / back)
      sz: new Spring(0, 16, 0.34),     // swing about z (sideways)
      tw: new Spring(0, 22, 0.42),     // twist about y: a wide twist sweeps into the neighbours
      sq: new Spring(0, 230, 0.26),    // squash (-) / stretch (+)
      away: new Spring(0, 75, 0.85),   // ENTER pressed: yanked up the cables, out of frame
      wasAway: false,
      // idle breath: hashed per letter (never index-modulo)
      f: [0.55 + 0.5 * hash(i, 11), 0.7 + 0.55 * hash(i, 12), 0.45 + 0.4 * hash(i, 13)],
      ph: [6.28 * hash(i, 14), 6.28 * hash(i, 15), 6.28 * hash(i, 16)],
      gag: null,                       // { kind, t0, sign }
    });
  }

  // fall time so the last letter's cables go taut on `land`: DROP_H = V0 T + G T^2 / 2
  const T_FALL = Math.max(0.22, land - start - gap * (letters.length - 1));
  const V0 = Math.max(0, (DROP_H - SLACK - 0.5 * G * T_FALL * T_FALL) / T_FALL);

  // ---- impulses scheduled for later frames (domino knocks) ------------------------
  const queue = [];
  const later = (t, fn) => queue.push({ t, fn });

  function knock(j, strength, side, delay, depth) {
    const L = letters[j];
    if (!L || !L.released) return;
    later(world.t + delay, () => {
      L.sz.kick(side * 0.5 * strength);
      L.sx.kick(hashS(j * 31 + depth, 21) * 0.5 * strength);
      L.sq.kick(-0.35 * strength);
      if (depth > 1) knock(j + side, strength * 0.45, side, 0.09, depth - 1);
    });
  }

  // cable just went taut: the landing. Stretch (the top stops, the body carries on),
  // wobble, and jostle the neighbours already hanging there.
  function onCatch(L, speed) {
    const u = clamp01(speed / 16);
    L.sq.kick(1.9 * u);
    L.sx.kick(hashS(L.i, 22) * 1.2 * u);
    L.tw.kick(hashS(L.i, 23) * 0.6 * u);
    if (u > 0.25) {
      knock(L.i - 1, 0.4 * u, -1, 0.06, 1);
      knock(L.i + 1, 0.4 * u, +1, 0.06, 1);
    }
  }

  // ---- public API -------------------------------------------------------------------
  const _v = new THREE.Vector3();
  let pokes = 0;
  function poke(i, strength = 1, dir = null) {
    const L = letters[i];
    if (!L || !L.landed) return;       // nothing to poke until it has arrived
    pokes++;
    const d = _v.set(0.35 * hashS(pokes, 30), 0, -1);
    if (dir) d.copy(dir);
    d.normalize();
    // push back (-z) swings the bottom back: +x rotation about the top pivot
    L.sx.kick(-d.z * 1.3 * strength);
    L.sz.kick(d.x * 0.7 * strength);
    L.tw.kick(hashS(pokes, 31) * 0.45 * strength);
    L.sq.kick(-1.1 * strength);
    if (d.y > 0.3) L.vy += d.y * 5.5 * strength;          // an upward poke is a hop
    const side = d.x >= 0 ? 1 : -1;
    knock(i + side, 0.5 * strength, side, 0.09, 2);
    knock(i - side, 0.25 * strength, -side, 0.14, 1);
    world.emit('poke', { letter: i, strength });
  }

  const center = (i, out = new THREE.Vector3()) => letters[i].mesh.localToWorld(out.copy(letters[i].center));

  function seam(i, u, out = new THREE.Vector3()) {
    const L = letters[i];
    const total = L.cum[L.cum.length - 1];
    const d = (((u % 1) + 1) % 1) * total;
    let lo = 0, hi = L.cum.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (L.cum[m] <= d) lo = m; else hi = m; }
    const a = L.seam[lo], b = L.seam[(lo + 1) % L.seam.length];
    const f = (d - L.cum[lo]) / Math.max(L.cum[lo + 1] - L.cum[lo], 1e-6);
    out.set(a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f, DEPTH / 2 + BEVEL_T + 1);
    return L.mesh.localToWorld(out);
  }

  const normal = (i, out = new THREE.Vector3()) =>
    out.set(0, 0, 1).transformDirection(letters[i].mesh.matrixWorld);

  const meshes = letters.map((L) => L.mesh);
  function pick(raycaster) {
    const hit = raycaster.intersectObjects(meshes, false)[0];
    return hit ? hit.object.userData.letter : -1;
  }

  world.title = { count: letters.length, poke, center, seam, normal, pick, letters };

  // ---- idle gags: one small bit of business every few seconds -----------------------
  let gagN = 0, nextGag = INTRO.work + 1.6;
  function scheduleGags(t) {
    if (t < nextGag) return;
    const k = gagN++;
    nextGag = t + 3.6 + 4.2 * hash(k, 40);
    const L = letters[Math.floor(hash(k, 41) * letters.length)];
    if (!L.landed) return;
    const r = hash(k, 42);
    const sign = hash(k, 43) < 0.5 ? -1 : 1;
    if (r < 0.3) {            // hop: a little jump on slack cables, then the catch
      L.vy += 3.4;
    } else if (r < 0.55) {    // twirl
      L.tw.kick(sign * 1.1);
    } else if (r < 0.8) {     // shiver
      L.gag = { kind: 'shiver', t0: t, sign };
    } else {                  // lean over to peek at the neighbour
      L.gag = { kind: 'peek', t0: t, sign };
    }
  }

  // ---- update ------------------------------------------------------------------------
  let lastClick = world.click.t;
  let tadaFired = false;
  const _c = new THREE.Vector3();

  function update(w) {
    const t = w.t, dt = Math.min(w.dt, 1 / 30);

    if (w.click.t !== lastClick) {
      lastClick = w.click.t;
      if (w.click.letter >= 0) {
        _c.copy(w.click.point).sub(w.camera.position).normalize();
        poke(w.click.letter, 1.0, _c);
      }
    }
    for (let q = queue.length - 1; q >= 0; q--) {
      if (queue[q].t <= t) { const { fn } = queue[q]; queue.splice(q, 1); fn(); }
    }
    scheduleGags(t);

    // TA-DA: the last letter lands on INTRO.tada and all five answer together — one
    // squash (anticipation), a synchronized hop with a chorus-line wiggle, then down.
    // Kinematic on top of the physics: introTada is sin^2, so it joins at zero value
    // and zero velocity on both ends.
    const joy = introTada(t);
    const joyU = Math.min(1, Math.max(0, (t - INTRO.tada) / 0.7));
    if (!tadaFired && t >= INTRO.tada) {
      tadaFired = true;
      for (const L of letters) L.sq.kick(-0.9);
    }

    let cableCount = 0;
    for (const L of letters) {
      const shown = t >= L.tStart;
      L.hang.visible = shown;
      for (const c of L.cables) c.mesh.visible = shown;
      // drop / hang: gravity always, the cable only pulls once taut
      if (t < L.tStart) {
        L.y = DROP_H; L.vy = 0;
      } else {
        if (!L.released) L.vy = -V0;        // launched downward, off-frame
        L.released = true;
        const h = dt / 4;
        for (let k = 0; k < 4; k++) {
          const taut = L.y < SLACK;
          let a = -G;
          if (taut) a += K_CABLE * (SLACK - L.y) - 2 * Z_CABLE * Math.sqrt(K_CABLE) * L.vy;
          if (taut && !L.taut && L.vy < -1.5) { onCatch(L, -L.vy); L.landed = true; }
          L.taut = taut;
          L.vy += a * h;
          L.y += L.vy * h;
        }
        if (!L.landed && Math.abs(L.y) < 0.05 && Math.abs(L.vy) < 0.3) L.landed = true;
      }

      // idle breath, ramped in after landing so it joins at zero
      const amp = smooth(L.tStart + 0.5, L.tStart + 2.5, t);
      let tx = amp * 0.045 * Math.sin(L.f[0] * t + L.ph[0]);
      let tz = amp * 0.028 * Math.sin(L.f[1] * t + L.ph[1]);
      let ty = amp * 0.07 * Math.sin(L.f[2] * t + L.ph[2]);

      // the cursor: letters lean shyly away when it comes close, and turn to look
      if (w.cursor.seen) {
        L.mesh.getWorldPosition(_c);
        const dx = w.cursor.point.x - _c.x, dy = w.cursor.point.y - (_c.y - 0.9);
        const near = Math.exp(-(dx * dx + dy * dy) / (2 * 1.3 * 1.3)) * amp;
        tx += 0.11 * near;
        ty += Math.max(-0.14, Math.min(0.14, dx * 0.09)) * near;
      }

      if (L.gag) {
        const u = t - L.gag.t0;
        if (L.gag.kind === 'shiver') {
          // sin(0) = 0 and the envelope rises from 0: no step at the start
          tz += L.gag.sign * 0.05 * Math.sin(2 * Math.PI * 7 * u) * (1 - Math.exp(-u * 18)) * Math.exp(-u * 2.6);
          if (u > 2) L.gag = null;
        } else if (L.gag.kind === 'peek') {
          tz += L.gag.sign * 0.07 * Math.sin(Math.PI * clamp01(u / 1.6)) ** 2;
          ty += L.gag.sign * 0.12 * Math.sin(Math.PI * clamp01(u / 1.6)) ** 2;
          if (u > 1.6) L.gag = null;
        }
      }

      ty += 0.16 * Math.sin(4 * Math.PI * joyU) * joy;
      tz += 0.05 * Math.sin(4 * Math.PI * joyU + 0.9) * joy;

      const sx = L.sx.step(tx, dt), sz = L.sz.step(tz, dt), tw = L.tw.step(ty, dt);
      const q = Math.max(-0.32, Math.min(0.45, L.sq.step(0, dt)));
      // everything leaves: a dip (the wind-up), then up the cables, letter after letter;
      // when the hero comes back they drop back in on the same spring
      const goAway = !!(world.away && world.away.on && t > world.away.since + 0.06 * L.i);
      if (goAway && !L.wasAway) { L.away.kick(-4.5); L.sq.kick(-1.2); }
      L.wasAway = goAway;
      L.away.step(goAway ? 11 : 0, dt);
      L.hang.position.set(L.rest.x, L.rest.y + L.y + 0.34 * joy + L.away.x, L.rest.z);
      L.hang.rotation.set(sx, tw, sz);
      const side = 1 / Math.sqrt(1 + q);
      L.body.scale.set(side, 1 + q, side);
      L.hang.updateMatrixWorld(true);

      for (const c of L.cables) {
        L.mesh.localToWorld(c.end.copy(c.local));
        stretchBetween(c.mesh, c.anchor, c.end);
        if (shown) {
          c.mesh.updateMatrix();
          cableBatch.setMatrixAt(cableCount++, c.mesh.matrix);
        }
      }
    }
    cableBatch.count = cableCount;
    cableBatch.instanceMatrix.needsUpdate = true;
  }

  return { update, letters };
}
