// Decorative geometry stays outside sensor imagery and never receives pointer input.
export function mountPanelEffects() {
  const backdrop = document.createElement('div');
  backdrop.className = 'seer-ambient';
  backdrop.setAttribute('aria-hidden', 'true');
  const forms = [
    [4,14,68,'ring'],[29,10,32,'diamond'],[65,16,100,'ring'],[93,24,44,'diamond'],
    [8,43,22,'spark'],[40,39,36,'bar'],[85,47,120,'ring'],[97,62,25,'spark'],
    [5,78,85,'ring'],[30,88,28,'spark'],[62,76,46,'diamond'],[88,91,58,'bar'],
    [18,60,30,'diamond'],[76,5,20,'spark'],[48,64,72,'ring'],[52,96,24,'diamond']
  ];
  forms.forEach(([x,y,size,form],i) => {
    const shape = document.createElement('i'); shape.className = `ambient-shape ambient-${form}`;
    shape.style.cssText = `--x:${x}%;--y:${y}%;--size:${size}px;--tone:${['#f757b6','#a894ff','#ffad87','#75ddd5'][i%4]};--delay:${-i*2.7}s;--turn:${i*23}deg`;
    backdrop.append(shape);
  });
  document.body.prepend(backdrop);
  const panels = [...document.querySelectorAll('body > .robotlive, .tboard > section')];
  const ornaments = panels.map((panel, index) => {
    panel.classList.add('seer-lit-panel');
    panel.style.setProperty('--panel-hue', ['#f757b6', '#a894ff', '#ffad87', '#75ddd5'][index % 4]);
    const ornament = document.createElement('div');
    ornament.className = 'seer-panel-shapes';
    ornament.setAttribute('aria-hidden', 'true');
    ornament.innerHTML = `<svg viewBox="0 0 240 110" fill="none" xmlns="http://www.w3.org/2000/svg">
      <g class="shape-orbit"><ellipse cx="165" cy="45" rx="49" ry="20" transform="rotate(-28 165 45)"/><ellipse cx="165" cy="45" rx="24" ry="35" transform="rotate(32 165 45)"/></g>
      <path class="shape-solid" d="M81 30 99 40 93 60 72 62 63 43Z"/>
      <path class="shape-facet" d="m81 30 2 18 16-8M83 48l10 12m-10-12-20-5"/>
      <path class="shape-spark" d="m216 73 3 9 9 3-9 3-3 9-3-9-9-3 9-3Z"/>
      <rect x="24" y="74" width="26" height="8" rx="4" transform="rotate(-32 24 74)"/>
      <circle cx="119" cy="86" r="3"/><circle cx="204" cy="17" r="2"/>
    </svg>`;
    panel.prepend(ornament);
    return ornament;
  });
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) entry.target.classList.toggle('effects-visible', entry.isIntersecting);
  });
  panels.forEach(panel => observer.observe(panel));
  const visibility = () => document.body.classList.toggle('effects-paused', document.hidden);
  document.addEventListener('visibilitychange', visibility);
  return () => {
    backdrop.remove();
    observer.disconnect(); document.removeEventListener('visibilitychange', visibility);
    document.body.classList.remove('effects-paused');
    ornaments.forEach(node => node.remove());
    panels.forEach(panel => { panel.classList.remove('seer-lit-panel', 'effects-visible'); panel.style.removeProperty('--panel-hue'); });
  };
}

