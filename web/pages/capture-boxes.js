// capture-boxes.js — on /robot, a box around each object THIS CAPTURE detected. Nothing else.
//
// THE RULE. A capture's boxes are its own detections and no others: what the room happens to hold at
// that commit is a different question, and drawing it over one capture's cloud puts boxes round things
// this capture never saw (a desk's objects over a hallway, an object carried forward from last week).
// A capture with no detections gets NO boxes — silence, not a guess.
//
// WHERE EACH HALF COMES FROM. GET /api/capture/<id> is the detections: one entry per object the capture
// saw, with each camera's own raw_x/y/z and confidence — that is room-observations, which only exists
// once a capture has been SCANNED. Empty (catalogued but never scanned, or scanned and found nothing)
// means no boxes, which is the honest answer either way. GET /api/object-map?instance&ref is the only
// source of SIZE: a detection knows where it was seen, the committed record knows how big it is. An
// object detected here but absent from that commit gets no box rather than an invented one.
//
// The centre is the capture's own measurement (the mean of its cameras' raw positions), not the
// committed pose: the cloud under it is this capture, so the box belongs where this capture saw it.
// `yaw` and extents come from the record, and yaw is an AXIS about the room's z (roomctl/state.py).
//
// WHAT IT ADDS TO THE PAGE. One Group, "object-boxes", in room-cloud.js's scene (it owns the canvas,
// camera, orbit and frame loop and hands them over as window.roomCloud). The page's scene is y-up and
// the room frame is z-up, so the group is turned once — rotation.x = -pi/2, the mapping room-cloud.js
// uses for its own points. While room-map.js has the fused map on, the page's own cloud is hidden and
// these boxes hide with it: two sets of boxes over two clouds in two frames is nonsense.
//
// CLICKING. A click that isn't a drag picks the nearest box; the card names the object, what saw it and
// how sure it was. A `room-object-selected` event carries the same for any other panel. Escape clears.
// The click also flies the camera to the thing: a scan of half a million points looks coarse only
// because the whole room has to fit on screen, and a metre away the same points read as an object.
// Escape, or a click on nothing, flies back to exactly the view the click started from.
import * as THREE from 'three';

const page = window.roomCloud;
const POLL_MS = 1500;               // the instance, the commit and the capture arrive after first paint
const HISTORY_MS = 20000;           // sha -> capture_id, refreshed for commits made while the page is open
const DRAG_PX = 4;                  // a pointer that moved more than this was orbiting, not picking
const CLICK_MS = 500;
const PICK_PAD = 0.02;              // m: the pick mesh is a little larger than the line box, for small things
const SELECTED = 0xffffff;
const DEFAULT_COLOUR = '#7ee787';

