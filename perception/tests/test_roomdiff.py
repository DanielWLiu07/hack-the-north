"""roomdiff.py: git's questions asked about objects — what changed, and who wins when two
rooms disagree. The rules under test are the ones a person would argue with: how far a thing
must move before it counts, and what happens when both sides moved the same thing."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import roomdiff  # noqa: E402
from roomctl.state import MOVE_M, Extents, ObjectRecord, Pose  # noqa: E402
from roomdiff import ADDED, MOVED, REMOVED, SAME  # noqa: E402


def rec(oid="mug_a1b2", cls="mug", zone="desk", x=0.60, y=0.20, z=0.75, yaw=0):
    return ObjectRecord(oid, cls, zone, Pose(x, y, z, yaw), Extents(0.09, 0.09, 0.10),
                        "#2b4c7e", "2026-09-19T14:00:00Z")


def kinds(changes):
    return {c.object_id: c.kind for c in changes}


def test_a_small_move_is_not_a_change():
    """The room's own threshold, so a diff never claims a move the COMMIT would not record:
    roomctl.state.MOVE_M is what settle() uses."""
    before = {"mug_a1b2": rec()}
    after = {"mug_a1b2": rec(x=0.60 + MOVE_M - 0.005)}
    [c] = roomdiff.diff(before, after)
    assert c.kind == SAME and not c.counts
    assert "under the" in c.note                       # and it says what it ignored

    further = {"mug_a1b2": rec(x=0.60 + MOVE_M + 0.005)}
    [c] = roomdiff.diff(before, further)
    assert c.kind == MOVED and c.counts and c.distance == pytest.approx(MOVE_M + 0.005, abs=1e-6)


def test_a_change_of_zone_counts_however_small():
    """Crossing a zone edge is a rename in git's eyes (the file moves), so it is never swallowed
    by the threshold."""
    before = {"mug_a1b2": rec(zone="desk", x=0.99)}
    after = {"mug_a1b2": rec(zone="shelf", x=1.00)}
    [c] = roomdiff.diff(before, after)
    assert c.kind == MOVED and "desk -> shelf" in c.note


def test_added_and_removed_read_like_a_diff():
    before = {"mug_a1b2": rec(), "keys_7c2e": rec("keys_7c2e", "keys", "shelf", 0.6, 0.8, 0.9)}
    after = {"mug_a1b2": rec(), "cup_1f1f": rec("cup_1f1f", "cup", "desk", 0.3, -0.2, 0.74)}
    got = kinds(roomdiff.diff(before, after))
    assert got == {"mug_a1b2": SAME, "keys_7c2e": REMOVED, "cup_1f1f": ADDED}
    text = roomdiff.explain(roomdiff.diff(before, after))
    assert text.splitlines()[0].startswith("2 changes, 1 under")
    assert "- keys_7c2e" in text and "+ cup_1f1f" in text


def test_one_side_changed_it_that_side_wins():
    base = {"mug_a1b2": rec()}
    ours = {"mug_a1b2": rec(x=0.90)}                   # we moved it
    theirs = {"mug_a1b2": rec()}                       # they left it
    merged, conflicts = roomdiff.merge3(base, ours, theirs)
    assert not conflicts and merged["mug_a1b2"].pose.x == 0.90


def test_both_moved_it_to_the_same_place_is_not_a_conflict():
    base = {"mug_a1b2": rec()}
    ours = {"mug_a1b2": rec(x=0.90)}
    theirs = {"mug_a1b2": rec(x=0.90 + MOVE_M / 2)}    # agreeing within the threshold
    merged, conflicts = roomdiff.merge3(base, ours, theirs)
    assert not conflicts and merged["mug_a1b2"].pose.x == 0.90


def test_both_moved_it_somewhere_else_is_a_conflict_that_states_both_places():
    base = {"mug_a1b2": rec()}
    ours = {"mug_a1b2": rec(x=0.90)}
    theirs = {"mug_a1b2": rec(x=0.30)}
    merged, conflicts = roomdiff.merge3(base, ours, theirs)
    assert "mug_a1b2" not in merged                    # a conflict is NOT silently resolved
    [c] = conflicts
    assert c.distance == pytest.approx(0.60) and "both moved it" in c.why
    text = roomdiff.explain_conflicts(conflicts)
    assert "ours" in text and "theirs" in text and "60 cm apart" in text
    assert "rescan the room" in text                   # the answer is in the room, not the file


def test_removed_on_one_side_and_moved_on_the_other_is_a_conflict():
    base = {"mug_a1b2": rec()}
    ours: dict = {}                                    # we saw it gone
    theirs = {"mug_a1b2": rec(x=0.95)}                 # they saw it moved
    merged, conflicts = roomdiff.merge3(base, ours, theirs)
    assert "mug_a1b2" not in merged
    assert conflicts[0].why.startswith("ours removed it")


def test_both_removed_it_is_agreement_not_conflict():
    merged, conflicts = roomdiff.merge3({"mug_a1b2": rec()}, {}, {})
    assert merged == {} and conflicts == []


def test_alignment_turns_a_room_that_seems_to_have_moved_into_one_that_did_not():
    """The robot turned between the two scans, so every object's centre changed. Restating one
    room in the other's frame is what makes the diff about the OBJECTS. Measured on the real
    hallway pair: 7.4 deg of turn read as 16 and 19 cm of movement; aligned, 1.8 and 3.3 cm."""
    import math

    before = {"mug_a1b2": rec(x=0.60, y=0.20), "cup_1f1f": rec("cup_1f1f", "cup", "desk", 1.00, -0.30)}
    turn = 7.4
    c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))
    after = {oid: type(r)(r.id, r.cls, r.zone,
                          Pose(round(r.pose.x * c - r.pose.y * s, 3), round(r.pose.x * s + r.pose.y * c, 3),
                               r.pose.z, r.pose.yaw), r.extents, r.color, r.first_seen)
             for oid, r in before.items()}

    raw = roomdiff.diff(before, after)
    assert all(ch.kind == MOVED for ch in raw)                     # the whole room "moved"

    aligned = roomdiff.apply_se2(before, (0.0, 0.0, turn))
    assert all(ch.kind == SAME for ch in roomdiff.diff(aligned, after, MOVE_M + roomdiff.ALIGN_SLACK))


def test_a_motion_and_its_inverse_cancel():
    """Registration is not symmetric — A into B can hold with hundreds of matches while B into A
    finds none — so a chain has to be free to walk a hop backwards."""
    import math

    hop = (0.35, -0.12, 27.0)
    there_and_back = roomdiff.compose(hop, roomdiff.invert(hop))
    assert there_and_back[0] == pytest.approx(0.0, abs=1e-9)
    assert there_and_back[1] == pytest.approx(0.0, abs=1e-9)
    assert math.remainder(there_and_back[2], 360) == pytest.approx(0.0, abs=1e-9)


def test_composing_two_hops_is_the_same_as_taking_them_in_turn():
    a, b = (0.20, 0.05, 15.0), (-0.10, 0.30, -40.0)
    rec_a = {"mug_a1b2": rec(x=0.60, y=0.20)}
    step = roomdiff.apply_se2(roomdiff.apply_se2(rec_a, a), b)["mug_a1b2"].pose
    once = roomdiff.apply_se2(rec_a, roomdiff.compose(a, b))["mug_a1b2"].pose
    assert (step.x, step.y) == pytest.approx((once.x, once.y), abs=2e-3)


def test_a_registration_is_only_believed_when_the_clouds_lie_on_each_other():
    """The check a fit cannot do for itself. On the real set a 38-match fit claimed 114 deg and
    left the median point 57 cm from its neighbour, while every good fit sat at 0.6-0.7 cm; no
    inlier count separates those, so the clouds are asked directly."""
    rng = np.random.default_rng(0)
    wall = np.c_[rng.uniform(-2, 2, 4000), np.full(4000, 1.5), rng.uniform(0.1, 1.9, 4000)]
    view = (wall[None].repeat(1, 0).reshape(1, -1, 3), np.ones((1, len(wall)), bool), None)

    near, overlap = roomdiff.agreement(view, view, (0.0, 0.0, 0.0))
    assert near < 0.01 and overlap > 0.99                      # itself, unmoved

    near, overlap = roomdiff.agreement(view, view, (0.0, 0.0, 90.0))
    assert near > roomdiff.ACCEPT_NN_M or overlap < roomdiff.ACCEPT_OVERLAP   # a turn the room never made
