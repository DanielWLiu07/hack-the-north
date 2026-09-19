"""Identity is ASSIGNED, then DEFENDED (docs/25 §3). These tests try to break it.

Every scan here goes through the real write path: associate -> for_serialize -> serialize
(stabilize + roomctl.state.settle) -> the working tree, against a FIXED committed HEAD, as
pipeline.scan_into does. An id that survives here is an id `git log --follow` can follow.
"""
import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import associate  # noqa: E402
from associate import ADDED, MISSED, MOVED, REMOVED, RETURNED, UNCHANGED, UNOBSERVED  # noqa: E402
from cluster import Instance  # noqa: E402
from describe import ViewDescription  # noqa: E402
from merge import MergedObject  # noqa: E402
from roomctl.state import new_id, read_tree, slug  # noqa: E402
from serialize import serialize  # noqa: E402

ZONES = {"desk": {"min": [-0.2, -0.6, 0.6], "max": [1.2, 0.6, 1.2]},
         "shelf": {"min": [-0.2, 0.6, 0.6], "max": [1.2, 1.4, 1.2]}}


def thing(at, label="cup", size=(0.09, 0.09, 0.10), color="#2b4c7e", cams=("cam0", "cam1"), seed=0,
          face=None, words=None):
    """One physical object as merge.py hands it over. `face` shifts each view's points toward the
    side that camera sees, as a single-view centroid is biased in reality."""
    rng = np.random.default_rng(seed)
    views = []
    for k, cam in enumerate(cams):
        pts = rng.uniform(-0.5, 0.5, (300, 3)) * size + at
        if face is not None:
            pts = pts + np.asarray(face[k])
        views.append(Instance(points=pts, label=label, source="segment", camera=cam, score=0.9, color=color,
                              description=ViewDescription(cam, None, (words or ["a blue mug"])[k % len(words or [1])], "t", 1)))
    return MergedObject(views)


class Room:
    """A room repo with ONE committed HEAD; scans write the working tree against it."""

    def __init__(self, path):
        self.path, self.head, self.misses, self.n = path, {}, {}, 0

    def scan(self, objects, occluded=lambda r: False, history=None, fresh=None):
        self.n += 1
        assocs = associate.associate(objects, self.head, f"cap_{self.n:04d}", history=history, occluded=occluded,
                                     now=f"2026-09-19T16:{self.n:02d}:00Z", zones=ZONES, misses=self.misses,
                                     **({"fresh": fresh} if fresh is not None else {}))
        measured, carried = associate.for_serialize(assocs, zones=ZONES)
        serialize(self.path, measured, self.head, carried)
        self.misses = associate.next_misses(assocs, self.misses)
        return assocs

    def commit(self):
        self.head, self.misses = read_tree(self.path), {}

    def files(self):
        return {p.relative_to(self.path).as_posix(): p.read_bytes() for p in (self.path / "zones").rglob("*.yaml")}


def by_obj(assocs):
    return {id(a.obj): a for a in assocs if a.obj is not None}


@pytest.fixture
def room(tmp_path):
    return Room(tmp_path)


# ── the id itself ────────────────────────────────────────────────────────────

def test_the_id_contains_nothing_that_can_change():
    """class slug + a hash of (class, first capture, ordinal): no position, extents or colour."""
    oid = new_id("cup", "cap_0007", 2)
    assert oid == f"cup_{hashlib.sha1(b'cup|cap_0007|2').hexdigest()[:4]}"
    assert new_id("cup", "cap_0007", 2) == oid                      # deterministic, pure


def test_the_same_first_sighting_mints_the_same_id_wherever_the_object_stood(tmp_path):
    ids = []
    for k, at in enumerate(((0.3, 0.0, 0.8), (0.9, -0.4, 0.8))):
        r = Room(tmp_path / str(k))
        (a,) = r.scan([thing(at, color="#aa0000" if k else "#00aa00")])
        ids.append(a.object_id)
    assert ids[0] == ids[1]                                          # geometry and colour never enter it


# ── defended: move, occlude, re-observe ─────────────────────────────────────

@pytest.mark.parametrize("dx, want", [(0.01, UNCHANGED), (0.20, MOVED), (0.80, MOVED)])
def test_a_moved_object_keeps_its_id(room, dx, want):
    (a0,) = room.scan([thing((0.2, 0.0, 0.8))])
    room.commit()
    (a1,) = room.scan([thing((0.2 + dx, 0.0, 0.8), seed=3)])
    assert (a1.object_id, a1.verdict) == (a0.object_id, want)
    assert list(room.files()) == [f"zones/desk/{a0.object_id}.yaml"]   # modified in place, never delete+add


