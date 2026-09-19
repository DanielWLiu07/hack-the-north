"""Synthetic stereo recordings: the real pipeline, run where no camera is.

NOT real data. A ray-traced fisheye stereo pair of a hand-built room -- floor, back wall, a
desk inside room.yaml's desk zone, three objects on it -- and every scan gets its own sensor
noise and a millimetre of pose jitter: what a rescan of an untouched room looks like to the
pipeline. Everything downstream of the JPEGs is the real code (pipeline.scan_into).

    python perception/synthetic.py OUT_DIR --scans 3         # -> OUT_DIR/scan_000 ... scan_002

A recording (what pipeline.load_recording reads):
    capture.json   capture_id, at, pose {x, z, yaw} (BB odometry), skew_ms, tilt_rate_max,
                   source, frames [{camera, file}], rig {camera: {calib, mount}}
    cam0.jpg       2560x720 side-by-side, as the camera delivers it
    cam0.yaml      that rig's stereo calibration (setup/calibrate_stereo_camera.py's keys)
    room/          room.yaml, anchors/, .roomignore -- to initialise a fresh room repo
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

import depth
from fuse import Mount, rect_to_world

ROOT = Path(__file__).resolve().parents[1]
W, H = depth.CALIB_SIZE

# The rig: the same synthetic fisheye pair perception/tests/test_depth.py uses.
K_L = np.array([[500.0, 0, 640], [0, 500, 360], [0, 0, 1]])
K_R = np.array([[505.0, 0, 636], [0, 503, 362], [0, 0, 1]])
D_L = np.array([[0.02], [-0.005], [0.001], [0.0]])
D_R = np.array([[0.018], [-0.004], [0.0], [0.0]])
R_LR = cv2.Rodrigues(np.radians([0.3, 0.5, 0.1]))[0]
T_LR = np.array([[-60.0], [0.5], [0.3]])                  # mm
MOUNT = Mount(pitch_down_deg=32.0, height_m=1.05)
POSE = {"x": -0.55, "z": 0.0, "yaw": 0.0}                 # BB odometry: 0.55 m behind the anchor

# The room, F_world metres. Boxes are (lo, hi); cylinders (cx, cy, r, z0, z1). BGR albedo.
DESK = ((0.10, -0.48, 0.67), (0.98, 0.48, 0.70))
LEGS = [((x, y, 0.0), (x + 0.04, y + 0.04, 0.67)) for x in (0.13, 0.91) for y in (-0.45, 0.41)]
BOOK = ((0.30, -0.30, 0.70), (0.52, -0.15, 0.75))          # 22 x 15 x 5 cm
BLOCK = ((0.75, -0.25, 0.70), (0.83, -0.17, 0.90))         # 8 x 8 x 20 cm
MUG = (0.60, 0.10, 0.045, 0.70, 0.80)                      # r 4.5 cm, 10 cm tall
WALL_X = 2.3


def scene():
    boxes = [(DESK, (90, 120, 150))] + [(leg, (60, 60, 60)) for leg in LEGS] + \
            [(BOOK, (40, 60, 170)), (BLOCK, (170, 90, 40))]
    return boxes, [(MUG, (60, 150, 60))]


def write_calib(path: Path) -> None:
    R1, R2, P1, P2, Q = cv2.fisheye.stereoRectify(K_L, D_L, K_R, D_R, (W, H), R_LR, T_LR,
                                                  flags=cv2.CALIB_ZERO_DISPARITY)
    fs = cv2.FileStorage(str(path), cv2.FILE_STORAGE_WRITE)
    for k, v in dict(mtx_l=K_L, dist_l=D_L, mtx_r=K_R, dist_r=D_R, R=R_LR, T=T_LR,
                     R1=R1, R2=R2, P1=P1, P2=P2, Q=Q).items():
        fs.write(k, np.asarray(v))
    fs.release()


def _raw_rays(K, D) -> np.ndarray:
    u, v = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    xy = cv2.fisheye.undistortPoints(np.stack([u, v], -1).reshape(-1, 1, 2), K, D).reshape(-1, 2)
    return np.column_stack([xy, np.ones(len(xy))])


def _noise(p: np.ndarray, cell: float, seed: int) -> np.ndarray:
    """3-D value noise in [0, 1): the texture is a property of the world, so both eyes agree."""
    q = p / cell
    i = np.floor(q).astype(np.int64)
    f = q - i
    f = f * f * (3 - 2 * f)
    out = np.zeros(len(p))
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                h = ((i[:, 0] + dx) * 73856093) ^ ((i[:, 1] + dy) * 19349663) ^ ((i[:, 2] + dz) * 83492791) ^ seed
                h = (h ^ (h >> 13)) * 1274126177
                val = ((h ^ (h >> 16)) & 0xFFFF) / 65536.0
                wgt = (f[:, 0] if dx else 1 - f[:, 0]) * (f[:, 1] if dy else 1 - f[:, 1]) * (f[:, 2] if dz else 1 - f[:, 2])
                out += wgt * val
    return out


def _trace(origin: np.ndarray, d: np.ndarray) -> np.ndarray:
    """Nearest hit per ray -> BGR (N, 3) float."""
    n = len(d)
    t_best = np.full(n, np.inf)
    albedo = np.zeros((n, 3))
    eps = 1e-6

    def take(t, colour):
        better = (t > eps) & (t < t_best)
        t_best[better] = t[better]
        albedo[better] = colour

    with np.errstate(divide="ignore", invalid="ignore"):
        take(np.where(d[:, 2] < 0, -origin[2] / d[:, 2], np.inf), (150, 150, 140))           # floor
        take(np.where(d[:, 0] > 0, (WALL_X - origin[0]) / d[:, 0], np.inf), (180, 170, 160))  # back wall
        boxes, cyls = scene()
        for (lo, hi), colour in boxes:
            t1, t2 = (np.asarray(lo) - origin) / d, (np.asarray(hi) - origin) / d
            tmin, tmax = np.minimum(t1, t2).max(axis=1), np.maximum(t1, t2).min(axis=1)
            take(np.where((tmax >= tmin) & (tmin > eps), tmin, np.inf), colour)
        for (cx, cy, r, z0, z1), colour in cyls:
            ox, oy = origin[0] - cx, origin[1] - cy
            a = d[:, 0] ** 2 + d[:, 1] ** 2
            b = 2 * (ox * d[:, 0] + oy * d[:, 1])
            disc = b * b - 4 * a * (ox * ox + oy * oy - r * r)
            t = (-b - np.sqrt(np.maximum(disc, 0))) / (2 * a)
            z = origin[2] + t * d[:, 2]
            take(np.where((disc >= 0) & (z >= z0) & (z <= z1), t, np.inf), colour)
            tc = (z1 - origin[2]) / d[:, 2]
            hx, hy = origin[0] + tc * d[:, 0] - cx, origin[1] + tc * d[:, 1] - cy
            take(np.where(hx * hx + hy * hy <= r * r, tc, np.inf), colour)
    p = origin + np.where(np.isfinite(t_best), t_best, 0.0)[:, None] * d
    shade = 0.35 + 0.65 * (0.6 * _noise(p, 0.012, 7) + 0.4 * _noise(p, 0.05, 11))
    return albedo * shade[:, None]


def render_sbs(true_pose: tuple[float, float, float], mount: Mount, rng: np.random.Generator,
               rays=None) -> np.ndarray:
    """The 2560x720 side-by-side frame this rig sees from true_pose (F_world x, y, yaw)."""
    R1 = cv2.fisheye.stereoRectify(K_L, D_L, K_R, D_R, (W, H), R_LR, T_LR, flags=cv2.CALIB_ZERO_DISPARITY)[0]
    rays = rays or (_raw_rays(K_L, D_L), _raw_rays(K_R, D_R))
    to_world = lambda p: rect_to_world(p, mount, true_pose)
    o_rect = np.zeros(3)
    eyes = []
    for raw, centre_raw, to_left in ((rays[0], np.zeros(3), np.eye(3)),
                                     (rays[1], (-R_LR.T @ T_LR).ravel() / 1000.0, R_LR)):
        d_rect = (raw @ to_left) @ R1.T                         # raw eye -> left raw -> rectified
        origin = to_world(R1 @ centre_raw)
        d = to_world(d_rect) - to_world(o_rect)
        img = _trace(origin, d / np.linalg.norm(d, axis=1, keepdims=True)).reshape(H, W, 3)
        eyes.append(img)
    sbs = np.hstack(eyes) + rng.normal(0, 2.0, (H, 2 * W, 3))   # sensor noise, fresh every scan
    return np.clip(sbs, 0, 255).astype(np.uint8)


def write_recordings(out: Path, scans: int, seed: int = 0, start: datetime | None = None) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    rays = (_raw_rays(K_L, D_L), _raw_rays(K_R, D_R))
    start = start or datetime(2026, 9, 19, 1, 0, 0, tzinfo=timezone.utc)
    written = []
    for n in range(scans):
        rec = out / f"scan_{n:03d}"
        rec.mkdir(exist_ok=True)
        x, y, yaw = POSE["x"], POSE["z"], POSE["yaw"]
        jitter = (x + rng.normal(0, 0.001), y + rng.normal(0, 0.001), yaw + math.radians(rng.normal(0, 0.05)))
        wobble = Mount(MOUNT.pitch_down_deg + rng.normal(0, 0.05), MOUNT.height_m, MOUNT.yaw_left_deg)
        cv2.imwrite(str(rec / "cam0.jpg"), render_sbs(jitter, wobble, rng, rays), [cv2.IMWRITE_JPEG_QUALITY, 92])
        write_calib(rec / "cam0.yaml")
        at = start + timedelta(minutes=n)
        (rec / "capture.json").write_text(json.dumps({
            "capture_id": f"synth_{seed:02d}{n:03d}", "at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),   # web: ^[a-z]+_[0-9]+$
            "pose": POSE, "skew_ms": round(float(rng.uniform(0.8, 3.0)), 2),
            "tilt_rate_max": round(float(rng.uniform(0.005, 0.03)), 4), "source": "synthetic",
            "frames": [{"camera": "cam0", "file": "cam0.jpg"}],
            "rig": {"cam0": {"calib": "cam0.yaml", "mount": vars(MOUNT)}},
        }, indent=2))
        room = rec / "room"
        if room.exists():
            shutil.rmtree(room)
        room.mkdir()
        for rel in ("room.yaml", ".roomignore"):
            shutil.copy(ROOT / "room.git" / rel, room / rel)
        shutil.copytree(ROOT / "room.git" / "anchors", room / "anchors")
        written.append(rec)
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("--scans", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--now", action="store_true", help="stamp the scans from the current time (TSDS indices accept 7 days back to a few minutes ahead)")
    a = ap.parse_args()
    start = datetime.now(timezone.utc).replace(microsecond=0) if a.now else None
    for rec in write_recordings(a.out, a.scans, a.seed, start):
        print(rec)


if __name__ == "__main__":
    main()
