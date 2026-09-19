"""The graph's previews — scrub state, merge, resolve, cherry-pick — against the REAL room.git.

room.git holds a genuine conflict: mug_a1b2 was moved on main AND on movie-night, both from
the tidied bench. Every preview here must be computed from git READS: the guard proves no
object, ref, index entry or MERGE_HEAD appeared."""
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
    assert fingerprint() == before, "room.git changed: a PREVIEW wrote something"


def shape(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"error", "detail", "retryable"} and body["error"] == code, body
    return body


def pose_at(ref: str, path: str) -> dict:
    return yaml.safe_load(git("show", f"{ref}:{path}"))["pose"]


# ── scrub ─────────────────────────────────────────────────────────────────────────────

def test_state_is_the_tree_at_that_commit(c):
    for ref in ("HEAD", "movie-night", git("rev-parse", "HEAD~2")):      # `~` is refused on purpose: shas, branches, HEAD
        st = c.get(f"/api/state?ref={ref}").json()
        files = [p for p in git("ls-tree", "-r", "--name-only", ref, "--", "zones").splitlines()]
        assert st["sha"] == git("rev-parse", ref) and len(st["objects"]) == len(files)
        assert {f"zones/{o['zone']}/{o['object_id']}.yaml" for o in st["objects"]} == set(files)
    mug = next(o for o in c.get("/api/state?ref=HEAD").json()["objects"] if o["object_id"] == "mug_a1b2")
    want = pose_at("HEAD", "zones/desk/mug_a1b2.yaml")
    assert all(mug["pose"][k] == float(want[k]) for k in ("x", "y", "z", "yaw"))
    zones = c.get("/api/state?ref=HEAD").json()["zones"]
    assert set(zones) == {"desk", "shelf"} and zones["desk"]["min"][0] < zones["desk"]["max"][0]
    shape(c.get("/api/state?ref=nope-nope"), 404, "not_found")
    shape(c.get("/api/state?ref=-x"), 422, "bad_request")
    shape(c.get("/api/state?ref=HEAD~1"), 422, "bad_request")            # no revision expressions from the browser


# ── merge ─────────────────────────────────────────────────────────────────────────────

def test_merge_preview_finds_the_real_mug_conflict(c):
    m = c.get("/api/merge-preview?ours=main&theirs=movie-night").json()
    assert m["base"] == git("merge-base", "main", "movie-night")
    assert m["ours"]["name"] == "main" and m["theirs"]["name"] == "movie-night"
    assert not m["up_to_date"] and not m["fast_forward"]
    assert [x["object_id"] for x in m["conflicts"]] == ["mug_a1b2"]
    k = m["conflicts"][0]
    ours, theirs, base = (pose_at(r, "zones/desk/mug_a1b2.yaml") for r in ("main", "movie-night", m["base"]))
    for side, want in (("ours", ours), ("theirs", theirs), ("base", base)):
        assert all(k[side]["pose"][a] == float(want[a]) for a in ("x", "y", "z", "yaw")), side
    assert k["ours"]["pose"] != k["theirs"]["pose"]                        # two DIFFERENT candidate poses
    assert k["distance_m"] == round(math.dist([ours[a] for a in "xyz"], [theirs[a] for a in "xyz"]), 3) > 0.3


def test_merge_preview_lists_the_clean_changes_by_side(c):
    m = c.get("/api/merge-preview?ours=main&theirs=movie-night").json()
    by_side = {s: {(o["op"], o["object_id"]) for o in m["clean"] if o["side"] == s} for s in ("ours", "theirs", "both")}
    assert by_side["theirs"] == {("added", "bowl_0c55"), ("moved", "lamp_2d9b"), ("removed", "notebook_5a0c"),
                                 ("moved", "speaker_6b12")}
    assert by_side["ours"] == {("removed", "marker_c3d4"), ("added", "scissors_9f3a")}
    assert by_side["both"] == set()
    assert m["summary"] == {"would_apply": 4, "already_on_ours": 2, "conflicts": 1}
    assert "mug_a1b2" not in {o["object_id"] for o in m["clean"]}


def test_merge_preview_edge_cases(c):
    parent = git("rev-parse", "HEAD~1")
    ff = c.get(f"/api/merge-preview?ours={parent}&theirs=main").json()      # ours is an ancestor of theirs
    assert ff["fast_forward"] and not ff["conflicts"] and ff["summary"]["would_apply"] == 3
    done = c.get(f"/api/merge-preview?ours=main&theirs={parent}").json()    # theirs already contained
    assert done["up_to_date"] and not done["conflicts"] and done["summary"]["would_apply"] == 0
    assert {o["side"] for o in done["clean"]} == {"ours"}                    # everything is already on ours
    assert c.get("/api/merge-preview?theirs=movie-night").json()["ours"]["name"] == "main"   # ours defaults to HEAD


