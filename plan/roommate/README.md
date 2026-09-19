# The roommate refactor: ready to execute, NOT active

**Status: ACTIVE since Sat 12:45 EDT** (the user asked every workstream to work toward it). Develop on
**localhost only**; master commits, pushes and deploys. The pitch and the demo live in
[`../../PLAN.md`](../../PLAN.md); this folder is the engineering package behind it.

> GITIRL: git for the people you live with. A robot roommate that keeps the shared space at
> `main`. It cleans up the mess, not your decisions.

| file | what it answers |
|---|---|
| [`01-architecture.md`](01-architecture.md) | the target system: three loops (watch · tidy · pull request), who decides what, what stays, what's new |
| [`02-change-inventory.md`](02-change-inventory.md) | **every file that changes**, file by file: what, why, owner, size, risk; plus new and retired files |
| [`03-interfaces.md`](03-interfaces.md) | the contracts for new modules: `bb_nav`, `frames`, registration, the voxel source, freshness, policy, `room watch`, pull requests and chores, events, Sentry, Elastic |
| [`04-test-plan.md`](04-test-plan.md) | unit, simulation and hardware acceptance; the gates |
| [`05-rollout.md`](05-rollout.md) | order of work, feature flags, demo tiers A/B/C as flag sets, rollback |
| [`06-bbsim.md`](06-bbsim.md) | **the dev harness**: a fake Bracket Bot nav server, so every track can build without the robot |
| [`tasks/`](tasks/) | draft TASK files, one per session or person, activated on approval |

## How to activate (in order)
1. The user approves the reframe (PLAN.md §9).
2. Master copies `tasks/TASK-*.md` to each session and briefs it (one message each), and records the
   decision in `docs/10-open-questions.md`.
3. **Phase 1 starts in parallel with the hardware check (Gate 1):** bbsim + frames + the `bb_nav`
   client (master), with no dependency on the robot.
4. Everything new lands **behind feature flags** (`05-rollout.md`). The current system keeps
   working at every step, and `ROOM_NAV=mock ROOM_SOURCE=fake` is today's behaviour, byte for byte.

## The principles this refactor holds to
1. **Add, don't rewrite.** The git layer, the frozen object schema, the publish hook, the six
   indices, `obs.py`, the deployment, the bridge and the jobs endpoints stay as they are.
2. **One frame module.** Every conversion between the room frame and Bracket Bot's world frame
   goes through `roomctl/frames.py`, with golden tests. There are no ad-hoc `sin`/`cos` anywhere
   else.
3. **Evidence before verdicts.** Nothing is "moved" or "gone" unless its spot was freshly seen,
   with line of sight, on two passes.
4. **Deterministic plans; the robot can always refuse.** No LLM output moves the robot.
5. **The demo can't depend on the internet.** The cloud is for people and memory, never for
   motion.

## Update: aligned with Andrew's Housebot Edge (`9582081`)
Andrew refocused his edge on a roommate/caretaker robot. PLAN.md §0 has the merged story and the demo
ladder. What changes in this package:

| item | before | now |
|---|---|---|
| motion path | our executor → `BBNavRobot` → BB `/navigate` | **primary:** our job → Housebot Edge `POST /v1/jobs` → Ryan/Sarah `RobotAdapter` (`POINT_AT_OBJECT`, `MOVE_OBJECT`). `BBNavRobot` stays as the **fallback** when the edge or the adapter is down |
| `roomctl/registration.py` | we estimate `T_bb←room` | the robot adapter **owns** the transform and **publishes** it (`GET :8765/registration`); we consume it for reading the voxel map. One owner, one estimate |
| `roomctl/frames.py` | the conversion module | a reader of the published transform + the golden tests (BB yaw 0 faces +y) |
| **new** dispatcher | none | laptop web: `HOUSEBOT_EDGE_URL` + `HOUSEBOT_EDGE_TOKEN`; `point`/`move` jobs POSTed to the edge, terminal results back to the dashboard (owner: web + cloud) |
| demo order | the tidy loop first | **"Where are my keys?" point ×5 first** (his stop gate), then drift → chore, then one move, then the PR beat |

## Update 2 (Sat 14:10): the room model comes from the robot's own mapping daemon
The robot's bbos `mapping` daemon already fuses every depth frame with its SLAM pose into a colour voxel map
(3 cm, world frame, a floor label per voxel), and it runs whether or not bbapps/nav is up. We read it instead of
rebuilding the room from single stereo views, which drift the moment the robot turns (agreement fell from 90 % to
40–60 %).

| item | decision |
|---|---|
| voxel source | `scripts/bbos_map.py` (read-only over ssh) exposes a VoxelMirror-shaped source: `.points()` → (N,3) world metres + rgb, plus a floor mask, and `.state` from slam.pose. bbapps/nav's `/heavy` stays an equivalent source when the nav app runs (same frame) |
| objects | **one extractor**: `perception/bb_source` (candidates → scan_into_bb → associate, settle, serialize, voxelize.stage, publish.stage_scan). Commits publish through the normal hook; D37 holds |
| room frame (demo) | the robot's SLAM world frame for the current map generation: registration = identity, recorded with `map_gen`. `room.yaml`'s desk zone is measured from the map's table top. A map reset invalidates it (and is a warning in Sentry) |
| freshness | no freshness grid from this source: every snapshot counts as fresh; the map keeps unseen voxels, so hidden objects are not deleted |
| people | only the table-top band counts, 2-pass debounce, and the segment workstream's person mask when it lands |
| capture ids | the robot's counter moves past 1000 (earlier simulated senders used cap_0010..13); a conflicting room-clouds write is loud, never silent |
