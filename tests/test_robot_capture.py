"""robot/capture.py with no camera attached: the latch, the gate, the replay.

docs/22's claim is arithmetic — round-robin puts the decode between the latches, and on a robot
that never stops correcting its balance 1500 ms of skew is 520 mm against a 10 mm quantum. So
the first thing proved here is ORDER: every camera is latched before any is decoded, and a slow
decode never becomes skew. `test_round_robin_would_fail_this` is the control that shows the
check has teeth.

Nothing here opens a socket. The Sentry test runs the REAL sentry_sdk into an in-memory
transport, so what is asserted is the event that would have been sent.
"""
import json
import re
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import obs  # noqa: E402
from robot import capture as cap  # noqa: E402
from robot import config as C  # noqa: E402
from robot import sim  # noqa: E402


@pytest.fixture(autouse=True)
def no_live_sentry(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "")


class StubTel:
    """The three things the rig reads from telemetry, scripted. peaks: tilt_rate_max per attempt."""
    hz, t_mono_base, t_wall_base = 50, 0.0, 1_789_780_000.0

    def __init__(self, peaks=(0.01,), quiet=True):
        self.peaks, self.quiet, self.asked = list(peaks), quiet, []

    def peak(self, signal, t0, t1, wait_s=0.0):
        self.asked.append((signal, t0, t1))
        return self.peaks.pop(0) if len(self.peaks) > 1 else self.peaks[0]

    def recent(self, n):
        return [{"tilt_rate": 0.0 if self.quiet else 0.3, "balanced": 1.0}] * n


class FakeCam:
    def __init__(self, name, calls, decode_s=0.0, coverage=0.9, age=0.0):
        self.name, self.calls, self.decode_s, self.coverage, self.age = name, calls, decode_s, coverage, age

    def open(self):
        self.calls.append(("open", self.name))

    def info(self):
        return {"camera": self.name, "kind": "fake"}

    def grab(self, latch_no):
        self.calls.append(("grab", self.name))
        return time.monotonic() - self.age

    def retrieve(self, quality):
        self.calls.append(("retrieve", self.name))
        time.sleep(self.decode_s)
        return cap.Shot([cap.Payload("color", "mjpg", b"\xff\xd8fake", 4, 2)], coverage=self.coverage)

    def unlatch(self):
        self.calls.append(("unlatch", self.name))

    def close(self):
        pass


POSE = {"x": 1.24, "z": 0.85, "yaw": 0.31, "source": "anchor"}


def rig_of(cams, tel=None, calls=None, tmp=None, **kw):
    def pose(n):
        if calls is not None:
            calls.append(("pose", ""))
        return dict(POSE)
    return cap.CaptureRig(cams, tel or StubTel(), pose, tmp, sleep=lambda s: None, **kw).open()


# ── the latch ────────────────────────────────────────────────────────────────────
def test_every_camera_is_latched_before_any_is_decoded():
    calls = []
    rig = rig_of([FakeCam(n, calls) for n in ("cam0", "cam1", "cam2")], calls=calls)
    rig.capture(frames=2)
    steps = [c for c in calls if c[0] in ("grab", "retrieve", "pose")]
    one_latch = [("grab", "cam0"), ("grab", "cam1"), ("grab", "cam2")]
    decode = [("retrieve", "cam0"), ("retrieve", "cam1"), ("retrieve", "cam2")]
    # the pose is read WITH the shutter — after the last grab, before the first decode — and once
    assert steps == one_latch + [("pose", "")] + decode + one_latch + decode


def test_a_slow_decode_does_not_become_skew():
    calls = []
    rig = rig_of([FakeCam(n, calls, decode_s=0.05) for n in ("cam0", "cam1", "cam2")])
    got = rig.capture(frames=1)
    assert got.skew_ms < 10, got.skew_ms                    # three 50 ms decodes; the latch saw none of them
    assert got.accepted and got.gate == "full"


