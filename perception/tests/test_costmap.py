"""costmap.py (docs/24 Part A): the COLLISION BAND, inflation, base-pose and viewpoint solving.
FRAME: F_world, X fwd / Y left / Z up. UNITS: metres, yaw in radians."""
import logging
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import costmap  # noqa: E402
from costmap import Costmap, solve_base_pose, solve_viewpoint  # noqa: E402
from raycast import line_of_sight  # noqa: E402
import voxelize  # noqa: E402
from voxelize import VoxelGrid  # noqa: E402

CUBE = ((-4.0, -4.0, 0.0), 8.0, 7)
TABLE = (2.0, 0.0)          # pedestal table: centre
TOP_R, TOP_Z = 0.40, 0.75   # round top, radius and height
POST_R = 0.06               # the pedestal
EYE_H = 1.0                 # camera height on the robot
MUG = np.array([2.05, 0.10, 0.80])


@dataclass
class Arm:
    """Stand-in for robot/arm.py: an annulus, and IK only at table height."""
    r_min: float = 0.15
    r_max: float = 0.45

    def reachable(self, target, base_pose):
        r = math.hypot(target[0] - base_pose[0], target[1] - base_pose[1])
        return self.r_min - 1e-9 <= r <= self.r_max + 1e-9 and 0.5 < target[2] < 1.0


