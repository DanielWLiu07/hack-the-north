// scene-model.js — the room's 3D model, as three.js objects, for any page that draws one: /scene (scene.js) and the Room
// page (room-map.js). Nothing here knows about a page: no DOM, no fetch, no camera. It gives a page
//   Layer      one cloud — a map's voxels, a capture's points, a map's dense layer — drawn as points OR solid cells
//   readPly    the one PLY layout scripts/room_live.py writes (float x y z + uchar r g b), straight into a Layer's arrays
//   Boxes      a map's objects, walls and floor finds, as wireframe boxes with a size label each
//   Robot      the robot: mast, camera, look line, floor arrow, name — placed from a pose and a camera mount
//   label      a word as a sprite that keeps its size on screen and is never hidden by points
// and the numbers they share: the height ramp, the box colours, the measured camera mount.
//
// FRAMES. Every model is z-up, floor at z = 0, metres (a map in SLAM's world frame, a capture in the robot's own frame:
// x forward, y left). These objects are built in that frame. A page whose scene is y-up puts them in a Group and turns
// the group (room-map.js does: rotation.x = -pi/2, so map (x, y, z) lands on (x, z, -y)).
//
// WHAT KEEPS HALF A MILLION POINTS SMOOTH: one BufferGeometry per layer, Float32 positions + normalized Uint8 colours,
// arrays REUSED from model to model (allocated again only when a cloud is bigger than any before it), colour mode, point
// size and the crop as UNIFORMS of one small shader (a slider uploads nothing), and nothing allocated per frame.
import * as THREE from 'three';

export const PLY_COLUMNS = ['float x', 'float y', 'float z', 'uchar red', 'uchar green', 'uchar blue'];
export const POINT_BYTES = 15;
export const RAMP = [[0.20, 0.35, 0.62], [0.13, 0.62, 0.60], [0.40, 0.80, 0.40], [0.98, 0.88, 0.20], [0.95, 0.45, 0.23]];   // scene.css .ramp
export const RAMP_Z = { capture: [0, 2.5], map: [0, 1.3] };        // bbos caps its map at 1.3 m; a capture sees up to the ceiling
// an object's box: a colour neither the height ramp nor a camera image has; a wall grey; a floor find green, a large one amber
export const BOX = '#f06bd0', WALL = '#a4a4b0', FLOOR = '#5fd47a', LARGE = '#f2b53c', INK = '#efece6';
export const BOX_COLOUR = { object: BOX, wall: WALL, floor: FLOOR, large: LARGE };
export const MOUNT = { height_m: 1.59, pitch_down_deg: 38, yaw_left_deg: 0 };       // the head camera, measured; used when a model records no mount
export const cm = (m) => Math.round(m * 100);
export const triple = (v) => Array.isArray(v) && v.length === 3 && v.every((n) => Number.isFinite(n));

// ── uniforms every layer on a page shares: set once, seen by all ────────────────
export function makeShared() {
  return { uScale: { value: 800 }, uMaxPx: { value: 32 }, uCrop: { value: 1e9 }, uHeight: { value: 0 },
    uRobot: { value: new THREE.Vector2() }, uZ: { value: new THREE.Vector2(0, 1.3) } };
}
export function fitShared(shared, heightPx, fovDeg, dpr) {   // after a resize: device px per metre at 1 m, and the largest point
  shared.uScale.value = heightPx * dpr / (2 * Math.tan(fovDeg * Math.PI / 360));
  shared.uMaxPx.value = 64 * dpr;          // a 3 cm voxel is 64 px at about half a metre: closer than that, cells stop growing
}