def test_merge_preview_bad_refs(c):
    shape(c.get("/api/merge-preview?ours=main&theirs=--exec=x"), 422, "bad_request")
    shape(c.get("/api/merge-preview?ours=ma..in&theirs=movie-night"), 422, "bad_request")
    shape(c.get("/api/merge-preview?ours=main&theirs=no-such-branch"), 404, "not_found")
    shape(c.get("/api/merge-preview?ours=main"), 422, "bad_request")        # theirs is required


# ── resolve ───────────────────────────────────────────────────────────────────────────

def test_resolve_plans_and_never_writes(c):
    import events
    seen, orig = [], events.hub.publish
    events.hub.publish = lambda name, data: (seen.append((name, data)), orig(name, data))[1]
    try:
        r = c.post("/api/resolve", json={"object_id": "mug_a1b2", "resolution": "theirs", "theirs": "movie-night"})
    finally:
        events.hub.publish = orig
    assert r.status_code == 202, r.text
    j = r.json()
    assert j["applying"] == "movie-night" and j["executor"] == "not_connected" and j["state"] == "planned"
    assert len(j["ops"]) == 1 and j["ops"][0]["op"] == "moved" and j["estimated_s"] == 28
    assert j["ops"][0]["to"]["x"] == float(pose_at("movie-night", "zones/desk/mug_a1b2.yaml")["x"])
    assert seen[-1][0] == "job" and seen[-1][1]["command"] == "resolve" and seen[-1][1]["executor"] == "not_connected"
    keep = c.post("/api/resolve", json={"object_id": "mug_a1b2", "resolution": "ours", "theirs": "movie-night"}).json()
    assert keep["applying"] == "main" and keep["ops"] == [] and keep["estimated_s"] == 0   # the room is already there
    assert not (ROOM / ".git" / "MERGE_HEAD").exists()


def test_resolve_guards(c, monkeypatch):
    base = {"object_id": "mug_a1b2", "theirs": "movie-night"}
    shape(c.post("/api/resolve", json={**base, "resolution": "mine"}), 422, "bad_request")
    shape(c.post("/api/resolve", json={**base}), 422, "bad_request")
    shape(c.post("/api/resolve", json={**base, "object_id": "../etc", "resolution": "ours"}), 422, "bad_request")
    shape(c.post("/api/resolve", json={**base, "object_id": "lamp_2d9b", "resolution": "ours"}), 409, "not_in_conflict")
    shape(c.post("/api/resolve", json={**base, "theirs": "no-such", "resolution": "ours"}), 404, "not_found")
    shape(c.post("/api/resolve", json={**base, "theirs": "--x", "resolution": "ours"}), 422, "bad_request")
    shape(c.post("/api/resolve", json={"object_id": "mug_a1b2", "resolution": "ours"}), 409, "no_merge")   # not mid-merge
    monkeypatch.setenv("WEB_ALLOWED_COMMANDS", "status,revert")
    shape(c.post("/api/resolve", json={**base, "resolution": "ours"}), 403, "command_not_allowed")


# ── cherry-pick ───────────────────────────────────────────────────────────────────────

def test_cherry_pick_preview_flags_what_would_conflict(c):
    p = c.get("/api/cherry-pick-preview?commit=movie-night&onto=HEAD").json()
    assert p["commit"]["sha"] == git("rev-parse", "movie-night") and p["onto"]["name"] == "main"
    status = {o["object_id"]: o["status"] for o in p["ops"]}
    assert status == {"speaker_6b12": "applies", "lamp_2d9b": "applies", "notebook_5a0c": "applies",
                      "bowl_0c55": "applies", "mug_a1b2": "conflict"}
    mug = next(o for o in p["ops"] if o["object_id"] == "mug_a1b2")
    assert "already stands elsewhere" in mug["reason"] and "19 cm" in mug["reason"]
    assert p["summary"] == {"applies": 4, "conflict": 1, "already_applied": 0}
    assert p["allowed"] is False and "allow-list" in p["reason"]           # cherry-pick is not allow-listed


def test_cherry_pick_preview_edge_cases(c):
    root = git("rev-list", "--max-parents=0", "HEAD")
    p = c.get(f"/api/cherry-pick-preview?commit={root}&onto=HEAD").json()   # a root commit: parent = the empty tree
    assert p["commit"]["parent"] is None and all(o["op"] == "added" for o in p["ops"])
    assert p["summary"]["already_applied"] >= 8 and p["summary"]["conflict"] >= 1      # the mug has moved since
    same = c.get("/api/cherry-pick-preview?commit=HEAD&onto=HEAD").json()
    assert {o["status"] for o in same["ops"]} == {"already_applied"}
    shape(c.get("/api/cherry-pick-preview?commit=zzz..z&onto=HEAD"), 422, "bad_request")
    shape(c.get("/api/cherry-pick-preview?commit=deadbeef&onto=HEAD"), 404, "not_found")