def test_round_robin_would_fail_this():
    """The control: the SAME cameras, grabbed and decoded one after another as docs/02 said to."""
    cams = [FakeCam(n, [], decode_s=0.05) for n in ("cam0", "cam1", "cam2")]
    stamps = []
    for c in cams:
        stamps.append(c.grab(0))
        c.retrieve(85)
    assert (max(stamps) - min(stamps)) * 1000 > 80          # two decodes sit between the first and last latch
    assert not (max(stamps) - min(stamps)) * 1000 < C.MAX_SKEW_MS


def test_a_camera_is_unlatched_even_when_a_decode_fails():
    calls = []
    bad = FakeCam("cam1", calls)
    bad.retrieve = lambda q: (_ for _ in ()).throw(cap.CameraUnavailable("cam1", "retrieve() failed"))
    rig = rig_of([FakeCam("cam0", calls), bad, FakeCam("cam2", calls)])
    with pytest.raises(cap.CameraUnavailable):
        rig.capture(frames=1)
    assert {n for op, n in calls if op == "unlatch"} == {"cam0", "cam1", "cam2"}
    rig.cameras["cam1"].retrieve = FakeCam("cam1", calls).retrieve
    assert rig.capture(frames=1).accepted                   # and the rig is not left busy


def test_a_stalled_camera_is_unavailable_never_a_stale_frame():
    rig = rig_of([FakeCam("cam0", []), FakeCam("cam1", [], age=3.0)])      # cam1's newest frame: 3 s old
    with pytest.raises(cap.CameraUnavailable) as e:
        rig.capture(frames=1)
    assert e.value.camera == "cam1" and "stalled" in e.value.detail


def test_the_cameras_are_one_resource():
    calls = []
    rig = rig_of([FakeCam("cam0", calls, decode_s=0.3)])
    t = threading.Thread(target=rig.capture, kwargs={"frames": 1})
    t.start()
    while ("retrieve", "cam0") not in calls:
        time.sleep(0.005)
    with pytest.raises(cap.Busy):
        rig.capture(frames=1)
    t.join()


def test_an_unopened_camera_is_named_and_the_rest_still_capture():
    dead = FakeCam("cam2", [])
    dead.open = lambda: (_ for _ in ()).throw(cap.CameraUnavailable("cam2", "/dev/v4l/by-path/x did not open"))
    rig = rig_of([FakeCam("cam0", []), dead])
    assert rig.available() == ["cam0"] and "did not open" in rig.unavailable["cam2"]
    assert rig.capture(frames=1).cameras == ["cam0"]        # omit = every camera that opened
    with pytest.raises(cap.CameraUnavailable) as e:
        rig.capture(cameras=["cam0", "cam2"], frames=1)     # asked for by name: say so (docs/16 §2.7)
    assert e.value.camera == "cam2"


# ── the quality gate ─────────────────────────────────────────────────────────────
def test_a_wobbling_robot_is_rejected_then_retried_in_a_quiet_window():
    events = []
    tel = StubTel(peaks=[0.13, 0.01])
    rig = rig_of([FakeCam("cam0", []), FakeCam("cam1", [])], tel, on_event=events.append)
    got = rig.capture(frames=1)
    assert (got.attempt, got.capture_id, got.tilt_rate_max) == (2, "cap_0002", 0.01)
    (rej,) = events                                         # the rejected attempt is its own capture, numbers and all
    assert rej["t"] == "capture_rejected" and rej["capture_id"] == "cap_0001"
    assert rej["tilt_rate_max"] == 0.13 and rej["quality_ok"] is False and rej["rejected_by"] == ["tilt_rate_max"]


def test_the_tilt_window_is_100ms_either_side_of_every_latch():
    tel = StubTel()
    got = rig_of([FakeCam("cam0", [])], tel).capture(frames=3)
    (signal, t0, t1), = tel.asked
    assert signal == "tilt_rate"
    assert t0 == pytest.approx(got.t_capture_mono - 0.1) and t1 == pytest.approx(got.t_last_mono + 0.1)


