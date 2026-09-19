"""raycast.line_of_sight: exact 3-D DDA on the voxel grid, in F_world metres."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from raycast import first_hit, line_of_sight  # noqa: E402
from voxelize import VoxelGrid  # noqa: E402

CUBE = ((-4.0, -4.0, 0.0), 8.0, 7)


def _slab(x0, x1, y0, y1, z0, z1, step=0.02):
    g = np.mgrid[x0:x1 + 1e-9:step, y0:y1 + 1e-9:step, z0:z1 + 1e-9:step].reshape(3, -1).T
    return g


def _grid(*parts):
    return VoxelGrid.from_points(np.vstack(parts), CUBE)


# ── FRAME and UNITS ──────────────────────────────────────────────────────────

def test_wall_blocks_along_x_not_along_y():
    """A wall at x = 1 m, y in [-1, 1], z in [0, 1.5]: axes and metres, not indices."""
    g = _grid(_slab(1.0, 1.02, -1.0, 1.0, 0.0, 1.5))
    assert not line_of_sight((0, 0, 1.0), (2, 0, 1.0), g)          # through it
    assert line_of_sight((0, 0, 1.8), (2, 0, 1.8), g)              # over it
    assert line_of_sight((0, 1.3, 1.0), (2, 1.3, 1.0), g)          # past its +Y end
    assert line_of_sight((0.5, -2, 1.0), (0.5, 2, 1.0), g)         # parallel, in front of it
    assert not line_of_sight((2, 0, 1.0), (0, 0, 1.0), g)          # symmetric


def test_blocking_voxel_is_where_the_occluder_is():
    g = _grid(_slab(1.0, 1.02, -0.2, 0.2, 0.7, 0.9))
    hit = first_hit((0, 0, 0.8), (2, 0, 0.8), g)
    assert hit is not None
    centre = g.origin + (np.array(hit) + 0.5) * g.leaf
    assert np.allclose(centre, [1.0, 0.0, 0.8], atol=g.leaf)


# ── the two callers' situations ──────────────────────────────────────────────

def _table_scene(with_laptop):
    parts = [_slab(1.5, 2.5, -0.5, 0.5, 0.74, 0.76),                  # table top at 0.75
             _slab(1.96, 2.04, -0.04, 0.04, 0.76, 0.86)]              # the mug itself
    if with_laptop:
        parts.append(_slab(1.6, 1.62, -0.2, 0.2, 0.76, 1.0))          # laptop screen, between
    return _grid(*parts)


def test_occluded_vs_removed():
    """docs/20 Part 4: the camera at 1.0 m looks at where the mug was. Something in between ->
    UNOBSERVED (carry it forward). Nothing -> it's REMOVED. The mug's own voxels and the table
    under it must not count as an occluder."""
    cam, mug = (0.0, 0.0, 1.0), (2.0, 0.0, 0.81)
    assert line_of_sight(cam, mug, _table_scene(with_laptop=False), ignore_end=0.12)
    assert not line_of_sight(cam, mug, _table_scene(with_laptop=True), ignore_end=0.12)


def test_target_does_not_hide_itself():
    g = _grid(_slab(1.96, 2.04, -0.04, 0.04, 0.76, 0.86))
    assert line_of_sight((0, 0, 1.0), (2.0, 0.0, 0.81), g)                  # default: one voxel diagonal
    assert not line_of_sight((0, 0, 1.0), (2.0, 0.0, 0.81), g, ignore_end=0.0)


def test_outside_the_cube_is_free():
    g = _grid(_slab(1.0, 1.02, -1.0, 1.0, 0.0, 1.5))
    assert not line_of_sight((-6, 0, 1.0), (6, 0, 1.0), g)    # starts and ends outside, wall inside
    assert line_of_sight((-6, 0, 1.0), (-4.5, 0, 1.0), g)


# ── exactness: DDA visits every voxel the segment passes through ─────────────

def _slab_enter(a, b, lo, hi):
    """Parametric t in [0,1] where segment a->b enters the box [lo, hi], or None."""
    d = b - a
    t0, t1 = 0.0, 1.0
    for ax in range(3):
        if abs(d[ax]) < 1e-15:
            if not lo[ax] <= a[ax] <= hi[ax]:
                return None
            continue
        u, w = sorted(((lo[ax] - a[ax]) / d[ax], (hi[ax] - a[ax]) / d[ax]))
        t0, t1 = max(t0, u), min(t1, w)
    return t0 if t0 <= t1 else None


@pytest.mark.parametrize("seed", range(5))
def test_dda_is_exact(seed):
    """Against brute force: the first occupied voxel the segment touches (slab intersection
    over every occupied voxel) is exactly what DDA returns."""
    rng = np.random.default_rng(seed)
    g = VoxelGrid.from_points(np.repeat(rng.uniform([-1, -1, 0], [1, 1, 1.5], (60, 3)), 3, axis=0), CUBE)
    lo = g.origin + g.ijk * g.leaf
    for _ in range(200):
        a, b = rng.uniform([-1.2, -1.2, 0.0], [1.2, 1.2, 1.5], (2, 3))
        ts = [(t, tuple(c)) for c, l in zip(g.ijk, lo) if (t := _slab_enter(a, b, l, l + g.leaf)) is not None]
        want = min(ts)[1] if ts else None
        got = first_hit(a, b, g, ignore_end=1e-9)
        if want is None or got is None:
            assert got == want
        else:   # ties at shared faces/edges may resolve to either neighbour; both are hit at the same t
            t_got = _slab_enter(a, b, g.origin + np.array(got) * g.leaf, g.origin + (np.array(got) + 1) * g.leaf)
            assert t_got is not None and abs(t_got - min(ts)[0]) < 1e-9


def _mug():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from roomctl.state import Extents, ObjectRecord, Pose
    return ObjectRecord("mug_a1b2", "mug", "desk", Pose(2.0, 0.0, 0.81, 0), Extents(0.08, 0.08, 0.10),
                        "#2b4c7e", "2026-09-18T21:00:00Z")


def test_occlusion_check_counts_only_cameras_that_could_see_it():
    """associate's callback. Per object, only cameras whose view cone and depth range hold
    its last pose count. A camera facing AWAY has a clear line and no view: it must not turn
    a hidden object into a deleted file."""
    from raycast import Camera, occlusion_check
    mug, g = _mug(), _table_scene(with_laptop=True)             # the laptop hides it from -X
    front = Camera((0.0, 0.0, 1.0), (1.0, 0.0, -0.1), 40)       # looks at it, through the laptop
    side = Camera((2.0, 2.0, 1.0), (0.0, -1.0, -0.1), 40)       # looks at it, clear
    away = Camera((2.0, 2.0, 1.0), (0.0, 1.0, 0.0), 40)         # same spot, facing away: clear line, no view
    far = Camera((2.0, 7.5, 1.0), (0.0, -1.0, 0.0), 40, max_range=5.0)   # beyond depth range
    assert occlusion_check([front], g)(mug)                     # the only viewer is blocked: UNOBSERVED
    assert not occlusion_check([front, side], g)(mug)           # a clear view saw nothing: REMOVED
    assert occlusion_check([front, away], g)(mug)               # `away` doesn't count
    assert occlusion_check([away, far], g)(mug)                 # nobody could see it: never REMOVED
    assert occlusion_check([], g)(mug)


def test_occlusion_check_refuses_bare_positions():
    from raycast import occlusion_check
    with pytest.raises(TypeError, match="Camera"):
        occlusion_check([(0.0, 0.0, 1.0)], _table_scene(with_laptop=False))


def test_camera_from_mount_is_in_the_world_frame():
    """Position and axis come from fuse.rect_to_world: metres, X fwd / Y left / Z up."""
    import math
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fuse import Mount
    from raycast import Camera
    c = Camera.from_mount(Mount(pitch_down_deg=30, height_m=0.6, yaw_left_deg=90), 40, robot_pose=(1.0, 0.0, 0.0))
    np.testing.assert_allclose(c.position, [1.0, 0.0, 0.6], atol=1e-12)
    np.testing.assert_allclose(c.forward, [0.0, math.cos(math.radians(30)), -math.sin(math.radians(30))], atol=1e-12)
    assert c.covers((1.0, 1.0, 0.6 - math.tan(math.radians(30)))) and not c.covers((1.0, -1.0, 0.6))
