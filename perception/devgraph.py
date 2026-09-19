"""Dev backend for the room graph as a control surface (docs/24 Part B) -- web/landing/dev-graph.html.

    python3 perception/devgraph.py            # http://127.0.0.1:8125 ; the page is on :8124

READS come from the real room.git (roomctl's reader: object state, diffs, and revert / cherry-pick /
merge previews computed per object from git reads) and the real indices (room-events, room-clouds,
room-voxels over REST). web's /api/graph is used when :8000 answers. This process adds:

  * the WRITE path, and only on explicit confirmation: preview -> STAGE (a second, separate
    request: `git revert|cherry-pick|merge --no-commit`, or `git restore --source` for restore)
    -> COMMIT or ABORT. Never auto-applied. Staging refuses unless room.git is clean and HEAD is
    still the commit the preview was computed from.
  * base poses for every op (perception.costmap on HEAD's live room-voxels, docs/24 A2).
  * the agent panel relay to docs/31's POST /api/agent/command (the cloud session's endpoint).

Anything not wired answers `pending` with the reason -- never a stand-in: the robot (no executor
connected), publishing a graph-made commit (no scan-less publish path), the agent endpoint (until
docs/31 is live).
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(HERE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi import Body, FastAPI, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from roomctl.repo import Repo, default_path  # noqa: E402

WEB = os.getenv("GITSPACE_WEB", "http://127.0.0.1:8000")
PORT = int(os.getenv("DEVGRAPH_PORT", "8125"))
SHA = re.compile(r"^[0-9a-f]{7,40}$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$")
STAGEABLE = ("restore", "revert", "cherry-pick", "merge")
GRAPH_NATIVE = re.compile(r"^\s*(?:room\s+|git\s+)?(revert|merge|cherry-?pick|branch|checkout|switch|reset)\b", re.I)

app = FastAPI(title="gitspace dev graph")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8124", "http://localhost:8124"],
                   allow_methods=["GET", "POST"], allow_headers=["*"])


def room() -> Repo:
    return Repo(Path(os.getenv("ROOM_GIT_PATH_DEVGRAPH") or default_path()))


def pending(what: str, why: str, **extra) -> dict:
    return {"status": "pending", "what": what, "why": why, **extra}


def err(code: str, message: str, status: int = 409, **extra) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}, **extra}, status_code=status)


# ── reads: the web API, relayed ──────────────────────────────────────────────

def web(path: str, method: str = "GET", body: dict | None = None, timeout: float = 20):
    """(status, json) from web/'s API, or (None, reason) when it isn't answering."""
    req = urllib.request.Request(WEB + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {"error": {"code": "http", "message": str(e)}}
    except (OSError, ValueError) as e:
        return None, f"{WEB} not answering: {type(e).__name__}"


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return room().git(*args, check=check)


def resolve(ref: str) -> str | None:
    r = git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
    return r.stdout.strip() or None


def head() -> str:
    return git("rev-parse", "HEAD").stdout.strip()


@app.get("/dev/status")
def status():
    r = room()
    st = r.status()
    bridge = web("/api/agent/bridge", timeout=4)
    return {
        "room": str(r.path), "head": head(), "branch": r.branch(), "clean": st.clean,
        "in_progress": in_progress(), "staged": staged_state(),
        "web": {"url": WEB, "live": web("/api/status", timeout=4)[0] == 200},
        "agent_bridge": bridge[1] if bridge[0] == 200 else pending(
            "agent panel", "docs/31 POST /api/agent/command is not live yet (GET /api/agent/bridge -> "
            f"{bridge[0] or 'no answer'})"),
        "robot": pending("robot", "no executor connected: plans stop at an op list (docs/31 rule 3)"),
    }


# ── Elasticsearch, read-only, over plain REST (no client package needed) ──────

class RestES:
    """The two reads this page needs from the live indices: search and count."""
    def __init__(self):
        from dotenv import dotenv_values
        import es_sink
        env = {**dotenv_values(ROOT / ".env"), **os.environ}
        self.url, key = es_sink._usable(env.get("ELASTIC_URL")), es_sink._usable(env.get("ELASTIC_API_KEY"))
        if not (self.url and key):
            raise RuntimeError("ELASTIC_URL / ELASTIC_API_KEY parked or unset")
        self.auth = f"ApiKey {key}"

    def search(self, index, size=100, query=None, sort=None, search_after=None, _source=None):
        body = {"size": size, "query": query or {"match_all": {}}}
        body.update({k: v for k, v in (("sort", sort), ("search_after", search_after), ("_source", _source)) if v})
        req = urllib.request.Request(f"{self.url.rstrip('/')}/{index}/_search", data=json.dumps(body).encode(),
                                     method="POST", headers={"Authorization": self.auth, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())


def _enrich(shas: list[str]) -> tuple[dict, dict | None]:
    """Per commit, from room-events (what it changed, which capture built it) and room-clouds
    (did that capture pass the gate; its Sentry trace). Missing -> absent, never guessed."""
    try:
        es = RestES()
        ev = es.search("room-events", size=len(shas) * 2, query={"bool": {"filter": [
            {"terms": {"commit_sha": shas}}, {"term": {"event_type": "commit"}}]}})["hits"]["hits"]
        by_sha = {h["_source"]["commit_sha"]: h["_source"] for h in ev}
        caps = sorted({e["capture_id"] for e in by_sha.values() if e.get("capture_id")})
        cl = es.search("room-clouds", size=max(1, len(caps)), query={"terms": {"capture_id": caps}})["hits"]["hits"] if caps else []
        by_cap = {h["_source"]["capture_id"]: h["_source"] for h in cl}
    except Exception as e:  # noqa: BLE001 - the graph still renders from git alone
        return {}, pending("enrichment", f"Elasticsearch: {type(e).__name__}: {e}")
    out = {}
    for sha, e in by_sha.items():
        c = by_cap.get(e.get("capture_id"), {})
        out[sha] = {"changed": {k: e.get(k) or [] for k in ("objects_added", "objects_moved", "objects_removed")},
                    "capture_id": e.get("capture_id"), "quality_ok": c.get("quality_ok"),
                    "sentry_trace_id": c.get("sentry_trace_id") or e.get("sentry_trace_id")}
    return out, None


@app.get("/dev/graph")
def graph(limit: int = Query(60, ge=1, le=300)):
    code, body = web(f"/api/graph?limit={limit}", timeout=8)
    if code == 200:
        return {**body, "served_by": "web /api/graph (git log + Elasticsearch)"}
    lines = git("log", "--all", "--topo-order", f"-{limit}", "--format=%H|%P|%D|%ct|%s").stdout.splitlines()
    nodes = []
    for line in lines:
        sha, parents, refs, ts, subject = line.split("|", 4)
        refs = [r.strip().removeprefix("HEAD -> ") for r in refs.split(",") if r.strip() and r.strip() != "HEAD"]
        nodes.append({"sha": sha, "parents": parents.split(), "ts": int(ts), "subject": subject,
                      "refs": [{"name": r, "kind": "tag" if r.startswith("tag: ") else "branch"} for r in refs],
                      "changed": None, "quality_ok": None, "capture_id": None, "sentry_trace_id": None})
    extra, why = _enrich([n["sha"] for n in nodes])
    for n in nodes:
        n.update(extra.get(n["sha"], {}))
    return {"nodes": nodes, "head": head(), "branch": room().branch(),
            "served_by": "room.git (git log) + Elasticsearch (room-events, room-clouds) — web :8000 not answering",
            "source": None if why else "elasticsearch", "enrichment": why}


# ── object state and object-level ops, straight from room.git ────────────────

def _obj(rec) -> dict:
    return {"object_id": rec.id, "class": rec.cls, "zone": rec.zone, "pose": vars(rec.pose),
            "extents": vars(rec.extents), "color": rec.color}


def _state(ref: str) -> tuple[dict, dict]:
    """(objects by id, room.yaml zones) at a commit -- roomctl's reader, no web needed."""
    import voxelize
    return {o: _obj(r) for o, r in room().records(ref).items()}, voxelize.load_room(room().path).get("zones") or {}


