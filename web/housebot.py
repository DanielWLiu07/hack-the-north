"""housebot.py — the dispatcher: our `point` and `move` jobs, pushed to Andrew's Housebot Edge (PLAN.md §0).

    this web (:8000) --POST $HOUSEBOT_EDGE_URL/v1/jobs, Bearer $HOUSEBOT_EDGE_TOKEN--> Housebot Edge (:8780)
        --> Ryan/Sarah's RobotAdapter --> the robot. His CaretakerJobResult comes back IN the HTTP answer
        (his /v1/jobs is synchronous), and we publish it as the SSE `job` event and keep it in the ledger.

    GET  /api/housebot                    is the dispatcher on, where does it point, what may it send
    POST /api/jobs/{job_id}/dispatch      send a planned ONE-move job (web/jobs.py) to the edge as `move`
    (point jobs are dispatched by POST /api/object-life/{id}/point itself, web/object_api.py)
    GET  /api/jobs/{job_id}               also answers for a dispatched job (web/jobs.py falls back here)

OFF unless HOUSEBOT_EDGE_URL is set. Then a kind is sent only if it is on WEB_ALLOWED_COMMANDS (`point`,
`move`): the operator's switch, the same allow-list every other command obeys.

DELIVERED AT MOST ONCE. His edge remembers finished job_ids in memory only, so if his process restarts
it forgets them. The durable half is here: a job is recorded BEFORE it is sent, and a job_id is POSTed
once. A request that provably never left this machine (connection refused, DNS) is retried, because
nothing started. Anything that may have reached him (a timeout after sending, a dropped connection) is
NEVER re-sent: it ends `unknown`, and a person looks at the robot. roomctl/robot_client.py follows the same rule.

Everything we send is in OUR frame (`world_z_up`, metres, degrees) and says so. The conversion to
Bracket Bot's frame happens once, in Ryan/Sarah's adapter (PLAN.md §0, "one transform owner").
"""
from __future__ import annotations

import asyncio
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi.responses import JSONResponse

try:                      # repo-root obs.py: spans + issues when this process called obs.init()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import obs
except ImportError:  # pragma: no cover
    obs = None

router = APIRouter()

KINDS = ("point", "move")
FRAME = "world_z_up"
UNITS = {"position": "m", "yaw": "deg", "duration": "s"}
# his CaretakerJobResult.status -> our job state
HIS_STATUS = {"succeeded": "succeeded", "failed": "failed", "retryable_failure": "failed"}
RETRIES = 3               # only for requests that provably never left
BACKOFF_S = 0.5
_LOCK = threading.Lock()
_TASKS: set[asyncio.Task] = set()


