#!/usr/bin/env python3
"""Re-measure the two relevance floors against the room as it stands right now. Read-only.

    .venv/bin/python scripts/measure_relevance_floors.py

The floors are measurements of a ROOM, not constants: a bowl arriving mid-evening on 2026-09-19
moved "the trash" from 1.029 to 1.095, which was enough to carry it over the search floor. If the
object set changes before a demo, run this.

  queries.MIN_RELEVANCE          (1.05) search floor  -- below it, say "not here" instead of guessing
  caretaker.RESOLVE_MIN_SCORE    (1.20) acting floor  -- below it, never move a gripper unasked
  between them                          confirm band  -- ask "did you mean the mug?" and act on yes

Measured the way the CALLER calls it: no branch filter, because bridge/caretaker.py passes none
and therefore searches every branch. Measuring branch-scoped is how the search floor came to be
too low in production while sixteen tests stayed green.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "elastic"))

import setup_elastic as S           # noqa: E402
from queries import MIN_RELEVANCE, Queries   # noqa: E402

# Things the room holds. Every one must stay actionable, or the beat that shows it off breaks.
PRESENT = ["where are my keys", "the thing I cut paper with", "something to drink from",
           "something to write with", "where is the hammer", "my notebook", "the lamp",
           "the spiky plant", "my glasses case", "the tape measure", "coffee cup",
           "the whiteboard marker", "the scissors"]
# Things it does not. The destructive phrasings matter most: acting on one moves the wrong object.
ABSENT = ["pick up the trash", "the trash", "throw away the rubbish", "tidy up",
          "where is my phone", "my shoes", "the dog", "my umbrella", "my laptop charger"]
# Absent, but the nearest object is a DEFENSIBLE answer. Never raise a floor to catch these --
# that is how a threshold stops meaning anything.
NEAR_MISS = ["a bottle of water", "the television remote", "the banana"]

def _act_floor() -> float:
    """The acting floor, READ FROM the code that enforces it -- never copied.

    caretaker's is float(os.getenv("RESOLVE_MIN_SCORE", "1.20")), so it is env-overridable and a
    hardcoded 1.20 here would quietly misreport the confirm band the moment anyone set that
    variable. Copying a constant into the tool that checks the constant is how a measurement ends
    up agreeing with itself instead of with the system (elastic/NOTES.md, and four times over on
    2026-09-19 with voxel_key depth).
    """
    sys.path.insert(0, str(ROOT))
    from bridge.caretaker import MIN_ACT_SCORE
    return float(MIN_ACT_SCORE)


ACT_FLOOR = _act_floor()


def main() -> int:
    q = Queries(S.connect())

    def top(t):
        r = q.resolve_object(t, k=3)          # no branch: the production call
        return r["top_score"], r["matches"][0]["object_id"]

    groups = {"PRESENT": PRESENT, "ABSENT": ABSENT, "NEAR MISS": NEAR_MISS}
    scored = {g: sorted((top(t)[0], t, top(t)[1]) for t in ts) for g, ts in groups.items()}

    for name, rows in scored.items():
        print(f"\n{name}")
        for s, t, o in rows:
            band = ("refuse " if s < MIN_RELEVANCE else
                    "CONFIRM" if s < ACT_FLOOR else "act    ")
            print(f"  {s:7.3f}  {band}  {t:<30} -> {o}")

    worst_present = min(s for s, _, _ in scored["PRESENT"])
    best_absent = max(s for s, _, _ in scored["ABSENT"])
    print(f"\nworst present {worst_present:.3f}   best absent {best_absent:.3f}   "
          f"{'gap ' + format(worst_present - best_absent, '.3f') if worst_present > best_absent else 'OVERLAP'}")

    problems = []
    for s, t, o in scored["ABSENT"]:
        if s >= ACT_FLOOR:
            problems.append(f"'{t}' would be ACTED ON ({s:.3f} >= {ACT_FLOOR}) -> {o}")
    for s, t, o in scored["PRESENT"]:
        if s < MIN_RELEVANCE:
            problems.append(f"'{t}' would be REFUSED as absent ({s:.3f} < {MIN_RELEVANCE}) -> {o}")
    n_confirm = sum(1 for s, _, _ in scored["PRESENT"] if MIN_RELEVANCE <= s < ACT_FLOOR)
    print(f"{n_confirm} of {len(PRESENT)} real phrasings land in the confirm band (asked, not refused)")

    if problems:
        print("\nPROBLEMS -- the floors need moving:")
        for p in problems:
            print(f"  {p}")
        return 1
    print("\nboth floors still hold: nothing absent is acted on, nothing real is refused outright")
    return 0


if __name__ == "__main__":
    sys.exit(main())
