# The roommate refactor: ready to execute, NOT active

**Status: DRAFT, awaiting approval.** Nothing in this folder changes the running system.
No existing file has been edited to prepare it. The pitch and the demo live in
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
