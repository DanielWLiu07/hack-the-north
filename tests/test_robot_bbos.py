"""robot/bbos.py against a fake `bbos.Reader` shaped like the real one as MEASURED on the robot:
rpy in DEGREES (their registry comment says radians), pitch and pitch-rate on axis 1, timestamps
from the wall clock, ready() False for a frame already seen or a topic with no writer."""
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from robot import bbos  # noqa: E402
from robot import capture as cap  # noqa: E402
from robot import config as C  # noqa: E402
from robot.balance_source import Held  # noqa: E402

JPEG = cv2.imencode(".jpg", np.full((48, 64, 3), 90, np.uint8))[1].tobytes()


class World:
    """What bbos is publishing right now. Tests change it; every FakeReader reads it."""
    def __init__(self):
        self.pitch_deg, self.pitch_rate, self.imu_alive, self.cam_alive = 2.0, 0.01, True, True
        self.frame_age_s, self.opened, self.closed = 0.0, [], []
        self.slam, self.map_n, self.map_reads = None, None, 0
        self.health = {"localized": True, "vo_lost": False, "degraded": False, "stalled": False}


def reader_for(world):
    class FakeReader:
        def __init__(self, name, keeptime=True):
            assert keeptime is False, "keeptime=True enters bbos's global, non-thread-safe Loop"
            self.name, self.data, self._last = name, None, None
            world.opened.append((name, __import__("threading").current_thread().name))

        def ready(self):
            alive = world.cam_alive if self.name.startswith("camera") else world.imu_alive
            if not alive:
                return False
            ts = np.datetime64(time.time_ns() - int(world.frame_age_s * 1e9) * self.name.startswith("camera"), "ns")
            if self.name.startswith("camera"):
                if self._last is not None and time.monotonic() - self._last < 0.03:
                    return False                                   # 30 Hz: nothing new yet
                buf = np.zeros(4096, np.uint8)
                buf[:len(JPEG)] = np.frombuffer(JPEG, np.uint8)
                self.data = {"jpeg": buf, "jpeg_len": np.int32(len(JPEG)), "timestamp": ts}
            elif self.name == "imu.orientation":
                self.data = {"rpy": np.array([0.1, world.pitch_deg, -114.0], np.float32), "timestamp": ts}
            elif self.name == "imu.raw":
                self.data = {"gyro": np.array([0.3, world.pitch_rate, 0.2], np.float32), "timestamp": ts}
            elif self.name == "drive.state":
                self.data = {"pos": np.array([1.5, 2.5], np.float32), "iq": np.array([0.4, 0.5], np.float32), "timestamp": ts}
            elif self.name == "slam.pose" and world.slam is not None:
                self.data = {**world.slam, "timestamp": ts}
            elif self.name == "slam.health" and world.slam is not None:
                self.data = {k: np.bool_(v) for k, v in world.health.items()}
            elif self.name == "mapping.voxels" and world.map_n is not None:
                world.map_reads += 1
                coords = np.zeros((1000, 3), np.float32)
                coords[:world.map_n] = np.arange(world.map_n * 3, dtype=np.float32).reshape(-1, 3) / 100
                self.data = {"num_voxels": np.int32(world.map_n), "coords": coords, "colors": np.full((1000, 3), 7, np.uint8),
                             "labels": np.where(np.arange(1000) % 2, 1, -1).astype(np.int8), "origin": np.array([-21.12, -21.12], np.float32),
                             "robot_pos": np.array([-1.0, 0.34], np.float32), "robot_heading": np.float32(0.3958), "timestamp": ts}
            else:
                return False                                   # a topic with no writer
            self._last = time.monotonic()
            return True

        def __exit__(self, *a):
            world.closed.append(self.name)
    return FakeReader


@pytest.fixture
def rig():
    world = World()
    h = bbos.Hub(Held(stale_s=0.1), reader=reader_for(world)).start()
    yield world, h
    h.stop()


def state(h, wait=0.2):
    end = time.monotonic() + wait
    while time.monotonic() < end and not h.held.read():
        time.sleep(0.005)
    return h.held.read()


