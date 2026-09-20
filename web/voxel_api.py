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


def octree_key(x, y, z, origin, size, levels):
    """A point -> the octree key of the leaf it falls in. docs/11's encoder, and the SAME walk
    as perception/voxelize.octree_key — kept identical on purpose: if these two ever disagree,
    an object's key and the voxels indexed around it name different cells. Outside the pinned
    cube returns None, because a key only means anything relative to that cube."""
    f = [(v - o) / size for v, o in zip((x, y, z), origin)]
    if any(not math.isfinite(c) or c < 0.0 or c >= 1.0 for c in f):
        return None
    digits = []
    for _ in range(levels):
        f = [c * 2 for c in f]
        bits = [int(c) for c in f]
        f = [c - b for c, b in zip(f, bits)]
        digits.append(str((bits[0] << 2) | (bits[1] << 1) | bits[2]))
    return "".join(digits)


def fit_depth(extents, size, levels):
    """How deep a prefix should be to name a region THE SIZE OF THIS OBJECT.

    A prefix is a region, and cell side = size / 2**depth. Picking a fixed rung is wrong in both
    directions: at l3 a 12 cm mug drills to the 1 m box around it, and at the leaf a lamp drills
    to a 3 cm crumb of itself. So take the DEEPEST depth whose cell still fits the object's
    largest extent — the tightest region that can hold the whole thing.

    None when the record carries no usable extents; the caller then has to say so rather than
    pick a rung and imply it measured something.
    """
    if not isinstance(extents, dict):
        return None
    vals = [extents.get(k) for k in ("x", "y", "z")]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v) and v > 0 for v in vals):
        return None
    depth = math.floor(math.log2(size / max(vals)))            # cell >= the object's longest side
    return max(1, min(levels, int(depth)))


INSTANCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CAPTURE = re.compile(r"^cap_[0-9]{1,12}$")


def _commit_of_capture(repo, capture_id: str) -> str | None:
    """The commit that recorded a capture, found by its id in the subject — `[cap_1003]`.

    Not every capture has one: a capture only becomes a commit when it is committed, and the graph
    shows both. None means "this capture was never committed", which is a fact worth saying rather
    than an error worth hiding.
    """
    import scene_api
    if not CAPTURE.fullmatch(capture_id or ""):
        return None
    out = scene_api._git(repo, "log", "-200", "--format=%H%x1f%s")                        # noqa: SLF001
    for line in out.splitlines():
        sha, _, subject = line.partition("\x1f")
        if SHA.fullmatch(sha) and capture_id in subject:
            return sha
    return None


def _instance_state(instance: str, ref: str):
    """The object tree of ONE SCENE INSTANCE at a ref — `~/.cache/gitspace/rooms/<instance>/`,
    the same repo the 3D viewer and the History graph read.

    A scene instance is its own git repo, separate from room.git: one commit there holds both
    cloud/current.ply and the zones/<zone>/<id>.yaml found in it. Reading room.git instead is how
    the Objects tab came to list a room nobody was looking at.

    Returns (sha, {object_id: record}) or (None, None) when the repo or ref cannot be read.
    """
    import scene_api
    repo = scene_api._under_rooms(instance)                                # noqa: SLF001
    if not (repo / ".git").exists():
        return None, None
    sha = scene_api._git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")   # noqa: SLF001
    if not SHA.fullmatch(sha):
        # The History graph's nodes are keyed by CAPTURE ID (cap_1003), not by sha — a capture that
        # was never committed has no sha at all. Resolve the id to the commit that recorded it, the
        # same way scene_api reads it back: the subject carries the id.
        sha = _commit_of_capture(repo, ref)
    if not SHA.fullmatch(sha or ""):
        return None, None
    objects = {}
    for path in scene_api._git(repo, "ls-tree", "-r", "--name-only", sha, "--", "zones").splitlines():  # noqa: SLF001
        object_id, zone = room._object_of(path)                            # noqa: SLF001
        if not object_id:
            continue
        rec = room._record(scene_api._git(repo, "show", f"{sha}:{path}") or "")           # noqa: SLF001
        objects[object_id] = {
            "object_id": object_id, "zone": zone, "class": rec.get("class"),
            "color": rec.get("color"), "first_seen": rec.get("first_seen"),
            "pose": room._pose(rec),                                       # noqa: SLF001
            "extents": rec.get("extents") if isinstance(rec.get("extents"), dict) else None,
        }
    return sha, objects


