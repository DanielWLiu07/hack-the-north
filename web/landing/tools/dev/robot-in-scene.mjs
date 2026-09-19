// node tools/dev/robot-in-scene.mjs <outdir> [who=primitives|splat] [layer=over|under|ink] [place=left|centre] [base=http://127.0.0.1:8124] [warm|cold]
// The robot's entrance inside the REAL landing page, without touching scene.js: imports the
// module into the live page and calls it exactly as a MODULES entry would (buildRobot /
// buildSplatRobot), only later, so the entrance can be watched. It waits for the title's own
// `landed` flags like it would at load.
//   layer=over   world.controlsScene, the overlay scene.js renders AFTER the ink pass: nothing covers the robot
//   layer=under  world.rawScene, the raw pass scene.js draws UNDER the inked composite (robotpop.js's layer)
//   layer=ink    inside the inked scene: what the near-binary pass does to it, for the record
//   place=centre robotpop.js's spot (x 0, z -1.4, behind the title) instead of front left
// robotpop.js's own robot is taken out of the raw scene IN THIS TEST PAGE ONLY, so one robot is judged at a time.
// Captures the whole entrance; reports fps / draw calls before and after, the worst frame
// around the moment it first appears (measured BEFORE the first screenshot: a screenshot
// stalls rAF by itself), and the console.
// NOTE: web/server.py (:8000) does not serve .ksplat / .ply (LandingFiles.SERVED); :8124 serves everything.
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const [out = '/tmp/robot-in-scene', who = 'primitives', layer = 'over', place = 'left', base = 'http://127.0.0.1:8124', warm = 'warm'] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const browser = await puppeteer.launch({
  executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl', '--allow-file-access-from-files'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 810, deviceScaleFactor: 1 });
const logs = [];
page.on('console', (m) => { if (m.type() !== 'log' && !/api\/|favicon|Failed to load resource/.test(m.text())) logs.push(`${m.type()}: ${m.text()}`); });
page.on('pageerror', (e) => logs.push(`pageerror: ${e.message}`));
await page.goto(`${base}/index.html`, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForFunction(() => window.gitrl && window.gitrl.world.t > 5.5, { timeout: 120000, polling: 200 });
const sample = (ms = 2000) => page.evaluate((ms) => new Promise((res) => {
  const g = window.gitrl, f0 = g.performance.frames, t0 = performance.now();
  setTimeout(() => res({ fps: +((g.performance.frames - f0) / ((performance.now() - t0) / 1000)).toFixed(1), calls: g.renderer.info.render.calls }), ms);
}), ms);
const before = await sample();
const built = await page.evaluate(async (who, layer, warm, place) => {
  const g = window.gitrl, t0 = performance.now();
  if (g.world.robot) g.world.robot.removeFromParent();
  const pop = place === 'centre' ? { x: 0.0, z: -1.4, yaw: 0.55 } : {};
  const world = layer === 'ink' ? Object.create(g.world, { controlsScene: { value: null } }) : g.world;
  const mod = who === 'splat' ? await (await import('./robot-splat.js')).buildSplatRobot(world, { warmUp: warm, layer, pop })
    : await (await import('./robot.js')).buildRobot(world, { warmUp: warm, pop });
  window.__robot = mod;
  let worst = 0, last = performance.now();
  const tick = () => { const n = performance.now(), rt = mod.startAt == null ? -1 : g.world.t - mod.startAt;
    if (rt < 0.15) { worst = Math.max(worst, n - last); window.__worstFrame = worst; } last = n;
    mod.update(g.world); requestAnimationFrame(tick); };
  tick();
  return { buildMs: Math.round(performance.now() - t0) };
}, who, layer, warm !== 'cold', place);
const TIMES = [0.2, 0.45, 0.72, 0.9, 1.05, 1.2, 1.35, 1.6, 2.0, 2.4, 3.0, 3.6, 4.3, 5.3];
for (const t of TIMES) {
  await page.waitForFunction((t) => window.__robot.startAt != null && window.gitrl.world.t - window.__robot.startAt >= t, { polling: 'raf', timeout: 60000 }, t);
  await page.screenshot({ path: `${out}/in-${t.toFixed(2)}.png` });
}
const after = await sample();
const worstFrameMs = await page.evaluate(() => Math.round(window.__worstFrame));
// a contact sheet of the robot's corner, and one full frame for composition
const cell = 360, crop = place === 'centre' ? { x: 400, y: 180, w: 640, h: 630, full: 1440 } : { x: 0, y: 250, w: 640, h: 560, full: 1440 };
const html = `<body style="margin:0;background:#121116;display:grid;grid-template-columns:repeat(7,${cell}px)">` + TIMES.map((t) => `<div style="width:${cell}px;height:${crop.h * cell / crop.w}px;overflow:hidden;position:relative"><img src="file://${out}/in-${t.toFixed(2)}.png" style="position:absolute;width:${crop.full * cell / crop.w}px;left:${-crop.x * cell / crop.w}px;top:${-crop.y * cell / crop.w}px"><span style="position:absolute;left:6px;top:4px;font:11px monospace;color:#fff;background:#0008">${t.toFixed(2)} s</span></div>`).join('');
const p2 = await browser.newPage();
await p2.setViewport({ width: 7 * cell, height: Math.round(2 * crop.h * cell / crop.w), deviceScaleFactor: 1 });
await p2.goto('file://' + out + '/', { waitUntil: 'domcontentloaded' }).catch(() => {});
await p2.setContent(html, { waitUntil: 'load' });
await p2.screenshot({ path: `${out}/sheet.png` });
console.log(JSON.stringify({ who, layer, place, built, before, after, worstFrameMsAsItAppears: worstFrameMs, logs }, null, 1));
await browser.close();
