#!/usr/bin/env python3
"""robot/server.py — the on-device process. The contract is docs/16-api.md; this file is routes.

    HTTP        POST /capture   GET /pose   POST /drive   POST /arm   POST /say   POST /led
                GET /camera/<name>.jpg   the latest picture for a live view — NOT a capture (no id, no gate)
    SSE         GET /events   everything structured, as text/event-stream: `curl -N` is a client,
                          it reconnects by itself and resumes from Last-Event-ID (robot/events.py)
    WebSocket   /stream   telemetry · job · log · capture_begin / capture_end / capture_rejected
                /frames   the pixels, binary, on their OWN connection (docs/16 §3b): 5 MB of
                          frames must never queue 10 msg/s of telemetry behind it

Every handler is a thin call downward: capture.py owns the cameras, jobs.py the actuators,
telemetry.py the tap. A capture blocks for a second or more, so it runs in a worker thread —
the event loop that feeds /stream never waits on a camera.

Sentry (obs.py, role `robot`): obs.init() runs before FastAPI() so the SDK's FastAPI integration
sees the app. It opens the transaction for each request and continues the laptop's trace from
its `sentry-trace` header, so `room status` and the Pi's latch are ONE waterfall (docs/16 §6).
capture.py tags it with the capture_id and records the quality gate on it.

No hardware needed — the cameras are with teammates:

    python -m robot.server --sim                   # synthetic recording, simulated robot
    python -m robot.server --replay session_0001/  # Sarah's collector folder, or pipeline recordings
    python -m robot.server --hardware              # D415 816612060665 · D435 938422076694 · cam0
    python -m robot.server --sim --once            # one capture, printed, no server
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import dataclasses
import importlib
import ipaddress
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if __name__ == "__main__":                  # obs reads SENTRY_ORG_SLUG at import: .env has to be first.
    try:                                    # Only as the entry point — importing this never reads .env,
        from dotenv import load_dotenv      # so a test can never reach the live Sentry by accident.
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

# module level, not inside create_app: with `from __future__ import annotations` FastAPI resolves
# "Request" against THESE globals, and an unresolved one silently becomes a query parameter
import anyio  # noqa: E402
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import JSONResponse, Response, StreamingResponse  # noqa: E402

import obs  # noqa: E402
from robot import capture as cap_mod  # noqa: E402
from robot import config as C  # noqa: E402
from robot import events as sse  # noqa: E402
from robot import sim  # noqa: E402
from robot.jobs import JobError, Jobs  # noqa: E402
from robot.telemetry import Telemetry  # noqa: E402

log = logging.getLogger("gitspace.robot")

FRAME_QUEUE = 256             # binary frames per /frames client (~20 captures); past that, oldest dropped


def err(status: int, code: str, detail: str, retryable: bool = False, **extra):
    """docs/16 §2.7: one error shape everywhere."""
    return JSONResponse({"error": code, "detail": detail, "retryable": retryable, **extra}, status_code=status)


class FrameBus:
    """/frames clients. Each has its own bounded queue, so one stalled laptop cannot hold frames
    for another, and nothing here can block the capture that produced them."""

    def __init__(self):
        self.clients: set[asyncio.Queue] = set()
        self.dropped = 0

    def join(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=FRAME_QUEUE)
        self.clients.add(q)
        return q

    def leave(self, q: asyncio.Queue) -> None:
        self.clients.discard(q)

    def send(self, blobs: list[bytes]) -> int:
        for q in self.clients:
            for b in blobs:
                if q.full():
                    q.get_nowait()
                    self.dropped += 1
                q.put_nowait(b)
        return len(self.clients)


class PeerAllowList:
    """Who may talk to the robot, by the address the TCP connection really came from. The API has no
    auth, and on it are a camera that sees people, /drive and /arm — on `0.0.0.0` that is offered to
    everyone on whatever wifi the robot joined. Binding to the tailnet address is the real fix
    (docs/16 §8); this is for when that is not possible yet. Pure ASGI, so the WebSockets and the SSE
    stream are covered too. No forwarding header is ever believed: nothing sits in front of this."""

    def __init__(self, app, allow: tuple[str, ...] = ()):
        self.app = app
        self.networks = [ipaddress.ip_network(a, strict=False) for a in allow]
        self.refused = 0

    def permits(self, host: str) -> bool:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False                           # no address, no entry
        ip = getattr(ip, "ipv4_mapped", None) or ip
        return any(ip in n for n in self.networks)

    async def __call__(self, scope, receive, send):
        if self.networks and scope["type"] in ("http", "websocket"):
            host = (scope.get("client") or ("", 0))[0]
            if not self.permits(host):
                self.refused += 1
                if self.refused in (1, 10, 100, 1000):
                    log.warning("refused %s (%d so far): not in ROBOT_ALLOW", host or "?", self.refused)
                if scope["type"] == "websocket":
                    return await send({"type": "websocket.close", "code": 1008})
                body = json.dumps({"error": "forbidden", "detail": "this address is not in ROBOT_ALLOW",
                                   "retryable": False}).encode()
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                return await send({"type": "http.response.body", "body": body})
        await self.app(scope, receive, send)


class StreamLog(logging.Handler):
    """docs/16 §3.1 LOG: what the Pi warns about reaches the laptop's screen (and its Sentry)."""

    def __init__(self, tel: Telemetry, iso):
        super().__init__(logging.WARNING)
        self.tel, self.iso = tel, iso

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.tel.publish({"t": "log", "level": "warn" if record.levelno < logging.ERROR else "error",
                              "msg": record.getMessage(), "ts": self.iso(time.monotonic())})
        except Exception:  # noqa: BLE001 -- logging must never raise
            pass


