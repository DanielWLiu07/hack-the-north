// Sample the live robot choreography and capture its responsive composition.
import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
try {
  const page = await browser.newPage(), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.setViewport({ width: 1440, height: 810 });
  await page.goto('http://127.0.0.1:8000/');
  await page.waitForFunction(() => window.gitrl?.world.t > 6, { timeout: 60000 });
  const report = await page.evaluate(() => {
    const g = window.gitrl, w = g.world, robot = w.robot, mod = g.slots[4];
    let prev, maxStep = 0, maxTurn = 0, invalid = 0;
    const samples = [];
    for (let i = 0; i <= 480; i++) {
      const t = i / 60;
      mod.update({ ...w, t, dt: 1 / 60, away: { on: false } });
      const p = robot.position.clone(), q = robot.quaternion.clone();
      if (prev) { maxStep = Math.max(maxStep, p.distanceTo(prev.p)); maxTurn = Math.max(maxTurn, q.angleTo(prev.q)); }
      if (![...p.toArray(), ...q.toArray()].every(Number.isFinite)) invalid++;
      if (i % 30 === 0) samples.push({ t, y: +p.y.toFixed(3) });
      prev = { p, q };
    }
    mod.update(w);
    return { frames: 481, maxStep, maxTurnDegrees: maxTurn * 180 / Math.PI, invalid, samples };
  });
  await page.setViewport({ width: 390, height: 844 });
  await new Promise(r => setTimeout(r, 1000));
  await page.screenshot({ path: '/tmp/robot-mobile.png' });
  console.log(JSON.stringify({ report, errors }));
} finally { await browser.close(); }
