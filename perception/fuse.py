"""Per-camera clouds -> the canonical world frame. Stage 7 of docs/20-perception-logic.md.

    F_rect   depth.py: rectified left camera, X right, Y down, Z fwd, metres
      |  Mount (T_rob<-cam): measured pitch, height, yaw of that camera. Still Y-down.
      |  cam_to_world_axes                  <- the ONLY Y-down -> Z-up conversion
    F_rob    X fwd, Y left, Z up, floor at z = 0, robot at the origin
      |  robot pose (T_world<-rob): odom_to_world() of BB's {x, z, yaw}
    F_world  X fwd from the anchor, Y left, Z up, floor at z = 0

BB's odometry is X fwd / Z LEFT / Y down (docs/20 Fact 3). For a planar pose that is just a
relabel, z -> y, done in odom_to_world(); it must NOT go through cam_to_world_axes, which
swaps X and Z. Not here yet: extrinsics.yaml + ICP for cameras 2 and 3 (calib.py); Mount
covers the stock camera.
"""
from __future__ import annotations

import sys
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import obs  # noqa: E402

# docs/20 Part 6, after stage 7
BELOW_FLOOR_M = -0.30     # nothing real is this far under the floor...
BELOW_FLOOR_FRAC = 0.01   # ...but mismatches and floor reflections put a few points there
FLOOR_TOL_M = 0.05        # the floor IS at z = 0, to within this
HORIZONTAL_DEG = 10.0     # normal this close to +Z: a horizontal plane
PLANE_DIST_M = 0.03       # RANSAC inlier distance; stereo floors are noisy at range
MIN_PLANE_FRAC = 0.05     # planes smaller than this share of the cloud are ignored


@dataclass(frozen=True)
class Mount:
    """Where a rig's RECTIFIED left camera sits on the robot. docs/20: calibrate the
    rectified frame, so R1 is already inside these numbers. MEASURE them: BB's example
    uses pitch 36 deg / height 1.5 m for some other rig -- placeholders, not ours."""
    pitch_down_deg: float       # tilt toward the floor
    height_m: float             # lens above the floor; the robot origin is the floor below it
    yaw_left_deg: float = 0.0   # heading, counter-clockwise seen from above; 0 = robot forward

    def matrix(self) -> tuple[np.ndarray, np.ndarray]:
        """(R, t) of T_rob<-cam, in the Y-DOWN robot frame, as BB's example applies it."""
        p, y = np.radians(self.pitch_down_deg), np.radians(self.yaw_left_deg)
        pitch = np.array([[1, 0, 0], [0, np.cos(p), np.sin(p)], [0, -np.sin(p), np.cos(p)]])
        yaw = np.array([[np.cos(y), 0, -np.sin(y)], [0, 1, 0], [np.sin(y), 0, np.cos(y)]])
        return yaw @ pitch, np.array([0.0, -self.height_m, 0.0])     # +Y is down


# perception/fuse.py -- the ONLY place this conversion may appear
def cam_to_world_axes(p_yDown):
    """OpenCV (X right, Y down, Z fwd)  ->  canonical (X fwd, Y left, Z up)."""
    x, y, z = p_yDown[..., 0], p_yDown[..., 1], p_yDown[..., 2]
    return np.stack([z, -x, -y], axis=-1)


def rect_to_world(p: np.ndarray, mount: Mount, robot_pose=(0.0, 0.0, 0.0)) -> np.ndarray:
    """(...,3) F_rect -> F_world, the robot standing at robot_pose = F_world (x, y, yaw rad).
    Shape kept and NaN stays NaN. For segmented instances use segment.run(..., mount=): it
    lifts in F_rect FIRST, because lift()'s edge filter reads column 2 as range from the
    camera -- in F_world that column is height."""
    R, t = mount.matrix()
    rob = cam_to_world_axes(p @ R.T + t)
    x, y, yaw = robot_pose
    c, s = math.cos(yaw), math.sin(yaw)
    return rob @ np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]]) + np.array([x, y, 0.0])


def odom_to_world(pose: dict) -> tuple[float, float, float]:
    """BB's planar pose -- GET /pose, /capture's "pose" (docs/16) -- -> F_world (x, y, yaw).

    examples/example_localization.py: x forward, z LEFT, yaw in radians, 0 = facing +x, and a
    faster right wheel raises yaw while z grows -- counter-clockwise seen from above, kept in
    [0, 2 pi). F_world is x forward, y LEFT, same yaw sense. So: z -> y, yaw wrapped to
    (-pi, pi], no sign flips."""
    return float(pose["x"]), float(pose["z"]), _wrap(float(pose["yaw"]))


def world_to_odom(x: float, y: float, yaw: float) -> dict:
    """F_world (x, y, yaw rad) -> BB's {x, z, yaw}: POST /drive's "target" (docs/16 §2.3)."""
    return {"x": float(x), "z": float(y), "yaw": float(yaw) % (2 * math.pi)}


def _wrap(a: float) -> float:
    return math.pi - (math.pi - a) % (2 * math.pi)


