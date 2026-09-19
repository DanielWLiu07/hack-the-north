"""The object record: the one YAML shape every tracked object is committed as. FROZEN.

This module owns the schema (TEAM.md "the seams"). Perception produces records,
`fake/scene_gen.py` produces records, roomctl diffs and executes records — all through
`to_yaml` / `from_yaml` here, so there is exactly one text form. Changing it changes every
file in every room.git; `tests/test_state.py` pins the exact bytes so that can't happen by
accident.

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

The file lives at `zones/<zone>/<id>.yaml`. Frame and units are docs/20's canonical world
frame: metres, X forward from the anchor tag, Y left, Z up, floor at z = 0.

| field      | meaning                                              | changes when            |
|------------|------------------------------------------------------|-------------------------|
| id         | `<class_slug>_<4 hex>`, assigned once (`new_id`)     | never                   |
| class      | free-text label at first sight ("tape measure")      | never                   |
| zone       | the directory it lives in                            | it moves zone (a rename)|
| pose.x/y/z | bbox centre, metres, 1 cm quanta                     | it MOVED (see settle)   |
| pose.yaw   | AXIS of extents.x, integer degrees in [0, 180), 5°   | it MOVED                |
| extents    | x = length along the yaw axis, y = width, z = height | never (first sight)     |
| color      | dominant colour at first sight, `#rrggbb`            | never                   |
| first_seen | UTC, whole seconds                                   | never                   |

Yaw is an axis, not a heading. Perception gets it from the principal axis of the XY
footprint (perception/cluster.py `Instance.box()`), which can't tell front from back — so
15° and 195° are the same object and only [0, 180) is allowed. Footprints rounder than
aspect 1.2 have no axis at all and are always yaw 0. Compare yaws modulo 180, never with a
plain subtraction: 175° vs 5° is a 10° turn, not 170°.

Rules the text form guarantees, because `git diff` of these files IS the demo:
- one field per line, fixed key order, block style, trailing newline
- positions and extents with exactly two decimals; yaw an integer in [0, 180)
- NOTHING that wobbles between scans of an unchanged room. `confidence`, `observed_by`,
  `point_count` and descriptions live in Elasticsearch, never here (docs/20 Part 5).
  Any of them in the YAML is a phantom diff on every scan.

Quantizing is the writer's job (perception/serialize.py `stabilize()`, with the constants
below); this module formats and refuses values that aren't on the grid.

WHETHER an object moved is decided per object, not per field — `settle()`, docs/20 Part 4:
"matched, ‖Δp‖ < threshold → unchanged, file byte-identical". Measured on the real pipeline
(synthetic single-view recordings, docs/10 P15): an untouched object's centre wanders up to
3 cm and its footprint 3–5 cm / 20°, far past a per-field 1.5-quantum deadband. So: moved less
than MOVE_M in the same zone → the committed record, whole; moved → a fresh pose and yaw,
identity carried (class, color, first_seen, and extents — an object doesn't change size, and
re-measuring it only imports noise). The cost, stated: a move or turn smaller than MOVE_M is
invisible. Every demo move is 15+ cm.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

SCHEMA_VERSION = 1

# docs/20 Part 5. serialize.stabilize() and fake/scene_gen.py both read these.
Q_POS = 0.01    # metres
Q_YAW = 5       # degrees
YAW_PERIOD = 180  # yaw is an axis: compare modulo this
HYST = 1.5      # a committed value is kept unless the new one differs by >= HYST quanta
MOVE_M = 0.05   # an object "moved" when its centre moved this far (or it changed zone). MEASURED:
                # untouched objects wander <= 3 cm between single-view scans (docs/10 P15)

ID_RE = re.compile(r"^[a-z][a-z0-9_]*_[0-9a-f]{4}$")
ZONE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
COLOR_RE = re.compile(r"^#[0-9a-f]{6}$")
TIME_FMT = "%Y-%m-%dT%H:%M:%SZ"
KEYS = ["id", "class", "zone", "pose", "extents", "color", "first_seen"]

# libyaml's loader is ~20x faster than pure Python; status reads every object file.
_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

# Plain scalars YAML 1.1 would read as something other than a string.
_YAML_SPECIAL = {"y", "n", "yes", "no", "on", "off", "true", "false", "null", "~"}


class SchemaError(ValueError):
    pass


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    z: float
    yaw: int


@dataclass(frozen=True)
class Extents:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class ObjectRecord:
    id: str
    cls: str  # `class` in YAML
    zone: str
    pose: Pose
    extents: Extents
    color: str
    first_seen: str  # ISO-8601 UTC, whole seconds, "Z"

    @property
    def path(self) -> str:
        return object_path(self.zone, self.id)


def object_path(zone: str, object_id: str) -> str:
    return f"zones/{zone}/{object_id}.yaml"


def new_id(cls: str, capture_id: str, ordinal: int) -> str:
    """Assigned ONCE, at first sight, then carried forward by association — never recomputed
    from geometry (a geometry-derived id renames the file on every move).

    docs/20 Part 5 hashes `first_seen_commit`, but at scan time that commit doesn't exist
    yet: the id is inside the files the commit is made of. The capture that first saw the
    object is known, unique, and stable, so hash that instead."""
    s = slug(cls)
    return f"{s}_{hashlib.sha1(f'{s}|{capture_id}|{ordinal}'.encode()).hexdigest()[:4]}"


def slug(cls: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", cls.lower()).strip("_") or "unknown"


# ── the text form ────────────────────────────────────────────────────────────

def fmt_m(v: float) -> str:
    s = f"{v:.2f}"
    return "0.00" if s == "-0.00" else s


def _scalar(s: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9 _-]*", s) and s not in _YAML_SPECIAL and not s.endswith(" "):
        return s
    return json.dumps(s)  # a JSON string is a valid YAML double-quoted scalar


def to_yaml(rec: ObjectRecord) -> str:
    validate(rec)
    p, e = rec.pose, rec.extents
    return (
        f"id: {rec.id}\n"
        f"class: {_scalar(rec.cls)}\n"
        f"zone: {rec.zone}\n"
        f"pose:\n"
        f"  x: {fmt_m(p.x)}\n"
        f"  y: {fmt_m(p.y)}\n"
        f"  z: {fmt_m(p.z)}\n"
        f"  yaw: {p.yaw}\n"
        f"extents:\n"
        f"  x: {fmt_m(e.x)}\n"
        f"  y: {fmt_m(e.y)}\n"
        f"  z: {fmt_m(e.z)}\n"
        f"color: {json.dumps(rec.color)}\n"
        f"first_seen: {json.dumps(rec.first_seen)}\n"
    )


def from_yaml(text: str) -> ObjectRecord:
    if "\n<<<<<<< " in f"\n{text}":
        raise SchemaError("file has merge conflict markers — resolve with --ours/--theirs first")
    try:
        d = yaml.load(text, Loader=_Loader)
    except yaml.YAMLError as e:
        raise SchemaError(f"not YAML: {e}") from None
    if not isinstance(d, dict):
        raise SchemaError("object file is not a mapping")
    if list(d) != KEYS:
        raise SchemaError(f"keys must be exactly {KEYS} in that order, got {list(d)}")
    first_seen = d["first_seen"]
    if isinstance(first_seen, datetime):  # tolerate a hand-edited unquoted timestamp
        first_seen = iso_utc(first_seen)
    try:
        rec = ObjectRecord(
            id=str(d["id"]), cls=str(d["class"]), zone=str(d["zone"]),
            pose=Pose(float(d["pose"]["x"]), float(d["pose"]["y"]), float(d["pose"]["z"]),
                      int(d["pose"]["yaw"])),
            extents=Extents(float(d["extents"]["x"]), float(d["extents"]["y"]),
                            float(d["extents"]["z"])),
            color=str(d["color"]), first_seen=str(first_seen))
    except (KeyError, TypeError, ValueError) as e:
        raise SchemaError(f"bad field: {type(e).__name__} {e}") from None
    validate(rec)
    return rec


def validate(rec: ObjectRecord) -> None:
    where = rec.id
    if not ID_RE.match(rec.id):
        raise SchemaError(f"bad object id {rec.id!r} (want <class_slug>_<4 hex>)")
    if not rec.cls or rec.cls != rec.cls.strip() or "\n" in rec.cls:
        raise SchemaError(f"{where}: bad class {rec.cls!r}")
    if not ZONE_RE.match(rec.zone):
        raise SchemaError(f"{where}: bad zone {rec.zone!r}")
    for name, v in (("pose.x", rec.pose.x), ("pose.y", rec.pose.y), ("pose.z", rec.pose.z),
                    ("extents.x", rec.extents.x), ("extents.y", rec.extents.y),
                    ("extents.z", rec.extents.z)):
        if abs(v / Q_POS - round(v / Q_POS)) > 1e-6:
            raise SchemaError(f"{where}: {name}={v!r} is not on the {Q_POS} m grid — quantize first")
    if min(rec.extents.x, rec.extents.y, rec.extents.z) <= 0:
        raise SchemaError(f"{where}: extents must be positive")
    if not isinstance(rec.pose.yaw, int) or not 0 <= rec.pose.yaw < YAW_PERIOD or rec.pose.yaw % Q_YAW:
        raise SchemaError(f"{where}: yaw={rec.pose.yaw!r} must be an int multiple of {Q_YAW} in "
                          f"[0, {YAW_PERIOD}) — it's an axis, fold it with % {YAW_PERIOD}")
    if not COLOR_RE.match(rec.color):
        raise SchemaError(f"{where}: bad color {rec.color!r} (want #rrggbb, lowercase)")
    try:
        datetime.strptime(rec.first_seen, TIME_FMT)
    except ValueError:
        raise SchemaError(f"{where}: first_seen {rec.first_seen!r} is not YYYY-MM-DDTHH:MM:SSZ") from None


def settle(prev: ObjectRecord | None, measured: ObjectRecord) -> ObjectRecord:
    """docs/20 Part 4's unchanged-vs-moved, for one object. `measured` is already quantized.

    Not moved (same zone, centre within MOVE_M of the committed one) -> `prev`, byte-identical.
    Moved -> the measured pose and yaw, with prev's identity: class, color, first_seen, extents.
    New (no prev) -> `measured` as it is."""
    if prev is None:
        return measured
    d = ((prev.pose.x - measured.pose.x) ** 2 + (prev.pose.y - measured.pose.y) ** 2
         + (prev.pose.z - measured.pose.z) ** 2) ** 0.5
    if prev.zone == measured.zone and d < MOVE_M:
        return prev
    return ObjectRecord(prev.id, prev.cls, measured.zone, measured.pose, prev.extents,
                        prev.color, prev.first_seen)


def iso_utc(t: datetime) -> str:
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime(TIME_FMT)


# ── the working tree ─────────────────────────────────────────────────────────

def read_tree(root: Path) -> dict[str, ObjectRecord]:
    """Every object file under root/zones, by id. Raises on a malformed file, a file in
    the wrong zone directory, or a file whose name doesn't match its id."""
    out: dict[str, ObjectRecord] = {}
    zones = Path(root) / "zones"
    for p in sorted(zones.glob("*/*.yaml")) if zones.is_dir() else []:
        try:
            rec = from_yaml(p.read_text())
        except SchemaError as e:
            raise SchemaError(f"{p.relative_to(root)}: {e}") from None
        if rec.path != p.relative_to(root).as_posix():
            raise SchemaError(f"{p.relative_to(root)}: contents say {rec.path}")
        if rec.id in out:
            raise SchemaError(f"{rec.id} is in two zones: {out[rec.id].path} and {rec.path}")
        out[rec.id] = rec
    return out


def write_tree(root: Path, records: list[ObjectRecord]) -> None:
    """Make root/zones/** hold exactly these records. Files whose bytes wouldn't change are
    not touched; files for objects not in `records` are deleted; empty zone dirs removed."""
    root = Path(root)
    want = {r.path: to_yaml(r) for r in records}
    if len(want) != len(records):
        raise SchemaError("duplicate object ids")
    zones = root / "zones"
    have = {p.relative_to(root).as_posix() for p in zones.glob("*/*.yaml")} if zones.is_dir() else set()
    for rel in sorted(have - want.keys()):
        (root / rel).unlink()
    for rel, text in sorted(want.items()):
        p = root / rel
        if not p.is_file() or p.read_text() != text:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
    if zones.is_dir():
        for d in sorted(zones.iterdir()):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
