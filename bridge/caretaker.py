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
import sys

from bridge.contract import FRAME, ContractError

UNITS = {"position": "m", "yaw": "deg", "duration": "s"}
# Below this rerank margin (1st - 2nd) the top two objects are too close to call: ask, never guess.
# Measured on the live room: clear phrases resolve at 0.22-0.52, "something to drink from" at 0.071.
MIN_MARGIN = float(os.getenv("RESOLVE_MIN_MARGIN", "0.05"))
# THREE BANDS, because two were a false choice between a robot that guesses and a room that pretends it
# has never heard of a mug. Measured on THIS room, jina-reranker-v3.5, 2026-09-19 (elastic-09 + here):
#
#   score < 1.11    REFUSE   nothing in the room is that thing — and name the nearest, so it is checkable
#   1.11 <= s < 1.20 ASK     "I think you mean the mug — shall I?"   (a yes acts; no yes, nothing happens)
#   score >= 1.20    ACT
#
#   THE ABSURD CLUSTER, things this room does not have: "banana" 1.056, "the trash" 1.065-1.095,
#   "television remote" 1.101 (the top of it). These must REFUSE: a question invites a yes, and a yes
#   would act on a bowl nobody asked for.
#   THE VAGUE-BUT-REAL CLUSTER: "something to write with" 1.126, "something to drink from" 1.169. These
#   must not be refused — they are the sentences the conversational beat is built on.
#   ACTED ON OUTRIGHT: "the thing I cut paper with" 1.266, "coffee cup" 1.271, "my keys" 1.454, 1.616.
#   1.11 is the gap between the two clusters: 0.009 of daylight below it, 0.016 above.
#
# These are measurements of THIS ROOM's object set, not constants of the model: elastic-09 saw "the trash"
# move 1.029 -> 1.095 when a bowl arrived mid-evening. If the objects change before a demo, MEASURE AGAIN.
# And part of the overlap is labelling, not model error — "a bottle of water" -> the mug is a defensible
# answer, so do not widen the act band to make a reasonable answer count as wrong (elastic/queries.py
# keeps those phrasings in a separate near-miss list for exactly that reason).
MIN_CONFIRM_SCORE = float(os.getenv("RESOLVE_CONFIRM_SCORE", "1.11"))   # below this: refuse, never ask
MIN_ACT_SCORE = float(os.getenv("RESOLVE_MIN_SCORE", "1.20"))           # at or above: act without asking


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


# ── the room that is actually on screen ───────────────────────────────────────────────
# WHY THIS EXISTS. Elastic's index is built from room.git. A scene instance
# (~/.cache/gitspace/rooms/<name>, the room /robot draws and the one rooms/.current names) is a
# DIFFERENT room, so asking one about the other answers "there is nothing in the room that matches
# 'snack bag'; the nearest are notebook, book and scissors" — about a desk nobody is looking at,
# while the packet is plainly on the floor in front of the robot.
#
# So when Elastic DECLINES, and none of the things it offered are in this room, the question is put
# to this room instead: by the words first, and then, for a name this room does not use ("snack
# bag" for a chip packet, "crumpled paper" for a small box), to a language model that must choose
# from this room's own list or say none.
#
# TWO RULES, because this reaches a gripper. The model can never invent an object: its answer is
# checked against the list it was handed, and anything else is thrown away. And a model's pick is
# always returned as an ASK (needs_confirmation), never as something to act on — only an exact
# name from the room's own vocabulary acts without a question, which is what `how: "room"` has
# always meant. If nothing here matches either, Elastic's original refusal stands, unchanged.
RESOLVE_MODEL = os.getenv("RESOLVE_LLM_MODEL", "gpt-5")
RESOLVE_EFFORT = os.getenv("RESOLVE_LLM_EFFORT", "minimal")
RESOLVE_TIMEOUT_S = float(os.getenv("RESOLVE_LLM_TIMEOUT", "12"))
PICK_SCHEMA = {"type": "object", "additionalProperties": False,
               "properties": {"object_id": {"type": "string"}, "why": {"type": "string"}},
               "required": ["object_id", "why"]}


