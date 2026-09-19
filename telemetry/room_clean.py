"""telemetry/room_clean.py — the `room-clean` cron check-in: the room's CI badge, behind ONE switch.

    ROOM_CLEAN_CRON=1     the only switch. Off by default: the plan has ONE cron monitor, and until the user
                          deletes `watch-loop` in Sentry, a second slug would try to take a seat that isn't free.

Whatever knows whether the room is clean feeds it: the watch loop's RoomState (roomctl/watch.py, `clean`
after its 2-pass debounce, error while a confirmed mess exists; plan/roommate/03-interfaces.md §6 + §9) or,
until that loop runs, robot_sentry.IssueMirror's `git status` of room.git. Either way:

- `ok` when clean, `error` while it is not. Sent on every FLIP at once, and at least every 30 s otherwise,
  so a dead feeder misses its check-in and Sentry says so (interval 1 min, margin 2).
- The verdict is ALWAYS written to a small state file, switch on or off, so the dashboard's
  /api/room/ci can show `since` (when it last flipped) and the last check-in, and say whether any
  check-in was really sent. Stdlib only: web reads it with `read_state()`.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SLUG = "room-clean"
MONITOR = {"schedule": {"type": "interval", "value": 1, "unit": "minute"},
           "checkin_margin": 2, "max_runtime": 1, "timezone": "UTC"}
EVERY_S = 30.0


def enabled() -> bool:
    return os.getenv("ROOM_CLEAN_CRON", "").strip() == "1"


def state_path() -> Path:
    return Path(os.getenv("ROOM_CLEAN_STATE") or "~/.cache/gitspace/room-clean.json").expanduser()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_state() -> dict | None:
    """What the badge last decided, for the dashboard. None when nothing has fed it yet."""
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return None


def _write(doc: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".room-clean-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, p)


class RoomCleanBeat:
    """Call it with the room's state on every tick: `beat(clean)` or `beat(room_state)` (anything with
    `.clean` and optionally `.confirmed`). Returns the record it wrote."""

    def __init__(self, heartbeat: Callable[..., Any] | None = None, every_s: float = EVERY_S,
                 switch: Callable[[], bool] = enabled, clock: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic):
        if heartbeat is None:
            import obs
            heartbeat = obs.heartbeat
        self.heartbeat, self.every_s, self.switch, self.clock, self.mono = heartbeat, every_s, switch, clock, mono
        prior = read_state() or {}
        self.last: str | None = prior.get("last")
        self.since: str | None = prior.get("since")
        self.sent_at: str | None = prior.get("at")
        self._sent_mono: float | None = None

    def __call__(self, room: Any, source: str = "watch") -> dict:
        clean = room if isinstance(room, bool) else bool(getattr(room, "clean", None) if not isinstance(room, dict)
                                                         else room.get("clean"))
        confirmed = room.get("confirmed") if isinstance(room, dict) else getattr(room, "confirmed", None)
        status = "ok" if clean else "error"
        now = self.clock()
        if status != self.last:
            self.since = _iso(now)
        flipped = status != self.last
        self.last = status
        on = self.switch()
        due = self._sent_mono is None or self.mono() - self._sent_mono >= self.every_s
        sent = False
        if on and (flipped or due):
            try:
                self.heartbeat(SLUG, status, monitor_config=MONITOR)
                sent = True
                self.sent_at, self._sent_mono = _iso(now), self.mono()
            except Exception:  # noqa: BLE001 — a failed check-in is a MISSED check-in: Sentry says so itself
                pass
        doc = {"slug": SLUG, "enabled": on, "last": status, "at": self.sent_at, "since": self.since,
               "evaluated_at": _iso(now), "sent": sent, "source": source,
               "confirmed": len(confirmed) if isinstance(confirmed, (list, tuple)) else confirmed,
               "why_off": None if on else "ROOM_CLEAN_CRON is not 1: the Sentry cron seat is still `watch-loop`'s"}
        try:
            _write(doc)
        except OSError:
            pass
        return doc
