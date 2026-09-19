"""objdiff.py — the object-level diff of two commits, for ANY room repository.

This is `graph_api._ops` lifted out of room.git so the scene instances under
ROOM_LIVE_DIR (~/.cache/gitspace/rooms/<instance>/) can use the SAME diff. Both repos
have the same layout — `zones/<zone>/<object_id>.yaml` — so there is no second
implementation to keep in step, and there must not be one.

It takes its git from the caller, because the two callers reach different repositories:

    git(*args) -> str     a read-only `git` in ONE repo, '' or raising on failure
    show(spec) -> str     the text of one blob, e.g. "<sha>:zones/desk/mug_a1b2.yaml"

IDENTITY IS NOT DECIDED HERE, and must never be. perception/associate.py is the
association pass (Hungarian on distance, hard-gated at 1.5 m, plus a hybrid
Elasticsearch search that recognises an object that left and came back), and
roomctl/state.py carries `class`, `color`, `first_seen` and `extents` forward from the
first sight of an object and never re-derives them. So:

  * the same `object_id` at two commits MEANS the same physical object;
  * a new `object_id` means the association pass minted one, i.e. genuinely new;
  * a `first_seen` that predates the commit you are diffing FROM means the room had
    seen it before — associate.py's `returned` row, reusing the old id.

All this module does is carry those three fields out so a UI can say so. It guesses
nothing: a record with no `first_seen` comes back as None, never as a date.
"""
from __future__ import annotations

import math
from typing import Any, Callable

from room import _object_of, _pose, _record        # pure parsers: a path, a pose dict, one YAML record

Git = Callable[..., str]
Show = Callable[[str], str]

# `moved` first, then the biggest move first: the UI groups by zone and keeps this order,
# so the zone where the most actually happened comes first.
ORDER = {"moved": 0, "changed": 1, "removed": 2, "added": 3}


def identity(rec: dict) -> dict:
    """The three fields that are carried forward, never re-measured (roomctl/state.py)."""
    return {"class": rec.get("class"), "color": rec.get("color"), "first_seen": rec.get("first_seen")}


def ops(git: Git, show: Show, a: str, b: str) -> list[dict]:
    """Object-level ops that turn the room at commit `a` into the room at commit `b`."""
    out = git("diff-tree", "-r", "--name-status", "--no-renames", a, b, "--", "zones")
    found: list[dict[str, Any]] = []
    for line in (out or "").splitlines():
        status, _, path = line.partition("\t")
        object_id, zone = _object_of(path)
        if not object_id:
            continue
        if status.startswith("A"):
            rec = _record(show(f"{b}:{path}") or "")
            found.append({"op": "added", "object_id": object_id, **identity(rec), "zone": zone, "to": _pose(rec)})
        elif status.startswith("D"):
            rec = _record(show(f"{a}:{path}") or "")
            found.append({"op": "removed", "object_id": object_id, **identity(rec), "zone": zone, "from": _pose(rec)})
        else:
            before, after = _record(show(f"{a}:{path}") or ""), _record(show(f"{b}:{path}") or "")
            p0, p1 = _pose(before), _pose(after)
            op: dict[str, Any] = {"op": "moved", "object_id": object_id, **identity(after),
                                  "zone": zone, "from": p0, "to": p1}
            if p0 and p1:
                op["delta_m"] = round(math.dist((p0["x"], p0["y"], p0["z"]), (p1["x"], p1["y"], p1["z"])), 3)
                if "yaw" in p0 and "yaw" in p1:
                    op["delta_yaw_deg"] = round(((p1["yaw"] - p0["yaw"] + 180) % 360) - 180, 1)
                if op["delta_m"] == 0 and not op.get("delta_yaw_deg"):
                    op["op"] = "changed"    # the record changed, the object did not move
            found.append(op)
    # an object that changed ZONE changed path (zones/<zone>/<id>.yaml): git shows a delete and
    # an add, but for the room — and for the arm — that is one move
    gone = {o["object_id"]: o for o in found if o["op"] == "removed"}
    for o in [o for o in found if o["op"] == "added" and o["object_id"] in gone]:
        was = gone[o["object_id"]]
        found.remove(was)
        o.update(op="moved", from_zone=was["zone"], **{"from": was["from"]})
        if o["from"] and o["to"]:
            o["delta_m"] = round(math.dist(*[(p["x"], p["y"], p["z"]) for p in (o["from"], o["to"])]), 3)
    return sorted(found, key=lambda o: (ORDER[o["op"]], -(o.get("delta_m") or 0), o["object_id"]))


def summary(rows: list[dict]) -> dict:
    return {k: sum(1 for o in rows if o["op"] == k) for k in ("moved", "removed", "added", "changed")}


def objects_at(git: Git, show: Show, sha: str) -> list[dict]:
    """Every object the room held at one commit — what "3 of 11 moved" is counted against."""
    out = []
    for path in (git("ls-tree", "-r", "--name-only", sha, "--", "zones") or "").splitlines():
        object_id, zone = _object_of(path)
        if not object_id:
            continue
        rec = _record(show(f"{sha}:{path}") or "")
        out.append({"object_id": object_id, "zone": zone, **identity(rec), "pose": _pose(rec),
                    "extents": rec.get("extents") if isinstance(rec.get("extents"), dict) else None})
    return sorted(out, key=lambda o: o["object_id"])
