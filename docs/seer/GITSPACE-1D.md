# GITSPACE-1D — Cron failure: room-clean

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7743117071/ |
| seen | 142 times, 2026-09-19 23:40 → 2026-09-20 20:27 |
| level | error |
| culprit | `—` |
| Seer run | `16898174` · COMPLETED · stopped at `root_cause` · 10 turn(s), 11 tool call(s) |
| collected | 2026-09-20T20:42:37+00:00 by `scripts/seer_sweep.py` |

**Files Seer named:** `robot_sentry.py`, `scripts/room_clean_beat.py`

## Root cause, in Seer's words

**Root cause:** The `room-clean` cron monitor is configured with a 1-minute interval and a 2-minute margin (3-minute miss window). Its heartbeats are only sent when:
1. `ROOM_CLEAN_CRON=1` is set in the environment, and
2. The feeder process (`robot_sentry.py mirror` or `scripts/room_clean_beat.py`) is actively running

The 28 auto-resolutions over ~21 hours show that check-ins succeed intermittently — the feeder is being run manually/occasionally and then stops. The intended long-term feeder (the `roomctl` watch-loop) is disabled. Without a process supervisor keeping the feeder alive continuously, any gap >3 minutes triggers a missed check-in failure.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-1D.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
