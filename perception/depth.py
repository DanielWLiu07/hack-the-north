#!/usr/bin/env python3
"""A camera -> per-pixel 3-D points. Stage 4 of docs/20-perception-logic.md.

Two sources, ONE output contract, so everything downstream is source-blind:
    xyz        (H,W,3) float32 · the camera's optical frame, X right, Y down, Z fwd · METRES
    valid      (H,W)   bool    · xyz is NaN wherever this is False
    image      (H,W,3) uint8   · BGR, aligned pixel-for-pixel with xyz, so an image mask
                                 lifts to 3-D as `xyz[mask & valid]`

StereoDepth -- Bracket Bot's fisheye pair: forked from examples/example_depth.py
(BracketBotCapstone/quickstart). Rectify, SGBM and reprojection are theirs; the frame is the
RECTIFIED left camera, at DOWNSAMPLE scale. Removed: the loop, Rerun, the colourised depth,
and the camera->robot tilt (that is T_rob<-cam, fuse.py's job).

RealSenseDepth -- active-IR D415/D435 via Sarah's collector (docs/27). Depth comes off the
sensor already aligned to colour; no SGBM, no texture needed.
"""

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import obs  # noqa: E402

# ───────────────── CONFIG ─────────────────
DOWNSAMPLE   = 0.75             # upstream 0.375 is a Pi CPU budget; this runs on the laptop. At
                               # 0.375 SGBM pixel-locking terraces a floor 13 mm std (desk top 9 mm)
                               # and plane removal leaves the terraces as "objects"; at 0.75: 8 / 3 mm
CALIB_SIZE   = (1280, 720)      # one eye; lib/camera.py splits the 2560x720 frame at x=1280. The DEFAULT:
                               # a calibration that declares image_width/image_height says its own (calib_size)

# SGBM params (identical to "big" file)
WINDOW_SIZE  = 7
MIN_DISP     = -32
NUM_DISP     = 144              # must be /16
UNIQUENESS   = 5
SPECKLE_WIN  = 100
SPECKLE_RANGE = 1
P1           = 8  * 3 * WINDOW_SIZE ** 2
P2           = 32 * 3 * WINDOW_SIZE ** 2

MAX_RANGE_M  = 5.0              # upstream's far-point cull


# ─────────── Load calibration (verbatim) ───────────
def load_calib_yaml(path: str, scale: float):
    fs = cv2.FileStorage(path, cv2.FILE_STORAGE_READ)
    mtx_l = fs.getNode("mtx_l").mat();  dist_l = fs.getNode("dist_l").mat()
    mtx_r = fs.getNode("mtx_r").mat();  dist_r = fs.getNode("dist_r").mat()
    R1 = fs.getNode("R1").mat();        R2 = fs.getNode("R2").mat()
    P1 = fs.getNode("P1").mat();        P2 = fs.getNode("P2").mat()
    Q  = fs.getNode("Q").mat().astype(np.float32)
    fs.release()
    # down-sample Q's translation entries
    for i in range(4):
        Q[i, 3] *= scale
    return mtx_l, dist_l, mtx_r, dist_r, R1, R2, P1, P2, Q


def calib_size(path: str) -> tuple[int, int]:
    """(w, h) of ONE EYE, as the calibration was solved. A yaml that carries `image_width` / `image_height` says
    so itself; one that does not is upstream's 1280x720. bracketbot-0183's head camera is 2560x960 (bbos
    Config("depth").input_width/height; its principal point, 622 x 492, agrees), so its yaml declares 1280x960.
    Declared by the CALIBRATION, never taken from the frame: the size check in compute() exists to catch a frame
    that does not match its calibration, and a size read off the frame could never fail it."""
    fs = cv2.FileStorage(path, cv2.FILE_STORAGE_READ)
    w, h = fs.getNode("image_width"), fs.getNode("image_height")
    size = (int(w.real()), int(h.real())) if not (w.empty() or h.empty()) else CALIB_SIZE
    fs.release()
    return size


def split_sbs(frame: np.ndarray, eye_width: int = CALIB_SIZE[0]):
    """Side-by-side camera frame -> (left, right), split where lib/camera.py splits it."""
    return frame[:, :eye_width], frame[:, eye_width:]


