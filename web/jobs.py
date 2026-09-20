"""jobs.py — the job ledger: what the robot was asked to do, and what the edge says it did.

    GET  /api/jobs/{job_id}           the job: state, the ORDERED plan, progress, the terminal result
    POST /api/jobs/{job_id}/result    the edge reports what it DID      Authorization: Bearer <token>

Andrew's gitirl-agent edge runs remote (awzheng/gitirl docs/INTEGRATION.md, b4f3e07): it is handed a
job id (the SSE `job` event, or POST /api/command's answer), polls this, drives the robot through its
RobotAdapter, and reports back here. ANDREW-HANDOFF.md §2b is the contract; this is its server.

A JOB IS NAMED BY WHAT IT WOULD DO, not by when it was asked:
    job_id = "job_" + sha256(gitspace.job/1, command, target sha, HEAD sha, observed-room digest)[:16]
The same command against the same room is the same job. Asking twice returns the stored job
(`replayed: true`), whatever state it is in. A new attempt needs a new room: the rescan after a
run moves HEAD (or the working tree), and that names a new job. So a retry after a dropped
connection can never be turned into a second physical run by asking again.

A job RUNS ONCE. The first report claims it for that report's `run_id`. Reports from any other run
are refused (`claimed`). The first TERMINAL report (success | failed) stands: sending the identical
body again answers `replayed: true`, and a different one is refused (`result_conflict`). An op that
reached success/failed/skipped cannot change.

WHY THE WRITE IS AUTHENTICATED AND THE READ IS NOT. A result is the robot claiming a physical action
happened, and :8000 is public through Tailscale Funnel, so POST .../result needs
`Authorization: Bearer $GITIRL_CLOUD_TOKEN` (the header his DanielAPIClient already sends) from
everyone, loopback included. The check is constant-time, and the token is never logged or echoed.
With no token configured (< 32 chars) the write fails CLOSED (503). The read carries the same poses
/api/state already serves publicly, so it takes no token, and a token that is sent is ignored.

THIS PROCESS STILL NEVER WRITES room.git and never moves the robot. A report is a claim, recorded;
our rescan decides what the room IS (ANDREW-HANDOFF.md §4). `motion` says whether the real robot
may run jobs at all: "mock_only" until JOBS_REAL_MOTION=1 is set, which is the user's go-ahead.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

router = APIRouter()

CONTRACT = "gitspace.job/1"
FRAME = "world_z_up"                          # the token bridge/contract.py asserts; gitspace.plan/1 says it too
UNITS = {"position": "m", "yaw": "deg", "duration": "s"}
FRAME_DEF = ("X forward from the anchor tag, Y left, Z UP, floor z=0; metres. pose.yaw = the AXIS of extents.x "
             "in degrees [0,180) about +Z. delta_yaw_deg is a signed difference in degrees. Bracket Bot is "
             "Y-DOWN: convert at YOUR adapter, never infer (ANDREW-HANDOFF.md §5).")
JOB_ID = re.compile(r"^job_[0-9a-f]{16}$")
RUN_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
PLANNABLE = ("restore", "checkout")           # the target tree is a ref's tree: roomctl can order it read-only
PLAN_ONLY = ("revert", "cherry-pick", "resolve")   # git computes the tree by committing: previewed here, run by roomctl
JOB_STATUS = {"running": "running", "success": "succeeded", "failed": "failed"}
OP_STATUS = ("pending", "running", "success", "retryable", "failed", "skipped")
OP_TERMINAL = ("success", "failed", "skipped")
MIN_TOKEN = 32
_LOCK = threading.Lock()


def init(es=None) -> None:
    """server.mount_router's hook. The ledger is files, not Elasticsearch: nothing to wire."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str)


def _error(code: str, detail: str, status: int, **extra) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail, "retryable": False, **extra}, status_code=status)


def framed(body: dict) -> dict:
    """Every response that carries a pose says which frame and units, so nobody infers them."""
    return {**body, "frame": FRAME, "units": UNITS}


# ── identity ──────────────────────────────────────────────────────────────────────

