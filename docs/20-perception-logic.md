# 20 — Perception pipeline logic

Stage-by-stage contracts: what goes in, what comes out, **in which frame, in which units**.

Written after reading Bracket Bot's `examples/example_depth.py` and
`examples/example_localization.py` line by line. Three of the facts below are not
guesses — they are what their code actually does, and two of them disagree with each
other.

---

## Part 1 — The three convention facts

### Fact 1: `Q` is in MILLIMETRES

```python
pts_cam = cv2.reprojectImageTo3D(disp, Q) / 1000.0      # <- their line, note the /1000
baseline_m = abs(P2_cam[0, 3] / P2_cam[0, 0]) / 1000.0  # <- and here
```

`lib/stereo_calibration_fisheye.yaml` was produced from a millimetre-scale target, so
everything derived from `Q` and `P` is millimetres. **Divide by 1000 at the boundary,
once, in `depth.py`, and never again.** A second division is a 1000× error that looks
like "every object is at the origin"; a missing one looks like "every object is a
kilometre away". Both are silent.

> **Invariant:** every value leaving `depth.py` is in metres. Assert it —
> `assert np.nanmax(np.abs(pts)) < 50` catches both directions instantly.

### Fact 2: the camera frame is Y-DOWN

OpenCV convention, and their code says so explicitly:

```python
t_cam = np.array([0.0, -1.5, 0.0])   # +Y is down
R_cam = R_scipy.from_euler('x', -36.0, degrees=True)
pts_robot = R_cam.apply(pts_cam) + t_cam
```

So: **X right, Y down, Z forward.** The viewer conversion confirms it —
`_world_to_viewer` maps `(x, y, z) → (x, z, -y)`, flipping Y to get a Z-up viewer.

Both numbers in that snippet are **placeholders to measure, not values to trust**: the
−36° pitch and the 1.5 m height are for whatever rig the example was written against.
Measure ours and put them in `config.py`.

### Fact 3: their odometry uses a DIFFERENT axis meaning — this is the trap

`lib/localization.py`:

> *"Pose estimate in world frame (x points forward, z to the left to match the RHS
> Y-down convention used elsewhere in the codebase)."*

| | X means | Y means | Z means |
|---|---|---|---|
| `example_depth.py` (cloud) | right | down | **forward** |
| `localization.py` (odometry) | **forward** | down | left |

**X and Z swap roles between the two modules we have to combine.** Compose them
naively and every cloud is rotated 90° about the vertical axis relative to the robot's
own pose — which looks like "the room is sideways" or, worse, like drift, because it
only shows up once the robot turns.

---

## Part 2 — Our canonical frame, and where the conversion happens

### We adopt Z-UP for everything downstream of `fuse.py`

Not preference — **our storage schema requires it.** From
[`11-elastic.md`](11-elastic.md): a voxel document is

```json
"cell":  { "x": 1.25, "y": 0.85 },     // the FLOOR-PLANE point (type: point)
"z_min": 0.74, "z_max": 0.81           // HEIGHT above the floor
```

`cell` is a 2-D cartesian `point` used for `within`/`intersects` queries against zones,
and `z` is height. That is only coherent if **Z is up**. The octree cube, the zone
envelopes and every spatial query inherit it.

```
  CANONICAL WORLD FRAME  (right-handed, Z-up, ROS REP-103 style)
      +X  forward from the anchor tag
      +Y  left
      +Z  up          floor at z = 0
```

### One adapter, one place

```python
# perception/fuse.py — the ONLY place this conversion may appear
def cam_to_world_axes(p_yDown):
    """OpenCV (X right, Y down, Z fwd)  ->  canonical (X fwd, Y left, Z up)."""
    x, y, z = p_yDown[..., 0], p_yDown[..., 1], p_yDown[..., 2]
    return np.stack([z, -x, -y], axis=-1)
```

Grep-able rule: **`cam_to_world_axes` appears exactly once in the codebase.** If it
appears twice, one of them is wrong. If it appears zero times, the clouds are sideways.

### The full chain

