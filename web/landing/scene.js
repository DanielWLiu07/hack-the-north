// GITRL landing — the hero.
//
// A crowd of camera-headed arms swings in from every edge of the frame and looks at
// GITRL as it drops into the space they are all watching; ENTER hangs just below it.
// Press ENTER and everything leaves the stage, then the page scrolls to the dashboard.
// Everything is 3D and everything goes through pomme's MangaPass({ bw: 1, grit: 1 }).
//
// Each module loads on its own: if one is missing or throws, the rest keeps running.
// They talk only through the shared `world` (layout.js).

import * as THREE from 'three';
import { MangaPass } from './styles.js';
import { createWorld, TITLE, ENTER } from './layout.js';
import { createSparks, yieldBuild, Rigid } from './mech.js';

const canvas = document.getElementById('gl');
document.documentElement.style.overscrollBehaviorY = 'none';
document.body.style.overscrollBehaviorY = 'none';
document.title = TITLE.text;
canvas.setAttribute('aria-label', `${TITLE.text}: a factory of camera arms`);
// PERF. The scene is drawn into MangaPass's own target and reaches the canvas as ONE
// full-screen quad, so canvas MSAA would only multisample that quad: off. And the pass
// works in CSS pixels (gl_FragCoord / uRes): at pixel ratio 1 the canvas, the target and
// the halftone scale all agree, and a retina screen costs the same as any other — the
// browser upscales a gritty ink image, which hides it completely.
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: 'high-performance' });
const renderScale = 1;                 // native CSS resolution; preserve crisp ink and lettering
renderer.setPixelRatio(1);
renderer.info.autoReset = false;        // count the scene AND the post pass per frame

// The stage: the camera and the four lights pomme's rig used (snakeArms.js), so every
// material reads exactly as it did under it. snakeArms.js itself is no longer loaded:
// its camera arm and its claw are both retired, and with them a 3.5 MB model.
const scene = new THREE.Scene();
scene.background = new THREE.Color('#060608');
const camera = new THREE.PerspectiveCamera(40, 2, 0.1, 100);
camera.position.set(0, 2.3, 8.4);
camera.lookAt(0, 2.1, 0);
{
  const key = new THREE.DirectionalLight('#ffffff', 3.0); key.position.set(4, 6, 3);
  const fill = new THREE.DirectionalLight('#8fa8ff', 0.8); fill.position.set(-4, 2, -2);
  // FRONT fill: without it, camera-facing surfaces crush to black under the gritty pass
  const front = new THREE.DirectionalLight('#ffffff', 1.5); front.position.set(0.5, 2.5, 10);
  scene.add(key, fill, front, new THREE.AmbientLight('#46424e', 1.1));
}

// Same texture pixels, smaller transfer; leave the shared style module untouched.
THREE.DefaultLoadingManager.setURLModifier(url => url === './textures/crosshatch.png'
  ? './title/hatch-lossless.webp' : url === './textures/halftone.png'
  ? './title/halftone-lossless.webp' : url);
const manga = new MangaPass(renderer, { bw: 1, grit: 1 });
manga.mix = 1;
// The ink pass maps everything above white to paper. HDR storage adds bandwidth
// without adding visible detail, so use an ordinary RGBA8 scene target.
manga.rtScene.texture.type = THREE.UnsignedByteType;

const world = createWorld(scene, camera);
world.sparks = createSparks(scene);
world.canvas = canvas;
world.hero = [];                        // nothing the crowd has to make room for any more

// ---- modules, in update order ---------------------------------------------------
// Parked for now (the files stay, one line brings each back): the factory hall, the
// gears-and-girders dressing, the welder / poker / inspector tentacles.
const MODULES = [
  ['./hall.js', 'buildBackdrop'],
  ['./title.js', 'buildTitle'],
  ['./enter.js', 'buildEnter'],          // publish hover before the cameras react
  // ['./tentacles.js', 'buildTentacles'],
  ['./watchers.js', 'buildWatchers'],
  // ['./dressing.js', 'buildDressing'],
];
const slots = MODULES.map(() => null);
// dev switches: ?only=title,watchers loads a subset; ?auto drives a scripted pointer
const Q = new URLSearchParams(location.search);
const only = Q.get('only') ? Q.get('only').split(',') : null;
const pending = { modules: MODULES.length, assetsDone: false, started: performance.now() };
MODULES.forEach(([path, fn], i) => {
  if (only && !only.some((name) => path.includes(name))) { pending.modules--; return; }
  import(path)
    .then((m) => m[fn](world))
    .then((mod) => { slots[i] = mod; })
    .catch((e) => console.warn(`[gitrl] ${path} not running:`, e))
    .finally(() => { pending.modules--; });
});

