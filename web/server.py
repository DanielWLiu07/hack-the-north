"""gitspace web — the dashboard's backend.

The browser never holds the Elasticsearch key: every query is proxied through
here, which is also the one place that shapes ES responses for display and logs
exactly what the demo ran. Spec: ../docs/16-api.md §4b.

Run:  ../.venv/bin/python server.py    (the repo-root venv: search imports elastic/queries.py,
      which needs the `elasticsearch` package; binds per WEB_BIND in ../.env)
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import mimetypes
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

HERE = Path(__file__).resolve().parent
LANDING = HERE / "landing"

load_dotenv(HERE.parent / ".env")  # real environment variables win over the file

sys.path.insert(0, str(HERE.parent))  # obs.py lives at the repo root
import obs  # noqa: E402 -- after load_dotenv: obs reads SENTRY_* from the environment



def usable(value: str | None) -> str:
    """A credential worth sending, or "". Keys get PARKED in ../.env to save quota before the demo
    (`ELASTIC_API_KEY=# parked…` beside `ELASTIC_API_KEY_PARKED=<the key>`), and python-dotenv
    reads `KEY=# comment` as the literal VALUE "# comment": non-empty, so a naive `bool(key)` would
    happily send it to the real service. A secret never contains whitespace or starts with "#".
    """
    v = (value or "").strip()
    return "" if (not v or v.startswith("#") or any(c.isspace() for c in v)) else v


def parked(name: str) -> bool:
    """True when `name` has been parked: `<name>_PARKED` holds the real value and `name` does not."""
    return bool(usable(os.getenv(f"{name}_PARKED"))) and not usable(os.getenv(name))


# before FastAPI() so Sentry's FastAPI integration sees the app. obs.init() is a no-op for any DSN
# that is not a URL (absent, blank, parked), so it can never take the site down at import.
obs.init("web")

import events  # noqa: E402 -- local modules, after sys.path and the environment are set
import room  # noqa: E402

ELASTIC_URL = usable(os.getenv("ELASTIC_URL")).rstrip("/")
ELASTIC_API_KEY = usable(os.getenv("ELASTIC_API_KEY"))
ELASTIC_PAUSED = parked("ELASTIC_API_KEY")   # deliberately off: make NO calls, serve the fixtures
WEB_BIND = os.getenv("WEB_BIND", "127.0.0.1:8000")
STARTED = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())   # when THIS process began: a router newer than this is not mounted

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s  %(message)s")
log = logging.getLogger("gitspace.web")


# ── errors: one shape everywhere (docs/16-api.md §2.7) ─────────────────────────

class ApiError(Exception):
    def __init__(self, code: str, detail: str, status: int = 502, retryable: bool = False):
        super().__init__(detail)
        self.code, self.detail, self.status, self.retryable = code, detail, status, retryable


def error_response(code: str, detail: str, status: int, retryable: bool = False) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail, "retryable": retryable}, status_code=status)


# ── Elasticsearch: the only holder of the key ──────────────────────────────────

class Elastic:
    """Server-side ES client. Nothing it returns to a route contains the URL or key;
    errors are reduced to a code and ES's own reason string."""

    def __init__(self, url: str, api_key: str):
        self.configured = bool(url and api_key)
        self._client = httpx.AsyncClient(
            base_url=url or "http://unconfigured.invalid",
            headers={"Authorization": f"ApiKey {api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, index: str, body: dict, label: str = "search") -> dict:
        return await self._request("POST", f"/{index}/_search", label, body)

    async def esql(self, query: str, params: list | None = None, label: str = "esql") -> dict:
        body: dict = {"query": query}
        if params:
            body["params"] = params
        return await self._request("POST", "/_query", label, body)

    async def info(self) -> dict:
        return await self._request("GET", "/", "info")

    async def _request(self, method: str, path: str, label: str, body: dict | None = None) -> dict:
        if not self.configured and ELASTIC_PAUSED:
            raise ApiError("elastic_paused", "Elasticsearch is parked in ../.env (ELASTIC_API_KEY_PARKED) "
                           "to save quota; no calls are being made", status=503)
        if not self.configured:
            raise ApiError("elastic_unconfigured",
                           "ELASTIC_URL / ELASTIC_API_KEY are not set in ../.env", status=503)
        with obs.span("es.query", label, method=method, path=path) as sp:
            return await self._send(method, path, label, body, sp)

    async def _send(self, method: str, path: str, label: str, body: dict | None, sp) -> dict:
        t0 = time.perf_counter()
        try:
            r = await self._client.request(method, path, json=body)
        except httpx.TimeoutException:
            log.warning("es %s %s %s -> timeout", label, method, path)
            raise ApiError("elastic_timeout", "Elasticsearch did not answer in time",
                           status=504, retryable=True) from None
        except httpx.HTTPError as e:
            log.warning("es %s %s %s -> %s", label, method, path, type(e).__name__)
            raise ApiError("elastic_unreachable", f"could not reach Elasticsearch ({type(e).__name__})",
                           status=502, retryable=True) from None
        ms = (time.perf_counter() - t0) * 1000
        if sp is not None:
            sp.set_data("status", r.status_code)
        # The query log: exactly what the demo ran, one line per ES call.
        log.info("es %s %s %s -> %d in %.0f ms  %s", label, method, path, r.status_code, ms,
                 json.dumps(body, separators=(",", ":")) if body else "")
        if r.status_code in (401, 403):
            raise ApiError("elastic_auth", "Elasticsearch rejected the API key", status=502)
        if r.status_code >= 400:
            raise ApiError("elastic_error", _es_reason(r), status=502,
                           retryable=r.status_code in (429, 502, 503, 504))
        return r.json()


def _es_reason(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", {})
        if isinstance(err, dict):
            root = (err.get("root_cause") or [err])[0]
            return f"{root.get('type', 'error')}: {root.get('reason', '')}".strip(": ")
        return str(err)
    except ValueError:
        return f"HTTP {r.status_code}"


es = Elastic(ELASTIC_URL, ELASTIC_API_KEY)


# ── app ────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_: FastAPI):
    if not es.configured:
        log.warning("ELASTIC_URL / ELASTIC_API_KEY missing — every ES-backed endpoint will 503")
    if not _search_ready()["ok"]:
        log.error("SEARCH WILL 503: %s", _search_ready()["detail"])
    app.state.loop = asyncio.get_running_loop()
    watcher = asyncio.create_task(events.watch_room(), name="room-watcher")
    yield
    events.hub.close()                       # ends every open /api/events stream
    watcher.cancel()
    await es.aclose()


app = FastAPI(title="gitspace web", lifespan=lifespan)
# No CORS middleware on purpose: the page is served from this origin, and nothing
# else should be able to read the proxy's responses.


@app.exception_handler(ApiError)
async def _api_error(_: Request, e: ApiError) -> JSONResponse:
    return error_response(e.code, e.detail, e.status, e.retryable)


NOT_FOUND_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>not here — GITRL</title>
<style>@view-transition{navigation:auto}@media (prefers-reduced-motion:reduce){@view-transition{navigation:none}}</style>
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark"><link rel="icon" href="data:,">
<link rel="stylesheet" href="/pages/pages.css"><link rel="stylesheet" href="/pages/sitenav.css"></head><body>
<nav class="sitenav" aria-label="GITRL"><a class="brand" href="/">GITRL</a><a class="sec" href="/?info#status">Status</a>
<a class="sec" href="/?info#search">Search</a><a class="sec" href="/?info#history">History</a><a class="sec" href="/telemetry">Telemetry</a>
<a class="sec" href="/robot">Room</a></nav>
<main style="padding:clamp(24px,6vw,80px) clamp(16px,4vw,48px);max-width:60ch">
<p class="crumb">404</p><h1 style="font:600 clamp(30px,5vw,56px)/1.05 var(--mono);margin:12px 0 18px">Nothing is kept at this address.</h1>
<p style="color:var(--dim)"><code>__PATH__</code> is not a page on this server. The room's history, its search and the robot's telemetry are one click away:</p>
<p style="display:flex;flex-wrap:wrap;gap:10px;margin-top:22px"><a class="tag" href="/?info#history">the commit graph</a><a class="tag" href="/?info#search">search the room</a>
<a class="tag" href="/capture">the latest capture</a><a class="tag" href="/telemetry">telemetry</a><a class="tag" href="/robot">the room</a></p></main></body></html>"""


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, e: StarletteHTTPException):
    code = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "http_error")
    # a person who mistyped a URL gets a page with a way back; a program (and everything under /api/) gets §2.7
    if (e.status_code == 404 and request.method == "GET" and not request.url.path.startswith("/api/")
            and "text/html" in request.headers.get("accept", "")):
        import html
        return HTMLResponse(NOT_FOUND_PAGE.replace("__PATH__", html.escape(request.url.path[:120])), status_code=404)
    return error_response(code, str(e.detail), e.status_code)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, e: RequestValidationError) -> JSONResponse:
    first = e.errors()[0] if e.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", ()))
    return error_response("bad_request", f"{where}: {first.get('msg', 'invalid request')}", 422)


@app.get("/api/health")
async def health() -> dict:
    """Is the proxy up, and can it reach Elasticsearch? Never echoes the URL or key."""
    try:
        info = await es.info()
    except ApiError as e:
        reached = e.code not in ("elastic_unconfigured", "elastic_paused", "elastic_unreachable", "elastic_timeout")
        return {"ok": False, "elastic": {"configured": es.configured, "reachable": reached,
                                         "error": e.code, "detail": e.detail}, "search": _search_ready()}
    version = info.get("version", {})
    search = _search_ready()
    return {"ok": search["ok"], "elastic": {"configured": True, "reachable": True,
                                            "version": version.get("number"),
                                            "flavor": version.get("build_flavor")},
            "search": search}


def _search_ready() -> dict:
    """Can THIS process run the hybrid search? It needs elastic/queries.py and the `elasticsearch` package,
    which live in the repo-root .venv. Started with another interpreter, everything else works and
    /api/search answers 503 — which a health check that only pings the cluster never notices."""
    try:
        import es_shared
        why = es_shared.IMPORT_ERROR
    except Exception as e:  # noqa: BLE001
        why = f"{type(e).__name__}: {e}"
    return {"ok": why is None, "detail": None if why is None else
            f"{why} — start web with the repo venv: cd web && ../.venv/bin/python server.py", "python": sys.executable}


# ── public browser config ──────────────────────────────────────────────────────

def _rate(name: str, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(os.getenv(name, default))))
    except ValueError:
        return default


@app.get("/api/config")
async def config() -> dict:
    """What the page needs to start the browser Sentry SDK. An explicit whitelist: a DSN
    is public by design (it can only SEND events); nothing else in the environment is."""
    return {"sentry": {
        # parking the server DSN pauses Sentry as a whole: the browser must not keep sending
        # sessions and replays (each page load is a replay) just because its own DSN was left in place
        "dsn": None if parked("SENTRY_DSN") else (usable(os.getenv("SENTRY_DSN_WEB")) or None),
        "paused": parked("SENTRY_DSN"),
        "environment": os.getenv("SENTRY_ENVIRONMENT", "htn2026"),
        "release": os.getenv("SENTRY_RELEASE", "gitspace@0.1.0"),
        "replaysSessionSampleRate": _rate("SENTRY_REPLAYS_SAMPLE_RATE", 1.0),
        "tracesSampleRate": _rate("SENTRY_TRACES_SAMPLE_RATE", 1.0),
    }}


# ── the room: status, and the live stream ──────────────────────────────────────

@app.get("/api/status")
async def status() -> dict:
    try:
        snap = await asyncio.to_thread(room.snapshot)
    except room.RoomError as e:
        raise ApiError("room_unavailable", str(e), status=503, retryable=True) from None
    snap.pop("rev", None)
    return snap


def _untrace() -> None:
    """An SSE request lasts for hours; as a Sentry transaction that is one giant span that
    never finishes. obs.py owns init (no traces_sampler there), so drop it from here."""
    try:
        import sentry_sdk
        tx = sentry_sdk.get_current_scope().transaction
        if tx is not None:
            tx.sampled = False
    except Exception:  # noqa: BLE001 -- observability must never break the stream
        pass


_STATIC = (".js", ".css", ".glb", ".png", ".jpg", ".webp", ".svg", ".woff2", ".json", ".map", ".ico")


@app.middleware("http")
async def _no_transactions_for_files(request: Request, call_next):
    """A page is worth a transaction; its forty module files are not. obs.py owns init and has no
    traces_sampler, so — like the SSE stream — they are dropped from here. /api/* is never touched."""
    path = request.url.path
    if not path.startswith("/api/") and path.lower().endswith(_STATIC):
        _untrace()
    return await call_next(request)


@app.get("/api/events")
async def event_stream(request: Request) -> StreamingResponse:
    """SSE, not a WebSocket: server -> browser only, and EventSource reconnects for free."""
    _untrace()
    hello = await asyncio.to_thread(events.status_frames)
    body = events.hub.stream(request.headers.get("last-event-id"), hello)
    return StreamingResponse(body, media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


_FORWARDED = ("x-forwarded-for", "forwarded", "x-real-ip", "cf-connecting-ip")


@app.post("/api/internal/event")
async def push_event(request: Request) -> dict:
    """The inlet for events this process cannot see: the executor's `job` progress and the
    laptop ingest's decimated `telemetry`. LOOPBACK ONLY — and a request that came through
    a reverse proxy also arrives from 127.0.0.1, so anything carrying a forwarding header
    is refused too."""
    peer = request.client.host if request.client else ""
    if peer not in ("127.0.0.1", "::1") or any(h in request.headers for h in _FORWARDED):
        raise ApiError("forbidden", "this endpoint only accepts local connections", status=403)
    try:
        payload = await request.json()
    except ValueError:
        raise ApiError("bad_request", "body must be JSON", status=400) from None
    name, data = (payload or {}).get("event"), (payload or {}).get("data")
    if not isinstance(data, dict) or (name not in events.EVENT_NAMES and name not in events.EDGE_EVENTS):
        raise ApiError("bad_request", f"event must be one of {', '.join(events.EVENT_NAMES)} (or gitirl-agent's "
                                      f"{', '.join(events.EDGE_EVENTS)}); data must be an object", status=400)
    if name == "robot_observation":                  # poses cross only in our frame (docs/31 §4)
        try:
            from bridge.contract import ContractError, assert_frame
            assert_frame(data, "robot_observation")
        except ContractError as e:
            raise ApiError(e.code, e.message, status=422) from None
        except ImportError:
            pass
    ours, data = events.from_edge(name, data)
    events.hub.publish(ours, data)
    return {"published": ours, **({"from": name} if ours != name else {}), "clients": events.hub.clients}


# ── standalone pages: a file in pages/ with no API module of its own ───────────
# Registered BEFORE the routers: capture_api's /pages/{name} serves only .js / .css, and would
# answer /pages/robot.html with a 404 first. `robot` = the Robot Info / room-splat page
# (pages/robot.{html,css,js}); its assets already come through /pages/{name}.
STANDALONE_PAGES = ("robot",)


def _standalone(name: str) -> FileResponse:
    path = HERE / "pages" / f"{name}.html"
    if not path.is_file():
        raise ApiError("not_found", f"pages/{name}.html has not been written yet", status=404)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


for _page in STANDALONE_PAGES:
    app.add_api_route(f"/{_page}", (lambda n=_page: _standalone(n)), methods=["GET"], include_in_schema=False)
    app.add_api_route(f"/pages/{_page}.html", (lambda n=_page: _standalone(n)), methods=["GET"], include_in_schema=False)


# ── routers owned by other builders (web/PAGES.md) ─────────────────────────────
# Each module exposes `router` (an APIRouter; may carry page routes such as
# GET /capture/{capture_id}) and `init(es)`. A module that does not exist yet is skipped.
# One that exists and is BROKEN is skipped too — loudly, with its traceback in the log and its
# reason in GET /api/routers: several sessions add routers here and the person restarting the
# server at 3 am should get a site with one panel missing, not no site.
ROUTERS: dict[str, str] = {}                 # name -> "loaded (n routes)" | "not present" | "FAILED: why"
OPTIONAL_ROUTERS = ("capture_api", "dash_api", "graph_api", "object_api", "replay_api", "telemetry_api",
                    "voxel_api",          # GET /api/voxels — room-voxels for the room page (its own session's module)
                    "bridge.agent_api",   # the agent panel <-> gitirl-agent (cloud session, docs/31)
                    "roommate_api",       # the caretaker dashboard: /api/room/ci, /api/blame, chores, PRs, nav (plan/roommate 03 §8)
                    "jobs",               # GET /api/jobs/{id} + authenticated POST .../result (cloud, ANDREW-HANDOFF §2b)
                    "housebot",           # point / move jobs -> Andrew's housebot edge POST /v1/jobs (cloud, PLAN.md §0)
                    "robot_view_api")     # GET /live + /api/robot/view.mjpg — the robot's head camera, live (link session, docs/33)


def mount_router(name: str) -> str:
    """Import `name`, init(es), include its router. Returns (and records) what happened; never raises."""
    try:
        mod = importlib.import_module(name)
        mod.init(es)
        app.include_router(mod.router)
        ROUTERS[name] = f"loaded ({len(mod.router.routes)} routes)"
        log.info("router %s: %s", name, ROUTERS[name])
    except ImportError as e:
        if e.name and (name == e.name or name.startswith(e.name + ".")):     # the module (or its package) is not there
            ROUTERS[name] = "not present"
            log.info("router %s: not present, skipped", name)
        else:                                                                # it is there; an import INSIDE it failed
            ROUTERS[name] = f"FAILED: {type(e).__name__}: {e}"
            log.exception("router %s: FAILED — skipped, the rest of the site is up", name)
    except Exception as e:  # noqa: BLE001 — a syntax error or a bad init() in one panel must not take the site down
        ROUTERS[name] = f"FAILED: {type(e).__name__}: {e}"
        log.exception("router %s: FAILED — skipped, the rest of the site is up", name)
    return ROUTERS[name]


for _name in OPTIONAL_ROUTERS:
    mount_router(_name)


@app.get("/api/routers")
async def routers() -> dict:
    """Which optional routers this PROCESS mounted when it started. A module written after the process
    started is "not present" here until the next restart: there is no hot-loading (see `started`)."""
    return {"started": STARTED, "routers": ROUTERS, "hot_reload": False}


# ── the page: landing/ at /, same origin as /api ───────────────────────────────

class LandingFiles(StaticFiles):
    """Only the page's own asset types, so landing/serve.py and its devlog.txt stay
    private. no-cache makes the browser revalidate module JS on every load (the
    stale-module problem serve.py solves with no-store) while unchanged files
    still come back as a cheap 304."""

    SERVED = {".html", ".js", ".css", ".glb", ".png", ".jpg", ".webp", ".svg", ".woff2", ".json",
              ".ksplat", ".splat", ".ply"}
    mimetypes.add_type("model/gltf-binary", ".glb")  # stdlib maps it to nothing
    # the trained Bracket Bot splat and its raw/point-cloud forms; stdlib knows none of them,
    # and without an explicit type the loader gets text/html and fails on the ArrayBuffer
    mimetypes.add_type("application/octet-stream", ".ksplat")
    mimetypes.add_type("application/octet-stream", ".splat")
    mimetypes.add_type("application/octet-stream", ".ply")

    async def get_response(self, path: str, scope):
        if path != "." and Path(path).suffix.lower() not in self.SERVED:
            raise StarletteHTTPException(404)
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Keep this mount LAST: it matches every path, so any route declared below it is
# unreachable. Unknown /api/* paths fall through to it and still 404 in the
# §2.7 error shape via the handler above.
app.mount("/", LandingFiles(directory=LANDING, html=True), name="landing")


def parse_bind(bind: str) -> tuple[str, int]:
    host, _, port = bind.strip().rpartition(":")
    return (host.strip("[]") or "127.0.0.1"), int(port)


if __name__ == "__main__":
    import uvicorn

    host, port = parse_bind(WEB_BIND)

    class _Server(uvicorn.Server):
        def handle_exit(self, sig, frame):
            loop = getattr(app.state, "loop", None)
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(events.hub.close)      # every /api/events stream returns; nothing is left to cancel
            super().handle_exit(sig, frame)

    # the timeout stays as the backstop for a client that will not let go
    _Server(uvicorn.Config(app, host=host, port=port, timeout_graceful_shutdown=2)).run()
