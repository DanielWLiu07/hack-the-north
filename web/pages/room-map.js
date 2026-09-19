// room-map.js — the REAL map on the Room page (/robot): the robot's own fused map as solid 3 cm cells, the camera's dense
// layer over it, every object, wall and floor find as a box, and the robot where it stands and faces — drawn INTO the
// page's existing three.js scene (room-cloud.js owns the canvas, the camera, the orbit and the frame loop; it hands them
// over as window.roomCloud). The objects are scene-model.js's, the same ones /scene draws; only the wiring is here.
//
// WHAT IT DOES TO THE PAGE. It adds one Group, "real-map". The page's scene is y-up; a map is z-up; the group is turned
// once (rotation.x = -pi/2) so map (x, y, z) lands on (x, z, -y), the same mapping room-cloud.js uses for its own points.
// It adds a "Real map" checkbox beside "Show Elastic octree" in Settings, ON by default when a map exists. While it is
// on, the page's own point cloud, grid and bounds are hidden — they are a capture in the ROBOT's frame, and two rooms in
// two frames on one floor is nonsense — and the page's robot splat, when it has one, is stood at the MAP's robot pose,
// which is the whole point of the page. Off, everything is put back. The Elasticsearch octree is untouched either way.
// No map (no instance has one, or the server is not this laptop): nothing is drawn, the checkbox stays hidden, no error.
//
// It polls /api/scene/{instance}/captures every 5 s and swaps in a newer map by itself, like /scene, so typing
// `python scripts/room_live.py add` fills this page too.
import * as THREE from 'three';
import { Layer, Boxes, Robot, readPly, thingsOf, makeShared, fitShared, RAMP_Z } from './scene-model.js';

const POLL_MS = 5000, PREF = 'gitirl-room-map-v1';
const page = window.roomCloud;                // room-cloud.js runs first (robot.html script order); no page scene, no map
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString() : '?');

let group, shared, cloud, dense, boxes, robot, toggle, box, text;
let instance = '', shown = '', framed = false, on = true, loading = null, pollTimer = 0, polls = 0, meta = null, pose = null;
const hidden = new Map();                     // child -> what its `visible` was before this module hid it (restore puts back exactly that)
let pageRobot = null;                        // the page's own splat (room-cloud.js robotSplat), when it has loaded one
let pagePoints = null;                       // the page's own cloud, last seen: a new one means it loaded and re-framed its camera

function build() {
  group = new THREE.Group(); group.name = 'real-map'; group.rotation.x = -Math.PI / 2; group.visible = false;
  shared = makeShared(); shared.uZ.value.set(...RAMP_Z.map);
  cloud = new Layer(shared, 0.033, 0.03); cloud.mode = 'cubes';
  dense = new Layer(shared, 0.010, 0.010);
  boxes = new Boxes(); robot = new Robot();
  group.add(cloud.group, dense.group, boxes.group, robot.group);
  page.scene.add(group);
  // the "Real map" line in Settings > Scene layers, right after the octree's, in its clothes
  toggle = document.createElement('label'); toggle.className = 'voxel-toggle map-toggle'; toggle.hidden = true;
  box = document.createElement('input'); box.type = 'checkbox'; box.id = 'enable-map';
  text = document.createElement('span'); text.textContent = ' Real map';
  toggle.append(box, text);
  const octree = document.querySelector('#enable-voxels');
  if (octree && octree.closest('label')) octree.closest('label').after(toggle); else document.body.append(toggle);
  try { on = localStorage.getItem(PREF) !== 'off'; } catch (e) { on = true; }
  box.checked = on;
  box.addEventListener('change', () => { on = box.checked; try { localStorage.setItem(PREF, on ? 'on' : 'off'); } catch (e) { /* private mode */ } apply(); });
  // sized with the page's canvas; the page's Settings (render quality) change the pixel ratio through the same path
  const fit = () => { fitShared(shared, Math.max(1, page.canvas.clientHeight), page.camera.fov, page.renderer.getPixelRatio()); page.wake(); };
  new ResizeObserver(fit).observe(page.canvas); window.addEventListener('room:settings', fit); fit();
  page.onFrame(eachFrame);
  for (const id of ['perspective', 'reset', 'top']) document.getElementById(id)?.addEventListener('click', () => { if (on && shown) frame(id === 'top'); });
}

