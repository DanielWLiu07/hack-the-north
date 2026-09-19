# 05: Rollout (order, flags, tiers, rollback)

## Phases (EDT, re-timed at 12:30 Sat; Sun 08:00 deadline)

| phase | when | work | exit criterion |
|---|---|---|---|
| **0 · approve** | now | the user approves; master briefs each session with its `tasks/TASK-*.md`; the decision goes in docs/10 | every session acknowledges |
| **1 · harness + contracts** | 12:30 → 14:30, **in parallel with Gate 1** | master: `fake/bbsim.py`, `frames.py` + tests, the `bb_nav.py` protocol + tests. Robot team: Gate 1. Everyone else: read 03, stub against bbsim | bbsim serves `/heavy` `/ws` `:8020`; frames golden tests green; **Gate 1 → tier** |
| **2 · eyes** | 14:30 → 17:30 | `registration.py`; `bb_source.py` (pointcloud); freshness + line-of-sight miss rule (segment); the dashboard skeleton: CI badge + live map (web) | G2 across 5 bbsim passes; the real mirror renders on the dashboard |
| **3 · loops** | 17:30 → 20:30 | `watch.py` + policy; executor → `BBNavRobot`; `pr.py` + the web PR panel; room-clean heartbeat (cloud); `arm_bbos` if Tier A | **Gate 2** on the real robot → **video v1** |
| **4 · story** | 20:30 → 00:00 | blame card with frame; `restore --before`, `why`; chores (Tier B); the sponsor stories; docs sweep | the 90 s script 3× clean → **freeze** |
| **5 · ship** | 00:00 → 07:30 | Devpost, final video, rehearsal ×5, sleep in shifts; 07:00 table check | submitted by 07:30 |

## Merge order (the critical path, in bold)
**bbsim → frames → bb_nav → registration → bb_source → watch → executor/BBNavRobot**
→ PR flow → dashboard panels → sponsor showpieces → docs.
Each merge keeps every existing suite green and ships behind its flag.

## Feature flags = the demo tiers

| tier | `ROOM_NAV` | `ROOM_SOURCE` | `ROOM_TIER` | what the robot does |
|---|---|---|---|---|
| today (unchanged) | `mock` | `fake:<scene>` | `C` | nothing; the fake room |
| **C** | `bb` (read-only: pose + map) | `bb` | `C` | a stationary or teleoperated scanner; status, blame, PRs on screen |
| **B** | `bb` | `bb` | `B` | patrols; drives to the mess, faces it, says it; chores; verifies |
| **A** | `bb` + arm | `bb` | `A` | patrols; tidies with the arm; verifies |

A failure in rehearsal drops one tier with **one env change**. Nothing is rebuilt.

## Rollback
- Every new path is opt-in, so unsetting the flags gives exactly today's system (the existing
  tests pin it).
- `room.yaml` v2 fields are optional; old rooms load unchanged.
- The demo room.git: tag `pre-roommate` before the first v2 commit; `git reset --hard
  pre-roommate` restores it (and the mirror hooks sync it to the cloud).
- The cloud: `scripts/gcp_mirror.sh ship` of the previous tree; Vercel "promote" the previous
  deployment.

## Guardrails during the build
- **Nobody presses Start/Stop in the robot's browser UI while a job runs.** Watching is fine.
- The robot's ports (:8010/:8020) stay on the robot network or the tailnet, never public.
- `JOBS_REAL_MOTION` stays off on the cloud; motion comes from the laptop's executor only.
- A `map_gen` change pauses everything until re-registration succeeds.
- Hack the North rules: all code written this weekend; public libraries and Bracket Bot's
  sponsor software (bbapps/nav) only; **nothing from the July SplatCraft project**.

## The cut list (we lost 3.5 h to the pause; cut in this order if a gate slips)
1. **Gate 1 not green by 14:30 → Tier B is the plan.** The arm stops being on the critical path;
   `arm_bbos` becomes a stretch that only lands if it works before 20:30.
2. **bb_source late at 17:30 →** the watch loop runs on today's recording/fake source for the demo
   rehearsal; the BB live map still shows on the dashboard (read-only).
3. **Gate 2 slips past 21:30 →** record the video from bbsim + the real robot navigating, and
   demo Tier C live. Say so honestly.
4. **The PR beat not live by 23:00 →** show it from the CLI (`room pr open/approve`) against the
   real repo; skip the phone approval.
5. **Never cut:** the backup video, the 5 rehearsals, submitting by 07:30.
