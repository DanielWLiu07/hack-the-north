import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
try {
  const page = await browser.newPage(), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.setViewport({ width: 1440, height: 810 });
  await page.evaluateOnNewDocument(() => {
    addEventListener('pageswap', () => {
      const w = window.gitrl?.world;
      if (w?.away.on) sessionStorage.exitElapsed = String(w.t - w.away.since);
    });
    addEventListener('pagereveal', e => {
      window.revealCheck = { transition: !!e.viewTransition };
      e.viewTransition?.ready.then(() => {
        window.revealCheck.animation = getComputedStyle(document.documentElement, '::view-transition-new(root)').animationName;
      }).catch(() => {});
    });
  });
  await page.goto('http://127.0.0.1:8000/?auto');
  await page.waitForFunction(() => window.gitrl?.world.t > 6, { timeout: 60000 });
  await page.evaluate(() => window.gitrl.world.emit('enter-press', {}));
  for (const t of [0.5, 0.95, 1.12]) {
    await page.waitForFunction(t => window.gitrl.world.t - window.gitrl.world.away.since >= t, {}, t);
    await page.screenshot({ path: `/tmp/exit-stage-${t}.png` });
  }
  await page.goto('http://127.0.0.1:8000/');
  await page.waitForFunction(() => window.gitrl?.world.t > 6, { timeout: 60000 });
  await page.evaluate(() => window.gitrl.world.emit('enter-press', {}));
  await page.waitForFunction(() => location.pathname === '/robot', { timeout: 30000 });
  await new Promise(r => setTimeout(r, 500));
  console.log(JSON.stringify(await page.evaluate(() => ({ exitElapsed: sessionStorage.exitElapsed, reveal: window.revealCheck, url: location.pathname }))));
  await page.screenshot({ path: '/tmp/exit-destination.png' });
  console.log(JSON.stringify({ errors }));
} finally { await browser.close(); }
