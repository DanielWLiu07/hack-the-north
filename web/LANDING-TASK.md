# Landing page — the GITRL factory

`web/landing/` is served by `server.py` at `/` (same origin as `/api/*`), or standalone with
`cd web/landing && python3 serve.py` → :8124.

These rules replace the earlier version of this file (two-layer DOM + 3D, manga pass
hero-only, dashboard below the fold). Those rules no longer apply.

---

## The rules

**1. Everything is 3D.** No HTML overlays on the landing page: no DOM headline, no terminal
block, no hint text, no CSS lamp pool or grain. The page is one canvas.

**2. The title is `GITRL`, centered, set in Katie Roze** — built as 3D geometry in the scene
from the font's own vector outlines, rendered through the same pass as everything else.

**3. (superseded 2026-09-18 evening) The pomme CAMERA arm is retired** — its Meshy head did not
fit the new family of camera heads; a hero `pod` watcher hangs in its place (top right).
`snakeArms.js` still provides the scene, camera, lights and the claw, and is still not edited.
Originally: **The existing arm is perfect — do not change it.** The pomme spy-cam arm
(`snakeArms.js`) and its render (`styles.js` `MangaPass({ bw: 1, grit: 1 })`) stay exactly
as they are. Everything new matches them: same part vocabulary (knuckle pins, clevis forks,
alternating box/cylinder links, pistons, cable runs, bolts, hoses), same materials, same
manga pass.

**4. A factory of arms.** Generate many more rigged mechanical arms and tentacles —
fully rigged (nested joint Groups, a real kinematic chain) and **working**: each one is
doing a job, not just swaying.

**5. The background is gritty and dark** — a factory interior, in 3D, through the pass.

**The idea: everything is looking at YOU.** GITRL is a camera on an arm watching a room;
the landing page turns that around — the visitor is what is being watched. The frame is
FILLED with camera-headed arms, tentacles and CCTV units reaching in from all four edges,
every lens facing the viewer and tracking the cursor (`landing/watchers.js`,
`landing/heads.js`, `gazePoint()` in `landing/layout.js`). GITRL's box stays readable and
the pomme camera arm keeps its space: the crowd gives way to both.

**6. Playful, full of personality.** Every machine is a character: anticipation before a
move, overshoot and settle after it (springs, not tweens), reactions to the cursor, to
clicks and to each other. Nothing just sways.

**7. A fun intro.** The machines come up in a sine wave — entries ripple left to right,
each one snaking in from its nearest screen edge — then close in on the title from all
sides while GITRL drops in letter by letter, then a ta-da, then work. One shared clock:
`INTRO` in `landing/layout.js`.

**8. The page.** Per `PAGES.md`: `/` is the 3D hero (one full-viewport canvas, nothing over
it) and then the dashboard as ordinary DOM below, on one scroll. The 3D **ENTER** button
(`enter.js`) scrolls to `#dashboard`. The render loop STOPS when the hero scrolls out of view
and when the tab is hidden. A loading gate holds the intro until every module, texture and
shader is ready (a gear fills tooth by tooth), then the intro starts from t = 0.

**9. Calm, not twitchy.** Every change of gaze is eased; blinks are a movement (lids ease);
losing window focus is NOT "the visitor left"; startle needs a real whip of the mouse; only the
six nearest watchers lean in; roots are always off-screen; GITRL's and ENTER's boxes stay clear.

**10. Follow "the visualizations"** — open: confirm which visualizations are meant
(`docs/diagrams.html` is the current guess) before building anything off them.

---

## Work, in order

1. **Mount it** — done: `server.py` serves `landing/` at `/`, `/api/*` unchanged.
2. **Strip the HTML** — canvas only.
3. **GITRL title** — 3D, Katie Roze outlines, centered.
4. **Arms and tentacles** — rigged, working, same look as the existing arm.
5. **Factory background** — gritty, dark, 3D.
6. **Intro + personality pass** — the whole cast on the `INTRO` clock.
7. **The visualizations** — once confirmed.

## Files (all in `landing/`)

| file | what |
|---|---|
| `snakeArms.js`, `styles.js` | pomme's arm rig and manga pass, verbatim — never edited |
| `scene.js` | renderer, loop, cursor/click, the pomme arms' part in the intro |
| `layout.js` | where everything stands, the shared `world`, the `INTRO` clock |
| `mech.js` | shared materials (same values as `snakeArms.js`), `Rigid` merging, springs, sparks |
| `hall.js` | the factory interior, lamps, conveyor |
| `title.js` + `title/` | GITRL, traced from Katie Roze by `tools/build_gitrl.py` |
| `watchers.js` | THE CROWD: 24 camera-headed arms/tentacles + CCTV on the hall's mounts, all watching you |
| `heads.js` | nine camera head kinds with irises and eyelids |
| `vendor/three/` | three.js r170, local — the page makes no external request |
| `tentacles.js` | welder, poker, inspector, background tentacles |
| `dressing.js` | gears, girders, beams filling the corners and bottom strip |
| `enter.js` | the 3D ENTER button (+ a hidden real link for keyboards) |
| `dev-*.html` | one harness per module |

## Still true (facts about the demo machine, not style rules)

- The manga pass is a full-screen post pass every frame, on the laptop that also runs
  perception and Rerun during the demo. Cost scales with pixels, not node count; if it is
  slow, render smaller and upsample — the gritty pass hides it.
- Pause the loop on `visibilitychange`.
- Measured 2026-09-18 late: 60 fps at 2560x1440, six modules, 815 draw calls, 368k tris, after
  cutting the hall's four SpotLights (every lit fragment paid for them), canvas MSAA (it only
  multisampled the post quad) and pixel ratio > 1. A governor drops render scale to 0.8 / 0.62 if
  frames run long for over a second. Dev switches: `?auto` (scripted visitor), `?only=hall,watchers` (subset).
- More arms means more draw calls: merge each joint's rigid parts per material.
- `armseg.glb` reads as "black beads on a string" at segment scale — the procedural links
  read right.
