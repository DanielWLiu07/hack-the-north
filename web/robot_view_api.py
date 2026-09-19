"""Live view: what the robot's head camera sees, right now, on the website.

    GET /live                       the page (pages/live.html)
    GET /api/robot/view.mjpg        the stream — multipart/x-mixed-replace, an <img> plays it with no JS
    GET /api/robot/view.jpg         the newest frame, once (a client that cannot hold a stream open)
    GET /api/robot/view/status      is the robot answering · frame age · fps · how many are watching
    GET /api/robot/link             the link and the robot, as facts: address, reachable, rtt, /healthz, and whether
                                    Sentry is watching it from outside (scripts/robot_sentry_watch.py)

WHY THE LAPTOP SITS IN THE MIDDLE. The browser cannot fetch from the robot: its address is a private
one on the venue wifi (or a tailnet 100.x), and a page served over https may not load http pixels. More
important is what the robot can afford: every camera read there copies a 4 MB shared-memory slot twice,
on the machine that is balancing the robot (robot/bbos.py). So however many people open /live — a judge,
three phones, a teammate — the robot sees exactly ONE reader, at VIEW_FPS, and NONE when nobody is
watching: the poller starts with the first viewer and stops IDLE_STOP_S after the last one leaves.

This is a PREVIEW, not a capture. It asks the robot for `GET /camera/<name>.jpg` (the newest frame, no
quality gate, no capture id, nothing announced on /stream — docs/16). Nothing here is written anywhere:
not to Elasticsearch, not to room.git. A frame on this page is not evidence of anything; a capture is.

PI_HOST is read from the .env FILE at poll time, not from this process's environment: `scripts/pi_link.py
use tailnet` repoints the robot's address (docs/33) and must not need the web server restarted to follow.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

router = APIRouter()
log = logging.getLogger("gitspace.web.robot_view")

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE.parent / ".env"
CAMERA = os.getenv("ROBOT_VIEW_CAMERA", "cam0")
VIEW_FPS = min(4.0, max(0.5, float(os.getenv("ROBOT_VIEW_FPS", "2"))))     # 4 is the robot's own cap
IDLE_STOP_S = 10.0            # nobody watching for this long -> stop asking the robot for frames
STALE_S = 3.0                 # a frame older than this is not "live"; the page says so instead of lying
BOUNDARY = b"gitspaceframe"


def _unsampled() -> dict[str, str]:
    """`sentry-trace: <trace>-<span>-0` — "the caller decided not to sample this". The robot runs sentry_sdk's FastAPI
    integration at traces_sample_rate 1.0 with no route filter (obs.py), so every preview poll would be a
    transaction: 2 a second for as long as anyone has /live open. The SDK inherits a parent's decision, so this
    header costs the robot nothing and sends Sentry nothing. Measured against a fake DSN: 6 polls, 0 transactions.
    Captures are untouched — they are traced by whoever calls POST /capture."""
    return {"sentry-trace": f"{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-0"}


def init(es) -> None:         # the router contract (web/server.py mount_router). Nothing here touches ES.
    return None


# ── where the robot is ────────────────────────────────────────────────────────────
_env_cache: tuple[float, dict[str, str]] = (0.0, {})


def _robot_base() -> str:
    """http://<PI_HOST>:<PI_PORT>, from the .env file as it is NOW (mtime-cached)."""
    global _env_cache
    try:
        mtime = ENV_FILE.stat().st_mtime
        if mtime != _env_cache[0]:
            found = {}
            for line in ENV_FILE.read_text().splitlines():
                m = re.match(r"^\s*(PI_HOST|PI_PORT|PI_LINK)=([^#\s]*)", line)
                if m:
                    found[m.group(1)] = m.group(2).strip().strip("\"'")
            _env_cache = (mtime, found)
    except OSError:
        pass
    env = _env_cache[1]
    host = env.get("PI_HOST") or os.getenv("PI_HOST", "")
    port = env.get("PI_PORT") or os.getenv("PI_PORT", "8080")
    return f"http://{host}:{port}" if host else ""