def test_pitch_is_converted_from_degrees_and_tilt_rate_is_gyro_axis_1(rig):
    world, h = rig
    s = state(h)
    assert s["pitch"] == pytest.approx(math.radians(2.0), abs=1e-6)        # NOT 2.0 rad: the wire is degrees
    assert s["tilt_rate"] == pytest.approx(0.01)                           # gyro[1] — not gyro[0]=0.3, gyro[2]=0.2
    assert (s["left_enc"], s["right_enc"], s["motor_current_l"]) == (1.5, 2.5, pytest.approx(0.4))
    assert s["balanced"] == 1.0 and "odom_residual" not in s               # nothing publishes one: absent, not invented


def test_balanced_is_derived_from_pitch_because_bbos_publishes_no_such_flag(rig):
    world, h = rig
    world.pitch_deg = 35.0
    time.sleep(0.05)
    assert state(h)["balanced"] == 0.0


def test_a_dead_imu_daemon_is_no_evidence_even_while_the_wheels_still_report(rig):
    world, h = rig
    assert state(h)
    world.imu_alive = False                                                # drive.state keeps publishing in the fake
    time.sleep(0.25)
    assert h.held.read() == {}


def test_every_reader_lives_on_the_one_hub_thread_and_is_closed(rig):
    world, h = rig
    state(h)
    h.request("camera.head.jpeg")
    assert {thread for _, thread in world.opened} == {"bbos-hub"}
    h.stop()
    assert set(world.closed) == {"imu.orientation", "imu.raw", "drive.state", "slam.pose", "slam.health", "camera.head.jpeg"}


def test_a_camera_is_read_only_when_a_capture_asks(rig):
    world, h = rig
    state(h)
    time.sleep(0.1)
    assert not any(n.startswith("camera") for n, _ in world.opened)        # no standing 4 MB copies
    jpeg, t = h.request("camera.head.jpeg")
    assert jpeg == JPEG and abs(time.monotonic() - t) < 0.05               # their bytes; stamped when published


def test_a_capture_through_the_rig_passes_bbos_jpeg_through_and_gates_on_the_latch(rig):
    world, h = rig
    (spec,) = C.parse_cameras("cam0=bbos:camera.head.jpeg")
    assert (spec.kind, spec.device, spec.model) == ("bbos", "camera.head.jpeg", "head")

    class Tel:
        hz, t_mono_base, t_wall_base = 50, 0.0, 0.0
        def peak(self, *a, **k): return 0.01
        def recent(self, n): return []
    r = cap.CaptureRig([bbos.BbosCamera(spec, hub_=h)], Tel(), lambda n: {"x": 0, "z": 0, "yaw": 0}).open()
    got = r.capture(frames=2)
    assert [f.payload.data for f in got.frames] == [JPEG, JPEG] and (got.frames[0].payload.w, got.frames[0].payload.h) == (64, 48)
    assert got.frames[1].t_mono > got.frames[0].t_mono                     # two latches = two DIFFERENT frames
    assert got.gate == "latch_only" and got.accepted and got.rig[0]["topic"] == "camera.head.jpeg"


def test_a_stalled_or_absent_camera_daemon_is_camera_unavailable(rig):
    world, h = rig
    (spec,) = C.parse_cameras("cam0=bbos:camera.head.jpeg")
    cam = bbos.BbosCamera(spec, hub_=h)
    cam.open()
    world.cam_alive = False
    with pytest.raises(cap.CameraUnavailable, match="no new camera.head.jpeg"):
        cam.grab(0)
    world.cam_alive, world.frame_age_s = True, 3.0                          # publishing, but 3 s old frames
    r = cap.CaptureRig([cam], type("T", (), {"hz": 50, "t_mono_base": 0.0, "t_wall_base": 0.0,
                                             "peak": lambda *a, **k: 0.01, "recent": lambda *a: []})(), lambda n: {})
    with pytest.raises(cap.CameraUnavailable, match="stalled"):
        r.capture(frames=1)


def test_without_bbos_installed_it_says_what_is_missing(monkeypatch):
    monkeypatch.setattr(bbos, "BBOS_PATH", "/nonexistent/bbos")
    monkeypatch.setitem(sys.modules, "bbos", None)                          # `from bbos import Reader` -> ImportError
    h = bbos.Hub(Held()).start()
    with pytest.raises(RuntimeError, match="posix_ipc"):
        h.request("camera.head.jpeg", timeout=0.5)
    assert h.held.read() == {}                                              # and telemetry is "no evidence"
    h.stop()


