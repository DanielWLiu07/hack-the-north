"""graph_api.py — the room's commit graph as a control surface (docs/24 Part B).

    GET  /api/graph?limit=100     the DAG, from real `git log`, enriched per node; + `trunk`
    GET  /api/state?ref=          every object at a commit, with its pose, + the zones (scrubber)
    GET  /api/diff?a=<sha>&b=<sha> object-level ops that turn state a into state b
    GET  /api/merge-preview?ours=&theirs=   three-way, object-level: clean changes + conflicts
    GET  /api/cherry-pick-preview?commit=&onto=   one commit's ops, tried against another state
    GET  /api/commands            what this server's allow-list lets the graph run
    POST /api/command             {command, args:{ref}} -> 202 {job_id, ops, estimated_s, ...}
    POST /api/resolve             {object_id, resolution: ours|theirs} -> 202 {job_id, applying, ...}

EVERY PREVIEW IS BUILT FROM GIT READS ONLY: rev-parse, merge-base, rev-list, ls-tree,
diff-tree, show. Never `git merge`, never `merge-tree --write-tree`, nothing that writes an
object, the index or a ref — the three-way merge is done here, per object, in Python.

GIT IS THE SOURCE, ELASTICSEARCH IS THE ENRICHMENT. The graph renders from git alone;
what each commit changed, which capture built it, whether that capture passed the quality
gate and its Sentry trace id come from room-events / room-clouds through store.py (ES
first, the labelled fixture file as fallback). Missing enrichment is null, never guessed.

A REJECTED capture is not a commit — that is the gate working. So a node also carries
`rejected_before`: the captures that were thrown away between its parent and itself. That
is what turns the graph from a log into a diagnosis: the commit is fine, and one click
shows the attempt that was not (/capture/<id>).

THIS PROCESS NEVER WRITES TO room.git, and it does not pretend a robot moved. There is no
executor connected yet (roomctl/executor.py is not built), so POST /api/command validates,
works out the ops an executor WOULD run, answers 202 with `executor: "not_connected"`, and
publishes a `job` event saying exactly that.
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

import room
import store

router = APIRouter()

SHA = re.compile(r"^[0-9a-f]{4,40}$")
BRANCH = re.compile(r"^(?!-)(?!.*\.\.)[A-Za-z0-9._/-]{1,64}$")
HANDLED = ("revert", "checkout", "cherry-pick")   # what POST /api/command can plan (if allow-listed)
GRAPH_COMMANDS = ("revert", "checkout", "resolve", "merge", "cherry-pick")   # what the graph UI asks about
OBJECT_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"   # built into git: a root commit's "parent"
SECONDS_PER_OP = 28                         # docs/16-api.md §2.4: one arm action, ~28 s
US, RS = "\x1f", "\x1e"


def init(es) -> None:
    store.init(es)                          # idempotent: capture_api does the same


def _error(code: str, detail: str, status: int, retryable: bool = False) -> JSONResponse:
    """docs/16-api.md §2.7 — one error shape everywhere."""
    return JSONResponse({"error": code, "detail": detail, "retryable": retryable}, status_code=status)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── git: read-only, through room._git (always --no-optional-locks) ────────────────────

def _parse_refs(decoration: str) -> list[dict]:
    refs = []
    for raw in filter(None, (r.strip() for r in decoration.split(","))):
        head = raw.startswith("HEAD -> ")
        name = raw[8:] if head else raw
        if name == "HEAD":                  # detached HEAD decorates as a bare "HEAD"
            refs.append({"name": "HEAD", "kind": "head", "head": True})
        elif name.startswith("tag: "):
            refs.append({"name": name[5:], "kind": "tag", "head": False})
        else:
            refs.append({"name": name, "kind": "remote" if "/" in name else "branch", "head": head})
    return refs


def _log(limit: int) -> tuple[list[dict], str | None, str | None]:
    """Newest first. --date-order never shows a parent before all of its children: time
    order is the one thing a commit graph must respect, and the layout relies on it."""
    out = room._git("log", "--all", "--date-order", f"-n{limit}",          # noqa: SLF001
                    f"--format=%H{US}%P{US}%D{US}%ct{US}%s{RS}")
    nodes = []
    for rec in filter(None, (r.strip("\n") for r in out.split(RS))):
        sha, parents, deco, ct, subject = (rec.split(US) + [""] * 5)[:5]
        nodes.append({"sha": sha, "parents": parents.split(), "refs": _parse_refs(deco),
                      "ts": _iso(float(ct or 0)), "subject": subject})
    head = room._git("rev-parse", "--verify", "--quiet", "HEAD", check=False).strip() or None   # noqa: SLF001
    branch = room._git("symbolic-ref", "--short", "-q", "HEAD", check=False).strip() or None    # noqa: SLF001
    return nodes, head, branch


def _trunk(limit: int) -> list[str]:
    """HEAD's first-parent history, newest first: the one line through time the scrubber walks."""
    return room._git("rev-list", "--first-parent", f"-n{limit}", "HEAD", check=False).split()      # noqa: SLF001


