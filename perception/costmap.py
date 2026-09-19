"""Where the robot's BASE may stand, and where to stand to reach or see something.
docs/24-traversal-and-graph.md Part A: A1 costmap, A2 base pose, A3 viewpoint.

We compute WHERE; Bracket Bot's nav computes HOW (A0). The path-length field here filters
and ranks candidate poses; it never drives.

The collision band (A1): an obstacle is a voxel with points between Z_FLOOR and ROBOT_H,
not any occupied voxel. A table's pedestal blocks the base; its top, above ROBOT_H, does
not. Treat the whole table as an obstacle and the robot never gets within arm's reach of
anything on it -- which shows up as "IK: unreachable" and sends you to the wrong file.
solve_base_pose's span says which filter rejected the candidates, so read that first.

Frame and units: F_world (X fwd, Y left, Z up), metres; poses are (x, y, yaw) with yaw in
RADIANS, counter-clockwise from +X. The costmap's cells are the voxel grid's columns.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from scipy import ndimage
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

try:                                 # as perception.costmap, or flat with perception/ on sys.path
    from .raycast import line_of_sight
    from .voxelize import VoxelGrid
except ImportError:
    from raycast import line_of_sight
    from voxelize import VoxelGrid

import obs  # noqa: E402  (voxelize put the repo root on sys.path)

log = logging.getLogger(__name__)

Z_FLOOR = 0.02      # m. a voxel whose MEDIAN point is below this is floor. Stereo floors are
                    # noisier beyond ~1.5 m; if open floor turns into obstacles, raise it
ROBOT_H = 0.60      # m. MEASURE. Top of the part of the robot as wide as INFLATE_M. Must sit
                    # BELOW table-top height or table tops block (A1). docs/24's ~1.0 m is the
                    # whole robot: with that, a 0.75 m top is inside the band.
INFLATE_M = 0.28    # half the 0.425 m wheelbase, plus margin. Inflate ONCE, here.
PATH_TIE_M = 0.25   # path lengths this close count as equal when ranking poses


class Arm(Protocol):
    """What the solvers need from robot/arm.py."""
    r_min: float    # reach annulus around the BASE centre, in the floor plane, metres
    r_max: float

    def reachable(self, target, base_pose) -> bool: ...   # IK exists: F_world xyz, (x, y, yaw)


@dataclass
class Costmap:
    grid: VoxelGrid
    obstacle: np.ndarray   # (n,n) bool: this voxel column holds a body-band voxel
    blocked: np.ndarray    # (n,n) bool: within INFLATE_M of an obstacle -- the CENTRE can't be here

    @classmethod
    def from_grid(cls, grid: VoxelGrid, robot_h: float = ROBOT_H, z_floor: float = Z_FLOOR,
                  inflate: float = INFLATE_M) -> "Costmap":
        """Project the BODY BAND to the floor and grow it by the robot's radius.

        In the band: the voxel's median point is above z_floor and its 10th percentile is below
        robot_h. The median, not the top, at the floor edge: the cube starts at z = 0, so a
        floor voxel keeps only the upper half of the floor's noise, and its top few points
        sit above 2 cm routinely. Anything taller than a voxel is caught by the layers above.
        And a real obstacle lower than one voxel is wider than one: a LONE low voxel is floor
        noise, and inflated it would wall off 0.25 m^2 of open floor.
        """
        with obs.span("perception.costmap", robot_h=robot_h, inflate=inflate) as sp:
            band = (grid.z_mid > z_floor) & (grid.z_lo < robot_h)
            i, j, k = grid.ijk[band].T
            tall, low = np.zeros((grid.n, grid.n), bool), np.zeros((grid.n, grid.n), bool)
            tall[i[k > 0], j[k > 0]] = True
            low[i[k == 0], j[k == 0]] = True
            any_ = tall | low
            neighbours = ndimage.convolve(any_.astype(np.uint8), np.ones((3, 3), np.uint8), mode="constant") - any_
            obstacle = tall | (low & (neighbours > 0))
            clearance = ndimage.distance_transform_edt(~obstacle) * grid.leaf   # metres, centre to centre
            blocked = clearance <= inflate
            if sp is not None:
                sp.set_data("obstacle_cells", int(obstacle.sum()))
                sp.set_data("speckle_dropped", int((low & ~obstacle).sum()))
        return cls(grid, obstacle, blocked)

    @classmethod
    def from_bb_grid(cls, area, reg, grid: VoxelGrid | None = None, inflate: float = INFLATE_M,
                     levels: int = 8) -> "Costmap":
        """Bracket Bot's own 2-D map (plan/roommate 03 §3 AreaMap, or GET /map's JSON: 3 cm cells,
        1 floor / 2 obstacle / 0 unknown, in the AREA frame) -> a room-frame costmap on the pinned
        cube at `levels` (8: 3.125 cm, BB's own resolution).

        BB already decided what is an obstacle, so there is no band here: its obstacles are
        inflated by the robot's radius exactly as from_grid's. UNKNOWN is blocked for standing
        (never a stance on floor nobody has mapped) but is not an obstacle, so it doesn't grow.
        grid: the 3-D map the solvers ray-cast through (bb_source.visibility_grid); None -> an
        empty one, every ray clear. Every cell crosses frames through roomctl.frames."""
        from roomctl import frames
        try:
            from .bb_source import _area
            from .voxelize import pinned_cube
        except ImportError:
            from bb_source import _area
            from voxelize import pinned_cube
        with obs.span("perception.costmap_bb", inflate=inflate) as sp:
            if grid is None:
                origin, size, _ = pinned_cube()
                grid = VoxelGrid.from_points(np.zeros((0, 3)), cube=(origin, size, levels), min_pts=1)
            a = _area(area)
            n, leaf = grid.n, grid.leaf
            gx, gy = np.meshgrid(grid.origin[0] + (np.arange(n) + 0.5) * leaf,
                                 grid.origin[1] + (np.arange(n) + 0.5) * leaf, indexing="ij")   # [i, j], as cell()
            bb = frames.room_to_bb_array(np.column_stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)]), reg.T)
            # BB world -> area frame is affine for a fixed anchor: read it off frames, don't re-derive it
            o = np.array(frames.bb_to_robot_rel(0.0, 0.0, *a.anchor))
            ax = np.array(frames.bb_to_robot_rel(1.0, 0.0, *a.anchor)) - o
            ay = np.array(frames.bb_to_robot_rel(0.0, 1.0, *a.anchor)) - o
            rel = o + np.outer(bb[:, 0], ax) + np.outer(bb[:, 1], ay)
            col = np.floor((rel[:, 0] - a.xmin) / a.res).astype(np.int64)
            row = np.floor((rel[:, 1] - a.ymin) / a.res).astype(np.int64)
            ny, nx = a.grid.shape
            inside = (row >= 0) & (row < ny) & (col >= 0) & (col < nx)
            val = np.zeros(len(rel), np.uint8)
            val[inside] = a.grid[row[inside], col[inside]]
            val = val.reshape(n, n)
            obstacle = val == 2
            clearance = ndimage.distance_transform_edt(~obstacle) * leaf
            blocked = (clearance <= inflate) | (val == 0)
            if sp is not None:
                sp.set_data("obstacle_cells", int(obstacle.sum()))
                sp.set_data("unknown_cells", int((val == 0).sum()))
        return cls(grid, obstacle, blocked)

    def cell(self, x: float, y: float) -> tuple[int, int] | None:
        i, j = (math.floor((v - o) / self.grid.leaf) for v, o in zip((x, y), self.grid.origin[:2]))
        return (i, j) if 0 <= i < self.grid.n and 0 <= j < self.grid.n else None

    def occupied(self, x: float, y: float) -> bool:
        """Can the robot's centre NOT stand at (x, y)? Outside the mapped cube: can't."""
        c = self.cell(x, y)
        return c is None or bool(self.blocked[c])

    def path_lengths(self, start_xy) -> np.ndarray:
        """(n,n) metres along the shortest 8-connected path through unblocked cells from
        start_xy to each cell; inf where unreachable. One pass serves every candidate.
        The start cell counts as free even if blocked: the robot is standing there."""
        n, leaf = self.grid.n, self.grid.leaf
        free = ~self.blocked
        start = self.cell(*start_xy)
        out = np.full((n, n), np.inf)
        if start is None:
            return out
        free[start] = True
        idx = np.full((n, n), -1)
        idx[free] = np.arange(int(free.sum()))
        rows, cols, w = [], [], []
        for di, dj in ((1, 0), (0, 1), (1, 1), (1, -1)):
            i0, i1, j0, j1 = max(0, -di), n - max(0, di), max(0, -dj), n - max(0, dj)
            a, b = idx[i0:i1, j0:j1], idx[i0 + di:i1 + di, j0 + dj:j1 + dj]
            m = (a >= 0) & (b >= 0)
            rows.append(a[m]), cols.append(b[m]), w.append(np.full(int(m.sum()), leaf * math.hypot(di, dj)))
        size = int(free.sum())
        graph = csr_matrix((np.concatenate(w), (np.concatenate(rows), np.concatenate(cols))), shape=(size, size))
        out[free] = dijkstra(graph, directed=False, indices=int(idx[start]))
        return out


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def _ring(centre, r_min, r_max):
    """docs/24 A2's sampling: 36 headings x 5 radii around centre. Yields (x, y, yaw, r) with
    yaw FACING the centre."""
    for theta in np.linspace(0, 2 * np.pi, 36, endpoint=False):
        for r in np.linspace(r_min, r_max, 5):
            yield centre[0] - r * math.cos(theta), centre[1] - r * math.sin(theta), _wrap(theta), r


BASE_FILTERS = ("base_fits", "ik", "line_of_sight", "path")        # docs/24 A2, in order
VIEW_FILTERS = ("base_fits", "same_angle", "line_of_sight", "path")


def solve_base_pose(target, costmap: Costmap, arm: Arm, robot_pose, eye_h: float,
                    ignore_end: float | None = None):
    """Where to stand to pick up `target` (F_world xyz, m) -> (x, y, yaw) or None.
    solve_base_pose_why() also says which filters rejected the candidates."""
    return solve_base_pose_why(target, costmap, arm, robot_pose, eye_h, ignore_end)[0]


def solve_base_pose_why(target, costmap: Costmap, arm: Arm, robot_pose, eye_h: float,
                        ignore_end: float | None = None):
    """-> ((x, y, yaw) or None, {filter: candidates it rejected}) -- every filter, in order.

    Samples the reach annulus and keeps poses that pass docs/24 A2's four filters, in its
    order: the base fits, IK exists, the camera (eye_h metres up) can see the target, and a
    path exists. Ranked by path length, then how centred the target is in the annulus
    (mid-reach has the most IK slack), then least turning. None is a real answer -- the
    "cannot apply hunk" case: report it WITH the counts, don't retry forever. All 180 at
    base_fits means the costmap, not IK (docs/24 A1).
    """
    with obs.span("perception.base_pose") as sp:
        dist = costmap.path_lengths(robot_pose[:2])
        r_mid = (arm.r_min + arm.r_max) / 2
        why, best = dict.fromkeys(BASE_FILTERS, 0), None
        for bx, by, yaw, r in _ring(target, arm.r_min, arm.r_max):
            if costmap.occupied(bx, by):
                why["base_fits"] += 1
            elif not arm.reachable(target, (bx, by, yaw)):
                why["ik"] += 1
            elif not line_of_sight((bx, by, eye_h), target, costmap.grid, ignore_end):
                why["line_of_sight"] += 1
            elif not math.isfinite(path := float(dist[costmap.cell(bx, by)])):
                why["path"] += 1
            else:
                key = (round(path / PATH_TIE_M), round(abs(r - r_mid), 6), abs(_wrap(yaw - robot_pose[2])))
                if best is None or key < best[0]:
                    best = (key, (bx, by, yaw))
        _report(sp, why, best, "base pose", target)
        return (best and best[1]), why


def solve_viewpoint(last_xyz, costmap: Costmap, blocked_from, robot_pose, eye_h: float,
                    r_min: float = 0.8, r_max: float = 1.6, min_sep_deg: float = 45.0,
                    ignore_end: float | None = None):
    """A pose that can SEE last_xyz from a different angle than blocked_from (docs/24 A3) ->
    (x, y, yaw) facing it, or None. solve_viewpoint_why() also says why not."""
    return solve_viewpoint_why(last_xyz, costmap, blocked_from, robot_pose, eye_h, r_min, r_max,
                               min_sep_deg, ignore_end)[0]


def solve_viewpoint_why(last_xyz, costmap: Costmap, blocked_from, robot_pose, eye_h: float,
                        r_min: float = 0.8, r_max: float = 1.6, min_sep_deg: float = 45.0,
                        ignore_end: float | None = None):
    """-> (pose or None, {filter: candidates it rejected}). The > min_sep_deg term matters: a
    second look from nearly the same angle is blocked by the same thing and tells you nothing."""
    with obs.span("perception.viewpoint") as sp:
        dist = costmap.path_lengths(robot_pose[:2])
        away = math.atan2(blocked_from[1] - last_xyz[1], blocked_from[0] - last_xyz[0])
        why, best = dict.fromkeys(VIEW_FILTERS, 0), None
        for bx, by, yaw, _ in _ring(last_xyz, r_min, r_max):
            if costmap.occupied(bx, by):
                why["base_fits"] += 1
            elif abs(_wrap(math.atan2(by - last_xyz[1], bx - last_xyz[0]) - away)) <= math.radians(min_sep_deg):
                why["same_angle"] += 1
            elif not line_of_sight((bx, by, eye_h), last_xyz, costmap.grid, ignore_end):
                why["line_of_sight"] += 1
            elif not math.isfinite(path := float(dist[costmap.cell(bx, by)])):
                why["path"] += 1
            else:
                key = (round(path / PATH_TIE_M), abs(_wrap(yaw - robot_pose[2])))
                if best is None or key < best[0]:
                    best = (key, (bx, by, yaw))
        _report(sp, why, best, "viewpoint", last_xyz)
        return (best and best[1]), why


def _report(sp, why: dict, best, what: str, target) -> None:
    if sp is not None:
        sp.set_data("rejected", why)
        sp.set_data("found", best is not None)
    if best is None:
        log.info("%s for %s: none; rejected by %s", what, np.round(target, 2).tolist(),
                 {k: v for k, v in why.items() if v})