HEX_TAIL = re.compile(r"_[0-9a-f]{4}$")
STOP = {"the", "a", "an", "my", "our", "that", "this", "find", "get", "please", "some", "of", "it"}


def _words(text: str) -> set[str]:
    return {_singular(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower())}


def _object_words(oid: str, o: dict) -> set[str]:
    """Everything this object is called: its class, and the name it was given at first sight."""
    return _words(HEX_TAIL.sub("", oid).replace("_", " ")) | _words(o.get("class"))


def _rooms_dir():
    from pathlib import Path
    return Path(os.getenv("ROOM_LIVE_DIR", "~/.cache/gitspace/rooms")).expanduser()


def _current_instance(want: str | None = None):
    """(name, repo) of the room the question is about, or None.

    `want` is the room the PAGE says it is showing (payload.instance). It wins, because
    rooms/.current is a pointer every session's `room_live.py add` rewrites — a page displaying
    `chips` was answered about `f5-centred` the moment another session scanned. Without it, the
    room being worked in is the best guess there is."""
    rooms = _rooms_dir()
    try:
        name = want or (rooms / ".current").read_text().strip()
    except OSError:
        name = want or ""
    if not name:
        return None
    if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
        return None
    repo = rooms / name
    return (name, repo) if (repo / ".git").is_dir() else None


def _instance_objects(repo) -> dict[str, dict]:
    """{object_id: {class, zone}} from a scene instance's working tree, read leniently."""
    import room
    out: dict[str, dict] = {}
    for f in sorted(repo.glob("zones/*/*.yaml")):
        try:
            rec = room._record(f.read_text())                                  # noqa: SLF001
        except Exception:  # noqa: BLE001 — a half-written record is skipped, never fatal
            continue
        oid = rec.get("id") or f.stem
        if oid:
            pose = rec.get("pose") if isinstance(rec.get("pose"), dict) else {}
            out[oid] = {"class": rec.get("class"), "zone": f.parent.name,
                        "pose": {k: pose.get(k) for k in ("x", "y", "z", "yaw")} if pose else None}
    return out


def _llm_pick(query: str, objects: dict[str, dict]) -> dict | None:
    """The model's choice among THESE objects, or None. Never a new object, never an exception."""
    # A suite must not spend money on a call nobody mocked: bridge/conftest.py blanks Sentry, not
    # OpenAI, and the refuse-band tests walk straight through here. A test that wants this path
    # substitutes _llm_pick, which is what the tests below do.
    if os.getenv("PYTEST_CURRENT_TEST"):
        return None
    try:
        # keys.py lives in perception/, which is not on sys.path in every process that imports this.
        root = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "perception")
        if root not in sys.path:
            sys.path.insert(0, root)
        from keys import credential
        key, _why = credential("OPENAI_API_KEY")
        if not key:
            return None
        from openai import OpenAI

        listing = "\n".join(f"{oid}: {o.get('class') or 'unknown'} (in the {o.get('zone') or 'room'})"
                            for oid, o in sorted(objects.items()))
        prompt = ("A robot's room holds exactly these objects:\n" + listing +
                  f"\n\nSomeone asked for: {query!r}\nWhich ONE of the listed object_ids did they mean? "
                  "People describe a thing by what it looks like or what it is for, not by the room's "
                  "own wording. If none of them is plausibly that thing, answer with object_id \"none\". "
                  "Answer with an object_id from the list above and one short clause saying why.")
        kw = {"reasoning": {"effort": RESOLVE_EFFORT}} if RESOLVE_EFFORT else {}
        r = OpenAI(api_key=key, timeout=RESOLVE_TIMEOUT_S, max_retries=0).responses.create(
            model=RESOLVE_MODEL, input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            text={"format": {"type": "json_schema", "name": "object_pick", "schema": PICK_SCHEMA, "strict": True}},
            **kw)
        import json as _json
        pick = _json.loads(r.output_text)
        oid = pick.get("object_id")
        return {"object_id": oid, "why": (pick.get("why") or "").strip()[:160]} if oid in objects else None
    except Exception:  # noqa: BLE001 — no key, no network, a bad answer: the room's own words still stand
        return None


