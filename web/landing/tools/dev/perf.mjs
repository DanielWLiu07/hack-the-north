import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const [url, out, waitMs = '6000', w = '1440', h = '900'] = process.argv.slice(2);
const browser = await puppeteer.launch({
  executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'],
});
const page = await browser.newPage();
await page.setViewport({ width: +w, height: +h, deviceScaleFactor: 1 });
const logs = [];
page.on('console', (m) => { if (m.type() !== 'log' || /gitrl|error/i.test(m.text())) logs.push(`${m.type()}: ${m.text()}`); });
page.on('pageerror', (e) => logs.push(`pageerror: ${e.message}`));
// NOT networkidle: the page keeps an SSE stream (/api/events) open for ever, so the network never idles
await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForFunction(() => window.gitrl && window.gitrl.world.t > 0.1, { timeout: 60000, polling: 100 });
await new Promise((r) => setTimeout(r, +waitMs));
const stats = await page.evaluate(() => new Promise((res) => {
  let n = 0; const t0 = performance.now();
  const g = window.gitrl;
  const renderedAtStart = g.performance?.frames;
  const durations = [], frameTimes = []; let previous = t0, updateMs = 0;
  const originals = g.slots.map(s => s?.update);
  g.slots.forEach(s => { if (!s) return; const update = s.update;
    s.update = function(w) { const start = performance.now(); update.call(this, w); updateMs += performance.now() - start; }; });
  const percentile = (a, p) => +a.sort((a, b) => a - b)[Math.min(a.length - 1, Math.floor(a.length * p))].toFixed(2);
  const tick = () => { const now = performance.now(); frameTimes.push(now - previous); previous = now;
    durations.push(updateMs); updateMs = 0;
    n++; if (now - t0 < 3000) requestAnimationFrame(tick); else {
      g.slots.forEach((s, i) => { if (s) s.update = originals[i]; });
      res({
    fps: +(n / ((performance.now() - t0) / 1000)).toFixed(1),
    renderedFps: renderedAtStart == null ? null : +((g.performance.frames - renderedAtStart) / ((performance.now() - t0) / 1000)).toFixed(1),
    calls: g?.renderer.info.render.calls, tris: g?.renderer.info.render.triangles,
    geos: g?.renderer.info.memory.geometries, tex: g?.renderer.info.memory.textures,
    renderScale: g.renderer.getPixelRatio(), frameP95Ms: percentile(frameTimes, 0.95),
    updateMedianMs: percentile(durations, 0.5), updateP95Ms: percentile(durations, 0.95),
    modules: g?.slots.map((s) => !!s), t: g?.world.t.toFixed(2) }); } };
  requestAnimationFrame(tick);
}));
await page.screenshot({ path: out });
console.log(JSON.stringify({ stats, logs }, null, 1));
await browser.close();