def test_three_bad_attempts_raise_with_every_attempts_numbers():
    rig = rig_of([FakeCam("cam0", [])], StubTel(peaks=[0.2]))
    with pytest.raises(cap.CaptureRejected) as e:
        rig.capture(frames=1)
    assert [a.capture_id for a in e.value.attempts] == ["cap_0001", "cap_0002", "cap_0003"]
    assert all(a.tilt_rate_max == 0.2 and not a.accepted for a in e.value.attempts)


def test_missing_tilt_evidence_rejects():
    """The ring did not cover the latch window -> peak() is None. A gate that passes on missing
    evidence passes the capture it exists to reject (obs.capture_quality says the same)."""
    rig = rig_of([FakeCam("cam0", [])], StubTel(peaks=[None]), attempts=1)
    with pytest.raises(cap.CaptureRejected) as e:
        rig.capture(frames=1)
    assert e.value.attempts[0].rejected_by == ["tilt_rate_max:unmeasured"]


def test_too_little_depth_rejects():
    rig = rig_of([FakeCam("cam0", [], coverage=0.4), FakeCam("cam1", [], coverage=0.5)], attempts=1)
    with pytest.raises(cap.CaptureRejected) as e:
        rig.capture(frames=1)
    a = e.value.attempts[0]
    assert a.coverage == 0.45 and a.coverage_by_camera == {"cam0": 0.4, "cam1": 0.5} and a.rejected_by == ["coverage"]


def test_a_stereo_only_rig_decides_the_latch_half_and_says_so():
    """No depth on the Pi (SGBM is the laptop's): coverage is unknown HERE, so the capture is
    accepted on skew + tilt with quality_ok left None — perception.depth.depth_capture finishes it."""
    got = rig_of([FakeCam("cam0", [], coverage=None)]).capture(frames=1)
    assert (got.gate, got.latch_ok, got.quality_ok, got.coverage, got.accepted) == ("latch_only", True, None, None, True)
    rig = rig_of([FakeCam("cam0", [], coverage=None)], StubTel(peaks=[0.2]), attempts=1)
    with pytest.raises(cap.CaptureRejected):
        rig.capture(frames=1)


@pytest.mark.parametrize("skew, tilt, cover", [(C.MAX_SKEW_MS, 0.01, 0.9), (1.0, C.MAX_TILT_RATE, 0.9),
                                                (1.0, 0.01, cap.MIN_COVERAGE)])
def test_the_gates_numbers_are_obs_capture_qualitys(skew, tilt, cover):
    """config.py names the thresholds for the latch-only path and for `rejected_by`; obs.py owns
    them. At each boundary the two must agree, or one moved without the other."""
    assert obs.capture_quality(skew, tilt, cover) is False
    assert obs.capture_quality(min(skew, C.MAX_SKEW_MS - 0.01), min(tilt, C.MAX_TILT_RATE - 0.001),
                               max(cover, cap.MIN_COVERAGE + 0.01)) is True


# ── Sentry: the real SDK, an in-memory transport ─────────────────────────────────
@pytest.fixture
def sentry_events(monkeypatch):
    import sentry_sdk
    from sentry_sdk.transport import Transport
    envelopes = []

    class InMemory(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope)

    sentry_sdk.init(dsn="http://public@localhost:9/1", transport=InMemory, traces_sample_rate=1.0,
                    default_integrations=False, auto_enabling_integrations=False)

    def transactions():
        return [i.payload.json for e in envelopes for i in e.items if i.type == "transaction"]
    try:
        yield transactions
    finally:
        sentry_sdk.get_global_scope().set_client(None)      # back to the no-op client for every later test


def test_a_capture_lands_in_sentry_with_its_id_its_spans_and_its_numbers(sentry_events):
    rig = rig_of([FakeCam("cam0", []), FakeCam("cam1", [])], traced=True)
    got = rig.capture(frames=1)
    (tx,) = sentry_events()
    assert tx["tags"]["capture_id"] == got.capture_id
    ops = [s["op"] for s in tx["spans"]]
    assert ops.count("robot.latch") == 1 and ops.count("robot.retrieve") == 2 and "robot.capture_gate" in ops
    m = tx["measurements"]
    assert m["skew_ms"]["value"] == got.skew_ms and m["tilt_rate_max"]["value"] == 0.01
    assert m["coverage"]["value"] == 0.9 and m["capture_attempts"]["value"] == 1
    assert all(s["data"]["capture_id"] == got.capture_id for s in tx["spans"] if s["op"].startswith("robot."))
    assert got.trace["sentry_trace_id"] == tx["contexts"]["trace"]["trace_id"]     # the Elastic -> Sentry join


