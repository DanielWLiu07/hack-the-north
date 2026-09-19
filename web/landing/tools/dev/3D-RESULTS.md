# Hero revision — 2026-09-18

All changes are in the owned landing 3D files and dev tools. No commit made.
The main server was neither stopped nor restarted. Root PROGRESS.md was not
edited because the ownership rules prohibit edits outside landing/.

## Models and composition

- Smaller foreground cameras; common socket, cheek plates and pivot pin on all nine kinds.
- Neck width follows head size; long reaches add vertebrae instead of thickening wrists.
- Three-quarter inward gaze keeps the lenses readable. Gentler entrance overshoot.
- Three overhead characters in portrait; desktop retains twenty total characters.
- Back side characters separated, ENTER bevel brighter and heavier.
- Captured and inspected entrance, settled view, hover and exit at 1440×810;
  portrait at 390×844; all nine kinds in dev-heads.html.

## Performance

Same perf.mjs command, URL `/`, wait 6500 ms, viewport 2560×1440:

| Metric | Fresh before | After, including backdrop |
| --- | ---: | ---: |
| FPS | 60.1 | 60.3 |
| Draw calls | 217 | 90 |
| Triangles | 234,394 | 224,294 |
| Geometries | 216 | 89 |
| Textures | 9 | 9 |

After: render scale 1, frame p95 19.7 ms, module update median 1.6 ms / p95 3.1 ms.
No module errors or governor messages. Final side-camera spacing reduces the
triangle count further to 221,022 in the settled screenshot.
The handoff's 182 calls / 193k triangles was a different observed state;
use the freshly measured full-cast baseline above for this comparison.
Headless vsync does not establish smoothness under the user's real display load.

- Rigid head surfaces merge to one draw, retaining per-vertex material properties.
- Pupils and shutters share three instanced draws across the cast.
- Eye animation runs once per scene-clock frame instead of on every setter.
- Hidden chains skip curve solving and are omitted from instance draws.
- Hermite basis and spatial wave terms are precomputed.
- Lower radial detail on small arm parts offsets added long-neck vertebrae.
- perf.mjs now also reports render scale, frame p95 and module-update timings.

## Motion and background

- Arc endpoint tangent cannot cancel against gaze during exit; reduced body wave.
- 13-second scripted audit: 825 sampled frames, zero flagged steps or NaNs.
- Pointer/click audit: 784 frames, zero flagged steps or NaNs; max joint step 10.3°.
- return.mjs verifies real ENTER click, paused dashboard, scroll back, all 20 heads returning.
- Visibility test passes after requiring intersectionRatio > 0.02.
- New buildBackdrop in hall.js creates a two-draw recessed cabinet enclosure and
  floor junction. No additional lights, beams or background cameras. Old buildHall stays parked.

Useful verification:

```sh
node tools/dev/perf.mjs http://127.0.0.1:8000/ /tmp/gitrl-perf.png 6500 2560 1440
node tools/dev/shots.mjs 'http://127.0.0.1:8000/?auto' /tmp/gitrl 3,7,10,11 1440 810
node tools/dev/audit.mjs 'http://127.0.0.1:8000/?auto' 13 mouse
node tools/dev/return.mjs
```

## Detail / clearance / lag revision

- Added machined lens grips, retaining screws, vented service covers, connector
  fittings, identification tabs, arm inspection covers and safety cuts.
- All nine camera kinds now appear among 22 characters. Two moved from the top
  to the lower corners; two small characters fill the lower edge.
- Shorter chains, wider hinge spacing, monotonic chord progress, and accurate
  kinematic head positions address self-intersections. Added clearance.mjs:
  260 samples across entrance/hold/exit, no non-neighbour capsule overlaps,
  no onscreen roots, zero difference between reported and rendered head position.
  Smallest tested clearance ratio: 1.143 (1 would touch). This is a conservative
  link-capsule test, not an exhaustive triangle or cross-arm collision proof.
- Warm-started arc-length fitting; instanced tally lamps; 75 calls for the larger,
  more detailed cast (previous revision 90). Return test: all 22 restored, pause passes.
- User still reported lag with 1080p cap. Replaced with a 720p pixel budget,
  RGBA8 scene target and 60 Hz rendering ceiling. Governor now accumulates
  intermittent missed-frame budget, and reductions multiply the pixel cap.
