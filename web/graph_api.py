"""graph_api.py — the room's commit graph as a control surface (docs/24 Part B).

    GET  /api/graph?limit=100     the DAG, from real `git log`, enriched per node; + `trunk`
    GET  /api/state?ref=          every object at a commit, with its pose, + the zones (scrubber)
    GET  /api/diff?a=<sha>&b=<sha> object-level ops that turn state a into state b
    GET  /api/merge-preview?ours=&theirs=   three-way, object-level: clean changes + conflicts
    GET  /api/cherry-pick-preview?commit=&onto=   one commit's ops, tried against another state
    GET  /api/commands            what this server's allow-list lets the graph run
    GET  /api/when?phrase=        "before dinner" -> the commit the room was at then, and how that was found
    GET  /api/why/{ref}           was that commit's picture of the room trustworthy? gate + telemetry + trace
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
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

import room
import store

router = APIRouter()

SHA = re.compile(r"^[0-9a-f]{4,40}$")
BRANCH = re.compile(r"^(?!-)(?!.*\.\.)[A-Za-z0-9._/-]{1,64}$")
HANDLED = ("revert", "restore", "checkout", "cherry-pick")   # what POST /api/command can plan (if allow-listed)
# Reads, not plans: allow-listed verbs gitirl-agent's DanielAPIClient.plan_command() sends (awzheng/gitirl
# @b4f3e07). They answer 200 {kind:"read"} from git reads — no job, no ops — instead of a 400 (docs/31 §3b).
READS = ("status", "diff", "log")
GRAPH_COMMANDS = ("revert", "restore", "checkout", "resolve", "merge", "cherry-pick")   # what the graph UI asks about
OBJECT_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"   # built into git: a root commit's "parent"
SECONDS_PER_OP = 28                         # docs/16-api.md §2.4: one arm action, ~28 s
# Every answer that carries a pose DECLARES its frame and units (Andrew's ask 4, ANDREW-HANDOFF.md §5):
# never left for a client to infer. Same token as bridge/contract.FRAME and roomctl's gitspace.plan/1.
FRAME = "world_z_up"
UNITS = {"position": "m", "yaw": "deg", "duration": "s"}


def _framed(body: dict) -> dict:
    return {**body, "frame": FRAME, "units": UNITS}
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
    """Exactly what git resolves, nothing more: callers treat a name that resolves here as a REAL ref
    (the bridge prints it in `git restore --source=<ref>`). Names as people say them go through resolve_state()."""
    sha = room._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).strip()   # noqa: SLF001
    return sha if SHA.match(sha) and len(sha) == 40 else None


def _state_key(name: str) -> str:
    """`Study Mode`, `study_mode`, `study-mode` and `study` are one state. Andrew's parser turns "set my
    room back to study mode" into target_state `study` but leaves `restore study-mode` as `study-mode`,
    so whichever way the ref was named, one of the two phrasings missed it."""
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return re.sub(r"-mode$", "", key) or key


def _es_client():
    """roomctl's joins take the OFFICIAL elasticsearch client; web's own `Elastic` is an async httpx proxy
    with a different shape. None (not an error) when the cluster is unconfigured or parked: the callers
    fall back to git, or say plainly that this answer lives in Elasticsearch."""
    try:
        import es_shared
        return es_shared.client()
    except Exception:  # noqa: BLE001 — unconfigured, parked, or the package is missing
        return None


def _roomctl_repo():
    from roomctl.repo import Repo
    return Repo(room.room_path())


def _at_time(said: str) -> dict | None:
    """"before dinner" -> the last commit strictly before then. roomctl owns both halves: `when.parse_when`
    places the phrase (and REFUSES anything it cannot place, so a state name never becomes a time), and
    `when.commit_before` finds the commit — from room-events through ES|QL (the Elastic showpiece), with git's
    own history as the fallback, and `source` says which answered. None when the words are not a time at all;
    that is not an error, just a different question."""
    try:
        from roomctl.when import commit_before, parse_when
    except ImportError:
        return None                                       # a checkout without roomctl: times are simply not a thing here
    try:
        when = parse_when(said)
    except (ValueError, OverflowError):
        return None
    es = _es_client()                                     # the official client the joins need; None when there is no cluster
    try:
        got = commit_before(_roomctl_repo(), when, es=es)
    except Exception as e:  # noqa: BLE001 — "no commit before then" is an answer, not a crash
        return {"sha": None, "how": "time", "when": when.isoformat(timespec="minutes"), "detail": str(e)}
    return {"sha": got["sha"], "ref": got["sha"][:7], "how": "time", "when": when.isoformat(timespec="minutes"),
            "at": got.get("at"), "message": got.get("message"), "source": got.get("source")}


def resolve_state(said: str) -> dict:
    """{"sha", "ref", "how", "candidates"}. how: "exact" (git resolved it as written) · "alias" (a tag or
    local branch that is the same state under another spelling) · "time" (a moment, not a name: "before
    dinner" — `when` and `at` say which commit and how it was found) · "ambiguous" (two spellings, two
    DIFFERENT commits: sha is None and candidates says which — a robot is never sent to a guess) · None."""
    out = {"said": said, "sha": None, "ref": None, "how": None, "candidates": []}
    if not isinstance(said, str) or not said.strip():
        return out
    sha = _resolve(said)
    if sha:
        return {**out, "sha": sha, "ref": said, "how": "exact"}
    if SHA.match(said) and len(said) >= 7:               # it was a sha, and git does not have it: not a state name
        return out
    key = _state_key(said)
    if not key:
        return {**out, **(_at_time(said) or {})}
    raw = room._git("for-each-ref", "--format=%(refname:short)%09%(objectname)%09%(*objectname)",   # noqa: SLF001
                    "refs/tags", "refs/heads", check=False)
    same = {}
    for line in raw.splitlines():                        # the raw text never reaches git: it is matched HERE, against real refs
        name, obj, peeled = (line.split("\t") + ["", ""])[:3]
        if name and _state_key(name) == key:
            same[name] = peeled or obj                   # an annotated tag peels to its commit
    if not same:
        return {**out, **(_at_time(said) or {})}          # not a name in this room: it may still be a TIME
    names = sorted(same, key=lambda n: (n.lower() != said.strip().lower(), len(n), n))
    if len(set(same.values())) > 1:
        return {**out, "how": "ambiguous", "candidates": names}
    commit = room._git("rev-parse", "--verify", "--quiet", f"{names[0]}^{{commit}}", check=False).strip()   # noqa: SLF001
    return {**out, "sha": commit if SHA.match(commit) and len(commit) == 40 else None, "ref": names[0], "how": "alias", "candidates": names}


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


def _identity(rec: dict) -> dict:
    """The fields roomctl carries FORWARD, never re-derives: class, colour and when the room first saw
    this object (roomctl/state.py `settle`, docs/20 Part 4). A stable object_id across two commits IS
    "the same physical thing" — perception/associate.py decided that, and nothing here re-decides it.
    `first_seen` is what tells a genuinely new object from an old one that has only moved."""
    return {"class": rec.get("class"), "color": rec.get("color"), "first_seen": rec.get("first_seen")}


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
            ops.append({"op": "added", "object_id": object_id, **_identity(rec), "zone": zone,
                        "to": room._pose(rec)})                             # noqa: SLF001
        elif status.startswith("D"):
            rec = _record_at(a, path)
            ops.append({"op": "removed", "object_id": object_id, **_identity(rec), "zone": zone,
                        "from": room._pose(rec)})                           # noqa: SLF001
        else:
            before, after = _record_at(a, path), _record_at(b, path)
            p0, p1 = room._pose(before), room._pose(after)                  # noqa: SLF001
            op: dict[str, Any] = {"op": "moved", "object_id": object_id, **_identity(after),
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


STATE = re.compile(r"^(?!-)(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9 ._/-]{0,63}$")   # "study mode": a name as SAID, spaces allowed


def _valid_ref(ref: Any) -> bool:
    return isinstance(ref, str) and bool(ref == "HEAD" or SHA.match(ref) or BRANCH.match(ref) or STATE.match(ref))


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
            objects[object_id] = {"object_id": object_id, "zone": zone, **_identity(rec),
                                  "pose": room._pose(rec),                                  # noqa: SLF001
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
    return _try_ops(_ops(parents[0] if parents else EMPTY_TREE, commit), commit, parents, onto, onto_name)


def _revert(commit: str, onto: str, onto_name: str) -> dict | None:
    """`git revert <commit>`: the INVERSE of that one commit's own ops (commit -> commit^), each tried
    against the state at `onto`. It undoes ONE commit and leaves everything after it alone — it is not
    "put the room back to how it was then" (that is restore / checkout: _ops(HEAD, ref)). An object
    somebody moved again since is a conflict, reported, never silently dragged back.
    None = a merge commit (git needs -m to know which parent; the graph does not guess)."""
    parents = room._git("rev-list", "--parents", "-n1", commit).split()[1:]     # noqa: SLF001
    if len(parents) > 1:
        return None
    return _try_ops(_ops(commit, parents[0] if parents else EMPTY_TREE), commit, parents, onto, onto_name)


def _try_ops(candidate: list[dict], commit: str, parents: list[str], onto: str, onto_name: str) -> dict:
    target = _state(onto)["objects"]
    ops = []
    for op in candidate:
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
                          + (f", {d * 100:.0f} cm from where this expects it" if d is not None else ""))
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
    return _framed({"a": sa, "b": sb, "ops": ops, "summary": _summary(ops)})


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
        return _error("command_not_allowed", _not_allowed(name), 403)
    if name in READS:
        return JSONResponse(await asyncio.to_thread(_read_command, name, ref), status_code=200)
    if name not in HANDLED:
        return _error("unsupported_command", f"'{name}' is allowed but is not a graph command "
                                             f"({', '.join(HANDLED)})", 400)
    if not _valid_ref(ref):
        return _error("bad_request", "args.ref must be a commit sha, a branch name or HEAD", 422)
    try:
        head, found = await asyncio.gather(asyncio.to_thread(_resolve, "HEAD"), asyncio.to_thread(resolve_state, ref))
        target = found["sha"]
        if found["how"] == "ambiguous":     # `focus` and `focus-mode` on two different commits: say so, never pick
            return _error("ambiguous_state", f"{ref!r} could be {' or '.join(found['candidates'])}, and they are different "
                                             "commits — name one of them exactly", 409)
        if not head or not target:
            return _error("not_found", f"no commit, branch or state {ref!r} in the room's history", 404)
        want = payload.get("base_sha")      # the HEAD the preview was computed from: a plan for a room that has moved on is stale
        if want is not None and not (isinstance(want, str) and re.fullmatch(r"[0-9a-f]{7,40}", want) and head.startswith(want)):
            return _error("head_moved", f"HEAD is {head[:7]} now, not {str(want)[:7]}: preview again before running", 409, retryable=True)
        skipped: list[dict] = []
        if name in ("revert", "cherry-pick"):   # ONE commit's own ops (inverted for revert), and only the ones that still apply
            tried = await asyncio.to_thread(_revert if name == "revert" else _cherry_pick, target, head, "HEAD")
            if tried is None:
                return _error("bad_request", f"a merge commit cannot be {'reverted' if name == 'revert' else 'cherry-picked'} here", 422)
            strip = lambda o: {k: v for k, v in o.items() if k not in ("status", "reason", "onto")}   # noqa: E731
            ops = [strip(o) for o in tried["ops"] if o["status"] == "applies"]
            skipped = [{"object_id": o["object_id"], "op": o["op"], "status": o["status"], "why": o["reason"]}
                       for o in tried["ops"] if o["status"] != "applies"]
        else:                                   # restore, checkout: make the room match that state
            ops = await asyncio.to_thread(_ops, head, target)
        dirty = bool((await asyncio.to_thread(room._git, "status", "--porcelain=v1",            # noqa: SLF001
                                              "--untracked-files=all")).strip())
    except room.RoomError as e:
        return _error("room_unavailable", str(e), 503, retryable=True)
    physical = [o for o in ops if o["op"] != "changed"]
    import jobs                          # the ledger (jobs.py): a job is named by what it would DO
    prep = await asyncio.to_thread(jobs.prepare, name, found["ref"], target)
    job = {"job_id": jobs.job_id_for(name, target, head, prep["observed"]), "command": name, "ref": ref,
           "head": head, "target": target,
           # how the words became a commit: "exact", or "alias" when `study` found the ref named `study-mode`
           "resolved": {"ref": found["ref"], "how": found["how"]},
           # `ops` is the git-level PREVIEW (HEAD -> target) the graph draws. `plan` is what an edge
           # EXECUTES: roomctl's ordered gitspace.plan/1 from the room as last scanned (null for revert /
           # cherry-pick, which git must commit before they can be planned — see plan_unavailable)
           "ops": physical, "skipped": skipped, "summary": _summary(physical), "estimated_s": SECONDS_PER_OP * len(physical),
           "plan": prep["plan"], "plan_unavailable": prep["plan_unavailable"],
           # the room as last scanned (working tree), hashed: the 4th input of job_id, so anyone can recompute it
           "observed_room": prep["observed"],
           "working_tree_dirty": dirty,
           # the honest part: this process wrote nothing to room.git and moved nothing
           "executor": "not_connected",
           "detail": "validated and planned only: no robot moved and room.git was not touched"}
    stored, replayed = await asyncio.to_thread(jobs.open_job, job)
    # how THIS request's words became a commit (a replay's stored `resolved` is the first asker's)
    asked = {"ref": ref, "resolved": {"ref": found["ref"], "how": found["how"]}}
    if replayed:                         # the same job, asked again: its CURRENT state, and no second event
        return JSONResponse({**jobs.view(stored), "asked": asked, "replayed": True}, status_code=200)
    import events                                                           # the SSE hub (events.py)
    events.hub.publish("job", {"id": stored["job_id"], "state": stored["state"], "progress": 0, "command": name,
                               "target": target, "ops": len(physical), "executor": "not_connected",
                               "executable": jobs.view(stored)["executable"]})
    return {**jobs.view(stored), "asked": asked, "replayed": False}


def _read_command(name: str, ref: str | None) -> dict:
    """status / diff [ref] / log through POST /api/command: git reads only, never a job."""
    try:
        if name == "log":
            raw = room._git("log", "--format=%h%x09%s", "-n", "15")                    # noqa: SLF001
            result = {"commits": [dict(zip(("sha", "subject"), l.split("\t", 1))) for l in raw.splitlines() if l]}
        elif name == "diff" and ref:
            head, target = _resolve("HEAD"), (_resolve(ref) if _valid_ref(ref) else None)
            if not target:
                return {"kind": "read", "command": name, "ref": ref, "error": "not_found",
                        "detail": f"no commit or branch {ref!r} in the room's history"}
            result = {"a": head, "b": target, "ops": _ops(head, target)}
        else:                                                                         # status, bare diff
            result = room.summary(room.snapshot())
    except room.RoomError as e:
        return {"kind": "read", "command": name, "ref": ref, "error": "room_unavailable", "detail": str(e)}
    return _framed({"kind": "read", "command": name, "ref": ref, "result": result})


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
    return _framed({"sha": sha, "objects": sorted(st["objects"].values(), key=lambda o: o["object_id"]), "zones": st["zones"]})


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
    return _framed({"ours": {"ref": ours, "sha": shas[0], "name": names[0]},
                    "theirs": {"ref": theirs, "sha": shas[1], "name": names[1]}, **preview,
                    "executor": "not_connected", "detail": "a preview computed from reads: nothing was merged or written"})


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
    return _framed({**picked, "onto": {"ref": onto, "sha": shas[1], "name": onto_name}, "allowed": allowed,
                    "reason": None if allowed else "cherry-pick is not on this server's allow-list (WEB_ALLOWED_COMMANDS)",
                    "executor": "not_connected"})


def _not_allowed(name: str) -> str:
    """Say WHY and what to do, not a bare refusal: an edge reading this decides what to call next."""
    allowed = sorted(_allowed())
    now = f"Allowed here now: {', '.join(allowed) or 'nothing'}."
    if name in HANDLED:
        kind = ("an EXECUTABLE job (a gitspace.plan/1 an edge can run)" if name in ("restore", "checkout")
                else "a PLAN-ONLY job (git computes that tree by committing; roomctl runs it)")
        return (f"'{name}' is a command this server plans as {kind}, but it is not on this server's allow-list "
                f"(WEB_ALLOWED_COMMANDS): the operator enables it by adding '{name}'. No other endpoint makes this "
                f"job. {now}")
    if name in READS:
        return f"'{name}' is a read, but it is not on this server's allow-list (WEB_ALLOWED_COMMANDS). {now}"
    return f"'{name}' is not on this server's allow-list (WEB_ALLOWED_COMMANDS). {now}"


@router.get("/api/commands")
async def commands():
    """The allow-list, so the UI never hardcodes what it may run. `jobs` says, per graph command, whether
    its job is EXECUTABLE by an edge (it carries a gitspace.plan/1) or PLAN-ONLY (roomctl runs it)."""
    allowed = _allowed()
    return {"allowed": sorted(allowed), "graph": {c: c in allowed for c in GRAPH_COMMANDS},
            "jobs": {"executable": [c for c in ("restore", "checkout") if c in allowed],
                     "plan_only": [c for c in ("revert", "cherry-pick", "resolve") if c in allowed],
                     "reads": [c for c in READS if c in allowed]},
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
    import jobs
    head = await asyncio.to_thread(_resolve, "HEAD")
    job = {"job_id": jobs.job_id_for("resolve", object_id, resolution, shas[0], shas[1], head), "command": "resolve",
           "object_id": object_id, "resolution": resolution, "head": head,
           "applying": names[0] if resolution == "ours" else names[1], "ours": shas[0], "theirs": shas[1],
           "ops": ops, "skipped": [], "estimated_s": SECONDS_PER_OP * len(ops),
           "plan": None, "plan_unavailable": "a merge resolution is committed and applied by roomctl (room resolve)",
           "executor": "not_connected",
           "detail": "validated and planned only: no robot moved, nothing was merged, room.git was not touched"}
    stored, replayed = await asyncio.to_thread(jobs.open_job, job)
    if replayed:
        return JSONResponse({**jobs.view(stored), "replayed": True}, status_code=200)
    import events                                                           # the SSE hub (events.py)
    events.hub.publish("job", {"id": stored["job_id"], "state": stored["state"], "progress": 0, "command": "resolve",
                               "object_id": object_id, "applying": stored["applying"], "ops": len(ops),
                               "executor": "not_connected"})
    return {**jobs.view(stored), "replayed": False}


# ── a moment instead of a name ────────────────────────────────────────────────────────────────
@router.get("/api/when")
async def when(phrase: str = Query(..., min_length=1, max_length=64)):
    """"before dinner" -> the commit the room was at then. A read: it plans nothing and moves nothing.
    `source` says whether room-events (ES|QL) or git's own history answered — the fallback is never hidden."""
    if not _valid_ref(phrase):
        return _error("bad_request", "phrase is a moment in words, like 'before dinner' or '2 hours ago'", 422)
    found = await asyncio.to_thread(resolve_state, phrase)
    if found["how"] != "time":
        detail = (f"{phrase!r} is a state name, not a time" if found["how"] else
                  f"{phrase!r} cannot be placed in time — try 'before dinner', 'yesterday lunch' or '2 hours ago'")
        return _error("not_a_time", detail, 422)
    if not found["sha"]:
        return _error("not_found", found.get("detail") or f"the room has no commit before {found['when']}", 404)
    return {"phrase": phrase, "sha": found["sha"], "when": found["when"], "at": found.get("at"),
            "message": found.get("message"), "source": found.get("source"),
            "restore": {"command": "restore", "args": {"ref": found["sha"]}}}


