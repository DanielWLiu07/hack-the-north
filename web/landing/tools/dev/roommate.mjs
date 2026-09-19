// node tools/dev/roommate.mjs <outdir> [live|native]   — roommate.js, looked at and measured.
//   (default) dev-roommate.html on :8124: the three point cases (near / far / behind), the job states, the room moods
//   live      the REAL dashboard, http://127.0.0.1:8000/?info#search: imports /roommate.js into the page (the one
//             script tag it needs is the dashboard owner's to add), fires the dashboard's own example payload
//   native    the REAL dashboard loading roommate.js by its own script tag (nothing injected), plus the hero page /,
//             which loads the same tag and must stay clean: no stage, no canvas, no console noise
// Checks: mounts and un-hides the container, 0 console errors, no request leaves localhost, the caption says
// "planned" while the executor is not_connected, the robot drives when the object is out of reach and does not
// when it is near, the loop STOPS off screen, and prefers-reduced-motion gets a still with the arm already up.
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const [out = '/tmp/roommate', mode = 'dev'] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
const logs = [], external = new Set();
const newPage = async (w = 1200, h = 900) => { const p = await browser.newPage(); await p.setViewport({ width: w, height: h, deviceScaleFactor: 1 });
  p.on('console', (m) => { if ((m.type() === 'error' || m.type() === 'warning') && !/Failed to load resource/.test(m.text())) logs.push(`${m.type()}: ${m.text()}`); });
  p.on('pageerror', (e) => logs.push(`pageerror: ${e.message}`));
  p.on('response', (r) => { if (r.status() >= 400) logs.push(`http ${r.status()}: ${new URL(r.url()).pathname}`); });
  p.on('request', (r) => { const host = new URL(r.url()).hostname; if (host && !/^(127\.0\.0\.1|localhost)$/.test(host) && !r.url().startsWith('data:')) external.add(host); });
  return p; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const ready = (p) => p.waitForFunction(() => window.gitrlRoommate && !document.getElementById('roommate-stage').hidden, { timeout: 90000, polling: 200 });
const stageShot = async (p, name) => { const el = await p.$('#roommate-stage'); await el.screenshot({ path: `${out}/${name}.png` }); };
const state = (p) => p.evaluate(() => { const s = window.gitrlRoommate.state(); return { x: +s.me.x.toFixed(2), z: +s.me.z.toFixed(2), yaw: +s.me.yaw.toFixed(2), pointing: !!s.point, running: s.running, reduced: s.reduced, caption: document.querySelector('.roommate-caption').textContent }; });
const report = { mode };

if (mode === 'native') {
  const hero = await newPage(1280, 800);
  await hero.goto('http://127.0.0.1:8000/', { waitUntil: 'domcontentloaded' });
  await hero.waitForFunction(() => window.gitrl && window.gitrl.world.t > 4, { timeout: 90000, polling: 200 });
  report.hero = await hero.evaluate(() => ({ stage: !!document.getElementById('roommate-stage'), mounted: !!window.gitrlRoommate,
    canvases: document.querySelectorAll('canvas').length, moduleScriptTag: !!document.querySelector('script[src$="roommate.js"]') }));
  await hero.close();
}
if (mode === 'live' || mode === 'native') {
  const p = await newPage(1280, 900);
  await p.goto('http://127.0.0.1:8000/?info#search', { waitUntil: 'domcontentloaded' });
  await p.waitForFunction(() => document.getElementById('roommate-stage'), { timeout: 60000 });
  if (mode === 'live') await p.evaluate(() => import('/roommate.js'));
  // it mounts lazily, when the stage scrolls near: do what a reader does
  await p.evaluate(() => document.getElementById('roommate-stage').scrollIntoView({ block: 'center' }));
  await ready(p);
  await p.evaluate(() => document.getElementById('roommate-stage').scrollIntoView({ block: 'center' }));
  await wait(1200); report.idle = await state(p); await stageShot(p, 'live-idle');
  await p.evaluate(() => window.dispatchEvent(new CustomEvent('gitrl:point', { detail: { object_id: 'keys_7c2e', class: 'keys', zone: 'shelf', pose: { x: 0.62, y: 0.78, z: 0.91, yaw: 0 }, job_id: 'job_demo', state: 'planned', executor: 'not_connected', frame: 'world_z_up' } })));
  await wait(3200); report.pointing = await state(p); await stageShot(p, 'live-pointing');
  await p.screenshot({ path: `${out}/live-page.png` });
  await p.evaluate(() => scrollTo(0, document.body.scrollHeight)); await wait(900);
  report.offScreen = await state(p);
} else {
  const p = await newPage();
  await p.goto('http://127.0.0.1:8124/dev-roommate.html?tall', { waitUntil: 'domcontentloaded' });
  await ready(p); await wait(1000);
  report.idle = await state(p); await stageShot(p, '0-idle');
  await p.evaluate(() => devRoommate['room-dirty']()); await wait(950); await stageShot(p, '1-dirty-double-take'); report.dirty = await state(p); await wait(2200);
  await p.evaluate(() => devRoommate['point-near']()); await wait(700); await stageShot(p, '2-near-notices'); await wait(2300);
  report.near = await state(p); await stageShot(p, '3-near-there');
  await p.evaluate(() => devRoommate['point-far']()); await wait(1500); await stageShot(p, '4-far-driving'); await wait(3600);
  report.far = await state(p); await stageShot(p, '5-far-pointing');
  await p.evaluate(() => devRoommate['job-running']()); await wait(300); report.running = await state(p);
  await p.evaluate(() => devRoommate['job-succeeded']()); await wait(2600); report.afterSucceeded = await state(p); await stageShot(p, '6-released');
  await p.evaluate(() => devRoommate['point-behind']()); await wait(4200); report.behind = await state(p); await stageShot(p, '7-behind');
  await p.evaluate(() => devRoommate['room-conflict']()); await wait(300);
  await p.evaluate(() => scrollTo(0, document.body.scrollHeight)); await wait(900); report.offScreen = await state(p);
  await p.evaluate(() => scrollTo(0, 0)); await wait(700); report.backOnScreen = await state(p);
  // prefers-reduced-motion: a still, arm already raised
  const q = await newPage(); await q.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
  await q.goto('http://127.0.0.1:8124/dev-roommate.html', { waitUntil: 'domcontentloaded' }); await ready(q); await wait(600);
  await q.evaluate(() => devRoommate['point-far']()); await wait(500); report.reduced = await state(q); await stageShot(q, '8-reduced-still');
}
report.logs = logs; report.externalHosts = [...external];
console.log(JSON.stringify(report, null, 1));
await browser.close();
