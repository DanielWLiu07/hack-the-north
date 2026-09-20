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

  const bar = el('div', 'ob-bar');
  const title = el('span', 'ob-title', 'OBJECTS');
  const count = el('span', 'ob-count', 'loading…');
  const hint = el('span', 'ob-hint', 'location ↔ geohash · a prefix is a region · click one to drill the octree');
  bar.append(title, count, hint);
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
      // split so the region reads as a region: 1 m box | down to 25 cm | the leaf
      key.append(el('b', 'ob-k3', o.voxel_key.slice(0, 3)),
                 el('span', 'ob-k5', o.voxel_key.slice(3, 5)),
                 el('span', 'ob-krest', o.voxel_key.slice(5)));
    } else {
      key.append(el('span', 'ob-nokey', 'outside the pinned cube · no key'));
    }

    btn.append(head, where, key);
    btn.onclick = () => {
      if (!o.voxel_key) { note.textContent = `${o.object_id} is outside the pinned octree cube, so it has no geohash to drill.`; return; }
      const said = drill(o.voxel_key.slice(0, 3));
      note.textContent = said
        ? `${o.object_id} · ${said} — the 1 m region its pose falls in. Full leaf key ${o.voxel_key}.`
        : `${o.object_id} · ${o.voxel_key} (the octree view is not on this page).`;
      for (const b of body.querySelectorAll('.ob-row')) b.classList.toggle('ob-sel', b === li);
    };
    li.append(btn);
    return li;
  }

  async function load() {
    let data;
    try {
      const r = await fetch('/api/object-map', { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(8000) });
      data = await r.json();
      if (!r.ok) throw new Error(data?.detail || `HTTP ${r.status}`);
    } catch (e) {
      count.textContent = 'unavailable';
      note.textContent = `Could not read the object map: ${e.message || e}`;
      return;
    }
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
    const cell = typeof c.cell_size_m === 'number' ? `${(c.cell_size_m * 100).toFixed(1)} cm` : '—';
    note.textContent = `${c.size_m ?? '—'} m cube at [${(c.origin || []).join(', ')}], ${c.levels ?? '—'} levels, `
      + `leaf ${cell}. Poses are ${data.units || 'metres'} in the ${data.frame || 'world'} frame, from `
      + `${(data.sha || '').slice(0, 7) || data.ref}.`
      + (data.outside_cube ? ` ${data.outside_cube} object(s) fall outside the cube and have no key.` : '');
  }

  load();
  // refresh when this tab is opened, so it is never stale after a commit
  const tab = document.querySelector('[data-panel="room-objects"]');
  if (tab) tab.addEventListener('click', () => { if (!host.hidden) load(); });
}
