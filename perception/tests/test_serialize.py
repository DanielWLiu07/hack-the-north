"""serialize.py: UNITS (metres on the 1 cm grid, whole degrees on the 5 deg grid, yaw an axis
in [0, 180)), FRAME (F_world passes through untouched), and the gate docs/20 exists for:
rescan an unchanged room -> `git status` stays empty."""
import random
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import serialize  # noqa: E402
from serialize import Measured, stabilize, stabilize_record, stabilize_yaw  # noqa: E402
from roomctl.repo import Repo  # noqa: E402
from roomctl.state import HYST, Q_POS, Q_YAW, ObjectRecord, Pose, Extents, to_yaml, validate  # noqa: E402

T0 = "2026-09-18T21:00:00Z"


def _m(id_, centre, extents=(0.12, 0.09, 0.11), yaw=0.0, zone="desk", cls="mug", color="#2b4c7e"):
    return Measured(id_, cls, zone, tuple(centre), tuple(extents), yaw, color, T0)


# ── UNITS and FRAME ──────────────────────────────────────────────────────────

def test_records_are_on_the_grid_in_metres_and_degrees():
    """Whatever comes in -- float32, negative, yaw far outside [0, 180) -- what comes out is
    metres on the 1 cm grid and an int yaw in [0, 180) on the 5 deg grid (state.validate)."""
    rng = np.random.default_rng(0)
    for i in range(500):
        dt = rng.choice([np.float32, np.float64, float])
        m = _m(f"thing_{i:04x}", [dt(v) for v in rng.uniform(-3, 3, 3)],
               [dt(v) for v in rng.uniform(0.001, 0.5, 3)], yaw=dt(rng.uniform(-720, 720)))
        head = None if i % 2 else stabilize_record(_m(m.id, rng.uniform(-3, 3, 3), yaw=rng.uniform(0, 180)), None)
        rec = stabilize_record(m, head)
        validate(rec)
        assert type(rec.pose.yaw) is int and 0 <= rec.pose.yaw < 180


def test_frame_passes_through():
    """F_world in, F_world out: no axis swap, no sign flip, no scaling. Y right of the anchor
    is negative and stays negative; z is height above the floor."""
    rec = stabilize_record(_m("mug_a1b2", (1.23, -0.45, 0.76), (0.12, 0.09, 0.11), 15), None)
    assert (rec.pose.x, rec.pose.y, rec.pose.z, rec.pose.yaw) == pytest.approx((1.23, -0.45, 0.76, 15))
    assert (rec.extents.x, rec.extents.y, rec.extents.z) == pytest.approx((0.12, 0.09, 0.11))


def test_constants_are_roomctls():
    """One source of truth for the quanta, shared with fake/scene_gen.py's twin."""
    assert (serialize.Q_POS, serialize.Q_YAW, serialize.HYST, serialize.YAW_PERIOD) == (0.01, 5, 1.5, 180)
    assert serialize.Q_POS is Q_POS and serialize.HYST is HYST and serialize.Q_YAW is Q_YAW


# ── stabilize: quantize AND hysteresis ───────────────────────────────────────

def test_deadband_is_1_5_quanta():
    assert stabilize(0.4349, 0.42) == 0.42                   # 1.49 quanta: kept
    assert stabilize(0.4351, 0.42) == pytest.approx(0.44)    # 1.51 quanta: re-quantized
    assert stabilize(0.4051, 0.42) == 0.42
    assert stabilize(0.4049, 0.42) == pytest.approx(0.40)
    assert stabilize(0.4251, None) == pytest.approx(0.43)    # first sight: plain quantize


def test_quantizing_alone_flickers_hysteresis_does_not():
    """docs/20 Part 5's example: an object truly at 0.4250 m with +-0.5 mm of noise."""
    rng = random.Random(0)
    scans = [0.425 + rng.uniform(-0.0005, 0.0005) for _ in range(30)]
    quantized_only = {f"{stabilize(v, None):.2f}" for v in scans}
    assert quantized_only == {"0.42", "0.43"}               # dirty on every other scan
    committed = stabilize(scans[0], None)
    assert {f"{stabilize(v, committed):.2f}" for v in scans[1:]} == {f"{committed:.2f}"}


@pytest.mark.parametrize("new,committed,want", [
    (-89, 90, 90),     # cluster's [-90, 90) vs a committed 90: the same axis, 1 deg apart
    (-89, 89, 89),     # plain subtraction says 178 deg; as axes it's 2
    (178, 0, 0),       # 2 deg across the wrap
    (2, 175, 175),     # 7 deg across the wrap: inside the 7.5 deg deadband
    (3, 175, 5),       # 8 deg: outside, re-quantized
    (97.6, 90, 100),   # 7.6 deg: outside
    (97.4, 90, 90),    # 7.4 deg: inside
    (178, None, 0),    # rounds to 180, which is 0 -- fold AFTER rounding
    (-2.6, None, 175),
    (-89, None, 90),
    (360 + 47, None, 45),
])
def test_yaw_is_an_axis(new, committed, want):
    assert stabilize_yaw(new, committed) == want


