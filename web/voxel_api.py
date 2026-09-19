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


def decode(doc, cube):
    key = doc.get("voxel_key")
    if not isinstance(key, str) or len(key) != cube["levels"] or any(c not in "01234567" for c in key):
        raise ValueError("not a full valid octree key")
    lo, side = list(cube["origin"]), cube["size_m"]
    for char in key:
        side /= 2
        digit = int(char)
        for axis, shift in enumerate((2, 1, 0)):
            lo[axis] += side * ((digit >> shift) & 1)
    center = [v + side / 2 for v in lo]
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
            "density": density, "z_min": z_min, "z_max": z_max}


async def _search(body, label):
    if _es is None:
        raise RuntimeError("voxel endpoint not initialized")
    return await _es.search("room-voxels", body, label=label)


async def build(commit_sha=None, object_id=None, limit=12000):
    if commit_sha is not None and not SHA.fullmatch(commit_sha):
        raise ValueError("commit_sha must be a full 40-character lowercase SHA")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20000:
        raise ValueError("limit must be between 1 and 20000")
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
    cells, invalid, total = [], 0, 0
    if sha:
        filters = [{"term": {"commit_sha": sha}}]
        if object_id:
            filters.append({"term": {"object_id": object_id}})
        # Stay within Elasticsearch's default 10k result window. search_after allows
        # a bounded second page without increasing the cluster result window.
        after, scanned, seen = None, 0, set()
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
    return {"source": "elasticsearch", "fetched_at": datetime.now(timezone.utc).isoformat(),
            "commit_sha": sha, "snapshot_source": selection,
            "snapshot": {"selection": selection, "head": head, "timestamp": stamp},
            "object_id": object_id, "frame": "world", "units": "metres", "cube": cube,
            "cells": cells, "total": total, "returned": len(cells),
            "truncated": total > len(cells) + invalid, "invalid": invalid,
            "provenance": {"kind": "unknown", "detail": "Indexed voxel geometry. The room-voxels mapping does not record whether a physical camera or a synthetic source produced it."}}


@router.get("/api/voxels")
async def voxels(commit_sha: str | None = Query(None, pattern=r"^[0-9a-f]{40}$"),
                 object_id: str | None = Query(None, min_length=1, max_length=128),
                 limit: int = Query(12000, ge=1, le=20000)):
    try:
        return await build(commit_sha, object_id, limit)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return JSONResponse({"error": "voxel_geometry_unavailable", "detail": "Pinned room geometry or indexed snapshot is invalid or unavailable.", "retryable": False}, status_code=503)
    except Exception as exc:
        # Never echo arbitrary exception text: upstream URLs may contain credentials.
        return JSONResponse({"error": getattr(exc, "code", "voxel_upstream_unavailable"),
                             "detail": "Elasticsearch voxel data is currently unavailable.",
                             "retryable": bool(getattr(exc, "retryable", True))},
                            status_code=int(getattr(exc, "status", 503)))