def test_a_rejected_capture_lands_in_sentry_too(sentry_events):
    rig = rig_of([FakeCam("cam0", [])], StubTel(peaks=[0.2]), attempts=1)
    with pytest.raises(cap.CaptureRejected):
        rig.capture(frames=1)
    (tx,) = sentry_events()
    assert tx["tags"]["capture_rejected"] == "true" and tx["measurements"]["tilt_rate_max"]["value"] == 0.2
    gate = next(s for s in tx["spans"] if s["op"] == "robot.capture_gate")
    assert gate["data"]["quality_ok"] is False


def test_no_trace_ids_on_a_capture_nobody_traced():
    """Sentry off: trace_fields() would still hand back ids — of a trace that was never sent."""
    assert rig_of([FakeCam("cam0", [])]).capture(frames=1).trace == {}


# ── identity and the wire ────────────────────────────────────────────────────────
def test_capture_ids_survive_a_restart(tmp_path):
    assert rig_of([FakeCam("cam0", [])], tmp=tmp_path).capture(frames=1).capture_id == "cap_0001"
    assert rig_of([FakeCam("cam0", [])], tmp=tmp_path).capture(frames=1).capture_id == "cap_0002"


def test_timestamps_are_pi_monotonic_mapped_by_the_hello_pairing():
    tel = StubTel()
    rig = rig_of([FakeCam("cam0", [])], tel)
    assert rig.iso(tel.t_mono_base + 1.5) == "2026-09-19T01:06:41.500Z"          # t_wall_base + 1.5 s, nothing else
    meta = rig.capture(frames=1).meta(rig.iso)
    assert meta["pose"] == {"x": 1.24, "z": 0.85, "yaw": 0.31} and meta["pose_source"] == "anchor"


def test_a_binary_frame_is_self_describing():
    f = cap.Frame("cam1", 2, 81234.5519, cap.Payload("depth", "png16", b"\x89PNGdata", 640, 480))
    head, payload = cap.unpack_frame(f.pack("cap_0912"))
    assert head == {"t": "frame", "capture_id": "cap_0912", "camera": "cam1", "seq": 2, "kind": "depth",
                    "t_mono": 81234.5519, "w": 640, "h": 480, "fmt": "png16"}
    assert payload == b"\x89PNGdata"


