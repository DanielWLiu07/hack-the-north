import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 2560, height: 1440 });
  await page.evaluateOnNewDocument(() => {
    window.startup = { tasks: [], gaps: [], ready: 0 };
    new PerformanceObserver(list => {
      for (const e of list.getEntries()) window.startup.tasks.push({ at: Math.round(e.startTime), ms: Math.round(e.duration) });
    }).observe({ type: 'longtask', buffered: true });
    let previous = performance.now();
    const tick = now => {
      if (window.gitrl?.world.t > 0) { window.startup.ready = Math.round(now); return; }
      if (now - previous > 25) window.startup.gaps.push(Math.round(now - previous));
      previous = now; requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await page.goto('http://127.0.0.1:8000/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.gitrl?.world.t > 4, { timeout: 60000 });
  console.log(JSON.stringify(await page.evaluate(() => ({ ...window.startup,
    resources: performance.getEntriesByType('resource').filter(e => /png|webp/.test(e.name))
      .map(e => ({ name: e.name.split('/').pop(), bytes: e.decodedBodySize })) })), null, 2));
} finally { await browser.close(); }
