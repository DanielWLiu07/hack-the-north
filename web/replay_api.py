"""replay_api.py — Robot Session Replay (docs/29-how-we-use-sentry.md ★).

Sentry replays browser sessions; this replays a robot, from logs that already exist:

    GET /api/replay/{capture_id}?before=8&after=2     the seconds around one shutter
    GET /api/replay/window?from=<iso>&to=<iso>        any stretch of telemetry (no capture needed)
    GET /replay/{capture_id}   GET /replay?from=&to=  the scrubber page (pages/replay.html)

    { t0_ts, window, path: [[t,x,y,yaw]…] | null, pitch: [[t,rad]…], signals: {name: [[t,v]…]},
      drift: [[t,m]…], anchors: […], spans: [{op,t0,t1,…}…], arm_spans: [{op,t0,t1,from,to}…],
      events: […], trace_url, … }

`t` is ALWAYS seconds from the shutter (negative = before it), on the wall clock that telemetry,
room-events and Sentry spans share — which is what lets the scrubber and the trace be one event in
two views.

What is measured and what is derived
  * path      DERIVED: differential-drive odometry over left_enc / right_enc (turns), wheel Ø 165 mm,
              wheelbase 425 mm (docs/02-hardware.md). It starts at (0,0,0): nothing stores where the robot
              WAS, so the path is a shape, not a place in the room. No encoder samples -> path is null and
              `path_info.reason` says where the nearest ones are. Never a made-up line.
  * drift     MEASURED: odom_residual is the odometry-vs-anchor disagreement the robot itself reports. The
              page draws it as the radius of doubt around the robot. Integration drift is shown, not hidden.
  * anchors   docs/29 says to anchor on "the capture poses we already store in room-clouds". The live index
              stores NO pose yet (fake/scene_gen.py writes `pose {x, y, yaw°}` the moment the mapping has the
              field), so today `anchors` is [] and `anchors_reason` says so. When a pose IS there, the path is
              PINNED to the room at the anchor nearest the shutter, and every other anchor in the window
              reports `gap_m`: how far the integrated path had wandered from where the robot really was.
              That gap is the integration drift, drawn — not hidden.
  * raw only  robot-telemetry downsamples (5-min buckets) after its backing index rolls over. A window that
              comes back as buckets cannot be replayed; `downsampled` says so instead of drawing 1 point.
  * spans     READ from Sentry for a real trace; a synthetic capture has none and says so.
"""
from __future__ import annotations

import math
import os
import re
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

import sentry_client
import store

try:                      # repo-root obs.py; optional, the page works without Sentry
    import obs
except ImportError:       # pragma: no cover
    obs = None

PAGES = Path(__file__).resolve().parent / "pages"
CAPTURE_ID = re.compile(r"^[a-z]+_[0-9]+$")
SIGNALS = ("pitch", "tilt_rate", "odom_residual", "left_enc", "right_enc", "motor_current_l", "motor_current_r", "balanced")
UNITS = {"pitch": "rad", "tilt_rate": "rad/s", "odom_residual": "m", "left_enc": "turns", "right_enc": "turns",
         "motor_current_l": "A", "motor_current_r": "A", "balanced": "0/1"}
WHEEL_RADIUS_M = float(os.getenv("ROBOT_WHEEL_RADIUS_M", "0.0825"))     # docs/02-hardware.md: 165 mm wheels
WHEEL_BASE_M = float(os.getenv("ROBOT_WHEEL_BASE_M", "0.425"))          # docs/02-hardware.md: 425 mm wheelbase
MAX_WINDOW_S = 30.0                       # 50 Hz x 8 signals x 30 s = 12 000 docs; past that, ask for less
RESET_TURNS = 2.0                         # a wheel cannot make 2 turns in 20 ms: that step is a counter reset
MOTION_OPS = ("arm.", "drive", "robot.drive", "robot.arm", "base.")   # spans that are physical motion (docs/29)
POSE_FIELDS = ("pose", "robot_pose", "capture_pose", "base_pose")     # none exists in room-clouds today

router = APIRouter()
sentry = sentry_client.SentryClient()     # makes no call while Sentry is parked


def init(es) -> None:
    store.init(es)


def _error(code: str, detail: str, status: int, retryable: bool = False) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail, "retryable": retryable}, status_code=status)


def _upstream(e: Exception) -> JSONResponse:
    return _error(getattr(e, "code", "internal_error"), str(getattr(e, "detail", e)),
                  int(getattr(e, "status", 500)), bool(getattr(e, "retryable", False)))


