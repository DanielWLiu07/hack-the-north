// plyshot.worker.js — one commit's point cloud, turned into pixels, off the main thread and without a GPU.
//
// WHY NOT WEBGL. The dashboard is allowed exactly ONE WebGL context and the hero already spends it
// (tools/dev/framewatch.mjs hooks getContext before any page script and fails the build on a second
// one, hidden or not). A thumbnail of a point cloud does not need a GPU: it is a projection and a
// z-buffer, which is the forty lines below. Everything here runs in a worker, so a 6 MB capture
// never costs the page a frame.
//
// STREAMED, NOT BUFFERED. The cloud is rasterised AS IT ARRIVES — a point is read, projected and
// dropped in the same pass — so the worker's footprint is the framebuffer (a few hundred kB),
// not the file. That is what makes it safe to draw seven captures in a row.
//
// THE CAMERA IS GIVEN, NEVER DERIVED. The caller passes one basis for every node in the timeline:
// same angle, same metres-per-pixel, same centre. Nodes are only comparable — "what moved between
// these two frames" — if the camera did not move between them.

const POINT_BYTES = 15;                                   // float x y z + uchar r g b, tightly packed: NOT 4-byte aligned
const COLUMNS = ['float x', 'float y', 'float z', 'uchar red', 'uchar green', 'uchar blue'];   // pages/scene-model.js PLY_COLUMNS
const HEAD_MAX = 4096;

// The clouds are lit by a room's own light and land dark on a #0e0e12 panel. One gamma curve,
// precomputed, lifts them enough to read as a room without inventing colour that is not there.
const LIFT = new Uint8ClampedArray(256);
for (let i = 0; i < 256; i++) LIFT[i] = Math.round(255 * Math.pow(i / 255, 0.72) * 1.08);

const jobs = new Map();                                   // id -> AbortController

function parseHeader(bytes) {
  const text = new TextDecoder('latin1').decode(bytes.subarray(0, Math.min(bytes.length, HEAD_MAX)));
  const end = text.indexOf('end_header\n');
  if (end < 0) return bytes.length > HEAD_MAX ? { fail: 'the file does not begin with a PLY header' } : null;
  if (!text.startsWith('ply\n')) return { fail: 'the file does not begin with a PLY header' };
  const lines = text.slice(0, end).split('\n').map((l) => l.trim());
  if (!lines.includes('format binary_little_endian 1.0')) return { fail: `the PLY is "${lines[1] || '?'}"; this reads binary_little_endian 1.0` };
  const columns = lines.filter((l) => l.startsWith('property ')).map((l) => l.slice(9));
  if (columns.join('|') !== COLUMNS.join('|')) return { fail: `the PLY's columns are "${columns.join(', ')}"` };
  const count = lines.map((l) => /^element vertex (\d+)$/.exec(l)).find(Boolean);
  if (!count) return { fail: 'the PLY header names no "element vertex"' };
  return { n: +count[1], start: end + 'end_header\n'.length };
}

function makeTarget(w, h) {
  return { w, h, rgba: new Uint8ClampedArray(w * h * 4), z: new Float32Array(w * h).fill(Infinity), hit: 0 };
}

// One point: world -> the caller's basis -> a pixel, nearest wins. `splat` is the square a point
// covers; a 110k-point cloud in a 300 px card is sparse at 1 px and reads as static.
function raster(t, cam, x, y, z, r, g, b) {
  const px = x - cam.c[0], py = y - cam.c[1], pz = z - cam.c[2];
  const d = px * cam.d[0] + py * cam.d[1] + pz * cam.d[2];
  const u = px * cam.r[0] + py * cam.r[1] + pz * cam.r[2];
  const v = px * cam.u[0] + py * cam.u[1] + pz * cam.u[2];
  const sx = (u * cam.k + cam.ox) | 0, sy = (cam.oy - v * cam.k) | 0;
  if (!(sx >= 0) || !(sy >= 0) || sx >= t.w || sy >= t.h) return;                 // NaN fails both tests
  const shade = 1 - 0.42 * Math.min(1, Math.max(0, (d - cam.d0) / (cam.d1 - cam.d0 || 1)));
  const cr = LIFT[r] * shade, cg = LIFT[g] * shade, cb = LIFT[b] * shade;
  const s = cam.splat, x1 = Math.min(t.w, sx + s), y1 = Math.min(t.h, sy + s);
  for (let yy = sy; yy < y1; yy++) {
    for (let xx = sx; xx < x1; xx++) {
      const i = yy * t.w + xx;
      if (d >= t.z[i]) continue;
      if (t.z[i] === Infinity) t.hit++;
      t.z[i] = d;
      const o = i * 4;
      t.rgba[o] = cr; t.rgba[o + 1] = cg; t.rgba[o + 2] = cb; t.rgba[o + 3] = 255;
    }
  }
}

async function shoot({ id, url, w, h, cam }) {
  const ctl = new AbortController();
  jobs.set(id, ctl);
  try {
    const res = await fetch(url, { signal: ctl.signal, headers: { accept: 'application/octet-stream' } });
    if (!res.ok) throw Object.assign(new Error(`the server answered ${res.status} for that cloud`), { soft: res.status === 404 });
    if (!res.body) throw new Error('this browser cannot stream the response');
    const target = makeTarget(w, h);
    const reader = res.body.getReader();
    let head = null, tail = new Uint8Array(0), n = 0, bytes = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.length;
      let buf = value;
      if (tail.length) { const j = new Uint8Array(tail.length + value.length); j.set(tail); j.set(value, tail.length); buf = j; tail = new Uint8Array(0); }
      let at = 0;
      if (!head) {
        const got = parseHeader(buf);
        if (!got) { tail = buf; continue; }                                        // header not complete yet
        if (got.fail) throw Object.assign(new Error(got.fail), { soft: true });
        head = got; at = got.start;
      }
      const usable = at + Math.floor((buf.length - at) / POINT_BYTES) * POINT_BYTES;
      const view = new DataView(buf.buffer, buf.byteOffset, buf.length);
      for (let o = at; o < usable; o += POINT_BYTES, n++) {
        raster(target, cam, view.getFloat32(o, true), view.getFloat32(o + 4, true), view.getFloat32(o + 8, true),
          buf[o + 12], buf[o + 13], buf[o + 14]);
      }
      if (usable < buf.length) tail = buf.slice(usable);
    }
    if (!head) throw Object.assign(new Error('the cloud was empty'), { soft: true });
    postMessage({ id, ok: true, n, declared: head.n, bytes, coverage: target.hit / (w * h), pixels: target.rgba, w, h }, [target.rgba.buffer]);
  } catch (e) {
    if (e && e.name === 'AbortError') postMessage({ id, ok: false, aborted: true });
    else postMessage({ id, ok: false, reason: (e && e.message) || 'the cloud could not be read', soft: !!(e && e.soft) });
  } finally {
    jobs.delete(id);
  }
}

onmessage = (ev) => {
  const m = ev.data;
  if (!m) return;
  if (m.cancel != null) { jobs.get(m.cancel)?.abort(); return; }
  shoot(m);
};
