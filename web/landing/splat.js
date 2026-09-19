// splat.js — the Bracket Bot as a 3D Gaussian splat: the real machine, scanned.
//
//   const robot = await loadRobotSplat();     // a THREE.Group
//   scene.add(robot);
//   robot.position.x = ...; robot.rotation.y = ...;   // the choreography owns the transform
//
// The returned group is NORMALISED, so whoever drives it never sees COLMAP's frame:
// origin = the middle of the robot's footprint ON THE FLOOR, +Y is up, and it stands
// `height` world units tall. Position / rotation / scale on the group move the splat like
// any other Object3D; nothing else has to be called per frame.
//
// RENDERER. @mkkellogg/gaussian-splats-3d 0.4.7, vendored byte-for-byte from npm in
// vendor/gaussian-splats-3d/ (no CDN). Chosen because it declares three >= 0.160 and we
// ship r170; Spark 2.x, the other real candidate, needs three >= 0.180. Its DropInViewer is
// a THREE.Group that draws inside an ordinary renderer.render(scene, camera), which is what
// MangaPass needs: the splat lands in the pass's render target with everything else.
//
// Three settings here are load-bearing, not taste:
//   dynamicScene: true            the landing camera never moves, the ROBOT does. In static
//                                 mode the library only re-sorts when the camera turns, so a
//                                 robot that walks or yaws would keep a stale draw order.
//   sharedMemoryForWorkers: false neither serve.py nor web/server.py sends COOP/COEP, so
//                                 SharedArrayBuffer does not exist on these pages.
//   sphericalHarmonicsDegree: 0   view-dependent colour cannot survive a near-binary ink
//                                 pass; degree 0 is a quarter of the GPU memory.
//
// WHICH FILE. models/robot_splat.json names it (see the note in that file). While "file" is
// null the trained splat has not landed, and the COLMAP point cloud is converted, in the
// browser, into a real INRIA-format 3DGS .ply (one small round gaussian per point, sized by
// its neighbour spacing) and pushed through the SAME parser and renderer the real file will
// take. So the stand-in tests the plumbing, not a look-alike code path.

import * as THREE from 'three';
import * as GS from './vendor/gaussian-splats-3d/gaussian-splats-3d.module.js';

export const ROBOT_MANIFEST = './models/robot_splat.json';
const SH_C0 = 0.28209479177387814;            // 3DGS stores colour as (c - 0.5) / SH_C0

// ---- the stand-in: COLMAP points -> a 3DGS .ply in memory ---------------------------

// any binary-little-endian PLY with float x y z and (optionally) uchar red green blue
function readCloud(buf) {
  const head = new TextDecoder().decode(new Uint8Array(buf, 0, Math.min(buf.byteLength, 4096)));
  const end = head.indexOf('end_header\n');
  if (end < 0 || !head.includes('binary_little_endian')) throw new Error('splat: stand-in is not a binary little-endian PLY');
  const n = parseInt(/element vertex (\d+)/.exec(head)[1], 10);
  const SIZE = { char: 1, uchar: 1, int8: 1, uint8: 1, short: 2, ushort: 2, int16: 2, uint16: 2,
    int: 4, uint: 4, int32: 4, uint32: 4, float: 4, float32: 4, double: 8, float64: 8 };
  const at = {}; let stride = 0;
  for (const m of head.slice(0, end).matchAll(/property (\w+) (\w+)/g)) { at[m[2]] = stride; stride += SIZE[m[1]]; }
  const dv = new DataView(buf, end + 'end_header\n'.length);
  const pos = new Float32Array(n * 3), col = new Float32Array(n * 3).fill(0.75);
  const rgb = 'red' in at && 'green' in at && 'blue' in at;
  for (let i = 0; i < n; i++) {
    const o = i * stride;
    pos[i * 3] = dv.getFloat32(o + at.x, true);
    pos[i * 3 + 1] = dv.getFloat32(o + at.y, true);
    pos[i * 3 + 2] = dv.getFloat32(o + at.z, true);
    if (rgb) {
      col[i * 3] = dv.getUint8(o + at.red) / 255;
      col[i * 3 + 1] = dv.getUint8(o + at.green) / 255;
      col[i * 3 + 2] = dv.getUint8(o + at.blue) / 255;
    }
  }
  return { pos, col, n };
}

