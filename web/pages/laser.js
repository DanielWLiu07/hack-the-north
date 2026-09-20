// laser.js — the shot that takes a FIXED Sentry issue off the board.
//
// Seer (pages/seer/seer.js) already aims a thin scanning ray at whatever it is looking at, drawn
// from the pupil's client position. This is that ray at full power, once, on an explicit press of
// [remove] — never on load, never on resolve. Same palette as the scan overlay and the intro
// effects (#ba7cf5 / #ef85d8 / #f5dcff over ink), so it reads as the same character, doing the
// same thing, in earnest.
//
// THE CURVE. What makes it land is the beat of nothing before the release, so the timing is fixed
// and deliberate rather than tuned by feel each frame:
//
//   0.00 → 0.72   BRACE    the PAGE GOES DOWN (one composited ramp to 50% black) while light falls
//                          inward to the eye (ease-in, accelerating), a ring collapses 300px → 20px,
//                          four brackets close on the doomed card, the core saturates to white, and
//                          the card trembles harder and harder. Seer is told 'thinking'.
//                          The dim is what makes the wind-up carry: tuned on a laptop the gather was
//                          legible, but on a projector from the back of a room a judge saw only the
//                          impact. Taking the room down reads at any distance; fatter strokes do not.
//   0.72 → 0.79   DARK     everything snaps to a point and goes out. 70 ms of nothing. This beat is
//                          the whole trick: the release has to be earned.
//   0.79          IMPACT   one clear frame. Flash, shake, shockwave, the card destroyed, the words.
//   0.79 → 0.90   ATTACK   the beam opens in 110 ms, overshooting to 1.12× before it settles.
//   0.90 → 1.12   HOLD     full power. The width breathes ±3%; the BRIGHTNESS never does.
//   1.12 → 1.67   DECAY    the width collapses fast (the words under it must become readable),
//                          the brightness follows more slowly, and the debris falls with gravity.
//   1.67 → 2.12   EMBERS   afterglow and ash. Then the canvas is blank and nothing is scheduled.
//
// BUDGET. This page's gate allows one WebGL context, 60 fps and a worst frame under 50 ms, so:
//   · ONE 2-D canvas, created on the first shot and reused. Never a second WebGL context.
//   · the two most fill-hungry parts are NOT drawn on it — the contact flash is a composited CSS
//     layer and the shake is a transform. Both are compositor work, not pixels we pay for.
//   · the bloom and the ember are pre-rendered ONCE into small offscreen canvases and stretched
//     with drawImage, instead of building a radial gradient per element per frame.
//   · no allocation in the loop: the debris is generated once and stepped in place, and every
//     "random" value comes from a deterministic hash of its index.
//
// REDUCED MOTION. No loop, no shake, no flash, no debris, no strobe, and NO DIM — that path never
// enters the wind-up at all: the beam is drawn once, held, and faded out once, and the words are
// identical. The only luminance ramps anywhere in this file are single and monotonic — the dim goes
// down once and is released once. Nothing here blinks, in either mode.

const HOT = '#fff6ff';           // the overexposed core
const BEAM = '#ef85d8';          // Seer's spark pink, the body of the shot
const HALO = '#ba7cf5';          // Seer's scan violet, the glow
const EMBER = '#f5dcff';         // packets, ash, debris

const BRACE = 0.72, DARK = 0.07, ATTACK = 0.11, HOLD = 0.22, DECAY = 0.55, EMBERS = 0.45;
const FIRE = BRACE + DARK;                      // 0.79 s — the impact frame
const GONE = FIRE + ATTACK + HOLD + DECAY;      // the beam is finished
const TOTAL = GONE + EMBERS;
const SHAKE_S = 0.5, PUSH_S = 0.7, FLASH_MS = 150;
const DIM_MAX = 0.5;            // how far the page goes down during the wind-up (0 = off)
const HOLD_CHARGE = 6;          // seconds the wind-up will wait for `ready` before giving up on it
const STILL_MS = 300, STILL_FADE_MS = 200;      // the reduced-motion version: hold, then one fade
const MIN_BEAM = 420;                           // obliterating: never narrower than this, whatever the card
// ...and never narrower than the SCREEN either. The shot that takes an issue off the board is meant to
// cover almost the whole viewport at the point it lands, so the width is driven by the viewport when the
// card is small. Anything past the edges is clipped by the canvas, so the extra costs no fill.

