// sentry.js — browser Sentry for the page: errors, tracing into /api, and SESSION REPLAY.
//
// Nothing here may ever break the page: no DSN, no network, a blocked script — every
// path ends in a silent return. The SDK is vendored (vendor/sentry/, see VERSION) so
// the page still loads with no internet; only the events themselves need the network.
//
// THE CATCH: this page is one WebGL canvas. Replay records the DOM (rrweb) and a canvas
// is opaque to it, so a plain replay of this page is a black rectangle. WebGL without
// preserveDrawingBuffer cannot be read back after the frame is presented, so the canvas
// integration runs in MANUAL mode and we hand it the canvas from scene.js's afterRender
// hook, right after the draw, once a second.
//
// Privacy: replay masks all text and blocks media by default. This dashboard stores no
// people (obs.py: send_default_pii=False) and the point of replay here is watching how
// a judge uses the search panel (docs/18-sentry.md §7), so text and media are recorded.
// INPUTS STAY MASKED unless an element opts in with `data-sentry-unmask`.

const SNAPSHOT_EVERY_MS = 1000;         // the 'low' quality tier records at most 1 frame/s anyway
const VENDOR = new URL('./vendor/sentry/', import.meta.url);

const stats = { snapshots: 0 };           // canvas frames handed to the replay (gitrlSentry.stats)

const loadScript = (name) => new Promise((resolve, reject) => {
  const s = document.createElement('script');
  s.src = new URL(name, VENDOR).href;
  s.onload = resolve;
  s.onerror = () => reject(new Error(`could not load ${name}`));
  document.head.appendChild(s);
});

async function start() {
  const res = await fetch('/api/config', { cache: 'no-store' });
  if (!res.ok) return;
  const cfg = (await res.json()).sentry;
  if (!cfg || !cfg.dsn) return;                         // Sentry is simply off (or parked: cfg.paused)
  // never record our own test runs: every headless capture of this page was a Session Replay
  // billed against the 500/month quota. navigator.webdriver is true under puppeteer / automation.
  if (navigator.webdriver) return;

  await loadScript('bundle.tracing.replay.min.js');     // defines window.Sentry
  await loadScript('replay-canvas.min.js');             // adds Sentry.replayCanvasIntegration
  const Sentry = window.Sentry;
  if (!Sentry || !Sentry.init) return;

  const canvasReplay = Sentry.replayCanvasIntegration
    // 'low' = at most 1 frame/s as WebP q0.25; maxCanvasSize keeps a 2560x1440 canvas
    // from costing ~250 KB per frame (measured: ~90 KB/frame at 1440x810 uncapped)
    ? Sentry.replayCanvasIntegration({ enableManualSnapshot: true, quality: 'low', maxCanvasSize: [1280, 1280] }) : null;
  Sentry.init({
    dsn: cfg.dsn,
    environment: cfg.environment,
    release: cfg.release,
    integrations: [
      Sentry.browserTracingIntegration(),
      Sentry.replayIntegration({
        maskAllText: false, blockAllMedia: false, maskAllInputs: true,
        unmask: ['[data-sentry-unmask]'],
      }),
      ...(canvasReplay ? [canvasReplay] : []),
    ],
    replaysSessionSampleRate: cfg.replaysSessionSampleRate,
    replaysOnErrorSampleRate: 1.0,
    tracesSampleRate: cfg.tracesSampleRate,
    // continue the trace into our own API only: browser click -> /api/* -> Elasticsearch
    tracePropagationTargets: [/^\/api\//, `${location.origin}/api/`],
  });
  Sentry.setTag('role', 'browser');

  if (canvasReplay) hookCanvas(canvasReplay);
  window.gitrlSentry = { Sentry, canvasReplay, stats };   // console handle, and for verification
}

// scene.js calls every fn in world.afterRender with the canvas right after drawing.
// The scene may not exist yet (modules load in any order), so wait for it.
function hookCanvas(canvasReplay) {
  let last = 0, busy = false, tries = 0;
  const snap = (canvas) => {
    const now = performance.now();
    if (busy || now - last < SNAPSHOT_EVERY_MS) return;
    last = now; busy = true; stats.snapshots++;
    // skipRequestAnimationFrame: capture NOW. The default defers to the next animation
    // frame, by which time WebGL has cleared the drawing buffer — the integration then
    // sees a blank frame and silently records nothing.
    Promise.resolve(canvasReplay.snapshot(canvas, { skipRequestAnimationFrame: true }))
      .catch(() => {}).finally(() => { busy = false; });
  };
  const attach = () => {
    const world = window.gitrl && window.gitrl.world;
    if (world && Array.isArray(world.afterRender)) { world.afterRender.push(snap); return; }
    if (++tries < 100) setTimeout(attach, 200);
  };
  attach();
}

start().catch(() => {});