- Final 2560×1440 headless sample: 60 browser fps, 75 calls, 232,706 triangles,
  render scale 0.5 (1280×720 internal), update median 0.6 ms / p95 1.2 ms.
  No module errors or governor messages. Visual capture checked at final scale.
  Actual display responsiveness still requires user feedback; headless is not proof.
- Backdrop shell moved fully behind the cast so it cannot cut off portrait roots.

## Sharpness correction

User rejected the blur from the 720p cap. Restored native CSS-pixel rendering
and removed automatic resolution reduction; retained the 60 Hz ceiling,
RGBA8 target and geometry batching. Head scale reduced from 0.84 to 0.66
(21% smaller); lower radial/subdivision counts for lenses, lids, shells,
domes, torus rings and wrist balls. Mechanical details retained.

2560×1440: renderScale 1, 60.2 actual rendered fps, 75 calls,
213,226 triangles (previous 232,706), module update median 0.9 ms /
p95 1.9 ms. No errors. Inspected the full-resolution screenshot.

## Continuous intro / white background accents

Replaced the synchronized 120 ms letter-target switching with a continuous
handoff from entrance gaze to each camera's assigned letter. Small pendular
motion and body waves continue through the drop and ease into idle; anticipation
and the landing response are staggered per character. Crowd separation now
precedes the text-clearance guards.

Added outer-cabinet vent slats, painted corner returns, floor safety marks and
high clerestory slots to the existing merged backdrop: +696 triangles, no extra
draws or lights. Full CSS resolution retained.

Inspected captures at 2.5, 3.3, 3.9, 4.6 and 7 seconds. Motion audit starting at
2 seconds: 781 frames, no flagged steps or NaNs. Clearance audit: 261 samples,
no non-neighbour link overlaps or onscreen roots; minimum ratio 1.181.
audit.mjs now accepts an optional fourth argument for the audit start time.

## Title-entry hitch / GITIRL / suspension cables

Cold-start instrumentation in intro-perf.mjs reproduced a 40.3 ms reveal frame
(38.8 ms render) with shader links and texture/geometry uploads as the letters
first became visible. compile() alone had not uploaded the offscreen assets.
The loading gate now initializes textures, compiles asynchronously, renders all
drawables offscreen, and waits asynchronously for a GPU fence before starting
the scene clock. The same reveal test then recorded no GPU uploads and an
18.2 ms worst frame. This is a headless measurement, not a real-display guarantee.

Generated GITIRL in the existing Katie Roze font; kept the word's total width and
last-letter landing time unchanged. Camera letter targets support six letters;
the repeated I shares its texture. Browser title and canvas label also updated.
Title and ENTER cables are slightly thicker silver-white instead of dark steel.

Viewed captures at 3, 7, 10 and 11 seconds: title and ENTER clear, cables readable,
all modules loaded, no page errors. 2560x1440 perf.mjs: 60.2 rendered fps,
81 calls, 218,394 triangles, renderScale 1, update median 1 ms / p95 2 ms.
The sixth letter adds six draws; native CSS-pixel sharpness is retained.
Motion audit from t=2: 791 frames, zero flagged steps, NaNs or page errors;
largest joint step 10 degrees/frame.

## Faster entrance / Spying loader / upper-corner density

Moved the coordinated entrance and title beats 450 ms earlier: first entrance cue
is now 50 ms after readiness. Kept the GPU warm-up gate that removed the reveal
hitch. Replaced the loading progress wheel with two counter-rotating, spoked
gears and a canvas-rendered "Spying..." label (no HTML overlay or external asset).
loading.mjs holds metadata for inspection; two viewed captures confirm rotation,
and subsequent entrance/title captures report no page errors.

Redistributed two background side cameras into the bare upper corners, removing
middle-side crowding without increasing cast size or draw count. Viewed final
1440x810 composition: title and ENTER remain clear, 81 calls / 221,902 triangles.
Initial corner-layout clearance sample: 261 frames, no tested non-neighbour
self-overlaps or onscreen roots, minimum clearance ratio 1.186.
Full early-entrance motion sample: 491 frames, no errors or NaNs; one 25.4-degree
joint step flagged on foreground watcher12 at t=1.39 (earlier audits began at t=2).
This early-entry flag remains to be investigated; it is not a clean motion pass.

## Responsive pipeline / animated backdrop / separated I–T strokes

- Entrance sweep shortened to 0.65 s, title landing at 2.25 s (was 3.65 s),
  faster damped arm/ENTER springs, exit-to-scroll 0.75 s (was 1.25 s).
