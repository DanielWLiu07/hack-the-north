// node tools/dev/transitions.mjs [reduce]
// Walks / -> /robot -> /?info -> /telemetry -> /capture/cap_0004 by real link clicks, then Back
// through all of it. Per document: did a cross-document view transition run (pagereveal),
// did it finish or get skipped, were styles applied at first reveal, any console/page errors.
import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const reduce = process.argv[2] === 'reduce';
const out = process.argv[3] || '/tmp';
const browser = await puppeteer.launch({ headless: 'new', executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 810 });
if (reduce) await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
const log = [];
page.on('pageerror', e => log.push(`PAGEERROR ${page.url()} :: ${e.message}`));
page.on('console', m => { if (['error', 'warning'].includes(m.type())) log.push(`CONSOLE.${m.type()} ${page.url()} :: ${m.text().slice(0, 300)}`); });
page.on('requestfailed', r => log.push(`REQFAILED ${r.url()} :: ${r.failure()?.errorText}`));
page.on('response', r => { if (r.status() >= 400) log.push(`HTTP ${r.status()} ${r.url()}`); });
await page.evaluateOnNewDocument(() => {
  const rec = window.__vt = { reveal: null, persisted: null };
  addEventListener('pagereveal', e => {
    rec.reveal = { vt: !!e.viewTransition, sheets: document.styleSheets.length,
      bg: document.body ? getComputedStyle(document.body).backgroundColor : null,
      htmlbg: getComputedStyle(document.documentElement).backgroundColor, at: performance.now() };
    if (e.viewTransition) {
      e.viewTransition.ready.then(() => rec.ready = 'ok', x => rec.ready = 'rejected: ' + x.message);
      e.viewTransition.finished.then(() => rec.finished = Math.round(performance.now() - rec.reveal.at) + 'ms', x => rec.finished = 'rejected: ' + x.message);
    }
  });
  addEventListener('pageshow', e => rec.persisted = e.persisted);
  addEventListener('pageswap', e => { sessionStorage.__swap = JSON.stringify({ from: location.pathname + location.search, vt: !!e.viewTransition, type: e.activation?.navigationType }); });
});
const sleep = ms => new Promise(r => setTimeout(r, ms));
const DWELL = +(process.env.DWELL || 1500);
const report = async label => {
  await sleep(DWELL);
  const r = await page.evaluate(() => ({ url: location.pathname + location.search + location.hash, reveal: window.__vt?.reveal?.vt, finished: window.__vt?.finished,
    bfcache: window.__vt?.persisted, swap: sessionStorage.__swap, bodyBg: getComputedStyle(document.body).backgroundColor, htmlBg: getComputedStyle(document.documentElement).backgroundColor }));
  console.log(label.padEnd(30), JSON.stringify(r));
};
// navigate by an in-page action, then wait for the URL to change (waitForNavigation can miss a transitioned swap)
const go = async (label, act, arg, shot) => {
  const before = page.url();
  page.evaluate(act, arg).catch(() => {});
  if (shot) { await sleep(90); await page.screenshot({ path: `${out}/vt-${shot}-mid.png` }).catch(() => {}); }
  for (let i = 0; i < 150 && page.url() === before; i++) await sleep(100);
  await page.waitForFunction(() => document.readyState !== 'loading', { timeout: 20000 }).catch(() => {});
  await report(label);
  if (shot) await page.screenshot({ path: `${out}/vt-${shot}-after.png` });
};
const clickLink = s => { const a = [...document.querySelectorAll(s)].find(a => a.offsetParent !== null) || document.querySelector(s); a.click(); };

await page.goto('http://127.0.0.1:8000/', { waitUntil: 'domcontentloaded' });
await page.waitForFunction(() => window.gitrl?.world.t > 5, { timeout: 60000 });
await report('load /');
await go('ENTER -> /robot', () => window.gitrl.world.emit('enter-press', {}), null, 'robot');
await go('/robot -> /?info#history', clickLink, 'a[href="/?info#history"]', 'info');
await go('/?info -> /telemetry', clickLink, 'a[href="/telemetry"]', 'telemetry');
await go('/telemetry -> /robot', t => location.assign(t), '/robot');
await go('/robot -> /capture/cap_0004', t => location.assign(t), '/capture/cap_0004', 'capture');
await go('/capture -> / (hero)', t => location.assign(t), '/', 'hero');
for (let i = 0; i < 6; i++) await go(`BACK ${i + 1}`, () => history.back());
await go('FORWARD', () => history.forward());
console.log(log.length ? '\n' + [...new Set(log)].join('\n') : '\nno console/page/network errors');
await browser.close();
