"""robot/bbos.py — the robot's own cameras and IMU, read from Bracket Bot's `bbos` shared memory.

    ROBOT_CAMERAS=cam0=bbos:camera.head.jpeg      the raw 2560x960 side-by-side pair, colour only
    ROBOT_CAMERAS=cam0=bbos:camera.rect           the rectified LEFT image + bbos's own DEPTH + intrinsics
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
    camera.rect      10 Hz  `left` (384,512,3) uint8, RGB (the daemon fills it from las2_depth_left_rgb;
                            a grey room could not confirm the order by colour — look once at something red).
    camera.depth     10 Hz  `depth` (384,512) uint16 MILLIMETRES: camera-frame z of bbos's own points /
                            depth = 0.00100038 over 60,697 px. Pixel-aligned with camera.rect `left` and
                            published with the IDENTICAL bbos timestamp (as is camera.points) — they are one
                            frame, ~136 ms older than the head jpeg beside them. `depth` is confidence-
                            filtered (31% of pixels in a normal room); `depth_raw` is everything (98.6%).
    intrinsics       512x384: fx = fy = 131.21, ppx = 229.07, ppy = 200.75 — the calibration yaml's P1 x
                     Config("depth").downsample (0.4), AND fitted from bbos's own points (0.02 px residual).
                     Read at open(), never hard-coded: a recalibration must not leave a stale K here.
    slam.pose        28 Hz  pos float32[3] + quat float32[4], SCALAR-LAST [x, y, z, w]: yaw about z from that
                            order is 0.381 rad against mapping.robot_heading 0.396 (the other order: -2.93).
                            pos agrees with mapping.robot_pos to 2-5 cm — SLAM's pose IS in the map's frame.
                            The quaternion carries the body's tilt too; only its yaw is used.
    drive.status     0.1 Hz `voltage` (the BUS voltage) — VALUE UNKNOWN: nothing has ever read it on this
                            robot, so do not take any number in this repo's tests or notes as a measurement,
                            and do not derive an alarm threshold from one. docs/02 says hoverboard motors via
                            ODrive (such packs are often ~36 V) while the 12 V in that doc is the ARM's servo
                            rail — a different thing. Ask Bracket Bot for the pack's cutoff.
                            Also `errors[2]`, `loop_hz`. Read because a robot that
                            browns out takes its daemons down with it and hard-resets: on 2026-09-19 it rebooted
                            four times with no shutdown record, and every symptom we chased that day (daemons
                            restarting together, the camera publishing nothing, the IMU at 22 Hz instead of 97)
                            is what undervoltage looks like from up here. Surfaced on /healthz so it can be
                            WATCHED, rather than diagnosed again after the next reset.
    slam.health      28 Hz  localized, relocalized, vo_lost, degraded, stalled. A pose from a lost tracker is
                            published as lost, never as a pose.
    mapping.voxels          bbos's fused, SLAM-registered map: coords float32 (1e6,3) m, colors uint8, labels int8
                            (-1 floor, 1 not floor), num_voxels ~58k live, 3 cm voxels, z capped at 1.3 m.
                            The slot is 36 MB and one read takes 75 ms: read ONLY when asked, never pumped.
    camera.points    is in the BASE frame (z = height above the floor), float16. Not shipped.
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
RECT, DEPTH = "camera.rect", "camera.depth"
# Which depth image rides with camera.rect. "depth" = bbos's confidence-filtered pixels, the ones its own
# point cloud is built from. "depth_raw" = every pixel the network produced, noisier at edges.
DEPTH_FIELD = os.getenv("ROBOT_BBOS_DEPTH_FIELD", "depth")
# What `coverage` means for the gate. "raw": the share of pixels the SENSOR produced depth for — what the
# gate is for (a blocked or blind camera). "confident": the share that survives bbos's confidence mask,
# ~0.31 in a normal room, which obs.capture_quality's 0.60 (set for dense SGBM) would reject every time.
COVERAGE_FROM = os.getenv("ROBOT_BBOS_COVERAGE", "raw")
# The hub shares a starved computer with the loop that balances the robot (seen: load average 41). Every
# ready() is two syscalls, a read and a copy, so each topic is polled no faster than its consumer needs:
POLL_S = 0.01                 # the IMU, every cycle: 100 Hz under a tap that samples at 50 Hz
EVERY = {"drive": 2, "slam": 4, "health": 25, "power": 200}   # cycles: wheels 50 Hz · slam.pose 25 Hz (it publishes
                                                 # 28) · slam.health 4 Hz · drive.status 0.5 Hz (it publishes 0.1)
FRESH_FRAME_S = 0.2           # how long a latch waits for a frame it has not already handed out
MAP = "mapping.voxels"
SLAM_STALE_S = 0.5            # slam.pose is 28 Hz; older than this, there is no pose

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
        self._slam: dict = {}
        self._slam_at = 0.0
        self._power: dict = {}
        self._power_at = 0.0
        self.faults: dict = {}
        self._cycle = 0
        self._busy_s, self._since = 0.0, time.monotonic()
        self.busy = 0.0                            # share of wall time this thread spent working, last ~5 s
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
                t_work = time.perf_counter()
                self._cycle += 1
                # each part on its own: a topic whose layout changed under us (a bbos update) must cost
                # that topic, not the camera, the IMU and the gate along with it
                for part, args in ((self._poll_state, ()), (self._poll_slam, ()), (self._poll_power, ())):
                    self._guarded(part, *args)
                with self._lock:
                    wanted = [t for t, ev in self._want.items() if not ev.is_set()]
                for topic in wanted:
                    self._guarded(self._read_frame, topic)
                self._busy_s += time.perf_counter() - t_work
                if time.monotonic() - self._since >= 5.0:
                    self.busy = round(self._busy_s / (time.monotonic() - self._since), 4)
                    self._busy_s, self._since = 0.0, time.monotonic()
        finally:
            for r in self._readers.values():
                try:
                    r.__exit__(None, None, None)       # frees our slots in their timing table
                except Exception:  # noqa: BLE001
                    pass

    def _guarded(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001
            key = (fn.__name__, *args)
            self.faults[key] = self.faults.get(key, 0) + 1
            if self.faults[key] in (1, 1000, 100000):
                log.error("bbos %s%s failed (%d x): %s: %s — that topic is unavailable; the rest carry on",
                          fn.__name__, args, self.faults[key], type(e).__name__, e)

    def _poll_state(self) -> None:
        s, fresh = self._state, False
        r = self._reader("imu.orientation")
        if r.ready():
            s["pitch"], fresh = math.radians(float(r.data["rpy"][PITCH_AXIS])), True     # DEGREES on the wire
        r = self._reader("imu.raw")
        if r.ready():
            s["tilt_rate"], fresh = float(r.data["gyro"][PITCH_AXIS]), True              # rad/s already
        r = self._reader("drive.state") if self._cycle % EVERY["drive"] == 0 else None
        if r is not None and r.ready():
            d = r.data
            s["left_enc"], s["right_enc"] = float(d["pos"][0]), float(d["pos"][1])       # axis0, axis1; their units
            s["motor_current_l"], s["motor_current_r"] = float(d["iq"][0]), float(d["iq"][1])
        if fresh and "pitch" in s and "tilt_rate" in s:
            s["balanced"] = 1.0 if abs(math.degrees(s["pitch"])) < BALANCED_PITCH_DEG else 0.0
            self.held.put(dict(s))                 # IMU-fresh only: a live wheel topic must not keep a dead IMU alive

    def _poll_slam(self) -> None:
        if self._cycle % EVERY["slam"]:
            return
        rp, rh = self._reader("slam.pose"), self._reader("slam.health")
        if ("localized" not in self._slam or self._cycle % EVERY["health"] == 0) and rh.ready():   # at once, the first time
            h = rh.data
            self._slam.update({k: bool(h[k]) for k in ("localized", "vo_lost", "degraded", "stalled")})
        if rp.ready():
            d = rp.data
            x, y, z, w = (float(v) for v in d["quat"])                   # scalar-LAST: measured, see the docstring
            self._slam.update(x=float(d["pos"][0]), y=float(d["pos"][1]), pgo_count=int(d["pgo_count"]),
                              heading=math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)),
                              t_mono=_mono(d["timestamp"]))
            self._slam_at = time.monotonic()

    def _poll_power(self) -> None:
        if self._cycle % EVERY["power"]:
            return
        r = self._reader("drive.status")
        if r.ready():
            d = r.data
            self._power = {"voltage": round(float(d["voltage"]), 2), "loop_hz": round(float(d["loop_hz"]), 1),
                           "errors": [float(v) for v in d["errors"]]}
            self._power_at = time.monotonic()

    def power(self) -> dict | None:
        """The drive bus's voltage, or None if drive.status has gone quiet. Published every 10 s, so
        `age_s` is normally under 20; much more than that and the base daemon is not talking either."""
        if not self._power:
            return None
        return {**self._power, "age_s": round(time.monotonic() - self._power_at, 1)}

    def slam(self) -> dict | None:
        """The robot in bbos's WORLD frame — the frame mapping.voxels and nav goals live in — or None
        if slam.pose has gone quiet. `ok` is False when the tracker says it is lost or stalled: the
        numbers are still there for debugging, and must not be used as a pose."""
        self.start()
        s = dict(self._slam)
        if "x" not in s or time.monotonic() - self._slam_at > SLAM_STALE_S:
            return None
        s["age_ms"] = int((time.monotonic() - s["t_mono"]) * 1000)
        # measured: 45 ms median, 395 ms worst in steady state, 1.3 s while a fresh Reader catches up. A pose
        # that old, on a robot that moves, is not where the robot is — whatever the tracker says about itself.
        s["ok"] = (bool(s.get("localized")) and not s.get("vo_lost") and not s.get("stalled")
                   and s["age_ms"] <= SLAM_STALE_S * 1000)
        return s

    def _read_map(self) -> None:
        r = self._reader(MAP)
        r.ready()                                  # newest or not: a map that has not changed is still the map
        d = r.data
        if d is None:
            return
        n = int(d["num_voxels"])
        self._frames[MAP] = ({"coords": d["coords"][:n].copy(), "colors": d["colors"][:n].copy(),
                              "labels": d["labels"][:n].copy(), "origin": [float(v) for v in d["origin"]],
                              "robot_pos": [float(v) for v in d["robot_pos"]], "robot_heading": float(d["robot_heading"]),
                              "stamp_ns": int(d["timestamp"].view("i8"))}, _mono(d["timestamp"]), None)
        # bbos's Reader keeps TWO full copies of the slot for as long as it lives: 72 MB for this topic, on a
        # robot seen with 139 MB free. The map is asked for every few seconds at most — open, read, let go.
        try:
            r.__exit__(None, None, None)
        finally:
            self._readers.pop(MAP, None)
        self._want[MAP].set()

    def _read_frame(self, topic: str) -> None:
        if topic == RECT:
            return self._read_rect()
        if topic == MAP:
            return self._read_map()
        r = self._reader(topic)
        if r.ready():                              # False = no writer, or the frame we already handed out
            d = r.data
            n = int(d["jpeg_len"])
            self._frames[topic] = (bytes(d["jpeg"][:n]), _mono(d["timestamp"]), None)
            self._want[topic].set()

    def _read_rect(self) -> None:
        """Colour + depth of ONE bbos frame. They are published with the identical timestamp; only
        that identity pairs them — two topics read a moment apart can be two different frames, and a
        depth image laid over the wrong colour image is a cloud that looks right and is not."""
        rc, rd = self._reader(RECT), self._reader(DEPTH)
        rc.ready()
        rd.ready()
        c, d = rc.data, rd.data
        if c is None or d is None or c["timestamp"] != d["timestamp"]:
            return                                 # next cycle: the other topic catches up within one
        if self._frames.get(RECT, (None, None, None))[2] == int(c["timestamp"].view("i8")):
            return                                 # the pair we already handed out
        frame = {"rgb": c["left"].copy(), "depth": d["depth"].copy(), "depth_raw": d["depth_raw"].copy()}
        self._frames[RECT] = (frame, _mono(c["timestamp"]), int(c["timestamp"].view("i8")))
        self._want[RECT].set()

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
        return self._frames[topic][:2]


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

    def __init__(self, spec, cfg=None, hub_: Hub | None = None, intrinsics: dict | None = None):
        self.name, self.spec, self._hub = spec.name, spec, hub_
        self._jpeg = None                          # bytes (a .jpeg topic) or {"rgb", "depth", "depth_raw"} (camera.rect)
        self.intrinsics = intrinsics
        # the head jpeg waits up to a frame and a half at 30 Hz; the rectified pair is 10 Hz
        self._wait = 0.5 if spec.device == RECT else FRESH_FRAME_S
        # MEASURED at latch: the rectified pair is 160-207 ms old — 136 ms of bbos's depth pipeline plus up
        # to a 10 Hz period. capture.py's 250 ms "stalled" rule was written for 30 Hz cameras and would fire
        # on a healthy one. The stamp stays honest, so the tilt gate is still judged at the FRAME's time.
        if spec.device == RECT:
            self.max_frame_age_s = 0.6

    def open(self) -> None:
        from robot.capture import CameraUnavailable
        self._hub = self._hub or hub()
        try:
            self._hub.request(self.spec.device, timeout=2.0)
        except (TimeoutError, RuntimeError) as e:
            raise CameraUnavailable(self.name, str(e)) from e
        if self.spec.device == RECT and self.intrinsics is None:
            self.intrinsics = rect_intrinsics()
            if self.intrinsics is None:            # depth without K cannot be deprojected; say so, ship it anyway
                log.warning("%s: no intrinsics for camera.rect (could not read bbos's calibration) — the laptop "
                            "cannot make a cloud from this depth", self.name)

    def info(self) -> dict:
        out = {"camera": self.name, "kind": "bbos", "model": self.spec.model, "topic": self.spec.device}
        if self.spec.device == RECT:
            out.update(depth_topic=DEPTH, depth_field=DEPTH_FIELD, coverage_from=COVERAGE_FROM)
            if self.intrinsics:
                out["intrinsics"] = self.intrinsics
        return out

    def grab(self, latch_no: int) -> float:
        from robot.capture import CameraUnavailable
        try:
            self._jpeg, t = self._hub.request(self.spec.device, self._wait)
        except (TimeoutError, RuntimeError) as e:
            raise CameraUnavailable(self.name, str(e)) from e
        return t

    def retrieve(self, quality: int):
        from robot.capture import Payload, Shot, encode_depth_mm, encode_jpeg, jpeg_size
        if isinstance(self._jpeg, dict):                                # camera.rect: colour + its own depth
            import numpy as np
            f = self._jpeg
            depth, (h, w) = f[DEPTH_FIELD], f["rgb"].shape[:2]
            seen = f["depth_raw"] if COVERAGE_FROM == "raw" else f["depth"]
            return Shot([Payload("color", "mjpg", encode_jpeg(np.ascontiguousarray(f["rgb"][..., ::-1]), quality), w, h),   # RGB -> BGR
                         Payload("depth", "png16", encode_depth_mm(np.ascontiguousarray(depth)), w, h)],
                        coverage=float((seen > 0).mean()),
                        meta={"depth_scale": 0.001, "coverage_confident": round(float((f["depth"] > 0).mean()), 4),
                              "coverage_raw": round(float((f["depth_raw"] > 0).mean()), 4)})
        w, h = jpeg_size(self._jpeg)
        return Shot([Payload("color", "mjpg", self._jpeg, w, h)])      # bbos's bytes, as published

    def unlatch(self) -> None:
        self._jpeg = None

    def close(self) -> None:
        pass                                       # the hub is shared with telemetry; it dies with the process


def rect_intrinsics() -> dict | None:
    """K of camera.rect, from bbos's own files: the stereo calibration's P1 scaled by the depth
    daemon's downsample. Read each start — a recalibration changes it. None if anything is missing."""
    try:
        import cv2
        _reader_class()                            # puts bbos on sys.path if it is not already
        from bbos import Config
        cfg = Config("depth")
        fs = cv2.FileStorage(str(cfg.calib_path), cv2.FILE_STORAGE_READ)
        p1, s = fs.getNode("P1").mat(), float(cfg.downsample)
        fs.release()
        return {"fx": round(float(p1[0, 0]) * s, 4), "fy": round(float(p1[1, 1]) * s, 4),
                "ppx": round(float(p1[0, 2]) * s, 4), "ppy": round(float(p1[1, 2]) * s, 4),
                "w": int(cfg.out_width), "h": int(cfg.out_height), "model": "rectified_pinhole", "coeffs": []}
    except Exception as e:  # noqa: BLE001
        log.warning("camera.rect intrinsics unavailable: %s: %s", type(e).__name__, e)
        return None
