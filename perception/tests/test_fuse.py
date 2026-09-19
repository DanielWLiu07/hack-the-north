"""fuse.py: FRAME (X fwd, Y left, Z up, floor at z=0) and UNITS (metres in, metres out).

The camera-frame input is derived here from first principles -- camera axes as world
vectors, dot products -- not by inverting fuse.py, so a shared mistake can't cancel out.
"""
import math
import re
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fuse  # noqa: E402
from fuse import Mount  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TABLE_Z, WALL_X = 0.75, 4.0


def _grid(a0, a1, b0, b1, step=0.02):
    a, b = np.meshgrid(np.arange(a0, a1, step), np.arange(b0, b1, step))
    return a.ravel(), b.ravel()


def _room():
    """World frame, metres: floor, the wall ahead, a table top."""
    x, y = _grid(0.2, WALL_X, -2.5, 2.5)
    floor = np.column_stack([x, y, np.zeros_like(x)])
    y, z = _grid(-2.5, 2.5, 0.0, 2.0)
    wall = np.column_stack([np.full_like(y, WALL_X), y, z])
    x, y = _grid(1.0, 1.6, -0.4, 0.4)
    table = np.column_stack([x, y, np.full_like(x, TABLE_Z)])
    return floor, wall, table


def _seen_by(world, pitch_down, height, yaw_left=0.0, fov_deg=70):
    """World points -> OpenCV camera coords (X right, Y down, Z fwd) of a camera at
    (0, 0, height), tilted down and turned left by the given degrees."""
    p, y = np.radians(pitch_down), np.radians(yaw_left)
    fwd = np.array([np.cos(p) * np.cos(y), np.cos(p) * np.sin(y), -np.sin(p)])
    right = np.array([np.sin(y), -np.cos(y), 0.0])
    down = np.cross(fwd, right)                       # right x down = fwd: right-handed
    rel = world - [0.0, 0.0, height]
    cam = np.column_stack([rel @ right, rel @ down, rel @ fwd])
    ang = np.degrees(np.arccos(np.clip(cam[:, 2] / np.linalg.norm(cam, axis=1), -1, 1)))
    keep = (cam[:, 2] > 0.2) & (ang < fov_deg)
    return cam[keep], world[keep]


# ── FRAME: the axes mean what docs/20 Part 2 says ────────────────────────────

def test_axes_level_camera():
    """Level camera, lens at 0 height: forward -> +X, right -> -Y (Y is LEFT), down -> -Z."""
    m = Mount(pitch_down_deg=0, height_m=0)
    cam = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    np.testing.assert_allclose(fuse.rect_to_world(cam, m), [[1, 0, 0], [0, -1, 0], [0, 0, -1]], atol=1e-12)


def test_mount_pitch_height_yaw_mean_what_they_say():
    # straight ahead of a camera pitched fully down from 1 m: the floor right under it
    np.testing.assert_allclose(fuse.rect_to_world(np.array([0, 0, 1.0]), Mount(90, 1.0)), [0, 0, 0], atol=1e-12)
    # 1 m straight ahead of a camera turned 90 deg left: +Y
    np.testing.assert_allclose(fuse.rect_to_world(np.array([0, 0, 1.0]), Mount(0, 0.5, 90)), [0, 1, 0.5], atol=1e-12)


@pytest.mark.parametrize("pitch,height,yaw", [(36, 1.5, 0), (25, 0.45, 0), (30, 0.6, 120), (30, 0.6, -120)])
def test_round_trip_exact_in_metres(pitch, height, yaw):
    """Every point lands exactly where it is in the world: frame AND units (metres, not a
    scaled or relabelled copy)."""
    world = np.vstack(_room())
    cam, truth = _seen_by(world, pitch, height, yaw)
    assert len(cam) > 1000
    np.testing.assert_allclose(fuse.rect_to_world(cam, Mount(pitch, height, yaw)), truth, atol=1e-9)


def test_agrees_with_bb_example_depth():
    """BB's example: R_x(-36) + [0,-1.5,0], then its viewer map (x, z, -y) = X right, Y fwd,
    Z up. Ours is the same room with X fwd / Y left: (viewer_y, -viewer_x, viewer_z)."""
    cam, _ = _seen_by(np.vstack(_room()), 36, 1.5)
    rob = Rotation.from_euler("x", -36.0, degrees=True).apply(cam) + [0.0, -1.5, 0.0]
    viewer = np.column_stack([rob[:, 0], rob[:, 2], -rob[:, 1]])
    np.testing.assert_allclose(fuse.rect_to_world(cam, Mount(36, 1.5)),
                               np.column_stack([viewer[:, 1], -viewer[:, 0], viewer[:, 2]]), atol=1e-9)


