"""roomctl/robot_client.py against a fake Pi: the API boundary mocked, never hit.

The shapes are docs/16's: POST /drive {target{x, z, yaw rad}} and POST /arm {action, pose{x,
y, z, yaw deg}} answer 202 + job_id; the outcome arrives through `jobs.wait()` (the telemetry
hub's Jobs, fed by /stream). And the retry rules: never re-send a motion that may have started.
"""
import json
import math
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roomctl.executor import BasePose, MockRobot, Op, Plan, RobotError, Spot, execute  # noqa: E402
from roomctl.robot_client import HttpRobot, NotSent, to_drive_target, web_inlet  # noqa: E402
from roomctl.state import Extents, Pose  # noqa: E402


class FakePi:
    """Scripted responses per path; records every request."""

    def __init__(self, script: dict | None = None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.sent: list[tuple[str, str, dict | None]] = []
        self.n = 0

    def __call__(self, method, path, body):
        self.sent.append((method, path, body))
        queue = self.script.get(path)
        if queue:
            r = queue.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        self.n += 1
        return 202, {"job_id": f"job_{self.n:04x}", "accepted": True}


class FakeJobs:
    def __init__(self, outcomes: dict | None = None, timeout: set | None = None):
        self.outcomes, self.timeout, self.waited = outcomes or {}, timeout or set(), []

    def wait(self, job_id, timeout):
        self.waited.append((job_id, timeout))
        if job_id in self.timeout:
            raise TimeoutError(job_id)
        return self.outcomes.get(job_id, {"state": "done", "result": {"grasped": True}})


def robot(pi, jobs=None):
    return HttpRobot(pi, FakeJobs() if jobs is None else jobs, sleep=lambda s: None)


MUG = Pose(0.42, 0.18, 0.75, 15)


def test_drive_speaks_bbs_axes_and_radians():
    pi = FakePi()
    robot(pi).drive(BasePose(0.30, -0.20, 90.0))
    (method, path, body), = pi.sent
    assert (method, path) == ("POST", "/drive")
    assert body["target"] == {"x": 0.3, "z": -0.2, "yaw": round(math.pi / 2, 4)}   # z = world y
    assert to_drive_target(BasePose(1.0, 2.0, 180.0))["yaw"] == round(math.pi, 4)


def test_arm_speaks_the_world_frame_and_degrees():
    pi = FakePi()
    r = robot(pi)
    r.pick("mug_a1b2", MUG)
    r.place("mug_a1b2", Pose(0.61, 0.18, 0.75, 40), "desk")
    assert pi.sent[0][2] == {"action": "pick", "pose": {"x": 0.42, "y": 0.18, "z": 0.75, "yaw": 15},
                             "approach": "top_down", "speed": 0.15}
    assert pi.sent[1][2]["action"] == "place" and pi.sent[1][2]["pose"]["yaw"] == 40


def test_a_failed_job_is_a_robot_error_with_the_pis_code():
    jobs = FakeJobs({"job_0001": {"state": "failed", "error": "grasp_slipped",
                                  "detail": "gripper closed to 2mm, expected 78mm"}})
    with pytest.raises(RobotError) as e:
        robot(FakePi(), jobs).pick("mug_a1b2", MUG)
    assert e.value.code == "grasp_slipped" and "78mm" in e.value.detail


def test_a_job_that_never_reports_times_out_rather_than_hanging():
    with pytest.raises(RobotError) as e:
        robot(FakePi(), FakeJobs(timeout={"job_0001"})).drive(BasePose(0, 0, 0))
    assert e.value.code == "job_timeout"


def test_busy_and_never_sent_are_retried():
    pi = FakePi({"/arm": [(409, {"error": "busy", "retryable": True}), NotSent("refused")]})
    robot(pi).pick("mug_a1b2", MUG)
    assert len(pi.sent) == 3


def test_a_motion_that_may_have_started_is_never_resent():
    pi = FakePi({"/arm": [(500, {"error": "internal", "detail": "servo bus"})]})
    with pytest.raises(RobotError) as e:
        robot(pi).pick("mug_a1b2", MUG)
    assert e.value.code == "internal" and len(pi.sent) == 1


def test_timeout_after_sending_is_not_retried():
    pi = FakePi({"/arm": [RobotError("robot_timeout", "no answer")]})
    with pytest.raises(RobotError):
        robot(pi).pick("mug_a1b2", MUG)
    assert len(pi.sent) == 1


def test_an_unreachable_pi_gives_up_with_a_clear_code():
    pi = FakePi({"/drive": [NotSent("refused")] * 10})
    with pytest.raises(RobotError) as e:
        robot(pi).drive(BasePose(0, 0, 0))
    assert e.value.code == "robot_unreachable" and len(pi.sent) == 4


def test_not_balanced_refuses_the_arm():
    pi = FakePi({"/arm": [(409, {"error": "not_balanced", "detail": "recovering"})]})
    with pytest.raises(RobotError) as e:
        robot(pi).pick("mug_a1b2", MUG)
    assert e.value.code == "not_balanced"


def test_voice_and_led_never_stop_a_pick():
    pi = FakePi({"/say": [NotSent("refused")], "/led": [(500, {"error": "gpio"})]})
    r = robot(pi)
    r.say("Merge conflict.")
    r.led("conflict")
    assert r.dropped == 2


def test_no_job_stream_is_an_error_not_a_hang():
    with pytest.raises(RobotError) as e:
        HttpRobot(FakePi(), None).drive(BasePose(0, 0, 0))
    assert e.value.code == "no_job_stream"


def spot(x):
    return Spot("desk", Pose(x, 0.0, 0.75, 0), Extents(0.08, 0.08, 0.10))


def test_execute_through_the_http_robot_calls_in_order_and_narrates():
    base = BasePose(-0.2, 0.0, 0.0)
    p = Plan(ops=[Op("move", "mug_a1b2", spot(0.3), spot(0.6), base, BasePose(0.1, 0.0, 0.0))])
    pi, events = FakePi(), []
    out = execute(p, robot(pi), publish=lambda e, d: events.append((e, d)), job_id="job_test")
    assert [path for _, path, _ in pi.sent] == ["/drive", "/arm", "/drive", "/arm"]
    assert out.done and not out.failed
    states = [d["state"] for _, d in events]
    assert states == ["planned", "moving_to_pick", "placing", "done"]
    assert all(e == "job" and d["id"] == "job_test" for e, d in events)
    assert events[-1][1]["progress"] == 1.0 and events[-1][1]["result"]["done"] == 1


def test_a_slip_is_narrated_as_a_failed_job():
    p = Plan(ops=[Op("move", "mug_a1b2", spot(0.3), spot(0.6))])
    events = []
    execute(p, MockRobot(fail={"mug_a1b2"}, out=lambda s: None), publish=lambda e, d: events.append(d))
    assert [d["state"] for d in events] == ["planned", "moving_to_pick", "op_failed", "failed"]
    assert events[2]["error"] == "grasp_slipped"


def test_web_inlet_posts_the_documented_shape_and_survives_a_dead_dashboard():
    got = []

    class Inlet(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            got.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), Inlet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        web_inlet(f"127.0.0.1:{srv.server_port}")("job", {"id": "job_1", "state": "planned"})
    finally:
        srv.shutdown()
    assert got == [("/api/internal/event", {"event": "job", "data": {"id": "job_1", "state": "planned"}})]
    web_inlet("127.0.0.1:9")("job", {"id": "x"})  # nothing listening: returns, doesn't raise


def test_both_robots_speak_one_span_vocabulary(monkeypatch):
    """robot.drive / robot.pick / robot.place, tagged robot=mock|pi: `room revert` from the CLI
    traces like the agent's, and a simulated pick never reads as a real one."""
    import contextlib
    import obs
    spans = []
    monkeypatch.setattr(obs, "span", lambda op, desc="", **d: spans.append((op, desc, d.get("robot")))
                        or contextlib.nullcontext())
    MockRobot(out=lambda s: None).pick("mug_a1b2", MUG)
    robot(FakePi()).pick("mug_a1b2", MUG)
    robot(FakePi()).drive(BasePose(0, 0, 0))
    assert spans == [("robot.pick", "arm.pick mug_a1b2", "mock"), ("robot.pick", "arm.pick mug_a1b2", "pi"),
                     ("robot.drive", "drive pi", "pi")]


def test_a_failure_reported_on_the_stream_is_not_filed_twice(monkeypatch):
    """docs/10 D25: the hub files /stream failures (with the lean and the frame); this client
    files only what the hub never sees — and tags it with the capture, so it can be resolved."""
    import obs
    filed = []
    monkeypatch.setattr(obs, "robot_failure", lambda kind, detail, **tags: filed.append((kind, tags)))
    jobs = FakeJobs({"job_0001": {"state": "failed", "error": "grasp_slipped", "detail": "2mm"}})
    with pytest.raises(RobotError):
        robot(FakePi(), jobs).pick("mug_a1b2", MUG)
    assert filed == []
    r = robot(FakePi({"/drive": [NotSent("refused")] * 10}))
    r.context["capture_id"] = "cap_0042"
    with pytest.raises(RobotError):
        r.drive(BasePose(0, 0, 0))
    assert filed == [("robot_unreachable", {"action": "drive", "capture_id": "cap_0042"})]