# ── one poller, many viewers ──────────────────────────────────────────────────────
class View:
    def __init__(self) -> None:
        self.frame: bytes = b""
        self.seq = 0                          # bumps on every NEW frame; a viewer waits for it to move
        self.got_at = 0.0                     # monotonic, when the newest frame arrived HERE
        self.robot_age_ms: int | None = None  # the robot's own X-Frame-Age-Ms for it
        self.boot_id = ""
        self.error = "no viewer yet"          # why there is no live frame, in words for the page
        self.viewers = 0
        self.frames = 0
        self.bytes = 0
        self.polls = 0
        self.last_viewer_at = 0.0
        self._task: asyncio.Task | None = None
        self._changed = asyncio.Condition()

    def ensure_running(self) -> None:
        self.last_viewer_at = time.monotonic()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="robot-view-poller")

    async def _run(self) -> None:
        period, backoff = 1.0 / VIEW_FPS, 1.0
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=2.5)) as client:
            while time.monotonic() - self.last_viewer_at < IDLE_STOP_S or self.viewers:
                started = time.monotonic()
                base = _robot_base()
                if not base:
                    self.error, wait = "PI_HOST is not set in .env (scripts/pi_link.py status)", 5.0
                else:
                    wait = period
                    try:
                        self.polls += 1
                        r = await client.get(f"{base}/camera/{CAMERA}.jpg", headers=_unsampled())
                        if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
                            age = r.headers.get("x-frame-age-ms", "")
                            self.robot_age_ms = int(age) if age.lstrip("-").isdigit() else None
                            self.boot_id = r.headers.get("x-boot-id", self.boot_id)
                            fresh = r.content != self.frame       # the robot caches: identical bytes = no new frame
                            self.frame, self.got_at, self.error, backoff = r.content, time.monotonic(), "", 1.0
                            if fresh:
                                self.seq += 1
                                self.frames += 1
                                self.bytes += len(r.content)
                                async with self._changed:
                                    self._changed.notify_all()
                        else:
                            try:
                                doc = r.json()
                            except ValueError:
                                doc = {}
                            if r.status_code == 404 and "error" not in doc:
                                # FastAPI's own 404 ({"detail": "Not Found"}): the ROUTE is missing, not the camera
                                self.error, wait = ("the robot's server predates the live view (no GET /camera/<name>.jpg)"
                                                    " — ./scripts/push_to_pi.sh <user>@<robot> --start"), 5.0
                            else:
                                why = doc.get("detail") or doc.get("error") or ""
                                self.error, wait = f"robot answered {r.status_code}: {why}"[:200], 2.0
                    except httpx.HTTPError as e:
                        self.error = f"robot unreachable at {base} — {type(e).__name__}"
                        wait, backoff = backoff, min(backoff * 2, 5.0)      # a dead robot is asked less, not more
                await asyncio.sleep(max(0.0, wait - (time.monotonic() - started)))
        self.error = "idle — nobody is watching, so the robot is not being asked for frames"

    async def next_frame(self, after_seq: int, timeout: float) -> tuple[int, bytes]:
        """The newest frame once it is newer than `after_seq`. A slow viewer skips frames; it never queues them."""
        async with self._changed:
            try:
                await asyncio.wait_for(self._changed.wait_for(lambda: self.seq != after_seq), timeout)
            except asyncio.TimeoutError:
                pass
        return self.seq, self.frame

    def status(self) -> dict:
        age = (time.monotonic() - self.got_at) if self.got_at else None
        live = bool(self.frame) and age is not None and age < STALE_S and not self.error
        return {"live": live, "camera": CAMERA, "robot": _robot_base(), "boot_id": self.boot_id,
                "frame_age_s": None if age is None else round(age, 2), "robot_frame_age_ms": self.robot_age_ms,
                "target_fps": VIEW_FPS, "viewers": self.viewers, "frames": self.frames, "polls": self.polls,
                "frame_kb": round(len(self.frame) / 1024) if self.frame else 0, "error": self.error or None}


view = View()


def _untrace() -> None:
    """A stream lasts for hours; as a Sentry transaction that is one span that never finishes (server.py)."""
    try:
        import sentry_sdk
        tx = sentry_sdk.get_current_scope().transaction
        if tx is not None:
            tx.sampled = False
    except Exception:  # noqa: BLE001 -- observability must never break the stream
        pass