class StereoDepth:
    """One per stereo rig. Calibration, rectification maps and matcher are built once."""
    SPAN = "perception.stereo"

    def observe(self, frame: np.ndarray):
        """The source interface depth_capture uses: one side-by-side frame -> (xyz, valid, image)."""
        return self.compute(*split_sbs(frame, self.size[0]))

    def coverage(self, valid: np.ndarray) -> float:
        return coverage(valid)

    def __init__(self, calib_path: str):
        (mtx_l, dist_l, mtx_r, dist_r,
         R1, R2, P1_cam, P2_cam, self.Q) = load_calib_yaml(calib_path, DOWNSAMPLE)
        self.size = calib_size(calib_path)                      # one eye, as THIS calibration was solved

        # Rectification maps
        self.map1x, self.map1y = cv2.fisheye.initUndistortRectifyMap(
            mtx_l, dist_l, R1, P1_cam, self.size, cv2.CV_32FC1)
        self.map2x, self.map2y = cv2.fisheye.initUndistortRectifyMap(
            mtx_r, dist_r, R2, P2_cam, self.size, cv2.CV_32FC1)

        # SGBM matcher
        self.stereo = cv2.StereoSGBM_create(
            minDisparity=MIN_DISP,
            numDisparities=NUM_DISP,
            blockSize=WINDOW_SIZE,
            P1=P1,            # 8 * 3 * WINDOW_SIZE**2
            P2=P2,            # 32 * 3 * WINDOW_SIZE**2
            disp12MaxDiff=1,
            uniquenessRatio=UNIQUENESS,
            speckleWindowSize=SPECKLE_WIN,
            speckleRange=SPECKLE_RANGE,
            preFilterCap=63,
            mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY,
        )

    def half_fov_deg(self) -> float:
        """Half-angle of the rectified view, the NARROWER axis (vertical): a cone that is too
        small makes raycast.occlusion_check answer UNOBSERVED, never a false REMOVED."""
        f = float(self.Q[2, 3])                                   # focal length at DOWNSAMPLE, px
        w, h = (int(v * DOWNSAMPLE) for v in self.size)
        return math.degrees(math.atan(min(w, h) / 2 / f))

    def compute(self, left_raw: np.ndarray, right_raw: np.ndarray):
        """Raw BGR eyes at CALIB_SIZE -> (xyz, valid, left_rect). See the module docstring."""
        for img in (left_raw, right_raw):
            # The maps are only right at the size the intrinsics were solved at. A scaled
            # capture would not crash; it would quietly produce the wrong geometry.
            if img.shape[1::-1] != self.size:
                raise ValueError(f"eye is {img.shape[1::-1]}, calibration is {self.size}")
        w, h = self.size

        # Rectify → downsample
        with obs.span("perception.rectify"):
            left_rect  = cv2.remap(left_raw,  self.map1x, self.map1y, cv2.INTER_LINEAR)
            right_rect = cv2.remap(right_raw, self.map2x, self.map2y, cv2.INTER_LINEAR)
            tgt_sz = (int(w * DOWNSAMPLE), int(h * DOWNSAMPLE))
            left_ds  = cv2.resize(left_rect,  tgt_sz, interpolation=cv2.INTER_AREA)
            right_ds = cv2.resize(right_rect, tgt_sz, interpolation=cv2.INTER_AREA)

        # Disparity (px)
        with obs.span("perception.sgbm"):
            disp = self.stereo.compute(left_ds, right_ds).astype(np.float32) / 16.0

        with obs.span("perception.reproject") as sp:
            # Q is in MILLIMETRES (22.5 mm checkerboard). This is the one /1000 in the pipeline.
            xyz = cv2.reprojectImageTo3D(disp, self.Q) / 1000.0

            # SGBM matched it (upstream's test) AND it is in front of the lens. The calibration
            # is CALIB_ZERO_DISPARITY, so the negative disparities MIN_DISP=-32 admits reproject
            # BEHIND the camera; upstream's 5 m cull keeps most of them.
            valid = (disp > (MIN_DISP + 0.5)) & np.isfinite(xyz).all(axis=2) & (xyz[..., 2] > 0)
            _assert_metres(xyz, valid)

            # Throw away far points (> 5 m)
            valid &= np.linalg.norm(xyz, axis=2) < MAX_RANGE_M

            xyz[~valid] = np.nan
            assert np.nanmax(np.abs(xyz[valid]), initial=0.0) < 50   # stage-4 contract, docs/20 Part 6
            if sp is not None:
                sp.set_data("coverage", coverage(valid))
        return xyz, valid, left_ds


def coverage(valid: np.ndarray) -> float:
    """Share of MATCHABLE pixels that got depth, 0..1. SGBM can never match the leftmost
    MIN_DISP + NUM_DISP columns (112 of the image's width); counted in, they cap coverage --
    at 77% for a 480-wide image -- and capture_quality's > 0.60 would reject real scenes."""
    return float(valid[:, MIN_DISP + NUM_DISP:].mean())