const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const ease = (t) => { const u = clamp01(t); return u * u * (3 - 2 * u); };
const out = (t) => 1 - (1 - clamp01(t)) ** 3;
const inCube = (t) => clamp01(t) ** 3;
// deterministic per-index noise: the same shot looks the same twice, and nothing allocates
const hash = (i, k) => { const x = Math.sin(i * 127.1 + k * 311.7) * 43758.5453; return x - Math.floor(x); };

let surface = null;

// ---- one-time sprites: a radial bloom and a soft ember, stretched with drawImage -------------
function sprite(size, stops) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const x = c.getContext('2d');
  const g = x.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  for (const [at, colour] of stops) g.addColorStop(at, colour);
  x.fillStyle = g;
  x.fillRect(0, 0, size, size);
  return c;
}

function stage() {
  if (surface) return surface;
  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true');
  canvas.dataset.seerLaser = '';
  canvas.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:12;'
    + 'display:none;opacity:1;transition:opacity 200ms linear';
  const flash = document.createElement('div');
  flash.setAttribute('aria-hidden', 'true');
  flash.dataset.seerFlash = '';
  // the contact flash: a composited layer, so the canvas never pays for a full-screen fill
  flash.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:11;opacity:0;'
    + 'background:radial-gradient(circle at var(--fx,50%) var(--fy,50%),#fff6ff 0%,#ef85d8 22%,#ba7cf5 46%,transparent 72%);'
    + `transition:opacity ${FLASH_MS}ms cubic-bezier(.2,0,.5,1);will-change:opacity`;
  // THE HOUSE LIGHTS. The wind-up used to be thin violet strokes around a small eye, which is
  // legible on the laptop it was tuned on and invisible on a projector from the back of a room.
  // Rather than make the strokes fatter — which only competes with the page — the page itself goes
  // down, once, across the whole brace: the gather then reads as the only light left in the room.
  // One composited opacity ramp, monotonic, under both the canvas and the flash. Nothing blinks.
  const dim = document.createElement('div');
  dim.setAttribute('aria-hidden', 'true');
  dim.dataset.seerDim = '';
  dim.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:10;opacity:0;'
    + 'background:#07030b;will-change:opacity';
  document.body.append(dim, flash, canvas);
  surface = {
    canvas, flash, dim, ctx: canvas.getContext('2d'), busy: false,
    bloom: sprite(128, [[0, HOT], [0.18, '#ffd9f6'], [0.42, BEAM], [0.7, 'rgba(186,124,245,.45)'], [1, 'rgba(186,124,245,0)']]),
    spark: sprite(64, [[0, HOT], [0.35, EMBER], [1, 'rgba(245,220,255,0)']]),
  };
  return surface;
}

// 1.5, not 2: every pixel here is glow, debris or an overexposed core, none of which shows the
// difference — and the backing store is the single biggest cost in the shot. At 1440x940 this is
// 12 MB instead of 21 MB, and the fill rate drops with it.
const DPR_CAP = 1.5;

function size(canvas) {
  const dpr = Math.min(devicePixelRatio || 1, DPR_CAP);
  const w = Math.round(innerWidth * dpr), h = Math.round(innerHeight * dpr);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  return dpr;
}

/**
 * Build everything the shot needs and get it onto the compositor, while nobody is waiting.
 *
 * Measured: doing this on the press cost a 450 ms first frame — allocating a full-viewport backing
 * store, rasterising two sprites and painting a viewport-sized radial gradient, all inside the
 * frame that was supposed to start the wind-up. None of it depends on WHAT is being shot, so none
 * of it belongs on the press. Idempotent, and safe to call as often as you like.
 */
