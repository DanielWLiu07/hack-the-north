import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 810 });
  await page.goto('http://127.0.0.1:8000/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.gitrl?.world.t > 4);
  const point = await page.evaluate(() => {
    const g = window.gitrl, p = g.world.enter.pos.clone().project(g.camera), r = g.world.canvas.getBoundingClientRect();
    window.hoverSamples = []; let start;
    addEventListener('pointermove', () => { start = performance.now(); }, { once: true });
    const tick = () => {
      if (start) window.hoverSamples.push({ ms: Math.round(performance.now() - start), ...g.slots.find(s => s?.feedback).feedback() });
      if (!start || performance.now() - start < 400) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    return { x: r.left + (p.x + 1) * r.width / 2, y: r.top + (1 - p.y) * r.height / 2 };
  });
  await page.mouse.move(point.x, point.y);
  await new Promise(r => setTimeout(r, 500));
  console.log(JSON.stringify(await page.evaluate(() => ({
    hoverAt: window.hoverSamples.find(s => s.hovered),
    underline90: window.hoverSamples.find(s => s.underline >= 0.9),
    swell90: window.hoverSamples.find(s => s.swell >= 0.9),
  })), null, 2));
  await page.screenshot({ path: '/tmp/gitirl-hover.png' });
  await page.evaluate(() => {
    window.pressTime = null; window.scrollDelay = null;
    addEventListener('pointerdown', () => { window.pressTime = performance.now(); }, { once: true });
    addEventListener('scroll', () => { if (window.pressTime) window.scrollDelay = performance.now() - window.pressTime; }, { once: true });
  });
  await page.mouse.click(point.x, point.y);
  await page.waitForFunction(() => scrollY > 100, { timeout: 5000 });
  console.log(JSON.stringify(await page.evaluate(() => ({ clickToScrollMs: Math.round(window.scrollDelay),
    overscroll: getComputedStyle(document.documentElement).overscrollBehaviorY, scrollingWorks: scrollY > 100 }))));
} finally { await browser.close(); }
