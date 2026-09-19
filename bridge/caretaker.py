"""bridge/caretaker.py — OUR logic: a validated Intent → an answer, a proposal, or finished jobs
(plan/roommate/03-interfaces.md §12). An LLM never produces a job; only this module does.

    find / point   resolve the object → its last pose → ONE `point` job → the housebot edge
    tidy           the room as last scanned vs where things belong (HEAD) → one `move` job per moved
                   object, run in order, stopping at the first that fails
    move           a PROPOSAL: where a thing belongs changes only through approval (a PR), never from text
    status, blame  read-only
    restore_time   not built: it needs a time resolved to a commit, which is not guessed here

Resolving "my keys" to one object: Elastic's hybrid search (elastic/queries.py through web/es_shared.py)
first. When Elastic is parked or unconfigured, the objects in room.git, matched by class or id, with the
answer labelled `how: "room"`. Two matches there is `ambiguous_object`, with both named: never a guess.

Job ids are deterministic per request (jobs.job_id_for over the request_id), so asking again with the
same request_id is the same job, and the edge's in-memory cache and our ledger agree.
Runs inside the web process (web/ is on sys.path there).
"""
from __future__ import annotations

import asyncio
import os
import re

from bridge.contract import FRAME, ContractError

UNITS = {"position": "m", "yaw": "deg", "duration": "s"}
# Below this rerank margin (1st - 2nd) the top two objects are too close to call: ask, never guess.
# Measured on the live room: clear phrases resolve at 0.22-0.52, "something to drink from" at 0.071.
MIN_MARGIN = float(os.getenv("RESOLVE_MIN_MARGIN", "0.05"))


def _singular(w: str) -> str:
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("es") and w[:-2].endswith(("s", "x", "ch", "sh")):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


# ── resolving a description to one object ─────────────────────────────────────────────

def _room_objects() -> dict[str, str]:
    """{object_id: class} for everything in the room as committed (HEAD) or as last scanned (the working
    tree), read with web's lenient readers: a half-written record is skipped, never fatal."""
    import graph_api
    import room
    out: dict[str, str] = {}
    head = graph_api._resolve("HEAD")                                          # noqa: SLF001
    if head:
        for oid, o in graph_api._state(head)["objects"].items():               # noqa: SLF001
            out[oid] = o.get("class")
    root = room.room_path()
    for f in sorted(root.glob("zones/*/*.yaml")):
        oid, _ = room._object_of(str(f.relative_to(root)))                     # noqa: SLF001
        if oid:
            try:
                out[oid] = room._record(f.read_text()).get("class") or out.get(oid)   # noqa: SLF001
            except Exception:  # noqa: BLE001
                continue
    return out


def _from_room(query: str) -> dict:
    words = {_singular(w) for w in re.findall(r"[a-z0-9]+", query.lower())}
    objs = _room_objects()
    hits = sorted(oid for oid, cls in objs.items()
                  if _singular((cls or "").lower()) in words or oid.split("_")[0] in words or oid == query)
    if not hits:
        raise ContractError("not_found", f"nothing in the room matches {query!r}", 404,
                            {"known_objects": sorted(objs), "how": "room"})
    if len(hits) > 1:
        raise ContractError("ambiguous_object", f"{query!r} could be {' or '.join(hits)}: say which one", 409,
                            {"candidates": hits, "how": "room"})
    return {"object_id": hits[0], "class": objs[hits[0]], "how": "room", "score": None, "candidates": hits}


