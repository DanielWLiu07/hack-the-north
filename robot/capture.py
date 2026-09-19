#!/usr/bin/env python3
"""robot/capture.py — the three cameras, latched together. Design: docs/22-camera-sync.md.

    for c in cams: c.grab()        THE LATCH. Every camera, back to back, nothing slow between.
    pose = read()                  with the shutter — not after the decode (docs/16 §2.1: atomic)
    for c in cams: c.retrieve()    decode / encode. Slow, and its order no longer matters.

Round-robin (open, grab, close, next) put the decode BETWEEN the latches: ~1500 ms of skew, and
on a robot that is always correcting its balance that is 520 mm of error against a 10 mm quantum
(docs/22 §1). Here the cameras are opened once and held open, and skew_ms is what the latch loop
alone costs.

grab() must return the time the frame ARRIVED, on the Pi's monotonic clock (docs/22 §3) — not
the time we asked for it. A free-running camera's newest frame is up to a frame period old, and a
V4L2 queue of one hands back a frame as old as the last capture. So each backend keeps its newest
frame latched with its arrival time, continuously; grab() freezes it; a frame older than
MAX_FRAME_AGE_S is a stalled camera (`camera_unavailable`), never a stale picture.

Cameras:
    V4L2Camera        BB's side-by-side stereo pair, bound by /dev/v4l/by-path.  } NOT YET RUN ON
    RealSenseCamera   D415 / D435 by serial (docs/27): colour + depth in mm.      } HARDWARE.
    BbosCamera        (robot/bbos.py) the robot's own cameras, from Bracket Bot's bbos shared
                      memory — the only way in on the robot, where bbos holds every /dev/video*.
    ReplayCamera      recorded frames, so all of this runs with no camera attached. Both layouts:
                        perception/pipeline.py   <dir>/capture.json + cam0.jpg (2560x720 side-by-side)
                        Sarah's collector        capture_NNNN/<d415|d435>_{color.png, depth_raw.npy}

The quality gate (docs/22 §4) rejects a bad capture here, before its pixels go anywhere:
obs.capture_quality(skew_ms, tilt_rate_max, coverage), retried in a quiet window up to three
times, every attempt's numbers recorded — a rejected one's most of all. Nothing in this module
opens a socket: telemetry is read from the in-process ring, Sentry is the SDK's own queue.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import obs  # noqa: E402
from robot import config as C  # noqa: E402

log = logging.getLogger("gitspace.capture")

MAX_FRAME_AGE_S = 0.25        # a latched frame older than this: the camera stalled
PREVIEW_STALE_S = 1.0         # GET /camera/<name>.jpg refuses to show a picture older than this
PREVIEW_GRACE_S = 0.15        # a capture waits this long for a preview read (~20 ms) to let go of the cameras
MIN_COVERAGE = 0.60           # docs/22 §4; decided by obs.capture_quality, named here for `rejected_by`


class CameraUnavailable(Exception):
    """docs/16 §2.7 `camera_unavailable`: retry once, then capture with the rest."""

    def __init__(self, camera: str, detail: str):
        super().__init__(f"{camera}: {detail}")
        self.camera, self.detail = camera, detail


class Busy(Exception):
    """A capture is already running: the cameras are one resource."""


class CaptureRejected(Exception):
    """Every attempt failed the gate. `attempts` holds each one, numbers included."""

    def __init__(self, attempts: list["Capture"]):
        super().__init__(f"{len(attempts)} attempts rejected: " + "; ".join(
            f"{a.capture_id} {','.join(a.rejected_by)}" for a in attempts))
        self.attempts = attempts


# ── what a camera hands back ─────────────────────────────────────────────────────
@dataclass
class Payload:
    kind: str                 # "color" | "depth"
    fmt: str                  # "mjpg" | "png16" (uint16 MILLIMETRES, lossless)
    data: bytes
    w: int
    h: int


@dataclass
class Shot:
    payloads: list[Payload]
    coverage: float | None = None      # share of pixels with depth. None: no depth on the Pi
    meta: dict = field(default_factory=dict)


class Camera(Protocol):
    name: str

    def open(self) -> None: ...
    def info(self) -> dict: ...
    def grab(self, latch_no: int) -> float: ...       # FAST. -> Pi-monotonic arrival of the frame
    def retrieve(self, quality: int) -> Shot: ...     # SLOW. what grab() froze
    def unlatch(self) -> None: ...                    # always called after grab(), even on error
    def close(self) -> None: ...


def encode_jpeg(bgr, quality: int) -> bytes:
    import cv2
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise ValueError("JPEG encode failed")
    return buf.tobytes()


def encode_depth_mm(depth) -> bytes:
    """uint16 millimetres -> 16-bit PNG. Lossless: a JPEG would invent depth at every edge."""
    import cv2
    import numpy as np
    if depth.dtype != np.uint16 or depth.ndim != 2:
        raise ValueError(f"depth is {depth.dtype} {depth.shape}; want (H,W) uint16 millimetres (docs/27)")
    ok, buf = cv2.imencode(".png", depth)
    if not ok:
        raise ValueError("PNG encode failed")
    return buf.tobytes()


def jpeg_size(data: bytes) -> tuple[int, int]:
    """(w, h) from the SOF marker, so a recorded JPEG is never decoded just to be measured."""
    i = 2
    while i + 9 < len(data) and data[i] == 0xFF:
        marker, n = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + n
    raise ValueError("not a JPEG: no SOF marker")


# ── V4L2: Bracket Bot's stereo pair ──────────────────────────────────────────────
class V4L2Camera:
    """A pump thread grab()s continuously and stamps each frame as it arrives; grab() here
    freezes the newest one (waiting out at most the one grab() in flight, <= a frame period),
    retrieve() reads it, unlatch() lets the pump run again. NOT YET RUN ON HARDWARE."""

    SIZE = (2560, 720)

    def __init__(self, spec: C.CameraSpec, cfg: C.Config):
        self.name, self.spec, self.cfg = spec.name, spec, cfg
        self.cap = None
        self._t: float | None = None
        self._lock, self._run = threading.Lock(), threading.Event()
        self._stop, self._held = threading.Event(), False

    def info(self) -> dict:
        return self.spec.info()

    def open(self) -> None:
        import cv2
        dev = self.spec.device
        if not dev:
            raise CameraUnavailable(self.name, "no device path configured (ROBOT_CAM0_PATH or ROBOT_CAMERAS)")
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise CameraUnavailable(self.name, f"{dev} did not open")
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.SIZE[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.SIZE[1])
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)       # retrieve() returns the camera's own MJPG bytes:
        if self.cfg.exposure is not None:          # never decode and re-encode on the Pi (docs/22 §6)
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)                 # 1 = manual on most V4L2 backends
            cap.set(cv2.CAP_PROP_EXPOSURE, self.cfg.exposure)
        if self.cfg.wb_temperature is not None:
            cap.set(cv2.CAP_PROP_AUTO_WB, 0)
            cap.set(cv2.CAP_PROP_WB_TEMPERATURE, self.cfg.wb_temperature)
        if self.cfg.exposure is None or self.cfg.wb_temperature is None:
            log.warning("%s: exposure / white balance left on AUTO — docs/22 §5 wants both locked "
                        "(ROBOT_EXPOSURE, ROBOT_WB_TEMPERATURE)", self.name)
        self.cap = cap
        self._run.set()
        threading.Thread(target=self._pump, name=f"latch-{self.name}", daemon=True).start()

    def _pump(self) -> None:
        while not self._stop.is_set():
            if not self._run.wait(0.2):
                continue
            with self._lock:
                if not self._run.is_set():         # frozen between the wait and the lock
                    continue
                ok = self.cap.grab()
                self._t = time.monotonic() if ok else None
            if not ok:
                self._stop.wait(0.05)

    def grab(self, latch_no: int) -> float:
        self._run.clear()                          # park the pump BEFORE taking the lock, or it re-takes it
        self._lock.acquire()
        self._held = True
        if self._t is None:
            raise CameraUnavailable(self.name, f"{self.spec.device} is delivering no frames")
        return self._t

    def retrieve(self, quality: int) -> Shot:
        ok, buf = self.cap.retrieve()
        if not ok or buf is None:
            raise CameraUnavailable(self.name, "retrieve() failed after a good grab()")
        if buf.ndim == 3:                          # the backend decoded anyway: encode it ourselves
            h, w = buf.shape[:2]
            return Shot([Payload("color", "mjpg", encode_jpeg(buf, quality), w, h)])
        data = buf.tobytes()
        w, h = jpeg_size(data)
        return Shot([Payload("color", "mjpg", data, w, h)])

    def unlatch(self) -> None:
        if self._held:
            self._held = False
            self._lock.release()
        self._run.set()

    def close(self) -> None:
        self._stop.set()
        self._run.set()
        if self.cap is not None:
            with self._lock:
                self.cap.release()


# ── RealSense D415 / D435 ────────────────────────────────────────────────────────
class RealSenseCamera:
    """docs/27 asked for the sync story re-derived; it is the same split. librealsense's own
    thread delivers framesets into a queue of ONE (always the newest); grab() takes it — a
    reference, no pixels touched — and dates it by its time_of_arrival; retrieve() does the slow
    part: align depth to colour, encode. Depth leaves as uint16 MILLIMETRES whatever the sensor's
    depth scale, the same unit as the collector's depth_raw.npy. NOT YET RUN ON HARDWARE."""

    def __init__(self, spec: C.CameraSpec, cfg: C.Config, sync_mode: int = 0):
        self.name, self.spec, self.cfg, self.sync_mode = spec.name, spec, cfg, sync_mode
        self._pipe = self._queue = self._align = self._fs = self._rs = None
        self._scale, self.intrinsics = 0.001, None

    def info(self) -> dict:
        return {**self.spec.info(), **({"intrinsics": self.intrinsics} if self.intrinsics else {})}

    def open(self) -> None:
        try:
            import pyrealsense2 as rs
        except ImportError as e:
            raise CameraUnavailable(self.name, "pyrealsense2 is not installed") from e
        (w, h), fps = self.cfg.rs_size, self.cfg.rs_fps
        c = rs.config()
        c.enable_device(self.spec.device)
        c.enable_stream(rs.stream.depth, w, h, rs.format.z16, fps)
        c.enable_stream(rs.stream.color, w, h, rs.format.bgr8, fps)
        self._rs, self._queue, self._pipe = rs, rs.frame_queue(1), rs.pipeline()
        try:
            profile = self._pipe.start(c, self._queue)
        except RuntimeError as e:      # unplugged, or two RealSense on one USB controller (docs/27)
            raise CameraUnavailable(self.name, f"{self.spec.model} {self.spec.device}: {e}") from e
        self._align = rs.align(rs.stream.color)
        dev = profile.get_device()
        depth_sensor = dev.first_depth_sensor()
        self._scale = float(depth_sensor.get_depth_scale())
        k = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
        self.intrinsics = {"fx": k.fx, "fy": k.fy, "ppx": k.ppx, "ppy": k.ppy, "w": k.width, "h": k.height,
                           "model": str(k.model), "coeffs": list(k.coeffs)}
        self._lock_exposure(dev, depth_sensor)

    def _lock_exposure(self, dev, depth_sensor) -> None:
        rs = self._rs
        try:
            if self.sync_mode:                     # 1 = master, 2 = slave: needs the sync cable
                depth_sensor.set_option(rs.option.inter_cam_sync_mode, self.sync_mode)
            color = next((s for s in dev.query_sensors() if not s.is_depth_sensor()), None)
            if color is not None and self.cfg.exposure is not None:
                color.set_option(rs.option.enable_auto_exposure, 0)
                color.set_option(rs.option.exposure, self.cfg.exposure)
            if color is not None and self.cfg.wb_temperature is not None:
                color.set_option(rs.option.enable_auto_white_balance, 0)
                color.set_option(rs.option.white_balance, self.cfg.wb_temperature)
        except Exception as e:  # noqa: BLE001 -- an option this firmware lacks must not lose the camera
            log.warning("%s: could not set a sensor option: %s", self.name, e)
        if self.cfg.exposure is None or self.cfg.wb_temperature is None:
            log.warning("%s: colour exposure / white balance left on AUTO (docs/22 §5)", self.name)

    def grab(self, latch_no: int) -> float:
        rs = self._rs
        try:
            self._fs = self._queue.wait_for_frame(1000).as_frameset()
        except RuntimeError as e:
            raise CameraUnavailable(self.name, f"no frameset within 1 s: {e}") from e
        mono, wall = time.monotonic(), time.time()
        toa = rs.frame_metadata_value.time_of_arrival          # system clock, ms
        age = (wall - self._fs.get_frame_metadata(toa) / 1000.0) if self._fs.supports_frame_metadata(toa) else 0.0
        return mono - min(max(age, 0.0), 5.0)

    def retrieve(self, quality: int) -> Shot:
        import numpy as np
        fs = self._align.process(self._fs)
        d, c = fs.get_depth_frame(), fs.get_color_frame()
        if not d or not c:
            raise CameraUnavailable(self.name, "frameset is missing depth or colour")
        depth, color = np.asanyarray(d.get_data()), np.asanyarray(c.get_data())
        if abs(self._scale - 0.001) > 1e-9:        # the wire is millimetres; this sensor is not
            depth = np.round(depth * (self._scale / 0.001)).astype(np.uint16)
        h, w = depth.shape
        return Shot([Payload("color", "mjpg", encode_jpeg(color, quality), color.shape[1], color.shape[0]),
                     Payload("depth", "png16", encode_depth_mm(depth), w, h)],
                    coverage=float((depth > 0).mean()), meta={"depth_scale": 0.001})

    def unlatch(self) -> None:
        self._fs = None

    def close(self) -> None:
        if self._pipe is not None:
            try:
                self._pipe.stop()
            except RuntimeError:
                pass


