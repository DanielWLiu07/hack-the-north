"""Read-only bounded room-voxels geometry, decoded against the pinned room octree.

No fixture fallback and no claim that indexed data came from a physical camera: the
current strict index mapping does not carry that provenance.
"""
from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime, timezone

import yaml
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

import room

router = APIRouter()
_es = None
SHA = re.compile(r"^[0-9a-f]{40}$")


def init(es) -> None:
    global _es
    _es = es


def _pinned():
    data = yaml.safe_load((room.room_path() / "room.yaml").read_text())
    oc = data["octree"]
    origin = [float(v) for v in oc["origin"]]
    size, levels = float(oc["size_m"]), oc["levels"]
    if (len(origin) != 3 or not all(math.isfinite(v) for v in origin)
            or not math.isfinite(size) or size <= 0 or isinstance(levels, bool)
            or not isinstance(levels, int) or not 1 <= levels <= 24):
        raise ValueError("invalid pinned octree")
    return {"origin": origin, "size_m": size, "levels": levels,
            "cell_size_m": size / 2 ** levels}


def _head():
    try:
        sha = room._git("rev-parse", "--verify", "--quiet", "HEAD", check=False).strip()
        return sha if SHA.fullmatch(sha) else None
    except room.RoomError:
        return None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("non-finite number")
    return float(value)


# A rung is a prefix length: 8 m / 2**n. At the pinned cube l3 = 1 m, l5 = 25 cm, l6 = 12.5 cm,
# l7 = 6.25 cm; "full" is the leaf, whatever depth the cube is pinned at. The coarse rungs exist
# because a prefix IS a region -- and because the 1 m rung alone draws 6 m^3 of boxes for 483 L
# of real occupancy (8% fill), so the page needs somewhere finer to go.
LEVELS = {"full": "voxel_key", "l7": "voxel_key_l7", "l6": "voxel_key_l6",
          "l5": "voxel_key_l5", "l3": "voxel_key_l3"}
LEVEL_DEPTH = {"l3": 3, "l5": 5, "l6": 6, "l7": 7}
# The route's own check, derived from LEVELS: spelling the rungs out twice is how l6 and l7
# reached the page and were then rejected at the door as "should match ^(full|l3|l5)$".
LEVEL_PATTERN = "^(" + "|".join(LEVELS) + ")$"


def decode_prefix(key, cube):
    """Any octree prefix → the cube it names. Same walk as a geohash: truncate, get a coarser cell."""
    if not isinstance(key, str) or not key or any(c not in "01234567" for c in key):
        raise ValueError("not a valid octree key")
    if len(key) > cube["levels"]:
        raise ValueError("octree key deeper than the pinned cube")
    lo, side = list(cube["origin"]), cube["size_m"]
    for char in key:
        side /= 2
        digit = int(char)
        for axis, shift in enumerate((2, 1, 0)):
            lo[axis] += side * ((digit >> shift) & 1)
    return {"voxel_key": key, "center": [v + side / 2 for v in lo], "size": side, "lo": lo}


def decode(doc, cube):
    """A leaf document -> its cube cell. DEPTH comes from the key, not from the pinned cube: a
    commit written before an OCTREE_LEVELS change is still all leaves, just shallower ones, and
    decode_prefix already walks the key's own digits to get its cell and size. Demanding
    len(key) == cube.levels rejected every document of every older commit -- the page showed
    "0 cells, 4,970 invalid" and the room vanished. A key DEEPER than the cube stays an error
    (decode_prefix raises): that means the writer is newer than this reader. Matches
    perception/voxelize.py's from_docs, which takes its depth from the keys for the same reason."""
    parsed = decode_prefix(doc.get("voxel_key"), cube)
    key, center, side, lo = parsed["voxel_key"], parsed["center"], parsed["size"], parsed["lo"]
    cell = doc.get("cell")
    if not isinstance(cell, dict):
        raise ValueError("missing indexed cell")
    if any(abs(_number(cell[a]) - center[i]) > side / 2 + 0.0001 for i, a in enumerate("xy")):
        raise ValueError("indexed cell belongs to a different cube")
    z_min, z_max = _number(doc["z_min"]), _number(doc["z_max"])
    if z_min > z_max or z_min < lo[2] - 0.0001 or z_max > lo[2] + side + 0.0001:
        raise ValueError("height samples outside indexed cell")
    density = _number(doc.get("density", 0))
    if density < 0:
        raise ValueError("negative density")
    return {"voxel_key": key, "center": center, "size": side,
            "object_id": doc.get("object_id"), "zone": doc.get("zone"),
            "density": density, "z_min": z_min, "z_max": z_max, "count": 1}


