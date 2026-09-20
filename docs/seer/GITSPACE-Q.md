# GITSPACE-Q — OverflowError: Python integer -1 out of bounds for uint16

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7742645149/ |
| seen | 4 times, 2026-09-19 17:11 → 2026-09-19 17:15 |
| level | error |
| culprit | `capture` |
| Seer run | `16890656` · COMPLETED · stopped at `root_cause` · 8 turn(s), 9 tool call(s) |
| collected | 2026-09-20T07:09:29+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

**Files Seer named:** `test_robot_bbos.py`

## Root cause, in Seer's words

## Root Cause

The crash happens in `FakeReader.ready()` in `test_robot_bbos.py` line 269:

```python
n = world.n - (world.skew if self.name == "camera.depth" else 0)
# ...
np.where(world.conf > 0, world.conf + n, 0).astype(np.uint16)
```

**The chain:**
1. `test_depth_is_never_paired_with_a_colour_frame_it_does_not_belong_to` sets `world.skew = 1` before any `tick()` call, so `world.n = 0`.
2. The background hub thread starts and immediately polls `_read_rect` → `rd.ready()` for `camera.depth`.
3. `n = 0 - 1 = -1`.
4. `world.conf` is a `np.uint16` array (initialized in `RectWorld.__init__`). Adding Python int `-1` to it triggers numpy's strict bounds check on newer numpy versions: **OverflowError: Python integer -1 out of bounds for uint16**.

The `FakeReader` has no guard to return early or skip when `n < 0` (which indicates depth is "behind" and no valid frame exists yet). The test was designed to verify that mismatched frames aren't paired, but the helper crashes before that logic is even reached.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-Q.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