def _op(oid: str, cur: dict | None, tgt: dict | None) -> dict | None:
    """docs/31's op shape: kind move|add|remove, from/to {zone, pose}, frame."""
    if cur == tgt:
        return None
    side = lambda o: o and {"zone": o["zone"], "pose": o["pose"]}          # noqa: E731
    if cur and tgt and cur["zone"] == tgt["zone"] and cur["pose"] == tgt["pose"]:
        return None
    kind = "move" if cur and tgt else "add" if tgt else "remove"
    o = {"object_id": oid, "class": (tgt or cur).get("class"), "kind": kind, "from": side(cur), "to": side(tgt),
         "frame": "world_z_up"}
    if kind == "move":
        a, b = cur["pose"], tgt["pose"]
        o["delta_m"] = round(math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"])), 3)
    return o


def _ops(now: dict, tgt: dict) -> list[dict]:
    return [o for oid in sorted(set(now) | set(tgt)) if (o := _op(oid, now.get(oid), tgt.get(oid)))]


def _apply_commit(sha: str, onto: dict, invert: bool) -> tuple[list[dict], list[dict]]:
    """A commit's per-object change (parent -> sha), inverted for revert, applied onto a state
    wherever that state still has the version the change starts from (else: a conflict)."""
    parent = resolve(f"{sha}~1")
    if parent is None:
        return [], [{"why": "a root commit: nothing before it to revert to or pick from"}]
    before, _ = _state(parent)
    after, _ = _state(sha)
    src, dst = (after, before) if invert else (before, after)
    ops, conflicts = [], []
    for oid in sorted(set(before) | set(after)):
        if src.get(oid) == dst.get(oid):
            continue
        if onto.get(oid) != src.get(oid):
            conflicts.append({"object_id": oid, "why": "changed again since that commit" if invert
                              else "HEAD's version differs from the one that commit changed"})
        elif (o := _op(oid, onto.get(oid), dst.get(oid))) is not None:
            ops.append(o)
    return ops, conflicts


def _merge(base_sha: str, ours: dict, theirs: dict) -> tuple[list[dict], list[dict]]:
    """Three-way, per object: take theirs where only they changed it; conflict where both did."""
    base, _ = _state(base_sha)
    ops, conflicts = [], []
    for oid in sorted(set(base) | set(ours) | set(theirs)):
        b, o, t = base.get(oid), ours.get(oid), theirs.get(oid)
        if t == b or o == t:
            continue
        if o == b:
            if (op := _op(oid, o, t)) is not None:
                ops.append(op)
        else:
            conflicts.append({"object_id": oid, "ours": o and o["pose"], "theirs": t and t["pose"],
                              "why": "both sides changed it"})
    return ops, conflicts


@app.get("/dev/diff")
def diff(a: str, b: str):
    ta, tb = resolve(a), resolve(b)
    if not (ta and tb):
        return err("not_found", "both sides must be commits in room.git", 404)
    ops = _ops(_state(ta)[0], _state(tb)[0])
    return {"a": ta, "b": tb, "ops": ops, "summary": {k: sum(o["kind"] == k for o in ops) for k in ("move", "add", "remove")}}


# ── preview: what WOULD happen, computed from git reads only ─────────────────

@app.get("/dev/preview")
def preview(op: str = Query(..., pattern="^(restore|revert|cherry-pick|merge|switch)$"), ref: str = Query(...)):
    """Nothing is written. The page shows this, then asks for a SECOND, explicit confirmation."""
    if not (SHA.match(ref) or BRANCH.match(ref)):
        return err("bad_request", "ref must be a sha or a branch name", 422)
    if op == "switch" and git("show-ref", "--verify", "-q", f"refs/heads/{ref}", check=False).returncode != 0:
        return err("bad_request", "switch takes an existing branch name (a sha would detach HEAD)", 422)
    target_sha = resolve(ref)
    if target_sha is None:
        return err("not_found", f"{ref} is not a commit in room.git", 404)
    base = head()
    now, zones = _state(base)
    conflicts: list[dict] = []
    if op in ("restore", "switch"):
        ops = _ops(now, _state(target_sha)[0])
    elif op in ("revert", "cherry-pick"):
        ops, conflicts = _apply_commit(target_sha, now, invert=op == "revert")
    else:
        mb = git("merge-base", base, target_sha, check=False).stdout.strip()
        if not mb:
            return err("no_merge_base", "no common history to merge from", 409)
        ops, conflicts = _merge(mb, now, _state(target_sha)[0])
    git_cmd = {"restore": f"git restore --source={target_sha[:10]} --staged --worktree -- zones  &&  git commit",
               "revert": f"git revert --no-commit {target_sha[:10]}  &&  git commit",
               "cherry-pick": f"git cherry-pick --no-commit {target_sha[:10]}  &&  git commit",
               "merge": f"git merge --no-commit --no-ff {ref}  &&  git commit",
               "switch": f"git switch {ref}   (moves HEAD; nothing to commit)"}[op]
    return {"op": op, "ref": ref, "base_sha": base, "target_sha": target_sha, "git": git_cmd,
            "ops": _route(ops), "conflicts": conflicts, "zones": zones, "frame": "world_z_up",
            "costmap": _costmap_summary(),
            "current": list(now.values()), "stageable": op in STAGEABLE and not conflicts and bool(ops),
            "applied": False, "robot": pending("robot", "no executor connected: this is the op list it would run")}


def _route(ops: list[dict]) -> list[dict]:
    """base_pose {pick, place}: where the robot would stand (docs/24 A2) on HEAD's live voxels."""
    try:
        cm, arm, robot, home = _costmap()
    except Exception as e:  # noqa: BLE001 - the plan still stands without stances
        return [{**o, "base_pose": pending("base pose", f"no costmap: {type(e).__name__}: {e}")} for o in ops]
    import costmap as cmod
    out, here = [], (home.x, home.y, math.radians(home.yaw))
    for o in ops:
        bp = {}
        for leg, side in (("pick", "from"), ("place", "to")):
            s = o.get(side)
            if not s:
                continue
            p = s["pose"]
            pose, why = cmod.solve_base_pose_why((p["x"], p["y"], p["z"]), cm, arm, here, robot.eye_h)
            bp[leg] = ({"x": round(pose[0], 3), "y": round(pose[1], 3), "yaw_deg": round(math.degrees(pose[2]), 1)}
                       if pose else {"unreachable": {k: v for k, v in why.items() if v}})
            if pose:
                here = pose
        out.append({**o, "base_pose": bp})
    return out


_COSTMAP: dict[str, tuple] = {}


def _costmap_summary() -> dict:
    """What the stances were checked against -- so an empty costmap can't pass for a clear room."""
    try:
        cm = _costmap()[0]
    except Exception as e:  # noqa: BLE001
        return pending("costmap", f"{type(e).__name__}: {e}")
    n = int(cm.obstacle.sum())
    out = {"head": head(), "voxels": int(len(cm.grid.ijk)), "obstacle_cells": n}
    if n == 0:
        out["warning"] = ("0 obstacle cells: HEAD's indexed voxels hold no floor, legs or walls, so these stances "
                          "are NOT collision-checked (scene_gen adds pedestals from its next generation)")
    return out


def _costmap():
    """HEAD's occupancy from the live room-voxels (VoxelGrid.from_docs), cached per HEAD."""
    sha = head()
    if sha not in _COSTMAP:
        import costmap as cmod
        import voxelize
        from roomctl.executor import ARM, HOME, ROBOT
        docs = voxelize.voxels_for_commit(RestES(), sha)
        if not docs:
            raise RuntimeError(f"room-voxels has no documents for HEAD {sha[:10]}")
        _COSTMAP.clear()
        _COSTMAP[sha] = (cmod.Costmap.from_grid(voxelize.VoxelGrid.from_docs(docs)), ARM, ROBOT, HOME)
    return _COSTMAP[sha]


# ── the write path: stage, then commit or abort ─────────────────────────────

def _state_file() -> Path:
    return room().path / ".git" / "gitspace" / "devgraph-staged.json"


def staged_state() -> dict | None:
    p = _state_file()
    return json.loads(p.read_text()) if p.is_file() else None


def in_progress() -> str | None:
    g = room().path / ".git"
    for name, marker in (("merge", "MERGE_HEAD"), ("revert", "REVERT_HEAD"), ("cherry-pick", "CHERRY_PICK_HEAD")):
        if (g / marker).exists():
            return name
    return None


@app.post("/dev/stage")
def stage(req: dict = Body(...)):
    """The FIRST confirmation. Writes the index and working tree of room.git; commits nothing."""
    op, ref, base = req.get("op"), req.get("ref"), req.get("base_sha")
    if op not in STAGEABLE:
        return err("bad_request", f"op must be one of {STAGEABLE}", 422)
    if staged_state() or in_progress():
        return err("already_staged", "something is already staged: commit it or abort it first")
    if not room().status().clean:
        return err("dirty", "room.git has uncommitted changes (a scan?): staging on top would mix them in")
    if head() != base:
        return err("moved", f"HEAD moved since the preview ({str(base)[:10]} -> {head()[:10]}): preview again")
    target = resolve(ref or "")
    if target is None:
        return err("not_found", f"{ref} is not a commit", 404)
    cmd = {"restore": ["restore", f"--source={target}", "--staged", "--worktree", "--", "zones"],
           "revert": ["revert", "--no-commit", target],
           "cherry-pick": ["cherry-pick", "--no-commit", target],
           "merge": ["merge", "--no-commit", "--no-ff", target]}[op]
    r = git(*cmd, check=False)
    if r.returncode != 0:
        _abort_git()
        return err("git_refused", (r.stderr or r.stdout).strip()[-500:])
    rec = {"op": op, "ref": ref, "target_sha": target, "base_sha": base,
           "staged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    _state_file().parent.mkdir(parents=True, exist_ok=True)
    _state_file().write_text(json.dumps(rec))
    return {**rec, "staged": staged_diff(),
            "next": "COMMIT (records it) or ABORT (puts room.git back exactly as it was)"}


def staged_diff() -> list[dict]:
    """What the index now holds vs HEAD, in docs/31's op shape."""
    r = room()
    return _ops({o: _obj(x) for o, x in r.records("HEAD").items()}, {o: _obj(x) for o, x in r.records(":").items()})


@app.post("/dev/commit")
def commit(req: dict = Body(default={})):
    """The SECOND confirmation: record what was staged, as the robot."""
    st = staged_state()
    if not st:
        return err("nothing_staged", "stage first: a commit here only ever records a staged preview")
    if head() != st["base_sha"]:
        return err("moved", "HEAD moved while staged: abort and preview again")
    default = {"restore": f"restore the room to {st['target_sha'][:10]} ({st['ref']})",
               "revert": f"Revert {st['target_sha'][:10]}",
               "cherry-pick": f"cherry-pick {st['target_sha'][:10]}",
               "merge": f"Merge {st['ref']}"}[st["op"]]
    c = room().commit((req.get("message") or default).strip())
    _state_file().unlink(missing_ok=True)
    if c is None:
        return err("empty", "nothing was staged after all (the room already matched)", 409)
    return {"committed": c.sha, "message": c.message, "op": st["op"],
            "publish": pending("Elasticsearch", "a commit made from the graph has no capture; roomctl's post-commit "
                               "publish would attach the LAST scan's capture metadata. Needs a scan-less publish path."),
            "robot": pending("robot", "the room is recorded, nothing moved yet. Next step: `room restore` (no ref) "
                             "-- the robot makes the room match this commit (ANDREW-HANDOFF.md §1)")}


def _abort_git() -> None:
    kind = in_progress()
    if kind:
        git(kind, "--abort", check=False)
    git("reset", "-q", "--hard", "HEAD")            # safe: staging refused a dirty tree, so this is only ours


@app.post("/dev/abort")
def abort():
    if not staged_state() and not in_progress():
        return err("nothing_staged", "nothing to abort")
    _abort_git()
    _state_file().unlink(missing_ok=True)
    return {"aborted": True, "head": head(), "clean": room().status().clean}


@app.post("/dev/switch")
def switch(req: dict = Body(...)):
    """`git switch <branch>`: its own single explicit confirmation -- it moves HEAD, commits nothing."""
    ref, base = req.get("ref"), req.get("base_sha")
    if not (isinstance(ref, str) and BRANCH.match(ref)
            and git("show-ref", "--verify", "-q", f"refs/heads/{ref}", check=False).returncode == 0):
        return err("bad_request", "switch takes an existing branch name", 422)
    if staged_state() or in_progress() or not room().status().clean:
        return err("dirty", "room.git has staged or uncommitted changes")
    if head() != base:
        return err("moved", "HEAD moved since the preview: preview again")
    r = git("switch", ref, check=False)
    if r.returncode != 0:
        return err("git_refused", (r.stderr or r.stdout).strip()[-500:])
    return {"switched": ref, "head": head(), "robot": pending("robot", "no executor connected")}


# ── the agent panel: Andrew's envelope, relayed to docs/31 ──────────────────

def envelope(text: str, request_id: str | None = None) -> dict:
    """awzheng/gitirl docs/PROTOCOL.md, verbatim: only type user_command, a non-empty request_id."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("user_command payload requires text")
    return {"type": "user_command", "request_id": request_id or uuid.uuid4().hex,
            "timestamp": datetime.now(timezone.utc).isoformat(), "payload": {"text": text.strip()}}


def route(text: str) -> tuple[str, str]:
    """Which path SHOULD serve this text (docs/31 section 1). The endpoint decides for real;
    this is what the page shows before sending, and what it falls back to while it is down."""
    m = GRAPH_NATIVE.match(text)
    if m:
        return "graph", f"'{m.group(1).lower()}' is graph-native: never sent to Andrew's middleware"
    return "middleware", "not a graph verb: Andrew's parser deciphers it (add/commit/status/diff/restore/log)"


@app.post("/dev/agent")
def agent(req: dict = Body(...)):
    try:
        env = envelope(req.get("text", ""), req.get("request_id"))
    except ValueError as e:
        return err("bad_request", str(e), 422)
    path, why = route(env["payload"]["text"])
    code, body = web("/api/agent/command", "POST", env, timeout=30)
    if code is not None and isinstance(body, dict) and "request_id" in body:   # docs/31: errors keep the body
        return {"sent": env, "response": body, "served_by": body.get("served_by"), "path": body.get("path"),
                "http_status": code}   # pre-"ok" builds signal failure by status alone (docs/31 §3)
    return {"sent": env, "path": path, "why": why, "served_by": None,
            "response": pending("agent panel", f"docs/31 POST /api/agent/command is not live yet "
                                f"({code or 'no answer'}): nothing was deciphered, nothing planned")}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
