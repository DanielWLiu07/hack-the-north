# GITSPACE-1E — robot: housebot_failed — robot API failed; delivery status may be unknown: <urlopen error [Errno 61] Connect…

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7743164380/ |
| seen | 34 times, 2026-09-20 00:27 → 2026-09-20 06:40 |
| level | error |
| culprit | `point marker_c3d4` |
| Seer run | `16891270` · COMPLETED · stopped at `root_cause` · 9 turn(s), 12 tool call(s) |
| collected | 2026-09-20T07:09:27+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

**Files Seer named:** `scripts/run_robot_api.py`, `andrew/README.md`

## Root cause, in Seer's words

**Root cause identified:** The robot adapter service (`scripts/run_robot_api.py`, expected on port 8765) is not running. 

Here's the full chain:
- `point marker_c3d4` (and other objects) → dispatched to Housebot Edge at `:8780` (succeeds)
- Housebot Edge's `HTTPRobotAdapter._request()` tries `POST $HOUSEBOT_ROBOT_BASE_URL/v1/actions` (`:8765`)
- OS immediately returns `ECONNREFUSED` — nothing is listening on that port
- `urllib.error.URLError` is caught in `execute()` → `ActionResult(UNKNOWN, "robot API failed; delivery status may be unknown: …")` — captured by Sentry as `housebot_failed`

This is **not a code bug** — the robot adapter process simply isn't running. Per the [README](andrew/README.md), it must be started separately with `python3 scripts/run_robot_api.py --mock --host 0.0.0.0 --port 8765`.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-1E.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
