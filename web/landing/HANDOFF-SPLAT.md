# Handoff — the Bracket Bot as a Gaussian splat (`web/landing/`)

> **Updated 2026-09-19 midday.** The scan is now a CANONICAL asset: upright, feet on y = 0, mast axis on
> x = z = 0, facing +Z, all baked into the file by the scan pipeline. `splat.js` only scales it and checks
> the facing; do not tune `orient`. It is never drawn through the ink pass. Its entrance and the
> primitives robot are in `HANDOFF-ROBOT.md`. `dev-splat.html` is a static raw viewer now (the demo
> roll-in and the ink toggle below are gone), and `:8000` serves `.ksplat`. The rest of this note is
> the history of how the renderer was chosen and verified.


The robot that enters from the left is a 3D Gaussian splat of the real Bracket Bot, trained with
`~/Dev/projects/2026/minecraft/scan-pipeline/splat/` (COLMAP -> `robot_isolate.py` -> OpenSplat on
Metal, 15000 iterations). This note covers the web side only. Nothing here edits `scene.js`,
`index.html`, `server.py` or any existing module.

## What exists

| file | what it is |
|---|---|
| `vendor/gaussian-splats-3d/` | `@mkkellogg/gaussian-splats-3d` 0.4.7, the npm build byte for byte, plus LICENSE and VERSION. No CDN. |
| `splat.js` | `loadRobotSplat(opts)` -> `Promise<THREE.Group>`. Read its header comment first. |
| `models/robot_splat.json` | which file to load, and the orientation that stands it upright. `file: null` = stand-in. |
| `dev-splat.html` | harness on `http://127.0.0.1:8124/dev-splat.html` (`?view=stage ?manga ?walk ?standin ?still`). |
| `tools/install_splat.mjs` + `tools/pack.html` | trained `.ply` -> `models/robot_splat.ksplat`, and sets the manifest. |
| `tools/dev/splat.mjs` | captures + numbers for the harness. |
| `tools/dev/splat-in-scene.mjs` | injects the robot into the REAL landing page and rolls it in; fps / draw calls before and after. |

Why this renderer: it declares `three >= 0.160` and we vendor r170. Spark 2.x needs `three >= 0.180`.
Its `DropInViewer` is a `THREE.Group` that draws inside a normal `renderer.render(scene, camera)`,
so it lands in MangaPass's render target together with the arms and the title.

## The contract

```js
import { loadRobotSplat } from './splat.js';
const robot = await loadRobotSplat({ height: 2.4 });   // world units; the frame is ~6 tall at z = 0
scene.add(robot);
robot.position.set(x, floorY, z);  robot.rotation.y = yaw;   // that is all a choreography does
```

The group is normalised: origin = middle of the footprint on the floor, +Y up, faces +Z, `height`
tall. No per-frame call. `robot.userData.splat` has `{ source, count, size, refit(), dispose() }`.

As a `scene.js` module it would be one line in `MODULES` plus something like:

```js
export async function buildRobot(world) {
  const robot = await loadRobotSplat({ height: 2.4 });
  world.scene.add(robot);
  return { update(world) { /* write robot.position / rotation from world.t, INTRO, world.away */ } };
}
```

**There is no walk-in choreography in `web/landing/` today** (the only left entrance was the claw
arm in `snakeArms.js`, which is retired and not loaded). `dev-splat.html` has a DEMO roll-in
(`pose()`): quintic travel from off the left edge, a lean into the acceleration and back to brake
(it is a two-wheeled inverted pendulum, it does not walk), then a turn on the spot. It is eased
and deterministic, so it can be lifted, but where the robot stops among the arms is a composition
decision that belongs to whoever owns `watchers.js` / `layout.js`.

## Verified (headless Chrome, 1440x810, stand-in)

- Draws through MangaPass in the real page next to the arms and GITRL, depth-tested against them:
  60 fps before and after, +2 draw calls, 0 console messages (`splat-in-scene.mjs`).
- Draw order follows the OBJECT under a fixed camera: a robot turned by 1 / 2 / 3 rad matches a
  camera swung the other way to within 0.00 mean abs difference per lit pixel. This is what
  `dynamicScene: true` is for; the library otherwise only re-sorts when the camera moves. Control,
  same test with the flag off: 1.89 / 16.04 / 25.05. Do not turn it off to save the per-frame sort.
- `.ply` with 45 `f_rest` fields (OpenSplat's layout) and the packed `.ksplat` both load and fit
  identically; packing took a 5.6 MB test file to 0.4 MB.
- No request leaves 127.0.0.1.

## Not done / needs a decision

1. **`web/server.py` will not serve the splat.** `LandingFiles.SERVED` is an extension allowlist
   and has neither `.ksplat` nor `.ply`, so both 404 on :8000 (and wherever `deploy_web.sh` puts
   it). Add `".ksplat"` there and restart. `serve.py` on :8124 serves everything, which is why
   the harness works today.
2. **Orientation.** COLMAP's frame is arbitrary. `orient: [pi, 0, 0]` is what `dev-cloud.html`
   used; check the trained splat in the harness, fix it with the sliders, paste into the manifest.
3. **The look under the ink pass** can only be judged on the trained splat. The stand-in is a
   sparse SfM cloud of the whole capture, so it reads as a blob; that is expected.
4. The library appends three hidden `<div>`s (its spinner / progress / info panel) to `<body>`.
   They never show (`showLoadingUI` is off) but they are there.
5. Do not commit a raw trained `.ply` (tens of MB). Commit the `.ksplat`.
