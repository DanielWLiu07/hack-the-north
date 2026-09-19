"""object_api.py — /object/<object_id>: the full life of one thing.

    GET  /api/object-life/{object_id}         everything the page draws, in one response
    POST /api/object-life/{object_id}/point   "drive there and point" — planned, never executed here
    GET  /object/{object_id}                  the page (pages/object.html)

This is where "find the hammer" lands when the hammer is gone (docs/11-elastic.md,
"Searching through time"): where and when it was last seen, the commit after which it is
absent, and a VERDICT — left the room, probably occluded, or not enough evidence — with
the observations that verdict was read from.

Data rules are store.py's: Elasticsearch first, the fixture file as a LABELLED fallback
(`source`), a real ES query error is an error, and a value that was not recorded is null.
The verdict in particular never guesses: the pipeline decides occluded-vs-removed with a
voxel raycast from each camera (docs/20-perception-logic.md Part 4); camera poses and
voxels are not read here, so this page applies a stated PROXY rule to the recorded
observations and shows every number the rule used.

Nothing here writes to room.git, and nothing here moves a robot: there is no executor
connected (roomctl/executor.py is not built), so POST .../point validates, works out the
target pose, answers 202 with `executor: "not_connected"` and publishes a `job` event
saying exactly that.
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import secrets
import statistics
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

import room
import store

try:                      # repo-root obs.py; optional, the page works without Sentry
    import obs
except ImportError:       # pragma: no cover
    obs = None

PAGES = Path(__file__).resolve().parent / "pages"
OBJECT_ID = re.compile(r"^[a-z][a-z0-9_]{0,40}_[0-9a-f]{4}$")        # mug_a1b2, tape_measure_91be
NEAR_M = 0.25             # "the spot where it stood": observations within this of its last pose
FLAKY = {"min_sightings": 5, "conf_stdev": 0.15, "conf_mean": 0.5, "occluded_fraction": 0.3}
SECONDS_TO_POINT = 40     # a drive plus one arm action (docs/16-api.md §2.3, §2.4)
MAX_OBSERVATIONS = 3000

router = APIRouter()


def init(es) -> None:
    store.init(es)                          # idempotent: capture_api / graph_api do the same


def _error(code: str, detail: str, status: int, retryable: bool = False) -> JSONResponse:
    """docs/16-api.md §2.7 — one error shape everywhere."""
    return JSONResponse({"error": code, "detail": detail, "retryable": retryable}, status_code=status)


def _upstream(e: Exception) -> JSONResponse:
    return _error(getattr(e, "code", "internal_error"), str(getattr(e, "detail", e)),
                  int(getattr(e, "status", 500)), bool(getattr(e, "retryable", False)))


def _dist(a: dict | None, b: dict | None) -> float | None:
    if not a or not b or any(a.get(k) is None or b.get(k) is None for k in "xyz"):
        return None
    return math.sqrt(sum((a[k] - b[k]) ** 2 for k in "xyz"))


# ── git: which commit is "now", and what is in its history (read-only) ────────────────

def _head() -> dict:
    """HEAD from the real room.git (always --no-optional-locks, through room._git)."""
    try:
        sha = room._git("rev-parse", "HEAD").strip()                         # noqa: SLF001
        branch = room._git("rev-parse", "--abbrev-ref", "HEAD").strip()      # noqa: SLF001
        ancestry = set(room._git("rev-list", "HEAD").split())                # noqa: SLF001
        return {"sha": sha, "branch": None if branch == "HEAD" else branch, "ancestry": ancestry, "from": "git"}
    except Exception:  # noqa: BLE001 — no repository here (e.g. the AWS tier): fall back to the index
        return {"sha": None, "branch": None, "ancestry": None, "from": None}


# ── the verdict ───────────────────────────────────────────────────────────────────────

def _history_stats(sightings: list[dict]) -> dict:
    confs = [s["confidence"] for s in sightings if s["confidence"] is not None]
    by_cam: dict[str, list[float]] = {}
    for s in sightings:
        if s["confidence"] is not None:
            by_cam.setdefault(s["camera"], []).append(s["confidence"])
    occluded = sum(1 for s in sightings if s["occluded"])
    return {
        "sightings": len(sightings),
        "cameras": {c: {"sightings": len(v), "mean_confidence": round(sum(v) / len(v), 3)} for c, v in sorted(by_cam.items())},
        "confidence": ({"min": min(confs), "max": max(confs), "mean": round(sum(confs) / len(confs), 3),
                        "stdev": round(statistics.pstdev(confs), 3)} if confs else None),
        "occluded": occluded,
        "occluded_fraction": round(occluded / len(sightings), 3) if sightings else None,
    }


def _near(obs_docs: list[dict], pose: dict, object_id: str) -> list[dict]:
    """Other things each camera reported within NEAR_M of where the object last stood."""
    out = []
    for o in obs_docs:
        if o.get("object_id") == object_id:
            continue
        p = {"x": store._num(o.get("raw_x")), "y": store._num(o.get("raw_y")), "z": store._num(o.get("raw_z"))}  # noqa: SLF001
        d = _dist(p, pose)
        if d is not None and d <= NEAR_M:
            out.append({"camera": o.get("camera"), "object_id": o.get("object_id"), "distance_m": round(d, 3),
                        "confidence": store._num(o.get("confidence")), "occluded": bool(o.get("occluded")),  # noqa: SLF001
                        "rejected_reason": o.get("rejected_reason"), "capture_id": o.get("capture_id")})
    return out


async def _verdict(object_id: str, last: dict, gone: dict | None, sightings: list[dict]) -> dict:
    """left_room | occluded | flaky | unknown — with the rule in words and its numbers."""
    stats = _history_stats(sightings)
    cams_ever = sorted(stats["cameras"])
    last_sighting = sightings[-1] if sightings else None
    out: dict = {"kind": "unknown", "headline": "not enough evidence recorded", "rule": None,
                 "near_m": NEAR_M, "history": stats, "cameras_that_saw_it": cams_ever,
                 "last_sighting": last_sighting, "gone_after": gone, "after": None, "watch": None,
                 "method": "A proxy applied to the recorded observations. The pipeline itself decides this with a voxel "
                           "raycast from each camera (docs/20 Part 4); camera poses and voxels are not read here."}
    if not sightings:
        out["rule"] = "No observation of this object is recorded, so nothing can be said about how it left."
        return out

    c, f = stats["confidence"], FLAKY
    if stats["sightings"] >= f["min_sightings"] and c and (
            c["stdev"] > f["conf_stdev"] or c["mean"] < f["conf_mean"] or stats["occluded_fraction"] > f["occluded_fraction"]):
        out.update(kind="flaky", headline="chronically flaky — not enough evidence",
                   rule=f"Its own sightings were unreliable (confidence mean {c['mean']:.2f}, spread {c['stdev']:.2f}, "
                        f"{stats['occluded']} of {stats['sightings']} occluded), so its absence proves nothing either way.")
        return out

    capture_id = (gone or {}).get("capture_id")
    if not capture_id:
        out["rule"] = "No later capture is recorded for the commit where it went missing, so the cameras' view of the spot is unknown."
        return out

    after_docs, _ = await store._find("room-observations", {"capture_id": capture_id}, size=2000,  # noqa: SLF001
                                      label="object.vanish_capture")
    per_cam = []
    for cam in cams_ever:
        mine = [o for o in after_docs if o.get("camera") == cam]
        near = [n for n in _near(mine, last["pose"], object_id)]
        clear = sorted((n for n in near if not n["occluded"] and n["object_id"]), key=lambda n: n["distance_m"])
        blocked = [n for n in near if n["occluded"]]
        state = "occluded_nearby" if blocked else "clear" if clear else "no_evidence"
        per_cam.append({"camera": cam, "reported": len(mine), "state": state,
                        "nearest": clear[0] if clear else None, "occluded_nearby": blocked[:3]})
    out["after"] = {"capture_id": capture_id, "cameras": per_cam}

    # the watch loop between its last sighting and that capture: did the camera keep seeing the spot?
    if last_sighting and gone and gone.get("ts") and store.when(last_sighting["ts"]) < store.when(gone["ts"]):
        # only the watch loop's captures, asked of the index: filtering them out of a size-capped page of
        # EVERY observation in the window silently lost the evidence once the window got busy
        watch_docs, _ = await store._find("room-observations", {}, time_range=(last_sighting["ts"], gone["ts"]),  # noqa: SLF001
                                          prefix={"capture_id": "watch_"},
                                          size=MAX_OBSERVATIONS, label="object.watch_window")
        # strictly AFTER its last sighting: the boundary document is that sighting itself
        watch_docs = [o for o in watch_docs if str(o.get("capture_id", "")).startswith("watch_")
                      and store.when(o) > store.when(last_sighting["ts"]) and o.get("capture_id") != last_sighting["capture_id"]]
        caps = {o["capture_id"] for o in watch_docs}
        saw_spot = {n["capture_id"] for n in _near(watch_docs, last["pose"], object_id) if not n["occluded"] and n["object_id"]}
        saw_it = {o["capture_id"] for o in watch_docs if o.get("object_id") == object_id}
        out["watch"] = {"captures": len(caps), "saw_the_spot": len(saw_spot - saw_it), "saw_the_object": len(saw_it),
                        "cameras": sorted({o["camera"] for o in watch_docs if o.get("camera")})}

    states = {c["state"] for c in per_cam}
    names = ", ".join(cams_ever)
    if "occluded_nearby" in states:
        who = ", ".join(c["camera"] for c in per_cam if c["state"] == "occluded_nearby")
        out.update(kind="occluded", headline="probably occluded — not proven gone",
                   rule=f"In {capture_id}, {who} reported an OCCLUDED observation within {NEAR_M * 100:.0f} cm of where it last "
                        f"stood, so something was in the way of a camera that used to see it.")
    elif states == {"clear"}:
        out.update(kind="left_room", headline="it left the room — it was not hidden",
                   rule=f"Every camera that ever saw it ({names}) reported another object, unoccluded, within "
                        f"{NEAR_M * 100:.0f} cm of where it last stood in {capture_id}, none reported an occlusion there, "
                        f"and its own sightings had been steady (confidence {c['min']:.2f}–{c['max']:.2f} over "
                        f"{stats['sightings']} sightings).")
    else:
        who = ", ".join(c["camera"] for c in per_cam if c["state"] == "no_evidence")
        out["rule"] = (f"In {capture_id}, {who} reported nothing within {NEAR_M * 100:.0f} cm of where it last stood, "
                       f"so whether that camera could still see the spot is not recorded.")
    return out


# ── the life ──────────────────────────────────────────────────────────────────────────

async def life(object_id: str) -> dict:
    (objs, source), (obs_docs, _), (events, _), caps, head = await asyncio.gather(
        store._find("room-objects", {"object_id": object_id}, size=1000, newest_first=True, label="object.appearances"),   # noqa: SLF001
        store._find("room-observations", {"object_id": object_id}, size=MAX_OBSERVATIONS, newest_first=True,  # noqa: SLF001
                    label="object.observations"),
        store._find("room-events", {}, size=1000, newest_first=True, label="object.events"),                                # noqa: SLF001
        store.captures(500), asyncio.to_thread(_head))
    if not objs and not obs_docs:
        raise store.NotFound(object_id)
    obs_docs = obs_docs[::-1]                                         # oldest first
    commits = {e["commit_sha"]: e for e in events if e.get("commit_sha")}
    capture_meta = {c["capture_id"]: c for c in caps["captures"]}
    objs.sort(key=store.when)

    # which commit is "now": real git; else the newest commit the index knows on main
    if not head["sha"]:
        on_main = sorted((e for e in commits.values() if e.get("branch") == "main"), key=store.when)
        if on_main:
            head = {"sha": on_main[-1]["commit_sha"], "branch": "main", "ancestry": None, "from": "index"}
    indexed = head["sha"] in commits or any(o.get("commit_sha") == head["sha"] for o in objs)
    present_now = (any(o.get("commit_sha") == head["sha"] for o in objs) if head["sha"] and indexed else None)
    in_history = (lambda sha: sha in head["ancestry"]) if head["ancestry"] is not None else (
        lambda sha: (commits.get(sha) or {}).get("branch") == head["branch"])

    by_sha = {o["commit_sha"]: o for o in objs}
    timeline = []
    for o in objs:
        ev = commits.get(o["commit_sha"]) or {}
        parent = by_sha.get(o.get("parent_sha"))
        moved = _dist(o.get("pose"), parent.get("pose")) if parent else None
        timeline.append({
            "commit_sha": o["commit_sha"], "parent_sha": o.get("parent_sha"), "branch": o.get("branch"),
            "ts": o.get("@timestamp"), "subject": ev.get("message"), "capture_id": o.get("capture_id"),
            "zone": o.get("zone"), "pose": o.get("pose"), "confidence": store._num(o.get("confidence")),  # noqa: SLF001
            "observed_by": o.get("observed_by") or [], "in_head_history": in_history(o["commit_sha"]),
            "moved_m": round(moved, 3) if moved is not None else None,
            "change": ("first appearance" if not parent else "moved" if moved and moved >= 0.005 else "unchanged"),
            "yaw_delta": (None if not parent or (o.get("pose") or {}).get("yaw") is None or (parent.get("pose") or {}).get("yaw") is None
                          else (o["pose"]["yaw"] - parent["pose"]["yaw"])),
        })
    timeline.reverse()                                                # newest first

    sightings = [{"ts": o["@timestamp"], "camera": o.get("camera"), "confidence": store._num(o.get("confidence")),  # noqa: SLF001
                  "occluded": bool(o.get("occluded")), "capture_id": o.get("capture_id")} for o in obs_docs]

    # every description, per camera, per capture — contradictions kept
    by_capture: dict[str, dict[str, dict]] = {}
    for o in obs_docs:
        text = store._text(o.get("raw_description"))                  # noqa: SLF001
        if not text:
            continue
        cam = by_capture.setdefault(o["capture_id"], {}).setdefault(o["camera"], {
            "camera": o["camera"], "confidence": store._num(o.get("confidence")),   # noqa: SLF001
            "occluded": bool(o.get("occluded")), "descriptions": []})
        cam["descriptions"].append({"text": text, "label": o.get("raw_label"), "attempt": o.get("label_attempt"),
                                    "model": o.get("vlm_model")})
    descriptions = []
    for capture_id, cams in by_capture.items():
        rows = [cams[c] for c in sorted(cams)]
        store._mark_unique_words(rows)                                # noqa: SLF001
        meta = capture_meta.get(capture_id) or {}
        labels = sorted({d["label"] for r in rows for d in r["descriptions"] if d["label"]})
        descriptions.append({"capture_id": capture_id, "ts": meta.get("ts") or min(
            (o["@timestamp"] for o in obs_docs if o["capture_id"] == capture_id), default=None),
            "commit_sha": meta.get("commit_sha"), "gate_pass": meta.get("gate_pass"),
            "labels": labels, "labels_disagree": len(labels) > 1, "cameras": rows})
    descriptions.sort(key=lambda d: d["ts"] or "", reverse=True)

    last = objs[-1] if objs else None
    lived = [o for o in objs if in_history(o["commit_sha"])]
    last_here = lived[-1] if lived else None
    zones: list[str] = []
    for o in objs:
        if o.get("zone") and o["zone"] not in zones:
            zones.append(o["zone"])
    branches = sorted({o["branch"] for o in objs if o.get("branch")})

    verdict = None
    if present_now is False:
        if last and not last_here:
            verdict = {"kind": "other_branch", "near_m": NEAR_M, "gone_after": None, "after": None, "watch": None,
                       "history": _history_stats(sightings), "cameras_that_saw_it": sorted({s["camera"] for s in sightings if s["camera"]}),
                       "last_sighting": sightings[-1] if sightings else None,
                       "headline": f"it exists on another branch ({', '.join(branches)})",
                       "rule": f"It was never committed on {head['branch'] or 'this branch'}: every commit it appears in is on "
                               f"{', '.join(branches)}, which is not in HEAD's history. It is not missing — it is somewhere else in the graph.",
                       "method": "Read from git ancestry (git rev-list HEAD) and the commits this object appears in."}
        elif last_here:
            # the commit after which it is gone: the one that records its removal, else the next one in HEAD's history
            after = sorted((e for e in commits.values() if store.when(e) > store.when(last_here)
                            and in_history(e["commit_sha"])), key=store.when)
            removal = next((e for e in after if object_id in (e.get("objects_removed") or [])), after[0] if after else None)
            gone = (None if not removal else {
                "commit_sha": removal["commit_sha"], "ts": removal["@timestamp"], "subject": removal.get("message"),
                "capture_id": removal.get("capture_id"), "branch": removal.get("branch"),
                "recorded_removal": object_id in (removal.get("objects_removed") or [])})
            verdict = await _verdict(object_id, last_here, gone, sightings)

    shown = last_here or last
    ev = commits.get((shown or {}).get("commit_sha")) or {}
    total_commits = len(commits)
    return {
        "source": source, "object_id": object_id, "class": (shown or {}).get("class"),
        "color": (shown or {}).get("color"), "extents": (shown or {}).get("extents"),
        "present_now": present_now,
        "head": {"sha": head["sha"], "branch": head["branch"], "from": head["from"], "indexed": bool(indexed)},
        "first_seen": (objs[0].get("first_seen") or objs[0].get("@timestamp")) if objs else (sightings[0]["ts"] if sightings else None),
        "last_seen": None if not shown else {
            "ts": shown.get("@timestamp"), "commit_sha": shown["commit_sha"], "branch": shown.get("branch"),
            "zone": shown.get("zone"), "pose": shown.get("pose"), "capture_id": shown.get("capture_id"),
            "subject": ev.get("message"), "in_head_history": bool(last_here)},
        "appearances": {"commits": len(objs), "of": total_commits or None, "sightings": len(sightings),
                        "truncated": len(sightings) >= MAX_OBSERVATIONS},
        "zones": zones, "branches": branches,
        "timeline": timeline, "descriptions": descriptions, "sightings": sightings,
        "verdict": verdict,
        "point": None if not shown or not shown.get("pose") else {
            "target_pose": shown["pose"], "zone": shown.get("zone"), "commit_sha": shown["commit_sha"],
            "at": "the object" if present_now else "the empty space where it was last seen"},
    }


async def _why_nothing() -> str:
    """An unknown id is a 404 either way — but "Elasticsearch answered and holds no room at all" is a
    different situation from a typo, and the person looking at the page should be told which."""
    try:
        docs, source = await store._find("room-objects", {}, size=1, label="object.any")   # noqa: SLF001
    except Exception:  # noqa: BLE001
        return ""
    if source == "elasticsearch" and not docs:
        return (" — Elasticsearch is reachable but room-objects is EMPTY there: the room has not been ingested "
                "(fixtures are only read when Elasticsearch is unavailable)")
    return ""


def _allowed() -> set[str]:
    return {c.strip() for c in os.getenv("WEB_ALLOWED_COMMANDS", "").split(",") if c.strip()}


@router.get("/api/object-life/{object_id}")
async def get_life(object_id: str):
    if not OBJECT_ID.match(object_id):
        return _error("bad_request", "object_id must look like mug_a1b2", 422)
    try:
        if obs is not None:
            with obs.span("object.life", object_id, object_id=object_id):
                return await life(object_id)
        return await life(object_id)
    except store.NotFound:
        return _error("not_found", f"no object {object_id}" + await _why_nothing(), 404)
    except Exception as e:  # noqa: BLE001
        return _upstream(e)


@router.post("/api/object-life/{object_id}/point", status_code=202)
async def point(object_id: str):
    """Plan "drive there and point". Moves nothing: no executor is connected."""
    if not OBJECT_ID.match(object_id):
        return _error("bad_request", "object_id must look like mug_a1b2", 422)
    try:
        data = await life(object_id)
    except store.NotFound:
        return _error("not_found", f"no object {object_id}", 404)
    except Exception as e:  # noqa: BLE001
        return _upstream(e)
    if not data["point"]:
        return _error("unreachable_pose", f"{object_id} has no recorded pose to point at", 409)
    job = {
        "job_id": "job_" + secrets.token_hex(2), "command": "point", "object_id": object_id,
        "target_pose": data["point"]["target_pose"], "zone": data["point"]["zone"], "pointing_at": data["point"]["at"],
        "estimated_s": SECONDS_TO_POINT,
        # `point` is not in WEB_ALLOWED_COMMANDS today; a connected executor must refuse it until an operator adds it
        "allow_listed": "point" in _allowed(),
        # the honest part: nothing is wired to a robot yet, and nothing was written anywhere
        "executor": "not_connected", "state": "queued (no executor connected)",
        "detail": "validated and planned only: no robot moved and room.git was not touched",
    }
    import events                                                     # the SSE hub (events.py)
    events.hub.publish("job", {"id": job["job_id"], "state": job["state"], "progress": 0, "command": "point",
                               "object_id": object_id, "executor": "not_connected"})
    return job


@router.get("/object/{object_id}", include_in_schema=False)
async def object_page(object_id: str):
    if not OBJECT_ID.match(object_id):
        return _error("not_found", "no such page", 404)
    return FileResponse(PAGES / "object.html", headers={"Cache-Control": "no-cache"})