async def _search(body, label):
    if _es is None:
        raise RuntimeError("voxel endpoint not initialized")
    return await _es.search("room-voxels", body, label=label)


def _payload(sha, selection, head, stamp, cube, object_id, cells, total, invalid, truncated,
             level="full", prefix=None, aggregated=False):
    return {"source": "elasticsearch", "fetched_at": datetime.now(timezone.utc).isoformat(),
            "commit_sha": sha, "snapshot_source": selection,
            "snapshot": {"selection": selection, "head": head, "timestamp": stamp},
            "object_id": object_id, "frame": "world", "units": "metres", "cube": cube,
            "level": level, "prefix": prefix or "", "aggregated": aggregated,
            "cells": cells, "total": total, "returned": len(cells),
            "truncated": truncated, "invalid": invalid,
            "provenance": {"kind": "unknown", "detail": "Indexed voxel geometry. The room-voxels mapping does not record whether a physical camera or a synthetic source produced it."}}


def _filters(sha, object_id=None, prefix=None):
    filters = [{"term": {"commit_sha": sha}}]
    if object_id:
        filters.append({"term": {"object_id": object_id}})
    if prefix:
        filters.append({"prefix": {"voxel_key": prefix}})
    return filters


async def _leaves(sha, cube, filters, limit):
    cells, invalid, total, stamp, after, scanned, seen = [], 0, 0, None, None, 0, set()
    # Stay within Elasticsearch's default 10k result window. search_after allows
    # a bounded second page without increasing the cluster result window.
    while scanned < limit:
        page_size = min(5000, limit - scanned)
        body = {"size": page_size, "query": {"bool": {"filter": filters}},
                "sort": [{"voxel_key": "asc"}], "track_total_hits": True,
                "_source": ["voxel_key", "cell", "z_min", "z_max", "density", "zone", "object_id", "@timestamp"]}
        if after is not None:
            body["search_after"] = after
        result = await _search(body, "voxels.cells")
        hits_block = result.get("hits", {})
        total_field = hits_block.get("total", 0)
        total = total_field.get("value", 0) if isinstance(total_field, dict) else total_field
        hits = hits_block.get("hits", [])
        for hit in hits:
            doc = hit.get("_source", {})
            stamp = stamp or doc.get("@timestamp")
            try:
                cell = decode(doc, cube)
                if cell["voxel_key"] in seen:
                    raise ValueError("duplicate cell")
                seen.add(cell["voxel_key"])
                cells.append(cell)
            except (ValueError, KeyError, TypeError, OverflowError):
                invalid += 1
        scanned += len(hits)
        if len(hits) < page_size or not hits or not hits[-1].get("sort"):
            break
        after = hits[-1]["sort"]
    depths = {len(c["voxel_key"]) for c in cells}
    if len(depths) > 1:  # can't happen for a commit-scoped query; loud beats one grid at two sizes
        raise ValueError(f"room-voxels cells of several depths ({sorted(depths)}): a result spanning "
                         f"an OCTREE_LEVELS change would draw one grid at two cell sizes")
    return cells, invalid, total, stamp, total > len(cells) + invalid


async def _prefixes(sha, cube, filters, limit, level, prefix):
    field = LEVELS[level]
    depth = LEVEL_DEPTH[level]
    result = await _search({
        "size": 0, "query": {"bool": {"filter": filters}}, "track_total_hits": True,
        "aggs": {"cells": {"terms": {"field": field, "size": limit, "order": {"_key": "asc"}},
                           "aggs": {"objects": {"terms": {"field": "object_id", "size": 8}},
                                    "density": {"sum": {"field": "density"}}}}},
    }, "voxels.grid")
    agg = (result.get("aggregations") or {}).get("cells") or {}
    buckets = agg.get("buckets") or []
    cells, invalid, seen = [], 0, set()
    for bucket in buckets:
        key = str(bucket.get("key"))
        try:
            if not isinstance(key, str) or len(key) != depth:
                raise ValueError("aggregated key is not this level")
            if key in seen:
                raise ValueError("duplicate cell")
            if prefix and not key.startswith(prefix):
                raise ValueError("aggregated key escaped the prefix filter")
            parsed = decode_prefix(key, cube)
            seen.add(key)
            owners = [b["key"] for b in (bucket.get("objects") or {}).get("buckets") or [] if b.get("key")]
            cells.append({"voxel_key": key, "center": parsed["center"], "size": parsed["size"],
                          "count": int(bucket.get("doc_count") or 0),
                          "density": _number((bucket.get("density") or {}).get("value") or 0),
                          "object_id": owners[0] if owners else None, "owners": owners,
                          "zone": None, "z_min": parsed["lo"][2], "z_max": parsed["lo"][2] + parsed["size"]})
        except (ValueError, KeyError, TypeError, OverflowError):
            invalid += 1
    leaf_total = result.get("hits", {}).get("total", 0)
    leaves = leaf_total.get("value", 0) if isinstance(leaf_total, dict) else leaf_total
    truncated = int(agg.get("sum_other_doc_count") or 0) > 0 or len(buckets) >= limit
    return cells, invalid, max(leaves, len(cells)), truncated


