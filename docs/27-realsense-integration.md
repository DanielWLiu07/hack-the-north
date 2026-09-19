# 27 — RealSense integration (Sarah's collector)

Repo: **https://github.com/sarahyoo011725/depth-camera** — "RealSense Data Collection
Pipeline … for the GitIRL project's perception module."

```
D435  938422076694
D415  816612060665

python data_collect.py --d415-serial 816612060665 --d435-serial 938422076694
python data_collect.py --d415-serial 816612060665 --d435-serial 938422076694 --auto --interval 2.0
```

---

## This supersedes several of our assumptions — read before touching perception

| our doc said | reality |
|---|---|
| fisheye stereo + StereoSGBM ([`02`](02-hardware.md)) | **active-IR RealSense D415 + D435.** Depth arrives from the sensor |
| `Q` is in millimetres, divide by 1000 ([`20` Fact 1](20-perception-logic.md)) | `get_point_cloud()` already returns **metres**. `depth_raw.npy` is still **uint16 mm** |
| textureless surfaces kill us ([`R3`](08-risks.md)) | **largely solved.** Active IR does not need texture — this was a top-3 risk |
| three stereo pairs, `grab()`/`retrieve()` sync ([`22`](22-camera-sync.md)) | two RealSense + whatever else; sync story needs re-deriving |

**`docs/22-camera-sync.md` still applies in spirit** — skew on a balancing robot is what
matters, not the sensor type. But `grab()`/`retrieve()` is an OpenCV/V4L2 mechanism;
RealSense has its own frame-arrival semantics and supports hardware sync. Re-derive it.

---

## The contract perception consumes

Per capture, per camera, on disk:

```
capture_0007/
  d415_color.png          HxWx3 uint8 BGR
  d415_depth_raw.npy      HxW  uint16  MILLIMETRES
  d415_pointcloud.npy     Nx3  float32 METRES, CAMERA frame
  d435_*                  same
  metadata.json           timestamp, capture index, serials
```

In-process:

```python
from camera import RealSenseCamera
with RealSenseCamera(serial=..., name="D415") as cam:
    color, depth = cam.get_frames()      # BGR uint8 · uint16 mm
    points = cam.get_point_cloud()       # Nx3 float32, METRES, camera frame
    K = cam.get_intrinsics()
```

### The two unit traps, now in one place
- `get_point_cloud()` → **metres already**. Do NOT divide by 1000.
- `depth_raw.npy` → **millimetres**. Do divide if you use it directly.

Both in the same folder, differing by 1000×. Assert on load:
`assert np.nanmax(np.abs(points)) < 50`.

### Frames are unchanged
Sarah's README is explicit: points are in **camera frame, not robot or world** — the
transform is a per-mount calibration done separately. So
[`20-perception-logic.md` Part 2](20-perception-logic.md) stands exactly as written:
`cam_to_world_axes()` still appears **exactly once**, and the canonical world frame is still
Z-up because the Elasticsearch `cell`/`z_min`/`z_max` schema requires it.

---

## What we should NOT duplicate

Her README's "next steps" list overlaps our plan. Divide it explicitly:

| item | owner |
|---|---|
| ArUco detection → 6-DOF pose | **Sarah** — hers, and it is the anchor we need |
| camera→robot extrinsic calibration | **Sarah** — one-time, per mount |
| "WebSocket server to stream processed pose JSON (not raw frames)" | **ours** — [`16-api.md` §3b](16-api.md) already specs this. Send her the spec rather than letting a second protocol appear |

That last row is the real integration risk: two teams are about to design the same socket.
Ours already exists on paper — binary frames, `capture_id` grouping, atomic captures.

---

## Immediate actions
1. `perception/depth.py` grows a **RealSense source** beside the stereo one. Same output
   contract: `(xyz HxWx3 metres, valid mask, color image)`.
   **Done:** `depth.RealSenseDepth` + `RealSenseFrame.load` ([`20` Part 3](20-perception-logic.md)).
2. Point `fake/` replay at a real `session_*/` folder so the whole chain runs on **real
   depth** with no robot attached.
   **The Pi's half is done:** `python -m robot.server --replay session_0001/` serves a collector
   folder over the real API (`d415`→`cam1`, `d435`→`cam2`; colour as JPEG, `depth_raw.npy` as a
   16-bit PNG in mm). The laptop's half — assembling `/frames` into `RealSenseFrame`s — is not built:
   the wire carries depth, not `pointcloud.npy`, so it needs a deprojection from `rig[].intrinsics`.
3. Re-derive sync for RealSense — and note **her own warning**: two RealSense on one USB
   controller is the most common failure. Separate controllers, not a hub.
   **Done, on paper and in code:** [`22` §8](22-camera-sync.md) and `robot/capture.py`
   (`RealSenseCamera`: newest frameset off a `frame_queue(1)`, dated by `time_of_arrival`; align +
   encode afterwards). **Not yet run against the cameras.** A shared controller shows up as
   `camera_unavailable` at startup — `GET /healthz`.
4. Send Sarah `docs/16-api.md` §3b before she writes a second WebSocket protocol.