// ---- pointer -> world -------------------------------------------------------------
const ndc = new THREE.Vector2(0, 0.05);
const ray = new THREE.Raycaster();
const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
const want = new THREE.Vector3(0, 2.1, 0), lastPoint = new THREE.Vector3(0, 2.1, 0), sweepNdc = new THREE.Vector3();

// pointer -> NDC through the canvas's own rect: the hero scrolls with the page
function toNdc(e, out) {
  const r = canvas.getBoundingClientRect();
  return out.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
}
addEventListener('pointermove', (e) => {
  toNdc(e, ndc);
  world.cursor.seen = true;
  world.cursor.movedAt = world.t;
}, { passive: true });

// leaving = the pointer leaving the PAGE. Losing window focus (typing in a terminal on
// another monitor) is not leaving.
const setPresent = (on) => {
  if (world.cursor.present === on) return;
  world.cursor.present = on;
  world.cursor[on ? 'returnedAt' : 'leftAt'] = world.t;
};
document.documentElement.addEventListener('pointerleave', () => setPresent(false));
document.documentElement.addEventListener('pointerenter', () => setPresent(true));

// a click pokes whatever letter it lands on, and presses ENTER if it lands there
canvas.addEventListener('pointerdown', (e) => {
  ray.setFromCamera(toNdc(e, new THREE.Vector2()), camera);
  const point = new THREE.Vector3();
  ray.ray.intersectPlane(plane, point);
  const letter = world.title ? world.title.pick(ray) : -1;
  world.click = { t: world.t, point, letter };
  world.emit('click', world.click);
});

// ?auto: a scripted visitor, so every beat can be captured headlessly (scene clock):
// wander -> hover ENTER -> press it (everything leaves) -> scroll back (it all returns)
const lerpN = (a, b, u) => a + (b - a) * Math.min(1, Math.max(0, u));
function autoPointer(t) {
  const T = t - 5.2;
  if (T < 0) return;
  world.cursor.seen = true;
  if (T < 3) ndc.set(Math.sin(T * 1.4) * 0.7, 0.1 + Math.sin(T * 2.3) * 0.35);
  else {
    sweepNdc.copy(ENTER.center).project(camera);
    ndc.set(lerpN(ndc.x, sweepNdc.x, 0.2), lerpN(ndc.y, sweepNdc.y, 0.2));
    if (T > 5 && !autoPointer.pressed) {
      autoPointer.pressed = true;
      const r = canvas.getBoundingClientRect();
      canvas.dispatchEvent(new PointerEvent('pointerdown', {
        clientX: r.left + (sweepNdc.x + 1) / 2 * r.width, clientY: r.top + (1 - sweepNdc.y) / 2 * r.height }));
    }
  }
}

