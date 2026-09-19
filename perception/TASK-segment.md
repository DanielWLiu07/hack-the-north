# YOUR JOB — perception: instances & labels

You own `perception/segment.py`, `cluster.py`, `describe.py`, `merge.py`, `associate.py`.
Do NOT touch `depth.py`, `fuse.py`, `voxelize.py`, `serialize.py` — another session owns those.

Read `../docs/15-segmentation.md` and `../docs/20-perception-logic.md` (Part 4, association).

Key insight that makes this easy: `cv2.reprojectImageTo3D` returns an **(H,W,3) array
aligned pixel-for-pixel with the rectified left image**, so a 2D mask is already a 3D
point selection:

    pts = xyz[mask & valid]        # that is the entire lift-to-3D step

Build in this order:
1. `cluster.py` — the FALLBACK path: plane removal (RANSAC ×4) then DBSCAN. Do this
   first because it proves the plumbing without a model. **Plane removal is
   non-negotiable** — leave the table in and every object on it is one blob. Keep the
   removed planes; they become zones.
2. `segment.py` — the PRIMARY path: SAM 3 / YOLO-seg masks on `left_rect`. Start from
   BB's `example_segmentation.py` and `example_yolo.py`.
3. `describe.py` — VLM description per instance **per camera view**. Keep all three.
   Their disagreement is the entity-resolution signal and half the Elastic story;
   never collapse them to one label.
4. `merge.py` — cross-camera merge: centroid < 15 cm + label/embedding agreement + bbox overlap.
5. `associate.py` — the decision table in `20-perception-logic.md` Part 4. The
   "returned" row (object left and came back) needs an Elasticsearch hybrid search,
   not Hungarian matching.

Acceptance: touching objects (a mug against a book) come back as TWO instances, and an
object that leaves for 3 commits and returns keeps its original `object_id`.
