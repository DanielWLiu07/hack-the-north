// Fictional coloured entrance bugs; never attached to real issue rows.
import { createImpactPunch } from './panel-effects.js';

// Entrance-clock seconds at which each shot LANDS on its bug. The windup that leads into a shot and the
// impact that follows one are both keyed off these instants, so they are exported rather than inlined.
export const SHOTS = [1.75, 2.35, 2.95];
const starts = [.15, .35, .55];
const accents = ['#f757b6', '#ffb287', '#a596ed'];
const glints = ['#ffd6ee', '#ffe3c6', '#e0d8ff'];
const TAU = Math.PI * 2;
// How long after landing each part of an impact is still painting. The hit is heavy but brief: the bloom
// is gone in four frames, and only the scorch is still there half a second later.
// BOLT is how long the beam HOLDS on its target. It is not a flash: it blasts, and for as long as it
// blasts the contact point keeps burning -- sparks recycle, rings pulse, the core flickers. RING_EVERY is
// how often the burn throws a fresh shockwave while it holds.
const BOLT = .72, FALL = .18, RING_EVERY = .3;
const BLOOM = .12, RING = .34, SPARK = .66, SHARD = .8, EMBER = .92, SCORCH = .98;
const LIFE = BOLT + .6;
// The hunt ENDS, and it ends on purpose rather than petering out. All three beams blast, then one
// screen-wide whiteout at WIPE, and the canvas is taken away UNDERNEATH it -- so the embers, the last
// beam and the debris are simply gone when the light lifts, instead of trailing through the dock.
// HIDE sits while the cover is still near full, which is what makes the removal invisible.
const WIPE = SHOTS[SHOTS.length - 1] + BOLT, HIDE = WIPE + .13;
const frac = x => x - Math.floor(x);

/** The shot nearest t: which bug, when it lands, how long ago that was. Null once they are all spent. */
export function shotAt(t) {
  const i = SHOTS.findIndex(s => t < s + LIFE);
  return i < 0 ? null : { i, at: SHOTS[i], age: t - SHOTS[i] };
}

export function bugPosition(i, t, width, height) {
  const mobile = width < 760;
  const end = { x: width * (mobile ? [.14,.86,.70] : [.22,.79,.70])[i],
    y: height * (mobile ? [.20,.30,.46] : [.24,.39,.68])[i] };
  const start = [{ x:-48,y:height*.16 },{ x:width+48,y:height*.46 },{ x:width*.7,y:height+48 }][i];
  const u = Math.max(0, Math.min(1, (t - starts[i]) / (SHOTS[i] - starts[i])));
  return { x:start.x+(end.x-start.x)*u + Math.sin(u*Math.PI)*Math.sin(u*8+i)*18,
    y:start.y+(end.y-start.y)*u + Math.sin(u*Math.PI)*Math.cos(u*9+i)*14 };
}

// Every particle is a pure function of (shot, index, age) off a fixed table, so an impact allocates
// nothing per frame and the same frame of the same shot is always the same picture.
const hash = (a, b) => { const s = Math.sin(a * 127.1 + b * 311.7) * 43758.545; return s - Math.floor(s); };
const table = (count, make) => SHOTS.map((_, i) => Array.from({ length: count }, (_, k) => make(n => hash(i * 31 + k, n), k, count)));
// Two thirds splash back down the beam; the rest graze away along the surface it struck. A few
// long runners carry the sense of scale.
const SPARKS = table(34, (r, k) => ({
  a: k % 3 === 1 ? (r(19) > .5 ? 1 : -1) * (1.15 + r(20) * .55) : (r(1) - .5) * 2.4,
  v: 480 + r(2) * 900 + (k % 7 ? 0 : 700),
  life: .22 + r(3) * .3, w: 1 + r(4) * 1.7, hot: r(5) > .4 }));
