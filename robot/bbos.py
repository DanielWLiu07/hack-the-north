"""robot/bbos.py — the robot's own cameras and IMU, read from Bracket Bot's `bbos` shared memory.

    ROBOT_CAMERAS=cam0=bbos:camera.head.jpeg
    ROBOT_TELEMETRY_SOURCE=robot.bbos:read

The robot runs bbos, and bbos's camera daemon already HOLDS every /dev/video*: opening one
ourselves is EBUSY or a fight with their daemon. bbos publishes what we need into /dev/shm, and
`bbos.Reader` is the extension point their own bbapps use. We read; we command nothing, and no
bbos file is touched (bbos is put on OUR sys.path — never `pip install -e`, which writes egg-info
into their tree). "Read" precisely: the data segments are read through a seqlock and never
written; each Reader does claim one slot of bbos's shared timing table (128 slots), as every
bbapp does, and frees it on close.

Measured on the robot (bracketbot-0183, 2026-09-19), not taken from their comments:
    imu.orientation  97 Hz  rpy = [roll, pitch, yaw] in DEGREES. registry.py's comment says radians;
                            base/daemon.py publishes math.degrees(), and d(rpy[1])/dt against gyro
                            has slope 62 ~ 57.3. Believing the comment is a 57x error in pitch.
    imu.raw          97 Hz  gyro in rad/s, bias-corrected. PITCH RATE IS gyro[1]: correlation +0.99
                            with d(pitch)/dt (gyro[0] -0.23). This is tilt_rate — a gyro rate, not a
                            differentiated pitch (RUNBOOK §1 on why that matters).
    drive.state      97 Hz  pos, iq per wheel [axis0, axis1].
    camera.head.jpeg 30 Hz  already JPEG (~280 KB in a 4 MB slot): passed through, never re-encoded.
    base.mode        no writer — so there is NO published "balanced". It is derived here: state is
                     fresh and |pitch| < ROBOT_BALANCED_PITCH_DEG. A guess at their fall threshold,
                     on the cautious side; it gates /arm, so check it against the real robot.

Two rules from their side, both kept by ONE thread owning every Reader: bbos's Loop pacing is
global and not thread-safe (we pass keeptime=False and pace ourselves, so Loop is never entered),
and every ready() on a camera topic copies the whole 4 MB slot twice. So the IMU is polled, but a
camera is read ONLY when a capture asks — no standing 240 MB/s of memcpy on the computer that is
balancing the robot. The reader returns the newest frame, stamped with when bbos published it.
"""
from __future__ import annotations

import logging
import math
import os
import sys
import threading
import time

from robot.balance_source import Held

BBOS_PATH = os.getenv("ROBOT_BBOS_PATH", "/home/bracketbot/bbos")
BALANCED_PITCH_DEG = float(os.getenv("ROBOT_BALANCED_PITCH_DEG", "20"))
PITCH_AXIS = 1                # rpy[1] and gyro[1]: measured, see above
POLL_S = 0.005                # 200 Hz over a 97 Hz IMU; the tap samples us at 50 Hz
FRESH_FRAME_S = 0.2           # how long a latch waits for a frame it has not already handed out

log = logging.getLogger("gitspace.bbos")


def _reader_class():
    try:
        from bbos import Reader
    except ImportError:
        if BBOS_PATH not in sys.path:
            sys.path.append(BBOS_PATH)             # ours only; nothing is installed into their tree
        from bbos import Reader                    # needs posix_ipc + numpy in OUR venv
    return Reader


def _mono(timestamp) -> float:
    """bbos stamps with the WALL clock (time.time_ns at publish). Same machine, so its age is
    exact; subtract it from our monotonic clock (docs/22 §3: one clock, monotonic)."""
    age = (time.time_ns() - int(timestamp.view("i8"))) / 1e9
    return time.monotonic() - min(max(age, 0.0), 5.0)


