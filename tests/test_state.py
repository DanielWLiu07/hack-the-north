"""The object record is frozen. These tests pin its exact bytes: if one fails, you are about
to change every file in every room.git — tell the other tracks first (TEAM.md "the seams")."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roomctl.state import (MOVE_M, Extents, ObjectRecord, Pose, SchemaError, from_yaml,  # noqa: E402
                           new_id, read_tree, settle, to_yaml, write_tree)

MUG = ObjectRecord("mug_a1b2", "mug", "desk", Pose(0.42, 0.18, 0.76, 15), Extents(0.12, 0.09, 0.11),
                   "#2b4c7e", "2026-09-18T14:12:33Z")

GOLDEN = """\
id: mug_a1b2
class: mug
zone: desk
pose:
  x: 0.42
  y: 0.18
  z: 0.76
  yaw: 15
extents:
  x: 0.12
  y: 0.09
  z: 0.11
color: "#2b4c7e"
first_seen: "2026-09-18T14:12:33Z"
"""


def test_golden_bytes():
    assert to_yaml(MUG) == GOLDEN


def test_round_trip():
    assert from_yaml(GOLDEN) == MUG
    assert to_yaml(from_yaml(to_yaml(MUG))) == GOLDEN


def test_float_noise_formats_identically():
    # round(0.61 / 0.01) * 0.01 == 0.6100000000000001 — must not leak into the file
    noisy = ObjectRecord(MUG.id, MUG.cls, MUG.zone, Pose(round(0.61 / 0.01) * 0.01, 0.18, 0.76, 15),
                         MUG.extents, MUG.color, MUG.first_seen)
    assert "  x: 0.61\n" in to_yaml(noisy)
    assert "-0.00" not in to_yaml(ObjectRecord(MUG.id, MUG.cls, MUG.zone, Pose(-0.0, 0.18, 0.76, 15),
                                               MUG.extents, MUG.color, MUG.first_seen))


def test_multiword_and_yaml_special_classes_stay_strings():
    for cls in ("tape measure", "yes", "null", "Mug"):
        rec = ObjectRecord("thing_0a1b", cls, "desk", MUG.pose, MUG.extents, MUG.color, MUG.first_seen)
        assert from_yaml(to_yaml(rec)).cls == cls


@pytest.mark.parametrize("bad", [
    dict(pose=Pose(0.425, 0.18, 0.76, 15)),        # off the 1 cm grid: quantize first
    dict(pose=Pose(0.42, 0.18, 0.76, 17)),         # off the 5 degree grid
    dict(pose=Pose(0.42, 0.18, 0.76, 180)),        # yaw is an axis: [0, 180)
    dict(pose=Pose(0.42, 0.18, 0.76, 195)),
    dict(extents=Extents(0.12, 0.0, 0.11)),
    dict(id="Mug_a1b2"), dict(id="mug"), dict(zone="Desk"),
    dict(color="#2B4C7E"), dict(first_seen="2026-09-18 14:12:33"),
])
def test_rejects(bad):
    fields = {**dict(id=MUG.id, cls=MUG.cls, zone=MUG.zone, pose=MUG.pose, extents=MUG.extents,
                     color=MUG.color, first_seen=MUG.first_seen), **bad}
    with pytest.raises(SchemaError):
        to_yaml(ObjectRecord(**fields))


def test_nothing_that_wobbles():
    for field in ("confidence", "observed_by", "point_count", "description"):
        assert field not in GOLDEN
        with pytest.raises(SchemaError):
            from_yaml(GOLDEN + f"{field}: 0.9\n")


def test_conflict_markers_are_named():
    text = GOLDEN.replace("  x: 0.42\n", "<<<<<<< HEAD\n  x: 0.42\n=======\n  x: 0.61\n>>>>>>> b\n")
    with pytest.raises(SchemaError, match="merge conflict"):
        from_yaml(text)


def test_new_id_is_stable_and_slugged():
    a = new_id("Tape Measure", "cap_0007", 0)
    assert a == new_id("Tape Measure", "cap_0007", 0)
    assert a.startswith("tape_measure_") and len(a) == len("tape_measure_") + 4
    assert a != new_id("Tape Measure", "cap_0007", 1)


def test_write_then_read_tree(tmp_path):
    book = ObjectRecord("book_e5f6", "book", "shelf", Pose(0.4, 0.8, 1.01, 90), Extents(0.03, 0.15, 0.22),
                        "#2e7d32", MUG.first_seen)
    write_tree(tmp_path, [MUG, book])
    assert read_tree(tmp_path) == {"mug_a1b2": MUG, "book_e5f6": book}
    mtime = (tmp_path / MUG.path).stat().st_mtime_ns
    write_tree(tmp_path, [MUG])                       # book removed, mug untouched
    assert read_tree(tmp_path) == {"mug_a1b2": MUG}
    assert (tmp_path / MUG.path).stat().st_mtime_ns == mtime
    assert not (tmp_path / "zones" / "shelf").exists()


def test_read_tree_catches_misfiled_objects(tmp_path):
    (tmp_path / "zones" / "shelf").mkdir(parents=True)
    (tmp_path / "zones" / "shelf" / "mug_a1b2.yaml").write_text(GOLDEN)  # says zone: desk
    with pytest.raises(SchemaError, match="contents say"):
        read_tree(tmp_path)


def moved(rec, dx=0.0, yaw=None, zone=None, ext=None):
    p = rec.pose
    return ObjectRecord(rec.id, rec.cls, zone or rec.zone, Pose(round(p.x + dx, 2), p.y, p.z, rec.pose.yaw if yaw is None else yaw),
                        ext or rec.extents, rec.color, rec.first_seen)


def test_settle_keeps_the_committed_record_whole_when_nothing_moved():
    wobbly = moved(MUG, dx=0.03, yaw=40, ext=Extents(0.16, 0.07, 0.11))  # 3 cm, 25°, fatter: noise
    assert settle(MUG, wobbly) is MUG


def test_settle_takes_a_real_move_but_never_re_measures_identity():
    far = moved(MUG, dx=0.19, yaw=40, ext=Extents(0.16, 0.07, 0.11))
    got = settle(MUG, ObjectRecord(far.id, "cup", far.zone, far.pose, far.extents, "#ffffff", "2030-01-01T00:00:00Z"))
    assert got.pose == far.pose                                   # moved: fresh pose and yaw
    assert (got.cls, got.color, got.first_seen, got.extents) == (MUG.cls, MUG.color, MUG.first_seen, MUG.extents)


def test_settle_treats_a_zone_change_as_a_move_and_new_objects_as_measured():
    assert settle(MUG, moved(MUG, dx=0.01, zone="shelf")).zone == "shelf"
    assert settle(None, MUG) is MUG
    assert MOVE_M == 0.05
