# 01: Target architecture

## The system at a glance

```
 ROBOT (Bracket Bot)                          LAPTOP (the brain, robot network, no internet needed)
 ─────────────────────────────                ───────────────────────────────────────────────────
 bbapps/nav main.py  :8010                    roomctl/bb_nav.py      (NEW) client: voxel mirror, pose,
   /heavy  colour voxel map 1.5 cm   ───────►                         map_gen, :8020 jobs
   /ws     pose + status 8 Hz        ───────►  roomctl/frames.py      (NEW) BB world ⇄ room frame
 bbapps/nav nav_api.py :8020                   roomctl/registration.py(NEW) T_bb←room from tag sightings
   /map /stream  2D grid + freshness ───────►  perception/bb_source.py(NEW) voxels → object candidates
   /map/rectangle /navigate /patrol  ◄───────    + freshness + raycast → records (serialize/settle)
   /stop /pose /health                         roomctl watch          (NEW) patrol → status → policy
 bbos daemons                                                                  → heartbeat · chores · jobs
   camera, IMU (read today)          ───────►  roomctl executor       (CHANGED) plan → base pose (BB grid)
   arm_left/right state (read)                                                 → /navigate → arm → verify
   arm command (Gate 1: unknown)     ◄───────  robot/server.py (ours) captures gated on tilt, camera, /stream
                                               telemetry/hub.py        robot-telemetry → ES; failures → Sentry

 CLOUD (people + memory; never in the motion path)
 ─────────────────────────────────────────────────
 Vercel + GCP web tier   dashboard: room CI badge · live map · pull requests (approve) · chores · blame
 Elasticsearch           commits, observations, voxels, telemetry, events: search, blame, time travel
 Sentry                  room-clean cron (the CI badge) · tidy traces · robot issues it resolves itself
 Andrew's edge           natural language → pull request (his parser) → jobs endpoints
```

## The three loops

### 1. The watch loop: is the room at `main`? (continuous)
```
BB patrol (drives to the stalest 0.5 m block)
  → /heavy deltas update the voxel mirror; /stream freshness updates per block
  → for each FRESHLY SEEN block that holds a tracked or new object:
       bb_source: cluster voxels on zone surfaces → candidates (centroid, extents, colour)
       semantics: YOLO + VLM on the latest robot frame, projected onto the candidates
       identity: associate with HEAD (position, class, colour; Elastic re-id as backup)
       line of sight: raycast through the voxel map; blocked → carry forward (hidden ≠ gone)
  → settle + quantize → working tree → `git status`
  → a change must survive TWO fresh passes (debounce) before it counts
  → policy: mess (shared zone, no PR) | decision (merged PR) | personal (ignore) | untracked
  → outputs: SSE `room_state` · the Sentry room-clean check-in (ok/error) · chores (Tier B) · tidy jobs (Tier A)
```

### 2. The tidy loop: reconcile to `main` (on mess, Tier A)
```
restore main (roomctl, git: restore --source=main + commit when there is anything to record)
  → executor.plan(current, main) → ordered ops (staging for cycles; unapplied = honest conflicts)
  → for each op: base pose from BB's 2D grid (solve_base_pose, ≥ 0.25 m margin)
       → pause patrol → POST /navigate (x, y, heading in BB frame via frames.py)
       → read the actual /pose → arm target in the robot frame → pick (bbos arm)
       → navigate to the place standoff → place → next op
  → verify: solve_viewpoint for the touched spots → navigate → fresh pass → status
  → resume patrol; green check-in; job result; the Sentry trace closes
```
Tier B replaces pick and place with: navigate to face the object, say + LED, open a chore;
patrol verifies after a person fixes it.

### 3. The pull-request loop: an intended change becomes `main`
```
"move the lamp to the shelf" (dashboard form, or Andrew's parser for the words)
  → propose: pick a free spot in the target zone (executor.staging_spot search) → a commit on
    branch pr/<n>-<slug> (roomctl; room.git)
  → dashboard: a preview (the existing graph preview/merge code), the ops, the base poses
  → approve (a second click, or a phone) → merge into main (the existing devgraph stage → commit
    path) → a tidy job
  → from now on, drift of the lamp means drift from the NEW spot
```

## Who decides what (unchanged from PLAN.md §5)
| layer | decides | may be offline? |
|---|---|---|
| robot | safety right now (balance, route collisions, stop, refuse) | no, it IS the robot |
| laptop | what is where; what to do; whether it worked | **must work with no internet** |
| cloud | what the room should be (PRs and approvals); memory; observability; words | yes: it loses approvals and search, and the tidy loop keeps working |

## What stays exactly as it is
room.git layout and the frozen `ObjectRecord` schema (`roomctl/state.py`) · `repo.py` · `publish.py`
and the six indices · `obs.py` · `robot_sentry.py`'s self-heal · the web graph, the merge/cherry-pick
previews · `bridge/` + the jobs endpoints (D46) · the GCP/Vercel deployment · `fake/scene_gen.py`
scenes · all existing tests.

## What is new
`roomctl/bb_nav.py` · `roomctl/frames.py` · `roomctl/registration.py` · `roomctl/policy.py` ·
`roomctl/watch.py` (+ the `room watch`, `room pr`, `room chores` verbs) · `perception/bb_source.py` ·
`fake/bbsim.py` (the dev harness) · dashboard panels (CI badge, live map, PRs, chores, blame card) ·
an arm adapter (`robot/arm_bbos.py`, the robot team's, if Gate 1 says the arm moves).

## What gets retired or demoted
- `HttpRobot.drive` over our own `/drive` becomes the fallback; BB's `/navigate` is primary.
- The laptop stereo→fuse path stays for rehearsal and recordings; the live source is BB's voxel
  map (`ROOM_SOURCE=bb`).
- `web/landing` demo scenes that assume the old "revert" story get re-copied to the roommate
  story (copy only).
