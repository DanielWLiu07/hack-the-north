"""Scene view: the room's 3D model — the robot's fused map, and every capture's point cloud — turning in a browser tab.

    GET /scene                            the page (pages/scene.html + scene.js + scene.css)
    GET /api/scene/instances              the room_live.py instances: commits, last commit, models, which is current
    GET /api/scene/{instance}/captures    BOTH kinds of model, newest first, each with `kind`: "map" | "capture"
    GET /api/scene/{instance}/history         time as a node graph (+ parents, refs, branches, dirty): complete cap_*.ply when they exist, else git log
    GET /api/scene/{instance}/history/{sha}.ply|.json   that commit's cloud/current.ply (or its sidecar)
    GET /api/scene/{instance}/diff?a=&b=  the OBJECT diff of two nodes (objdiff.py) — the same two shas as the clouds
    POST /api/scene/{instance}/add        `git add` for the room: capture the robot's CURRENT fused map as a new commit
    POST /api/scene/{instance}/branch     a new branch at a node. HEAD does not move; nothing in the room changes
    POST /api/scene/{instance}/checkout   move HEAD to a branch or commit. Refused on a dirty tree, or mid-add

THERE IS NO MERGE ENDPOINT, and there must not be one. roomctl puts `merge`, `cherry-pick` and `stash` in
WRITE_VERBS and exits 2 (roomctl/cli.py); this file agrees with the CLI. Two people's answers to "where does
this belong" go through a pull request, never an automatic merge of a physical room.
    GET /api/scene/{instance}/{id}.ply    the model: binary PLY, float x y z + uchar r g b     (id: map_<14 digits> | cap_<n> | latest)
    GET /api/scene/{instance}/{id}.json   a MAP's sidecar: the robot's pose, SLAM state, and each object's and wall's box
    GET /api/scene/{instance}/{id}.dense.ply   a MAP's dense layer, when it has one: the camera's own points in the map frame
    GET /api/scene/{instance}/{id}.png    the 2D picture room_live.py draws beside it

WHAT IS SERVED. scripts/room_live.py keeps an instance as siblings under ROOM_LIVE_DIR (~/.cache/gitspace/rooms): <name>/
the room repo (text, so it diffs), <name>.scene/ the 3D models, <name>.recordings/<capture_id>/capture.json what the robot
said about a capture. Two kinds of model live in <name>.scene/:
  map_<YYYYMMDDHHMMSS>.ply|.json|.png   what `room_live.py add` writes: the robot's own FUSED map — ~50,000 voxels of 3 cm in
      the SLAM world frame (z up, floor at 0; the origin is where SLAM started, NOT under the robot). The .json beside it
      carries everything that is not a point: where the robot stood and faced, whether SLAM was localized, the objects,
      and (when the floor pass ran) the small things it found standing on the floor. A map has no recordings entry: it
      is not one capture, it is all of them.
  map_<stamp>.dense.ply                 optional, newer snapshots only: the stereo camera's points (~120,000 at 1 cm) placed
      in the map frame — finer than the voxels where the camera looked. Absent = no layer, nothing wrong.
  cap_<n>.ply|.png                      one capture's single-view cloud, ~500,000 points, in the ROBOT's frame (x forward, y left).
This module READS those files, and has one write: POST /add, which runs `scripts/room_live.py add` on this laptop so
`/robot` can `git add` the room as it is now. Every GET never captures, never scans, never writes, and never talks to
the robot. The page polls /history (and /captures), so a model appears in the browser a few seconds after `add` writes
it — that is the whole "live" mechanism.

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

NEVER FORK. These endpoints are plain `def`, so FastAPI runs them in a worker thread, and /robot polls two of
them every 5 s. fork() from a thread can deadlock the child between fork and exec (it copies one thread, and the
malloc lock may be held by another), and a stuck child keeps a share of the listening socket — the site then
answers a fraction of its requests. So every call here uses room.GIT (absolute, and the real binary rather than
the /usr/bin/git xcrun shim: 41 ms a call instead of 80) with room.SPAWN, and passes `-C <repo>` instead of cwd=.
See web/room.py for the full conditions and web/tests/test_no_fork.py for the proof.
"""
from __future__ import annotations