@router.get("/api/object-map")
async def object_map(ref: str = Query("HEAD", min_length=1, max_length=64),
                     instance: str | None = Query(None, min_length=1, max_length=64)):
    """Every object at `ref`, mapped to WHERE IT IS in both languages the room speaks:
    its location (zone + metric pose) and its geohash (the octree key of the cell that pose
    falls in, plus the coarser rungs — a prefix IS a region).

    `instance` picks WHICH ROOM. A scene instance (`hallway-test`, …) is its own git repo and is
    what the 3D viewer and the History graph are showing; without it this falls back to room.git,
    which is a DIFFERENT room. Always pass the instance the page is displaying, or the list will
    honestly describe a room that is not on screen.

    Git is the source for the objects; the cube is room.yaml's pinned octree. Nothing here reads
    Elasticsearch: this is the mapping a voxel query is BUILT from, so deriving it from the index
    would be circular. An object outside the pinned cube gets a null key and says so.
    """
    import graph_api                                        # the object tree at a ref, from git alone
    if instance is not None and not INSTANCE.fullmatch(instance):
        return JSONResponse({"error": "bad_instance", "detail": "Not a valid scene instance name.",
                             "retryable": False}, status_code=400)
    try:
        cube = _pinned()
        if instance:
            sha, records = _instance_state(instance, ref)
            if sha is None:
                uncommitted = bool(CAPTURE.fullmatch(ref))
                return JSONResponse({
                    "error": "capture_not_committed" if uncommitted else "unknown_ref",
                    "detail": (f"{ref} is a capture that was never committed in {instance}, so it has "
                               "no object tree to map." if uncommitted
                               else f"No commit {ref} in scene instance {instance}."),
                    "instance": instance, "ref": ref, "retryable": False}, status_code=404)
            zones = {}
        else:
            sha = graph_api._resolve(ref)                                  # noqa: SLF001
            if not sha:
                return JSONResponse({"error": "unknown_ref", "detail": f"No such commit or branch: {ref}",
                                     "retryable": False}, status_code=404)
            state = graph_api._state(sha)                                  # noqa: SLF001
            records, zones = state.get("objects", {}), state.get("zones", {})
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return JSONResponse({"error": "object_map_unavailable",
                             "detail": "Pinned room geometry or the object tree is unavailable.",
                             "retryable": False}, status_code=503)

    origin, size, levels = cube["origin"], cube["size_m"], cube["levels"]
    objects, outside = [], 0
    for obj in records.values():
        pose = obj.get("pose") or {}
        key = octree_key(pose.get("x"), pose.get("y"), pose.get("z"), origin, size, levels) \
            if all(isinstance(pose.get(k), (int, float)) for k in ("x", "y", "z")) else None
        if key is None:
            outside += 1
        extents = obj.get("extents")
        depth = fit_depth(extents, size, levels)
        objects.append({
            "object_id": obj.get("object_id"), "class": obj.get("class"), "zone": obj.get("zone"),
            "color": obj.get("color"), "first_seen": obj.get("first_seen"), "pose": pose or None,
            "extents": extents if isinstance(extents, dict) else None,
            "voxel_key": key,
            # the coarser rungs the octree page already speaks (LEVELS): a prefix is a region
            **{f"voxel_key_l{n}": (key[:n] if key else None) for n in (3, 5, 6, 7)},
            # the region the size of THIS object — what a click should drill to
            "fit_depth": depth,
            "fit_key": (key[:depth] if key and depth else None),
            "fit_cell_m": (round(size / 2 ** depth, 4) if depth else None),
            "in_cube": key is not None,
        })
    objects.sort(key=lambda o: (o["zone"] or "", o["object_id"] or ""))
    return {"ref": ref, "sha": sha, "cube": cube, "frame": "world", "units": "metres",
            # which room this is: never leave a caller to assume it matches what is on screen
            "instance": instance, "source": f"scene instance {instance}" if instance else "room.git",
            "zones": zones, "objects": objects,
            "total": len(objects), "outside_cube": outside}


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
