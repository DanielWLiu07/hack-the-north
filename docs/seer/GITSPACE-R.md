# GITSPACE-R — AssertionError: lowest horizontal plane is at z=-0.055 m, not 0: check the mount height and pitch, or this c…

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7742662415/ |
| seen | 2 times, 2026-09-19 17:23 → 2026-09-20 03:35 |
| level | error |
| culprit | `room commit` |
| Seer run | `16890655` · COMPLETED · stopped at `root_cause` · 8 turn(s), 11 tool call(s) |
| collected | 2026-09-20T06:09:33+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

**Files Seer named:** `capture_to_recording.py`

**Commits Seer named:** `49a10adb`

## Root cause, in Seer's words

**Root cause identified.**

The recording `cap_0019` was written to disk on 2026-09-19 at ~17:23 UTC using the *first version* of `capture_to_recording.py` (commit `49a10adb`), which hardcoded bbos's own nominal mount values:
```
MOUNT = {"pitch_down_deg": 33.0, "height_m": 1.55}
```

These values are correct for bbos's own rectified frame but **wrong for this project's rectification** (`stereo_calibration_fisheye.yaml`). The code itself documents that with pitch 33°/1.55 m, the reconstructed floor ends up "~5 cm low" — exactly matching the observed `floor_z = -0.055 m`.

The correct mount (pitch 38.1°/height 1.59 m) was measured empirically from three captures that same day and committed later, but `cap_0019`'s `capture.json` was already saved to `~/.cache/gitspace/rooms/hallway-test.recordings/cap_0019/` with the wrong values. Every `room commit` that re-processes that recording reads the stale mount from disk, transforms the point cloud with the wrong geometry, and fails `assert_floor`'s ±5 cm tolerance.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-R.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
