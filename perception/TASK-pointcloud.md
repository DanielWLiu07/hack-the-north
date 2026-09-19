# YOUR JOB — perception: geometry & voxels

You own `perception/depth.py`, `fuse.py`, `voxelize.py`, `serialize.py`.
Do NOT touch `segment.py`, `describe.py`, `associate.py` — another session owns those.

**Read `../docs/20-perception-logic.md` before writing a line.** It contains three
convention facts taken from Bracket Bot's actual code, two of which contradict each other:
- `Q` is in MILLIMETRES — divide by 1000 once, in `depth.py`
- the camera frame is Y-DOWN (OpenCV)
- their odometry uses X-forward/Z-left while their depth code uses X-right/Z-forward

We adopt **Z-up, X-forward, Y-left** downstream of `fuse.py`, because the Elasticsearch
schema stores `cell: {x,y}` as a floor-plane point with `z` as height. `cam_to_world_axes`
must appear **exactly once** in the codebase.

Build in this order, verifying each before the next:
1. `depth.py` — fork BB's `example_depth.py`, strip the loop and Rerun, return
   `(xyz, valid, left_rect)`. Keep the **(H,W,3) shape** — do not flatten.
   Assert metres: `np.nanmax(np.abs(xyz[valid])) < 50`.
2. `cam_to_world_axes` + the floor assertion (floor at z≈0). 20 lines, de-risks everything.
3. `serialize.py` with `stabilize()` — quantize 1 cm / 5° AND hysteresis at 1.5 quanta.
   Both are required; quantization alone flickers on bucket boundaries.
4. `voxelize.py` + `octree_key()` — base-8 octree path, 8 m cube, 7 levels.
   Read `../docs/11-elastic.md` for the encoder and the "use a CUBE" warning.

Acceptance (**this is the project's most important gate**): scan an unchanged scene
twice → `git diff --exit-code` is clean.