const EMBERS = table(14, r => ({ a: (r(6) - .5) * 3.1, v: 90 + r(7) * 230, life: .5 + r(8) * .4,
  rise: 34 + r(9) * 62, size: 1.3 + r(10) * 2.1, blink: 12 + r(11) * 16 }));
const SHARDS = table(12, r => ({ a: (r(12) - .5) * 3, v: 230 + r(13) * 430, life: .4 + r(14) * .36,
  size: 4 + r(15) * 7, spin: (r(16) - .5) * 26, leg: r(17) > .55, dark: r(18) > .45 }));
// Air drag, integrated so position and speed are closed-form rather than stepped.
const DRAG = 5.2, glide = (v, t) => v * (1 - Math.exp(-DRAG * t)) / DRAG, speed = (v, t) => v * Math.exp(-DRAG * t);
const rgbOf = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
const ACCENT = accents.map(rgbOf), GLINT = glints.map(rgbOf);
const tint = ([r, g, b], a) => `rgba(${r},${g},${b},${a})`;
const WHITE = [255, 255, 255];

// Where the beam lands, for as long as it keeps landing. Drawn back-to-front: the mark it burns, then
// what it throws off, then the contact itself. `dir` is the beam's incoming unit vector, frozen at the
// hit. While the beam holds, emitters recycle rather than fire once; when it cuts out, whatever is
// already in the air finishes its arc and the heat fades.
function drawImpact(ctx, p, age, dir, i, S, beamOn) {
  const axis = Math.atan2(dir.y, dir.x), back = axis + Math.PI;
  const burning = age < beamOn, since = Math.max(0, age - beamOn);
  ctx.save();
  // Scorch: deepens the whole time it burns, then fades out rather than popping.
  {
    const grow = Math.min(1, age / Math.max(.2, beamOn * .5)), out = (1 - Math.min(1, since / SCORCH)) ** 2.2;
    const a = grow * out * .62, r = (12 + 40 * grow) * S;
    if (a > .004) {
      const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
      g.addColorStop(0, `rgba(17,9,25,${a})`); g.addColorStop(.6, `rgba(38,20,52,${a * .45})`); g.addColorStop(1, 'rgba(38,20,52,0)');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, TAU); ctx.fill();
    }
  }
  ctx.globalCompositeOperation = 'lighter';
  // Smoke: pours off the contact point while it burns and keeps rising after the beam stops.
  {
    const grow = Math.min(1, age / .35), out = (1 - Math.min(1, since / SCORCH)) ** 1.7;
    const a = grow * out * .46, r = (26 + 96 * Math.min(1, age / (beamOn * .8))) * S, y = p.y - Math.min(38, age * 22) * S;
    if (a > .004) {
      const g = ctx.createRadialGradient(p.x, y, 0, p.x, y, r);
      g.addColorStop(0, `rgba(96,66,114,${a})`); g.addColorStop(.55, `rgba(52,32,70,${a * .45})`); g.addColorStop(1, 'rgba(52,32,70,0)');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x, y, r, 0, TAU); ctx.fill();
    }
  }
  // Molten afterglow: full while the beam is on it, cooling once it leaves.
  {
    const out = burning ? 1 : (1 - Math.min(1, since / EMBER)) ** 1.9;
    const a = out * .6, r = (15 + 15 * Math.min(1, age / .5)) * S;
    if (a > .004) {
      const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
      g.addColorStop(0, tint(GLINT[i], a)); g.addColorStop(.45, tint(ACCENT[i], a * .55)); g.addColorStop(1, tint(ACCENT[i], 0));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, TAU); ctx.fill();
    }
  }
  // The bug only breaks once, so its fragments do not recycle.
  if (age < SHARD) {
    for (const s of SHARDS[i]) {
      if (age > s.life) continue;
      const ang = back + s.a, d = glide(s.v, age);
      ctx.globalAlpha = (1 - age / s.life) ** 1.6;
      ctx.save();
      ctx.translate(p.x + Math.cos(ang) * d, p.y + Math.sin(ang) * d + 700 * age * age * S);
      ctx.rotate(ang + s.spin * age);
      if (s.leg) {
        ctx.strokeStyle = tint(ACCENT[i], 1); ctx.lineWidth = 1.6 * S;
        ctx.beginPath(); ctx.moveTo(-s.size, 0); ctx.lineTo(0, -s.size * .6); ctx.lineTo(s.size, s.size * .3); ctx.stroke();
      } else {
        ctx.fillStyle = s.dark ? '#3a2550' : tint(ACCENT[i], .95);
        ctx.beginPath(); ctx.moveTo(-s.size, -s.size * .5); ctx.lineTo(s.size * .8, -s.size);
        ctx.lineTo(s.size, s.size * .7); ctx.lineTo(-s.size * .4, s.size); ctx.closePath(); ctx.fill();
      }
      ctx.restore();
    }
    ctx.globalAlpha = 1;
  }
  // Sparks and embers recycle for as long as the beam holds: each one relives its own arc, staggered, so
  // the contact point throws material continuously instead of once. After cut-off each finishes the arc
  // it is in and stops. `lastCycle` is what makes that exact rather than approximate.
  const recycle = (parts, spend) => {
    for (const s of parts) {
      const stagger = frac(s.a * 2.7 + s.v * .0013) * .2;
      let la = age - stagger;
      if (la < 0) continue;
      const cyc = Math.floor(la / s.life), lastCycle = Math.floor(Math.max(0, beamOn - stagger) / s.life);
      if (cyc > lastCycle) continue;
      la -= cyc * s.life;
      spend(s, la, la / s.life);
    }
  };
  ctx.lineCap = 'round';
  recycle(SPARKS[i], (s, la, u) => {
    const ang = back + s.a, d = glide(s.v, la), drop = 760 * la * la * S;
    const x = p.x + Math.cos(ang) * d, y = p.y + Math.sin(ang) * d + drop;
    const tail = Math.min(44, speed(s.v, la) * .026) * S;
    ctx.strokeStyle = tint(s.hot ? GLINT[i] : ACCENT[i], (1 - u) ** 1.7);
    ctx.lineWidth = Math.max(.35, s.w * S * (1 - u * .7));
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x - Math.cos(ang) * tail, y - Math.sin(ang) * tail - drop * .25); ctx.stroke();
  });
  recycle(EMBERS[i], (e, la, u) => {
    const ang = back + e.a, d = glide(e.v, la), flick = .55 + .45 * Math.sin(la * e.blink + e.a * 9);
    ctx.fillStyle = tint(GLINT[i], (1 - u) ** 1.5 * flick);
    ctx.beginPath();
    ctx.arc(p.x + Math.cos(ang) * d, p.y + Math.sin(ang) * d - e.rise * la * la * S * 1.6, e.size * S * (1 - u * .4), 0, TAU);
    ctx.fill();
  });
  // Shockwaves: the strike throws a big one, then the burn keeps pulsing them out while it holds.
  {
    const last = Math.floor(Math.min(age, beamOn) / RING_EVERY);
    for (let k = Math.max(0, last - 2); k <= last; k++) {
      const ra = age - k * RING_EVERY;
      if (ra < 0 || ra >= RING) continue;
      const first = k === 0;
      for (const [rate, squash, tone, w, reach, a0] of first
        ? [[3.4, .78, GLINT[i], 15, 90, 1], [1, .84, GLINT[i], 8, 252, .82], [.78, .93, ACCENT[i], 3.4, 340, .4]]
        : [[1.25, .84, GLINT[i], 5, 150, .5], [1, .93, ACCENT[i], 2.6, 210, .3]]) {
        const u = ra / RING * rate;
        if (u >= 1) continue;
        const r = (10 + reach * (1 - (1 - u) ** 3)) * S;
        ctx.strokeStyle = tint(tone, (1 - u) ** 1.9 * a0);
        ctx.lineWidth = Math.max(.4, w * (1 - u) ** 1.4 * S);
        ctx.beginPath(); ctx.ellipse(p.x, p.y, r, r * squash, axis, 0, TAU); ctx.stroke();
      }
    }
  }
  // The contact itself: white-hot and alive the whole time the beam is on it. One gradient built at the
  // origin serves the core and both bloom streaks, so the brightest part of the frame is also the cheapest.
  {
    const on = burning ? 1 : Math.max(0, 1 - since / .2);
    if (on > .01) {
      const flick = .84 + .16 * Math.sin(age * 41 + i) * Math.sin(age * 27 + 1.3);
      const strike = Math.max(0, 1 - age / BLOOM) ** 1.8;
      const r = (16 + 17 * flick + 96 * strike) * S * on;
      const g = ctx.createRadialGradient(0, 0, 0, 0, 0, r);
      g.addColorStop(0, tint(WHITE, on));
      g.addColorStop(.28, tint(GLINT[i], on * .85));
      g.addColorStop(.62, tint(ACCENT[i], on * .42));
      g.addColorStop(1, tint(ACCENT[i], 0));
      ctx.translate(p.x, p.y); ctx.rotate(axis); ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(0, 0, r, 0, TAU); ctx.fill();
      // The lens streaks are a strike signature: at full strength through a sustained burn, three of
      // them overlap into a single horizontal wash across the character's face.
      ctx.globalAlpha = on * .8 * (.14 + .86 * strike);
      for (const [sx, sy] of [[3.6, .1], [.1, 1.5]]) {
        ctx.save(); ctx.scale(sx, sy); ctx.beginPath(); ctx.arc(0, 0, r, 0, TAU); ctx.fill(); ctx.restore();
      }
      // and the light that splashes back off the surface, down the way the beam came in
      ctx.globalAlpha = on * .55 * (.3 + .7 * strike);
      ctx.save(); ctx.translate(-r * .5, 0); ctx.scale(1.9, .62); ctx.beginPath(); ctx.arc(0, 0, r, 0, TAU); ctx.fill(); ctx.restore();
    }
  }
  ctx.restore();
}

