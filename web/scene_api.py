"""Scene view: the room's 3D model — the robot's fused map, and every capture's point cloud — turning in a browser tab.

    GET /scene                            the page (pages/scene.html + scene.js + scene.css)
    GET /api/scene/instances              the room_live.py instances: commits, last commit, models, which is current
    GET /api/scene/{instance}/captures    BOTH kinds of model, newest first, each with `kind`: "map" | "capture"
    GET /api/scene/{instance}/{id}.ply    the model: binary PLY, float x y z + uchar r g b     (id: map_<14 digits> | cap_<n> | latest)
    GET /api/scene/{instance}/{id}.json   a MAP's sidecar: the robot's pose, SLAM state, and each object's and wall's box
    GET /api/scene/{instance}/{id}.png    the 2D picture room_live.py draws beside it

WHAT IS SERVED. scripts/room_live.py keeps an instance as siblings under ROOM_LIVE_DIR (~/.cache/gitspace/rooms): <name>/
the room repo (text, so it diffs), <name>.scene/ the 3D models, <name>.recordings/<capture_id>/capture.json what the robot
said about a capture. Two kinds of model live in <name>.scene/:
  map_<YYYYMMDDHHMMSS>.ply|.json|.png   what `room_live.py add` writes: the robot's own FUSED map — ~50,000 voxels of 3 cm in
      the SLAM world frame (z up, floor at 0; the origin is where SLAM started, NOT under the robot). The .json beside it
      carries everything that is not a point: where the robot stood and faced, whether SLAM was localized, the objects.
      A map has no recordings entry: it is not one capture, it is all of them.
  cap_<n>.ply|.png                      one capture's single-view cloud, ~500,000 points, in the ROBOT's frame (x forward, y left).
This module only READS them: it never captures, never scans, never writes, and never talks to the robot. The page polls
/captures, so a model appears in the browser a few seconds after `add` writes it — that is the whole "live" mechanism.

LOCAL ONLY — every route here, the page and the PLY download included. These clouds are made from a camera that sees a
room with people in it: a point cloud with the pixel colours on it IS a picture of them. :8000 binds every interface
and is published to the internet through tunnels, so a route on this site is a public URL unless it says otherwise.
These say otherwise, with the site's one rule (localonly.py; robot_view_api.py states the same one): served only to a
loopback peer whose request carries NO forwarding header — a tunnel's request also arrives "from 127.0.0.1", which is
why the peer address alone proves nothing. Everyone else gets 403 and no points. Responses are no-store.

THE TWO NAMES IN A PATH ARE NOT TRUSTED. `instance` and `capture_id` are matched against a strict pattern before they
touch the filesystem, and the resolved file must still sit under the rooms directory (a symlink out of it is a 404 too).
Anything else — `..`, a slash, an encoded slash, another extension — is a 404, not a 400: what exists is not disclosed.

ONLY THE HEADER IS PARSED. /captures reads the first 2 KB of each PLY for `element vertex N`; half a million points per
file are never loaded here. It also says whether the model is COMPLETE (header + 15 bytes per point = its size, and for a
map the sidecar parses): room_live.py copies the .ply, then the .json, in place — so for a moment a new model is short, or
has no boxes yet, and the page must not load it then.
"""
from __future__ import annotations

import json
import os
import re
import math
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

import localonly


def _local_only(request: Request) -> None:
    """Loopback peer AND no proxy header, or 403 — see the module docstring, and localonly.py for the header list."""
    if not localonly.is_local(request.client.host if request.client else "", request.headers.keys()):
        raise HTTPException(status_code=403, detail="the room's 3D model is served to this laptop only")


router = APIRouter(dependencies=[Depends(_local_only)])

HERE = Path(__file__).resolve().parent
INSTANCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")         # all three are used with fullmatch: `$` alone lets "x\n" through
CAPTURE = re.compile(r"^(cap_[0-9]+|map_[0-9]{14}|latest)$")
MODEL_FILE = re.compile(r"^(cap_[0-9]+|map_[0-9]{14}|latest)\.(ply|png|json)$")
MEDIA = {"ply": "application/octet-stream", "png": "image/png", "json": "application/json"}
SIDECAR_GRACE_S = 10          # a map whose .json has not appeared this long after its .ply is shown without one
POINT_BYTES = 15              # float x, y, z + uchar red, green, blue — the one layout room_live.write_scene writes
PRIVATE = {"Cache-Control": "no-store"}       # pixels of a real room: never in a shared cache, never on disk in one


def init(es) -> None:         # the router contract (web/server.py mount_router). Nothing here touches ES.
    return None


