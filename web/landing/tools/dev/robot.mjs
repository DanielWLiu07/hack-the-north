// node tools/dev/robot.mjs <outdir> [extra query, e.g. "who=splat" or "strokes=1.2&line=1.2"]
// dev-robot.html, captured at FROZEN times (?t=) so the frames are reproducible, plus the
// numbers that say the motion is physically honest:
//   pop-*.png      the entrance on the landing's stage camera: peek, dip, pop, landing, catch, the two head beats
//   orbit-*.png    close-ups from four sides (and ?style=0 next to it: is the look the material?)
// Prints: meshes / triangles / draw calls / programs, console errors, and from a 240 Hz sweep
// of the entrance: the lean at every reversal (the overshoot and the counter-leans, in
// degrees), how far the base darts, when it is still, wheel slip on the ground (must be ~0),
// and the largest step between neighbouring samples (nothing may snap).
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const [out = '/tmp/robot-shots', extra = ''] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const browser = await puppeteer.launch({
  executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--allow-file-access-from-files', '--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'],
});
const page = await browser.newPage();
const logs = [];
page.on('console', (m) => { if (m.type() !== 'log') logs.push(`${m.type()}: ${m.text()}`); });
page.on('pageerror', (e) => logs.push(`pageerror: ${e.message}`));
page.on('response', (r) => { if (r.status() >= 400) logs.push(`http ${r.status()}: ${r.url()}`); });
const base = 'http://127.0.0.1:8124/dev-robot.html?shot';
const TIMES = [0.2, 0.45, 0.72, 0.9, 1.05, 1.17, 1.3, 1.5, 1.7, 1.95, 2.3, 2.9, 3.5, 4.2, 5.2];
const shot = async (query, file, w = 1440, h = 810) => {
  await page.setViewport({ width: w, height: h, deviceScaleFactor: 1 });
  await page.goto(`${base}&${query}${extra ? '&' + extra : ''}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.devRobot, { timeout: 120000 });
  await new Promise((r) => setTimeout(r, 1400));                 // the stroke map has to arrive
  await page.screenshot({ path: `${out}/${file}.png` });
};
for (const t of TIMES) await shot(`t=${t}`, `pop-${t.toFixed(2)}`);
const report = { stats: await page.evaluate(() => ({ who: devRobot.who, meshes: devRobot.meshes, tris: devRobot.tris, ...devRobot.info() })) };
for (const [name, yaw] of [['front', 0], ['q', 0.7], ['side', 1.5708], ['back', 3.0]]) await shot(`view=orbit&yaw=${yaw}`, `orbit-${name}`, 700, 1000);
await shot('view=orbit&yaw=0.7&style=0', 'orbit-q-unstyled', 700, 1000);
await shot('view=orbit&yaw=0.7&bg=paper', 'orbit-q-paper', 700, 1000);
await shot('view=orbit&yaw=1.1&dist=1.5&lookY=0.25', 'close-base', 900, 700);
await shot('view=orbit&yaw=0.5&dist=1.7&lookY=2.0', 'close-head', 900, 700);

// motion audit, 240 samples a second
await page.goto(`${base}${extra ? '&' + extra : ''}`, { waitUntil: 'domcontentloaded' });
await page.waitForFunction(() => window.devRobot && window.devRobot.act, { timeout: 120000 });
report.motion = await page.evaluate(() => {
  const d = window.devRobot, act = d.act, R = 0.0825;
  const turns = [], step = { y: 0, x: 0, lean: 0, whip: 0 }; let prev = null, slip = 0, dart = [0, 0], stillAt = 0, x0 = null, roll0 = null;
  for (let i = 0; i <= 240 * 8; i++) {
    const t = i / 240, st = act.state(t);
    if (prev) {
      for (const k in step) if (!(prev.phase !== st.phase && k === 'y')) step[k] = Math.max(step[k], Math.abs(st[k] - prev[k]));
      const pp = prev.prev;
      if (pp && st.phase !== 'peek' && (prev.lean - pp.lean) * (st.lean - prev.lean) < 0 && Math.abs(prev.lean) > 0.004) turns.push([+prev.t.toFixed(2), +(prev.lean * 57.3).toFixed(1)]);
    }
    if (st.phase === 'catch' || st.phase === 'ground') {
      if (x0 == null) { x0 = st.x; roll0 = st.roll; }
      slip = Math.max(slip, Math.abs((st.roll - roll0) * R - (st.x - x0)));
      dart = [Math.min(dart[0], st.x), Math.max(dart[1], st.x)];
      if (Math.abs(st.lean) > 0.014) stillAt = t;
    }
    prev = { ...st, prev: prev ? { lean: prev.lean } : null };
  }
  return { timeline: { launch: +act.launchAt.toFixed(2), apex: +act.apexAt.toFixed(2), land: +act.landAt.toFixed(2), settle: +act.settleAt.toFixed(2), title: +act.titleAt.toFixed(2), viewer: +act.viewerAt.toFixed(2) },
    leanReversalsDeg: turns.slice(0, 9), baseDartMetres: dart.map((v) => +v.toFixed(3)), leanUnder0p8degAfter: +stillAt.toFixed(2),
    wheelSlipMetres: +slip.toExponential(1), maxStepPer240th: Object.fromEntries(Object.entries(step).map(([k, v]) => [k, +v.toFixed(4)])) };
});
// contact sheets, so one look covers the whole run
const sheet = async (file, names, cols, cellW, crop) => {
  const html = `<body style="margin:0;background:#121116;display:grid;grid-template-columns:repeat(${cols},${cellW}px)">` +
    names.map((n) => `<div style="width:${cellW}px;height:${crop.h * cellW / crop.w}px;overflow:hidden;position:relative"><img src="file://${out}/${n}.png" style="position:absolute;width:${crop.full * cellW / crop.w}px;left:${-crop.x * cellW / crop.w}px;top:${-crop.y * cellW / crop.w}px"><span style="position:absolute;left:6px;top:4px;font:11px monospace;color:#8e8b86">${n}</span></div>`).join('');
  const p2 = await browser.newPage();
  const rows = Math.ceil(names.length / cols);
  await p2.setViewport({ width: cols * cellW, height: Math.round(rows * crop.h * cellW / crop.w), deviceScaleFactor: 1 });
  await p2.goto('file://' + out + '/', { waitUntil: 'domcontentloaded' }).catch(() => {});
  await p2.setContent(html, { waitUntil: 'load' });
  await p2.screenshot({ path: `${out}/${file}.png` }); await p2.close();
};
await sheet('sheet-pop', TIMES.map((t) => `pop-${t.toFixed(2)}`), 5, 360, { full: 1440, x: 0, y: 250, w: 640, h: 560 });
await sheet('sheet-orbit', ['orbit-front', 'orbit-q', 'orbit-side', 'orbit-back', 'orbit-q-unstyled', 'orbit-q-paper'], 6, 300, { full: 700, x: 0, y: 0, w: 700, h: 1000 });
report.logs = logs;
console.log(JSON.stringify(report, null, 1));
await browser.close();
