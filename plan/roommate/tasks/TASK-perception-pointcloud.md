# TASK: perception · pointcloud · the room from the robot's own map
> **ACTIVE since Sat 12:45 EDT.** The caretaker-roommate plan is the team's goal (`../../../PLAN.md` §0 first).
> **Shared rules:** develop and test on **localhost only** (web http://localhost:8000, landing :8124, devgraph :8125,
> bbsim on loopback ports); bind servers to 127.0.0.1; don't point work at the Vercel/GCP/Tailscale URLs (master deploys).
> Don't commit or push (master batches commits). Don't put assistant or tool names in any file.
> **Open localhost pages in Chrome, never Safari**: `open -a "Google Chrome" http://localhost:8000`; browser tooling uses Chrome/Chromium.

**Goal:** turn Bracket Bot's live colour voxel map into the same object records serialize writes today.
Read: `../03-interfaces.md` §3–4, `../06-bbsim.md`. Build against **bbsim** first, then the recorded fixtures.

| # | task | done when |
|---|---|---|
| 1 | `perception/bb_source.py`: `candidates()` (surface crop, drop the plane, connected components, PCA extents/yaw axis, colour) | `tests/test_bb_source.py`: count, centroid ≤ 1 cm, extents ≤ 1.5 cm, yaw ±5° on the demo scene |
| 2 | `fresh_blocks()` from `/stream` `age_s`; `visible()` via `raycast.line_of_sight` on `VoxelGrid.from_points(mirror)` | an occluded object is never reported missing (bbsim scenario 3) |
| 3 | `pipeline.scan_into_bb()`: associate → settle → serialize → `voxelize.stage` → `publish.stage_scan` | a commit made from a live scan publishes exactly like today's |
| 4 | `Costmap.from_bb_grid()` | `solve_base_pose` / `solve_viewpoint` work on BB's 3 cm grid |
| 5 | **G2 across passes** on bbsim, then on the real desk | zero diff after every pass (5 bbsim, 3 real) |

Don't: convert frames yourself. Everything goes through `roomctl/frames.py`.
