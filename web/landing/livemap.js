// livemap.js — the robot on its own map, top-down, in the ROOM frame (world_z_up, metres; plan/roommate/03 §8).
//
//   GET /api/nav/snapshot   {pose{x, y, yaw}, status, path[[x, y]…], grid{res, bounds{xmin,xmax,ymin,ymax}, cells_b64[, nx, ny]},
//                            freshness{block_m, ages[[s…]…]}, map_gen, at, received_at, age_s, stale}
//   SSE `nav` (<= 2 Hz)     the same fields; usually the pose alone — the server keeps the map it belongs to
//
// The grid is roomctl/bb_nav.AreaMap's, byte for byte: (ny, nx) uint8, row 0 = ymin, col 0 = xmin, 1 floor · 2 obstacle ·
// 0 unknown. `pose.yaw` is RADIANS, counter-clockwise from +x (docs/20: a robot pose is (x, y, yaw rad)); a publisher
// that has degrees sends `pose.yaw_deg` instead. Anything that does not add up is NOT drawn, and the caption says why:
// a map that is guessed at is worse than no map. Nothing is drawn at all until a robot (or bbsim) has published.
const FRESH_S = 10;                                   // the server's NAV_FRESH_S: older than this is where the robot WAS
const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

let host = null, canvas = null, caption = null, snap = null, got = 0, queued = false, problem = null, cells = null;

function mount() {
  if (host) return host;
  const status = document.getElementById('status');
  const before = status && status.querySelector('.captures');
  if (!before) return null;
  canvas = document.createElement('canvas');
  canvas.setAttribute('role', 'img');
  caption = document.createElement('p');
  caption.className = 'livemap-cap mono';
  const h = document.createElement('h3');
  h.className = 'ledger-h';
  h.textContent = 'where the roommate is';
  host = document.createElement('div');
  host.className = 'livemap';
  host.hidden = true;
  host.append(h, canvas, caption);
  before.before(host);
  new ResizeObserver(() => draw()).observe(host);
  return host;
}

function decode(grid) {
  problem = null; cells = null;
  if (!grid || !grid.cells_b64) return;
  const b = grid.bounds || {}, res = +grid.res;
  if (!(res > 0) || ![b.xmin, b.xmax, b.ymin, b.ymax].every(Number.isFinite) || !(b.xmax > b.xmin) || !(b.ymax > b.ymin)) { problem = 'the map came without usable bounds or a cell size'; return; }
  const nx = Number.isInteger(grid.nx) ? grid.nx : Math.round((b.xmax - b.xmin) / res), ny = Number.isInteger(grid.ny) ? grid.ny : Math.round((b.ymax - b.ymin) / res);
  let raw;
  try { raw = atob(grid.cells_b64); } catch { problem = 'the map’s cells are not base64'; return; }
  if (raw.length !== nx * ny) { problem = `the map says ${nx} × ${ny} cells and ${raw.length} arrived — not drawn`; return; }
  // one pixel per cell, painted once per map and scaled up unsmoothed: no seams between cells, and a 3 cm map of a
  // whole room (tens of thousands of cells) costs nothing at 2 Hz. Row 0 is ymin, so the bitmap is written bottom-up.
  const bmp = document.createElement('canvas');
  bmp.width = nx; bmp.height = ny;
  const bg = bmp.getContext('2d'), img = bg.createImageData(nx, ny);
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    const v = raw.charCodeAt(j * nx + i);
    if (!v) continue;
    const o = ((ny - 1 - j) * nx + i) * 4;
    img.data[o] = 239; img.data[o + 1] = 236; img.data[o + 2] = 230; img.data[o + 3] = v === 2 ? 158 : 18;   // obstacle · floor
  }
  bg.putImageData(img, 0, 0);
  cells = { bmp, nx, ny, res, b };
}

function extent() {
  if (cells) return cells.b;
  const pts = [[snap.pose.x, snap.pose.y], ...(Array.isArray(snap.path) ? snap.path : [])].filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]));
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]), pad = 1;
  return { xmin: Math.min(...xs) - pad, xmax: Math.max(...xs) + pad, ymin: Math.min(...ys) - pad, ymax: Math.max(...ys) + pad };
}