import json
import os
import re
import math
import subprocess

from room import GIT, SPAWN   # the REAL git (not the xcrun shim) and the flags that keep CPython on posix_spawn
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response

import localonly
import objdiff


def _local_only(request: Request) -> None:
    """Loopback peer AND no proxy header, or 403 — see the module docstring, and localonly.py for the header list.

    SCENE_PUBLIC=1 lifts it, for a deployment the owner has decided should serve the 3D model to
    anyone. It is OFF by default and must stay off on this laptop: these clouds are built from the
    robot's cameras in a room people walk through, so publishing them is the owner's call about
    other people, not a configuration detail. Writing endpoints stay local-only whatever this says
    (a public visitor may look; only this laptop may `add`)."""
    if os.getenv("SCENE_PUBLIC") == "1":
        return
    if not localonly.is_local(request.client.host if request.client else "", request.headers.keys()):
        raise HTTPException(status_code=403, detail="the room's 3D model is served to this laptop only")


def _local_write(request: Request) -> None:
    """The same rule, never lifted: SCENE_PUBLIC opens reading, never writing."""
    if not localonly.is_local(request.client.host if request.client else "", request.headers.keys()):
        raise HTTPException(status_code=403, detail="only this laptop may change the room's 3D model")


router = APIRouter(dependencies=[Depends(_local_only)])

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ADD_LOCK = threading.Lock()
ADD_TIMEOUT_S = 180
INSTANCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")         # all three are used with fullmatch: `$` alone lets "x\n" through
CAPTURE = re.compile(r"^(cap_[0-9]+|map_[0-9]{14}|latest)$")
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
BRANCH = re.compile(r"^(?!-)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]{1,64}$")   # a ref this file is willing to make or move
CAPTURE_ID = re.compile(r"cap_[0-9]+")
MODEL_FILE = re.compile(r"^(cap_[0-9]+|map_[0-9]{14}|latest)\.(ply|dense\.ply|png|json)$")
MEDIA = {"ply": "application/octet-stream", "dense.ply": "application/octet-stream", "png": "image/png", "json": "application/json"}
SIDECAR_GRACE_S = 10          # a map whose .json has not appeared this long after its .ply is shown without one
POINT_BYTES = 15              # float x, y, z + uchar red, green, blue — the one layout room_live.write_scene writes
PRIVATE = {"Cache-Control": "no-store"}       # pixels of a real room: never in a shared cache, never on disk in one
# every answer carrying a pose says its frame and units, like graph_api and the bridge do
FRAME = "world_z_up"
UNITS = {"position": "m", "yaw": "deg"}


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


