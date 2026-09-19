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
//   0.00 → 0.72   BRACE    light falls inward to the eye (ease-in, accelerating), a ring collapses
//                          220px → 18px, the core saturates to white, and the doomed card trembles
//                          harder and harder. Seer is told 'thinking'.
//   0.72 → 0.79   DARK     everything snaps to a point and goes out. 70 ms of nothing. This beat is
//                          the whole trick: the release has to be earned.
//   0.79          IMPACT   one clear frame. Flash, shake, shockwave, the card destroyed, the words.
//   0.79 → 0.90   ATTACK   the beam opens in 110 ms, overshooting to 1.12× before it settles.
//   0.90 → 1.20   HOLD     full power. The width breathes ±3%; the BRIGHTNESS never does.
//   1.20 → 1.95   DECAY    the beam collapses inward, core last, while debris falls with gravity.
//   1.95 → 2.30   EMBERS   afterglow and ash. Then the canvas is blank and nothing is scheduled.
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
// REDUCED MOTION. No loop, no shake, no flash, no debris, no strobe: the beam is drawn once, held,
// and faded out once, and the words are identical. The only luminance ramps anywhere in this file
// are single and monotonic — nothing here blinks, in either mode.

const HOT = '#fff6ff';           // the overexposed core
const BEAM = '#ef85d8';          // Seer's spark pink, the body of the shot
const HALO = '#ba7cf5';          // Seer's scan violet, the glow
const EMBER = '#f5dcff';         // packets, ash, debris

