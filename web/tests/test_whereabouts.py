"""A MOTION IS NOT A SEARCH.

Elasticsearch indexes the room's WHOLE history on purpose: "where did my marker go" is the question
this product exists to answer, and filtering the index down to HEAD would delete the only thing that
can answer it. So the resolver can legitimately return an object that left the room — and for six
hours on 2026-09-20 it did, offering "shall I point at the marker on the desk?" for a marker that was
removed at 1a668ec. The three-band floors ASKED rather than acted, which is what saved it; a person
saying yes to a reasonable-sounding question is a real path to a robot driving at an empty patch of
desk. These tests pin the check that does not depend on anyone being careful.

Read-only, against the snapshot room the suite builds (main @ "afternoon: mug moved…", plus the
movie-night branch). No network.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["SENTRY_DSN"] = ""
os.environ["SENTRY_DSN_WEB"] = ""
logging.disable(logging.INFO)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import graph_api  # noqa: E402
import object_api  # noqa: E402
import server  # noqa: E402


@pytest.fixture(scope="module")
def c():
    with TestClient(server.app) as client:
        yield client


def test_a_thing_the_room_has_is_simply_present():
    w = graph_api.whereabouts("mug_a1b2")
    assert w["present"] is True and w["zone"] == "desk"
    assert graph_api.gone_sentence(w) == "the mug is in the room, on the desk"


def test_a_removed_object_says_when_it_went_and_where_it_was():
    """The refusal IS the demonstration: a room that knows the thing left, from where, and at which
    commit is a room with a history. "There is nothing like that" would be false about it."""
    w = graph_api.whereabouts("marker_c3d4")
    assert w["present"] is False and w["known"] is True
    assert w["last"]["sha"] == "e51a75a" and w["last"]["zone"] == "desk"
    assert w["gone"]["sha"] == "1a668ec" and "marker gone" in w["gone"]["subject"]
    said = graph_api.gone_sentence(w)
    assert said.startswith("the marker is not in the room any more")
    assert "on the desk at e51a75a" in said and "gone by 1a668ec" in said


def test_an_object_on_another_branch_is_a_different_true_sentence():
    """bowl_0c55 was never removed — it is on movie-night and main never had it. "It was removed" and
    "it is on another branch" are both true statements about an absent thing, and they are not the
    same statement."""
    w = graph_api.whereabouts("bowl_0c55")
    assert w["present"] is False and w["known"] is True and w["gone"] is None
    assert w["on_branches"] == ["movie-night"]
    assert "it is on movie-night" in graph_api.gone_sentence(w)


def test_an_object_no_commit_ever_had():
    w = graph_api.whereabouts("flamingo_9999")
    assert w["present"] is False and w["known"] is False and w["last"] is None
    assert graph_api.gone_sentence(w) == "there is no flamingo in the room, and no commit here ever had one"


def test_building_a_motion_for_a_departed_object_is_refused_with_its_history():
    with pytest.raises(object_api.Gone) as e:
        import asyncio
        asyncio.run(object_api.build_point("marker_c3d4", "job_test"))
    assert e.value.whereabouts["gone"]["sha"] == "1a668ec"
    assert "not in the room any more" in e.value.said
    assert isinstance(e.value, LookupError), "callers catching LookupError must keep working"


def test_the_point_endpoint_answers_409_and_carries_the_whereabouts(c):
    r = c.post("/api/object-life/marker_c3d4/point")
    assert r.status_code == 409, r.text
    j = r.json()
    assert j["error"] == "object_not_in_room" and j["retryable"] is False
    assert j["whereabouts"]["present"] is False and j["whereabouts"]["last"]["zone"] == "desk"
    assert "1a668ec" in j["detail"]


def test_a_thing_that_is_here_still_plans_its_motion(c):
    """The guard must not cost us the demo: the object the room HAS still builds a job."""
    r = c.post("/api/object-life/mug_a1b2/point")
    assert r.status_code in (200, 202), r.text
    assert r.json()["object_id"] == "mug_a1b2"


def test_history_is_still_searchable_for_what_left():
    """The counterpart of the refusal, and the reason (a) — filtering the resolver — was the wrong
    fix: the room must still KNOW about the marker. Elasticsearch is parked in this suite, so what is
    asserted here is the room's own record, which is what the index is built from."""
    assert graph_api.whereabouts("marker_c3d4")["known"] is True
    assert "marker_c3d4" in graph_api._state(graph_api._resolve("e51a75a"))["objects"]


def test_a_move_job_for_a_departed_object_is_not_executable():
    """The same boundary at the other seam. A `move` names a FROM pose; for an object that left the
    room that pose is where nothing is standing, and without this it fails later and less clearly."""
    import jobs
    job = {"command": "move", "head": graph_api._resolve("HEAD"),
           "plan": {"ops": [{"op": "moved", "object_id": "marker_c3d4",
                             "from": {"zone": "desk"}, "to": {"zone": "shelf"}}]}}
    code, why = jobs._blocker(job)
    assert code == "object_not_in_room"
    assert "not in the room any more" in why and "1a668ec" in why
    assert "a motion needs an object the room has now" in why


def test_a_move_job_for_something_here_is_still_executable():
    import jobs
    job = {"command": "move", "head": graph_api._resolve("HEAD"),
           "plan": {"ops": [{"op": "moved", "object_id": "mug_a1b2",
                             "from": {"zone": "desk"}, "to": {"zone": "shelf"}}]}}
    assert jobs._blocker(job) == (None, None)