def job_id_for(*parts: str | None) -> str:
    raw = "\x1f".join([CONTRACT, *[p or "" for p in parts]])
    return "job_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def store_dir() -> Path:
    """One ledger per room repository: a throwaway test room never replays the real room's jobs."""
    import room
    base = Path(os.getenv("JOBS_DIR") or "~/.cache/gitspace/jobs").expanduser()
    key = hashlib.sha1(str(room.room_path().resolve()).encode()).hexdigest()[:12]
    return base / key


def _path(job_id: str) -> Path:
    return store_dir() / f"{job_id}.json"


def _load(job_id: str) -> dict | None:
    try:
        return json.loads(_path(job_id).read_text())
    except (OSError, ValueError):
        return None


def _save(job: dict) -> None:
    d = store_dir()
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".job-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(job, f, indent=1, sort_keys=False, default=str)
    os.replace(tmp, _path(job["job_id"]))            # a reader never sees half a job


# ── the plan: roomctl's ordered op list, computed from reads ─────────────────────────

def prepare(command: str, ref: str | None, target: str) -> dict:
    """{"observed": digest of the room as last scanned, "plan": gitspace.plan/1 | None, "plan_unavailable"}.
    The plan is roomctl's own planner (`room restore <ref> --plan-only --json`) with routing skipped:
    working tree (the last scan) -> the target's objects. Reads only."""
    try:
        import room
        from roomctl.executor import plan
        from roomctl.repo import Repo, load_room
        from roomctl.state import read_tree
        repo = Repo(room.room_path())
        current = read_tree(repo.path)
        observed = hashlib.sha256(_canon({k: repr(v) for k, v in sorted(current.items())}).encode()).hexdigest()[:16]
        if command not in PLANNABLE:
            return {"observed": observed, "plan": None,
                    "plan_unavailable": f"'{command}' has no read-only plan: git computes that tree by "
                                        f"{'reverting' if command == 'revert' else 'applying the commit'}, "
                                        "so roomctl plans it after it commits (`room " + command + "`)"}
        p = plan(current, repo.records(target), load_room(repo.path)).to_dict(ref or target, target)
        p["routed"] = False                       # base poses need the costmap: roomctl's CLI adds them
        return {"observed": observed, "plan": p, "plan_unavailable": None}
    except Exception as e:  # noqa: BLE001 — no plan is an honest state, never a crash of /api/command
        return {"observed": "", "plan": None, "plan_unavailable": f"planner unavailable: {type(e).__name__}: {e}"}


def _op_status(plan: dict | None) -> list[dict]:
    return [{"seq": o["seq"], "object_id": o["object_id"], "kind": o["kind"], "status": "pending",
             "attempts": 0, "error": None} for o in (plan or {}).get("ops", [])]


def _progress(job: dict) -> dict:
    ops = job.get("op_status") or []
    n = {s: sum(1 for o in ops if o["status"] == s) for s in OP_STATUS}
    total = len(ops)
    return {"ops_total": total, "ops_done": n["success"], "ops_failed": n["failed"], "ops_skipped": n["skipped"],
            "ops_pending": n["pending"] + n["running"] + n["retryable"],
            "fraction": round(n["success"] / total, 3) if total else (1.0 if job.get("terminal") else 0.0)}


def open_job(job: dict) -> tuple[dict, bool]:
    """Store a new job, or return the one already stored under its id: (job, replayed)."""
    with _LOCK:
        have = _load(job["job_id"])
        if have is not None:
            return have, True
        job = {"contract": CONTRACT, **job, "frame": FRAME, "units": UNITS, "frame_def": FRAME_DEF,
               "state": "planned", "terminal": False, "claimed_by": None, "result": None, "reports": 0,
               "op_status": _op_status(job.get("plan")), "created_at": _now(), "updated_at": _now()}
        job["progress"] = _progress(job)
        _save(job)
        return job, False


def view(job: dict) -> dict:
    """The stored job plus what is true NOW: may an edge start it, and may the real robot move.
    `plan_only` and `why_not_code` are for machines: an edge decides from them BEFORE it tries."""
    out = dict(job)
    code, why = _blocker(job)
    out["executable"], out["why_not"], out["why_not_code"] = code is None, why, code
    out["plan_only"] = job.get("command") in PLAN_ONLY
    out["motion"] = "real" if os.getenv("JOBS_REAL_MOTION") == "1" else "mock_only"
    return out