# ── robot/probe_bbos.py: units are MEASURED against camera.points, never read off a name ──────
def test_depth_units_are_derived_from_the_points_not_guessed():
    from robot import probe_bbos as probe
    z = np.random.default_rng(0).uniform(0.6, 3.0, (48, 64))           # a room, in metres
    assert "MILLIMETRES" in probe.depth_units((z * 1000).astype(np.uint16), z) and "MEASURED" in probe.depth_units((z * 1000).astype(np.uint16), z)
    assert "=> METRES" in probe.depth_units(z.astype(np.float32), z)
    assert "CENTIMETRES" in probe.depth_units((z * 100).astype(np.float32), z)
    alone = probe.depth_units((z * 1000).astype(np.uint16), None)       # no points to check against:
    assert "probably MILLIMETRES" in alone and "Do not build on this" in alone   # a guess, and labelled as one
    assert "cannot tell" in probe.depth_units(np.zeros((4, 4), np.uint16), z)
    height = np.full((48, 64), 0.053)                                   # SEEN ON THE ROBOT: points in the base frame,
    verdict = probe.depth_units((z * 1000).astype(np.uint16), height)   # z = height above the floor, not depth
    assert "INCONSISTENT" in verdict and "MEASURED" not in verdict and "=> " not in verdict
    far = np.full((4, 4), 9000.0)                                       # "points" that are not room-sized are not trusted
    assert "NOT cross-checked" in probe.depth_units((z * 1000).astype(np.uint16), far)


def test_the_probe_runs_end_to_end_and_reports_a_topic_with_no_writer(monkeypatch, capsys):
    import types
    from robot import probe_bbos as probe
    z = np.random.default_rng(1).uniform(0.6, 3.0, (24, 32)).astype(np.float32)
    topics = {"camera.depth": [("depth", np.uint16, (24, 32))], "camera.points": [("xyz", np.float32, (24, 32, 3))],
              "camera.head.jpeg": [("jpeg_len", np.int32, ())]}

    class FakeReader:
        def __init__(self, name, keeptime=True):
            assert keeptime is False
            self.name, self.data = name, None

        def ready(self):
            if self.name not in topics:
                return False
            rec = np.zeros(1, np.dtype(topics[self.name] + [("timestamp", "datetime64[ns]")]))[0]
            rec["timestamp"] = np.datetime64(time.time_ns(), "ns")
            if self.name == "camera.depth":
                rec["depth"] = (z * 1000).astype(np.uint16)
            elif self.name == "camera.points":
                rec["xyz"][..., 2] = z
            self.data = rec
            return True

        def __exit__(self, *a):
            pass
    monkeypatch.setitem(sys.modules, "bbos", types.SimpleNamespace(Reader=FakeReader))
    monkeypatch.setattr(probe, "SECONDS", 0.05)
    assert probe.main(["camera.head.jpeg", "camera.depth", "camera.points", "slam.pose"]) == 0
    out = capsys.readouterr().out
    assert "MILLIMETRES" in out and "MEASURED against camera.points" in out and "(24, 32) uint16" in out
    assert "[slam.pose]  NO WRITER" in out and "behind camera.head.jpeg" in out


# ── camera.rect: colour + bbos's own depth, ONE frame ─────────────────────────────
class RectWorld:
    """camera.rect and camera.depth as measured: (384,512) in life, small here; RGB; uint16 mm;
    published with the identical timestamp. `skew` lets a test publish depth one frame late."""
    def __init__(self):
        self.n, self.skew, self.t0 = 0, 0, time.time_ns()
        self.rgb = np.zeros((24, 32, 3), np.uint8)
        self.rgb[..., 0] = 250                                  # a RED room, in RGB order
        self.raw = np.full((24, 32), 1500, np.uint16)
        self.raw[0, :] = 0                                      # the network produced depth for 23/24 rows
        self.conf = self.raw.copy()
        self.conf[:, 8:] = 0                                    # bbos trusts a quarter of them

    def tick(self):                                             # bbos publishes a new frame: ONE stamp for rect and depth
        self.n += 1
        self.t0 = time.time_ns() - self.n


def rect_reader(world):
    class FakeReader:
        def __init__(self, name, keeptime=True):
            assert keeptime is False
            self.name, self.data = name, None

        def ready(self):
            n = world.n - (world.skew if self.name == "camera.depth" else 0)
            ts = np.datetime64(world.t0 + n, "ns")              # frame number IS the timestamp; age ~0
            if self.name == "camera.rect":
                self.data = {"left": world.rgb, "timestamp": ts}
            elif self.name == "camera.depth":
                self.data = {"depth": np.where(world.conf > 0, world.conf + n, 0).astype(np.uint16),   # masked stays 0
                             "depth_raw": world.raw, "timestamp": ts}
            else:
                return False                                    # no IMU in this world
            return True

        def __exit__(self, *a):
            pass
    return FakeReader