def test_moving_to_another_zone_keeps_the_id_the_file_just_moves(room):
    (a0,) = room.scan([thing((0.4, 0.3, 0.8))])
    room.commit()
    (a1,) = room.scan([thing((0.4, 0.9, 0.8), seed=3)])
    assert (a1.object_id, a1.verdict) == (a0.object_id, MOVED)
    assert list(room.files()) == [f"zones/shelf/{a0.object_id}.yaml"]  # same id, same name: git sees a rename


def test_an_occluded_object_keeps_its_id_and_its_bytes(room):
    mug, book = thing((0.4, 0.2, 0.8)), thing((0.0, -0.3, 0.77), "book", (0.20, 0.15, 0.04), "#a01818", seed=2)
    ids = {k: v.object_id for k, v in by_obj(room.scan([mug, book])).items()}   # assocs come back sorted by id
    room.commit()
    before = room.files()
    for _ in range(4):                                               # hidden for four scans
        out = {a.object_id: a.verdict for a in room.scan([book], occluded=lambda r: r.id == ids[id(mug)])}
        assert out[ids[id(mug)]] == UNOBSERVED and room.files() == before
    back = by_obj(room.scan([thing((0.4, 0.2, 0.8), seed=9), book]))
    assert ids[id(mug)] in {a.object_id for a in back.values()}


def test_seen_by_a_different_camera_from_its_other_side_keeps_its_id(room):
    """cam0 sees the front face, cam1 later sees only the back: each view's centroid is pulled
    ~3 cm toward its own side, and the extents come out different."""
    (a0,) = room.scan([thing((0.4, 0.2, 0.8), cams=("cam0",), face=[(-0.015, 0, 0)])])
    room.commit()
    before = room.files()
    (a1,) = room.scan([thing((0.4, 0.2, 0.8), cams=("cam1",), face=[(0.015, 0, 0)], seed=5, size=(0.07, 0.1, 0.1))])
    assert (a1.object_id, a1.verdict) == (a0.object_id, UNCHANGED)
    assert room.files() == before                                    # settle(): 3 cm is not a move


def test_the_segmenter_relabelling_it_does_not_rename_it(room):
    """Association, not classification: YOLO calling the mug a bowl next scan changes nothing."""
    (a0,) = room.scan([thing((0.4, 0.2, 0.8), label="cup")])
    room.commit()
    before = room.files()
    (a1,) = room.scan([thing((0.4, 0.2, 0.8), label="bowl", seed=4)])
    assert (a1.object_id, a1.cls, a1.verdict) == (a0.object_id, "cup", UNCHANGED)
    assert room.files() == before


def test_rescanning_the_same_scene_twice_changes_nothing(room):
    scene = lambda s: [thing((0.4, 0.2, 0.8), seed=s), thing((0.0, -0.3, 0.77), "book", (0.2, 0.15, 0.04), "#a01818", seed=s + 50)]
    first = {a.object_id for a in room.scan(scene(0))}
    room.commit()
    before = room.files()
    for s in (1, 2):
        out = room.scan(scene(s))
        assert {a.object_id for a in out} == first and {a.verdict for a in out} == {UNCHANGED}
        assert room.files() == before


# ── two identical objects ────────────────────────────────────────────────────

def twins(a=(0.30, 0.0, 0.8), b=(0.50, 0.0, 0.8), s=0):
    return thing(a, seed=s), thing(b, seed=s + 1)


def test_two_identical_objects_get_two_ids_and_keep_them(room):
    a, b = twins()
    ids = {k: v.object_id for k, v in by_obj(room.scan([a, b])).items()}
    assert len(set(ids.values())) == 2
    room.commit()
    a2, b2 = twins(s=10)
    out = by_obj(room.scan([a2, b2]))
    assert (out[id(a2)].object_id, out[id(b2)].object_id) == (ids[id(a)], ids[id(b)])


def test_moving_one_twin_moves_only_that_one(room):
    a, b = twins()
    ids = {k: v.object_id for k, v in by_obj(room.scan([a, b])).items()}
    room.commit()
    a2, b2 = twins(b=(0.50, 0.12, 0.8), s=20)                        # b slides 12 cm sideways
    out = by_obj(room.scan([a2, b2]))
    assert (out[id(a2)].object_id, out[id(a2)].verdict) == (ids[id(a)], UNCHANGED)
    assert (out[id(b2)].object_id, out[id(b2)].verdict) == (ids[id(b)], MOVED)


