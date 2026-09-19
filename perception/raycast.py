"""Line of sight on the voxel grid (docs/24 A4). One implementation, two callers:

- costmap.solve_base_pose / solve_viewpoint: can the robot SEE the target from there?
- the occluded-vs-removed verdict (docs/20 Part 4): is something between the camera and
  where the object was? Blocked -> UNOBSERVED (carry it forward); clear -> REMOVED.

It lives here so neither owns it. Frame and units: F_world, metres, on voxelize.VoxelGrid.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

try:                                 # as perception.raycast, or flat with perception/ on sys.path
    from .voxelize import VoxelGrid
except ImportError:
    from voxelize import VoxelGrid


def line_of_sight(a, b, grid: VoxelGrid, ignore_end: float | None = None) -> bool:
    """True if the segment a -> b (F_world metres) crosses no occupied voxel.

    The last `ignore_end` metres before b don't count: the target's own voxels, and the
    surface it rests on, must not hide it from itself. Default: one voxel diagonal. Pass
    the object's half-diagonal plus a voxel for anything bigger than a voxel.
    """
    return first_hit(a, b, grid, ignore_end) is None


def first_hit(a, b, grid: VoxelGrid, ignore_end: float | None = None) -> tuple[int, int, int] | None:
    """The first occupied voxel (i, j, k) the segment enters, or None. 3-D DDA (Amanatides &
    Woo): visits every voxel the segment passes through, in order, and nothing else, so a
    thin wall can't slip between samples. Cells outside the cube are free (never observed)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    leaf = grid.leaf
    if ignore_end is None:
        ignore_end = leaf * math.sqrt(3)
    length = float(np.linalg.norm(b - a))
    if length <= ignore_end:
        return None
    t_end = 1.0 - ignore_end / length                     # t runs 0 (at a) .. 1 (at b)

    p = (a - grid.origin) / leaf                          # voxel units
    v = (b - a) / leaf
    cell = [math.floor(c) for c in p]
    step = [1 if c > 0 else -1 if c < 0 else 0 for c in v]
    t_max = [((cell[i] + (step[i] > 0)) - p[i]) / v[i] if step[i] else math.inf for i in range(3)]
    t_delta = [abs(1.0 / v[i]) if step[i] else math.inf for i in range(3)]

    i, j, k = cell
    tx, ty, tz = t_max
    dx, dy, dz = t_delta
    sx, sy, sz = step
    n, occ = grid.n, grid.occ
    while True:
        if 0 <= i < n and 0 <= j < n and 0 <= k < n and occ[i, j, k]:
            return (i, j, k)
        if tx <= ty and tx <= tz:
            if tx > t_end:
                return None
            i += sx
            tx += dx
        elif ty <= tz:
            if ty > t_end:
                return None
            j += sy
            ty += dy
        else:
            if tz > t_end:
                return None
            k += sz
            tz += dz


@dataclass(frozen=True)
class Camera:
    """What a camera could have seen at this capture: F_world metres, a cone around its axis."""
    position: tuple[float, float, float]
    forward: tuple[float, float, float]     # optical axis; normalised on use
    half_fov_deg: float                     # depth.StereoDepth.half_fov_deg(): the narrower half-angle
    max_range: float = 5.0                  # depth.MAX_RANGE_M: no depth beyond it, so no view

    @classmethod
    def from_mount(cls, mount, half_fov_deg: float, robot_pose=(0.0, 0.0, 0.0), max_range: float = 5.0) -> "Camera":
        from fuse import rect_to_world                      # the one place frames change
        pos, ahead = rect_to_world(np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]]), mount, robot_pose)
        return cls(tuple(map(float, pos)), tuple(map(float, ahead - pos)), half_fov_deg, max_range)

    def covers(self, p) -> bool:
        d = np.asarray(p, float) - self.position
        r = float(np.linalg.norm(d))
        f = np.asarray(self.forward, float)
        return 0 < r <= self.max_range and float(d @ f) / (r * np.linalg.norm(f)) >= math.cos(math.radians(self.half_fov_deg))


def occlusion_check(cameras: list[Camera], grid: VoxelGrid):
    """The `occluded` callback associate.associate() takes, for the occluded-vs-removed verdict.

    Per object, only the cameras whose field of view (and depth range) covers its last pose
    count; a camera facing away has a clear line and no view. Then: UNOBSERVED if EVERY such
    camera's line is blocked, REMOVED if one had a clear view and saw nothing -- and if NO
    camera covered the spot, UNOBSERVED: nothing could have seen it, so its file must not be
    deleted. (docs/20 Part 4's sketch breaks on the first blocked camera; "every" is right.)
    """
    for c in cameras:
        if not isinstance(c, Camera):
            raise TypeError(f"occlusion_check needs raycast.Camera (position, forward, half_fov), got {c!r}: "
                            "a bare position covers every object, and a camera facing away would REMOVE it")

    def occluded(rec) -> bool:
        target = (rec.pose.x, rec.pose.y, rec.pose.z)
        own = 0.5 * math.dist((0.0, 0.0, 0.0), (rec.extents.x, rec.extents.y, rec.extents.z)) + grid.leaf
        covering = [c for c in cameras if c.covers(target)]
        return all(not line_of_sight(c.position, target, grid, own) for c in covering)   # all([]) -> True
    return occluded
