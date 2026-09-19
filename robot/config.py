"""robot/config.py — every knob the on-device process reads. Environment only; nothing hardcoded
that differs between the Pi, a laptop bench and a test.

    ROBOT_MODE          hardware | replay | sim      default: replay if ROBOT_REPLAY_DIR, else sim
    ROBOT_REPLAY_DIR    recorded frames to serve instead of cameras (both formats, see capture.py)
    ROBOT_CAMERAS       cam0=v4l2:/dev/v4l/by-path/...,cam1=realsense:816612060665:D415,...
                        cam0=bbos:camera.head.jpeg   <- on the robot: bbos holds /dev/video* (robot/bbos.py)
    ROBOT_ALLOW         who may connect: IPs / CIDRs, e.g. 127.0.0.1,10.37.20.56,100.64.0.0/10. Unset = anyone
                        who can reach the port — and this API has no auth, a camera, /drive and /arm
    ROBOT_HOST / ROBOT_PORT                          0.0.0.0 : 8080  (docs/16 §8)
    ROBOT_PREVIEW_MIN_INTERVAL_MS   GET /camera/<name>.jpg re-reads a camera at most this often (250 = 4 fps)
    ROBOT_CAPTURE_SEQ_MIN   capture ids start above this (the robot: 1000, clear of every simulated sender's ids)
    ROBOT_STATE_DIR     the capture counter lives here, OUTSIDE the repo   ~/.cache/gitspace/robot
    ROBOT_TELEMETRY_SOURCE   module:callable returning the balance loop's dict (docs/23 §1)

The two RealSense serials are the physical cameras Sarah's collector runs against (docs/27).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

D415_SERIAL = "816612060665"
D435_SERIAL = "938422076694"

# docs/22 §4. The same numbers live inside obs.capture_quality, which is what decides whenever
# coverage can be measured on the Pi; these are for the LATCH HALF of the gate when it can't
# (a stereo-only rig: depth is SGBM on the laptop). tests/test_robot_capture.py holds the two
# in agreement, so neither can move alone.
MAX_SKEW_MS = 25.0
MAX_TILT_RATE = 0.05          # rad/s
LATCH_WINDOW_S = 0.1          # tilt_rate_max is the peak within +-100 ms of the shutter
ATTEMPTS = 3                  # "wait for a quiet window and retry, up to three times"
QUIET_S = 0.2                 # a quiet window: this long with |tilt_rate| under MAX_TILT_RATE
QUIET_TIMEOUT_S = 2.0         # ...and this long to find one before trying anyway

FW = "gitspace-pi-0.4"


@dataclass(frozen=True)
class CameraSpec:
    name: str                 # the wire name: cam0 / cam1 / cam2
    kind: str                 # "v4l2" | "realsense" | "bbos"
    device: str = ""          # /dev/v4l/by-path/... , never an index (README gotchas) | the serial | the bbos topic
    model: str = ""           # "stereo" | "D415" | "D435"; lowercased, the collector's file prefix

    def info(self) -> dict:
        return {"camera": self.name, "kind": self.kind, "model": self.model,
                **({"serial": self.device} if self.kind == "realsense" else {})}


DEFAULT_CAMERAS = (
    CameraSpec("cam0", "v4l2", os.getenv("ROBOT_CAM0_PATH", ""), "stereo"),   # BB's stock stereo pair
    CameraSpec("cam1", "realsense", D415_SERIAL, "D415"),
    CameraSpec("cam2", "realsense", D435_SERIAL, "D435"),
)


def parse_cameras(text: str) -> tuple[CameraSpec, ...]:
    """'cam0=v4l2:/dev/v4l/by-path/x,cam1=realsense:816612060665:D415' -> specs."""
    out = []
    for part in filter(None, (p.strip() for p in text.split(","))):
        name, _, rest = part.partition("=")
        kind, _, rest = rest.partition(":")
        if kind == "realsense":
            device, _, model = rest.partition(":")
        elif kind == "bbos":                         # camera.head.jpeg -> model "head"
            device, model = rest, (rest.split(".") + ["", ""])[1]
        else:
            device, model = rest, "stereo"           # a by-path device is full of colons
        if kind not in ("v4l2", "realsense", "bbos") or not name or not device:
            raise ValueError(f"ROBOT_CAMERAS: cannot read {part!r}")
        if kind == "v4l2" and device.isdigit():
            raise ValueError(f"ROBOT_CAMERAS: {name} is bound by INDEX {device}; indices shuffle "
                             "between boots. Use /dev/v4l/by-path/... (robot/README gotchas)")
        out.append(CameraSpec(name, kind, device, model))
    return tuple(out)


@dataclass(frozen=True)
class Config:
    mode: str = "sim"
    host: str = "0.0.0.0"
    port: int = 8080
    cameras: tuple[CameraSpec, ...] = DEFAULT_CAMERAS
    replay_dir: Path | None = None
    state_dir: Path = field(default_factory=lambda: Path("~/.cache/gitspace/robot").expanduser())
    telemetry_source: str = ""
    frames: int = 4               # per camera, for majority voting (docs/16 §2.1)
    quality: int = 85
    # docs/22 §5: lock exposure and white balance, the same on every camera. Unset = the camera's
    # auto modes stay on and open() says so, because no value is right for a room we haven't seen.
    exposure: float | None = None
    wb_temperature: float | None = None
    rs_size: tuple[int, int] = (640, 480)
    rs_fps: int = 30
    rs_hw_sync: bool = False      # needs the sync CABLE between the two RealSense (docs/22 §8)
    preview_min_interval_ms: int = 250
    allow: tuple[str, ...] = ()   # peer allowlist (IPs / CIDRs); empty = open
    capture_seq_min: int = 0

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Config":
        e = os.environ if env is None else env
        replay = e.get("ROBOT_REPLAY_DIR", "").strip()
        mode = e.get("ROBOT_MODE", "").strip() or ("replay" if replay else "sim")
        if mode not in ("hardware", "replay", "sim"):
            raise ValueError(f"ROBOT_MODE={mode!r}: want hardware | replay | sim")
        num = lambda k: float(e[k]) if e.get(k, "").strip() else None  # noqa: E731
        return cls(
            mode=mode, host=e.get("ROBOT_HOST", "0.0.0.0"), port=int(e.get("ROBOT_PORT", "8080")),
            cameras=parse_cameras(e["ROBOT_CAMERAS"]) if e.get("ROBOT_CAMERAS", "").strip() else DEFAULT_CAMERAS,
            replay_dir=Path(replay).expanduser() if replay else None,
            state_dir=Path(e.get("ROBOT_STATE_DIR", "~/.cache/gitspace/robot")).expanduser(),
            telemetry_source=e.get("ROBOT_TELEMETRY_SOURCE", "").strip(),
            exposure=num("ROBOT_EXPOSURE"), wb_temperature=num("ROBOT_WB_TEMPERATURE"),
            rs_hw_sync=e.get("ROBOT_RS_HW_SYNC", "0") == "1",
            preview_min_interval_ms=max(0, int(e.get("ROBOT_PREVIEW_MIN_INTERVAL_MS", "250"))),
            allow=tuple(a.strip() for a in e.get("ROBOT_ALLOW", "").split(",") if a.strip()),
            capture_seq_min=max(0, int(e.get("ROBOT_CAPTURE_SEQ_MIN", "0"))))