function draw() {
  queued = false;
  if (!host || !snap || !snap.pose) return;
  const age = (Date.now() - got) / 1000 + (snap.age_s || 0), stale = age > FRESH_S;
  const e = extent(), wM = e.xmax - e.xmin, hM = e.ymax - e.ymin;
  const W = Math.max(240, Math.min(host.clientWidth || 320, 640)), H = Math.round(Math.min(380, Math.max(140, W * (hM / wM))));
  const k = Math.min(W / wM, H / hM), ox = (W - wM * k) / 2, oy = (H - hM * k) / 2, dpr = Math.min(devicePixelRatio || 1, 2);
  if (canvas.width !== W * dpr || canvas.height !== H * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.width = `${W}px`; canvas.style.height = `${H}px`; }
  const g = canvas.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  const X = (x) => ox + (x - e.xmin) * k, Y = (y) => H - oy - (y - e.ymin) * k;          // +y is up the page: the room frame, seen from above
  const ink = css('--ink', '#efece6'), accent = css('--accent', '#f2a03c'), clean = css('--led-clean', '#4fd37a');

  if (cells) {
    const { bmp, nx, ny, res, b } = cells;
    g.imageSmoothingEnabled = false;
    g.drawImage(bmp, X(b.xmin), Y(b.ymin + ny * res), nx * res * k, ny * res * k);
    const f = snap.freshness;                                                             // seconds since each block was last SEEN; -1 = never
    if (f && f.block_m > 0 && Array.isArray(f.ages)) {
      const bs = f.block_m * k;
      f.ages.forEach((row, j) => Array.isArray(row) && row.forEach((t, i) => {
        if (!(t >= 0)) return;
        const fresh = Math.max(0, 1 - t / 600);                                           // green fades out over ten minutes
        g.fillStyle = fresh > 0 ? `rgba(79, 211, 122, ${(0.13 * fresh).toFixed(3)})` : 'rgba(242, 160, 60, .10)';
        g.fillRect(X(b.xmin + i * f.block_m), Y(b.ymin + (j + 1) * f.block_m), bs, bs);
      }));
    }
  } else {
    g.strokeStyle = 'rgba(239, 236, 230, .12)'; g.strokeRect(0.5, 0.5, W - 1, H - 1);
  }

  const path = (Array.isArray(snap.path) ? snap.path : []).filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]));
  if (path.length > 1) {
    g.beginPath(); path.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(p[1])) : g.moveTo(X(p[0]), Y(p[1]))));
    g.strokeStyle = accent; g.lineWidth = 1.5; g.setLineDash([5, 4]); g.stroke(); g.setLineDash([]);
    const end = path[path.length - 1];
    g.beginPath(); g.arc(X(end[0]), Y(end[1]), 3.5, 0, 6.2832); g.fillStyle = accent; g.fill();
  }

  const yaw = Number.isFinite(snap.pose.yaw_deg) ? snap.pose.yaw_deg * Math.PI / 180 : +snap.pose.yaw || 0, r = 9;
  g.save(); g.translate(X(snap.pose.x), Y(snap.pose.y)); g.rotate(-yaw);                 // canvas y runs down the page
  g.beginPath(); g.moveTo(r * 1.7, 0); g.lineTo(-r * 0.8, r * 0.75); g.lineTo(-r * 0.3, 0); g.lineTo(-r * 0.8, -r * 0.75); g.closePath();   // the long end is the front
  if (stale) { g.strokeStyle = ink; g.globalAlpha = 0.55; g.lineWidth = 1.5; g.stroke(); } else { g.fillStyle = clean; g.fill(); }
  g.restore();

  const where = `x ${(+snap.pose.x).toFixed(2)} · y ${(+snap.pose.y).toFixed(2)} m`, t = age < 90 ? `${Math.round(age)} s ago` : `${Math.round(age / 60)} min ago`;
  caption.textContent = [stale ? `last heard ${t} — this is where it WAS` : (snap.status || 'publishing'), where,
    snap.map_gen != null ? `map ${snap.map_gen}` : null, problem || (cells ? null : 'no map yet: pose and path only')].filter(Boolean).join('  ·  ');
  caption.dataset.stale = stale ? '1' : '';
  canvas.setAttribute('aria-label', `The robot on its map: ${caption.textContent}`);
}
const redraw = () => { if (!queued) { queued = true; requestAnimationFrame(draw); } };

function take(next, whole) {
  const gen = snap && snap.map_gen;
  snap = whole ? next : { ...snap, ...next, age_s: 0 };
  got = Date.now();
  if (whole || next.grid) decode(snap.grid);
  if (!whole && next.map_gen != null && next.map_gen !== gen && !next.grid) { load(); return; }   // a new map: fetch it whole
  host.hidden = false;
  redraw();
}

async function load() {
  if (!mount()) { setTimeout(load, 400); return; }               // dash.js has not built the status section yet
  try {
    const r = await fetch('/api/nav/snapshot', { headers: { accept: 'application/json' } });
    if (!r.ok) return;                                           // 503: nothing is publishing. No robot is drawn.
    const j = await r.json();
    if (j && j.pose && Number.isFinite(+j.pose.x) && Number.isFinite(+j.pose.y)) take(j, true);
  } catch { /* offline: the rest of the dashboard says so */ }
}

load();
(function listen(tries) {
  const es = window.gitrlEvents;                                // the page's ONE stream (dash.js opens it)
  if (!es) { if (tries < 40) setTimeout(() => listen(tries + 1), 250); return; }
  es.addEventListener('nav', (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    if (!d || !d.pose || !Number.isFinite(+d.pose.x) || !Number.isFinite(+d.pose.y) || !mount()) return;
    if (!snap) { load(); return; }
    take(d, false);
  });
}(0));
setInterval(() => { if (snap && !document.hidden) redraw(); }, 1000);   // the age in the caption, and fresh -> "where it WAS"
