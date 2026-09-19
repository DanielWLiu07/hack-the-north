"""web/graph_api.py against the REAL room.git (read-only) + the fixture enrichment.
No network: Elasticsearch is parked, store.py serves fake/out/demo.ndjson.

The guard fixture proves the whole module left room.git exactly as it found it: HEAD,
every ref, the working tree, the object count and the index file itself."""
from __future__ import annotations

import logging
import math
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["SENTRY_DSN"] = ""
os.environ["SENTRY_DSN_WEB"] = ""
logging.disable(logging.INFO)

import pytest  # noqa: E402
import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import room  # noqa: E402
import server  # noqa: E402

ROOM = room.room_path()


def git(*args: str) -> str:
    return subprocess.run(["git", "--no-optional-locks", "-C", str(ROOM), *args],
                          capture_output=True, text=True, check=False).stdout.strip()


def fingerprint() -> tuple:
    index = ROOM / ".git" / "index"
    return (git("rev-parse", "HEAD"), git("for-each-ref"), git("status", "--porcelain"),
            git("count-objects", "-v"), index.stat().st_mtime_ns if index.exists() else None,
            (ROOM / ".git" / "MERGE_HEAD").exists())


@pytest.fixture(scope="module")
def c():
    before = fingerprint()
    assert before[2] == "", "room.git must be clean before the suite runs"
    with TestClient(server.app) as client:
        yield client
    assert fingerprint() == before, "room.git changed: HEAD / refs / tree / objects / index must all be untouched"


def shape(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"error", "detail", "retryable"} and body["error"] == code, body
    return body


def test_graph_is_the_real_log(c):
    g = c.get("/api/graph").json()
    assert g["head"] == git("rev-parse", "HEAD") and g["branch"] == git("symbolic-ref", "--short", "HEAD")
    assert [n["sha"] for n in g["nodes"]] == git("log", "--all", "--date-order", "--format=%H").split()
    for n in g["nodes"]:
        assert {"sha", "parents", "refs", "ts", "subject", "changed", "quality_ok", "capture_id",
                "sentry_trace_id"} <= set(n)
    ts = [n["ts"] for n in g["nodes"]]
    assert ts == sorted(ts, reverse=True)                                  # time order is sacred
    assert g["trunk"] == git("rev-list", "--first-parent", "HEAD").split()  # what the scrubber walks
    assert g["executor"] == "not_connected"


def test_the_fork_is_there(c):
    nodes = c.get("/api/graph").json()["nodes"]
    tidied = next(n for n in nodes if n["subject"] == "the bench, tidied")
    kids = [n for n in nodes if tidied["sha"] in n["parents"]]
    assert {r["name"] for n in kids for r in n["refs"]} == {"main", "movie-night"}
    assert any(r["head"] for n in nodes for r in n["refs"] if r["name"] == "main")


def test_rejected_capture_hangs_on_the_retry_commit(c):
    g = c.get("/api/graph").json()
    assert g["source"] == "fixture" and g["enriched"]                      # ES is parked: fixtures, labelled
    head = next(n for n in g["nodes"] if n["sha"] == g["head"])
    assert head["capture_id"] == "cap_0005" and head["quality_ok"] is True and head["suspect"] is False
    assert [r["capture_id"] for r in head["rejected_before"]] == ["cap_0004"]
    failing = head["rejected_before"][0]["failing"][0]
    assert failing["name"] == "tilt_rate_max" and abs(failing["value"] - 0.0825) < 1e-6
    assert head["rejected_before"][0]["would_have"]["moved"] == 10
    assert all(not n["rejected_before"] for n in g["nodes"] if n["sha"] != g["head"])


def test_graph_survives_without_enrichment(c, monkeypatch):
    import graph_api

    async def boom(*a, **k):
        raise RuntimeError("no enrichment")
    monkeypatch.setattr(graph_api.store, "_find", boom)
    g = c.get("/api/graph").json()
    assert g["enriched"] is False and g["source"] == "git-only" and len(g["nodes"]) == 4
    n = g["nodes"][0]
    assert n["quality_ok"] is None and n["capture_id"] is None and n["changed"]["from"] == "git"
    assert "mug_a1b2" in n["changed"]["moved"] and "scissors_9f3a" in n["changed"]["added"]