def sentry_state(live: bool) -> dict:
    """For /healthz: is Sentry on, and is it REFUSING us? Over quota Sentry answers 429 and the SDK
    drops every event of that category without a word — "no spans from the robot" with a perfect
    DSN. `rate_limited` names the categories being dropped right now (e.g. "transaction") and for
    how many more seconds. Reads the SDK's transport; never sends anything."""
    out = {"live": bool(live)}
    if live:
        try:
            import sentry_sdk
            limits = getattr(sentry_sdk.get_client().transport, "_disabled_until", {}) or {}
            now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            out["rate_limited"] = {str(k or "all"): int((v - now).total_seconds()) for k, v in limits.items() if v > now}
        except Exception as e:  # noqa: BLE001 -- a health page must not fail on an SDK internal
            out["rate_limited"] = f"unknown ({type(e).__name__})"
    return out


def telemetry_source(cfg: C.Config):
    """-> (source, SimBalance | None). The balance loop's reader is Sarah and Ryan's to supply
    (ROBOT_TELEMETRY_SOURCE=module:callable). Without one the tap records NaN — and the capture
    gate, given no tilt evidence, rejects every capture, which is the right answer."""
    if cfg.telemetry_source:
        mod, _, name = cfg.telemetry_source.partition(":")
        return getattr(importlib.import_module(mod), name), None
    if cfg.mode == "hardware":
        log.error("no ROBOT_TELEMETRY_SOURCE: tilt_rate is unmeasured, so the quality gate will reject "
                  "EVERY capture (docs/22 §4). Fix: robot/RUNBOOK.md §1 — "
                  "ROBOT_TELEMETRY_SOURCE=robot.balance_source:udp, then `python -m robot.check_source`.")
        return (lambda: {}), None
    balance = sim.SimBalance()
    return balance, balance