async def build(commit_sha=None, object_id=None, limit=12000, level="full", prefix=None):
    if commit_sha is not None and not SHA.fullmatch(commit_sha):
        raise ValueError("commit_sha must be a full 40-character lowercase SHA")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20000:
        raise ValueError("limit must be between 1 and 20000")
    if level not in LEVELS:
        raise ValueError(f"level must be one of {', '.join(LEVELS)}")
    if prefix is not None and (not isinstance(prefix, str) or any(c not in "01234567" for c in prefix)
                               or len(prefix) > 24):
        raise ValueError("prefix must be octree digits 0-7")
    cube, head = await asyncio.gather(asyncio.to_thread(_pinned), asyncio.to_thread(_head))
    selection = "requested" if commit_sha else "head"
    sha, stamp = commit_sha or head, None
    if not commit_sha:
        found = []
        if head:
            result = await _search({"size": 1, "query": {"term": {"commit_sha": head}},
                                    "_source": ["commit_sha", "@timestamp"]}, "voxels.head")
            found = result.get("hits", {}).get("hits", [])
        if not found:
            result = await _search({"size": 1, "query": {"exists": {"field": "commit_sha"}},
                                    "sort": [{"@timestamp": "desc"}],
                                    "_source": ["commit_sha", "@timestamp"]}, "voxels.latest")
            found = result.get("hits", {}).get("hits", [])
            selection = "latest_indexed"
        sha = found[0]["_source"].get("commit_sha") if found else None
        stamp = found[0]["_source"].get("@timestamp") if found else None
        if sha is not None and (not isinstance(sha, str) or not SHA.fullmatch(sha)):
            raise ValueError("indexed snapshot lacks a full valid commit SHA")
    cells, invalid, total, truncated, aggregated = [], 0, 0, False, False
    if sha:
        filters = _filters(sha, object_id, prefix)
        depth = LEVEL_DEPTH.get(level)
        if depth and (not prefix or len(prefix) <= depth):
            cells, invalid, total, truncated = await _prefixes(sha, cube, filters, limit, level, prefix)
            aggregated = True
        else:
            cells, invalid, total, stamp_cells, truncated = await _leaves(sha, cube, filters, limit)
            stamp = stamp or stamp_cells
    return _payload(sha, selection, head, stamp, cube, object_id, cells, total, invalid, truncated,
                    level, prefix, aggregated)


@router.get("/api/voxels")
async def voxels(commit_sha: str | None = Query(None, pattern=r"^[0-9a-f]{40}$"),
                 object_id: str | None = Query(None, min_length=1, max_length=128),
                 limit: int = Query(12000, ge=1, le=20000),
                 level: str = Query("full", pattern=LEVEL_PATTERN),
                 prefix: str | None = Query(None, pattern=r"^[0-7]{1,24}$")):
    try:
        return await build(commit_sha, object_id, limit, level, prefix)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return JSONResponse({"error": "voxel_geometry_unavailable", "detail": "Pinned room geometry or indexed snapshot is invalid or unavailable.", "retryable": False}, status_code=503)
    except Exception as exc:
        # Never echo arbitrary exception text: upstream URLs may contain credentials.
        return JSONResponse({"error": getattr(exc, "code", "voxel_upstream_unavailable"),
                             "detail": "Elasticsearch voxel data is currently unavailable.",
                             "retryable": bool(getattr(exc, "retryable", True))},
                            status_code=int(getattr(exc, "status", 503)))