def _from_this_room(query: str, offered: list[str], want: str | None = None) -> dict | None:
    """Elastic declined and was talking about another room: ask THIS room. None = it cannot help either."""
    here = _current_instance(want)
    if not here:
        return None
    name, repo = here
    objs = _instance_objects(repo)
    if not objs or any(oid in objs for oid in offered):
        return None                      # Elastic WAS talking about this room: do not second-guess it
    # MATCHING ON WORDS, not on the whole string. A class is often two words ("chip packet"), and
    # `cls in query_words` can never be true for one — which is how a room holding a chip packet
    # answered "nothing matches 'the chip packet'". An object's words are its class AND its id,
    # because the id keeps what it was called when it was first seen ("crumpled_snack_bag"), which
    # is frequently the word a person reaches for.
    q = _words(query) - STOP
    scored = {oid: len(q & _object_words(oid, o)) for oid, o in objs.items()}
    # Acting without a question needs the query to be SPELLED OUT by the object: every word of it
    # (bar the stop words) is one of that object's own. One shared word is a hint, and a hint is
    # something to ask about, not to send a gripper at.
    exact = sorted(oid for oid, n in scored.items() if n and q <= _object_words(oid, objs[oid]))
    if len(exact) == 1:
        o = objs[exact[0]]
        return {"object_id": exact[0], "class": o.get("class"), "zone": o.get("zone"),
                "how": f"room:{name}", "score": None, "candidates": exact}
    best = max(scored.values(), default=0)
    literal = sorted(oid for oid, n in scored.items() if n and n == best)
    pick = _llm_pick(query, {k: objs[k] for k in (exact or literal)} if (exact or literal) else objs)
    if pick:
        o = objs[pick["object_id"]]
        return {"object_id": pick["object_id"], "class": o.get("class"), "zone": o.get("zone"),
                "how": f"llm:{name}", "score": None, "candidates": [pick["object_id"], *[c for c in literal if c != pick["object_id"]]],
                "needs_confirmation": True, "act_floor": MIN_ACT_SCORE,
                "why_ask": f"{query!r} is not a name this room uses; matched by description ({pick['why']})"
                           if pick["why"] else f"{query!r} is not a name this room uses; matched by description"}
    return None


