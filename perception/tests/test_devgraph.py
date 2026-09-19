"""perception/devgraph.py -- the graph's write path -- on a COPY of room.git (never the real one).
The web reads it relays are stubbed from the copy's own records; nothing reaches the network.
Preview writes nothing; STAGE and COMMIT are separate, explicit requests; ABORT restores exactly."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
fastapi_testclient = pytest.importorskip("fastapi.testclient")
import devgraph  # noqa: E402
from roomctl.repo import Repo  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def room(tmp_path, monkeypatch):
    copy = tmp_path / "room.git"
    shutil.copytree(REPO / "room.git", copy, ignore=shutil.ignore_patterns("gitspace"))
    subprocess.run(["git", "-C", str(copy), "reset", "-q", "--hard", "HEAD"], check=True)
    subprocess.run(["git", "-C", str(copy), "clean", "-qfd", "zones"], check=True)
    monkeypatch.setenv("ROOM_GIT_PATH_DEVGRAPH", str(copy))
    repo = Repo(copy)

    def fake_web(path, method="GET", body=None, timeout=20):
        if path.startswith("/api/state?ref="):
            ref = path.split("=", 1)[1]
            recs = repo.records(ref)
            return 200, {"sha": ref, "zones": {}, "objects": [
                {"object_id": r.id, "class": r.cls, "zone": r.zone, "pose": vars(r.pose),
                 "extents": vars(r.extents), "color": r.color} for r in recs.values()]}
        if path == "/api/status":
            return 200, {}
        return 404, {"error": {"code": "not_found", "message": "stubbed"}}

    monkeypatch.setattr(devgraph, "web", fake_web)
    monkeypatch.setattr(devgraph, "_costmap", lambda: (_ for _ in ()).throw(RuntimeError("no ES in tests")))
    monkeypatch.setattr(devgraph, "_enrich", lambda shas: ({}, devgraph.pending("enrichment", "no ES in tests")))
    return repo


@pytest.fixture
def client(room):
    return fastapi_testclient.TestClient(devgraph.app)


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo.path), *a], capture_output=True, text=True, check=True).stdout.strip()


def _oldest(repo):
    return _git(repo, "rev-list", "--max-parents=0", "HEAD").splitlines()[0]


def test_preview_writes_nothing(client, room):
    before = (_git(room, "rev-parse", "HEAD"), _git(room, "status", "--porcelain"))
    p = client.get("/dev/preview", params={"op": "restore", "ref": _oldest(room)}).json()
    assert p["base_sha"] == before[0] and p["frame"] == "world_z_up" and p["stageable"]
    want = {oid for oid in set(room.records("HEAD")) | set(room.records(_oldest(room)))
            if room.records("HEAD").get(oid) != room.records(_oldest(room)).get(oid)}
    assert {o["object_id"] for o in p["ops"]} == want
    assert all(o["base_pose"]["status"] == "pending" for o in p["ops"])    # no costmap: visibly pending
    assert all(o["kind"] in ("move", "add", "remove") and o["frame"] == "world_z_up" for o in p["ops"])   # docs/31 shape
    assert p["applied"] is False
    assert p["robot"]["status"] == "pending"
    assert (_git(room, "rev-parse", "HEAD"), _git(room, "status", "--porcelain")) == before


def test_stage_is_guarded(client, room):
    old = _oldest(room)
    assert client.post("/dev/stage", json={"op": "restore", "ref": old, "base_sha": "0" * 40}).json()["error"]["code"] == "moved"
    (room.path / "zones" / "desk" / "stray.yaml").write_text("x: 1\n")                  # a scan wrote something
    assert client.post("/dev/stage", json={"op": "restore", "ref": old, "base_sha": _git(room, "rev-parse", "HEAD")}
                       ).json()["error"]["code"] == "dirty"
    (room.path / "zones" / "desk" / "stray.yaml").unlink()
    assert client.post("/dev/commit", json={}).json()["error"]["code"] == "nothing_staged"


def test_stage_then_abort_leaves_room_exactly_as_it_was(client, room):
    head = _git(room, "rev-parse", "HEAD")
    before = {p: p.read_bytes() for p in (room.path / "zones").rglob("*.yaml")}
    s = client.post("/dev/stage", json={"op": "restore", "ref": _oldest(room), "base_sha": head}).json()
    assert s["staged"] and _git(room, "status", "--porcelain")                         # the index is written...
    assert _git(room, "rev-parse", "HEAD") == head                                     # ...nothing committed
    assert client.post("/dev/stage", json={"op": "restore", "ref": _oldest(room), "base_sha": head}
                       ).json()["error"]["code"] == "already_staged"
    assert client.post("/dev/abort").json()["clean"] is True
    assert {p: p.read_bytes() for p in (room.path / "zones").rglob("*.yaml")} == before


def test_restore_stages_then_commits_exactly_the_target(client, room):
    head, old = _git(room, "rev-parse", "HEAD"), _oldest(room)
    client.post("/dev/stage", json={"op": "restore", "ref": old, "base_sha": head})
    c = client.post("/dev/commit", json={}).json()
    assert re.fullmatch(r"[0-9a-f]{40}", c["committed"]) and c["publish"]["status"] == "pending"
    assert _git(room, "rev-parse", "HEAD~1") == head                                   # a NEW commit on top
    assert room.records("HEAD") == room.records(old)                                   # the room is the old state
    assert _git(room, "status", "--porcelain") == "" and devgraph.staged_state() is None


def test_revert_undoes_one_commit(client, room):
    head = _git(room, "rev-parse", "HEAD")
    p = client.get("/dev/preview", params={"op": "revert", "ref": head}).json()
    assert p["stageable"] and p["ops"]
    client.post("/dev/stage", json={"op": "revert", "ref": head, "base_sha": head})
    client.post("/dev/commit", json={})
    assert room.records("HEAD") == room.records(f"{head}~1")                           # reverting HEAD = its parent
    assert _git(room, "log", "-1", "--format=%s").startswith("Revert")


def test_switch_takes_a_branch_and_one_confirmation(client, room):
    branches = [b for b in _git(room, "branch", "--format=%(refname:short)").split() if b != room.branch()]
    if not branches:
        pytest.skip("the room has one branch")
    head = _git(room, "rev-parse", "HEAD")
    assert client.get("/dev/preview", params={"op": "switch", "ref": head}).status_code == 422   # a sha would detach
    r = client.post("/dev/switch", json={"ref": branches[0], "base_sha": head}).json()
    assert r["switched"] == branches[0] and room.branch() == branches[0]


def test_agent_envelope_is_andrews_verbatim_and_routes(client):
    r = client.post("/dev/agent", json={"text": "set my room back to study mode"}).json()
    env = r["sent"]
    assert set(env) == {"type", "request_id", "timestamp", "payload"} and env["type"] == "user_command"
    assert isinstance(env["request_id"], str) and env["request_id"] and env["payload"] == {"text": "set my room back to study mode"}
    assert r["path"] == "middleware" and r["response"]["status"] == "pending"          # docs/31 not live: says so
    g = client.post("/dev/agent", json={"text": "revert the last commit"}).json()
    assert g["path"] == "graph" and "never sent to Andrew" in g["why"]
    assert client.post("/dev/agent", json={"text": "  "}).status_code == 422


def test_cors_is_open_to_the_8124_page_only(client):
    ok = client.get("/dev/status", headers={"Origin": "http://127.0.0.1:8124"})
    assert ok.headers.get("access-control-allow-origin") == "http://127.0.0.1:8124"
    other = client.get("/dev/status", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_graph_renders_from_git_alone_when_web_and_es_are_down(client, room):
    g = client.get("/dev/graph").json()
    log = _git(room, "log", "--all", "--format=%H").split()
    assert sorted(n["sha"] for n in g["nodes"]) == sorted(log) and g["head"] == _git(room, "rev-parse", "HEAD")
    assert g["enrichment"]["status"] == "pending" and "git log" in g["served_by"]


def test_revert_is_one_commit_not_back_to_it(client, room):
    """docs/31: revert undoes ONE commit -- not 'go back to that commit' (which also undoes everything
    since). Reverting an older commit leaves later changes; objects touched since come back as conflicts."""
    commits = _git(room, "rev-list", "--first-parent", "HEAD").split()
    if len(commits) < 3:
        pytest.skip("need a commit with a successor")
    older = commits[1]
    p = client.get("/dev/preview", params={"op": "revert", "ref": older}).json()
    changed_by_older = {o for o in set(room.records(f"{older}~1")) | set(room.records(older))
                        if room.records(f"{older}~1").get(o) != room.records(older).get(o)}
    assert {o["object_id"] for o in p["ops"]} | {c["object_id"] for c in p["conflicts"]} <= changed_by_older
    restore = client.get("/dev/preview", params={"op": "restore", "ref": f"{older}"}).json()
    assert p["ops"] != restore["ops"] or not p["ops"]                    # not the same thing as going back


def test_agent_relays_docs31_errors_as_answers_not_as_down(client, monkeypatch):
    """docs/31 section 3: an error keeps the response body at any status -- a 404 for a state that
    doesn't exist is an ANSWER from the live endpoint, not 'endpoint not live'."""
    body = {"request_id": "r1", "path": "middleware", "served_by": "andrew:jsonl",
            "error": {"code": "not_found", "message": "no study"}, "trace": [{"node": "executor"}]}
    monkeypatch.setattr(devgraph, "web", lambda path, method="GET", b=None, timeout=20:
                        (404, body) if path == "/api/agent/command" else (404, {}))
    r = client.post("/dev/agent", json={"text": "set my room back to study mode", "request_id": "r1"}).json()
    assert r["served_by"] == "andrew:jsonl" and r["response"]["error"]["code"] == "not_found"
    assert r["http_status"] == 404   # a pre-"ok" server says "did not work" by status alone


def test_agent_relays_ok_false_at_200(client, monkeypatch):
    """docs/31 section 3, after black-box testing: anything a person can type answers 200 with
    "ok": false -- the relay must hand "ok" and the not_found hints through untouched."""
    body = {"ok": False, "request_id": "r2", "path": "middleware", "served_by": "andrew:jsonl",
            "error": {"code": "not_found", "message": "no state party",
                      "details": {"known_states": ["main", "movie-night", "study"], "hint": "known states: main, movie-night, study"}}}
    monkeypatch.setattr(devgraph, "web", lambda path, method="GET", b=None, timeout=20:
                        (200, body) if path == "/api/agent/command" else (404, {}))
    r = client.post("/dev/agent", json={"text": "restore party", "request_id": "r2"}).json()
    assert r["http_status"] == 200 and r["response"]["ok"] is False
    assert r["response"]["error"]["details"]["known_states"] == ["main", "movie-night", "study"]
