"""housebot.py: our point / move jobs to Andrew's housebot edge (PLAN.md §0; plan/roommate/03-interfaces.md §12).

A scripted FAKE edge on loopback plays every failure mode; his REAL edge (scripts/run_edge_api.py --mock,
awzheng/gitirl @ 9582081+, a mock robot) checks the contract when his checkout is present. Every test has
its own clone of the story room and its own ledger. Nothing leaves 127.0.0.1, nothing moves.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

TOKEN = "edge-token-" + "x" * 24


class FakeEdge:
    """His /v1/jobs, scripted: `mode` = ok | fail | retryable | sleep:<s>; bearer-checked; caches by job_id."""

    def __init__(self, token: str = TOKEN):
        self.token, self.mode, self.bodies, self.executions, self.cache = token, "ok", [], [], {}
        self.auth: list[str | None] = []
        edge = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                edge.bodies.append(body)
                edge.auth.append(self.headers.get("Authorization"))
                if self.headers.get("Authorization") != f"Bearer {edge.token}":
                    return self._send(401, {"error": "unauthorized", "detail": "invalid token", "retryable": False})
                if body["job_id"] in edge.cache:
                    return self._send(200, edge.cache[body["job_id"]])
                mode = edge.mode.pop(0) if isinstance(edge.mode, list) else edge.mode
                if mode.startswith("sleep:"):
                    time.sleep(float(mode.split(":")[1]))
                    mode = "ok"
                edge.executions.append(body["job_id"])
                status = {"ok": "succeeded", "fail": "failed", "retryable": "retryable_failure"}[mode]
                res = {"job_id": body["job_id"], "status": status,
                       "message": "caretaker job completed" if mode == "ok" else "simulated failure",
                       "attempts": 1, "actions": [{"action": {"action_type": body["command"]}, "status": status}]}
                edge.cache[body["job_id"]] = res
                self._send(200, res)

            def _send(self, code, doc):
                raw = json.dumps(doc).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def git(repo: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def edge():
    e = FakeEdge()
    yield e
    e.close()


@pytest.fixture()
def api(tmp_path, monkeypatch, edge):
    story = os.environ.get("ROOM_GIT_PATH")
    if not story or not Path(story).exists():
        pytest.skip("no snapshot of the room story")
    room = tmp_path / "room.git"
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", story, str(room)], check=True)
    monkeypatch.setenv("ROOM_GIT_PATH", str(room))
    monkeypatch.setenv("JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("WEB_ALLOWED_COMMANDS", "point,move,restore,checkout")
    monkeypatch.setenv("HOUSEBOT_EDGE_URL", edge.url)
    monkeypatch.setenv("HOUSEBOT_EDGE_TOKEN", TOKEN)
    monkeypatch.setenv("HOUSEBOT_EDGE_TIMEOUT_S", "5")
    monkeypatch.setenv("ANDREW_BRIDGE", "ours")
    monkeypatch.delenv("ANDREW_INTENT_URL", raising=False)
    import events
    seen: list[tuple[str, dict]] = []
    real = events.hub.publish
    monkeypatch.setattr(events.hub, "publish", lambda name, data: (seen.append((name, dict(data))), real(name, data))[1])
    with TestClient(server.app) as c:                 # one loop for the whole test: background dispatches finish
        c.room, c.seen, c.edge = room, seen, edge
        yield c


def terminal(c, job_id: str, timeout: float = 10.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j.get("terminal"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"{job_id} did not finish: {j}")


def states(c, job_id: str) -> list[str]:
    return [d["state"] for n, d in c.seen if n == "job" and d.get("id") == job_id]


def ask(c, text: str, rid: str | None = None) -> dict:
    return c.post("/api/agent/command", json={"type": "user_command", "request_id": rid or uuid.uuid4().hex,
                                              "payload": {"text": text}}).json()


# ── point ───────────────────────────────────────────────────────────────────────────

def test_off_until_an_edge_is_configured(api, monkeypatch):
    monkeypatch.delenv("HOUSEBOT_EDGE_URL")
    j = api.post("/api/object-life/mug_a1b2/point").json()
    assert j["executor"] == "not_connected" and j["dispatch"]["dispatched"] is False
    assert "HOUSEBOT_EDGE_URL" in j["dispatch"]["why"] and api.edge.bodies == []
    assert api.get("/api/housebot").json()["enabled"] is False


def test_the_allow_list_is_the_operators_switch(api, monkeypatch):
    monkeypatch.setenv("WEB_ALLOWED_COMMANDS", "restore")
    j = api.post("/api/object-life/mug_a1b2/point").json()
    assert j["dispatch"]["dispatched"] is False and "WEB_ALLOWED_COMMANDS" in j["dispatch"]["why"]
    assert api.edge.bodies == []


def test_a_point_reaches_the_edge_once_and_its_answer_reaches_the_dashboard(api):
    r = api.post("/api/object-life/mug_a1b2/point", headers={"Idempotency-Key": "click-1"})
    j = r.json()
    assert r.status_code == 202 and j["executor"] == "housebot-edge" and j["dispatch"]["dispatched"] is True
    done = terminal(api, j["job_id"])
    assert done["state"] == "succeeded" and done["result"]["status"] == "succeeded"
    sent = api.edge.bodies[0]
    assert set(sent) == {"job_id", "command", "object_id", "target_pose", "zone", "pointing_at", "frame", "units"}
    assert (sent["command"], sent["object_id"], sent["frame"]) == ("point", "mug_a1b2", "world_z_up")
    assert all(isinstance(sent["target_pose"][k], (int, float)) for k in "xyz")
    assert api.edge.auth == [f"Bearer {TOKEN}"]
    assert states(api, j["job_id"]) == ["dispatching", "succeeded"], "the dashboard hears the start and the end"
    for n, d in api.seen:                              # each event says WHERE, like the planned-point event
        if n == "job" and d.get("id") == j["job_id"]:
            assert (d["target_pose"], d["zone"], d["frame"]) == (sent["target_pose"], sent["zone"], "world_z_up")
    again = api.post("/api/object-life/mug_a1b2/point", headers={"Idempotency-Key": "click-1"}).json()
    assert again["job_id"] == j["job_id"] and again["dispatch"]["replayed"] is True
    assert len(api.edge.bodies) == 1, "the same click, retried, is never sent twice"


def test_where_is_the_mug_five_times_in_a_row(api):
    """Andrew's stop gate: the point demo five times in a row, through the panel's own endpoint."""
    ids = []
    for _ in range(5):
        b = ask(api, "Where is my mug?")
        assert b["ok"] is True and b["path"] == "caretaker" and b["served_by"] == "gitspace:grammar", b.get("error")
        assert b["intent"]["intent"] == "find" and b["intent"]["object_query"] == "mug"
        a = b["action"]
        assert a["kind"] == "job" and a["result"]["resolved"]["object_id"] == "mug_a1b2"
        assert a["result"]["dispatch"]["dispatched"] is True
        ids.append(a["result"]["job"]["job_id"])
    assert len(set(ids)) == 5, "five asks are five points"
    assert all(terminal(api, i)["state"] == "succeeded" for i in ids)
    assert sorted(api.edge.executions) == sorted(ids)


def test_a_failure_is_reported_as_it_came_back_and_filed_once(api):
    api.edge.mode = "fail"
    j = api.post("/api/object-life/mug_a1b2/point").json()
    done = terminal(api, j["job_id"])
    assert (done["state"], done["error"], done["attempts"]) == ("failed", "failed", 1)
    api.edge.mode = "retryable"
    done = terminal(api, api.post("/api/object-life/mug_a1b2/point").json()["job_id"])
    assert (done["state"], done["error"]) == ("failed", "retryable_failure"), "his retryable is not our success"


def test_a_wrong_token_is_a_failure_not_a_retry(api, monkeypatch):
    monkeypatch.setenv("HOUSEBOT_EDGE_TOKEN", "not-it")
    done = terminal(api, api.post("/api/object-life/mug_a1b2/point").json()["job_id"])
    assert (done["state"], done["error"], done["http_status"]) == ("failed", "unauthorized", 401)
    assert len(api.edge.bodies) == 1


def test_an_edge_that_is_not_there_is_retried_then_undelivered(api, monkeypatch):
    with socket.socket() as s:                        # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    monkeypatch.setenv("HOUSEBOT_EDGE_URL", f"http://127.0.0.1:{dead}")
    done = terminal(api, api.post("/api/object-life/mug_a1b2/point").json()["job_id"])
    assert (done["state"], done["error"], done["attempts"]) == ("undelivered", "edge_unreachable", 3)


def test_no_answer_is_unknown_and_never_resent(api, monkeypatch):
    monkeypatch.setenv("HOUSEBOT_EDGE_TIMEOUT_S", "1")
    api.edge.mode = "sleep:2"
    done = terminal(api, api.post("/api/object-life/mug_a1b2/point").json()["job_id"])
    assert (done["state"], done["error"]) == ("unknown", "edge_no_answer") and "look at the robot" in done["message"]
    assert len(api.edge.bodies) == 1, "it may have run: never re-sent"


# ── move ────────────────────────────────────────────────────────────────────────────

def _misplace(room: Path, n: int) -> list[str]:
    """Move n desk objects in the working tree (a rescan that saw them elsewhere). Returns their ids."""
    import re
    moved = []
    for f in sorted(room.glob("zones/desk/*.yaml"))[:n]:
        f.write_text(re.sub(r"(\n\s+x: )[-0-9.]+", r"\g<1>0.05", f.read_text(), count=1))
        moved.append(f.stem)
    return moved


def test_tidy_sends_one_move_per_object_in_order(api):
    moved = _misplace(api.room, 2)
    b = ask(api, "clean up the desk")
    assert b["ok"] is True and b["intent"]["intent"] == "tidy" and b["intent"]["zone"] == "desk", b.get("error")
    jobs = b["action"]["result"]["jobs"]
    assert b["action"]["kind"] == "jobs" and sorted(j["ops"][0]["object_id"] for j in jobs) == sorted(moved)
    for j in jobs:
        assert terminal(api, j["job_id"])["state"] == "succeeded"
    sent = api.edge.bodies
    assert [s["job_id"] for s in sent] == [j["job_id"] for j in jobs], "in the planner's order, one at a time"
    for s in sent:
        op = s["ops"][0]
        assert s["command"] == "move" and len(s["ops"]) == 1 and op["op"] == "moved" and s["frame"] == "world_z_up"
        assert set(op) >= {"object_id", "class", "zone", "from", "to"} and op["from"]["x"] == 0.05


def test_tidy_stops_at_the_first_failure(api):
    _misplace(api.room, 2)
    api.edge.mode = ["fail", "ok"]
    jobs = ask(api, "tidy up")["action"]["result"]["jobs"]
    assert len(jobs) == 2
    first, second = (terminal(api, j["job_id"]) for j in jobs)
    assert first["state"] == "failed" and second["state"] == "skipped" and "ended failed" in second["message"]
    assert len(api.edge.bodies) == 1, "after a failure the rest is never sent"


def test_a_one_move_job_from_the_ledger_goes_as_move_and_closes_there(api):
    tidied = git(api.room, "log", "--format=%H", "--grep=^the bench, tidied", "-n1")
    j = api.post("/api/command", json={"command": "restore", "args": {"ref": tidied}}).json()
    assert len(j["plan"]["ops"]) == 1 and j["plan"]["ops"][0]["kind"] == "move"
    r = api.post(f"/api/jobs/{j['job_id']}/dispatch")
    assert r.status_code == 202 and r.json()["dispatched"] is True
    end = time.monotonic() + 10
    while time.monotonic() < end and not api.get(f"/api/jobs/{j['job_id']}").json().get("terminal"):
        time.sleep(0.05)
    led = api.get(f"/api/jobs/{j['job_id']}").json()
    assert (led["state"], led["claimed_by"], led["op_status"][0]["status"]) == ("succeeded", "housebot-edge", "success")
    sent = api.edge.bodies[0]
    assert sent["command"] == "move" and sent["ops"][0]["object_id"] == j["plan"]["ops"][0]["object_id"]
    again = api.post(f"/api/jobs/{j['job_id']}/dispatch")
    assert again.status_code in (200, 409) and len(api.edge.bodies) == 1


def test_a_move_request_is_a_proposal_not_a_job(api):
    b = ask(api, "put the mug on the shelf")
    assert b["action"]["kind"] == "proposal" and b["action"]["result"]["approval_required"] is True
    assert api.edge.bodies == []


# ── his real edge, when his checkout is here ──────────────────────────────────────────

def _his_edge_repo() -> Path | None:
    from bridge import andrew
    repo = Path(andrew.JSONL.repo)
    return repo if (repo / "scripts" / "run_edge_api.py").exists() else None


def test_his_real_edge_runs_our_point_and_move(api, monkeypatch):
    repo = _his_edge_repo()
    if repo is None:
        pytest.skip("no gitirl-agent checkout with scripts/run_edge_api.py (awzheng/gitirl @ 9582081+)")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GITIRL_", "HOUSEBOT_"))}
    env["HOUSEBOT_EDGE_TOKEN"] = TOKEN
    proc = subprocess.Popen([sys.executable, "scripts/run_edge_api.py", "--mock", "--port", str(port)], cwd=repo,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        import urllib.request
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        monkeypatch.setenv("HOUSEBOT_EDGE_URL", f"http://127.0.0.1:{port}")
        b = ask(api, "where is the mug")
        done = terminal(api, b["action"]["result"]["job"]["job_id"])
        assert done["state"] == "succeeded", done
        assert done["result"]["status"] == "succeeded" and done["result"]["actions"][0]["action"]["action_type"] == "POINT_AT_OBJECT"
        _misplace(api.room, 1)
        jobs = ask(api, "tidy the desk")["action"]["result"]["jobs"]
        moved = terminal(api, jobs[0]["job_id"])
        assert moved["state"] == "succeeded" and moved["result"]["actions"][0]["action"]["action_type"] == "MOVE_OBJECT", moved
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_text_only_his_layer_understands_still_becomes_our_job(api, monkeypatch):
    """No grammar match → his intent service → an Intent we validate → OUR logic makes the job."""
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            got.update(req)
            raw = json.dumps({"request_id": req["request_id"], "intent": "find", "object_query": "mug", "object_id": None,
                              "zone": None, "when": None, "raw_text": req["text"], "confidence": 0.91,
                              "source": "openai"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            return
    svc = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=svc.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("INTENT_URL", f"http://127.0.0.1:{svc.server_address[1]}")
        b = ask(api, "the thing I drink coffee from, where did it end up")
        assert b["ok"] is True and (b["path"], b["served_by"]) == ("caretaker", "andrew:intent"), b.get("error")
        assert b["intent"]["source"] == "openai" and got["text"].startswith("the thing I drink coffee")
        assert b["action"]["kind"] == "job" and terminal(api, b["action"]["result"]["job"]["job_id"])["state"] == "succeeded"
    finally:
        svc.shutdown()
        svc.server_close()


def test_a_simulated_robot_is_recorded_as_such(api, monkeypatch):
    """Master's rule for the MVP: every result says whether a SIMULATED robot did it. His edge does not
    pass the adapter's flag through, so we take it from the answer (flag anywhere, or a SIMULATED: message)."""
    import housebot
    assert housebot.simulated_of({"status": "succeeded", "actions": [{"simulated": True}]}) is True
    assert housebot.simulated_of({"actions": [{"message": "SIMULATED: pointing at mug_a1b2 from 0.60 m"}]}) is True
    assert housebot.simulated_of({"status": "succeeded", "message": "done", "actions": [{"status": "success"}]}) is None
    assert housebot.simulated_of({"simulated": False}) is False
    j = api.post("/api/object-life/mug_a1b2/point").json()      # the fake edge answers without a flag
    done = terminal(api, j["job_id"])
    assert done["state"] == "succeeded" and done["simulated"] is None, "unknown stays unknown, never assumed real"
    ev = [d for n, d in api.seen if n == "job" and d.get("id") == j["job_id"]][-1]
    assert "simulated" in ev


def test_the_panels_own_job_says_it_was_dispatched(api):
    """The job object itself must not still read "queued (no executor connected)" once the dispatcher has
    taken it: two fields of one answer disagreeing is how a live demo looks broken (perception-f5)."""
    b = ask(api, "point at the mug")
    r = b["action"]["result"]
    assert r["dispatch"]["dispatched"] is True and r["executor"] == "housebot-edge"
    assert r["job"]["executor"] == "housebot-edge" and r["job"]["state"] == "dispatching"
    assert "no executor connected" not in r["job"]["state"] and "housebot edge" in r["job"]["detail"]
    assert terminal(api, r["job"]["job_id"])["state"] == "succeeded"


def test_a_job_that_was_not_sent_says_why_in_the_job_too(api, monkeypatch):
    monkeypatch.setenv("WEB_ALLOWED_COMMANDS", "restore")            # point not allow-listed
    r = ask(api, "point at the mug")["action"]["result"]
    assert r["executor"] == "not_connected" and r["dispatch"]["dispatched"] is False
    assert "not sent" in r["job"]["detail"] and "WEB_ALLOWED_COMMANDS" in r["job"]["detail"]