def _resolve(ref: str) -> str | None:
    sha = room._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).strip()   # noqa: SLF001
    return sha if SHA.match(sha) and len(sha) == 40 else None


def _record_at(sha: str, path: str) -> dict:
    return room._record(room._show(f"{sha}:{path}", "immutable") or "")     # noqa: SLF001


def _git_changed(sha: str, parent: str | None) -> dict:
    """What a commit changed, from git alone — used when no room-event describes it."""
    args = ["diff-tree", "-r", "--no-commit-id", "--name-status", "--no-renames"]
    args += [parent, sha] if parent else ["--root", sha]
    out = room._git(*args, "--", "zones", check=False)                      # noqa: SLF001
    changed: dict[str, list] = {"added": [], "moved": [], "removed": [], "zone": []}
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        object_id, zone = room._object_of(path)                             # noqa: SLF001
        if not object_id:
            continue
        changed[{"A": "added", "D": "removed"}.get(status[:1], "moved")].append(object_id)
        if zone not in changed["zone"]:
            changed["zone"].append(zone)
    return changed | {"from": "git"}


def _ops(a: str, b: str) -> list[dict]:
    """Object-level ops that turn the room at commit `a` into the room at commit `b`."""
    out = room._git("diff-tree", "-r", "--name-status", "--no-renames", a, b, "--", "zones")    # noqa: SLF001
    ops = []
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        object_id, zone = room._object_of(path)                             # noqa: SLF001
        if not object_id:
            continue
        if status.startswith("A"):
            rec = _record_at(b, path)
            ops.append({"op": "added", "object_id": object_id, "class": rec.get("class"), "zone": zone,
                        "to": room._pose(rec)})                             # noqa: SLF001
        elif status.startswith("D"):
            rec = _record_at(a, path)
            ops.append({"op": "removed", "object_id": object_id, "class": rec.get("class"), "zone": zone,
                        "from": room._pose(rec)})                           # noqa: SLF001
        else:
            before, after = _record_at(a, path), _record_at(b, path)
            p0, p1 = room._pose(before), room._pose(after)                  # noqa: SLF001
            op: dict[str, Any] = {"op": "moved", "object_id": object_id, "class": after.get("class"),
                                  "zone": zone, "from": p0, "to": p1}
            if p0 and p1:
                op["delta_m"] = round(math.dist((p0["x"], p0["y"], p0["z"]), (p1["x"], p1["y"], p1["z"])), 3)
                if "yaw" in p0 and "yaw" in p1:
                    op["delta_yaw_deg"] = round(((p1["yaw"] - p0["yaw"] + 180) % 360) - 180, 1)
                if op["delta_m"] == 0 and not op.get("delta_yaw_deg"):
                    op["op"] = "changed"    # the record changed, the object did not move
            ops.append(op)
    # an object that changed ZONE changed path (zones/<zone>/<id>.yaml): git shows a delete and
    # an add, but for the room — and for the arm — that is one move
    gone = {o["object_id"]: o for o in ops if o["op"] == "removed"}
    for o in [o for o in ops if o["op"] == "added" and o["object_id"] in gone]:
        was = gone[o["object_id"]]
        ops.remove(was)
        o.update(op="moved", from_zone=was["zone"], **{"from": was["from"]})
        if o["from"] and o["to"]:
            o["delta_m"] = round(math.dist(*[(p["x"], p["y"], p["z"]) for p in (o["from"], o["to"])]), 3)
    order = {"moved": 0, "changed": 1, "removed": 2, "added": 3}
    return sorted(ops, key=lambda o: (order[o["op"]], -(o.get("delta_m") or 0), o["object_id"]))


