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


def test_the_action_floor_is_stricter_than_the_searchs_own(monkeypatch):
    """`confident` can be True on an answer that was never close: measured 2026-09-19, "the trash" scores
    1.065 and "television remote" 1.101 against a search floor of 1.05, while real phrases score 1.27-1.62."""
    class Q:
        def resolve_object(self, text, k=5):
            return {"query": text, "confident": True, "top_score": 1.101,
                    "matches": [{"object_id": "keys_7c2e", "class": "keys", "zone": "shelf", "score": 1.101},
                                {"object_id": "speaker_6b12", "class": "speaker", "zone": "desk", "score": 0.9}],
                    "margin": 0.201}
    monkeypatch.setitem(__import__("sys").modules, "es_shared", SimpleNamespace(queries=lambda: Q()))
    with pytest.raises(ContractError) as e:
        asyncio.run(caretaker.resolve(intent("find", object_query="television remote")))
    assert e.value.code == "no_match" and e.value.details["act_floor"] == caretaker.MIN_ACT_SCORE
    assert e.value.details["confident"] is True, "the search was confident; acting is a higher bar"