```
 (u, v, d)          rectified left pixel + disparity
   │  reprojectImageTo3D(disp, Q)            [mm]
   ▼
 F_rect             RECTIFIED left camera · X right, Y down, Z fwd · mm
   │  / 1000.0                                ← Fact 1, once, in depth.py
   ▼
 F_rect (m)         same frame, metres
   │  T_cam←rect  ... see the note below
   ▼
 F_cam              physical camera
   │  T_rob←cam     measured mount pose (pitch, height, yaw per camera)
   ▼
 F_rob              robot body · still Y-down
   │  cam_to_world_axes()                     ← Fact 3 resolved, once, in fuse.py
   ▼
 F_rob (Z-up)       X fwd, Y left, Z up
   │  T_world←rob   robot pose: SLAM if BB provides it, else anchor tag + odometry
   ▼
 F_world            canonical. Everything after this point — clustering, voxels,
                    octree keys, YAML, Elasticsearch — lives here and nowhere else.
```

### The `T_cam←rect` note (this bites at 3 cameras, not at 1)

`reprojectImageTo3D` returns points in the **rectified** frame, which is the physical
camera rotated by `R1`. Their single-camera example ignores this and applies the mount
tilt straight to `pts_cam` — fine when `R1 ≈ I`, which it roughly is for one
well-aligned pair.

With three rigs it stops being fine. **Don't add an `R1ᵀ` term — remove the problem:**

> **Calibrate the extrinsics between the RECTIFIED frames, not the physical cameras.**

Put the ChArUco board where two rigs see it, solve each rig's pose *from its rectified
left image*, and chain those. `R1` is then already inside the number you measured and
never appears in the runtime path. Write the result to `extrinsics.yaml` once and treat
it as immutable ([R10](08-risks.md)).

---

## Part 3 — Stage contracts

Each stage is a pure function. Frame and units are part of the signature.

| # | stage | in | out | frame · units |
|---|---|---|---|---|
| 1 | `capture` | — | `{jpegs[], pose, capture_id, ts}` | — |
| 2 | `rectify` | raw stereo pair | `left_rect, right_rect` | pixels |
| 3 | `disparity` | rectified pair | `disp` float32, `valid` bool | pixels |
| 4 | `reproject` | `disp, Q` | `xyz` **(H,W,3)** | **F_rect · metres** |
| 5 | `segment` | `left_rect` | masks + labels + descriptions | pixels |
| 6 | `instances` | `xyz, masks, valid` | per-camera instances | F_rect · m |
| 7 | `fuse` | instances × 3, extrinsics, pose | instances | **F_world · m · Z-up** |
| 8 | `merge` | instances (all cameras) | merged objects | F_world |
| 9 | `voxelize` | full cleaned cloud | occupied cells + octree keys | F_world |
| 10 | `associate` | merged objects + ES history | objects with **stable ids** | F_world |
| 11 | `serialize` | objects | deterministic YAML | quantized |

**Stage 4 keeps the (H,W,3) shape.** Do not flatten it. The array is aligned
pixel-for-pixel with `left_rect`, which is the entire reason
[mask-first segmentation](15-segmentation.md) is a one-liner:

```python
pts = xyz[mask & valid]        # stage 6, in full
```

Flatten early and you have to carry index bookkeeping through every later stage.

---

## Part 4 — Association: the logic that makes ids stable

Stage 10 is where the system becomes trustworthy or becomes a liar. Three inputs, in
priority order:

```
1. spatial     centroid distance in F_world, hard gate at 1.5 m
2. semantic    hybrid search over ES history (BM25 + Jina dense + rerank)
3. geometric   bbox extent ratio + dominant-colour distance
```

### The decision table

For each merged object in the new scan, against `HEAD`'s objects:

| condition | verdict | what gets written |
|---|---|---|
| matched, `‖Δp‖` < threshold | unchanged | nothing — file byte-identical |
| matched, `‖Δp‖` ≥ threshold | **moved** | pose lines change |
| in scan, no match within 1.5 m | **added** | new file, new id |
| in scan, no spatial match but **ES semantic match** in an older commit | **returned** | **reuse the old id** — history stays continuous |
| in HEAD, absent from scan, cameras had line of sight | **removed** | file deleted |
| in HEAD, absent, occluder between camera and last pose | **unobserved** | carried forward unchanged |