# ── routes ────────────────────────────────────────────────────────────────────────
@router.get("/api/robot/view.mjpg")
async def view_mjpg() -> StreamingResponse:
    _untrace()

    async def body():
        view.viewers += 1
        seq = -1
        try:
            while True:
                view.ensure_running()
                new_seq, frame = await view.next_frame(seq, timeout=5.0)
                if new_seq == seq or not frame:
                    continue                  # nothing new in 5 s: keep the socket, the page shows why
                seq = new_seq
                yield (b"--" + BOUNDARY + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
        finally:
            view.viewers -= 1
            view.last_viewer_at = time.monotonic()

    return StreamingResponse(body(), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY.decode()}",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/api/robot/view.jpg")
async def view_jpg():
    view.ensure_running()
    if not view.frame or time.monotonic() - view.got_at > STALE_S:
        await view.next_frame(view.seq, timeout=3.0)
    if not view.frame or time.monotonic() - view.got_at > STALE_S:
        return JSONResponse({"error": "no_live_frame", "detail": view.error or "no frame from the robot yet",
                             "retryable": True}, status_code=503)
    return Response(view.frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/api/robot/view/status")
async def view_status() -> dict:
    return view.status()


# ── the link, as facts ────────────────────────────────────────────────────────────
WATCH_STATE = Path(os.getenv("ROBOT_WATCH_STATE", "~/.cache/gitspace/robot-watch.json")).expanduser()
_link_cache: tuple[float, dict] = (0.0, {})


def _watch_state() -> dict:
    """What scripts/robot_sentry_watch.py last wrote. Stale (> 3 checks old) or absent = NOT watching: a page
    that says Sentry is watching because a file exists would be the exact lie this panel is here to prevent."""
    try:
        st = json.loads(WATCH_STATE.read_text())
    except (OSError, ValueError):
        return {"watching": False, "reason": "scripts/robot_sentry_watch.py is not running"}
    age = time.time() - float(st.get("checked_at") or 0)
    if not st.get("watching") or age > 3 * float(st.get("every_s") or 5) + 5:
        return {"watching": False, "reason": f"the watcher stopped {age:.0f} s ago — start scripts/robot_sentry_watch.py"}
    return {"watching": True, "sentry_live": bool(st.get("sentry_live")), "dry_run": bool(st.get("dry_run")),
            "checked_s_ago": round(age, 1), "healthy": st.get("healthy"), "open": st.get("open") or {},
            "pending": st.get("pending") or {}, "filed": st.get("filed") or []}


@router.get("/api/robot/link")
async def link() -> dict:
    """One probe per 2 s however many pages ask; unsampled, so it is not a Sentry transaction on the robot."""
    global _link_cache
    if time.monotonic() - _link_cache[0] < 2.0 and _link_cache[1]:
        return {**_link_cache[1], "watch": _watch_state()}
    base = _robot_base()
    out: dict = {"link": _env_cache[1].get("PI_LINK") or "?", "robot": base.replace("http://", ""), "reachable": False,
                 "rtt_ms": None, "healthz": None, "error": None}
    if not base:
        out["error"] = "PI_HOST is not set in .env"
    else:
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=2.0)) as client:
                r = await client.get(f"{base}/healthz", headers=_unsampled())
            out["rtt_ms"] = round((time.monotonic() - t0) * 1000, 1)
            doc = r.json() if r.status_code == 200 else {}
            if "boot_id" in doc:
                out["reachable"], out["healthz"] = True, doc
            else:
                out["error"] = f"{out['robot']} answers, but it is not robot.server (HTTP {r.status_code})"
        except (httpx.HTTPError, ValueError) as e:
            out["error"] = (f"nothing answers at {out['robot']} ({type(e).__name__}) — the robot is off, or this laptop is "
                            f"not on a network that reaches it (PI_LINK={out['link']}; docs/33)")
    _link_cache = (time.monotonic(), out)
    return {**out, "watch": _watch_state()}


@router.get("/live", include_in_schema=False)
async def live_page() -> FileResponse:
    return FileResponse(HERE / "pages" / "live.html", headers={"Cache-Control": "no-cache"})
