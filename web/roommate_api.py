"""roommate_api.py — the caretaker-roommate dashboard's API (plan/roommate/03-interfaces.md §8).

    GET  /api/room/ci                 is the room at `main`?  {state, since, head, branch, changes, misplaced, heartbeat,
                                      last_verified_job, watch}
    GET  /api/blame/{object_id}       who moved it, and when: the commit that last changed it, from -> to, its capture
    GET  /api/chores[?status=open]    what the roommate could not put back itself (Tier B)
    GET  /api/prs · POST /api/prs     a change you MEANT goes through a pull request, not a tidy-up
                                      {object_id, zone, title?} = carry it to a free spot in zone · {object_id, as_seen: true}
                                      = "I meant that": main takes it exactly where the room has it
    POST /api/prs/{id}/approve        -> {merge_sha, job_id}      local, or Authorization: Bearer $GITIRL_CLOUD_TOKEN
    POST /api/prs/{id}/close          -> the PR, closed           same guard
    POST /api/edge/event              the watch loop / nav bridge push {event: room_state|nav|chore|pr, data}; same guard
    GET  /api/nav/snapshot            the robot on its map, in the ROOM frame

What is real, and where it comes from
  * /api/room/ci   git's own working tree of room.git (room.snapshot()), plus what only the watch loop knows:
                   `since` / `heartbeat` from the room-clean badge, `last_verified_job` and `watch` from the last
                   `room_state` it pushed here. None of them is ever made up: no push yet -> null, and `source` says so.
  * /api/blame     `git log` for the object's file, that commit's own from -> to, and the capture that made it
                   (room-events). `frame_url` is a camera frame filed under THAT capture id by camera_ingest.py
                   (/live/, this laptop only), or null with the reason: never the newest frame standing in for it.
  * chores, prs    roomctl's own stores (roomctl/chores.py, roomctl/pr.py), read through roomctl's own code. If roomctl
                   cannot be imported the reads answer an EMPTY LIST with `X-Roommate-Backend: not_connected` and the
                   writes answer 503.
  * nav            nothing publishes the robot's pose yet: 503 `not_connected` until a `nav` event has arrived.
Every pose is the room frame: world_z_up, metres (docs/20).

WRITES. This module's own code never touches room.git. Opening, approving and closing a pull request DO change it —
through roomctl.pr, the room's owner, exactly as `room pr` on the command line would — and only on an explicit POST.
Opening one adds a `pr/<n>-…` branch; approving merges it into the checked-out branch and leaves the working tree (the
room as last seen) alone, so the move the robot still owes shows up as drift and the watch loop picks it up.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

import events
import localonly
import room
import store

OBJECT_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
FRAME = "world_z_up"
HEARTBEAT_SLUG = "room-clean"
NOT_CONNECTED = {"X-Roommate-Backend": "not_connected"}
MIN_TOKEN = 32                      # the same floor jobs.py holds GITIRL_CLOUD_TOKEN to: a short token is no token
NAV_MAP_KEYS = ("grid", "freshness", "map_gen")
KEPT = ("room_state", "nav")        # pushed states a page that loads LATER still needs; chore / pr have stores of their own
ZONE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

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


def misplaced(changes: list[dict]) -> list[dict]:
    """One object, two git rows: `main` has it in one zone (deleted there) and the room has it in another (untracked
    there). That is what a merged pull request looks like until the robot has carried it over. `changes` stays git's."""
    gone = {c["object_id"]: c.get("zone") for c in changes if c.get("type") == "deleted" and c.get("object_id")}
    return [{"object_id": c["object_id"], "is_in": c.get("zone"), "belongs_in": gone[c["object_id"]]}
            for c in changes if c.get("type") == "untracked" and c.get("object_id") in gone and gone[c["object_id"]] != c.get("zone")]


