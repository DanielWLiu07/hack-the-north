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
//
// THE EXERCISER MUST NEVER CAUSE A SIDE EFFECT. This is the gate's own invariant, not a
// per-page precaution: any page it is ever pointed at may have a write behind a button, and
// nobody can enumerate in advance which label is dangerous. An earlier version of this file
// tried to, with a deny-list of words like "remove" and "resolve". It failed: pressing every
// visible control resolved nine real Sentry issues in the live project, and the reasoning that
// said it could not — that a confirm-arm would swallow synthetic clicks — was never measured,
// only read off the page's source. So the rule here is enforced, not named: while exercising,
// every request that is not a GET/HEAD/OPTIONS is ABORTED at the network layer, and the gate
// reports exactly what it stopped. A label nobody predicted still cannot write. --allow-writes
// turns that off, and it must NEVER be pointed at anything but a scratch server: this gate is now
// the only thing standing between an automated clicker and a live, paid service.
//
// KNOWN SIDE EFFECTS BEHIND BUTTONS ON THESE PAGES — add to this list, never rely on it:
//   POST /api/sentry/issues/<id>/resolve   resolves a REAL issue in the live project. Nine were
//                                          resolved this way before the block existed.
//   POST /api/sentry/issues/<id>/remove    removes it from the board, after a re-check.
//   POST /api/seer/ask                     COSTS MONEY. It starts a billed Sentry Seer run, and
//                                          the user's standing rule is that a Seer run is asked
//                                          for first. An automated clicker must never fire one.
//
//   --press=LABEL  the ONLY controls pressed, when given: an allow-list beats a deny-list,
//                  because the deny-list failed exactly where nobody thought to look
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
const ALLOW_WRITES = process.argv.includes('--allow-writes');
const [base = 'http://127.0.0.1:8000', pagesArg = '/telemetry', outDir = ''] = argv;
const LIMIT = { contexts: 1, fps: 55, worstFrameMs: 50, longFrames: 4, heapDriftMB: 8 };
const WATCH_MS = 15000, DRIFT_MS = 6000, SETTLE_MAX_MS = 60000;   // /robot streams ~10 MB and needs most of a minute
// A fixed settle is a trap on a page that streams megabytes in: measure too early and loading
// looks exactly like a leak. Wait for quiet before starting — and quiet means BOTH the JS heap
// and the GPU objects. Watching the heap alone is not enough: buffers and programs live outside
// it, so a page can look settled while it is still uploading geometry, and the "before" reading
// is then taken mid-upload. That misreads normal startup as a leak, which is precisely the
// false alarm this gate exists to avoid raising.
const settle = async (page, inflight) => {
  // Quiet has to mean the page has STOPPED FETCHING as well as stopped allocating. Heap and GPU
  // counts alone said /robot was settled after 5 s while it was still streaming point clouds for
  // another 85, and every count read in that window was loading mistaken for a leak — 1.2 GB of
  // it, against a true settled size of 190 MB. Network idle is the signal that actually holds.
  const t0 = Date.now(); let prev = null, quietSince = 0;
  while (Date.now() - t0 < SETTLE_MAX_MS) {
    await new Promise((r) => setTimeout(r, 1500));
    const now = await page.evaluate(() => { try { window.gc && window.gc(); } catch {}
      const l = window.__count.live;
      return { mb: (performance.memory?.usedJSHeapSize || 0) / 1048576, gpu: l.texture + l.buffer + l.program + l.framebuffer };
    }).catch(() => null);
    if (!now) break;
    inflight.prune && inflight.prune();
    const idle = !inflight.busy();
    const still = prev && Math.abs(now.mb - prev.mb) < 2 && now.gpu === prev.gpu;
    prev = now;
    // Eight seconds of CONTINUOUS quiet, not three. /robot downloads its clouds in about a second
    // and then parses and uploads them in bursts for another minute: three seconds of calm happens
    // between two bursts, and declaring settled there is what had the gate pressing buttons mid-parse
    // and reading a 1.2 GB transient as if it were the page's resting size.
    if (idle && still) { if (!quietSince) quietSince = Date.now();
      if (Date.now() - quietSince >= 8000) return { ms: Date.now() - t0, settled: true }; }
    else quietSince = 0;
  }
  return { ms: Date.now() - t0, settled: false };
};
if (outDir) mkdirSync(outDir, { recursive: true });
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  // --expose-gc is not a nicety: usedJSHeapSize without a forced collection reports "allocated and
  // not yet collected", not memory in use. On /robot that reads 1.2 GB while the true retained size
  // is 192 MB, because V8 has a 4 GB ceiling and no reason to collect early. Every heap number here
  // is taken after an explicit gc() for that reason.
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl', '--js-flags=--expose-gc'] });
const results = [];
for (const path of pagesArg.split(',')) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });
  // In-flight requests, EXCLUDING the ones that are never meant to finish. A live page holds an
  // endless multipart camera response and two SSE streams open for ever, so a naive count sits at
  // a permanent 3 and the page can never look network-idle. That mattered more than it sounds:
  // settle then always timed out, every leak check printed INCONCLUSIVE — and the run still exited
  // 0, so a page holding a stream reported a pass while having measured nothing at all.
  // Two defences, because guessing content types alone is the kind of enumeration that fails:
  //   * a response of text/event-stream or multipart/* stops counting the moment its headers land;
  //   * anything still open after 6 s stops counting regardless of what it claims to be.
  const logs = [], inflight = { n: 0 }, pending = new Map();
  const done = (req) => { if (pending.delete(req)) inflight.n = Math.max(0, inflight.n - 1); };
  inflight.prune = () => { const now = Date.now(); for (const [req, at] of pending) if (now - at > 6000) done(req); };
  // "Is anything SUBSTANTIAL still loading?" — not "is the network perfectly silent", which on a
  // live page it never is. /telemetry polls the robot link twice every ~3 s, so its longest gap
  // with no request at all is 6 s and a demand for 8 s of total silence could never be met: the
  // gate failed every run on a page that was in fact completely stable. A poll finishes in
  // milliseconds; a point cloud does not. So what counts is a request that has been open a while.
  inflight.busy = () => { const now = Date.now(); for (const at of pending.values()) if (now - at > 1500) return true; return false; };
  page.on('request', (r) => { pending.set(r, Date.now()); inflight.n++; });
  for (const ev of ['requestfinished', 'requestfailed']) page.on(ev, (r) => done(r));
  page.on('console', (m) => { const t = m.text(); if ((m.type() === 'error' || m.type() === 'warning') && !/Failed to load resource/.test(t)) logs.push(`${m.type()}: ${t.slice(0, 130)}`); });
  page.on('pageerror', (e) => logs.push(`pageerror: ${e.message.slice(0, 130)}`));
  page.on('response', (r) => {
    if (r.status() >= 500) logs.push(`http ${r.status()}: ${new URL(r.url()).pathname}`);
    if (/text\/event-stream|multipart\//i.test(r.headers()['content-type'] || '')) done(r.request());   // a stream, not a pending load
  });
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
  const settled = await settle(page, inflight);
  if (!settled.settled) logs.push(`note: heap still moving after ${(settled.ms / 1000).toFixed(0)} s; readings below may include loading`);
  // what the GPU is actually being asked to hold, and how much work each frame costs
  const probe = () => page.evaluate(() => { try { window.gc && window.gc(); } catch {}
    return { live: { ...window.__count.live }, draws: window.__count.draws,
      heapMB: +((performance.memory?.usedJSHeapSize || 0) / 1048576).toFixed(1) }; }).catch(() => ({ live: {}, draws: 0, heapMB: 0 }));
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
  const blockedWrites = [];
  if (EXERCISE) {
    // ENFORCEMENT, not etiquette: nothing this clicker does may reach a real service.
    if (!ALLOW_WRITES) {
      await page.setRequestInterception(true);
      page.on('request', (req) => {
        if (req.isInterceptResolutionHandled?.()) return;
        const m = req.method();
        if (m === 'GET' || m === 'HEAD' || m === 'OPTIONS') return req.continue().catch(() => {});
        blockedWrites.push(`${m} ${new URL(req.url()).pathname}`);
        req.abort('blockedbyclient').catch(() => {});
      });
    }
    const home = base + path, rounds = [];
    // A control that navigates ends the run otherwise: the frame detaches and every later read
    // throws. So each click is guarded, and a navigation is recorded and undone rather than fatal.
    const alive = async () => { try { await page.evaluate(() => 1); return true; } catch { return false; } };
    // A control that navigates costs a reload. Re-settling in full after each one turned /robot,
    // which streams 10 MB and settles in ~26 s, into a run of many minutes; a short fixed wait is
    // enough to carry on clicking, and the counts that matter are read after the rounds anyway.
    const backHome = async () => { try { await page.goto(home, { waitUntil: 'domcontentloaded' }); await new Promise((r) => setTimeout(r, 2500)); } catch {} };
    const deadline = Date.now() + 120000;                 // the exercise always ends, even on a page that fights it
    for (let round = 0; round < 3 && Date.now() < deadline; round++) {
      // A gate that presses every button will eventually press one that does something real. The
      // Sentry board's [mark fixed] writes to Sentry, and only its confirm-arm (a second press
      // within 4 s, under a changed label) stopped these runs from resolving live issues — luck,
      // not design. Anything that reads as destructive is skipped unless it is asked for by name.
      // kept only to NAME what it skips; the network block above is what makes it safe
      // This list is for MEASUREMENT QUALITY, not safety — the network block above is the safety.
      // It still matters: a blocked write leaves its feature half-done, which distorts the leak
      // counts. And it is still incomplete by nature; "ask Seer" (a billable run) was on no
      // version of it until the block caught the request nobody predicted.
      const DESTRUCTIVE = /\b(ask|seer|mark fixed|sure\?|confirm|remove|delete|resolve|ignore|undo|reset|clear|discard|approve|merge|commit|send|publish|run|apply|save)\b/i;
      const labels = await page.evaluate(() => [...document.querySelectorAll('button')]
        .filter((b) => !b.disabled && b.offsetParent !== null).map((b) => (b.textContent || '').trim()).filter(Boolean)).catch(() => []);
      const skipped = labels.filter((l) => DESTRUCTIVE.test(l) && !ALLOW.includes(l));
      if (skipped.length && round === 0) logs.push(`note: not pressed (would act for real): ${[...new Set(skipped)].join(', ')}`);
      const seen = new Set(); let pressed = 0;
      for (const label of labels) {
        if (Date.now() > deadline) break;
        if (seen.has(label)) continue; seen.add(label);
        if (ALLOW.length ? !ALLOW.includes(label) : DESTRUCTIVE.test(label)) continue;
        try {
          await page.evaluate((l) => { const b = [...document.querySelectorAll('button')]
            .find((x) => (x.textContent || '').trim() === l && !x.disabled && x.offsetParent !== null); if (b) b.click(); }, label);
          pressed++;
          await new Promise((r) => setTimeout(r, 320));
          if (!(await alive()) || page.url() !== home) { navigated = navigated || label; await backHome(); }
        } catch { navigated = navigated || label; await backHome(); }
      }
      await new Promise((r) => setTimeout(r, 1500));
      const p2 = await probe();
      rounds.push({ round: round + 1, clicked: pressed, offered: seen.size, ...p2.live, heapMB: p2.heapMB,
        contexts: await page.evaluate(() => window.__gl.length).catch(() => 0) });
    }
    exercised = rounds;
    if (Date.now() > deadline) logs.push('note: the exercise hit its 120 s ceiling; fewer rounds than planned');
    if (!ALLOW_WRITES) { try { await page.setRequestInterception(false); } catch {} }
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
    // A run that never settled has measured a page in motion, and every count below it is suspect.
    // It must not be possible to scan this output and see green: it fails, and --accept is the only
    // way past it, which at least forces someone to say out loud that they know.
    ['page settled', settled.settled, settled.settled ? `quiet after ${(settled.ms / 1000).toFixed(0)} s` : `NEVER SETTLED in ${(settled.ms / 1000).toFixed(0)} s — every count below was read while the page was still changing`],
    ['steady frame rate', frames.fps >= LIMIT.fps, `${frames.fps} fps`],
    ['no stalled frame', frames.worst <= LIMIT.worstFrameMs, `worst ${frames.worst} ms`],
    ['few long frames', frames.long.length <= LIMIT.longFrames, `${frames.long.length} over 30 ms in ${WATCH_MS / 1000} s${frames.long.length ? ' (' + frames.long.slice(0, 4).map((f) => f.ms + 'ms').join(', ') + ')' : ''}`],
    // If the page never went quiet, every count below was read while it was still building itself,
    // and calling that a leak is the single most repeated mistake this gate has made. Say so instead.
    ['no texture leak', !settled.settled || drift('texture') <= 0, `${settled.settled ? '' : 'INCONCLUSIVE, still loading: '}live textures ${first.live.texture} -> ${second.live.texture}`],
    ['no buffer leak', !settled.settled || drift('buffer') <= 2, `${settled.settled ? '' : 'INCONCLUSIVE, still loading: '}live buffers ${first.live.buffer} -> ${second.live.buffer}`],
    ['no program leak', !settled.settled || drift('program') <= 0, `${settled.settled ? '' : 'INCONCLUSIVE, still loading: '}live programs ${first.live.program} -> ${second.live.program}`],
    // ADVISORY, never a failure. The JS heap is the least trustworthy number here: it reports
    // allocated-not-yet-collected unless something forces a collection, it moves for the whole time
    // a page is streaming, and on /robot this harness reads ~1.2 GB against a true settled size of
    // ~190 MB measured directly. The GPU object counts above are the reliable signal and they are
    // what fails the gate. If this line looks wrong, measure the page on its own before believing it.
    ['heap (advisory)', true, `${first.heapMB} -> ${second.heapMB} MB${settled.settled ? '' : ' · page never settled, so this is loading, not drift'}`],
    // The exercise can stop early (its own ceiling, a page that navigates), so read the rounds that
    // actually ran rather than assuming three. With only one round there is nothing to compare and
    // the leak question is INCONCLUSIVE, which must not read as a pass.
    ...(exercised && exercised.length ? (() => {
      // Compare the LAST round to the FIRST, not to the one before it. A control that tears the
      // scene down and rebuilds it (Reset does) makes the counts dip and recover, so consecutive
      // rounds can differ by the rebuild alone: 12/8/12 is a probe landing mid-teardown, not a
      // leak. The question a leak check is actually asking is whether more is held after N cycles
      // than before them, and that is first against last.
      const last = exercised[exercised.length - 1], prev = exercised.length > 1 ? exercised[0] : null;
      const kinds = ['texture', 'buffer', 'program'];
      const trail = (k) => exercised.map((r) => r[k]).join('/');
      return [
        ['one context after use', last.contexts <= LIMIT.contexts, `${last.contexts} after pressing ${exercised[0].clicked} of ${exercised[0].offered} controls, ${exercised.length} round${exercised.length === 1 ? '' : 's'}`],
        ['use frees what it takes', !settled.settled ? true : (prev ? kinds.every((k) => last[k] - prev[k] <= 0) : false),
          `${settled.settled ? '' : 'INCONCLUSIVE, page never settled: '}${prev ? kinds.map((k) => `${k}s ${trail(k)}`).join(', ') + '  (first vs last)'
               : `only ${exercised.length} round finished, nothing to compare against`}`],
        ['heap under use (advisory)', true, `${exercised.map((r) => r.heapMB).join(' -> ')} MB${settled.settled ? '' : ' · still loading'}`],
      ];
    })() : []),
    ['caused no writes', blockedWrites.length === 0,
      blockedWrites.length ? `${blockedWrites.length} write(s) attempted and blocked: ${[...new Set(blockedWrites)].slice(0, 4).join(', ')}` : (EXERCISE ? 'no write request left the page while clicking' : 'not exercised')],
    ['clean console', logs.filter((l) => !l.startsWith('note:')).length === 0, logs.filter((l) => !l.startsWith('note:'))[0] || 'no errors or warnings'],
  ];
  const accepted = (name) => ACCEPT.some((a) => name.toLowerCase().includes(a.toLowerCase()));
  const newlyBroken = checks.filter(([n, ok]) => !ok && !accepted(n));
  const retirable = checks.filter(([n, ok]) => ok && accepted(n));
  results.push({ path, ok: newlyBroken.length === 0, checks, accepted, newlyBroken, retirable, gl, frames, live: second.live, settled, exercised, navigated, blockedWrites, logs });
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
