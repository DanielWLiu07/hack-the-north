// robot-rollin.js — PARKED (2026-09-19). The roll-in from the left, replaced by the pop-up in
// robot.js: "the robot must NOT walk in from the left or the right any more". Kept because it
// works and is one import away (dev-robot.html?act=rollin still plays it): enters already
// rolling, brakes with a back-lean, turns on the spot with the wheels counter-rotating, finds
// GITRL with its head, then waves. Pure function of time; wheels roll exactly the distance
// travelled (slip 2e-16 world units over the whole move).

import { aimAngles } from './robot.js';

const c01 = (u) => Math.min(1, Math.max(0, u));
const quintic = (u) => u * u * u * (u * (u * 6 - 15) + 10);
const ease = (a, b, x) => { const u = c01((x - a) / (b - a)); return u * u * (3 - 2 * u); };   // pomme's `ease`

export const ROLL = {
  travel: 2.6,            // seconds from the edge of frame to its mark
  cruise: 0.34,           // that share of it at steady speed; the rest is braking
  turn: 0.95,             // then it turns on the spot to face the room
  from: -5.75, to: -3.85, // world x at z below: just off the left edge of a 16:9 stage -> its mark
  floorY: -0.62, z: 0.6,  // wheels just above the bottom edge of the landing frame at this depth
  faceYaw: 0.42,          // where it ends up facing: between the viewer and GITRL
  cruiseLean: 0.05, brakeLean: 0.14,   // radians. "A little": 3 degrees into the motion, 8 back to stop
  title: [0, 2.2, -1.2], viewer: [0, 2.3, 8.4],   // what the head looks at (layout.js TITLE.center, the camera)
  twos: 12,               // poses per second; 0 = every frame
};

// It comes in ALREADY ROLLING (it has been coming down the corridor), holds that speed for
// `cruise`, then brakes to a stop. Speed = V * (1 - quintic(w)), so the acceleration is
// continuous and zero at both ends of the brake: the lean never jumps.
function travel(u, cruise) {
  const V = 2 / (1 + cruise), w = c01((u - cruise) / (1 - cruise));
  const pos = V * (u - (1 - cruise) * (w * w * w * w * (w * w - 3 * w + 2.5)));        // the integral of the speed
  return { pos: u >= 1 ? 1 : pos, speed: 1 - quintic(w), brake: (30 * w * w * (1 - w) * (1 - w)) / 1.875 };   // brake: 0..1
}

/**
 * Pose the rig for time t (seconds since the entrance began). Pure: same t, same pose.
 * @returns {{x:number, yaw:number, lean:number, roll:[number, number], moving:boolean}}
 */
export function rollIn(rig, t, cfg = ROLL) {
  if (cfg.twos) t = Math.floor(t * cfg.twos + 1e-6) / cfg.twos;               // ON TWOS
  const tt = Math.max(0, t), D = cfg.to - cfg.from;
  const u = c01(tt / cfg.travel), tTurn = tt - cfg.travel, v = ease(0, cfg.turn, tTurn), tRest = tTurn - cfg.turn;
  const go = travel(u, cfg.cruise);

  const x = cfg.from + D * go.pos;
  // into the motion while it rolls, back to brake; then the wobble of a balancer that has
  // just stopped, and the slow hunt it never loses
  const stopWobble = tTurn > 0 ? 0.04 * Math.sin(tTurn * 7.4) * Math.exp(-tTurn * 2.6) * ease(0, 0.25, tTurn) : 0;
  const hunting = ease(0, 1.2, tRest), hunt = 0.012 * Math.sin(tt * 1.7) * hunting;
  const lean = cfg.cruiseLean * go.speed - cfg.brakeLean * go.brake + stopWobble + hunt;

  const yaw = Math.PI / 2 + (cfg.faceYaw - Math.PI / 2) * v;
  // rolling without slipping: distance / radius. Hunting is a real few-mm rock on the wheels,
  // and turning on the spot runs the wheels in opposite directions.
  const huntX = -0.02 * rig.scale * Math.sin(tt * 1.7) * hunting;
  const rollDist = (x - cfg.from) + huntX, spin = (yaw - Math.PI / 2) * rig.halfTrack;
  const rollL = (rollDist + spin) / rig.wheelRadius, rollR = (rollDist - spin) / rig.wheelRadius;

  rig.root.position.set(x + huntX * Math.sin(yaw), cfg.floorY, cfg.z + huntX * Math.cos(yaw));
  rig.root.rotation.set(0, yaw, 0);
  rig.wheelL.rotation.x = rollL; rig.wheelR.rotation.x = rollR;
  rig.body.rotation.x = lean;

  // secondary motion, lagging the body: the carriage sinks a touch under braking and the arms
  // swing forward as it stops
  const lag = travel(c01((tt - 0.14) / cfg.travel), cfg.cruise).brake * (tt - 0.14 < cfg.travel ? 1 : 0);
  rig.lift.position.y = rig.lift.userData.home - 0.03 * lag - 0.05 * (1 - ease(cfg.travel * 0.3, cfg.travel, tt));

  // the head: looks where it is going, finds GITRL as it arrives, then the viewer
  rig.root.updateMatrixWorld(true);
  const toTitle = ease(cfg.travel * 0.35, cfg.travel * 0.85, tt), toViewer = ease(0.9, 1.8, tRest);
  const aT = aimAngles(rig, cfg.title), aV = aimAngles(rig, cfg.viewer);
  const clampPan = (a) => Math.max(-1.35, Math.min(1.35, a));
  const pan = clampPan(aT.pan) * toTitle * (1 - toViewer) + clampPan(aV.pan) * toViewer;
  const tilt = 0.1 * (1 - toTitle) + aT.tilt * toTitle * (1 - toViewer) + aV.tilt * toViewer;
  rig.headPan.rotation.y = pan;
  rig.headTilt.rotation.x = tilt - lean;                                         // level while the mast leans

  // and a wave once it is looking at you: arm out to the SIDE (pan the shoulder outward, then
  // lift), forearm up, so it reads in silhouette; the flap is the elbow
  const wave = ease(1.5, 2.2, tRest) - ease(3.9, 4.6, tRest);
  const flap = Math.sin(Math.max(0, tRest - 2.1) * 9.0) * ease(2.1, 2.4, tRest) * (1 - ease(3.6, 4.0, tRest));
  for (const a of rig.arms) {
    const w = a.side > 0 ? wave : 0;
    a.shoulder.rotation.order = 'YXZ';
    a.shoulder.rotation.set(0.12 + 0.4 * lag - lean * 0.6 - 1.85 * w, a.side * 1.4 * w, a.side * -0.05);
    a.elbow.rotation.x = -1.2 - 0.2 * lag - 0.2 * w + 0.45 * flap * w;
    a.wrist.rotation.x = -0.15 + 0.3 * flap * w;
  }
  return { x, yaw, lean, roll: [rollL, rollR], moving: u < 1 || v < 1 };
}

