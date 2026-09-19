"""difference.py: what appeared or went away between two captures of one room.

Two kinds of test. Ray-traced (always run): a floor and walls seen by the head camera's
mount, with stereo-like noise that is independent between captures -- so "untouched"
really means two different noisy looks at the same room. Real (skipped without the
recordings): LINK's hallway pair cap_0004 / cap_0005, untouched 5 s apart, which must
come back EMPTY, and the same pair with a box ray-cast onto its measured floor.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import difference as dm  # noqa: E402
from fuse import Mount  # noqa: E402

W, H, F, CX, CY = 480, 360, 123.0, 240.0, 180.0      # the real head stereo at half scale
MOUNT = Mount(pitch_down_deg=33.0, height_m=1.55)
WALLS = [((2.5, -9, -9), (9, 9, 9)), ((-9, 1.2, -9), (9, 9, 9)), ((-9, -9, -9), (9, -1.2, 9))]
BOX = ((0.9, -0.1, 0.0), (1.1, 0.1, 0.2))             # 20 cm, on the floor 1 m ahead
RECORDINGS = Path(os.getenv("RECORDINGS_DIR", "~/.cache/gitspace/recordings")).expanduser()


def rays(mount=MOUNT, pose=(0.0, 0.0, 0.0)):
    """Per pixel: F_rect direction with z = 1, and the same ray in F_world (origin, dir)."""
    v, u = np.mgrid[:H, :W]
    d = np.stack([(u - CX) / F, (v - CY) / F, np.ones((H, W))], -1)
    rot, t = dm._world_from_rect(mount, pose)
    return d, t, d @ rot.T


def render(boxes=(), seed=0, pose=(0.0, 0.0, 0.0), noise=True, holes=()):
    """A View of floor + walls + boxes. Depth noise along the ray, so x/z stays exact.

    The noise is the real pair's, measured inside the trusted cone: between two captures
    |dz| has median 2.4 cm at 1.4-2.0 m and adjacent pixels' dz correlate at 0.94 -- SGBM
    error is a smooth local warp, not per-pixel. So: a smooth field growing as z^2, a
    little per-pixel jitter, lone mismatches, and scattered pixels with no depth."""
    d, o, dw = rays(MOUNT, pose)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(dw[..., 2] < 0, -o[2] / dw[..., 2], np.inf)          # the floor, z = 0
        for lo, hi in [*WALLS, *boxes]:
            t1, t2 = (np.asarray(lo) - o) / dw, (np.asarray(hi) - o) / dw
            tn, tf = np.nanmax(np.minimum(t1, t2), -1), np.nanmin(np.maximum(t1, t2), -1)
            t = np.where((tn <= tf) & (tn > 0) & (tn < t), tn, t)
    valid = np.isfinite(t) & (t < 5.0)
    if noise:
        rng = np.random.default_rng(seed)
        warp = gaussian_filter(rng.normal(0, 1, t.shape), 2)
        warp /= warp.std()
        t = t + 0.0090 * t ** 2 * warp + rng.normal(0, 0.003, t.shape)
        speck = rng.random(t.shape) < 0.003                                # lone SGBM mismatches
        t = np.where(speck, t * rng.uniform(0.6, 0.85, t.shape), t)
        valid &= rng.random(t.shape) > 0.10                                # scattered no-match pixels
    for r0, r1, c0, c1 in holes:
        valid[r0:r1, c0:c1] = False
    xyz = np.where(valid[..., None], d * t[..., None], np.nan)
    return dm.View(xyz, valid, MOUNT, pose, np.full((H, W, 3), 128, np.uint8))


def centres(insts):
    """Box centres, as associate matches them: the point centroid leans toward the camera,
    since the face it looks at has more pixels than the one it doesn't."""
    return [tuple(i.box()[0][:2]) for i in insts]


def at(x, y):
    return pytest.approx((x, y), abs=0.05)


def test_intrinsics_are_read_off_the_points():
    f, cx, cy = dm.intrinsics(render().xyz, render().valid)
    assert (f, cx, cy) == pytest.approx((F, CX, CY), abs=1e-6)


def test_untouched_room_two_noisy_looks_is_empty():
    ch = dm.difference(render(seed=1), render(seed=2))
    assert (ch.appeared, ch.gone) == ([], [])


def test_a_box_that_appears_is_one_instance_where_it_stands():
    ch = dm.difference(render(seed=1), render([BOX], seed=2))
    assert len(ch.appeared) == 1 and ch.gone == []
    box = ch.appeared[0]
    assert centres([box]) == [at(1.0, 0.0)]
    assert box.source == "difference" and box.label == "unknown" and box.camera == "cam0"
    assert box.mask.shape == (H, W) and box.mask.sum() == len(box.points)
    _, ext, _ = box.box()
    assert sorted(ext[:2]) == pytest.approx([0.2, 0.2], abs=0.05)


def test_the_box_grows_down_its_front_face_not_just_its_lid():
    """Seen from above, the front face's depth change runs from 0 at the floor to 24 cm at
    the lid; only the lid clears tau. Growth must bring the face in, or the instance is
    1 cm tall and associate's extent gate calls a known box a new one."""
    box, = dm.difference(render(seed=1), render([BOX], seed=2)).appeared
    _, ext, _ = box.box()
    assert ext[2] > 0.12
    assert (box.points[:, 2] < 0.12).sum() > 20


def test_a_box_that_leaves_is_gone_not_appeared():
    ch = dm.difference(render([BOX], seed=1), render(seed=2))
    assert ch.appeared == [] and centres(ch.gone) == [at(1.0, 0.0)]


