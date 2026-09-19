"""Scene view: the room's 3D model — every capture's coloured point cloud — turning in a browser tab.

    GET /scene                                    the page (pages/scene.html + scene.js + scene.css)
    GET /api/scene/instances                      the room_live.py instances: commits, last commit, models, which is current
    GET /api/scene/{instance}/captures            newest first: id, time, points, size, pose source, tilt, has_png
    GET /api/scene/{instance}/{capture_id}.ply    the model: binary PLY, ~500k points, 7-8 MB    (capture_id: cap_<n> | latest)
    GET /api/scene/{instance}/{capture_id}.png    the two-view picture room_live.py draws beside it

WHAT IS SERVED. scripts/room_live.py keeps an instance as three siblings under ROOM_LIVE_DIR (~/.cache/gitspace/rooms):
<name>/ the room repo (text, so it diffs), <name>.recordings/<capture_id>/capture.json what the robot said about that
capture, and <name>.scene/<capture_id>.ply|.png the 3D model built from it. This module only READS them: it never
captures, never scans, never writes, and never talks to the robot. The page polls /captures, so a cloud appears in the
browser a few seconds after `room_live.py watch` writes it — that is the whole "live" mechanism, there is no socket.

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
file are never loaded here. It also says whether the file is COMPLETE (header + 15 bytes per point = its size):
room_live.py writes a model in place, so for a moment a new file is short, and the page must not load it then.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
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
CAPTURE = re.compile(r"^(cap_[0-9]+|latest)$")
MODEL_FILE = re.compile(r"^(cap_[0-9]+|latest)\.(ply|png)$")
MEDIA = {"ply": "application/octet-stream", "png": "image/png"}
POINT_BYTES = 15              # float x, y, z + uchar red, green, blue — the one layout room_live.write_scene writes
PRIVATE = {"Cache-Control": "no-store"}       # pixels of a real room: never in a shared cache, never on disk in one


def init(es) -> None:         # the router contract (web/server.py mount_router). Nothing here touches ES.
    return None


def _rooms() -> Path:
    """The rooms directory, exactly as scripts/room_live.py finds it. Read per request, so a test can point it elsewhere."""
    return Path(os.getenv("ROOM_LIVE_DIR", "~/.cache/gitspace/rooms")).expanduser()


def _under_rooms(*parts: str) -> Path:
    """rooms/<parts…>, resolved, and still inside rooms/ — or 404. The patterns already exclude `..` and `/`; this is
    the second lock, and the one that catches a symlink pointing somewhere else."""
    rooms = _rooms().resolve()
    p = rooms.joinpath(*parts).resolve()
    if not p.is_relative_to(rooms):
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
    try:
        doc = json.loads((_under_rooms(f"{instance}.recordings", capture_id) / "capture.json").read_text())
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError, HTTPException):
        return {}


def _models(instance: str) -> list[tuple[Path, os.stat_result]]:
    """cap_<n>.ply files of an instance, newest WRITTEN first (the robot's capture ids restart when it reboots)."""
    scene = _under_rooms(f"{instance}.scene")
    found = []
    for p in (scene.glob("cap_*.ply") if scene.is_dir() else ()):
        if CAPTURE.fullmatch(p.stem):
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
        if not INSTANCE.fullmatch(repo.name):
            continue                          # a name the file routes would refuse is not offered
        subject, _, rest = _git(repo, "log", "-1", "--format=%s%x00%cr%x00%cI").partition("\x00")
        when, _, at = rest.partition("\x00")
        out.append({"name": repo.name, "current": repo.name == current,
                    "commits": int(_git(repo, "rev-list", "--count", "HEAD") or 0),
                    "last_commit": {"subject": subject, "when": when, "at": at} if subject or when else None,
                    "captures": len(_models(repo.name))})
    return {"rooms_dir": str(rooms), "current": current or None, "instances": out}


@router.get("/api/scene/{instance}/captures")
def captures(instance: str) -> dict:
    _instance_or_404(instance)
    out = []
    for ply, st in _models(instance):
        cid = ply.stem
        points, complete = _ply_head(ply, st)
        doc = _capture_json(instance, cid)
        pose, rig = doc.get("pose"), doc.get("rig")
        mount = next(iter(rig.values()), {}).get("mount") if isinstance(rig, dict) and rig else None
        out.append({
            "capture_id": cid, "at": doc.get("at"), "points": points, "complete": complete,
            "size_mb": round(st.st_size / 1e6, 2), "size_bytes": st.st_size, "written_ms": st.st_mtime_ns // 1_000_000,
            "pose_source": doc.get("pose_source"), "tilt_rate_max": doc.get("tilt_rate_max"),
            # where the robot stood, in the cloud's own frame: BB's planar pose is x forward, z LEFT, yaw CCW, so
            # z -> y and nothing flips (perception/fuse.py odom_to_world). Null when the capture did not record one.
            "robot": ({"x": pose.get("x"), "y": pose.get("z"), "yaw": pose.get("yaw")} if isinstance(pose, dict) else None),
            "mount": mount if isinstance(mount, dict) else None,
            "has_png": ply.with_suffix(".png").is_file()})
    # a recording with no model beside it: taken with --no-scene, or the depth step failed for that one
    recs = _under_rooms(f"{instance}.recordings")
    have = {c["capture_id"] for c in out}
    bare = sorted(p.name for p in (recs.glob("cap_*") if recs.is_dir() else ()) if CAPTURE.fullmatch(p.name) and p.name not in have)
    return {"instance": instance, "captures": out, "without_model": bare}


@router.get("/api/scene/{instance}/{file}")
def model_file(instance: str, file: str) -> FileResponse:
    """<capture_id>.ply | <capture_id>.png. One route for both, so there is one place a name is checked."""
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