def _summary(ops: list[dict]) -> dict:
    return {k: sum(1 for o in ops if o["op"] == k) for k in ("moved", "removed", "added", "changed")}


# ── states, three-way merge and cherry-pick: all from reads ───────────────────────────

_state_cache: dict[str, dict] = {}          # sha -> state; commits are immutable


def _valid_ref(ref: Any) -> bool:
    return isinstance(ref, str) and bool(ref == "HEAD" or SHA.match(ref) or BRANCH.match(ref))


def _name(ref: str, sha: str) -> str:
    """What to call a side in words: the branch if there is one, else the short sha."""
    if ref == "HEAD":
        return room._git("symbolic-ref", "--short", "-q", "HEAD", check=False).strip() or sha[:7]   # noqa: SLF001
    return sha[:7] if SHA.match(ref) and sha.startswith(ref) else ref


def _state(sha: str) -> dict:
    """Every object in the room at a commit: {object_id: {object_id, class, zone, pose, ...}}."""
    if sha not in _state_cache:
        if len(_state_cache) > 256:
            _state_cache.clear()
        objects = {}
        for path in room._git("ls-tree", "-r", "--name-only", sha, "--", "zones").splitlines():    # noqa: SLF001
            object_id, zone = room._object_of(path)                         # noqa: SLF001
            if not object_id:
                continue
            rec = _record_at(sha, path)
            objects[object_id] = {"object_id": object_id, "class": rec.get("class"), "zone": zone,
                                  "pose": room._pose(rec), "color": rec.get("color"),       # noqa: SLF001
                                  "extents": rec.get("extents") if isinstance(rec.get("extents"), dict) else None}
        zones = {}
        for name, z in ((_record_at(sha, "room.yaml").get("zones")) or {}).items():
            try:
                zones[name] = {"min": [float(v) for v in z["min"][:3]], "max": [float(v) for v in z["max"][:3]]}
            except (KeyError, TypeError, ValueError):
                continue
        _state_cache[sha] = {"objects": objects, "zones": zones}
    return _state_cache[sha]


def _same_pose(p: dict | None, q: dict | None) -> bool:
    if not p or not q:
        return p is q or (not p and not q)
    return all(abs(p.get(k, 0.0) - q.get(k, 0.0)) < 1e-6 for k in ("x", "y", "z", "yaw"))


def _dist(p: dict | None, q: dict | None) -> float | None:
    if not p or not q:
        return None
    return round(math.dist((p["x"], p["y"], p["z"]), (q["x"], q["y"], q["z"])), 3)


def _where(obj: dict | None) -> dict | None:
    return {"zone": obj["zone"], "pose": obj["pose"]} if obj else None


def _merge_preview(ours: str, theirs: str) -> dict | None:
    """Object-level three-way merge of `theirs` INTO `ours`. None = no common ancestor."""
    base = room._git("merge-base", ours, theirs, check=False).strip()      # noqa: SLF001
    if not SHA.match(base):
        return None
    ours_ops = {o["object_id"]: o for o in _ops(base, ours)}
    theirs_ops = {o["object_id"]: o for o in _ops(base, theirs)}
    sb, so, st = (_state(x)["objects"] for x in (base, ours, theirs))
    clean, conflicts = [], []
    for object_id in sorted(set(ours_ops) | set(theirs_ops)):
        a, b = ours_ops.get(object_id), theirs_ops.get(object_id)
        if a and b:
            o, t = so.get(object_id), st.get(object_id)
            agree = (o is None and t is None) or (o and t and o["zone"] == t["zone"] and _same_pose(o["pose"], t["pose"]))
            if agree:
                clean.append(a | {"side": "both"})
                continue
            conflicts.append({"object_id": object_id, "class": (o or t or sb.get(object_id) or {}).get("class"),
                              "zone": (o or t or sb.get(object_id) or {}).get("zone"),
                              "base": _where(sb.get(object_id)), "ours": _where(o), "theirs": _where(t),
                              "ours_op": a["op"], "theirs_op": b["op"],
                              "distance_m": _dist((o or {}).get("pose"), (t or {}).get("pose"))})
        else:
            clean.append((a or b) | {"side": "ours" if a else "theirs"})
    order = {"theirs": 0, "both": 1, "ours": 2}
    clean.sort(key=lambda o: (order[o["side"]], o["object_id"]))
    return {"base": base, "up_to_date": base == theirs, "fast_forward": base == ours and base != theirs,
            "clean": clean, "conflicts": conflicts,
            "summary": {"would_apply": sum(1 for o in clean if o["side"] == "theirs" and o["op"] != "changed"),
                        "already_on_ours": sum(1 for o in clean if o["side"] != "theirs"),
                        "conflicts": len(conflicts)}}