const BRACE = 0.72, DARK = 0.07, ATTACK = 0.11, HOLD = 0.30, DECAY = 0.75, EMBERS = 0.35;
const FIRE = BRACE + DARK;                      // 0.79 s — the impact frame
const GONE = FIRE + ATTACK + HOLD + DECAY;      // the beam is finished
const TOTAL = GONE + EMBERS;
const SHAKE_S = 0.5, PUSH_S = 0.7, FLASH_MS = 150;
const STILL_MS = 300, STILL_FADE_MS = 200;      // the reduced-motion version: hold, then one fade
const MIN_BEAM = 220;                           // humongous: never narrower than this, whatever the card

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
  document.body.append(flash, canvas);
  surface = {
    canvas, flash, ctx: canvas.getContext('2d'), busy: false,
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
  if (sur.warm) return;
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
function brace(ctx, o, k, bloomImg) {
  const pull = inCube(k);                       // accelerating: slow, slow, then all at once
  const r = 220 * (1 - pull) + 18;
  ctx.globalCompositeOperation = 'lighter';
  ctx.lineCap = 'round';
  for (let i = 0; i < 14; i++) {
    const a = (i / 14) * Math.PI * 2 + hash(i, 1) * 0.5 + k * 0.6;
    const lead = r + 30 + hash(i, 2) * 120 * (1 - pull);
    ctx.strokeStyle = i % 3 ? HALO : BEAM;
    ctx.globalAlpha = (0.15 + 0.55 * ease(k)) * (0.5 + hash(i, 3) * 0.5);
    ctx.lineWidth = 1.5 + 2.5 * pull;
    ctx.beginPath();
    ctx.moveTo(o.x + Math.cos(a) * lead, o.y + Math.sin(a) * lead);
    ctx.lineTo(o.x + Math.cos(a) * (r + 6), o.y + Math.sin(a) * (r + 6));
    ctx.stroke();
  }
  for (const [spin, tint, width] of [[1, BEAM, 2.5], [-0.7, HALO, 1.5]]) {
    ctx.strokeStyle = tint; ctx.globalAlpha = 0.25 + 0.5 * ease(k); ctx.lineWidth = width;
    ctx.beginPath();
    ctx.arc(o.x, o.y, r * (spin > 0 ? 1 : 1.22), k * spin * 7, k * spin * 7 + Math.PI * 1.5);
    ctx.stroke();
  }
  blob(ctx, bloomImg, o.x, o.y, 26 + 74 * pull, 0.35 + 0.6 * pull);
}

// ---- the beam: humongous, tapered, overexposed in the middle ---------------------------------
function beam(ctx, o, p, power, width) {
  const dx = p.x - o.x, dy = p.y - o.y, len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len, nx = -uy, ny = ux;
  const near = Math.max(4, width * 0.06), far = width * 0.5;
  const over = 1.35;                            // the beam does not stop at the card, it goes through it
  const ex = p.x + ux * len * (over - 1) * 0.08, ey = p.y + uy * len * (over - 1) * 0.08;
  ctx.globalCompositeOperation = 'lighter';
  // FOUR layers, widest and dimmest first: glow, saturated body, overexposed core, white centre.
  // It was five, and the extra 2.5x glow layer alone cost more fill than the other four together —
  // at 1440x940 that run of frames dropped to 30 fps. The glow reads the same at 1.9x.
  const layers = [[1.6, HALO, 0.20], [1.0, BEAM, 0.55], [0.42, HOT, 0.95], [0.16, '#ffffff', 1]];
  for (const [scale, colour, alpha] of layers) {
    const wN = near * scale, wF = far * scale;
    ctx.globalAlpha = alpha * power;
    const grad = ctx.createLinearGradient(o.x, o.y, ex, ey);
    grad.addColorStop(0, colour);
    grad.addColorStop(0.55, colour);
    grad.addColorStop(1, HOT);
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.moveTo(o.x + nx * wN, o.y + ny * wN);
    ctx.lineTo(ex + nx * wF, ey + ny * wF);
    ctx.lineTo(ex - nx * wF, ey - ny * wF);
    ctx.lineTo(o.x - nx * wN, o.y - ny * wN);
    ctx.closePath();
    ctx.fill();
  }
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
export function fireLaser({ origin, target, reduced = false, onBrace = () => {}, onImpact = () => {} } = {}) {
  const sur = stage();
  const { canvas, ctx, flash } = sur;
  const aim = () => {
    if (!target || !target.isConnected) return null;
    const r = target.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return null;
    return { x: r.left + r.width * 0.5, y: r.top + Math.min(r.height * 0.5, 70), w: r.width };
  };
  const first = aim(), from = origin && origin();
  const bothWays = () => { try { onBrace(); } catch { /* the card still goes */ } try { onImpact(); } catch { /* ditto */ } };
  if (!first || !from || sur.busy) { bothWays(); return Promise.resolve(false); }
  sur.busy = true;
  canvas.style.display = 'block';
  canvas.style.opacity = '1';
  const width = Math.max(MIN_BEAM, first.w * 1.15);       // humongous: wider than the card it is aimed at

  if (reduced) {
    // One frame, held, faded once. No loop, no shake, no flash, no debris, no second ramp.
    const dpr = size(canvas);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    beam(ctx, from, first, 0.8, width * 0.8);
    blob(ctx, sur.bloom, first.x, first.y, 120, 0.7);
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    bothWays();
    return new Promise((done) => setTimeout(() => {
      canvas.style.opacity = '0';
      setTimeout(() => {
        ctx.clearRect(0, 0, innerWidth, innerHeight);
        canvas.style.display = 'none'; canvas.style.opacity = '1'; sur.busy = false; done(true);
      }, STILL_FADE_MS);
    }, STILL_MS));
  }

  // Everything that reads layout is done HERE, on the press, while the wind-up covers it — never
  // on the impact frame, which already pays for replacing the card's contents.
  const shook = reactor(target);
  const box = target.getBoundingClientRect();
  const bits = makeDebris({ x: box.left, y: box.top, w: box.width, h: box.height }, 26);
  let braced = false, landed = false, raf = 0;
  const t0 = performance.now();
  try { onBrace(); braced = true; } catch { braced = true; }

  return new Promise((done) => {
    const frame = (now) => {
      const t = (now - t0) / 1000;
      const dpr = size(canvas);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      const o = origin() || from;

      if (t < BRACE) {
        brace(ctx, o, t / BRACE, sur.bloom);
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
        if (age < ATTACK) { const u = out(age / ATTACK); power = u; w = width * (u * 1.12); }
        else if (age < ATTACK + HOLD) { power = 1; w = width * (1.12 - 0.12 * ease((age - ATTACK) / 0.08)) * (1 + Math.sin(age * 31) * 0.03); }
        else { const u = clamp01((age - ATTACK - HOLD) / DECAY); power = 1 - inCube(u); w = width * (1 - ease(u) * 0.94); }
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
      cancelAnimationFrame(raf);
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      canvas.style.display = 'none';
      flash.style.opacity = '0';
      shook.clear();
      sur.busy = false;
      done(true);
    };
    raf = requestAnimationFrame(frame);
    void braced;
  });
}

/** True while a shot is on screen — the board refuses to start a second one over the first. */
export const firing = () => !!(surface && surface.busy);

/** When the card should be destroyed, relative to the press: the caller needs it for its wording. */
export const IMPACT_AT_MS = Math.round(FIRE * 1000);