@router.get("/api/why/{ref:path}")
async def why(ref: str, seconds: float = Query(2.0, ge=0.1, le=30.0)):
    """"why was this diff wrong": the commit (room-events) -> the capture that made it (room-clouds: the
    quality gate's own numbers) -> the robot's telemetry in the seconds before the shutter (robot-telemetry,
    ES|QL) -> the Sentry trace of that same moment. roomctl.why owns the join and the verdict, so this can
    never disagree with the rule that accepted or rejected the capture. A read: nothing moves."""
    if not _valid_ref(ref):
        return _error("bad_request", "ref must be a commit sha, a branch name, HEAD or a state name", 422)
    found = await asyncio.to_thread(resolve_state, ref)
    sha = found["sha"]
    if found["how"] == "ambiguous":
        return _error("ambiguous_state", f"{ref!r} could be {' or '.join(found['candidates'])}, and they are "
                                         "different commits — name one of them exactly", 409)
    if not sha:
        return _error("not_found", f"no commit, state or moment {ref!r} in the room's history", 404)
    es = _es_client()
    if es is None:
        return _error("search_unavailable", "this answer lives in Elasticsearch (the capture, its gate and the "
                                            "telemetry): this server has no cluster to ask", 503, retryable=True)
    try:
        from roomctl import why as _why
        out = await asyncio.to_thread(_why.explain, _roomctl_repo(), sha, es, seconds)
    except ImportError:
        return _error("not_available", "roomctl is not importable on this server, so the join cannot be run", 503)
    except room.RoomError as e:
        return _error("room_unavailable", str(e), 503, retryable=True)
    except Exception as e:  # noqa: BLE001 — a missing index or a parked cluster is an outage, never a verdict
        return _error("search_unavailable", f"{type(e).__name__}: {e}", 503, retryable=True)
    return _framed({**out, "ref": ref, "resolved": {"how": found["how"], "ref": found.get("ref"),
                                                    **({"when": found["when"]} if found.get("when") else {})},
                    "capture_url": f"/capture/{out['capture_id']}" if out.get("capture_id") else None,
                    "replay_url": f"/replay/{out['capture_id']}" if out.get("capture_id") else None})
