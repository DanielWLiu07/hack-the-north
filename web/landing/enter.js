// enter.js — ENTER. Just the word, in Katie Roze, hanging under GITRL like a second,
// smaller line of the title: same light metal letters, same thin cables, the same
// traced font (tools/build_gitrl.py --word ENTER -> title/enter.json).
//
// It drops in behind the title right after the ta-da. Hover: it swells a touch and an
// underline draws itself. Press (pointer, or the hidden link for keyboards): it
// squashes, throws sparks, and emits 'enter-press' — scene.js then sends everything
// away and scrolls to the dashboard. This module never navigates by itself.
//
// Publishes world.enter = { pos, radius, hovered }; emits 'enter-hover' { on } and
// 'enter-press' {}. It is the ONLY writer of document.body.style.cursor.

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { MAT, Spring, stretchBetween, smooth, yieldBuild } from './mech.js';
import { ENTER, TITLE, INTRO } from './layout.js';

// the traced word -> one merged geometry, centred on the origin, `width` wide
async function wordGeometry(meta, width, tracking = 40) {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  const geos = [];
  for (const [i, L] of meta.letters.entries()) {
    await yieldBuild();
    const pen = L.pen + tracking * i;
    const shapes = L.parts.map((part) => {
      const shape = new THREE.Shape(part.outer.map(([x, y]) => new THREE.Vector2(x, y)));
      for (const h of part.holes) shape.holes.push(new THREE.Path(h.map(([x, y]) => new THREE.Vector2(x, y))));
      for (const [x, y] of part.outer) {
        minX = Math.min(minX, x + pen); maxX = Math.max(maxX, x + pen);
        minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      }
      return shape;
    });
    // the bevel fattens the brush strokes a little: they have to survive phone size
    geos.push(new THREE.ExtrudeGeometry(shapes, { depth: 40, bevelEnabled: true, bevelThickness: 6,
      bevelSize: 10, bevelSegments: 1, curveSegments: 1 }).translate(pen, 0, 0));
  }
  // an ExtrudeGeometry is non-indexed with two groups: [0] the faces, [1] the sides. Pull
  // them apart so every letter's faces merge into one light mesh and the sides into a dark one.
  const slice = (g, group) => {
    const out = new THREE.BufferGeometry();
    for (const name of ['position', 'normal', 'uv']) {
      const a = g.attributes[name];
      out.setAttribute(name, new THREE.BufferAttribute(
        a.array.slice(group.start * a.itemSize, (group.start + group.count) * a.itemSize), a.itemSize));
    }
    return out;
  };
  const s = width / (maxX - minX);
  const place = (g) => g.translate(-(minX + maxX) / 2, -(minY + maxY) / 2, 0).scale(s, s, s);
  const faces = place(mergeGeometries(geos.map((g) => slice(g, g.groups[0])), false));
  const sides = place(mergeGeometries(geos.map((g) => slice(g, g.groups[1])), false));
  for (const g of geos) g.dispose();
  return { faces, sides, halfW: width / 2, halfH: (maxY - minY) * s / 2 };
}