export function warmLaser() {
  const sur = stage();
  // `busy` matters as much as `warm`: warming DURING a shot would blank the canvas the shot is
  // drawing on, and the first press of the session is exactly when both can happen at once.
  if (sur.warm || sur.busy) return;
  sur.warm = true;
  const dpr = size(sur.canvas);
  sur.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  sur.ctx.clearRect(0, 0, innerWidth, innerHeight);
  // one off-screen frame so the canvas layer and the flash gradient are rasterised now, not later
  sur.canvas.style.display = 'block';
  sur.canvas.style.opacity = '0';
  blob(sur.ctx, sur.bloom, -400, -400, 8, 1);
  sur.flash.style.opacity = '0.001';
  requestAnimationFrame(() => {
    // The press can land in the SAME frame as the hover that warmed this up: a tap fires
    // pointerenter and click in one task, and so does a fast mouse. By the time this runs the shot
    // may already own the canvas — and putting it back the way we found it would set display:none
    // over the whole beam, which then paints its entire curve into a surface nobody can see.
    if (sur.busy) return;
    sur.ctx.clearRect(0, 0, innerWidth, innerHeight);
    sur.canvas.style.display = 'none';
    sur.canvas.style.opacity = '1';
    sur.flash.style.opacity = '0';
  });
}

const blob = (ctx, img, x, y, r, alpha) => {
  if (alpha <= 0.004 || r <= 0) return;
  ctx.globalAlpha = alpha;
  ctx.drawImage(img, x - r, y - r, r * 2, r * 2);
};

// ---- the brace: light falling inward, and a ring closing on the eye --------------------------
/** Four corner brackets closing on the doomed card. The gather says something is COMING; this says
 *  what it is coming FOR, and it is the only part of the wind-up drawn at the card rather than at
 *  the eye — which is what makes the target obvious across a room. Eight short strokes, no fill. */
function lockOn(ctx, box, k) {
  const close = ease(k);
  const pad = 54 * (1 - close) + 7;
  const x0 = box.x - pad, y0 = box.y - pad, x1 = box.x + box.w + pad, y1 = box.y + box.h + pad;
  const arm = Math.min(72, box.w * 0.24), leg = Math.min(30, box.h * 0.34);
  ctx.globalCompositeOperation = 'lighter';
  ctx.lineCap = 'round';
  ctx.strokeStyle = BEAM;
  ctx.globalAlpha = 0.22 + 0.66 * close;
  ctx.lineWidth = 2.5 + 3 * close;
  for (const [cx, cy, sx, sy] of [[x0, y0, 1, 1], [x1, y0, -1, 1], [x0, y1, 1, -1], [x1, y1, -1, -1]]) {
    ctx.beginPath();
    ctx.moveTo(cx + sx * arm, cy);
    ctx.lineTo(cx, cy);
    ctx.lineTo(cx, cy + sy * leg);
    ctx.stroke();
  }
}

function brace(ctx, o, k, bloomImg, box) {
  const pull = inCube(k);                       // accelerating: slow, slow, then all at once
  const r = 300 * (1 - pull) + 20;
  ctx.globalCompositeOperation = 'lighter';
  ctx.lineCap = 'round';
  // 20 spokes reaching half again as far as they used to, and thicker: against the dimmed page
  // these are the light being pulled in, and they have to survive a projector.
  for (let i = 0; i < 20; i++) {
    const a = (i / 20) * Math.PI * 2 + hash(i, 1) * 0.5 + k * 0.6;
    const lead = r + 40 + hash(i, 2) * 210 * (1 - pull);
    ctx.strokeStyle = i % 3 ? HALO : BEAM;
    ctx.globalAlpha = (0.2 + 0.62 * ease(k)) * (0.5 + hash(i, 3) * 0.5);
    ctx.lineWidth = 2 + 3.5 * pull;
    ctx.beginPath();
    ctx.moveTo(o.x + Math.cos(a) * lead, o.y + Math.sin(a) * lead);
    ctx.lineTo(o.x + Math.cos(a) * (r + 6), o.y + Math.sin(a) * (r + 6));
    ctx.stroke();
  }
  for (const [spin, tint, width] of [[1, BEAM, 3.5], [-0.7, HALO, 2.5]]) {
    ctx.strokeStyle = tint; ctx.globalAlpha = 0.3 + 0.55 * ease(k); ctx.lineWidth = width;
    ctx.beginPath();
    ctx.arc(o.x, o.y, r * (spin > 0 ? 1 : 1.26), k * spin * 7, k * spin * 7 + Math.PI * 1.5);
    ctx.stroke();
  }
  if (box) lockOn(ctx, box, k);
  blob(ctx, bloomImg, o.x, o.y, 32 + 96 * pull, 0.4 + 0.6 * pull);
}

