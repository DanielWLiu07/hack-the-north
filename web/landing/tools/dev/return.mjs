// Exercise the real ENTER hit target, paused dashboard, and scroll-back entrance.
import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
try {
  const page = await browser.newPage(), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (/stopped|not running|render scale/.test(m.text())) errors.push(m.text()); });
  await page.setViewport({ width: 1440, height: 810, deviceScaleFactor: 1 });
  await page.goto('http://127.0.0.1:8000/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.gitrl?.world.t > 7);
  const point = await page.evaluate(() => {
    const g = window.gitrl, p = g.world.enter.pos.clone().project(g.camera);
    return { x: (p.x + 1) * innerWidth / 2, y: (1 - p.y) * innerHeight / 2 };
  });
  await page.mouse.click(point.x, point.y);
  await page.waitForFunction(() => scrollY >= innerHeight * 0.95);
  await new Promise(r => setTimeout(r, 600)); // let smooth scrolling settle
  const t0 = await page.evaluate(() => window.gitrl.world.t);
  await new Promise(r => setTimeout(r, 500));
  const paused = await page.evaluate(t => Math.abs(window.gitrl.world.t - t) < 0.06, t0);
  await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
  await page.waitForFunction(() => !window.gitrl.world.away.on);
  await page.waitForFunction(t => window.gitrl.world.t > t + 4, {}, t0);
  await page.screenshot({ path: '/tmp/gitrl-return.png' });
  console.log(JSON.stringify({ paused, errors, returned: await page.evaluate(() => {
    const g = window.gitrl, w = g.slots.find(s => s?.watchers).watchers;
    return { visibleHeads: w.filter(w => !w.hidden).length, total: w.length, away: g.world.away.on };
  }) }));
} finally { await browser.close(); }
