// node tools/dev/splat-in-scene.mjs <out.png> [base=http://127.0.0.1:8124]
// The splat inside the REAL landing scene, without touching scene.js: waits for the intro to
// finish, imports splat.js into the live page, adds the robot to gitrl.scene and rolls it in
// from the left. Says whether it draws through the real MangaPass next to the arms and the
// title, what it costs (fps / draw calls before and after), and what the console said.
// NOTE: web/server.py (:8000) does not serve .ply / .ksplat (LandingFiles.SERVED), so this
// defaults to serve.py on :8124, which serves everything.
import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const [out = '/tmp/splat-in-scene.png', base = 'http://127.0.0.1:8124'] = process.argv.slice(2);
const browser = await puppeteer.launch({
  executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 810, deviceScaleFactor: 1 });
const logs = [];
page.on('console', (m) => { if (m.type() !== 'log' && !/api\/|favicon|Failed to load resource/.test(m.text())) logs.push(`${m.type()}: ${m.text()}`); });
page.on('pageerror', (e) => logs.push(`pageerror: ${e.message}`));
await page.goto(`${base}/index.html`, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForFunction(() => window.gitrl && window.gitrl.world.t > 5.5, { timeout: 120000, polling: 200 });
const sample = () => page.evaluate(() => new Promise((res) => {
  const g = window.gitrl, f0 = g.performance.frames, t0 = performance.now();
  setTimeout(() => res({ fps: +((g.performance.frames - f0) / ((performance.now() - t0) / 1000)).toFixed(1),
    calls: g.renderer.info.render.calls, tris: g.renderer.info.render.triangles }), 2000);
}));
const before = await sample();
const info = await page.evaluate(async () => {
  const { loadRobotSplat } = await import('./splat.js');
  const robot = await loadRobotSplat({ height: 2.4, pixelRatio: window.gitrl.renderer.getPixelRatio() });
  const g = window.gitrl; g.scene.add(robot); window.__robot = robot;
  // the dev page's roll-in, on the landing's own clock
  const q = (u) => u * u * u * (u * (u * 6 - 15) + 10), t0 = g.world.t, from = -(3.06 * g.camera.aspect + robot.userData.splat.size.x);
  robot.rotation.order = 'YXZ';
  const tick = () => { const u = Math.min(1, (g.world.t - t0) / 3.2), v = Math.min(1, Math.max(0, (g.world.t - t0 - 3.2) / 0.9));
    robot.position.set(from + (-3.9 - from) * q(u), -0.85, 1.2); robot.rotation.y = Math.PI / 2 + (0.35 - Math.PI / 2) * q(v);
    if (v < 1) requestAnimationFrame(tick); };
  tick();
  const s = robot.userData.splat; return { source: s.source, count: s.count, size: s.size.toArray().map((x) => +x.toFixed(2)) };
});
await new Promise((r) => setTimeout(r, 1600));
await page.screenshot({ path: out.replace(/\.png$/, '-mid.png') });
await new Promise((r) => setTimeout(r, 3200));
const after = await sample();
await page.screenshot({ path: out });
console.log(JSON.stringify({ info, before, after, x: await page.evaluate(() => +window.__robot.position.x.toFixed(2)), logs }, null, 1));
await browser.close();
