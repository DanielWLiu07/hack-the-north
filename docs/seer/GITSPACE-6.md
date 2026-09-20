# GITSPACE-6 — robot: fell_over — balanced went 0; pitch 0.350199, peak tilt_rate 0.698854

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7741552137/ |
| seen | 10 times, 2026-09-19 01:21 → 2026-09-19 23:21 |
| level | error |
| culprit | `—` |
| Seer run | `16890802` · COMPLETED · stopped at `root_cause` · 26 turn(s), 34 tool call(s) |
| collected | 2026-09-20T06:09:31+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

**Files Seer named:** `robot/NOTES.md`, `robot/bbos.py`

## Root cause, in Seer's words

Here's the root cause analysis for GITSPACE-6:

**The robot falls because the balance controller loses effectiveness due to undervoltage, while the `drive.state.iq` bbos bug hides all evidence of motor activity.**

Key findings:

1. **`motor_current_l/r = 0.0` throughout the entire fall** — this is a confirmed bbos firmware bug: `drive.state.iq` always reads 0.0 even when the motors are applying torque (`robot/NOTES.md` line 49, RUNBOOK §0). `robot/bbos.py` line 209 reads `d["iq"]` for motor current, which is the wrong field.

2. **Physical cause of the fall** — `bbos.py` docstring (lines 44–47) explicitly documents that on 2026-09-19, the robot experienced **undervoltage causing 4 hard reboots**, with symptoms including the IMU dropping from 97 Hz to 22 Hz. At 1/4 normal IMU frequency, the balance controller cannot react quickly enough to tilt disturbances — the pitch just keeps rising steadily at ~0.6 rad/s until it exceeds the 20° threshold.

3. **Zero-current telemetry masks the true state** — because `motor_current = 0.0` always (bbos bug), there's no way to distinguish "motors working but balance controller degraded" from "motors completely dead" purely from this telemetry.

**Fix paths:** (1) In `robot/bbos.py` line 209, switch from `d["iq"]` to the `torque` field as NOTES.md suggests; (2) The already-added `drive.status.voltage` monitoring on `/healthz` should be wired to a Sentry alert so undervoltage is caught before it degrades the IMU and causes falls.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-6.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
