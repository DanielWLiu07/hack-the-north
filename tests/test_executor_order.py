"""The executor's ordering: never place onto something still there; break cycles by staging."""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roomctl.executor import BIN, Box, MockRobot, Spot, execute, plan, render_plan  # noqa: E402
from roomctl.state import Extents, ObjectRecord, Pose  # noqa: E402

DESK = {"min": [0.08, -0.50, 0.68], "max": [1.00, 0.50, 1.30], "surface": 0.70}
ROOM = {"zones": {"desk": DESK}, "bin": {"pose": [0.30, -0.75, 0.45]}}


def rec(oid, x, y, yaw=0, ext=(0.10, 0.08, 0.10)):
    return ObjectRecord(oid, oid.split("_")[0], "desk", Pose(x, y, round(0.70 + ext[2] / 2, 2), yaw),
                        Extents(*ext), "#808080", "2026-09-18T12:00:00Z")


def replay(p, current):
    """Walk the plan against a live occupancy map; fail on any placement onto an occupied spot."""
    where = {oid: Spot(r.zone, r.pose, r.extents) for oid, r in current.items()}
    for i, op in enumerate(p.ops, 1):
        assert where[op.object_id] == op.src, f"op {i} picks {op.object_id} from where it isn't"
        if op.dst.zone != BIN:
            for other, s in where.items():
                assert other == op.object_id or not s.box.overlaps(op.dst.box), \
                    f"op {i} places {op.object_id} onto {other}\n{render_plan(p)}"
            where[op.object_id] = op.dst
        else:
            del where[op.object_id]
    return where


def test_cyclic_swap_produces_a_staging_move():
    cur = {"cup_000a": rec("cup_000a", 0.30, 0.0), "mug_000b": rec("mug_000b", 0.60, 0.0)}
    tgt = {"cup_000a": rec("cup_000a", 0.60, 0.0), "mug_000b": rec("mug_000b", 0.30, 0.0)}
    p = plan(cur, tgt, ROOM)
    assert [op.kind for op in p.ops] == ["stage", "move", "unstage"], render_plan(p)
    end = replay(p, cur)
    assert {o: s.pose for o, s in end.items()} == {o: r.pose for o, r in tgt.items()}


def test_three_cycle_needs_exactly_one_stage():
    xs = [0.25, 0.50, 0.75]
    ids = ["a_000a", "b_000b", "c_000c"]
    cur = {o: rec(o, x, 0.0) for o, x in zip(ids, xs)}
    tgt = {o: rec(o, x, 0.0) for o, x in zip(ids, xs[1:] + xs[:1])}
    p = plan(cur, tgt, ROOM)
    assert [op.kind for op in p.ops].count("stage") == 1 and not p.unapplied
    replay(p, cur)


def test_a_chain_is_ordered_without_staging():
    """a -> b's spot, b -> c's spot, c -> somewhere free: c, then b, then a."""
    cur = {"a_000a": rec("a_000a", 0.25, 0.0), "b_000b": rec("b_000b", 0.50, 0.0), "c_000c": rec("c_000c", 0.75, 0.0)}
    tgt = {"a_000a": rec("a_000a", 0.50, 0.0), "b_000b": rec("b_000b", 0.75, 0.0), "c_000c": rec("c_000c", 0.75, 0.35)}
    p = plan(cur, tgt, ROOM)
    assert [(op.kind, op.object_id) for op in p.ops] == [("move", "c_000c"), ("move", "b_000b"), ("move", "a_000a")]


@pytest.mark.parametrize("seed", range(300))
def test_never_places_into_an_occupied_spot(seed):
    rng = random.Random(seed)
    grid = [(round(0.18 + 0.14 * i, 2), round(-0.40 + 0.14 * j, 2)) for i in range(6) for j in range(6)]
    n = rng.randint(2, 10)
    ids = [f"obj_{k:04x}" for k in range(n)]
    here, there = rng.sample(grid, n), rng.sample(grid, n)
    yaw = {o: rng.choice([0, 45, 90, 135]) for o in ids}
    cur = {o: rec(o, *xy, yaw[o]) for o, xy in zip(ids, here)}
    tgt = {o: rec(o, *xy, yaw[o]) for o, xy in zip(ids, there) if rng.random() > 0.15}
    p = plan(cur, tgt, ROOM)
    end = replay(p, cur)
    assert not p.unapplied, render_plan(p)  # a free grid always has room to stage
    assert {o: s.pose for o, s in end.items()} == {o: r.pose for o, r in tgt.items()}


