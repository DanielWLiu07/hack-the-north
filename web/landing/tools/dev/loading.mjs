// Hold title metadata briefly to inspect the loader, then sample the handoff.
import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
try {
  const page = await browser.newPage(), errors = [];
  await page.setViewport({ width: 1440, height: 810 });
  page.on('pageerror', e => errors.push(e.message));
  await page.setRequestInterception(true);
  let release;
  const held = new Promise(resolve => { release = resolve; });
  page.on('request', async req => {
    if (req.url().endsWith('/title/gitirl.json')) await held;
    await req.continue();
  });
  await page.goto('http://127.0.0.1:8000/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.gitrl);
  await page.screenshot({ path: '/tmp/gitirl-loading-a.png' });
  await new Promise(resolve => setTimeout(resolve, 350));
  await page.screenshot({ path: '/tmp/gitirl-loading-b.png' });
  release();
  for (const t of [0.4, 0.9, 4.5]) {
    await page.waitForFunction(t => window.gitrl.world.t >= t, { timeout: 60000, polling: 10 }, t);
    await page.screenshot({ path: `/tmp/gitirl-start-${t}.png` });
  }
  console.log(JSON.stringify({ errors }));
} finally { await browser.close(); }
