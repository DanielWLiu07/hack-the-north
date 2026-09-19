// node tools/dev/framewatch.mjs [base=http://127.0.0.1:8000] [pages=/telemetry] [outdir] [--json] [--exercise]
//
// A regression gate for the pages the demo beats run on. It does not measure "is it fast" in the
// abstract: it checks the promises the page is supposed to keep, and prints PASS or FAIL per
// promise so a change can be judged in one line.
//
//   ONE RENDERER   every WebGL context the page creates is counted by hooking getContext before
//                  any script runs, so a second renderer is caught even if its canvas is hidden,
//                  offscreen, or created and thrown away. A second context costs its own copy of
//                  every shader, buffer and texture, and the two then fight over the GPU.
//   FRAME BUDGET   measured in STEADY STATE, after the page has settled: a hitch during loading
//                  is not what a judge sees when they click. 60 fps is a 16.7 ms budget; a frame
//                  over 50 ms is a visible stall.
//   NO DRIFT       the JS heap and the live GPU objects, sampled twice a few seconds apart.
//                  Counts that only ever go up are a leak that a 90 s demo finds.
//   --press=LABEL  press a control the destructive filter skips (it writes to a real service)
//   --accept=A,B   record checks that are known and decided, so the exit code speaks only about
//                  what is NEW. A gate that fails every run on something already settled is a gate
//                  people learn to ignore; an accepted check still prints its real state, and says
//                  so loudly if it starts passing, which means the exception can be retired.
//   --exercise     a page whose new work hides behind a button is not tested by watching it sit
//                  there. This clicks every control on the page three times over and checks the
//                  same promises afterwards: a view that allocates on each switch and frees
//                  nothing shows up as a climb across the rounds, which idling never reveals.
//
// Thresholds are deliberately loose: this is a gate against regressions, not a style opinion.
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const argv = process.argv.slice(2).filter((a) => !a.startsWith('--'));
const asJson = process.argv.includes('--json');
const EXERCISE = process.argv.includes('--exercise');
// --press "label" opts one control back in, for when acting for real is the point of the run
const ALLOW = process.argv.filter((a) => a.startsWith('--press=')).map((a) => a.slice(8));
const ACCEPT = process.argv.filter((a) => a.startsWith('--accept=')).flatMap((a) => a.slice(9).split(',')).map((x) => x.trim()).filter(Boolean);
const [base = 'http://127.0.0.1:8000', pagesArg = '/telemetry', outDir = ''] = argv;
const LIMIT = { contexts: 1, fps: 55, worstFrameMs: 50, longFrames: 4, heapDriftMB: 8 };
const WATCH_MS = 15000, DRIFT_MS = 6000, SETTLE_MAX_MS = 25000;
// A fixed settle is a trap on a page that streams megabytes in: measure too early and loading
// looks exactly like a leak. Wait for quiet before starting — and quiet means BOTH the JS heap
// and the GPU objects. Watching the heap alone is not enough: buffers and programs live outside
// it, so a page can look settled while it is still uploading geometry, and the "before" reading
// is then taken mid-upload. That misreads normal startup as a leak, which is precisely the
// false alarm this gate exists to avoid raising.
const settle = (page) => page.evaluate((maxMs) => new Promise((res) => {
  const t0 = performance.now(); let prev = null;
  const look = () => {
    const l = window.__count.live;
    const now = { mb: (performance.memory?.usedJSHeapSize || 0) / 1048576,
      gpu: l.texture + l.buffer + l.program + l.framebuffer };
    const quiet = prev && Math.abs(now.mb - prev.mb) < 2 && now.gpu === prev.gpu;
    if (quiet || performance.now() - t0 > maxMs) return res({ ms: Math.round(performance.now() - t0), settled: !!quiet });
    prev = now; setTimeout(look, 1500); };
  setTimeout(look, 1500);
}), SETTLE_MAX_MS).catch(() => ({ ms: 0, settled: false }));
if (outDir) mkdirSync(outDir, { recursive: true });
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
const results = [];
for (const path of pagesArg.split(',')) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });
  const logs = [];
  page.on('console', (m) => { const t = m.text(); if ((m.type() === 'error' || m.type() === 'warning') && !/Failed to load resource/.test(t)) logs.push(`${m.type()}: ${t.slice(0, 130)}`); });
  page.on('pageerror', (e) => logs.push(`pageerror: ${e.message.slice(0, 130)}`));
  page.on('response', (r) => { if (r.status() >= 500) logs.push(`http ${r.status()}: ${new URL(r.url()).pathname}`); });
  // Count every graphics context, and instrument the context itself, before a line of page
  // script runs. Going through the GL API rather than looking for a THREE.WebGLRenderer on
  // window means this works whatever the page does with its renderer, including keeping it
  // private inside a module — which is the normal case and made the first version print dashes.
  await page.evaluateOnNewDocument(() => {
    window.__gl = [];
    window.__count = { draws: 0, frames: 0, live: { texture: 0, buffer: 0, program: 0, framebuffer: 0 } };
    const c = window.__count;
    const instrument = (ctx) => {
      if (!ctx || ctx.__watched) return ctx; ctx.__watched = true;
      for (const m of ['drawElements', 'drawArrays', 'drawElementsInstanced', 'drawArraysInstanced']) {
        const real = ctx[m]; if (real) ctx[m] = function (...a) { c.draws++; return real.apply(this, a); }; }
      // live GPU objects = created minus deleted. Only ever going up is a leak a demo will find.
      for (const kind of ['texture', 'buffer', 'program', 'framebuffer']) {
        const C = 'create' + kind[0].toUpperCase() + kind.slice(1), D = 'delete' + kind[0].toUpperCase() + kind.slice(1);
        const rc = ctx[C], rd = ctx[D];
        if (rc) ctx[C] = function (...a) { c.live[kind]++; return rc.apply(this, a); };
        if (rd) ctx[D] = function (...a) { if (a[0]) c.live[kind]--; return rd.apply(this, a); }; }
      return ctx;
    };
    const hook = (proto, label) => { const real = proto.getContext;
      proto.getContext = function (type, ...rest) {
        const ctx = real.call(this, type, ...rest);
        if (/webgl/i.test(type)) {
          // the stack names the file and line that asked for the context: without it, "a second
          // context appeared" is a fact nobody can act on
          // skip the library's own frames: "three.module.js made a context" is true of every
          // renderer and tells nobody which file asked for a second one
          const stack = (new Error().stack || '').split('\n').slice(1)
            .filter((l) => !/evaluateOnNewDocument|<anonymous>:|framewatch/.test(l));
          const app = stack.find((l) => !/three\.module\.js|\/vendor\//.test(l)) || stack[0] || '';
          const at = app.trim().replace(/^at\s+/, '').slice(0, 150);
          // keep the element: an id or a size set just after the renderer is constructed is not
          // there yet at getContext time, and reporting the creation-time snapshot would say a
          // canvas is anonymous and 300x150 when by the time anyone looks it is neither
          window.__glCanvas = window.__glCanvas || [];
          window.__glCanvas.push(this);
          window.__gl.push({ type, label, at });
          instrument(ctx); }
        return ctx; }; };
    hook(HTMLCanvasElement.prototype, 'canvas');
    if (typeof OffscreenCanvas === 'function') hook(OffscreenCanvas.prototype, 'offscreen');
    window.__long = [];
    new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__long.push(+e.duration.toFixed(0)); }).observe({ type: 'longtask', buffered: true });
  });
  await page.goto(base + path, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch((e) => logs.push('goto: ' + e.message.slice(0, 90)));
  const settled = await settle(page);
  if (!settled.settled) logs.push(`note: heap still moving after ${(settled.ms / 1000).toFixed(0)} s; readings below may include loading`);
  // what the GPU is actually being asked to hold, and how much work each frame costs
  const probe = () => page.evaluate(() => ({ live: { ...window.__count.live }, draws: window.__count.draws,
    heapMB: +((performance.memory?.usedJSHeapSize || 0) / 1048576).toFixed(1) })).catch(() => ({ live: {}, draws: 0, heapMB: 0 }));
  let first = await probe();
  const frames = await page.evaluate((ms) => new Promise((res) => {
    const out = []; let last = performance.now(), t0 = last, n = 0, worst = 0;
    const d0 = window.__count.draws;
    const tick = () => { const now = performance.now(), d = now - last; last = now; n++;
      if (d > 30) out.push({ at: +(now - t0).toFixed(0), ms: +d.toFixed(0) });
      worst = Math.max(worst, d);
      if (now - t0 < ms) requestAnimationFrame(tick);
      else res({ fps: +(n / ((performance.now() - t0) / 1000)).toFixed(1), worst: +worst.toFixed(0), long: out,
        drawsPerFrame: +((window.__count.draws - d0) / n).toFixed(1) }); };
    requestAnimationFrame(tick);
  }), WATCH_MS).catch(() => ({ fps: 0, worst: 0, long: [], drawsPerFrame: 0 }));
  // Press everything, three times over, and see whether the page gives back what it takes.
  let exercised = null, navigated = null;
  if (EXERCISE) {
    const home = base + path, rounds = [];
    // A control that navigates ends the run otherwise: the frame detaches and every later read
    // throws. So each click is guarded, and a navigation is recorded and undone rather than fatal.
    const alive = async () => { try { await page.evaluate(() => 1); return true; } catch { return false; } };
    const backHome = async () => { try { await page.goto(home, { waitUntil: 'domcontentloaded' }); await settle(page); } catch {} };
    for (let round = 0; round < 3; round++) {
      // A gate that presses every button will eventually press one that does something real. The
      // Sentry board's [mark fixed] writes to Sentry, and only its confirm-arm (a second press
      // within 4 s, under a changed label) stopped these runs from resolving live issues — luck,
      // not design. Anything that reads as destructive is skipped unless it is asked for by name.
      const DESTRUCTIVE = /\b(mark fixed|sure\?|remove|delete|resolve|ignore|undo|reset|clear|discard|approve|merge|commit|send|publish)\b/i;
      const labels = await page.evaluate(() => [...document.querySelectorAll('button')]
        .filter((b) => !b.disabled && b.offsetParent !== null).map((b) => (b.textContent || '').trim()).filter(Boolean)).catch(() => []);
      const skipped = labels.filter((l) => DESTRUCTIVE.test(l) && !ALLOW.includes(l));
      if (skipped.length && round === 0) logs.push(`note: not pressed (would act for real): ${[...new Set(skipped)].join(', ')}`);
      const seen = new Set();
      for (const label of labels) {
        if (seen.has(label)) continue; seen.add(label);
        if (DESTRUCTIVE.test(label) && !ALLOW.includes(label)) continue;
        try {
          await page.evaluate((l) => { const b = [...document.querySelectorAll('button')]
            .find((x) => (x.textContent || '').trim() === l && !x.disabled && x.offsetParent !== null); if (b) b.click(); }, label);
          await new Promise((r) => setTimeout(r, 320));
          if (!(await alive()) || page.url() !== home) { navigated = navigated || label; await backHome(); }
        } catch { navigated = navigated || label; await backHome(); }
      }
      await new Promise((r) => setTimeout(r, 1500));
      const p2 = await probe();
      rounds.push({ round: round + 1, clicked: seen.size, ...p2.live, heapMB: p2.heapMB,
        contexts: await page.evaluate(() => window.__gl.length).catch(() => 0) });
    }
    exercised = rounds;
    if (navigated) logs.push(`note: "${navigated}" navigates; the page was reloaded and counts restart there`);
  }
  // With an exercise, comparing before-exercise to after-exercise is not a leak test: it just
  // counts what the clicking legitimately built. Re-base so the drift checks answer the question
  // that matters — once the hands come off, does it keep climbing?
  if (exercised) first = await probe();
  await new Promise((r) => setTimeout(r, DRIFT_MS));
  const second = await probe();
  const gl = await page.evaluate(() => (window.__gl || []).map((g, i) => {
    const c = (window.__glCanvas || [])[i];
    return { ...g, id: (c && (c.id || c.className)) || '', w: c ? (c.clientWidth || c.width) : 0, h: c ? (c.clientHeight || c.height) : 0 };
  })).catch(() => []);
  if (outDir) await page.screenshot({ path: `${outDir}/${path.replace(/[^\w]+/g, '_') || 'root'}.png` });
  const drift = (k) => (second.live[k] ?? 0) - (first.live[k] ?? 0);
  const checks = [
    ['one WebGL context', gl.length <= LIMIT.contexts, `${gl.length} context${gl.length === 1 ? '' : 's'}${gl.length > 1 ? ':\n' + gl.map((g) => `          ${g.label}#${g.id || '(no id)'} ${g.w}x${g.h}  created at ${g.at || 'unknown'}`).join('\n') : ''}`],
    ['steady frame rate', frames.fps >= LIMIT.fps, `${frames.fps} fps`],
    ['no stalled frame', frames.worst <= LIMIT.worstFrameMs, `worst ${frames.worst} ms`],
    ['few long frames', frames.long.length <= LIMIT.longFrames, `${frames.long.length} over 30 ms in ${WATCH_MS / 1000} s${frames.long.length ? ' (' + frames.long.slice(0, 4).map((f) => f.ms + 'ms').join(', ') + ')' : ''}`],
    ['no texture leak', drift('texture') <= 0, `live textures ${first.live.texture} -> ${second.live.texture}`],
    ['no buffer leak', drift('buffer') <= 2, `live buffers ${first.live.buffer} -> ${second.live.buffer}`],
    ['no program leak', drift('program') <= 0, `live programs ${first.live.program} -> ${second.live.program}`],
    ['no heap drift', second.heapMB - first.heapMB <= LIMIT.heapDriftMB, `heap ${first.heapMB} -> ${second.heapMB} MB`],
    ...(exercised ? [
      ['one context after use', exercised[2].contexts <= LIMIT.contexts, `${exercised[2].contexts} after clicking ${exercised[0].clicked} controls x3`],
      ['use frees what it takes', ['texture', 'buffer', 'program'].every((k) => exercised[2][k] - exercised[1][k] <= 0),
        ['texture', 'buffer', 'program'].map((k) => `${k}s ${exercised[0][k]}/${exercised[1][k]}/${exercised[2][k]}`).join(', ')],
      ['heap steady under use', exercised[2].heapMB - exercised[1].heapMB <= LIMIT.heapDriftMB, `heap ${exercised.map((r) => r.heapMB).join(' -> ')} MB`],
    ] : []),
    ['clean console', logs.filter((l) => !l.startsWith('note:')).length === 0, logs.filter((l) => !l.startsWith('note:'))[0] || 'no errors or warnings'],
  ];
  const accepted = (name) => ACCEPT.some((a) => name.toLowerCase().includes(a.toLowerCase()));
  const newlyBroken = checks.filter(([n, ok]) => !ok && !accepted(n));
  const retirable = checks.filter(([n, ok]) => ok && accepted(n));
  results.push({ path, ok: newlyBroken.length === 0, checks, accepted, newlyBroken, retirable, gl, frames, live: second.live, settled, exercised, navigated, logs });
  await page.close();
}
if (asJson) console.log(JSON.stringify(results, null, 1));
else for (const r of results) {
  console.log(`\n${base}${r.path}   ${r.ok ? 'PASS' : 'FAIL'}${ACCEPT.length ? `  (accepted: ${ACCEPT.join(', ')})` : ''}`);
  for (const [name, ok, detail] of r.checks) {
    const tag = ok ? (r.accepted(name) ? 'ok!' : 'ok') : (r.accepted(name) ? 'known' : 'FAIL');
    console.log(`  ${tag.padEnd(5)} ${name.padEnd(20)} ${detail}`);
  }
  if (r.retirable.length) console.log(`  NOTE  accepted but now PASSING — retire the exception: ${r.retirable.map((c) => c[0]).join(', ')}`);
  console.log(`  info  settled in ${(r.settled.ms / 1000).toFixed(0)} s, ${r.frames.drawsPerFrame} draw calls per frame, live: ${Object.entries(r.live).map(([k, v]) => `${v} ${k}s`).join(', ')}`);
}
if (outDir) writeFileSync(`${outDir}/framewatch.json`, JSON.stringify(results, null, 1));
await browser.close();
process.exit(results.every((r) => r.ok) ? 0 : 1);