if (page) {
  const group = new THREE.Group();
  group.name = 'object-boxes';
  group.rotation.x = -Math.PI / 2;  // room (x, y, z) -> page (x, z, -y)
  page.scene.add(group);

  const picks = [];
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let selected = null, down = null, card = null, drawn = null, captureOf = new Map(), historyAt = 0, tally = null;

  const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

  function hud() {
    if (card) return card;
    card = document.createElement('div');
    card.id = 'object-box-card';
    card.style.cssText = 'position:absolute;left:16px;bottom:16px;z-index:6;display:none;max-width:320px;' +
      'padding:12px 14px;border:1px solid rgba(255,255,255,.14);border-radius:10px;' +
      'background:rgba(10,12,16,.86);backdrop-filter:blur(8px);color:#e7e9ee;' +
      'font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
    (document.querySelector('.viewer') || document.body).appendChild(card);
    return card;
  }

  function show(o) {
    const c = hud();
    const cams = (o.detection.cameras || []).filter((v) => v && v.camera);
    const conf = cams.map((v) => num(v.confidence)).filter((v) => v !== null);
    c.style.display = 'block';
    c.innerHTML =
      `<div style="font-size:15px;font-weight:600;margin-bottom:2px">${o.record.class || o.detection.class || 'unknown'}</div>` +
      `<div style="color:#8b93a3;font-family:ui-monospace,SFMono-Regular,monospace;font-size:12px">${o.detection.object_id}</div>` +
      `<div style="margin-top:8px">${(o.extents[0] * 100).toFixed(0)} × ${(o.extents[1] * 100).toFixed(0)} × ${(o.extents[2] * 100).toFixed(0)} cm<br>` +
      `at (${o.centre[0].toFixed(2)}, ${o.centre[1].toFixed(2)}, ${o.centre[2].toFixed(2)}) m in this capture</div>` +
      (cams.length ? `<div style="margin-top:6px;color:#8b93a3">seen by ${cams.map((v) => v.camera).join(', ')}` +
        (conf.length ? ` · confidence ${Math.max(...conf).toFixed(2)}` : '') + `</div>`
        : (o.note ? `<div style="margin-top:6px;color:#8b93a3">${o.detection.pixels || 0} px · ${o.note}</div>` : ''));
  }

  // Half a million points spread over a 5 x 8 m room sit about 2 px apart while the camera holds the
  // whole scan in view — the scan is not coarse, the viewpoint is. Clicking a box walks the camera in
  // to where those same points read as an object; Escape, or a click on nothing, walks it back out.
  const FLY_MS = 420;
  const FILL = 2.4;                   // camera distance = this many of the object's own radii
  let home = null, fly = null;

  const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);

  function tween(pos, target) {
    fly = { t0: performance.now(), pos, target,
            from: page.camera.position.clone(), aim: page.controls.target.clone() };
    page.wake?.();
  }

  function frameOn(mesh) {
    group.updateMatrixWorld(true);
    const centre = mesh.getWorldPosition(new THREE.Vector3());
    const e = mesh.userData.box.extents;
    const distance = Math.max(0.55, Math.hypot(e[0], e[1], e[2]) / 2 * FILL);
    const dir = page.camera.position.clone().sub(page.controls.target);
    if (dir.lengthSq() < 1e-6) dir.set(0, 0.4, 1);
    dir.normalize();
    if (dir.y < 0.25) { dir.y = 0.25; dir.normalize(); }     // never end up looking from under the floor
    if (!home) home = { pos: page.camera.position.clone(), aim: page.controls.target.clone() };
    tween(centre.clone().addScaledVector(dir, distance), centre);
  }

  function flyHome() {
    if (!fly && !home) return;
    if (home) tween(home.pos, home.aim);
    home = null;
  }

  function step() {
    if (!fly) return;
    const k = Math.min(1, (performance.now() - fly.t0) / FLY_MS), t = ease(k);
    page.camera.position.lerpVectors(fly.from, fly.pos, t);
    page.controls.target.lerpVectors(fly.aim, fly.target, t);
    if (k >= 1) fly = null;
    page.wake?.();
  }

  function select(mesh) {
    if (selected) selected.userData.line.material.color.setHex(selected.userData.colour);
    selected = mesh || null;
    if (!selected) { if (card) card.style.display = 'none'; page.wake?.(); return; }
    selected.userData.line.material.color.setHex(SELECTED);
    show(selected.userData.box);
    window.dispatchEvent(new CustomEvent('room-object-selected', { detail: selected.userData.box }));
    page.wake?.();
  }

  /** A line in the viewer saying what this capture detected — including when that is nothing. */
  function status(text) {
    if (!tally) {
      tally = document.createElement('div');
      tally.id = 'object-box-tally';
      tally.style.cssText = 'position:absolute;right:16px;bottom:16px;z-index:6;padding:7px 11px;' +
        'border:1px solid rgba(255,255,255,.14);border-radius:8px;background:rgba(10,12,16,.82);' +
        'color:#c3cad8;font:12.5px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;pointer-events:none';
      (document.querySelector('.viewer') || document.body).appendChild(tally);
    }
    tally.textContent = text;
  }

  function clear() {
    for (const m of picks) {
      m.userData.line.geometry.dispose();
      m.userData.line.material.dispose();
      m.geometry.dispose();
      m.material.dispose();
      group.remove(m.userData.line);
      group.remove(m);
    }
    picks.length = 0;
    selected = null;
    home = null; fly = null;                   // another commit's cloud: this one's close-up is not its close-up
    if (card) card.style.display = 'none';
  }

  function nameplate(text, colour, centre, top) {
    const font = 42, pad = 13;
    const c = document.createElement('canvas'), g = c.getContext('2d');
    g.font = `600 ${font}px -apple-system, BlinkMacSystemFont, sans-serif`;
    c.width = Math.ceil(g.measureText(text).width) + pad * 2; c.height = font + pad * 2;
    const g2 = c.getContext('2d');
    g2.font = `600 ${font}px -apple-system, BlinkMacSystemFont, sans-serif`;
    g2.fillStyle = 'rgba(9,11,15,.82)'; g2.fillRect(0, 0, c.width, c.height);
    g2.fillStyle = `#${colour.toString(16).padStart(6, '0')}`; g2.fillText(text, pad, font + pad * 0.7);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthTest: false }));
    sprite.scale.set((c.width / c.height) * 0.15, 0.15, 1);
    sprite.position.set(centre[0], centre[1], top + 0.22);
    sprite.renderOrder = 9;
    return sprite;
  }

  function draw(boxes) {
    clear();
    for (const b of boxes) {
      const colour = new THREE.Color(/^#[0-9a-f]{6}$/i.test(b.record.color || '') ? b.record.color : DEFAULT_COLOUR).getHex();
      const geo = new THREE.BoxGeometry(...b.extents);
      // a wireframe alone disappears into half a million points: give it a body, a ring on the
      // floor beneath it, and its name in the air above it
      const body = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
        color: colour, transparent: true, opacity: 0.26, depthWrite: false }));
      const line = new THREE.LineSegments(new THREE.EdgesGeometry(geo),
        new THREE.LineBasicMaterial({ color: colour, transparent: true, opacity: 1, depthTest: false }));
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(b.extents[0] + PICK_PAD, b.extents[1] + PICK_PAD, b.extents[2] + PICK_PAD),
        new THREE.MeshBasicMaterial({ color: colour, transparent: true, opacity: 0.001, depthWrite: false }));
      for (const n of [body, line, mesh]) {
        n.position.set(...b.centre);
        n.rotation.z = (num(b.record.pose?.yaw) || 0) * Math.PI / 180;
        n.renderOrder = 3;
        group.add(n);
      }
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.15, 0.175, 36),
        new THREE.MeshBasicMaterial({ color: colour, transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthTest: false }));
      ring.position.set(b.centre[0], b.centre[1], 0.004);
      ring.renderOrder = 2;
      group.add(ring);
      try {                                   // a label is a nicety; never let one cost the boxes
        group.add(nameplate(b.record.class || b.detection.class || 'object', colour, b.centre,
                            b.centre[2] + b.extents[2] / 2));
      } catch { /* no canvas: the box and its ring say enough */ }
      mesh.userData = { box: b, line, colour };
      picks.push(mesh);
    }
    page.wake?.();
  }

  /** Where THIS capture saw the object: the mean of its cameras' raw positions. */
  function seenAt(detection) {
    const pts = (detection.cameras || [])
      .map((v) => [num(v.raw_x), num(v.raw_y), num(v.raw_z)])
      .filter((p) => p.every((v) => v !== null));
    if (!pts.length) return null;
    return [0, 1, 2].map((i) => pts.reduce((s, p) => s + p[i], 0) / pts.length);
  }

  async function json(url) {
    const r = await fetch(url, { cache: 'no-store', headers: { accept: 'application/json' } });
    return r.ok ? r.json() : null;
  }

  async function captureFor(instance, sha) {
    if (Date.now() - historyAt > HISTORY_MS || !captureOf.has(sha)) {
      const h = await json(`/api/scene/${encodeURIComponent(instance)}/history`);
      historyAt = Date.now();
      captureOf = new Map();
      for (const c of (h?.commits || [])) {
        const id = c.capture_id;
        if (!id) continue;
        // the viewer selects a NODE, whose id is the capture when the node is a capture cloud and
        // the sha when it is a bare commit: index every name the selection can arrive under
        for (const key of [c.commit_sha, c.sha, c.id]) if (key) captureOf.set(key, id);
      }
    }
    return captureOf.get(sha) || null;
  }

  async function load() {
    const st = page.state || {};
    if (!st.instance || !st.sha) return;                      // the viewer hasn't settled on a room yet
    const key = `${st.instance}@${st.sha}`;
    if (key === drawn) return;

    const capture = await captureFor(st.instance, st.sha);
    if (!capture) { drawn = key; clear(); status('this commit has no capture'); return; }

    // FIRST, what the room has COMMITTED at this node: the objects the Objects tab lists and the
    // ones a diff or a merge talks about. Drawing anything else here makes the page argue with
    // itself — a panel saying 2 objects beside a scene showing 5.
    const room = await json(`/api/object-map?instance=${encodeURIComponent(st.instance)}&ref=${encodeURIComponent(st.sha)}`);
    const committed = (room?.objects || []).filter(o => o && o.pose && o.extents);
    if (committed.length) {
      drawn = key;
      draw(committed.map(o => ({
        detection: { object_id: o.object_id, cameras: [], class: o.class },
        record: { class: o.class, color: o.color, pose: { yaw: o.pose.yaw } },
        centre: [o.pose.x, o.pose.y, o.pose.z],
        extents: [Math.max(o.extents.x, 0.02), Math.max(o.extents.y, 0.02), Math.max(o.extents.z, 0.02)],
        note: o.zone ? `in ${o.zone}` : '',
      })));
      status(`${committed.length} object${committed.length === 1 ? '' : 's'} in the room here · click a box to go in, Esc to go back`);
      return;
    }

    // otherwise the capture's own sidecar: what perception detected in THIS capture, with sizes.
    // It needs no Elasticsearch and no commit, so a catalogued capture still shows its objects.
    const side = await json(`/api/scene/${encodeURIComponent(st.instance)}/${encodeURIComponent(capture)}.json`);
    const sidecar = [...(side?.floor_objects || []), ...(side?.objects || [])]
      .filter((o) => Array.isArray(o.centre) && Array.isArray(o.size_m));
    if (side && Number.isFinite(side.detected)) {
      drawn = key;
      status(sidecar.length ? `${sidecar.length} object${sidecar.length === 1 ? '' : 's'} detected in ${capture} · click a box to go in, Esc to go back`
                             : `nothing detected in ${capture}`);
      draw(sidecar.map((o) => ({
        detection: { object_id: o.class || 'object', cameras: [], pixels: o.pixels, class: o.class },
        record: { class: o.class, color: o.colour, pose: { yaw: o.yaw_deg || 0 } },
        centre: o.centre.map(Number),
        extents: o.size_m.map((v) => Math.max(Number(v), 0.01)),
        note: o.source === 'floor' ? 'floor path' : o.source,
      })));
      return;                                                 // detected: 0 draws nothing, as it should
    }

    const cap = await json(`/api/capture/${encodeURIComponent(capture)}`);
    const found = Array.isArray(cap?.objects) ? cap.objects : [];
    if (!found.length) { drawn = key; clear(); status(`nothing detected in ${capture}`); return; }

    // SIZE comes from the commit; a detection on its own has none. The room on screen is asked first;
    // a capture whose OWN commit lives in room.git (the synthetic desk scans) is sized from there
    // instead, which is where its objects were written.
    const map = await json(`/api/object-map?instance=${encodeURIComponent(st.instance)}&ref=${encodeURIComponent(st.sha)}`);
    let records = new Map((map?.objects || []).map((o) => [o.object_id, o]));
    if (![...records.keys()].some((id) => found.some((d) => d.object_id === id)) && cap.commit_sha) {
      const own = await json(`/api/object-map?ref=${encodeURIComponent(cap.commit_sha)}`);
      const fallback = new Map((own?.objects || []).map((o) => [o.object_id, o]));
      if ([...fallback.keys()].some((id) => found.some((d) => d.object_id === id))) records = fallback;
    }

    const boxes = [];
    for (const d of found) {
      const record = records.get(d.object_id);
      const e = record?.extents;
      const centre = seenAt(d) || (record?.pose ? [record.pose.x, record.pose.y, record.pose.z] : null);
      if (!record || !e || !centre) continue;                  // no size, or nowhere: no box invented
      boxes.push({ detection: d, record, centre, extents: [Math.max(e.x, 0.01), Math.max(e.y, 0.01), Math.max(e.z, 0.01)] });
    }
    drawn = key;
    draw(boxes);
    status(boxes.length ? `${boxes.length} object${boxes.length === 1 ? '' : 's'} detected in ${capture} · click a box to go in, Esc to go back`
                        : `nothing detected in ${capture}`);
  }

  page.canvas.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY, t: Date.now() }; });
  page.canvas.addEventListener('pointerup', (e) => {
    const d = down; down = null;
    if (!d || Date.now() - d.t > CLICK_MS) return;
    if (Math.abs(e.clientX - d.x) > DRAG_PX || Math.abs(e.clientY - d.y) > DRAG_PX) return;
    const rect = page.canvas.getBoundingClientRect();
    pointer.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, page.camera);
    const hit = group.visible ? raycaster.intersectObjects(picks, false)[0] : null;
    select(hit ? hit.object : null);
    if (hit) frameOn(hit.object); else flyHome();
  });
  addEventListener('keydown', (e) => { if (e.key === 'Escape' && selected) { select(null); flyHome(); } });

  page.onFrame(() => {                                         // the map owns the view while it is on
    step();
    const want = wanted && page.cloudVisible !== false;
    if (group.visible !== want) {
      group.visible = want;
      if (!want && selected) select(null);
    }
  });

  // The page's other box control lives in room-map.js and is hidden whenever no fused map exists,
  // which left a room with objects and no way to show them. This one stands on its own, and it
  // belongs with the other layers in Settings — room-settings.js has already built that fieldset
  // by the time this runs. Only if that group is missing does it float over the viewer.
  const layers = [...document.querySelectorAll('#settings-content fieldset')]
    .find(f => f.querySelector('legend')?.textContent === 'Scene layers');
  const toggle = document.createElement('label');
  toggle.className = 'voxel-toggle';
  if (!layers) {
    toggle.style.cssText = 'position:absolute;right:16px;top:16px;z-index:6;display:flex;gap:7px;align-items:center;' +
      'padding:7px 11px;border:1px solid rgba(255,255,255,.14);border-radius:8px;background:rgba(10,12,16,.82);' +
      'color:#c3cad8;font:12.5px -apple-system,BlinkMacSystemFont,sans-serif;cursor:pointer';
  }
  const tick = document.createElement('input');
  tick.type = 'checkbox';
  try { tick.checked = localStorage.getItem('gitirl-object-boxes') !== 'off'; } catch { tick.checked = true; }
  const label = document.createElement('span');
  label.textContent = 'object boxes';
  toggle.append(tick, label);
  (layers || document.querySelector('.viewer') || document.body).appendChild(toggle);
  let wanted = tick.checked;
  tick.addEventListener('change', () => {
    wanted = tick.checked;
    try { localStorage.setItem('gitirl-object-boxes', wanted ? 'on' : 'off'); } catch { /* private mode */ }
    if (!wanted && selected) select(null);
    page.wake?.();
  });

  load();
  setInterval(load, POLL_MS);
}
