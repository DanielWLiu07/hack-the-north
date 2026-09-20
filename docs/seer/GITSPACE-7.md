# GITSPACE-7 — robot: grasp_slipped — gripper closed to 2mm, expected 78mm — target from a cloud captured mid-lean

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7741816842/ |
| seen | 1 times, 2026-09-19 05:31 → 2026-09-19 05:31 |
| level | error |
| culprit | `room revert HEAD [cap_live01]` |
| Seer run | `16891347` · COMPLETED · stopped at `root_cause` · 40 turn(s), 52 tool call(s) |
| collected | 2026-09-20T07:09:32+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

**Files Seer named:** `robot/capture.py`

## Root cause, in Seer's words

**Root cause identified.**

The issue is a **capture-gate bypass in the grasp pipeline**:

1. `cap_live01` was taken while the robot was actively leaning — `tilt_rate_max = 0.239 rad/s`, nearly 5× the 0.05 rad/s threshold
2. The tilted body caused depth cameras to measure `mug_a1b2`'s 3D geometry incorrectly → planner computed a 78mm gripper opening
3. The capture quality gate in `robot/capture.py` correctly issued `verdict: REJECT` (confirmed in SENTRY_STORY.md line 154)
4. **However**, `CaptureRig.last` is set unconditionally during each attempt — even rejected ones. If the `room revert HEAD` call stack catches `CaptureRejected` without re-raising, it silently falls through with the last rejected capture's distorted poses
5. The arm reached into empty space; gripper closed to 2mm

The fix must ensure `CaptureRejected` is always propagated (or the rejected capture's geometry is never passed to the planner), and `rig.last` should not be accessible as a fallback after a failed capture sequence.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-7.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
