"""robot_sentry.py with Sentry's REST API mocked at httpx: nothing is resolved, nothing is sent."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import obs  # noqa: E402
import robot_sentry as rs  # noqa: E402
from roomctl.executor import MockRobot, Op, Plan, RobotError, Spot, execute  # noqa: E402
from roomctl.state import Extents, Pose  # noqa: E402


class FakeSentry:
    """Issues in memory; records every request in order."""

    def __init__(self, issues):
        self.issues, self.calls = {i["id"]: dict(i) for i in issues}, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path, dict(req.url.params), json.loads(req.content or b"null")))
        if req.method == "GET" and req.url.path.endswith("/issues/"):
            q = req.url.params["query"]
            want = dict(t.split(":", 1) for t in q.split() if ":" in t and not t.startswith("is:"))
            rows = [i for i in self.issues.values() if ("is:unresolved" not in q or i["status"] == "unresolved")
                    and all(i["tags"].get(k) == v for k, v in want.items())]
            return httpx.Response(200, json=rows)
        iid = req.url.path.rstrip("/").split("/")[-2 if req.url.path.endswith("/comments/") else -1]
        if req.method == "PUT":
            self.issues[iid]["status"] = json.loads(req.content)["status"]
        return httpx.Response(200, json={})


def client(fake):
    return rs.SentryIssues("org", "tok", client=httpx.Client(base_url="https://x/api/0", transport=httpx.MockTransport(fake)))


def issue(iid, kind="grasp_slipped", cap="cap_1", level="error", status="unresolved", **tags):
    return {"id": iid, "shortId": f"GITSPACE-{iid}", "title": f"robot: {kind} — detail", "level": level,
            "status": status, "tags": {"failure_kind": kind, "capture_id": cap, **tags}}


def test_resolve_failures_notes_the_timeline_then_resolves_only_the_matching_open_ones():
    fake = FakeSentry([issue("1"), issue("2", cap="cap_other"), issue("3", status="resolved"),
                       issue("4", kind="fell_over")])
    assert client(fake).resolve_failures("grasp_slipped", "cap_1", "🤖 retried") == ["GITSPACE-1"]
    writes = [(m, p) for m, p, _, _ in fake.calls if m != "GET"]
    assert writes == [("POST", "/api/0/organizations/org/issues/1/comments/"),   # the note first,
                      ("PUT", "/api/0/organizations/org/issues/1/")]             # then resolved
    assert fake.issues["1"]["status"] == "resolved" and fake.issues["2"]["status"] == "unresolved"


def spot(x):
    return Spot("desk", Pose(x, 0.0, 0.75, 0), Extents(0.08, 0.08, 0.10))


class SlipsOnce(MockRobot):
    def __init__(self, **kw):
        super().__init__(out=lambda s: None, **kw)
        self.slips = 1

    def pick(self, object_id, pose):
        if self.slips:
            self.slips -= 1
            raise RobotError("grasp_slipped", "2mm")
        super().pick(object_id, pose)


def test_the_executor_path_retries_a_slip_and_the_robot_resolves_it_only_after_verification(monkeypatch):
    reports = []
    monkeypatch.setattr(obs, "robot_failure", lambda kind, detail, frame=None, **tags: reports.append((kind, frame, tags)))
    world = {"mug_a1b2": spot(0.30)}
    frames = SimpleNamespace(get=lambda cap, cam: b"jpeg-of-" + cap.encode())
    robot = rs.SelfHealingRobot(SlipsOnce(world=world), "cap_1", "a3f9c1", report=True, frames=frames, fw="mock-arm")
    out = execute(Plan(ops=[Op("move", "mug_a1b2", spot(0.30), spot(0.60))]), robot)
    assert len(out.done) == 1 and not out.failed                         # the retry landed it
    assert robot.healed == [{"kind": "grasp_slipped", "object_id": "mug_a1b2", "detail": "2mm", "attempts": 2}]
    kind, frame, tags = reports[0]
    assert kind == "grasp_slipped" and frame == b"jpeg-of-cap_1"         # the photo of the capture
    assert tags["capture_id"] == "cap_1" and tags["object_id"] == "mug_a1b2" and tags["fw"] == "mock-arm"
    fake = FakeSentry([issue("7")])
    assert robot.resolve_verified(client(fake), verified=set()) == []   # no proof, no resolve
    assert fake.issues["7"]["status"] == "unresolved"
    assert robot.resolve_verified(client(fake), verified={"mug_a1b2"}) == ["GITSPACE-7"]
    note = next(body for m, p, _, body in fake.calls if m == "POST")["text"]
    assert note.startswith("🤖 Resolved by the robot.") and "rescan came back clean" in note and "a3f9c1" in note


def test_a_failure_a_retry_cannot_fix_is_not_retried(monkeypatch):
    monkeypatch.setattr(obs, "robot_failure", lambda *a, **k: pytest.fail("reported a non-retryable"))

    class Unreachable(MockRobot):
        def pick(self, object_id, pose):
            raise RobotError("unreachable_pose", "outside the envelope")
    robot = rs.SelfHealingRobot(Unreachable(out=lambda s: None), "cap_1", report=True)
    with pytest.raises(RobotError):
        robot.pick("mug", Pose(0, 0, 0, 0))
    assert robot.healed == []


def test_the_mirror_leds_the_worst_state_says_new_issues_once_and_beats_the_room(tmp_path):
    room = tmp_path / "room.git"
    subprocess.run(["git", "init", "-q", str(room)], check=True)
    fake = FakeSentry([issue("1", level="warning")])
    leds, said, beats = [], [], []
    m = rs.IssueMirror(client(fake), leds.append, said.append, room,
                       heartbeat=lambda slug, status, monitor_config=None: beats.append((slug, status)),
                       beat_every=0, say_gap=0)
    assert m.tick()["state"] == "dirty" and said == []                  # a warning: amber; old news: silent
    m.tick()
    assert leds == ["dirty"]                                             # written on change only
    fake.issues["2"] = issue("2", kind="fell_over")
    fake.issues["2"]["title"] = "robot: fell_over — balanced went 0; pitch 1.2, peak tilt_rate 2.84"
    assert m.tick()["state"] == "error" and leds == ["dirty", "error"]
    assert said == ["Sentry issue 2. I fell over. Peak tilt rate 2.8."]
    fake.issues["1"]["status"] = fake.issues["2"]["status"] = "resolved"
    assert m.tick()["state"] == "clean" and leds[-1] == "clean"          # the robot resolved it: green
    (room / "mug.yaml").write_text("x: 1\n")                             # the room gets messy
    assert m.tick()["state"] == "dirty"
    assert beats[0] == ("room-clean", "ok") and beats[-1] == ("room-clean", "error")   # it FAILS its heartbeat


def test_spoken_lines():
    assert rs.spoken({"shortId": "GITSPACE-12", "title": "robot: grasp_slipped — 2mm"}) == "Sentry issue 12. My grasp slipped."
    assert rs.spoken({"shortId": "GITSPACE-9", "title": "Cron failure: room-clean"}) == "Sentry issue 9. The room failed its heartbeat."
