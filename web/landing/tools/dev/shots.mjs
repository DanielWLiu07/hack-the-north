// Timed screenshots on the scene clock, with console errors. Robust to a slow machine:
// every wait has a hard timeout so the script always finishes and reports.
import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const [url, prefix, times, w = '1440', h = '810'] = process.argv.slice(2);
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'], protocolTimeout: 60000 });
const out = { shots: [], errs: [] };
try {
  const page = await browser.newPage();
  await page.setViewport({ width: +w, height: +h, deviceScaleFactor: 1 });
  page.on('pageerror', (e) => out.errs.push('pageerror: ' + e.message.slice(0, 240)));
  page.on('console', (m) => { if (/error|warn/.test(m.type())) out.errs.push(m.text().slice(0, 240)); });
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  for (const T of times.split(',').map(Number)) {
    try {
      await page.waitForFunction((T) => window.gitrl && window.gitrl.world.t >= T, { timeout: 45000, polling: 50 }, T);
      await page.screenshot({ path: `${prefix}_${T.toFixed(1)}.png` });
      out.shots.push(T);
    } catch (e) { out.errs.push(`t=${T}: ${e.message.slice(0, 120)}`); break; }
  }
  out.stats = await page.evaluate(() => window.gitrl && ({ t: +window.gitrl.world.t.toFixed(1), calls: window.gitrl.renderer.info.render.calls,
    tris: window.gitrl.renderer.info.render.triangles, slots: window.gitrl.slots.map((s) => !!s) })).catch(() => null);
} finally { await browser.close(); }
out.errs = [...new Set(out.errs)].slice(0, 12);
console.log(JSON.stringify(out));