async def resolve(intent: dict) -> dict:
    """{object_id, class, how: id|elasticsearch|room|room:<instance>|llm:<instance>, score, candidates}."""
    if intent.get("object_id"):
        return {"object_id": intent["object_id"], "class": None, "how": "id", "score": None,
                "candidates": [intent["object_id"]]}
    query = intent["object_query"]
    try:
        import es_shared
        q = es_shared.queries()
    except Exception:  # noqa: BLE001 — parked / unconfigured / not importable: the room still knows
        try:
            return await asyncio.to_thread(_from_room, query)
        except ContractError:
            here = await asyncio.to_thread(_from_this_room, query, [], intent.get("instance"))
            if here:
                return here
            raise
    if hasattr(q, "resolve_object"):          # the resolver's Elastic half (elastic/queries.py)
        r = await asyncio.to_thread(lambda: q.resolve_object(query, k=5))
        matches, margin = r.get("matches") or [], r.get("margin")
        if not matches:
            here = await asyncio.to_thread(_from_this_room, query, [], intent.get("instance"))
            if here:
                return here
            raise ContractError("not_found", f"Elasticsearch has nothing matching {query!r}", 404,
                                {"how": "elasticsearch"})
        top = matches[0]
        # TWO different guards, and this is the first: `confident` answers "is any of these the thing at
        # all". A nearest neighbour ALWAYS exists, so without it "pick up the trash" resolves to a ceramic
        # cup and the robot throws it away, with nothing reporting a failure (elastic/queries.py). The
        # matches are still shown — useful in a search box, dangerous in a gripper.
        if r.get("confident") is False or float(top.get("score") or 0) < MIN_CONFIRM_SCORE:
            near = [m.get("class") or m["object_id"] for m in matches[:3]]
            nearest = (f"; the nearest are {', '.join(near[:-1])} and {near[-1]}" if len(near) > 1
                       else (f"; the nearest is {near[0]}" if near else ""))
            # Before refusing: was Elastic even talking about the room on screen? (see _from_this_room)
            here = await asyncio.to_thread(_from_this_room, query, [m["object_id"] for m in matches], intent.get("instance"))
            if here:
                return here
            raise ContractError("no_match", f"there is nothing in the room that matches {query!r}{nearest}", 404,
                                {"candidates": [m["object_id"] for m in matches], "top_score": r.get("top_score"),
                                 "confident": r.get("confident"), "act_floor": MIN_ACT_SCORE,
                                 "confirm_floor": MIN_CONFIRM_SCORE,
                                 "how": "elasticsearch",
                                 "hint": "say the name of a thing that is in the room, or point at it by id"})
        # ...and the second: `margin` (1st - 2nd). Below MIN_MARGIN the top two are too close to call, so
        # we ask instead of guessing — the same rule as two spellings of a state name. Rerank scores are
        # model-specific: this compares them only with each other.
        # Too close to call between the top two. Before the confirm band existed this could only refuse;
        # now the honest move is to ASK, naming both — the person settles it in one click, and a gripper
        # still never moves on a coin flip. (Two spellings of a STATE name still refuse: there is no
        # candidate to show there, only two names for one thing.)
        close = margin is not None and margin < MIN_MARGIN and len(matches) > 1
        score = float(top.get("score") or 0)
        out = {"object_id": top["object_id"], "class": top.get("class"), "zone": top.get("zone"),
               "how": "elasticsearch", "score": round(score, 3),
               "margin": None if margin is None else round(margin, 3),
               "confident": r.get("confident"), "top_score": r.get("top_score"),
               "candidates": [m["object_id"] for m in matches]}
        if score < MIN_ACT_SCORE or close:      # the ASK band: good enough to name, not to act on
            runner = matches[1] if len(matches) > 1 else None
            out["needs_confirmation"] = True
            out["why_ask"] = ("the top two are too close to call" if close
                              else f"the score is under the {MIN_ACT_SCORE} needed to act without asking")
            out["act_floor"] = MIN_ACT_SCORE
            out["runner_up"] = ({"object_id": runner["object_id"], "class": runner.get("class"),
                                 "zone": runner.get("zone"), "score": round(float(runner.get("score") or 0), 3)}
                                if runner else None)
        # Elastic answered — but about WHICH room? Its index is room.git's; the robot may be
        # standing in a scene instance whose objects it has never seen, and then a confident
        # "the bowl, on the desk" is a confident answer about somewhere else. _from_this_room
        # returns None unless it can show that: it declines the moment any offered object is
        # one of ours, so Elastic keeps every answer that is really about this room.
        here = await asyncio.to_thread(_from_this_room, query, [m["object_id"] for m in matches], intent.get("instance"))
        if here:
            here["instead_of"] = {"object_id": top["object_id"], "class": top.get("class"),
                                  "why": "Elasticsearch's index is another room's"}
            return here
        return out
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
    except object_api.Gone:
        raise                          # act() turns this into an ANSWER; it is not an error
    except LookupError as e:
        raise ContractError("unreachable_pose", str(e), 409) from None


def _zones() -> list[str]:
    import room
    from roomctl.repo import Repo, load_room
    return sorted((load_room(Repo(room.room_path()).path).get("zones") or {}).keys())


