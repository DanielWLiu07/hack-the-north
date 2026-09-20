# GITSPACE-1K — robot: map_reset — the robot's map generation changed 3971697493 -> 1614467593 with no push from us: SLAM re…

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7743510975/ |
| seen | 1 times, 2026-09-20 06:01 → 2026-09-20 06:01 |
| level | warning |
| culprit | `—` |
| Seer run | `16891324` · COMPLETED · stopped at `root_cause` · 10 turn(s), 15 tool call(s) |
| collected | 2026-09-20T07:09:32+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

> ## ALREADY FIXED
>
> Seer says the fix has already landed — *“The hardware fix (reseating the USB cable) was already identified in GITSPACE-1H. The deeper software gap is that bbos's relocalization failure counter keeps advancing even while the camera is completely offline — pausing the counter during a `bbos_silent` state would prevent the autonomous map reset in this scenario.”*
>
> **Resolve the issue in Sentry rather than editing the file.** Check the commit it names first.

**Files Seer named:** `constants.py`, `slam/daemon.py`, `robot_sentry_watch.py`

## Root cause, in Seer's words

**Root cause identified.**

The `map_reset` event is the end of a 2-hour domino chain:

1. **Head stereo camera dropped off the USB bus** (issues [GITSPACE-1H](https://na-alh.sentry.io/issues/7743510975/?referrer=seer.agent.in-chat-link) / GITSPACE-1B, active 03:41–05:58 UTC). Without camera frames, `slam.pose` had no writer.

2. **SLAM kept trying to relocalize** against the saved venue map (`slam.bbmap`), accumulating failure counts — the RUNBOOK records 1,592 attempts at 05:51Z and 1,716 at 05:58Z (~18/min).

3. **After exactly 2,000 failures** (`boot_reloc_max_attempts = 2000` in `constants.py`), `slam/daemon.py` autonomously called `fresh_map(archive=True)`, renaming `slam.bbmap` → `slam.bbmap.failed-<stamp>` and starting a new map with a new origin.

4. **Map generation changed** (3971697493 → 1614467593). `robot_sentry_watch.py` polls `/map/gen` every 60 seconds, detected the change on the same `boot_id`, and fired the `map_reset` warning — all registrations measured against the old generation are now stale.

The hardware fix (reseating the USB cable) was already identified in GITSPACE-1H. The deeper software gap is that bbos's relocalization failure counter keeps advancing even while the camera is completely offline — pausing the counter during a `bbos_silent` state would prevent the autonomous map reset in this scenario.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-1K.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
