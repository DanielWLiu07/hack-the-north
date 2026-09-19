// layout.js — where everything in the GITRL factory stands, and the one shared
// `world` object the modules talk through. Every module reads positions from here;
// nobody hard-codes another module's coordinates.
//
// Camera (owned by snakeArms.js, not changed): (0, 2.3, 8.4) looking at (0, 2.1, 0),
// vertical fov 40. At z = 0 the frame spans y -0.96..5.16 and x +-5.44 at 16:9; the
// visible half-height at distance d is d * 0.364. The pomme spy-cam arm hangs from
// (5.9, 7.0, -1.8) at the top right and its claw twin from (-5.9, 7.0, -1.8).

import * as THREE from 'three';

const V = (x, y, z) => new THREE.Vector3(x, y, z);

export const FLOOR_Y = -1.2;
export const CEILING_Y = 9.0;
export const BACK_WALL_Z = -10;

// GITRL hangs centred, on cables from the ceiling, in front of the factory line
export const TITLE = { text: 'GITIRL', center: V(0, 2.2, -1.2), width: 6.2, cableTopY: 8.4 };

// the line: crates ride left -> right along x
export const BELT = { z: -2.2, topY: -0.45, width: 0.95, speed: 0.55, x0: -12, x1: 12 };
export const CRATE = 0.46;                     // cube edge

// floor robots either side of the title; A picks off the belt and THROWS to B
export const ARM_A = V(-4.3, FLOOR_Y, -3.35);
export const ARM_B = V(4.3, FLOOR_Y, -3.35);

// overhead crane rail behind the title (visible across the top of the frame)
export const GANTRY = { y: 5.75, z: -3.1, x0: -15, x1: 15 };
// a second rail ABOVE the frame, in front of the title (never visible)
export const FRONT_RAIL = { y: 6.0, z: 0.35 };

// tentacle roots
export const WELDER_ROOT = V(-2.2, FRONT_RAIL.y, FRONT_RAIL.z);   // carriage slides in x
export const INSPECTOR_ROOT = V(0.9, GANTRY.y - 0.2, GANTRY.z);   // peeks over the title
export const POKER_ROOT = V(1.9, FLOOR_Y - 0.5, 0.9);             // floor hatch, off-frame
export const BG_ROOTS = [V(-6.6, CEILING_Y, -6.6), V(-2.8, CEILING_Y, -7.2),
                         V(3.4, CEILING_Y, -7.0), V(7.2, CEILING_Y, -6.4)];

// hanging lamps (shade height, and the SpotLight inside each points straight down)
export const LAMPS = [V(-3.6, 5.2, -4.8), V(0.3, 5.4, -5.6), V(3.9, 5.2, -4.6)];

// ---- the intro: one clock for every machine ---------------------------------------
//   0.0-0.9  POWER ON   hall lamps flick on one by one, the beacon starts turning
//   0.5-2.9  SINE WAVE  every machine enters from its nearest screen edge (floor arms
//                       and the poker rise from below, the welder and inspector drop
//                       from above, background tentacles swing in from the sides, the
//                       pomme arms unfurl from the top corners). Entry order ripples
//                       left -> right across the screen, and each machine SNAKES in
//                       on a sine that dies away as it arrives.
//   2.9-3.3  ANTICIPATE everyone pulls back a touch...
//   3.3-4.1  CLOSE IN   ...then reaches in toward the title from all sides and holds,
//                       framing a ring around it (presentPoint), while GITRL drops in
//                       letter by letter (TITLE_DROP, stagger TITLE_STAGGER)
//   4.1      TA-DA      the last letter lands: every machine flourishes (introTada)
//   4.8      WORK       routines begin (introWorking); the first crate is thrown after
export const INTRO = {
  powerOn: [0.0, 0.9],
  waveStart: 0.0, waveSweep: 0.65, enterDur: 0.75,
  anticipate: 1.25, closeIn: 1.5, release: 2.25,
  titleDrop: 1.65, titleStagger: 0.066,
  tada: 2.25, work: 2.8,
};