def tidy_jobs(zone: str | None, request_id: str, only: str | None = None) -> dict:
    """The room as last scanned (working tree) → where things belong (HEAD), through roomctl's own
    planner. One `move` job per `move` op, in its order; everything else is listed, not sent.
    `only` narrows it to ONE object: "pick up the mug" must not move the whole room."""
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
    out, skipped = [], [{"object_id": u["object_id"], "why": u["reason"]} for u in p["unapplied"]
                        if only is None or u["object_id"] == only]
    for op in p["ops"]:
        if only is not None and op["object_id"] != only:
            continue
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
    return {"head": head, "zone": zone, "object_id": only, "jobs": out, "skipped": skipped,
            "frame": FRAME, "units": UNITS}


def _ask_first(found: dict, intent: dict, doing: str) -> dict:
    """The ASK band, as an action the panel can render: a question, the candidate, the runner-up, and
    exactly how to say yes. There is no pending state and no timer, so nothing can auto-accept: a yes is
    the same sentence sent again with `payload.object_id`, and a no is simply not sending it."""
    it, runner = found.get("class") or found["object_id"], found.get("runner_up")
    question = (f"I think you mean the {it}" + (f" on the {found['zone']}" if found.get("zone") else "")
                + (f", not the {runner['class'] or runner['object_id']}" if runner and runner.get("class") else "")
                + f" — shall I {doing}?")
    return {"kind": "confirm", "as": doing, "ref": found["object_id"], "frame": FRAME,
            "result": {"question": question, "resolved": found, "candidate": {
                "object_id": found["object_id"], "class": found.get("class"), "zone": found.get("zone"),
                "score": found.get("score")}, "runner_up": runner,
                "why": f"{found.get('why_ask', 'it is not certain enough to act on')} "
                       f"(scored {found.get('score')}"
                       + (f", the next is {runner['score']}" if runner and runner.get("score") else "")
                       + ") — near enough to name, not near enough to move a robot on",
                "yes": {"type": "user_command", "payload": {"text": intent.get("raw_text"),
                                                            "object_id": found["object_id"]}},
                "no": "do not send it; nothing has been planned or dispatched"}}


# ── the dispatcher: what the edge was sent, or why nothing was ──────────────────────────

def _geohash(repo, pose: dict | None) -> dict | None:
    """Where this object is in the room's OWN octree: the leaf cell its pose falls in, and the
    coarser cell that is the size of a thing rather than the size of a crumb.

    The same key the Objects tab drills and the voxel index is written with — a prefix IS a region
    — computed from THIS room's pinned cube (its room.yaml), because a key only means a place
    inside the cube it was cut from."""
    if not pose or not all(isinstance(pose.get(k), (int, float)) for k in ("x", "y", "z")):
        return None
    try:
        import yaml
        import voxel_api
        oc = (yaml.safe_load((repo / "room.yaml").read_text()) or {})["octree"]
        origin, size, levels = [float(v) for v in oc["origin"]], float(oc["size_m"]), int(oc["levels"])
        key = voxel_api.octree_key(pose["x"], pose["y"], pose["z"], origin, size, levels)
        if not key:
            return None                                  # outside the cube: no key, and no pretending
        region = key[:5] if len(key) >= 5 else key       # 25 cm at an 8 m / 8-level cube
        return {"key": key, "cell_m": round(size / 2 ** levels, 4),
                "region": region, "region_m": round(size / 2 ** len(region), 4),
                # The Objects tab, not the octree layer: that layer draws Elasticsearch's index of
                # room.git, so drilling a scene instance's key in it would show an empty region and
                # present it as an answer. /api/object-map is where THIS room's keys come from.
                "url": f"/robot?instance={repo.name}&object={{oid}}"}
    except Exception:  # noqa: BLE001 — no room.yaml, no octree, no voxel_api: the pose still answers
        return None


