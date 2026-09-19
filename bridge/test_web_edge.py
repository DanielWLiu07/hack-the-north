"""The two vocabulary deltas with gitirl-agent@b4f3e07, and the decipherer's safety — all local."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "web")]
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from bridge import andrew  # noqa: E402
from bridge.test_bridge import room_repo  # noqa: E402,F401  (fixture)


def test_his_decipherer_cannot_reach_a_robot_even_if_ours_is_configured(monkeypatch):
    if not andrew.JSONL.available():
        pytest.skip("no gitirl-agent checkout at ANDREW_REPO")
    monkeypatch.setenv("GITIRL_ROBOT_BASE_URL", "http://127.0.0.1:9")    # a dead port: a leak would try it
    monkeypatch.setenv("GITIRL_CLOUD_BASE_URL", "http://127.0.0.1:9")
    assert not [k for k in andrew.JsonlBridge.env() if k.startswith("GITIRL_")]
    jb = andrew.JsonlBridge()
    try:
        msgs = jb.decipher({"type": "user_command", "request_id": "safe1",
                            "payload": {"text": "set my room back to study mode"}})
    finally:
        jb.close()
    assert msgs[0]["type"] == "parsed_command" and msgs[0]["payload"]["command"] == "restore"
    # his MOCK answered (it restores its own box_A world) — the HTTP robot adapter was never built
    assert [m["payload"]["status"] for m in msgs if m["type"] == "command_result"] == ["RESTORE_COMPLETE"]


def test_plan_command_status_gets_the_read_path_not_a_400(room_repo, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setenv("WEB_ALLOWED_COMMANDS", "status,diff,log,revert,checkout")
    monkeypatch.setenv("JOBS_DIR", str(tmp_path / "jobs"))           # the revert below makes a job: not in ~/.cache
    import graph_api
    app = FastAPI()
    app.include_router(graph_api.router)
    c = TestClient(app)
    r = c.post("/api/command", json={"command": "status", "args": {}})          # his DanielAPIClient's body
    assert r.status_code == 200 and r.json()["kind"] == "read" and "job_id" not in r.json()
    r = c.post("/api/command", json={"command": "diff", "args": {"ref": "study"}}).json()
    assert r["kind"] == "read" and {o["object_id"] for o in r["result"]["ops"]} == {"mug_a1", "book_b2"}
    r = c.post("/api/command", json={"command": "log", "args": {}}).json()
    assert [x["subject"] for x in r["result"]["commits"]] == ["book moved", "mug moved", "study"]
    assert c.post("/api/command", json={"command": "revert", "args": {"ref": "HEAD"}}).status_code == 202  # plans unchanged


def test_his_robot_events_map_to_our_job_event_explicitly():
    import events
    assert events.from_edge("status", {"clean": True}) == ("status", {"clean": True})     # ours: untouched
    ev, d = events.from_edge("robot_action_result", {"request_id": "r7", "result": {"status": "RESTORE_COMPLETE", "attempts": 1}})
    assert (ev, d["id"], d["kind"], d["state"]) == ("job", "r7", "robot_action_result", "done")
    ev, d = events.from_edge("robot_action_result", {"job_id": "j1", "result": {"status": "FAILED"}})
    assert d["state"] == "failed"
    for his, ours in (("success", "done"), ("failed", "failed"), ("retryable", "retrying")):   # his ActionStatus @ b4f3e07
        assert events.from_edge("robot_action_result", {"result": {"status": his}})[1]["state"] == ours, his
    ev, d = events.from_edge("robot_status", {"status": "balancing", "message": "recovering", "metadata": {"job_id": "j2"}})
    assert (ev, d["id"], d["state"], d["detail"]) == ("job", "j2", "balancing", "recovering")
    ev, d = events.from_edge("robot_observation", {"observation": {"objects": [], "metadata": {"frame": "world_z_up"}}})
    assert (ev, d["state"], d["id"]) == ("job", "observed", "robot")
    assert "robot_telemetry" not in events.EDGE_EVENTS                     # anything else: still refused