// ── the cloud, two ways: points, or solid cells ─────────────────────────────────
// Both are one small shader, for what PointsMaterial cannot do: a point is a size in the ROOM (millimetres, so a surface
// stays closed as you fly in) clamped to 1 px..uMaxPx; the crop hides points by ground range from the robot without
// touching a buffer; height colouring is a ramp on z, shaded by the pixel's own brightness so edges survive. A CELL is the
// same vertex, drawn as a cube of uCube metres around it (one box geometry, instanced once per point: 51,000 cubes is
// 1.8 M indices, nothing to a GPU), lit by one fixed light from above so its faces differ and a wall reads as a wall.
// The camera's colours are sRGB bytes and go to the screen as they are — no colour-space pass is included on purpose.
const glsl = (rgb) => `vec3(${rgb.map((v) => v.toFixed(3)).join(', ')})`;
const COLOUR_GLSL = `
    uniform float uCrop, uHeight; uniform vec2 uRobot, uZ;
    attribute vec3 rgb; varying vec3 vColor;
    vec3 ramp(float t) {
      float s = t * 4.0;
      vec3 c = mix(${glsl(RAMP[0])}, ${glsl(RAMP[1])}, clamp(s, 0.0, 1.0));
      c = mix(c, ${glsl(RAMP[2])}, clamp(s - 1.0, 0.0, 1.0));
      c = mix(c, ${glsl(RAMP[3])}, clamp(s - 2.0, 0.0, 1.0));
      return mix(c, ${glsl(RAMP[4])}, clamp(s - 3.0, 0.0, 1.0));
    }
    vec3 colourAt(vec3 p) {
      float luma = dot(rgb, vec3(0.299, 0.587, 0.114));
      return mix(rgb, ramp(clamp((p.z - uZ.x) / (uZ.y - uZ.x), 0.0, 1.0)) * (0.5 + 0.5 * luma), uHeight);
    }`;
const FRAGMENT = 'varying vec3 vColor; void main() { gl_FragColor = vec4(vColor, 1.0); }';

function pointsMaterial(shared, sizeM) {
  return new THREE.ShaderMaterial({
    uniforms: { ...shared, uSize: { value: sizeM } },
    vertexShader: `uniform float uSize, uScale, uMaxPx;${COLOUR_GLSL}
    void main() {
      vec4 mv = modelViewMatrix * vec4(position, 1.0);
      gl_Position = projectionMatrix * mv;
      gl_PointSize = clamp(uSize * uScale / max(-mv.z, 0.001), 1.0, uMaxPx);
      if (distance(position.xy, uRobot) > uCrop) gl_Position = vec4(2.0, 2.0, 2.0, 1.0);       // outside the clip volume: not drawn
      vColor = colourAt(position);
    }`,
    fragmentShader: FRAGMENT });
}
function cubesMaterial(shared, cellM) {
  return new THREE.ShaderMaterial({
    uniforms: { ...shared, uCube: { value: cellM } },
    vertexShader: `uniform float uCube; attribute vec3 offset;${COLOUR_GLSL}
    void main() {
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position * uCube + offset, 1.0);
      if (distance(offset.xy, uRobot) > uCrop) gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
      float lit = 0.62 + 0.38 * max(dot(normal, normalize(vec3(-0.35, 0.3, 0.9))), 0.0);     // one light, high and a little left: tops bright, sides mid, undersides dark
      vColor = colourAt(offset) * lit;
    }`,
    fragmentShader: FRAGMENT });
}

const CELL = new THREE.BoxGeometry(1, 1, 1);  // every layer's cubes instance this one box, scaled in the shader

