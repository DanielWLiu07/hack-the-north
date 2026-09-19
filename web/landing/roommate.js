// roommate.js — the caretaker on the dashboard: "Where are my keys?" -> it goes and POINTS.
//
// Mounts a small canvas of its own into the container the dashboard leaves for it
// (<div id="roommate-stage" hidden>, under the search box) and listens to the three DOM events
// dash.js dispatches on window. It edits nothing of the dashboard's: one line loads it,
//   <script type="module" src="./roommate.js"></script>
// and until the container exists AND is about to scroll into view it costs one observer (robot.js,
// three's shaders and the stroke map are imported only then; on the hero page, never). data-caption="off" on the
// container drops the one line of text it writes (class roommate-caption, bottom left).
//
//   gitrl:point       { object_id, class, zone, pose{x,y,z}, job_id, state, executor }  -> notices, drives over if it
//                     has to, turns, raises the near arm at the object's real pose, glances back at you: "there"
//   gitrl:job         { job_id, state, progress }   -> the caption follows it; a terminal state lowers the arm
//   gitrl:room-state  { state: clean | dirty | conflict, changes[] }   -> calm / a double-take / a look each way
//
// HONESTY. This is an ILLUSTRATION of the plan, not telemetry of the machine. While the job's
// executor is "not_connected" the real robot has not moved, and the caption says "planned", never
// "done"; after that it only ever repeats the job's own state string. Where the real robot IS is
// not known to this page (the map beside it will draw that from the nav snapshot), so ours starts
// at the room's origin facing +X.
//
// FRAMES. The dashboard speaks the ROOM frame: world_z_up, X forward, Y left, Z up, metres.
// three here is +Y up, so room (x, y, z) -> (-y, z, -x), a proper rotation. The robot is built at
// scale 1: one unit on this stage is one metre, and its arm really is 0.53 m long, so whether it
// has to drive over before it can point is decided by geometry, not by taste.
//
// HOUSE RULES (the dashboard's): its own canvas, no post pass, the loop runs only while the stage
// is on screen and the tab is visible, prefers-reduced-motion gets stills (arm already raised).

const STAGE_ID = 'roommate-stage';
const TERMINAL = /^(done|succeeded|failed|cancelled|rejected)/;
const roomToStage = (p) => [-(p.y || 0), p.z || 0, -(p.x || 0)];
const c01 = (u) => Math.min(1, Math.max(0, u));
const ease = (a, b, x) => { const u = c01((x - a) / (b - a)); return u * u * (3 - 2 * u); };
const quintic = (u) => { u = c01(u); return u * u * u * (u * (u * 6 - 15) + 10); };
const quinticAccel = (u) => (u <= 0 || u >= 1 ? 0 : 120 * u * u * u - 180 * u * u + 60 * u);
const backOut = (u, k = 1.4) => { u = c01(u) - 1; return 1 + u * u * ((k + 1) * u + k); };
const wrap = (a) => Math.atan2(Math.sin(a), Math.cos(a));

let mounted = null;

