# TASK: perception · segment · names and identity for the robot's map
> **ACTIVE since Sat 12:45 EDT.** The caretaker-roommate plan is the team's goal (`../../../PLAN.md` §0 first).
> **Shared rules:** develop and test on **localhost only** (web http://localhost:8000, landing :8124, devgraph :8125,
> bbsim on loopback ports); bind servers to 127.0.0.1; don't point work at the Vercel/GCP/Tailscale URLs (master deploys).
> Don't commit or push (master batches commits). Don't put assistant or tool names in any file.
> **Open localhost pages in Chrome, never Safari**: `open -a "Google Chrome" http://localhost:8000`; browser tooling uses Chrome/Chromium.

**Goal:** label the voxel clusters, and make "gone" require evidence.
Read: `../03-interfaces.md` §4, `../04-test-plan.md` (watch + occlusion cases).

| # | task | done when |
|---|---|---|
| 1 | Project YOLO masks from the robot's latest frame onto `bb_source` candidates (intrinsics + mount + pose via `frames`) | the right classes on the 4–6 demo objects |
| 2 | The `associate` miss rule: a miss counts only if the block is **fresh AND visible**; otherwise carry forward | bbsim scenario 3 passes; G2 holds |
| 3 | Descriptions (VLM) for new objects; `vlm_model` provenance kept | new objects arrive in Elastic with words |
| 4 | Observations as `camera: "bb_map"` with `occluded` from the raycast | the web object page's hidden-vs-gone rule works on live data |
