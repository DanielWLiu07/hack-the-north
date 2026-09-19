"""jobs.py: the job ledger Andrew's remote edge polls and reports to (ANDREW-HANDOFF.md §2b).
Each test gets its OWN clone of the story room and its own ledger: nothing here touches the shared
snapshot, the real room.git, or ~/.cache/gitspace/jobs."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import jobs  # noqa: E402
import server  # noqa: E402

TOKEN = "t" * 40


def git(repo: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def api(tmp_path, monkeypatch):
    story = os.environ.get("ROOM_GIT_PATH")
    if not story or not Path(story).exists():
        pytest.skip("no snapshot of the room story")
    room = tmp_path / "room.git"
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", story, str(room)], check=True)
    monkeypatch.setenv("ROOM_GIT_PATH", str(room))
    monkeypatch.setenv("JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("WEB_ALLOWED_COMMANDS", "restore,checkout,revert")
    monkeypatch.setenv("GITIRL_CLOUD_TOKEN", TOKEN)
    monkeypatch.delenv("JOBS_REAL_MOTION", raising=False)
    c = TestClient(server.app)
    c.room = room
    c.tidied = git(room, "log", "--format=%H", "--grep=^the bench, tidied", "-n1")
    return c


def restore(api):
    r = api.post("/api/command", json={"command": "restore", "args": {"ref": api.tidied}})
    assert r.status_code in (200, 202), r.text
    return r.json()


def report(api, job_id, body, token=TOKEN):
    h = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return api.post(f"/api/jobs/{job_id}/result", json=body, headers=h)


def all_ops(job, status="success"):
    return [{"seq": o["seq"], "object_id": o["object_id"], "status": status, "attempts": 1} for o in job["plan"]["ops"]]


def test_a_restore_job_is_an_ordered_plan_in_a_declared_frame(api):
    j = restore(api)
    assert j["state"] == "planned" and j["terminal"] is False and j["executable"] is True and j["motion"] == "mock_only"
    assert (j["frame"], j["units"]) == ("world_z_up", {"position": "m", "yaw": "deg", "duration": "s"})
    p = j["plan"]
    assert p["contract"] == "gitspace.plan/1" and p["frame"] == "world_z_up" and p["target_sha"] == api.tidied
    assert [o["seq"] for o in p["ops"]] == list(range(1, len(p["ops"]) + 1)) and p["ops"], "ordered, 1..n"
    assert j["progress"] == {"ops_total": len(p["ops"]), "ops_done": 0, "ops_failed": 0, "ops_skipped": 0,
                             "ops_pending": len(p["ops"]), "fraction": 0.0}
    assert j["job_id"] == jobs.job_id_for("restore", j["target"], j["head"], j["observed_room"]), "recomputable"
    got = api.get(f"/api/jobs/{j['job_id']}").json()
    assert {k: got[k] for k in ("job_id", "plan", "state", "frame")} == {k: j[k] for k in ("job_id", "plan", "state", "frame")}


def test_asking_twice_is_the_same_job_and_the_id_is_named_by_what_it_would_do(api):
    a, b = restore(api), restore(api)
    assert a["job_id"] == b["job_id"] and a["replayed"] is False and b["replayed"] is True
    assert restore(api)["job_id"] == api.post("/api/command", json={"command": "restore", "args": {"ref": api.tidied[:9]}}).json()["job_id"]
    other = api.post("/api/command", json={"command": "checkout", "args": {"ref": api.tidied}}).json()
    assert other["job_id"] != a["job_id"], "another command is another job"
    mug = next(api.room.glob("zones/*/mug_*.yaml"))                    # a rescan saw the mug somewhere else
    mug.write_text(re.sub(r"(\n\s+x: )[-0-9.]+", r"\g<1>0.05", mug.read_text(), count=1))
    moved = restore(api)
    assert moved["plan"] is not None, moved["plan_unavailable"]                # still a valid room, just a new one
    assert moved["job_id"] != a["job_id"] and moved["replayed"] is False and moved["observed_room"] != a["observed_room"]


def test_the_result_write_needs_the_token_and_fails_closed_without_one(api, monkeypatch):
    j = restore(api)
    body = {"run_id": "edge-1", "status": "running"}
    for tok, code in ((None, 401), ("wrong" * 10, 401), ("Bearer", 401)):
        r = report(api, j["job_id"], body, token=tok)
        assert r.status_code == code and r.json()["error"] == "unauthorized" and TOKEN not in r.text
    assert report(api, "job_" + "0" * 16, body, token=None).status_code == 401, "no token: not even whether a job exists"
    monkeypatch.setenv("GITIRL_CLOUD_TOKEN", "short")
    assert report(api, j["job_id"], body, token="short").json()["error"] == "edge_auth_unconfigured"
    monkeypatch.delenv("GITIRL_CLOUD_TOKEN")
    assert report(api, j["job_id"], body).status_code == 503
    assert api.get(f"/api/jobs/{j['job_id']}").json()["state"] == "planned", "nothing was recorded"


def test_a_job_runs_once_and_its_first_terminal_result_stands(api):
    j = restore(api)
    jid, ops = j["job_id"], all_ops(j)
    r = report(api, jid, {"run_id": "edge-1", "status": "running", "ops": [{**ops[0], "status": "running"}]})
    assert r.status_code == 200 and r.json()["state"] == "running" and r.json()["executor"] == "edge:edge-1"
    assert r.json()["executable"] is False, "running: nobody else may start it"
    assert report(api, jid, {"run_id": "edge-2", "status": "running"}).json()["error"] == "claimed"
    done = {"run_id": "edge-1", "status": "success", "message": "all placed", "ops": ops}
    r = report(api, jid, done)
    assert r.json()["state"] == "succeeded" and r.json()["terminal"] and r.json()["progress"]["fraction"] == 1.0
    again = report(api, jid, done)                                     # a retry after a dropped connection
    assert again.status_code == 200 and again.json()["replayed"] is True and again.json()["reports"] == 2
    lie = report(api, jid, {**done, "status": "failed"})
    assert lie.status_code == 409 and lie.json()["error"] == "result_conflict" and lie.json()["stored"]["status"] == "success"
    assert restore(api)["state"] == "succeeded", "asking again returns the finished job; it is never run twice"


def test_reports_are_checked_against_the_plan(api):
    j = restore(api)
    jid, ops = j["job_id"], all_ops(j)
    assert report(api, jid, {"run_id": "e", "status": "running", "ops": [{**ops[0], "seq": 99}]}).status_code == 422
    assert report(api, jid, {"run_id": "e", "status": "running", "ops": [{**ops[0], "object_id": "not_it"}]}).status_code == 422
    assert report(api, jid, {"run_id": "e", "status": "done"}).status_code == 422
    assert report(api, jid, {"run_id": "no spaces", "status": "running"}).status_code == 422
    assert report(api, jid, {"run_id": "e", "status": "running", "ops": [{**ops[0], "status": "failed"}]}).status_code == 200
    assert report(api, jid, {"run_id": "e", "status": "running", "ops": [ops[0]]}).json()["error"] == "op_terminal"
    r = report(api, jid, {"run_id": "e", "status": "success", "ops": ops[1:]})
    assert r.status_code == 422, "success with a failed op is not success"
    r = report(api, jid, {"run_id": "e", "status": "failed", "message": "gripper slipped", "ops": ops[1:]})
    assert r.json()["state"] == "failed" and r.json()["progress"]["ops_failed"] == 1


def test_poses_coming_back_must_declare_our_frame(api):
    j = restore(api)
    seen = {"objects": [{"object_id": "mug_a1b2", "pose": {"x": 0.4, "y": 0.2, "z": 0.75, "yaw": 15}}]}
    bad = report(api, j["job_id"], {"run_id": "e", "status": "running", "observations": seen})
    assert bad.status_code == 422 and bad.json()["error"] == "frame_mismatch"
    ok = report(api, j["job_id"], {"run_id": "e", "status": "running", "frame": "world_z_up", "observations": seen})
    assert ok.status_code == 200


def test_a_plan_for_a_room_that_moved_on_cannot_start(api):
    j = restore(api)
    git(api.room, "commit", "-q", "--allow-empty", "-m", "someone committed")
    got = api.get(f"/api/jobs/{j['job_id']}").json()
    assert got["executable"] is False and got["why_not"].startswith("head_moved")
    r = report(api, j["job_id"], {"run_id": "e", "status": "running"})
    assert r.status_code == 409 and r.json()["error"] == "head_moved"


def test_revert_has_no_read_only_plan_and_says_so(api):
    j = api.post("/api/command", json={"command": "revert", "args": {"ref": "HEAD"}}).json()
    assert j["plan"] is None and "roomctl plans it after it commits" in j["plan_unavailable"] and j["executable"] is False
    assert (j["plan_only"], j["why_not_code"]) == (True, "plan_only"), "machine-readable BEFORE an edge tries"
    got = api.get(f"/api/jobs/{j['job_id']}").json()
    assert (got["plan_only"], got["why_not_code"], got["executable"]) == (True, "plan_only", False)
    r = report(api, j["job_id"], {"run_id": "e", "status": "running"}).json()
    assert (r["error"], r["why_not_code"]) == ("not_executable", "plan_only")
    r = restore(api)
    assert (r["plan_only"], r["why_not_code"], r["executable"]) == (False, None, True)


def test_the_server_says_which_verbs_make_executable_jobs(api):
    j = api.get("/api/commands").json()["jobs"]
    assert j["executable"] == ["restore", "checkout"] and j["plan_only"] == ["revert"]


def test_a_refused_verb_says_why_and_how_to_enable_it(api, monkeypatch):
    monkeypatch.setenv("WEB_ALLOWED_COMMANDS", "status,checkout")
    r = api.post("/api/command", json={"command": "restore", "args": {"ref": "HEAD"}})
    body = r.json()
    assert r.status_code == 403 and body["error"] == "command_not_allowed" and set(body) == {"error", "detail", "retryable"}
    assert "EXECUTABLE job" in body["detail"] and "adding 'restore'" in body["detail"] and "checkout, status" in body["detail"]


def test_real_motion_is_the_users_switch(api, monkeypatch):
    assert restore(api)["motion"] == "mock_only"
    monkeypatch.setenv("JOBS_REAL_MOTION", "1")
    assert api.get(f"/api/jobs/{restore(api)['job_id']}").json()["motion"] == "real"


def test_ids_that_are_not_ids(api):
    assert api.get("/api/jobs/job_zz").status_code == 422
    assert api.get("/api/jobs/job_" + "a" * 16).json()["error"] == "not_found"


def test_the_published_fixture_is_a_job_this_server_would_make():
    fx = Path(__file__).resolve().parents[2] / "docs" / "fixtures" / "restore-job.json"
    if not fx.exists():
        pytest.skip("docs/fixtures/restore-job.json not generated yet (scripts/make_job_fixture.py)")
    j = json.loads(fx.read_text())
    assert j["job_id"] == jobs.job_id_for(j["command"], j["target"], j["head"], j["observed_room"])
    assert j["command"] == "restore" and j["frame"] == j["plan"]["frame"] == "world_z_up" and j["plan"]["contract"] == "gitspace.plan/1"
    assert [o["seq"] for o in j["plan"]["ops"]] == list(range(1, len(j["plan"]["ops"]) + 1))
    assert [o["seq"] for o in j["op_status"]] == [o["seq"] for o in j["plan"]["ops"]]