// ---- the beam: humongous, tapered, overexposed in the middle ---------------------------------
function beam(ctx, o, p, power, width) {
  const dx = p.x - o.x, dy = p.y - o.y, len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len, nx = -uy, ny = ux;
  const near = Math.max(8, width * 0.07), far = width * 0.5;
  const over = 1.35;                            // the beam does not stop at the card, it goes through it
  // The colour ramp still ends at the card, so nothing on screen changes hue; past it the gradient holds
  // its last stop and the beam stays hot all the way out.
  const gx = p.x + ux * len * (over - 1) * 0.08, gy = p.y + uy * len * (over - 1) * 0.08;
  const run = Math.hypot(innerWidth, innerHeight) * 1.4;
  const ex = p.x + ux * run, ey = p.y + uy * run;
  // EXPONENTIAL FLARE. A linear wedge is only a fraction of its final width by the time it reaches the
  // card -- most of the spread happens off screen where nobody sees it. This grows the half-width
  // geometrically instead, so it leaves the lens narrow and is already wider than the whole viewport by
  // the time it arrives. Growth is capped just past the card and then runs parallel off screen: left
  // uncapped, e^(g*u) at u=4.4 is millions of pixels and the rasteriser falls over.
  const coverHalf = Math.hypot(innerWidth, innerHeight) * 0.85;   // past the card it must exceed the screen
  const g = Math.log(Math.max(1.2, coverHalf / near));
  const halfAt = (u) => near * Math.exp(g * Math.min(u, 1.15));
  const uMax = 1 + run / len;
  const STEPS = 10;
  const wedge = (scale) => {
    ctx.beginPath();
    for (let i = 0; i <= STEPS; i++) {
      const u = (i / STEPS) * uMax, d = u * len, hw = halfAt(u) * scale;
      const cx = o.x + ux * d, cy = o.y + uy * d;
      if (i === 0) ctx.moveTo(cx + nx * hw, cy + ny * hw); else ctx.lineTo(cx + nx * hw, cy + ny * hw);
    }
    for (let i = STEPS; i >= 0; i--) {
      const u = (i / STEPS) * uMax, d = u * len, hw = halfAt(u) * scale;
      const cx = o.x + ux * d, cy = o.y + uy * d;
      ctx.lineTo(cx - nx * hw, cy - ny * hw);
    }
    // Round the emitter end rather than closing straight across it: a flat edge that wide, standing in
    // mid-air beside the lens, reads as a cut slab instead of light leaving an eye. Shallow on purpose.
    const hw0 = halfAt(0) * scale, CAP = 18;
    for (let k = 1; k < CAP; k++) {
      const a = Math.PI * (k / CAP), back = Math.sin(a) * hw0 * 0.38, side = -Math.cos(a) * hw0;
      ctx.lineTo(o.x + nx * side - ux * back, o.y + ny * side - uy * back);
    }
    ctx.closePath();
    ctx.fill();
  };
  const ramp = (colour, endColour) => {
    const g = ctx.createLinearGradient(o.x, o.y, gx, gy);
    g.addColorStop(0, colour); g.addColorStop(0.55, colour); g.addColorStop(1, endColour);
    return g;
  };
  for (const [scale, colour, alpha] of [[1.25, HALO, 0.28], [0.98, BEAM, 0.55]]) {
    ctx.globalAlpha = alpha * power;
    ctx.fillStyle = ramp(colour, HOT);
    wedge(scale);
  }
  ctx.globalCompositeOperation = 'source-over';
  for (const [scale, colour] of [[0.72, HOT], [0.46, '#ffffff']]) {
    ctx.globalAlpha = Math.min(1, power * 1.3);
    ctx.fillStyle = ramp(colour, colour);
    wedge(scale);
  }
  ctx.globalCompositeOperation = 'lighter';
}