def depth_capture(frames: dict, rigs: dict, skew_ms: float, tilt_rate_max: float):
    """One capture: {camera: frame} -> ({camera: (xyz, valid, image)}, ok). A frame is whatever
    that camera's source observes: a side-by-side BGR array for StereoDepth, a RealSenseFrame
    for RealSenseDepth.

    ok is docs/22's quality gate, obs.capture_quality: cameras latched together (skew_ms),
    robot settled (tilt_rate_max, rad/s over the latch window, from the capture metadata),
    enough depth (mean coverage). It runs before segmentation, so a rejected capture costs
    no model time; the caller retries in a quiet window, up to 3x (docs/22 §4). Call it
    inside obs.capture_scope(): a rejection sets a tag on the enclosing Sentry scope.
    """
    out = {}
    for cam, frame in sorted(frames.items()):
        with obs.span(rigs[cam].SPAN, camera=cam):
            out[cam] = rigs[cam].observe(frame)
    with obs.span("perception.capture_gate") as sp:
        cov = {cam: rigs[cam].coverage(v) for cam, (_, v, _) in out.items()}
        if sp is not None:
            sp.set_data("coverage_by_camera", cov)
        ok = obs.capture_quality(skew_ms, tilt_rate_max, float(np.mean(list(cov.values()))))
    return out, ok


def _assert_metres(xyz: np.ndarray, valid: np.ndarray) -> None:
    """Fact 1, checked in BOTH directions: a missing /1000 or a second one.

    Runs before the range cull, which would make any bound on the survivors true by
    construction. Median, not max: near-zero disparities legitimately reproject past
    50 m, but a whole indoor scene cannot, and no real one sits within 2 cm of the lens
    (SGBM here resolves nothing closer than ~8 cm).
    """
    if not valid.any():
        return                                  # blind camera, not a units error
    r = float(np.median(np.linalg.norm(xyz[valid], axis=1)))
    assert 0.02 < r < 50, (f"median range {r:.4g} is not metres: Q is mm, divide by 1000 "
                           f"exactly once, in depth.py (docs/20 Fact 1)")


# ───────────────── RealSense: Sarah's collector (docs/27) ─────────────────
# camera.py aligns depth to COLOUR and get_point_cloud() returns one vertex per colour pixel,
# (0, 0, 0) where there is no depth: reshaped to (H,W,3) it is already pixel-aligned, in the
# camera's optical frame -- X right, Y down, Z fwd, the frame StereoDepth produces -- so
# fuse.Mount and cam_to_world_axes apply unchanged. THE TWO UNIT TRAPS, side by side on disk:
#     <cam>_pointcloud.npy   METRES already       -- never divide it
#     <cam>_depth_raw.npy    uint16 MILLIMETRES   -- divide by 1000 (here: only to cross-check)
# Both are asserted on load. No intrinsics are saved, so the point cloud IS the geometry.

@dataclass
class RealSenseFrame:
    color: np.ndarray                     # (H,W,3) uint8 BGR
    points: np.ndarray                    # (H*W,3) float32, METRES, camera frame
    depth_raw: np.ndarray | None = None   # (H,W) uint16, MILLIMETRES

    @classmethod
    def load(cls, capture_dir, cam: str) -> "RealSenseFrame":
        """data_collect.py's capture_NNNN/<cam>_{color.png, pointcloud.npy, depth_raw.npy}."""
        d, cam = Path(capture_dir), cam.lower()
        color = cv2.imread(str(d / f"{cam}_color.png"))
        if color is None:
            raise FileNotFoundError(d / f"{cam}_color.png")
        raw = d / f"{cam}_depth_raw.npy"
        return cls(color, np.load(d / f"{cam}_pointcloud.npy"),       # METRES: do NOT divide
                   np.load(raw) if raw.is_file() else None)           # MILLIMETRES