def _cherry_pick(commit: str, onto: str, onto_name: str) -> dict | None:
    """One commit's ops (commit^ -> commit), each tried against the state at `onto`.
    None = a merge commit (it has no single parent to diff against)."""
    parents = room._git("rev-list", "--parents", "-n1", commit).split()[1:]     # noqa: SLF001
    if len(parents) > 1:
        return None
    target = _state(onto)["objects"]
    ops = []
    for op in _ops(parents[0] if parents else EMPTY_TREE, commit):
        have = target.get(op["object_id"])
        here, frm, to = (have or {}).get("pose"), op.get("from"), op.get("to")
        status, reason = "applies", None
        if op["op"] in ("moved", "changed"):
            if not have:
                status, reason = "conflict", f"it is not in {onto_name}: there is nothing to move"
            elif _same_pose(here, to):
                status, reason = "already_applied", f"it already stands there in {onto_name}"
            elif not _same_pose(here, frm):
                d = _dist(here, frm)
                status = "conflict"
                reason = (f"in {onto_name} it already stands elsewhere"
                          + (f", {d * 100:.0f} cm from where this commit expects it" if d is not None else ""))
        elif op["op"] == "added":
            if have and _same_pose(here, to):
                status, reason = "already_applied", f"it is already there in {onto_name}"
            elif have:
                status, reason = "conflict", f"it is already in {onto_name}, somewhere else"
        elif op["op"] == "removed":
            if not have:
                status, reason = "already_applied", f"it is already gone from {onto_name}"
            elif not _same_pose(here, frm):
                status, reason = "conflict", f"it was moved in {onto_name} since; removing it would discard that"
        ops.append(op | {"status": status, "reason": reason, "onto": _where(have)})
    subject = room._git("log", "-1", "--format=%s", commit).strip()            # noqa: SLF001
    return {"commit": {"sha": commit, "subject": subject, "parent": parents[0] if parents else None},
            "ops": ops, "summary": {k: sum(1 for o in ops if o["status"] == k)
                                    for k in ("applies", "conflict", "already_applied")}}


# ── enrichment: room-events + room-clouds through store.py ────────────────────────────

async def _enrichment() -> tuple[list[dict], dict[str, dict], str | None, str | None]:
    try:
        (events, source), (clouds, _) = await asyncio.gather(
            # newest first: `size` truncates, and the graph is about the most recent history
            store._find("room-events", {}, size=1000, newest_first=True, label="graph.events"),        # noqa: SLF001
            store._find("room-clouds", {}, size=1000, newest_first=True, label="graph.captures"))      # noqa: SLF001
    except Exception as e:                  # noqa: BLE001 — the graph still renders from git alone
        return [], {}, None, getattr(e, "code", type(e).__name__)
    return events, {c.get("capture_id"): c for c in clouds}, source, None


def _quality(cloud: dict | None) -> tuple[bool | None, list[dict]]:
    """(quality_ok, failing values). The recorded verdict wins; else the gate is evaluated on
    whatever was recorded; else None — never a guess."""
    if not cloud:
        return None, []
    g = store.gate(store._num(cloud.get("skew_ms")), store._num(cloud.get("tilt_rate_max")),    # noqa: SLF001
                   store._num(cloud.get("coverage_pct")))                                       # noqa: SLF001
    failing = [{"name": n, "value": g["values"][n], "limit": f'{store.GATE[n]["op"]} {store.GATE[n]["value"]:g}',
                "unit": store.GATE[n]["unit"]} for n in g["failing"]]
    recorded = cloud.get("quality_ok")
    return (recorded if isinstance(recorded, bool) else g["pass"]), failing