def rect_camera(world, **env):
    h = bbos.Hub(Held(), reader=rect_reader(world)).start()
    (spec,) = C.parse_cameras("cam0=bbos:camera.rect")
    k = {"fx": 131.205, "fy": 131.205, "ppx": 229.071, "ppy": 200.752, "w": 512, "h": 384}
    return h, bbos.BbosCamera(spec, hub_=h, intrinsics=k)


def test_rect_ships_colour_and_millimetre_depth_of_the_same_frame_with_intrinsics():
    world = RectWorld()
    h, cam = rect_camera(world)
    try:
        cam.open()
        world.tick()
        cam.grab(0)
        shot = cam.retrieve(90)
        cam.unlatch()
    finally:
        h.stop()
    colour, depth = shot.payloads
    assert (colour.kind, colour.fmt, colour.w, colour.h) == ("color", "mjpg", 32, 24)
    bgr = cv2.imdecode(np.frombuffer(colour.data, np.uint8), cv2.IMREAD_COLOR)
    assert bgr[..., 2].mean() > 200 and bgr[..., 0].mean() < 60           # still RED: RGB was converted, not mislabelled
    mm = cv2.imdecode(np.frombuffer(depth.data, np.uint8), cv2.IMREAD_UNCHANGED)
    assert (depth.kind, depth.fmt) == ("depth", "png16") and mm.dtype == np.uint16
    assert mm[5, 3] == 1500 + world.n and mm[5, 20] == 0                  # lossless mm; the confidence mask kept
    info = cam.info()
    assert info["intrinsics"]["fx"] == 131.205 and info["depth_field"] == "depth" and info["topic"] == "camera.rect"


def test_coverage_is_what_the_sensor_saw_and_the_confident_share_is_reported_beside_it():
    world = RectWorld()
    h, cam = rect_camera(world)
    try:
        cam.open(); world.tick(); cam.grab(0)
        shot = cam.retrieve(90)
    finally:
        h.stop()
    assert shot.coverage == pytest.approx(23 / 24)                        # depth_raw: would pass the 0.60 gate
    assert shot.meta["coverage_confident"] == 0.2396 and shot.meta["coverage_raw"] == pytest.approx(0.9583, abs=1e-4)


def test_depth_is_never_paired_with_a_colour_frame_it_does_not_belong_to():
    world = RectWorld()
    world.skew = 1                                                        # depth is always one frame behind colour
    h, cam = rect_camera(world)
    try:
        with pytest.raises(cap.CameraUnavailable):                        # no matching pair ever appears:
            cam.open()                                                    # refuse, rather than lay depth n-1 over colour n
    finally:
        h.stop()


def test_a_rect_capture_goes_through_the_full_gate_and_says_what_each_number_is():
    world = RectWorld()
    h, cam = rect_camera(world)

    class Tel:
        hz, t_mono_base, t_wall_base = 50, 0.0, 0.0
        def peak(self, *a, **k): return 0.01
        def recent(self, n): return []
    ticking = __import__("threading").Event()

    def publish():                                                        # bbos keeps publishing at 10 Hz
        while not ticking.wait(0.02):
            world.tick()
    t = __import__("threading").Thread(target=publish, daemon=True)
    t.start()
    try:
        got = cap.CaptureRig([cam], Tel(), lambda n: {"x": 0, "z": 0, "yaw": 0}).open().capture(frames=2)
    finally:
        ticking.set(); h.stop()
    assert got.gate == "full" and got.quality_ok is True and got.coverage == pytest.approx(0.9583, abs=1e-3)
    assert [(f.seq, f.payload.kind) for f in got.frames] == [(0, "color"), (0, "depth"), (1, "color"), (1, "depth")]
    meta = got.meta(lambda t: "ts")
    assert meta["camera_meta"]["cam0"]["coverage_confident"] == 0.2396 and meta["rig"][0]["intrinsics"]["ppx"] == 229.071


