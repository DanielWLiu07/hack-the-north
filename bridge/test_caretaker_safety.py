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
    for kind in ("job", "jobs", "confirm", "proposal", "plan", "read", "refused", "something_new"):
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