def _instance_answer(found: dict, intent: dict) -> dict | None:
    """An object resolved from the room on screen: say where it is, and build no job.

    room.git is where jobs are planned from — its zones, its frame, its history. A scene instance
    is a different room with its own origin, so a pose out of it is not a place to drive a robot
    to, and `graph_api.whereabouts` (which only knows room.git) would call it gone, which is the
    opposite of true: we just read it off the floor of the room the page is showing. So the honest
    answer is the one a person asked for — what it is and where it is — with the reason no job
    follows said plainly."""
    how = (found or {}).get("how") or ""
    if not (how.startswith("room:") or how.startswith("llm:")):
        return None
    name = how.split(":", 1)[1]
    obj = _instance_objects(_rooms_dir() / name).get(found["object_id"]) or {}
    pose = obj.get("pose") or {}
    at = (f" at ({pose['x']:+.2f}, {pose['y']:+.2f}) m" if isinstance(pose.get("x"), (int, float))
          and isinstance(pose.get("y"), (int, float)) else "")
    it = obj.get("class") or found.get("class") or found["object_id"]
    zone = obj.get("zone") or "room"
    where = "on the floor" if zone == "floor" else f"in the {zone}"
    said = f"the {it} is {where}{at}, in {name}"

    cell = _geohash(_rooms_dir() / name, obj.get("pose"))
    if cell:
        cell["url"] = cell["url"].replace("{oid}", found["object_id"])
        said += f", octree cell {cell['region']}"
    if how.startswith("llm:"):
        asked = intent.get("object_query") or intent.get("raw_text") or ""
        said += f" — you asked for {asked!r}; a {it} is what this room has"
    return {"kind": "read", "as": "where it is", "ref": found["object_id"], "frame": FRAME,
            "result": {"speech": said, "geohash": cell,
                       "object": {"object_id": found["object_id"], "class": obj.get("class"),
                                  "zone": obj.get("zone"), "pose": obj.get("pose"), "room": name,
                                  "geohash": cell},
                       "resolved": found, "units": UNITS,
                       "detail": "no job was built: jobs are planned from room.git, and this object is in "
                                 f"the scene instance {name!r}, which has its own frame"}}


async def _gone_answer(found: dict, intent: dict) -> dict | None:
    """`{kind: "gone"}` when what we resolved is not in the room now — the history, not an apology."""
    object_id = (found or {}).get("object_id")
    if not object_id:
        return None
    import graph_api
    where = graph_api.whereabouts(object_id)
    if where["present"]:
        return None
    return {"kind": "gone", "as": "say where it went", "ref": object_id, "frame": FRAME,
            "result": {"speech": graph_api.gone_sentence(where), "whereabouts": where, "resolved": found,
                       "detail": "no job was built: a motion needs an object the room has now",
                       "asked_for": intent.get("object_query") or intent.get("raw_text")}}


def _dispatch_summary(d: dict) -> dict:
    return {k: d.get(k) for k in ("dispatched", "why", "state", "replayed", "edge", "job_id") if k in d}


