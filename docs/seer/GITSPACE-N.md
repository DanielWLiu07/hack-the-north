# GITSPACE-N — AssertionError: no horizontal plane: cam_to_world_axes missing or applied twice, or the mount pitch is wrong

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7742632376/ |
| seen | 1 times, 2026-09-19 17:03 → 2026-09-19 17:03 |
| level | error |
| culprit | `room commit` |
| Seer run | `16890657` · COMPLETED · stopped at `root_cause` · 11 turn(s), 14 tool call(s) |
| collected | 2026-09-20T07:09:32+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

**Files Seer named:** `capture_to_recording.py`

## Root cause, in Seer's words

**Root cause identified.** Here's the summary:

The `room commit` fails because `fuse.assert_floor()` finds **no horizontal plane** in the world-frame point cloud from capture `cap_0006`. The transform chain (`cam_to_world_axes` + the mount matrix from `capture.json`) is supposed to rotate the camera's Y-down coordinate frame so the floor lands at z≈0. But when the mount `pitch_down_deg`/`height_m` are wrong — either left at placeholder values (36°/1.5m, explicitly called out as "not ours" in the `Mount` docstring) or stale from a remount — the entire cloud is tilted, and RANSAC finds no plane with a normal within 10° of Z-up.

**Why the leveller didn't save it**: `capture_to_recording.py`'s auto-levelling requires enough near-floor pixels in the first ~1.6m ahead of the robot. In a hallway scene, the camera looks down the corridor (mostly walls), so `near_floor()` returns `None` and the nominal (bad) mount values are frozen into `capture.json` unchanged.

**Fix**: Physically measure the actual mount pitch and height for this rig, update `capture.json` (or re-run `capture_to_recording.py` from a position with floor visible), and re-commit. The docs confirm: "re-capture, do not replay an old recording."

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-N.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
