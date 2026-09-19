# 15 — Separating objects in the point cloud

Two approaches. **Do the image-first one as primary** — it's both easier and better — and keep
the geometric one as a fallback for things the model doesn't recognize.

---

## Approach A (primary): segment in 2D, lift to 3D

The insight that makes this easy: `cv2.reprojectImageTo3D(disparity, Q)` returns an
**H×W×3 array aligned pixel-for-pixel with the rectified left image**. So a 2D mask is
already a 3D point selection — no clustering required at all.

```python
xyz  = cv2.reprojectImageTo3D(disparity, Q)      # (H, W, 3), aligned to left_rect
good = np.isfinite(xyz).all(axis=2) & (disparity > MIN_DISP)

masks = segment(left_rect)          # SAM 3 / YOLO-seg — per-instance boolean masks
for m in masks:
    sel = m & good
    if sel.sum() < 150:             # too few valid depth pixels to trust
        continue
    pts = xyz[sel]                  # <- the object's points. That's the whole step.
    yield Instance(points=pts, label=m.label, mask=m, camera=cam_id)
```

Why this beats clustering geometry:

- **2D segmentation models are vastly better than geometric heuristics.** They understand
  *objects*; DBSCAN understands *point spacing*.
- **Touching objects separate correctly.** A mug sitting against a book is one connected blob
  in 3D and two obvious instances in the image. This is the single biggest failure of the
  geometric approach and the model just doesn't have it.
