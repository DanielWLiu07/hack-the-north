// cloud-view.js — mount one capture's point cloud into a host element (the capture page, telemetry).
// Reads the same PLY layout room_live.write_scene writes (float x y z + uchar rgb, z up). The bytes
// live behind scene_api's local-only lock: a 403 is said in words, never drawn as an empty room.
import * as THREE from 'three';

const PLY_COLUMNS = ['float x', 'float y', 'float z', 'uchar red', 'uchar green', 'uchar blue'];
const POINT_BYTES = 15;

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(2)) if (kid != null && kid !== false) n.append(kid.nodeType ? kid : String(kid));
  return n;
}

function readPly(bytes) {
  const head = new TextDecoder('latin1').decode(bytes.subarray(0, Math.min(bytes.length, 4096)));
  const end = head.indexOf('end_header\n');
  if (!head.startsWith('ply\n') || end < 0) throw new Error('the file does not begin with a PLY header');
  const lines = head.slice(0, end).split('\n').map((l) => l.trim());
  if (!lines.includes('format binary_little_endian 1.0')) throw new Error(`the PLY is "${lines[1] || '?'}"`);
  const columns = lines.filter((l) => l.startsWith('property ')).map((l) => l.slice(9));
  if (columns.join('|') !== PLY_COLUMNS.join('|')) throw new Error(`unexpected PLY columns: ${columns.join(', ')}`);
  const count = lines.map((l) => /^element vertex (\d+)$/.exec(l)).find(Boolean);
  if (!count) throw new Error('the PLY header names no "element vertex"');
  const n = +count[1], start = end + 'end_header\n'.length;
  if (bytes.length < start + n * POINT_BYTES) throw new Error(`the file is cut short: ${n.toLocaleString()} points need more bytes than arrived`);
  const xyz = new Float32Array(n * 3), rgb = new Uint8Array(n * 3);
  const view = new DataView(bytes.buffer, bytes.byteOffset + start, n * POINT_BYTES);
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity, z0 = Infinity, z1 = -Infinity;
  for (let i = 0, o = 0, p = 0, c = start + 12; i < n; i++, o += POINT_BYTES, p += 3, c += POINT_BYTES) {
    const x = view.getFloat32(o, true), y = view.getFloat32(o + 4, true), z = view.getFloat32(o + 8, true);
    xyz[p] = x; xyz[p + 1] = y; xyz[p + 2] = z;
    rgb[p] = bytes[c]; rgb[p + 1] = bytes[c + 1]; rgb[p + 2] = bytes[c + 2];
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; if (z < z0) z0 = z; if (z > z1) z1 = z;
  }
  return { n, xyz, rgb, bounds: n ? { min: [x0, y0, z0], max: [x1, y1, z1] } : null };
}

class Orbit {
  constructor(cam, dom, changed) {
    this.cam = cam; this.dom = dom; this.changed = changed;
    this.target = new THREE.Vector3(); this.theta = Math.PI; this.phi = 1.05; this.radius = 4;
    this.dTheta = 0; this.dPhi = 0;
    this.damping = matchMedia('(prefers-reduced-motion: reduce)').matches ? 1 : 0.22;
    this.right = new THREE.Vector3(); this.up = new THREE.Vector3();
    this.down = new Map();
    dom.addEventListener('contextmenu', (e) => e.preventDefault());
    dom.addEventListener('pointerdown', (e) => { dom.setPointerCapture(e.pointerId); this.down.set(e.pointerId, { x: e.clientX, y: e.clientY, button: e.button }); });
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) dom.addEventListener(name, (e) => this.down.delete(e.pointerId));
    dom.addEventListener('pointermove', (e) => this.moved(e));
    dom.addEventListener('wheel', (e) => { e.preventDefault(); this.dolly(Math.exp(Math.max(-1, Math.min(1, e.deltaY * (e.deltaMode === 1 ? 16 : 1) * 0.0015)))); }, { passive: false });
  }
  moved(e) {
    const p = this.down.get(e.pointerId);
    if (!p) return;
    const dx = e.clientX - p.x, dy = e.clientY - p.y;
    if (p.button === 2 || p.button === 1 || e.shiftKey) this.pan(dx, dy);
    else { const k = 2 * Math.PI / this.dom.clientHeight; this.dTheta -= dx * k; this.dPhi -= dy * k; }
    p.x = e.clientX; p.y = e.clientY;
    this.changed();
  }
  pan(dx, dy) {
    const perPx = 2 * Math.tan(this.cam.fov * Math.PI / 360) * this.radius / this.dom.clientHeight;
    this.right.setFromMatrixColumn(this.cam.matrix, 0); this.up.setFromMatrixColumn(this.cam.matrix, 1);
    this.target.addScaledVector(this.right, -dx * perPx).addScaledVector(this.up, dy * perPx);
    this.changed();
  }
  dolly(k) { this.radius = Math.max(0.2, Math.min(40, this.radius * k)); this.changed(); }
  apply() {
    this.theta += this.dTheta; this.phi = Math.max(0.08, Math.min(Math.PI - 0.08, this.phi + this.dPhi));
    this.dTheta *= 1 - this.damping; this.dPhi *= 1 - this.damping;
    if (Math.abs(this.dTheta) < 1e-4) this.dTheta = 0;
    if (Math.abs(this.dPhi) < 1e-4) this.dPhi = 0;
    const s = Math.sin(this.phi);
    this.cam.position.set(
      this.target.x + this.radius * s * Math.sin(this.theta),
      this.target.y + this.radius * s * Math.cos(this.theta),
      this.target.z + this.radius * Math.cos(this.phi));
    this.cam.lookAt(this.target);
    return this.dTheta !== 0 || this.dPhi !== 0;
  }
}

