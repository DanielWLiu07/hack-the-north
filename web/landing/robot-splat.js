// robot-splat.js — the SCANNED Bracket Bot (splat.js) given the same entrance as the primitives
// robot (robot.js): GITRL drops, then it pops up from the bottom of the frame and catches its
// balance.
//
// A splat has no skeleton and must never be deformed, so it moves as ONE rigid piece: the
// whole Object3D rises, darts, and leans ABOUT ITS AXLE (a pivot at wheel-centre height), and
// the character comes from the motion curve alone, which is the same simulated cart-and-
// pendulum catch the jointed robot uses (robot.js makePopUp -> poseRigid). The mast whip is
// folded into the lean as a rigid shiver; the head beats become small turns on the spot.
//
// It is drawn RAW, in world.controlsScene (the overlay scene.js renders after the ink pass):
// a splat has no surface normals, so the near-binary ink pass has nothing to shade and turns it
// into speckle. Photoreal against ink is the point.

import * as THREE from 'three';
import { loadRobotSplat } from './splat.js';
import { BOT, stageEntrance, poseRigid } from './robot.js';

/** @returns {{ root, pivot, scale, splat }}: root on the floor with +Z forward, pivot at the axle */
export async function buildSplatBody(opts = {}) {
  const height = opts.height ?? 2.7, scale = height / BOT.height;
  const splat = await loadRobotSplat({ height, pixelRatio: opts.pixelRatio ?? 1, standIn: opts.standIn });
  const root = new THREE.Group(); root.name = 'splatRobot';
  const pivot = new THREE.Group(); pivot.name = 'splatRobot.axle'; pivot.position.y = BOT.wheelR * scale;
  splat.position.y = -BOT.wheelR * scale;
  pivot.add(splat); root.add(pivot);
  return { root, pivot, scale, splat, state: splat.userData.splat };
}

/**
 * scene.js module: ['./robot-splat.js', 'buildSplatRobot'].
 * opts.layer: 'over'  = world.controlsScene, the overlay drawn AFTER the ink pass: nothing covers the robot (default)
 *             'under' = world.rawScene, the raw pass scene.js draws UNDER the inked composite: GITRL, ENTER and
 *                       every arm stay in front of it (what robotpop.js uses)
 * opts.pop:   any of robot.js POP, e.g. { x: 0, z: -1.4, yaw: 0.6 } to stand it centre stage behind the title
 */
export async function buildSplatRobot(world, opts = {}) {
  const renderer = opts.renderer || world.renderer || (globalThis.gitrl && globalThis.gitrl.renderer);
  const body = await buildSplatBody({ height: opts.height, pixelRatio: renderer ? renderer.getPixelRatio() : 1 });
  const host = opts.layer === 'under' ? (world.rawScene = world.rawScene || new THREE.Scene()) : (world.controlsScene || world.scene);
  host.add(body.root);
  return stageEntrance(world, {
    root: body.root, scale: body.scale, body,
    keepWarm: true,          // parked below the frame but DRAWN: its shader and sort worker are ready when it pops
    pose: (st, cfg) => poseRigid(body, st, cfg),
    heroAt: (v) => { v.copy(body.root.position); v.y += 0.8 * BOT.height * body.scale; return v; },
  }, opts);
}