def test_adding_an_absent_object_is_an_unapplied_hunk():
    p = plan({}, {"scissors_9f3a": rec("scissors_9f3a", 0.5, 0.0)}, ROOM)
    assert p.unapplied == [("scissors_9f3a", "cannot apply hunk: object 'scissors_9f3a' not present in room")]
    assert "hint: 1 hunk could not be applied. Human intervention required." in render_plan(p)


def test_removing_needs_a_bin():
    cur = {"marker_c3d4": rec("marker_c3d4", 0.5, 0.0)}
    assert [op.kind for op in plan(cur, {}, ROOM).ops] == ["remove"]
    p = plan(cur, {}, {"zones": ROOM["zones"]})
    assert not p.ops and "no bin in room.yaml" in p.unapplied[0][1]


def test_a_target_under_a_static_object_is_refused():
    cur = {"mug_000a": rec("mug_000a", 0.30, 0.0), "book_000b": rec("book_000b", 0.60, 0.0)}
    tgt = {"mug_000a": rec("mug_000a", 0.60, 0.0)}  # book isn't in the target but there's no bin
    p = plan(cur, tgt, {"zones": ROOM["zones"]})
    assert not p.ops
    assert any("occupied by 'book_000b', which isn't moving" in msg for _, msg in p.unapplied)


def test_a_slip_never_leads_to_placing_onto_the_stuck_object():
    cur = {"cup_000a": rec("cup_000a", 0.30, 0.0), "mug_000b": rec("mug_000b", 0.60, 0.0)}
    tgt = {"cup_000a": rec("cup_000a", 0.60, 0.0), "mug_000b": rec("mug_000b", 0.30, 0.0)}
    p = plan(cur, tgt, ROOM)
    staged = p.ops[0].object_id
    other = next(o for o in cur if o != staged)
    robot = MockRobot(fail={staged}, out=lambda s: None)
    out = execute(p, robot)
    assert [o.object_id for o, _ in out.failed] == [staged]
    assert ("place", other) not in robot.calls  # its target is still occupied
    assert len(out.skipped) == 2 and not out.done


def test_boxes_respect_orientation():
    long = Extents(0.30, 0.04, 0.05)
    a = Box.of(Pose(0.5, 0.0, 0.72, 0), long)
    assert a.overlaps(Box.of(Pose(0.5, 0.10, 0.72, 90), long))     # crossing like a plus sign
    assert not a.overlaps(Box.of(Pose(0.5, 0.10, 0.72, 0), long))  # parallel, 10 cm apart
    assert not a.overlaps(Box.of(Pose(0.5, 0.0, 1.00, 0), long))   # a different surface


def test_the_plan_contract_the_edge_consumes():
    """docs/30: our side stops at an ordered op list; the edge executes, verifies, retries."""
    import json
    cur = {"cup_000a": rec("cup_000a", 0.30, 0.0), "mug_000b": rec("mug_000b", 0.60, 0.0)}
    tgt = {"cup_000a": rec("cup_000a", 0.60, 0.0), "mug_000b": rec("mug_000b", 0.30, 0.0)}
    d = json.loads(json.dumps(plan(cur, tgt, ROOM).to_dict("swap", "abc123")))
    assert d["contract"] == "gitspace.plan/1" and d["frame"] == "world_z_up"
    assert "Z UP" in d["frame_def"] and "Y-DOWN" in d["frame_def"]
    assert [(o["seq"], o["kind"]) for o in d["ops"]] == [(1, "stage"), (2, "move"), (3, "unstage")]
    assert set(d["ops"][0]["from"]["pose"]) == {"x", "y", "z", "yaw"} and d["ops"][0]["base"] == {"pick": None, "place": None}
