# GITSPACE-1B — robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no writer) — camera/slam/mapping daemons n…

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7743078588/ |
| seen | 5 times, 2026-09-19 23:01 → 2026-09-20 03:41 |
| level | error |
| culprit | `—` |
| Seer run | `16890882` · COMPLETED · stopped at `root_cause` · 7 turn(s), 8 tool call(s) |
| collected | 2026-09-20T06:09:32+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

**Files Seer named:** `robot_sentry_watch.py`

## Root cause, in Seer's words

**Root cause identified:** The head stereo camera (`cam_head`, configured to match a device with `card_substr: 'USB Camera'`, 2560×960 @ 60 Hz) has physically dropped off the USB bus. Only the two arm wrist cameras (`icspring camera`) are enumerated. As a result:

- The `bbos` camera daemon loops forever with `FileNotFoundError: could not find camera 'USB Camera'`
- `slam.pose`, `mapping.voxels`, `camera.depth`, `camera.head.jpeg` all have **NO WRITER** — they are all downstream of the head stereo pair
- `robot.server` reports `cameras: []` and `bbos.slam: false`
- `robot_sentry_watch.py` detects `bb.get("slam") is False` → fires `bbos_silent`
- All capture endpoints return 503 until the camera is physically reseated

This is a **hardware issue** (USB cable/hub loose or disconnected), not a software bug. The fix is to reseat the head stereo camera's USB cable at the robot.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-1B.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