def _rooms() -> Path:
    """The rooms directory, exactly as scripts/room_live.py finds it. Read per request, so a test can point it elsewhere."""
    return Path(os.getenv("ROOM_LIVE_DIR", "~/.cache/gitspace/rooms")).expanduser()


def _inside(*parts: str) -> Path | None:
    """rooms/<parts…>, resolved — or None when that is no longer inside rooms/. The patterns already exclude `..` and
    `/`; this is the second lock, and the one that catches a symlink pointing somewhere else."""
    rooms = _rooms().resolve()
    p = rooms.joinpath(*parts).resolve()
    return p if p.is_relative_to(rooms) else None


def _under_rooms(*parts: str) -> Path:
    p = _inside(*parts)
    if p is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return p


def _instance_or_404(instance: str) -> str:
    if not INSTANCE.fullmatch(instance) or not (_under_rooms(instance) / ".git").is_dir():
        raise HTTPException(status_code=404, detail="Not Found")
    return instance


def _git(repo: Path, *args: str) -> str:
    """One read-only git question about a room repo. '' when git cannot answer (no commits yet, not a repo)."""
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=5,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})     # room_live.py may be committing right now
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ── the PLY header, and nothing after it ──────────────────────────────────────────
_head_cache: dict[str, tuple[tuple[int, int], tuple[int | None, bool]]] = {}


def _ply_head(ply: Path, st: os.stat_result) -> tuple[int | None, bool]:
    """(points, complete) from the text header. Cached on (mtime, size): a finished model never changes, a growing one does."""
    key = (st.st_mtime_ns, st.st_size)
    hit = _head_cache.get(str(ply))
    if hit and hit[0] == key:
        return hit[1]
    points, complete = None, False
    try:
        with open(ply, "rb") as f:
            head = f.read(2048)
        end = head.find(b"end_header\n")
        m = re.search(rb"^element vertex (\d+)[ \r]*$", head[:max(end, 0)], re.M)
        if head.startswith(b"ply\n") and end > 0 and m:
            points = int(m.group(1))
            complete = st.st_size == end + len(b"end_header\n") + points * POINT_BYTES
    except OSError:
        pass
    _head_cache[str(ply)] = (key, (points, complete))
    return points, complete


def _capture_json(instance: str, capture_id: str) -> dict:
    path = _inside(f"{instance}.recordings", capture_id, "capture.json")
    try:
        doc = json.loads(path.read_text()) if path else {}
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _sidecar(ply: Path) -> dict | None:
    """A map's .json, or None while it is absent or half-copied. 2 KB; read on every listing, which is cheaper than being wrong."""
    try:
        doc = json.loads(ply.with_suffix(".json").read_text())
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _models(instance: str) -> list[tuple[Path, os.stat_result]]:
    """map_<stamp>.ply and cap_<n>.ply files of an instance, newest WRITTEN first (the robot's capture ids restart when it
    reboots, so the name is not the order). latest.ply is a copy of one of them and is not listed twice."""
    scene = _inside(f"{instance}.scene")
    found = []
    for p in (scene.glob("*.ply") if scene and scene.is_dir() else ()):
        if p.stem != "latest" and CAPTURE.fullmatch(p.stem) and _inside(f"{instance}.scene", p.name):     # a link out of rooms/ is not listed
            try:
                found.append((p, p.stat()))
            except OSError:
                pass                          # deleted between the glob and the stat
    return sorted(found, key=lambda e: (e[1].st_mtime_ns, e[0].name), reverse=True)