export const PANEL_THEME = `
body.seer-room { background:#0d0914; }
.seer-room:not(.seer-intro) .seerband { background:transparent; }
.seer-ambient { position:fixed;inset:0;overflow:hidden;pointer-events:none;z-index:0;
  background:radial-gradient(ellipse at 8% 22%,#9f24622e,transparent 44%),radial-gradient(ellipse at 88% 54%,#6540b638,transparent 48%),radial-gradient(ellipse at 40% 100%,#227d7920,transparent 50%),#0d0914; }
.seer-ambient::before { content:'';position:absolute;inset:0;opacity:.22;
  background-image:radial-gradient(circle,#e2b4da 0 1px,transparent 1.5px),radial-gradient(circle,#9b8bcb 0 1px,transparent 1.5px);
  background-size:127px 139px,211px 197px;background-position:17px 32px,80px 90px; }
.ambient-shape { position:absolute;left:var(--x);top:var(--y);width:var(--size);height:var(--size);color:var(--tone);
  border:1px solid currentColor;opacity:.4;box-shadow:0 0 18px color-mix(in srgb,var(--tone) 25%,transparent),inset 0 0 12px color-mix(in srgb,var(--tone) 12%,transparent);
  animation:ambient-drift 19s ease-in-out infinite alternate;animation-delay:var(--delay); }
.ambient-ring { border-radius:50%;height:calc(var(--size)*.55);border-width:2px; }
.ambient-ring::after { content:'';position:absolute;inset:7px -9px;border:1px solid currentColor;border-radius:50%;transform:rotate(55deg);opacity:.5; }
.ambient-diamond { border-radius:6px;background:linear-gradient(135deg,color-mix(in srgb,var(--tone) 23%,transparent),transparent); }
.ambient-bar { height:8px;border-radius:8px;background:currentColor;opacity:.25; }
.ambient-spark { border:0;background:currentColor;clip-path:polygon(50% 0,61% 39%,100% 50%,61% 61%,50% 100%,39% 61%,0 50%,39% 39%); }
.seer-intro .seer-ambient { visibility:hidden; }
.effects-paused .ambient-shape { animation-play-state:paused; }
@keyframes ambient-drift { from { transform:translate3d(-5px,-9px,0) rotate(var(--turn)); } to { transform:translate3d(9px,14px,0) rotate(calc(var(--turn) + 22deg)); } }
@media(max-width:760px) { .ambient-shape { width:calc(var(--size)*.65);height:calc(var(--size)*.65);opacity:.3; }.ambient-bar { height:6px; } }
@media(prefers-reduced-motion:reduce) { .ambient-shape { animation:none;transform:rotate(var(--turn)); } }

.seer-room .seer-lit-panel { position:relative;isolation:isolate;--panel-hue:#f757b6;
  border-color:color-mix(in srgb,var(--panel-hue) 26%,#292331);
  box-shadow:inset 0 1px 0 #ffffff16,0 14px 50px #0004,0 0 32px color-mix(in srgb,var(--panel-hue) 8%,transparent); }
.seer-lit-panel::before { content:'';position:absolute;inset:0;border-radius:inherit;z-index:-1;pointer-events:none;
  background:radial-gradient(ellipse at 98% 0,color-mix(in srgb,var(--panel-hue) 17%,transparent),transparent 42%),radial-gradient(ellipse at 0 100%,#8b65d90a,transparent 50%); }
.seer-panel-shapes { position:absolute;right:12px;top:-29px;width:210px;height:96px;pointer-events:none;z-index:-1;color:var(--panel-hue);opacity:.65; }
.seer-panel-shapes svg { width:100%;height:100%;overflow:visible;stroke:currentColor;stroke-width:1.4;filter:drop-shadow(0 0 7px color-mix(in srgb,var(--panel-hue) 45%,transparent)); }
.seer-panel-shapes .shape-solid { fill:color-mix(in srgb,var(--panel-hue) 24%,#181321);stroke-width:1.5; }
.seer-panel-shapes .shape-facet { opacity:.6; }
.seer-panel-shapes .shape-spark { fill:currentColor;stroke:none; }
.seer-panel-shapes .shape-orbit { transform-origin:165px 45px;animation:panel-orbit 16s ease-in-out infinite alternate;animation-play-state:paused; }
.effects-visible .shape-orbit { animation-play-state:running; }
.effects-paused .shape-orbit { animation-play-state:paused; }
.seer-room .seer-lit-panel > .rulehead { position:relative;width:fit-content;max-width:100%;padding:4px 12px 4px 0;
  color:#d9cbdf;background:linear-gradient(90deg,#11111bed 80%,transparent); }
.seer-room .seer-lit-panel > .rulehead::before { background:var(--panel-hue);box-shadow:0 0 12px var(--panel-hue); }
.seer-room .tboard { row-gap:36px; }
.seer-room .tile { position:relative;overflow:hidden; }
.seer-room .tile::after { content:'';position:absolute;right:-9px;top:-9px;width:30px;height:30px;border:1px solid #ed9ada30;border-radius:8px;transform:rotate(30deg);pointer-events:none; }
.seer-room .tile:hover { box-shadow:inset 0 0 24px #d48fff0a,0 0 20px #ed6eac0d; }
.seer-room .spatial-tabs button[aria-selected=true] { background:linear-gradient(125deg,#5b2f54,#302a48);box-shadow:inset 0 0 0 1px #f68dc066,0 0 16px #f757b617;color:#ffe4f6; }
.seer-room .spatial-stage,.seer-room .rl-stage { box-shadow:0 0 0 1px #eaa3df0a,0 0 25px #b47cff0d; }
@keyframes panel-orbit { from { transform:rotate(-9deg);opacity:.55; } to { transform:rotate(12deg);opacity:1; } }
@media(max-width:760px) { .seer-panel-shapes { width:125px;height:58px;right:5px;top:-18px;opacity:.48; }.seer-room .tboard { row-gap:25px; } }
@media(prefers-reduced-motion:reduce) { .seer-panel-shapes .shape-orbit { animation:none; } }
`;