function packets(ctx, o, p, t, power, img) {
  for (let i = 0; i < 7; i++) {
    const u = (t * 1.9 + hash(i, 7)) % 1;
    const w = 1 + hash(i, 8) * 2.2;
    blob(ctx, img, o.x + (p.x - o.x) * u, o.y + (p.y - o.y) * u, 6 * w, Math.sin(u * Math.PI) * 0.7 * power);
  }
}

// ---- the wreckage ----------------------------------------------------------------------------
function makeDebris(rect, n) {
  // spawned across the card's own footprint, so it reads as THAT card coming apart
  return Array.from({ length: n }, (_, i) => {
    const a = -Math.PI / 2 + (hash(i, 11) - 0.5) * 2.6;
    const speed = 150 + hash(i, 12) * 520;
    return {
      x: rect.x + hash(i, 13) * rect.w, y: rect.y + hash(i, 14) * rect.h,
      vx: Math.cos(a) * speed + (hash(i, 15) - 0.5) * 340, vy: Math.sin(a) * speed,
      spin: (hash(i, 16) - 0.5) * 16, turn: hash(i, 17) * 6.28,
      w: 2 + hash(i, 18) * 16, h: 1 + hash(i, 19) * 5,
      tint: [BEAM, HALO, EMBER, '#e8e2f2'][i % 4], hot: i % 6 === 0, drag: 0.86 + hash(i, 20) * 0.1,
    };
  });
}