The **returned** row is the one that needs Elasticsearch and cannot be done with
Hungarian matching alone — an object that left the room and came back has no spatial
neighbour to match against. See
[`11-elastic.md`](11-elastic.md#2-object-re-identification-across-absences).

### Occluded vs removed, concretely

```
for obj in HEAD not in scan:
    hist = es.observation_history(obj.id, last_n=40)
    cams_that_ever_saw_it = set(hist.camera)
    for cam in cams_that_ever_saw_it:
        if ray_blocked(cam_pose[cam], obj.last_pose, current_cloud):
            verdict = UNOBSERVED; break
    else:
        verdict = REMOVED
```

`ray_blocked` is a cheap occupancy-grid raycast against the *current* voxel grid —
which is another reason `voxelize` (stage 9) runs before `associate` (stage 10). Order
matters here and it is not obvious from the names.

---

## Part 5 — Quantization: the arithmetic that stops phantom diffs

Two mechanisms, and **both are required**. Quantization alone still flickers when a
value sits on a bucket boundary.

```python
Q_POS = 0.01      # 1 cm
Q_YAW = 5.0       # degrees
HYST  = 1.5       # committed value is kept unless the move exceeds 1.5 quanta

def stabilize(new, committed, q, hyst=HYST):
    """Quantize, but keep the committed value inside the deadband."""
    if committed is not None and abs(new - committed) < q * hyst:
        return committed                       # <- no diff, byte-identical
    return round(new / q) * q
```

Why hysteresis is not optional: an object truly at `0.4250 m` quantizes to `0.42` or
`0.43` depending on noise of ±0.5 mm. Without the deadband that file changes on every
scan and `git status` is permanently dirty for no physical reason.

Applied to: `pose.x/y/z`, `yaw`, `extents`. **Not** to `confidence` or `point_count` —
those never enter the committed YAML at all; they live in Elasticsearch.

### Stable ids

```python
object_id = f"{class_slug}_{sha1(f'{class_slug}|{first_seen_capture_id}|{ordinal}')[:4]}"
```
(`roomctl.state.new_id`. Not `first_seen_commit`: at scan time that commit doesn't exist
yet — the id is inside the files the commit is made of. The capture id is known and unique.)

Assigned **once**, at first sight, then carried forward by association — never
recomputed from geometry. A geometry-derived id changes when the object moves, which
renames the file, which makes every move look like a delete plus an add.

---

## Part 6 — The invariants

Each one is cheap to assert and each one catches a whole class of silent failure.

```python
# after stage 4
assert np.nanmax(np.abs(xyz[valid])) < 50           # metres, not mm (Fact 1)

# after stage 7
assert cloud[:, 2].min() > -0.30                    # nothing far below the floor (Z-up)
assert abs(np.percentile(floor_pts[:, 2], 50)) < 0.05   # the floor IS at z≈0

# after stage 9
assert all(k.startswith(zone_prefix) for k in zone_voxel_keys)   # cube origin unchanged

# after stage 11 — THE test
scan(); commit(); scan()
assert git("diff", "--exit-code").ok                # docs/03: the regression suite
```

The floor assertion is the one that catches an axis mistake immediately. If
`cam_to_world_axes` is wrong or applied twice, the floor is not at `z≈0` and you know
within one capture instead of after an hour of staring at a rotated point cloud.

---

## Part 7 — What to build first, in `pointcloud` and `segment`

Ordered so each step is verifiable before the next depends on it:

1. **`depth.py`** — fork their example. Strip the Rerun and the loop; return
   `(xyz, valid, left_rect)`. Add the metres assertion. *Verify:* log the cloud to
   Rerun, confirm a known 1 m object measures 1 m.
2. **`cam_to_world_axes` + the floor assertion.** *Verify:* floor at `z≈0`, wall
   perpendicular. This is 20 lines and it de-risks everything after it.
3. **`cluster.py`** — plane removal + DBSCAN. *Verify:* object count matches the table.
4. **`serialize.py` + `stabilize`.** *Verify:* scan twice, empty diff. **Gate G2.**
5. **`segment.py`** — masks over geometry. *Verify:* touching objects separate.
6. **`voxelize.py` + `octree_key`.** *Verify:* `test_octree_key` — a point inside cell
   `"370"` has a key starting `"370"`.
7. **`associate.py`** — ES re-identification last, because it needs history to exist.

Steps 1–4 are one person's afternoon and they produce the gate that the whole project
rests on. Do not let steps 5–7 start before step 4 is green.