def ci_from(snap: dict) -> dict:
    """room.snapshot() -> the CI answer. A merge in progress is its own state: two roommates, one lamp."""
    state = "conflict" if snap.get("conflicts") or snap.get("merging") else "clean" if snap.get("clean") else "dirty"
    b, w = _beat(), kept("room_state")
    watch = None
    if w:                                                 # the loop's own verdict: debounced, policy applied, fresh passes only
        d = w["data"]
        watch = {"clean": d.get("clean"), "confirmed": d.get("confirmed") or [], "pending": d.get("pending") or [],
                 "blocked": d.get("blocked"), "stale_blocks": d.get("stale_blocks"), "ignored": d.get("ignored"),
                 "passes": d.get("passes"), "head": d.get("head"), "at": d.get("at"), "received_at": w["received_at"]}
    return {"state": state, "branch": snap.get("branch"), "head": snap.get("head"), "changes": snap.get("changes") or [],
            "conflicts": snap.get("conflicts") or [], "last_capture": snap.get("last_capture"),
            "misplaced": misplaced(snap.get("changes") or []),
            # the badge's feeder owns these two: when the state last flipped, and the room-clean check-in it SENT
            # (`at` stays null while ROOM_CLEAN_CRON is off: the verdict is recorded, nothing is sent)
            "since": (b or {}).get("since"),
            "heartbeat": {"slug": HEARTBEAT_SLUG, "last": (b or {}).get("last"), "at": (b or {}).get("at")},
            # the last job (or chore) that a CLEAN FRESH pass came after: proof the room was looked at again, and only
            # the watch loop can say it. A job that merely ended is not here.
            "last_verified_job": (w["data"].get("last_verified_job") if w else None) or (b or {}).get("last_verified_job"),
            "watch": watch,
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
                             "heartbeat": {"slug": HEARTBEAT_SLUG, "last": None, "at": None},
                             "last_verified_job": None, "watch": None, "misplaced": []}, status_code=200)


# ── who moved it? ───────────────────────────────────────────────────────────────────────────
def blame_sync(object_id: str) -> dict | None:
    """The commit that last changed this object's record, and what that commit did to it. None = never recorded."""
    import graph_api                                      # its per-commit ops are the single planner (no second diff here)
    line = room._git("log", "-1", "--format=%H%x09%aI%x09%an%x09%s%x09%(trailers:key=Proposed-by,valueonly,separator=%x2C)",   # noqa: SLF001
                     "--", f"zones/*/{object_id}.yaml", check=False).strip()
    if not line:
        return None
    sha, at, author, subject, proposed_by = (line.split("\t") + ["", "", "", ""])[:5]
    parent = graph_api._resolve(f"{sha}^") or graph_api.EMPTY_TREE                     # noqa: SLF001
    op = next((o for o in graph_api._ops(parent, sha) if o["object_id"] == object_id), None)   # noqa: SLF001
    return {"object_id": object_id, "class": (op or {}).get("class"),
            # a pull request's commit is WRITTEN by the robot's identity; the person who asked is its Proposed-by trailer
            "moved_in": {"sha": sha, "at": at, "author": author, "subject": subject, "capture_id": None,
                         "proposed_by": proposed_by.strip() or None},
            "what": (op or {}).get("op"), "from": (op or {}).get("from"), "to": (op or {}).get("to"),
            "from_zone": (op or {}).get("from_zone") or ((op or {}).get("zone") if (op or {}).get("from") else None),
            "zone": (op or {}).get("zone"), "delta_m": (op or {}).get("delta_m"), "frame_url": None, "frame_reason": None,
            "frame": FRAME}


CAPTURE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LIVE_DIR = Path(__file__).resolve().parent / "landing" / "live"     # where camera_ingest.py files what the cameras sent


def frame_of(capture_id: str | None) -> dict:
    """The picture of THAT moment, or the reason there is none. Only a frame filed under this exact capture id counts:
    the newest frame on disk is a picture of some other moment. /live/ is served to this laptop alone (server.py)."""
    if not capture_id or not CAPTURE_ID.match(capture_id):
        return {"frame_url": None, "frame_reason": "that commit has no capture on record, so there is no picture of the moment"}
    shots = sorted((LIVE_DIR / capture_id).glob("*_color.jpg")) if (LIVE_DIR / capture_id).is_dir() else []
    if not shots:
        return {"frame_url": None, "frame_reason": f"no camera frame was stored for {capture_id}, so there is no picture of the moment"}
    return {"frame_url": f"/live/{capture_id}/{shots[0].name}", "frame_reason": None, "frame_local_only": True,
            "frames": [f"/live/{capture_id}/{f.name}" for f in shots]}


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
        events_, _ = await store._find("room-events", {"commit_sha": [out["moved_in"]["sha"], out["moved_in"]["sha"][:7]]},   # noqa: SLF001
                                       size=1, newest_first=True, label="blame.capture")
        if events_:
            out["moved_in"]["capture_id"] = events_[0].get("capture_id")
    except Exception:  # noqa: BLE001 — git already answered the question
        pass
    out.update(frame_of(out["moved_in"]["capture_id"]))
    return out