async def resolve(intent: dict) -> dict:
    """{object_id, class, how: id|elasticsearch|room, score, candidates}."""
    if intent.get("object_id"):
        return {"object_id": intent["object_id"], "class": None, "how": "id", "score": None,
                "candidates": [intent["object_id"]]}
    query = intent["object_query"]
    try:
        import es_shared
        q = es_shared.queries()
    except Exception:  # noqa: BLE001 — parked / unconfigured / not importable: the room still knows
        return await asyncio.to_thread(_from_room, query)
    if hasattr(q, "resolve_object"):          # the resolver's Elastic half (elastic/queries.py)
        r = await asyncio.to_thread(lambda: q.resolve_object(query, k=5))
        matches, margin = r.get("matches") or [], r.get("margin")
        if not matches:
            raise ContractError("not_found", f"Elasticsearch has nothing matching {query!r}", 404,
                                {"how": "elasticsearch"})
        top = matches[0]
        # `margin` (1st - 2nd) is the tie-break signal. Below MIN_MARGIN the top two are too close to
        # call, so we ask instead of guessing — the same rule as two spellings of a state name.
        # Rerank scores are model-specific: this compares them only with each other.
        if margin is not None and margin < MIN_MARGIN and len(matches) > 1:
            names = [m["object_id"] for m in matches[:3]]
            raise ContractError("ambiguous_object", f"{query!r} could be {' or '.join(names[:2])} "
                                f"(rerank margin {margin:.3f}): say which one", 409,
                                {"candidates": names, "margin": round(margin, 3), "how": "elasticsearch"})
        return {"object_id": top["object_id"], "class": top.get("class"), "zone": top.get("zone"),
                "how": "elasticsearch", "score": round(float(top.get("score") or 0), 3),
                "margin": None if margin is None else round(margin, 3),
                "candidates": [m["object_id"] for m in matches]}
    hits = await asyncio.to_thread(lambda: q.search_objects(query, size=5))
    if not hits:
        raise ContractError("not_found", f"Elasticsearch has nothing matching {query!r}", 404, {"how": "elasticsearch"})
    top = hits[0]
    return {"object_id": top["object_id"], "class": top.get("class"), "how": "elasticsearch",
            "score": round(float(top.get("score") or 0), 3), "candidates": [h["object_id"] for h in hits]}


# ── the jobs ────────────────────────────────────────────────────────────────────────

async def point_job(object_id: str, request_id: str) -> dict:
    import jobs
    import object_api
    import store
    try:
        return await object_api.build_point(object_id, jobs.job_id_for("point", object_id, request_id))
    except store.NotFound:
        raise ContractError("not_found", f"no object {object_id} in the room's history", 404) from None
    except LookupError as e:
        raise ContractError("unreachable_pose", str(e), 409) from None


def tidy_jobs(zone: str | None, request_id: str) -> dict:
    """The room as last scanned (working tree) → where things belong (HEAD), through roomctl's own
    planner. One `move` job per `move` op, in its order; everything else is listed, not sent."""
    import jobs
    import room
    from roomctl.executor import plan
    from roomctl.repo import Repo, load_room
    from roomctl.state import read_tree
    repo = Repo(room.room_path())
    layout = load_room(repo.path)
    zones = sorted((layout.get("zones") or {}).keys())
    if zone is not None and zone not in zones:
        raise ContractError("unknown_zone", f"the room has no zone {zone!r}", 404, {"known_zones": zones})
    head = repo.head()
    current, target = read_tree(repo.path), repo.records("HEAD")
    p = plan(current, target, layout).to_dict("HEAD", head)
    out, skipped = [], [{"object_id": u["object_id"], "why": u["reason"]} for u in p["unapplied"]]
    for op in p["ops"]:
        if zone is not None and zone not in (op["from"]["zone"], op["to"]["zone"]):
            continue
        if op["kind"] != "move":
            skipped.append({"object_id": op["object_id"], "why": f"a `{op['kind']}` op: the edge runs one `moved` "
                                                                  "op per job, so roomctl does this one"})
            continue
        rec = target.get(op["object_id"]) or current.get(op["object_id"])
        out.append({"job_id": jobs.job_id_for("tidy", request_id, str(op["seq"]), op["object_id"]),
                    "command": "move", "target": head,
                    "ops": [{"op": "moved", "object_id": op["object_id"], "class": getattr(rec, "cls", None),
                             "zone": op["to"]["zone"], "from": op["from"]["pose"], "to": op["to"]["pose"]}]})
    return {"head": head, "zone": zone, "jobs": out, "skipped": skipped, "frame": FRAME, "units": UNITS}