def test_removing_one_twin_never_swaps_the_survivors_id(room):
    a, b = twins()
    ids = {k: v.object_id for k, v in by_obj(room.scan([a, b])).items()}
    room.commit()
    for n in range(3):
        (only_a,) = [thing((0.30, 0.0, 0.8), seed=30 + n)]
        out = {x.object_id: x for x in room.scan([only_a])}
        assert out[ids[id(a)]].verdict == UNCHANGED                   # a keeps a's id, every time
        assert out[ids[id(b)]].verdict == (MISSED if n == 0 else REMOVED)


@pytest.mark.xfail(strict=True, reason="docs/25 §2: 'semantics fails when there are two mugs'. Two "
                   "indistinguishable objects swapping places look exactly like neither moving; "
                   "nothing in position, appearance or class can tell them apart")
def test_two_identical_objects_swapping_places_keep_their_ids(room):
    a, b = twins()
    ids = {k: v.object_id for k, v in by_obj(room.scan([a, b])).items()}
    room.commit()
    a2, b2 = thing((0.50, 0.0, 0.8), seed=40), thing((0.30, 0.0, 0.8), seed=41)   # a is where b was
    out = by_obj(room.scan([a2, b2]))
    assert (out[id(a2)].object_id, out[id(b2)].object_id) == (ids[id(a)], ids[id(b)])


# ── assigned: a genuinely new object gets a new id ───────────────────────────

def test_a_third_identical_object_gets_its_own_new_id(room):
    a, b = twins()
    known = {v.object_id for v in room.scan([a, b])}
    room.commit()
    c = thing((0.80, 0.3, 0.8), seed=60)
    out = by_obj(room.scan([*twins(s=61), c]))
    assert out[id(c)].verdict == ADDED and out[id(c)].object_id not in known


def test_a_new_object_where_a_removed_one_stood_is_new_without_history(room):
    """No history search (pipeline.scan_into today): a different object in the old spot is new.
    Different size and colour: HEAD matching's size gate refuses it even at 0 cm."""
    (a0,) = room.scan([thing((0.4, 0.2, 0.8))])
    room.commit()
    stapler = thing((0.4, 0.2, 0.8), "stapler", (0.15, 0.04, 0.06), "#111111", seed=7)
    out = {x.object_id: x for x in room.scan([stapler])}
    new = [x for x in out.values() if x.obj is stapler]
    assert new[0].verdict == ADDED and new[0].object_id != a0.object_id
    assert out[a0.object_id].verdict in (MISSED, REMOVED)


def test_a_returning_object_reuses_its_id_only_through_history(room):
    """Left, committed as gone, came back: HEAD has nothing to match, so only the history search
    can give the old id back; without it the object is (correctly, honestly) new."""
    class History:
        def __init__(self, rec):
            self.rec = rec

        def reidentify(self, obj, exclude):
            return [associate.Candidate(self.rec.id, 1.0, self.rec)] if self.rec.id not in exclude else []

    (a0,) = room.scan([thing((0.4, 0.2, 0.8))])
    room.commit()
    old = room.head[a0.object_id]
    for _ in range(2):
        room.scan([])
    room.commit()                                                   # the removal is committed
    back = thing((0.9, -0.3, 0.8), seed=8)
    (with_h,) = [x for x in room.scan([back], history=History(old)) if x.obj is back]
    assert (with_h.object_id, with_h.verdict) == (a0.object_id, RETURNED)


# ── what is deliberately NOT an object (docs/25 §6, .roomignore) ─────────────

def test_roomignore_labels_never_become_objects_and_paths_never_get_files(tmp_path):
    import segment
    from test_segment import _masks, _render

    (tmp_path / ".roomignore").write_text("person\nrobot\ncable  # the arm's own tether\nzones/floor/**\n")
    labels, paths = segment.roomignore(tmp_path)
    assert labels == {"person", "robot", "cable"} and paths == ("zones/floor/**",)

    xyz, valid, img, lab = _render()
    arm = segment.Mask(lab == 1, "Robot", 0.9)                        # case-insensitive
    kept = segment.lift(xyz, valid, [arm, segment.Mask(lab == 2, "cup", 0.9)], "cam0", ignore=labels)
    assert [i.label for i in kept] == ["cup"]

    zones = {**ZONES, "floor": {"min": [-2, -2, -0.1], "max": [2, 2, 0.3]}}
    committed = associate.associate([thing((0.5, 0.5, 0.1), seed=1)], {}, "cap_0001", zones=zones)
    head = {committed[0].object_id: None}
    new_on_floor = associate.associate([thing((0.5, 0.5, 0.1), seed=2)], {}, "cap_0002", zones=zones)
    measured, _ = associate.for_serialize(new_on_floor, zones=zones, ignore_paths=paths)
    assert measured == []                                             # untracked floor clutter: no file
    tracked = associate.Association(MOVED, "cup_aaaa", "cup", "#2b4c7e", "2026-09-19T16:00:00Z", "floor",
                                    thing((0.5, 0.5, 0.1)), centre=np.array([0.5, 0.5, 0.1]),
                                    extents=np.array([.1, .1, .1]), yaw=0.0)
    measured, _ = associate.for_serialize([tracked], zones=zones, ignore_paths=paths)
    assert [m.id for m in measured] == ["cup_aaaa"]                   # already tracked: stays tracked, like git