def test_a_box_that_moves_is_one_gone_and_one_appeared():
    moved = ((0.9, 0.3, 0.0), (1.1, 0.5, 0.2))
    ch = dm.difference(render([BOX], seed=1), render([moved], seed=2))
    assert centres(ch.appeared) == [at(1.0, 0.4)] and centres(ch.gone) == [at(1.0, 0.0)]


def test_a_box_in_both_is_no_change():
    ch = dm.difference(render([BOX], seed=1), render([BOX], seed=2))
    assert (ch.appeared, ch.gone) == ([], [])


def test_a_small_patch_of_bad_depth_is_not_an_object():
    """A 5 x 5 px mismatch at 1.8 m covers ~50 cm^2, under MIN_AREA_M2."""
    cur = render(seed=2)
    cur.xyz[240:245, 240:245] *= 0.6
    ch = dm.difference(render(seed=1), cur)
    assert ch.appeared == []


def test_nothing_is_reported_at_the_rim_of_the_view():
    """The measured noise lives beyond FOV_DEG off axis; a real change there is not seen
    either, by design: the robot turns to look."""
    cur = render(seed=2)
    cur.xyz[150:210, 0:40] *= 0.5                   # a big patch, 55+ deg off axis
    assert dm.difference(render(seed=1), cur).appeared == []


def test_no_baseline_depth_is_no_evidence():
    """Where the baseline saw nothing, nobody knows the space was empty: no change."""
    d, o, dw = rays()
    # the box's pixels in the current view, blanked in the baseline
    cur = render([BOX], seed=2, noise=False)
    ref = render(seed=1, noise=False)
    changed = np.nan_to_num(ref.xyz[..., 2]) - np.nan_to_num(cur.xyz[..., 2]) > 0.01
    rows, cols = np.nonzero(changed)
    base = render(seed=1, holes=[(rows.min() - 4, rows.max() + 5, cols.min() - 4, cols.max() + 5)])
    assert dm.difference(base, render([BOX], seed=2)).appeared == []


def test_the_robot_moved_between_captures():
    """The baseline is rendered into the current view first, so a step forward and a turn
    are not a room full of changes -- and a new box is still found where it stands."""
    step = (0.3, 0.1, np.radians(8))
    assert dm.difference(render(seed=1), render(seed=2, pose=step)).appeared == []
    ch = dm.difference(render(seed=1), render([BOX], seed=2, pose=step))
    assert centres(ch.appeared) == [at(1.0, 0.0)] and ch.gone == []


def test_the_robots_own_arm_is_not_a_change(monkeypatch):
    arm = ((0.20, -0.05, 1.0), (0.32, 0.05, 1.25))    # in view, 16 deg off axis, inside SELF_M
    assert dm.difference(render(seed=1), render([arm], seed=2)).appeared == []
    monkeypatch.setattr(dm, "SELF_M", 0.0)            # it IS a change; SELF_M is what drops it
    assert len(dm.difference(render(seed=1), render([arm], seed=2)).appeared) == 1


# ---------------------------------------------------------------- the real hallway pair

def _real(cap):
    if not (RECORDINGS / cap / "capture.json").is_file():
        pytest.skip(f"no recording {RECORDINGS / cap}")
    return dm.load_view(RECORDINGS / cap)[1]


@pytest.fixture(scope="module")
def hallway():
    return _real("cap_0004"), _real("cap_0005")


def test_real_untouched_pair_is_empty(hallway):
    """LINK acceptance (a): cap_0004 vs cap_0005, robot still, nothing touched -> ZERO."""
    base, cur = hallway
    for a, b in ((base, cur), (cur, base)):
        ch = dm.difference(a, b)
        assert (ch.appeared, ch.gone) == ([], [])


def _floor_z(view, x, y, r=0.1):
    from fuse import rect_to_world
    p = rect_to_world(view.xyz[view.valid], view.mount, view.pose)
    m = (np.hypot(p[:, 0] - x, p[:, 1] - y) < r) & (p[:, 2] < 0.2)
    return float(np.median(p[m, 2]))


def paint(view, lo, hi):
    """Ray-cast an axis-aligned box into a real capture's depth."""
    h, w = view.valid.shape
    f, cx, cy = dm.intrinsics(view.xyz, view.valid)
    v, u = np.mgrid[:h, :w]
    d = np.stack([(u - cx) / f, (v - cy) / f, np.ones((h, w))], -1)
    rot, o = dm._world_from_rect(view.mount, view.pose)
    dw = d @ rot.T
    with np.errstate(divide="ignore", invalid="ignore"):
        t1, t2 = (np.asarray(lo) - o) / dw, (np.asarray(hi) - o) / dw
    tn, tf = np.nanmax(np.minimum(t1, t2), -1), np.nanmin(np.maximum(t1, t2), -1)
    z = np.where(view.valid, view.xyz[..., 2], np.inf)
    hit = (tn <= tf) & (tn > 0) & (tn < z)
    xyz, valid = view.xyz.copy(), view.valid | hit
    xyz[hit] = d[hit] * tn[hit, None]
    return dm.View(xyz, valid, view.mount, view.pose, view.image)


@pytest.mark.parametrize("x, y", [(1.0, 0.0), (1.0, 0.5), (1.4, -0.3)])
def test_real_pair_with_a_box_on_the_floor_is_one_instance(hallway, x, y):
    """Rehearsal for LINK acceptance (b), on the measured floor (which sits 3-9 cm above
    the nominal mount's z = 0 out here): a 20 cm box -> exactly one instance, there."""
    base, cur = hallway
    z0 = _floor_z(base, x, y)
    ch = dm.difference(base, paint(cur, (x - 0.1, y - 0.1, z0), (x + 0.1, y + 0.1, z0 + 0.2)))
    assert centres(ch.appeared) == [at(x, y)] and ch.gone == []
