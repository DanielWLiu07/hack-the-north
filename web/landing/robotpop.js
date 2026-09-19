// robotpop.js — the scanned Bracket Bot springs up behind the lettering.
// Move the scan rigidly: a rising reveal, a three-quarter turn and a balance catch.
import * as THREE from 'three';
import { loadRobotSplat } from './splat.js';

export const POP = {
  at: 2.6,
  rise: 1.15,
  height: 5.8,
  z: -3.4,
};
const smooth = (v) => { const u = THREE.MathUtils.clamp(v, 0, 1); return u * u * u * (u * (u * 6 - 15) + 10); };

export async function buildRobotPop(world) {
  let robot;
  try {
    robot = await loadRobotSplat({ height: POP.height, pixelRatio: 1 });
  } catch (e) {
    console.warn('[gitrl] robot splat not loading:', e);
    return { update() {} };
  }
  world.scene.add(robot);
  world.robot = robot;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let exit = 0;

  return {
    update(w) {
      // Fit the hero to screen height even when the camera retreats on a phone.
      const distance = w.camera.position.z - POP.z;
      const frameHeight = 2 * distance * Math.tan(THREE.MathUtils.degToRad(w.camera.fov / 2));
      const scale = frameHeight * 0.70 / POP.height;
      const floor = 2.1 - frameHeight * 0.43;
      const from = floor - POP.height * scale - frameHeight * 0.22;
      const t = Math.max(0, w.t - POP.at);
      const u = smooth(t / (reduced ? 0.7 : POP.rise));
      const catchT = Math.max(0, t - POP.rise);
      const catchIn = smooth(catchT / 0.24);
      const balance = reduced ? 0 : catchIn * Math.exp(-2.5 * catchT) * Math.sin(9 * catchT);
      const lift = reduced ? 0 : Math.sin(Math.PI * u) ** 2;
      exit += ((w.away?.on ? 1 : 0) - exit) * (1 - Math.exp(-6 * Math.min(w.dt || 0, 0.05)));

      robot.scale.setScalar(scale);
      robot.position.set(0, THREE.MathUtils.lerp(from, floor, u) + scale * (0.60 * lift + 0.18 * balance)
        - smooth(exit) * frameHeight * 1.5, POP.z);
      robot.rotation.set(
        reduced ? 0 : -0.12 * lift + 0.055 * balance,
        reduced ? 0.15 : -0.65 * (1 - u) + 0.15 * u + 0.08 * balance,
        reduced ? 0 : 0.13 * lift - 0.055 * balance,
      );
    },
  };
}