# ── pure: encoders -> path ─────────────────────────────────────────────────────────────────────
def _level(v: Any) -> float | None:
    """An encoder is a LEVEL (turns so far), not a spike: a downsampled bucket is read as its mean."""
    if isinstance(v, dict):
        n = v.get("value_count")
        return float(v["sum"]) / n if isinstance(v.get("sum"), (int, float)) and n else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def integrate(left: list[list[float]], right: list[list[float]], *, radius_m: float = WHEEL_RADIUS_M,
              base_m: float = WHEEL_BASE_M) -> dict:
    """[[t, turns]…] per wheel -> {"path": [[t, x, y, yaw]…] | None, "info": {…}}.
    Midpoint differential-drive odometry from (0, 0, yaw 0) at the first sample both wheels share."""
    info = {"method": "differential-drive odometry over left_enc / right_enc", "wheel_radius_m": radius_m,
            "wheel_base_m": base_m, "origin": "(0, 0, yaw 0) at the first sample — a shape, not a place in the room",
            "samples": 0, "distance_m": None, "turned_rad": None, "resets": 0, "reason": None}
    r_by_t = {round(t, 4): v for t, v in right}
    both = sorted((t, v, r_by_t[round(t, 4)]) for t, v in left if round(t, 4) in r_by_t)
    if len(both) < 2:
        info["reason"] = ("no left_enc / right_enc samples in this window" if not (left or right) else
                          "left_enc and right_enc share fewer than two timestamps in this window")
        return {"path": None, "info": info}
    x = y = yaw = dist = 0.0
    path = [[both[0][0], 0.0, 0.0, 0.0]]
    k = 2 * math.pi * radius_m
    for (_, l0, r0), (t, l1, r1) in zip(both, both[1:]):
        if abs(l1 - l0) > RESET_TURNS or abs(r1 - r0) > RESET_TURNS:
            info["resets"] += 1                         # the counter restarted: that step is not motion
        else:
            dl, dr = (l1 - l0) * k, (r1 - r0) * k
            dc, dth = 0.5 * (dl + dr), (dr - dl) / base_m
            x += dc * math.cos(yaw + dth / 2)
            y += dc * math.sin(yaw + dth / 2)
            yaw += dth
            dist += abs(dc)
        path.append([t, round(x, 5), round(y, 5), round(yaw, 5)])
    info.update(samples=len(path), distance_m=round(dist, 4), turned_rad=round(yaw, 4))
    return {"path": path, "info": info}


def pin(path: list[list[float]], anchors: list[dict]) -> dict | None:
    """Rigidly move the (0,0,0)-origin `path` so it passes through the anchor nearest t = 0, in place.
    Every OTHER anchor then gets `gap_m`: pinned path vs. that capture's stored pose = integration drift."""
    usable = [a for a in anchors if a.get("yaw_deg") is not None and path[0][0] - 0.5 <= a["t"] <= path[-1][0] + 0.5]
    if not usable:
        return None
    at = lambda t: min(path, key=lambda q: abs(q[0] - t))   # noqa: E731
    home = min(usable, key=lambda a: abs(a["t"]))
    _, xa, ya, tha = at(home["t"])
    turn = math.radians(home["yaw_deg"]) - tha
    c, sn = math.cos(turn), math.sin(turn)
    for q in path:
        dx, dy = q[1] - xa, q[2] - ya
        q[1], q[2], q[3] = round(home["x"] + c * dx - sn * dy, 5), round(home["y"] + sn * dx + c * dy, 5), round(q[3] + turn, 5)
    for a in anchors:
        a["pinned_here"] = a is home
        a["gap_m"] = None if a is home or a not in usable else round(math.dist(at(a["t"])[1:3], (a["x"], a["y"])), 4)
    return home


def nearest(series: list[list[float]], t: float) -> float | None:
    """The sample closest in time to `t` (series sorted by t)."""
    if not series:
        return None
    i = bisect_left(series, [t])
    best = min((c for c in (i - 1, i) if 0 <= c < len(series)), key=lambda c: abs(series[c][0] - t))
    return series[best][1]


# ── assembling one replay ──────────────────────────────────────────────────────────────────────
async def _encoder_gap(t_anchor: datetime) -> tuple[str, list[dict]]:
    """Where ARE the encoder samples, when the window has none? Two one-document lookups -> (words,
    [{where, ts, gap_s, href}] nearest first; href replays the 25 s of telemetry on that side)."""
    far = timedelta(days=30)
    before, _ = await store._find("robot-telemetry", {"signal": "left_enc"}, size=1, newest_first=True,
                                  time_range=(store._iso(t_anchor - far), store._iso(t_anchor)), label="replay.enc_before")
    after, _ = await store._find("robot-telemetry", {"signal": "left_enc"}, size=1,
                                 time_range=(store._iso(t_anchor), store._iso(t_anchor + far)), label="replay.enc_after")
    bits, near = [], []
    for docs, word in ((before, "before"), (after, "after")):
        if docs:
            at = store.when(docs[0])
            gap = abs((at - t_anchor).total_seconds())
            bits.append(f"{gap:.0f} s {word}" if gap < 600 else f"{gap / 60:.0f} min {word}")
            a, b = (at - timedelta(seconds=25), at) if word == "before" else (at, at + timedelta(seconds=25))
            near.append({"where": word, "ts": store._iso(at), "gap_s": round(gap, 1),
                         "href": f"/replay?from={quote(store._iso(a))}&to={quote(store._iso(b))}"})
    near.sort(key=lambda n: n["gap_s"])
    return (("the nearest encoder sample is " + " and ".join(bits)) if bits else "robot-telemetry holds no encoder samples at all"), near


