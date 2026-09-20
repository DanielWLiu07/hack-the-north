# GITSPACE-X — robot: telemetry_unfed — tilt_rate is null in every sample — the IMU reader (ROBOT_TELEMETRY_SOURCE) is not …

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7742706493/ |
| seen | 2 times, 2026-09-19 17:55 → 2026-09-19 17:57 |
| level | error |
| culprit | `—` |
| Seer run | `16890935` · COMPLETED · stopped at `root_cause` · 8 turn(s), 9 tool call(s) |
| collected | 2026-09-20T06:09:34+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

**Files Seer named:** `robot/server.py`

## Root cause, in Seer's words

**Root cause identified.**

The `telemetry_unfed` issue was triggered by the **robot server at `10.37.101.235:8080` going down** for ~25 seconds (13:57:08–13:57:28 UTC-4). During that window:

1. `robot_sentry_watch` couldn't reach the robot's `/telemetry` endpoint, so `sample_telemetry()` returned all-`None` tilt values.
2. The watcher's check (`all(v is None for v in s["tilt"])`) fired → `telemetry_unfed` issue raised.
3. The quality gate, per design in `robot/server.py`, **rejects every capture when `tilt_rate` is unmeasured** — this is intentional safety behavior, not a logic bug.

After recovery (~13:57:34), the server came back online (RTT jumped from 3003 ms back to normal), `tilt_rate` resumed, and the issue self-resolved (a `telemetry_unfed_recovered` event was also filed). The **6052 overruns** visible post-recovery suggest CPU saturation may have been a contributing factor to the server crash.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-X.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