async function mount(container) {
  const THREE = await import('three');
  const R = await import('./robot.js');
  const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true');
  const caption = document.createElement('div');
  caption.className = 'roommate-caption';
  caption.style.cssText = 'position:absolute;left:14px;bottom:10px;font:12px/1.4 ui-monospace,monospace;color:#8e8b86;pointer-events:none;letter-spacing:.02em';
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);                           // the page's own ground shows through

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 3, 0.05, 60);
  // HOME view: from the robot's front right (it starts facing room +X, which is stage -Z). The light is
  // keyed to this view once, in the world: the camera may swing later, the painted shadow stays put.
  const HOME = Math.atan2(0.5, -0.87), ELEV = 0.29;
  const place = (az, dist, look) => { camera.position.set(look.x + Math.sin(az) * dist, look.y + ELEV * dist, look.z + Math.cos(az) * dist); camera.lookAt(look); };
  place(HOME, 4.2, new THREE.Vector3(0, 0.78, 0));
  const keyDir = R.keyFor(camera), KEY_AZ = Math.atan2(keyDir.x, keyDir.z);
  scene.add(R.robotLights(keyDir));
  const rig = R.buildBracketBot();                               // scale 1: metres
  R.paintRobot(rig, { strokeMap: new URL('./textures/watercolor_normal-1024.webp', import.meta.url).href, strokes: 0.8, keyDir });
  scene.add(rig.root);

  // the floor it stands on (no claim about the room's shape: that is the map's job), and the thing it points at
  const ink = new THREE.LineBasicMaterial({ color: 0x26262e }), gold = new THREE.Color('#e0ac2a');
  const floor = new THREE.Group();
  for (const r of [0.6, 1.2, 1.8, 2.4]) {
    const pts = []; for (let i = 0; i <= 72; i++) pts.push(new THREE.Vector3(Math.cos(i / 72 * 6.2832) * r, 0, Math.sin(i / 72 * 6.2832) * r));
    floor.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), ink));
  }
  scene.add(floor);
  const mark = new THREE.Group(); mark.visible = false; scene.add(mark);
  const gem = new THREE.Mesh(new THREE.OctahedronGeometry(0.055, 0), new THREE.MeshBasicMaterial({ color: gold }));
  const drop = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, -1, 0)]), new THREE.LineBasicMaterial({ color: gold, transparent: true, opacity: 0.45 }));
  const foot = new THREE.Mesh(new THREE.RingGeometry(0.1, 0.122, 32).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial({ color: gold, side: THREE.DoubleSide, transparent: true, opacity: 0.7 }));
  mark.add(gem, drop, foot);

  // ---- state: where it stands, what it is doing ------------------------------------------
  const me = { x: 0, z: 0, yaw: Math.PI, rollL: 0, rollR: 0 };   // room origin, facing room +X (= stage -Z)
  let move = null;      // { x0, z0, yaw0, yawGo, x1, z1, yawEnd, t0, tTurn, tGo, tEnd }
  let point = null;     // { target, t0, armAt, releaseAt, detail }
  let fading = null;    // the previous gesture, easing out under a new one
  let mood = { kind: 'unknown', t0: -99, n: 0 };               // until the badge speaks, the stage says nothing about the room
  let now = 0, last = performance.now(), view = { look: new THREE.Vector3(0, 0.78, 0), dist: 4.2, az: HOME };
  const REACH = 0.85, STANDOFF = 0.62;                           // metres: further than REACH and it has to drive

  function say() {
    if (point) {
      const d = point.detail, planned = d.executor === 'not_connected';
      const what = [d.class || d.object_id, d.zone].filter(Boolean).join(' · ');
      caption.textContent = `${planned ? 'planned' : String(d.state || 'planned')} · pointing at ${what}`;
    } else caption.textContent = mood.kind === 'unknown' ? '' : mood.kind === 'clean' ? 'room at main' : mood.kind === 'conflict' ? 'merge conflict in the room' : `room drifted${mood.n ? ` · ${mood.n} change${mood.n === 1 ? '' : 's'}` : ''}`;
  }

  function onPoint(e) {
    const d = e.detail || {}; if (!d.pose) return;
    const target = roomToStage(d.pose);
    target[1] = Math.max(0.03, target[1]);
    mark.position.fromArray(target); mark.visible = true;
    drop.scale.y = target[1]; foot.position.y = -target[1] + 0.004;
    const dx = target[0] - me.x, dz = target[2] - me.z, dist = Math.hypot(dx, dz), face = Math.atan2(dx, dz);
    const t0 = now; let armAt = t0 + 0.45;
    move = null;
    if (dist > REACH) {                                           // too far to point from here: turn, drive over, stop short
      const go = dist - STANDOFF, tTurn = 0.35 + 0.35 * Math.abs(wrap(face - me.yaw)), tGo = 0.7 + 0.75 * go;
      move = { x0: me.x, z0: me.z, yaw0: me.yaw, yawGo: me.yaw + wrap(face - me.yaw), x1: me.x + dx / dist * go, z1: me.z + dz / dist * go,
        t0: t0 + 0.3, tTurn, tGo: Math.min(tGo, 3.2), go };
      armAt = move.t0 + move.tTurn + move.tGo - 0.15;
    } else {                                                      // close enough: turn until it is a comfortable reach off its nose
      const off = wrap(face - me.yaw), keep = Math.sign(off) * Math.max(0, Math.abs(off) - 0.5);
      if (Math.abs(keep) > 0.02) { move = { x0: me.x, z0: me.z, yaw0: me.yaw, yawGo: me.yaw + keep, x1: me.x, z1: me.z, t0: t0 + 0.25, tTurn: 0.4 + 0.4 * Math.abs(keep), tGo: 0, go: 0 }; armAt = move.t0 + move.tTurn - 0.1; }
    }
    if (point && now > point.armAt) fading = { target: point.target, from: now, w: point.releaseAt == null ? 1 : 1 - ease(0, 0.7, now - point.releaseAt) };
    point = { target, t0, armAt, releaseAt: null, holdUntil: armAt + 7, detail: d };
    if (reduced) { if (move) { me.x = move.x1; me.z = move.z1; me.yaw = move.yawGo; move = null; } point.armAt = now - 5; }
    say(); wake();
  }
  function onJob(e) {
    const d = e.detail || {}; if (!point || (d.job_id && point.detail.job_id && d.job_id !== point.detail.job_id)) return;
    point.detail = { ...point.detail, state: d.state, progress: d.progress, executor: d.executor ?? point.detail.executor };
    if (TERMINAL.test(String(d.state || '')) && point.releaseAt == null) point.releaseAt = Math.max(now, point.armAt + 1.6);
    say(); wake();
  }
  function onRoom(e) {
    const d = e.detail || {}, kind = d.state === 'dirty' || d.state === 'conflict' ? d.state : 'clean';
    if (kind !== mood.kind) mood = { kind, t0: now, n: Array.isArray(d.changes) ? d.changes.length : 0 };
    else mood.n = Array.isArray(d.changes) ? d.changes.length : mood.n;
    say(); wake();
  }

  // ---- pose: everything eased, nothing random ---------------------------------------------
  function pose(dt) {
    R.restPose(rig);
    let lean = 0.011 * Math.sin(now * 1.7 + 0.5);                 // it never stops balancing
    if (move) {
      const a = c01((now - move.t0) / move.tTurn), b = move.tGo ? c01((now - move.t0 - move.tTurn) / move.tGo) : 1;
      const yaw = move.yaw0 + (move.yawGo - move.yaw0) * quintic(a), g = quintic(b);
      const x = move.x0 + (move.x1 - move.x0) * g, z = move.z0 + (move.z1 - move.z0) * g;
      // wheels: exactly the distance rolled, and opposite ways through the turn
      const dRoll = Math.hypot(x - me.x, z - me.z) / R.BOT.wheelR, dSpin = wrap(yaw - me.yaw) * (R.BOT.track / 2) / R.BOT.wheelR;
      me.rollL += dRoll + dSpin; me.rollR += dRoll - dSpin;
      me.x = x; me.z = z; me.yaw = yaw;
      if (move.tGo) lean += 0.11 * quinticAccel(b) / 5.77 * Math.min(1, move.go / 0.8);   // into the motion, back to brake
      if (a >= 1 && b >= 1) move = null;
    }
    rig.root.position.set(me.x, 0, me.z); rig.root.rotation.set(0, me.yaw, 0);
    rig.wheelL.rotation.x = me.rollL; rig.wheelR.rotation.x = me.rollR;
    rig.body.rotation.x = lean;
    rig.root.updateMatrixWorld(true);

    // the head: you, by default. Calm: a slow look around the room now and then, never a snap.
    const aim = (p, w) => { const a = R.aimAngles(rig, p);
      rig.headPan.rotation.y += (Math.max(-1.45, Math.min(1.45, a.pan)) - rig.headPan.rotation.y) * w;
      rig.headTilt.rotation.x += (a.tilt - rig.headTilt.rotation.x) * w; };
    const viewer = camera.position.toArray();
    aim(viewer, 0.85);
    rig.headPan.rotation.y += 0.22 * Math.sin(now * 0.31) * Math.max(0, Math.sin(now * 0.13));
    const m = now - mood.t0;
    if (!point && mood.kind === 'dirty' && m < 2.6) {            // a double-take: away, back, away again and a lean in
      const w = ease(0, 0.25, m) - ease(0.5, 0.7, m) + ease(0.85, 1.05, m) - ease(2.0, 2.5, m);
      rig.headPan.rotation.y += 0.9 * w; rig.headTilt.rotation.x += 0.18 * w; rig.body.rotation.x += 0.035 * w;
    }
    if (!point && mood.kind === 'conflict' && m < 3.4) {         // two roommates, one lamp: a look each way, then at you, head cocked
      const l = ease(0, 0.35, m) - ease(0.9, 1.25, m), r = ease(1.0, 1.35, m) - ease(1.9, 2.3, m), q = ease(2.2, 2.6, m) - ease(3.0, 3.4, m);
      rig.headPan.rotation.y += 0.95 * l - 0.95 * r; rig.headTilt.rotation.z = 0.2 * q;
    }
    if (fading) { const w = fading.w * (1 - ease(0, 0.5, now - fading.from)); if (w > 0.001) R.pointAt(rig, fading.target, w); else fading = null; }
    if (point) {
      const notice = ease(0, 0.35, now - point.t0), tr = now - point.armAt;
      const lower = point.releaseAt == null ? 1 : 1 - ease(0, 0.7, now - point.releaseAt);
      const raise = (tr > 0 ? backOut(tr / 0.55) : 0) * lower;
      aim(point.target, notice * lower * (1 - c01(raise)));       // the head is there before the arm
      R.pointAt(rig, point.target, raise);
      const glance = (ease(1.3, 1.7, tr) - ease(2.6, 3.0, tr)) * lower;   // "there."
      if (glance > 0) { aim(viewer, glance); rig.headTilt.rotation.z = 0.16 * glance; }
      if (point.releaseAt == null && now > point.holdUntil) point.releaseAt = now;   // a plan nobody executes: it does not hold the pose for ever
      if (point.releaseAt != null && now - point.releaseAt > 0.8) { point = null; mark.visible = false; say(); }
    }
    gem.rotation.y = now * 0.8;

    // The camera keeps the robot and the thing it points at in one wide frame, and it looks at a
    // gesture from the SIDE: an arm pointed at the lens is a foreshortened stub. So while it points,
    // the view eases round to a perpendicular of robot->object (never more than 70 degrees from
    // HOME: the light is keyed to HOME), and eases back afterwards.
    const want = new THREE.Vector3(me.x, 0.78, me.z); let sep = 0, az = HOME;
    if (point) {
      const dx = point.target[0] - me.x, dz = point.target[2] - me.z, g = Math.atan2(dx, dz); sep = Math.hypot(dx, dz);
      want.lerp(new THREE.Vector3(point.target[0], 0.78, point.target[2]), 0.4);
      // Two side views exist (one each side of robot->object). Each is clamped to HOME +- 70 degrees, and a
      // clamped view may no longer BE a side view, so judge them after clamping: how side-on is it
      // (1 = pure profile)? Only if both are good does the light decide (the far side shows a robot in shadow).
      const clampOff = (o) => Math.max(-1.22, Math.min(1.22, o));
      const cand = [g + Math.PI / 2, g - Math.PI / 2].map((c) => { const v = HOME + clampOff(wrap(c - HOME));
        return { v, profile: Math.abs(Math.sin(v - g)), lit: Math.abs(wrap(v - KEY_AZ)) }; });
      const [p, q] = cand; az = (Math.abs(p.profile - q.profile) > 0.12 ? (p.profile > q.profile ? p : q) : (p.lit <= q.lit ? p : q)).v;
    }
    const k = reduced ? 1 : 1 - Math.exp(-2.2 * dt);
    view.look.lerp(want, k); view.dist += ((3.9 + 0.55 * sep) - view.dist) * k; view.az += wrap(az - view.az) * k;
    place(view.az, view.dist, view.look);
  }

  // ---- loop: only while it can be seen ------------------------------------------------------
  let onScreen = false, running = false;
  function frame() {
    const t = performance.now(), dt = Math.min(0.05, (t - last) / 1000); last = t; now += dt;
    pose(dt); renderer.render(scene, camera);
  }
  function wake() {
    const should = onScreen && !document.hidden;
    if (reduced) { if (should) { last = performance.now(); frame(); } return; }   // stills: one frame per event
    if (should !== running) { running = should; last = performance.now(); renderer.setAnimationLoop(running ? frame : null); }
  }
  function resize() {
    const w = container.clientWidth || 600, h = container.clientHeight || 280;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    if (!running) { last = performance.now(); frame(); }
  }
  const io = new IntersectionObserver((es) => { onScreen = es.some((x) => x.isIntersecting); wake(); }, { threshold: 0.05 });
  const ro = new ResizeObserver(resize);
  const onVis = () => wake();

  function attach(el) {
    container = el; el.append(canvas, caption); el.hidden = false;
    caption.style.display = el.dataset.caption === 'off' ? 'none' : '';      // the words are the dashboard's to keep or drop
    io.disconnect(); io.observe(el); ro.disconnect(); ro.observe(el); resize();
  }
  attach(container);
  pose(0);
  await R.warmUp(rig, renderer, camera, scene);                   // the compile hitch happens before it is ever seen moving
  say(); frame();
  addEventListener('gitrl:point', onPoint); addEventListener('gitrl:job', onJob); addEventListener('gitrl:room-state', onRoom);
  document.addEventListener('visibilitychange', onVis);
  return { attach, get container() { return container; }, rig, renderer, state: () => ({ me: { ...me }, point: point && { ...point }, mood: { ...mood }, running, reduced, now }),
    dispose() { renderer.setAnimationLoop(null); io.disconnect(); ro.disconnect(); document.removeEventListener('visibilitychange', onVis);
      removeEventListener('gitrl:point', onPoint); removeEventListener('gitrl:job', onJob); removeEventListener('gitrl:room-state', onRoom);
      canvas.remove(); caption.remove(); renderer.dispose(); } };
}

