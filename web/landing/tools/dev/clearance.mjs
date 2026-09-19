// Conservative capsule audit of non-neighbour links, plus root and head tracking.
import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const [url = 'http://127.0.0.1:8000/?auto', seconds = '13', width = '1440', height = '810'] = process.argv.slice(2);
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
try {
  const page = await browser.newPage(), errors = [];
  await page.setViewport({ width: +width, height: +height });
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.gitrl?.slots.some(s => s?.watchers));
  const result = await page.evaluate(seconds => new Promise(resolve => {
    const g = window.gitrl, watchers = g.slots.find(s => s?.watchers).watchers;
    const V = g.camera.position.constructor, a = new V(), b = new V(), c = new V(), d = new V(), e = new V();
    const clamp = n => Math.max(0, Math.min(1, n));
    function distance(p, q, r, s) {
      a.subVectors(q, p); b.subVectors(s, r); c.subVectors(p, r);
      const aa = a.dot(a), bb = b.dot(b), ab = a.dot(b), ac = a.dot(c), bc = b.dot(c);
      const denom = aa * bb - ab * ab;
      let u = denom > 1e-10 ? clamp((ab * bc - ac * bb) / denom) : 0;
      let v = (ab * u + bc) / bb;
      if (v < 0) { v = 0; u = clamp(-ac / aa); }
      else if (v > 1) { v = 1; u = clamp((ab - ac) / aa); }
      return d.copy(p).addScaledVector(a, u).distanceTo(e.copy(r).addScaledVector(b, v));
    }
    const records = watchers.map(w => ({ id: w.i, kind: w.head.kind, minRatio: Infinity, overlaps: 0, first: [], rootOnscreen: 0, headError: 0,
      points: [...w.joints, w.tip].map(() => new V()) }));
    let frames = 0, last = -1; const end = g.world.t + seconds;
    function tick() {
      const t = g.world.t;
      if (t > last + 0.045) {
        last = t; frames++;
        watchers.forEach((w, index) => {
          if (w.hidden || !w.inited) return;
          const rec = records[index], points = rec.points;
          w.joints.forEach((j, k) => points[k].setFromMatrixPosition(j.matrixWorld));
          points[w.N].setFromMatrixPosition(w.tip.matrixWorld);
          rec.headError = Math.max(rec.headError, w.headPos.distanceTo(points[w.N]));
          a.copy(w.rootHome).project(g.camera);
          if (Math.abs(a.x) < 1 && Math.abs(a.y) < 1) rec.rootOnscreen++;
          // Immediate neighbours share designed hinge/clevis volumes. Test links
          // at least three joints apart, with the full outer housing radius.
          for (let j = 0; j < w.N - 3; j++) for (let k = j + 3; k < w.N; k++) {
            const radii = (w.scales[j] + w.scales[k]) * (w.style === 'arm' ? 0.9 : 0.56);
            const ratio = distance(points[j], points[j + 1], points[k], points[k + 1]) / radii;
            rec.minRatio = Math.min(rec.minRatio, ratio);
            if (ratio < 1) { rec.overlaps++; if (rec.first.length < 3) rec.first.push({ t: +t.toFixed(2), j, k, ratio: +ratio.toFixed(3) }); }
          }
        });
      }
      if (t < end) requestAnimationFrame(tick);
      else resolve({ frames, chains: records.map(({ points, ...r }) => ({ ...r, minRatio: +r.minRatio.toFixed(3), headError: +r.headError.toFixed(6) })) });
    }
    requestAnimationFrame(tick);
  }), +seconds);
  console.log(JSON.stringify({ errors, ...result }, null, 2));
} finally { await browser.close(); }
