"""The merge PREVIEW: what a merge would do, and the promise that it does none of it.

Three claims are worth a test. One side changed it and the other did not: that side wins, the way
git merges a file only one branch touched. Both sides changed it, differently: a conflict that
states both places and how far apart they are, because the object is in exactly one of them. And
through all of it nothing is written — not a ref, not the index, not the working tree — which is
the whole reason this endpoint is allowed to exist next to an API that refuses to merge.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import fastapi
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import roommerge_api  # noqa: E402
import scene_api  # noqa: E402

WHO = ("-c", "user.email=t@t", "-c", "user.name=t")


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def record(oid: str, cls: str, zone: str, x: float, y: float) -> str:
    return (f"id: {oid}\nclass: {cls}\nzone: {zone}\n"
            f"pose:\n  x: {x}\n  y: {y}\n  z: 0.05\n  yaw: 0\n"
            "extents:\n  x: 0.1\n  y: 0.1\n  z: 0.1\n"
            'color: "#2b4c7e"\nfirst_seen: "2026-09-19T14:00:00Z"\n')


def put(repo: Path, oid: str, cls: str, zone: str, x: float, y: float) -> None:
    d = repo / "zones" / zone
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{oid}.yaml").write_text(record(oid, cls, zone, x, y))


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """A room that diverged: ours kicked the packet, theirs took it away, and both left the box."""
    rooms = tmp_path / "rooms"
    repo = rooms / "demo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    put(repo, "packet_a1b2", "chip packet", "floor", 0.82, -0.58)
    put(repo, "box_7c2e", "small box", "floor", 1.27, 0.71)
    git(repo, "add", "-A")
    git(repo, *WHO, "commit", "-qm", "the floor")
    base = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-q", "-b", "kicked")
    put(repo, "packet_a1b2", "chip packet", "floor", 1.12, -0.33)          # 39 cm
    git(repo, "add", "-A")
    git(repo, *WHO, "commit", "-qm", "kicked across the floor")

    git(repo, "checkout", "-q", "-b", "eaten", base)
    (repo / "zones" / "floor" / "packet_a1b2.yaml").unlink()
    put(repo, "box_7c2e", "small box", "shelf", 1.27, 0.71)                # a zone change only theirs made
    (repo / "zones" / "floor" / "box_7c2e.yaml").unlink()
    git(repo, "add", "-A")
    git(repo, *WHO, "commit", "-qm", "someone took the packet")
    git(repo, "checkout", "-q", "main")

    monkeypatch.setenv("ROOM_LIVE_DIR", str(rooms))
    app = fastapi.FastAPI()
    roommerge_api.init(None)
    app.include_router(roommerge_api.router)
    return TestClient(app, client=("127.0.0.1", 1234)), repo


def fingerprint(repo: Path) -> tuple[str, str, str]:
    return (git(repo, "rev-parse", "--all"), git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD"))


def test_one_side_changed_it_so_that_side_is_what_the_merge_takes(demo):
    c, _ = demo
    d = c.get("/api/merge-preview/demo", params={"ours": "main", "theirs": "kicked"}).json()
    assert d["would_merge_cleanly"] and d["summary"]["conflicts"] == 0
    assert d["fast_forward"] is True                       # ours has nothing of its own
    [taken] = d["clean"]
    assert taken["object_id"] == "packet_a1b2" and taken["side"] == "theirs" and taken["op"] == "moved"
    assert taken["result"]["pose"]["x"] == 1.12            # and it says where it would end up
    assert round(taken["distance_m"], 2) == 0.39


def test_both_sides_changed_it_differently_and_the_preview_states_both_places(demo):
    c, _ = demo
    d = c.get("/api/merge-preview/demo", params={"ours": "kicked", "theirs": "eaten"}).json()
    assert d["would_merge_cleanly"] is False
    [conflict] = d["conflicts"]
    assert conflict["object_id"] == "packet_a1b2"
    assert conflict["ours"]["pose"]["x"] == 1.12 and conflict["theirs"] is None
    assert conflict["base"]["pose"]["x"] == 0.82           # where it was before either side spoke
    assert "removed it" in conflict["why"]
    assert "rescan the room" in conflict["options"]        # the answer is in the room, not the file
    # the object nobody disputes still merges, in the same answer
    assert [o["object_id"] for o in d["clean"]] == ["box_7c2e"]


def test_a_preview_writes_nothing(demo):
    """The claim that lets this live beside an API that refuses to merge."""
    c, repo = demo
    before = fingerprint(repo)
    for ours, theirs in (("main", "kicked"), ("kicked", "eaten"), ("eaten", "kicked"), ("main", "main")):
        assert c.get("/api/merge-preview/demo", params={"ours": ours, "theirs": theirs}).status_code == 200
    assert fingerprint(repo) == before
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    assert all(r.json()["written"] is False
               for r in [c.get("/api/merge-preview/demo", params={"ours": "main", "theirs": "kicked"})])


def test_up_to_date_is_not_a_change(demo):
    c, _ = demo
    d = c.get("/api/merge-preview/demo", params={"ours": "kicked", "theirs": "main"}).json()
    assert d["up_to_date"] is True and d["summary"]["conflicts"] == 0
    assert d["summary"]["takes_theirs"] == 0               # nothing to take: theirs is already in ours
    assert d["summary"]["already_ours"] == 1               # and ours' own move is named as ours, not as a merge


def test_a_ref_is_checked_before_git_sees_it(demo):
    c, _ = demo
    assert c.get("/api/merge-preview/demo", params={"ours": "main", "theirs": "--exec=x"}).status_code == 422
    assert c.get("/api/merge-preview/demo", params={"ours": "main", "theirs": "no-such"}).status_code == 404
    assert c.get("/api/merge-preview/demo", params={"ours": "main"}).status_code == 422
    assert c.get("/api/merge-preview/nope", params={"ours": "main", "theirs": "main"}).status_code == 404


def test_it_stays_out_of_the_namespace_that_forbids_merges():
    """scene_api's rule (web/tests/test_scene_graph.py): no route under /api/scene may name a merge."""
    scene = {r.path for r in scene_api.router.routes}
    mine = {r.path for r in roommerge_api.router.routes}
    assert scene and not [p for p in scene if "merge" in p or "cherry" in p or "rebase" in p]
    assert mine and not [p for p in mine if p.startswith("/api/scene")]


