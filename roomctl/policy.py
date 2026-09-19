"""Mess or decision? What the caretaker may touch (plan/roommate/03 §5).

Perception says what is where. Git says whether that is allowed: a change against `main` that nobody
decided is a mess; a change that an approved pull request put ON `main`, and the room has not caught up
with yet, is a decision the robot still owes. Whose zone it is decides whether it is our business at all.

    zones.<name>.policy   shared (default: today's rooms have no policy, and everything in them is shared)
                          personal: "your side of the room". Nothing in it is ever tidied or reported.
    zones.<name>.owner    who a personal zone belongs to; carried on chores, never used to decide.

    verdict               action, Tier A (the arm works)      Tier B / C (it does not)
    mess                  tidy                                 chore
    decision              tidy                                 chore
    personal              ignore                               ignore
    untracked_shared      lost_and_found                       chore
    untracked_personal    ignore                               ignore

A path outside zones/ (room.yaml, an anchor) is not an object: it is `personal`/`ignore` here, and
`room status` is where a person reads about it.
"""
from __future__ import annotations

from typing import Iterable, Literal

from roomctl.repo import Entry, GitError, Repo

Verdict = Literal["mess", "decision", "personal", "untracked_shared", "untracked_personal"]
Action = Literal["tidy", "chore", "ignore", "lost_and_found"]

TIERS = ("A", "B", "C")
MERGE_LOOKBACK = 20      # first-parent commits searched for approved merges the room has not caught up with


def zone_policy(room: dict, zone: str | None) -> str:
    z = (room.get("zones") or {}).get(zone or "") or {}
    p = str(z.get("policy") or "shared").lower()
    if p not in ("shared", "personal"):
        raise ValueError(f"room.yaml: zones.{zone}.policy must be shared or personal, not {p!r}")
    return p


def zone_owner(room: dict, zone: str | None) -> str | None:
    return ((room.get("zones") or {}).get(zone or "") or {}).get("owner")


def classify(entry: Entry, room: dict, head_sha: str | None, tier: str = "A",
             decided: Iterable[str] = ()) -> tuple[Verdict, Action]:
    """One `git status` entry -> (what it is, what to do about it).

    `decided`: object ids an approved pull request moved on `main` that the room still shows in their old
    place (decided_objects). `head_sha` is what the verdict was judged against: a caller that caches
    verdicts must drop them when it changes."""
    tier = str(tier).upper()
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {', '.join(TIERS)}, not {tier!r}")
    if entry.object_id is None:
        return "personal", "ignore"
    personal = zone_policy(room, entry.zone) == "personal"
    is_decided = entry.object_id in set(decided)
    if entry.untracked and not is_decided:           # (a decided object, still in its old zone, reads as untracked
        if personal:                                 # there once `main` has moved on: it is not lost property)
            return "untracked_personal", "ignore"
        return "untracked_shared", "lost_and_found" if tier == "A" else "chore"
    if personal:
        return "personal", "ignore"
    verdict: Verdict = "decision" if is_decided else "mess"
    return verdict, "tidy" if tier == "A" else "chore"


def decided_objects(repo: Repo) -> set[str]:
    """Objects an APPROVED pull request changed on this branch, whose file in the working tree (the room as
    last seen) is still exactly what it was before that merge. Approval moves `main` first and the robot
    second (roomctl/pr.py), so until the robot has moved it this is drift by design, not a mess."""
    out: set[str] = set()
    try:
        log = repo.git("log", "--first-parent", "--merges", f"-{MERGE_LOOKBACK}", "--format=%H%x00%B%x01").stdout
    except GitError:
        return out
    for chunk in log.split("\x01"):
        sha, _, body = chunk.strip().partition("\x00")
        if not sha or "Approved-by:" not in body:
            continue
        names = repo.git("diff", "--name-only", "--no-renames", "-z", f"{sha}^1", sha, check=False).stdout.split("\0")
        same: dict[str, bool] = {}                   # a move across zones is TWO paths: both must still be as before
        for path in filter(None, names):
            oid = Entry(path).object_id
            if oid is None:
                continue
            before = repo.git("show", f"{sha}^1:{path}", check=False)
            f = repo.path / path
            now = f.read_text() if f.is_file() else None
            same[oid] = same.get(oid, True) and now == (before.stdout if before.returncode == 0 else None)
        out |= {oid for oid, ok in same.items() if ok}
    return out