# ── replay: recorded frames instead of cameras ───────────────────────────────────
def collector_session(root, n=3, size=(32, 24)):
    """Sarah's layout (docs/27): capture_NNNN/<d415|d435>_{color.png, depth_raw.npy}."""
    w, h = size
    for i in range(n):
        d = root / "session_0001" / f"capture_{i:04d}"
        d.mkdir(parents=True)
        for cam in ("d415", "d435"):
            depth = np.full((h, w), 1000 + i, np.uint16)
            depth[:, : w // 4] = 0                                           # a quarter with no depth
            cv2.imwrite(str(d / f"{cam}_color.png"), np.full((h, w, 3), 40 * (i + 1), np.uint8))
            np.save(d / f"{cam}_depth_raw.npy", depth)
    return root / "session_0001"


def test_replay_of_a_collector_session_serves_colour_and_millimetre_depth(tmp_path):
    session = cap.ReplaySession(collector_session(tmp_path))
    assert session.cameras() == ["cam1", "cam2"]            # d415 -> cam1, d435 -> cam2 (config.DEFAULT_CAMERAS)
    rig = cap.CaptureRig([cap.ReplayCamera(n, session) for n in session.cameras()], StubTel(),
                         lambda n: dict(POSE), sleep=lambda s: None).open()
    got = rig.capture(frames=2)
    assert [(f.camera, f.seq, f.payload.kind, f.payload.fmt) for f in got.frames] == [
        ("cam1", 0, "color", "mjpg"), ("cam1", 0, "depth", "png16"), ("cam2", 0, "color", "mjpg"),
        ("cam2", 0, "depth", "png16"), ("cam1", 1, "color", "mjpg"), ("cam1", 1, "depth", "png16"),
        ("cam2", 1, "color", "mjpg"), ("cam2", 1, "depth", "png16")]
    depth = [cv2.imdecode(np.frombuffer(f.payload.data, np.uint8), cv2.IMREAD_UNCHANGED)
             for f in got.frames if f.camera == "cam1" and f.payload.kind == "depth"]
    assert depth[0].dtype == np.uint16 and depth[0][0, -1] == 1000 and depth[1][0, -1] == 1001   # lossless, mm, in order
    assert got.coverage == 0.75 and got.quality_ok is True
    assert [i["model"] for i in got.rig] == ["D415", "D435"]


def test_replay_of_a_pipeline_recording_passes_the_jpeg_through_untouched(tmp_path):
    """perception/pipeline.py's layout. docs/22 §6: the camera's JPEG is never decoded and re-encoded."""
    rec = tmp_path / "scan_000"
    rec.mkdir()
    ok, jpg = cv2.imencode(".jpg", np.random.default_rng(0).integers(0, 255, (72, 256, 3), np.uint8))
    (rec / "cam0.jpg").write_bytes(jpg.tobytes())
    (rec / "capture.json").write_text(json.dumps({"capture_id": "cap_x", "at": "2026-09-19T00:00:00Z",
                                                  "pose": {"x": 0.5, "z": -0.25, "yaw": 1.0},
                                                  "frames": [{"camera": "cam0", "file": "cam0.jpg"}], "rig": {}}))
    session = cap.ReplaySession(tmp_path)
    rig = cap.CaptureRig([cap.ReplayCamera("cam0", session)], StubTel(), lambda n: session.pose_at(n) or dict(POSE),
                         sleep=lambda s: None).open()
    got = rig.capture(frames=1)
    (f,) = got.frames
    assert f.payload.data == jpg.tobytes() and (f.payload.w, f.payload.h) == (256, 72)
    assert got.pose == {"x": 0.5, "z": -0.25, "yaw": 1.0, "source": "replay"}   # the pose the frames were taken at
    assert got.gate == "latch_only"                         # a stereo recording carries no depth


def test_an_empty_replay_dir_says_what_it_wanted(tmp_path):
    with pytest.raises(FileNotFoundError, match="no recorded captures"):
        cap.ReplaySession(tmp_path)


def test_sim_mode_builds_a_rig_with_no_hardware_and_no_driver(tmp_path):
    cfg = C.Config(mode="sim", state_dir=tmp_path)
    rig = cap.build_rig(cfg, StubTel(), sim.SimBase().read, sleep=lambda s: None).open()
    got = rig.capture(frames=1)
    assert got.accepted and got.cameras == ["cam1", "cam2"] and "pyrealsense2" not in sys.modules
    meta = json.loads((tmp_path / "sim_session" / "capture_0000" / "metadata.json").read_text())
    assert meta["synthetic"] is True and "816612060665" not in json.dumps(meta)   # never a real camera's serial


# ── config, and what must never be in a control loop ─────────────────────────────
def test_the_physical_cameras_are_the_default_rig():
    by_name = {s.name: s for s in C.Config.from_env({}).cameras}
    assert (by_name["cam1"].device, by_name["cam1"].model) == ("816612060665", "D415")
    assert (by_name["cam2"].device, by_name["cam2"].model) == ("938422076694", "D435")


def test_a_camera_bound_by_index_is_refused():
    with pytest.raises(ValueError, match="by-path"):
        C.parse_cameras("cam0=v4l2:0")
    (spec,) = C.parse_cameras("cam0=v4l2:/dev/v4l/by-path/platform-xhci-hcd.0-usb-0:1.2:1.0-video-index0")
    assert spec.device.endswith("video-index0")             # a by-path device is full of colons


def test_the_simulated_robot_is_settled_until_it_is_knocked():
    """Why sim.SimBalance exists: robot/telemetry.py's FakeRobot never passes a 0.05 rad/s gate."""
    b = sim.SimBalance()
    settled = [abs(b()["tilt_rate"]) for _ in range(200)]
    assert max(settled) < C.MAX_TILT_RATE
    b.bump()
    assert max(abs(b()["tilt_rate"]) for _ in range(5)) > C.MAX_TILT_RATE


def test_nothing_in_the_capture_or_telemetry_path_makes_a_network_call():
    """docs/11 latency tiers: a network call in the capture or telemetry path stalls the robot on
    a wifi hiccup. The Pi serves HTTP; those modules never make a request.

    `adapter.py` is the one exception and is checked separately below: it is a job path, not a
    control loop, and its one call is to the capture server on this same robot."""
    banned = re.compile(r"^\s*(import|from)\s+(requests|httpx|elasticsearch|urllib\.request|http\.client|aiohttp)\b", re.M)
    for f in sorted((ROOT / "robot").glob("*.py")):
        if f.name == "adapter.py":
            continue
        assert not banned.search(f.read_text()), f"{f.name} imports a network client"


def test_the_adapters_only_outbound_call_is_to_this_robots_own_capture_server():
    """It asks the local server which SLAM generation the live map is, before anything moves. On
    loopback, off the action path only — if it ever pointed off this machine, a motion would wait
    on someone else's network."""
    from robot import adapter
    assert adapter.MAP_GEN_URL.startswith("http://127.0.0.1:") and adapter.MAP_GEN_TIMEOUT_S <= 5
    urls = re.findall(r"https?://[^\s\"']+", (ROOT / "robot" / "adapter.py").read_text())
    assert all(u.startswith(("http://127.0.0.1", "http://localhost")) for u in urls), urls


def test_a_camera_that_was_not_ready_at_startup_is_tried_again(monkeypatch):
    """Our server and the camera daemon it reads both start at boot. Seen on the robot: bbos's
    camera daemon published nothing for minutes after a restart, so cam0 opened `unavailable` —
    and stayed that way until somebody restarted US. Their outage must not become ours."""
    monkeypatch.setattr(cap, "REOPEN_EVERY_S", 0.0)
    calls, cam = [], FakeCam("cam0", [])
    warming = {"n": 0}

    def open_():
        calls.append("open")
        warming["n"] += 1
        if warming["n"] < 3:                                   # the daemon is still warming up
            raise cap.CameraUnavailable("cam0", "bbos published no new camera.head.jpeg within 2.0 s")
    cam.open = open_
    rig = cap.CaptureRig([cam], StubTel(), lambda n: dict(POSE), sleep=lambda s: None).open()
    assert rig.available() == [] and "camera.head.jpeg" in rig.unavailable["cam0"]
    with pytest.raises(cap.CameraUnavailable):                 # second attempt: still warming
        rig.preview("cam0")
    assert rig.capture(frames=1).cameras == ["cam0"]           # third: back, with no restart of ours
    assert rig.unavailable == {} and calls == ["open"] * 3


def test_a_camera_that_stays_down_files_one_issue_not_one_per_retry(monkeypatch):
    monkeypatch.setattr(cap, "REOPEN_EVERY_S", 0.0)
    filed = []
    monkeypatch.setattr(obs, "robot_failure", lambda kind, detail, **tags: filed.append(tags.get("camera")))
    cam = FakeCam("cam0", [])
    cam.open = lambda: (_ for _ in ()).throw(cap.CameraUnavailable("cam0", "did not open"))
    rig = cap.CaptureRig([cam], StubTel(), lambda n: dict(POSE), sleep=lambda s: None).open()
    for _ in range(5):
        with pytest.raises(cap.CameraUnavailable):
            rig.preview("cam0")
    assert filed == ["cam0"]                                   # one issue at startup; the retries are quiet