def _enrich(nodes: list[dict], events: list[dict], clouds: dict[str, dict]) -> list[dict]:
    commits = {e["commit_sha"]: e for e in events if e.get("event_type") == "commit" and e.get("commit_sha")}
    by_sha = {n["sha"]: n for n in nodes}
    for n in nodes:
        ev = commits.get(n["sha"])
        cloud = clouds.get(ev.get("capture_id")) if ev else None
        ok, _ = _quality(cloud)
        n["capture_id"] = ev.get("capture_id") if ev else None
        n["quality_ok"] = ok
        n["suspect"] = ok is False          # its OWN capture failed the gate and it was committed anyway
        n["sentry_trace_id"] = (cloud or {}).get("sentry_trace_id") or (ev or {}).get("sentry_trace_id")
        n["rejected_before"] = []
        if ev:
            n["changed"] = {"added": ev.get("objects_added") or [], "moved": ev.get("objects_moved") or [],
                            "removed": ev.get("objects_removed") or [], "zone": ev.get("zone") or [],
                            "from": "room-events"}
    # a rejected capture belongs to the NEXT commit on its branch: the attempt thrown away before it
    pending = []
    ordered = sorted(commits.values(), key=store.when)
    for r in sorted((e for e in events if e.get("event_type") == "capture_rejected"),
                    key=store.when):
        later = [e for e in ordered if store.when(e) > store.when(r) and e["commit_sha"] in by_sha]
        nxt = next((e for e in later if e.get("branch") == r.get("branch")), later[0] if later else None)
        _, failing = _quality(clouds.get(r.get("capture_id")))
        item = {"capture_id": r.get("capture_id"), "quality_ok": False, "failing": failing,
                "ts": r.get("@timestamp"), "outcome": r.get("outcome"),
                "would_have": {"added": len(r.get("objects_added") or []), "moved": len(r.get("objects_moved") or []),
                               "removed": len(r.get("objects_removed") or [])},
                "sentry_trace_id": r.get("sentry_trace_id")}
        (by_sha[nxt["commit_sha"]]["rejected_before"] if nxt else pending).append(item)
    return pending


@router.get("/api/graph")
async def graph(limit: int = Query(100, ge=1, le=500)):
    try:
        (nodes, head, branch), trunk = await asyncio.gather(asyncio.to_thread(_log, limit),
                                                            asyncio.to_thread(_trunk, limit))
    except room.RoomError as e:
        return _error("room_unavailable", str(e), 503, retryable=True)
    events, clouds, source, err = await _enrichment()
    pending = _enrich(nodes, events, clouds)
    for n in nodes:                         # no room-event for it: say what git itself knows
        if "changed" not in n:
            n["changed"] = await asyncio.to_thread(_git_changed, n["sha"], n["parents"][0] if n["parents"] else None)
    return {"nodes": nodes, "head": head, "branch": branch, "trunk": trunk, "source": source or "git-only",
            "enriched": source is not None, "enrichment_error": err,
            "rejected_pending": pending,    # rejected captures with no commit after them yet
            "executor": "not_connected"}


@router.get("/api/diff")
async def diff(a: str = Query(...), b: str = Query(...)):
    for v in (a, b):
        if not SHA.match(v):
            return _error("bad_request", f"not a commit sha: {v[:48]!r}", 422)
    try:
        sa, sb = await asyncio.gather(asyncio.to_thread(_resolve, a), asyncio.to_thread(_resolve, b))
        if not sa or not sb:
            return _error("not_found", f"no commit {a if not sa else b} in the room's history", 404)
        ops = await asyncio.to_thread(_ops, sa, sb)
    except room.RoomError as e:
        return _error("room_unavailable", str(e), 503, retryable=True)
    return {"a": sa, "b": sb, "ops": ops, "summary": _summary(ops)}


def _allowed() -> set[str]:
    raw = os.getenv("WEB_ALLOWED_COMMANDS", "").split("#", 1)[0]
    return {c.strip() for c in raw.split(",") if c.strip()}


