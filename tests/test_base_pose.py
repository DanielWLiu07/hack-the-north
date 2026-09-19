"""The executor asks perception where to stand (docs/24 A2), and turns "nowhere" into a hunk.

perception/tests/test_costmap.py tests the solver itself; this tests the wiring: our Arm
fits its protocol, the fake room goes through the real voxelize -> costmap path, and route()
reports what can't be reached instead of retrying.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roomctl.cli import fake_costmap  # noqa: E402
from roomctl.executor import ARM, BasePose, Op, Plan, Spot, base_pose_for, plan, route  # noqa: E402
from roomctl.state import Extents, ObjectRecord, Pose  # noqa: E402

HOME = BasePose(-1.0, 0.0, 0.0)
SMALL = {"t": {"min": [0.0, -0.3, 0.68], "max": [0.6, 0.3, 1.2], "surface": 0.70, "overhang": 0.10}}
BIG = {"t": {"min": [0.0, -0.6, 0.68], "max": [1.2, 0.6, 1.2], "surface": 0.70, "overhang": 0.10}}
CUP = Extents(0.08, 0.08, 0.10)


def rec(oid, x, y, zone="t"):
    return ObjectRecord(oid, oid.split("_")[0], zone, Pose(x, y, 0.75, 0), CUP, "#808080", "2026-09-18T12:00:00Z")


def test_arm_fits_perceptions_protocol():
    """perception.costmap.Arm: r_min/r_max around the base centre, reachable(target, (x, y, yaw_rad))."""
    base = (0.0, 0.0, 0.0)
    assert ARM.reachable((ARM.r_min + 0.05, 0.0, 0.75), base)
    assert not ARM.reachable((ARM.r_max + 0.05, 0.0, 0.75), base)           # too far
    assert not ARM.reachable((0.3, 0.0, 1.5), base)                          # too high
    assert not ARM.reachable((-0.3, 0.0, 0.75), base)                        # behind it
    assert ARM.reachable((0.0, 0.3, 0.75), (0.0, 0.0, math.pi / 2))          # yaw is radians


def test_a_small_table_is_reachable_from_the_near_side():
    cm = fake_costmap({"zones": SMALL}, [rec("cup_000a", 0.15, 0.0)])
    bp, _ = base_pose_for((0.15, 0.0, 0.75), CUP, cm, HOME)
    assert bp is not None and bp.x < 0, bp
    assert not cm.occupied(bp.x, bp.y)
    assert ARM.reachable((0.15, 0.0, 0.75), (bp.x, bp.y, math.radians(bp.yaw)))


def test_the_middle_of_a_big_table_is_nowhere_to_stand():
    cm = fake_costmap({"zones": BIG}, [rec("cup_000a", 0.6, 0.0)])
    pose, why = base_pose_for((0.6, 0.0, 0.75), CUP, cm, HOME)
    assert pose is None and why.startswith("180 base poses sampled:") and "base fits" in why


def test_the_pedestal_blocks_the_base_and_the_overhang_does_not():
    cm = fake_costmap({"zones": SMALL}, [])
    assert cm.occupied(0.30, 0.0)            # over the pedestal, inflated
    assert not cm.occupied(-0.25, 0.0)       # beside the top: the overhang isn't a base obstacle


def test_route_turns_nowhere_to_stand_into_an_unapplied_hunk():
    room = {"zones": BIG}
    cur = {"cup_000a": rec("cup_000a", 0.60, 0.0), "mug_000b": rec("mug_000b", 0.10, 0.5)}
    tgt = {"cup_000a": rec("cup_000a", 0.60, 0.3), "mug_000b": rec("mug_000b", 0.10, -0.5)}
    p = route(plan(cur, tgt, room), fake_costmap(room, cur.values()), HOME)
    assert [op.object_id for op in p.ops] == ["mug_000b"]
    assert p.ops[0].pick_base and p.ops[0].place_base
    assert any("nowhere to stand to pick up 'cup_000a'" in m for _, m in p.unapplied)


def test_route_drops_ops_that_needed_an_unreachable_objects_spot():
    room = {"zones": BIG}
    stuck = Spot("t", Pose(0.6, 0.0, 0.75, 0), CUP)
    edge = Spot("t", Pose(0.1, 0.5, 0.75, 0), CUP)
    p = Plan(ops=[Op("move", "cup_000a", stuck, edge), Op("move", "mug_000b", edge, stuck)])
    out = route(p, fake_costmap(room, []), HOME)
    assert not out.ops and len(out.unapplied) == 2
    assert "nowhere to stand to pick up 'cup_000a'" in out.unapplied[0][1]
    assert "'cup_000a' can't be moved out of its way" in out.unapplied[1][1]
