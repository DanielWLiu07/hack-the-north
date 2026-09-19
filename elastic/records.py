"""Git-side object records -> room-objects documents. The ONE place `id` becomes `object_id`.

The git side (roomctl/state.py ObjectRecord, the YAML in room.git) says `id` and `class`;
Elasticsearch says `object_id` and `class`. Both names stay (docs/10 GAP 2). This module is
the translation, and tests/test_records.py pins its output key set to
mappings/room-objects.json exactly -- add a mapped field without handling it here and that
test fails, instead of dynamic:strict rejecting every commit at runtime.

    doc = to_es_doc(record, commit, at=committed_at, capture_id=cap,
                    meta={"confidence": .87, "point_count": 1420,
                          "observed_by": ["cam0", "cam2"], "raw_description": [d0, d1, d2]},
                    trace=obs.trace_fields())
    ingest.write(es, [ingest.action("room-objects", doc), ...])

`commit` is roomctl.repo.Commit (.sha, .parent, .branch; commit_event also reads .message and
.changes). ingest.commit_actions() turns one commit into its whole snapshot of bulk actions.
"""
from __future__ import annotations

import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from perception.voxelize import octree_key, pinned_cube  # noqa: E402  (same cube as room-voxels)
from roomctl.repo import ROBOT_NAME  # noqa: E402
from roomctl.state import ObjectRecord, iso_utc, validate  # noqa: E402

# Per-object facts that wobble between scans, so they live in Elasticsearch and never in the
# YAML (roomctl/state.py). Named exactly as the mapping names them.
META_FIELDS = ("confidence", "point_count", "observed_by", "raw_description")
TRACE_FIELDS = ("sentry_trace_id", "sentry_span_id", "sentry_url")


@lru_cache(maxsize=1)
def _cube():
    return pinned_cube()  # .env's ROOM_ORIGIN_*/ROOM_CUBE_SIZE/OCTREE_LEVELS, pinned forever


def to_es_doc(record: ObjectRecord, commit, *, at: datetime | str, capture_id: str,
              meta: dict | None = None, trace: dict | None = None,
              author: str = ROBOT_NAME) -> dict:
    """One room-objects document: `record` as committed in `commit`. Every mapped field is
    present; values that aren't known are None (a missing field and a null are the same to
    Elasticsearch, and a fixed key set is what the test can pin)."""
    validate(record)  # roomctl's own schema check: quanta, yaw axis, id/zone/colour shape
    meta, trace = meta or {}, trace or {}
    for name, given, allowed in (("meta", meta, META_FIELDS), ("trace", trace, TRACE_FIELDS)):
        unknown = sorted(set(given) - set(allowed))
        if unknown:
            raise ValueError(f"to_es_doc: unknown {name} keys {unknown} (allowed: {list(allowed)})")
    descriptions = meta.get("raw_description")
    if isinstance(descriptions, str):
        descriptions = [descriptions]

    p, e = record.pose, record.extents
    origin, size, levels = _cube()
    key = octree_key(p.x, p.y, p.z, origin, size, levels)  # None outside the indexed cube
    return {
        "@timestamp": at if isinstance(at, str) else iso_utc(at),
        "commit_sha": commit.sha,
        "parent_sha": commit.parent,
        "branch": commit.branch,
        "author": author,
        "capture_id": capture_id,
        "object_id": record.id,
        "class": record.cls,
        "zone": record.zone,
        "pose": {"x": p.x, "y": p.y, "z": p.z, "yaw": p.yaw},
        "position": {"x": p.x, "y": p.y},
        "extents": {"x": e.x, "y": e.y, "z": e.z},
        "color": record.color,
        "first_seen": record.first_seen,
        "confidence": meta.get("confidence"),
        "point_count": meta.get("point_count"),
        "observed_by": meta.get("observed_by"),
        "raw_description": descriptions,
        "voxel_key": key,
        "voxel_key_l5": key[:5] if key else None,
        "voxel_key_l3": key[:3] if key else None,
        **{f: trace.get(f) for f in TRACE_FIELDS},
    }


# git's name-status letter -> the room-events list it belongs in (R: the object changed zone)
CHANGE_LISTS = {"A": "objects_added", "D": "objects_removed", "M": "objects_moved", "R": "objects_moved"}


def commit_event(commit, *, at: datetime | str, capture_id: str | None,
                 trace: dict | None = None, author: str = ROBOT_NAME) -> dict:
    """The room-events document for one commit: the bridge from wall-clock to commit_sha, and
    what the analytics count. Built from commit.changes (git's name-status), so it can't
    disagree with the diff."""
    trace = trace or {}
    unknown = sorted(set(trace) - set(TRACE_FIELDS))
    if unknown:
        raise ValueError(f"commit_event: unknown trace keys {unknown}")
    lists = {name: [] for name in dict.fromkeys(CHANGE_LISTS.values())}
    zones = set()
    for status, path in commit.changes:
        parts = path.split("/")
        if len(parts) == 3 and parts[0] == "zones" and parts[2].endswith(".yaml"):
            lists[CHANGE_LISTS[status[0]]].append(parts[2].removesuffix(".yaml"))
            zones.add(parts[1])
    return {
        "@timestamp": at if isinstance(at, str) else iso_utc(at),
        "event_type": "commit",
        "commit_sha": commit.sha,
        "parent_sha": commit.parent,
        "branch": commit.branch,
        "message": commit.message,
        "author": author,
        "capture_id": capture_id,
        "outcome": "ok",
        "objects_affected": sorted({o for ids in lists.values() for o in ids}),
        **{name: sorted(ids) for name, ids in lists.items()},
        "zone": sorted(zones),
        **{f: trace.get(f) for f in TRACE_FIELDS},
    }