# ── the dispatcher: what the edge was sent, or why nothing was ──────────────────────────

def _dispatch_summary(d: dict) -> dict:
    return {k: d.get(k) for k in ("dispatched", "why", "state", "replayed", "edge", "job_id") if k in d}


async def act(intent: dict) -> dict:
    """A validated Intent → an action for the panel: {kind, as, result, frame}."""
    kind = intent["intent"]
    if kind in ("find", "point"):
        found = await resolve(intent)
        job = await point_job(found["object_id"], intent["request_id"])
        import housebot
        d = await housebot.submit(job)
        return {"kind": "job", "as": "point", "ref": found["object_id"], "frame": FRAME,
                "result": {"resolved": found, "job": job, "dispatch": _dispatch_summary(d),
                           "executor": "housebot-edge" if d.get("dispatched") else "not_connected"}}
    if kind == "tidy":
        planned = await asyncio.to_thread(tidy_jobs, intent.get("zone"), intent["request_id"])
        import housebot
        d = await housebot.submit_sequence(planned["jobs"]) if planned["jobs"] else \
            {"dispatched": False, "why": "nothing to tidy: every object is where it belongs"}
        return {"kind": "jobs", "as": "tidy", "ref": intent.get("zone"), "frame": FRAME,
                "result": {**planned, "dispatch": _dispatch_summary(d),
                           "executor": "housebot-edge" if d.get("dispatched") else "not_connected"}}
    if kind == "move":
        found = await resolve(intent)
        return {"kind": "proposal", "as": "move", "ref": found["object_id"], "frame": FRAME,
                "result": {"resolved": found, "to_zone": intent["zone"], "approval_required": True, "job": None,
                           "detail": "moving where something BELONGS changes the agreed room: it becomes a pull "
                                     "request on room.git, and only an approved merge makes a move job"}}
    if kind == "status":
        import room
        snap = await asyncio.to_thread(room.snapshot)
        return {"kind": "read", "as": "status", "ref": None, "frame": FRAME,
                "result": {**room.summary(snap), "changed": snap.get("changes") or []}}
    if kind == "blame":
        found = await resolve(intent)
        try:                                   # web's /api/blame reader: the commit that last moved it, from git
            import roommate_api
            b = await asyncio.to_thread(roommate_api.blame_sync, found["object_id"])
        except ImportError:
            b = None
        if b is None:
            raise ContractError("not_found", f"nothing in the room's history moved {found['object_id']}", 404)
        return {"kind": "read", "as": "blame", "ref": found["object_id"], "frame": FRAME,
                "result": {"resolved": found, **b}}
    if kind == "restore_time":
        # "the way it was before dinner": graph_api.resolve_state places the phrase (roomctl.when) and finds
        # the last commit strictly before it, then the SAME planner every restore uses. A phrase it cannot
        # place, or one with no commit before it, comes back as not_found — never as a guessed moment.
        from bridge.agent_api import _plan
        planned = await _plan("restore", intent["when"])
        return {"kind": "plan", "as": "restore", "ref": intent["when"], "frame": FRAME, "result": planned}
    if kind == "why":
        import graph_api
        ref = intent.get("ref") or "HEAD"
        out = await graph_api.why(ref, 2.0)             # roomctl.why's join: gate, telemetry, Sentry trace
        if hasattr(out, "body"):                        # an error response: give it back as ours
            import json as _json
            e = _json.loads(bytes(out.body) or b"{}")
            raise ContractError(e.get("error", "not_found"), e.get("detail", "no answer"), out.status_code)
        return {"kind": "read", "as": "why", "ref": ref, "frame": FRAME, "result": out}
    raise ContractError("not_built", f"'{kind}' has no executor on this server", 501)
