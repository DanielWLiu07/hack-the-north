// room-objects.js — OBJECTS: every thing in the room, mapped to where it is.
//
// The room says "where" in two languages and this tab is the dictionary between them:
//
//   LOCATION   zone + metric pose (x, y, z in metres, world frame, floor at z=0) — what a
//              human and the arm use.
//   GEOHASH    the octree key of the cell that pose falls in — what Elasticsearch indexes.
//              A PREFIX IS A REGION: the first 3 digits name the 1 m box, 5 the 25 cm box,
//              and the full 8 the 3.1 cm leaf. That is why the key is drawn split.
//
// Source is GET /api/object-map: git for the objects, room.yaml's PINNED cube for the keys.
// Not Elasticsearch — this is the mapping a voxel query is built FROM, so reading it back out
// of the index would be circular. An object outside the pinned cube has no key and says so
// rather than being given a wrong one.
//
// Read-only. Clicking a row drives the octree view on this page to that object's region.

const host = document.getElementById('room-objects');
if (host) {
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const num = (v, d = 2) => (typeof v === 'number' && isFinite(v) ? v.toFixed(d) : '—');
  // a cell is a physical size; say it the way the octree rail on this page already says it
  // ("L6 · 12.5 cm", "L5 · 25 cm") — rounding 0.125 m to "13 cm" would name a rung that isn't one
  const cellLabel = (m) => (m >= 1 ? `${+m.toFixed(2)} m` : `${+(m * 100).toFixed(1)} cm`);

  const bar = el('div', 'ob-bar');
  const title = el('span', 'ob-title', 'OBJECTS');
  const count = el('span', 'ob-count', 'loading…');
  const where = el('span', 'ob-where-room', '…');
  const hint = el('span', 'ob-hint', 'location ↔ geohash · bold = the region that fits the object · click one to drill the octree');
  bar.append(title, count, where, hint);
  const body = el('div', 'ob-body');
  const note = el('p', 'ob-note');
  host.append(bar, body, note);

  // The octree controls that room-voxels.js owns. Present only on this page; absent is fine.
  function drill(prefix) {
    const box = document.getElementById('geohash-q');
    const form = document.getElementById('geohash-search');
    if (box && form) {
      box.value = prefix;
      form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
      return `octree → ${prefix}`;
    }
    const legacy = document.getElementById('voxel-prefix');
    if (legacy) { legacy.value = prefix; legacy.dispatchEvent(new Event('input', { bubbles: true })); return `prefix → ${prefix}`; }
    return null;
  }

  function row(o) {
    const li = el('li', 'ob-row');
    li.dataset.objectId = o.object_id;
    const btn = el('button');
    btn.type = 'button';

    const swatch = el('span', 'ob-swatch');
    // a colour from the record is data, never markup: set it as a property, and only when it looks like one
    if (typeof o.color === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(o.color)) swatch.style.backgroundColor = o.color;
    else swatch.classList.add('ob-swatch-none');

    const head = el('span', 'ob-head');
    head.append(swatch, el('span', 'ob-id', o.object_id || '—'), el('span', 'ob-zone', o.zone || 'no zone'));

    const p = o.pose || {};
    const where = el('span', 'ob-where',
      `${o.class || 'unknown'} · ${num(p.x)}, ${num(p.y)}, ${num(p.z)} m`);

    const key = el('span', 'ob-key');
    if (o.voxel_key) {
      // Split at the FITTED depth, so the bold part is the region the size of this object and
      // the dim tail is detail finer than the thing itself. Without a fit, show the whole key dim.
      const d = o.fit_depth || 0;
      key.append(el('b', 'ob-k3', o.voxel_key.slice(0, d)),
                 el('span', 'ob-krest', o.voxel_key.slice(d)));
      if (o.fit_cell_m) key.append(el('span', 'ob-cell', `  ${cellLabel(o.fit_cell_m)}`));
    } else {
      key.append(el('span', 'ob-nokey', 'outside the pinned cube · no key'));
    }

    btn.append(head, where, key);
    btn.onclick = () => {
      if (!o.voxel_key) { note.textContent = `${o.object_id} is outside the pinned octree cube, so it has no geohash to drill.`; return; }
      // drill to the region the size of the object, not a fixed rung — a 1 m box around a mug is
      // not "where the mug is". No extents recorded means no fit, so fall back to the leaf.
      const prefix = o.fit_key || o.voxel_key;
      const said = drill(prefix);
      const fitted = o.fit_cell_m
        ? `the ${cellLabel(o.fit_cell_m)} cell that fits it (longest side ${num(Math.max(...Object.values(o.extents || {0: 0})), 2)} m)`
        : 'its leaf cell — no extents recorded, so no region could be fitted';
      note.textContent = said
        ? `${o.object_id} · ${said} — ${fitted}. Full leaf key ${o.voxel_key}.`
        : `${o.object_id} · ${prefix} (the octree view is not on this page).`;
      for (const b of body.querySelectorAll('.ob-row')) b.classList.toggle('ob-sel', b === li);
    };
    li.append(btn);
    return li;
  }

  // WHICH ROOM. A scene instance is its own git repo; room.git is a different room entirely.
  // The viewer and the History graph show the current instance, so this tab must ask for the same
  // one — otherwise it honestly lists objects nobody is looking at.
  async function currentInstance() {
    // ?instance= wins, then whatever the viewer actually settled on, and only then the server's
    // default. Without this the tab lists room.git's or the default room's objects while the
    // viewer draws another room's cloud — two rooms on one page, which is how "0 objects" gets
    // shown next to a scene that plainly has some.
    const asked = new URL(location.href).searchParams.get('instance');
    if (asked) return asked;
    const shown = window.roomCloud?.state?.instance;
    if (shown) return shown;
    try {
      const r = await fetch('/api/scene/instances', { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(6000) });
      const d = await r.json();
      return r.ok ? (d.current || null) : null;
    } catch { return null; }
  }

  // WHICH NODE. The History graph marks its selected commit with aria-current; following that
  // attribute keeps the two tabs on the same commit without reaching into room-cloud.js.
  const selectedRef = () =>
    document.querySelector('#room-history button[data-sha][aria-current="true"]')?.dataset.sha || 'HEAD';

  let inFlight = 0;
  async function load(instance, ref) {
    const seq = ++inFlight;
    if (instance === undefined) instance = await currentInstance();
    if (ref === undefined) ref = selectedRef();
    let data;
    try {
      const q = new URLSearchParams({ ref: ref || 'HEAD' });
      if (instance) q.set('instance', instance);
      const r = await fetch(`/api/object-map?${q}`, { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(8000) });
      data = await r.json();
      if (!r.ok) {
        if (seq !== inFlight) return;
        // A capture with no commit is not a failure — the graph shows both, and only a commit
        // has an object tree. Say that, rather than leaving the last room's list on screen.
        body.textContent = '';
        count.textContent = data?.error === 'capture_not_committed' ? 'not committed' : 'unavailable';
        where.textContent = `${instance || 'room.git'} · ${ref}`;
        note.textContent = data?.detail || `HTTP ${r.status}`;
        return;
      }
    } catch (e) {
      if (seq !== inFlight) return;
      count.textContent = 'unavailable';
      note.textContent = `Could not read the object map: ${e.message || e}`;
      return;
    }
    if (seq !== inFlight) return;          // a newer selection already won
    body.textContent = '';
    const objects = Array.isArray(data.objects) ? data.objects : [];
    count.textContent = `${objects.length} object${objects.length === 1 ? '' : 's'}`;

    const zones = new Map();
    for (const o of objects) {
      const z = o.zone || 'no zone';
      if (!zones.has(z)) zones.set(z, []);
      zones.get(z).push(o);
    }
    if (!objects.length) body.append(el('p', 'ob-empty', 'No objects at this commit.'));
    for (const [zone, list] of zones) {
      body.append(el('h3', 'ob-zone-head', `${zone} · ${list.length}`));
      const ol = el('ol', 'ob-list');
      for (const o of list) ol.append(row(o));
      body.append(ol);
    }

    const c = data.cube || {};
    const cell = typeof c.cell_size_m === 'number' ? `${+(c.cell_size_m * 100).toFixed(1)} cm` : '—';
    note.textContent = `${data.source || 'room.git'} at ${(data.sha || '').slice(0, 7) || data.ref}. `
      + `${c.size_m ?? '—'} m cube at [${(c.origin || []).join(', ')}], ${c.levels ?? '—'} levels, leaf ${cell}. `
      + `Poses are ${data.units || 'metres'} in the ${data.frame || 'world'} frame.`
      + (data.outside_cube ? ` ${data.outside_cube} object(s) fall outside the cube and have no key.` : '');
    // say WHICH ROOM in the header too: the old bug was invisible precisely because nothing did
    where.textContent = `${data.instance || 'room.git'} · ${(data.sha || '').slice(0, 7)}`;
  }

  load();

  // Follow the History graph's selection. It marks the selected node with aria-current, so watching
  // that attribute keeps both tabs on one commit with no coupling to room-cloud.js internals.
  const history = document.getElementById('room-history');
  if (history) {
    let pending = 0;
    new MutationObserver(() => { clearTimeout(pending); pending = setTimeout(() => load(), 120); })
      .observe(history, { subtree: true, attributes: true, attributeFilter: ['aria-current'] });
  }
  // refresh when this tab is opened, so it is never stale after a commit
  const tab = document.querySelector('[data-panel="room-objects"]');
  if (tab) tab.addEventListener('click', () => { if (!host.hidden) load(); });
  // let other modules point this tab at a room/commit directly
  window.gitspaceObjects = { show: (instance, ref) => load(instance, ref) };
}