def test_keeps_depth_shape_and_nans():
    """(H,W,3) in, (H,W,3) out, NaN stays NaN: masks from left_rect still select points."""
    xyz = np.random.default_rng(0).uniform(0.5, 3, (27, 48, 3)).astype(np.float32)
    xyz[5:9, 10:20] = np.nan
    out = fuse.rect_to_world(xyz, Mount(30, 0.6))
    assert out.shape == xyz.shape
    assert np.array_equal(np.isnan(out).any(axis=2), np.isnan(xyz).any(axis=2))


def test_cam_to_world_axes_defined_once_called_once():
    """docs/20: `cam_to_world_axes` appears exactly once. Zero calls: sideways clouds.
    Two: one of them is wrong. Tests are exempt, they exist to poke at it."""
    defs, calls = [], []
    for f in REPO.rglob("*.py"):
        if {".venv", "tests", "__pycache__"} & set(f.relative_to(REPO).parts):
            continue
        for line in f.read_text(errors="ignore").splitlines():
            if re.search(r"\bdef cam_to_world_axes\(", line):
                defs.append(f)
            elif re.search(r"\bcam_to_world_axes\(", line):
                calls.append(f)
    fuse_py = REPO / "perception" / "fuse.py"
    assert defs == [fuse_py] and calls == [fuse_py], (defs, calls)


# ── the floor assertion: catches an axis error in ONE capture ────────────────

