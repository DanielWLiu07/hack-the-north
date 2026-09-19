"""sentry_actions.py — the ONE place this server writes to Sentry.

/telemetry keeps a Sentry issue on the board after it has been fixed. Three states, and the
middle one is a real change in Sentry, not a class name on a card:

    open        the issue as Sentry reports it (the existing live feed)
    resolved    PUT status=resolved on the issue, then READ THE ISSUE BACK. The badge the page
                prints is the status Sentry answered with — never a local guess. If the read-back
                fails the page is told `read_back: false` and prints what went wrong instead.
    removed     only on a press of [remove]: we make sure Sentry says resolved, and then the CARD
                leaves OUR board. Nothing in Sentry is removed.

    POST /api/sentry/issues/{id}/resolve   {"status": "resolved" | "ignored" | "unresolved"}
    POST /api/sentry/issues/{id}/remove    ensure resolved, read back, then the page drops the card
    GET  /api/sentry/issues/status?ids=…   what Sentry says about these issues right now
    GET  /api/sentry/actions               may this caller write, and is Sentry reachable at all

NOTHING HERE DELETES. There is no call to Sentry's DELETE in this file and there must never be
one: deleting an issue throws away the event history that the rest of /telemetry is built on, and
it cannot be undone. "Remove" is a board word, not a Sentry word — see `remove_issue`.

LOCAL ONLY. The two POSTs change another service's state, so they take the same rule as the event
inlet (roommate_api._guard / localonly.py): a loopback peer carrying NO forwarding header, or the
GITIRL_CLOUD_TOKEN bearer. This site is reachable through a tunnel and a tunnel's request also
arrives from 127.0.0.1, so loopback alone is not enough. The Sentry auth token stays in this
process: the browser asks THIS server, THIS server asks Sentry. It is never logged or returned.
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

import localonly
import sentry_client

router = APIRouter()

MIN_TOKEN = 32                  # the same floor jobs.py and roommate_api.py hold: a short token is no token
MAX_STATUS_IDS = 10             # the board only ever re-checks the handful of issues this browser resolved
SEARCH_WINDOW = "90d"           # how far back the batched status read looks; a held card is hours old, not weeks
# what a press may set. "resolved" is the one the board uses; "ignored" (Sentry's archive) and
# "unresolved" (an undo, for a mis-press) are here so a wrong press is recoverable. No other value.
WRITABLE = ("resolved", "ignored", "unresolved")

sentry = sentry_client.SentryClient()      # makes no call while Sentry is parked (see its state())


def init(es) -> None:
    """server.mount_router() calls this. Nothing to set up: the client reads the environment per call."""
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _error(code: str, detail: str, status: int, retryable: bool = False) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail, "retryable": retryable}, status_code=status)


def _upstream(e: Exception) -> JSONResponse:
    """docs/16-api.md §2.7. A SentryError already carries its own code; anything else is a bug here,
    and says so rather than pretending Sentry answered."""
    if isinstance(e, sentry_client.SentryError):
        return _error(e.code, e.detail, e.status, e.retryable)
    return _error("sentry_error", f"this server failed talking to Sentry ({type(e).__name__})", 502, True)


def _local(request: Request) -> bool:
    """TestClient's peer is the literal string "testclient"; it is this process talking to itself.
    A forwarding header still disqualifies it, which is what the guard test leans on."""
    peer = request.client.host if request.client else ""
    return localonly.is_local("127.0.0.1" if peer == "testclient" else peer, request.headers.keys())


def _guard(request: Request, what: str) -> JSONResponse | None:
    """Local, or the cloud bearer — the rule in roommate_api.py's _guard, for the same reason: the
    site is reachable through a tunnel, and a stranger must not be able to change our Sentry project."""
    if _local(request):
        return None
    want = os.getenv("GITIRL_CLOUD_TOKEN", "")
    scheme, _, got = request.headers.get("authorization", "").partition(" ")
    if len(want) >= MIN_TOKEN and scheme.lower() == "bearer" and hmac.compare_digest(got.strip().encode(), want.encode()):
        return None
    if len(want) < MIN_TOKEN:
        return _error("forbidden", f"{what} is local-only on this server (it has no GITIRL_CLOUD_TOKEN configured)", 403)
    r = _error("unauthorized", f"{what} needs a local connection or Authorization: Bearer <GITIRL_CLOUD_TOKEN>", 401)
    r.headers["WWW-Authenticate"] = 'Bearer realm="gitspace-sentry"'
    return r


# ── talking to Sentry ────────────────────────────────────────────────────────────────────────
# SentryClient._request is reused on purpose rather than copied: it is the single definition of the
# Authorization header, the timeout, and the §2.7 error mapping, and it already refuses to retry a
# non-GET (a write is never sent twice). Copying it would put the auth token in a second place.

def _issue_path(org: str, issue_id: str) -> str:
    return f"/organizations/{org}/issues/{issue_id}/"


def _shape(raw: dict, *, read_back: bool, reason: str | None = None) -> dict:
    """One issue as the board draws it. `status`/`substatus` are Sentry's own words, verbatim."""
    status = raw.get("status")
    try:
        count = int(raw.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    return {"id": str(raw.get("id") or ""), "short_id": raw.get("shortId") or raw.get("short_id"),
            "title": raw.get("title") or "", "status": status, "substatus": raw.get("substatus"),
            "resolved": status == "resolved", "ignored": status == "ignored",
            "permalink": raw.get("permalink"), "count": count,
            "last_seen": raw.get("lastSeen") or raw.get("last_seen"),
            "read_back": read_back, "read_back_at": _now() if read_back else None,
            "read_back_failed_because": reason}


async def _read(issue_id: str) -> dict:
    """GET the issue. This is what the badge prints: Sentry's answer, after the write."""
    org, _ = sentry._guard()
    raw = await sentry._request("GET", _issue_path(org, issue_id))
    return _shape(raw if isinstance(raw, dict) else {"id": issue_id}, read_back=True)


async def _read_many(ids: list[str]) -> tuple[list[dict], list[dict]]:
    """Every status the board is holding, in ONE call.

    This used to be one GET per id, fired together. That is what gets this token rate limited: at
    a dozen concurrent Sentry calls the org starts answering 429, and every OTHER Sentry panel on
    /telemetry then fails at once — a capture's waterfall 502s in the console and the page looks
    broken. The org issue search takes `issue.id:[a,b,c]` and, unlike the unresolved feed, answers
    resolved issues too, which is exactly what a board holding fixed cards needs to re-read.
    """
    org, _ = sentry._guard()
    found = await sentry._request("GET", f"/organizations/{org}/issues/",
                                  params={"query": f"issue.id:[{','.join(ids)}]", "statsPeriod": SEARCH_WINDOW,
                                          "limit": max(len(ids), 1), "project": -1})
    by_id = {str(i.get("id")): i for i in (found or []) if isinstance(i, dict)}
    out = [_shape(by_id[i], read_back=True) for i in ids if i in by_id]
    gone = [{"id": i, "reason": f"Sentry did not return this issue in the last {SEARCH_WINDOW} "
                                "(deleted, merged, or outside the search window)"} for i in ids if i not in by_id]
    return out, gone


async def _write_status(issue_id: str, status: str) -> None:
    """PUT the new status. One attempt (SentryClient never retries a non-GET), and the answer is
    thrown away on purpose: the caller reads the issue back instead of trusting the echo."""
    org, _ = sentry._guard()
    await sentry._request("PUT", _issue_path(org, issue_id), body={"status": status})


async def _set_and_read(issue_id: str, status: str) -> dict:
    """The whole move: write, then read. A write that lands but a read that fails is reported as
    exactly that — `read_back: false` with the reason — so the page never invents a green badge."""
    await _write_status(issue_id, status)
    try:
        return await _read(issue_id)
    except Exception as e:  # noqa: BLE001 — the WRITE succeeded; the page must hear that, and that we cannot confirm it
        detail = e.detail if isinstance(e, sentry_client.SentryError) else f"{type(e).__name__}: {e}"
        return {**_shape({"id": issue_id}, read_back=False, reason=str(detail)[:200]),
                "status": None, "resolved": False, "requested_status": status}


# ── routes ───────────────────────────────────────────────────────────────────────────────────
@router.get("/api/sentry/actions")
async def actions_state(request: Request):
    """What the board needs BEFORE anyone presses anything: may this caller write, and is Sentry
    even reachable. No network call, and never the token — only whether one is configured."""
    st = sentry.state()
    local = _local(request)
    return {"can_write": bool(local and st["configured"]), "local": local,
            "why_not": None if local else "this page is being viewed through a tunnel or proxy; "
                                          "resolving an issue is local-only on this server",
            "sentry": {"configured": st["configured"], "paused": st["paused"], "until": st["until"],
                       "reason": st["reason"], "org": st["org"], "project": st["project"]},
            "statuses": list(WRITABLE), "deletes": False,
            "note": "resolve writes the status to Sentry and reads it back; remove only takes the "
                    "card off this board — nothing is ever deleted in Sentry"}


@router.get("/api/sentry/issues/status")
async def issues_status(ids: str = ""):
    """What Sentry says about these issues right now, so a reload can redraw a resolved card with a
    status it did not invent. Up to 10 ids, comma separated. Read-only: no guard, same as the rest
    of the Sentry reads on this server."""
    wanted = [i.strip() for i in ids.split(",") if i.strip()][:MAX_STATUS_IDS]
    bad = [i for i in wanted if not sentry_client.ISSUE_ID.match(i)]
    if bad:
        return _error("bad_request", f"issue ids must be digits: {', '.join(bad[:3])}", 422)
    st = sentry.state()
    if not wanted:
        return {"available": st["configured"], "reason": st["reason"], "issues": []}
    if not st["configured"]:
        return {"available": False, "reason": st["reason"], "issues": []}
    try:
        out, unreadable = await _read_many(wanted)
    except sentry_client.SentryError as e:
        # Sentry throttling or blinking is a passing condition, not a broken board: answer the
        # shape the page already draws, with the reason in it, rather than a 5xx in the console.
        # The cards stay exactly as they were and the next load re-reads them.
        return {"available": False, "reason": e.detail, "issues": [],
                "unreadable": [{"id": i, "reason": e.detail} for i in wanted], "retryable": e.retryable}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"this server failed talking to Sentry ({type(e).__name__})",
                "issues": [], "unreadable": [{"id": i, "reason": "unreadable"} for i in wanted], "retryable": True}
    return {"available": True, "reason": None, "issues": out, "unreadable": unreadable}


