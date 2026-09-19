"""roommate_api.py — the caretaker-roommate dashboard's API (plan/roommate/03-interfaces.md §8).

    GET  /api/room/ci                 is the room at `main`?  {state, since, head, branch, changes, heartbeat}
    GET  /api/blame/{object_id}       who moved it, and when: the commit that last changed it, from -> to, its capture
    GET  /api/chores                  what the roommate could not put back itself (Tier B)
    GET  /api/prs · POST /api/prs     a change you MEANT goes through a pull request, not a tidy-up
    POST /api/prs/{id}/approve        -> {merge_sha, job_id}
    GET  /api/nav/snapshot            the robot on its map, in the ROOM frame

What is real today, and what is waiting
  * /api/room/ci   REAL: git's own working tree of room.git (room.snapshot()). `since` and `heartbeat` belong to the
                   watch loop, which has not landed: they are null and the answer says why — never a made-up time.
  * /api/blame     REAL: `git log` for the object's file, that commit's own from -> to, and the capture that made it
                   (room-events). `frame_url` is null until a capture frame is stored anywhere (frames are not indexed).
  * chores, prs, nav   their owners (roomctl's watch loop / PR store, the robot's nav bridge) do not exist yet. Reads
                   answer an EMPTY LIST — true: there are none — with `X-Roommate-Backend: not_connected`; writes and
                   the nav snapshot answer 503 `not_connected` and say what is missing. Nothing here invents a chore,
                   a PR or a robot.
Every pose is the room frame: world_z_up, metres (docs/20). Reads only; this module never writes room.git.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

import room
import store

OBJECT_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
FRAME = "world_z_up"
HEARTBEAT_SLUG = "room-clean"
NOT_CONNECTED = {"X-Roommate-Backend": "not_connected"}

router = APIRouter()


def init(es) -> None:
    store.init(es)


def _error(code: str, detail: str, status: int, retryable: bool = False) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail, "retryable": retryable}, status_code=status)


# ── is the room at main? ─────────────────────────────────────────────────────────────────────
def _beat() -> dict | None:
    """The room-clean badge's last verdict (telemetry/room_clean.py writes it on every tick, switch on or off)."""
    try:
        from telemetry.room_clean import read_state
        return read_state()
    except Exception:  # noqa: BLE001 — no feeder yet is an honest "unknown", never an error
        return None


def ci_from(snap: dict) -> dict:
    """room.snapshot() -> the CI answer. A merge in progress is its own state: two roommates, one lamp."""
    state = "conflict" if snap.get("conflicts") or snap.get("merging") else "clean" if snap.get("clean") else "dirty"
    b = _beat()
    return {"state": state, "branch": snap.get("branch"), "head": snap.get("head"), "changes": snap.get("changes") or [],
            "conflicts": snap.get("conflicts") or [], "last_capture": snap.get("last_capture"),
            # the badge's feeder owns these two: when the state last flipped, and the room-clean check-in it SENT
            # (`at` stays null while ROOM_CLEAN_CRON is off: the verdict is recorded, nothing is sent)
            "since": (b or {}).get("since"),
            "heartbeat": {"slug": HEARTBEAT_SLUG, "last": (b or {}).get("last"), "at": (b or {}).get("at")},
            "source": ("git working tree; badge fed by " + b.get("source", "?") +
                       ("" if b.get("enabled") else " (recorded only: ROOM_CLEAN_CRON is off)")) if b else
                      "git working tree — the watch loop has not reported yet, so `since` and `heartbeat` are unknown",
            "frame": FRAME}


@router.get("/api/room/ci")
async def room_ci():
    try:
        return ci_from(await asyncio.to_thread(room.snapshot))
    except room.RoomError as e:
        return JSONResponse({"state": "unknown", "detail": str(e), "changes": [], "since": None,
                             "heartbeat": {"slug": HEARTBEAT_SLUG, "last": None, "at": None}}, status_code=200)


# ── who moved it? ───────────────────────────────────────────────────────────────────────────
def blame_sync(object_id: str) -> dict | None:
    """The commit that last changed this object's record, and what that commit did to it. None = never recorded."""
    import graph_api                                      # its per-commit ops are the single planner (no second diff here)
    line = room._git("log", "-1", "--format=%H%x09%aI%x09%an%x09%s", "--", f"zones/*/{object_id}.yaml", check=False).strip()   # noqa: SLF001
    if not line:
        return None
    sha, at, author, subject = (line.split("\t") + ["", "", ""])[:4]
    parent = graph_api._resolve(f"{sha}^") or graph_api.EMPTY_TREE                     # noqa: SLF001
    op = next((o for o in graph_api._ops(parent, sha) if o["object_id"] == object_id), None)   # noqa: SLF001
    return {"object_id": object_id, "class": (op or {}).get("class"),
            "moved_in": {"sha": sha, "at": at, "author": author, "subject": subject, "capture_id": None},
            "what": (op or {}).get("op"), "from": (op or {}).get("from"), "to": (op or {}).get("to"),
            "from_zone": (op or {}).get("from_zone") or ((op or {}).get("zone") if (op or {}).get("from") else None),
            "zone": (op or {}).get("zone"), "delta_m": (op or {}).get("delta_m"), "frame_url": None,
            "frame_reason": "no capture frame is stored yet (frames are not indexed), so there is no picture of the moment",
            "frame": FRAME}


@router.get("/api/blame/{object_id}")
async def blame(object_id: str):
    if not OBJECT_ID.match(object_id):
        return _error("bad_request", "object_id looks like mug_a1b2", 422)
    try:
        out = await asyncio.to_thread(blame_sync, object_id)
    except room.RoomError as e:
        return _error("room_unavailable", str(e), 503, retryable=True)
    if out is None:
        return _error("not_found", f"nothing in the room's history is called {object_id}", 404)
    try:                                                  # which capture made that commit: enrichment, never required
        events, _ = await store._find("room-events", {"commit_sha": [out["moved_in"]["sha"], out["moved_in"]["sha"][:7]]},   # noqa: SLF001
                                      size=1, newest_first=True, label="blame.capture")
        if events:
            out["moved_in"]["capture_id"] = events[0].get("capture_id")
    except Exception:  # noqa: BLE001 — git already answered the question
        pass
    return out


# ── chores, pull requests, the robot on its map: waiting for their backends ─────────────────────
@router.get("/api/chores")
async def chores():
    return JSONResponse([], headers=NOT_CONNECTED)       # true: nothing can file a chore yet, so there are none


@router.get("/api/prs")
async def prs():
    return JSONResponse([], headers=NOT_CONNECTED)


@router.post("/api/prs")
async def open_pr(_: dict[str, Any] = Body(...)):
    return _error("not_connected", "pull requests for the room are not wired yet: roomctl has no PR store to write to", 503)


@router.post("/api/prs/{pr_id}/approve")
async def approve_pr(pr_id: str):
    return _error("not_connected", "there is no PR store yet, so there is nothing to approve", 503)


@router.get("/api/nav/snapshot")
async def nav_snapshot():
    return _error("not_connected", "no robot (or bbsim) is publishing its pose and map to this server yet", 503, retryable=True)