function whyBox(title, text, wrong = true) {
  return el('div', { class: `cloud-why${wrong ? ' wrong' : ''}` },
    el('div', {}, el('strong', {}, title), el('span', {}, text)));
}

/**
 * @param {HTMLElement} host
 * @param {{ ply?: string, png?: string, page?: string, points?: number, reason?: string,
 *           available?: boolean, local_only?: boolean, robot?: {x:number,y:number,yaw:number}|null }} scene
 */
export function mountCloudView(host, scene) {
  host.replaceChildren();
  host.classList.add('cloud-host');
  if (!scene || scene.available === false) {
    host.append(whyBox('No 3D model for this capture',
      scene && scene.reason ? scene.reason : 'no point cloud was written for this capture.', false));
    if (scene && scene.page) host.append(el('p', { class: 'cloud-note' }, el('a', { href: scene.page }, 'open the scene page →')));
    return { dispose() {} };
  }

  const canvas = el('canvas', { tabindex: '0', 'aria-label': 'This capture as a 3D point cloud. Drag to orbit, right-drag or shift-drag to pan, wheel to zoom.' });
  const readout = el('p', { class: 'cloud-readout', role: 'status' }, 'loading…');
  const bar = el('div', { class: 'cloud-bar', hidden: true }, el('i'));
  host.append(canvas, bar, readout);

  let renderer, raf = 0, dead = false, dirty = true;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  } catch (e) {
    host.replaceChildren(whyBox('This browser could not start WebGL', 'The point cloud is drawn on the GPU. Try Chrome or Safari with hardware acceleration on.'));
    return { dispose() {} };
  }
  const scene3 = new THREE.Scene();
  scene3.background = new THREE.Color(0x08080b);
  const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 80);
  camera.up.set(0, 0, 1);
  const grid = new THREE.GridHelper(16, 16, 0x6c6c7a, 0x33333e);
  grid.rotation.x = Math.PI / 2;
  grid.material.transparent = true; grid.material.opacity = 0.7; grid.material.depthWrite = false;
  scene3.add(grid);

  const material = new THREE.ShaderMaterial({
    uniforms: { uSize: { value: 0.016 }, uScale: { value: 800 }, uMaxPx: { value: 22 } },
    vertexShader: `uniform float uSize, uScale, uMaxPx; attribute vec3 rgb; varying vec3 vColor;
      void main() {
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_Position = projectionMatrix * mv;
        gl_PointSize = clamp(uSize * uScale / max(-mv.z, 0.08), 1.2, uMaxPx);
        vColor = rgb;
      }`,
    fragmentShader: `varying vec3 vColor; void main() {
      vec2 d = gl_PointCoord - 0.5; if (dot(d, d) > 0.25) discard;
      gl_FragColor = vec4(vColor, 1.0);
    }`,
  });
  const points = new THREE.Points(new THREE.BufferGeometry(), material);
  points.frustumCulled = false;
  scene3.add(points);

  const invalidate = () => { dirty = true; if (!raf) raf = requestAnimationFrame(tick); };
  const orbit = new Orbit(camera, canvas, invalidate);

  function resize() {
    const w = host.clientWidth || 640, h = host.clientHeight || 360, pr = Math.min(devicePixelRatio || 1, 2);
    renderer.setPixelRatio(pr); renderer.setSize(w, h, false);
    camera.aspect = w / Math.max(1, h); camera.updateProjectionMatrix();
    invalidate();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(host);

  function tick() {
    raf = 0;
    if (dead) return;
    const coasting = orbit.apply();
    if (dirty || coasting) {
      renderer.render(scene3, camera);
      dirty = false;
    }
    if (coasting) raf = requestAnimationFrame(tick);
  }

  const ac = new AbortController();
  (async () => {
    readout.textContent = 'loading the point cloud…';
    bar.hidden = false;
    let r;
    try {
      r = await fetch(scene.ply, { cache: 'no-store', signal: ac.signal });
    } catch (e) {
      if (e.name === 'AbortError') return;
      host.replaceChildren(whyBox('The web server did not answer', 'Is web/server.py still running on this laptop?'));
      return;
    }
    if (r.status === 403) {
      const note = scene.page ? el('p', { class: 'cloud-note' }, el('a', { href: scene.page }, 'open the scene page on this laptop →')) : null;
      host.replaceChildren(whyBox('The 3D model is this laptop only',
        'A coloured point cloud of a real room is a picture of the people in it, so it is not served through the tunnel. Open this page as http://localhost:8000 on the machine that runs the server.'),
        ...[note].filter(Boolean));
      return;
    }
    if (!r.ok) {
      host.replaceChildren(whyBox('The point cloud did not load', r.status === 404 ? 'the file is gone (deleted, or another ROOM_LIVE_DIR).' : `HTTP ${r.status}`));
      return;
    }
    const total = +r.headers.get('content-length') || scene.size_bytes || 0;
    let buf = new Uint8Array(total || 1 << 22), got = 0;
    for (const reader = r.body.getReader(); ;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (got + value.length > buf.length) {
        const more = new Uint8Array(Math.max(buf.length * 2, got + value.length));
        more.set(buf.subarray(0, got)); buf = more;
      }
      buf.set(value, got); got += value.length;
      bar.firstChild.style.width = `${Math.round(100 * (total ? got / total : 0.5))}%`;
      readout.textContent = `loading… ${(got / 1e6).toFixed(1)}${total ? ` of ${(total / 1e6).toFixed(1)}` : ''} MB`;
    }
    bar.hidden = true;
    let cloud;
    try { cloud = readPly(buf.subarray(0, got)); }
    catch (e) { host.replaceChildren(whyBox('The file could not be read', e.message)); return; }
    points.geometry.setAttribute('position', new THREE.BufferAttribute(cloud.xyz, 3));
    points.geometry.setAttribute('rgb', new THREE.BufferAttribute(cloud.rgb, 3, true));
    points.geometry.setDrawRange(0, cloud.n);
    const b = cloud.bounds;
    if (b) {
      const cx = (b.min[0] + b.max[0]) / 2, cy = (b.min[1] + b.max[1]) / 2;
      const span = Math.max(b.max[0] - b.min[0], b.max[1] - b.min[1], 1.2);
      orbit.target.set(cx, cy, 0.4);
      orbit.radius = Math.max(2.2, span * 1.35);
      orbit.theta = Math.PI; orbit.phi = 1.05;
    }
    const shaky = typeof scene.tilt_rate_max === 'number' && scene.tilt_rate_max >= 0.05;
    readout.replaceChildren(
      `${cloud.n.toLocaleString()} points`,
      shaky ? el('span', { class: 'wrong' }, ` · tilt ${scene.tilt_rate_max.toFixed(3)} rad/s — the head was moving`) : '',
      ' · drag to orbit');
    resize();
  })();

  return {
    dispose() {
      dead = true; ac.abort(); ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
      points.geometry.dispose(); material.dispose(); renderer.dispose();
    },
  };
}