export function createIntroBugs(enabled) {
  if (!enabled) return { target: () => null, draw() {}, hide() {}, dispose() {} };
  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true'); canvas.dataset.seerBugs = '';
  // Top of the page, deliberately. The bolt and its impact must never pass behind page furniture, so this
  // sits above the character canvas (0), the ambient field (0), the board and band text (1-2) and the scan
  // overlay (8). Only the screen flash goes higher. It takes no pointer input and hides itself outside the
  // bug hunt, so being top-most costs the page nothing.
  canvas.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:9';
  document.body.append(canvas);
  const ctx = canvas.getContext('2d');
  const punch = createImpactPunch();
  const point = (i, t) => bugPosition(i, t, innerWidth, innerHeight);
  // Per shot: the landing time seer.js declared (if any), the beam direction frozen at the hit, and
  // whether the screen punch has already gone off. Rebuilt if the entrance clock ever runs backwards.
  const land = SHOTS.map(() => null), frozen = SHOTS.map(() => null), fired = SHOTS.map(() => false);
  let lastT = 0, wiped = false;
  return {
    target(t) { const i = SHOTS.findIndex(s => t < s + .1); return i < 0 || t < .9 ? null : point(i, Math.min(t, SHOTS[i])); },
    hide() { canvas.style.display = 'none'; },
    draw(t, source, active, aim) {
      if (!active || t > HIDE) { this.hide(); return; }
      canvas.style.display = 'block';
      if (canvas.width !== innerWidth || canvas.height !== innerHeight) { canvas.width = innerWidth; canvas.height = innerHeight; }
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (t < lastT - .001) { land.fill(null); frozen.fill(null); fired.fill(false); wiped = false; }
      lastT = t;
      // seer.js may own the bolt or the exact instant the beam front arrives; whatever it does not say,
      // the schedule above answers, so this keeps working on its own. The pre-shot charge ring below is
      // deliberately NOT switchable: the entrance runs in idle, which stays calm, and these three light
      // shots are meant to read against the real call's long windup rather than match it.
      const flying = SHOTS.findIndex(s => t < s + LIFE);
      if (aim && flying >= 0 && land[flying] == null && typeof aim.land === 'number') land[flying] = aim.land;
      const S = Math.max(.62, Math.min(1.1, innerWidth / 1440));
      // The finishing blow: one whiteout, once, and only if we are close enough to it to be honest about
      // the timing (a tab returning late gets the clean hand-over without the flash).
      if (!wiped && t >= WIPE && t < WIPE + .25) {
        wiped = true;
        punch.hit(innerWidth * .5, innerHeight * .45, { tone: '#ffffff', late: t - WIPE, cover: true });
      }
      SHOTS.forEach((shot, i) => {
        const at = land[i] ?? shot, p = point(i, Math.min(t, shot)), age = t - at;
        if (t < starts[i] || age > LIFE) return;
        if (age < 0) {
          const prev = point(i, Math.min(t, shot) - .015);
          ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(Math.atan2(p.y-prev.y,p.x-prev.x)+Math.PI/2);
          ctx.strokeStyle = accents[i]; ctx.fillStyle = '#22172f'; ctx.lineWidth = 2;
          for (const side of [-1, 1]) for (let leg = 0; leg < 3; leg++) {
            const y = -8 + leg * 8, kick = Math.sin(t * 22 + leg * 2) * 3;
            ctx.beginPath(); ctx.moveTo(side * 7, y); ctx.lineTo(side * 15, y - 4 + kick); ctx.lineTo(side * 20, y + 2 + kick); ctx.stroke();
          }
          ctx.beginPath(); ctx.ellipse(0, 2, 9, 14, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(0, -9); ctx.lineTo(0, 14); ctx.stroke();
          ctx.fillStyle = '#fff'; ctx.fillRect(-6, -8, 4, 4); ctx.fillRect(2, -8, 4, 4);
          ctx.beginPath(); ctx.moveTo(-4, -11); ctx.lineTo(-9, -20); ctx.moveTo(4, -11); ctx.lineTo(9, -20); ctx.stroke(); ctx.restore();
          if(age>-.24){const charge=1+age/.24;ctx.save();ctx.strokeStyle='#e3b8ff';ctx.globalAlpha=charge*.8;ctx.lineWidth=1.5;
            ctx.beginPath();ctx.arc(source.x,source.y,16-charge*10,-t*9,-t*9+Math.PI*1.6);ctx.stroke();ctx.restore();}
        } else {
          // Freeze the incoming direction on the landing frame: the eye keeps moving, the debris must not.
          if (!frozen[i]) {
            const dx = p.x - source.x, dy = p.y - source.y, len = Math.hypot(dx, dy) || 1;
            frozen[i] = { x: dx / len, y: dy / len };
          }
          // The screen punch, on the landing frame and nowhere else. Late arrivals (a tab coming back)
          // get the impact but not the shake.
          if (!fired[i] && age < .2) {
            fired[i] = true;
            punch.hit(p.x, p.y, { strength: [.85, 1, 1.15][i], tone: glints[i], late: age });
          }
          drawImpact(ctx, p, age, frozen[i], i, S, BOLT);
          // The bolt: mega-wide, opaque, and it HOLDS. Fast attack, a long sustain, then a release.
          // The soft halo is additive so it blooms, but the CORE is source-over at full alpha -- an
          // additive core washes out to transparent over bright pixels, and this one has to read solid.
          if (age < BOLT + FALL && !aim?.bolt) {
            const env = Math.min(1, age / .05) * Math.max(0, 1 - Math.max(0, age - BOLT) / FALL);
            if (env > .01) {
              const dx = p.x - source.x, dy = p.y - source.y, len = Math.hypot(dx, dy) || 1;
              const nx = -dy / len, ny = dx / len;
              // Vibration: a fast width flutter, plus a whole-beam lateral buzz. The beam is a live thing
              // under load, not a drawn line.
              const flick = .82 + .18 * Math.sin(age * 71 + i * 2) * Math.sin(age * 43);
              const buzz = Math.sin(age * 118 + i * 2.1) * 4.5 * S;
              // It does not stop at the bug. The lance carries a full viewport diagonal PAST it and off
              // the screen, so its far end is never visible and it reads as a beam that just keeps going.
              // The ripple is keyed to the on-screen span and vanishes at the target (sin(pi) = 0), so the
              // shot straightens out as it leaves rather than wobbling off into the corner.
              const ext = 1 + Math.hypot(innerWidth, innerHeight) * 1.4 / len;
              const lance = (w, style) => {
                ctx.strokeStyle = style; ctx.lineWidth = Math.max(.6, w * S * env * flick);
                ctx.beginPath(); ctx.moveTo(source.x, source.y);
                for (let j = 1; j <= 26; j++) {
                  const q = (j / 26) * ext, wob = Math.sin(Math.min(1, q) * Math.PI);
                  const rip = wob * (Math.sin(j * 1.7 + age * 58) * 15 + Math.sin(j * 3.9 - age * 91) * 7) * S * env + buzz * env;
                  ctx.lineTo(source.x + dx * q + nx * rip, source.y + dy * q + ny * rip);
                }
                ctx.stroke();
              };
              ctx.save(); ctx.lineCap = 'round'; ctx.lineJoin = 'round';
              ctx.globalCompositeOperation = 'lighter';
              for (const [w, a, tone] of [[300, .05, ACCENT[i]], [200, .09, ACCENT[i]], [124, .16, GLINT[i]]]) lance(w, tint(tone, a * env));
              // SOLID. Drawn source-over at full alpha so the beam OCCLUDES what it crosses instead of
              // tinting it — additive alone lets the page show straight through, which is what made it
              // read as transparent. This is most of the beam's width now, not a thin filament in it.
              ctx.globalCompositeOperation = 'source-over';
              lance(88, tint(ACCENT[i], 1));
              lance(58, tint(GLINT[i], 1));
              lance(30, tint(WHITE, 1));
              ctx.globalCompositeOperation = 'lighter';
              // The muzzle: where it leaves the eye, blown out and streaked along its own axis.
              const punchOut = Math.max(0, 1 - age / .16) ** 1.6;
              const mr = (16 + 22 * env + 74 * punchOut) * S;
              ctx.translate(source.x, source.y); ctx.rotate(Math.atan2(dy, dx));
              const mg = ctx.createRadialGradient(0, 0, 0, 0, 0, mr);
              mg.addColorStop(0, tint(WHITE, env * (.5 + .5 * punchOut)));
              mg.addColorStop(.3, tint(GLINT[i], env * (.34 + .46 * punchOut)));
              mg.addColorStop(.7, tint(ACCENT[i], env * .26));
              mg.addColorStop(1, tint(ACCENT[i], 0));
              ctx.fillStyle = mg;
              ctx.beginPath(); ctx.arc(0, 0, mr, 0, TAU); ctx.fill();
              ctx.globalAlpha = .25 + .75 * punchOut;
              ctx.save(); ctx.scale(2.8, .2); ctx.beginPath(); ctx.arc(0, 0, mr, 0, TAU); ctx.fill(); ctx.restore();
              ctx.globalAlpha = 1;
              ctx.restore();
            }
          }
          ctx.globalAlpha = 1;
        }
      });
    },
    dispose() { punch.dispose(); canvas.remove(); },
  };
}