- Backdrop accents slide from outside the frame through an eased vertex-shader
  reveal; they also retract on ENTER. No additional draws or lights.
- Cooperative construction yields between heads and letters. GPU texture upload,
  shader compilation and geometry upload are split into batches while gears paint.
  Loader gears merged down to two geometry draws plus the text plane.
- startup.mjs baseline: 215 ms long task and a 200 ms loader frame gap. After
  batching: 55 ms post-initialization long task; initial page setup still recorded
  76 ms and a 133 ms early gap. Do not claim zero browser/driver stalls.
  Readiness measured 0.88 s before versus 1.26 s after: more frequent loading
  frames trade some startup duration for avoiding one large freeze.
- Lossless WebP hatch, halftone and glyph assets reduce the loaded texture bytes
  from 5,232,202 to 2,917,435 (44.24%). Original files retained. Paper PNG kept
  because its WebP was larger; the unused generated alternative was removed.
- Instanced title cables and merged eyelets: 81 -> 64 draws, 221,086 triangles.
  Curve resampling 49 -> 33 points. 2560x1440 perf.mjs: 60.1 rendered fps,
  full renderScale 1, update median 0.7 ms / p95 1.2 ms (was 0.8 / 1.8 ms).
- New reveal interval 1.5–3.1 s: no GPU uploads, worst frame 17.9 ms.
- Viewed entrance, backdrop, title and settled captures. ENTER return check:
  pauses offscreen, all 22 heads return, no page errors.
- Lowered both I letters and raised T slightly to separate overlapping crossbars;
  viewed final GITIRL at 4 and 7 seconds.
- Located existing Seer in ../pages/seer/seer.js, already mounted by telemetry.js.
  Body/eye/arms are procedural; approved generated hands are in its models/ folder.
  No telemetry or Seer files modified.
- Reduced entrance body-wave amplitude while preserving the faster root swing;
  local-frame sag smoothing avoids projection instability. Final early-motion
  audit: 328 frames, zero flagged steps or NaNs, maximum joint step 19.3 degrees.
  Rechecked the merged loader and final staggered title visually; no page errors.

## Seer / ENTER responsiveness / scroll boundaries

User explicitly expanded the task to the telemetry character. Updated
web/pages/seer/seer.js: monochrome paper/ink palette, waveform-study panels,
playful magnifier inspection and presentation cycle, real state overrides intact.
No Seer API calls or artificial verdicts. Generated rigid gloves remain opt-in;
all eight hands use articulated finger joints by default. Arm roots now use the
current body transform and true 3D endpoints; magnifier follows the actual palm,
not its lagging target. Mobile character framing reserves room for the heading.

State audit: 472 samples, zero invalid poses, maximum wrist attachment error
0.000061 screen units; pause/resume passes. Viewed desktop/mobile and all state
captures. 17–19 draws / about 28,768 triangles versus 100,210 before. Seer toggle
now really pauses/resumes its loop; hidden/offscreen loops stop.

ENTER: independent fast underline (no second smoothstep over the spring), stiffer
damped scale response, camera hover target published before watcher updates, faster
gaze response. Latest measured hover: first sample 3 ms, underline 90% at 28 ms,
scale response 90% at 61 ms. Exit overlaps dashboard scroll after 220 ms instead
of waiting 750 ms. Real click/scroll/return check still restores all 22 heads.

Disabled vertical elastic overscroll on the landing document and shared DOM pages;
ordinary scrolling remains enabled. No overflow:hidden lock or wheel interception.

## Seer original-style glove refinement

Corrected sphere palm radii so palms no longer swallow the articulated fingers.
Kept the reference-led magenta/violet palette, stippled shading, cream sunburst,
and pupil-less purple eye. Removed unused geometry from the abandoned ray design.
This is a procedural interpretation of the saved artwork, not an identical asset.

Viewed desktop idle/thinking and actual telemetry desktop/mobile captures at
`/tmp/seer-glove-fit-*` and `/tmp/seer-page-mobile.png`. A 471-frame state audit
reported zero invalid poses, maximum wrist gap 0.000061 and root gap 0.000032
screen units. Pause/resume passed; no page errors. 17-18 draw calls and about
49,889 triangles. The rig tool now fails on missing hands, non-finite attachment
measurements, excessive gaps, browser errors, or broken pause/resume behavior.
