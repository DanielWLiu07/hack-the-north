// robotpop.js — a heavy mechanical reveal behind the hanging lettering.
// The scan stays rigid; a chest pivot keeps the large head from sweeping sideways.
import * as THREE from 'three';
import { loadRobotSplat } from './splat.js';

export const POP = {
  at: 2.6,
  rise: 1.8,
  height: 5.8,
  z: -6.0,
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
  const rotatedPivot = new THREE.Vector3();

  return {
    update(w) {
      // Fit the hero to screen height even when the camera retreats on a phone.
      const distance = w.camera.position.z - POP.z;
      const frameHeight = 2 * distance * Math.tan(THREE.MathUtils.degToRad(w.camera.fov / 2));
      const scale = frameHeight * 1.65 / POP.height;
      // The head settles high in frame; more than half of the full scan stays below it.
      const floor = 2.1 - frameHeight * 1.40;
      const from = floor - frameHeight * 0.90;
      const t = Math.max(0, w.t - POP.at);
      // One continuous lift, with a small hydraulic overtravel that settles once.
      const u = smooth(t / (reduced ? 1.0 : POP.rise));
      const travel = reduced ? 0 : Math.sin(Math.PI * u) ** 2;
      const settle = reduced ? 0 : Math.sin(Math.PI * smooth((t - 1.15) / 1.1)) ** 2;
      const acknowledge = reduced ? 0 : Math.sin(Math.PI * smooth((t - 2.3) / 1.2)) ** 2;
      exit += ((w.away?.on ? 1 : 0) - exit) * (1 - Math.exp(-6 * Math.min(w.dt || 0, 0.05)));

      robot.scale.setScalar(scale);
      robot.rotation.set(
        reduced ? 0 : -0.055 * travel + 0.018 * settle - 0.025 * acknowledge,
        reduced ? 0.08 : -0.20 * (1 - u) + 0.08 * u,
        reduced ? 0 : -0.025 * travel + 0.008 * settle,
      );
      // Rotate around the chest, not the invisible wheels far beneath the viewport.
      const pivotY = POP.height * scale * 0.78;
      rotatedPivot.set(0, pivotY, 0).applyEuler(robot.rotation);
      robot.position.set(
        -rotatedPivot.x,
        THREE.MathUtils.lerp(from, floor, u) + frameHeight * 0.022 * settle
          + pivotY - rotatedPivot.y - smooth(exit) * frameHeight * 1.5,
        POP.z - rotatedPivot.z,
      );
    },
  };
}
