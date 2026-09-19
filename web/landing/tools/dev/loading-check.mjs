// Capture the loader at successive progress values and the finished hero.
import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
try {
  const page = await browser.newPage(), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.setViewport({ width: 1440, height: 810 });
  await page.goto('http://127.0.0.1:8000/');
  await page.waitForFunction(() => window.gitrl?.world.t > 6, { timeout: 60000 });
  await page.screenshot({ path: '/tmp/hero-soft-lines.png' });
  await page.evaluate(() => window.gitrl.renderer.setAnimationLoop(null));
  for (const [progress, time] of [[0.25, 20], [0.65, 22], [0.97, 24]]) {
    await page.evaluate(({ progress, time }) => {
      for (let i = 0; i < 120; i++) window.gitrl.loader.render(progress, time + i / 60);
    }, { progress, time });
    await page.screenshot({ path: `/tmp/loader-${progress}.png` });
  }
  await page.setViewport({ width: 390, height: 844 });
  await page.evaluate(() => window.gitrl.loader.render(0.97, 26));
  await page.screenshot({ path: '/tmp/loader-mobile.png' });
  console.log(JSON.stringify({ errors }));
} finally { await browser.close(); }