const _c01 = (x) => Math.min(1, Math.max(0, x));
const _smoother = (u) => u * u * u * (u * (u * 6 - 15) + 10);

// Entrance of a machine whose RESTING position is at screen x = ndcX (-1 left .. 1
// right). e: 0 -> 1 arrival (smootherstep, zero velocity both ends). snake: signed
// lateral offset factor in [-1, 1] — a 1.5-cycle sine that decays to exactly 0 on
// arrival, starting at 0 (note 51). Multiply it by your own amplitude, applied
// perpendicular to the direction you enter along.
export function introEnter(t, ndcX) {
  const delay = INTRO.waveStart + INTRO.waveSweep * (0.5 + 0.5 * Math.max(-1, Math.min(1, ndcX)));
  const u = _c01((t - delay) / INTRO.enterDur);
  const snake = Math.sin(u * Math.PI * 3) * (1 - u) * (1 - u) * (u > 0 ? 1 : 0);
  return { e: _smoother(u), snake, delay };
}

// Close-in weight: 0 until anticipation, dips to -0.3 (pull back), rises to 1 and
// holds while the title drops, releases to 0 at INTRO.release. Drive it through your
// own springs so the release overshoots.
export function introConverge(t) {
  if (t < INTRO.anticipate || t > INTRO.release + 0.35) return 0;
  const back = -0.3 * Math.sin(Math.PI * _c01((t - INTRO.anticipate) / (INTRO.closeIn - INTRO.anticipate)));
  const inW = _smoother(_c01((t - INTRO.closeIn) / 0.45));
  const outW = _smoother(_c01((t - INTRO.release) / 0.35));
  return back + inW * (1 - outW);
}

// 0 -> 1 -> 0 over ~0.7 s starting at the ta-da: a flourish, not a pose
export function introTada(t) {
  const u = _c01((t - INTRO.tada) / 0.7);
  return Math.sin(Math.PI * u) ** 2;
}

export const introWorking = (t) => t >= INTRO.work;

// Where a machine at world position `from` presents the title during the close-in:
// the nearest point on an elliptical ring around it, just in front of the letters.
export function presentPoint(from, out = new THREE.Vector3()) {
  const c = TITLE.center;
  const a = Math.atan2((from.y - c.y) / 2.4, (from.x - c.x) / 4.1);
  return out.set(c.x + Math.cos(a) * 4.1, c.y + Math.sin(a) * 2.4, c.z + 0.6);
}

// ---- THE GAZE: everything in this factory is looking at YOU -----------------------
// The project is a camera on an arm watching a room; the landing page turns that
// around. Every machine with an eye calls this to find where to look.
//
// A single target plane cannot serve every depth (a plane near the viewer barely
// deflects a background head; a plane at z = 0 makes foreground heads look AWAY from
// the viewer), so each head looks at the pointer ray `ahead` units in front of
// ITSELF: always out of the screen toward the viewer, with a big, readable swing as
// the cursor crosses the page. When the pointer has left the window it returns the
// camera position: they stare straight at the viewer.
export function gazePoint(world, headPos, out = new THREE.Vector3(), ahead = 3) {
  const cam = world.camera.position;
  if (!world.cursor.present) return out.copy(cam);
  const { origin: o, direction: d } = world.cursor.ray;
  const zp = Math.min(headPos.z + ahead, cam.z - 0.6);
  const k = (zp - o.z) / (Math.abs(d.z) < 1e-6 ? -1e-6 : d.z);
  return out.copy(d).multiplyScalar(k).add(o);
}

// THE ENTER BUTTON (enter.js): a physical push-button under the title, the way into the
// dashboard. Everything else keeps out of ENTER_CLEAR, as it does out of TITLE_CLEAR.
export const ENTER = { center: V(0, -0.35, -1.7), width: 2.4, href: '/robot' };   // just below GITRL
export const ENTER_CLEAR = { x: 1.75, y0: -1.0, y1: 0.35 };           // at ENTER.center.z

// WATCHERS (watchers.js) crowd the frame from all four edges but keep this box clear
// at rest so GITRL stays readable (they may lean over its edges when curious)
export const TITLE_CLEAR = { x: 3.95, y0: 0.35, y1: 4.25 };       // generous: the centre stays calm

