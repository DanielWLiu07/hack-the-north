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

Tune `eps` and `distance_threshold` **on the actual demo table in hour one**. They are
scene-specific and everything downstream is built on them.

---

## The five failure modes, and what to do

1. **Touching objects merge.** The defining weakness of geometric clustering — use Approach A.
2. **One object splits in two** (dark side, specular highlight, depth dropout). Merge clusters
   whose OBBs overlap or sit within ~3 cm, or let the image mask arbitrate.
3. **Flat objects eaten by plane removal.** A book lying on the table gets absorbed into the
   table plane. Tighten `distance_threshold`, and check for thin layers just above each plane.
4. **Textureless surfaces produce no points at all** — white mugs, glossy things, glass. This
   is [R3](08-risks.md) and it's a *data* problem, not an algorithm one: textured tablecloth,
   matte patterned objects. Approach A degrades gracefully here (the mask still exists);
   Approach B just loses the object.
5. **The robot itself / people in frame.** Filter by `.roomignore` at the mask stage —
   `person` never becomes an object, and points inside the arm's known workspace volume get
   dropped by kinematics.

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
