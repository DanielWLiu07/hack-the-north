"""telemetry_api.py — /telemetry: the telemetry board. Every capture's quality gate next to the
robot's own motion around the shutter, so "was the robot still when it looked?" is one glance.

    GET /api/telemetry/board?limit=12   captures newest first: gate, telemetry ±2 s, spike, sentry
    GET /api/telemetry/sentry/{id}      that capture in Sentry: the trace as a stage waterfall, and
                                        the issues tagged with it (read-only, via sentry_client.py)
    GET /api/seer/status                can [ask Seer] work, and if not why — BEFORE it is pressed
    POST /api/seer/ask {capture_id}     docs/26-seer-embodied.md: find the capture's Sentry issue, start
                                        Seer on it, poll -> {state: "verdict" | "stumped", reason, …}.
                                        A `stumped` is an ANSWER (HTTP 200), never an error and never faked.
    GET /telemetry                      the page (pages/telemetry.html)
    GET /pages/seer/{path}              the Seer scene's assets (js / json / glb / png / webp / html)

server.py mounts it:  telemetry_api.init(es); app.include_router(telemetry_api.router)

Data rules are store.py's: Elasticsearch first, the fixture file only when ES is UNAVAILABLE and
always labelled (`source`), and a value that was not recorded is null — never a made-up number.
No ES|QL is sent from here (plain `_search` with an explicit `size`); if an ES|QL query is ever
added it must carry an explicit LIMIT — without one ES|QL silently truncates at 1000 rows.
"""
from __future__ import annotations

import asyncio
import os
import math
from datetime import timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import FileResponse, JSONResponse

import sentry_client
import store

try:                      # repo-root obs.py; optional
    import obs
except ImportError:       # pragma: no cover
    obs = None

PAGES = Path(__file__).resolve().parent / "pages"
SEER = (PAGES / "seer").resolve()
SEER_TYPES = {".js": "text/javascript", ".json": "application/json", ".glb": "model/gltf-binary",
              ".png": "image/png", ".webp": "image/webp", ".html": "text/html"}
SEER_PRIVATE = {"tools", "reference"}            # the generator scripts and the reference art stay off the wire

SIGNALS = ("tilt_rate", "odom_residual")         # the two the board charts (pitch rides the live strip)
UNITS = {"tilt_rate": "rad/s", "odom_residual": "m", "pitch": "rad"}
WINDOW_S = store.TELEMETRY_WINDOW_S              # ±2 s, the same window the capture page shows
LATCH_MS = 100                                   # tilt_rate_max is the peak |tilt_rate| within ±100 ms of the latch
MAX_POINTS = 200
RETRY_WITHIN_S = 60                              # store.capture's rule: a REJECT followed within a minute was retried

router = APIRouter()
sentry = sentry_client.SentryClient()              # makes no call while Sentry is parked (see its state())
_limit = asyncio.Semaphore(6)                    # a board is ~2 queries per capture: do not stampede the cluster


def init(es) -> None:
    store.init(es)


def _error(code: str, detail: str, status: int, retryable: bool = False) -> JSONResponse:
    """docs/16-api.md §2.7 — one error shape everywhere."""
    return JSONResponse({"error": code, "detail": detail, "retryable": retryable}, status_code=status)


def _upstream(e: Exception) -> JSONResponse:
    return _error(getattr(e, "code", "internal_error"), str(getattr(e, "detail", e)),
                  int(getattr(e, "status", 500)), bool(getattr(e, "retryable", False)))