# ── what the watch loop and the nav bridge push in ───────────────────────────────────────────
_kept: dict[str, dict] = {}


def _kept_path() -> Path:
    return Path(os.getenv("ROOM_STATE_FILE") or "~/.cache/gitspace/room-state.json").expanduser()


def kept(name: str) -> dict | None:
    """{data, received_at} for the last `room_state` / `nav` pushed here. The loop publishes when its verdict CHANGES,
    which can be hours apart, so the last one outlives a restart of this process (a small file beside the badge's)."""
    if not _kept:
        try:
            doc = json.loads(_kept_path().read_text())
            _kept.update({k: v for k, v in doc.items() if k in KEPT and isinstance(v, dict) and isinstance(v.get("data"), dict)})
        except (OSError, ValueError):
            pass
    return _kept.get(name)


def _keep(name: str, data: dict) -> None:
    prev = kept(name)                                     # load what the last process left before replacing part of it
    if name == "nav" and prev:
        # the pose comes at 2 Hz, the map only when it changes: a pose-only event keeps the map it belongs to. A new
        # map_gen WITHOUT a new grid means the old grid is a different map, and is dropped rather than drawn under it.
        same_map = data.get("map_gen") in (None, prev["data"].get("map_gen"))
        data = {**({k: v for k, v in prev["data"].items() if k in NAV_MAP_KEYS} if same_map else {}), **data}
    _kept[name] = {"data": data, "received_at": _now()}
    if name != "room_state":                              # a pose at 2 Hz is not worth a disk write; it is stale in a second
        return
    try:
        p = _kept_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".room-state-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump({"room_state": _kept["room_state"]}, f, indent=1)
        os.replace(tmp, p)
    except OSError:
        pass                                              # memory still has it; the file is only for the next restart


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _guard(request: Request, what: str) -> JSONResponse | None:
    """03 §8: local, or the cloud bearer. Local = a loopback peer with NO forwarding header (a tunnel or reverse proxy
    also arrives from 127.0.0.1). The token is compared in constant time and a short one counts as unset."""
    if localonly.is_local(request.client.host if request.client else "", request.headers.keys()):
        return None
    want = os.getenv("GITIRL_CLOUD_TOKEN", "")
    scheme, _, got = request.headers.get("authorization", "").partition(" ")
    if len(want) >= MIN_TOKEN and scheme.lower() == "bearer" and hmac.compare_digest(got.strip().encode(), want.encode()):
        return None
    if len(want) < MIN_TOKEN:
        return _error("forbidden", f"{what} is local-only on this server (it has no GITIRL_CLOUD_TOKEN configured)", 403)
    r = _error("unauthorized", f"{what} needs a local connection or Authorization: Bearer <GITIRL_CLOUD_TOKEN>", 401)
    r.headers["WWW-Authenticate"] = 'Bearer realm="gitspace-room"'
    return r


@router.post("/api/edge/event")
async def edge_event(request: Request):
    """roomctl's watch loop ($ROOM_WEB_URL) and the nav bridge push what this process cannot see. It fans out over
    /api/events under the same name; `room_state` and `nav` are also kept, for /api/room/ci and /api/nav/snapshot."""
    if (no := _guard(request, "POST /api/edge/event")) is not None:
        return no
    try:
        body = await request.json()
    except ValueError:
        return _error("bad_request", "body must be JSON", 400)
    name, data = (body or {}).get("event") if isinstance(body, dict) else None, (body or {}).get("data") if isinstance(body, dict) else None
    if name not in events.ROOMMATE_EVENTS or not isinstance(data, dict):
        return _error("bad_request", f"event must be one of {', '.join(events.ROOMMATE_EVENTS)}; data must be an object", 400)
    if name == "nav" and data.get("frame") not in (None, FRAME):      # poses cross only in the room frame (docs/20)
        return _error("frame_mismatch", f"nav poses must be in {FRAME}, got {data.get('frame')!r}", 422)
    if name == "nav":                                     # 2 Hz for as long as the robot is on: not 170,000 transactions a day
        try:
            import sentry_sdk
            if (tx := sentry_sdk.get_current_scope().transaction) is not None:
                tx.sampled = False
        except Exception:  # noqa: BLE001 — observability must never break the inlet
            pass
    if name in KEPT:
        _keep(name, data)
    events.note_arrival(name)              # the public copy's only evidence that a robot is on the other end
    events.hub.publish(name, data)
    return {"published": name, "clients": events.hub.clients}


