"""The room must be able to say "that is not here".

A vector search always returns a nearest neighbour, so before MIN_RELEVANCE existed "pick up the
trash" -- in a room containing no trash -- resolved to cup_7e21, a ceramic cup, with a margin of
0.015. A search box shrugs that off. A gripper bins the cup.

These run against the live cluster and the real room, because the floor is a measurement and a
mocked one would prove nothing. If the reranker changes, THIS is the file that fails and the
numbers in queries.MIN_RELEVANCE's comment are the ones to re-measure.
"""
from __future__ import annotations

import pytest

from queries import MIN_RELEVANCE, Queries

BRANCH = "main"

# Things the room really holds. Every one must resolve confidently.
PRESENT = [
    ("where are my keys", "keys_7c2e"),
    ("the thing I cut paper with", "scissors_9f3a"),
    ("where is the hammer", "tool_4f2a"),
    ("my notebook", "notebook_5a0c"),
    ("the lamp", "lamp_2d9b"),
    ("something to drink from", None),      # cup or mug: both are right, so no fixed answer
    ("something to write with", None),
]

# Things the room does not hold. The destructive phrasings matter most: acting on a wrong
# answer here is a robot throwing away something you own.
ABSENT = ["pick up the trash", "throw away the rubbish", "tidy up",
          "where is my phone", "my shoes", "the dog", "my umbrella"]

# Also absent, but the nearest object is a DEFENSIBLE answer -- a mug really is a thing you could
# put water in. These are where the floor stops being able to help, so they are kept apart from
# the clearly-absent set rather than mixed into it: a test that calls a reasonable answer wrong
# would push the threshold up until it started refusing real objects.
NEAR_MISS = ["a bottle of water", "the television remote", "the banana"]


@pytest.fixture(scope="module")
def q(es):
    return Queries(es)


@pytest.mark.parametrize("text,expected", PRESENT)
def test_a_real_object_resolves_confidently(q, text, expected):
    r = q.resolve_object(text, k=3, branch=BRANCH)
    assert r["confident"], f"{text!r} scored {r['top_score']:.3f}, under the {MIN_RELEVANCE} floor"
    if expected:
        assert r["matches"][0]["object_id"] == expected


@pytest.mark.parametrize("text", ABSENT)
def test_an_absent_object_is_refused_rather_than_guessed(q, text):
    r = q.resolve_object(text, k=3, branch=BRANCH)
    assert not r["confident"], (
        f"{text!r} resolved to {r['matches'][0]['object_id']} at {r['top_score']:.3f} -- "
        f"the room does not contain it, and a caller acting on this moves the wrong object")


def test_the_floor_sits_in_a_real_gap(q):
    """Not just "the threshold works" but "there is daylight on both sides of it". If this gap
    closes, the floor is doing nothing and the shape of the fix has to change."""
    worst_present = min(q.resolve_object(t, k=3, branch=BRANCH)["top_score"] for t, _ in PRESENT)
    best_absent = max(q.resolve_object(t, k=3, branch=BRANCH)["top_score"] for t in ABSENT)
    assert best_absent < MIN_RELEVANCE <= worst_present, (
        f"absent tops out at {best_absent:.3f}, present bottoms at {worst_present:.3f}, "
        f"floor {MIN_RELEVANCE}")


# ── the condition the CALLER actually uses ───────────────────────────────────
#
# Everything above is branch-scoped. bridge/caretaker.py calls resolve_object(query, k=5) with NO
# branch, so it searches every branch -- more objects, higher scores for absent phrasings. The
# tests above passed while "the trash" resolved confidently to bowl_0c55 in production, because
# they measured a condition the caller does not use. That is the bug these two exist to stop.

@pytest.mark.parametrize("text", ABSENT)
def test_unscoped_is_the_production_condition_and_is_measured_too(q, text):
    """Unscoped, MIN_RELEVANCE does NOT hold the line -- and that is the documented reason a
    second, higher floor lives at the point of action. This test records which phrasings get
    through so the list cannot grow silently."""
    r = q.resolve_object(text, k=3)                  # no branch, exactly as caretaker calls it
    known_to_pass = {"the trash", "tidy up", "the banana", "the television remote",
                     "a bottle of water", "pick up the trash"}
    if r["confident"]:
        assert text in known_to_pass, (
            f"{text!r} newly passes the search floor unscoped at {r['top_score']:.3f} -- the "
            f"absent set is drifting upward; re-measure both floors")


def test_acting_needs_a_higher_floor_than_searching(q):
    """The honest statement of the trade-off, as an assertion: unscoped there is NO threshold that
    both keeps every real object and rejects every absent one. If this ever fails, the overlap has
    closed and the two floors could collapse into one."""
    worst_present = min(q.resolve_object(t, k=3)["top_score"] for t, _ in PRESENT)
    best_wrong = max(q.resolve_object(t, k=3)["top_score"] for t in ABSENT + NEAR_MISS)
    assert best_wrong > worst_present, (
        f"present now bottoms at {worst_present:.3f}, above the best wrong answer at "
        f"{best_wrong:.3f}: the ranges have separated, so ONE floor could replace two -- revisit "
        f"MIN_RELEVANCE and caretaker.RESOLVE_MIN_SCORE together instead of keeping both")


def test_matches_are_still_returned_when_not_confident(q):
    """`confident` is advice, not censorship: a search box still shows the near misses. This is
    also why the flag alone is not safety -- a caller that ignores it is as wrong as before."""
    r = q.resolve_object("pick up the trash", k=3, branch=BRANCH)
    assert not r["confident"] and r["matches"], "the near misses are still useful to a human"
    assert r["top_score"] == r["matches"][0]["score"]