def _disk(cx, cy, r, z, step=0.015):
    x, y = np.mgrid[cx - r:cx + r:step, cy - r:cy + r:step]
    m = (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    return np.column_stack([x[m], y[m], np.full(m.sum(), z)])


def _cylinder(cx, cy, r, z0, z1, step=0.015):
    t, z = np.meshgrid(np.arange(0, 2 * np.pi, step / r), np.arange(z0, z1, step))
    return np.column_stack([cx + r * np.cos(t.ravel()), cy + r * np.sin(t.ravel()), z.ravel()])


def _floor(rng, x0=-1.0, x1=3.5, y0=-2.0, y1=2.0, sigma=0.01):
    x, y = np.mgrid[x0:x1:0.02, y0:y1:0.02]
    return np.column_stack([x.ravel(), y.ravel(), rng.normal(0, sigma, x.size)])


def _room(rng, extra=()):
    """Noisy floor, a pedestal table with an overhanging round top, a mug on it."""
    return np.vstack([_floor(rng),
                      _disk(*TABLE, TOP_R, TOP_Z), _disk(*TABLE, TOP_R, TOP_Z - 0.03),
                      _cylinder(*TABLE, POST_R, 0.0, TOP_Z - 0.03),
                      _cylinder(MUG[0], MUG[1], 0.04, TOP_Z, TOP_Z + 0.10), *extra])


@pytest.fixture(scope="module")
def room():
    return VoxelGrid.from_points(_room(np.random.default_rng(0)), CUBE)


# ── the collision band ───────────────────────────────────────────────────────

def test_pedestal_blocks_the_top_does_not(room):
    cm = Costmap.from_grid(room, robot_h=0.60)
    assert cm.obstacle[cm.cell(*TABLE)]                             # the pedestal
    for r in (0.20, 0.30, 0.38):                                   # under the overhanging top
        assert not cm.obstacle[cm.cell(TABLE[0] + r, TABLE[1])]
    # the robot's CENTRE can stand under the top, inflation-radius from the pedestal
    edge = POST_R + costmap.INFLATE_M
    assert cm.occupied(TABLE[0] + edge - room.leaf, TABLE[1])
    assert not cm.occupied(TABLE[0] + edge + 1.5 * room.leaf, TABLE[1])
    assert not cm.occupied(TABLE[0] + 0.39, TABLE[1])               # inside the top's footprint


def test_arm_reaches_the_mug(room):
    cm = Costmap.from_grid(room, robot_h=0.60)
    arm = Arm()
    pose = solve_base_pose(MUG, cm, arm, robot_pose=(0.0, 0.0, 0.0), eye_h=EYE_H)
    assert pose is not None
    bx, by, yaw = pose
    r = math.hypot(MUG[0] - bx, MUG[1] - by)
    assert arm.r_min - 1e-9 <= r <= arm.r_max + 1e-9                 # metres, inside the annulus
    assert abs(yaw - math.atan2(MUG[1] - by, MUG[0] - bx)) < 1e-9    # radians, facing the mug
    assert not cm.occupied(bx, by)
    assert math.hypot(bx - TABLE[0], by - TABLE[1]) < TOP_R          # standing UNDER the top
    assert line_of_sight((bx, by, EYE_H), MUG, room)


def test_whole_table_as_obstacle_is_the_unreachable_symptom(room, caplog):
    """docs/24 A1's failure, reproduced: ROBOT_H at docs/24's 1.0 m puts the 0.75 m top in the
    band. Every candidate dies at "base fits" -- the costmap -- not at IK."""
    cm = Costmap.from_grid(room, robot_h=1.0)
    assert cm.obstacle[cm.cell(TABLE[0] + 0.3, TABLE[1])]
    with caplog.at_level(logging.INFO, logger="costmap"):
        assert solve_base_pose(MUG, cm, Arm(), robot_pose=(0.0, 0.0, 0.0), eye_h=EYE_H) is None
    assert "'base_fits': 180" in caplog.text and "ik" not in caplog.text


def test_floor_noise_is_not_an_obstacle():
    """1 cm of floor noise: no obstacle ANYWHERE on 18 m^2 of open floor (one inflated speckle
    walls off 0.25 m^2). 2 cm defeats Z_FLOOR = 0.02; raising it to ~3 sigma is the knob."""
    for seed in range(3):
        g = VoxelGrid.from_points(_floor(np.random.default_rng(seed), sigma=0.01), CUBE)
        assert Costmap.from_grid(g, robot_h=0.6).obstacle.sum() == 0
    g = VoxelGrid.from_points(_floor(np.random.default_rng(0), sigma=0.02), CUBE)
    assert Costmap.from_grid(g, robot_h=0.6).obstacle.sum() > 0
    assert Costmap.from_grid(g, robot_h=0.6, z_floor=0.06).obstacle.sum() == 0


def test_overhead_is_ignored_low_things_block():
    rng = np.random.default_rng(1)
    shelf = _disk(0.5, 1.0, 0.3, 1.30)                            # overhead, above ROBOT_H
    box = np.vstack([_disk(1.0, -1.0, 0.1, z) for z in np.arange(0.0, 0.10, 0.015)])  # 10 cm tall
    book = _disk(2.0, -1.0, 0.12, 0.04)                           # 4 cm: under one voxel, wider than one
    mat = _disk(-0.5, -1.0, 0.2, 0.005)                           # 5 mm thick
    pts = np.vstack([_floor(rng), shelf, box, book, mat])
    cm = Costmap.from_grid(VoxelGrid.from_points(pts, CUBE), robot_h=0.6)
    assert not cm.obstacle[cm.cell(0.5, 1.0)]
    assert cm.obstacle[cm.cell(1.0, -1.0)]
    assert cm.obstacle[cm.cell(2.0, -1.0)]
    assert not cm.obstacle[cm.cell(-0.5, -1.0)]


def test_frame_and_units():
    """An obstacle at (x=1.0, y=-0.5): Y is LEFT, so it is on the right; inflation in metres."""
    post = _cylinder(1.0, -0.5, 0.03, 0.0, 0.5)
    cm = Costmap.from_grid(VoxelGrid.from_points(post, CUBE), robot_h=0.6)
    assert cm.occupied(1.0, -0.5)
    assert not cm.occupied(1.0, 0.5) and not cm.occupied(-0.5, 1.0)       # not mirrored, not swapped
    assert cm.occupied(1.0 + costmap.INFLATE_M - 0.07, -0.5)              # inside the 0.28 m radius
    assert not cm.occupied(1.0 + costmap.INFLATE_M + 0.10, -0.5)
    assert cm.occupied(9.0, 0.0)                                           # outside the cube: no


def test_path_lengths_are_metres():
    wall = np.vstack([_cylinder(1.0, y, 0.03, 0.0, 0.5) for y in np.arange(-1.5, 1.51, 0.05)])
    cm = Costmap.from_grid(VoxelGrid.from_points(wall, CUBE), robot_h=0.6)
    d = cm.path_lengths((0.0, 0.0))
    assert d[cm.cell(0.0, 0.0)] == 0.0
    assert d[cm.cell(0.0, 1.0)] == pytest.approx(1.0, abs=cm.grid.leaf)   # open floor: straight line
    around = d[cm.cell(2.0, 0.0)]
    assert math.isfinite(around) and around > 2.0 + 2 * 1.2              # detour round the wall's ends


def test_viewpoint_sees_past_the_occluder(room):
    """The mug is hidden from the robot's camera by a box; find a pose > 45 deg around that
    has a clear view (docs/24 A3)."""
    box = np.vstack([_disk(1.55, 0.05, 0.08, z) for z in np.arange(0.76, 1.1, 0.015)])
    screen = np.vstack([_disk(x, 0.45, 0.02, z) for x in np.arange(1.75, 2.36, 0.03)
                        for z in np.arange(0.76, 1.3, 0.03)])       # blocks the view from +Y
    grid = VoxelGrid.from_points(_room(np.random.default_rng(0), extra=[box, screen]), CUBE)
    cm = Costmap.from_grid(grid, robot_h=0.60)
    here = (0.8, 0.0, EYE_H)
    assert not line_of_sight(here, MUG, grid)
    robot = (1.2, 1.4, 0.0)                                            # nearest ring poses: +Y side
    assert not line_of_sight((1.6, 1.2, EYE_H), MUG, grid)
    pose = solve_viewpoint(MUG, cm, blocked_from=here, robot_pose=robot, eye_h=EYE_H)
    assert pose is not None
    bx, by, yaw = pose
    sep = abs(math.atan2(by - MUG[1], bx - MUG[0]) - math.atan2(here[1] - MUG[1], here[0] - MUG[0]))
    assert math.degrees(min(sep, 2 * math.pi - sep)) > 45
    assert line_of_sight((bx, by, EYE_H), MUG, grid) and not cm.occupied(bx, by)
    assert 0.8 - 1e-9 <= math.hypot(bx - MUG[0], by - MUG[1]) <= 1.6 + 1e-9


def test_base_pose_needs_line_of_sight():
    """A screen on the table hides the mug from the robot's side (-X). The nearest poses fail
    filter 3, so the answer comes from another side, and it can see the mug."""
    screen = np.vstack([_disk(1.85, y, 0.02, z) for y in np.arange(-0.25, 0.46, 0.03)
                        for z in np.arange(0.76, 1.4, 0.03)])
    grid = VoxelGrid.from_points(_room(np.random.default_rng(0), extra=[screen]), CUBE)
    cm = Costmap.from_grid(grid, robot_h=0.60)
    pose = solve_base_pose(MUG, cm, Arm(), robot_pose=(0.0, 0.0, 0.0), eye_h=EYE_H)
    assert pose is not None
    assert line_of_sight((pose[0], pose[1], EYE_H), MUG, grid)
    assert pose[0] > 1.85                                  # round the far side of the screen


def test_base_pose_needs_a_path(caplog):
    """The robot is fenced in: every pose that fits, reaches and sees fails filter 4."""
    fence = np.vstack([_cylinder(0.9 * math.cos(t), 0.9 * math.sin(t), 0.03, 0.0, 0.5)
                       for t in np.arange(0, 2 * np.pi, 0.05)])
    grid = VoxelGrid.from_points(_room(np.random.default_rng(0), extra=[fence]), CUBE)
    cm = Costmap.from_grid(grid, robot_h=0.60)
    with caplog.at_level(logging.INFO, logger="costmap"):
        assert solve_base_pose(MUG, cm, Arm(), robot_pose=(0.0, 0.0, 0.0), eye_h=EYE_H) is None
    assert "'path':" in caplog.text


def test_why_names_the_filter_for_the_executor(room):
    """docs/10 D20: "cannot apply hunk: nowhere to stand" must say WHICH filter. Every filter
    is reported, in docs/24 A2's order, zeros included; the counts cover every candidate."""
    from costmap import BASE_FILTERS, solve_base_pose_why
    pose, why = solve_base_pose_why(MUG, Costmap.from_grid(room, robot_h=1.0), Arm(), (0.0, 0.0, 0.0), EYE_H)
    assert pose is None and tuple(why) == BASE_FILTERS
    assert why == {"base_fits": 180, "ik": 0, "line_of_sight": 0, "path": 0}      # the costmap, not IK
    pose, why = solve_base_pose_why(MUG, Costmap.from_grid(room, robot_h=0.60), Arm(), (0.0, 0.0, 0.0), EYE_H)
    assert pose == solve_base_pose(MUG, Costmap.from_grid(room, robot_h=0.60), Arm(), (0.0, 0.0, 0.0), EYE_H)
    assert pose is not None and sum(why.values()) < 180


def test_why_for_a_fenced_robot_is_path():
    from costmap import solve_base_pose_why
    fence = np.vstack([_cylinder(0.9 * math.cos(t), 0.9 * math.sin(t), 0.03, 0.0, 0.5)
                       for t in np.arange(0, 2 * np.pi, 0.05)])
    cm = Costmap.from_grid(VoxelGrid.from_points(_room(np.random.default_rng(0), extra=[fence]), CUBE), robot_h=0.6)
    pose, why = solve_base_pose_why(MUG, cm, Arm(), (0.0, 0.0, 0.0), EYE_H)
    assert pose is None and why["path"] > 0 and sum(why.values()) == 180


def test_viewpoint_why_has_its_own_filters(room):
    from costmap import VIEW_FILTERS, solve_viewpoint_why
    pose, why = solve_viewpoint_why(MUG, Costmap.from_grid(room, robot_h=0.60), (0.8, 0.0, EYE_H), (0.0, 0.0, 0.0), EYE_H)
    assert pose is not None and tuple(why) == VIEW_FILTERS and why["same_angle"] > 0


def test_open_floor_after_a_round_trip_through_the_documents():
    """The costmap path that never reaches Elasticsearch, and the one that did.

    `from_points` takes a voxel's mid height from its own points; `from_docs` rebuilds it as
    the midpoint of the stored 10th/90th percentiles, so the round trip is NOT lossless for
    the body band:
      - a fixture floor drawn one cell thick (0 -> 0.0625) reads back at z_mid 0.0625 and
        puts 4,608 cells in the band -- the whole drivable room becomes an obstacle. That
        shipped in a fixture on 2026-09-19 and was caught before it drove anything.
        A drawn floor of thickness t reads back at ~t/2, so it must stay under 2 * Z_FLOOR;
        scene_gen's FLOOR_THICK = 0.02 lands at 0.01, a 2x margin.
      - a MEASURED floor (1 cm noise) is free on the direct path but leaves a couple of
        cells in the band after the round trip, because the midpoint of two percentiles is
        not the median. Harmless at this size, and the reason it is not zero is worth
        knowing: it would take a stored median (z_med on the doc) to make the two paths
        agree. Pinned loosely so the honest number can move; if it reaches zero, someone
        fixed that, and this comment should go.
    """
    import obs

    x, y = np.mgrid[-1.0:3.5:0.02, -2.0:2.0:0.02]

    def slab(thick, n=4):
        return np.column_stack([np.repeat(x.ravel(), n), np.repeat(y.ravel(), n),
                                np.tile(np.linspace(0, thick, n), x.size)])

    def obstacles(points):
        g = VoxelGrid.from_points(points, CUBE)
        with obs.span("test"):
            docs = voxelize.voxel_docs(g, "abc123", None, "main", "2026-09-19T05:00:00Z")
        return (Costmap.from_grid(g, robot_h=0.6).obstacle.sum(),
                Costmap.from_grid(VoxelGrid.from_docs(docs, CUBE), robot_h=0.6).obstacle.sum())

    leaf = CUBE[1] / 2 ** CUBE[2]
    assert obstacles(slab(leaf))[1] > 1000                       # one cell thick: the room walls itself off
    assert obstacles(slab(costmap.Z_FLOOR)) == (0, 0)            # FLOOR_THICK: free on both paths

    direct, round_trip = obstacles(_floor(np.random.default_rng(0), sigma=0.01))
    assert direct == 0                                           # 18 m^2 of measured floor, all free
    assert round_trip < 5                                        # ...and near-free once it has been a document