# ── settling a conflict: a decision, written down ──────────────────────────────────────
# The preview says what a merge would do; this is the person answering it. The rules under test are
# the ones that keep it a DECISION: nothing is chosen for you, a stale view is refused, and what got
# written says which way each thing went.

def resolve(c, **body):
    return c.post("/api/merge-resolve/demo", json=body)


def test_it_will_not_settle_a_conflict_for_you(demo):
    c, repo = demo
    before = fingerprint(repo)
    r = resolve(c, ours="kicked", theirs="eaten")
    assert r.status_code == 409 and "packet_a1b2" in r.json()["detail"]
    assert fingerprint(repo) == before                       # and a refusal writes nothing
    assert resolve(c, ours="kicked", theirs="eaten", choices={"packet_a1b2": "sideways"}).status_code == 422
    assert resolve(c, ours="kicked", theirs="eaten", choices={"box_7c2e": "ours"}).status_code == 409


def test_keeping_it_writes_a_merge_that_still_has_the_packet(demo):
    c, repo = demo
    r = resolve(c, ours="kicked", theirs="eaten", choices={"packet_a1b2": "ours"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert git(repo, "rev-parse", "kicked") == d["merged"]
    assert git(repo, "rev-list", "--parents", "-n1", d["merged"]).split()[1:] == d["parents"]
    assert "packet_a1b2" in d["objects_after"]
    assert git(repo, "show", f"{d['merged']}:zones/floor/packet_a1b2.yaml").count("x: 1.12") == 1
    [settled] = d["conflicts_settled"]
    assert settled["did"] == "keep" and settled["chose"] == "ours"
    assert "kept" in git(repo, "log", "-1", "--format=%B", d["merged"])
    assert git(repo, "rev-parse", "eaten") != d["merged"]     # the other branch is left where it was


def test_removing_it_writes_a_merge_without_it(demo):
    c, repo = demo
    d = resolve(c, ours="kicked", theirs="eaten", choices={"packet_a1b2": "theirs"}).json()
    assert "packet_a1b2" not in d["objects_after"]
    assert "packet_a1b2" not in git(repo, "ls-tree", "-r", "--name-only", d["merged"], "--", "zones")
    assert d["conflicts_settled"][0]["did"] == "remove"
    assert "removed — confirmed gone" in git(repo, "log", "-1", "--format=%B", d["merged"])


def test_the_side_nobody_disputed_comes_across_too(demo):
    """theirs moved the box to the shelf and ours never touched it: a merge takes that, unasked —
    that is what makes it a merge and not a choice between two snapshots."""
    c, repo = demo
    d = resolve(c, ours="kicked", theirs="eaten", choices={"packet_a1b2": "ours"}).json()
    assert "zones/shelf/box_7c2e.yaml" in git(repo, "ls-tree", "-r", "--name-only", d["merged"], "--", "zones")
    assert "zones/floor/box_7c2e.yaml" not in git(repo, "ls-tree", "-r", "--name-only", d["merged"], "--", "zones")


def test_a_decision_made_against_a_stale_preview_is_refused(demo):
    c, repo = demo
    before = fingerprint(repo)
    r = resolve(c, ours="kicked", theirs="eaten", choices={"packet_a1b2": "ours"},
                expect={"ours": "0" * 40})
    assert r.status_code == 409 and "moved since" in r.json()["detail"]
    assert fingerprint(repo) == before


def test_it_refuses_to_commit_onto_something_that_is_not_a_branch(demo):
    c, repo = demo
    sha = git(repo, "rev-parse", "kicked")
    r = resolve(c, ours=sha, theirs="eaten", choices={"packet_a1b2": "ours"})
    assert r.status_code == 422 and "branch" in r.json()["detail"]


def test_nothing_to_merge_is_said_rather_than_committed(demo):
    c, repo = demo
    r = resolve(c, ours="kicked", theirs="main", choices={})
    assert r.status_code == 409 and "already part of" in r.json()["detail"]


# ── the demo, back where it started ────────────────────────────────────────────────────

def tag(repo, name, ref):
    git(repo, "tag", "-f", name, ref)


def test_a_room_without_demo_tags_is_never_touched(demo):
    c, repo = demo
    before = fingerprint(repo)
    assert c.post("/api/demo-reset/demo").status_code == 404
    assert fingerprint(repo) == before


def test_a_merge_made_during_the_demo_is_put_back(demo):
    c, repo = demo
    for branch in ("main", "kicked", "eaten"):
        tag(repo, f"demo/{branch}", branch)
    started = {b: git(repo, "rev-parse", b) for b in ("main", "kicked", "eaten")}

    merged = resolve(c, ours="kicked", theirs="eaten", choices={"packet_a1b2": "ours"}).json()["merged"]
    assert git(repo, "rev-parse", "kicked") == merged            # the demo happened

    d = c.post("/api/demo-reset/demo").json()
    assert [b["branch"] for b in d["reset"]] == ["kicked"]
    assert {b: git(repo, "rev-parse", b) for b in ("main", "kicked", "eaten")} == started
    assert git(repo, "cat-file", "-t", merged) == "commit"        # unreferenced, not destroyed


def test_resetting_a_room_already_at_its_start_changes_nothing(demo):
    c, repo = demo
    for branch in ("main", "kicked", "eaten"):
        tag(repo, f"demo/{branch}", branch)
    before = fingerprint(repo)
    d = c.post("/api/demo-reset/demo").json()
    assert d["already"] is True and d["reset"] == []
    assert fingerprint(repo) == before


def test_the_checked_out_branch_gets_its_files_back_too(demo):
    c, repo = demo
    git(repo, "checkout", "-q", "kicked")
    for branch in ("main", "kicked", "eaten"):
        tag(repo, f"demo/{branch}", branch)
    resolve(c, ours="kicked", theirs="eaten", choices={"packet_a1b2": "theirs"})   # removes the packet
    assert not (repo / "zones" / "floor" / "packet_a1b2.yaml").exists()
    c.post("/api/demo-reset/demo")
    assert (repo / "zones" / "floor" / "packet_a1b2.yaml").exists()                # and the file is back
    assert git(repo, "status", "--porcelain") == ""


# ── the picture has to agree with the decision ─────────────────────────────────────────
# A commit carries the objects AND the scan they were found in. Keeping a packet whose points were
# deleted from ours' scan committed a record saying "it is on the floor" over a picture of an empty
# floor, and the 3D view showed it gone — the user pressed Keep it and watched it vanish.

def with_clouds(repo):
    """Give each branch a cloud you can tell apart, the way a real room's commits carry one."""
    for branch, text in (("kicked", "PLY-with-the-packet"), ("eaten", "PLY-without-the-packet")):
        git(repo, "checkout", "-q", branch)
        (repo / "cloud").mkdir(exist_ok=True)
        (repo / "cloud" / "current.ply").write_text(text)
        (repo / "cloud" / "current.json").write_text(f'{{"from": "{branch}"}}\n')
        git(repo, "add", "-A")
        git(repo, *WHO, "commit", "-qm", f"{branch}: its own cloud")
    git(repo, "checkout", "-q", "main")


def test_keeping_it_brings_the_scan_that_shows_it(demo):
    c, repo = demo
    with_clouds(repo)
    d = resolve(c, ours="eaten", theirs="kicked", choices={"packet_a1b2": "theirs"}).json()
    assert d["picture"]["from"] == "theirs" and d["picture"]["shows_the_decision"] is True
    assert git(repo, "show", f"{d['merged']}:cloud/current.ply") == "PLY-with-the-packet"
    assert "packet_a1b2" in d["objects_after"]        # record and picture agree


def test_removing_it_keeps_the_scan_without_it(demo):
    c, repo = demo
    with_clouds(repo)
    d = resolve(c, ours="eaten", theirs="kicked", choices={"packet_a1b2": "ours"}).json()
    assert d["picture"]["from"] == "ours"
    assert git(repo, "show", f"{d['merged']}:cloud/current.ply") == "PLY-without-the-packet"
    assert "packet_a1b2" not in d["objects_after"]