// distance from every point to its nearest neighbour, on a hash grid (22k points: ~10 ms)
function neighbourSpacing(pos, n) {
  let min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < n; i++) for (let a = 0; a < 3; a++) {
    const v = pos[i * 3 + a]; if (v < min[a]) min[a] = v; if (v > max[a]) max[a] = v;
  }
  const vol = Math.max(1e-9, (max[0] - min[0]) * (max[1] - min[1]) * (max[2] - min[2]));
  const cell = Math.cbrt(vol / n) * 1.5 || 1e-3;
  const key = (x, y, z) => `${x},${y},${z}`;
  const grid = new Map();
  const cx = new Int32Array(n), cy = new Int32Array(n), cz = new Int32Array(n);
  for (let i = 0; i < n; i++) {
    cx[i] = Math.floor((pos[i * 3] - min[0]) / cell);
    cy[i] = Math.floor((pos[i * 3 + 1] - min[1]) / cell);
    cz[i] = Math.floor((pos[i * 3 + 2] - min[2]) / cell);
    const k = key(cx[i], cy[i], cz[i]);
    (grid.get(k) || grid.set(k, []).get(k)).push(i);
  }
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let best = cell * cell * 4;               // nothing within the 27 cells: call it 2 cells
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
      const bucket = grid.get(key(cx[i] + dx, cy[i] + dy, cz[i] + dz));
      if (!bucket) continue;
      for (const j of bucket) {
        if (j === i) continue;
        const x = pos[j * 3] - pos[i * 3], y = pos[j * 3 + 1] - pos[i * 3 + 1], z = pos[j * 3 + 2] - pos[i * 3 + 2];
        const d = x * x + y * y + z * z;
        if (d < best && d > 0) best = d;
      }
    }
    out[i] = Math.sqrt(best);
  }
  return out;
}

// -> ArrayBuffer holding an INRIA 3DGS .ply: exactly the layout OpenSplat writes, minus the
// higher SH bands (f_rest_*), which the parser treats as optional
export function cloudToSplatPly(cloudBuf, { fatness = 0.5, opacity = 0.95 } = {}) {
  const { pos, col, n } = readCloud(cloudBuf);
  const spacing = neighbourSpacing(pos, n);
  const sorted = Float32Array.from(spacing).sort(), median = sorted[n >> 1] || 0.01;
  const FIELDS = ['x', 'y', 'z', 'nx', 'ny', 'nz', 'f_dc_0', 'f_dc_1', 'f_dc_2', 'opacity',
    'scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3'];
  const header = new TextEncoder().encode(['ply', 'format binary_little_endian 1.0',
    'comment stand-in: COLMAP points as gaussians (web/landing/splat.js)', `element vertex ${n}`,
    ...FIELDS.map((f) => `property float ${f}`), 'end_header', ''].join('\n'));
  const out = new ArrayBuffer(header.length + n * FIELDS.length * 4);
  new Uint8Array(out).set(header);
  const dv = new DataView(out, header.length);
  const logit = Math.log(opacity / (1 - opacity));
  for (let i = 0; i < n; i++) {
    // lone points would otherwise become balloons: never more than 3x the typical spacing
    const s = Math.log(Math.max(1e-5, Math.min(spacing[i], median * 3) * fatness));
    const v = [pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2], 0, 0, 0,
      (col[i * 3] - 0.5) / SH_C0, (col[i * 3 + 1] - 0.5) / SH_C0, (col[i * 3 + 2] - 0.5) / SH_C0,
      logit, s, s, s, 1, 0, 0, 0];             // rot = w x y z, identity
    for (let f = 0; f < v.length; f++) dv.setFloat32((i * FIELDS.length + f) * 4, v[f], true);
  }
  return out;
}

// ---- fit: COLMAP's arbitrary frame -> feet on the floor, +Y up, `height` tall ----------

