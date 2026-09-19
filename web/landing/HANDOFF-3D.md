# Handoff — the GITRL 3D hero (`web/landing/`)

You (gpt-6-astra, in Codex, right-hand tmux pane) now own ALL the 3D work on this page. It is handed
over by the Claude `web` session in the pane to your LEFT (tmux `htn:4.1`), which from now on does only
the API / dashboard / capture / graph pages and will NOT edit your files, so there are never two
writers on one file.

**You own:** `scene.js layout.js mech.js watchers.js heads.js title.js enter.js`, the parked
`hall.js tentacles.js dressing.js`, `title/`, `tools/`, and the `dev-*.html` harnesses.
**Not yours — do not edit:** `index.html`, `dash.js dash.css graph.js graph.css sentry.js vendor/`, and
everything outside `web/landing/` (`server.py`, the other tracks' folders, `room.git`, `.env`).
Read `../LANDING-TASK.md` (the user's rules for this page) and `../PAGES.md` (what `/` is: this 3D
hero, then an ordinary-DOM dashboard below it on one scroll).

The page is already being served: `http://127.0.0.1:8000/` by `web/server.py`. Do NOT stop or restart
that server. It serves files with `Cache-Control: no-cache`, so a browser reload always gets your edit.
The user watches the page live on their right monitor and reloads it themselves (Cmd-R).

## YOUR TASK NOW — the user's words
> "give it all the context and make it work on the 3d stuff for the landing page by making the
> models better fit, more optimization, make the bg a bit more plausible and fit"

Read that as three jobs, in this order. If one is ambiguous once you have looked at the page, ASK the
user in your pane rather than guessing — they are there and they answer fast.
1. **Models that fit better.** The arms, tentacle vertebrae and the nine camera heads were built
   quickly and procedurally. Make them a coherent family: heads in proportion to the necks that carry
   them (some heads dwarf their arm, some necks are over-thick because long reaches get thicker necks),
   a proper mount between neck and head, consistent detail density, silhouettes that read at both the
   huge foreground size and the tiny background size. They must also fit the COMPOSITION: nothing may
   cover GITRL or ENTER (`TITLE_CLEAR`, `ENTER_CLEAR`), roots always off-screen, no two heads fused.
2. **More optimisation.** Measured state is below. The user has complained about lag before. The known
   costs: the full-screen MangaPass; ~9 draw calls per head x ~21 heads (static parts merge per
   MATERIAL today — one vertex-coloured mesh per head, or instancing pupils / lids across heads, would
   cut most of it); a per-frame curve solve per arm (bisection over a 25-point polyline, then 49
   samples) even for hidden or barely-moving arms; `crowd.updateMatrixWorld` over ~250 joints.
   Measure before and after with `tools/dev/perf.mjs`; do not trade away the look.
3. **A background that is plausible and fits.** Right now the background is flat `#060608`: the user
   had the factory hall, the gears-and-girders dressing and the working tentacles removed "for now"
   because they did not sit right. What they disliked, in their words: "the backstage lights don't make
   sense", "centre wants to be more clear", "remove the beam stuff". So: a backdrop that makes it
   plausible that these arms hang from SOMETHING and live SOMEWHERE (structure for roots to bolt to,
   depth, a floor line) while staying quiet, dark and behind everything, with the centre calm. `hall.js`
   and `dressing.js` are parked in `scene.js` MODULES (one line each) — reuse what is good in them, or
   replace them. No SpotLights (see PERF lesson), no lamp starbursts.

## What the user asked for, in their order (latest wins)
1. Everything in the hero is 3D; title **GITRL**, centred, Katie Roze (a colour font: its outlines are
   empty, the letters are traced from its embedded art by `tools/build_gitrl.py`).
2. Lots of rigged arms / tentacles with CAMERA heads — "the whole idea is the camera thing".
3. Playful, full of personality; a fun intro.
4. Calm, not twitchy: no snapping gazes, no random looking about, animated blinks, no letter
   collisions, no arms "hanging in the air" (roots ALWAYS off-screen), sensible density, no lag.
5. LATEST: **remove the claw** (done — `snakeArms.js` is no longer loaded at all), **ENTER is just the
   word, directly below GITRL** (done), **clicking ENTER makes everything animate away** (done, then
   the page scrolls to `#dashboard`), **remove the beams and the background for now** (done — hall,
   dressing and tentacles are PARKED in `scene.js` MODULES, one line each to bring back), and the page
   "should literally right now be the animation for everything **swinging in and looking at GITRL**".

## State right now (verified headless, 1440x810, `?auto`)
Modules loaded: `title.js`, `watchers.js`, `enter.js`. 0 console errors from the 3D code.
BASELINE (2026-09-18 21:40, headless Chrome, 2560x1440, arms on stage, `tools/dev/perf.mjs`):
**60.2 fps, 182 draw calls, 193k triangles, 216 geometries, 9 textures.** With the hall, dressing and
tentacles it was 815 calls and still 60 fps headless — but the user FELT lag on the real display,
alongside other GPU work, so do not read 60 fps headless as "fast enough"; drive the calls and the
per-frame JS down, and check the frame-time governor in `scene.js` never trips.
- 0.5–3 s: each watcher swings in about its off-screen root like a gate (`SWING_FROM`, per-watcher
  `Spring`), cued left→right by `introEnter(t, ndcX).delay`; a wave runs down the body while it moves.
- 3.35–4.1 s: GITRL drops letter by letter into the space they are all watching; ta-da hop at 4.1.
- ENTER drops in behind the title at ~4.3 s. Hover → underline draws, every head looks at it
  (`world.enter.hovered`). Press → `'enter-press'` → `scene.js` sets `world.away = { on, since }`:
  letters are yanked up their cables, watchers swing back out, ENTER goes up; 1.25 s later the page
  scrolls. Scrolling back to the hero sets `away.on = false` and everything returns.
- `watchers.js` `FOLLOW_CURSOR = false` → heads look at GITRL (each at its own letter). `true`
  restores the earlier "everything watches the visitor" behaviour (delayed gaze, lean-in, startle).

## Not verified / known weak spots — start here
- The RETURN (scroll back up → swing back in) is only reasoned about, never captured. Test it.
- ENTER reads thin and small at 1440 px; check phone size. Consider heavier bevel / bigger width
  (`ENTER` and `ENTER_CLEAR` in `layout.js`; watchers keep out of that box and of `TITLE_CLEAR`).
- The two big `pod` / `stereo` heads flanking the title turn side-on to look at it: decide whether
  profile reads well or whether big ones should stay three-quarter toward the viewer.
- No background at all now (flat `#060608`). The user said "for now".
- Swing overshoot (`Spring(phi0, 13..8, 0.56)`) can carry a head a little into `TITLE_CLEAR` for a beat.
- `heads.js` paparazzi `flash()` is only fired on click; nothing else uses it now.

## How it is built (read the header comment of each file first)
- `layout.js`: every position, the shared `world` object (its comment block is the contract between
  modules), the `INTRO` clock, `gazePoint()`, `TITLE_CLEAR`, `ENTER`.
- `watchers.js`: chains are REAL nested joints posed from a space curve (Hermite + slack bulge solved
  to the chain's length, parallel transport) but DRAWN by six InstancedMeshes with vertex colours.
  Cast list is authored in screen space (`CAST`), re-laid-out on aspect change.
- `heads.js`: nine camera kinds behind one interface; lids and pupils EASE toward targets.
- `mech.js`: the material values (= pomme's), `Rigid` merging, `Spring`/`Spring3`, sparks, `hash()`.
  Never `Math.random` — motion must reproduce.
- The manga pass at grit 1 is nearly BINARY (linear luminance > ~0.18 prints white): design in big
  light shapes vs dark. `renderer` runs at pixel ratio 1, no MSAA, with a frame-time governor.
- PERF lesson: SpotLights cost every lit fragment on the page. Do not add lights casually.

## Tools (`tools/dev/`, puppeteer comes from `~/Dev/projects/2026/blender-to-threejs`)
- `node tools/dev/shots.mjs "<url>" <outprefix> t1,t2,... [w h]` — screenshots on the SCENE clock.
- `node tools/dev/audit.mjs "<url>" <seconds> mouse` — per-chain joint step / head speed / NaN audit.
- `node tools/dev/perf.mjs "<url>" <out.png> <waitMs> [w h]` — fps, draw calls, triangles.
- `?auto` = scripted visitor (wander → hover ENTER → press). `?only=title,watchers` = subset.
- Judge motion by sampling it, never by stills; judge frames by LOOKING at them.
- Another project's headless Chrome (port 9222) eats ~5 cores on this machine; captures are slow.
  Do not kill Chrome processes you did not launch.

## Verifying from Codex
The tools launch the system Chrome headless through puppeteer and talk to `127.0.0.1:8000`. If your
sandbox blocks launching Chrome or reaching localhost, ask the user to approve it (they expect to);
do not work blind — every earlier bug on this page was found by LOOKING at captured frames or by
sampling motion numerically, never by reading the code.

## Rules of the road
- Save in small WORKING increments: the user reloads the live page at any moment, and a module that
  throws is dropped by `scene.js` (look for `[gitrl] ... stopped` in the console).
- The user's standing rules: never create a git commit unless they explicitly ask; when they do, no
  AI attribution of any kind (no Co-Authored-By, no "generated with"). `web/` is not a git repo today.
- Append a block to the repo-root `PROGRESS.md` (`cat >>`, append-only, its exact format) when a
  numbered step lands. Never add AI attribution to commits; never commit unless the user asks.