async def _spans(trace: dict, t_anchor: datetime, synthetic: bool) -> tuple[list[dict], str | None]:
    tid = trace.get("trace_id")
    if not tid:
        return [], trace.get("why_no_link") or "no sentry_trace_id on this capture's documents"
    if synthetic:
        return [], "synthetic capture — its trace id was never recorded in Sentry"
    st = sentry.state()
    if not st["configured"]:
        return [], st["reason"]
    try:
        found = await sentry.trace_spans(tid)
    except sentry_client.SentryError as e:
        return [], f"Sentry could not be read — {e.code}: {e.detail}"
    if not found:
        return [], "Sentry has the trace id but returned no spans for it"
    epoch = t_anchor.timestamp()
    return [{"op": s["op"], "description": s["description"], "t0": round(s["start"] - epoch, 4), "t1": round(s["end"] - epoch, 4),
             "depth": s["depth"], "is_transaction": s["is_transaction"], "errors": s["errors"], "span_id": s["span_id"]}
            for s in found], None


async def build(t_anchor: datetime, before_s: float, after_s: float, *, capture: dict | None = None) -> dict:
    lo, hi = t_anchor - timedelta(seconds=before_s), t_anchor + timedelta(seconds=after_s)
    window = (store._iso(lo), store._iso(hi))
    rel = lambda d: round((store.when(d) - t_anchor).total_seconds(), 4)   # noqa: E731

    samples, source = await store._find("robot-telemetry", {"signal": list(SIGNALS)}, time_range=window,
                                        size=int(50 * len(SIGNALS) * (before_s + after_s)) + 200, label="replay.telemetry")
    signals: dict[str, list[list[float]]] = {}
    buckets = sum(1 for s in samples if isinstance(s.get("value"), dict))
    for s in samples:
        name = s.get("signal")
        v = _level(s.get("value")) if name in ("left_enc", "right_enc") else store._metric(s.get("value"))
        if v is not None:
            signals.setdefault(name, []).append([rel(s), v])

    odo = integrate(signals.get("left_enc") or [], signals.get("right_enc") or [])
    if odo["path"] is None and not (signals.get("left_enc") or signals.get("right_enc")):
        words, odo["info"]["nearest"] = await _encoder_gap(t_anchor)
        odo["info"]["reason"] += " — " + words

    # every capture inside the window: would-be anchors, and markers on the timeline
    clouds, _ = await store._find("room-clouds", {}, time_range=window, size=50, label="replay.captures")
    anchors, events = [], []
    for c in clouds:
        pose = next((c[f] for f in POSE_FIELDS if isinstance(c.get(f), dict)), None)
        if pose and all(isinstance(pose.get(k), (int, float)) for k in ("x", "y")):
            yaw = pose.get("yaw")                        # fake/scene_gen.py and room-objects: yaw in DEGREES
            anchors.append({"t": rel(c), "capture_id": c.get("capture_id"), "x": float(pose["x"]), "y": float(pose["y"]),
                            "yaw_deg": float(yaw) if isinstance(yaw, (int, float)) else None})
        events.append({"t": rel(c), "ts": c.get("@timestamp"), "type": "shutter", "capture_id": c.get("capture_id"),
                       "outcome": None if c.get("quality_ok") is None else ("ok" if c["quality_ok"] else "rejected"),
                       "message": None, "derived": False})
    logged, _ = await store._find("room-events", {}, time_range=(window[0], store._iso(hi + timedelta(seconds=30))),
                                  size=100, label="replay.events")
    for e in logged:                                     # a verdict lands seconds AFTER its shutter: keep it, clamp nothing
        events.append({"t": rel(e), "ts": e.get("@timestamp"), "type": e.get("event_type"), "capture_id": e.get("capture_id"),
                       "commit_sha": e.get("commit_sha"), "outcome": e.get("outcome"), "message": e.get("message"), "derived": False})
    if odo["path"] and anchors:
        home = pin(odo["path"], anchors)
        if home:
            odo["info"]["origin"] = f"pinned to the room at {home['capture_id']}'s stored capture pose (room-clouds.pose)"
            odo["info"]["frame"] = "room"
    limit = store.GATE["tilt_rate_max"]["value"]
    tilt = signals.get("tilt_rate") or []
    spike = max(tilt, key=lambda p: abs(p[1])) if tilt else None
    if spike and abs(spike[1]) >= limit:                 # DERIVED from the samples, and labelled as such
        events.append({"t": spike[0], "ts": store._iso(t_anchor + timedelta(seconds=spike[0])), "type": "tilt_spike",
                       "capture_id": None, "outcome": None, "derived": True,
                       "message": f"|tilt_rate| peaked at {abs(spike[1]):.3f} rad/s — the gate needs < {limit}"})
    events.sort(key=lambda e: e["t"])

    trace = (capture or {}).get("sentry") or {"trace_id": None, "url": None, "why_no_link": "a time window has no capture, so no trace"}
    spans, spans_reason = await _spans(trace, t_anchor, bool((capture or {}).get("synthetic"))) if capture else ([], trace["why_no_link"])
    arm = [{"op": s["op"], "description": s["description"], "t0": s["t0"], "t1": s["t1"], "from": None, "to": None}
           for s in spans if any((s["op"] or "").startswith(p) for p in MOTION_OPS)]

    return {
        "capture_id": (capture or {}).get("capture_id"), "source": source, "t0_ts": store._iso(t_anchor),
        "t0_is": "the shutter" if capture else "the start of the window",
        "window": {"before_s": before_s, "after_s": after_s, "from": window[0], "to": window[1]},
        "gate": (capture or {}).get("gate"), "synthetic": (capture or {}).get("synthetic"),
        # `synthetic` = the capture never ran (no Sentry trace). `provenance` = who wrote what is on screen: a
        # scripts/story_demo capture has a REAL trace and scripted numbers, and the page must say both.
        "provenance": (capture or {}).get("provenance"),
        "path": odo["path"], "path_info": odo["info"],
        "pitch": signals.get("pitch") or [], "signals": signals, "units": UNITS,
        "recorded": {name: len(signals.get(name) or []) for name in SIGNALS},
        "downsampled": None if not buckets else (f"{buckets} of {len(samples)} telemetry documents in this window are downsampled buckets, not 50 Hz "
                                                 "samples — robot-telemetry keeps raw samples only until its index rolls over, so this moment can no longer be replayed faithfully"),
        "drift": signals.get("odom_residual") or [],
        "drift_is": "odom_residual — the odometry-vs-anchor disagreement the robot reports (MEASURED, metres)",
        "anchors": anchors, "anchors_reason": None if anchors else
        "room-clouds stores no capture pose for this window (the live mapping has no `pose` field yet), so the path cannot be pinned to the room",
        "spans": spans, "spans_reason": spans_reason,
        "arm_spans": arm, "arm_spans_reason": None if arm else (spans_reason or "this trace has no arm.* / drive spans — the executor did not move the robot during it"),
        "events": events, "spike": {"t": spike[0], "value": spike[1], "threshold": limit} if spike and abs(spike[1]) >= limit else None,
        "trace_url": trace.get("url"), "trace": trace,
    }