# ── replay: recorded frames instead of cameras ───────────────────────────────────
class ReplaySession:
    """Every capture directory under `root`, in name order, read once into an index. Latch n
    serves recorded capture n (mod the count), to every camera alike, so a replayed capture is
    one recorded instant across cameras exactly as a live one is."""

    def __init__(self, root, specs: tuple[C.CameraSpec, ...] = C.DEFAULT_CAMERAS):
        self.root = Path(root)
        self._wire = {s.model.lower(): s.name for s in specs if s.model}     # d415 -> cam1
        self._spec = {s.name: s for s in specs}
        dirs = [self.root, *sorted(self.root.glob("*")), *sorted(self.root.glob("*/*"))]
        self.captures = [c for c in (self._read(d) for d in dirs if d.is_dir()) if c]
        if not self.captures:
            raise FileNotFoundError(f"{self.root}: no recorded captures (want capture.json + frames, or "
                                    "capture_NNNN/<cam>_color.png as Sarah's collector writes them)")

    def _read(self, d: Path) -> dict | None:
        if (d / "capture.json").is_file():                     # perception/pipeline.py's recording
            m = json.loads((d / "capture.json").read_text())
            streams = {f["camera"]: {"color": d / f["file"]} for f in m.get("frames", [])}
            return {"dir": d, "pose": m.get("pose"), "streams": streams} if streams else None
        streams = {}
        for color in sorted([*d.glob("*_color.png"), *d.glob("*_color.jpg")]):   # the collector's
            prefix = color.name[:-len("_color.png")]
            depth = d / f"{prefix}_depth_raw.npy"
            streams[self._wire.get(prefix.lower(), prefix)] = {
                "color": color, "depth": depth if depth.is_file() else None, "model": prefix}
        return {"dir": d, "pose": None, "streams": streams} if streams else None

    def cameras(self) -> list[str]:
        return sorted({name for c in self.captures for name in c["streams"]})

    def at(self, latch_no: int) -> dict:
        return self.captures[latch_no % len(self.captures)]

    def pose_at(self, latch_no: int) -> dict | None:
        """The pose the frames were recorded at, when the recording carries one."""
        p = self.at(latch_no)["pose"]
        return {**p, "source": "replay"} if p else None

    def info(self, name: str) -> dict:
        spec = self._spec.get(name)
        return {"camera": name, "kind": "replay", "model": spec.model if spec else name,
                "source": str(self.root)}