@router.post("/api/command", status_code=202)
async def command(payload: dict = Body(...)):
    name = payload.get("command")
    ref = (payload.get("args") or {}).get("ref") if isinstance(payload.get("args"), dict) else None
    if not isinstance(name, str) or not re.fullmatch(r"[a-z-]{1,24}", name):
        return _error("bad_request", "command must be a short lowercase name", 422)
    if name not in _allowed():
        return _error("command_not_allowed", f"'{name}' is not on this server's allow-list", 403)
    if name not in HANDLED:
        return _error("unsupported_command", f"'{name}' is allowed but is not a graph command "
                                             f"({', '.join(HANDLED)})", 400)
    if not _valid_ref(ref):
        return _error("bad_request", "args.ref must be a commit sha, a branch name or HEAD", 422)
    try:
        head, target = await asyncio.gather(asyncio.to_thread(_resolve, "HEAD"), asyncio.to_thread(_resolve, ref))
        if not head or not target:
            return _error("not_found", f"no commit or branch {ref!r} in the room's history", 404)
        if name == "revert" and target == head:
            # docs/16-api.md's own example, `revert HEAD`: undo the last commit
            target = await asyncio.to_thread(_resolve, "HEAD^") or target
        if name == "cherry-pick":           # only that commit's own ops, and only the ones that apply
            picked = await asyncio.to_thread(_cherry_pick, target, head, "HEAD")
            if picked is None:
                return _error("bad_request", "a merge commit cannot be cherry-picked here", 422)
            ops = [{k: v for k, v in o.items() if k not in ("status", "reason", "onto")}
                   for o in picked["ops"] if o["status"] == "applies"]
        else:
            ops = await asyncio.to_thread(_ops, head, target)
        dirty = bool((await asyncio.to_thread(room._git, "status", "--porcelain=v1",            # noqa: SLF001
                                              "--untracked-files=all")).strip())
    except room.RoomError as e:
        return _error("room_unavailable", str(e), 503, retryable=True)
    physical = [o for o in ops if o["op"] != "changed"]
    job = {"job_id": "job_" + secrets.token_hex(2), "command": name, "ref": ref, "head": head, "target": target,
           "ops": physical, "summary": _summary(physical), "estimated_s": SECONDS_PER_OP * len(physical),
           "working_tree_dirty": dirty,
           # the honest part: nothing is wired to a robot yet, and nothing was written to room.git
           "executor": "not_connected", "state": "queued (no executor connected)",
           "detail": "validated and planned only: no robot moved and room.git was not touched"}
    import events                                                           # the SSE hub (events.py)
    events.hub.publish("job", {"id": job["job_id"], "state": job["state"], "progress": 0, "command": name,
                               "target": target, "ops": len(physical), "executor": "not_connected"})
    return job


# ── reads for the scrubber, merge and cherry-pick previews ────────────────────────────

async def _two_refs(a: str, b: str, names: tuple[str, str]):
    """Validate + resolve two refs -> ((sha, sha), None) or (None, error response)."""
    for label, v in zip(names, (a, b)):
        if not _valid_ref(v):
            return None, _error("bad_request", f"{label} must be a commit sha, a branch name or HEAD", 422)
    sa, sb = await asyncio.gather(asyncio.to_thread(_resolve, a), asyncio.to_thread(_resolve, b))
    if not sa or not sb:
        return None, _error("not_found", f"no commit or branch {(a if not sa else b)!r} in the room's history", 404)
    return (sa, sb), None


@router.get("/api/state")
async def state(ref: str = Query("HEAD")):
    if not _valid_ref(ref):
        return _error("bad_request", "ref must be a commit sha, a branch name or HEAD", 422)
    try:
        sha = await asyncio.to_thread(_resolve, ref)
        if not sha:
            return _error("not_found", f"no commit or branch {ref!r} in the room's history", 404)
        st = await asyncio.to_thread(_state, sha)
    except room.RoomError as e:
        return _error("room_unavailable", str(e), 503, retryable=True)
    return {"sha": sha, "objects": sorted(st["objects"].values(), key=lambda o: o["object_id"]), "zones": st["zones"]}


@router.get("/api/merge-preview")
async def merge_preview(ours: str = Query("HEAD"), theirs: str = Query(...)):
    try:
        shas, err = await _two_refs(ours, theirs, ("ours", "theirs"))
        if err:
            return err
        preview = await asyncio.to_thread(_merge_preview, *shas)
        if preview is None:
            return _error("no_common_ancestor", f"{ours} and {theirs} share no history", 409)
        names = await asyncio.gather(asyncio.to_thread(_name, ours, shas[0]), asyncio.to_thread(_name, theirs, shas[1]))
    except room.RoomError as e:
        return _error("room_unavailable", str(e), 503, retryable=True)
    return {"ours": {"ref": ours, "sha": shas[0], "name": names[0]},
            "theirs": {"ref": theirs, "sha": shas[1], "name": names[1]}, **preview,
            "executor": "not_connected", "detail": "a preview computed from reads: nothing was merged or written"}