function debrisAt(ctx, bits, age, img, dpr) {
  // closed form, not integrated: every frame is exact, and a dropped frame never changes the path
  const fade = 1 - ease(clamp01((age - 0.45) / 0.85));
  if (fade <= 0.004) return;
  ctx.globalCompositeOperation = 'lighter';
  for (const b of bits) {
    const k = (1 - Math.pow(b.drag, age * 60)) / (1 - b.drag) / 60;     // velocity with drag, integrated
    const x = b.x + b.vx * k, y = b.y + b.vy * k + 900 * age * age * 0.5;
    if (y > innerHeight + 40 || x < -40 || x > innerWidth + 40) continue;
    if (b.hot) blob(ctx, img, x, y, 7, fade * 0.8);
    ctx.globalAlpha = fade * (b.hot ? 1 : 0.8);
    ctx.fillStyle = b.hot ? HOT : b.tint;
    // setTransform, not save/rotate/restore: 26 of these a frame, and the stack is the expensive part
    const a = b.turn + b.spin * age, cos = Math.cos(a) * dpr, sin = Math.sin(a) * dpr;
    ctx.setTransform(cos, sin, -sin, cos, x * dpr, y * dpr);
    ctx.fillRect(-b.w / 2, -b.h / 2, b.w, b.h);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function ash(ctx, bits, age, img) {
  const fade = clamp01(age / 0.3) * (1 - ease(clamp01((age - 0.6) / 0.9)));
  if (fade <= 0.004) return;
  for (let i = 0; i < 18; i++) {
    const b = bits[i % bits.length];
    const rise = -34 * age + 120 * age * age;                       // buoyant for a moment, then it falls
    blob(ctx, img, b.x + b.vx * age * 0.12 + Math.sin(age * 2 + i) * 9, b.y + rise,
      2 + hash(i, 21) * 3.5, fade * (0.2 + hash(i, 22) * 0.35));
  }
}

// ---- the page's own reaction: shake and shockwave, on the compositor -------------------------
function reactor(target) {
  const main = document.querySelector('main.tboard') || document.getElementById('main');
  const was = main ? main.style.transform : '';
  const host = target && target.parentElement;
  const kin = host ? [...host.children].filter((n) => n !== target).slice(0, 24) : [];
  const mid = target ? target.getBoundingClientRect().top : 0;
  const pushed = kin.map((n) => {
    const r = n.getBoundingClientRect();
    const away = Math.sign(r.top - mid) || 1;
    const near = Math.max(0, 1 - Math.abs(r.top - mid) / 900);
    return { n, was: n.style.transform, dy: away * 26 * near * near };
  }).filter((p) => Math.abs(p.dy) > 0.6).slice(0, 6);
  return {
    step(age) {
      if (main) {
        const k = 1 - ease(clamp01(age / SHAKE_S));                // ease-out, and deterministic
        const amp = 14 * k * k;
        main.style.transform = amp < 0.15 ? was
          : `translate3d(${(Math.sin(age * 176) * amp).toFixed(2)}px,${(Math.cos(age * 149) * amp * 0.7).toFixed(2)}px,0)`;
      }
      const s = 1 - out(clamp01(age / PUSH_S));
      for (const p of pushed) p.n.style.transform = s < 0.01 ? p.was : `translate3d(0,${(p.dy * s).toFixed(2)}px,0)`;
    },
    clear() {
      if (main) main.style.transform = was;
      for (const p of pushed) p.n.style.transform = p.was;
    },
  };
}

// ---- the shot ---------------------------------------------------------------------------------

/**
 * Fire once, from `origin()` at the middle of `target`.
 *   origin   () => {x, y} in CLIENT pixels (Seer's pupil), re-read every frame so a scroll follows
 *   target   the element being destroyed; its rect is re-read every frame too
 *   reduced  true for prefers-reduced-motion: one still frame, no loop, no shake, no flash
 *   onBrace  called once as the wind-up starts (the card begins to tremble)
 *   onImpact called once on the impact frame — the card is destroyed and wears its words from here
 * Resolves true when the canvas is blank again, false if there was nothing to draw (and in that
 * case onBrace/onImpact still run, so the caller always finishes what it started).
 */
export function fireLaser({ origin, target, reduced = false, ready = null, onBrace = () => {}, onImpact = () => {} } = {}) {
  const sur = stage();
  const { canvas, ctx, flash, dim } = sur;
  const aim = () => {
    if (!target || !target.isConnected) return null;
    const r = target.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return null;
    // the upper third, not the middle: the beam lands ON the card but above where its words go
    return { x: r.left + r.width * 0.5, y: r.top + Math.min(r.height * 0.34, 46), w: r.width };
  };
  const first = aim(), from = origin && origin();
  // `ready` gates DESTRUCTION, not just the beam: every path below waits for it, so a card is never
  // destroyed on a press that Sentry went on to refuse — reduced motion and the can't-draw path
  // included. Both of those used to fire onImpact straight away, which destroyed the card before
  // the answer came back and printed the raw issue id because the verdict had not arrived yet.
  const confirmed = () => Promise.resolve(ready === null ? true : ready).then((v) => !!v, () => false);

  if (!first || !from || sur.busy) {
    try { onBrace(); } catch { /* nothing drawn; the caller still finishes */ }
    return confirmed().then((ok) => { if (ok) { try { onImpact(); } catch { /* ditto */ } } return false; });
  }
  const width = Math.max(MIN_BEAM, first.w * 2.6, (typeof innerWidth === 'number' ? innerWidth : 1440) * 1.15);

  if (reduced) {
    // One frame, held, faded once. No loop, no shake, no flash, no debris, no second ramp.
    try { onBrace(); } catch { /* the shot goes on */ }
    return confirmed().then((ok) => {
      if (!ok) return false;
      sur.busy = true;
      canvas.style.display = 'block';
      canvas.style.opacity = '1';
      const dpr = size(canvas);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      const p = aim() || first;
      beam(ctx, origin() || from, p, 0.85, width * 0.8);
      blob(ctx, sur.bloom, p.x, p.y, 130, 0.75);
      ctx.globalCompositeOperation = 'source-over';
      ctx.globalAlpha = 1;
      try { onImpact(); } catch { /* the card still goes */ }
      return new Promise((done) => setTimeout(() => {
        canvas.style.opacity = '0';
        setTimeout(() => {
          ctx.clearRect(0, 0, innerWidth, innerHeight);
          canvas.style.display = 'none'; canvas.style.opacity = '1'; sur.busy = false; done(true);
        }, STILL_FADE_MS);
      }, STILL_MS));
    });
  }
  sur.busy = true;
  canvas.style.display = 'block';
  canvas.style.opacity = '1';
  dim.style.transition = `opacity ${Math.round(BRACE * 1000)}ms cubic-bezier(.4,0,.8,1)`;
  dim.style.opacity = String(DIM_MAX);

  // Everything that reads layout is done HERE, on the press, while the wind-up covers it — never
  // on the impact frame, which already pays for replacing the card's contents.
  const shook = reactor(target);
  const box = target.getBoundingClientRect();
  const bits = makeDebris({ x: box.left, y: box.top, w: box.width, h: box.height }, 26);
  let landed = false, raf = 0;
  // `ready` is how the wind-up pays for the network. The press starts the charge IMMEDIATELY and
  // the caller's confirmation (Sentry really does say resolved) lands during it: with a ~400 ms
  // round trip the charge simply holds a beat longer, and nobody sees a dead half-second between
  // the press and anything happening. Resolving false fizzles the charge and fires nothing.
  let go = ready ? null : true, held = 0, fizzled = 0;
  if (ready) Promise.resolve(ready).then((ok) => { go = !!ok; }, () => { go = false; });
  let t0 = performance.now();
  try { onBrace(); } catch { /* the shot goes on */ }

  return new Promise((done) => {
    const finish = (fired) => {
      cancelAnimationFrame(raf);
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      canvas.style.display = 'none';
      flash.style.opacity = '0';
      dim.style.transition = 'none';
      dim.style.opacity = '0';
      shook.clear();
      sur.busy = false;
      done(fired);
    };
    const frame = (now) => {
      // The charge waits at full gather for `ready`, then the curve resumes where it paused. t0 is
      // pushed forward by exactly the time spent waiting, so everything after the wind-up keeps its
      // own timing no matter how long Sentry took.
      if (go === null && (now - t0) / 1000 >= BRACE) { held = now; }
      else if (go !== null && held) { t0 += now - held; held = 0; }
      if (held && (now - held) / 1000 > HOLD_CHARGE) go = false;
      const t = held ? BRACE - 0.0001 : (now - t0) / 1000;

      const dpr = size(canvas);
      if (go === false && !landed) {
        // Sentry did not confirm: the charge dies where it stands. No beam, nothing destroyed, and
        // the caller is told false so it can put the reason on the card instead.
        if (!fizzled) { fizzled = now; dim.style.transition = 'opacity 260ms linear'; dim.style.opacity = '0'; }
        const u = clamp01((now - fizzled) / 260);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, innerWidth, innerHeight);
        const oF = origin() || from;
        blob(ctx, sur.bloom, oF.x, oF.y, 64 * (1 - u), 0.55 * (1 - u));
        ctx.globalCompositeOperation = 'source-over';
        ctx.globalAlpha = 1;
        if (u >= 1) return finish(false);
        raf = requestAnimationFrame(frame);
        return;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      const o = origin() || from;

      if (t < BRACE) {
        // the rect is re-read each frame: frame() is still smooth-scrolling the card into place
        const b = target.getBoundingClientRect();
        brace(ctx, o, t / BRACE, sur.bloom, b.width > 1 ? { x: b.left, y: b.top, w: b.width, h: b.height } : null);
      } else if (t < FIRE) {
        // the beat of nothing. One dim point, nothing else: the page holds its breath.
        blob(ctx, sur.bloom, o.x, o.y, 22, 0.3 * (1 - (t - BRACE) / DARK));
      } else {
        const age = t - FIRE;
        if (!landed) {
          landed = true;
          const p0 = aim() || first;
          try { onImpact(); } catch { /* the card still goes */ }
          flash.style.setProperty('--fx', `${((p0.x / innerWidth) * 100).toFixed(1)}%`);
          flash.style.setProperty('--fy', `${((p0.y / innerHeight) * 100).toFixed(1)}%`);
          dim.style.transition = 'none';
          dim.style.opacity = '0';
          flash.style.transition = 'none';
          flash.style.opacity = '0.42';
          requestAnimationFrame(() => {                 // one monotonic decay, on the compositor
            flash.style.transition = `opacity ${FLASH_MS}ms cubic-bezier(.2,0,.5,1)`;
            flash.style.opacity = '0';
          });
        }
        const p = aim() || first;
        shook.step(age);
        // attack overshoots, hold breathes in WIDTH only, decay collapses inward with the core last
        let power, w;
        // The WIDTH snaps open in a third of the attack; the brightness still takes the full 110ms.
        // Decoupling them is what makes it read as a beam flung open rather than one fading up.
        if (age < ATTACK) { const u = out(age / ATTACK); power = u; w = width * 1.12 * out(clamp01(age / (ATTACK * 0.34))); }
        else if (age < ATTACK + HOLD) { power = 1; w = width * (1.12 - 0.12 * ease((age - ATTACK) / 0.08)) * (1 + Math.sin(age * 31) * 0.03); }
        else {
          // The WIDTH collapses fast and the brightness follows more slowly: the words underneath
          // have to be readable well before the glow is finished, and a beam that stays fat over
          // its own message is just a beam covering a message.
          const u = clamp01((age - ATTACK - HOLD) / DECAY);
          power = 1 - ease(u);
          w = width * (1 - out(u) * 0.97);
        }
        if (power > 0.008) {
          beam(ctx, o, p, power, w);
          packets(ctx, o, p, age, power, sur.spark);
          // the muzzle, as a stretched sprite — the brace's 16 strokes are wind-up, not firing
          blob(ctx, sur.bloom, o.x, o.y, 60 + 40 * power, 0.75 * power);
        }
        // impact bloom, then the afterglow that outlives the beam
        blob(ctx, sur.bloom, p.x, p.y, 70 + 230 * out(clamp01(age / 0.24)), (1 - ease(clamp01(age / 0.9))) * 0.95);
        blob(ctx, sur.bloom, p.x, p.y, 40 + 90 * out(clamp01(age / 1.2)), (1 - ease(clamp01(age / 1.5))) * 0.3);
        for (const [delay, tint] of [[0, HOT], [0.14, BEAM]]) {
          const u = clamp01((age - delay) / 0.6);
          if (u <= 0 || u >= 1) continue;
          ctx.globalCompositeOperation = 'lighter';
          ctx.globalAlpha = (1 - u) * 0.7; ctx.strokeStyle = tint; ctx.lineWidth = 4 * (1 - u) + 0.6;
          ctx.beginPath(); ctx.arc(p.x, p.y, 18 + 420 * out(u), 0, Math.PI * 2); ctx.stroke();
        }
        debrisAt(ctx, bits, age, sur.spark, dpr);
        ash(ctx, bits, age, sur.spark);
      }

      ctx.globalCompositeOperation = 'source-over';
      ctx.globalAlpha = 1;
      if (t < TOTAL) { raf = requestAnimationFrame(frame); return; }
      finish(true);
    };
    raf = requestAnimationFrame(frame);
  });
}

/** True while a shot is on screen — the board refuses to start a second one over the first. */
export const firing = () => !!(surface && surface.busy);

/** When the card should be destroyed, relative to the press: the caller needs it for its wording. */
export const IMPACT_AT_MS = Math.round(FIRE * 1000);