export class Layer {
  // One set of arrays — Float32 xyz, Uint8 rgb — behind two objects: THREE.Points, and a Mesh of instanced cubes whose
  // per-instance `offset` and `rgb` are the SAME arrays. The arrays are reused from model to model (a new one is only
  // allocated when a cloud is bigger than any before it), and only the object on screen is uploaded to: a 7.5 MB capture
  // shown as points does not also fill the cubes' buffers until you switch.
  constructor(shared, sizeM, cellM) {
    this.points = new THREE.Points(new THREE.BufferGeometry(), pointsMaterial(shared, sizeM));
    this.cubes = new THREE.Mesh(new THREE.InstancedBufferGeometry(), cubesMaterial(shared, cellM));
    this.cubes.geometry.setIndex(CELL.index); this.cubes.geometry.setAttribute('position', CELL.attributes.position); this.cubes.geometry.setAttribute('normal', CELL.attributes.normal);
    this.cubes.geometry.instanceCount = 0;
    this.points.frustumCulled = this.cubes.frustumCulled = false;      // one object, always wanted: a bounding sphere over 500k points buys nothing
    this.points.visible = this.cubes.visible = false;
    this.group = new THREE.Group(); this.group.add(this.points, this.cubes);
    this.capacity = 0; this.n = 0; this.mode = 'points'; this.stale = { points: false, cubes: false };
  }
  buffers(n) {                               // arrays with room for n points, on the GPU side already if they were big enough
    if (n > this.capacity) {
      this.points.geometry.dispose(); this.cubes.geometry.dispose();      // dispose FIRST: attributes replaced before it are never released
      this.capacity = Math.ceil(n * 1.15);
      const xyz = new Float32Array(this.capacity * 3), rgb = new Uint8Array(this.capacity * 3);
      this.points.geometry.setAttribute('position', new THREE.BufferAttribute(xyz, 3));
      this.points.geometry.setAttribute('rgb', new THREE.BufferAttribute(rgb, 3, true));
      this.cubes.geometry.setAttribute('offset', new THREE.InstancedBufferAttribute(xyz, 3));
      this.cubes.geometry.setAttribute('rgb', new THREE.InstancedBufferAttribute(rgb, 3, true));
    }
    return [this.points.geometry.attributes.position.array, this.points.geometry.attributes.rgb.array];
  }
  filled(n) {                                // the first n points of the arrays are the model now
    this.n = n; this.stale.points = this.stale.cubes = true;
    this.points.geometry.setDrawRange(0, n); this.cubes.geometry.instanceCount = n;
    this.show(this.mode);
  }
  show(mode, on = true) {                    // which of the two is drawn; uploads its buffers if they lag the arrays
    this.mode = mode;
    const obj = mode === 'cubes' ? this.cubes : this.points, geom = obj.geometry;
    if (on && this.n && this.stale[mode]) {
      for (const a of [geom.attributes[mode === 'cubes' ? 'offset' : 'position'], geom.attributes.rgb]) { a.clearUpdateRanges(); a.addUpdateRange(0, this.n * 3); a.needsUpdate = true; }
      this.stale[mode] = false;
    }
    this.points.visible = on && this.n > 0 && mode === 'points'; this.cubes.visible = on && this.n > 0 && mode === 'cubes';
  }
  set size(m) { this.points.material.uniforms.uSize.value = m; }
  set cell(m) { this.cubes.material.uniforms.uCube.value = m; }
  get cell() { return this.cubes.material.uniforms.uCube.value; }
}

export function unreadable(words) { const e = new Error(words); e.unreadable = true; return e; }     // the FILE is at fault: asking again will not help

export function readPly(bytes, layer) {
  // header: ASCII lines up to "end_header\n"; after it, n records of <f4 x y z, u1 r g b — 15 bytes, so NOT 4-byte aligned:
  // a Float32Array cannot be laid over them, hence the DataView. ~10 ms for half a million points, into `layer`'s arrays.
  const head = new TextDecoder('latin1').decode(bytes.subarray(0, Math.min(bytes.length, 4096)));
  const end = head.indexOf('end_header\n');
  if (!head.startsWith('ply\n') || end < 0) throw unreadable('the file does not begin with a PLY header');
  const lines = head.slice(0, end).split('\n').map((l) => l.trim());
  if (!lines.includes('format binary_little_endian 1.0')) throw unreadable(`the PLY is "${lines[1] || '?'}"; this page reads binary_little_endian 1.0`);
  const columns = lines.filter((l) => l.startsWith('property ')).map((l) => l.slice(9));
  if (columns.join('|') !== PLY_COLUMNS.join('|')) throw unreadable(`the PLY's columns are "${columns.join(', ')}"; this page reads exactly "${PLY_COLUMNS.join(', ')}"`);
  const count = lines.map((l) => /^element vertex (\d+)$/.exec(l)).find(Boolean);
  if (!count) throw unreadable('the PLY header names no "element vertex"');
  const n = +count[1], start = end + 'end_header\n'.length;
  if (bytes.length < start + n * POINT_BYTES) throw new Error(`the file is cut short: ${n.toLocaleString()} points need ${(start + n * POINT_BYTES).toLocaleString()} bytes and ${bytes.length.toLocaleString()} arrived — it was probably still being written`);
  const [xyz, col] = layer.buffers(n);
  const view = new DataView(bytes.buffer, bytes.byteOffset + start, n * POINT_BYTES);
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;          // the floor plan's extent, for framing the camera
  for (let i = 0, o = 0, p = 0, c = start + 12; i < n; i++, o += POINT_BYTES, p += 3, c += POINT_BYTES) {
    const x = view.getFloat32(o, true), y = view.getFloat32(o + 4, true);
    xyz[p] = x; xyz[p + 1] = y; xyz[p + 2] = view.getFloat32(o + 8, true);
    col[p] = bytes[c]; col[p + 1] = bytes[c + 1]; col[p + 2] = bytes[c + 2];
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;      // (NaN fails every test: it moves nothing)
  }
  layer.filled(n);
  return { n, bounds: n && x1 >= x0 ? { min: [x0, y0], max: [x1, y1] } : null };
}

