import { GLASS_THEME } from './glass-theme.js';
import { mountPanelEffects, PANEL_THEME } from './panel-effects.js';
// Presentation only: real data continues loading during the entrance.
export function createIntroStage(canvas, reduced) {
  const stage = canvas.closest('.seerband');
  if (!stage) return { fullscreen: false, update: () => 0, react() {}, finish() {}, dispose() {} };
  const style = document.createElement('style');
  style.textContent = `
    body.seer-room { overflow-x:hidden; }
    .seer-room .seerband { position:fixed;inset:0;width:100%;height:100dvh;max-height:none;min-height:0;border:0;pointer-events:none;z-index:0; }
    .seer-room .seerband #seer { width:100%;height:100%; }
    .seer-room .bandtext { position:fixed;left:54%;top:105px;bottom:auto;right:28px; }
    .seer-room .bandnav { position:fixed;top:66px;right:28px;pointer-events:auto; }
    .seer-room > .tboard, .seer-room > .state-msg { position:relative;z-index:2;width:48%;margin:190px 0 0 auto;box-sizing:border-box;padding:24px 28px 72px;background:linear-gradient(90deg,rgba(6,6,8,.85),#060608 22%); }
    .seer-room .tiles { grid-template-columns:repeat(2,minmax(0,1fr));gap:18px 0; }
    .seer-room .tile:nth-child(3) { border-left:0;padding-left:0; }
    .seer-room .tile .val { font-size:clamp(30px,3.5vw,54px); }
    .seer-room .stack, .seer-room .rowbody { grid-template-columns:minmax(0,1fr); }
    .seer-room .c3 { grid-column:auto; }
    .seer-room .rulehead { flex-wrap:wrap; }
    .seer-room .seerband.noseer { height:100dvh;min-height:0;padding:0; }
    .seer-room .seerband.noseer .bandtext { position:fixed;margin:0;padding:0; }
    @media(max-width:760px) {
      .seer-room .bandtext { left:12%;top:53vh;right:18px; }
      .seer-room .bandnav { top:66px;right:16px; }
      .seer-room > .tboard, .seer-room > .state-msg { width:92%;margin-top:64vh;padding:20px 16px 64px;background:rgba(6,6,8,.94); }
      .seer-room .tile .val { font-size:36px; }
    }
    body.seer-intro { overflow:hidden; }
    .seer-intro > .sitenav, .seer-intro .bandtext, .seer-intro .bandnav,
    .seer-intro > .robotlive, .seer-intro > .tboard, .seer-intro > .state-msg { opacity:0;visibility:hidden;pointer-events:none; }
    .seer-intro .seerband { max-height:none;min-height:0;border-color:transparent; }
  `;
  style.textContent += GLASS_THEME + PANEL_THEME;
  document.head.append(style);
  const heading=stage.querySelector('.bandtext'),headingHome=heading?.parentNode,headingNext=heading?.nextSibling;
  if(heading) document.body.append(heading);
  const status = document.createElement('span'); status.className = 'seer-state'; status.setAttribute('role', 'status'); status.textContent = ''; status.hidden = true;
  stage.querySelector('.bandnav')?.prepend(status);
  document.body.classList.add('seer-room');
  const removePanelEffects = mountPanelEffects();
  document.body.classList.add('seer-intro');
  let done = false, requested = false, animations = [];
  const finish = (animate = false) => {
    if (done) return;
    done = true;
    document.body.classList.remove('seer-intro');
    if (animate) {
      const items = document.querySelectorAll('body > .sitenav, body > .bandtext, .seerband .bandnav, body > .robotlive, .tboard > *, body > .state-msg:not([hidden])');
      animations = [...items].map((el, i) => el.animate([
        { opacity: 0, transform: `translateX(${i < 3 ? 20 : 64}px)` },
        { opacity: 1, transform: 'translateX(0)' },
      ], { duration: 520, delay: Math.min(i, 7) * 65, easing: 'cubic-bezier(.16,1,.3,1)', fill: 'backwards' }));
    }
  };
  const escape = e => { if (e.key === 'Escape') { requested = true; finish(); } };
  document.addEventListener('keydown', escape);
  const safety = setTimeout(() => finish(), 8000);
  if (reduced) finish();
  return {
    fullscreen: true,
    react(state) {
      document.body.dataset.seerState = state;
      status.hidden = state === 'idle';
      status.textContent = { idle:'', summoned:'Looking', thinking:'Investigating', verdict:'Answer ready', stumped:'No answer' }[state];
    },
    update(time, reducedMotion) {
      if (reducedMotion || requested) { finish(); return 0; }
      if (done) return 0;
      if (time > 0) clearTimeout(safety);
      const u = Math.min(1, Math.max(0, (time - 3.35) / 2.3));
      // Minimum-jerk travel: velocity AND acceleration vanish at both ends.
      const focus = 1 - u*u*u*(u*(u*6-15)+10);
      if (u === 1) finish(true);
      return focus;
    },
    finish() { requested = true; finish(); },
    dispose() { removePanelEffects(); finish(); clearTimeout(safety); animations.forEach(a => a.cancel()); if(heading)headingHome.insertBefore(heading,headingNext); style.remove(); status.remove(); delete document.body.dataset.seerState; document.body.classList.remove('seer-room'); document.removeEventListener('keydown', escape); },
  };
}