class RealSenseDepth:
    """A D415 / D435 as a depth source, with StereoDepth's output contract."""
    SPAN = "perception.realsense"
    MAX_MM_DISAGREE = 0.005     # m: pointcloud z vs depth_raw/1000, median. Same depth, 1000x apart.

    def __init__(self, name: str = "realsense"):
        self.name = name
        self._half_fov: float | None = None

    def observe(self, frame: RealSenseFrame):
        """-> (xyz (H,W,3) metres, NaN where invalid; valid; the BGR colour image)."""
        h, w = frame.color.shape[:2]
        pts = np.asarray(frame.points, np.float32)
        if pts.shape != (h * w, 3):
            raise ValueError(f"{self.name}: point cloud {pts.shape} is not one vertex per {h}x{w} colour "
                             "pixel -- save get_point_cloud() unfiltered (camera.py aligns depth to colour)")
        xyz = pts.reshape(h, w, 3).copy()
        valid = np.isfinite(xyz).all(axis=2) & (xyz[..., 2] > 0)      # (0,0,0) = no depth
        _assert_metres(xyz, valid)                                    # trap 1: a /1000 on METRES
        if frame.depth_raw is not None:
            self._assert_raw_is_mm(frame.depth_raw, xyz, valid)       # trap 2: 1000x, same folder
        valid &= np.linalg.norm(xyz, axis=2) < MAX_RANGE_M
        xyz[~valid] = np.nan
        assert np.nanmax(np.abs(xyz[valid]), initial=0.0) < 50       # stage-4 contract, docs/20 Part 6
        if valid.any():                                               # the depth's vertical half-angle
            p = xyz[valid]
            self._half_fov = float(np.degrees(np.arctan(np.percentile(np.abs(p[:, 1]) / p[:, 2], 99))))
        return xyz, valid, frame.color

    def coverage(self, valid: np.ndarray) -> float:
        return float(valid.mean())                  # no SGBM dead band: every pixel can have depth

    def half_fov_deg(self) -> float:
        """For raycast.Camera: measured from the last frame's own points (no intrinsics on
        disk), vertical, so a cone that is too narrow errs toward UNOBSERVED."""
        if self._half_fov is None:
            raise RuntimeError(f"{self.name}: observe() a frame with depth first")
        return self._half_fov

    def _assert_raw_is_mm(self, raw: np.ndarray, xyz: np.ndarray, valid: np.ndarray) -> None:
        if raw.dtype != np.uint16 or raw.shape != valid.shape:
            raise ValueError(f"{self.name}: depth_raw is {raw.dtype} {raw.shape}; want uint16 millimetres "
                             f"at {valid.shape}")
        both = valid & (raw > 0)
        if both.any():
            off = float(np.median(np.abs(raw[both] / 1000.0 - xyz[..., 2][both])))
            assert off < self.MAX_MM_DISAGREE, (
                f"{self.name}: depth_raw/1000 and the point cloud's z disagree by {off:.4g} m (median): "
                f"depth_raw is not millimetres (depth scale != 0.001?) or the point cloud is not metres")


def main():
    ap = argparse.ArgumentParser(description="Depth from one side-by-side stereo frame.")
    ap.add_argument("calib", help="this rig's stereo_calibration_fisheye.yaml")
    ap.add_argument("image", help="2560x720 side-by-side frame, as the camera delivers it")
    ap.add_argument("--px", action="append", default=[], metavar="U,V",
                    help="left_rect pixel to report; give two to measure between them")
    ap.add_argument("--save-left", metavar="PNG", help="write left_rect, to pick --px from")
    args = ap.parse_args()

    frame = cv2.imread(args.image)
    if frame is None:
        sys.exit(f"cannot read {args.image}")
    xyz, valid, left_rect = StereoDepth(args.calib).compute(*split_sbs(frame))

    r = np.linalg.norm(xyz[valid], axis=1)
    print(f"xyz {xyz.shape}  valid {valid.mean():.1%}  "
          f"median range {np.median(r) if r.size else float('nan'):.3f} m")
    if args.save_left:
        cv2.imwrite(args.save_left, left_rect)

    pts = []
    for s in args.px:
        u, v = map(int, s.split(","))
        win = xyz[max(v - 2, 0):v + 3, max(u - 2, 0):u + 3].reshape(-1, 3)
        win = win[np.isfinite(win).all(axis=1)]           # median of the valid 5x5 around it
        p = np.median(win, axis=0) if len(win) else np.full(3, np.nan)
        pts.append(p)
        print(f"({u},{v})  xyz {np.round(p, 3)} m  from {len(win)}/25 valid px")
    if len(pts) == 2:
        print(f"distance {np.linalg.norm(pts[0] - pts[1]):.3f} m")


if __name__ == "__main__":
    from pathlib import Path

    from dotenv import load_dotenv

    ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(ROOT / ".env")
    sys.path.insert(0, str(ROOT))  # obs.py lives at the repo root
    import obs

    try:
        obs.init("laptop")
    except Exception as e:  # noqa: BLE001 - a parked DSN is non-empty garbage and init raises
        print(f"depth: Sentry not initialised ({type(e).__name__}); depth runs anyway", file=sys.stderr)
    main()
