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
const BLOOM = .1, RING = .3, SPARK = .62, SHARD = .78, EMBER = .9, SCORCH = .95;
const LIFE = 1, HIDE = SHOTS[SHOTS.length - 1] + LIFE;

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
  v: 420 + r(2) * 1150 + (k % 7 ? 0 : 900),
  life: .26 + r(3) * .34, w: 1 + r(4) * 1.7, hot: r(5) > .4 }));
const EMBERS = table(14, r => ({ a: (r(6) - .5) * 3.1, v: 90 + r(7) * 230, life: .5 + r(8) * .4,
  rise: 34 + r(9) * 62, size: 1.3 + r(10) * 2.1, blink: 12 + r(11) * 16 }));
const SHARDS = table(12, r => ({ a: (r(12) - .5) * 3, v: 180 + r(13) * 340, life: .4 + r(14) * .36,
  size: 3 + r(15) * 5.5, spin: (r(16) - .5) * 26, leg: r(17) > .55, dark: r(18) > .45 }));
// Air drag, integrated so position and speed are closed-form rather than stepped.
const DRAG = 3.4, glide = (v, t) => v * (1 - Math.exp(-DRAG * t)) / DRAG, speed = (v, t) => v * Math.exp(-DRAG * t);
const rgbOf = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
const ACCENT = accents.map(rgbOf), GLINT = glints.map(rgbOf);
const tint = ([r, g, b], a) => `rgba(${r},${g},${b},${a})`;

// Where the beam lands. Drawn back-to-front: the mark it leaves, then what it throws off, then the
// contact itself on top. `dir` is the beam's incoming unit vector, frozen at the moment of the hit.
function drawImpact(ctx, p, age, dir, i, S) {
  const axis = Math.atan2(dir.y, dir.x), back = axis + Math.PI;
  ctx.save();
  // Scorch: normal blending, under everything, and tight. On a dark field it barely shows, which is
  // right -- the smoke puff below it is what actually reads. It fades out rather than popping.
  if (age < SCORCH) {
    const u = age / SCORCH, a = Math.min(1, age / .045) * (1 - u) ** 2.2 * .6, r = (10 + u * 26) * S;
    const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
    g.addColorStop(0, `rgba(17,9,25,${a})`); g.addColorStop(.6, `rgba(38,20,52,${a * .45})`); g.addColorStop(1, 'rgba(38,20,52,0)');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, TAU); ctx.fill();
  }
  ctx.globalCompositeOperation = 'lighter';
  // Smoke: a wide, faint puff lit from inside by the afterglow, drifting up as it thins out.
  if (age < SCORCH) {
    const u = age / SCORCH, a = Math.min(1, age / .06) * (1 - u) ** 1.7 * .44, r = (18 + u * 86) * S, y = p.y - u * 30 * S;
    const g = ctx.createRadialGradient(p.x, y, 0, p.x, y, r);
    g.addColorStop(0, `rgba(96,66,114,${a})`); g.addColorStop(.55, `rgba(52,32,70,${a * .45})`); g.addColorStop(1, 'rgba(52,32,70,0)');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x, y, r, 0, TAU); ctx.fill();
  }
  // Afterglow: the spot stays hot long after the bloom is gone.
  if (age < EMBER) {
    const u = age / EMBER, a = (1 - u) ** 1.9 * .66, r = (16 + u * 34) * S;
    const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
    g.addColorStop(0, tint(GLINT[i], a)); g.addColorStop(.45, tint(ACCENT[i], a * .55)); g.addColorStop(1, tint(ACCENT[i], 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, TAU); ctx.fill();
  }
  // What is left of the bug: chips of carapace and snapped-off legs, tumbling as they fall.
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
  // Embers: slow, buoyant, guttering.
  if (age < EMBER) {
    for (const e of EMBERS[i]) {
      if (age > e.life) continue;
      const u = age / e.life, ang = back + e.a, d = glide(e.v, age);
      const flick = .55 + .45 * Math.sin(age * e.blink + e.a * 9);
      ctx.fillStyle = tint(GLINT[i], (1 - u) ** 1.5 * flick);
      ctx.beginPath();
      ctx.arc(p.x + Math.cos(ang) * d, p.y + Math.sin(ang) * d - e.rise * age * age * S * 1.6, e.size * S * (1 - u * .4), 0, TAU);
      ctx.fill();
    }
  }
  // Sparks: thrown back along the beam, streaked by their own speed, dragged and pulled down.
  if (age < SPARK) {
    ctx.lineCap = 'round';
    for (const s of SPARKS[i]) {
      if (age > s.life) continue;
      const u = age / s.life, ang = back + s.a, d = glide(s.v, age), drop = 760 * age * age * S;
      const x = p.x + Math.cos(ang) * d, y = p.y + Math.sin(ang) * d + drop;
      const tail = Math.min(44, speed(s.v, age) * .026) * S;
      ctx.strokeStyle = tint(s.hot ? GLINT[i] : ACCENT[i], (1 - u) ** 1.7);
      ctx.lineWidth = Math.max(.35, s.w * S * (1 - u * .7));
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x - Math.cos(ang) * tail, y - Math.sin(ang) * tail - drop * .25); ctx.stroke();
    }
  }
  // Shockwave: squashed along the beam axis, so it reads as a surface struck at an angle. A thick,
  // bright contact flare snaps out first, then one wider ring that thins as it goes. Both are gone
  // inside a third of a second -- a slow, wide ring reads as a soap bubble, not a shock.
  if (age < RING) {
    for (const [rate, squash, tone, w, reach, a0] of [[3.4, .78, GLINT[i], 11, 62, 1], [1, .84, GLINT[i], 5.5, 172, .8]]) {
      const u = age / RING * rate;
      if (u >= 1) continue;
      const r = (10 + reach * (1 - (1 - u) ** 3)) * S;
      ctx.strokeStyle = tint(tone, (1 - u) ** 1.9 * a0);
      ctx.lineWidth = Math.max(.4, w * (1 - u) ** 1.4 * S);
      ctx.beginPath(); ctx.ellipse(p.x, p.y, r, r * squash, axis, 0, TAU); ctx.stroke();
    }
  }
  // The contact itself: white-hot, on top, and over almost before it is there. One gradient built at the
  // origin serves the core and both bloom streaks, so the brightest part of the frame is also the cheapest.
  if (age < BLOOM) {
    const u = age / BLOOM, fall = (1 - u) ** 1.8, r = (20 + 52 * Math.min(1, age / .016)) * (.55 + .45 * fall) * S;
    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, r);
    g.addColorStop(0, `rgba(255,255,255,${fall})`);
    g.addColorStop(.28, tint(GLINT[i], fall * .85));
    g.addColorStop(.62, tint(ACCENT[i], fall * .42));
    g.addColorStop(1, tint(ACCENT[i], 0));
    ctx.translate(p.x, p.y); ctx.rotate(axis); ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(0, 0, r, 0, TAU); ctx.fill();
    ctx.globalAlpha = fall * .8;
    for (const [sx, sy] of [[3.6, .1], [.1, 1.5]]) {
      ctx.save(); ctx.scale(sx, sy); ctx.beginPath(); ctx.arc(0, 0, r, 0, TAU); ctx.fill(); ctx.restore();
    }
    // and the light that splashes back off the surface, down the way the beam came in
    ctx.globalAlpha = fall * .55;
    ctx.save(); ctx.translate(-r * .5, 0); ctx.scale(1.9, .62); ctx.beginPath(); ctx.arc(0, 0, r, 0, TAU); ctx.fill(); ctx.restore();
  }
  ctx.restore();
}