# ── routes ─────────────────────────────────────────────────────────────────────────────────────
def _when(text: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@router.get("/api/replay/window")
async def replay_window(start: str = Query(..., alias="from"), end: str = Query(..., alias="to")):
    a, b = _when(start), _when(end)
    if a is None or b is None or not (0 < (b - a).total_seconds() <= MAX_WINDOW_S):
        return _error("bad_request", f"from / to must be ISO timestamps at most {MAX_WINDOW_S:.0f} s apart", 422)
    try:
        return await build(a, 0.0, (b - a).total_seconds())
    except Exception as e:  # noqa: BLE001
        return _upstream(e)


@router.get("/api/replay/{capture_id}")
async def replay(capture_id: str, before: float = Query(8.0, ge=0.5, le=25.0), after: float = Query(2.0, ge=0.5, le=5.0)):
    if not CAPTURE_ID.match(capture_id):
        return _error("bad_request", "capture_id must look like cap_0912", 422)
    try:
        if obs is not None:
            with obs.capture_scope(capture_id), obs.span("replay.build", capture_id, capture_id=capture_id):
                cap = await store.capture(capture_id)
                return await build(store._ts(cap["ts"]), before, after, capture=cap)
        cap = await store.capture(capture_id)
        return await build(store._ts(cap["ts"]), before, after, capture=cap)
    except store.NotFound:
        return _error("not_found", f"no capture {capture_id}", 404)
    except Exception as e:  # noqa: BLE001
        return _upstream(e)


@router.get("/replay", include_in_schema=False)
@router.get("/replay/{capture_id}", include_in_schema=False)
async def replay_page(capture_id: str | None = None):
    if capture_id is not None and not CAPTURE_ID.match(capture_id):
        return _error("not_found", "no such page", 404)
    return FileResponse(PAGES / "replay.html", headers={"Cache-Control": "no-cache"})