// ---- ENTER: everything leaves, then the page moves on --------------------------------
// One reversible flag. Every module reads world.away and takes itself off stage in its
// own way (the crowd swings back out the way it came, the letters are yanked up their
// cables); when the hero is scrolled back into view they all come back.
const LEAVE_FOR = 0.22;                 // exit and scroll overlap; never hold the click hostage
let scrollAt = Infinity;
world.on('enter-press', () => {
  if (world.away.on) return;
  world.away = { on: true, since: world.t };
  scrollAt = world.t + LEAVE_FOR;
});
function moveOn() {
  scrollAt = Infinity;
  if (Q.has('auto')) return;            // captures stay on the hero
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const el = document.querySelector(ENTER.href);
  if (el) { el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' }); el.setAttribute('tabindex', '-1'); el.focus({ preventScroll: true }); }
  else location.hash = ENTER.href.slice(1);
}

// ---- resize ---------------------------------------------------------------------
function resize() {
  const w = canvas.clientWidth || innerWidth, h = canvas.clientHeight || innerHeight;
  const pixelScale = renderScale;
  renderer.setPixelRatio(pixelScale);
  renderer.setSize(w, h, false);
  manga.setSize(w * pixelScale, h * pixelScale);
  camera.aspect = w / h;
  // keep the whole title framed on any screen: pull back until its half-width plus
  // a margin fits at the title's depth (tan(fov / 2) = 0.364 for fov 40)
  const fit = (TITLE.width / 2 + 0.55) / (0.364 * (w / h)) + TITLE.center.z;
  camera.position.z = Math.max(8.4, fit);
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize);
resize();

// ---- loop -----------------------------------------------------------------------
// The clock only advances while the page can be seen: a hidden tab or a scrolled-away
// hero stops the loop entirely, and the scene resumes where it was instead of jumping.
let clock = 0, last = performance.now() / 1000;
let nextFrameAt = 0;
const performanceStats = { frames: 0, frameMs: 0, updateMs: 0, renderMs: 0, captureMs: 0, scale: 1 };

function frame() {
  const now = performance.now() / 1000;
  // High-refresh displays otherwise submit 120–240 full post passes per second.
  if (now < nextFrameAt - 0.002) return;
  nextFrameAt = Math.max(nextFrameAt + 1 / 60, now);
  const dt = Math.min(1 / 20, Math.max(0, now - last));
  last = now;
  clock += dt;
  const t = clock;
  world.t = t;
  world.dt = dt;
  renderer.info.reset();
  if (Q.has('auto')) autoPointer(t);

  // the pointer on the z = 0 plane, and as a ray; before it first moves, a slow idle sweep
  if (world.cursor.seen) { ray.setFromCamera(ndc, camera); ray.ray.intersectPlane(plane, want); world.cursor.ndc.copy(ndc); }
  else {
    want.set(Math.sin(t * 0.35) * 3.2, 2.0 + Math.sin(t * 0.27) * 0.9, 0);
    sweepNdc.copy(want).project(camera);
    world.cursor.ndc.set(sweepNdc.x, sweepNdc.y);
  }
  ray.setFromCamera(world.cursor.ndc, camera);
  world.cursor.ray.copy(ray.ray);
  const speed = lastPoint.distanceTo(want) / Math.max(dt, 1e-3);
  world.cursor.speed += (speed - world.cursor.speed) * (1 - Math.exp(-8 * dt));
  world.cursor.point.copy(want);
  lastPoint.copy(want);

  if (t >= scrollAt) moveOn();
  const updateStart = performance.now();

  // one machine breaking must never stop the show: drop it, keep rendering
  for (let i = 0; i < slots.length; i++) {
    if (!slots[i]) continue;
    try { slots[i].update(world); } catch (e) {
      console.warn(`[gitrl] ${MODULES[i][0]} stopped:`, e);
      slots[i] = null;
    }
  }
  world.sparks.update(dt);
  const renderStart = performance.now();
  manga.render(scene, camera);
  const captureStart = performance.now();
  for (const fn of world.afterRender) fn(canvas);
  performanceStats.frames++;
  performanceStats.frameMs = dt * 1000;
  performanceStats.updateMs = renderStart - updateStart;
  performanceStats.renderMs = captureStart - renderStart;
  performanceStats.captureMs = performance.now() - captureStart;
  performanceStats.scale = renderer.getPixelRatio();
}

// ---- the loading gate ---------------------------------------------------------------
// Nothing enters until everything is here: every module built (or failed), every
// texture in, every shader compiled — otherwise machines pop in mid-intro and the
// first seconds hitch. Until then two meshed gears turn above "Spying...".
const loader = (() => {
  const s = new THREE.Scene(), cam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 10);
  cam.position.z = 5;
  s.background = new THREE.Color('#060608');
  const white = new THREE.MeshBasicMaterial({ color: '#efece6' });
  const makeGear = (radius, count, x, y) => {
    const gear = new THREE.Group();
    gear.position.set(x, y, 0);
    const rigid = new Rigid();
    rigid.add(new THREE.RingGeometry(radius * 0.58, radius * 0.87, 40), white);
    rigid.add(new THREE.RingGeometry(radius * 0.13, radius * 0.25, 24), white);
    for (let i = 0; i < count; i++) {
      const a = i / count * Math.PI * 2;
      rigid.add(new THREE.PlaneGeometry(radius * 0.25, radius * 0.25).rotateZ(-a), white,
        Math.sin(a) * radius * 0.94, Math.cos(a) * radius * 0.94);
    }
    for (let i = 0; i < 3; i++) {
      const a = i * Math.PI * 2 / 3;
      rigid.add(new THREE.PlaneGeometry(radius * 0.13, radius * 0.5).rotateZ(-a), white,
        Math.sin(a) * radius * 0.4, Math.cos(a) * radius * 0.4);
    }
    rigid.into(gear);
    s.add(gear); return gear;
  };
  const gear = makeGear(0.17, 12, -0.10, 0.09);
  const pinion = makeGear(0.1133, 8, 0.155, 0.16);
  const labelCanvas = document.createElement('canvas');
  labelCanvas.width = 768; labelCanvas.height = 128;
  const ctx = labelCanvas.getContext('2d');
  ctx.font = '500 64px monospace'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillStyle = '#efece6'; ctx.fillText('Spying...', 384, 64);
  const labelMap = new THREE.CanvasTexture(labelCanvas);
  labelMap.colorSpace = THREE.SRGBColorSpace;
  const label = new THREE.Mesh(new THREE.PlaneGeometry(0.96, 0.16),
    new THREE.MeshBasicMaterial({ map: labelMap, transparent: true, depthWrite: false }));
  label.position.y = -0.22; s.add(label);
  return { render(progress, time) {
    cam.left = -camera.aspect; cam.right = camera.aspect; cam.updateProjectionMatrix();
    gear.rotation.z = -time * 1.15;
    pinion.rotation.z = time * 1.15 * 1.5 + Math.PI / 8;
    renderer.setRenderTarget(null); renderer.render(s, cam);
  } };
})();
THREE.DefaultLoadingManager.onLoad = () => { pending.assetsDone = true; };
let assetsLoaded = 0, assetsTotal = 1;
THREE.DefaultLoadingManager.onProgress = (_url, n, total) => { assetsLoaded = n; assetsTotal = total; };
let ready = false, warming = false;
async function warmStage() {
  const saved = [], drawables = [], textures = new Set();
  // compile() alone does not upload geometry or textures, and frustum-culling
  // hides the hanging letters until their first visible frame. Exercise every
  // drawable offscreen, in the actual render target, before starting the clock.
  scene.traverse(o => {
    saved.push([o, o.visible, o.frustumCulled]);
    o.visible = true; o.frustumCulled = false;
    if (o.isMesh || o.isPoints || o.isLine) drawables.push(o);
    for (const mat of o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [])
      for (const value of Object.values(mat)) if (value?.isTexture) textures.add(value);
  });
  const target = new THREE.WebGLRenderTarget(1, 1);
  try {
    for (const uniform of Object.values(manga.mat.uniforms))
      if (uniform.value?.isTexture) textures.add(uniform.value);
    for (const texture of textures) { await yieldBuild(); renderer.initTexture(texture); }
    // Upload geometry in small draws; a single forced-visible render used to
    // stall the loading gears while every buffer was allocated at once.
    for (let i = 0; i < drawables.length; i += 12) {
      await yieldBuild();
      drawables.forEach(o => { o.visible = false; });
      for (const o of drawables.slice(i, i + 12)) {
        for (let p = o; p && p !== scene; p = p.parent) p.visible = true;
      }
      const batch = new THREE.Group();
      // compile() only traverses this list; keep the actual scene graph intact.
      batch.children = drawables.slice(i, i + 12);
      renderer.setRenderTarget(target);
      await renderer.compileAsync(batch, camera, scene);
      renderer.setRenderTarget(target); renderer.render(scene, camera);
    }
    drawables.forEach(o => { o.visible = true; });
    await yieldBuild();
    renderer.setRenderTarget(target);
    await renderer.compileAsync(manga.fsqScene, manga.fsqCam);
    await yieldBuild();
    manga.render(scene, camera, target);
    // Wait for the GPU without blocking the loading animation on the main thread.
    const gl = renderer.getContext(), fence = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
    gl.flush();
    if (fence) await new Promise(resolve => {
      const started = performance.now();
      const poll = () => {
        const status = gl.clientWaitSync(fence, 0, 0);
        if (status === gl.TIMEOUT_EXPIRED && performance.now() - started < 10000) setTimeout(poll, 16);
        else { gl.deleteSync(fence); resolve(); }
      };
      poll();
    });
  } finally {
    for (const [o, visible, culled] of saved) { o.visible = visible; o.frustumCulled = culled; }
    target.dispose();
  }
}
function loadingFrame() {
  const waited = (performance.now() - pending.started) / 1000;
  const progress = 0.5 * (1 - pending.modules / MODULES.length) + 0.5 * (pending.assetsDone ? 1 : assetsLoaded / assetsTotal);
  loader.render(Math.min(progress, 0.97), waited);
  // a missing asset must never hold the page hostage: start anyway after 12 s
  if (!warming && ((pending.modules <= 0 && pending.assetsDone) || waited > 12)) {
    warming = true;
    warmStage().catch(e => console.warn('[gitrl] warm-up failed:', e)).finally(() => {
      ready = true;
      applyRunning();
    });
  }
}

// The loop runs only while it can be seen: tab visible AND the hero on screen. Below
// the hero the dashboard is text; a post-processed WebGL loop behind it would cost
// frames for nothing on the laptop that is also running perception (PAGES.md).
let heroOnScreen = true, wasAway = false;
function applyRunning() {
  const on = !document.hidden && heroOnScreen;
  if (on) { last = performance.now() / 1000; nextFrameAt = last; }
  renderer.setAnimationLoop(on ? (ready ? frame : loadingFrame) : null);
}
document.addEventListener('visibilitychange', applyRunning);
new IntersectionObserver(([entry]) => {
  heroOnScreen = entry.isIntersecting && entry.intersectionRatio > 0.02;
  // scrolled away after ENTER, and now back: everything returns to the stage
  if (!heroOnScreen && world.away.on) wasAway = true;
  if (heroOnScreen && wasAway) { wasAway = false; world.away = { on: false, since: world.t }; }
  applyRunning();
}, { threshold: 0.02 }).observe(canvas);
applyRunning();

// console handle for tuning
window.gitrl = { world, scene, camera, renderer, manga, slots, performance: performanceStats };