// ── the page's own things, while the real map is on ─────────────────────────────
const yUp = (x, y, z, into) => into.set(x, z, -y);        // map frame -> the page's scene
const v3 = new THREE.Vector3();

function eachFrame() {                        // once per drawn frame, after the page's own update: cheap, ~10 children
  if (!on || !shown) return;
  let reframe = false;
  for (const c of page.scene.children) {
    if (c === group) continue;
    if (c.isPoints || c.type === 'GridHelper' || c.type === 'Box3Helper' || (c.isGroup && !c.name && c.children.some((k) => k.geometry && k.geometry.type === 'CircleGeometry'))) {   // its capture cloud, grid, bounds, pose marker
      if (!hidden.has(c)) hidden.set(c, c.visible);
      c.visible = false;
    }
    if (c.isPoints && c !== pagePoints) { pagePoints = c; reframe = true; }       // the page just mounted a cloud and framed ITS box: frame the map again
    if (c.name === 'robotSplat') { pageRobot = c; standSplat(c); }
  }
  if (reframe && framed) setTimeout(() => frame(false), 0);
  const hide = Boolean(pageRobot && pageRobot.visible);    // the splat IS the robot: keep only the arrow and the name from the marker
  robot.lines.visible = robot.head.visible = !hide; robot.ray.visible = !hide && robot.ray.visible;
}
function standSplat(splat) {                  // the page's splat at the map's robot pose. Canonical splat faces +Z (scene); the map's yaw is CCW from +x
  if (!pose) return;
  splat.position.set(pose.x, 0, -pose.y);
  splat.rotation.set(0, pose.yaw + Math.PI / 2, 0);
  splat.visible = true;
}
function restore() {                          // off: the page's things come back AS THEY WERE — a thing the page itself hid stays hidden
  for (const [c, was] of hidden) c.visible = was;
  hidden.clear();
}

function apply() {
  group.visible = on && Boolean(shown);
  toggle.hidden = !shown;
  if (!on) restore();
  page.wake();
}

// ── where to look from: the whole map from above and behind the robot, like /scene ──
function frame(top = false) {
  if (!meta) return;
  const b = meta.bounds_m, mx = (b.min[0] + b.max[0]) / 2, my = (b.min[1] + b.max[1]) / 2;
  const r = Math.hypot(b.max[0] - b.min[0], b.max[1] - b.min[1]) / 2;
  let dx = pose.x - mx, dy = pose.y - my, d = Math.hypot(dx, dy);
  if (d < 0.3) { dx = -Math.cos(pose.yaw); dy = -Math.sin(pose.yaw); d = 1; }
  const back = Math.max(d + 1.5, 1.25 * r);
  const cam = page.camera, controls = page.controls;
  yUp(mx, my, 0.3, controls.target);
  if (top) yUp(mx + dx / d * 0.05, my + dy / d * 0.05, 0.3 + 2.4 * r, cam.position);
  else yUp(mx + dx / d * back, my + dy / d * back, 0.3 + 1.05 * back, cam.position);
  cam.up.set(0, 1, 0); cam.lookAt(controls.target); controls.sync();
  const say = document.querySelector('#view-label'); if (say) say.textContent = top ? 'Real map / top / metres' : 'Real map / drag to orbit';
  page.wake();
}

// ── the map: which one, and loading it ──────────────────────────────────────────
async function getJSON(url, signal) {
  const r = await fetch(url, { cache: 'no-store', signal });
  if (!r.ok) throw new Error(`${url} -> HTTP ${r.status}`);
  return r.json();
}
const keyOf = (c) => `${c.capture_id}@${c.written_ms}${c.sidecar ? '+json' : ''}${c.dense_points ? '+dense' : ''}`;

