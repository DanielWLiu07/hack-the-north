// The robot's local self-representation. This is the scanned machine, not a claim
// that the physical robot or its camera is currently connected.
import * as THREE from 'three';
import { loadRobotSplat } from '/splat.js';

const host = document.querySelector('.viewer');
const canvas = document.createElement('canvas');
canvas.className = 'robot-splat-canvas';
canvas.tabIndex = 0;
canvas.setAttribute('aria-label', 'Scanned 3D self-view of the robot. Drag to rotate and scroll to zoom.');
const state = document.createElement('p');
state.className = 'robot-splat-state';
state.textContent = 'loading self model';
state.dataset.ready = 'false';
const video = document.createElement('video');
video.className = 'attached-camera';
video.autoplay = true;
video.muted = true;
video.playsInline = true;
const cameraTools = document.createElement('div');
cameraTools.className = 'attached-camera-tools';
const cameraButton = document.createElement('button');
cameraButton.type = 'button';
cameraButton.textContent = 'Use attached camera';
const stillButton = document.createElement('button');
stillButton.type = 'button';
stillButton.textContent = 'Save current frame';
stillButton.title = 'Save a permanent PNG from the attached camera. A 3D Git room snapshot still requires room_live.py add.';
stillButton.hidden = true;
const viewButton = document.createElement('button');
viewButton.type = 'button';
viewButton.textContent = 'Show self model';
viewButton.hidden = true;
cameraTools.append(cameraButton, viewButton, stillButton);
host.append(video, canvas, state, cameraTools);

const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: false, powerPreference: 'high-performance' });
renderer.setClearColor(0x000000, 0);
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.25));
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(34, 1, 0.05, 100);
const target = new THREE.Vector3(0, 2.25, 0);
let yaw = 0.44, pitch = 0.04, distance = 8.1;
let targetYaw = yaw, targetPitch = pitch, targetDistance = distance;
let drag = null;
function updateCamera() {
  yaw += (targetYaw - yaw) * .12;
  pitch += (targetPitch - pitch) * .12;
  distance += (targetDistance - distance) * .12;
  camera.position.set(target.x + Math.sin(yaw) * Math.cos(pitch) * distance,
    target.y + Math.sin(pitch) * distance, target.z + Math.cos(yaw) * Math.cos(pitch) * distance);
  camera.lookAt(target);
}
canvas.addEventListener('pointerdown', event => { drag = [event.clientX, event.clientY]; canvas.setPointerCapture(event.pointerId); });
canvas.addEventListener('pointermove', event => {
  if (!drag) return;
  targetYaw -= (event.clientX - drag[0]) * .006;
  targetPitch = THREE.MathUtils.clamp(targetPitch + (event.clientY - drag[1]) * .004, -.55, .72);
  drag = [event.clientX, event.clientY];
});
for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) canvas.addEventListener(name, () => { drag = null; });
canvas.addEventListener('wheel', event => { event.preventDefault(); targetDistance = THREE.MathUtils.clamp(targetDistance * Math.exp(event.deltaY * .001), 4.8, 11); }, { passive: false });

let robot = null;
let cameraLive = false;
let raf = 0;
let frames = 0;
let loadedAt = 0;
const reduced = matchMedia('(prefers-reduced-motion: reduce)');

function resize() {
  const width = Math.max(1, host.clientWidth), height = Math.max(1, host.clientHeight);
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(host);
resize();

function draw(now) {
  raf = requestAnimationFrame(draw);
  if (document.hidden || !robot || document.body.classList.contains('voxels-enabled')) return;
  updateCamera();
  if (!reduced.matches) {
    const t = Math.max(0, (now - loadedAt) / 1000);
    const settle = 1 - Math.exp(-4.2 * t);
    robot.position.y = -0.16 * (1 - settle) + Math.sin(t * 0.65) * 0.012;
    robot.rotation.y = -0.08 * (1 - settle);
  }
  renderer.render(scene, camera);
  frames++;
}

// Camera is opt-in: auto-starting it covers the hallway cloud.

try {
  robot = await loadRobotSplat({ height: 4.7, pixelRatio: Math.min(devicePixelRatio, 1.25) });
  scene.add(robot);
  loadedAt = performance.now();
  state.textContent = 'self model · scanned splat';
  state.dataset.ready = 'true';
  canvas.classList.add('is-ready');
  raf = requestAnimationFrame(draw);
} catch (error) {
  state.textContent = 'self model unavailable';
  console.warn('[room] robot splat failed:', error);
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && robot && !raf) raf = requestAnimationFrame(draw);
});
addEventListener('pagehide', () => {
  cancelAnimationFrame(raf);
  raf = 0;
  renderer.dispose();
  robot?.userData.splat?.dispose();
  for (const track of video.srcObject?.getTracks?.() || []) track.stop();
}, { once: true });

async function useAttachedCamera() {
  cameraButton.disabled = true;
  cameraButton.textContent = 'Connecting camera…';
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
    video.srcObject = stream;
    await video.play();
    cameraLive = true;
    video.classList.add('is-live');
    const voxelToggle = document.querySelector('#enable-voxels');
    if (voxelToggle?.checked) {
      voxelToggle.checked = false;
      voxelToggle.dispatchEvent(new Event('change', { bubbles: true }));
    }
    document.body.classList.add('camera-live', 'camera-current');
    cameraButton.textContent = 'Attached camera · live';
    viewButton.hidden = false;
    stillButton.hidden = false;
    dispatchEvent(new CustomEvent('room:local-camera', { detail: { live: true } }));
  } catch (error) {
    cameraButton.disabled = false;
    cameraButton.textContent = 'Retry attached camera';
    state.textContent = error.name === 'NotAllowedError' ? 'camera permission needed · self model active' : 'camera unavailable · self model active';
  }
}
cameraButton.addEventListener('click', useAttachedCamera);
viewButton.addEventListener('click', () => {
  const current = document.body.classList.toggle('camera-current');
  viewButton.textContent = current ? 'Show self model' : 'Show current view';
  state.hidden = current;
});
stillButton.addEventListener('click', () => {
  if (!video.videoWidth) return;
  const shot = document.createElement('canvas');
  shot.width = video.videoWidth; shot.height = video.videoHeight;
  shot.getContext('2d').drawImage(video, 0, 0);
  const link = document.createElement('a');
  link.download = `gitirl-current-${new Date().toISOString().replace(/[:.]/g, '-')}.png`;
  link.href = shot.toDataURL('image/png');
  link.click();
  const previous = stillButton.textContent;
  stillButton.textContent = 'Frame saved';
  setTimeout(() => { stillButton.textContent = previous; }, 1800);
});
window.roomSplat = {
  get state() { return { ready: !!robot, frames, source: robot?.userData.splat?.source, count: robot?.userData.splat?.count, cameraLive }; },
};