- **You get the label for free**, in the same pass — and the free-text description that
  [hybrid search depends on](11-elastic.md#gap-2--thin-text-means-hybrid-search-has-nothing-to-chew-on).
- **It survives sparse depth.** The mask defines the instance even where stereo dropped out;
  you just get fewer points for it.
- The BB quickstart already ships `example_segmentation.py` and `example_yolo.py` — start there.

### Merging instances across the three cameras
Each camera yields its own instances, so the same mug appears up to three times. Merge in the
world frame:

```python
# transform every instance's points by its camera extrinsics, then the robot pose
# then group candidates that are plausibly the same physical object:
#   centroid distance  < ~15 cm
#   AND (label agrees OR description embeddings are close)
#   AND bounding boxes overlap
```

Keep **all three descriptions** on the merged object — the disagreement between them is the
entity-resolution signal, and throwing it away kills half the Elastic story.

*As built* (`perception/merge.py`, `merge(instances, embed=None) -> list[MergedObject]`): the three
tests above with `MERGE_DIST = 0.15`, boxes = the 1st–99th percentile AABB grown by
`BOX_MARGIN = 0.02`, and "labels agree" = equal segmenter labels, OR either side `unknown` (the
fallback's absence of a label never disagrees), OR description embeddings with cosine ≥
`EMBED_COS = 0.85` when an `embed` callable is given (production passes none). Two rules the
sketch lacks: **never two views from the same camera** (a mug touching a book, both seen by cam0,
stays two objects), and **complete linkage** (A~B and B~C can't chain A with C). Every view is
kept: `MergedObject.observations()` is one room-observations row per view with that camera's own
`raw_x/y/z` and `raw_description`, every contract key present (explicit nulls);
`object_fields()` gives room-objects' `observed_by`, `raw_description[]`, `confidence`,
`point_count`. In today's pipeline every instance comes from the fused cloud (`camera=None`, one
"camera"), so merge passes them through one object each.

---

## Approach B (fallback): geometric clustering

For anything the segmentation model doesn't recognize — and to catch objects entirely, so
`class: unknown` things still get tracked.

```python
pcd = pcd.voxel_down_sample(0.01)                       # 1 cm
pcd, _ = pcd.remove_statistical_outlier(20, 2.0)

# 1. REMOVE THE SUPPORT PLANES — the step everything hinges on
for _ in range(4):                                       # floor, table, maybe walls
    plane, inliers = pcd.segment_plane(distance_threshold=0.015,
                                       ransac_n=3, num_iterations=1000)
    if len(inliers) < 2000:        # no big plane left
        break
    planes.append((plane, pcd.select_by_index(inliers)))  # keep it — this is a ZONE
    pcd = pcd.select_by_index(inliers, invert=True)

# 2. now objects are physically disconnected, so density clustering just works
labels = np.array(pcd.cluster_dbscan(eps=0.025, min_points=40))

for i in range(labels.max() + 1):
    cluster = pcd.select_by_index(np.where(labels == i)[0])
    if not (0.02 < extent(cluster) < 0.60):   # reject noise and wall fragments
        continue
    obb = cluster.get_oriented_bounding_box()
```

### Why plane removal is non-negotiable
Leave the table in and **every object on it is connected through the tabletop**, so DBSCAN
returns one enormous cluster containing the table and everything on it. Removing the plane is
what makes objects physically separate in space.

Bonus: the removed planes **are your zones**. The table plane becomes `zones/desk/`, the floor
becomes `zones/floor/`. Keep them, don't just discard the inliers.

### Parameters that actually matter
| param | start at | too small | too large |
|---|---|---|---|
| voxel size | `0.01` | slow, noisy | objects lose shape |
| plane `distance_threshold` | `0.015` | table survives in fragments | **flat objects get eaten with the table** |
| DBSCAN `eps` | `0.025` (≈2–3× voxel) | one object splits into several | adjacent objects merge |
| `min_points` | `40` | noise becomes objects | small objects vanish |

> **As built, that last row is TWO parameters, and passing 40 to DBSCAN is wrong.**
> `perception/cluster.py`: `DBSCAN_CORE = 10` is DBSCAN's density (neighbours within `eps`, self
> included, to be a core point); `MIN_CLUSTER_PTS = 40` is a **size filter applied afterwards**.
> `eps` is `DBSCAN_EPS = 0.025` and the plane threshold `PLANE_DIST = 0.015`, as in the table.

Tune `eps` and `distance_threshold` **on the actual demo table in hour one**. They are
scene-specific and everything downstream is built on them.

---

## The five failure modes, and what to do

1. **Touching objects merge.** The defining weakness of geometric clustering — use Approach A.
2. **One object splits in two** (dark side, specular highlight, depth dropout). Merge clusters
   whose OBBs overlap or sit within ~3 cm, or let the image mask arbitrate.
   *As built:* `cluster.merge_split_clusters` runs right after DBSCAN: two clusters are one
   object when their footprints touch (within `SPLIT_XY = 0.01` m) **and** the vertical gap
   between them is ≤ `SPLIT_Z = 0.05` m — on a table, a piece hovering over another is a dropout
   band, not two things. Side-by-side objects more than 1 cm apart stay separate. This was G2's
   last phantom (seed 7): an 8×8×20 cm block's base came and went as its own `unknown`.
3. **Flat objects eaten by plane removal.** A book lying on the table gets absorbed into the
   table plane. Tighten `distance_threshold`, and check for thin layers just above each plane.
4. **Textureless surfaces produce no points at all** — white mugs, glossy things, glass. This
   is [R3](08-risks.md) and it's a *data* problem, not an algorithm one: textured tablecloth,
   matte patterned objects. Approach A degrades gracefully here (the mask still exists);
   Approach B just loses the object.
5. **The robot itself / people in frame.** Filter by `.roomignore` at the mask stage —
   `person` never becomes an object, and points inside the arm's known workspace volume get
   dropped by kinematics.
   *As built:* `segment.roomignore(repo_dir)` reads `<repo>/.roomignore` → (labels, path globs):
   a line with `/` or `*` is a glob, any other line a segmenter label (case-insensitive), and
   `person` is always in (`segment.IGNORE_LABELS`, the default when there's no file). room.git's
   gives labels {person, robot, cable} and `zones/floor/**`. Labels go to
   `segment.lift/run(..., ignore=)` and never become instances; globs go to
   `associate.for_serialize(..., ignore_paths=)`, which `pipeline.scan_into` already passes. Like
   `.gitignore`, a glob keeps an **untracked** (`added`) object out; one already in HEAD stays
   tracked. The arm-workspace drop is **not built**.

---

## Recommended pipeline

```
per camera:
    rectify → SGBM → disparity → xyz (H×W×3, aligned to left image)
    SAM 3 / YOLO-seg on left_rect → instance masks          ── Approach A
    for each mask: pts = xyz[mask & valid]  → instance + label + description
    residual points not under any mask:                      ── Approach B
        plane removal → DBSCAN → instances with class "unknown"

merge across cameras in world frame → final object instances
per instance: OBB, centroid, extents, dominant colour, point count

→ room-observations  (one doc per instance per camera — keep the disagreement)
→ re-identify against history via ES hybrid search
→ room-objects + git
```

**The voxel grid is computed separately** from the *whole* cleaned cloud — occupancy is not
instance segmentation. A voxel may carry an `object_id` when an instance claims it, and
plenty of voxels legitimately belong to no object at all (walls, table, clutter we never
identified). Don't conflate the two.

---

## As built — what runs today (reconciled with `perception/cluster.py`, `segment.py`, `pipeline.py`)

Only facts checked against the source. **These files are under active edit** — if a line here
disagrees with the code, the code is newer; regenerate the constants with
`grep -nE "^[A-Z_]+ *=" perception/cluster.py perception/segment.py`.

**The production pipeline runs A, then B on the residual (wired 2026-09-19).**
`pipeline.scan_into` → `segment_then_cluster`: per camera, `segment.run(..., mount=, robot_pose=,
ignore=.roomignore labels, keep=)` lifts YOLO masks to F_world, keeping an instance only inside
cluster's size window (`MIN_EXTENT`..`MAX_EXTENT`) with its centre in a zone; then
`cluster.cluster(in_zones(residual))` runs on the fused points **no kept mask claimed**, so a
rejected mask (YOLO's "dining table" = the desk) hands its pixels back and what stands on it is
still found. Chain: depth → gate → fuse → **segment + cluster** → merge → voxelize → associate →
serialize. **On by default only where the model is installed** (`$MODELS_DIR/weights/yolo11s-seg.pt`,
the bootstrap puts it there); otherwise, or with `GITSPACE_SEGMENTER=off`, cluster runs alone on
the whole fused cloud, as before. VLM descriptions in the chain only with `GITSPACE_DESCRIBE=1`
(API calls). Verified: G2 0/11 on seeds 0/1/3/7/11 with the mask path on, in both Pythons. On
the synthetic renders YOLO finds only the mug ("cup") and the desk; book and block still come
from the fallback. Known cost: a single-view mask sees one face, so the mug's footprint reads
7×5 cm against a true 9×9. **SAM 3 is not built**: the segmenter is YOLO
(`WEIGHTS = "yolo11s-seg.pt"`, `CONF, IOU = 0.25, 0.45`); SAM fits the same
`image -> list[Mask]` interface.

**The snippets above are pseudocode: there is no Open3D** (nor sklearn) in `perception/`.
`cluster.py` is numpy/scipy with its own `voxel_down_sample`, `remove_statistical_outliers`,
`segment_plane`, `dbscan`. Entry point:
`cluster.cluster(points (N,3) F_world metres Z-up, seed=0) -> (list[Plane], list[Instance])`,
deterministic. Constants: `VOXEL = 0.01` · `OUTLIER_K = 20`, `OUTLIER_STD = 2.0` ·
`PLANE_DIST = 0.015`, `PLANE_ITERS = 1000`, `PLANE_SCORE_PTS = 20000`, `MAX_PLANES = 4`,
`MIN_PLANE_PTS = 2000` · `DBSCAN_EPS = 0.025`, `DBSCAN_CORE = 10`, `MIN_CLUSTER_PTS = 40` ·
`MIN_EXTENT, MAX_EXTENT = 0.02, 0.60` (longest box side) · `SPLIT_XY, SPLIT_Z = 0.01, 0.05`.

**Boxes are upright, not OBBs.** `Instance.box(yaw=None) -> (centre, extents, yaw_deg)`: a Z-up
box with yaw only, yaw an **axis** (it repeats every 180°), and 0 when the footprint is rounder
than `ROUND_ASPECT = 1.2` — a round object has no meaningful yaw, and inventing one is a phantom
diff. The axis is the long side of the smallest footprint rectangle, searched over rotation on
the 1st–99th percentile extents (`_footprint_axis`) — **not** principal axes, which wandered
~12° between rescans of a 13×10 cm box, nor `cv2.minAreaRect`, which 0.5% stray points swing
by up to 90°. Passing `yaw=` measures the extents along a given axis (associate holds the
committed one near the round cutoff). This is what `serialize.Measured` consumes
([`20` Part 5](20-perception-logic.md)).

**Planes are classified, then discarded; zones are NOT the removed planes.**
`Plane.kind` ∈ floor / surface / wall / slanted (`HORIZONTAL_DEG = 10`, `VERTICAL_DEG = 80`,
`FLOOR_TOL = 0.05`), but `scan_into` drops them (`_, instances = …`). Zones are the boxes pinned
in the room repo's `room.yaml`, and they **restrict the input**: `in_zones()` keeps only points
inside a zone before clustering, because far walls and the floor's stereo terraces are what the
fallback clusterer mistakes for objects.

**The lift (Approach A), as built** — `segment.lift(xyz, valid, masks, camera, image=None,
ignore=IGNORE_LABELS)`, in **F_rect** (its depth filter reads column 2 as range from the camera;
in F_world that column is height): drop `ignore`d labels, erode the mask
(`ERODE_PX_AT_1280 = 5`, scaled by image width: `erode_px(w)`), drop points further than
`MAX_DEPTH_SPREAD = 0.30` m from the mask's median depth, reject under `MIN_POINTS = 150`, and
take `color` as the median BGR under the eroded mask. `segment.residual()` uses the FULL masks,
so an object's eroded edge never comes back as a second, `unknown` object.
`segment.run(xyz, valid, left_rect, camera, segmenter=None, mount=None, *, robot_pose=None,
ignore=IGNORE_LABELS) -> (instances, residual)` moves both to F_world through
`fuse.rect_to_world(p, mount, robot_pose)` when given a `mount`, and then **requires** `robot_pose`
(the same pose `fuse.fuse()` got; an origin default would disagree with the cloud once the robot
moves). Neither file carries a reason string: a rejected mask or cluster is skipped, and
`cluster.py` only **counts** them in one log line. So the `rejected_reason` column in
room-observations is **always null today** (`merge.observations()` writes `None`). No path emits
the fake's "discard pile" rows yet: open, perception/segment.

## Approach C: difference against a baseline — as built (`perception/difference.py`)

Approaches A and B ask what is in *this* capture. C asks what **changed** since a baseline capture, which
needs no model and no zones. It is the segmenter for a room with no `room.yaml` zones yet (the LINK
hallway recordings). It is **not wired into `pipeline.scan_into`**: callers run it on two recordings,
`python perception/difference.py <baseline_dir> <current_dir>`, or call
`difference.difference(baseline: View, current: View, camera) -> Change(appeared, gone)`.

- **Evidence is free space along the ray.** APPEARED: the current depth is nearer than *everything* the
  baseline saw in a `WINDOW = 5` px neighbourhood, by `tau = TAU_M + TAU_Z2·z²` (5 cm + 3 cm/m²; stereo
  noise grows as z²). GONE: the same test with the captures swapped. A pixel with no baseline depth nearby
  is never a change. When the pose differs, the baseline is z-buffered into the current camera first,
  through `fuse.rect_to_world` (no second frame conversion). Intrinsics are fitted from the F_rect
  points themselves (`intrinsics()`), so RealSense frames work too.
- **Crops, all measured on the real untouched pair `cap_0004`/`cap_0005`:**
  - `FOV_DEG = 38` around the optical axis. The rectified fisheye's rim is where every untouched blob
    over 40 cm² sits, more than 41° off-axis.
  - `RANGE_M = 1.8` horizontally. Beyond it, noise groups reach 65 cm²; the median |dz| between
    captures is 7 cm at 2–2.6 m.
  - `SELF_M = 0.35`, the robot's own arm.
  - `MIN_HEIGHT = 0.03` above the floor.
- **Blobs** are image-connected, then split in F_world with DBSCAN at `eps = max(3 cm, 3 px · z/f)`.
- **Growth.** Each blob grows into connected pixels that changed by `TAU_GROW = 2 cm` against the
  baseline's window *median*, within `GROW_M = 5 cm` of its footprint. Seen from above, a box's front
  face changes by 0 at the floor. Without growth, an instance is only the lid, about 1 cm tall.
- **Merging and the area floor.** Blobs whose grown regions meet are merged into one object. An object
  whose **blob** (seed) pixels face the camera with less than `MIN_AREA_M2 = 0.01` in total is noise.
  Growth never counts toward that area.
- **Output.** `Instance(source="difference", label="unknown", camera, mask, color)`, in F_world:
  - appeared instances carry the current capture's points and mask;
  - gone instances carry the baseline's.
  They are ready for `describe.py` and `merge.py`.
- **Numbers.**
  - The real untouched pair gives 0 appeared and 0 gone, in both directions. Its largest untouched
    group is 21 cm², 4.8× under the floor.
  - Every lower tau tried raised that largest blob from 32 to 190+ cm². What sensitivity remains is
    limited by the sensor.
  - `tests/test_difference.py` renders with noise calibrated to that pair: median |dz| 2.4 cm at
    1.4–2 m, neighbour correlation 0.9. Over 30 draws:

    | Case | Detected |
    |---|---|
    | 20 cm box at 1 m | 30/30 |
    | Moved box | 29/30 |
    | 20 cm box at 1.5 m | 21/30 |
    | 15 cm box at 1 m | 5/30 |

  - Runtime is 0.15 s per camera pair (0.24 s when the pose differs).
- **Known gaps.**
  - Objects smaller than 10 × 10 cm are not reported: a mug on the floor falls below the area floor.
  - A multi-frame median per capture is what would let tau and the area floor come down.
  - Found while measuring: the recording's floor is not at z = 0. It sits at +0.2 cm at 0.6 m and
    +9 cm at 1.5 m, about 6 cm per metre, so the nominal 33° mount pitch is about 5° off, or depth
    scale is. This belongs to fuse/mount calibration and is recorded in docs/10.