def _blocker(job: dict) -> tuple[str | None, str | None]:
    """(why_not_code, why_not): None, None when an edge may start it now. Codes: terminal · claimed ·
    plan_only (revert / cherry-pick / resolve: by design) · planner_unavailable · nothing_to_move ·
    head_moved · object_not_in_room."""
    if job.get("terminal"):
        return "terminal", f"terminal: {job['state']}"
    if job.get("claimed_by"):
        return "claimed", f"running under run_id {job['claimed_by']}: a job runs once"
    if not job.get("plan"):
        if job.get("command") in PLAN_ONLY:
            return "plan_only", job.get("plan_unavailable") or f"'{job.get('command')}' is previewed here, run by roomctl"
        return "planner_unavailable", job.get("plan_unavailable") or "no plan"
    if not job["plan"]["ops"]:
        return "nothing_to_move", "nothing to move: the room already matches"
    import graph_api
    if graph_api._resolve("HEAD") != job.get("head"):                        # noqa: SLF001
        return "head_moved", "head_moved: the room has a new commit since this was planned; ask again for a new job"
    # A MOTION IS NOT A SEARCH (docs/31 §3c). The same boundary object_api.build_point draws for a
    # `point`: an op naming something the room no longer has describes a pose where nothing is
    # standing. The present check is a lookup in the cached HEAD state; only an ABSENT object costs
    # a walk through history, and then it is worth it because the answer says where the thing went.
    for op in job["plan"]["ops"]:
        oid = op.get("object_id")
        if oid and not graph_api.whereabouts(oid)["present"]:                # noqa: SLF001
            return "object_not_in_room", (graph_api.gone_sentence(graph_api.whereabouts(oid))
                                          + " — a motion needs an object the room has now")
    return None, None


def _executable(job: dict) -> tuple[bool, str | None]:
    code, why = _blocker(job)
    return code is None, why


# ── routes ──────────────────────────────────────────────────────────────────────────

@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    if not JOB_ID.match(job_id):
        return _error("bad_request", "job_id looks like job_ followed by 16 hex characters", 422)
    job = _load(job_id)
    if job is None:
        try:                                     # a point / move job pushed to the housebot edge (housebot.py)
            import housebot
            pushed = housebot.load(job_id)
        except Exception:  # noqa: BLE001
            pushed = None
        if pushed is not None:
            return pushed
        return _error("not_found", f"no job {job_id} (jobs are made by POST /api/command)", 404)
    return view(job)


def _authorized(request: Request) -> JSONResponse | None:
    want = os.getenv("GITIRL_CLOUD_TOKEN", "")
    if len(want) < MIN_TOKEN:
        return _error("edge_auth_unconfigured", "this server has no GITIRL_CLOUD_TOKEN configured, so it "
                                                "accepts no results (fail closed)", 503)
    scheme, _, got = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(got.strip().encode(), want.encode()):
        r = _error("unauthorized", "POST /api/jobs/{id}/result needs Authorization: Bearer <GITIRL_CLOUD_TOKEN>", 401)
        r.headers["WWW-Authenticate"] = 'Bearer realm="gitspace-jobs"'
        return r
    return None


