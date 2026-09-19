// Motion audit: every frame, for every chain in watchers.js and tentacles.js, record
// the largest single-joint rotation (deg/frame) and the head/tip speed. A "freak-out"
// is a joint turning > 20 deg in one frame or a head moving > 30 u/s.
import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const [url, seconds = '26', mode = 'auto', startAt = '5.5'] = process.argv.slice(2);
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'], protocolTimeout: 240000 });
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 810, deviceScaleFactor: 1 });
  const errs = [];
  page.on('pageerror', (e) => errs.push(e.message.slice(0, 200)));
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(() => window.gitrl?.slots.some(s => s?.watchers), { timeout: 60000 });
  if (mode === 'mouse') {           // a REAL pointer: fast human-like sweeps, stops, clicks
    (async () => { const t0 = Date.now();
      while (Date.now() - t0 < seconds * 1000) {
        const T = (Date.now() - t0) / 1000;
        const x = 720 + Math.sin(T * 2.9) * 650 * Math.sin(T * 0.7), y = 405 + Math.cos(T * 2.1) * 360;
        if (Math.floor(T) % 6 < 4) await page.mouse.move(x, y).catch(() => {});
        if (Math.floor(T * 10) % 70 === 0) await page.mouse.click(x, y).catch(() => {});
        await new Promise((r) => setTimeout(r, 16));
      } })();
  }
  const report = await page.evaluate(({ seconds, startAt }) => new Promise((resolve) => {
    const g = window.gitrl, mods = g.slots.filter(Boolean);
    const chains = [];
    for (const m of mods) {
      if (m.watchers) m.watchers.forEach((w, i) => chains.push({ name: `watcher${i}:${w.style}:${w.edge}:${w.depth}:${w.temper}:${w.head.kind}`,
        joints: w.joints, head: w.head.group }));
      if (m.actors) for (const [k, A] of Object.entries({ welder: m.actors.welder, poker: m.actors.poker, inspector: m.actors.inspector,
        bg0: m.actors.background[0], bg1: m.actors.background[1], bg2: m.actors.background[2], bg3: m.actors.background[3] }))
        if (A && A.chain) chains.push({ name: `tentacle:${k}`, joints: A.chain.joints, head: A.chain.tip });
    }
    const P = new g.world.camera.position.constructor();
    for (const c of chains) { c.prevQ = c.joints.map((j) => j.quaternion.clone()); c.prevP = null; c.maxRot = 0; c.maxSpd = 0; c.events = []; c.nan = 0; }
    const tEnd = g.world.t + seconds; let lastT = g.world.t, frames = 0;
    function tick() {
      const t = g.world.t, dt = Math.max(1e-3, t - lastT); lastT = t; frames++;
      if (t > startAt) for (const c of chains) {
        let worst = 0, at = -1;
        c.joints.forEach((j, k) => { const a = 2 * Math.acos(Math.min(1, Math.abs(j.quaternion.dot(c.prevQ[k])))) * 57.3;
          if (!(a >= 0)) c.nan++; if (a > worst) { worst = a; at = k; } c.prevQ[k].copy(j.quaternion); });
        c.head.getWorldPosition(P);
        const spd = c.prevP ? P.distanceTo(c.prevP) / dt : 0;
        c.prevP = (c.prevP || P.clone()).copy(P);
        c.maxRot = Math.max(c.maxRot, worst); c.maxSpd = Math.max(c.maxSpd, spd);
        if (worst > 20 || spd > 30) c.events.push({ t: +t.toFixed(2), joint: at, rot: +worst.toFixed(0), spd: +spd.toFixed(0) });
      } else for (const c of chains) c.joints.forEach((j, k) => c.prevQ[k].copy(j.quaternion));
      if (t < tEnd) requestAnimationFrame(tick);
      else resolve({ frames, chains: chains.map((c) => ({ name: c.name, maxRot: +c.maxRot.toFixed(1), maxSpd: +c.maxSpd.toFixed(1),
        n: c.events.length, nan: c.nan, first: c.events.slice(0, 4) })) });
    }
    requestAnimationFrame(tick);
  }), { seconds: +seconds, startAt: +startAt });
  report.chains.sort((a, b) => b.n - a.n || b.maxRot - a.maxRot);
  console.log(`frames ${report.frames}  errs ${JSON.stringify(errs)}`);
  for (const c of report.chains) console.log(`${String(c.n).padStart(4)} events  maxRot ${String(c.maxRot).padStart(6)}°/f  maxSpd ${String(c.maxSpd).padStart(6)} u/s  nan ${c.nan}  ${c.name}  ${c.n ? JSON.stringify(c.first) : ''}`);
} finally { await browser.close(); }