# ── chores and pull requests: roomctl's stores, through roomctl's code ────────────────────────
def _repo():
    from roomctl.repo import Repo                         # lazy: a checkout without roomctl still serves the rest
    return Repo(room.room_path())


def _git_error(e: Exception) -> JSONResponse:
    """roomctl's GitError, in words a panel can show. Its message decides the status: it is the only signal there is."""
    msg = str(e)
    if re.search(r"^no (pull request|object|zone) | is not in the room as last scanned", msg):
        return _error("not_found", msg, 404)
    if re.search(r"already (merged|in )|is closed|conflicts with|no free spot|was seen in ", msg):
        return _error("conflict", msg, 409)
    return _error("room_unavailable", msg, 503, retryable=True)


@router.get("/api/chores")
async def chores(status: str | None = None):
    if status not in (None, "open", "closed"):
        return _error("bad_request", "status is open or closed", 422)
    try:
        from roomctl import chores as store_
        rows = await asyncio.to_thread(lambda: store_.list_chores(_repo(), status))
    except ImportError:
        return JSONResponse([], headers=NOT_CONNECTED)
    except Exception as e:  # noqa: BLE001 — an unreadable ledger is an outage, never "no chores"
        return _error("room_unavailable", str(e), 503, retryable=True)
    return [{**c, "frame_url": c.get("frame_url")} for c in rows]


@router.get("/api/prs")
async def prs():
    try:
        from roomctl import pr
        return [p.to_dict() for p in await asyncio.to_thread(lambda: pr.list_prs(_repo()))]
    except ImportError:
        return JSONResponse([], headers=NOT_CONNECTED)
    except Exception as e:  # noqa: BLE001
        return _git_error(e)


def _who(request: Request, said: Any) -> str:
    """Whose name goes in the commit trailer. A name is a label, not a login: the guard above is the authority."""
    said = re.sub(r"[^\w .@-]", "", str(said or ""))[:40].strip()
    return said or ("you" if localonly.is_local(request.client.host if request.client else "", request.headers.keys()) else "cloud")


@router.post("/api/prs", status_code=201)
async def open_pr(request: Request, body: dict[str, Any] = Body(...)):
    if (no := _guard(request, "opening a pull request")) is not None:
        return no
    object_id, zone, title, as_seen = body.get("object_id"), body.get("zone"), body.get("title"), body.get("as_seen", False)
    if not isinstance(object_id, str) or not OBJECT_ID.match(object_id):
        return _error("bad_request", "object_id looks like mug_a1b2", 422)
    if not isinstance(as_seen, bool):
        return _error("bad_request", "as_seen is true or false", 422)
    # as_seen: "I meant that" — main takes the object exactly where the room has it, so approving leaves no drift behind.
    # Without it, roomctl picks a free spot in `zone` and the robot carries the object there.
    if (zone is not None or not as_seen) and (not isinstance(zone, str) or not ZONE.match(zone)):
        return _error("bad_request", "zone is one of room.yaml's zones, like shelf (optional only with as_seen)", 422)
    if title is not None and (not isinstance(title, str) or not title.strip() or len(title) > 120 or "\n" in title):
        return _error("bad_request", "title is one line, at most 120 characters", 422)
    try:
        from roomctl import pr
        made = await asyncio.to_thread(lambda: pr.propose(_repo(), object_id, zone, _who(request, body.get("author")),
                                                           title.strip() if title else None, as_seen=as_seen))
    except ImportError:
        return _error("not_connected", "roomctl is not importable on this server, so there is no PR store to write to", 503)
    except Exception as e:  # noqa: BLE001
        return _git_error(e)
    out = made.to_dict()
    events.hub.publish("pr", out)
    return JSONResponse(out, status_code=201)


def _pr_id(raw: str) -> int | None:
    return int(raw) if raw.isdigit() and len(raw) < 7 else None