def init(es=None) -> None:
    """server.mount_router's hook: nothing to wire."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def edge_url() -> str:
    return os.getenv("HOUSEBOT_EDGE_URL", "").strip().rstrip("/")


def enabled() -> bool:
    return bool(edge_url())


def _allowed(kind: str) -> bool:
    raw = os.getenv("WEB_ALLOWED_COMMANDS", "").split("#", 1)[0]
    return kind in {c.strip() for c in raw.split(",") if c.strip()}


def _timeout_s() -> float:
    try:
        return max(1.0, float(os.getenv("HOUSEBOT_EDGE_TIMEOUT_S", "120")))
    except ValueError:
        return 120.0


def status() -> dict:
    """What the dispatcher would do right now. Never the token, only whether there is one."""
    u = urlparse(edge_url()) if enabled() else None
    return {"enabled": enabled(), "edge": f"{u.scheme}://{u.netloc}" if u else None,
            "token": bool(os.getenv("HOUSEBOT_EDGE_TOKEN", "").strip()),
            "kinds": {k: _allowed(k) for k in KINDS}, "timeout_s": _timeout_s(),
            "why_off": None if enabled() else "HOUSEBOT_EDGE_URL is not set"}


def refusal(kind: str) -> str | None:
    """Why `kind` would NOT be sent now, or None."""
    if not enabled():
        return "HOUSEBOT_EDGE_URL is not set: no edge to send to"
    if not _allowed(kind):
        return f"'{kind}' is not on WEB_ALLOWED_COMMANDS: the operator adds it to let the robot act"
    return None


# ── the ledger: one file per dispatched job, next to web/jobs.py's ─────────────────────

def _dir() -> Path:
    import jobs
    return jobs.store_dir() / "housebot"


def load(job_id: str) -> dict | None:
    try:
        return json.loads((_dir() / f"{job_id}.json").read_text())
    except (OSError, ValueError):
        return None


def _save(rec: dict) -> None:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".job-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f, indent=1, default=str)
    os.replace(tmp, d / f"{rec['job_id']}.json")


def outbound(job: dict) -> dict:
    """The body his /v1/jobs parses (protocol/daniel.py @ 9582081): point_action_from_daniel_job for
    `point`, robot_actions_from_daniel_job (exactly one `moved` op) for `move`. Declared frame + units."""
    if job["command"] == "point":
        body = {k: job.get(k) for k in ("job_id", "command", "object_id", "target_pose", "zone", "pointing_at")}
    else:
        body = {k: job.get(k) for k in ("job_id", "command", "target", "ops")}
    return {**body, "frame": FRAME, "units": UNITS}


# ── delivery ─────────────────────────────────────────────────────────────────────

class _NotSent(Exception):
    """The request never left this machine: safe to retry."""


class _Unknown(Exception):
    """It may have reached the edge: never re-send."""


def _post(body: dict) -> tuple[int, dict]:
    token = os.getenv("HOUSEBOT_EDGE_TOKEN", "").strip()
    req = urllib.request.Request(f"{edge_url()}/v1/jobs", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=_timeout_s()) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except urllib.error.URLError as e:
        if isinstance(e.reason, (ConnectionRefusedError, socket.gaierror)):
            raise _NotSent(str(e.reason)) from None
        raise _Unknown(str(e.reason)) from None
    except (TimeoutError, socket.timeout):
        raise _Unknown(f"no answer within {_timeout_s():g} s") from None
    except (ConnectionError, http.client.HTTPException, ValueError) as e:
        raise _Unknown(f"{type(e).__name__}: {e}") from None


def _deliver(rec: dict) -> dict:
    """Blocking: POST once (retrying only what never left), then the terminal record. Runs in a thread."""
    body = rec["sent"]
    tx = obs.transaction("housebot.job", f"{rec['command']} {rec.get('object_id') or rec['job_id']}") if obs else None
    with (tx if tx is not None else _Null()):
        for attempt in range(1, RETRIES + 1):
            rec["attempts"] = attempt
            try:
                with (obs.span("housebot.post", f"POST /v1/jobs {rec['command']}", job_id=rec["job_id"],
                               command=rec["command"], object_id=rec.get("object_id"), attempt=attempt)
                      if obs else _Null()):
                    code, answer = _post(body)
            except _NotSent as e:
                if attempt < RETRIES:
                    time.sleep(BACKOFF_S * 2 ** (attempt - 1))
                    continue
                return _end(rec, "undelivered", f"the edge never received it: {e}", error="edge_unreachable")
            except _Unknown as e:
                return _end(rec, "unknown", f"sent, and no answer came back ({e}). It may have run: look at "
                                            "the robot. Never re-sent automatically.", error="edge_no_answer")
            if code == 200 and isinstance(answer, dict) and answer.get("status") in HIS_STATUS:
                return _end(rec, HIS_STATUS[answer["status"]], answer.get("message") or "", result=answer,
                            error=None if answer["status"] == "succeeded" else answer["status"])
            err = (answer or {}).get("error") or f"http_{code}"
            return _end(rec, "failed", (answer or {}).get("detail") or f"the edge answered HTTP {code}",
                        error=err, http_status=code, result=answer or None)
    return rec  # pragma: no cover


def _end(rec: dict, state: str, message: str, *, error: str | None = None, result: Any = None,
         http_status: int | None = None) -> dict:
    rec.update(state=state, terminal=True, message=message, error=error, result=result,
               finished_at=_now(), updated_at=_now(), **({"http_status": http_status} if http_status else {}))
    if error and obs is not None:                 # a job that did not succeed is an issue someone should see
        try:
            obs.robot_failure(f"housebot_{error}", message or error, action=rec["command"], job_id=rec["job_id"],
                              object_id=rec.get("object_id") or "", executor="housebot-edge")
        except Exception:  # noqa: BLE001 — reporting must never lose the result
            pass
    return rec


class _Null:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def _event(rec: dict) -> dict:
    return {"id": rec["job_id"], "state": rec["state"], "terminal": rec["terminal"], "command": rec["command"],
            "object_id": rec.get("object_id"), "executor": "housebot-edge", "message": rec.get("message"),
            "error": rec.get("error"), "result": rec.get("result"),
            "progress": 1.0 if rec["state"] == "succeeded" else 0.0}


def _publish(rec: dict) -> None:
    try:
        import events
        events.hub.publish("job", _event(rec))
    except Exception:  # noqa: BLE001 — the dashboard missing a frame never loses a result
        pass


async def _run(job_id: str, after=None) -> None:
    rec = load(job_id)
    if rec is None:
        return
    done = await asyncio.to_thread(_deliver, rec)
    with _LOCK:
        _save(done)
    _publish(done)                                # on the event loop: the SSE hub is not thread-safe
    if after is not None:
        try:
            after(done)
        except Exception:  # noqa: BLE001
            pass


async def submit(job: dict, after=None) -> dict:
    """Record the job, then push it in the background. Returns the dispatch record; the same job_id
    again returns the stored record (`replayed: true`) and is never sent twice. `after(record)` runs
    once the terminal answer is in (web/jobs.py uses it to close a move job in its ledger)."""
    kind = job.get("command")
    if kind not in KINDS:
        raise ValueError(f"the housebot edge takes {', '.join(KINDS)}, not {kind!r}")
    why = refusal(kind)
    if why:
        return {"dispatched": False, "why": why}
    with _LOCK:
        have = load(job["job_id"])
        if have is not None:
            return {**have, "dispatched": True, "replayed": True}
        rec = {"job_id": job["job_id"], "command": kind, "object_id": job.get("object_id"),
               "executor": "housebot-edge", "edge": status()["edge"], "sent": outbound(job),
               "state": "dispatching", "terminal": False, "attempts": 0, "message": None, "error": None,
               "result": None, "frame": FRAME, "units": UNITS, "created_at": _now(), "updated_at": _now()}
        _save(rec)
    _publish(rec)
    task = asyncio.create_task(_run(rec["job_id"], after))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {**rec, "dispatched": True, "replayed": False}


# ── routes ──────────────────────────────────────────────────────────────────────────

@router.get("/api/housebot")
async def get_status():
    return status()


def _error(code: str, detail: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail, "retryable": False}, status_code=status_code)


@router.post("/api/jobs/{job_id}/dispatch", status_code=202)
async def dispatch_move(job_id: str):
    """Send a planned job whose plan is exactly ONE move to the edge, as his `move` (one `moved` op).
    The job is claimed in web/jobs.py's ledger first, so it runs once, and the edge's answer closes it."""
    import jobs
    if not jobs.JOB_ID.match(job_id):
        return _error("bad_request", "job_id looks like job_ followed by 16 hex characters", 422)
    why = refusal("move")
    if why:
        return _error("dispatch_off", why, 409)
    with jobs._LOCK:                                                           # noqa: SLF001
        job = jobs._load(job_id)                                               # noqa: SLF001
        if job is None:
            return _error("not_found", f"no job {job_id}", 404)
        have = load(job_id)
        if have is not None:
            return JSONResponse({**have, "dispatched": True, "replayed": True}, status_code=200)
        ok, why_not = jobs._executable(job)                                    # noqa: SLF001
        if not ok:
            return _error("not_executable", f"{job_id} cannot start: {why_not}", 409)
        moves = [o for o in job["plan"]["ops"] if o["kind"] == "move"]
        if len(job["plan"]["ops"]) != 1 or len(moves) != 1:
            return _error("not_one_move", f"the edge runs ONE move per job; this plan has {len(job['plan']['ops'])} "
                                          "op(s). Run it with roomctl, or curate a one-move job", 409)
        op = moves[0]
        job.update(claimed_by="housebot-edge", executor="edge:housebot-edge", started_at=jobs._now(),  # noqa: SLF001
                   state="running", updated_at=jobs._now())                    # noqa: SLF001
        job["op_status"][0].update(status="running", attempts=1)
        job["progress"] = jobs._progress(job)                                  # noqa: SLF001
        jobs._save(job)                                                        # noqa: SLF001
    move = {"job_id": job_id, "command": "move", "target": job.get("target"),
            "ops": [{"op": "moved", "object_id": op["object_id"], "zone": op["to"]["zone"],
                     "from": op["from"]["pose"], "to": op["to"]["pose"]}]}
    rec = await submit(move, after=lambda done: _close_ledger_job(job_id, done))
    return JSONResponse(rec, status_code=202)


def _close_ledger_job(job_id: str, done: dict) -> None:
    import jobs
    with jobs._LOCK:                                                           # noqa: SLF001
        job = jobs._load(job_id)                                               # noqa: SLF001
        if job is None or job.get("terminal"):
            return
        ok = done["state"] == "succeeded"
        job["op_status"][0].update(status="success" if ok else "failed", error=None if ok else done.get("error"))
        report = {"run_id": "housebot-edge", "status": "success" if ok else "failed",
                  "message": done.get("message"), "edge": done.get("result")}
        job.update(state="succeeded" if ok else "failed", terminal=True, updated_at=jobs._now(),  # noqa: SLF001
                   finished_at=jobs._now(), reports=job.get("reports", 0) + 1,             # noqa: SLF001
                   result={"status": report["status"], "message": done.get("message"), "run_id": "housebot-edge",
                           "at": jobs._now(), "report": report})                      # noqa: SLF001
        job["progress"] = jobs._progress(job)                                  # noqa: SLF001
        jobs._save(job)                                                        # noqa: SLF001
