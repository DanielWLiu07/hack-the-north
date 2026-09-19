"""Wall-clock -> version time: "put it back the way it was before dinner" (plan/roommate/03 §12).

Two steps, kept apart so each is testable:
    parse_when(text)             a phrase -> an aware datetime (deterministic; free-form language
                                 beyond these forms is the intent service's job, never guessed here)
    commit_before(repo, when)    the last commit on the branch STRICTLY before that time: from
                                 Elasticsearch's room-events (ES|QL, the Elastic showpiece) with git's
                                 own history as the fallback, and the answer says which one it used.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from roomctl.repo import GitError, Repo

# The day's landmarks a roommate means, in local time.
NAMED = {"breakfast": 8, "morning": 9, "this morning": 9, "lunch": 12, "noon": 12, "lunchtime": 12,
         "afternoon": 14, "this afternoon": 14, "dinner": 18, "supper": 18, "evening": 18,
         "this evening": 18, "tonight": 21, "last night": 21, "bedtime": 22, "midnight": 0}
UNITS = {"minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400}


def _local(now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone()


def parse_when(text: str, now: datetime | None = None) -> datetime:
    """'before dinner', 'dinner', '18:00', '6pm', '2 hours ago', 'yesterday lunch', ISO 8601.
    A named time or a clock time later than now means that time yesterday. ValueError when the
    phrase can't be placed: the caller says so instead of guessing."""
    now = _local(now)
    t = " ".join(text.lower().strip().split())
    t = re.sub(r"^(before|until|at|around|by)\s+", "", t)
    t = re.sub(r"^(the way it was|how it was)\s+(before\s+)?", "", t)
    day = 0
    if t.startswith("yesterday"):
        day, t = -1, t.removeprefix("yesterday").strip() or "midnight"
    try:
        dt = datetime.fromisoformat(t.upper() if "t" in t and "-" in t else t)
        return dt if dt.tzinfo else dt.astimezone()
    except ValueError:
        pass
    if m := re.fullmatch(r"(\d+(?:\.\d+)?)\s*(minute|min|hour|hr|day)s?\s+ago", t):
        return now - timedelta(seconds=float(m.group(1)) * UNITS[m.group(2)])
    hour = minute = None
    if m := re.fullmatch(r"(\d{1,2}):(\d{2})", t):
        hour, minute = int(m.group(1)), int(m.group(2))
    elif m := re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", t):
        hour, minute = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0), int(m.group(2) or 0)
    elif t in NAMED:
        hour, minute = NAMED[t], 0
        if t == "last night":
            day = -1
    if hour is None or not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"can't place {text!r} in time")
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=day)
    if day == 0 and at > now:
        at -= timedelta(days=1)                       # "before dinner" at 14:00 means yesterday's dinner
    return at


def commit_before(repo: Repo, when: datetime, branch: str | None = None, es=None) -> dict:
    """The last commit on `branch` strictly before `when`: {sha, message, at, source}."""
    branch = branch or repo.branch()
    if when.tzinfo is None:
        raise ValueError("`when` must carry a timezone")
    if es is not None:
        try:
            from roomctl.publish import _import
            Queries = _import("elastic", "queries").Queries   # elastic/queries.py
            row = Queries(es).commit_at(when, branch, strictly_before=True)
            if row:
                return {"sha": row["commit_sha"], "message": row.get("message"), "at": row.get("@timestamp"),
                        "branch": branch, "source": "elasticsearch"}
        except Exception as e:  # noqa: BLE001 — history still exists in git: fall back, and say so
            fallback_why = f"{type(e).__name__}: {e}"
        else:
            fallback_why = "no commit event before that time in room-events"
    else:
        fallback_why = "no Elasticsearch client"
    cutoff = (when - timedelta(seconds=1)).astimezone(timezone.utc).isoformat()
    sha = repo.git("rev-list", "-1", f"--before={cutoff}", branch, check=False).stdout.strip()
    if not sha:
        raise GitError(f"no commit on {branch} before {when.isoformat(timespec='minutes')}")
    info = repo.git("log", "-1", "--format=%s%x1f%cI", sha).stdout.strip().split("\x1f")
    return {"sha": sha, "message": info[0], "at": info[1], "branch": branch, "source": f"git ({fallback_why})"}