@router.post("/api/prs/{pr_id}/approve")
async def approve_pr(pr_id: str, request: Request):
    if (no := _guard(request, "approving a pull request")) is not None:
        return no
    if (n := _pr_id(pr_id)) is None:
        return _error("bad_request", "a pull request id is a number", 422)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    try:
        from roomctl import pr
        merge = await asyncio.to_thread(lambda: pr.approve(_repo(), n, _who(request, (body or {}).get("approver"))))
        now = next((p.to_dict() for p in await asyncio.to_thread(lambda: pr.list_prs(_repo())) if p.id == n), None)
    except ImportError:
        return _error("not_connected", "roomctl is not importable on this server, so there is nothing to approve", 503)
    except Exception as e:  # noqa: BLE001
        return _git_error(e)
    if now:
        events.hub.publish("pr", now)
    # merging changes what `main` SAYS; the room has not moved. No job is made here: the watch loop sees the drift on
    # its next fresh pass and queues the move (or files a chore), and that job is the one that gets verified.
    return {"merge_sha": merge, "job_id": None, "pr": now,
            "job_reason": "no job yet: the watch loop queues the move when its next fresh pass sees the room differ from main"}


@router.post("/api/prs/{pr_id}/close")
async def close_pr(pr_id: str, request: Request):
    if (no := _guard(request, "closing a pull request")) is not None:
        return no
    if (n := _pr_id(pr_id)) is None:
        return _error("bad_request", "a pull request id is a number", 422)
    try:
        from roomctl import pr
        await asyncio.to_thread(lambda: pr.close(_repo(), n))
        now = next((p.to_dict() for p in await asyncio.to_thread(lambda: pr.list_prs(_repo())) if p.id == n), None)
    except ImportError:
        return _error("not_connected", "roomctl is not importable on this server, so there is nothing to close", 503)
    except Exception as e:  # noqa: BLE001
        return _git_error(e)
    if now:
        events.hub.publish("pr", now)
    return now or {"id": n, "status": "closed"}


# ── is a robot connected to THIS copy? ───────────────────────────────────────────────────────
@router.get("/api/link")
async def link():
    """Whether a robot is feeding this server, and what a visitor can do about it.

    `connected` is true because DATA ARRIVED and for no other reason. A configured token means a robot
    COULD connect, never that one has — on the public copy that distinction is the difference between an
    honest empty state and lying to a stranger. Everything under `without_a_robot` is live regardless.
    """
    a = events.arrivals()
    want = os.getenv("GITIRL_CLOUD_TOKEN", "")
    accepts = len(want) >= MIN_TOKEN
    return {**a,
            "accepts_remote": accepts,
            "why_not": None if a["connected"] else
                       ("no robot has posted to this copy yet — it is the same code and the same room history "
                        "as the laptop, but nothing is feeding it live"
                        if accepts else
                        "this server has no GITIRL_CLOUD_TOKEN configured, so only a robot on this machine "
                        "(loopback) can post to it"),
            # what a person with a robot would actually do. The hub already posts these events; it needs to be
            # pointed here and to carry the token.
            "how": {"events_url": "<this site>/api/edge/event",
                    "env": ["WEB_EVENTS_URL=<this site>/api/edge/event", "GITIRL_CLOUD_TOKEN=<the room's token>"],
                    "accepts": list(events.ROOMMATE_EVENTS),
                    "note": "the token is the room's, not ours: a visitor connects THEIR robot to THEIR copy"},
            # the page must lead with this rather than with the absence
            "without_a_robot": ["the room's whole commit history", "the object search over every capture",
                                "every past capture and its replay", "the Sentry board"],
            "frame": FRAME}


# ── the robot on its map ────────────────────────────────────────────────────────────────────
NAV_FRESH_S = 10


@router.get("/api/nav/snapshot")
async def nav_snapshot():
    n = kept("nav")
    if not n:
        return _error("not_connected", "no robot (or bbsim) is publishing its pose and map to this server yet", 503, retryable=True)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(n["received_at"].replace("Z", "+00:00"))).total_seconds()
    # a pose is only "where the robot is" while it keeps arriving; after that it is where the robot WAS, and says so
    return {**n["data"], "frame": FRAME, "received_at": n["received_at"], "age_s": round(age, 1), "stale": age > NAV_FRESH_S}
