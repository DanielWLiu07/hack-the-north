# TASK: robot (Ryan, Sarah + the robot session) · Gate 1 decides the demo
> **ACTIVE since Sat 12:45 EDT.** The caretaker-roommate plan is the team's goal (`../../../PLAN.md` §0 first).
> **Shared rules:** develop and test on **localhost only** (web http://localhost:8000, landing :8124, devgraph :8125,
> bbsim on loopback ports); bind servers to 127.0.0.1; don't point work at the Vercel/GCP/Tailscale URLs (master deploys).
> Don't commit or push (master batches commits). Don't put assistant or tool names in any file.
> **Open localhost pages in Chrome, never Safari**: `open -a "Google Chrome" http://localhost:8000`; browser tooling uses Chrome/Chromium.

**Goal:** the facts that decide Tier A/B/C by **14:30**, then the arm if it moves.
Read: `../04-test-plan.md` (Gate 1), the Bracket Bot nav docs (bbapps/nav).

| # | task | done when |
|---|---|---|
| 1 | The laptop reaches the robot (the robot's router or the tailnet; today there's no route to 10.37.101.235) | `GET :8020/health` from the laptop |
| 2 | Start `uv run main.py` + `uv run nav_api.py` in `~/bbapps/nav`; `/ws` `ready: true` | `scripts/bb_check.py` steps 1–3 pass |
| 3 | Map the 2 × 2 m demo area; one `/navigate` → `reached`, within 0.25 m | recorded with `scripts/bb_record.py` |
| 4 | **One commanded arm motion through bbos** (the arm belongs to bbos daemons; don't open the servo bus) | yes/no by 14:30 → the tier |
| 5 | Measure BB z = 0, the camera→base mount (in BB's robot frame: +x right, +y forward, z up), and the arm's reach from the base | numbers in robot/RUNBOOK.md |
| 6 | If the arm moves: `robot/arm_bbos.py` pick/place + the gripper load read (slip) → `/arm` backed by it | one pick and place on the demo desk while balanced |

**Never** press Start/Stop in the robot's browser UI while our jobs run. Keep :8010/:8020 off public networks.

**Update (Housebot Edge, Andrew `9582081`):** the robot-side contract is his `RobotAdapter` over HTTP
(`scripts/run_robot_api.py`, :8765). **First capability: `POINT_AT_OBJECT`** (drive over with BB nav,
face the object, point), then `MOVE_OBJECT`. The world-to-robot transform lives in your adapter
(the room frame in, BB frame out). **Publish it** (`GET /registration`: `T_bb←room`, `map_gen`,
residual) so perception reads the voxel map with the same transform. Never open BBOS writers
outside that adapter.
