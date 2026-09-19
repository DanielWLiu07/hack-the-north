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
            else:
                self.data = {"pos": np.array([1.5, 2.5], np.float32), "iq": np.array([0.4, 0.5], np.float32), "timestamp": ts}
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
    assert set(world.closed) == {"imu.orientation", "imu.raw", "drive.state", "camera.head.jpeg"}


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
