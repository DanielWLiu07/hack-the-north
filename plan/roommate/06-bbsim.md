# 06: bbsim, the fake Bracket Bot nav server (the dev harness)

**Why it comes first:** the robot is one machine, often unreachable, and slow (0.14 m/s). Six
tracks can't queue behind it. bbsim speaks **byte-compatible** bbapps/nav on loopback, driven
by `fake/scene_gen.py` scenes, so every track builds and tests with no hardware, and CI covers
the full loop.

**File:** `fake/bbsim.py` (new; fake/ is master's). Stdlib + `websockets` + numpy.

```
python fake/bbsim.py --scene clean_bench [--ws-port 18010 --api-port 18020] [--speed 0.14]
                     [--fail-nav 0.0] [--seed 0]
BB_HOST=127.0.0.1:18010 ROOM_NAV=bb ROOM_SOURCE=bb room watch
```

## What it serves (the same shapes as bbapps/nav)

| endpoint | behaviour |
|---|---|
| `ws /heavy` | the scene's coloured points (`scene_gen.scene_cloud` + colours) → 1.5 cm cells, **in a BB world frame offset from the room frame by a configurable `T`** (so registration is really exercised). A full copy in ≤ 40,000-cell chunks (type 4, `first=1` on the first), then type-5 deltas when the scene changes. **Honours the 8-message queue:** a slow reader gets a fresh full copy |
| `ws /ws` | `{"t": "state"}` at 8 Hz: `ready`, `rx, ry, rh` (integrating toward the goal at `--speed`, turning in place first), `status` (idle · navigating · reached · failed: … · waiting_for_drive), `running`, `waypoints`, `wp`, `gx, gy`, `path`, `map_gen` |
| `GET /health` · `/pose` · `/map` | `main_py.connected: true`; the pose (503 until "SLAM" is ready, ~1 s after start); the 2D grid of the area (3 cm, 1/2/0) + `freshness` per 0.5 m block |
| `ws /stream` | the /map payload once per second + the current job |
| `POST /map/rectangle` | defines the area in the robot's frame at call time; `sweep` drives lanes (sped up with `--fast`) and marks blocks seen |
| `POST /navigate` | drives to the target, 0.25 m arrival; a target inside an obstacle → the nearest free floor, and still `reached` (as documented) |
| `POST /patrol` | repeatedly picks the stalest block; runs until `/stop` or a new job |
| `POST /stop` | ends the job, `error: "cancelled"` |

**Freshness:** a block is seen when it's within 2 m and inside a 120° cone of the robot's
heading (the documented rule; walls are ignored, **but occluders in the scene do block the
voxels**, so `bb_source` + raycast must handle it).

## Test controls (sim only, under `/sim/*`)

| call | effect |
|---|---|
| `POST /sim/scene {name}` | swap to another scene (e.g. `messy_bench`): "a roommate moved things". Only cells inside the current view update (realistic staleness) |
| `POST /sim/move {object_id, x, y, yaw}` | move one object |
| `POST /sim/occlude {object_id, by}` | put an occluder between the robot and the object |
| `POST /sim/reset_map` | `map_gen += 1`, an empty full copy, then a rebuild |
| `POST /sim/fail {kind: nav\|drive_busy\|manual, once: true}` | inject the documented failure statuses |
| `POST /sim/arm {pick\|place, object_id, pose}` | the Tier-A arm stand-in: moves the object in the scene (so a fresh pass verifies it) |

## Acceptance
- The receiver from Bracket Bot's own docs, run unmodified against bbsim, rebuilds the map and
  prints the pose.
- `tests/test_bbsim_e2e.py` scenarios 1–7 (04).
- Recorded real-robot fixtures (`scripts/bb_record.py`) replay through the same client code as
  bbsim. **Where they differ, the real robot wins** and bbsim gets fixed.