# ── routes ────────────────────────────────────────────────────────────────────────
@router.get("/api/scene/instances")
def instances() -> dict:
    rooms = _rooms()
    try:
        current = (rooms / ".current").read_text().strip()
    except OSError:
        current = ""
    out = []
    for repo in sorted(p for p in (rooms.glob("*") if rooms.is_dir() else ()) if (p / ".git").is_dir()):
        if not INSTANCE.fullmatch(repo.name) or _inside(repo.name) is None:
            continue                          # a name, or a link out of rooms/, that the file routes would refuse is not offered
        subject, _, rest = _git(repo, "log", "-1", "--format=%s%x00%cr%x00%cI").partition("\x00")
        when, _, at = rest.partition("\x00")
        models = _models(repo.name)
        maps = sum(m[0].name.startswith("map_") for m in models)
        out.append({"name": repo.name, "current": repo.name == current,
                    "commits": int(_git(repo, "rev-list", "--count", "HEAD") or 0),
                    "last_commit": {"subject": subject, "when": when, "at": at} if subject or when else None,
                    # newest_ms: so the page can open the instance that HAS a model when the current one has none yet
                    "maps": maps, "captures": len(models) - maps, "models": len(models),
                    "newest_ms": models[0][1].st_mtime_ns // 1_000_000 if models else None})
    return {"rooms_dir": str(rooms), "current": current or None, "instances": out}


@router.get("/api/scene/{instance}/captures")
def captures(instance: str) -> dict:
    _instance_or_404(instance)
    out = []
    for ply, st in _models(instance):
        cid = ply.stem
        points, complete = _ply_head(ply, st)
        common = {"capture_id": cid, "size_mb": round(st.st_size / 1e6, 2), "size_bytes": st.st_size,
                  "written_ms": st.st_mtime_ns // 1_000_000, "has_png": ply.with_suffix(".png").is_file()}
        if cid.startswith("map_"):
            side = _sidecar(ply)
            objs = [o for o in (side or {}).get("objects") or [] if isinstance(o, dict)]
            robot, slam = (side or {}).get("robot"), (side or {}).get("slam")
            h = _num(robot.get("heading_rad")) if isinstance(robot, dict) else None
            out.append({
                **common, "kind": "map", "at": (side or {}).get("at"),
                "points": points if points is not None else (int(v) if (v := _num((side or {}).get("voxels"))) is not None else None),
                # the .json is copied AFTER the .ply: until it parses the map is not ready (it would load with no boxes and
                # never be looked at again). One that never gets a sidecar is still a cloud worth showing, after a grace.
                "complete": complete and (side is not None or time.time() - st.st_mtime > SIDECAR_GRACE_S), "sidecar": side is not None,
                "voxel_m": _num((side or {}).get("voxel_m")), "objects": sum(o.get("kind") == "object" for o in objs),
                "walls": sum(o.get("kind") == "wall" for o in objs),
                "localized": slam.get("localized") if isinstance(slam, dict) else None, "bounds_m": (side or {}).get("bounds_m"),
                # `yaw` is the direction the robot FACES, CCW from +x, like a capture's. bbos's heading h is not that: at h
                # the robot faces (-sin h, cos h) — h = 0 is +y (measured, docs/20 Fact 3) — which is the angle h + pi/2.
                "robot": ({"x": _num(robot.get("x")), "y": _num(robot.get("y")), "yaw": None if h is None else h + math.pi / 2,
                           "heading_rad": h} if isinstance(robot, dict) else None),
                "mount": None})                # not recorded for a map: the page draws the measured 1.59 m / 38 deg head
            continue
        doc = _capture_json(instance, cid)
        pose, rig = doc.get("pose"), doc.get("rig")
        mount = next(iter(rig.values()), {}).get("mount") if isinstance(rig, dict) and rig else None
        out.append({
            **common, "kind": "capture", "at": doc.get("at"), "points": points, "complete": complete,
            "pose_source": doc.get("pose_source"), "tilt_rate_max": doc.get("tilt_rate_max"),
            # where the robot stood, in the cloud's own frame: BB's planar pose is x forward, z LEFT, yaw CCW, so
            # z -> y and nothing flips (perception/fuse.py odom_to_world). Null when the capture did not record one.
            "robot": ({"x": pose.get("x"), "y": pose.get("z"), "yaw": pose.get("yaw")} if isinstance(pose, dict) else None),
            "mount": mount if isinstance(mount, dict) else None})
    # a recording with no model beside it: taken with --no-scene, or the depth step failed for that one
    recs = _inside(f"{instance}.recordings")
    have = {c["capture_id"] for c in out}
    bare = sorted(p.name for p in (recs.glob("cap_*") if recs and recs.is_dir() else ()) if CAPTURE.fullmatch(p.name) and p.name not in have)
    # (a map never has a recording: it is the robot's fusion of everything it has seen, not one capture)
    try:                                      # .current rides along, so a page following `add` notices within one poll that it moved
        current = (_rooms() / ".current").read_text().strip() or None
    except OSError:
        current = None
    return {"instance": instance, "current": current, "captures": out, "without_model": bare}


@router.get("/api/scene/{instance}/{file}")
def model_file(instance: str, file: str) -> FileResponse:
    """<id>.ply | <id>.json | <id>.png. One route for all three, so there is one place a name is checked."""
    m = MODEL_FILE.fullmatch(file)
    if not m:
        raise HTTPException(status_code=404, detail="Not Found")
    _instance_or_404(instance)
    path = _under_rooms(f"{instance}.scene", file)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(path, media_type=MEDIA[m.group(2)], headers=PRIVATE)


@router.get("/scene", include_in_schema=False)
def scene_page() -> FileResponse:
    return FileResponse(HERE / "pages" / "scene.html", headers={"Cache-Control": "no-cache"})
