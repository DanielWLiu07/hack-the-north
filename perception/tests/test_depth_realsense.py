"""depth.RealSenseDepth: Sarah's D415/D435 captures (docs/27) through StereoDepth's contract.
FRAME: X right, Y down, Z fwd, pixel-aligned with the colour image. UNITS: metres -- and the
two traps that sit side by side in one capture folder, asserted in both directions."""
import math
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import depth  # noqa: E402  (puts the repo root on sys.path)
import fuse  # noqa: E402
from depth import RealSenseDepth, RealSenseFrame  # noqa: E402

W, H = 640, 480
FX = FY = 615.0                 # D415-ish colour intrinsics at 640x480
CX, CY = 320.0, 240.0


def _rays():
    u, v = np.meshgrid(np.arange(W, dtype=np.float64), np.arange(H, dtype=np.float64))
    return u, v, (u - CX) / FX, (v - CY) / FY


def _capture(tmp_path, z_mm, name="d415", points_scale=1.0, raw=None):
    """What data_collect.py writes: <cam>_color.png, <cam>_depth_raw.npy (uint16 mm) and
    <cam>_pointcloud.npy -- pyrealsense2's vertices, (H*W, 3) float32 METRES, zeros = no depth."""
    _, _, x, y = _rays()
    z = z_mm / 1000.0
    pts = np.stack([x * z, y * z, z], -1).reshape(-1, 3).astype(np.float32) * points_scale
    color = (np.random.default_rng(0).integers(0, 256, (H, W, 3))).astype(np.uint8)
    d = tmp_path / "capture_0000"
    d.mkdir(exist_ok=True)
    cv2.imwrite(str(d / f"{name}_color.png"), color)
    np.save(d / f"{name}_depth_raw.npy", z_mm.astype(np.uint16) if raw is None else raw)
    np.save(d / f"{name}_pointcloud.npy", pts)
    return d, color


def _wall(mm=1000):
    z = np.full((H, W), float(mm))
    z[100:140, 200:260] = 0                              # a hole: no IR return
    return z


def test_pixel_uv_is_x_right_y_down_z_forward_in_metres(tmp_path):
    d, color = _capture(tmp_path, _wall())
    xyz, valid, image = RealSenseDepth("d415").observe(RealSenseFrame.load(d, "D415"))
    assert xyz.shape == (H, W, 3) and xyz.dtype == np.float32 and valid.shape == (H, W)
    assert np.array_equal(image, color)                  # BGR, untouched
    for u, v in [(0, 0), (639, 0), (320, 240), (100, 400)]:
        np.testing.assert_allclose(xyz[v, u], [(u - CX) / FX, (v - CY) / FY, 1.0], atol=1e-6)
    assert xyz[0, 639, 0] > 0 and xyz[479, 0, 1] > 0     # right is +X, down is +Y
    assert not valid[100:140, 200:260].any() and np.isnan(xyz[100:140, 200:260]).all()
    assert valid.sum() == H * W - 40 * 60


def test_trap_1_the_point_cloud_is_already_metres(tmp_path):
    """Divided by 1000 (someone 'fixed' it) or left in mm: both refuse to load -- by the metres
    assertion alone (no depth_raw beside it), and by the cross-check too when depth_raw is there."""
    for scale in (1e-3, 1e3):
        d, _ = _capture(tmp_path, _wall(), points_scale=scale)
        with pytest.raises(AssertionError, match="disagree|is not metres"):
            RealSenseDepth().observe(RealSenseFrame.load(d, "d415"))
        (d / "d415_depth_raw.npy").unlink()
        with pytest.raises(AssertionError, match="median range .* is not metres"):
            RealSenseDepth().observe(RealSenseFrame.load(d, "d415"))


def test_trap_2_depth_raw_is_millimetres(tmp_path):
    """depth_raw at another depth scale (0.1 mm units, the D400 high-accuracy preset) disagrees
    with the point cloud by 10x; depth_raw saved as float metres isn't uint16."""
    d, _ = _capture(tmp_path, _wall(), raw=(_wall() * 10).astype(np.uint16))
    with pytest.raises(AssertionError, match="disagree"):
        RealSenseDepth().observe(RealSenseFrame.load(d, "d415"))
    d, _ = _capture(tmp_path, _wall(), raw=(_wall() / 1000).astype(np.float32))
    with pytest.raises(ValueError, match="uint16"):
        RealSenseDepth().observe(RealSenseFrame.load(d, "d415"))


def test_a_filtered_point_cloud_is_refused(tmp_path):
    d, _ = _capture(tmp_path, _wall())
    pts = np.load(d / "d415_pointcloud.npy")
    np.save(d / "d415_pointcloud.npy", pts[pts[:, 2] > 0])            # drops the per-pixel alignment
    with pytest.raises(ValueError, match="one vertex per"):
        RealSenseDepth().observe(RealSenseFrame.load(d, "d415"))


def test_half_fov_is_measured_from_the_points(tmp_path):
    src = RealSenseDepth()
    with pytest.raises(RuntimeError):
        src.half_fov_deg()
    d, _ = _capture(tmp_path, _wall())
    src.observe(RealSenseFrame.load(d, "d415"))
    assert src.half_fov_deg() == pytest.approx(math.degrees(math.atan(CY / FY)), abs=0.5)   # ~21 deg


def test_a_floor_seen_by_a_mounted_realsense_lands_at_z0(tmp_path):
    """End to end through fuse: camera 1.2 m up, pitched 45 deg down, the world floor built from
    first principles (camera axes as world vectors), not by inverting fuse."""
    h, pitch = 1.2, math.radians(45)
    fwd = np.array([math.cos(pitch), 0.0, -math.sin(pitch)])
    right = np.array([0.0, -1.0, 0.0])
    down = np.cross(fwd, right)
    _, _, x, y = _rays()
    rays = x[..., None] * right + y[..., None] * down + fwd               # world directions per pixel
    t = np.where(rays[..., 2] < 0, h / -rays[..., 2], np.inf)             # hit the floor z = 0
    z_cam = np.where(np.isfinite(t) & (t < 4.5), t, 0.0)                   # camera-frame depth (fwd = 1)
    d, _ = _capture(tmp_path, np.round(z_cam * 1000.0))
    src = RealSenseDepth()
    xyz, valid, _ = src.observe(RealSenseFrame.load(d, "d415"))
    world, cloud = fuse.fuse([(xyz, valid, fuse.Mount(45, h))])
    assert abs(fuse.assert_floor(cloud)) < 0.002
    assert np.abs(cloud[:, 2]).max() < 0.002                              # every point ON the floor, metres


def test_the_capture_gate_takes_a_realsense_source(tmp_path):
    d, _ = _capture(tmp_path, _wall())
    src = RealSenseDepth("d415")
    out, ok = depth.depth_capture({"d415": RealSenseFrame.load(d, "d415")}, {"d415": src}, 1.0, 0.01)
    assert ok and src.coverage(out["d415"][1]) == pytest.approx(1 - 40 * 60 / (H * W))
    assert not depth.depth_capture({"d415": RealSenseFrame.load(d, "d415")}, {"d415": src}, None, 0.01)[1]
