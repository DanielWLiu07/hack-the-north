import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const prefix = process.argv[2] || '/tmp/seer';
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
try {
  const page = await browser.newPage(), errors = [];
  await page.setViewport({ width: 1440, height: 900 });
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('http://127.0.0.1:8000/telemetry', { waitUntil: 'domcontentloaded' });
  await new Promise(r => setTimeout(r, 3000));
  await page.screenshot({ path: `${prefix}-page.png` });
  await page.goto('http://127.0.0.1:8000/pages/seer/dev-seer.html', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.seerReady, { timeout: 60000 });
  const states = [];
  for (const state of ['idle', 'thinking', 'stumped', 'verdict']) {
    await page.evaluate(state => window.seer.react(state), state);
    await new Promise(r => setTimeout(r, 2000));
    await page.screenshot({ path: `${prefix}-${state}.png` });
    states.push(await page.evaluate(() => window.seer._debug()));
  }
  await page.setViewport({ width: 390, height: 844 });
  await page.evaluate(() => window.seer.react('idle'));
  await new Promise(r => setTimeout(r, 2000));
  await page.screenshot({ path: `${prefix}-mobile.png` });
  console.log(JSON.stringify({ states, errors }, null, 2));
} finally { await browser.close(); }
