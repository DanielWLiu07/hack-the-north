"""bridge/agent_api.py — the agent panel's endpoint. Contract: docs/31-agent-panel-contract.md.

    POST /api/agent/command   Andrew's envelope in; which path served it, the intent, what our side
                              did with it, his messages, and a node-per-hop trace out
    GET  /api/agent/bridge    which decipherer is armed (ws / jsonl / stub) before anything is sent
    WS   /ws/gitirl-agent     where his agent dials in (his GITIRL_WS_URL)

Two paths. Graph-native verbs (revert, merge, cherry-pick, branch, checkout, reset, …) are planned
on our side and NEVER reach his middleware. Everything else goes to his parser; of his six verbs,
`restore X` is planned as `git restore --source=X` + a new commit — never checkout, never revert —
and status/diff/log are read-only answers. Every plan is built from git READS with graph_api's
planner helpers and ends there: base_sha + target_sha + ops, applied=false. Nothing moves from text,
nothing is queued, and this process never writes room.git — the panel's own backend stages and
commits on a second click (docs/31 §3). His command_result (from HIS orchestrator) is returned
under `ignored`, never acted on.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import time
from collections import OrderedDict

from fastapi import APIRouter, Body, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from bridge import caretaker, intents
from bridge.andrew import HUB, JSONL, decipher, will_serve
from bridge.contract import (FRAME, INFRA, ContractError, assert_frame, check_intent, confirmed_object,
                             envelope, for_parser,
                             now_iso, read_request, route)

try:
    import obs
except ImportError:  # pragma: no cover
    obs = None

router = APIRouter()
_DONE: OrderedDict[str, tuple[dict, int]] = OrderedDict()   # request_id -> (body, status): never plan twice
_INFLIGHT: dict[str, asyncio.Future] = {}
GRAPH_PLANNED = ("revert", "checkout", "cherry-pick")        # what graph_api.command can plan
_FORWARDED = ("x-forwarded-for", "forwarded", "x-real-ip", "cf-connecting-ip")


def init(es) -> None:  # server.py's router protocol
    pass


def _span(kind: str, name: str, **kw):
    import contextlib
    if obs is None:
        return contextlib.nullcontext()
    return obs.agent_tool(name, kind=kind, **kw)


def _set(sp, **data) -> None:
    for k, v in data.items():
        if sp is not None:
            sp.set_data(k, v if isinstance(v, (str, int, float, bool)) else json.dumps(v, default=str)[:2000])


SECONDS_PER_OP = 28                                           # docs/16 §2.4, as graph_api uses
KIND = {"moved": "move", "added": "add", "removed": "remove"}


def _op(o: dict) -> dict:
    """graph_api's op -> the panel's op (docs/31 §3): kind, from/to {zone, pose}, frame."""
    here = o.get("zone")
    return {"object_id": o["object_id"], "class": o.get("class"), "kind": KIND[o["op"]],
            "from": {"zone": o.get("from_zone", here), "pose": o["from"]} if o.get("from") else None,
            "to": {"zone": here, "pose": o["to"]} if o.get("to") else None,
            "base_pose": None,     # roomctl routes; if ever set: {"pick": {...}, "place": {...}} (docs/31)
            "delta_m": o.get("delta_m"), "frame": FRAME}


def _known_states() -> list[str]:
    import room
    raw = room._git("for-each-ref", "--format=%(refname:short)", "refs/tags", "refs/heads")      # noqa: SLF001
    return sorted(r for r in raw.splitlines() if r)


def _resolve_state(ref: str) -> tuple[str | None, str]:
    """(sha, the ref that EXISTS) for a named state as people say it: `study`, `study-mode`, `Study mode`
    find tag `study`, and `study` finds a tag named `study-mode`. Only an exact ref short-circuits;
    every alias goes through graph_api.resolve_state [web session], which matches against the real
    tags and branches and never guesses between two spellings on two different commits."""
    import graph_api
    base = ref.strip()
    sha = graph_api._resolve(base)                                                              # noqa: SLF001
    if sha:
        return sha, base
    if hasattr(graph_api, "resolve_state"):
        found = graph_api.resolve_state(base)
        if found.get("sha"):
            return found["sha"], found["ref"]
        if found.get("how") == "time" and found.get("detail"):
            # a placeable MOMENT with no commit before it is not a misspelt state name: say which moment
            raise ContractError("not_found", found["detail"], 404,
                                {"how": "time", "when": found.get("when"), "said": base})
        if found.get("how") == "ambiguous":             # two spellings, two commits: ask, never guess
            names = found.get("candidates") or []
            raise ContractError("ambiguous_state", f"{base!r} could be {' or '.join(names)}, which are "
                                "different commits: say which one", 409,
                                {"candidates": names, "hint": "say one of: " + ", ".join(names)})
        return None, base
    tries = [base.lower()] + [base.lower()[: -len(x)] for x in ("-mode", "_mode", " mode") if base.lower().endswith(x)]
    for name in dict.fromkeys(tries):                   # an older graph_api: the said name, minus "mode"
        sha = graph_api._resolve(name)                                                          # noqa: SLF001
        if sha:
            return sha, name
    return None, base


def _plan_sync(as_: str, ref: str) -> dict:
    """base_sha + target_sha + ops, from git READS only (graph_api's planner helpers).
    restore/checkout: HEAD's room -> ref's room. revert: the INVERSE of that one commit, only for
    objects nothing has touched since (the rest are conflicts). cherry-pick: that commit's own ops."""
    import graph_api
    import room
    try:
        head = graph_api._resolve("HEAD")                                              # noqa: SLF001
        target, ref = _resolve_state(ref)
        if not head or not target:
            known = _known_states()
            raise ContractError("not_found", f"the room has no saved state called {ref!r}", 404,
                                {"known_states": known,
                                 "hint": ("known states: " + ", ".join(known)) if known else
                                         "no named states yet — a state is a git tag or branch in room.git"})
        conflicts: list[dict] = []
        if as_ == "revert" and hasattr(graph_api, "_revert"):
            # web's own single-commit revert (the node graph uses it): the same answer everywhere
            res = graph_api._revert(target, head, "HEAD")                                # noqa: SLF001
            if res is None:
                raise ContractError("bad_request", "a merge commit cannot be reverted here", 422)
            raw = [o for o in res["ops"] if o.get("status") == "applies"]
            conflicts = [{"object_id": o["object_id"], "why": o.get("reason") or o.get("status")}
                         for o in res["ops"] if o.get("status") == "conflict"]
        elif as_ == "revert":
            parent = graph_api._resolve(f"{target}^")                                  # noqa: SLF001
            if not parent:
                raise ContractError("bad_request", "the first commit has nothing to revert to", 422)
            since = {o["object_id"] for o in graph_api._ops(target, head)}           # noqa: SLF001
            raw = []
            for o in graph_api._ops(target, parent):                                   # noqa: SLF001  (inverse of that commit)
                (conflicts if o["object_id"] in since else raw).append(o)
            conflicts = [{"object_id": o["object_id"], "why": "changed again after that commit"} for o in conflicts]
        elif as_ == "cherry-pick":
            picked = graph_api._cherry_pick(target, head, "HEAD")                      # noqa: SLF001
            if picked is None:
                raise ContractError("bad_request", "a merge commit cannot be cherry-picked here", 422)
            raw = [o for o in picked["ops"] if o.get("status") == "applies"]
            conflicts = [{"object_id": o["object_id"], "why": o.get("reason")} for o in picked["ops"]
                         if o.get("status") != "applies"]
        else:                                                                          # restore, checkout
            raw = graph_api._ops(head, target)                                         # noqa: SLF001
        ops = [_op(o) for o in raw if o["op"] in KIND]
        dirty = bool(room._git("status", "--porcelain=v1", "--untracked-files=all").strip())  # noqa: SLF001
    except room.RoomError as e:
        raise ContractError("room_unavailable", str(e), 503)
    return {"base_sha": head, "target_sha": target, "ref_resolved": ref, "ops": ops, "conflicts": conflicts,
            "summary": {k: sum(1 for o in ops if o["kind"] == k) for k in ("move", "add", "remove")},
            "estimated_s": SECONDS_PER_OP * len(ops), "working_tree_dirty": dirty,
            "applied": False, "executor": "not_connected"}


async def _plan(as_: str, ref: str) -> dict:
    return await asyncio.to_thread(_plan_sync, as_, ref)


def _read(command: str, state: str | None) -> dict:
    """status / diff / log: git reads only (room.py, graph_api), never a write."""
    import graph_api
    import room
    try:
        if command == "log":
            raw = room._git("log", "--format=%h%x09%s", "-n", "15")                     # noqa: SLF001
            return {"commits": [dict(zip(("sha", "subject"), l.split("\t", 1))) for l in raw.splitlines() if l]}
        if command == "diff" and state:
            head, target = graph_api._resolve("HEAD"), graph_api._resolve(state)       # noqa: SLF001
            if not target:
                raise ContractError("not_found", f"no commit, branch or tag {state!r} in the room's history", 404)
            return {"a": head, "b": target, "ops": graph_api._ops(head, target)}      # noqa: SLF001
        return room.summary(room.snapshot())                                            # status, bare diff
    except room.RoomError as e:
        raise ContractError("room_unavailable", str(e), 503)


async def _apply(intent: dict) -> dict:
    cmd, state = intent["command"], intent.get("target_state")
    if cmd == "restore":
        if not state:
            raise ContractError("bad_request", "restore needs a target state", 422)
        # restore = make the room match a state, ON TOP of HEAD, as a new commit (history keeps the
        # undo). Not checkout (that moves HEAD), and never revert (that undoes one commit).
        result = await _plan("restore", state)
        # the ref that EXISTS (ref_resolved), never the word that was said: `git restore --source=study-mode`
        # fails in a room whose tag is `study`, and this line is shown to people as the thing to type
        result["git_equivalent"] = (f"git restore --source={result['ref_resolved']} --staged --worktree"
                                    " -- zones && git commit")
        return {"kind": "plan", "as": "restore", "ref": state, "result": result, "frame": FRAME}
    if cmd in ("status", "diff", "log"):
        as_ = "diff" if (cmd == "diff" and state) else ("status" if cmd == "diff" else cmd)
        return {"kind": "read", "as": as_, "ref": state, "result": await asyncio.to_thread(_read, cmd, state),
                "frame": FRAME}
    return {"kind": "refused", "as": cmd, "ref": state, "frame": FRAME,        # add, commit
            "result": {"detail": f"'{cmd}' writes room.git — roomctl's job (the CLI / executor). "
                                 "This server never writes room.git."}}


async def _graph(verb: str, ref: str | None) -> dict:
    if verb in GRAPH_PLANNED:
        ref = ref or ("HEAD" if verb == "revert" else None)
        if not ref:
            raise ContractError("bad_request", f"'{verb}' needs a ref (a sha, branch or tag)", 422)
        return {"kind": "plan", "as": verb, "ref": ref, "result": await _plan(verb, ref), "frame": FRAME}
    where = {"merge": "preview it with GET /api/merge-preview; resolve conflicts with POST /api/resolve"}
    return {"kind": "refused", "as": verb, "ref": ref, "frame": FRAME,
            "result": {"detail": f"'{verb}' is graph-native but not plannable from the panel. "
                                 + where.get(verb, "Use the room CLI.")}}


def _outcome(action: dict) -> tuple[str, str]:
    """One line for the trace and the command_result. It runs on EVERY answer, so it must never raise:
    a missing field here turns work that already SUCCEEDED into a 500 (Sentry caught that once, below)."""
    r = action.get("result") or {}
    if action["kind"] == "job":
        sent = (r.get("dispatch") or {}).get("dispatched")
        return ("DISPATCHED" if sent else "PLANNED"), (f"point {action.get('ref')}: {(r.get('job') or {}).get('job_id')} "
                                                       + ("sent to the housebot edge" if sent else
                                                          f"not sent ({(r.get('dispatch') or {}).get('why')})"))
    if action["kind"] == "jobs":
        sent = (r.get("dispatch") or {}).get("dispatched")
        return ("DISPATCHED" if sent else "PLANNED"), (f"tidy {action.get('ref') or 'the room'}: "
                                                       f"{len(r.get('jobs') or [])} move job(s), "
                                                       f"{len(r.get('skipped') or [])} not sendable, "
                                                       + ("sent in order" if sent else f"not sent ({(r.get('dispatch') or {}).get('why')})"))
    if action["kind"] == "confirm":
        return "ASKED", r.get("question") or f"which {action.get('ref')}?"   # ASK: named, nothing sent
    if action["kind"] == "proposal":
        return "PROPOSED", (f"move {action.get('ref')} to {r.get('to_zone')}: needs approval "
                            "(a pull request), no job yet")
    if action["kind"] == "plan":
        return "PLANNED", (f"{action.get('as')} {action.get('ref')}: {len(r.get('ops') or [])} op(s), "
                           f"executor {r.get('executor')}")
    if action["kind"] == "read":
        return "READ", f"{action.get('as')}" + (f" {action.get('ref')}" if action.get("ref") else "")
    # The catch-all runs for any kind not named above, so it cannot assume that shape carries a
    # "detail". It did, and an action without one raised KeyError here and turned the whole
    # /api/agent/command request into a 500 -- a crash in the summariser, after the work had
    # already succeeded. Sentry caught it (issue "KeyError: 'detail'", culprit /api/agent/command).
    return "REFUSED", r.get("detail") or f"no outcome for action kind {action.get('kind')!r}"


async def _nothing_like_that(text: str, rid: str) -> None:
    """Raise the resolver's own refusal when a sentence nobody could parse names nothing in the room.
    Returns quietly when the room DOES have something like it — then the sentence, not the object, was
    the problem, and `unknown_command` is the honest answer. Never raises anything else: this runs on a
    path that is already failing, and a resolver outage must not replace the real reason."""
    try:
        found = await caretaker.resolve({"object_query": text, "object_id": None})
    except ContractError as e:
        if e.code == "no_match":
            raise
        return
    except Exception:  # noqa: BLE001 — no cluster, no roomctl: keep the original refusal
        return
    if found.get("object_id"):
        raise ContractError("unknown_command", f"I know the {found.get('class') or found['object_id']}"
                            + (f" on the {found['zone']}" if found.get("zone") else "")
                            + f", but not what you want done with it in {text.strip()!r}. Try \"where is "
                              f"the {found.get('class') or 'mug'}\", \"tidy up\", or \"put it back\".", 422,
                            {"object_id": found["object_id"], "score": found.get("score")})


async def _handle(rid: str, text: str, confirmed: str | None = None, who: str | None = None) -> tuple[dict, int]:
    out = {"request_id": rid, "path": None, "served_by": None, "intent": None, "action": None,
           "messages": [], "ignored": [], "trace": [{"node": "panel", "label": text}]}
    trace, status = out["trace"], 200
    turn_cm = obs.agent_turn(text, model="gitirl-agent", sdk_visible=False) if obs else None
    turn = turn_cm.__enter__() if turn_cm else None
    stage = "route"                                     # the node a failure is pinned to
    care: dict | None = None                            # a caretaker Intent (bridge/intents.py), when there is one
    try:
        path, verb, ref = route(text)
        out["path"] = path
        if path == "graph":
            out["served_by"] = "gitspace"
            trace.append({"node": "route", "label": "graph",
                          "why": f"'{verb}' is graph-native: planned on our side, never sent to the middleware"})
        else:
            stage = "grammar"
            care = intents.parse(text, rid)                             # OUR caretaker grammar first (§12)
        if path != "graph" and care is not None:
            path = out["path"] = "caretaker"
            out["served_by"] = "gitspace:grammar"
            trace.append({"node": "route", "label": "caretaker",
                          "why": f"our grammar: {care['intent']}" + (f" {care['object_query'] or care['object_id']}"
                                                                      if care.get("object_query") or care.get("object_id") else "")})
        elif path != "graph":
            trace.append({"node": "route", "label": "middleware",
                          "why": "not a caretaker phrase and not graph-native: the six-verb grammar"})
            env = {"type": "user_command", "request_id": rid, "timestamp": now_iso(),
                   "payload": {"text": for_parser(text)}}
            stage = "decipher"
            t0 = time.perf_counter()
            with _span("gitirl-agent", "decipher", text=text, request_id=rid) as sp:
                who, msgs = await decipher(env)
                _set(sp, **{"gen_ai.tool.call.result": msgs, "served_by": who})
            out["served_by"] = who
            for m in msgs:                                              # his side, in: asserted, not converted
                assert_frame(m.get("payload") or {}, f"from gitirl-agent ({m.get('type')})")
            out["messages"] = [m for m in msgs if m.get("type") in ("parsed_command", "command_status", "error")]
            out["ignored"] = [{"message": m, "why": "from gitirl-agent's own orchestrator — gitspace's executor "
                                                     "applies intents, the bridge only deciphers them"}
                              for m in msgs if m.get("type") in ("command_result", "robot_action")]
            parsed = next((m for m in msgs if m.get("type") == "parsed_command"), None)
            err = next((m for m in msgs if m.get("type") == "error"), None)
            trace.append({"node": "decipher", "served_by": who, "ms": round((time.perf_counter() - t0) * 1000, 1),
                          "label": (f"{parsed['payload'].get('command')} {parsed['payload'].get('target_state') or ''}".strip()
                                    if parsed else (err or {}).get("payload", {}).get("code", "no answer"))})
            if not parsed:
                p = (err or {}).get("payload", {})
                stage = "intent"                                        # nothing of ours: Andrew's understanding layer
                try:
                    care = intents.from_service(text, rid, who=who)
                except intents.IntentError as e:
                    raise ContractError(e.code, e.message, 503 if e.code == "intent_unavailable" else 422) from None
                if care is None:
                    # Nobody's grammar knew it and there is no understanding layer here (the cloud tier has
                    # no OpenAI key by design). The RESOLVER is still available, so before giving up, ask
                    # the room whether the sentence even names something it has: "pick up the trash" then
                    # gets the same honest refusal, naming the nearest, instead of "unknown command".
                    try:
                        await _nothing_like_that(text, rid)
                    except ContractError:
                        stage = "resolve"          # the ROOM answered: that is where this failed
                        raise
                    raise ContractError(p.get("code", "unknown_command"), p.get("message", "not deciphered"), 422)
                path = out["path"] = "caretaker"
                out["served_by"] = "andrew:intent"
                trace.append({"node": "intent", "served_by": "andrew:intent", "label": care["intent"],
                              "confidence": care["confidence"]})
        if path == "middleware":
            parsed = next((m for m in out["messages"] if m.get("type") == "parsed_command"), None)
            if parsed:
                intent = {"command": parsed["payload"].get("command"), "target_state": parsed["payload"].get("target_state"),
                          "message": parsed["payload"].get("message"), "raw_text": text, "metadata": {}}
                check_intent(text, intent)
                out["intent"] = intent
        if path == "caretaker":
            if confirmed and care is not None:
                # "yes, that one": the person picked from the ASK band, so the Intent now NAMES the object
                # and resolve() takes the id path. Re-validated, because an Intent is only ever a valid one.
                care = intents.validate({**care, "object_id": confirmed})
                trace.append({"node": "confirm", "label": confirmed, "why": "the person confirmed which object"})
            out["intent"] = care
        stage = "executor"
        t0 = time.perf_counter()
        with _span("gitspace", "executor.plan", path=path) as sp:
            action = await (_graph(verb, ref) if path == "graph" else
                            caretaker.act(care) if path == "caretaker" else _apply(out["intent"]))
            assert_frame(action, "to the executor")                     # our side, out: declared Z-up
            _set(sp, **{"gen_ai.tool.call.result": {k: action[k] for k in ("kind", "as", "ref")}})
        if action["kind"] == "plan":
            action["base_sha"], action["target_sha"] = action["result"]["base_sha"], action["result"]["target_sha"]
        out["action"] = action
        st, msg = _outcome(action)
        trace.append({"node": "executor", "label": msg, "ms": round((time.perf_counter() - t0) * 1000, 1),
                      "executor": action["result"].get("executor") if action["kind"] in ("plan", "job", "jobs") else None})
        out["messages"].append(envelope("command_result", rid, {"status": st, "message": msg, "attempts": 0}))
    except ContractError as e:
        status = e.status if e.code in INFRA else 200          # typing can't earn a 4xx (contract.INFRA)
        out["error"] = {"code": e.code, "message": e.message, **({"details": e.details} if e.details else {})}
        if not any(m.get("type") == "error" for m in out["messages"]):
            out["messages"].append(envelope("error", rid, {"code": e.code, "message": e.message, "details": {}}))
        trace.append({"node": stage, "label": e.code, "error": e.message})
        if turn is not None:
            turn.set_status("internal_error")
    finally:
        out["ok"] = "error" not in out
        if turn is not None:
            turn.set_data("gen_ai.response.model", out.get("served_by") or "none")
        out["sentry_trace_id"] = (obs.trace_fields().get("sentry_trace_id") if obs else None)
        if turn_cm:
            turn_cm.__exit__(None, None, None)
    return out, status


@router.post("/api/agent/command")
async def agent_command(request: Request, body: dict = Body(...)):
    try:
        rid, text = read_request(body)
        confirmed = confirmed_object(body)
    except ContractError as e:
        return JSONResponse({"request_id": body.get("request_id") if isinstance(body, dict) else None,
                             "error": {"code": e.code, "message": e.message}, "trace": []}, status_code=e.status)
    if rid in _DONE:                                                    # the same request never plans twice
        b, st = _DONE[rid]
        return JSONResponse({**b, "replayed": True}, status_code=st)
    if rid in _INFLIGHT:
        b, st = await asyncio.shield(_INFLIGHT[rid])
        return JSONResponse({**b, "replayed": True}, status_code=st)
    fut = asyncio.get_running_loop().create_future()
    _INFLIGHT[rid] = fut
    try:
        b, st = await _handle(rid, text, confirmed, who=_caller(request))
        fut.set_result((b, st))
        _DONE[rid] = (b, st)
        while len(_DONE) > 512:
            _DONE.popitem(last=False)
        return JSONResponse(b, status_code=st)
    finally:
        _INFLIGHT.pop(rid, None)


@router.get("/api/agent/bridge")
async def bridge_status():
    return {"mode": os.getenv("ANDREW_BRIDGE", "auto"), "live": {"ws": HUB.connected(), "jsonl": JSONL.available()},
            "will_serve": will_serve(),
            "understanding": intents.gate_stats(),
            "andrew": {"rev": JSONL.revision() if JSONL.available() else None,
                       "ws": "retired on his side at b4f3e07 (HTTP+SSE instead): nothing dials /ws/gitirl-agent",
                       "jsonl": "his real parser, run with no GITIRL_* variables — it cannot reach a robot"}}


def _caller(request: Request) -> str:
    """Who to count against the per-caller limit. Behind Vercel -> GCP the browser's address is the
    first entry of x-forwarded-for; direct callers have none. It is spoofable, which is why the DAILY
    cap exists as well — that one no header can move."""
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "?")


def _trusted(ws: WebSocket) -> bool:
    """His agent may dial in from this laptop or the LAN with no token. Anything that came through a
    proxy (the Cloudflare tunnel makes this server public) needs GITIRL_WS_TOKEN."""
    token = os.getenv("GITIRL_WS_TOKEN", "")
    if token and ws.headers.get("authorization") == f"Bearer {token}":
        return True
    if any(h in ws.headers for h in _FORWARDED):
        return False
    try:
        ip = ipaddress.ip_address(ws.client.host if ws.client else "")
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


@router.websocket("/ws/gitirl-agent")
async def agent_socket(ws: WebSocket):
    if not _trusted(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    HUB.socket = ws
    try:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except ValueError:
                continue
            if isinstance(msg, dict):
                HUB.deliver(msg)
    except WebSocketDisconnect:
        pass
    finally:
        if HUB.socket is ws:
            HUB.socket = None
