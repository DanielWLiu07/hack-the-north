// robotpop.js — the Bracket Bot rises from below the frame, after the title lands.
//
// The entrance is NOT a walk-in from the side. The title drops, then the robot pushes up
// from the bottom of the screen, overshoots, and catches its own balance.
//
// WHY THAT MOTION: a Bracket Bot is a self-balancing two-wheeled robot, so overshooting
// and then oscillating back is what the real machine does, not an animation flourish.
// That is the whole personality budget and it costs nothing.
//
// A SPLAT HAS NO SKELETON. It can only be moved RIGIDLY — every bit of character here is
// in the motion curve of one Object3D. Never deform it.
import * as THREE from 'three';
import { loadRobotSplat } from './splat.js';

export const POP = {
  at: 2.6,          // s — after the title has dropped
  rise: 0.85,       // s — time to clear the floor line
  from: -2.6,       // y it starts at, below frame
  to: 0,            // y it settles at
  over: 0.18,       // overshoot height, in metres
  lean: 0.30,       // rad — first counter-lean as it catches balance
  hz: 1.9,          // oscillation rate of the balance correction
  decay: 1.5,       // how fast the wobble dies
  height: 2.4,      // fitted height on the landing stage
  z: -1.4,          // pushed BACK so the title and ENTER read in front of it
};

const ease = (u) => 1 - Math.pow(1 - u, 3);     // out-cubic: fast off the floor, soft at the top

export async function buildRobotPop(world) {
  let robot = null;
  try {
    robot = await loadRobotSplat({ height: POP.height, pixelRatio: 1 });
  } catch (e) {
    console.warn('[gitrl] robot splat not loading:', e);
    return { update() {} };
  }
  robot.position.set(0, POP.from, POP.z);
  world.scene.add(robot);
  world.robot = robot;

  return {
    update(w) {
      const t = w.t - POP.at;
      if (t < 0) { robot.position.y = POP.from; robot.visible = false; return; }
      robot.visible = true;

      // rise with an overshoot, then settle
      const u = Math.min(1, t / POP.rise);
      const base = POP.from + (POP.to - POP.from) * ease(u);
      const wobbleT = Math.max(0, t - POP.rise);
      const damp = Math.exp(-POP.decay * wobbleT);
      const osc = Math.sin(2 * Math.PI * POP.hz * wobbleT);
      robot.position.y = base + (u >= 1 ? POP.over * damp * osc : 0);
      robot.position.z = POP.z;

      // it is balancing: the mast leans AGAINST the motion, then corrects, then stills
      robot.rotation.z = (u < 1 ? -POP.lean * (1 - u) : -POP.lean * damp * osc * 0.8);

      // once settled, look up at the title
      const settled = Math.max(0, wobbleT - 1.2);
      robot.rotation.x = -0.10 * Math.min(1, settled / 0.6);
    },
  };
}
