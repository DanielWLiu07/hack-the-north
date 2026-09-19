"""robot/server.py against docs/16-api.md, in sim: no camera, no robot, no network.

The whole process runs — the 50 Hz telemetry tap, the capture rig replaying a recording, the job
runner — behind Starlette's in-process TestClient, so no port is opened. The wire shapes asserted
here are the ones roomctl/robot_client.py and telemetry/hub.py already speak.
"""
import base64
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from robot import capture as cap  # noqa: E402
from robot import config as C  # noqa: E402
from robot import server  # noqa: E402
from robot.telemetry import SIGNALS, Telemetry  # noqa: E402


@pytest.fixture(autouse=True)
def no_live_sentry(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "")


def settle(seconds=0.25):
    time.sleep(seconds)          # the telemetry ring has to cover 100 ms BEFORE the first latch


@pytest.fixture
def client(tmp_path):
    app = server.create_app(C.Config(mode="sim", state_dir=tmp_path), time_scale=0.0)
    with TestClient(app) as c:
        settle()
        yield c


def until(ws, want, limit=400):
    """Read /stream until want(msg); everything seen on the way is returned too."""
    seen = []
    for _ in range(limit):
        m = ws.receive_json()
        seen.append(m)
        if want(m):
            return m, seen
    raise AssertionError(f"never arrived; saw types {sorted({m.get('t') for m in seen})}")