@router.post("/api/sentry/issues/{issue_id}/resolve")
async def resolve_issue(request: Request, issue_id: str, body: dict = Body(default=None)):
    """Mark the issue resolved IN SENTRY, then read it back. The card stays on the board, struck
    through, wearing the status Sentry answered with. `status` may be "ignored" (Sentry's archive)
    or "unresolved" (undo a mis-press) instead."""
    if (no := _guard(request, "POST /api/sentry/issues/{id}/resolve")) is not None:
        return no
    if not sentry_client.ISSUE_ID.match(issue_id or ""):
        return _error("bad_request", "issue id must be digits", 422)
    status = (body or {}).get("status", "resolved")
    if status not in WRITABLE:
        return _error("bad_request", f"status must be one of {', '.join(WRITABLE)}", 422)
    try:
        return await _set_and_read(issue_id, status)
    except Exception as e:  # noqa: BLE001
        return _upstream(e)


@router.post("/api/sentry/issues/{issue_id}/remove")
async def remove_issue(request: Request, issue_id: str):
    """[remove] on the board. It does NOT delete anything.

    We read the issue; if Sentry does not already call it resolved we PUT resolved; then we read it
    back once more and answer with Sentry's word. `may_remove` is true only when Sentry itself says
    resolved — the card is allowed off the board on the strength of Sentry's answer, not the press.

    Why resolved and not archived: "ignored" is Sentry for *stop telling me about this*, and setting
    it would take the issue back OUT of resolved. The beam says the bug is fixed, so the state we
    leave behind in Sentry is the one that says the same thing.
    """
    if (no := _guard(request, "POST /api/sentry/issues/{id}/remove")) is not None:
        return no
    if not sentry_client.ISSUE_ID.match(issue_id or ""):
        return _error("bad_request", "issue id must be digits", 422)
    try:
        before = await _read(issue_id)
        after = before if before["resolved"] else await _set_and_read(issue_id, "resolved")
    except Exception as e:  # noqa: BLE001
        return _upstream(e)
    return {**after, "was_already_resolved": before["resolved"],
            "may_remove": after["resolved"] is True,
            "removed_from_board": after["resolved"] is True,
            "deleted_in_sentry": False, "kept_in_sentry": True,
            "what_we_did": ("it was already resolved in Sentry; only the card leaves this board"
                            if before["resolved"] else
                            "resolved in Sentry (read back), then the card leaves this board")
            if after["resolved"] else
            "Sentry does not report this issue as resolved, so the card stays"}
