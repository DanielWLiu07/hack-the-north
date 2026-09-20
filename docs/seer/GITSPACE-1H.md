# GITSPACE-1H — robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no writer). First check, at the robot: `ta…

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7743379189/ |
| seen | 6 times, 2026-09-20 03:43 → 2026-09-20 05:58 |
| level | error |
| culprit | `—` |
| Seer run | `16890851` · COMPLETED · stopped at `root_cause` · 14 turn(s), 17 tool call(s) |
| collected | 2026-09-20T06:09:31+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

**Files Seer named:** `robot_sentry_watch.py`, `docs/35-robot-access.md`

## Root cause, in Seer's words

**Root cause identified:** The head stereo camera (2560×960 "USB Camera") is physically absent from the robot's USB bus. Here's the chain:

1. bbos's camera daemon loops with `FileNotFoundError: could not find camera 'USB Camera'` every 2 s — it can't open the device
2. Without `camera.head.rgb`, bbos's SLAM engine has no visual input and never publishes `slam.pose` (no writer)
3. `hub().slam()` returns `None` → `/healthz` reports `bbos.slam: false`
4. `robot_sentry_watch.py` detects `bb.get("slam") is False` and files the `bbos_silent` error

As documented in RUNBOOK §10 and `docs/35-robot-access.md`: *"This one fault explains `depth`, `points`, `rect`, `mapping.voxels` and `slam.pose` all being dead… It is not a software fix — plug the head camera in."*

The fix is physical: reseat the head stereo camera's USB cable (three QinHeng hubs + Realtek 4-port chain). Once reseated, the camera daemon self-recovers within 2 s, SLAM relocalizes, and `/healthz` shows `bbos.slam: true` within ~20 s.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-1H.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