def _check_report(job: dict, body: Any) -> tuple[dict | None, JSONResponse | None]:
    if not isinstance(body, dict):
        return None, _error("bad_request", "the body is a JSON object", 422)
    run, status = body.get("run_id"), body.get("status")
    if not isinstance(run, str) or not RUN_ID.match(run):
        return None, _error("bad_request", "run_id: 1-64 of [A-Za-z0-9._:-], the SAME for every report of one run", 422)
    if status not in JOB_STATUS:
        return None, _error("bad_request", f"status must be one of {', '.join(JOB_STATUS)}", 422)
    if "message" in body and not isinstance(body["message"], (str, type(None))):
        return None, _error("bad_request", "message must be a string", 422)
    ops = body.get("ops", [])
    if not isinstance(ops, list) or not all(isinstance(o, dict) for o in ops):
        return None, _error("bad_request", "ops must be a list of objects", 422)
    known = {o["seq"]: o for o in job.get("op_status") or []}
    for o in ops:
        seq = o.get("seq")
        if seq not in known:
            return None, _error("bad_request", f"op seq {seq!r} is not in this job's plan", 422)
        if o.get("object_id") != known[seq]["object_id"]:
            return None, _error("bad_request", f"op {seq} is {known[seq]['object_id']}, not {o.get('object_id')!r}", 422)
        if o.get("status") not in OP_STATUS:
            return None, _error("bad_request", f"op {seq} status must be one of {', '.join(OP_STATUS)}", 422)
        if not isinstance(o.get("attempts", 0), int) or o.get("attempts", 0) < 0:
            return None, _error("bad_request", f"op {seq} attempts must be a non-negative integer", 422)
    try:                                         # poses the edge sends back are in OUR frame, declared
        from bridge.contract import ContractError, assert_frame
        try:
            assert_frame(body, "job result")
        except ContractError as e:
            return None, _error(e.code, e.message, 422)
    except ImportError:
        pass
    return body, None


@router.post("/api/jobs/{job_id}/result")
async def post_result(job_id: str, request: Request, body: Any = Body(None)):
    denied = _authorized(request)                 # before anything about the job is revealed
    if denied is not None:
        return denied
    if not JOB_ID.match(job_id):
        return _error("bad_request", "job_id looks like job_ followed by 16 hex characters", 422)
    with _LOCK:
        job = _load(job_id)
        if job is None:
            return _error("not_found", f"no job {job_id}", 404)
        body, bad = _check_report(job, body)
        if bad is not None:
            return bad
        run, status = body["run_id"], body["status"]
        if job["terminal"]:
            if _canon(job["result"]["report"]) == _canon(body):
                return JSONResponse({**view(job), "replayed": True}, status_code=200)
            return _error("result_conflict", f"{job_id} already ended {job['state']} (run {job['claimed_by']}); "
                                             "the first terminal result stands", 409, stored=job["result"])
        if job["claimed_by"] not in (None, run):
            return _error("claimed", f"{job_id} is being run by {job['claimed_by']}; a job runs once", 409)
        new = copy.deepcopy(job)
        if new["claimed_by"] is None:            # the first report claims it: only an executable job can start
            code, why = _blocker(new)
            if code is not None:
                return _error("head_moved" if code == "head_moved" else "not_executable",
                              f"{job_id} cannot start: {why}", 409, why_not_code=code)
            new.update(claimed_by=run, executor=f"edge:{run}", started_at=_now())
        rows = {o["seq"]: o for o in new["op_status"]}
        for o in body.get("ops", []):
            row = rows[o["seq"]]
            if row["status"] in OP_TERMINAL and o["status"] != row["status"]:
                return _error("op_terminal", f"op {o['seq']} ({row['object_id']}) already ended {row['status']}", 409)
            row.update(status=o["status"], attempts=max(row["attempts"], o.get("attempts", 0)),
                       error=o.get("error") if o.get("error") is not None else row["error"])
        if status == "success" and any(r["status"] != "success" for r in new["op_status"]):
            return _error("bad_request", "status success means every op in the plan reported success; "
                                         "anything less is failed, with the ops saying which", 422)
        new["state"], new["terminal"] = JOB_STATUS[status], status != "running"
        if new["terminal"]:
            new["result"] = {"status": status, "message": body.get("message"), "run_id": run, "at": _now(),
                             "report": body}
            new["finished_at"] = new["result"]["at"]
        new["reports"] += 1
        new["updated_at"] = _now()
        new["progress"] = _progress(new)
        _save(new)
    try:
        import events
        events.hub.publish("job", {"id": job_id, "state": new["state"], "progress": new["progress"]["fraction"],
                                   "command": new.get("command"), "executor": new.get("executor"),
                                   "terminal": new["terminal"]})
    except Exception:  # noqa: BLE001 — the dashboard missing a frame never loses a report
        pass
    return JSONResponse({**view(new), "replayed": False}, status_code=200)
