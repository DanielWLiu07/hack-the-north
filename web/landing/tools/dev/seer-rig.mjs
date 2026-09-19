import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
try {
  const page = await browser.newPage(), errors = [];
  await page.setViewport({ width: 1440, height: 900 });
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('http://127.0.0.1:8000/pages/seer/dev-seer.html', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.seerReady);
  const audit = await page.evaluate(async () => {
    const s = window.seer, result = { frames: 0, invalid: 0, maxWristGap: 0, maxRootGap: 0, states: [] };
    for (const state of ['idle', 'summoned', 'thinking', 'stumped', 'verdict', 'idle']) {
      s.react(state); const end = performance.now() + 1300;
      await new Promise(resolve => {
        const tick = () => {
          const d = s._debug(); result.frames++;
          if (d.hands.length !== 8 || d.hands.some(p => !p || p.some(v => !Number.isFinite(v))) ||
              !Number.isFinite(d.attachError) || !Number.isFinite(d.rootError)) result.invalid++;
          result.maxWristGap = Math.max(result.maxWristGap, d.attachError || 0);
          result.maxRootGap = Math.max(result.maxRootGap, d.rootError || 0);
          if (performance.now() < end) requestAnimationFrame(tick); else { result.states.push({ state, calls: d.calls, tris: d.tris }); resolve(); }
        }; requestAnimationFrame(tick);
      });
    }
    s.pause(); const before = s._debug().frames;
    await new Promise(r => setTimeout(r, 250)); result.pausePassed = s._debug().frames === before;
    s.resume(); await new Promise(r => setTimeout(r, 250)); result.resumePassed = s._debug().frames > before;
    return result;
  });
  await page.setViewport({ width: 390, height: 844 });
  await page.goto('http://127.0.0.1:8000/telemetry', { waitUntil: 'domcontentloaded' });
  await new Promise(r => setTimeout(r, 2500));
  await page.screenshot({ path: '/tmp/seer-page-mobile.png' });
  console.log(JSON.stringify({ audit, errors }, null, 2));
  if (errors.length || audit.invalid || audit.maxWristGap > 0.05 || audit.maxRootGap > 0.05 ||
      !audit.pausePassed || !audit.resumePassed) throw new Error('Seer rig regression: inspect the audit above');
} finally { await browser.close(); }
