"""Synthetic-rig tests for depth.py. No camera: a fisheye pair is ray-traced at a textured
plane 1 m in front of the left lens, then run through the real pipeline."""
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import depth  # noqa: E402

W, H = depth.CALIB_SIZE
PLANE_Z = 1000.0                          # mm, LEFT camera frame
MARKER = (150.0, 250.0, -50.0, 50.0)      # x0, x1, y0, y1 in mm on the plane
TEX_MM = 4.0                              # texel size
TEX_N = 3000                              # texels per side, centred on the optical axis

K_L = np.array([[500.0, 0, 640], [0, 500, 360], [0, 0, 1]])
K_R = np.array([[505.0, 0, 636], [0, 503, 362], [0, 0, 1]])
D_L = np.array([[0.02], [-0.005], [0.001], [0.0]])
D_R = np.array([[0.018], [-0.004], [0.0], [0.0]])
R_LR = cv2.Rodrigues(np.radians([0.3, 0.5, 0.1]))[0]     # x_right = R_LR x_left + T_LR
T_LR = np.array([[-60.0], [0.5], [0.3]])                 # mm: right eye 60 mm to the +X


def _write_calib(path, t_scale=1.0):
    """What setup/calibrate_stereo_camera.py writes, for this rig. t_scale fakes a
    checkerboard measured in the wrong unit."""
    T = T_LR * t_scale
    R1, R2, P1, P2, Q = cv2.fisheye.stereoRectify(K_L, D_L, K_R, D_R, (W, H), R_LR, T,
                                                  flags=cv2.CALIB_ZERO_DISPARITY)
    fs = cv2.FileStorage(str(path), cv2.FILE_STORAGE_WRITE)
    for k, v in dict(mtx_l=K_L, dist_l=D_L, mtx_r=K_R, dist_r=D_R, R=R_LR, T=T,
                     R1=R1, R2=R2, P1=P1, P2=P2, Q=Q).items():
        fs.write(k, np.asarray(v))
    fs.release()
    return R1, P1


def _texture():
    """Blurred noise; the marker is the same noise tinted red, so stereo can still match it."""
    rng = np.random.default_rng(0)
    g = cv2.GaussianBlur(rng.uniform(0, 255, (TEX_N, TEX_N)).astype(np.float32), (0, 0), 1.0)
    g = cv2.normalize(g, None, 0, 255, cv2.NORM_MINMAX)
    tex = np.dstack([g, g, g])
    x0, x1, y0, y1 = (int(c / TEX_MM + TEX_N / 2) for c in MARKER)
    m = g[y0:y1, x0:x1]
    tex[y0:y1, x0:x1] = np.dstack([0.2 * m, 0.2 * m, 128 + 0.5 * m])     # BGR
    return tex.astype(np.uint8)