def _git(repo: Path, *args: str, timeout: int = 5) -> str:
    """One read-only git question about a room repo. '' when git cannot answer (no commits yet, not a repo)."""
    try:
        r = subprocess.run([GIT, "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout, **SPAWN,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})     # room_live.py may be committing right now
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _head(repo: Path) -> str:
    """The commit HEAD names, read from .git by hand: a file read is microseconds, a `git` process is seconds when this
    laptop is busy (the perception pipelines load it to 50), and /instances is asked every few seconds by the page."""
    try:
        ref = (repo / ".git" / "HEAD").read_text().strip()
        if not ref.startswith("ref: "):
            return ref
        name = ref[5:]
        loose = repo / ".git" / name
        if loose.is_file():
            return loose.read_text().strip()
        for line in (repo / ".git" / "packed-refs").read_text().splitlines():
            if line.endswith(" " + name):
                return line.split(" ", 1)[0]
    except OSError:
        pass
    return ""


_git_cache: dict[str, tuple[str, dict]] = {}     # repo -> (the HEAD it was true for, {commits, subject, at})


def _git_facts(repo: Path) -> dict:
    """Commit count and the last commit — asked of git once per HEAD, then remembered: a room repo only changes when
    room_live.py commits, and that moves HEAD. Measured: with the laptop at load 50, /instances took 7-11 s asking git
    every time (four processes), 0.1 s from this."""
    sha = _head(repo)
    hit = _git_cache.get(str(repo))
    if hit and sha and hit[0] == sha:
        return hit[1]
    subject, _, at = _git(repo, "log", "-1", "--format=%s%x00%cI").partition("\x00")
    facts = {"commits": int(_git(repo, "rev-list", "--count", "HEAD") or 0), "subject": subject, "at": at}
    if sha:
        _git_cache[str(repo)] = (sha, facts)
    return facts


def _ago(iso: str) -> str:
    """`git log --format=%cr` without asking git: "34 seconds ago", "12 minutes ago", "3 hours ago", "2 days ago"."""
    try:
        s = max(0.0, time.time() - datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return ""
    for unit, per in (("second", 1), ("minute", 60), ("hour", 3600), ("day", 86400)):
        if s < per * (60 if unit == "second" else 60 if unit == "minute" else 24 if unit == "hour" else 1e9):
            n = int(s // per)
            return f"{n} {unit}{'' if n == 1 else 's'} ago"
    return ""


def _git_bytes(repo: Path, *args: str, timeout: int = 15) -> bytes | None:
    """Binary `git show` of a blob. None when git cannot answer — never a partial file."""
    try:
        r = subprocess.run([GIT, "-C", str(repo), *args], capture_output=True, timeout=timeout, **SPAWN,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _has_blob(repo: Path, spec: str) -> bool:
    """True when that tree-ish path exists. `git cat-file -e` does not print the bytes."""
    try:
        r = subprocess.run([GIT, "-C", str(repo), "cat-file", "-e", spec], capture_output=True, timeout=5, **SPAWN,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _commit_or_404(instance: str, sha: str) -> tuple[Path, str]:
    """Resolve sha to a full commit id inside this instance. The name is checked before it touches git."""
    instance = _instance_or_404(instance)
    if not COMMIT.fullmatch(sha):
        raise HTTPException(status_code=404, detail="Not Found")
    repo = _under_rooms(instance)
    full = _git(repo, "rev-parse", "--verify", f"{sha}^{{commit}}")
    if not full:
        raise HTTPException(status_code=404, detail="Not Found")
    return repo, full


def _robot_from_meta(doc: dict) -> dict | None:
    r = doc.get("robot")
    if isinstance(r, dict) and isinstance(r.get("x"), (int, float)):
        return {"x": r.get("x"), "y": r.get("y"), "heading_rad": r.get("heading_rad")}
    p = doc.get("pose")
    if isinstance(p, dict) and isinstance(p.get("x"), (int, float)):
        yaw = p.get("yaw")
        heading = None if not isinstance(yaw, (int, float)) else yaw - math.pi / 2  # yaw faces +x; bbos heading 0 faces +y
        return {"x": p.get("x"), "y": p.get("z"), "heading_rad": heading}
    return None


def _cloud_meta(repo: Path, sha: str) -> dict | None:
    raw = _git(repo, "show", f"{sha}:cloud/current.json")
    if not raw:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    cap = doc.get("capture_id") or doc.get("capture")
    if not isinstance(cap, str):
        found = CAPTURE_ID.search(_git(repo, "log", "-1", "--format=%s", sha) or "")
        cap = found.group(0) if found else None
    return {"capture_id": cap if isinstance(cap, str) else None, "at": doc.get("at"),
            "points": doc.get("points") or doc.get("voxels"), "robot": _robot_from_meta(doc)}


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
        facts = _git_facts(repo)
        models = _models(repo.name)
        maps = sum(m[0].name.startswith("map_") for m in models)
        out.append({"name": repo.name, "current": repo.name == current, "commits": facts["commits"],
                    "last_commit": {"subject": facts["subject"], "when": _ago(facts["at"]), "at": facts["at"]} if facts["at"] else None,
                    # newest_ms: so the page can open the instance that HAS a model when the current one has none yet
                    "maps": maps, "captures": len(models) - maps, "models": len(models),
                    "newest_ms": models[0][1].st_mtime_ns // 1_000_000 if models else None})
    return {"rooms_dir": str(rooms), "current": current or None, "instances": out}


def describe_capture(capture_id: str) -> dict:
    """Where this capture's 3D model lives, or why it does not. Filesystem only — no HTTP, no
    local-only check (the caller decides who may see the bytes). A missing model is said out loud."""
    empty = {"available": False, "reason": "no point cloud was written for this capture",
             "local_only": True, "page": "/scene", "instance": None, "ply": None, "png": None}
    if not re.fullmatch(r"cap_[0-9]+", capture_id or ""):
        return {**empty, "reason": "not a capture id"}
    rooms = _rooms()
    try:
        current = (rooms / ".current").read_text().strip() or None
    except OSError:
        current = None
    names = []
    if current and INSTANCE.fullmatch(current):
        names.append(current)
    for repo in sorted(p for p in (rooms.glob("*") if rooms.is_dir() else ()) if (p / ".git").is_dir()):
        if INSTANCE.fullmatch(repo.name) and repo.name not in names and _inside(repo.name) is not None:
            names.append(repo.name)
    for name in names:
        ply = _inside(f"{name}.scene", f"{capture_id}.ply")
        if ply is None or not ply.is_file():
            continue
        try:
            st = ply.stat()
        except OSError:
            continue
        points, complete = _ply_head(ply, st)
        has_png = ply.with_suffix(".png").is_file()
        doc = _capture_json(name, capture_id)
        pose = doc.get("pose") if isinstance(doc, dict) else None
        return {
            "available": bool(complete),
            "reason": None if complete else "the point cloud is still being written",
            "local_only": True, "instance": name, "kind": "capture",
            "points": points, "size_mb": round(st.st_size / 1e6, 2), "size_bytes": st.st_size,
            "complete": complete, "has_png": has_png,
            "ply": f"/api/scene/{name}/{capture_id}.ply",
            "png": f"/api/scene/{name}/{capture_id}.png" if has_png else None,
            "page": f"/scene?instance={name}&capture={capture_id}",
            "tilt_rate_max": _num(doc.get("tilt_rate_max")) if isinstance(doc, dict) else None,
            "robot": ({"x": pose.get("x"), "y": pose.get("z"), "yaw": pose.get("yaw")}
                      if isinstance(pose, dict) else None),
        }
    return empty


@router.get("/api/scene/find/{capture_id}")
def find_capture(capture_id: str) -> dict:
    """Which instance holds this capture's PLY. 404 when the id is not a capture id; a missing
    model is 200 with available: false (nothing to hide — the capture page says so)."""
    if not re.fullmatch(r"cap_[0-9]+", capture_id or ""):
        raise HTTPException(status_code=404, detail="Not Found")
    return describe_capture(capture_id)


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
            dense_points = None                      # the dense layer's count, once that file is whole; None = no layer (yet)
            try:
                dense = ply.with_name(f"{cid}.dense.ply")
                dst = dense.stat()
                dn, dok = _ply_head(dense, dst)
                dense_points = dn if dok else None
            except OSError:
                pass
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
                "dense_points": dense_points,
                "floor_objects": sum(isinstance(o, dict) for o in ((side or {}).get("floor_objects") or [])),
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


def _parse_refs(decoration: str) -> list[dict]:
    """`%D` -> the labels on a commit. origin/* is dropped by the caller: it repeats the local branches."""
    refs = []
    for raw in filter(None, (r.strip() for r in decoration.split(","))):
        head = raw.startswith("HEAD -> ")
        name = raw[8:] if head else raw
        if name == "HEAD":
            refs.append({"name": "HEAD", "kind": "head", "head": True})
        elif name.startswith("tag: "):
            refs.append({"name": name[5:], "kind": "tag", "head": False})
        else:
            refs.append({"name": name, "kind": "remote" if "/" in name else "branch", "head": head})
    return refs


def _branches(repo: Path) -> list[str]:
    return [b for b in (_git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads") or "").splitlines() if b]


def _git_commits(instance: str) -> tuple[str, list[dict]]:
    """--all, so a branch that is not HEAD's ancestor is in the graph at all; --date-order, because a
    railroad lane walk is only correct when a parent never comes before one of its children."""
    repo = _under_rooms(instance)
    head = _git(repo, "rev-parse", "HEAD")
    log = _git(repo, "log", "--all", "--date-order", "-80", "--format=%H%x1f%P%x1f%cI%x1f%s%x1f%D")
    commits = []
    for line in (log.split("\n") if log else []):
        sha, parents, at, subject, deco = (line.split("\x1f", 4) + ["", "", "", "", ""])[:5]
        if not COMMIT.fullmatch(sha):
            continue
        has_cloud = _has_blob(repo, f"{sha}:cloud/current.ply")
        meta = _cloud_meta(repo, sha) if has_cloud else None
        found = CAPTURE_ID.search(subject)
        commits.append({
            "id": sha, "sha": sha, "parents": [p for p in parents.split() if COMMIT.fullmatch(p)],
            "at": at or (meta or {}).get("at"), "subject": subject, "head": sha == head,
            "refs": [r for r in _parse_refs(deco) if r["kind"] != "remote"],
            "cloud": has_cloud, "kind": "commit", "file": None,
            "capture_id": (meta or {}).get("capture_id") or (found.group(0) if found else None),
            "points": (meta or {}).get("points"), "robot": (meta or {}).get("robot"),
        })
    return head, commits


def _robot_from_capture(doc: dict) -> dict | None:
    pose = doc.get("pose") if isinstance(doc, dict) else None
    if not isinstance(pose, dict) or not isinstance(pose.get("x"), (int, float)):
        return None
    yaw = pose.get("yaw") if isinstance(pose.get("yaw"), (int, float)) else 0
    return {"x": pose.get("x"), "y": pose.get("z"), "yaw": yaw, "heading_rad": yaw}


def _cap_parents(sha: str, by_sha: dict[str, dict], cap_of: dict[str, str]) -> list[str]:
    """The nearest ancestors of `sha` that are themselves capture nodes, nearest first.

    Git is the topology, not the order the .ply files happened to be written. A commit with no
    capture of its own — a branch point, a settings change — is walked THROUGH, so a capture taken
    on a second branch hangs off the capture it really came after, and the rail draws a fork."""
    out: list[str] = []
    seen = {sha}
    queue = list(by_sha.get(sha, {}).get("parents", []))
    while queue:
        p = queue.pop(0)
        if p in seen:
            continue
        seen.add(p)
        if p in cap_of:
            if cap_of[p] not in out:
                out.append(cap_of[p])
            continue                      # stop at the first capture on this line: it is the parent NODE
        queue.extend(by_sha.get(p, {}).get("parents", []))
    return out


def _capture_nodes(instance: str, git_commits: list[dict]) -> list[dict] | None:
    """The detailed stereo captures in <instance>.scene/ — these are the nodes you walk back and forth in time."""
    rows = []
    for ply, st in _models(instance):
        if not ply.stem.startswith("cap_"):
            continue
        points, complete = _ply_head(ply, st)
        if not complete:
            continue
        doc = _capture_json(instance, ply.stem)
        rows.append({"capture_id": ply.stem, "points": points, "at": doc.get("at"),
                     "robot": _robot_from_capture(doc)})
    if not rows:
        return None
    # NEWEST wins: a capture can be committed more than once (the snapshot, then a rescan that finds
    # its objects), and the node should show the latest thing git says about it. A plain dict
    # comprehension over a newest-first list keeps the OLDEST, which pinned cap_0018 to the commit
    # that held no objects while the rescan sat invisible.
    by_cap: dict[str, dict] = {}
    for c in git_commits:
        if c.get("capture_id"):
            by_cap.setdefault(c["capture_id"], c)
    by_sha = {c["sha"]: c for c in git_commits}
    mine = {r["capture_id"] for r in rows}
    cap_of = {c["sha"]: c["capture_id"] for c in git_commits if c.get("capture_id") in mine}
    nodes = []
    for i, row in enumerate(rows):
        g = by_cap.get(row["capture_id"]) or {}
        older = rows[i + 1]["capture_id"] if i + 1 < len(rows) else None
        sha = g.get("sha")
        # git's parents when this capture IS a commit; the next file back only when git has nothing to say
        parents = _cap_parents(sha, by_sha, cap_of) if sha else []
        nodes.append({
            "id": row["capture_id"], "sha": sha or row["capture_id"], "commit_sha": sha,
            "parents": parents or ([older] if older and not sha else []),
            "parents_from": "git" if parents else ("file order" if older and not sha else "root"),
            "refs": g.get("refs") or [],
            "at": row["at"] or g.get("at"), "subject": g.get("subject") or row["capture_id"],
            "head": bool(g.get("head")) if sha else False, "cloud": True, "kind": "capture",
            "file": f"{row['capture_id']}.ply",
            "capture_id": row["capture_id"], "points": row["points"],
            "robot": row["robot"] or g.get("robot"),
        })
    # The head of the TIME graph is the newest complete capture — also when git HEAD sits on an older one (a capture
    # taken but not yet committed: the robot is ahead of the repo). The page follows this node and compares `head`
    # against it, so exactly one node carries the flag. git HEAD itself stays visible: `head_sha`, and the
    # "HEAD -> branch" ref chip on its own node.
    for n in nodes:
        n["head"] = False
    nodes[0]["head"] = True
    return nodes


@router.get("/api/scene/{instance}/history")
def history(instance: str) -> dict:
    """Time as a node graph. Prefer the detailed capture PLYs in .scene/; fall back to git log of cloud/current.ply.

    Either way a node carries `parents` and `refs`, so /robot lays the nodes out with the standard
    railroad lane walk and a branch is a lane, not a surprise. `branch` is the branch HEAD is on
    (None when HEAD is detached), and `branches` is every branch this repo has."""
    instance = _instance_or_404(instance)
    repo = _under_rooms(instance)
    head, commits = _git_commits(instance)
    branches = _branches(repo)
    on = _git(repo, "symbolic-ref", "--short", "-q", "HEAD") or None
    captures = _capture_nodes(instance, commits)
    nodes = captures if captures else commits
    lead = next((n for n in nodes if n.get("head")), nodes[0] if nodes else None)
    return {"instance": instance, "head": (lead or {}).get("id") if captures else (head or None),
            "head_sha": head or None, "branch": on, "branches": branches, "detached": on is None,
            # uncommitted work in the room: a checkout would throw it away, so the page says so BEFORE offering one
            "dirty": _dirty(repo),
            "kind": "captures" if captures else "commits",
            "nodes_are": "capture point clouds in .scene/" if captures else "commits of cloud/current.ply",
            "commits": nodes}


# ── the object diff: the SAME two shas as the cloud diff ──────────────────────────
# One commit is one point cloud AND the objects found in it, so a pair of nodes answers both
# questions from one place. The diff itself is objdiff.py — the implementation room.git's own
# graph uses, not a second one written for this page.
@router.get("/api/scene/{instance}/diff")
def diff(instance: str, a: str, b: str) -> dict:
    instance = _instance_or_404(instance)
    repo, sa = _commit_or_404(instance, a)
    _, sb = _commit_or_404(instance, b)
    git = lambda *args: _git(repo, *args)                                      # noqa: E731
    show = lambda spec: _git(repo, "show", spec)                               # noqa: E731
    rows = objdiff.ops(git, show, sa, sb)
    at_a, at_b = objdiff.objects_at(git, show, sa), objdiff.objects_at(git, show, sb)
    return {"instance": instance, "a": sa, "b": sb, "ops": rows, "summary": objdiff.summary(rows),
            "objects": {"a": len(at_a), "b": len(at_b)},
            "at": {"a": _git(repo, "log", "-1", "--format=%cI", sa), "b": _git(repo, "log", "-1", "--format=%cI", sb)},
            "subject": {"a": _git(repo, "log", "-1", "--format=%s", sa), "b": _git(repo, "log", "-1", "--format=%s", sb)},
            # the same pair, as clouds: the page fetches these itself to diff the points
            "cloud": {"a": f"/api/scene/{instance}/history/{sa}.ply" if _has_blob(repo, f"{sa}:cloud/current.ply") else None,
                      "b": f"/api/scene/{instance}/history/{sb}.ply" if _has_blob(repo, f"{sb}:cloud/current.ply") else None},
            "frame": FRAME, "units": UNITS}


# ── the two writes that move a ref. THERE IS NO MERGE HERE, and there must not be. ────────
# `merge`, `cherry-pick` and `stash` sit in roomctl's WRITE_VERBS and exit 2 (roomctl/cli.py);
# this file agrees with the CLI. A branch is a second line of snapshots of one room — two
# people's answers to "where does this belong" are a pull request, never an automatic merge.
def _git_write(repo: Path, *args: str, timeout: int = 10) -> tuple[int, str]:
    """A git that is allowed to change a ref. Returns (code, the message to show)."""
    try:
        r = subprocess.run([GIT, "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout,
                           **SPAWN, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, f"git {args[0]} did not finish ({type(e).__name__})"
    return r.returncode, ((r.stderr or "") + (r.stdout or "")).strip().splitlines()[0][:200] if r.returncode else ""


def _dirty(repo: Path) -> bool:
    return bool(_git(repo, "status", "--porcelain=v1", "--untracked-files=no"))


def _ref_or_400(name: str) -> str:
    if not BRANCH.fullmatch(name or "") or name.endswith(".lock") or "@{" in name:
        raise HTTPException(status_code=400, detail="a branch name is letters, digits, . _ - / — no spaces, no ..")
    return name


@router.post("/api/scene/{instance}/branch", status_code=201, dependencies=[Depends(_local_write)])
def branch(instance: str, body: dict) -> dict:
    """A new branch at a commit. HEAD does not move and no file changes: a branch is a NAME for a
    node you can take the room's story on from. Nothing is captured and nothing is merged."""
    instance = _instance_or_404(instance)
    repo = _under_rooms(instance)
    name = _ref_or_400(str(body.get("name") or ""))
    at = str(body.get("at") or "HEAD")
    if at != "HEAD" and not COMMIT.fullmatch(at):       # checked before git ever sees it
        raise HTTPException(status_code=404, detail="Not Found")
    sha = _git(repo, "rev-parse", "--verify", f"{at}^{{commit}}")
    if not sha:
        raise HTTPException(status_code=404, detail="Not Found")
    if name in _branches(repo):
        raise HTTPException(status_code=409, detail=f"{name} already exists here, on {_git(repo, 'rev-parse', '--short', name)}")
    code, why = _git_write(repo, "branch", "--", name, sha)
    if code:
        raise HTTPException(status_code=409, detail=why or "git refused to make that branch")
    return {"instance": instance, "branch": name, "at": sha, "head_moved": False,
            "detail": f"{name} now names {sha[:7]}. HEAD is still {_git(repo, 'symbolic-ref', '--short', '-q', 'HEAD') or sha[:7]}; nothing in the room changed."}


@router.post("/api/scene/{instance}/checkout", dependencies=[Depends(_local_write)])
def checkout(instance: str, body: dict) -> dict:
    """Move HEAD to a branch (or onto a commit). This rewrites the working tree — cloud/current.ply
    and zones/ become that node's — so it is refused while there is uncommitted work, and while an
    `add` is capturing."""
    instance = _instance_or_404(instance)
    repo = _under_rooms(instance)
    ref = str(body.get("ref") or "")
    if not (COMMIT.fullmatch(ref) or BRANCH.fullmatch(ref)) or "@{" in ref:
        raise HTTPException(status_code=400, detail="ref must be a branch name or a commit sha")
    if not _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}"):
        raise HTTPException(status_code=404, detail="Not Found")
    if not ADD_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="an add is capturing the room: try again when it lands")
    try:
        if _dirty(repo):
            raise HTTPException(status_code=409, detail="this room has uncommitted changes — checkout would throw them away")
        code, why = _git_write(repo, "checkout", ref, "--")     # the trailing -- : ref is a REF, never a path
        if code:
            raise HTTPException(status_code=409, detail=why or "git refused that checkout")
    finally:
        ADD_LOCK.release()
    _git_cache.pop(str(repo), None)                      # HEAD moved: /instances must not answer from the old one
    on = _git(repo, "symbolic-ref", "--short", "-q", "HEAD") or None
    sha = _git(repo, "rev-parse", "HEAD")
    return {"instance": instance, "ref": ref, "branch": on, "head": sha or None, "detached": on is None,
            "detail": f"HEAD is {on or sha[:7]}. The room's files are that node's now; nothing was merged."}


def _history_blob(instance: str, sha: str, kind: str) -> Response:
    """The point cloud (or its json sidecar) AS IT WAS in that commit. sha is matched before git sees it."""
    repo, full = _commit_or_404(instance, sha)
    spec = f"{full}:cloud/current.{kind}"
    if kind == "json":
        text = _git(repo, "show", spec)
        if not text:
            raise HTTPException(status_code=404, detail="Not Found")
        return Response(content=text, media_type="application/json", headers=PRIVATE)
    blob = _git_bytes(repo, "show", spec)
    if not blob:
        raise HTTPException(status_code=404, detail="Not Found")
    return Response(content=blob, media_type="application/octet-stream", headers=PRIVATE)


@router.get("/api/scene/{instance}/history/{sha}.ply")
def history_ply(instance: str, sha: str) -> Response:
    return _history_blob(instance, sha, "ply")


@router.get("/api/scene/{instance}/history/{sha}.json")
def history_json(instance: str, sha: str) -> Response:
    return _history_blob(instance, sha, "json")


def _add_detail(out: str, code: int) -> str:
    """The real reason add failed — not Sentry's 'Press Ctrl-C to quit' footer after a flush."""
    skip = ("press ctrl-c", "waiting up to", "sentry is attempting", "uvicorn")
    lines = [ln.strip() for ln in out.splitlines() if ln.strip() and not any(s in ln.lower() for s in skip)]
    for ln in reversed(lines):
        if ln.startswith("[") or ln.startswith("sentry trace"):
            continue
        return ln
    return f"add exited {code}"


def _run_add(instance: str, message: str) -> dict:
    """`scripts/room_live.py add`: pull the robot's CURRENT fused map, commit it, fill the scene. Tests replace this."""
    repo = _under_rooms(instance)
    before = _git(repo, "rev-parse", "HEAD")
    n_map = sum(1 for p, _ in _models(instance) if p.stem.startswith("map_"))
    n_cap = sum(1 for p, _ in _models(instance) if p.stem.startswith("cap_"))
    verb = "snapshot" if n_cap > n_map else "add"
    cmd = [sys.executable, str(ROOT / "scripts" / "room_live.py"), verb, "-m", message, instance]
    env = {**os.environ, "ROOM_LIVE_DIR": str(_rooms()), "SENTRY_DSN": ""}  # this process already reports; the child must not stall on flush
    try:
        # no cwd=: it forces the fork path, and room_live.py takes its root from __file__, not the working directory
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=ADD_TIMEOUT_S, env=env, **SPAWN)
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail="the robot did not finish capturing in time") from e
    after = _git(repo, "rev-parse", "HEAD")
    out = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
    if r.returncode != 0 and after == before:
        if "nothing was committed" in out:
            return {"instance": instance, "committed": False, "sha": after or None, "head": after or None,
                    "subject": _git(repo, "log", "-1", "--format=%s") or None, "detail": "the map has not changed"}
        raise HTTPException(status_code=502, detail=_add_detail(out, r.returncode))
    return {"instance": instance, "committed": after != before, "sha": after or None, "head": after or None,
            "subject": _git(repo, "log", "-1", "--format=%s") or None}


@router.post("/api/scene/{instance}/add", dependencies=[Depends(_local_write)])
def add_current(instance: str) -> dict:
    """git add the room as it is now: capture the robot's current fused map as a new commit on this instance."""
    instance = _instance_or_404(instance)
    if not ADD_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="an add is already capturing the room")
    try:
        return _run_add(instance, "current")
    finally:
        ADD_LOCK.release()


@router.get("/api/scene/{instance}/{file}")
def model_file(instance: str, file: str) -> FileResponse:
    """<id>.ply | <id>.dense.ply | <id>.json | <id>.png. One route for all, so there is one place a name is checked."""
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
