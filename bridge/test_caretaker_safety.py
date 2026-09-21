"""Any intent that NAMES an object must resolve it before acting, and must honour the resolver's verdict.

A vector search has no "not found": a nearest neighbour always exists, so "pick up the trash" in a room
with no trash comes back as a ceramic cup. Two guards, and a path that skips them is the bug:
  · `confident` / the action floor — is any of these the thing at ALL (elastic/queries.py, MIN_ACT_SCORE)
  · `margin` — are the top two too close to call
The audit these tests pin: find, point, tidy, move, blame and restore_time all resolve what they name."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))   # room, graph_api, housebot
from types import SimpleNamespace

import pytest

from bridge import caretaker
from bridge.contract import ContractError

NAMING = ["find", "point", "tidy", "move", "blame", "restore_time"]


def intent(kind, **kw):
    base = {"request_id": "r1", "intent": kind, "object_query": "the trash", "object_id": None,
            "zone": None, "when": None, "raw_text": "…", "confidence": 1.0, "source": "openai"}
    return {**base, **kw}


@pytest.mark.parametrize("kind", NAMING)
def test_an_intent_that_names_a_thing_resolves_it_before_acting(kind, monkeypatch):
    asked = []

    async def resolve(i):
        asked.append(i["intent"])
        raise ContractError("no_match", "there is nothing in the room that matches 'the trash'; "
                                        "the nearest are bowl, plant and cup", 404, {"candidates": ["bowl_0c55"]})
    monkeypatch.setattr(caretaker, "resolve", resolve)
    monkeypatch.setattr(caretaker, "tidy_jobs", lambda *a, **k: pytest.fail("planned before resolving"))
    monkeypatch.setattr(caretaker, "_zones", lambda: ["desk", "shelf", "bin"])
    kw = {"zone": "shelf"} if kind == "move" else ({"when": "2 hours ago"} if kind == "restore_time" else {})
    with pytest.raises(ContractError) as e:
        asyncio.run(caretaker.act(intent(kind, **kw)))
    assert e.value.code == "no_match", f"{kind} acted on an object the room does not have"
    assert asked == [kind], f"{kind} never asked the resolver"
    assert "nothing in the room" in e.value.message


def test_a_tidy_that_names_a_thing_tidies_that_thing_not_the_room(monkeypatch):
    seen = {}

    async def resolve(i):
        return {"object_id": "mug_a1b2", "class": "mug", "how": "elasticsearch", "score": 1.4, "confident": True}
    monkeypatch.setattr(caretaker, "resolve", resolve)
    monkeypatch.setattr(caretaker, "_zones", lambda: ["desk", "shelf", "bin"])
    monkeypatch.setattr(caretaker, "tidy_jobs",
                        lambda zone, rid, only=None: seen.update(zone=zone, only=only) or
                        {"head": "abc", "zone": zone, "object_id": only, "jobs": [], "skipped": [], "frame": "world_z_up"})
    out = asyncio.run(caretaker.act(intent("tidy", object_query="the mug")))
    assert seen == {"zone": None, "only": "mug_a1b2"}, "a named thing must not become a whole-room tidy"
    assert out["ref"] == "mug_a1b2" and "already where it belongs" in out["result"]["detail"]


def test_a_tidy_with_no_object_is_still_the_whole_room(monkeypatch):
    seen = {}
    monkeypatch.setattr(caretaker, "resolve", lambda i: pytest.fail("nothing was named"))
    monkeypatch.setattr(caretaker, "tidy_jobs",
                        lambda zone, rid, only=None: seen.update(zone=zone, only=only) or
                        {"head": "abc", "zone": zone, "object_id": only, "jobs": [], "skipped": [], "frame": "world_z_up"})
    asyncio.run(caretaker.act(intent("tidy", object_query=None)))
    assert seen == {"zone": None, "only": None}


def _es(score, confident=True, runner=0.9):
    class Q:
        def resolve_object(self, text, k=5):
            return {"query": text, "confident": confident, "top_score": score,
                    "matches": [{"object_id": "mug_a1b2", "class": "mug", "zone": "desk", "score": score},
                                {"object_id": "bowl_0c55", "class": "bowl", "zone": "desk", "score": runner}],
                    "margin": round(score - runner, 3)}
    return SimpleNamespace(queries=lambda: Q())


@pytest.mark.parametrize("score,confident,band", [
    (0.98, False, "refuse"),     # below the search floor: nothing in the room is that thing
    (1.065, True, "refuse"),     # "the trash" with a bowl in the room: absurd, so refuse — never ask,
    (1.101, True, "refuse"),     # "television remote": the TOP of the absurd cluster, still a refusal,
                                 #   because a question invites a yes and a yes would act on a bowl
    (1.126, True, "ask"),        # "something to write with": a REAL object, vaguely said
    (1.169, True, "ask"),        # "something to drink from": likewise
    (1.266, True, "act"),        # "the thing I cut paper with"
    (1.454, True, "act"),        # "my keys"
])
def test_three_bands_refuse_ask_act(monkeypatch, score, confident, band):
    """Measured on this room (see the constants' comment). A 1.20 hard floor refused the two vague-but-real
    phrasings the conversational beat is built on; a 1.05 floor alone acted on "the trash"."""
    monkeypatch.setitem(__import__("sys").modules, "es_shared", _es(score, confident))
    if band == "refuse":
        with pytest.raises(ContractError) as e:
            asyncio.run(caretaker.resolve(intent("find", object_query="whatever")))
        assert e.value.code == "no_match"
        return
    found = asyncio.run(caretaker.resolve(intent("find", object_query="whatever")))
    assert bool(found.get("needs_confirmation")) is (band == "ask"), f"{score} should {band}"
    if band == "ask":
        assert found["runner_up"]["object_id"] == "bowl_0c55" and found["act_floor"] == caretaker.MIN_ACT_SCORE


def test_every_branch_of_the_outcome_line_survives_a_missing_field():
    """_outcome runs on every answer. A field it assumed once turned a SUCCEEDED request into a 500 on
    /api/agent/command (Sentry: KeyError 'detail'), so every kind is checked with an empty result."""
    from bridge import agent_api
    for kind in ("job", "jobs", "confirm", "gone", "proposal", "plan", "read", "refused", "something_new"):
        status, line = agent_api._outcome({"kind": kind, "result": {}})
        assert isinstance(status, str) and isinstance(line, str) and line
    status, line = agent_api._outcome({"kind": "job"})              # no `result` at all
    assert status in ("PLANNED", "DISPATCHED") and isinstance(line, str)


def test_the_ask_band_asks_and_moves_nothing(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "es_shared", _es(1.169))
    monkeypatch.setattr(caretaker, "point_job", lambda *a: pytest.fail("planned a job on a guess"))
    out = asyncio.run(caretaker.act(intent("point", object_query="something to drink from")))
    assert out["kind"] == "confirm" and out["ref"] == "mug_a1b2"
    r = out["result"]
    assert "shall I point at it?" in r["question"] and "mug" in r["question"]
    assert r["candidate"]["score"] == 1.169 and r["runner_up"]["class"] == "bowl"
    assert r["yes"]["payload"]["object_id"] == "mug_a1b2", "the yes is an explicit second request"
    assert "nothing has been planned" in r["no"]


def test_a_yes_acts_and_a_no_is_simply_never_sent(monkeypatch):
    """The confirmation is a real exchange: the same sentence again, naming the object. There is no pending
    state and no timer on our side, so an unanswered question can never become an action."""
    monkeypatch.setitem(__import__("sys").modules, "es_shared", _es(1.169))
    built = []
    async def fake_point_job(oid, rid):
        built.append(oid)
        return {"job_id": "job_x", "command": "point", "object_id": oid, "target_pose": {"x": 0, "y": 0, "z": 0},
                "zone": "desk", "pointing_at": "it", "frame": "world_z_up", "executor": "not_connected",
                "state": "queued (no executor connected)"}
    monkeypatch.setattr(caretaker, "point_job", fake_point_job)
    asked = asyncio.run(caretaker.act(intent("point", object_query="something to drink from")))
    assert asked["kind"] == "confirm" and built == [], "asking must not build anything"
    said_yes = {**intent("point", object_query="something to drink from"), "object_id": "mug_a1b2"}
    out = asyncio.run(caretaker.act(said_yes))
    assert out["kind"] == "job" and built == ["mug_a1b2"], "the confirmed object is acted on"
    assert out["result"]["resolved"]["how"] == "id"


def test_a_too_close_call_asks_with_both_named_rather_than_refusing(monkeypatch):
    """Before the confirm band existed, two candidates a hair apart could only be refused ("say which
    one"). Now the person settles it in one click — and a gripper still never moves on a coin flip."""
    monkeypatch.setitem(__import__("sys").modules, "es_shared", _es(1.463, runner=1.441))   # margin 0.022
    found = asyncio.run(caretaker.resolve(intent("find", object_query="the mug")))
    assert found["needs_confirmation"] is True and found["why_ask"] == "the top two are too close to call"
    assert found["score"] == 1.463 and found["runner_up"]["object_id"] == "bowl_0c55"
    out = asyncio.run(caretaker.act(intent("point", object_query="the mug")))
    assert out["kind"] == "confirm" and "not the bowl" in out["result"]["question"]
    assert "too close to call" in out["result"]["why"] and "1.441" in out["result"]["why"]


def test_a_clear_winner_still_acts_without_asking(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "es_shared", _es(1.454, runner=0.9))
    found = asyncio.run(caretaker.resolve(intent("find", object_query="my keys")))
    assert not found.get("needs_confirmation") and found["score"] == 1.454


# ── a motion is not a search ───────────────────────────────────────────────────────────────────────
GONE = {"object_id": "marker_c3d4", "present": False, "known": True,
        "last": {"sha": "e51a75a", "subject": "initial scan: the bench as found", "zone": "desk"},
        "gone": {"sha": "1a668ec", "subject": "afternoon: mug moved, marker gone, scissors out"},
        "on_branches": []}


def _departed(monkeypatch, score):
    """The resolver's best match is a REAL object that is no longer in the room. Elasticsearch indexes
    the room's whole history on purpose, so this is not a bug in the search — it is the correct answer
    to "something to write with" in a room that had a marker. The git lookup is stubbed here; what it
    returns for real is pinned in web/tests/test_whereabouts.py."""
    import graph_api
    class Q:
        def resolve_object(self, text, k=5):
            return {"query": text, "confident": True, "top_score": score,
                    "matches": [{"object_id": "marker_c3d4", "class": "marker", "zone": "desk", "score": score},
                                {"object_id": "bowl_0c55", "class": "bowl", "zone": "desk", "score": 0.9}],
                    "margin": round(score - 0.9, 3)}
    monkeypatch.setitem(__import__("sys").modules, "es_shared", SimpleNamespace(queries=lambda: Q()))
    monkeypatch.setattr(graph_api, "whereabouts", lambda oid: GONE if oid == "marker_c3d4" else
                        {"object_id": oid, "present": True, "known": True, "zone": "desk"})
    monkeypatch.setattr(caretaker, "point_job", lambda *a: pytest.fail("built a motion for a thing that left"))


@pytest.mark.parametrize("score,band", [(1.126, "ask"), (1.454, "act")])
def test_a_thing_that_left_the_room_is_answered_never_offered(monkeypatch, score, band):
    """THE FLOORS DO NOT SAVE YOU HERE, which is the whole point. At 1.126 the resolver would ASK
    ("shall I point at the marker on the desk?") and a person saying yes is a real path to a robot
    driving at an empty patch of desk; at 1.454 it would ACT outright. Neither happens: the room says
    where the thing went. This is the check that does not depend on anybody being careful."""
    _departed(monkeypatch, score)
    out = asyncio.run(caretaker.act(intent("point", object_query="something to write with")))
    assert out["kind"] == "gone", f"the {band} band must not reach a motion for a departed object"
    said = out["result"]["speech"]
    assert said.startswith("the marker is not in the room any more")
    assert "on the desk at e51a75a" in said and "gone by 1a668ec" in said
    assert out["result"]["whereabouts"]["present"] is False
    assert "no job" in out["result"]["detail"]


def test_a_yes_for_a_departed_object_still_builds_nothing(monkeypatch):
    """The confirmation exchange is an explicit second request naming the object. If the first answer
    had been the ask, the yes must not become the motion the ask never should have offered."""
    _departed(monkeypatch, 1.126)
    out = asyncio.run(caretaker.act(intent("point", object_query="the marker", object_id="marker_c3d4")))
    assert out["kind"] == "gone" and out["ref"] == "marker_c3d4"


def test_the_outcome_line_reports_gone_as_an_answer_not_a_refusal():
    from bridge import agent_api
    status, line = agent_api._outcome({"kind": "gone", "result": {"speech": "the marker is not in the room any more"}})
    assert status == "GONE" and line.startswith("the marker is not in the room")


def test_a_thing_that_is_here_is_untouched_by_the_guard(monkeypatch):
    """The guard must not cost the demo its best sentence."""
    monkeypatch.setitem(__import__("sys").modules, "es_shared", _es(1.454))
    import graph_api
    monkeypatch.setattr(graph_api, "whereabouts", lambda oid: {"object_id": oid, "present": True, "known": True,
                                                               "zone": "desk"})
    monkeypatch.setattr(caretaker, "point_job", lambda oid, rid: _done({"job_id": "job_1", "object_id": oid}))
    import housebot
    monkeypatch.setattr(housebot, "submit", lambda job: _done({"dispatched": True, "state": "dispatching"}))
    out = asyncio.run(caretaker.act(intent("point", object_query="my keys")))
    assert out["kind"] == "job" and out["ref"] == "mug_a1b2"


def _done(value):
    async def go():
        return value
    return go()


# ── the room on screen, when Elastic is answering about a different one ────────────────
# A scene instance is its own room; Elastic's index is room.git's. These cover the fallback that
# noticed the difference, and the two rules that keep a language model away from the gripper.

def _room_with(tmp_path, monkeypatch, **objects):
    """A scene instance holding these {object_id: class} on the floor, set as rooms/.current."""
    rooms = tmp_path / "rooms"
    repo = rooms / "chips"
    (repo / "zones" / "floor").mkdir(parents=True)
    (repo / ".git").mkdir()
    for oid, cls in objects.items():
        (repo / "zones" / "floor" / f"{oid}.yaml").write_text(
            f"id: {oid}\nclass: {cls}\nzone: floor\npose:\n  x: 0.8\n  y: -0.6\n  z: 0.05\n  yaw: 0\n")
    (rooms / ".current").write_text("chips\n")
    monkeypatch.setenv("ROOM_LIVE_DIR", str(rooms))
    return repo


def test_a_name_this_room_does_use_is_answered_from_this_room(tmp_path, monkeypatch):
    """Elastic offered a desk nobody is looking at; the packet is on the floor in front of us."""
    _room_with(tmp_path, monkeypatch, packet_a1b2="chip packet", box_7c2e="small box")
    monkeypatch.setitem(__import__("sys").modules, "es_shared", _es(0.98, False))
    found = asyncio.run(caretaker.resolve(intent("find", object_query="the chip packet")))
    assert found["object_id"] == "packet_a1b2" and found["how"] == "room:chips"
    assert not found.get("needs_confirmation")          # its own word for it: no question needed


def test_a_name_it_does_not_use_reaches_the_model_and_comes_back_as_a_question(tmp_path, monkeypatch):
    _room_with(tmp_path, monkeypatch, packet_a1b2="chip packet", box_7c2e="small box")
    monkeypatch.setitem(__import__("sys").modules, "es_shared", _es(0.98, False))
    monkeypatch.setattr(caretaker, "_llm_pick",
                        lambda q, objs: {"object_id": "packet_a1b2", "why": "a snack bag is a chip packet"})
    found = asyncio.run(caretaker.resolve(intent("find", object_query="snack bag")))
    assert found["object_id"] == "packet_a1b2" and found["how"] == "llm:chips"
    assert found["needs_confirmation"] is True          # a description is never acted on outright
    assert "snack bag" in found["why_ask"]


def test_the_model_can_never_invent_an_object(tmp_path, monkeypatch):
    """Its answer is checked against the list it was handed; anything else is thrown away, and
    Elastic's refusal stands."""
    _room_with(tmp_path, monkeypatch, packet_a1b2="chip packet")
    monkeypatch.setitem(__import__("sys").modules, "es_shared", _es(0.98, False))
    monkeypatch.setattr(caretaker, "_llm_pick", lambda q, objs: None)
    with pytest.raises(ContractError) as e:
        asyncio.run(caretaker.resolve(intent("find", object_query="a teapot")))
    assert e.value.code == "no_match"


def test_elastic_keeps_the_answer_when_it_is_about_this_room(tmp_path, monkeypatch):
    """The fallback only fires when Elastic was talking about somewhere else: if it offered an
    object that IS here, its refusal is about this room and is the honest answer."""
    _room_with(tmp_path, monkeypatch, mug_a1b2="mug")         # the id _es() offers
    monkeypatch.setitem(__import__("sys").modules, "es_shared", _es(0.98, False))
    monkeypatch.setattr(caretaker, "_llm_pick", lambda q, objs: {"object_id": "mug_a1b2", "why": "no"})
    with pytest.raises(ContractError) as e:
        asyncio.run(caretaker.resolve(intent("find", object_query="a teapot")))
    assert e.value.code == "no_match"


def test_a_confident_answer_about_another_room_does_not_win_over_this_one(tmp_path, monkeypatch):
    """The live failure this was written for: Elastic answered 'the bowl, on the desk' — confidently,
    and about room.git — while the robot stood in a room holding a chip packet on the floor."""
    _room_with(tmp_path, monkeypatch, packet_a1b2="chip packet")
    monkeypatch.setitem(__import__("sys").modules, "es_shared", _es(1.45, True))     # the ACT band
    monkeypatch.setattr(caretaker, "_llm_pick",
                        lambda q, objs: {"object_id": "packet_a1b2", "why": "a snack bag is a chip packet"})
    found = asyncio.run(caretaker.resolve(intent("find", object_query="snack bag")))
    assert found["object_id"] == "packet_a1b2" and found["instead_of"]["object_id"] == "mug_a1b2"
    assert found["needs_confirmation"] is True
