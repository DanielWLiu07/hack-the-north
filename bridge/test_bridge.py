"""docs/31, asserted. Every boundary is local: a temp room repo (git reads only), Andrew's real
parser as a subprocess (no network), a fake socket for the ws hub, recorders for the obs spans."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "web")]
os.environ["SENTRY_DSN"] = ""
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import bridge.agent_api as api  # noqa: E402
from bridge import andrew  # noqa: E402
from bridge.contract import ContractError, assert_frame, check_intent, route  # noqa: E402


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()


def put(repo, oid, x, zone="desk"):
    p = repo / "zones" / zone / f"{oid}.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"id: {oid}\nclass: {oid.split('_')[0]}\npose:\n  x: {x:.2f}\n  y: 0.00\n  z: 0.75\n  yaw: 0\n")


@pytest.fixture
def room_repo(tmp_path, monkeypatch):
    """c1 (tag study): mug .30, book .60 · c2: mug -> .45 · c3: book -> .70 (HEAD)."""
    r = tmp_path / "room.git"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    put(r, "mug_a1", 0.30); put(r, "book_b2", 0.60)
    git(r, "add", "-A"); git(r, "commit", "-qm", "study"); git(r, "tag", "study")
    put(r, "mug_a1", 0.45); git(r, "commit", "-qam", "mug moved")
    put(r, "book_b2", 0.70); git(r, "commit", "-qam", "book moved")
    monkeypatch.setenv("ROOM_GIT_PATH", str(r))
    import graph_api
    import room
    graph_api._state_cache.clear()
    room._blob_cache.clear()
    return r


class Rec:
    def __init__(self):
        self.spans = []

    @contextlib.contextmanager
    def cm(self, kind, name, **kw):
        sp = SimpleNamespace(data={}, status=None)
        sp.set_data = lambda k, v: sp.data.__setitem__(k, v)
        sp.set_status = lambda s: setattr(sp, "status", s)
        self.spans.append((kind, name, sp))
        yield sp


@pytest.fixture
def client(monkeypatch):
    rec = Rec()
    fake_obs = SimpleNamespace(agent_turn=lambda text, model, sdk_visible=None: rec.cm("turn", model),
                               agent_tool=lambda name, kind, **kw: rec.cm(kind, name),
                               trace_fields=lambda: {"sentry_trace_id": "0" * 32})
    monkeypatch.setattr(api, "obs", fake_obs)
    api._DONE.clear()
    app = FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    c.rec = rec
    return c


def send(c, text, rid="r1"):
    return c.post("/api/agent/command", json={"type": "user_command", "request_id": rid, "payload": {"text": text}})


# ── routing and the revert/restore line ───────────────────────────────────────────
@pytest.mark.parametrize("text,path,verb", [
    ("revert HEAD", "graph", "revert"), ("room revert HEAD~2", "graph", "revert"),
    ("git cherry-pick 1a668ec", "graph", "cherry-pick"), ("merge movie-night", "graph", "merge"),
    ("checkout study", "graph", "checkout"), ("reset --hard study", "graph", "reset"),
    ("set my room back to study mode", "middleware", None), ("restore study", "middleware", None),
    ("show me the diff", "middleware", None), ("status", "middleware", None)])
def test_graph_native_text_never_goes_to_the_middleware(text, path, verb):
    assert route(text)[:2] == (path, verb)


def test_restore_is_never_accepted_for_text_that_says_revert():
    with pytest.raises(ContractError) as e:
        check_intent("please revert to study", {"command": "restore", "target_state": "study"})
    assert e.value.code == "intent_mismatch"


def test_frames_are_asserted_never_converted():
    pose = {"x": 0.3, "y": 0.0, "z": 0.75}
    assert_frame({"frame": "world_z_up", "objects": [{"pose": pose}]}, "in")
    for bad in ({"objects": [{"pose": pose}]}, {"frame": "bracketbot_y_down", "pose": pose}):
        with pytest.raises(ContractError) as e:
            assert_frame(bad, "from gitirl-agent")
        assert e.value.code == "frame_mismatch"
    assert_frame({"command": "restore", "target_state": "study"}, "in")    # no poses: nothing to assert
    # ANDREW-HANDOFF §2 row 6: his observation declares the frame under observation.metadata
    assert_frame({"observation": {"objects": [{"object_id": "mug", "pose": pose}],
                                  "metadata": {"frame": "world_z_up"}}}, "robot_observation")
    with pytest.raises(ContractError):                                   # and a nested lie is still caught
        assert_frame({"frame": "world_z_up", "objects": [{"pose": pose, "frame": "bracketbot_y_down"}]}, "in")


# ── the endpoint ─────────────────────────────────────────────────────────────────
def test_restore_plans_as_restore_on_top_of_head_and_applies_nothing(client, room_repo, monkeypatch):
    monkeypatch.setenv("ANDREW_BRIDGE", "stub")
    before = (git(room_repo, "rev-parse", "HEAD"), git(room_repo, "status", "--porcelain"), git(room_repo, "for-each-ref"))
    b = send(client, "set my room back to study mode").json()
    assert (b["path"], b["served_by"]) == ("middleware", "stub")                 # the UI can say WHO
    assert b["intent"]["command"] == "restore" and b["intent"]["target_state"] == "study"
    a = b["action"]
    assert (a["kind"], a["as"], a["ref"]) == ("plan", "restore", "study")        # not checkout, not revert
    assert a["base_sha"] == before[0] and a["target_sha"] == git(room_repo, "rev-parse", "study")
    assert a["result"]["applied"] is False and a["result"]["git_equivalent"].startswith("git restore --source=study")
    ops = {o["object_id"]: o for o in a["result"]["ops"]}
    assert ops["mug_a1"]["kind"] == "move" and ops["mug_a1"]["to"]["pose"]["x"] == 0.30
    assert ops["book_b2"]["from"] == {"zone": "desk", "pose": {"x": 0.70, "y": 0.0, "z": 0.75, "yaw": 0.0}}
    assert all(o["frame"] == "world_z_up" and "base_pose" in o for o in ops.values())
    assert [m["type"] for m in b["messages"]] == ["parsed_command", "command_result"]
    assert [n["node"] for n in b["trace"]] == ["panel", "route", "decipher", "executor"]
    assert [(k, n) for k, n, _ in client.rec.spans] == [("turn", "gitirl-agent"), ("gitirl-agent", "decipher"),
                                                       ("gitspace", "executor.plan")]
    assert (git(room_repo, "rev-parse", "HEAD"), git(room_repo, "status", "--porcelain"),
            git(room_repo, "for-each-ref")) == before                           # read-only, provably


def test_the_command_it_prints_names_the_ref_that_exists_not_the_word_that_was_said(client, room_repo, monkeypatch):
    monkeypatch.setenv("ANDREW_BRIDGE", "stub")
    a = send(client, "restore study-mode").json()["action"]                     # the tag is `study`
    assert (a["ref"], a["result"]["ref_resolved"]) == ("study-mode", "study")     # said · exists
    cmd = a["result"]["git_equivalent"]
    assert cmd.startswith("git restore --source=study --staged"), cmd            # typeable as printed
    ref = cmd.split("--source=")[1].split()[0]
    assert git(room_repo, "rev-parse", "--verify", f"{ref}^{{commit}}") == a["target_sha"]

def test_two_spellings_on_two_commits_are_asked_about_not_guessed(client, room_repo, monkeypatch):
    import graph_api
    if not hasattr(graph_api, "resolve_state"):
        pytest.skip("web's graph_api.resolve_state is not in this checkout")
    monkeypatch.setenv("ANDREW_BRIDGE", "stub")
    git(room_repo, "tag", "focus", "HEAD~1")
    git(room_repo, "tag", "focus-mode", "HEAD")
    r = send(client, "restore Focus_Mode", rid="amb")
    b = r.json()
    assert r.status_code == 200 and b["ok"] is False and b.get("action") is None     # typed: 200, nothing planned
    assert b["error"]["code"] == "ambiguous_state"
    assert b["error"]["details"]["candidates"] == ["focus", "focus-mode"]
    assert b["trace"][-1]["node"] == "executor"
    assert send(client, "restore focus", rid="exact").json()["action"]["target_sha"] == git(room_repo, "rev-parse", "HEAD~1")

def test_revert_undoes_exactly_one_commit_and_never_touches_the_middleware(client, room_repo, monkeypatch):
    monkeypatch.setattr(andrew, "decipher", lambda *a, **k: pytest.fail("graph text reached the bridge"))
    b = send(client, "revert HEAD~1").json()                                     # c2: the mug move
    assert (b["path"], b["served_by"], b["intent"]) == ("graph", "gitspace", None)
    ops = b["action"]["result"]["ops"]
    assert [(o["object_id"], o["to"]["pose"]["x"]) for o in ops] == [("mug_a1", 0.30)]   # book untouched
    put(room_repo, "mug_a1", 0.50)
    git(room_repo, "commit", "-qam", "mug moved again")
    b = send(client, "revert HEAD~2", rid="r2").json()                           # c2 again: mug changed since
    assert b["action"]["result"]["ops"] == []
    (c,) = b["action"]["result"]["conflicts"]                                    # web's _revert gives the reason
    assert c["object_id"] == "mug_a1" and c["why"]


def test_his_real_parser_serves_and_his_own_result_is_ignored(client, room_repo, monkeypatch):
    if not andrew.JSONL.available():
        pytest.skip("no gitirl-agent checkout at ANDREW_REPO")
    monkeypatch.setenv("ANDREW_BRIDGE", "jsonl")
    b = send(client, "set my room back to study mode").json()
    assert b["served_by"] == "andrew:jsonl" and b["intent"]["command"] == "restore"
    assert [i["message"]["payload"]["status"] for i in b["ignored"]] == ["RESTORE_COMPLETE"]   # his mock's claim
    assert b["messages"][-1]["payload"]["status"] == "PLANNED"                                 # ours
    b = send(client, "revert HEAD", rid="r2").json()                             # still graph: never his
    assert b["served_by"] == "gitspace"


def test_unknown_text_is_his_error_and_a_repeat_never_plans_twice(client, room_repo, monkeypatch):
    monkeypatch.setenv("ANDREW_BRIDGE", "stub")
    r = send(client, "make me a sandwich")                                      # nobody's grammar: 200, ok false
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["error"]["code"] == "unknown_command"
    assert r.json()["messages"][0]["type"] == "error" and r.json()["trace"][-1]["node"] == "intent"
    # "put X on Y" is the caretaker's `move`: where a thing BELONGS changes only through approval, never a job
    r = send(client, "put the mug back on the shelf", rid="mv").json()
    assert (r["path"], r["intent"]["intent"], r["action"]["kind"]) == ("caretaker", "move", "proposal")
    assert r["action"]["result"]["approval_required"] is True and r["action"]["result"]["job"] is None
    r = send(client, "restore nowhere", rid="nf")                               # deciphered fine, planning fails
    assert r.status_code == 200 and r.json()["error"]["code"] == "not_found"
    assert [n["node"] for n in r.json()["trace"]][-2:] == ["decipher", "executor"]
    assert r.json()["error"]["details"]["known_states"] == ["main", "study"]    # what the judge CAN say
    calls = []
    real = api._plan_sync
    monkeypatch.setattr(api, "_plan_sync", lambda *a: calls.append(a) or real(*a))
    first = send(client, "restore study", rid="dup").json()
    again = send(client, "restore study", rid="dup").json()
    assert len(calls) == 1 and again["replayed"] is True and again["action"] == first["action"]
    assert client.post("/api/agent/command", json={"type": "merge", "request_id": "x", "payload": {}}).status_code == 400


def test_stub_mirrors_his_parser(room_repo):
    if not andrew.JSONL.available():
        pytest.skip("no gitirl-agent checkout at ANDREW_REPO")
    phrases = ["set my room back to study mode", "restore study", "commit morning", "save this as night",
               "show me the diff", "diff study", "gitirl status", "log", "add", "revert HEAD", "tidy up"]
    for i, t in enumerate(phrases):
        env = {"type": "user_command", "request_id": f"p{i}", "payload": {"text": t}}
        real = [m for m in andrew.JSONL.decipher(env) if m["type"] != "command_result"]
        ours = andrew.stub_parse(t, f"p{i}")
        strip = lambda ms: [(m["type"], {k: v for k, v in m["payload"].items() if v is not None}) for m in ms]  # noqa: E731
        assert strip(ours) == strip(real), t
    andrew.JSONL.close()


# ── his agent dialling in ────────────────────────────────────────────────────────
def test_ws_hub_correlates_his_replies_by_request_id():
    hub = andrew.WsHub()
    sent = []

    class Sock:
        async def send_text(self, t):
            sent.append(json.loads(t))
            hub.deliver({"type": "parsed_command", "request_id": "other", "payload": {}})   # not ours
            hub.deliver({"type": "parsed_command", "request_id": "w1", "payload": {"command": "status"}})
            hub.deliver({"type": "command_result", "request_id": "w1", "payload": {"status": "X"}})
    hub.socket = Sock()
    got = asyncio.run(hub.decipher({"type": "user_command", "request_id": "w1", "payload": {"text": "status"}}))
    assert [m["type"] for m in got] == ["parsed_command", "command_result"] and sent[0]["request_id"] == "w1"
    # the finalized contract: a user_command is answered by parsed_command ALONE — no timeout
    class Contract:
        async def send_text(self, t):
            hub.deliver({"type": "parsed_command", "request_id": "w3", "payload": {"command": "log"}})
    hub.socket = Contract()
    import time as _t
    t0 = _t.perf_counter()
    got = asyncio.run(hub.decipher({"type": "user_command", "request_id": "w3", "payload": {"text": "log"}}))
    assert [m["type"] for m in got] == ["parsed_command"] and _t.perf_counter() - t0 < 1.0
    hub.socket = None
    with pytest.raises(ContractError) as e:
        asyncio.run(hub.decipher({"request_id": "w2", "payload": {"text": "status"}}))
    assert e.value.code == "bridge_unavailable"


def test_a_stranger_through_the_tunnel_cannot_pose_as_his_agent(monkeypatch):
    ws = lambda host, **h: SimpleNamespace(client=SimpleNamespace(host=host), headers=h)  # noqa: E731
    monkeypatch.delenv("GITIRL_WS_TOKEN", raising=False)
    assert api._trusted(ws("127.0.0.1")) and api._trusted(ws("192.168.2.10"))
    assert not api._trusted(ws("127.0.0.1", **{"cf-connecting-ip": "1.2.3.4"}))     # came via cloudflared
    monkeypatch.setenv("GITIRL_WS_TOKEN", "s3cret")
    assert api._trusted(ws("127.0.0.1", **{"cf-connecting-ip": "1.2.3.4", "authorization": "Bearer s3cret"}))


def test_the_demo_phrases_as_people_say_them(client, room_repo, monkeypatch):
    monkeypatch.setenv("ANDREW_BRIDGE", "stub")
    for i, text in enumerate(["set my room back to study mode", "restore study-mode", "restore Study"]):
        b = send(client, text, rid=f"demo{i}").json()
        assert b["ok"] is True and b["action"]["as"] == "restore", (text, b.get("error"))
        assert b["action"]["result"]["ref_resolved"] == "study" and len(b["action"]["result"]["ops"]) == 2
    b = send(client, "room status", rid="rs").json()                            # CLI habit: the caretaker's status
    assert b["ok"] is True and b["intent"]["intent"] == "status" and b["intent"]["raw_text"] == "room status"
    assert b["action"]["kind"] == "read" and "clean" in b["action"]["result"]
    b = send(client, "revert HEAD", rid="rv").json()
    assert b["ok"] is True and b["path"] == "graph"


def test_a_moment_plans_through_the_same_planner_and_an_unplaceable_one_says_so(client, room_repo, monkeypatch):
    """"the way it was 2 hours ago" is a restore of the commit before that moment (web's resolve_state
    places the phrase). A moment with no commit before it is named as a MOMENT, not as a misspelt state."""
    import graph_api
    if not hasattr(graph_api, "resolve_state"):
        pytest.skip("web's graph_api.resolve_state is not in this checkout")
    b = send(client, "put the room back the way it was 2 hours ago", rid="t1").json()
    if b.get("error", {}).get("code") == "not_found":
        assert "before" in b["error"]["message"], b["error"]                 # no commit that old in this fixture
        assert b["error"]["details"]["how"] == "time" and b["error"]["details"]["when"]
    else:
        assert b["ok"] and b["action"]["as"] == "restore" and b["action"]["result"]["applied"] is False
    b = send(client, "put the room back like it was before dinner", rid="t2").json()
    if not b["ok"]:
        assert b["error"]["code"] in ("not_found", "room_unavailable")
        if b["error"]["code"] == "not_found":
            assert b["error"]["details"].get("how") == "time", "a moment, not a state name"