async function pickInstance() {               // room_live.py's current instance if it has a map, else the one with the newest map
  const doc = await getJSON('/api/scene/instances');
  const withMaps = (doc.instances || []).filter((i) => i.maps).sort((a, b) => b.newest_ms - a.newest_ms);
  const current = withMaps.find((i) => i.current);
  return (current || withMaps[0] || {}).name || '';
}

async function load(c) {
  if (loading) loading.abort();
  const mine = loading = new AbortController(), base = `/api/scene/${instance}/${c.capture_id}`;
  try {
    text.textContent = ` Real map · loading ${c.capture_id.slice(4)}`;
    const [ply, side, denseBuf] = await Promise.all([
      fetch(`${base}.ply`, { cache: 'no-store', signal: mine.signal }).then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(`map HTTP ${r.status}`)))),
      c.sidecar ? getJSON(`${base}.json`, mine.signal).catch(() => null) : null,
      c.dense_points ? fetch(`${base}.dense.ply`, { cache: 'no-store', signal: mine.signal }).then((r) => (r.ok ? r.arrayBuffer() : null)).catch(() => null) : null,
    ]);
    if (mine.signal.aborted) return;
    const { n, bounds } = readPly(new Uint8Array(ply), cloud);
    cloud.cell = side && Number.isFinite(side.voxel_m) && side.voxel_m > 0 ? side.voxel_m : 0.03;
    cloud.show('cubes');
    let dn = 0;
    if (denseBuf) { try { dn = readPly(new Uint8Array(denseBuf), dense).n; } catch (e) { console.warn('[room-map] dense layer:', e.message); } }
    dense.n = dn; dense.show('points', dn > 0);
    meta = { ...(side || {}), bounds_m: side && side.bounds_m && side.bounds_m.min && side.bounds_m.max ? side.bounds_m : (bounds ? { min: [...bounds.min, 0], max: [...bounds.max, 1.3] } : { min: [-3, -3, 0], max: [3, 3, 1.3] }) };
    pose = robot.place(c.robot, null);
    shared.uRobot.value.set(pose.x, pose.y);
    const things = thingsOf(side); boxes.draw(things);
    shown = keyOf(c);
    text.textContent = ` Real map · ${c.at ? new Date(c.at).toLocaleTimeString([], { hour12: false }) : c.capture_id.slice(4)} · ${num(n)} cells${dn ? ` + ${num(dn)} dense` : ''} · ${num(things.filter((t) => t.kind !== 'wall').length)} objects${c.localized === false ? ' · SLAM NOT localized' : ''}`;
    apply();
    if (!framed) { framed = true; if (on) frame(false); }
    page.wake();
  } catch (e) {
    if (e.name !== 'AbortError') {
      console.warn('[room-map] map did not load:', e.message);
      text.textContent = ` Real map · ${c.capture_id.slice(4)} did not load (${e.message})`;
    }
  } finally {
    if (loading === mine) loading = null;
  }
}

async function poll() {
  clearTimeout(pollTimer);
  try {
    if (!document.hidden) {
      if (!instance || ++polls % 6 === 0) instance = (await pickInstance()) || instance;
      if (instance) {
        const doc = await getJSON(`/api/scene/${instance}/captures`);
        const newest = (doc.captures || []).find((c) => c.kind === 'map' && c.complete);
        if (newest && keyOf(newest) !== shown && !loading) await load(newest);
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') console.warn('[room-map] poll:', e.message);      // the server is not this laptop, or is down: the page is still the page
  }
  pollTimer = setTimeout(poll, POLL_MS);
}

if (page && page.scene && page.camera && page.canvas && page.renderer && page.controls) {
  try { build(); poll(); } catch (e) { console.warn('[room-map] not started:', e.message); }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
}
window.roomMap = { get state() { return { on, instance, shown, cells: cloud ? cloud.n : 0, dense: dense ? dense.n : 0, boxes: boxes ? boxes.things.length : 0, pose, framed, splat: Boolean(pageRobot) }; } };