def test_the_rectified_pairs_known_latency_is_not_mistaken_for_a_stalled_camera():
    """Measured on the robot: a healthy camera.rect frame is 160-207 ms old at latch. 250 ms is the
    rule for 30 Hz cameras; this one declares its own, and a genuinely dead daemon still trips it."""
    (spec,) = C.parse_cameras("cam0=bbos:camera.rect")
    assert bbos.BbosCamera(spec).max_frame_age_s == 0.6
    (head,) = C.parse_cameras("cam0=bbos:camera.head.jpeg")
    assert not hasattr(bbos.BbosCamera(head), "max_frame_age_s")          # the head jpeg keeps the 250 ms rule

    class Aged:
        name, max_frame_age_s = "cam0", 0.6
        def __init__(self, age): self.age = age
        def open(self): pass
        def close(self): pass
        def unlatch(self): pass
        def info(self): return {}
        def grab(self, n): return time.monotonic() - self.age
        def retrieve(self, q): return cap.Shot([cap.Payload("color", "mjpg", b"\xff\xd8", 4, 2)])
    tel = type("T", (), {"hz": 50, "t_mono_base": 0.0, "t_wall_base": 0.0, "peak": lambda *a, **k: 0.01, "recent": lambda *a: []})()
    assert cap.CaptureRig([Aged(0.4)], tel, lambda n: {}).capture(frames=1).accepted
    with pytest.raises(cap.CameraUnavailable, match="stalled"):
        cap.CaptureRig([Aged(0.9)], tel, lambda n: {}).capture(frames=1)


# ── SLAM pose and the map ────────────────────────────────────────────────────────
def quat_xyzw(yaw, tilt=0.11):
    """A body yawed about z AND tilted a little, scalar-LAST, as slam.pose publishes it."""
    from math import cos, sin
    cz, sz, cx, sx = cos(yaw / 2), sin(yaw / 2), cos(tilt / 2), sin(tilt / 2)
    return np.array([cz * sx, sz * sx, sz * cx, cz * cx], np.float32)          # q = Rz(yaw) * Rx(tilt)


def test_the_slam_pose_is_read_scalar_last_and_its_heading_survives_the_bodys_tilt(rig):
    world, h = rig
    world.slam = {"pos": np.array([-1.0317, 0.3848, 0.0], np.float32), "quat": quat_xyzw(0.3807), "pgo_count": np.int32(41)}
    time.sleep(0.1)
    s = h.slam()
    assert (round(s["x"], 4), round(s["y"], 4), s["pgo_count"]) == (-1.0317, 0.3848, 41)
    assert s["heading"] == pytest.approx(0.3807, abs=1e-3)               # read as [w,x,y,z] this comes out near -2.9
    assert s["ok"] is True and s["age_ms"] < 200


def test_a_lost_tracker_is_published_as_lost_and_a_quiet_one_as_no_pose(rig):
    world, h = rig
    world.slam = {"pos": np.zeros(3, np.float32), "quat": quat_xyzw(1.0), "pgo_count": np.int32(3)}
    world.health["vo_lost"] = True
    time.sleep(0.1)
    assert h.slam()["ok"] is False and h.slam()["vo_lost"] is True       # the numbers stay, flagged: never a pose
    world.slam = None                                                    # the daemon goes quiet
    time.sleep(bbos.SLAM_STALE_S + 0.15)
    assert h.slam() is None


def test_the_map_is_read_only_when_asked_and_only_its_live_voxels_are_kept(rig):
    world, h = rig
    world.map_n = 120
    state(h)
    time.sleep(0.15)
    assert world.map_reads == 0                                          # a 36 MB slot is never pumped
    m, t = h.request(bbos.MAP, 1.0)
    assert world.map_reads == 1 and m["coords"].shape == (120, 3) and m["labels"].shape == (120,)
    assert m["coords"][119].tolist() == pytest.approx([3.57, 3.58, 3.59]) and m["robot_heading"] == pytest.approx(0.3958)
    assert abs(time.monotonic() - t) < 0.1


def test_one_topic_whose_layout_changed_does_not_take_the_others_down(rig):
    """The hub is ONE thread for every topic. A bbos update that renames a field in slam.pose must
    cost slam.pose — not the IMU, the capture gate and the camera with it."""
    world, h = rig
    world.slam = {"position": np.zeros(3, np.float32)}                   # no "quat", no "pos"
    time.sleep(0.15)
    assert h.slam() is None and any(k[0] == "_poll_slam" for k in h.faults)
    assert state(h)["tilt_rate"] == pytest.approx(0.01)                  # the gate still has its evidence
    assert h.request("camera.head.jpeg")[0] == JPEG                      # and the camera still answers