def fuse(views: list[tuple[np.ndarray, np.ndarray, Mount]], robot_pose=(0.0, 0.0, 0.0)):
    """[(xyz, valid, mount) per rig] -> (per-rig (H,W,3) F_world arrays, (N,3) cloud of
    every valid point). robot_pose: F_world (x, y, yaw rad), odom_to_world() of the capture's
    pose. The floor check runs on every call."""
    with obs.span("perception.fuse", n_views=len(views)) as sp:
        world = [rect_to_world(xyz, m, robot_pose) for xyz, _, m in views]
        cloud = np.concatenate([w[v] for w, (_, v, _) in zip(world, views)])
        with obs.span("perception.floor_check") as fsp:
            floor_z, normal, point = _floor_plane(cloud)
            fit = _fit(normal, point, robot_pose)
            if fsp is not None:
                fsp.set_data("floor_z", floor_z)
                for k, v in fit.items():
                    fsp.set_data(k, v)
            # charted per capture: frame drift shows in floor_z; a wrong Mount pitch in the tilt,
            # a wrong height (or depth scale) in z_at_robot -- docs/10, LINK's rising floor
            obs.measure(floor_z=floor_z, floor_tilt_ahead_deg=fit["tilt_ahead_deg"],
                        floor_z_at_robot=fit["z_at_robot"])
        if sp is not None:
            sp.set_data("n_points", len(cloud))
    return world, cloud


def assert_floor(cloud: np.ndarray) -> float:
    """docs/20 Part 6: catches an axis mistake within one capture. Returns the floor height.

    A missing, doubled or sign-flipped cam_to_world_axes, or a wrong mount, leaves no
    horizontal plane at z = 0 or puts the room under the floor. The floor is found by
    RANSAC, not by looking near z = 0, which would pass by construction.
    """
    return _floor_plane(cloud)[0]


def floor_fit(cloud: np.ndarray, robot_pose=(0.0, 0.0, 0.0)) -> dict:
    """The floor plane in the terms a Mount error shows up in (docs/10: LINK's cap_0004 floor
    rises ~10 cm/m and sits ~6 cm low under the robot). Asserts like assert_floor.

    tilt_ahead_deg  how steeply the floor rises straight ahead of the robot. Positive: the
                    camera is pitched STEEPER than its Mount says, by about this much.
    tilt_side_deg   the same toward the robot's left (a roll, or a yaw_left_deg error).
    z_at_robot      the floor's height under the robot. A pitch error alone leaves this ~0
                    (rotating about the lens barely moves the floor below it), so a non-zero
                    value is the Mount's height -- or the depth scale, which one capture of a
                    bare floor can't tell apart: a tape-measured distance in the same capture can.
    """
    _, normal, point = _floor_plane(cloud)
    return _fit(normal, point, robot_pose)


def _fit(normal, point, robot_pose) -> dict:
    n = normal if normal[2] > 0 else -normal
    x, y, yaw = robot_pose
    ahead, left = (math.cos(yaw), math.sin(yaw)), (-math.sin(yaw), math.cos(yaw))
    return {"tilt_ahead_deg": round(math.degrees(math.atan2(-(n[0] * ahead[0] + n[1] * ahead[1]), n[2])), 2),
            "tilt_side_deg": round(math.degrees(math.atan2(-(n[0] * left[0] + n[1] * left[1]), n[2])), 2),
            "z_at_robot": round(float(point[2] - (n[0] * (x - point[0]) + n[1] * (y - point[1])) / n[2]), 4)}


def _floor_plane(cloud: np.ndarray):
    """assert_floor's checks -> (floor z, its unit normal, a point on it)."""
    pts = cloud[np.isfinite(cloud).all(axis=1)]
    assert len(pts), "empty cloud: every camera blind?"
    below = float((pts[:, 2] < BELOW_FLOOR_M).mean())
    assert below < BELOW_FLOOR_FRAC, (
        f"{below:.1%} of points are >30 cm under the floor: is Z flipped (+Y down not negated)?")
    flat = [(z, n, c) for n, z, c in _planes(pts) if abs(n[2]) > np.cos(np.radians(HORIZONTAL_DEG))]
    assert flat, ("no horizontal plane: cam_to_world_axes missing or applied twice, "
                  "or the mount pitch is wrong")
    floor_z, normal, point = min(flat, key=lambda f: f[0])
    assert abs(floor_z) < FLOOR_TOL_M, (
        f"lowest horizontal plane is at z={floor_z:+.3f} m, not 0: check the mount height and "
        f"pitch, or this camera can't see the floor")
    return floor_z, normal, point


def _planes(pts: np.ndarray, max_planes: int = 3, iters: int = 300, sample: int = 10000):
    """Dominant planes, largest first, as (unit normal, median z of inliers, inlier centroid). Deterministic:
    strided subsample and a fixed seed, so a rescan can't pass or fail by luck."""
    rng = np.random.default_rng(0)
    p = pts[::len(pts) // sample + 1]
    min_inliers = MIN_PLANE_FRAC * len(p)
    out = []
    for _ in range(max_planes):
        if len(p) < 3:
            break
        tri = p[rng.integers(0, len(p), size=(iters, 3))]
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        norm = np.linalg.norm(n, axis=1)
        ok = norm > 1e-9
        n = n[ok] / norm[ok, None]
        d = -np.einsum("ij,ij->i", n, tri[ok, 0])
        k = int((np.abs(p @ n.T + d) < PLANE_DIST_M).sum(axis=0).argmax())
        inl = np.abs(p @ n[k] + d[k]) < PLANE_DIST_M
        if inl.sum() < min_inliers:
            break
        q = p[inl]
        normal = np.linalg.svd(q - q.mean(axis=0), full_matrices=False)[2][-1]   # refit
        out.append((normal, float(np.median(q[:, 2])), q.mean(axis=0)))
        p = p[~inl]
    return out
