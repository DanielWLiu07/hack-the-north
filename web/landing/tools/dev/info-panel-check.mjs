import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
try {
  const page = await browser.newPage(), errors = [], results = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.setViewport({ width: 1440, height: 810 });
  await page.goto('http://127.0.0.1:8000/');
  await page.waitForFunction(() => window.gitrl?.world.t > 5, { timeout: 60000 });
  for (const [width, height] of [[1440,810],[390,844]]) {
    await page.setViewport({ width, height });
    await page.evaluate(() => document.querySelector('a[aria-controls="landing-info"]').click());
    await page.waitForSelector('dialog[open]');
    const start = await page.evaluate(() => window.gitrl.world.t);
    await new Promise(r => setTimeout(r, 350));
    results.push(await page.evaluate(start => {
      const d = document.querySelector('dialog'), rect = d.getBoundingClientRect();
      return { width: innerWidth, open: d.open, path: location.pathname + location.search, paused: window.gitrl.world.t === start, fits: rect.top >= 0 && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth, dashboardHidden: getComputedStyle(document.querySelector('#dashboard')).display === 'none' };
    }, start));
    await page.screenshot({ path: `/tmp/info-panel-${width}.png` });
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('dialog').open);
    await page.waitForFunction(t => window.gitrl.world.t > t, {}, start);
  }
  await page.evaluate(() => window.gitrl.world.emit('info-press', {}));
  await page.click('.gitrl-info-enter');
  await page.waitForFunction(() => location.pathname === '/robot', { timeout: 30000 });
  console.log(JSON.stringify({ results, destination: page.url(), errors }));
} finally { await browser.close(); }