def test_the_real_room_git_roomignore_parses():
    import segment

    labels, paths = segment.roomignore(Path(__file__).resolve().parents[2] / "room.git")
    assert {"person", "robot", "cable"} <= labels and "zones/floor/**" in paths



# ── the caretaker's miss rule: a miss counts only if the block is FRESH and VISIBLE ─────
# plan/roommate/03-interfaces.md §4 · 04-test-plan.md ring 2 scenario 3: hidden is not gone.

def _mug_committed(room):
    (a,) = room.scan([thing((0.4, 0.2, 0.8))])
    room.commit()
    return a.object_id, room.files()


def test_a_stale_block_never_counts_a_miss(room):
    """The robot's map hasn't re-observed that 0.5 m block: nothing was looked at, nothing is gone."""
    mug, before = _mug_committed(room)
    for _ in range(6):
        (a,) = [x for x in room.scan([], fresh=lambda r: False) if x.object_id == mug]
        assert a.verdict == UNOBSERVED and "stale" in a.note
        assert room.files() == before and room.misses == {}


def test_fresh_but_hidden_is_carried_not_counted(room):
    mug, before = _mug_committed(room)
    for _ in range(4):
        (a,) = room.scan([], fresh=lambda r: True, occluded=lambda r: True)
        assert a.verdict == UNOBSERVED and "line of sight" in a.note
        assert room.files() == before and room.misses == {}


def test_fresh_and_visible_misses_count_toward_removal(room):
    mug, _ = _mug_committed(room)
    (a,) = room.scan([], fresh=lambda r: True)
    assert a.verdict == MISSED and room.misses == {mug: 1}
    (a,) = room.scan([], fresh=lambda r: True)
    assert a.verdict == REMOVED


def test_only_fresh_visible_scans_count(room):
    """One real miss, then three passes that never looked at the block, then a real miss: removed
    on the SECOND real miss, not the second scan."""
    mug, before = _mug_committed(room)
    (a,) = room.scan([], fresh=lambda r: True)
    assert a.verdict == MISSED
    for _ in range(3):
        (a,) = room.scan([], fresh=lambda r: False)
        assert a.verdict == UNOBSERVED and room.misses == {mug: 1} and room.files() == before
    (a,) = room.scan([], fresh=lambda r: True)
    assert a.verdict == REMOVED


def test_scenario_3_a_box_between_the_robot_and_the_tape_measure_never_deletes_it(room):
    """04-test-plan ring 2 scenario 3, offline: the real raycast against a real voxel grid, with a
    box standing between the robot's viewpoint and the tape measure's last pose. The block IS
    fresh (the robot is looking right at it), the line of sight is not. Ten passes: no delete."""
    import raycast
    import voxelize

    (a,) = room.scan([thing((1.0, 0.0, 0.8), "tape measure", (0.07, 0.07, 0.04), "#d4a017")])
    room.commit()
    tape, before = a.object_id, room.files()
    box = np.array([[0.6, y, z] for y in np.arange(-0.25, 0.26, 0.02) for z in np.arange(0.55, 1.1, 0.02)])
    grid = voxelize.VoxelGrid.from_points(np.repeat(box, 4, axis=0))
    eye = raycast.Camera((0.0, 0.0, 0.85), (1.0, 0.0, -0.05), 40)
    for _ in range(10):
        (t,) = [x for x in room.scan([], fresh=lambda r: True, occluded=raycast.occlusion_check([eye], grid))
                if x.object_id == tape]
        assert t.verdict == UNOBSERVED, t.verdict                  # hidden != gone
    assert room.files() == before and room.misses == {}
    (t,) = [x for x in room.scan([], fresh=lambda r: True,
                                 occluded=raycast.occlusion_check([eye], voxelize.VoxelGrid.from_points(np.zeros((0, 3)))))
            if x.object_id == tape]
    assert t.verdict == MISSED                                     # box gone, still not there: now it counts