def _render(K, D, R, C, tex, z):
    """Fisheye image of the plane at depth z mm (left frame). R rotates left-frame vectors
    into this camera's frame; C is this camera's centre in the left frame."""
    u, v = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    xy = cv2.fisheye.undistortPoints(np.stack([u, v], -1).reshape(-1, 1, 2), K, D).reshape(-1, 2)
    rays = np.column_stack([xy, np.ones(len(xy))]) @ R          # rows of R.T @ d: left frame
    t = (z - C[2]) / rays[:, 2]
    X, Y = C[0] + t * rays[:, 0], C[1] + t * rays[:, 1]
    mx = (X / TEX_MM + TEX_N / 2).reshape(H, W).astype(np.float32)
    my = (Y / TEX_MM + TEX_N / 2).reshape(H, W).astype(np.float32)
    return cv2.remap(tex, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def _pair(tex, z):
    return (_render(K_L, D_L, np.eye(3), np.zeros(3), tex, z),
            _render(K_R, D_R, R_LR, (-R_LR.T @ T_LR).ravel(), tex, z))


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    calib = tmp_path_factory.mktemp("rig") / "stereo_calibration_fisheye.yaml"
    R1, P1 = _write_calib(calib)
    tex = _texture()
    left, right = _pair(tex, PLANE_Z)
    sd = depth.StereoDepth(str(calib))
    return dict(sd=sd, R1=R1, P1=P1, tex=tex, left=left, right=right,
                out=sd.compute(left, right))


def _truth(R1, P1, shape, z):
    """Where each left_rect pixel's ray meets the plane, in F_rect metres, using the pixel
    convention depth.load_calib_yaml's scaled Q implies (full-res u = u_ds / DOWNSAMPLE)."""
    s = depth.DOWNSAMPLE
    u, v = np.meshgrid(np.arange(shape[1]) / s, np.arange(shape[0]) / s)
    ray = np.stack([(u - P1[0, 2]) / P1[0, 0], (v - P1[1, 2]) / P1[1, 1], np.ones_like(u)], -1)
    t = z / (ray @ R1)[..., 2]                # p_left = R1.T p_rect must have depth z
    return t[..., None] * ray / 1000.0


def test_shapes_and_mask_contract(rig):
    xyz, valid, left_rect = rig["out"]
    hw = (int(H * depth.DOWNSAMPLE), int(W * depth.DOWNSAMPLE))
    assert xyz.shape == hw + (3,) and xyz.dtype == np.float32      # NOT flattened
    assert valid.shape == hw and left_rect.shape == hw + (3,)
    assert np.isnan(xyz[~valid]).all() and np.isfinite(xyz[valid]).all()
    assert (xyz[valid][:, 2] > 0).all()
    assert (np.linalg.norm(xyz[valid], axis=1) < depth.MAX_RANGE_M).all()
    assert valid.mean() > 0.5, "synthetic scene should mostly match"


def _err(xyz, valid, rig, z):
    return np.linalg.norm(xyz[valid] - _truth(rig["R1"], rig["P1"], valid.shape, z)[valid], axis=1)


def test_geometry_is_exact_at_whole_pixel_disparity(rig):
    """Plane placed where the true disparity is a WHOLE number of pixels, near 1 m -- the one
    place SGBM's sub-pixel estimate is unbiased. What is left is the fork's own geometry: Q
    scaling, the /1000, the rectified frame. Millimetres, not centimetres."""
    Q = rig["sd"].Q
    fb = Q[2, 3] / Q[3, 2]                          # f*B at DOWNSAMPLE, mm*px
    z = fb / round(fb / 1000.0)                     # whole-pixel disparity, ~1 m
    xyz, valid, _ = rig["sd"].compute(*_pair(rig["tex"], z))
    err = _err(xyz, valid, rig, z)
    assert np.median(err) < 0.005
    assert np.percentile(err, 90) < 0.015


def test_sgbm_baseline_at_1m(rig):
    """At fractional disparities SGBM pixel-locks: up to ~0.25 px of bias toward whole
    pixels, ~2 cm at 1 m with only ~9 px of disparity at DOWNSAMPLE=0.375. That is the
    matcher, not the fork; this pins the baseline that SGBM tuning should beat."""
    xyz, valid, _ = rig["out"]
    err = _err(xyz, valid, rig, PLANE_Z)
    assert np.median(err) < 0.030


def test_image_mask_lifts_to_the_object(rig):
    """The segment path's whole lift-to-3D step is xyz[mask & valid]. Find the red marker in
    left_rect and check the points land where the marker physically is."""
    xyz, valid, left_rect = rig["out"]
    b, g, r = (left_rect[..., i].astype(int) for i in range(3))
    mask = cv2.erode((r - np.maximum(g, b) > 60).astype(np.uint8), np.ones((3, 3))) > 0
    pts = xyz[mask & valid]
    assert len(pts) > 100
    centre = np.median(pts @ rig["R1"], axis=0)              # F_rect -> left camera frame
    x0, x1, y0, y1 = MARKER
    np.testing.assert_allclose(centre, [(x0 + x1) / 2000, (y0 + y1) / 2000, PLANE_Z / 1000],
                               atol=0.015)


def test_swapped_eyes_put_nothing_behind_the_camera(rig):
    """Swapped eyes turn every disparity negative. Upstream's valid = disp > MIN_DISP + 0.5
    accepts those (MIN_DISP = -32) and its 5 m cull keeps them: a mirrored scene behind
    the robot. Ours must reject them."""
    xyz, valid, _ = rig["sd"].compute(rig["right"], rig["left"])
    assert valid.mean() < 0.02
    assert (xyz[valid][:, 2] > 0).all()


@pytest.mark.parametrize("t_scale", [1e-3, 1e3], ids=["doubled-div", "missing-div"])
def test_wrong_units_trip_the_metres_assertion(rig, tmp_path, t_scale):
    calib = tmp_path / "calib.yaml"
    _write_calib(calib, t_scale)
    with pytest.raises(AssertionError, match="is not metres"):
        depth.StereoDepth(str(calib)).compute(rig["left"], rig["right"])


def test_rejects_scaled_capture(rig):
    small = cv2.resize(rig["left"], (W // 2, H // 2))
    with pytest.raises(ValueError, match="calibration is"):
        rig["sd"].compute(small, small)


def test_capture_gate(rig):
    """docs/22 §4 via obs.capture_quality. Units: skew in ms, tilt rate in rad/s, coverage a
    0..1 fraction of the pixels SGBM can match."""
    sbs = np.hstack([rig["left"], rig["right"]])
    rigs = {"cam0": rig["sd"], "cam1": rig["sd"]}
    out, ok = depth.depth_capture({"cam0": sbs, "cam1": sbs}, rigs, skew_ms=1.4, tilt_rate_max=0.031)
    assert ok and sorted(out) == ["cam0", "cam1"]
    assert all(np.array_equal(a, b) for a, b in zip(out["cam0"][:2], rig["out"][:2]) if a.dtype == bool)
    cov = depth.coverage(out["cam0"][1])
    assert 0.6 < cov <= 1.0, cov                      # a fraction, not a percentage
    one = {"cam0": rig["sd"]}
    assert not depth.depth_capture({"cam0": sbs}, one, 30.0, 0.031)[1]    # cameras latched 30 ms apart
    assert not depth.depth_capture({"cam0": sbs}, one, 1.4, 0.2)[1]       # mid-correction, 0.2 rad/s
    blind = np.zeros_like(sbs)                                            # lens cap on cam1
    assert not depth.depth_capture({"cam0": sbs, "cam1": blind}, rigs, 1.4, 0.031)[1]


def test_half_fov_is_the_narrower_axis_in_degrees(rig):
    """For raycast.Camera: atan(half the smaller image side / focal length), both at DOWNSAMPLE."""
    import math
    f = float(rig["sd"].Q[2, 3])
    assert f == pytest.approx(rig["P1"][0, 0] * depth.DOWNSAMPLE)
    h = int(H * depth.DOWNSAMPLE)
    assert rig["sd"].half_fov_deg() == pytest.approx(math.degrees(math.atan(h / 2 / f)))
    assert 20 < rig["sd"].half_fov_deg() < 60
