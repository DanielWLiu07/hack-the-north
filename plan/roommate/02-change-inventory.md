# 02: Change inventory (file by file)

Sizes: **S** < 1 h · **M** 1–3 h · **L** > 3 h. Risk is what breaks if it goes wrong.
**Nothing below has been done.** Every change is additive and behind a flag (`05-rollout.md`)
unless it says otherwise.

## roomctl/ (master)

| file | change | why | size | risk |
|---|---|---|---|---|
| **NEW** `roomctl/frames.py` | the only room ⇄ BB-world conversions: points, poses, headings (`h = θ + φ_room − π/2`), the robot-relative frames (`area`, `robot`) | one tested place for the trap that makes a correct plan face the wrong way | S | high if wrong, so golden tests |
| **NEW** `roomctl/registration.py` | estimate `T_bb←room` (SE(2) + dz) from tag sightings + the live pose; a two-point-teach fallback; invalidate on `map_gen` change; persisted in `.git/gitspace/registration.json` (local, **never committed**) | history stays in the tag frame while BB's map can reset | M | high: every pose depends on it |
| **NEW** `roomctl/bb_nav.py` | the client: `/heavy` voxel mirror (types 4/5, `first=1` rebuild, dict by cell), `/ws` state (ready, pose, status, `map_gen`), `:8020` jobs (rectangle, navigate, patrol, stop, pose, health, map, stream); auto-reconnect; `BBNavRobot` implementing the executor's `Robot.drive` + `pose()` | the robot's nav stack is our driver and our eyes | L | medium: protocol decode, keeping up with the stream |
| **NEW** `roomctl/policy.py` | classify each change: `mess` / `decision` / `personal` / `untracked` → `tidy` / `chore` / `ignore` / `lost_and_found`, from zones (`policy: shared\|personal`, `owner`) and PR history | the line between a mess and a decision | S | low |
| **NEW** `roomctl/watch.py` | the watch loop (01 §1): patrol control, fresh-block scheduling, two-pass debounce, the verdict → SSE events, the Sentry check-in, chores, tidy jobs | continuous `git status` | M | medium: timing, debounce |
| **NEW** `roomctl/pr.py` | `propose(object_id, zone)` (a free spot via the executor's staging search) → a commit on `pr/<n>-<slug>`; list, approve (merge into main), close | the intent beat | M | low: git plumbing we already have |
| `roomctl/cli.py` | new verbs: `watch`, `pr open\|list\|approve\|close`, `chores`, `why <commit>`; `restore --before "<time>"` (ES\|QL `commit_at`); `make_robot` gains `bb`; `scan()` gains the `bb:<host>` source; flags `ROOM_NAV`, `ROOM_SOURCE` | the user surface of all of the above | M | medium: 20 CLI tests must stay green |
| `roomctl/executor.py` | `Robot` protocol + `pose()`; `route()` takes a costmap from BB's grid; `base_pose_for(..., arrive_tol=0.25)` keeps the margin; `execute()` reads the actual pose after each drive and aims the arm from it; a `verify(spots)` hook (viewpoint → fresh pass); expose the staging search for `pr.propose` | the tidy loop on the real robot | M | medium: executor tests |
| `roomctl/repo.py` | `load_room()`/`room_yaml()` read optional `zones.<z>.policy`, `zones.<z>.owner`, `lost_and_found` (alias of `bin`), `nav.area`; the defaults keep today's rooms valid | policy data, versioned with the room | S | low: backward compatible |
| `roomctl/publish.py` | write `room-events` for `chore_opened/closed`, `pr_opened/merged`, `tidy` (existing fields only) | the apartment's memory | S | low |
| `roomctl/state.py` | **no change**: the object schema stays frozen | | | |
| `roomctl/robot_client.py` | no change (it becomes the fallback for `ROOM_NAV=http`) | | | |

## perception/ (pointcloud + segment sessions)

| file | change | owner | size | risk |
|---|---|---|---|---|
| **NEW** `perception/bb_source.py` | the voxel mirror (room frame, via `frames`) → per-zone surface crop → clusters → candidates (centroid, extents, colour, cell count) → the same records `serialize` writes; which blocks are fresh (from `/stream` `age_s`) | pointcloud | L | medium |
| `perception/pipeline.py` | `scan_into_bb(repo, mirror, fresh_blocks, frame)`, reusing associate → settle → serialize → voxelize stage → publish stage | pointcloud | M | medium |
| `perception/costmap.py` | `Costmap.from_bb_grid(cells, resolution, bounds, T)`; `solve_base_pose` / `solve_viewpoint` unchanged | pointcloud | S | low |
| `perception/raycast.py` | no API change; build its `VoxelGrid` from the BB mirror (`VoxelGrid.from_points`) | pointcloud | S | low |
| `perception/voxelize.py` | stage the per-commit voxels from the BB mirror (room frame, inside the pinned cube) | pointcloud | S | low |
| `perception/associate.py` | a miss counts **only** if the block is fresh AND in line of sight; otherwise carry forward (hidden ≠ gone) | segment | M | medium: G2 must hold across passes |
| `perception/segment.py`, `describe.py` | label the BB clusters by projecting YOLO masks from the robot's frame (intrinsics + mount + pose) | segment | M | medium |

## web/ (web session; jobs.py and bridge are cloud's)

| file | change | size |
|---|---|---|
| **NEW** `web/roommate_api.py` | `GET /api/room/ci` (the badge), `GET /api/chores`, `GET/POST /api/prs`, `POST /api/prs/{id}/approve`, `GET /api/blame/{object_id}` (the move commit + the capture frame), `GET /api/nav/snapshot` (the last grid, robot pose, path, freshness pushed by the laptop) | M |
| `web/events.py`, `web/server.py` | SSE events `room_state`, `nav`, `chore`, `pr`; register the router | S |
| `web/landing/index.html`, `dash.js` (+ css) | the roommate dashboard: CI badge, live map (grid + robot + patrol path + freshness heat), PR list with approve, chores, blame card; copy rewritten to the roommate story | L |
| `web/object_api.py` | the blame card includes the frame of the capture where the object moved | S |
| `web/jobs.py` (cloud) | job kinds `tidy`, `pr_merge`; `_executable` rules for them | S |
| `bridge/agent_api.py` (cloud) | "move/put X on/to Y" → a PR proposal, **graph-side** (Andrew's six verbs stay six; docs/31) | M |

**The cloud dashboard can't reach the robot.** The laptop must **push** nav snapshots and room
state to the cloud through an **authenticated** endpoint (the D46 bearer-token pattern), not the
loopback inlet. Owner: cloud. Size: S.

## elastic/ (elastic session)

| item | change | mapping change? |
|---|---|---|
| `room-events` | new `event_type` values (`chore_opened`, `chore_closed`, `pr_opened`, `pr_merged`, `tidy`) using existing fields (`objects_affected`, `zone`, `message`, `author`, `branch`, `outcome`) | **no** |
| `room-observations` | BB clusters as observations with `camera: "bb_map"`, `occluded` from the raycast | **no** |
| `robot-telemetry` | the BB pose as signals `nav_x`, `nav_y`, `nav_yaw` (numeric `value`) | **no** |
| `elastic/queries.py` | `moved_at(object_id)`: the commit where the pose last changed + capture + frame (blame); wire `commit_at` for `restore --before` | no |
| `elastic/records.py` / `ingest.py` | builders for the chore and PR events | no |

## telemetry/, obs.py, robot_sentry.py (cloud)

| file | change | size |
|---|---|---|
| `telemetry/hub.py` | optionally consume BB `/ws` (8 Hz pose/status) → `robot-telemetry`; `status: failed: …` → a Sentry issue with the pose and path as context | M |
| `obs.py` | `heartbeat()`'s docstring and default say `room-clean`; nav span helpers (`nav.navigate`, `nav.patrol`) | S |
| `robot_sentry.py` | `IssueMirror` takes the watch loop's verdict (after debounce) instead of raw `git status` | S |

## robot/ (robot session + Ryan/Sarah)

| file | change | size | risk |
|---|---|---|---|
| **NEW** `robot/arm_bbos.py` | only if Gate 1 shows the arm moves through bbos: pick/place via bbos topics; read the arm state's per-joint current (gripper load → slip) | L | **high**: unknown hardware |
| `robot/server.py` | back `/arm` with `arm_bbos` when present; keep the sim | M | medium |
| `robot/RUNBOOK.md` | start bbapps/nav (`uv run main.py`, `uv run nav_api.py`), check `/health`, define the area | S | low |

## agent/ (Andrew)
| file | change | size |
|---|---|---|
| `agent/tools.py` | `open_pr(object, zone)`, `where_is(object)`; `room_revert` becomes `room_restore(main)` for tidying | S |

## fake/, scripts/ (master)

| file | change | size |
|---|---|---|
| **NEW** `fake/bbsim.py` | the fake BB nav server (`06-bbsim.md`) | L |
| `fake/scene_gen.py` | export a scene's coloured points for bbsim (`scene_cloud` + colours) | S |
| **NEW** `scripts/bb_check.py` | automate Gate 1: health → ready → rectangle → navigate → report | S |
| **NEW** `scripts/bb_record.py` | record `/heavy` + `/ws` + `/stream` to fixtures (the replay tests use them) | S |

## docs (after approval, same turn as the code, per CONTRIBUTING)
README (the pitch) · docs/04 (policy, PRs) · docs/16 (a BB nav reference section) · docs/20 (the
BB world frame + registration) · docs/24 (the BB grid costmap, arrival tolerance) · docs/28/29 (room
CI badge; **`server_name` is `robot`**) · docs/07, 11, 14 (**remove the Agent Builder claim**; the
real loop) · ANDREW-HANDOFF (nav goes through BB; move/put is graph-side) · TEAM.md · DEMO-RUNBOOK ·
docs/10 (the decision).

## New tests (see 04)
`tests/test_frames.py` · `test_registration.py` · `test_bb_protocol.py` (decode fixtures) ·
`test_bb_source.py` · `test_policy.py` · `test_watch.py` (debounce, freshness) · `test_pr.py` ·
`test_bbsim_e2e.py` (the whole loop against bbsim).

## Totals
New modules: 9 · changed files: ~20 · mapping changes: **0** · hardware-dependent: 1 (`arm_bbos`).
The critical path is **bbsim → frames/registration → bb_nav → bb_source → watch → executor wiring**;
everything else parallelises around it.
