import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
const errors = [];
try {
  for (const [width, height] of [[1440, 810], [390, 844]]) {
    for (const action of ['info', 'enter']) {
      const page = await browser.newPage();
      page.on('pageerror', e => errors.push(e.message));
      await page.setViewport({ width, height });
      await page.goto('http://127.0.0.1:8000/', { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => window.gitrl?.world.t > 4);
      await page.mouse.wheel({ deltaY: 900 });
      await new Promise(r => setTimeout(r, 250));
      if (await page.evaluate(() => scrollY !== 0 || document.documentElement.scrollHeight > innerHeight + 1))
        throw new Error('Landing should not scroll');
      if (action === 'info') {
        await page.mouse.move(width - 80, height - 40);
        await page.waitForFunction(() => window.gitrl.world.info?.hovered);
        await new Promise(r => setTimeout(r, 250));
        const gaze = await page.evaluate(() => {
          const w = window.gitrl, target = w.world.info.pos;
          const cast = w.slots.find(s => s?.watchers).watchers.filter(h => !h.hidden);
          return Math.max(...cast.map(h => Math.hypot(h.gazeNow.x - target.x, h.gazeNow.y - target.y)));
        });
        if (gaze > 0.15) throw new Error(`INFO gaze error: ${gaze}`);
        await page.screenshot({ path: `/tmp/gitirl-info-hover-${width}.png` });
        await Promise.all([page.waitForNavigation({ waitUntil: 'domcontentloaded' }), page.mouse.click(width - 80, height - 40)]);
        if (!new URL(page.url()).searchParams.has('info')) throw new Error('INFO did not navigate');
        if (!await page.evaluate(() => getComputedStyle(document.getElementById('hero')).display === 'none' && !window.gitrl))
          throw new Error('Information view should not run the hero');
      } else {
        await page.evaluate(() => [...document.querySelectorAll('a')].find(a => a.textContent === 'Enter room workspace').focus({ preventScroll: true }));
        await Promise.all([page.waitForNavigation({ waitUntil: 'domcontentloaded' }), page.keyboard.press('Enter')]);
        if (new URL(page.url()).pathname !== '/robot') throw new Error('ENTER did not navigate');
      }
      console.log({ width, action, url: page.url(), passed: true });
      await page.close();
    }
  }
  console.log({ errors });
  if (errors.length) throw new Error('Browser errors');
} finally { await browser.close(); }