class ReplayCamera:
    def __init__(self, name: str, session: ReplaySession):
        self.name, self.session = name, session
        self._files: dict | None = None

    def open(self) -> None:
        pass

    def info(self) -> dict:
        return self.session.info(self.name)

    def grab(self, latch_no: int) -> float:
        cap = self.session.at(latch_no)
        self._files = cap["streams"].get(self.name)
        if self._files is None:
            raise CameraUnavailable(self.name, f"{cap['dir'].name} has no frames for {self.name}")
        return time.monotonic()

    def retrieve(self, quality: int) -> Shot:
        f = self._files
        color: Path = f["color"]
        if color.suffix.lower() in (".jpg", ".jpeg"):          # as recorded: never re-encoded
            data = color.read_bytes()
            w, h = jpeg_size(data)
        else:
            import cv2
            img = cv2.imread(str(color))
            if img is None:
                raise CameraUnavailable(self.name, f"cannot read {color}")
            (h, w), data = img.shape[:2], encode_jpeg(img, quality)
        shot = Shot([Payload("color", "mjpg", data, w, h)], meta={"file": str(color)})
        if f.get("depth"):
            import numpy as np
            depth = np.load(f["depth"])                        # uint16 MILLIMETRES (docs/27)
            shot.payloads.append(Payload("depth", "png16", encode_depth_mm(depth), depth.shape[1], depth.shape[0]))
            shot.coverage, shot.meta["depth_scale"] = float((depth > 0).mean()), 0.001
        return shot

    def unlatch(self) -> None:
        self._files = None

    def close(self) -> None:
        pass