def create_app(cfg: C.Config | None = None, *, rig: cap_mod.CaptureRig | None = None,
               tel: Telemetry | None = None, time_scale: float = 1.0,
               sse_heartbeat_s: float = sse.HEARTBEAT_S) -> FastAPI:
    cfg = cfg or C.Config.from_env()
    live = obs.init("robot")                # BEFORE FastAPI(): the integration patches the app it sees
    balance = None
    if tel is None:
        source, balance = telemetry_source(cfg)
        tel = Telemetry(source=source)
    simulated = cfg.mode != "hardware"
    # On hardware nothing reads a real pose yet (robot/pose.py over bbos's slam.pose is not built), and
    # /drive cannot move this one. "none" says so: (0,0,0) labelled "sim" on real frames reads as a pose.
    base = sim.SimBase(source="sim" if simulated else "none")
    if not simulated:
        log.warning("pose_source is 'none': every capture carries the placeholder pose (0,0,0). Do not fuse "
                    "captures taken from different places until robot/pose.py exists (robot/RUNBOOK.md §5)")

    def balanced() -> bool:
        s = tel.recent(1)
        return bool(s and s[-1]["balanced"] is not None and s[-1]["balanced"] >= 0.5)

    jobs = Jobs(tel.publish, base, balanced, sim=simulated, time_scale=time_scale)
    bus = FrameBus()
    log_ = sse.EventLog(tel.boot_id)

    @asynccontextmanager
    async def lifespan(app):
        tel.start()                          # before any client, so the ring is already filling
        if app.state.rig is None:            # here, not at import: sim writes its recording to disk
            app.state.rig = cap_mod.build_rig(cfg, tel, base.read, on_event=tel.publish, traced=live)
        r = app.state.rig
        await asyncio.to_thread(r.open)      # the cameras are opened ONCE and held (docs/22 §7.1)
        handler = StreamLog(tel, r.iso)
        logging.getLogger("gitspace").addHandler(handler)
        tap = asyncio.create_task(log_.run(tel), name="sse-tap")     # /events' one tap on the telemetry
        log.info("robot up: mode=%s cameras=%s unavailable=%s", cfg.mode, r.available(), r.unavailable)
        try:
            yield
        finally:
            tap.cancel()
            await asyncio.gather(tap, return_exceptions=True)
            logging.getLogger("gitspace").removeHandler(handler)
            r.close()
            tel.stop()

    app = FastAPI(title="gitspace robot", version=C.FW, lifespan=lifespan)
    app.add_middleware(PeerAllowList, allow=cfg.allow)
    if not cfg.allow and not simulated and cfg.host in ("0.0.0.0", "::"):
        log.warning("OPEN to every device that can reach %s:%d — no auth, a camera that sees people, /drive, /arm. "
                    "Set ROBOT_HOST to the tailnet address, or ROBOT_ALLOW (robot/RUNBOOK.md §4)", cfg.host, cfg.port)
    app.state.rig, app.state.tel, app.state.jobs, app.state.bus = rig, tel, jobs, bus
    app.state.base, app.state.balance, app.state.cfg, app.state.events = base, balance, cfg, log_

    def hello() -> dict:
        r = app.state.rig
        return tel.hello(cameras=r.available(), rig=r.info(), arm=simulated, fw=C.FW, mode=cfg.mode,
                         simulated=simulated)

    async def body_of(request: Request) -> dict:
        raw = await request.body()
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise JobError("bad_request", f"body is not JSON: {e}") from e
        if not isinstance(data, dict):
            raise JobError("bad_request", "body must be a JSON object")
        return data

    # ── POST /capture ────────────────────────────────────────────────────────────
    @app.post("/capture")
    async def capture(request: Request):
        r: cap_mod.CaptureRig = app.state.rig
        try:
            body = await body_of(request)
            cameras, frames, quality = body.get("cameras"), body.get("frames"), body.get("quality")
            if cameras is not None and not (isinstance(cameras, list) and all(isinstance(c, str) for c in cameras)):
                raise JobError("bad_request", "cameras must be a list of camera names")
            if frames is not None and not (isinstance(frames, int) and 1 <= frames <= 16):
                raise JobError("bad_request", "frames must be an integer 1..16")
            if quality is not None and not (isinstance(quality, int) and 1 <= quality <= 100):
                raise JobError("bad_request", "quality must be an integer 1..100")
        except JobError as e:
            return err(e.status, e.code, e.detail)
        try:
            got = await asyncio.to_thread(r.capture, cameras, frames, quality)     # never on the event loop
        except cap_mod.Busy as e:
            return err(409, "busy", str(e), retryable=True)
        except cap_mod.CameraUnavailable as e:
            return err(503, "camera_unavailable", e.detail, retryable=True, camera=e.camera, available=r.available())
        except cap_mod.CaptureRejected as e:
            last = e.attempts[-1]
            return err(409, "capture_rejected", f"{len(e.attempts)} attempts failed the quality gate (docs/22 §4); "
                       f"the last by {', '.join(last.rejected_by)}", retryable=True,
                       attempts=[a.meta(r.iso) for a in e.attempts])
        meta, listing = got.meta(r.iso), got.frame_list(r.iso)
        # pixels go to /frames when a laptop is listening there, else inline: `curl` still works
        inline = bool(body.get("inline", not bus.clients))
        tel.publish({"t": "capture_begin", **meta, "frames_expected": len(got.frames)})
        clients = bus.send([f.pack(got.capture_id) for f in got.frames])
        tel.publish({"t": "capture_end", "capture_id": got.capture_id,
                     "frames_sent": len(got.frames) if clients else 0})
        if inline:
            for entry, f in zip(listing, got.frames):
                entry["jpeg_b64" if f.payload.kind == "color" else "png_b64"] = base64.b64encode(f.payload.data).decode()
        return {**meta, "frames": listing, "inline": inline, "frames_clients": clients}

    # ── GET /camera/<name>.jpg ───────────────────────────────────────────────────
    @app.get("/camera/{name}.jpg")
    async def camera_jpg(name: str):
        """For a live view. Polling /capture instead would burn a capture id, run the gate and its
        retries, and announce a capture to the hub — for every frame of a video."""
        r: cap_mod.CaptureRig = app.state.rig
        try:
            jpeg, t_mono = await asyncio.to_thread(r.preview, name)
        except KeyError:
            return err(404, "not_found", f"no camera {name!r}; this rig has {sorted(r.cameras)}")
        except cap_mod.CameraUnavailable as e:
            return err(503, "camera_unavailable", e.detail, retryable=True, camera=name)
        return Response(jpeg, media_type="image/jpeg", headers={
            "Cache-Control": "no-store", "Access-Control-Allow-Origin": "*", "X-Boot-Id": tel.boot_id,
            "X-T-Mono": f"{t_mono:.6f}", "X-Frame-Age-Ms": str(int((time.monotonic() - t_mono) * 1000)),
            "Access-Control-Expose-Headers": "X-T-Mono, X-Frame-Age-Ms, X-Boot-Id"})

    # ── GET /pose ────────────────────────────────────────────────────────────────
    @app.get("/pose")
    async def pose():
        s = (tel.recent(1) or [{}])[-1]
        now = time.monotonic()
        return {**base.read(), "odom_residual_m": s.get("odom_residual"), "balanced": balanced(),
                "ts": app.state.rig.iso(now), "t_mono": round(now, 6)}

    # ── POST /drive /arm /say /led ───────────────────────────────────────────────
    def job_route(path: str, call, status: int):
        async def route(request: Request):
            try:
                return JSONResponse(call(await body_of(request)), status_code=status)
            except JobError as e:
                return err(e.status, e.code, e.detail, e.retryable)
        app.post(path)(route)

    job_route("/drive", jobs.drive, 202)
    job_route("/arm", jobs.arm, 202)
    job_route("/say", jobs.say, 202)
    job_route("/led", jobs.led, 200)

    # ── WebSocket /stream ────────────────────────────────────────────────────────
    @app.websocket("/stream")
    async def stream(ws: WebSocket, since: float | None = None, boot: str = ""):
        await ws.accept()
        await ws.send_text(json.dumps(hello()))
        try:                                 # replay only what THIS boot's ring can still hold
            await tel.stream(ws.send_text, since if boot == tel.boot_id else None)
        except (WebSocketDisconnect, RuntimeError):
            pass

    # ── SSE /events ──────────────────────────────────────────────────────────────
    @app.get("/events")
    async def events(request: Request, types: str = "", limit: int = 0, heartbeat: float = 0.0,
                     last_event_id: str = ""):
        """The same messages as /stream, as Server-Sent Events. ?types=job,log filters (a bare curl
        of all of it is 10 telemetry events a second); ?limit=N ends after N events, so a proof
        terminates; ?heartbeat=S (1..60) overrides the 15 s keepalive; ?last_event_id= is the header
        for clients that cannot set one."""
        last = request.headers.get("last-event-id") or last_event_id
        wanted = frozenset(t for t in (x.strip() for x in types.split(",")) if t) or None
        beat = min(60.0, max(1.0, heartbeat)) if heartbeat > 0 else sse_heartbeat_s
        body = log_.subscribe(hello(), last, wanted, max(0, limit), beat)
        return StreamingResponse(body, media_type="text/event-stream", headers=sse.HEADERS)

    # ── WebSocket /frames ────────────────────────────────────────────────────────
    @app.websocket("/frames")
    async def frames(ws: WebSocket):
        q = bus.join()                       # before accept(): a client that is connected is registered
        await ws.accept()

        try:
            # An anyio task group, not bare asyncio tasks: Starlette cancels a handler through an
            # anyio scope, and a hand-rolled create_task/gather leaks that cancellation upward.
            async with anyio.create_task_group() as tg:
                async def gone():            # sending alone would not notice a QUIET client leaving,
                    while (await ws.receive())["type"] != "websocket.disconnect":   # and a stale one
                        pass                 # makes the next `curl` capture default to inline=false
                    tg.cancel_scope.cancel()
                tg.start_soon(gone)
                while True:
                    await ws.send_bytes(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            bus.leave(q)

    # ── ops, and the sim's hands ─────────────────────────────────────────────────
    @app.get("/healthz")
    async def healthz():
        r = app.state.rig
        return {"mode": cfg.mode, "fw": C.FW, "boot_id": tel.boot_id, "cameras": r.available(),
                "unavailable": r.unavailable, "frames_clients": len(bus.clients), "frames_dropped": bus.dropped,
                "last_capture": r.last.capture_id if r.last else None, "led": jobs.led_state,
                "events": log_.stats(), "preview": r.preview_stats, "sentry": sentry_state(live),
                "telemetry": {"overruns": tel.overruns, "source_errors": tel.source_errors, "dropped": tel.dropped}}

    @app.post("/sim/{what}")
    async def sim_poke(what: str):
        """Knock the simulated robot (`bump`) or tip it over (`fall`): the capture gate and
        `not_balanced` then have something real to refuse. 404 on hardware."""
        b = app.state.balance
        if b is None or what not in ("bump", "fall"):
            return err(404, "not_found", "only /sim/bump and /sim/fall, and only with a simulated robot")
        getattr(b, what)()
        return {"ok": True, "did": what}

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--sim", action="store_true", help="synthetic recording + simulated robot")
    mode.add_argument("--replay", type=Path, metavar="DIR", help="serve recorded frames from DIR")
    mode.add_argument("--hardware", action="store_true", help="the real cameras")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--once", action="store_true", help="take one capture, print it, exit")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s  %(message)s")
    cfg = C.Config.from_env()
    if a.replay:
        cfg = dataclasses.replace(cfg, mode="replay", replay_dir=a.replay.expanduser())
    elif a.sim:
        cfg = dataclasses.replace(cfg, mode="sim", replay_dir=None)
    elif a.hardware:
        cfg = dataclasses.replace(cfg, mode="hardware")
    cfg = dataclasses.replace(cfg, host=a.host or cfg.host, port=a.port or cfg.port)

    if a.once:
        live = obs.init("robot")
        source, _ = telemetry_source(cfg)
        tel = Telemetry(source=source).start()
        rig = cap_mod.build_rig(cfg, tel, sim.SimBase().read, traced=live).open()
        time.sleep(0.3)                      # let the ring cover the latch window
        try:
            got = rig.capture()
            print(json.dumps({**got.meta(rig.iso), "frames": got.frame_list(rig.iso)}, indent=2))
            return 0
        except (cap_mod.CaptureRejected, cap_mod.CameraUnavailable) as e:
            print(f"{type(e).__name__}: {e}", file=sys.stderr)
            return 1
        finally:
            rig.close()
            tel.stop()
            obs.flush(3)

    import uvicorn
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
