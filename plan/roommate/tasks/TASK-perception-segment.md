# TASK: perception · segment · names and identity for the robot's map
> **DRAFT: not active.** Activated only when the user approves the roommate reframe (`../README.md`). Until then, keep working on your current TASK file.

**Goal:** label the voxel clusters, and make "gone" require evidence.
Read: `../03-interfaces.md` §4, `../04-test-plan.md` (watch + occlusion cases).

| # | task | done when |
|---|---|---|
| 1 | Project YOLO masks from the robot's latest frame onto `bb_source` candidates (intrinsics + mount + pose via `frames`) | the right classes on the 4–6 demo objects |
| 2 | The `associate` miss rule: a miss counts only if the block is **fresh AND visible**; otherwise carry forward | bbsim scenario 3 passes; G2 holds |
| 3 | Descriptions (VLM) for new objects; `vlm_model` provenance kept | new objects arrive in Elastic with words |
| 4 | Observations as `camera: "bb_map"` with `occluded` from the raycast | the web object page's hidden-vs-gone rule works on live data |