def _noisy_scene(rng, pitch=30, height=0.6, yaw=0.0):
    cam, truth = _seen_by(np.vstack(_room()), pitch, height, yaw)
    cam = cam + rng.normal(0, 0.005, cam.shape)                   # 5 mm stereo noise
    bad = rng.choice(len(cam), len(cam) // 200, replace=False)    # 0.5% mismatches...
    cam[bad] *= rng.uniform(0.5, 2.0, (len(bad), 1))              # ...anywhere along the ray
    return cam, truth


def test_floor_at_zero_and_wall_perpendicular():
    rng = np.random.default_rng(1)
    cam, truth = _noisy_scene(rng)
    xyz = cam.reshape(1, -1, 3)
    valid = np.ones(xyz.shape[:2], bool)
    world, cloud = fuse.fuse([(xyz, valid, Mount(30, 0.6))])
    assert world[0].shape == xyz.shape and cloud.shape == (len(cam), 3)
    assert abs(fuse.assert_floor(cloud)) < 0.01
    on_wall = np.abs(truth[:, 0] - WALL_X) < 1e-9
    w = cloud[on_wall]
    normal = np.linalg.svd(w - w.mean(axis=0), full_matrices=False)[2][-1]
    assert abs(normal[0]) > np.cos(np.radians(2)), normal          # facing us along X
    assert abs(np.median(w[:, 0]) - WALL_X) < 0.01                   # 4 m ahead, in metres


def _relabel(p):   # the mapping written out again, to build the "applied twice" mistake
    return np.stack([p[..., 2], -p[..., 0], -p[..., 1]], axis=-1)


@pytest.mark.parametrize("mistake", ["axes missing", "axes twice", "Z flipped", "pitch sign", "height +20 cm",
                                     "mm not m"])
def test_floor_assertion_catches(mistake):
    cam, _ = _noisy_scene(np.random.default_rng(2))
    m = Mount(30, 0.6)
    R, t = m.matrix()
    cloud = {
        "axes missing": lambda: cam @ R.T + t,
        "axes twice": lambda: _relabel(fuse.rect_to_world(cam, m)),
        "Z flipped": lambda: fuse.rect_to_world(cam, m) * [1, 1, -1] + [0, 0, 0],
        "pitch sign": lambda: fuse.rect_to_world(cam, Mount(-30, 0.6)),
        "height +20 cm": lambda: fuse.rect_to_world(cam, Mount(30, 0.8)),
        "mm not m": lambda: fuse.rect_to_world(cam * 1000, m),
    }[mistake]()
    with pytest.raises(AssertionError):
        fuse.assert_floor(cloud)


def test_floor_assertion_is_deterministic():
    cam, _ = _noisy_scene(np.random.default_rng(3))
    cloud = fuse.rect_to_world(cam, Mount(30, 0.6))
    assert fuse.assert_floor(cloud) == fuse.assert_floor(cloud.copy())


def test_depth_output_fuses_to_the_floor_in_metres(tmp_path):
    """The chain, frame AND units: depth.py's F_rect metres -> fuse.py's Z-up world. A camera
    1 m up looking straight down at the synthetic textured plane: that plane is the floor."""
    import depth
    import test_depth as td
    td._write_calib(tmp_path / "c.yaml")
    xyz, valid, _ = depth.StereoDepth(str(tmp_path / "c.yaml")).compute(*td._pair(td._texture(), 1000.0))
    world, cloud = fuse.fuse([(xyz, valid, Mount(90, 1.0))])
    assert abs(fuse.assert_floor(cloud)) < 0.03
    assert abs(np.median(cloud[:, 2])) < 0.03                 # metres: mm would put it at -999
    assert np.ptp(cloud[:, 0]) > 1.0 and np.ptp(cloud[:, 1]) > 1.0   # spread over the floor, not a wall


# ── the robot pose: BB's odometry {x, z, yaw} -> F_world (docs/20 Fact 3, for poses) ──

def _bb_step(pose, dl, dr, wheel_base=0.425):
    """examples/example_localization.py's DifferentialDriveOdometry.update, verbatim maths:
    the reference for what BB's x, z and yaw MEAN."""
    x, z, yaw = pose["x"], pose["z"], pose["yaw"]
    dc, dtheta = 0.5 * (dl + dr), (dr - dl) / wheel_base
    half = yaw + 0.5 * dtheta
    return {"x": x + dc * math.cos(half), "z": z + dc * math.sin(half), "yaw": (yaw + dtheta) % (2 * math.pi)}


def test_bb_left_turn_is_world_left():
    """Right wheel faster -> BB's yaw and z grow -> in F_world that is +y (LEFT) and a
    counter-clockwise yaw. Driving straight -> +x."""
    pose = {"x": 0.0, "z": 0.0, "yaw": 0.0}
    assert fuse.odom_to_world(_bb_step(pose, 0.5, 0.5)) == pytest.approx((0.5, 0.0, 0.0))
    for _ in range(40):
        pose = _bb_step(pose, 0.02, 0.05)                 # arcing left
    x, y, yaw = fuse.odom_to_world(pose)
    assert y > 0.1 and 0 < yaw <= math.pi                   # left of the start, turned CCW


def test_drive_target_round_trips():
    for x, y, yaw in [(1.8, 0.4, 1.57), (-0.5, -1.2, -2.0), (0.0, 0.0, math.pi)]:
        t = fuse.world_to_odom(x, y, yaw)
        assert set(t) == {"x", "z", "yaw"} and 0 <= t["yaw"] < 2 * math.pi   # docs/16 §2.3's shape
        assert fuse.odom_to_world(t) == pytest.approx((x, y, yaw))


def test_robot_pose_moves_the_cloud():
    """Robot at (1, 2) facing +y (yaw pi/2): a level camera's forward point 1 m out is 1 m
    further along +y, in metres; height untouched."""
    m = Mount(pitch_down_deg=0, height_m=0.8)
    p = fuse.rect_to_world(np.array([0.0, 0.0, 1.0]), m, robot_pose=(1.0, 2.0, math.pi / 2))
    np.testing.assert_allclose(p, [1.0, 3.0, 0.8], atol=1e-12)
    cam, truth = _seen_by(np.vstack(_room()), 30, 0.6)
    yaw = 0.7
    moved = fuse.rect_to_world(cam, Mount(30, 0.6), robot_pose=(0.3, -0.2, yaw))
    rot = np.array([[math.cos(yaw), -math.sin(yaw)], [math.sin(yaw), math.cos(yaw)]])
    np.testing.assert_allclose(moved[:, :2], truth[:, :2] @ rot.T + [0.3, -0.2], atol=1e-9)
    np.testing.assert_allclose(moved[:, 2], truth[:, 2], atol=1e-9)
    assert abs(fuse.assert_floor(moved)) < 0.01                                # still Z-up, floor at 0