// ── words in the picture ────────────────────────────────────────────────────────
export function label(text, color) {           // a word that stays the same size on screen and is never hidden by points
  const c = document.createElement('canvas'), g = c.getContext('2d'), font = '600 44px ui-monospace, Menlo, monospace';
  g.font = font;
  c.width = Math.ceil(g.measureText(text).width) + 16; c.height = 64;
  g.font = font; g.fillStyle = color; g.textBaseline = 'middle'; g.fillText(text, 8, 34);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, sizeAttenuation: false, transparent: true }));
  s.scale.set(0.032 * c.width / c.height, 0.032, 1); s.center.set(0.5, 0); s.renderOrder = 3;       // the word stands ON its anchor
  return s;
}

// ── a map's objects, walls and floor finds, as boxes ────────────────────────────
// The sidecar gives each one a centre and an AXIS-ALIGNED size, so a box is 12 edges and no rotation. Each kind is ONE
// LineSegments — a map has tens of them, not thousands — rebuilt when a map loads, never per frame. The label is the size
// in cm (a floor find: its height); the chosen one also gets a faint solid so it reads in a crowd.
const EDGES = [0, 1, 1, 3, 3, 2, 2, 0, 4, 5, 5, 7, 7, 6, 6, 4, 0, 4, 1, 5, 2, 6, 3, 7];     // corner i = (x: i&1, y: i&2, z: i&4)

export function thingsOf(meta) {             // the sidecar's two lists as one: `box` names the colour, `kind` what it is
  const objects = ((meta && meta.objects) || []).filter((t) => t && (t.kind === 'object' || t.kind === 'wall') && triple(t.centre_m) && triple(t.size_m))
    .map((t) => ({ ...t, box: t.kind }));
  // floor objects: the floor pass's finds — {centre, size_m, height_m, kind: "object" | "large", range_m, flags}. Missing key = none.
  const floor = ((meta && meta.floor_objects) || []).filter((t) => t && triple(t.centre) && triple(t.size_m))
    .map((t) => ({ kind: 'floor', box: t.kind === 'large' ? 'large' : 'floor', centre_m: t.centre, size_m: t.size_m, height_m: t.height_m, from_robot_m: t.range_m, flags: t.flags }));
  return [...objects, ...floor];
}

// A one-pixel wire is a fine diagram on black and invisible over half a million points, so each box is drawn three
// ways on one geometry: the crisp wire, a translucent ADDITIVE solid that lifts everything standing inside the box,
// and the same wire again with depth off and faint. The wire and the solid are depth-tested, so a box behind an
// object still reads as behind it; the ghost is what stops a box swallowed by the cloud from disappearing outright,
// and being much dimmer it still reads as "back there". The solid adds light and never subtracts it — nothing
// inside a box is veiled by the thing that points at it, which is the whole job.
// BOX_COLOUR itself is untouched: scene.css's legend swatches are those hexes, and a legend has to match. What is
// DRAWN is each one lifted toward white, same hue, more of it.
const WHITE = new THREE.Color(1, 1, 1);
const lift = (hex, k) => new THREE.Color(hex).lerp(WHITE, k);
// GL_LINES is one pixel wide on every driver that matters — `linewidth` is a documented no-op in WebGL — and one
// pink pixel over half a million points is nothing. So the wire is drawn as a stroke: the same geometry five
// times, once straight and once nudged each way in clip space, which is a 3 px pen. The nudge is in NDC, so the
// stroke grows with the display instead of thinning out on the big screen in the room.
const NUDGE = 0.0024;
const PEN = [[0, 0], [NUDGE, 0], [-NUDGE, 0], [0, NUDGE], [0, -NUDGE]];
function strokeMaterial(colour, nudge, ghost) {
  return new THREE.ShaderMaterial({
    // the five passes composite, so a ghost pass is worth about 2.5x its own alpha where they overlap
    uniforms: { uColour: { value: new THREE.Color(colour) }, uNudge: { value: new THREE.Vector2(...nudge) }, uOpacity: { value: ghost ? 0.14 : 1 } },
    vertexShader: 'uniform vec2 uNudge; void main(){ vec4 p = projectionMatrix * modelViewMatrix * vec4(position, 1.0); p.xy += uNudge * p.w; gl_Position = p; }',
    fragmentShader: 'uniform vec3 uColour; uniform float uOpacity; void main(){ gl_FragColor = vec4(uColour, uOpacity); }',
    transparent: Boolean(ghost), depthTest: !ghost, depthWrite: !ghost,
  });
}