async def act(intent: dict) -> dict:
    """A validated Intent → an action for the panel: {kind, as, result, frame}."""
    kind = intent["intent"]
    if kind in ("find", "point"):
        found = await resolve(intent)
        here = _instance_answer(found, intent)
        if here:
            return here
        # WHERE IT WENT, before we offer to go to it. Elasticsearch searches the room's whole history,
        # so the best match can be a thing that left — and "shall I point at the marker on the desk?"
        # is a reasonable-sounding question whose YES drives a robot at an empty patch of desk. The
        # room knows when it went and from where; saying so is a better answer than either the offer
        # or a flat "nothing matches", which would be false about a room that used to have one.
        gone = await _gone_answer(found, intent)
        if gone:
            return gone
        if found.get("needs_confirmation"):
            return _ask_first(found, intent, "point at it")
        job = await point_job(found["object_id"], intent["request_id"])
        import housebot
        d = await housebot.submit(job)
        # the job itself carries what HAPPENED to it. It is built by object_api as "queued (no executor
        # connected)", which is true only until the dispatcher takes it: leaving that on a dispatched job
        # made the panel read as "nothing ran" while the robot was already pointing (perception-f5).
        job = {**job, "executor": "housebot-edge", "state": d["state"],
               "detail": "sent to the housebot edge; its answer arrives as the SSE `job` event and in "
                         "GET /api/jobs/{id}"} if d.get("dispatched") else {
            **job, "detail": f"planned only, not sent: {d.get('why')}"}
        return {"kind": "job", "as": "point", "ref": found["object_id"], "frame": FRAME,
                "result": {"resolved": found, "job": job, "dispatch": _dispatch_summary(d),
                           "executor": job["executor"]}}
    if kind == "tidy":
        zone, named = intent.get("zone"), intent.get("object_query")
        if named and not zone:
            # "pick up the trash" arrives here as tidy + object_query (the understanding layer's reading).
            # A tidy moves EVERYTHING out of place, so a request naming one thing must never become one.
            # If the thing is really a zone, tidy that; otherwise resolve it, which refuses when the room
            # has nothing like it, and say what we can actually do.
            zones = await asyncio.to_thread(_zones)
            if named in zones:
                zone = named
            else:
                found = await resolve(intent)          # raises no_match / ambiguous_object when it should
                if found.get("needs_confirmation"):
                    return _ask_first(found, intent, "put it back")
                planned = await asyncio.to_thread(tidy_jobs, None, intent["request_id"], found["object_id"])
                planned["resolved"] = found
                if not planned["jobs"]:
                    planned["detail"] = (f"{found['object_id']} is already where it belongs: nothing to move"
                                         if not planned["skipped"] else
                                         f"{found['object_id']} cannot be put back: "
                                         f"{planned['skipped'][0]['why']}")
                import housebot
                d = await housebot.submit_sequence(planned["jobs"]) if planned["jobs"] else \
                    {"dispatched": False, "why": planned.get("detail") or "nothing to move"}
                return {"kind": "jobs", "as": "tidy", "ref": found["object_id"], "frame": FRAME,
                        "result": {**planned, "dispatch": _dispatch_summary(d),
                                   "executor": "housebot-edge" if d.get("dispatched") else "not_connected"}}
        planned = await asyncio.to_thread(tidy_jobs, zone, intent["request_id"])
        import housebot
        d = await housebot.submit_sequence(planned["jobs"]) if planned["jobs"] else \
            {"dispatched": False, "why": "nothing to tidy: every object is where it belongs"}
        return {"kind": "jobs", "as": "tidy", "ref": intent.get("zone"), "frame": FRAME,
                "result": {**planned, "dispatch": _dispatch_summary(d),
                           "executor": "housebot-edge" if d.get("dispatched") else "not_connected"}}
    if kind == "move":
        found = await resolve(intent)
        if found.get("needs_confirmation"):
            return _ask_first(found, intent, f"propose moving it to the {intent['zone']}")
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
        # resolve FIRST when a thing is named: "put the banana back to how it was" is refused before we
        # plan anything, and only that object is restored — never the whole room on an object's behalf.
        found = await resolve(intent) if (intent.get("object_query") or intent.get("object_id")) else None
        if found and found.get("needs_confirmation"):
            return _ask_first(found, intent, "restore it")
        from bridge.agent_api import _plan
        planned = await _plan("restore", intent["when"])
        if found is not None:
            planned["ops"] = [o for o in planned.get("ops", []) if o.get("object_id") == found["object_id"]]
            planned["summary"] = {k: sum(1 for o in planned["ops"] if o["kind"] == k)
                                  for k in ("move", "add", "remove")}
            planned["resolved"], planned["scope"] = found, "one object"
        import graph_api
        found = await asyncio.to_thread(graph_api.resolve_state, intent["when"])
        planned["moment"] = {k: found.get(k) for k in ("when", "at", "how", "source")}
        if not planned.get("ops"):
            # 0 ops is an ANSWER, not a failure: the room already looks the way it did then. Say which
            # commit and which moment, because "nothing to do" on stage reads as a broken demo.
            at = found.get("at") or "that moment"
            planned["detail"] = (f"the room already looks the way it did at {at}: nothing to move "
                                 f"(commit {planned.get('target_sha', '')[:7]})")
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
