# `perception/` — runs on the LAPTOP

**Branch:** `track/perception` · **Shared folder — three owners, split by file.** See
[`TEAM.md`](../TEAM.md#where-perception-splits). Works from **recorded captures**, so it never
blocks on the live robot.

> `depth` `calib` `fuse` → Sarah/Ryan · `segment` `describe` `associate` → Andrew ·
> `voxelize` `serialize` → Daniel

## What this module owns
Pixels → object instances → a scene graph. Everything between `POST /capture` returning and
`roomctl` having a list of objects with stable IDs.

## Files

| file | purpose |
|---|---|
| `depth.py` | Fork of BB `example_depth.py`. Fisheye rectify → StereoSGBM → `reprojectImageTo3D`. **Do not rewrite it — it works.** |
| `calib.py` | Stereo calibration loader + multi-camera extrinsics via ChArUco/AprilTag. Writes `extrinsics.yaml` **once**; it must then never change. |
| `fuse.py` | Three per-camera clouds → robot frame → world frame (anchor tag + odometry) → ICP refine. Logs the ICP residual so silent misalignment is visible. |
| `segment.py` | **Primary path.** SAM 3 / YOLO-seg masks on the rectified left image → `xyz[mask]` gives the instance directly. See [`../docs/15-segmentation.md`](../docs/15-segmentation.md). |
| `cluster.py` | **Fallback path.** Plane removal (RANSAC ×4) → DBSCAN, for anything the model doesn't recognize. The removed planes become zones. |
| `describe.py` | VLM free-text description per instance **per view**. Keeps all three; never collapses them. |
| `merge.py` | Cross-camera instance merge in the world frame: centroid <15 cm + label/embedding agreement + bbox overlap. |
| `voxelize.py` | Whole cleaned cloud → occupancy grid → octree keys (`octree_key()` lives here). T2. |
| `associate.py` | Re-identify against history via Elasticsearch hybrid search, then Hungarian assignment. Carries stable `object_id`s forward. |
| `serialize.py` | Quantize (1 cm / 5°), hysteresis, deterministic ordering → YAML. |

## Build order
1. `depth.py` running against the stock camera. **Tune SGBM on the actual demo table in hour one.**
2. `cluster.py` — plane removal + DBSCAN. Proves objects can be found at all.
3. `serialize.py` + quantization. Then immediately write `tests/test_idempotent_scan.py`.
4. `segment.py` — switch to mask-first once the geometric path proves the plumbing.
5. `describe.py`, `associate.py`.
6. `calib.py` + `fuse.py` for cameras 2 and 3 (T2, **timebox to 2 hours**).
7. `voxelize.py` (T2).

## Acceptance criteria
- [ ] **The one that matters:** scan an untouched table twice → `git diff --exit-code` is clean
- [ ] Move one object → exactly one modified file in the diff
- [ ] An object leaving and returning keeps its original `object_id`
- [ ] ICP residual is logged on every capture and alarms when it jumps

## Gotchas
- **Phantom diffs are the enemy**, not missing objects. A liar is worse than a crash.
- **Plane removal is non-negotiable** — leave the table in and every object on it is one blob.
- Textureless surfaces produce no points at all. That's a *data* problem: textured tablecloth,
  matte objects.
- Keep **all three** VLM descriptions. Their disagreement is the entity-resolution signal and
  half the Elastic story.