def test_diff_matches_the_yaml(c):
    g = c.get("/api/graph").json()
    head = g["head"]
    tidied = next(n["sha"] for n in g["nodes"] if n["subject"] == "the bench, tidied")
    d = c.get(f"/api/diff?a={head[:8]}&b={tidied[:8]}").json()
    ops = {o["object_id"]: o for o in d["ops"]}
    assert ops["mug_a1b2"]["op"] == "moved" and ops["scissors_9f3a"]["op"] == "removed"
    p0 = yaml.safe_load(git("show", f"{head}:zones/desk/mug_a1b2.yaml"))["pose"]
    p1 = yaml.safe_load(git("show", f"{tidied}:zones/desk/mug_a1b2.yaml"))["pose"]
    assert ops["mug_a1b2"]["delta_m"] == round(math.dist([p0[k] for k in "xyz"], [p1[k] for k in "xyz"]), 3)
    assert c.get(f"/api/diff?a={head}&b={head}").json()["ops"] == []


def test_a_zone_change_is_one_move_not_a_delete_plus_an_add(c):
    a, b = git("rev-parse", "main"), git("rev-parse", "movie-night")
    ops = {o["object_id"]: o for o in c.get(f"/api/diff?a={a}&b={b}").json()["ops"]}
    assert ops["speaker_6b12"]["op"] == "moved" and ops["speaker_6b12"]["from_zone"] == "shelf"
    assert ops["speaker_6b12"]["zone"] == "desk" and ops["speaker_6b12"]["delta_m"] > 0.5


def test_diff_errors_keep_the_shape(c):
    shape(c.get("/api/diff?a=zzzz&b=1a668ec"), 422, "bad_request")
    shape(c.get("/api/diff?a=deadbeef&b=deadbeef"), 404, "not_found")
    shape(c.get("/api/diff?a=HEAD;rm&b=x"), 422, "bad_request")
    shape(c.get("/api/diff?a=1a66"), 422, "bad_request")                   # b is missing


def test_command_plans_but_never_pretends(c):
    g = c.get("/api/graph").json()
    tidied = next(n["sha"] for n in g["nodes"] if n["subject"] == "the bench, tidied")
    r = c.post("/api/command", json={"command": "revert", "args": {"ref": tidied}})
    assert r.status_code == 202, r.text
    j = r.json()
    assert j["executor"] == "not_connected" and j["state"] == "planned" and j["target"] == tidied
    assert (j["frame"], j["units"]["position"], j["units"]["yaw"]) == ("world_z_up", "m", "deg")   # declared, never inferred
    again = c.post("/api/command", json={"command": "revert", "args": {"ref": tidied}})   # same command, same room
    assert again.status_code == 200 and again.json()["replayed"] is True and again.json()["job_id"] == j["job_id"]
    # revert undoes THAT ONE commit: tidying put the tool away, so reverting it brings the tool back —
    # and leaves the afternoon's mug / scissors / marker exactly where the afternoon left them
    assert [(o["op"], o["object_id"]) for o in j["ops"]] == [("added", "tool_4f2a")] and j["skipped"] == [] and j["estimated_s"] == 28
    j2 = c.post("/api/command", json={"command": "checkout", "args": {"ref": "movie-night"}}).json()
    assert j2["target"] == git("rev-parse", "movie-night")
    j3 = c.post("/api/command", json={"command": "revert", "args": {"ref": "HEAD"}}).json()
    assert j3["target"] == git("rev-parse", "HEAD"), "the target of a revert is the commit being undone (docs/31)"
    assert {(o["op"], o["object_id"]) for o in j3["ops"]} == {("moved", "mug_a1b2"), ("removed", "scissors_9f3a"), ("added", "marker_c3d4")}