export async function buildEnter(world) {
  const { scene } = world;
  const meta = await (await fetch('./title/enter.json')).json();
  const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

  const root = new THREE.Group();             // at ENTER.center; rides up and down (drop-in, away)
  root.position.copy(ENTER.center);
  scene.add(root);
  const sway = new THREE.Group();             // swings about a pivot above the word, like the title
  sway.position.y = 0.6;
  root.add(sway);
  const word = new THREE.Group();
  word.position.y = -0.6;
  sway.add(word);

  const { faces, sides, halfW, halfH } = await wordGeometry(meta, ENTER.width);
  const mesh = new THREE.Mesh(faces, MAT.lifted), edge = new THREE.Mesh(sides, MAT.lifted);
  mesh.frustumCulled = edge.frustumCulled = false;
  word.add(mesh, edge);
  const underline = new THREE.Mesh(new THREE.BoxGeometry(1, 0.035, 0.05), MAT.cable);
  underline.position.set(0, -halfH - 0.12, 0.05);
  underline.scale.x = 0.0001;
  word.add(underline);

  // two thin cables up to the title's cable rail; they pass BEHIND the title's letters
  const cables = [-1, 1].map((side) => {
    const c = new THREE.Mesh(new THREE.CylinderGeometry(0.011, 0.011, 1, 5), MAT.cable);
    c.frustumCulled = false;
    scene.add(c);
    return { mesh: c, side, a: new THREE.Vector3(), b: new THREE.Vector3() };
  });

  const state = { pos: new THREE.Vector3(), radius: halfW, hovered: false };
  world.enter = state;
  const drop = new Spring(9, 85, 0.7);        // fast arrival, short damped catch
  const gone = new Spring(0, 95, 0.85);       // 'away': yanked back up out of frame
  const swell = new Spring(0, 1600, 0.85), squash = new Spring(0, 700, 0.55), swing = new Spring(0, 14, 0.3);
  let underlineReveal = 0;
  let pointerOver = false, focused = false, lastClickT = world.click.t, pressedAt = -Infinity;

  function press() {
    if (world.t - pressedAt < 1.5 || (world.away && world.away.on)) return;
    pressedAt = world.t;
    squash.kick(-9);
    swing.kick(1.2);
    for (const side of [-1, 1]) {
      world.sparks.emit(state.pos.clone().add(new THREE.Vector3(side * halfW, 0, 0.2)),
        new THREE.Vector3(side, 0.5, 0.6), 16, 3.2, 0.9);
    }
    world.emit('enter-press', {});
  }

  // keyboards and screen readers get a real link; it triggers the same press
  const link = document.createElement('a');
  link.href = ENTER.href;
  link.textContent = 'Enter the dashboard';
  link.style.cssText = 'position:absolute;left:0;top:0;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap';
  link.addEventListener('focus', () => { focused = true; });
  link.addEventListener('blur', () => { focused = false; });
  link.addEventListener('click', (e) => { e.preventDefault(); press(); });
  (document.getElementById('hero') || document.body).append(link);

  // is a ray over the word? a padded box in the word's own frame
  const _inv = new THREE.Matrix4(), _ray = new THREE.Ray(), _hit = new THREE.Vector3();
  const box = new THREE.Box3(new THREE.Vector3(-halfW - 0.2, -halfH - 0.25, -0.3), new THREE.Vector3(halfW + 0.2, halfH + 0.2, 0.4));
  const over = (ray) => { _inv.copy(word.matrixWorld).invert(); return !!_ray.copy(ray).applyMatrix4(_inv).intersectBox(box, _hit); };
  const setCursor = (on) => { document.body.style.cursor = on ? 'pointer' : ''; };
  addEventListener('scroll', () => { if (state.hovered) { state.hovered = false; setCursor(false); } }, { passive: true });

  function update(world) {
    const { t, dt } = world, cur = world.cursor, away = !!(world.away && world.away.on);
    // drop in behind the title just after the ta-da; when everything leaves, so does this
    drop.step(t >= INTRO.tada + 0.08 ? 0 : 9, dt);
    gone.step(away ? 9 : 0, dt);
    root.position.y = ENTER.center.y + Math.max(0, drop.x) + gone.x - 0.12 * Math.max(0, -drop.x);
    root.visible = root.position.y < ENTER.center.y + 8.5;
    const present = root.visible && drop.x < 0.4 && !away;

    pointerOver = present && cur.seen && cur.present && over(cur.ray);
    const hovered = pointerOver || (focused && present);
    if (hovered !== state.hovered) { state.hovered = hovered; setCursor(pointerOver); world.emit('enter-hover', { on: hovered }); }
    if (world.click.t !== lastClickT) {
      lastClickT = world.click.t;
      _ray.origin.copy(world.camera.position);
      _ray.direction.copy(world.click.point).sub(world.camera.position).normalize();
      if (present && over(_ray)) press();
    }

    // life: a slow hang-sway, a swell on hover, a squash on press, a "press me" hop now and then
    const hop = reduced || !present ? 0 : Math.max(0, Math.sin((t - INTRO.work) * 1.02)) ** 24 * 0.1;
    swing.step(Math.sin(t * 0.7) * 0.025, dt);
    sway.rotation.z = swing.x;
    const sw = 1 + 0.09 * swell.step(hovered ? 1 : 0, dt), sq = squash.step(0, dt) * 0.02;
    word.scale.set(sw * (1 - sq), sw * (1 + sq), sw);
    word.position.y = -0.6 + hop;
    // Fast independent feedback: do not smoothstep an already eased spring,
    // which used to hide the first part of the hover response.
    underlineReveal += ((hovered ? 1 : 0) - underlineReveal) * (1 - Math.exp(-48 * dt));
    underline.scale.x = Math.max(0.0001, underlineReveal * ENTER.width * 0.9);

    root.updateMatrixWorld(true);
    mesh.getWorldPosition(state.pos);
    for (const c of cables) {
      c.a.set(c.side * halfW * 0.72, halfH * 0.7, -0.02).applyMatrix4(word.matrixWorld);
      c.b.set(ENTER.center.x + c.side * halfW * 0.72, TITLE.cableTopY + gone.x, ENTER.center.z);
      stretchBetween(c.mesh, c.a, c.b);
      c.mesh.visible = root.visible;
    }
  }

  return { update, feedback: () => ({ hovered: state.hovered, underline: underlineReveal, swell: swell.x }) };
}