// Robust bounds of the splat centres in the ORIENTED frame. Percentiles, not min/max: a
// trained splat always has a few floaters metres away, and one of them would shrink the
// robot to a dot.
function orientedBounds(splatBuffer, quat, trim) {
  const count = splatBuffer.getSplatCount(), step = Math.max(1, Math.floor(count / 60000));
  const m = Math.ceil(count / step), xs = new Float32Array(m), ys = new Float32Array(m), zs = new Float32Array(m);
  const c = new THREE.Vector3();
  let k = 0;
  for (let i = 0; i < count; i += step, k++) {
    splatBuffer.getSplatCenter(i, c);
    c.applyQuaternion(quat);
    xs[k] = c.x; ys[k] = c.y; zs[k] = c.z;
  }
  const range = (a) => { a = a.subarray(0, k).sort(); return [a[Math.floor((k - 1) * trim)], a[Math.ceil((k - 1) * (1 - trim))]]; };
  const [x0, x1] = range(xs), [y0, y1] = range(ys), [z0, z1] = range(zs);
  return new THREE.Box3(new THREE.Vector3(x0, y0, z0), new THREE.Vector3(x1, y1, z1));
}

// Which way does an upright Bracket Bot face? The AXLE is the long horizontal axis of whatever
// is near the floor (two wheels 0.27 heights apart, a drum between them); forward is
// perpendicular to it, on the side the arms are on. Measured on robot_upright.ply: axle at
// -46 degrees, arms at +25..29, so forward = +44; track / height = 0.24 against the real 0.27.
function facing(splatBuffer) {
  const n = splatBuffer.getSplatCount(), step = Math.max(1, Math.floor(n / 40000)), c = new THREE.Vector3(), P = [];
  for (let i = 0; i < n; i += step) { splatBuffer.getSplatCenter(i, c); P.push([c.x, c.y, c.z]); }
  const ys = Float32Array.from(P, (q) => q[1]).sort(), top = ys[Math.floor((ys.length - 1) * 0.995)];
  const low = P.filter((q) => q[1] < 0.12 * top);
  let mx = 0, mz = 0; for (const q of low) { mx += q[0]; mz += q[2]; } mx /= low.length; mz /= low.length;
  let sxx = 0, sxz = 0, szz = 0; for (const q of low) { const x = q[0] - mx, z = q[2] - mz; sxx += x * x; sxz += x * z; szz += z * z; }
  const axle = 0.5 * Math.atan2(2 * sxz, sxx - szz);                   // angle of the principal axis, from +x toward +z
  const ax = Math.cos(axle), az = Math.sin(axle);
  const along = Float32Array.from(low, (q) => (q[0] - mx) * ax + (q[2] - mz) * az).sort();
  const track = along[Math.floor(along.length * 0.99)] - along[Math.floor(along.length * 0.01)];
  let fx = -az, fz = ax, ahead = 0;                                     // perpendicular to the axle; the arms pick the sign
  for (const q of P) if (q[1] > 0.45 * top && q[1] < 0.85 * top && Math.hypot(q[0], q[2]) > 0.06 * top) ahead += q[0] * fx + q[2] * fz;
  if (ahead < 0) { fx = -fx; fz = -fz; }
  return { top, track, yaw: Math.atan2(fx, fz) };
}

const FORMAT = (name) => (/\.ksplat$/i.test(name) ? 'ksplat' : /\.splat$/i.test(name) ? 'splat' : 'ply');

/**
 * @param {object}  [opts]
 * @param {number}  [opts.height=2.4]     fitted height in world units (the landing frame is ~6 tall at z = 0)
 * @param {number[]} [opts.orient]        XYZ euler, radians; overrides the manifest's
 * @param {number}  [opts.pixelRatio=1]   the pixel ratio of the renderer that will draw it (scene.js runs at 1)
 * @param {boolean} [opts.standIn=false]  force the point-cloud stand-in even if a trained splat is installed
 * @param {string}  [opts.manifest]       another manifest url
 * @param {number}  [opts.alphaCutoff=6]  drop splats fainter than this (0-255) at load
 * @returns {Promise<THREE.Group>}        see the header comment; details in group.userData.splat
 */