@router.get("/api/cherry-pick-preview")
async def cherry_pick_preview(commit: str = Query(...), onto: str = Query("HEAD")):
    try:
        shas, err = await _two_refs(commit, onto, ("commit", "onto"))
        if err:
            return err
        onto_name = await asyncio.to_thread(_name, onto, shas[1])
        picked = await asyncio.to_thread(_cherry_pick, shas[0], shas[1], onto_name)
        if picked is None:
            return _error("bad_request", "a merge commit cannot be cherry-picked here", 422)
    except room.RoomError as e:
        return _error("room_unavailable", str(e), 503, retryable=True)
    allowed = "cherry-pick" in _allowed()
    return {**picked, "onto": {"ref": onto, "sha": shas[1], "name": onto_name}, "allowed": allowed,
            "reason": None if allowed else "cherry-pick is not on this server's allow-list (WEB_ALLOWED_COMMANDS)",
            "executor": "not_connected"}


@router.get("/api/commands")
async def commands():
    """The allow-list, so the UI never hardcodes what it may run."""
    allowed = _allowed()
    return {"allowed": sorted(allowed), "graph": {c: c in allowed for c in GRAPH_COMMANDS},
            "executor": "not_connected"}


@router.post("/api/resolve", status_code=202)
async def resolve(payload: dict = Body(...)):
    """docs/16-api.md §4b. `ours` defaults to HEAD; `theirs` to MERGE_HEAD when the room really is
    mid-merge, else it must be given (the graph previews merges that have not been started)."""
    object_id, resolution = payload.get("object_id"), payload.get("resolution")
    ours, theirs = payload.get("ours", "HEAD"), payload.get("theirs")
    if "resolve" not in _allowed():
        return _error("command_not_allowed", "'resolve' is not on this server's allow-list", 403)
    if not isinstance(object_id, str) or not OBJECT_ID.match(object_id):
        return _error("bad_request", "object_id must look like mug_a1b2", 422)
    if resolution not in ("ours", "theirs"):
        return _error("bad_request", "resolution must be 'ours' or 'theirs'", 422)
    try:
        if theirs is None:
            mid_merge = await asyncio.to_thread(_resolve, "MERGE_HEAD")
            if not mid_merge:
                return _error("no_merge", "the room is not mid-merge: say which branch with `theirs`", 409)
            theirs = mid_merge
        shas, err = await _two_refs(ours, theirs, ("ours", "theirs"))
        if err:
            return err
        preview = await asyncio.to_thread(_merge_preview, *shas)
        if preview is None:
            return _error("no_common_ancestor", f"{ours} and {theirs} share no history", 409)
        conflict = next((c for c in preview["conflicts"] if c["object_id"] == object_id), None)
        if not conflict:
            return _error("not_in_conflict", f"{object_id} is not in conflict between {ours} and {theirs}", 409)
        names = await asyncio.gather(asyncio.to_thread(_name, ours, shas[0]), asyncio.to_thread(_name, theirs, shas[1]))
    except room.RoomError as e:
        return _error("room_unavailable", str(e), 503, retryable=True)
    # the room physically stands at `ours`: keeping ours moves nothing, taking theirs is one op
    ops = []
    if resolution == "theirs":
        o, t = conflict["ours"], conflict["theirs"]
        kind = "moved" if o and t else "removed" if o else "added"
        ops = [{"op": kind, "object_id": object_id, "class": conflict["class"], "zone": (t or o)["zone"],
                "from": (o or {}).get("pose"), "to": (t or {}).get("pose"), "delta_m": conflict["distance_m"]}]
    job = {"job_id": "job_" + secrets.token_hex(2), "object_id": object_id, "resolution": resolution,
           "applying": names[0] if resolution == "ours" else names[1], "ours": shas[0], "theirs": shas[1],
           "ops": ops, "estimated_s": SECONDS_PER_OP * len(ops),
           "executor": "not_connected", "state": "queued (no executor connected)",
           "detail": "validated and planned only: no robot moved, nothing was merged, room.git was not touched"}
    import events                                                           # the SSE hub (events.py)
    events.hub.publish("job", {"id": job["job_id"], "state": job["state"], "progress": 0, "command": "resolve",
                               "object_id": object_id, "applying": job["applying"], "ops": len(ops),
                               "executor": "not_connected"})
    return job
