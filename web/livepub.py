"""livepub.py — the operator publishes the live camera to a PUBLIC copy of this site, on purpose, briefly.

    POST /api/live/publish        {seconds}  open or extend a window   (local, or the room's token)
    POST /api/live/publish/stop              close it now and DROP the frame
    POST /api/live/frame          image/jpeg  one frame, only while a window is open
    GET  /api/live/state                     publishing? until when? how old is the frame?   (public read)
    GET  /api/live/frame.jpg                 the frame, only while a window is open AND it is fresh

WHY THIS IS NOT `/live/`. `/live/` is the laptop's own camera directory and server.py serves it to loopback
ONLY. That rule does not move. This is a different thing with a different rule: frames a person DELIBERATELY
published, for a few minutes, to a copy of the site strangers can see. Off by default; nothing here runs
until someone turns it on, and it turns itself off.

THE PROPERTY THAT MATTERS, and every rule below exists to keep it: THE PUBLIC SIDE NEVER SHOWS A FRAME IT
HAS NOT JUST BEEN GIVEN.
  * no window open          -> a frame POST is refused and NOTHING is stored
  * the window ends         -> the bytes are dropped, not merely hidden; the next read is a 404 with a reason
  * the publisher stalls    -> a frame older than STALE_S is not served, so a frozen picture cannot read as live
  * a window cannot outlive MAX_S whatever it asks for, because "leave it on" is how a demo becomes a webcam
  * every response is no-store, so nothing of this lands in a shared cache
A cache of the last frame would break all of that at once, so there is no cache: one slot, cleared on the way
out. `state` is readable by anyone — a person in the room is entitled to know their camera is being published.
"""
from __future__ import annotations

import hmac
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

import localonly

router = APIRouter()

MAX_S = 15 * 60          # a demo needs minutes; a window may never outlive this, whatever it asks for
DEFAULT_S = 5 * 60
STALE_S = 10.0           # a frame older than this is not live any more, whoever is still holding the window
MAX_BYTES = 2_000_000    # one frame, not a film
MIN_TOKEN = 32           # the floor the rest of this server holds GITIRL_CLOUD_TOKEN to

_until = 0.0             # monotonic deadline; 0 means closed
_frame: bytes | None = None
_frame_at = 0.0
_opened_at = 0.0
_source = ""
_seen = 0


def init(es) -> None:    # the router takes no Elasticsearch; the signature is the mount contract
    return None


def _error(code: str, detail: str, status: int, retryable: bool = False) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail, "retryable": retryable}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def _drop() -> None:
    """Forget the frame. Called on stop and on every expiry check — the bytes go, not just the flag."""
    global _frame, _frame_at, _source
    _frame, _frame_at, _source = None, 0.0, ""


def _open() -> bool:
    """Is a window open right now? Closing one here is what makes expiry self-enforcing: every read and
    every write goes through this, so there is no path that serves a frame from a window that has ended."""
    global _until
    if _until and time.monotonic() >= _until:
        _until = 0.0
        _drop()
    return bool(_until)


def _guard(request: Request, what: str) -> JSONResponse | None:
    """Publishing is the operator's, never a visitor's: a loopback peer with no forwarding header, or the
    room's own token. The same guard the room's write paths use."""
    if localonly.is_local(request.client.host if request.client else "", request.headers.keys()):
        return None
    want = os.getenv("GITIRL_CLOUD_TOKEN", "")
    scheme, _, got = request.headers.get("authorization", "").partition(" ")
    if len(want) >= MIN_TOKEN and scheme.lower() == "bearer" and hmac.compare_digest(got.strip().encode(), want.encode()):
        return None
    if len(want) < MIN_TOKEN:
        return _error("forbidden", f"{what} is local-only on this server (it has no GITIRL_CLOUD_TOKEN configured)", 403)
    r = _error("unauthorized", f"{what} needs a local connection or Authorization: Bearer <GITIRL_CLOUD_TOKEN>", 401)
    r.headers["WWW-Authenticate"] = 'Bearer realm="gitspace-live"'
    return r


def state() -> dict:
    """What both ends show. Truthful about a stalled publisher: `live` needs a window AND a fresh frame."""
    open_ = _open()
    age = round(time.monotonic() - _frame_at, 2) if (_frame and _frame_at) else None
    fresh = bool(_frame) and age is not None and age <= STALE_S
    return {"publishing": open_, "live": bool(open_ and fresh),
            "seconds_left": round(_until - time.monotonic(), 1) if open_ else 0,
            "frame_age_s": age if open_ else None, "frames": _seen if open_ else 0,
            "source": _source if open_ else None, "stale_after_s": STALE_S, "max_seconds": MAX_S,
            "detail": ("the live camera stays on the operator's machine; this copy shows the recorded captures "
                       "and the 3D scans instead" if not open_ else
                       "the operator is publishing the live camera to this copy, briefly and on purpose")}


@router.post("/api/live/publish")
async def publish(request: Request):
    global _until, _opened_at, _source, _seen
    if (no := _guard(request, "publishing the live camera")) is not None:
        return no
    try:
        body = await request.json()
    except ValueError:
        body = {}
    want = (body or {}).get("seconds", DEFAULT_S)
    try:
        secs = max(1, min(int(want), MAX_S))
    except (TypeError, ValueError):
        return _error("bad_request", f"seconds must be a whole number of seconds, at most {MAX_S}", 422)
    if not _open():
        _opened_at, _seen = time.monotonic(), 0
    _until = time.monotonic() + secs        # extending REPLACES the deadline; it never accumulates past MAX_S
    _source = str((body or {}).get("source") or "operator")[:40]
    return JSONResponse({**state(), "capped": secs < int(want) if isinstance(want, int) else False},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/live/publish/stop")
async def stop(request: Request):
    global _until
    if (no := _guard(request, "stopping the live camera")) is not None:
        return no
    _until = 0.0
    _drop()                                  # the bytes go NOW: "off" must not leave a last frame behind
    return JSONResponse(state(), headers={"Cache-Control": "no-store"})


@router.post("/api/live/frame")
async def put_frame(request: Request):
    global _frame, _frame_at, _seen
    if (no := _guard(request, "publishing a frame")) is not None:
        return no
    if not _open():
        return _error("not_publishing", "no publishing window is open; nothing was stored", 409)
    raw = await request.body()
    if not raw or len(raw) > MAX_BYTES:
        return _error("bad_request", f"a frame is 1 to {MAX_BYTES} bytes of JPEG", 413 if raw else 422)
    if not raw.startswith(b"\xff\xd8"):      # a JPEG, not whatever else was posted at it
        return _error("bad_request", "the body must be a JPEG", 415)
    _frame, _frame_at = raw, time.monotonic()
    _seen += 1
    return JSONResponse(state(), headers={"Cache-Control": "no-store"})


@router.get("/api/live/state")
async def get_state():
    return JSONResponse(state(), headers={"Cache-Control": "no-store"})


@router.get("/api/live/frame.jpg")
async def get_frame():
    if not _open():
        return _error("not_publishing", "the live camera is not being published to this copy", 404)
    if not _frame or time.monotonic() - _frame_at > STALE_S:
        return _error("stale", f"no frame in the last {STALE_S:g} s — the publisher stopped sending", 404, retryable=True)
    return Response(_frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