def test_revert_is_not_restore_and_says_what_it_will_not_undo(c, monkeypatch):
    monkeypatch.setenv("WEB_ALLOWED_COMMANDS", "revert,restore")
    g = c.get("/api/graph").json()
    tidied = next(n["sha"] for n in g["nodes"] if n["subject"] == "the bench, tidied")
    # restore <ref>: make the room MATCH that state — everything since is undone. That is what the old
    # `revert <older sha>` wrongly planned: 3 ops, none of them the tool that tidying actually moved.
    restore = c.post("/api/command", json={"command": "restore", "args": {"ref": tidied}}).json()
    assert {o["object_id"] for o in restore["ops"]} == {"mug_a1b2", "scissors_9f3a", "marker_c3d4"} and restore["skipped"] == []
    # a commit on ANOTHER branch: most of it never happened here, and the mug has moved since
    night = c.post("/api/command", json={"command": "revert", "args": {"ref": "movie-night"}}).json()
    assert night["ops"] == [] and {x["object_id"]: x["status"] for x in night["skipped"]}["mug_a1b2"] == "conflict"
    assert sum(1 for x in night["skipped"] if x["status"] == "already_applied") == 4
    assert "39 cm" in next(x["why"] for x in night["skipped"] if x["object_id"] == "mug_a1b2")
    # a preview computed for a HEAD that has since moved is refused, not run
    stale = c.post("/api/command", json={"command": "revert", "args": {"ref": "HEAD"}, "base_sha": tidied})
    assert stale.status_code == 409 and stale.json()["error"] == "head_moved" and stale.json()["retryable"] is True
    ok = c.post("/api/command", json={"command": "revert", "args": {"ref": "HEAD"}, "base_sha": git("rev-parse", "HEAD")[:12]})
    # accepted — as a new job, or (this module already asked `revert HEAD` of this same room) that job, replayed
    assert ok.status_code in (200, 202) and ok.json()["replayed"] is (ok.status_code == 200), ok.text


def test_command_guards(c):
    shape(c.post("/api/command", json={"command": "cherry-pick", "args": {"ref": "HEAD"}}), 403, "command_not_allowed")
    shape(c.post("/api/command", json={"command": "rm", "args": {"ref": "HEAD"}}), 403, "command_not_allowed")
    # status / diff / log are READS now (the agent panel asks through the same door): answered, never queued
    read = c.post("/api/command", json={"command": "status", "args": {}})
    assert read.status_code == 200 and read.json()["kind"] == "read" and "job_id" not in read.json()
    shape(c.post("/api/command", json={"command": "search", "args": {}}), 400, "unsupported_command")   # allowed, but not a graph command
    shape(c.post("/api/command", json={"command": "checkout", "args": {"ref": "--upload-pack=x"}}), 422, "bad_request")
    shape(c.post("/api/command", json={"command": "checkout", "args": {"ref": "a..b"}}), 422, "bad_request")
    shape(c.post("/api/command", json={"command": "checkout", "args": {"ref": "no-such-branch"}}), 404, "not_found")
    shape(c.post("/api/command", json={"command": "revert"}), 422, "bad_request")


def test_commands_reflects_the_allow_list(c, monkeypatch):
    live = c.get("/api/commands").json()
    env = {x.strip() for x in os.environ["WEB_ALLOWED_COMMANDS"].split("#")[0].split(",") if x.strip()}
    assert set(live["allowed"]) == env and live["executor"] == "not_connected"
    assert live["graph"]["cherry-pick"] == ("cherry-pick" in env) and live["graph"]["resolve"] == ("resolve" in env)
    monkeypatch.setenv("WEB_ALLOWED_COMMANDS", "status, cherry-pick   # a comment")
    flipped = c.get("/api/commands").json()
    assert flipped["allowed"] == ["cherry-pick", "status"]
    assert flipped["graph"] == {"revert": False, "restore": False, "checkout": False, "resolve": False, "merge": False,
                                "cherry-pick": True}
    # and once it IS allowed, the command plans only the ops that apply cleanly
    j = c.post("/api/command", json={"command": "cherry-pick", "args": {"ref": "movie-night"}})
    assert j.status_code == 202 and "mug_a1b2" not in {o["object_id"] for o in j.json()["ops"]}
    assert len(j.json()["ops"]) == 4 and j.json()["executor"] == "not_connected"


def test_a_command_reaches_the_sse_hub(c):
    import events
    seen, orig = [], events.hub.publish
    events.hub.publish = lambda name, data: (seen.append((name, data)), orig(name, data))[1]
    try:
        first = c.post("/api/command", json={"command": "checkout", "args": {"ref": "main"}})
        n = len(seen)
        again = c.post("/api/command", json={"command": "checkout", "args": {"ref": "main"}})
    finally:
        events.hub.publish = orig
    assert first.status_code == 202 and seen and seen[-1][0] == "job" and seen[-1][1]["executor"] == "not_connected"
    assert seen[-1][1]["id"] == first.json()["job_id"]
    assert again.status_code == 200 and again.json()["replayed"] is True and len(seen) == n, "a replay is not a new event"
