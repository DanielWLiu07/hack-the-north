# GITSPACE-W — robot: camera_unavailable — cam0: preview unavailable (HTTP 503, no valid JPEG)

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7742706378/ |
| seen | 2 times, 2026-09-19 17:55 → 2026-09-19 17:57 |
| level | error |
| culprit | `—` |
| Seer run | `16890909` · COMPLETED · stopped at `root_cause` · 21 turn(s), 26 tool call(s) |
| collected | 2026-09-20T06:09:33+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

**Files Seer named:** `robot_sentry_watch.py`

## Root cause, in Seer's words

**Root cause identified.**

The `camera_unavailable` error (HTTP 503) is a downstream symptom of the robot's Pi CPU being critically overwhelmed:

- **~97% of 50 Hz telemetry ticks were skipped** in 30 seconds (+1,465 overruns), indicating near-total CPU saturation
- This caused `robot.server` to crash/hang — port 8080 was unreachable for ~25 seconds (`robot_server_down`)
- When the server recovered, RTT was 3,003 ms (still saturated), and the camera preview cache was empty or > 1 s stale
- The `/camera/cam0.jpg` endpoint returned HTTP 503 per its documented contract ("no frame yet, or newest is > 1 s old")
- `robot_sentry_watch.py` caught this and filed the `camera_unavailable` error to Sentry

The underlying overload likely stems from RealSense depth processing, SLAM, and the FastAPI server all competing for CPU on the same Raspberry Pi in hardware mode. Profiling is needed to identify which workload to optimize or offload.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-W.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
