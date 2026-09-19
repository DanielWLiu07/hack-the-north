"""Chores: what the roommate could not put back itself (Tier B/C), as a small file beside the repo's objects.

    <room>/.git/gitspace-chores.json     travels with the room, is never tracked, never shows in `git status`

One chore per (object, kind of change). Opening the same one twice returns the open one; a chore closes when
a fresh pass no longer shows the change ("a person fixed it; the next patrol verified it"). The shape is the
dashboard's `GET /api/chores` row (plan/roommate/03 §8): id, object_id, zone, verdict, opened_at, status,
frame_url; plus type, owner, closed_at and what closed it.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FILE = "gitspace-chores.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _path(repo) -> Path:
    return Path(repo.path) / ".git" / FILE


def _load(repo) -> dict:
    try:
        d = json.loads(_path(repo).read_text())
        return d if isinstance(d, dict) and isinstance(d.get("chores"), list) else {"next": 1, "chores": []}
    except (OSError, ValueError):
        return {"next": 1, "chores": []}


def _save(repo, d: dict) -> None:
    p = _path(repo)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".chores-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, p)


def list_chores(repo, status: str | None = None) -> list[dict]:
    rows = _load(repo)["chores"]
    return [c for c in rows if status is None or c["status"] == status]


def open_chore(repo, change: dict, at: str | None = None) -> tuple[dict, bool]:
    """(the chore, whether it is new). `change` is one RoomState.confirmed row."""
    d = _load(repo)
    for c in d["chores"]:
        if c["status"] == "open" and c["object_id"] == change["object_id"] and c["type"] == change["type"]:
            return c, False
    c = {"id": f"chore-{d['next']}", "object_id": change["object_id"], "zone": change.get("zone"),
         "type": change["type"], "verdict": change["verdict"], "owner": change.get("owner"),
         "opened_at": at or _now(), "status": "open", "closed_at": None, "closed_by": None, "frame_url": None}
    d["next"] += 1
    d["chores"].append(c)
    _save(repo, d)
    return c, True


def close_chore(repo, chore_id: str, by: str = "rescan", at: str | None = None) -> dict | None:
    d = _load(repo)
    for c in d["chores"]:
        if c["id"] == chore_id and c["status"] == "open":
            c.update(status="closed", closed_at=at or _now(), closed_by=by)
            _save(repo, d)
            return c
    return None