# ── one capture ──────────────────────────────────────────────────────────────────
@dataclass
class Frame:
    camera: str
    seq: int
    t_mono: float
    payload: Payload

    def header(self, capture_id: str) -> dict:
        p = self.payload
        return {"t": "frame", "capture_id": capture_id, "camera": self.camera, "seq": self.seq,
                "kind": p.kind, "t_mono": round(self.t_mono, 6), "w": p.w, "h": p.h, "fmt": p.fmt}

    def pack(self, capture_id: str) -> bytes:
        """docs/16 §3b: uint32 LE header length · UTF-8 JSON header · the payload, as-is."""
        head = json.dumps(self.header(capture_id), separators=(",", ":")).encode()
        return struct.pack("<I", len(head)) + head + self.payload.data


def unpack_frame(blob: bytes) -> tuple[dict, bytes]:
    n = struct.unpack_from("<I", blob)[0]
    return json.loads(blob[4:4 + n]), blob[4 + n:]


@dataclass
class Capture:
    capture_id: str
    attempt: int
    cameras: list[str]
    pose: dict
    frames: list[Frame]
    t_capture_mono: float                 # the first latch: the shutter
    t_last_mono: float
    started_mono: float
    finished_mono: float
    skew_ms: float                        # the WORST latch of the capture: max - min across cameras
    tilt_rate_max: float | None           # None: the telemetry ring did not cover the latch window
    coverage: float | None                # None: no camera on this rig measures depth on the Pi
    coverage_by_camera: dict
    gate: str                             # "full" | "latch_only" (the laptop's depth_capture finishes it)
    latch_ok: bool
    quality_ok: bool | None               # None under latch_only: not decided here
    rejected_by: list[str]
    rig: list[dict]
    trace: dict = field(default_factory=dict)
    camera_meta: dict = field(default_factory=dict)      # per camera, what its last shot reported about itself

    @property
    def accepted(self) -> bool:
        return self.latch_ok if self.quality_ok is None else self.quality_ok

    def meta(self, iso: Callable[[float], str]) -> dict:
        """Everything but the pixels: the /capture response, capture_begin, capture_rejected."""
        pose = {k: self.pose[k] for k in ("x", "z", "yaw") if k in self.pose}
        return {"capture_id": self.capture_id, "attempt": self.attempt,
                "started_at": iso(self.started_mono), "finished_at": iso(self.finished_mono),
                "t_capture_mono": round(self.t_capture_mono, 6),
                "pose": pose, "pose_source": self.pose.get("source", "odometry"),
                "cameras": self.cameras, "rig": self.rig,
                "skew_ms": self.skew_ms, "tilt_rate_max": self.tilt_rate_max,
                "coverage": self.coverage, "coverage_by_camera": self.coverage_by_camera,
                "gate": self.gate, "latch_ok": self.latch_ok, "quality_ok": self.quality_ok,
                "rejected_by": self.rejected_by, **({"camera_meta": self.camera_meta} if self.camera_meta else {}),
                **self.trace}

    def frame_list(self, iso: Callable[[float], str]) -> list[dict]:
        return [{"camera": f.camera, "seq": f.seq, "kind": f.payload.kind, "fmt": f.payload.fmt,
                 "t_mono": round(f.t_mono, 6), "ts": iso(f.t_mono), "width": f.payload.w,
                 "height": f.payload.h, "bytes": len(f.payload.data)} for f in self.frames]