class Hub:
    """The one thread that touches bbos. IMU + drive state -> `held` continuously; a camera frame
    only when request() asks."""

    def __init__(self, held: Held | None = None, reader=None):
        self.held = held or Held()
        self._Reader, self._readers, self._state = reader, {}, {}
        self._want: dict[str, threading.Event] = {}
        self._frames: dict[str, tuple[bytes, float] | str] = {}
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    def start(self) -> "Hub":
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="bbos-hub", daemon=True)
                self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    # ── the thread ───────────────────────────────────────────────────────────────
    def _reader(self, topic: str):
        if topic not in self._readers:
            self._readers[topic] = self._Reader(topic, keeptime=False)
        return self._readers[topic]

    def _run(self) -> None:
        try:
            self._Reader = self._Reader or _reader_class()
        except Exception as e:  # noqa: BLE001 -- no bbos here: say so once, publish nothing, gate rejects
            self.error = f"cannot import bbos ({type(e).__name__}: {e}); is posix_ipc installed and ROBOT_BBOS_PATH right?"
            log.error(self.error)
            for ev in list(self._want.values()):
                ev.set()
            return
        try:
            while not self._stop.wait(POLL_S):
                self._poll_state()
                with self._lock:
                    wanted = [t for t, ev in self._want.items() if not ev.is_set()]
                for topic in wanted:
                    self._read_frame(topic)
        finally:
            for r in self._readers.values():
                try:
                    r.__exit__(None, None, None)       # frees our slots in their timing table
                except Exception:  # noqa: BLE001
                    pass

    def _poll_state(self) -> None:
        s, fresh = self._state, False
        r = self._reader("imu.orientation")
        if r.ready():
            s["pitch"], fresh = math.radians(float(r.data["rpy"][PITCH_AXIS])), True     # DEGREES on the wire
        r = self._reader("imu.raw")
        if r.ready():
            s["tilt_rate"], fresh = float(r.data["gyro"][PITCH_AXIS]), True              # rad/s already
        r = self._reader("drive.state")
        if r.ready():
            d = r.data
            s["left_enc"], s["right_enc"] = float(d["pos"][0]), float(d["pos"][1])       # axis0, axis1; their units
            s["motor_current_l"], s["motor_current_r"] = float(d["iq"][0]), float(d["iq"][1])
        if fresh and "pitch" in s and "tilt_rate" in s:
            s["balanced"] = 1.0 if abs(math.degrees(s["pitch"])) < BALANCED_PITCH_DEG else 0.0
            self.held.put(dict(s))                 # IMU-fresh only: a live wheel topic must not keep a dead IMU alive

    def _read_frame(self, topic: str) -> None:
        r = self._reader(topic)
        if r.ready():                              # False = no writer, or the frame we already handed out
            d = r.data
            n = int(d["jpeg_len"])
            self._frames[topic] = (bytes(d["jpeg"][:n]), _mono(d["timestamp"]))
            self._want[topic].set()

    # ── for a capture ────────────────────────────────────────────────────────────
    def request(self, topic: str, timeout: float = FRESH_FRAME_S) -> tuple[bytes, float]:
        """The newest frame bbos has published and we have not yet returned -> (jpeg, t_mono)."""
        ev = threading.Event()
        with self._lock:
            self._want[topic] = ev
        self.start()
        ok = ev.wait(timeout)
        with self._lock:
            self._want.pop(topic, None)
        if self.error:
            raise RuntimeError(self.error)
        if not ok:
            raise TimeoutError(f"bbos published no new {topic} within {timeout:.1f} s: is its camera daemon running?")
        return self._frames[topic]


_hub: Hub | None = None
_hub_lock = threading.Lock()


def hub() -> Hub:
    global _hub
    with _hub_lock:
        if _hub is None:
            _hub = Hub().start()
    return _hub


def read() -> dict:
    """ROBOT_TELEMETRY_SOURCE=robot.bbos:read. Instant: the hub's latest state, {} once the IMU has
    been quiet for 100 ms — a dead daemon is "no evidence", never a still robot."""
    return hub().held.read()


class BbosCamera:
    """ROBOT_CAMERAS=cam0=bbos:camera.head.jpeg. grab() takes bbos's newest frame; its stamp is when
    bbos published it, so a camera daemon that has stalled is `camera_unavailable` (capture.py's
    250 ms rule), not an old picture. No depth here: the gate is latch_only on this camera."""

    def __init__(self, spec, cfg=None, hub_: Hub | None = None):
        self.name, self.spec, self._hub = spec.name, spec, hub_
        self._jpeg: bytes | None = None

    def open(self) -> None:
        from robot.capture import CameraUnavailable
        self._hub = self._hub or hub()
        try:
            self._hub.request(self.spec.device, timeout=2.0)
        except (TimeoutError, RuntimeError) as e:
            raise CameraUnavailable(self.name, str(e)) from e

    def info(self) -> dict:
        return {"camera": self.name, "kind": "bbos", "model": self.spec.model, "topic": self.spec.device}

    def grab(self, latch_no: int) -> float:
        from robot.capture import CameraUnavailable
        try:
            self._jpeg, t = self._hub.request(self.spec.device)
        except (TimeoutError, RuntimeError) as e:
            raise CameraUnavailable(self.name, str(e)) from e
        return t

    def retrieve(self, quality: int):
        from robot.capture import Payload, Shot, jpeg_size
        w, h = jpeg_size(self._jpeg)
        return Shot([Payload("color", "mjpg", self._jpeg, w, h)])      # bbos's bytes, as published

    def unlatch(self) -> None:
        self._jpeg = None

    def close(self) -> None:
        pass                                       # the hub is shared with telemetry; it dies with the process
