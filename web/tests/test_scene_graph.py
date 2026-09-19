"""The point-cloud commit graph on /robot: lanes, the object diff, and the two writes.

One commit of a room instance is one point cloud AND the objects found in it, so the same pair
of shas answers both questions. These cover the parts /robot's rail now relies on:

  * /history carries `parents` and `refs` for every node, over the WHOLE DAG (--all), so a branch
    that is not HEAD's ancestor is a lane and not a missing node;
  * /diff is objdiff.py — the same implementation room.git's own graph uses — including the
    delete+add that git reports for a zone change being one move, and identity (class, colour,
    first_seen) carried out so the page can say "new" or "we have seen this before";
  * branch and checkout move a ref and nothing else, and there is NO merge endpoint.

Local only, like every other route in scene_api: TestClient's default peer is not loopback.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ast  # noqa: E402

import fastapi  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import scene_api  # noqa: E402
import server  # noqa: E402


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r.stdout.strip()


def ply(n: int) -> bytes:
    head = ("ply\nformat binary_little_endian 1.0\n"
            f"element vertex {n}\n"
            "property float x\nproperty float y\nproperty float z\n"
            "property uchar red\nproperty uchar green\nproperty uchar blue\n"
            "end_header\n").encode()
    return head + b"".join(struct.pack("<fffBBB", i / 10, 0.0, 0.0, 9, 9, 9) for i in range(n))


def record(oid: str, cls: str, zone: str, xyz: tuple, yaw: int, color: str, first_seen: str) -> str:
    return (f"id: {oid}\nclass: {cls}\nzone: {zone}\npose:\n  x: {xyz[0]:.2f}\n  y: {xyz[1]:.2f}\n"
            f"  z: {xyz[2]:.2f}\n  yaw: {yaw}\nextents:\n  x: 0.10\n  y: 0.10\n  z: 0.10\n"
            f'color: "{color}"\nfirst_seen: "{first_seen}"\n')


T0, T1, T2 = "2026-09-19T09:00:00Z", "2026-09-19T12:00:00Z", "2026-09-19T15:00:00Z"


def write(repo: Path, objects: list[tuple], points: int, cap: str) -> None:
    for p in (repo / "zones").glob("*/*.yaml"):
        p.unlink()
    for oid, cls, zone, xyz, yaw, color, first in objects:
        d = repo / "zones" / zone
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{oid}.yaml").write_text(record(oid, cls, zone, xyz, yaw, color, first))
    (repo / "cloud").mkdir(exist_ok=True)
    (repo / "cloud" / "current.ply").write_bytes(ply(points))
    (repo / "cloud" / "current.json").write_text(json.dumps({"capture_id": cap, "points": points}) + "\n")


def route_paths(router) -> set[str]:
    """Every path mounted under `router`, following the routers it includes.

    FastAPI 0.141 stopped copying an included router's routes into the parent: `include_router`
    now leaves ONE stand-in object behind that defers to the child at match time. So `app.routes`
    holds only what server.py declares itself — it is empty of /api/scene, and of every other
    optional router, whether or not that router mounted. Asking it "is this route there?" cannot
    tell a missing panel from a present one, so the tree is walked instead. An older FastAPI that
    really did copy the routes in walks the same way and finds them in one pass.
    """
    found = set()
    for route in getattr(router, "routes", []):
        child = getattr(route, "original_router", None)          # 0.141's included-router stand-in
        if child is not None:
            prefix = getattr(getattr(route, "include_context", None), "prefix", "") or ""
            found |= {prefix + p for p in route_paths(child)}
        elif getattr(route, "path", ""):
            found.add(route.path)
    return found


@pytest.fixture()
def room(monkeypatch, tmp_path):
    """A room whose history forks, and whose objects move, change zone, leave and come back."""
    rooms = tmp_path / "rooms"
    repo = rooms / "hall"
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    shas = {}

    write(repo, [("mug_a1b2", "mug", "desk", (0.30, -0.20, 0.75), 0, "#2b4c7e", T0),
                 ("book_e5f6", "book", "shelf", (2.10, 0.30, 1.05), 0, "#3f7d52", T0),
                 ("pen_77aa", "pen", "desk", (0.40, 0.10, 0.74), 45, "#c0392b", T0)], 5, "cap_0001")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "first scan  [cap_0001]")
    shas["first"] = git(repo, "rev-parse", "HEAD")

    # the mug moves and turns; the book changes ZONE (git sees a delete + an add); the pen goes away
    write(repo, [("mug_a1b2", "mug", "desk", (0.70, 0.10, 0.75), 60, "#2b4c7e", T0),
                 ("book_e5f6", "book", "desk", (0.90, -0.30, 0.76), 0, "#3f7d52", T0)], 7, "cap_0002")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "midday  [cap_0002]")
    shas["mid"] = git(repo, "rev-parse", "HEAD")

    # a branch off the FIRST node, so the graph forks and --all is the only way to see it
    git(repo, "checkout", "-q", "-b", "movie-night", shas["first"])
    write(repo, [("mug_a1b2", "mug", "desk", (0.30, -0.20, 0.75), 0, "#2b4c7e", T0),
                 ("book_e5f6", "book", "shelf", (2.10, 0.30, 1.05), 0, "#3f7d52", T0),
                 ("pen_77aa", "pen", "desk", (0.40, 0.10, 0.74), 45, "#c0392b", T0),
                 ("bowl_0c55", "bowl", "desk", (0.20, 0.50, 0.75), 0, "#e67e22", T2)], 9, "cap_0003")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "movie night  [cap_0003]")
    shas["night"] = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-q", "main")
    # the pen comes back: a NEW path for git, but first_seen is the one it was minted with
    write(repo, [("mug_a1b2", "mug", "desk", (0.70, 0.10, 0.75), 60, "#2b4c7e", T0),
                 ("book_e5f6", "book", "desk", (0.90, -0.30, 0.76), 0, "#3f7d52", T0),
                 ("pen_77aa", "pen", "desk", (0.44, 0.10, 0.74), 45, "#c0392b", T0)], 11, "cap_0004")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "evening  [cap_0004]")
    shas["eve"] = git(repo, "rev-parse", "HEAD")

    (rooms / ".current").write_text("hall\n")
    monkeypatch.setenv("ROOM_LIVE_DIR", str(rooms))
    return TestClient(server.app, client=("127.0.0.1", 50000)), repo, shas


def test_history_is_the_whole_dag_with_parents_and_refs(room):
    c, _repo, shas = room
    doc = c.get("/api/scene/hall/history").json()
    by = {n["sha"]: n for n in doc["commits"]}
    assert set(by) == set(shas.values()), "--all: the branch that is not HEAD's ancestor is still a node"
    assert by[shas["night"]]["parents"] == [shas["first"]], "the fork hangs off the first node"
    assert by[shas["mid"]]["parents"] == [shas["first"]]
    assert by[shas["first"]]["parents"] == []
    assert [r["name"] for r in by[shas["night"]]["refs"]] == ["movie-night"]
    assert [r["name"] for r in by[shas["eve"]]["refs"]] == ["main"]
    assert by[shas["eve"]]["refs"][0]["head"] is True and by[shas["night"]]["refs"][0]["head"] is False
    assert doc["branch"] == "main" and sorted(doc["branches"]) == ["main", "movie-night"]
    assert doc["detached"] is False and doc["head_sha"] == shas["eve"]
    # --date-order is what the lane walk relies on: a parent never comes before one of its children
    order = [n["sha"] for n in doc["commits"]]
    for n in doc["commits"]:
        for p in n["parents"]:
            assert order.index(p) > order.index(n["sha"])


def test_the_object_diff_is_identity_aware(room):
    c, _repo, shas = room
    d = c.get(f"/api/scene/hall/diff?a={shas['first'][:9]}&b={shas['mid'][:9]}").json()
    ops = {o["object_id"]: o for o in d["ops"]}
    assert d["summary"] == {"moved": 2, "removed": 1, "added": 0, "changed": 0}
    assert d["objects"] == {"a": 3, "b": 2}
    # a zone change is ONE move carrying from_zone, not a delete plus an add
    assert ops["book_e5f6"]["op"] == "moved" and ops["book_e5f6"]["from_zone"] == "shelf"
    assert ops["book_e5f6"]["zone"] == "desk" and ops["book_e5f6"]["delta_m"] > 1.0
    assert ops["mug_a1b2"]["op"] == "moved" and ops["mug_a1b2"]["delta_yaw_deg"] == 60.0
    assert ops["pen_77aa"]["op"] == "removed"
    # identity is carried out, never re-derived: this is what "new" vs "seen before" is decided on
    for op in ops.values():
        assert op["first_seen"] == T0 and op["color"].startswith("#") and op["class"]
    assert [o["op"] for o in d["ops"]] == ["moved", "moved", "removed"], "moved first, biggest move first"
    assert d["ops"][0]["object_id"] == "book_e5f6"
    assert d["frame"] == "world_z_up" and d["units"]["position"] == "m"


def test_an_object_that_left_and_came_back_keeps_its_first_seen(room):
    """The page calls this `returned` (perception/associate.py's row). It is decidable from git alone:
    a new path at b, but a first_seen older than commit a."""
    c, _repo, shas = room
    d = c.get(f"/api/scene/hall/diff?a={shas['mid']}&b={shas['eve']}").json()
    pen = next(o for o in d["ops"] if o["object_id"] == "pen_77aa")
    assert pen["op"] == "added" and pen["first_seen"] == T0
    assert d["at"]["a"] > T0, "the commit is later than the object's first sight, so the page can tell"
    # and one that really is new carries a first_seen from after the left-hand commit
    fresh = c.get(f"/api/scene/hall/diff?a={shas['first']}&b={shas['night']}").json()
    bowl = next(o for o in fresh["ops"] if o["object_id"] == "bowl_0c55")
    assert bowl["op"] == "added" and bowl["first_seen"] == T2


def test_two_nodes_with_the_same_objects_diff_to_nothing(room):
    c, _repo, shas = room
    d = c.get(f"/api/scene/hall/diff?a={shas['mid']}&b={shas['mid']}").json()
    assert d["ops"] == [] and d["summary"] == {"moved": 0, "removed": 0, "added": 0, "changed": 0}
    assert d["objects"] == {"a": 2, "b": 2}, "not 'no data': the room really does hold two objects at both"


def test_diff_refuses_what_is_not_a_commit_here(room):
    c, _repo, shas = room
    assert c.get("/api/scene/hall/diff?a=zzzz&b=" + shas["mid"]).status_code == 404
    assert c.get("/api/scene/hall/diff?a=deadbee&b=" + shas["mid"]).status_code == 404
    assert c.get(f"/api/scene/hall/diff?a={shas['mid']}").status_code == 422      # b is missing
    assert c.get(f"/api/scene/nope/diff?a={shas['mid']}&b={shas['eve']}").status_code == 404


def test_branch_names_a_node_and_moves_nothing(room):
    c, repo, shas = room
    before = git(repo, "rev-parse", "HEAD")
    r = c.post("/api/scene/hall/branch", json={"name": "try-a", "at": shas["first"]})
    assert r.status_code == 201 and r.json()["at"] == shas["first"] and r.json()["head_moved"] is False
    assert git(repo, "rev-parse", "HEAD") == before, "a branch is a name: HEAD does not move"
    assert git(repo, "rev-parse", "try-a") == shas["first"]
    doc = c.get("/api/scene/hall/history").json()
    assert "try-a" in doc["branches"] and doc["branch"] == "main"
    assert "try-a" in [r["name"] for n in doc["commits"] if n["sha"] == shas["first"] for r in n["refs"]]
    assert c.post("/api/scene/hall/branch", json={"name": "try-a", "at": shas["mid"]}).status_code == 409
    for bad in ("../escape", "has space", "-leading", "a..b", "x.lock"):
        assert c.post("/api/scene/hall/branch", json={"name": bad}).status_code == 400, bad
    assert c.post("/api/scene/hall/branch", json={"name": "ok", "at": "deadbee"}).status_code == 404


def test_checkout_moves_head_and_refuses_to_throw_work_away(room):
    c, repo, shas = room
    r = c.post("/api/scene/hall/checkout", json={"ref": "movie-night"})
    assert r.status_code == 200 and r.json()["branch"] == "movie-night" and r.json()["head"] == shas["night"]
    assert git(repo, "rev-parse", "HEAD") == shas["night"]
    doc = c.get("/api/scene/hall/history").json()
    assert doc["branch"] == "movie-night" and doc["detached"] is False
    # a dirty room is not silently reset
    (repo / "zones" / "desk" / "mug_a1b2.yaml").write_text("id: mug_a1b2\nclass: mug\n")
    assert c.post("/api/scene/hall/checkout", json={"ref": "main"}).status_code == 409
    git(repo, "checkout", "--", "zones")
    assert c.post("/api/scene/hall/checkout", json={"ref": "main"}).status_code == 200
    assert c.post("/api/scene/hall/checkout", json={"ref": "no-such-branch"}).status_code == 404
    assert c.post("/api/scene/hall/checkout", json={"ref": "a b"}).status_code == 400


def test_there_is_no_merge_anywhere_in_this_api(room):
    """roomctl puts merge, cherry-pick and stash in WRITE_VERBS and exits 2. This agrees with the CLI,
    and the agreement is the test: not a route, not a verb, not a code path."""
    c, _repo, _shas = room
    for path in ("merge", "cherry-pick", "rebase", "stash"):
        assert c.post(f"/api/scene/hall/{path}", json={"ref": "movie-night"}).status_code in (404, 405)
    # First prove the walker can still say NO, so that "no merge route" cannot pass by finding nothing
    # at all: an app with the scene router has its paths, an app without it has none.
    mounted = fastapi.FastAPI()
    mounted.include_router(scene_api.router)
    assert {p for p in route_paths(mounted) if p.startswith("/api/scene")}
    assert not {p for p in route_paths(fastapi.FastAPI()) if p.startswith("/api/scene")}

    scene = {p for p in route_paths(server.app) if p.startswith("/api/scene")}
    assert scene, "the scene router is mounted"
    assert not [p for p in scene if "merge" in p or "cherry" in p or "rebase" in p]
    # (room.git's own dashboard graph keeps /api/merge-preview for other pages — a READ, and not this API)
    # and no call in the module hands git one of those verbs: prose may SAY "nothing was merged",
    # but no argument anywhere may BE `merge`. Parsed, not grepped, so a comment cannot fail it.
    tree = ast.parse((Path(__file__).resolve().parent.parent / "scene_api.py").read_text())
    forbidden = {"merge", "merge-tree", "cherry-pick", "cherry", "rebase", "stash", "--merge", "-X", "--squash"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert "merge" not in node.name.lower(), node.name
        if isinstance(node, ast.Call):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    assert arg.value not in forbidden, f"a merge verb reaches git: {arg.value!r}"


def test_the_writes_stay_on_this_laptop(room):
    c, _repo, shas = room
    stranger = TestClient(server.app)
    assert stranger.post("/api/scene/hall/branch", json={"name": "x"}).status_code == 403
    assert stranger.post("/api/scene/hall/checkout", json={"ref": "main"}).status_code == 403
    assert stranger.get(f"/api/scene/hall/diff?a={shas['mid']}&b={shas['eve']}").status_code == 403
    tunnel = TestClient(server.app, client=("127.0.0.1", 50000))
    assert tunnel.post("/api/scene/hall/branch", json={"name": "x"},
                       headers={"x-forwarded-for": "1.2.3.4"}).status_code == 403