# ── POST /capture ────────────────────────────────────────────────────────────────
def test_capture_with_curl_returns_pixels_pose_and_the_gates_numbers(client):
    r = client.post("/capture", json={"frames": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capture_id"] == "cap_0001" and body["cameras"] == ["cam1", "cam2"]
    assert body["pose"] == {"x": 0.0, "z": 0.0, "yaw": 0.0} and body["pose_source"] == "sim"
    assert body["quality_ok"] is True and body["gate"] == "full" and body["attempt"] == 1
    assert body["skew_ms"] < 25 and body["tilt_rate_max"] < 0.05 and body["coverage"] > 0.60
    assert body["started_at"].endswith("Z") and body["t_capture_mono"] > 0
    assert len(body["frames"]) == 2 * 2 * 2                  # cameras x frames x (colour + depth)
    assert body["inline"] is True                            # nobody on /frames: the pixels come back here
    colour = next(f for f in body["frames"] if f["kind"] == "color")
    img = cv2.imdecode(np.frombuffer(base64.b64decode(colour["jpeg_b64"]), np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (colour["height"], colour["width"])
    depth = next(f for f in body["frames"] if f["kind"] == "depth")
    mm = cv2.imdecode(np.frombuffer(base64.b64decode(depth["png_b64"]), np.uint8), cv2.IMREAD_UNCHANGED)
    assert mm.dtype == np.uint16 and 1000 < int(np.median(mm[mm > 0])) < 2500      # millimetres, not metres
    assert "sentry_trace_id" not in body                     # Sentry is off: no ids of a trace nobody sent


def test_capture_brackets_the_pixels_on_stream_and_sends_them_on_frames(client):
    with client.websocket_connect("/stream") as stream, client.websocket_connect("/frames") as frames:
        until(stream, lambda m: m["t"] == "telemetry")       # registered: nothing published from here is missed
        r = client.post("/capture", json={"frames": 1})
        body = r.json()
        assert body["inline"] is False and body["frames_clients"] == 1
        assert all("jpeg_b64" not in f and "png_b64" not in f for f in body["frames"])
        begin, _ = until(stream, lambda m: m["t"] == "capture_begin")
        end, _ = until(stream, lambda m: m["t"] == "capture_end")
        assert begin["capture_id"] == end["capture_id"] == body["capture_id"]
        assert begin["frames_expected"] == end["frames_sent"] == 4
        for k in ("pose", "skew_ms", "tilt_rate_max", "t_capture_mono", "cameras", "quality_ok"):
            assert begin[k] == body[k]
        got = [cap.unpack_frame(frames.receive_bytes()) for _ in range(begin["frames_expected"])]
    assert {h["capture_id"] for h, _ in got} == {body["capture_id"]}     # assembled by identity, not arrival
    assert sorted((h["camera"], h["kind"], h["fmt"]) for h, _ in got) == [
        ("cam1", "color", "mjpg"), ("cam1", "depth", "png16"), ("cam2", "color", "mjpg"), ("cam2", "depth", "png16")]
    assert all(payload[:2] == b"\xff\xd8" for h, payload in got if h["fmt"] == "mjpg")


def test_a_knocked_robot_is_rejected_then_captured_in_the_next_quiet_window(client):
    """docs/22 §4, end to end on the real 50 Hz ring: knock it, the latch lands in the ringing,
    the capture is rejected with its numbers, and the retry waits for calm."""
    with client.websocket_connect("/stream") as stream:
        until(stream, lambda m: m["t"] == "telemetry")
        assert client.post("/sim/bump").status_code == 200
        r = client.post("/capture", json={"frames": 1})
        assert r.status_code == 200, r.text
        body = r.json()
        rejected, _ = until(stream, lambda m: m["t"] == "capture_rejected")
        until(stream, lambda m: m["t"] == "capture_begin" and m["capture_id"] == body["capture_id"])
    assert body["attempt"] >= 2 and body["tilt_rate_max"] < 0.05
    assert rejected["tilt_rate_max"] > 0.05 and rejected["rejected_by"] == ["tilt_rate_max"]
    assert rejected["capture_id"] != body["capture_id"] and rejected["quality_ok"] is False


def test_a_robot_that_never_settles_answers_409_with_every_attempts_numbers(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "QUIET_TIMEOUT_S", 0.05)
    shaking = Telemetry(source=lambda: {**dict.fromkeys(SIGNALS, 0.0), "tilt_rate": 0.3, "balanced": 1.0})
    app = server.create_app(C.Config(mode="sim", state_dir=tmp_path), tel=shaking)
    with TestClient(app) as c:
        settle()
        r = c.post("/capture", json={"frames": 1})
    assert r.status_code == 409
    e = r.json()
    assert e["error"] == "capture_rejected" and e["retryable"] is True and "tilt_rate_max" in e["detail"]
    assert [a["attempt"] for a in e["attempts"]] == [1, 2, 3]
    assert all(a["tilt_rate_max"] == 0.3 and a["quality_ok"] is False for a in e["attempts"])


def test_a_slow_capture_never_blocks_the_event_loop(tmp_path):
    """A capture is a second of decoding. It runs in a worker thread, so the loop that feeds
    /stream its 10 msg/s — and answers /pose — never waits on a camera."""
    calls = []

    class SlowCam:
        def __init__(self, name):
            self.name = name

        def open(self): pass
        def close(self): pass
        def unlatch(self): pass
        def info(self): return {"camera": self.name}
        def grab(self, latch_no): return time.monotonic()

        def retrieve(self, quality):
            calls.append(self.name)
            time.sleep(0.25)
            return cap.Shot([cap.Payload("color", "mjpg", b"\xff\xd8x", 4, 2)], coverage=0.9)

    from robot import sim
    tel = Telemetry(source=sim.SimBalance())
    rig = cap.CaptureRig([SlowCam("cam0"), SlowCam("cam1")], tel, lambda n: {"x": 0, "z": 0, "yaw": 0}, tmp_path)
    app = server.create_app(C.Config(mode="sim", state_dir=tmp_path), rig=rig, tel=tel)
    with TestClient(app) as c:
        settle()
        result = {}
        t = threading.Thread(target=lambda: result.update(r=c.post("/capture", json={"frames": 2})))
        t.start()                                            # 2 cameras x 2 frames x 250 ms: a full second
        while not calls:
            time.sleep(0.005)
        t0 = time.monotonic()
        pose = c.get("/pose")
        answered_in = time.monotonic() - t0
        still_capturing = t.is_alive()
        busy = c.post("/capture")                            # and a second capture is refused, not queued
        t.join()
    assert pose.status_code == 200 and answered_in < 0.2 and still_capturing
    assert busy.status_code == 409 and busy.json()["error"] == "busy" and busy.json()["retryable"] is True
    assert result["r"].status_code == 200


def test_capture_errors_use_the_one_error_shape(client):
    r = client.post("/capture", json={"cameras": ["cam7"]})
    assert r.status_code == 503
    assert r.json() == {"error": "camera_unavailable", "retryable": True, "camera": "cam7",
                        "available": ["cam1", "cam2"], "detail": "not configured; this rig has ['cam1', 'cam2']"}
    for bad in ({"frames": 0}, {"frames": "4"}, {"quality": 101}, {"cameras": "cam0"}):
        r = client.post("/capture", json=bad)
        assert r.status_code == 400 and r.json()["error"] == "bad_request" and r.json()["retryable"] is False
    r = client.post("/capture", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json()["error"] == "bad_request"


def test_capture_runs_with_a_bare_post_as_the_readme_promises(client):
    r = client.post("/capture")                              # curl -X POST <pi>:8080/capture
    assert r.status_code == 200 and len(r.json()["frames"]) == 2 * 4 * 2


# ── GET /pose · POST /drive ──────────────────────────────────────────────────────
def test_pose_is_bbs_odometry_axes_with_balance_and_a_pi_timestamp(client):
    p = client.get("/pose").json()
    assert set(p) == {"x", "z", "yaw", "source", "odom_residual_m", "balanced", "ts", "t_mono"}
    assert p["balanced"] is True and p["source"] == "sim" and p["odom_residual_m"] == pytest.approx(0.004)


def test_drive_answers_202_at_once_and_finishes_on_the_stream(client):
    with client.websocket_connect("/stream") as stream:
        until(stream, lambda m: m["t"] == "telemetry")
        t0 = time.monotonic()
        r = client.post("/drive", json={"target": {"x": 1.8, "z": 0.4, "yaw": 1.57}, "speed": 0.25, "timeout_s": 30})
        assert r.status_code == 202 and (time.monotonic() - t0) < 0.5
        job = r.json()
        assert job["accepted"] is True and job["job_id"].startswith("job_")
        done, _ = until(stream, lambda m: m["t"] == "job" and m["id"] == job["job_id"] and m["state"] in ("done", "failed"))
    assert done["state"] == "done" and done["result"]["final_pose"]["x"] == 1.8
    assert client.get("/pose").json()["z"] == 0.4            # /pose and /capture now see the robot THERE


def test_a_newer_drive_supersedes_the_one_still_running(tmp_path):
    app = server.create_app(C.Config(mode="sim", state_dir=tmp_path), time_scale=1.0)
    with TestClient(app) as c, c.websocket_connect("/stream") as stream:
        until(stream, lambda m: m["t"] == "telemetry")
        first = c.post("/drive", json={"target": {"x": 5.0, "z": 0.0, "yaw": 0.0}}).json()["job_id"]
        until(stream, lambda m: m["t"] == "job" and m["id"] == first and m["state"] == "driving")
        c.post("/drive", json={"target": {"x": 0.0, "z": 0.0, "yaw": 0.0}})
        ended, _ = until(stream, lambda m: m["t"] == "job" and m["id"] == first and m["state"] == "failed")
    assert ended["error"] == "job_superseded"


# ── POST /arm ────────────────────────────────────────────────────────────────────
PICK = {"action": "pick", "pose": {"x": 0.42, "y": 0.18, "z": 0.75, "yaw": 15}, "approach": "top_down", "speed": 0.15}


def test_arm_narrates_a_pick_on_the_stream(client):
    with client.websocket_connect("/stream") as stream:
        until(stream, lambda m: m["t"] == "telemetry")
        r = client.post("/arm", json=PICK)                   # the body roomctl/robot_client.py sends
        assert r.status_code == 202 and r.json()["estimated_s"] == 28.0
        jid = r.json()["job_id"]
        _, seen = until(stream, lambda m: m["t"] == "job" and m["id"] == jid and m["state"] == "done")
    states = [m["state"] for m in seen if m["t"] == "job" and m["id"] == jid]
    assert states == ["moving_to_pick", "grasping", "lifting", "done"]
    assert seen[-1]["result"]["grasped"] is True and seen[-1]["result"]["simulated"] is True


def test_the_arm_is_refused_while_the_robot_is_down(client):
    assert client.post("/sim/fall").status_code == 200
    time.sleep(0.1)                                          # a few 20 ms ticks: the tap has seen it
    r = client.post("/arm", json=PICK)
    assert r.status_code == 409 and r.json()["error"] == "not_balanced" and r.json()["retryable"] is False
    assert client.get("/pose").json()["balanced"] is False


def test_never_two_arm_jobs_at_once(tmp_path):
    app = server.create_app(C.Config(mode="sim", state_dir=tmp_path), time_scale=1.0)     # a real 28 s pick
    with TestClient(app) as c:
        settle(0.1)
        assert c.post("/arm", json=PICK).status_code == 202
        r = c.post("/arm", json={"action": "stow"})
    assert r.status_code == 409 and r.json() == {"error": "busy", "detail": "an arm job is already running",
                                                  "retryable": True}


def test_arm_validates_before_it_moves(client):
    for bad in ({"action": "throw"}, {"action": "pick"}, {"action": "place", "pose": {"x": 0.1, "y": "left", "z": 0.7}}):
        r = client.post("/arm", json=bad)
        assert r.status_code == 400 and r.json()["error"] == "bad_request", bad
    assert client.post("/arm", json={"action": "stow"}).status_code == 202   # stow needs no pose


# ── POST /say · POST /led ────────────────────────────────────────────────────────
def test_say_and_led(client):
    r = client.post("/say", json={"text": "Merge conflict. The mug was moved in both branches.", "voice": "elevenlabs"})
    assert r.status_code == 202 and r.json()["duration_s"] > 0 and r.json()["job_id"].startswith("job_")
    for state in ("clean", "dirty", "conflict", "working", "error"):
        assert client.post("/led", json={"state": state}).json() == {"state": state}
    assert client.get("/healthz").json()["led"] == "error"
    assert client.post("/led", json={"state": "purple"}).status_code == 400
    assert client.post("/say", json={"text": "  "}).status_code == 400


def test_without_the_real_actuators_hardware_mode_says_so_rather_than_pretending(tmp_path):
    """robot/nav.py, arm.py, audio.py, led.py are not built. A 202 for a drive that never happens
    would be a lie the executor acts on."""
    rig = cap.CaptureRig([], Telemetry(source=dict), lambda n: {}, tmp_path)
    app = server.create_app(C.Config(mode="hardware", state_dir=tmp_path), rig=rig)
    with TestClient(app) as c:
        for path, body in (("/drive", {"target": {"x": 1, "z": 0, "yaw": 0}}), ("/arm", {"action": "stow"}),
                           ("/say", {"text": "hi"}), ("/led", {"state": "clean"})):
            r = c.post(path, json=body)
            assert r.status_code == 503 and r.json()["error"] == "backend_unavailable", path
        r = c.post("/capture")                               # and no cameras is camera_unavailable, not a crash
        assert r.status_code == 503 and r.json()["error"] == "camera_unavailable"
        assert c.post("/sim/bump").status_code == 404        # nothing simulated to knock
        assert c.get("/pose").json()["source"] == "none"     # a placeholder must not read as a measured pose


# ── WebSocket /stream ────────────────────────────────────────────────────────────
def test_hello_comes_first_and_carries_the_clock_pairing_the_hub_requires(client):
    with client.websocket_connect("/stream") as stream:
        hello = stream.receive_json()
        assert hello["t"] == "hello" and hello["hz"] == 50 and hello["signals"] == list(SIGNALS)
        for k in ("boot_id", "t_mono_base", "t_wall_base", "t_mono_now"):    # telemetry/hub.py rejects telemetry without
            assert k in hello
        assert hello["cameras"] == ["cam1", "cam2"] and hello["mode"] == "sim" and hello["simulated"] is True
        assert [(r["camera"], r["model"]) for r in hello["rig"]] == [("cam1", "D415"), ("cam2", "D435")]
        tm, _ = until(stream, lambda m: m["t"] == "telemetry" and not m.get("replay"))
        assert set(tm["signals"]) == set(SIGNALS) and len(tm["signals"]["pitch"]) == 5      # 5 samples / 100 ms


def test_a_reconnect_replays_only_what_was_missed(client):
    with client.websocket_connect("/stream") as stream:
        boot = stream.receive_json()["boot_id"]
        tm, _ = until(stream, lambda m: m["t"] == "telemetry" and not m.get("replay"))
        last = tm["from_mono"] + (len(tm["signals"]["pitch"]) - 1) / tm["hz"]
    time.sleep(0.3)
    with client.websocket_connect(f"/stream?since={last:.6f}&boot={boot}") as stream:
        stream.receive_json()
        first, _ = until(stream, lambda m: m["t"] == "telemetry")
    assert first.get("replay") is True and first["from_mono"] == pytest.approx(last + 1 / 50, abs=1e-5)


def test_the_pi_log_reaches_the_stream(client):
    import logging
    with client.websocket_connect("/stream") as stream:
        until(stream, lambda m: m["t"] == "telemetry")
        logging.getLogger("gitspace.capture").warning("cam2 dropped 2 of 4 frames")
        msg, _ = until(stream, lambda m: m["t"] == "log")
    assert msg["level"] == "warn" and msg["msg"] == "cam2 dropped 2 of 4 frames" and msg["ts"].endswith("Z")


# ── the contract, as the auditor and the laptop's client see it ──────────────────
def test_all_six_endpoints_and_both_sockets_are_routed(client):
    routes = {getattr(r, "path", "") for r in client.app.routes}
    assert {"/capture", "/pose", "/drive", "/arm", "/say", "/led", "/stream", "/frames"} <= routes


def test_roomctls_http_client_drives_this_server_unchanged(client):
    """The real roomctl.robot_client.HttpRobot, its transport swapped for the in-process app."""
    from roomctl.executor import BasePose
    from roomctl.robot_client import HttpRobot
    from roomctl.state import Pose

    finished = {}

    class StreamJobs:                                        # what telemetry/hub.py's Jobs does, over /stream
        def wait(self, job_id, timeout):
            return finished[job_id]

    def send(method, path, body):
        with client.websocket_connect("/stream") as stream:
            until(stream, lambda m: m["t"] == "telemetry")
            r = client.request(method, path, json=body)
            if r.status_code == 202 and "job_id" in r.json():
                jid = r.json()["job_id"]
                finished[jid], _ = until(stream, lambda m: m["t"] == "job" and m["id"] == jid
                                         and m["state"] in ("done", "failed"))
        return r.status_code, r.json()

    robot = HttpRobot(send, StreamJobs(), sleep=lambda s: None)
    robot.drive(BasePose(0.30, -0.20, 90.0))
    robot.pick("mug_a1b2", Pose(0.42, 0.18, 0.75, 15))
    robot.led("working")
    assert robot.pose()["x"] == 0.3 and robot.dropped == 0


# ── GET /camera/<name>.jpg — a live view is not a capture ────────────────────────
def test_the_preview_is_a_jpeg_with_its_age_and_costs_no_capture(client):
    with client.websocket_connect("/stream") as stream:
        until(stream, lambda m: m["t"] == "telemetry")
        r = client.get("/camera/cam1.jpg")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
        assert r.headers["cache-control"] == "no-store" and r.headers["x-boot-id"]
        assert float(r.headers["x-t-mono"]) > 0 and 0 <= int(r.headers["x-frame-age-ms"]) < 1000
        for _ in range(5):
            client.get("/camera/cam1.jpg")
        assert client.post("/capture", json={"frames": 1}).json()["capture_id"] == "cap_0001"   # no ids were burnt
        _, seen = until(stream, lambda m: m["t"] == "capture_end")
    assert [m["capture_id"] for m in seen if m["t"] == "capture_begin"] == ["cap_0001"]          # and nothing announced


def test_the_camera_is_read_at_most_once_per_interval_however_many_ask(client):
    first = client.get("/camera/cam1.jpg")
    for _ in range(20):
        again = client.get("/camera/cam1.jpg")
    assert again.content == first.content and again.headers["x-t-mono"] == first.headers["x-t-mono"]
    p = client.get("/healthz").json()["preview"]
    assert p["reads"] == 1 and p["served"] == 21
    time.sleep(0.3)                                          # past ROBOT_PREVIEW_MIN_INTERVAL_MS (250)
    assert client.get("/camera/cam1.jpg").headers["x-t-mono"] != first.headers["x-t-mono"]
    assert client.get("/healthz").json()["preview"]["reads"] == 2


def test_an_unknown_camera_is_404_and_a_depth_camera_previews_its_colour(client):
    r = client.get("/camera/cam9.jpg")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    assert client.get("/camera/cam2.jpg").content[:2] == b"\xff\xd8"


def test_a_preview_never_contends_with_a_capture_it_serves_what_it_has(tmp_path):
    from robot import sim
    calls = []

    class SlowCam:
        name = "cam0"
        def open(self): pass
        def close(self): pass
        def unlatch(self): calls.append("unlatch")
        def info(self): return {"camera": "cam0"}
        def grab(self, latch_no): calls.append("grab"); return time.monotonic()

        def retrieve(self, quality):
            time.sleep(0.3)
            return cap.Shot([cap.Payload("color", "mjpg", b"\xff\xd8" + bytes([len(calls)]), 4, 2)], coverage=0.9)

    tel = Telemetry(source=sim.SimBalance())
    rig = cap.CaptureRig([SlowCam()], tel, lambda n: {"x": 0, "z": 0, "yaw": 0}, tmp_path)
    rig.preview_min_interval_s = 0.0                         # always due: only the capture can hold it back
    app = server.create_app(C.Config(mode="sim", state_dir=tmp_path), rig=rig, tel=tel)
    with TestClient(app) as c:
        settle()
        assert c.get("/camera/cam0.jpg").status_code == 200  # one read, cached
        got = {}
        t = threading.Thread(target=lambda: got.update(r=c.post("/capture", json={"frames": 2})))
        t.start()
        while calls.count("grab") < 2:
            time.sleep(0.005)
        grabs = calls.count("grab")
        t0 = time.monotonic()
        r = c.get("/camera/cam0.jpg")                        # the capture holds the camera for 0.6 s
        assert r.status_code == 200 and time.monotonic() - t0 < 0.2 and calls.count("grab") == grabs
        t.join()
    assert got["r"].status_code == 200                       # and the capture never noticed


def test_a_stale_picture_is_refused_not_shown(tmp_path):
    from robot import sim

    class Frozen:
        name = "cam0"
        def open(self): pass
        def close(self): pass
        def unlatch(self): pass
        def info(self): return {"camera": "cam0"}
        def grab(self, latch_no): return time.monotonic() - 5.0          # the daemon's newest frame: 5 s old
        def retrieve(self, quality): return cap.Shot([cap.Payload("color", "mjpg", b"\xff\xd8x", 4, 2)])

    tel = Telemetry(source=sim.SimBalance())
    app = server.create_app(C.Config(mode="sim", state_dir=tmp_path), tel=tel,
                            rig=cap.CaptureRig([Frozen()], tel, lambda n: {}, tmp_path))
    with TestClient(app) as c:
        r = c.get("/camera/cam0.jpg")
    assert r.status_code == 503 and r.json()["error"] == "camera_unavailable" and "5.0 s old" in r.json()["detail"]


# ── ROBOT_ALLOW: who may talk to a robot that has no auth ────────────────────────
def allowed_app(tmp_path, allow):
    return server.create_app(C.Config(mode="sim", state_dir=tmp_path, allow=allow), time_scale=0.0)


def test_a_stranger_on_the_wifi_gets_no_picture_no_capture_and_no_stream(tmp_path):
    app = allowed_app(tmp_path, ("127.0.0.1", "10.37.20.56", "100.64.0.0/10"))
    with TestClient(app, client=("10.37.99.12", 50000)) as stranger:
        for method, path in (("GET", "/camera/cam1.jpg"), ("POST", "/capture"), ("GET", "/healthz"),
                             ("GET", "/events?limit=1"), ("POST", "/arm")):
            r = stranger.request(method, path)
            assert r.status_code == 403 and r.json()["error"] == "forbidden", path
        for ws in ("/stream", "/frames"):
            with pytest.raises(Exception):                   # closed 1008 before it was ever accepted
                with stranger.websocket_connect(ws):
                    pass
        assert stranger.get("/camera/cam1.jpg", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403


def test_the_laptop_and_the_tailnet_get_in(tmp_path):
    app = allowed_app(tmp_path, ("127.0.0.1", "10.37.20.56", "100.64.0.0/10"))
    for peer in ("10.37.20.56", "100.117.116.94", "127.0.0.1", "::ffff:10.37.20.56"):
        with TestClient(app, client=(peer, 50000)) as c:
            time.sleep(0.05)
            assert c.get("/healthz").status_code == 200, peer
            with c.websocket_connect("/stream") as ws:
                assert ws.receive_json()["t"] == "hello"


def test_unset_means_open_as_before_and_config_reads_the_list():
    assert C.Config.from_env({}).allow == ()
    assert C.Config.from_env({"ROBOT_ALLOW": "127.0.0.1, 100.64.0.0/10 ,"}).allow == ("127.0.0.1", "100.64.0.0/10")
    with pytest.raises(ValueError):
        server.PeerAllowList(None, ("not-an-address",))      # a typo must not silently mean "nobody" or "everybody"


# ── Sentry on the HTTP path: the integration owns the transaction, the capture runs in a thread ──
@pytest.fixture
def sentry_http(monkeypatch):
    """The REAL sentry_sdk with its FastAPI integration, into memory. obs.init is what the server
    calls; here it initialises the same SDK with a transport that keeps the envelopes."""
    import sentry_sdk
    from sentry_sdk.transport import Transport
    import obs
    envelopes = []

    class InMemory(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope)

    def init(role):
        sentry_sdk.init(dsn="http://public@localhost:9/1", transport=InMemory, traces_sample_rate=1.0, server_name=role)
        return True
    monkeypatch.setattr(obs, "init", init)
    try:
        yield lambda: [i.payload.json for e in envelopes for i in e.items if i.type == "transaction"]
    finally:
        sentry_sdk.get_global_scope().set_client(None)


def test_a_capture_over_http_lands_in_sentry_with_its_spans_and_numbers(sentry_http, tmp_path):
    app = server.create_app(C.Config(mode="sim", state_dir=tmp_path), time_scale=0.0)
    with TestClient(app) as c:
        settle()
        body = c.post("/capture", json={"frames": 1}).json()
    (tx,) = [t for t in sentry_http() if t["transaction"].endswith("/capture")]
    assert tx["server_name"] == "robot" and tx["contexts"]["trace"]["op"] == "http.server"
    ops = [s["op"] for s in tx["spans"]]                      # spans made in the WORKER THREAD, on the request's transaction
    assert "robot.capture" in ops and "robot.latch" in ops and "robot.capture_gate" in ops and ops.count("robot.retrieve") == 2
    assert tx["tags"]["capture_id"] == body["capture_id"]
    m = tx["measurements"]
    assert m["skew_ms"]["value"] == body["skew_ms"] and m["tilt_rate_max"]["value"] == pytest.approx(body["tilt_rate_max"])
    assert body["sentry_trace_id"] == tx["contexts"]["trace"]["trace_id"]        # the Elastic <-> Sentry join, on the wire


def test_the_laptops_trace_is_continued_not_restarted(sentry_http, tmp_path):
    app = server.create_app(C.Config(mode="sim", state_dir=tmp_path), time_scale=0.0)
    trace, parent = "a" * 32, "b" * 16
    with TestClient(app) as c:
        settle()
        body = c.post("/capture", json={"frames": 1}, headers={"sentry-trace": f"{trace}-{parent}-1"}).json()
        c.get("/healthz", headers={"sentry-trace": f"{'c' * 32}-{parent}-0"})    # a probe that says "do not sample me"
    txs = sentry_http()
    (tx,) = [t for t in txs if t["transaction"].endswith("/capture")]
    assert tx["contexts"]["trace"]["trace_id"] == trace and tx["contexts"]["trace"]["parent_span_id"] == parent
    assert body["sentry_trace_id"] == trace                  # docs/16 §6: room status and the robot's latch, ONE waterfall
    assert not [t for t in txs if t["transaction"].endswith("/healthz")]        # and an unsampled probe stays unsampled


def test_healthz_says_sentry_is_off_when_there_is_no_dsn(client):
    assert client.get("/healthz").json()["sentry"] == {"live": False}


def test_healthz_says_whether_sentry_is_refusing_us(sentry_http, tmp_path):
    import datetime as dt
    import sentry_sdk
    app = server.create_app(C.Config(mode="sim", state_dir=tmp_path / "b"), time_scale=0.0)
    with TestClient(app) as c:
        assert c.get("/healthz").json()["sentry"] == {"live": True, "rate_limited": {}}
        sentry_sdk.get_client().transport._disabled_until = {                     # what a 429 leaves behind
            "transaction": dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=90)}
        limited = c.get("/healthz").json()["sentry"]["rate_limited"]
    assert list(limited) == ["transaction"] and 80 <= limited["transaction"] <= 90
