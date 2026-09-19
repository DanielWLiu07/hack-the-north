"""web/roommate_api.py — the caretaker dashboard's API, on the pinned snapshot of the room story (conftest).
What is real (the CI answer, blame) is checked against git; what is waiting for its backend must SAY so."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import room  # noqa: E402
import roommate_api  # noqa: E402
import server  # noqa: E402


def git(*args: str) -> str:
    return subprocess.run(["git", "--no-optional-locks", "-C", str(room.room_path()), *args], capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def api():
    return TestClient(server.app)


def test_the_ci_answer_is_gits_own_working_tree_and_never_invents_a_heartbeat(api):
    j = api.get("/api/room/ci").json()
    assert j["state"] == "clean" and j["branch"] == "main" and j["changes"] == [] and git("rev-parse", "HEAD").startswith(j["head"])
    assert j["since"] is None and j["heartbeat"] == {"slug": "room-clean", "last": None, "at": None}, "the watch loop owns these"
    drifted = roommate_api.ci_from({"clean": False, "branch": "main", "head": "1a668ec", "conflicts": [], "merging": None,
                                    "changes": [{"type": "moved", "object_id": "mug_a1b2", "zone": "desk", "delta_m": 0.19}]})
    assert drifted["state"] == "dirty" and drifted["changes"][0]["object_id"] == "mug_a1b2"
    assert roommate_api.ci_from({"clean": False, "conflicts": [{"object_id": "lamp_2d9b"}], "changes": []})["state"] == "conflict"


def test_blame_names_the_commit_that_moved_it_and_what_that_commit_did(api):
    mug = api.get("/api/blame/mug_a1b2").json()
    assert mug["moved_in"]["subject"].startswith("afternoon: mug moved") and mug["moved_in"]["sha"] == git("rev-parse", "HEAD")
    assert mug["what"] == "moved" and mug["delta_m"] == pytest.approx(0.19, abs=0.01) and mug["from"] != mug["to"] and mug["frame"] == "world_z_up"
    assert mug["frame_url"] is None and "no camera frame was stored for cap_0005" in mug["frame_reason"], "no picture is claimed until one exists"
    assert mug["moved_in"]["capture_id"] == "cap_0005", "the capture that made that commit (fixture room-events)"
    tool = api.get("/api/blame/tool_4f2a").json()                       # gone from the room: blame still knows who took it away
    assert tool["what"] == "removed" and tool["moved_in"]["subject"] == "the bench, tidied" and tool["to"] is None
    assert api.get("/api/blame/nobody_0000").status_code == 404 and api.get("/api/blame/..%2Fetc").status_code in (404, 422)


@pytest.fixture()
def own_room(monkeypatch, tmp_path):
    """Pull requests WRITE refs: they get a clone of their own, never the snapshot the other tests share."""
    clone = tmp_path / "room.git"
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(room.room_path()), str(clone)], check=True, capture_output=True)
    monkeypatch.setenv("ROOM_GIT_PATH", str(clone))
    monkeypatch.setenv("ROOM_STATE_FILE", str(tmp_path / "room-state.json"))
    monkeypatch.setenv("GITIRL_CLOUD_TOKEN", "")                         # server.py read .env: no test leans on the real one
    monkeypatch.setattr(roommate_api, "_kept", {})
    return clone


@pytest.fixture()
def local():
    return TestClient(server.app, client=("127.0.0.1", 50000))          # the default peer is "testclient": not loopback


def test_nothing_is_published_about_the_robot_until_it_publishes(api, own_room):
    r = api.get("/api/nav/snapshot")
    assert r.status_code == 503 and r.json()["error"] == "not_connected"
    assert api.get("/api/chores").json() == [] and api.get("/api/prs").json() == [], "true: there are none"
    assert "x-roommate-backend" not in api.get("/api/chores").headers, "roomctl answered; an empty list is its answer"
    assert api.get("/api/chores?status=mine").status_code == 422


def test_chores_come_from_roomctls_ledger(api, own_room):
    from roomctl import chores
    from roomctl.repo import Repo
    row, fresh = chores.open_chore(Repo(own_room), {"object_id": "lamp_2d9b", "zone": "desk", "type": "moved", "verdict": "too_heavy"})
    assert fresh
    got = api.get("/api/chores").json()
    assert [c["object_id"] for c in got] == ["lamp_2d9b"] and got[0]["status"] == "open" and got[0]["id"] == row["id"]
    assert {"id", "object_id", "zone", "verdict", "opened_at", "status", "frame_url"} <= set(got[0]), "03 §8's row"
    chores.close_chore(Repo(own_room), row["id"])
    assert api.get("/api/chores?status=open").json() == [] and api.get("/api/chores?status=closed").json()[0]["closed_by"] == "rescan"


def test_a_pull_request_is_opened_listed_and_merged_through_roomctl(local, own_room):
    before = git("rev-parse", "main")
    made = local.post("/api/prs", json={"object_id": "mug_a1b2", "zone": "shelf", "title": "the mug lives on the shelf now"})
    assert made.status_code == 201, made.text
    pr = made.json()
    assert pr["id"] == 1 and pr["status"] == "open" and pr["branch"].startswith("pr/1-") and pr["author"] == "you"
    assert [(o["object_id"], o.get("to_zone") or (o.get("to") or {}).get("zone")) for o in pr["ops"]][0][0] == "mug_a1b2"
    assert git("rev-parse", "main") == before, "proposing moves no branch you are on"
    assert [p["id"] for p in local.get("/api/prs").json()] == [1]

    merged = local.post("/api/prs/1/approve", json={"approver": "katie"}).json()
    assert merged["merge_sha"] == git("rev-parse", "main") != before and merged["pr"]["status"] == "merged"
    assert merged["job_id"] is None and "watch loop" in merged["job_reason"], "no job is invented: the loop makes the real one"
    assert "Approved-by: katie" in git("log", "-1", "--format=%B", "main")
    assert git("status", "--porcelain") != "", "the room has not moved: what the robot owes shows up as drift"
    ci = local.get("/api/room/ci").json()
    assert ci["state"] == "dirty" and ci["misplaced"] == [{"object_id": "mug_a1b2", "is_in": "desk", "belongs_in": "shelf"}]
    assert {c["type"] for c in ci["changes"]} == {"deleted", "untracked"}, "`changes` stays git's own two rows"
    again = local.post("/api/prs/1/approve")
    assert again.status_code == 409 and "already merged" in again.json()["detail"]


def test_pull_request_mistakes_are_typed(local, own_room):
    assert local.post("/api/prs", json={"object_id": "nobody_0000", "zone": "shelf"}).status_code == 404
    assert local.post("/api/prs", json={"object_id": "mug_a1b2", "zone": "garage"}).status_code == 404
    assert local.post("/api/prs", json={"object_id": "mug_a1b2", "zone": "desk"}).status_code == 409, "already there"
    assert local.post("/api/prs", json={"object_id": "../x", "zone": "shelf"}).status_code == 422
    assert local.post("/api/prs", json={"object_id": "mug_a1b2", "zone": "shelf", "title": "two\nlines"}).status_code == 422
    assert local.post("/api/prs/9/approve").status_code == 404 and local.post("/api/prs/one/approve").status_code == 422
    made = local.post("/api/prs", json={"object_id": "mug_a1b2", "zone": "shelf"}).json()
    closed = local.post(f"/api/prs/{made['id']}/close").json()
    assert closed["status"] == "closed" and local.post(f"/api/prs/{made['id']}/approve").status_code == 409


TOKEN = "t" * 40


def test_writes_are_local_or_carry_the_cloud_token(api, local, own_room, monkeypatch):
    body = {"object_id": "mug_a1b2", "zone": "shelf"}
    event = {"event": "room_state", "data": {"clean": True}}
    assert api.post("/api/prs", json=body).status_code == 403, "not local, and this server has no token: local-only"
    assert api.post("/api/edge/event", json=event).status_code == 403
    proxied = local.post("/api/prs/1/approve", headers={"x-forwarded-for": "203.0.113.9"})
    assert proxied.status_code == 403, "a tunnel also arrives from 127.0.0.1; its forwarding header gives it away"
    monkeypatch.setenv("GITIRL_CLOUD_TOKEN", TOKEN)
    refused = api.post("/api/prs", json=body, headers={"authorization": "Bearer " + "x" * 40})
    assert refused.status_code == 401 and refused.headers["www-authenticate"].startswith("Bearer")
    assert api.post("/api/prs", json=body).status_code == 401
    ok = api.post("/api/prs", json=body, headers={"authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 201 and ok.json()["author"] == "cloud"
    monkeypatch.setenv("GITIRL_CLOUD_TOKEN", "short")
    assert api.post("/api/edge/event", json=event, headers={"authorization": "Bearer short"}).status_code == 403, "a short token is no token"
    assert git("for-each-ref", "refs/heads/pr/").count("\n") == 0, "exactly the one authorised PR was written"


def test_the_watch_loops_room_state_reaches_the_ci_answer_and_outlives_a_restart(local, api, own_room, monkeypatch):
    assert api.get("/api/room/ci").json()["last_verified_job"] is None and api.get("/api/room/ci").json()["watch"] is None
    state = {"clean": True, "head": "1a668ec", "branch": "main", "confirmed": [], "pending": [], "stale_blocks": 0,
             "at": "2026-09-19T15:00:00Z", "last_verified_job": "job_9a2f", "ignored": 1, "passes": 7, "blocked": None}
    sent = local.post("/api/edge/event", json={"event": "room_state", "data": state})
    assert sent.status_code == 200 and sent.json()["published"] == "room_state"
    ci = api.get("/api/room/ci").json()
    assert ci["last_verified_job"] == "job_9a2f" and ci["watch"]["passes"] == 7 and ci["watch"]["received_at"]
    monkeypatch.setattr(roommate_api, "_kept", {})                        # a new process: memory is gone, the file is not
    assert api.get("/api/room/ci").json()["last_verified_job"] == "job_9a2f"
    for bad in ({"event": "telemetry", "data": {}}, {"event": "room_state", "data": "clean"}, ["room_state"]):
        assert local.post("/api/edge/event", json=bad).status_code == 400
    assert local.post("/api/edge/event", content=b"{", headers={"content-type": "application/json"}).status_code == 400


def test_the_robots_pose_is_served_in_the_room_frame_and_says_when_it_is_old(local, api, own_room, monkeypatch):
    wrong = local.post("/api/edge/event", json={"event": "nav", "data": {"pose": {"x": 1, "y": 2, "yaw": 0}, "frame": "bb_map"}})
    assert wrong.status_code == 422 and wrong.json()["error"] == "frame_mismatch"
    assert local.post("/api/edge/event", json={"event": "nav", "data": {"pose": {"x": 1.2, "y": 0.4, "yaw": 90}, "status": "idle",
                                                                        "frame": "world_z_up"}}).status_code == 200
    snap = api.get("/api/nav/snapshot").json()
    assert snap["pose"] == {"x": 1.2, "y": 0.4, "yaw": 90} and snap["frame"] == "world_z_up" and snap["stale"] is False
    grid = {"res": 0.5, "bounds": {"xmin": 0, "xmax": 1, "ymin": 0, "ymax": 1}, "cells_b64": "AQECAQ=="}
    push = lambda d: local.post("/api/edge/event", json={"event": "nav", "data": {"frame": "world_z_up", **d}})   # noqa: E731
    push({"pose": {"x": 1.0, "y": 0.4, "yaw": 0}, "map_gen": 7, "grid": grid})
    push({"pose": {"x": 1.1, "y": 0.4, "yaw": 0}})                                                       # 2 Hz: the pose alone
    snap = api.get("/api/nav/snapshot").json()
    assert snap["pose"]["x"] == 1.1 and snap["grid"] == grid and snap["map_gen"] == 7, "a pose-only event keeps its map"
    push({"pose": {"x": 1.2, "y": 0.4, "yaw": 0}, "map_gen": 8})
    assert "grid" not in api.get("/api/nav/snapshot").json(), "a new map without its grid: the old grid is not drawn under it"
    roommate_api._kept["nav"]["received_at"] = "2026-09-19T00:00:00.000Z"                                  # noqa: SLF001
    assert api.get("/api/nav/snapshot").json()["stale"] is True, "where the robot WAS is not where it is"
    assert not (own_room.parent / "room-state.json").exists(), "a 2 Hz pose is never written to disk"


def test_the_hub_carries_the_roommate_events_and_never_replays_a_pose():
    import events
    assert {"room_state", "chore", "pr", "nav"} <= set(events.EVENT_NAMES) and set(events.ROOMMATE_EVENTS) <= set(events.EVENT_NAMES)
    assert "nav" in events.VOLATILE and "room_state" not in events.VOLATILE


def test_a_blame_picture_is_the_frame_of_that_capture_or_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(roommate_api, "LIVE_DIR", tmp_path)
    (tmp_path / "cap_0099").mkdir()
    (tmp_path / "cap_0099" / "cam0_color.jpg").write_bytes(b"jpg")                 # some OTHER moment's frame
    assert roommate_api.frame_of("cap_0005")["frame_url"] is None, "the newest frame on disk is not a picture of this moment"
    got = roommate_api.frame_of("cap_0099")
    assert got["frame_url"] == "/live/cap_0099/cam0_color.jpg" and got["frame_local_only"] is True and got["frame_reason"] is None
    for bad in (None, "", "../cap_0099", "cap 0099"):
        assert roommate_api.frame_of(bad)["frame_url"] is None


def test_the_badge_shows_what_the_room_clean_feeder_last_decided(monkeypatch, tmp_path):
    import roommate_api
    from telemetry.room_clean import RoomCleanBeat
    monkeypatch.setenv("ROOM_CLEAN_STATE", str(tmp_path / "rc.json"))
    monkeypatch.delenv("ROOM_CLEAN_CRON", raising=False)
    RoomCleanBeat(lambda *a, **k: None)(False, source="watch")
    j = roommate_api.ci_from({"clean": True, "branch": "main", "head": "abc1234"})
    assert j["heartbeat"]["last"] == "error" and j["heartbeat"]["at"] is None and j["since"]
    assert "recorded only" in j["source"], "off: the verdict is shown, and it says nothing was sent"