// ---- the shared world ----------------------------------------------------------
// Created once in scene.js and handed to every module's update(world). Modules
// PUBLISH their APIs onto it at build time and READ each other through it at run
// time, so no module imports another.
//
//   world.t, world.dt                 seconds since load, clamped frame step
//   world.scene, world.camera
//   world.cursor.point  Vector3       pointer on the z = 0 plane (idle sweep before
//                                     the first move — see cursor.seen)
//   world.cursor.ray    THREE.Ray     camera -> pointer (idle sweep before first move);
//                                     use gazePoint() rather than reading it directly
//   world.cursor.present bool         false while the pointer is outside the window
//   world.cursor.leftAt / returnedAt  world.t of the last leave / re-enter
//   world.cursor.ndc    Vector2       pointer in NDC
//   world.cursor.seen   bool          has the pointer ever moved over the page
//   world.cursor.speed  number        world units / s, smoothed
//   world.cursor.movedAt number       world.t of the last pointer move
//   world.click         { t, point: Vector3, letter }   latest click (letter = index
//                                     hit on the title, or -1); t = -Infinity if none
//   world.sparks        { emit(pos, dir, n, speed, spread) }   (mech.createSparks)
//   world.hall          published by hall.js     — { mounts: [{ pos, normal }] } places
//                                     a bracket-mounted CCTV camera can bolt on (columns,
//                                     wall, gantry underside); watchers.js populates them
//   world.hero          published by scene.js    — [{ pos, radius }] the pomme camera head and
//                                     claw hand, live: everything else keeps clear of them
//   world.heads         published by watchers.js — [{ pos: Vector3 (world, live), radius }]
//                                     every watching head, so others can avoid / glance at them
//   world.title         published by title.js    — see TITLE API below
//   world.crates        published by arms.js     — see CRATES API below
//   world.afterRender   [fn(canvas)] called after each frame is drawn (Sentry's replay
//                                     snapshots the WebGL canvas from here)
//   world.away          { on, since } set by scene.js: ENTER was pressed, everything leaves the
//                                     stage (on = true); it comes back when the hero is scrolled
//                                     into view again (on = false). Reversible by design.
//   world.on(name, fn) / world.emit(name, payload)   events between modules
//
// TITLE API (title.js):
//   count                          number of letters (5)
//   poke(i, strength, dirWorld)    knock letter i (swing + squash); strength ~0.2..1.5
//   center(i, out)                 world centre of letter i -> out
//   seam(i, u, out)                world point on letter i's FRONT face along its
//                                  outline, u in [0, 1) walks the whole outline
//   normal(i, out)                 letter i's front-face normal (world)
//   pick(raycaster)                letter index hit, or -1
//
// CRATES API (arms.js):
//   list                           [{ object3D, state }] state: 'belt' | 'held' |
//                                  'flying' | 'gone'
//   flying()                       the crate in the air, or null
//
// EVENTS: 'throw' { crate, from, to, tCatch } · 'catch' { crate } · 'poke' { letter }
//         'enter-hover' { on } · 'enter-press' {}
//         'weld' { letter, point } · 'click' { point, letter }
export function createWorld(scene, camera) {
  const handlers = new Map();
  return {
    t: 0, dt: 1 / 60, scene, camera,
    cursor: { point: V(0, 2.1, 0), seen: false, speed: 0, movedAt: -Infinity,
              ray: new THREE.Ray(camera.position.clone(), V(0, 0, -1)),
              ndc: new THREE.Vector2(), present: true, leftAt: -Infinity, returnedAt: -Infinity },
    click: { t: -Infinity, point: V(0, 2.1, 0), letter: -1 },
    sparks: null, title: null, crates: null, hall: null, heads: null, afterRender: [],
    away: { on: false, since: -Infinity },
    on(name, fn) {
      if (!handlers.has(name)) handlers.set(name, []);
      handlers.get(name).push(fn);
    },
    emit(name, payload) {
      for (const fn of handlers.get(name) || []) fn(payload);
    },
  };
}