export class Boxes {
  constructor() {
    this.group = new THREE.Group();
    this.lines = {}; this.ghosts = {};
    for (const [kind, hex] of Object.entries(BOX_COLOUR)) {
      const geometry = new THREE.BufferGeometry(), colour = lift(hex, 0.24);
      const pens = (ghost) => PEN.map((nudge) => {
        const line = new THREE.LineSegments(geometry, strokeMaterial(colour, nudge, ghost));
        line.frustumCulled = false; line.renderOrder = ghost ? 3 : 0;
        return line;
      });
      this.lines[kind] = pens(false);        // the crisp wire, depth-tested: behind an object it goes behind it
      this.ghosts[kind] = pens(true);        // and the same wire with depth off and faint, so it is never lost
    }
    this.solids = null; this.capacity = 0;     // one InstancedMesh for every box, grown only when a map has more
    this.names = new THREE.Group();
    this.chosen = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial({ color: INK, transparent: true, opacity: 0.16, depthWrite: false }));
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(this.chosen.geometry), new THREE.LineBasicMaterial({ color: INK, depthTest: false }));
    this.chosen.visible = false; this.chosen.add(edges); edges.renderOrder = 2;
    this.group.add(...Object.values(this.lines).flat(), ...Object.values(this.ghosts).flat(), this.names, this.chosen);
    this.things = [];
  }
  room(n) {                                  // the solids, with room for n boxes
    if (this.solids && n <= this.capacity) return this.solids;
    if (this.solids) { this.group.remove(this.solids); this.solids.geometry.dispose(); this.solids.material.dispose(); this.solids.dispose(); }
    this.capacity = Math.max(32, Math.ceil(n * 1.3));
    this.solids = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.075, depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false }), this.capacity);
    this.solids.frustumCulled = false;
    this.solids.renderOrder = 1;
    this.group.add(this.solids);
    return this.solids;
  }
  draw(things) {
    for (const s of this.names.children) { s.material.map.dispose(); s.material.dispose(); }
    this.names.clear(); this.chosen.visible = false; this.things = things;
    const solids = this.room(things.length), m = new THREE.Matrix4(), c = new THREE.Color();
    let n = 0;
    for (const kind of Object.keys(BOX_COLOUR)) {
      const of = things.filter((t) => t.box === kind), xyz = new Float32Array(of.length * EDGES.length * 3);
      of.forEach((t, k) => {
        const [cx, cy, cz] = t.centre_m, [sx, sy, sz] = t.size_m;
        EDGES.forEach((corner, e) => xyz.set([cx + (corner & 1 ? sx : -sx) / 2, cy + (corner & 2 ? sy : -sy) / 2, cz + (corner & 4 ? sz : -sz) / 2], (k * EDGES.length + e) * 3));
        solids.setMatrixAt(n, m.makeScale(Math.max(sx, 0.02), Math.max(sy, 0.02), Math.max(sz, 0.02)).setPosition(cx, cy, cz));
        solids.setColorAt(n, c.copy(lift(BOX_COLOUR[kind], 0.3)));
        n++;
        const name = label(kind === 'wall' ? 'wall' : t.kind === 'floor' ? `floor · ${cm(t.height_m ?? sz)} cm` : `${cm(sx)}×${cm(sy)}×${cm(sz)} cm`, BOX_COLOUR[kind]);
        name.scale.multiplyScalar(0.6); name.material.opacity = 0.9; name.position.set(cx, cy, cz + sz / 2 + 0.03); this.names.add(name);
      });
      this.lines[kind][0].geometry.dispose();   // tens of boxes, once per map: a fresh buffer is simpler than a pool, and as fast
      const geometry = new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(xyz, 3));
      for (const line of [...this.lines[kind], ...this.ghosts[kind]]) line.geometry = geometry;    // one buffer, ten passes
    }
    solids.count = n;
    solids.instanceMatrix.needsUpdate = true;
    if (solids.instanceColor) solids.instanceColor.needsUpdate = true;
  }
  choose(t) {                                // the faint solid on one of them (null: none)
    this.chosen.visible = Boolean(t);
    if (t) { this.chosen.position.set(...t.centre_m); this.chosen.scale.set(...t.size_m.map((v) => Math.max(v, 0.03))); }
  }
}