// The screen-level punch behind a laser impact: a flash centred on the point of contact, and a short
// shake of the scene layers. The caller fires it on the frame the beam LANDS — never during a windup.
// Both are one rise and one fall, never a repeat: a flash, not a strobe.
export function createImpactPunch() {
  if (!document.getElementById('seer-impact-style')) {
    const style = document.createElement('style');
    style.id = 'seer-impact-style'; style.textContent = IMPACT_THEME;
    document.head.append(style);
  }
  const flash = document.createElement('div');
  flash.className = 'seer-impact-flash'; flash.setAttribute('aria-hidden', 'true');
  document.body.append(flash);
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  // Shake the character's own canvas, never the band around it. The band is the page's layout anchor --
  // displacing it moves a box other code measures -- and the canvas fills it, so jolting the canvas reads
  // exactly the same on screen. It also keeps the bug canvas untransformed, which matters: seer.js derives
  // the beam's source from the character canvas's client rect, so that point already carries the shake.
  // Drawing the bolt on an untransformed canvas therefore anchors it to the eye exactly, with no
  // double-displacement to correct for.
  const layers = () => {
    const band = [...document.querySelectorAll('.seer-room .seerband canvas')];
    return band.length ? band : [...document.querySelectorAll('[data-seer-bugs]')];
  };
  let running = [];
  return {
    // `late` is how far past the landing instant the caller already is, in seconds. A freshly created
    // animation is PENDING until the compositor hands it a start time, which costs a frame -- so the
    // flash would bloom one frame after the beam arrived. Back-dating startTime onto the hit's own
    // clock cancels both that frame and `late`, and style for THIS frame already sees it.
    hit(x, y, { strength = 1, tone = '#ffe4f6', late = 0 } = {}) {
      running.forEach(a => a.cancel()); running = [];
      const gentle = motion.matches, back = Math.max(0, late * 1000);
      const onto = a => { try { if (document.timeline.currentTime != null) a.startTime = document.timeline.currentTime - back; } catch {} return a; };
      flash.style.setProperty('--fx', `${Math.round(x)}px`);
      flash.style.setProperty('--fy', `${Math.round(y)}px`);
      flash.style.setProperty('--ftone', tone);
      running.push(onto(flash.animate(
        gentle ? [{ opacity: 0 }, { opacity: .17, offset: .38 }, { opacity: 0 }]
               : [{ opacity: 0 }, { opacity: Math.min(.52, .34 * strength), offset: .055 }, { opacity: 0 }],
        { duration: gentle ? 560 : 250, easing: 'cubic-bezier(.2,.75,.3,1)' })));
      if (gentle) return;                                  // reduced motion still reads as a hit, but nothing moves
      // Decaying kicks, direction stepped by the golden angle so no two land the same way. The first
      // kick sits at 4.5% of the run -- about 10ms -- so the hit and the jolt are the same frame, and
      // the last keyframe is dead centre so nothing is left displaced.
      const amp = 13 * strength * Math.min(1, innerWidth / 1100), offs = [0, .045, .18, .34, .5, .66, .82, 1], keys = [];
      for (let s = 0; s < offs.length; s++) {
        const u = offs[s], decay = s === 0 || s === offs.length - 1 ? 0 : (1 - u) ** 1.6, a = s * 2.399963;
        keys.push({ offset: u, transform: `translate3d(${(Math.cos(a) * amp * decay).toFixed(2)}px,${(Math.sin(a) * amp * decay * .66).toFixed(2)}px,0)` });
      }
      // composite:'add' layers the shake ON TOP of whatever transform a layer already carries.
      for (const el of layers()) running.push(onto(el.animate(keys, { duration: 230, easing: 'linear', composite: 'add' })));
    },
    dispose() { running.forEach(a => a.cancel()); running = []; flash.remove(); },
  };
}

export const IMPACT_THEME = `
.seer-impact-flash { position:fixed;inset:0;pointer-events:none;z-index:4;opacity:0;will-change:opacity;mix-blend-mode:screen;
  background:radial-gradient(circle 44vmax at var(--fx,50%) var(--fy,50%),
    color-mix(in srgb,var(--ftone,#ffe4f6) 82%,#fff) 0,
    color-mix(in srgb,var(--ftone,#ffe4f6) 34%,transparent) 13%,
    transparent 58%); }
@media(prefers-reduced-motion:reduce) { .seer-impact-flash { mix-blend-mode:normal; } }
`;
