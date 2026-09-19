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
    assert mug["frame_url"] is None and "no capture frame is stored" in mug["frame_reason"], "no picture is claimed until one exists"
    assert mug["moved_in"]["capture_id"] == "cap_0005", "the capture that made that commit (fixture room-events)"
    tool = api.get("/api/blame/tool_4f2a").json()                       # gone from the room: blame still knows who took it away
    assert tool["what"] == "removed" and tool["moved_in"]["subject"] == "the bench, tidied" and tool["to"] is None
    assert api.get("/api/blame/nobody_0000").status_code == 404 and api.get("/api/blame/..%2Fetc").status_code in (404, 422)


def test_what_is_not_connected_says_so_and_invents_nothing(api):
    for path in ("/api/chores", "/api/prs"):
        r = api.get(path)
        assert r.status_code == 200 and r.json() == [] and r.headers["x-roommate-backend"] == "not_connected"
    for call in (api.post("/api/prs", json={"object_id": "lamp_2d9b", "zone": "shelf", "title": "move the lamp"}),
                 api.post("/api/prs/pr_1/approve"), api.get("/api/nav/snapshot")):
        assert call.status_code == 503 and call.json()["error"] == "not_connected"