export async function loadRobotSplat(opts = {}) {
  // resolved against the PAGE, as every other asset in this folder is ('./title/...')
  const manifestUrl = new URL(opts.manifest || ROBOT_MANIFEST, location.href);
  const manifest = await fetch(manifestUrl).then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
  const useStandIn = opts.standIn || !manifest.file;
  const name = useStandIn ? (manifest.standIn || 'robot_cloud.ply') : manifest.file;
  const fileUrl = new URL(name, manifestUrl);        // files sit beside the manifest
  const res = await fetch(fileUrl);
  if (!res.ok) throw new Error(`splat: ${fileUrl.pathname} -> HTTP ${res.status}`);
  let data = await res.arrayBuffer();
  const bytes = data.byteLength;
  if (useStandIn) data = cloudToSplatPly(data);

  const alphaCutoff = opts.alphaCutoff ?? 6;
  const format = useStandIn ? 'ply' : FORMAT(name);
  const splatBuffer = format === 'ksplat' ? await GS.KSplatLoader.loadFromFileData(data)
    : format === 'splat' ? await GS.SplatLoader.loadFromFileData(data, alphaCutoff, 0, true)
    : await GS.PlyLoader.loadFromFileData(data, alphaCutoff, 0, true, 0);
  data = null;

  const viewer = new GS.DropInViewer({
    dynamicScene: true,
    sharedMemoryForWorkers: false,
    gpuAcceleratedSort: false,
    sphericalHarmonicsDegree: 0,
    sceneRevealMode: GS.SceneRevealMode.Instant,   // entrances are the choreography's job, not a fade
    antialiased: false,                            // OpenSplat does not train with the AA kernel
    ignoreDevicePixelRatio: true,
    logLevel: GS.LogLevel.None,
  });
  // the library reads window.devicePixelRatio; what matters is the renderer that draws it
  const pr = opts.pixelRatio ?? 1;
  viewer.viewer.devicePixelRatio = pr;
  if (viewer.viewer.splatMesh) viewer.viewer.splatMesh.devicePixelRatio = pr;

  await viewer.viewer.addSplatBuffers([splatBuffer], [{ splatAlphaRemovalThreshold: alphaCutoff }], true, false, false);

  const robot = new THREE.Group();
  robot.name = 'robotSplat';
  const fit = new THREE.Group();
  fit.name = 'robotSplat.fit';
  fit.add(viewer);
  robot.add(fit);

  const state = {
    source: useStandIn ? 'stand-in' : 'splat', url: fileUrl.pathname, bytes, format,
    count: splatBuffer.getSplatCount(), height: opts.height ?? 2.4,
    orient: (opts.orient || manifest.orient || [Math.PI, 0, 0]).slice(0, 3),
    canonical: !useStandIn && !opts.orient && manifest.canonical === true,
    size: new THREE.Vector3(), viewer,
    // re-fit after changing the orientation or the height (the dev page's sliders)
    refit(orient = state.orient, height = state.height) {
      state.orient = orient.slice(0, 3); state.height = height;
      fit.quaternion.setFromEuler(new THREE.Euler(orient[0], orient[1], orient[2], 'XYZ'));
      if (manifest.canonical && !useStandIn) {
        // The asset's frame is BAKED (upright, feet on y = 0, mast axis on x = z = 0): nothing is
        // re-centred and orient stays as given. Only two things are left to do: scale it, and
        // find which way it FACES, because a balancer leans about its axle and nothing else.
        const f = facing(splatBuffer);
        const s = height / f.top;
        state.forwardYaw = f.yaw; state.track = f.track * s;
        fit.quaternion.premultiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), -f.yaw));
        fit.scale.setScalar(s); fit.position.set(0, 0, 0);
        return state.size.set(f.track * s, height, f.track * s);
      }
      const box = orientedBounds(splatBuffer, fit.quaternion, 0.01);
      const s = height / Math.max(1e-6, box.max.y - box.min.y);
      fit.scale.setScalar(s);
      // group matrix = T * R * S, and `box` is already in the R-rotated frame
      fit.position.set(-s * (box.min.x + box.max.x) / 2, -s * box.min.y, -s * (box.min.z + box.max.z) / 2);
      box.getSize(state.size).multiplyScalar(s);
      return state.size;
    },
    dispose() { robot.removeFromParent(); return viewer.dispose(); },
  };
  state.refit();
  robot.userData.splat = state;
  return robot;
}
