# Handoff — the robot on the landing: two bodies, one entrance, one gesture

Two versions of the Bracket Bot exist, and they share their motion:

| | primitives (`robot.js`) | scan (`splat.js` + `robot-splat.js`) |
|---|---|---|
| what it is | 12 low-poly vertex-coloured meshes on nested joint Groups, painterly material from `styles2.js` | the real machine as a 3D Gaussian splat, canonical frame baked into the asset |
| can move | every joint: wheels, lean, mast whip, lift carriage, both arms, head pan / tilt / roll | only as ONE rigid piece |
| can point | yes (`pointAt`, `pointBeat`) | no: it has no arm to raise |
| drawn | raw, over the ink pass (`world.controlsScene`) | raw. Never through the ink pass: no surface normals, it turns to speckle |
| cost in the live page | +24 draw calls, 60 fps | +2 draw calls, 60 fps |

`robotpop.js` (wired in `scene.js` by the hero's owner) is a third, separate implementation of the
scan's entrance. Nothing here edits it, `scene.js`, `index.html` or any copy.

## The entrance: GITRL drops, THEN the robot pops up from the bottom and catches its balance

Order is enforced by the title's own state, not a guessed time: `stageEntrance()` starts 0.3 s
after every `world.title.letters[i].landed` is true and the ta-da has played.

The balance catch is simulated once at build time, not keyframed (`makePopUp`): a cart and an
inverted pendulum under a balance controller with placed poles, plus three lighter oscillators
riding on the base acceleration (mast in its socket, arms, carriage on its belt). The result is a
240 Hz table; `state(t)` only reads it, so the pose is a pure function of time and scrubbable
(`dev-robot.html?t=1.6`). `poseRig` spreads the state over the joints; `poseRigid` puts the same
state on one Object3D, leaning about the axle, with the whip folded into the lean.

Measured (`node tools/dev/robot.mjs <out>`), same for both bodies:

| moment | t (s) | lean |
|---|---|---|
| crouch, launch | 0.81 | -2.9 deg |
| apex, `hop` above standing height | 1.15 | |
| lands | 1.24 | +6.3 deg |
| counter-leans | 1.59 / 2.01 / 2.40 / 2.78 | -9.0 / +4.3 / -1.5 / +1.4 deg |
| still (under 0.6 deg) | 2.9 | |
| looks at GITRL, then at the viewer with its head cocked | 3.22, 4.57 | |

Base darts 25 cm forward and returns. Wheel slip on the ground 1e-16 m. No sample-to-sample jump.
`prefers-reduced-motion`: it simply rises. ENTER hovered: it looks at ENTER. `away`: a small hop,
then back out through the bottom. It lists itself in `world.hero`, so the crowd makes room.

To use one: a line in `scene.js` MODULES, `['./robot.js', 'buildRobot']` or
`['./robot-splat.js', 'buildSplatRobot']`. Options: `{ height, pop: { x, z, yaw, ... }, layer }`.
`layer: 'under'` needs a raw under-pass in `scene.js` (it existed briefly on Sep 19 and was removed);
the default overlay layer needs nothing.

## The gesture: it points at the thing you lost

`pointAt(rig, [x, y, z], weight)`: shoulder pan + lift solved in closed form, near arm, slightly
bent elbow, head follows the hand, a small counter-lean. Layers on any pose by `weight`.
`pointBeat(rig, t, target)`: notices (head first), turns on the spot with the wheels running
opposite ways, raises the arm, glances back at the viewer. `dev-robot.html?point=-1.3,-0.35,1.7&t=2`.
Where it lives on the page and what publishes the target is the landing owner's call (asked).

## The caretaker on the dashboard (`roommate.js`)

"Where are my keys?" -> [Point at it] -> the robot notices, drives over if it has to, turns and points.
It mounts its own small canvas into the dashboard's `#roommate-stage` (under the search box at
`/?info#search`) and listens to the dashboard's three window events. It edits no dashboard file; it
needs one script tag in `index.html`, which is the dashboard owner's to add.

| event | what the robot does |
|---|---|
| `gitrl:point` | pose is ROOM frame (X forward, Y left, Z up, metres) -> stage `(-y, z, -x)`, 1 unit = 1 m. Further than 0.85 m: turns, drives, stops 0.62 m short. Raises the near arm at the object's real height, glances back. |
| `gitrl:job` | caption repeats the job's own state; a terminal state lowers the arm; no terminal state -> lowers after 7 s |
| `gitrl:room-state` | clean: calm. dirty: a double-take. conflict: a look each way, then at the viewer |

Honesty: it is an illustration of the PLAN, not telemetry. While `executor` is `not_connected` the
caption says "planned", never "done"; before any room-state event it says nothing about the room; it
starts at the room origin because the page does not know the real pose (the map will draw that).
House rules kept: own canvas, no post pass, loop only while on screen and the tab is visible,
`prefers-reduced-motion` gets stills. The camera eases round to see each gesture from the side, never
more than 70 degrees from its home view, because the painted light is keyed to that view (`keyFor`).

Verified with `node tools/dev/roommate.mjs <out>` (dev page) and `... <out> live` (the real page on
:8000): mounts and un-hides, 0 console errors or warnings, no request leaves localhost, loop stops off
screen and resumes, reduced motion is a still with the arm up. `dev-roommate.html` fires the three
events with the dashboard's own example payloads.

## Honest notes

- In-page capture labels are "at least t": a screenshot takes ~0.25 s while the page keeps
  running. The frozen-time harness sheets are the exact ones.
- Painterly on a thin white robot at ~330 px: what reads is the cel split, the brushed shadow
  boundary on curved parts and the line. The stroke map's dabs are ~7 px at `strokes: 0.6`;
  finer than that mip-averages to nothing. Its pigment zones are pinned light on flat faces by
  the map's own mean tilt. `styles2.js` is pomme's, verbatim; the cel key is re-aimed per scene
  from `robot.js`, not edited in place.
- `textures/watercolor_normal.png` is 7 MB. `paintRobot(rig, { strokeMap })` can point at
  `textures/watercolor_normal-1024.webp` (0.2 MB) instead; the dashboard stage does. Not for the hero:
  `scene.js` owns the loading manager's one URL modifier there.
- The roll-in from the left is parked in `robot-rollin.js`. Nothing imports it.
