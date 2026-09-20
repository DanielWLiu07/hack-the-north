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
    if (card) card.style.display = 'none';
  }

  function draw(boxes) {
    clear();
    for (const b of boxes) {
      const colour = new THREE.Color(/^#[0-9a-f]{6}$/i.test(b.record.color || '') ? b.record.color : DEFAULT_COLOUR).getHex();
      const line = new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.BoxGeometry(...b.extents)),
        new THREE.LineBasicMaterial({ color: colour, transparent: true, opacity: 0.95, depthTest: false }));
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(b.extents[0] + PICK_PAD, b.extents[1] + PICK_PAD, b.extents[2] + PICK_PAD),
        new THREE.MeshBasicMaterial({ color: colour, transparent: true, opacity: 0.07, depthWrite: false }));
      for (const n of [line, mesh]) {
        n.position.set(...b.centre);
        n.rotation.z = (num(b.record.pose?.yaw) || 0) * Math.PI / 180;
        n.renderOrder = 3;
        group.add(n);
      }
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
        if (id) captureOf.set(c.commit_sha || c.sha, id);
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

    // FIRST, the capture's own sidecar: <capture>.json beside its PLY, what perception detected in
    // THIS capture, with sizes. It needs no Elasticsearch and no commit, so a capture that was only
    // catalogued still shows its own objects. Without one, fall back to the indexed detections.
    const side = await json(`/api/scene/${encodeURIComponent(st.instance)}/${encodeURIComponent(capture)}.json`);
    const sidecar = [...(side?.floor_objects || []), ...(side?.objects || [])]
      .filter((o) => Array.isArray(o.centre) && Array.isArray(o.size_m));
    if (side && Number.isFinite(side.detected)) {
      drawn = key;
      status(sidecar.length ? `${sidecar.length} object${sidecar.length === 1 ? '' : 's'} detected in ${capture} · click a box`
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
    status(boxes.length ? `${boxes.length} object${boxes.length === 1 ? '' : 's'} detected in ${capture} · click a box`
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
  });
  addEventListener('keydown', (e) => { if (e.key === 'Escape' && selected) select(null); });

  page.onFrame(() => {                                         // the map owns the view while it is on
    const want = page.cloudVisible !== false;
    if (group.visible !== want) {
      group.visible = want;
      if (!want && selected) select(null);
    }
  });

  load();
  setInterval(load, POLL_MS);
}
