#!/usr/bin/env python3
"""scripts/room_clean_beat.py — the room's CI badge, fed from the REAL room.git.

    ROOM_CLEAN_CRON=1 .venv/bin/python scripts/room_clean_beat.py            # every 30 s, forever
    .venv/bin/python scripts/room_clean_beat.py --once                       # one verdict, recorded only

`ok` while room.git's working tree is clean, `error` while it is not (docs/29, plan/roommate/03 §9).
The check-in goes to Sentry's `room-clean` cron monitor only with ROOM_CLEAN_CRON=1; without it the
verdict is still recorded for the dashboard (`/api/room/ci`), and nothing is sent.

It watches ONE room: the real one. A sim room (~/.cache/gitspace/rooms/…, anything the demo seeds)
is refused, because the badge in the pitch is the state of the actual room — telemetry/room_clean.py
is the same feeder the watch loop will use once it runs.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SIM_MARKERS = ("/.cache/gitspace/rooms/", "sim-demo", "/tmp/")


def real_room(raw: str | None) -> Path:
    p = Path(raw or ROOT / "room.git").expanduser()
    p = p if p.is_absolute() else (ROOT / p).resolve()
    where = str(p)
    if any(m in where for m in SIM_MARKERS):
        raise SystemExit(f"refusing to beat for a simulated room ({where}): the badge is the REAL room's")
    if not (p / "HEAD").exists() and not (p / ".git").exists():
        raise SystemExit(f"no room repository at {where}")
    return p


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--every", type=float, default=30.0, help="seconds between check-ins (monitor: 1 min, margin 2)")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()

    import obs
    from robot_sentry import room_is_clean
    from telemetry.room_clean import RoomCleanBeat, enabled
    room = real_room(os.getenv("ROOM_GIT_PATH"))
    obs.init("laptop")
    beat = RoomCleanBeat(every_s=0 if a.once else a.every)
    print(f"room-clean: watching {room} every {a.every:g}s · sending={enabled()} "
          f"(ROOM_CLEAN_CRON=1 to send) · state file {os.getenv('ROOM_CLEAN_STATE') or '~/.cache/gitspace/room-clean.json'}",
          flush=True)
    while True:
        clean = room_is_clean(room)
        if clean is None:
            print(f"{time.strftime('%H:%M:%S')} room.git unreadable — no verdict", flush=True)
        else:
            doc = beat(clean, source=f"git status ({room.name})")
            print(f"{time.strftime('%H:%M:%S')} {doc['last']:5} sent={doc['sent']} since={doc['since']}", flush=True)
        if a.once:
            return 0
        time.sleep(a.every)


if __name__ == "__main__":
    raise SystemExit(main())