export function createIntroBugs(enabled) {
  if (!enabled) return { target: () => null, draw() {}, hide() {}, dispose() {} };
  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true'); canvas.dataset.seerBugs = '';
  canvas.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:1';
  document.body.append(canvas);
  const ctx = canvas.getContext('2d');
  const punch = createImpactPunch();
  const point = (i, t) => bugPosition(i, t, innerWidth, innerHeight);
  // Per shot: the landing time seer.js declared (if any), the beam direction frozen at the hit, and
  // whether the screen punch has already gone off. Rebuilt if the entrance clock ever runs backwards.
  const land = SHOTS.map(() => null), frozen = SHOTS.map(() => null), fired = SHOTS.map(() => false);
  let lastT = 0;
  return {
    target(t) { const i = SHOTS.findIndex(s => t < s + .1); return i < 0 || t < .9 ? null : point(i, Math.min(t, SHOTS[i])); },
    hide() { canvas.style.display = 'none'; },
    draw(t, source, active, aim) {
      if (!active || t > HIDE) { this.hide(); return; }
      canvas.style.display = 'block';
      if (canvas.width !== innerWidth || canvas.height !== innerHeight) { canvas.width = innerWidth; canvas.height = innerHeight; }
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (t < lastT - .001) { land.fill(null); frozen.fill(null); fired.fill(false); }
      lastT = t;
      // seer.js may own the bolt or the exact instant the beam front arrives; whatever it does not say,
      // the schedule above answers, so this keeps working on its own. The pre-shot charge ring below is
      // deliberately NOT switchable: the entrance runs in idle, which stays calm, and these three light
      // shots are meant to read against the real call's long windup rather than match it.
      const flying = SHOTS.findIndex(s => t < s + LIFE);
      if (aim && flying >= 0 && land[flying] == null && typeof aim.land === 'number') land[flying] = aim.land;
      const S = Math.max(.62, Math.min(1.1, innerWidth / 1440));
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
          if (!fired[i] && age < .2) { fired[i] = true; punch.hit(p.x, p.y, { strength: [.85, 1, 1.15][i], tone: glints[i], late: age }); }
          drawImpact(ctx, p, age, frozen[i], i, S);
          if (age < .24 && !aim?.bolt) {
            const power = 1 - age / .24, dx = p.x-source.x, dy = p.y-source.y, len = Math.hypot(dx,dy)||1;
            ctx.save(); ctx.lineCap = 'round';
            for (const [width, alpha, color] of [[18,.10,accents[i]],[6,.65,accents[i]],[1.8,1,'#ffe4ef']]) {
              ctx.strokeStyle = color; ctx.lineWidth = width * power; ctx.globalAlpha = alpha * power;
              ctx.shadowColor = '#c46bff'; ctx.shadowBlur = width === 6 ? 18 : 0;
              ctx.beginPath(); ctx.moveTo(source.x,source.y);
              for(let j=1;j<=12;j++) { const u=j/12, ripple=Math.sin(u*Math.PI)*Math.sin(j*2+age*65)*3*power;
                ctx.lineTo(source.x+dx*u-dy/len*ripple,source.y+dy*u+dx/len*ripple); }
              ctx.stroke();
            }
            ctx.shadowBlur = 0; ctx.globalAlpha = power; ctx.strokeStyle='#d995ff'; ctx.lineWidth=1;
            ctx.beginPath();ctx.arc(source.x,source.y,5+age*40,0,Math.PI*2);ctx.stroke();
            ctx.restore();
          }
          ctx.globalAlpha = 1;
        }
      });
    },
    dispose() { punch.dispose(); canvas.remove(); },
  };
}
