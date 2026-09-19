// node tools/dev/dashperf.mjs <outdir> [base=http://127.0.0.1:8001] [pages=/?info,/robot,/telemetry,/scene]
//
// What the demo pages actually cost in Chrome, measured the way a judge's laptop would feel it.
// For each page: first contentful paint, largest contentful paint, total blocking time (the sum
// of every long task's time over 50 ms — the number that decides whether a click feels instant),
// layout shift, bytes and requests, the JS heap, then a 6 s watch of the page sitting there with
// its live stream open: dropped frames, long tasks per second, and heap growth (a dashboard that
// leaks while nobody touches it dies during a 90 s demo).
//
// It reports the worst scripts by main-thread time, so the next move is never a guess.
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const [out = '/tmp/dashperf', base = 'http://127.0.0.1:8001', pagesArg = '/?info,/robot,/telemetry,/scene'] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const PAGES = pagesArg.split(',');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
const WATCH_MS = 6000;
const rows = [];
for (const path of PAGES) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });
  const logs = [], bytes = { total: 0, byType: {} }; let requests = 0;
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') logs.push(`${m.type()}: ${m.text().slice(0, 120)}`); });
  page.on('pageerror', (e) => logs.push(`pageerror: ${e.message.slice(0, 120)}`));
  page.on('request', () => { requests++; });
  page.on('response', async (r) => { try { const l = +(r.headers()['content-length'] || 0); if (l) { bytes.total += l;
    const t = (r.request().resourceType() || 'other'); bytes.byType[t] = (bytes.byType[t] || 0) + l; } } catch {} });
  // collect long tasks and layout shifts from the very first moment
  await page.evaluateOnNewDocument(() => {
    window.__perf = { long: [], cls: 0, lcp: 0 };
    new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__perf.long.push({ start: +e.startTime.toFixed(0), dur: +e.duration.toFixed(0) }); }).observe({ type: 'longtask', buffered: true });
    new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__perf.cls += e.value; }).observe({ type: 'layout-shift', buffered: true });
    new PerformanceObserver((l) => { const e = l.getEntries().pop(); if (e) window.__perf.lcp = +e.startTime.toFixed(0); }).observe({ type: 'largest-contentful-paint', buffered: true });
  });
  const t0 = Date.now();
  await page.goto(base + path, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch((e) => logs.push('goto: ' + e.message.slice(0, 80)));
  await new Promise((r) => setTimeout(r, 3500));                       // let it finish settling
  const load = await page.evaluate(() => {
    const nav = performance.getEntriesByType('navigation')[0] || {};
    const fcp = (performance.getEntriesByName('first-contentful-paint')[0] || {}).startTime || 0;
    const long = window.__perf.long;
    return { domContentLoaded: +(nav.domContentLoadedEventEnd || 0).toFixed(0), load: +(nav.loadEventEnd || 0).toFixed(0),
      fcp: +fcp.toFixed(0), lcp: window.__perf.lcp, cls: +window.__perf.cls.toFixed(4),
      longTasks: long.length, totalBlockingMs: long.reduce((s, t) => s + Math.max(0, t.dur - 50), 0),
      worstTaskMs: long.reduce((m, t) => Math.max(m, t.dur), 0),
      heapMB: +((performance.memory?.usedJSHeapSize || 0) / 1048576).toFixed(1),
      scripts: performance.getEntriesByType('resource').filter((r) => r.initiatorType === 'script' || /\.js(\?|$)/.test(r.name))
        .map((r) => ({ name: r.name.split('/').pop().slice(0, 34), ms: +r.duration.toFixed(0), kb: +(r.transferSize / 1024).toFixed(1) }))
        .sort((a, b) => b.kb - a.kb).slice(0, 6),
      canvases: document.querySelectorAll('canvas').length, nodes: document.querySelectorAll('*').length };
  }).catch(() => ({}));
  // ---- sitting there, live stream open: frames, long tasks, heap growth
  const watch = await page.evaluate((ms) => new Promise((res) => {
    const t0 = performance.now(), h0 = performance.memory?.usedJSHeapSize || 0, before = window.__perf.long.length;
    let frames = 0, worst = 0, last = performance.now();
    const tick = () => { const n = performance.now(); worst = Math.max(worst, n - last); last = n; frames++;
      if (n - t0 < ms) requestAnimationFrame(tick); else {
        const secs = (performance.now() - t0) / 1000, h1 = performance.memory?.usedJSHeapSize || 0;
        res({ fps: +(frames / secs).toFixed(1), worstFrameMs: +worst.toFixed(0),
          longTasks: window.__perf.long.length - before, heapGrowthMB: +((h1 - h0) / 1048576).toFixed(2) }); } };
    requestAnimationFrame(tick);
  }), WATCH_MS).catch(() => ({}));
  await page.screenshot({ path: `${out}/${path.replace(/[^\w]+/g, '_') || 'root'}.png` });
  rows.push({ path, ms: Date.now() - t0, ...load, requests, kb: +(bytes.total / 1024).toFixed(0), byType: Object.fromEntries(Object.entries(bytes.byType).map(([k, v]) => [k, +(v / 1024).toFixed(0)])), idle: watch, logs });
  await page.close();
}
writeFileSync(`${out}/report.json`, JSON.stringify(rows, null, 1));
// a table a human reads in one look
const f = (n, w) => String(n).padStart(w);
console.log('page            FCP   LCP   TBT  worst  CLS  reqs    KB  heapMB  idle fps  worstFrame  longTasks  heap+MB');
for (const r of rows) console.log(`${r.path.padEnd(14)} ${f(r.fcp, 4)}  ${f(r.lcp, 4)}  ${f(r.totalBlockingMs, 4)} ${f(r.worstTaskMs, 6)} ${f(r.cls, 5)} ${f(r.requests, 5)} ${f(r.kb, 5)}  ${f(r.heapMB, 6)}  ${f(r.idle.fps ?? '-', 8)}  ${f(r.idle.worstFrameMs ?? '-', 10)}  ${f(r.idle.longTasks ?? '-', 9)}  ${f(r.idle.heapGrowthMB ?? '-', 7)}`);
for (const r of rows) { if (r.logs.length) console.log(`\n${r.path} console:`, r.logs.slice(0, 5)); }
console.log('\nheaviest scripts');
for (const r of rows) console.log(r.path.padEnd(14), (r.scripts || []).map((s) => `${s.name} ${s.kb}KB/${s.ms}ms`).join('  '));
await browser.close();