def test_first_sight_facts_come_from_head():
    """class, color and first_seen are never re-measured; zone and geometry are."""
    head = ObjectRecord("mug_a1b2", "mug", "desk", Pose(0.42, 0.18, 0.76, 15),
                        Extents(0.12, 0.09, 0.11), "#2b4c7e", "2026-09-18T14:12:33Z")
    m = Measured("mug_a1b2", "cup", "shelf", (0.421, 0.181, 0.761), (0.121, 0.091, 0.111), 16.0,
                 "#ffffff", T0)
    assert stabilize_record(m, head) == ObjectRecord(
        "mug_a1b2", "mug", "shelf", head.pose, head.extents, "#2b4c7e", "2026-09-18T14:12:33Z")


def test_extents_never_quantize_to_zero():
    rec = stabilize_record(_m("chip_0a1b", (0, 0, 0.75), (0.004, 0.003, 0.002)), None)
    assert min(rec.extents.x, rec.extents.y, rec.extents.z) == Q_POS
    to_yaml(rec)


# ── THE gate: an unchanged room rescans byte-identical ───────────────────────

TRUTH = [  # adversarial on purpose: every value sits on or near a bucket edge
    dict(id_="mug_a1b2", centre=(0.425, -0.315, 0.765), extents=(0.125, 0.085, 0.115), yaw=2.5),
    dict(id_="book_c3d4", centre=(0.605, 0.215, 0.755), extents=(0.235, 0.155, 0.035), yaw=87.5),
    dict(id_="can_e5f6", centre=(-0.105, 0.0, 0.8), extents=(0.066, 0.066, 0.122), yaw=0.0),
    dict(id_="tape_measure_0a1b", centre=(1.005, -1.005, 0.035), extents=(0.075, 0.035, 0.075),
         yaw=-88.0, zone="floor", cls="tape measure"),
    dict(id_="marker_9f8e", centre=(0.3, 0.3, 0.76), extents=(0.145, 0.015, 0.015), yaw=177.5),
]


def _scan(rng, truth=TRUTH, jit=0.004, jit_yaw=3.0):
    """One scan's measurements: truth + up to 4 mm / 3 deg of jitter (cluster.py's box stays
    within ~1 deg; 4 mm is the G2 test's figure)."""
    out = []
    for t in truth:
        c = tuple(v + rng.uniform(-jit, jit) for v in t["centre"])
        e = tuple(v + rng.uniform(-jit, jit) for v in t["extents"])
        y = (t["yaw"] + rng.uniform(-jit_yaw, jit_yaw) + 90) % 180 - 90   # cluster folds to [-90, 90)
        out.append(_m(t["id_"], c, e, y, t.get("zone", "desk"), t.get("cls", "mug")))
    return out


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def room(tmp_path):
    _git(tmp_path, "init", "-q")
    serialize.serialize(tmp_path, _scan(random.Random(0)), head={})
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "baseline")
    return tmp_path


def test_unchanged_room_rescans_clean(room):
    rng = random.Random(1)
    for _ in range(25):
        serialize.serialize(room, _scan(rng), head=Repo(room).records())
        assert _git(room, "status", "--porcelain", "-uall") == ""
    subprocess.run(["git", "-C", str(room), "diff", "--exit-code"], check=True)


def test_move_one_object_20cm_changes_exactly_one_file(room):
    moved = [dict(t) for t in TRUTH]
    moved[1]["centre"] = (0.805, 0.215, 0.755)
    serialize.serialize(room, _scan(random.Random(2), moved), head=Repo(room).records())
    assert _git(room, "status", "--porcelain", "-uall").splitlines() == [" M zones/desk/book_c3d4.yaml"]
    assert "\n-  x: 0.60\n+  x: 0.80\n" in _git(room, "diff")


def test_unobserved_is_carried_removed_is_deleted(room):
    head = Repo(room).records()
    scan = [m for m in _scan(random.Random(3)) if m.id not in ("mug_a1b2", "can_e5f6")]
    serialize.serialize(room, scan, head=head, unobserved=("mug_a1b2",))   # occluded, not gone
    assert _git(room, "status", "--porcelain", "-uall").splitlines() == [" D zones/desk/can_e5f6.yaml"]


def test_a_move_is_an_object_decision(room):
    """roomctl.state.settle (docs/20 Part 4): under MOVE_M the committed file stands, even when
    every field would have crossed its own deadband; past it, the pose moves and the extents
    (identity since first sight) don't."""
    from roomctl.state import MOVE_M
    nudged = [dict(t) for t in TRUTH]
    nudged[1]["centre"] = (0.605 + 0.03, 0.215, 0.755)                     # 3 cm: below MOVE_M
    serialize.serialize(room, _scan(random.Random(4), nudged), head=Repo(room).records())
    assert _git(room, "status", "--porcelain", "-uall") == "" and MOVE_M == 0.05
    moved = [dict(t) for t in TRUTH]
    moved[1]["centre"], moved[1]["extents"] = (0.605 + 0.08, 0.215, 0.755), (0.30, 0.20, 0.05)
    serialize.serialize(room, _scan(random.Random(5), moved), head=Repo(room).records())
    diff = _git(room, "diff", "-U0")
    assert _git(room, "status", "--porcelain", "-uall").splitlines() == [" M zones/desk/book_c3d4.yaml"]
    assert diff.count("\n+  ") == 1 and re.search(r"\n\+  x: 0\.6[89]\n", diff)   # pose.x only; 0.685 +- 4 mm
