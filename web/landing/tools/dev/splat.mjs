// node tools/dev/splat.mjs <outdir> [query]   — dev-splat.html: the scanned robot, raw and static.
//   orbit-<yaw>.png   four sides, orbit camera        stage.png   on the landing's stage camera
// Prints: source, gaussian count, which way the loader decided it FACES (forwardYaw, degrees,
// in the file's frame), fitted size, fps, console errors, and whether any request left 127.0.0.1.
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const [out = '/tmp/splat-shots', query = ''] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const browser = await puppeteer.launch({
  executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1100, height: 900, deviceScaleFactor: 1 });
const logs = [], external = new Set();
page.on('console', (m) => { if (m.type() !== 'log') logs.push(`${m.type()}: ${m.text()}`); });
page.on('pageerror', (e) => logs.push(`pageerror: ${e.message}`));
page.on('response', (r) => { if (r.status() >= 400) logs.push(`http ${r.status()}: ${r.url()}`); });
page.on('request', (r) => { const h = new URL(r.url()).host; if (h && !/^127\.0\.0\.1|^localhost/.test(h)) external.add(h); });
const open = async (q) => {
  await page.goto(`http://127.0.0.1:8124/dev-splat.html?still${q ? '&' + q : ''}${query ? '&' + query : ''}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.devSplat && (window.devSplat.robot || document.getElementById('err').textContent), { timeout: 120000, polling: 200 });
  await new Promise((r) => setTimeout(r, 1500));
};
for (const yaw of [0, 1.57, 3.14, 4.71]) { await open(`yaw=${yaw}`); await page.screenshot({ path: `${out}/orbit-${yaw}.png` }); }
const info = await page.evaluate(() => { const s = window.devSplat.state; return s && { source: s.source, url: s.url, count: s.count, bytes: s.bytes,
  forwardYawDeg: s.forwardYaw == null ? null : +(s.forwardYaw * 57.2958).toFixed(1), size: s.size.toArray().map((v) => +v.toFixed(3)), orient: s.orient,
  fps: document.getElementById('fps').textContent, err: document.getElementById('err').textContent }; });
await open('view=stage'); await page.screenshot({ path: `${out}/stage.png` });
console.log(JSON.stringify({ info, logs, externalHosts: [...external] }, null, 1));
await browser.close();
