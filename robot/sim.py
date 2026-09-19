"""robot/sim.py — the robot, when there is no robot. NOT real data; everything here says so.

  SimBalance   the telemetry source. A wheeled inverted pendulum that is settled most of the
               time and can be knocked: bump() rings tilt_rate to ~0.13 rad/s and lets it decay,
               which is exactly what the capture gate exists to reject (docs/22 §4).
  SimBase      where the base is: /pose reads it, /drive moves it.
  ensure_session   a small recording in Sarah's collector layout (docs/27), so `sim` replays
               recorded frames through the same code path as a real session_*/ folder.

Why not robot/telemetry.py's FakeRobot as the source: its tilt_rate is the finite difference of a
NOISY pitch (3 mrad of noise over 20 ms is 0.2 rad/s), so the peak over any +-100 ms window is
never under 0.18 rad/s — measured: 0 of 139 windows pass a 0.05 gate. Behind the gate it would
reject every capture. Here tilt_rate is the analytic derivative, and the noise is on the rate.
"""
from __future__ import annotations

import json
import math
import random
import threading
import time
from pathlib import Path

WOBBLE_RAD, WOBBLE_HZ = 0.004, 0.7      # settled: 0.004 * 2 pi * 0.7 = 0.018 rad/s, under the 0.05 gate
RATE_NOISE = 0.004                      # rad/s
BUMP_RATE, BUMP_HZ, BUMP_TAU = 0.13, 2.5, 0.35    # a knock: 0.13 rad/s, ringing, gone in about a second


class SimBalance:
    """source() for robot.telemetry.Telemetry: the eight signals of docs/23 §1."""

    def __init__(self, seed: int = 0, bump_every: float = 0.0):
        self.t0, self.rng, self.bump_every = time.monotonic(), random.Random(seed), bump_every
        self._bump_at: float | None = None
        self._fallen_until = 0.0

    def bump(self) -> None:
        self._bump_at = time.monotonic()

    def fall(self, seconds: float = 3.0) -> None:
        self._fallen_until = time.monotonic() + seconds

    def __call__(self) -> dict:
        now = time.monotonic()
        t = now - self.t0
        if self.bump_every > 0 and (self._bump_at is None or now - self._bump_at >= self.bump_every):
            self._bump_at = now
        w = 2 * math.pi * WOBBLE_HZ
        pitch, rate = WOBBLE_RAD * math.sin(w * t), WOBBLE_RAD * w * math.cos(w * t)
        resid = 0.004
        if self._bump_at is not None:
            b = now - self._bump_at
            env = math.exp(-b / BUMP_TAU)
            wb = 2 * math.pi * BUMP_HZ
            rate += BUMP_RATE * env * math.cos(wb * b)
            pitch += BUMP_RATE * env * math.sin(wb * b) / wb
            resid += 0.016 * env                       # a knock shows in the odometry too (fake/README)
        fallen = now < self._fallen_until
        if fallen:
            pitch, rate = 1.2, 0.0
        rate += self.rng.gauss(0, RATE_NOISE)
        cur = 0.45 + 3.0 * abs(rate) + self.rng.gauss(0, 0.02)
        return {"pitch": pitch, "tilt_rate": rate, "left_enc": 0.05 * t, "right_enc": 0.049 * t,
                "motor_current_l": cur, "motor_current_r": cur * 0.97,
                "odom_residual": resid, "balanced": 0.0 if fallen else 1.0}


class SimBase:
    """BB's planar odometry pose {x forward, z left, yaw rad} (docs/20 Fact 3). Thread-safe."""

    def __init__(self, x: float = 0.0, z: float = 0.0, yaw: float = 0.0, source: str = "sim"):
        self._pose, self._lock, self.source = (x, z, yaw), threading.Lock(), source

    def read(self) -> dict:
        with self._lock:
            x, z, yaw = self._pose
        return {"x": round(x, 4), "z": round(z, 4), "yaw": round(yaw, 4), "source": self.source}

    def set(self, x: float, z: float, yaw: float) -> None:
        with self._lock:
            self._pose = (float(x), float(z), float(yaw) % (2 * math.pi))


def ensure_session(root: Path, n: int = 8, size: tuple[int, int] = (320, 240)) -> Path:
    """root/capture_NNNN/{d415,d435}_{color.png, depth_raw.npy} + metadata.json, written once.

    A floor, a back wall and a box, seen by two cameras a few centimetres apart, with a
    millimetre of depth noise per capture. depth_raw is uint16 MILLIMETRES as the collector
    writes it. No <cam>_pointcloud.npy: nothing on the Pi reads one, and perception has its
    own synthetic recordings (perception/synthetic.py). metadata.json says "synthetic": true."""
    import cv2
    import numpy as np
    root = Path(root)
    if (root / "capture_0000" / "metadata.json").is_file():
        return root
    w, h = size
    v, u = np.mgrid[0:h, 0:w].astype(np.float32)
    rng = np.random.default_rng(0)
    for i in range(n):
        d = root / f"capture_{i:04d}"
        d.mkdir(parents=True, exist_ok=True)
        for cam, shift in (("d415", 0), ("d435", w // 16)):
            depth = np.full((h, w), 2400.0, np.float32)                       # back wall, 2.4 m
            floor = v > h * 0.55
            depth[floor] = 2400.0 - (v[floor] - h * 0.55) / (h * 0.45) * 1500.0   # floor runs toward us
            box = (abs(u - w * 0.5 - shift) < w * 0.12) & (abs(v - h * 0.5) < h * 0.16)
            depth[box] = 1100.0
            depth += rng.normal(0, 1.0, depth.shape).astype(np.float32)
            depth[(u < 4) | (v < 3)] = 0                                      # no depth at the border
            grey = np.clip(255 - depth / 12.0, 0, 255).astype(np.uint8)
            color = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
            color[box] = (60, 90, 170)
            cv2.putText(color, f"SYNTHETIC {cam} {i:04d}", (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1)
            cv2.imwrite(str(d / f"{cam}_color.png"), color)
            np.save(d / f"{cam}_depth_raw.npy", np.clip(depth, 0, 65535).astype(np.uint16))
        (d / "metadata.json").write_text(json.dumps(
            {"capture_index": i, "synthetic": True, "source": "robot/sim.py",
             "serials": {"d415": "synthetic", "d435": "synthetic"}}, indent=2))   # never a real camera's
    return root