class CaptureRig:
    """Owns the cameras. open() once at startup; capture() as often as asked, one at a time."""

    def __init__(self, cameras: list[Camera], tel, pose: Callable[[int], dict], state_dir: Path | None = None,
                 *, attempts: int = C.ATTEMPTS, on_event: Callable[[dict], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep, frames: int = 4, quality: int = 85,
                 traced: bool = False):
        self.cameras = {c.name: c for c in cameras}
        self.tel, self.pose, self.state_dir = tel, pose, state_dir
        self.attempts, self.on_event, self._sleep = attempts, on_event or (lambda m: None), sleep
        self.frames, self.quality = frames, quality
        # obs.init()'s answer. trace_fields() returns ids even with no DSN — ids of a trace nobody
        # sent. On a capture document that is a dead link into Sentry, so: only when it is live.
        self.traced = traced
        self.unavailable: dict[str, str] = {}
        self.last: Capture | None = None
        self._busy = threading.Lock()
        self.preview_min_interval_s = 0.25
        self._preview: dict[str, tuple[bytes, float, float]] = {}      # camera -> (jpeg, t_mono, when we read it)
        self.preview_stats = {"served": 0, "reads": 0, "last_age_ms": None}
        self._latch_no = 0
        self._seq: int | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────────
    def open(self) -> "CaptureRig":
        for name, cam in self.cameras.items():
            try:
                cam.open()
            except CameraUnavailable as e:         # start with the rest: /capture says which is missing
                self.unavailable[name] = e.detail
                log.error("camera_unavailable %s: %s", name, e.detail)
                obs.robot_failure("camera_unavailable", e.detail, camera=name)
        return self

    def close(self) -> None:
        for cam in self.cameras.values():
            try:
                cam.close()
            except Exception as e:  # noqa: BLE001
                log.warning("closing %s: %s", cam.name, e)

    def available(self) -> list[str]:
        return [n for n in self.cameras if n not in self.unavailable]

    def info(self) -> list[dict]:
        return [self.cameras[n].info() for n in self.available()]

    def iso(self, t_mono: float) -> str:
        """Pi monotonic -> wall clock, by the ONE pairing the telemetry hello publishes."""
        wall = self.tel.t_wall_base + (t_mono - self.tel.t_mono_base)
        return dt.datetime.fromtimestamp(wall, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    # ── capture ──────────────────────────────────────────────────────────────────
    def capture(self, cameras: list[str] | None = None, frames: int | None = None,
                quality: int | None = None) -> Capture:
        """The accepted capture, or CaptureRejected carrying every attempt."""
        if not self._busy.acquire(timeout=PREVIEW_GRACE_S):        # a preview read lets go in ~20 ms; a capture does not
            raise Busy("a capture is already running")
        try:
            cams = self._select(cameras)
            frames, quality = frames or self.frames, quality or self.quality
            rejected: list[Capture] = []
            with _transaction_unless_traced("robot.capture", "capture"):
                for attempt in range(1, self.attempts + 1):
                    cap = self._attempt(cams, frames, quality, attempt)
                    self.last = cap
                    if cap.accepted:
                        obs.measure(capture_attempts=attempt)
                        return cap
                    rejected.append(cap)
                    log.warning("capture_rejected %s (attempt %d/%d): %s", cap.capture_id, attempt,
                                self.attempts, ", ".join(cap.rejected_by))
                    self.on_event({"t": "capture_rejected", **cap.meta(self.iso)})
                    if attempt < self.attempts:
                        self._wait_quiet()
                obs.measure(capture_attempts=self.attempts)
            raise CaptureRejected(rejected)
        finally:
            self._busy.release()

    # ── preview: the latest picture, and nothing else ────────────────────────────
    def _remember(self, name: str, shot: Shot, t_mono: float) -> None:
        color = next((p for p in shot.payloads if p.kind == "color"), None)
        if color is not None:
            self._preview[name] = (color.data, t_mono, time.monotonic())

    def preview(self, name: str) -> tuple[bytes, float]:
        """The newest colour JPEG of one camera -> (bytes, t_mono). NOT a capture: no id, no gate,
        no retry, nothing published, the replay position does not move.

        The camera is re-read at most once per preview_min_interval_s however many clients ask —
        on the robot every read copies a 4 MB bbos slot twice, on the machine that is balancing it
        (robot/bbos.py). And it never contends with a capture: the cameras are taken only if they
        are free RIGHT NOW; while a capture holds them, the cached frame is the answer."""
        if name not in self.cameras:
            raise KeyError(name)
        if name in self.unavailable:
            raise CameraUnavailable(name, self.unavailable[name])
        cached = self._preview.get(name)
        due = cached is None or time.monotonic() - cached[2] >= self.preview_min_interval_s
        if due and self._busy.acquire(blocking=False):
            cam = self.cameras[name]
            try:
                t = cam.grab(self._latch_no)                   # the position a capture would use next; not advanced
                self._remember(name, cam.retrieve(self.quality), t)
                self.preview_stats["reads"] += 1
            finally:
                cam.unlatch()
                self._busy.release()
            cached = self._preview.get(name)
        if cached is None:
            raise CameraUnavailable(name, "no frame yet")
        age = time.monotonic() - cached[1]
        if age > PREVIEW_STALE_S:
            raise CameraUnavailable(name, f"newest frame is {age:.1f} s old")
        self.preview_stats["served"] += 1
        self.preview_stats["last_age_ms"] = int(age * 1000)
        return cached[0], cached[1]

    def _select(self, names: list[str] | None) -> list[Camera]:
        for n in names or ():
            if n not in self.cameras:
                raise CameraUnavailable(n, f"not configured; this rig has {sorted(self.cameras)}")
            if n in self.unavailable:
                raise CameraUnavailable(n, self.unavailable[n])
        chosen = [self.cameras[n] for n in (names or self.available())]
        if not chosen:
            first = next(iter(self.unavailable.items()), ("cameras", "none configured"))
            raise CameraUnavailable(first[0], first[1])
        return chosen

    def _attempt(self, cams: list[Camera], frames: int, quality: int, attempt: int) -> Capture:
        cid = self._next_id()
        names = [c.name for c in cams]
        with obs.capture_scope(cid):
            with obs.span("robot.capture", cid, attempt=attempt, cameras=",".join(names), frames=frames):
                started, pose, out, skews, stamps_all = time.monotonic(), None, [], [], []
                cover: dict[str, list[float]] = {}
                shot_meta: dict[str, dict] = {}
                for seq in range(frames):
                    n, self._latch_no = self._latch_no, self._latch_no + 1
                    stamps, latched = {}, []
                    try:
                        with obs.span("robot.latch", f"latch {seq}", seq=seq):
                            for c in cams:             # THE LATCH. Nothing slow may enter this loop.
                                latched.append(c)
                                stamps[c.name] = c.grab(n)
                        if pose is None:               # with the shutter, not after the decode
                            pose = self.pose(n)
                        now = time.monotonic()
                        for name, t in stamps.items():
                            # a camera may declare its own pipeline latency (bbos's depth: 136 ms + a 10 Hz period)
                            if now - t > getattr(self.cameras[name], "max_frame_age_s", MAX_FRAME_AGE_S):
                                raise CameraUnavailable(name, f"newest frame is {now - t:.2f} s old: camera stalled")
                        for c in cams:
                            with obs.span("robot.retrieve", f"retrieve {c.name}", camera=c.name, seq=seq):
                                shot = c.retrieve(quality)
                            out += [Frame(c.name, seq, stamps[c.name], p) for p in shot.payloads]
                            self._remember(c.name, shot, stamps[c.name])     # a capture feeds the preview for free
                            if shot.coverage is not None:
                                cover.setdefault(c.name, []).append(shot.coverage)
                            extra = {k: v for k, v in shot.meta.items() if k != "file"}
                            if extra:
                                shot_meta[c.name] = extra
                    finally:
                        for c in latched:
                            c.unlatch()
                    skews.append((max(stamps.values()) - min(stamps.values())) * 1000.0)
                    stamps_all += stamps.values()
                t0, t1 = min(stamps_all), max(stamps_all)
                tilt = self.tel.peak("tilt_rate", t0 - C.LATCH_WINDOW_S, t1 + C.LATCH_WINDOW_S, wait_s=0.3)
                by_cam = {k: round(sum(v) / len(v), 4) for k, v in cover.items()}
                coverage = round(sum(by_cam.values()) / len(by_cam), 4) if by_cam else None
                cap = Capture(cid, attempt, names, pose, out, t0, t1, started, 0.0, round(max(skews), 3),
                              tilt, coverage, by_cam, "", False, None, [], [c.info() for c in cams])
                cap.camera_meta = shot_meta
                self._gate(cap)
                cap.trace = obs.trace_fields() if self.traced else {}
                cap.finished_mono = time.monotonic()
        return cap

    def _gate(self, cap: Capture) -> None:
        """docs/22 §4. With depth on the Pi (RealSense, replay) obs.capture_quality decides all
        three. Without it, coverage cannot be known here — SGBM runs on the laptop — so this
        decides the latch half and perception.depth.depth_capture finishes the gate there, in
        the same trace. Either way the numbers are recorded, and a rejection most of all."""
        skew_ok = cap.skew_ms < C.MAX_SKEW_MS
        tilt_ok = cap.tilt_rate_max is not None and cap.tilt_rate_max < C.MAX_TILT_RATE
        cap.latch_ok = skew_ok and tilt_ok
        cap.rejected_by = ([] if skew_ok else ["skew_ms"]) + (
            [] if tilt_ok else ["tilt_rate_max" if cap.tilt_rate_max is not None else "tilt_rate_max:unmeasured"])
        with obs.span("robot.capture_gate", cap.capture_id, gate="latch_only" if cap.coverage is None else "full"):
            if cap.coverage is not None:
                cap.gate, cap.quality_ok = "full", obs.capture_quality(cap.skew_ms, cap.tilt_rate_max, cap.coverage)
                if cap.coverage <= MIN_COVERAGE:
                    cap.rejected_by.append("coverage")
            else:
                cap.gate = "latch_only"
                if cap.latch_ok:
                    obs.measure(skew_ms=cap.skew_ms, tilt_rate_max=cap.tilt_rate_max)
                else:                                  # records the numbers AND tags capture_rejected
                    obs.capture_quality(cap.skew_ms, cap.tilt_rate_max, None)
            obs.context("capture_quality", {
                "capture_id": cap.capture_id, "attempt": cap.attempt, "gate": cap.gate, "skew_ms": cap.skew_ms,
                "tilt_rate_max": cap.tilt_rate_max, "coverage": cap.coverage,
                "coverage_by_camera": cap.coverage_by_camera, "rejected_by": cap.rejected_by})

    def _wait_quiet(self) -> bool:
        """A balancing robot has moments of calm between corrections: take the picture in one.
        Reads the in-process telemetry ring only."""
        need = max(1, int(C.QUIET_S * self.tel.hz))
        deadline = time.monotonic() + C.QUIET_TIMEOUT_S
        with obs.span("robot.wait_quiet") as sp:
            found = False
            while time.monotonic() < deadline:
                recent = self.tel.recent(need)
                if len(recent) >= need and all(s["tilt_rate"] is not None and abs(s["tilt_rate"]) < C.MAX_TILT_RATE
                                               for s in recent):
                    found = True
                    break
                self._sleep(0.02)
            if sp is not None:
                sp.set_data("found", found)
        return found

    def _next_id(self) -> str:
        """cap_NNNN, counted across restarts: room-clouds' _id is the capture_id, so an id that
        came back after a reboot would overwrite the capture that first had it."""
        path = self.state_dir / "capture_seq" if self.state_dir else None
        if self._seq is None:
            try:
                self._seq = int(path.read_text()) if path and path.is_file() else 0
            except (OSError, ValueError):
                self._seq = 0
        self._seq += 1
        if path:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(str(self._seq))
                tmp.replace(path)
            except OSError as e:
                log.warning("capture counter not saved (%s): ids will repeat after a restart", e)
                self.state_dir = None
        return f"cap_{self._seq:04d}"


def _transaction_unless_traced(op: str, name: str):
    """Under the server, Sentry's FastAPI integration has already opened the transaction — and
    continued the laptop's trace from its sentry-trace header (docs/16 §6), which a second
    transaction would break. Anywhere else (--once, tests) open our own."""
    import contextlib
    return contextlib.nullcontext() if obs.trace_fields() else obs.transaction(op, name)


def build_rig(cfg: C.Config, tel, base_pose: Callable[[], dict], **kw) -> CaptureRig:
    """The rig for cfg.mode. replay/sim need no hardware and import no camera driver."""
    if cfg.mode == "hardware":
        rs_specs = [s for s in cfg.cameras if s.kind == "realsense"]
        from robot.bbos import BbosCamera              # imports nothing of bbos until a camera opens
        cams: list[Camera] = [
            V4L2Camera(s, cfg) if s.kind == "v4l2" else BbosCamera(s, cfg) if s.kind == "bbos" else
            RealSenseCamera(s, cfg, (1 if s is rs_specs[0] else 2) if cfg.rs_hw_sync else 0)
            for s in cfg.cameras]
        pose = lambda n: base_pose()  # noqa: E731
    else:
        root = cfg.replay_dir
        if root is None:                               # sim: nothing recorded — make something to replay
            from robot import sim
            root = sim.ensure_session(cfg.state_dir / "sim_session")
        session = ReplaySession(root, cfg.cameras)
        cams = [ReplayCamera(n, session) for n in session.cameras()]
        pose = lambda n: session.pose_at(n) or base_pose()  # noqa: E731
        log.info("%s: %d recorded captures from %s, cameras %s", cfg.mode, len(session.captures),
                 root, session.cameras())
    rig = CaptureRig(cams, tel, pose, cfg.state_dir, frames=cfg.frames, quality=cfg.quality, **kw)
    rig.preview_min_interval_s = cfg.preview_min_interval_ms / 1000.0
    return rig
