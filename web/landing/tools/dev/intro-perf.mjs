// Cold-start profile: GPU uploads and frame stalls around the title reveal.
import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 2560, height: 1440, deviceScaleFactor: 1 });
  await page.evaluateOnNewDocument(() => {
    window.introProfile = { gpu: [], frames: [] };
    for (const op of ['bufferData', 'texImage2D', 'texSubImage2D', 'generateMipmap', 'linkProgram']) {
      const original = WebGL2RenderingContext.prototype[op];
      WebGL2RenderingContext.prototype[op] = function(...args) {
        const start = performance.now();
        const result = original.apply(this, args);
        window.introProfile.gpu.push({ t: window.gitrl?.world.t ?? -1, op, ms: +(performance.now() - start).toFixed(2) });
        return result;
      };
    }
    let last = performance.now();
    const tick = () => {
      const now = performance.now(), g = window.gitrl;
      if (g) window.introProfile.frames.push({ t: +g.world.t.toFixed(3), ms: +(now - last).toFixed(2), ...g.performance });
      last = now;
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await page.goto('http://127.0.0.1:8000/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.gitrl?.world.t > 5.5, { timeout: 60000 });
  console.log(JSON.stringify(await page.evaluate(() => {
    const { gpu, frames } = window.introProfile;
    const reveal = frames.filter(f => f.t > 1.5 && f.t < 3.1);
    return { revealWorst: reveal.sort((a, b) => b.ms - a.ms).slice(0, 8),
      revealUploads: gpu.filter(e => e.t > 1.5 && e.t < 3.1),
      startupUploads: gpu.filter(e => e.t <= 0).length };
  }), null, 2));
} finally { await browser.close(); }