// The dashboard builds (and may rebuild) the container: follow it. But mount NOTHING until the
// stage is about to be seen: the same DOM exists on the hero page (/), inside a #dashboard that
// scene.js sets to display:none, and a hidden stage there would still cost a WebGL context and
// thirteen shader compiles on the page that has to stay light.
//
// What is watched is the stage's PARENT, not the stage: the stage starts `hidden`, and a
// display:none element never intersects anything, so watching it would wait for a reveal that
// only mounting performs. The parent is the search section, which is visible on the dashboard and
// display:none (with the whole dashboard) on the hero page — which is exactly the question being
// asked. 600 px of margin means it is ready by the time it is read.
let pending = null, watching = null;
const near = new IntersectionObserver((es) => { if (es.some((x) => x.isIntersecting)) { near.disconnect(); watching = null; start(); } }, { rootMargin: '600px' });
// ...and even then, not while the dashboard is still building itself. Mounting costs one task
// (geometry, one shader program, the stroke map) and the dashboard's own first render must not
// queue behind it: measured on /?info, the page has NO long task of its own, so whatever this
// module spends is the only thing between a judge and the content. requestIdleCallback waits for
// a free main thread, and its timeout stops that wait from being unbounded on a busy page.
const soon = (fn) => (typeof requestIdleCallback === 'function' ? requestIdleCallback(fn, { timeout: 1200 }) : setTimeout(fn, 200));
function start() {
  const el = document.getElementById(STAGE_ID);
  if (!el || pending) return;
  pending = new Promise((res) => soon(res)).then(() => mount(el)).then((m) => { mounted = m; window.gitrlRoommate = m; follow(); })
    .catch((e) => { console.warn('[roommate] not running:', e); el.hidden = true; }).finally(() => { pending = null; });
}
function follow() {
  const el = document.getElementById(STAGE_ID);
  if (!el) return;
  if (mounted) { if (mounted.container !== el) mounted.attach(el); return; }
  if (pending) return;
  const watch = el.parentElement || el;
  if (watching === watch) return;
  near.disconnect(); near.observe(watch); watching = watch;
}
new MutationObserver(follow).observe(document.documentElement, { childList: true, subtree: true });
follow();
