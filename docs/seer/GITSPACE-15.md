# GITSPACE-15 — robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bbos published no new camera.head.jpeg w…

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7743047616/ |
| seen | 19 times, 2026-09-19 22:31 → 2026-09-20 05:07 |
| level | error |
| culprit | `—` |
| Seer run | `16890768` · COMPLETED · stopped at `root_cause` · 7 turn(s), 10 tool call(s) |
| collected | 2026-09-20T07:09:27+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

**Files Seer named:** `robot/bbos.py`

## Root cause, in Seer's words

**Root cause: the head stereo camera is physically off the USB bus.**

The bbos camera daemon on the robot is configured to find a device with card name `'USB Camera'` at 2560×960@60Hz, but the only devices enumerated on the USB bus are the two arm wrist cameras (`icspring camera`, `/dev/video0-3`). Because no device matches, the daemon loops with `FileNotFoundError` every 2 s and never writes to the `camera.head.jpeg` shared-memory topic — leaving it with **NO WRITER**.

When `robot/bbos.py` tries to read that topic, the newest frame is perpetually stale (or absent), triggering the `"bbos published no new camera.head.jpeg within 2.0 s"` timeout. All downstream systems (SLAM, depth, rect, voxels) also fail since they all derive from `camera.head.jpeg`.

The fix is physical: reseat the head camera's USB cable on the robot. Once connected, the daemon reopens automatically within 2 s, and `/healthz` recovers within ~20 s without restarting the server.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-15.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
