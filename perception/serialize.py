"""Objects -> the room repo's working tree. Stage 11 of docs/20-perception-logic.md.

The text form and the quanta belong to roomctl/state.py (frozen); this module only decides
WHICH value to write. stabilize() quantizes AND keeps the committed value inside a
deadband, so scanning an unchanged room writes byte-identical files and
`git diff --exit-code` stays clean (docs/20 Part 5). Both halves are required: quantizing
alone flips a value that sits on a bucket edge (0.425 m -> 0.42 / 0.43) every scan.

Units: metres on the Q_POS grid, yaw in whole degrees on the Q_YAW grid. Frame: F_world
(Z-up), as fuse.py produces it; nothing here converts frames.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import obs  # noqa: E402
from roomctl.state import (HYST, Q_POS, Q_YAW, YAW_PERIOD, Extents, ObjectRecord, Pose,  # noqa: E402
                           settle, write_tree)


@dataclass(frozen=True)
class Measured:
    """One object as this scan saw it, after associate.py gave it its stable id."""
    id: str                                  # roomctl.state.new_id at first sight, then carried
    cls: str
    zone: str
    centre: tuple[float, float, float]       # F_world, metres  (cluster.Instance.box()[0])
    extents: tuple[float, float, float]      # along the yaw axis, across it, height (box()[1])
    yaw: float                               # degrees, any range; an AXIS (box()[2])
    color: str                               # "#rrggbb"; only used at first sight
    first_seen: str                          # "YYYY-MM-DDTHH:MM:SSZ"; only used at first sight


def stabilize(new: float, committed: float | None, q: float = Q_POS, hyst: float = HYST) -> float:
    """Quantize, but keep the committed value inside the deadband."""
    if committed is not None and abs(new - committed) < q * hyst:
        return committed                       # <- no diff, byte-identical
    return round(new / q) * q


def yaw_diff(a: float, b: float) -> float:
    """Smallest turn between two AXES, in [-90, 90): 175 vs 5 is 10, not 170."""
    h = YAW_PERIOD / 2
    return (a - b + h) % YAW_PERIOD - h


def stabilize_yaw(new: float, committed: int | None) -> int:
    """stabilize() for an axis: deadband compared modulo YAW_PERIOD, result folded into
    [0, YAW_PERIOD) AFTER rounding (178 rounds to 180, which is 0)."""
    if committed is not None and abs(yaw_diff(new, committed)) < Q_YAW * HYST:
        return committed
    return int(round(new / Q_YAW)) * Q_YAW % YAW_PERIOD


UNNAMED = "unknown"          # not a class: the absence of one (associate.UNKNOWN, roomctl.state)


def stabilize_record(m: Measured, head: ObjectRecord | None) -> ObjectRecord:
    """This scan's measurement, stabilized against the committed record. class, color and
    first_seen are first-sight facts: carried from HEAD, never re-measured.

    ONE exception, and it is not a re-measurement: a committed class of "unknown" is the ABSENCE
    of a name (no segmenter label and no VLM text at first sight), so a scan that CAN name the
    object gives it that name. Without this a thing the room can describe stays "unknown" for
    ever. The id never changes with it — `unknown_1231` keeps its id once it is a snack bag,
    because the id IS the identity (docs/25). A real class is never overwritten by a later one.
    """
    p, e = (head.pose, head.extents) if head else (None, None)
    x, y, z = (stabilize(v, getattr(p, a) if p else None) for v, a in zip(m.centre, "xyz"))
    ex, ey, ez = (max(Q_POS, stabilize(v, getattr(e, a) if e else None)) for v, a in zip(m.extents, "xyz"))
    return ObjectRecord(
        id=m.id,
        cls=(m.cls if head.cls == UNNAMED and m.cls != UNNAMED else head.cls) if head else m.cls,
        zone=m.zone,
        pose=Pose(x, y, z, stabilize_yaw(m.yaw, p.yaw if p else None)),
        extents=Extents(ex, ey, ez),
        color=head.color if head else m.color,
        first_seen=head.first_seen if head else m.first_seen,
    )


def serialize(root: Path, measured: list[Measured], head: dict[str, ObjectRecord],
              unobserved: tuple[str, ...] = ()) -> list[ObjectRecord]:
    """Make root/zones/** hold exactly this scan's objects, stabilized against `head`.

    head is the COMMITTED state (`roomctl.repo.Repo(root).records()`), not the working
    tree. The tree ends up holding `measured` plus the `unobserved` ids carried from HEAD
    untouched (docs/20 Part 4: occluded, not gone). Any other HEAD object is deleted.

    Two decisions, in order. stabilize() quantizes each field with its deadband. Then
    roomctl.state.settle() makes docs/20 Part 4's per-OBJECT call: same zone and centre
    within MOVE_M (5 cm) -> the committed record, byte-identical; moved -> the new pose and
    yaw, extents and identity kept. Per-field hysteresis alone let single-view stereo's
    3 cm wander through as phantom diffs (docs/10 P15).
    """
    with obs.span("perception.serialize", n_objects=len(measured)) as sp:
        records = [settle(head.get(m.id), stabilize_record(m, head.get(m.id))) for m in measured]
        records += [head[i] for i in unobserved]
        write_tree(Path(root), records)
        ids = {r.id for r in records}      # the phantom-diff gauge: 0 on an unchanged room, every scan
        n_changed = sum(head.get(r.id) != r for r in records) + sum(i not in ids for i in head)
        if sp is not None:
            sp.set_data("n_changed", n_changed)
        obs.measure(n_changed=n_changed)          # charted: a phantom diff is a spike on a flat line
    return records
