// tune.js — live control panel for the landing, opened with ?tune
//
// The blender-to-threejs rule this follows: a request of the form "move it / bigger /
// a bit left" is answered with a SWITCH, not with a guessed edit and a screenshot round
// trip. Drag until it looks right, press COPY, paste the block back, and it gets baked
// as the new default. Every knob here is a runtime value, never a compile-time constant.
import { POP } from './robotpop.js';

const FIELDS = [
  ['— entrance timing —'],
  ['at',     0,    8,   0.05, 's   when the robot starts rising (after the title)'],
  ['rise',   0.2,  3,   0.05, 's   time to clear the floor line'],
  ['from',  -6,    0,   0.1,  'm   y it starts at, below frame'],
  ['to',    -2,    3,   0.05, 'm   y it settles at'],
  ['— personality —'],
  ['over',   0,    1,   0.01, 'm   overshoot above the settle height'],
  ['lean',   0,    1.2, 0.01, 'rad first counter-lean as it catches balance'],
  ['hz',     0.2,  6,   0.05, 'Hz  how fast it wobbles'],
  ['decay',  0.1,  6,   0.05, '    how fast the wobble dies'],
  ['— size —'],
  ['height', 0.5,  6,   0.05, 'm   fitted height on the stage'],
  ['z',     -6,    2,   0.05, 'm   depth — more negative pushes it BEHIND the text'],
];
const XF = [
  ['— robot transform —'],
  ['px', -6, 6, 0.01, 'm   position x'],
  ['py', -6, 6, 0.01, 'm   position y  (offset on top of the entrance)'],
  ['pz', -6, 6, 0.01, 'm   position z'],
  ['ry', -3.15, 3.15, 0.01, 'rad yaw — turn it until it faces you'],
  ['sc',  0.1, 4, 0.01, '    extra uniform scale'],
];
const xf = { px: 0, py: 0, pz: 0, ry: 0, sc: 1 };

export function buildTune(world) {
  if (!new URLSearchParams(location.search).has('tune')) return { update() {} };
  const el = document.createElement('div');
  el.style.cssText = `position:fixed;top:12px;right:12px;width:360px;max-height:94vh;overflow:auto;
    background:#111114ee;color:#e8e8ea;font:11px ui-monospace,SFMono-Regular,Menlo,monospace;
    padding:12px 14px;border:1px solid #33343a;border-radius:8px;z-index:9999;backdrop-filter:blur(6px)`;
  const rows = [];
  const mk = (spec, get, set) => {
    const [k, lo, hi, step, help] = spec;
    if (lo === undefined) {
      const h = document.createElement('div');
      h.textContent = k; h.style.cssText = 'margin:10px 0 4px;color:#8a8a93;letter-spacing:.06em';
      el.appendChild(h); return;
    }
    const row = document.createElement('div'); row.style.cssText = 'margin:5px 0';
    const lab = document.createElement('div');
    lab.style.cssText = 'display:flex;justify-content:space-between;align-items:baseline';
    const name = document.createElement('span'); name.textContent = k;
    const num = document.createElement('input');
    num.type = 'number'; num.step = step; num.value = get(k);
    num.style.cssText = 'width:74px;background:#1c1d22;color:#e8e8ea;border:1px solid #34353c;border-radius:4px;padding:1px 4px;font:inherit;text-align:right';
    lab.append(name, num); row.appendChild(lab);
    const s = document.createElement('input');
    s.type = 'range'; s.min = lo; s.max = hi; s.step = step; s.value = get(k);
    s.style.cssText = 'width:100%;margin:2px 0 0';
    const hint = document.createElement('div');
    hint.textContent = help; hint.style.cssText = 'color:#6c6c76;font-size:10px;margin-top:1px';
    row.append(s, hint); el.appendChild(row);
    const push = (v) => { set(k, +v); s.value = v; num.value = v; };
    s.oninput = () => push(s.value); num.oninput = () => push(num.value);
    rows.push(() => push(get(k)));
  };
  FIELDS.forEach((f) => mk(f, (k) => POP[k], (k, v) => { POP[k] = v; }));
  XF.forEach((f) => mk(f, (k) => xf[k], (k, v) => { xf[k] = v; }));

  const bar = document.createElement('div');
  bar.style.cssText = 'display:flex;gap:6px;margin-top:12px;flex-wrap:wrap';
  const btn = (txt, fn) => {
    const b = document.createElement('button'); b.textContent = txt;
    b.style.cssText = 'flex:1;min-width:76px;background:#24252b;color:#e8e8ea;border:1px solid #3a3b43;border-radius:5px;padding:5px;font:inherit;cursor:pointer';
    b.onclick = fn; bar.appendChild(b); return b;
  };
  const out = document.createElement('textarea');
  out.readOnly = true; out.rows = 9;
  out.style.cssText = 'width:100%;margin-top:8px;background:#0d0e11;color:#9fd39f;border:1px solid #34353c;border-radius:5px;font:inherit;padding:6px';

  const dump = () => JSON.stringify({ POP: { ...POP }, transform: { ...xf } }, null, 2);
  btn('replay', () => { world.t = 0; });
  btn('copy', () => {
    out.value = dump();
    navigator.clipboard?.writeText(out.value).catch(() => {});
    out.select();
  });
  btn('reset', () => { Object.assign(xf, { px: 0, py: 0, pz: 0, ry: 0, sc: 1 }); rows.forEach((f) => f()); });
  el.append(bar, out);
  document.body.appendChild(el);

  return {
    update(w) {
      const r = w.robot; if (!r) return;
      // OFFSETS, never assignments: this runs after robotpop each frame, and assigning
      // would wipe the entrance (it silently reset the robot's depth to 0 that way).
      r.position.x += xf.px;
      r.position.y += xf.py;
      r.position.z += xf.pz;
      r.rotation.y = xf.ry;
      r.scale.setScalar(xf.sc);
    },
  };
}