def decimate(points: list[list[float]], max_points: int = MAX_POINTS) -> list[list[float]]:
    """MIN/MAX-PRESERVING decimation. The series is cut into max_points // 2 consecutive buckets
    and each bucket contributes its minimum AND its maximum sample, in time order. A one-sample
    spike is by definition some bucket's extreme, so it always survives with its true value and
    time; striding or averaging would erase exactly the sample this board exists to show.
    Every returned point is a REAL sample — nothing is interpolated."""
    n = len(points)
    if n <= max_points:
        return points
    buckets = max(1, max_points // 2)
    out: list[list[float]] = []
    for b in range(buckets):
        chunk = points[b * n // buckets:(b + 1) * n // buckets]
        if not chunk:
            continue
        lo, hi = min(chunk, key=lambda p: p[1]), max(chunk, key=lambda p: p[1])
        for p in sorted({id(lo): lo, id(hi): hi}.values(), key=lambda p: p[0]):
            out.append(p)
    return out


def find_spike(tilt: list[list[float]]) -> dict | None:
    """The largest |tilt_rate| in the window, if it crosses the gate's 0.05 rad/s. Computed on the
    RAW samples, before decimation."""
    if not tilt:
        return None
    at_ms, value = max(tilt, key=lambda p: abs(p[1]))
    limit = store.GATE["tilt_rate_max"]["value"]
    if abs(value) < limit:
        return None
    return {"signal": "tilt_rate", "at_ms": at_ms, "value": value, "threshold": limit,
            "in_latch_window": abs(at_ms) <= LATCH_MS}


def _p95(values: list[float]) -> float | None:
    """Nearest-rank percentile: always one of the recorded values, never an interpolation."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


async def _telemetry(shutter: str) -> dict:
    t0 = store._ts(shutter)
    window = (store._iso(t0 - timedelta(seconds=WINDOW_S)), store._iso(t0 + timedelta(seconds=WINDOW_S)))
    async with _limit:
        samples, _ = await store._find("robot-telemetry", {"signal": list(SIGNALS)}, time_range=window,
                                       size=4000, label="board.telemetry")
    raw: dict[str, list[list[float]]] = {}
    for s in samples:
        v = store._num(s.get("value"))
        if v is not None:
            raw.setdefault(s["signal"], []).append(
                [round((store._ts(s["@timestamp"]) - t0).total_seconds() * 1000, 1), v])
    for pts in raw.values():
        pts.sort(key=lambda p: p[0])
    return {"recorded": bool(raw), "window_s": WINDOW_S, "latch_ms": LATCH_MS, "shutter_ts": shutter,
            "units": {k: UNITS[k] for k in raw}, "samples": {k: len(v) for k, v in raw.items()},
            "decimated": any(len(v) > MAX_POINTS for v in raw.values()),
            "signals": {k: decimate(v) for k, v in raw.items()}, "spike": find_spike(raw.get("tilt_rate") or [])}


async def _origin(capture_id: str) -> tuple[bool, dict]:
    """Two different questions about one capture, from one read:
    never_ran   — fake/scene_gen wrote it (the capture page uses the same rule): Sentry never saw its trace
                  id, so no link may be built from it;
    provenance  — who wrote what a judge reads (store.provenance). scripts/story_demo captures DID run and
                  DO have a real trace, and their text and numbers are still scripted: both must be said."""
    async with _limit:
        obs_docs, _ = await store._find("room-observations", {"capture_id": capture_id}, size=1, label="board.synthetic")
    model = next((o.get("vlm_model") for o in obs_docs if o.get("vlm_model")), None)
    return model == "fake/scene_gen", store.provenance(model, capture_id)


async def _is_synthetic(capture_id: str) -> bool:
    return (await _origin(capture_id))[0]


def sentry_doorways(doc: dict, event: dict | None, link: dict, *, real: bool, org: str | None) -> dict:
    """What a judge would paste into Sentry's search to find this capture, and the pages it opens.
    obs.capture_scope() tags every span, log line and issue of a capture with `capture_id` (and
    `commit_sha` once there is one), so that string is the join key across Sentry's products.
    Links exist only for REAL captures: a synthetic one never ran, so nothing in Sentry carries its
    tags and a link would open an empty search."""
    tags = {"capture_id": doc.get("capture_id")}
    sha = doc.get("commit_sha") or (event or {}).get("commit_sha")
    if sha:
        tags["commit_sha"] = sha
    search = f"capture_id:{tags['capture_id']}"
    why = None if real else ("synthetic capture — it never ran, so nothing in Sentry carries these tags"
                             if link.get("trace_id") else "no sentry_trace_id on this capture's documents")
    base = f"https://{org}.sentry.io" if (real and org) else None
    q = quote(search, safe="")
    return {"tags": tags, "search": search, "why_no_links": why if not base else None,
            "trace": link.get("url"),
            "issues": base and f"{base}/issues/?query={q}",
            "logs": base and f"{base}/explore/logs/?query={q}",
            "replays": base and f"{base}/replays/"}


FAILURE_EVENTS = ("capture_rejected", "failed_op")


def failures(clouds: list[dict], events: list[dict], gates: dict, links: dict, limit: int = 8) -> list[dict]:
    """The FAILURE ROWS (docs/26): a rejected capture or a failed op, newest first. From room-events
    (`capture_rejected` / `failed_op`) and from captures whose gate failed but left no event. `kind`
    and `detail` are read off the documents — a gate failure names the value that failed."""
    rows, seen = [], set()
    cloud_of = {c["capture_id"]: c for c in clouds}
    def gate_detail(cid: str) -> str | None:
        g = gates.get(cid)
        if not g or not g["failing"]:
            return None
        n = g["failing"][0]
        v, rule = g["values"][n], g["thresholds"][n]
        shown = f"{v * 100:.1f} %" if n == "coverage" else f"{v:.3g} {rule['unit']}".strip()
        return f"{n} {shown} — the gate needs {rule['op']} {rule['value'] * 100:g} %" if n == "coverage" \
            else f"{n} {shown} — the gate needs {rule['op']} {rule['value']:g}"
    for e in events:
        if e.get("event_type") not in FAILURE_EVENTS:
            continue
        cid = e.get("capture_id")
        key = (e.get("event_type"), cid, e.get("@timestamp"))
        if key in seen:
            continue
        seen.add(key)
        rows.append({"kind": e["event_type"], "capture_id": cid, "ts": e.get("@timestamp") or (cloud_of.get(cid) or {}).get("@timestamp"),
                     "detail": gate_detail(cid) if e["event_type"] == "capture_rejected" and gate_detail(cid) else (e.get("outcome") or e.get("message")),
                     "from": "room-events"})
    have = {r["capture_id"] for r in rows if r["kind"] == "capture_rejected"}
    for c in clouds:
        cid = c["capture_id"]
        if cid not in have and gates[cid]["pass"] is False:
            rows.append({"kind": "capture_rejected", "capture_id": cid, "ts": c["@timestamp"], "detail": gate_detail(cid), "from": "room-clouds (gate values)"})
    rows.sort(key=lambda r: r["ts"] or "", reverse=True)
    for r in rows[:limit]:
        cid = r["capture_id"]
        r["id"] = f"{r['kind']}:{cid}:{r['ts']}"
        r["capture_url"] = f"/capture/{cid}" if cid and sentry_client.CAPTURE_ID.match(cid) else None
        r["trace"] = links.get(cid) or {"url": None, "trace_id": None, "why_no_link": "this failure names no capture"}
    return rows[:limit]


async def board(limit: int) -> dict:
    (clouds, source), (events, _) = await asyncio.gather(
        store._find("room-clouds", {}, size=500, newest_first=True, label="board.captures"),
        store._find("room-events", {}, size=500, newest_first=True, label="board.events"))
    clouds = [c for c in clouds if c.get("capture_id") and c.get("@timestamp")]
    event_of = {}
    for e in events:
        if e.get("capture_id"):
            event_of.setdefault(e["capture_id"], e)

    gates = {c["capture_id"]: store.gate(store._num(c.get("skew_ms")), store._num(c.get("tilt_rate_max")),
                                         store._num(c.get("coverage_pct")),
                                         c.get("quality_ok") if isinstance(c.get("quality_ok"), bool) else None)
             for c in clouds}

    # retry relationships over the WHOLE timeline (oldest first), before cutting to `limit`
    timeline = clouds[::-1]
    retry, retry_of = {}, {}
    for a, b in zip(timeline, timeline[1:]):
        rejected = gates[a["capture_id"]]["pass"] is False or \
            (event_of.get(a["capture_id"]) or {}).get("event_type") == "capture_rejected"
        close = abs((store._ts(b["@timestamp"]) - store._ts(a["@timestamp"])).total_seconds()) <= RETRY_WITHIN_S
        if rejected and close:
            retry[a["capture_id"]], retry_of[b["capture_id"]] = b["capture_id"], a["capture_id"]

    shown = clouds[:limit]
    stack = sentry.state()
    tel, synthetic = await asyncio.gather(
        asyncio.gather(*[_telemetry(c["@timestamp"]) for c in shown]),
        asyncio.gather(*[_origin(c["capture_id"]) for c in shown]))

    captures, links = [], {}
    for c, t, (synth, origin) in zip(shown, tel, synthetic):
        cid, e = c["capture_id"], event_of.get(c["capture_id"])
        real = source == "elasticsearch" and not synth
        link = store.sentry_link([c, e], real=real)
        links[cid] = {"url": link["url"], "trace_id": link["trace_id"], "why_no_link": link["why_no_link"]}
        captures.append({
            "capture_id": cid, "ts": c["@timestamp"], "commit_sha": c.get("commit_sha") or None,
            "synthetic": synth, "provenance": origin, "gate": gates[cid],
            "sentry": {**link, **sentry_doorways(c, e, link, real=real, org=stack["org"])},
            "event": e and {"event_type": e.get("event_type"), "message": e.get("message"),
                            "outcome": e.get("outcome"), "branch": e.get("branch"),
                            "moved": len(e.get("objects_moved") or []), "added": len(e.get("objects_added") or []),
                            "removed": len(e.get("objects_removed") or [])},
            "retry": retry.get(cid), "retry_of": retry_of.get(cid), "telemetry": t})

    tilts = [(g["values"]["tilt_rate_max"], cid) for cid, g in gates.items() if g["values"]["tilt_rate_max"] is not None]
    skews = [g["values"]["skew_ms"] for g in gates.values() if g["values"]["skew_ms"] is not None]
    worst = max(tilts, default=None)
    return {
        "source": source, "limit": limit, "captures": captures,
        "sentry_stack": stack, "seer": sentry.seer_status(),
        "failures": failures(clouds, events, gates, links),
        "summary": {"captures": len(clouds), "rejected": sum(1 for g in gates.values() if g["pass"] is False),
                    "passed": sum(1 for g in gates.values() if g["pass"] is True),
                    "worst_tilt_rate_max": worst and {"value": worst[0], "capture_id": worst[1]},
                    "p95_skew_ms": _p95(skews), "skew_samples": len(skews),
                    "thresholds": store.GATE, "shown": len(captures)},
    }


@router.get("/api/telemetry/board")
async def get_board(limit: int = Query(12, ge=1, le=60)):
    try:
        if obs is not None:
            with obs.span("telemetry.board", "board", limit=limit):
                return await board(limit)
        return await board(limit)
    except Exception as e:  # noqa: BLE001
        return _upstream(e)


@router.get("/api/telemetry/sentry/{capture_id}")
async def capture_in_sentry(capture_id: str):
    """The capture as Sentry sees it: the trace's stage waterfall and the issues tagged with it.
    Paused / unconfigured Sentry answers in the §2.7 shape (503 sentry_paused) and NO call is made;
    a synthetic or fixture capture answers available: false — its trace id never existed in Sentry."""
    if not sentry_client.CAPTURE_ID.match(capture_id):
        return _error("bad_request", "capture_id must look like cap_0912", 422)
    try:
        clouds, source = await store._find("room-clouds", {"capture_id": capture_id}, size=1, label="board.capture")
        if not clouds:
            return _error("not_found", f"no capture {capture_id}", 404)
        synth = await _is_synthetic(capture_id)
        link = store.sentry_link([clouds[0]], real=source == "elasticsearch" and not synth)
        if not link["url"]:
            return {"capture_id": capture_id, "available": False, "reason": link["why_no_link"], "waterfall": None, "issues": []}
        waterfall, issues = await asyncio.gather(sentry.trace_summary(link["trace_id"]), sentry.issues_for_capture(capture_id))
        return {"capture_id": capture_id, "available": True, "reason": None, "waterfall": waterfall, "issues": issues}
    except sentry_client.SentryError as e:
        return _upstream(e)
    except Exception as e:  # noqa: BLE001
        return _upstream(e)


@router.get("/api/seer/status")
async def seer_status():
    """Why [ask Seer] will or will not answer, before anyone presses it. Makes no network call."""
    return sentry.seer_status()


_FORWARDED = ("x-forwarded-for", "forwarded", "x-real-ip", "cf-connecting-ip")    # same test as server.py's inlet


@router.post("/api/seer/ask")
async def seer_ask(request: Request, body: dict = Body(...)):
    """docs/26-seer-embodied.md. The outcome is ALWAYS a 200: `verdict` with Seer's words, or `stumped`
    with the reason verbatim (paused, no issue, no endpoint, missing scope, timeout). Never faked."""
    capture_id, depth = body.get("capture_id"), body.get("context_depth", 40)
    if not isinstance(capture_id, str) or not sentry_client.CAPTURE_ID.match(capture_id):
        return _error("bad_request", "capture_id must look like cap_0912", 422)
    if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= 40:
        return _error("bad_request", "context_depth must be an integer 0..40 (obs.robot_failure attaches at most 40 breadcrumbs)", 422)
    # the site is reachable through a public tunnel: a stranger's press must not bill a Seer run. Tunnel
    # traffic arrives from 127.0.0.1 too, so it is told apart by its forwarding headers (WEB_PUBLIC_SEER=1 opens it).
    peer = request.client.host if request.client else ""
    local = peer in ("127.0.0.1", "::1", "testclient") and not any(h in request.headers for h in _FORWARDED)
    may_start = local or os.getenv("WEB_PUBLIC_SEER", "").strip() == "1"
    if obs is not None:
        with obs.capture_scope(capture_id), obs.span("seer.ask", capture_id, capture_id=capture_id, context_depth=depth):
            return await sentry.ask_seer(capture_id, context_depth=depth, may_start=may_start)
    return await sentry.ask_seer(capture_id, context_depth=depth, may_start=may_start)


@router.get("/telemetry", include_in_schema=False)
async def telemetry_page():
    return FileResponse(PAGES / "telemetry.html", headers={"Cache-Control": "no-cache"})


@router.get("/pages/seer/{path:path}", include_in_schema=False)
async def seer_asset(path: str):
    """The Seer scene's own files. Whitelisted suffixes only, nothing under tools/ or reference/,
    and the resolved path must still be inside pages/seer/ (no `..`, no symlink escape)."""
    parts = PurePosixPath(path).parts
    media = SEER_TYPES.get(PurePosixPath(path).suffix.lower())
    if (not parts or media is None or any(p in ("..", ".") or p.startswith(".") or p in SEER_PRIVATE for p in parts)
            or "\\" in path):
        return _error("not_found", "Not Found", 404)
    target = (SEER / path).resolve()
    if SEER not in target.parents or not target.is_file():
        return _error("not_found", "Not Found", 404)
    return FileResponse(target, media_type=media, headers={"Cache-Control": "no-cache"})