// ── the robot ───────────────────────────────────────────────────────────────────
// A mast from the floor to its camera, the camera as a small frustum, a short line along where it looks and — faint —
// the rest of that line down to the floor; on the floor, an arrow the way it FACES; over its head, its name. The group's
// +x is the robot's forward: a capture's frame already is that; a map gives a heading, which scene_api turns into yaw.
export class Robot {
  constructor() {
    this.group = new THREE.Group();
    this.lines = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: INK }));
    this.ray = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: INK, transparent: true, opacity: 0.3 }));
    this.lines.geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(10 * 2 * 3), 3));
    this.ray.geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(3 * 2 * 3), 3));
    this.head = new THREE.Mesh(new THREE.SphereGeometry(0.035, 16, 10), new THREE.MeshBasicMaterial({ color: INK }));
    this.name = label('robot', INK);
    this.arrow = new THREE.Group();
    const m = new THREE.MeshBasicMaterial({ color: INK, depthTest: false });       // floor voxels sit at the same height: do not let them bury it
    this.arrow.add(new THREE.Mesh(new THREE.CylinderGeometry(0.014, 0.014, 0.5, 8).translate(0, 0.25, 0), m),
      new THREE.Mesh(new THREE.ConeGeometry(0.05, 0.16, 14).translate(0, 0.58, 0), m), new THREE.Mesh(new THREE.SphereGeometry(0.04, 14, 10), m));
    this.arrow.children.forEach((c) => { c.renderOrder = 2; });
    this.arrow.rotation.z = -Math.PI / 2; this.arrow.position.z = 0.02;             // built along three's y; forward is +x
    this.group.add(this.lines, this.ray, this.head, this.name, this.arrow);
    this.pose = { x: 0, y: 0, yaw: 0, h: MOUNT.height_m, pitch: MOUNT.pitch_down_deg * Math.PI / 180 };
    this.place(null, null);
  }
  place(where, mount) {                      // where: {x, y, yaw} in the model's frame (yaw CCW from +x); mount: {height_m, pitch_down_deg, yaw_left_deg}
    const m = { ...MOUNT, ...(mount || {}) }, r = where || {}, pose = this.pose;
    pose.x = +r.x || 0; pose.y = +r.y || 0; pose.h = +m.height_m || MOUNT.height_m;
    pose.yaw = (+r.yaw || 0) + (+m.yaw_left_deg || 0) * Math.PI / 180;
    pose.pitch = (Number.isFinite(+m.pitch_down_deg) ? +m.pitch_down_deg : MOUNT.pitch_down_deg) * Math.PI / 180;
    this.group.position.set(pose.x, pose.y, 0); this.group.rotation.z = pose.yaw;
    const h = pose.h, d = [Math.cos(pose.pitch), 0, -Math.sin(pose.pitch)], u = [Math.sin(pose.pitch), 0, Math.cos(pose.pitch)];
    const at = (f, l, v) => [d[0] * f + u[0] * v, l, h + d[2] * f + u[2] * v];      // f along the look, l to the left, v up the image
    const c = [at(0.3, 0.17, 0.12), at(0.3, -0.17, 0.12), at(0.3, -0.17, -0.12), at(0.3, 0.17, -0.12)], eye = [0, 0, h];
    this.lines.geometry.attributes.position.array.set([0, 0, 0, ...eye, ...eye, ...at(0.6, 0, 0),
      ...eye, ...c[0], ...eye, ...c[1], ...eye, ...c[2], ...eye, ...c[3], ...c[0], ...c[1], ...c[1], ...c[2], ...c[2], ...c[3], ...c[3], ...c[0]]);
    this.lines.geometry.attributes.position.needsUpdate = true; this.lines.geometry.computeBoundingSphere();      // culling uses it; 20 vertices
    // the look line carried on to the floor, and a small cross where it lands. A camera looking level or up never lands.
    const reach = pose.pitch > 0.05 ? h / Math.sin(pose.pitch) : 0, fx = reach * d[0];
    this.ray.visible = reach > 0.6 && reach < 12;
    this.ray.geometry.attributes.position.array.set([...at(0.6, 0, 0), fx, 0, 0.003, fx - 0.12, 0, 0.003, fx + 0.12, 0, 0.003, fx, -0.12, 0.003, fx, 0.12, 0.003]);
    this.ray.geometry.attributes.position.needsUpdate = true; this.ray.geometry.computeBoundingSphere();
    this.head.position.set(0, 0, h);
    this.name.position.set(0, 0, h + 0.06);
    return pose;
  }
}
