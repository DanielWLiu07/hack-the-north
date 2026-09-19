# robot/NOTES.md — found in other sessions' code; not changed by the robot session

Per CONTRIBUTING: not my folders, so written down rather than edited. Each was checked against the
source on 2026-09-19.

## For `obs.py` (cloud)
- **`obs.measure()` uses `tx.set_measurement()`, deprecated in sentry-sdk 2.69** ("will be removed
  in the next major version. Please use `set_data()`"). 85+ DeprecationWarnings per robot test run.
  Works today; a major SDK bump silently stops every chartable measurement (`skew_ms`,
  `tilt_rate_max`, `coverage`, `floor_z`, `n_changed`).
- **`obs.trace_fields()` returns trace ids with no DSN** (also in `elastic/NOTES.md`, with the
  one-line fix). `robot/capture.py` guards locally: it attaches trace fields only when
  `obs.init()` returned True.
- `obs.init()`'s docstring lists roles `'pi' | 'laptop' | 'web'`; the robot server calls
  `obs.init("robot")` as instructed. Nothing filters on the role tag today (grepped).

- **Polling routes will flood Sentry tracing.** sentry_sdk's FastAPI integration opens a
  transaction per request and `obs.init` samples at 1.0: a 2 fps live view of
  `GET /camera/<name>.jpg` is ~170k transactions/day from the robot, before `/healthz` probes.
  Needs a `traces_sampler` in `obs.init` that drops `/camera/*` and `/healthz` — obs.py is not
  mine, so not done.

## For `robot/telemetry.py` (telemetry) — in my folder, not my file
- **`FakeRobot` can never pass the capture gate**: `tilt_rate` is the finite difference of a noisy
  pitch, so the peak over any ±100 ms window is ≥ 0.18 rad/s vs the 0.05 limit (measured: 0/139
  windows). Fine for exercising the stream; the server uses `robot/sim.py SimBalance` instead.
  Left unchanged — `telemetry/test_telemetry.py` depends on it.

## For perception (pointcloud)
- **The Pi's wire format is not yet consumable by `depth.RealSenseDepth`.** `/frames` carries
  colour JPEG + `png16` depth (uint16 mm) + `rig[].intrinsics`; `RealSenseFrame` wants the
  collector's `pointcloud.npy` (N×3 m). Missing piece, laptop side: assemble `/frames` by
  `capture_id`, deproject depth with the intrinsics → `RealSenseFrame`. 3.7 MB of float32 per
  camera per latch is why the cloud is not shipped.
- `pipeline._rig()` builds `StereoDepth` only, so a RealSense recording cannot go through
  `scan_into` yet (docs/20 Part 7 "Status").
- `room-clouds.pose` is mapped `{x, y, yaw}`; `/capture` returns BB odometry `{x, z, yaw}`. Under
  `dynamic: strict` a forwarded `pose.z` rejects the whole doc — convert with `fuse.odom_to_world`
  (docs/13 "As built"). `capture_docs()` currently omits `pose`, so nothing breaks today.

## Found on the real robot (bracketbot-0183) — for perception and for whoever owns bbos upstream
- **The head camera is 2560×960**, not 2560×720. `perception/depth.py` raises on an eye that is
  not `CALIB_SIZE = (1280, 720)`, and the calibration yaml was solved at that size. The first real
  capture through `scan_into` will fail on this until there is a calibration for 1280×960.
- bbos also publishes `camera.depth`, `camera.points`, `camera.rect`, `slam.pose`. Nobody reads
  them. `slam.pose` is what `GET /pose` should return (today: the simulated base) — README "task
  zero" asked whether SLAM gives a global pose; it is at least published.
- bbos `registry.py` says `imu_orientation.rpy` is "in radians"; it is published in degrees.
- `drive.state.iq` read exactly 0.0 on both axes while `torque` was non-zero: `motor_current_*`
  may want `torque` instead. Charts only; nothing gates on it.

## Docs I reconciled that describe other sessions' code
`docs/13`, `docs/20` (asked for), and `docs/04`, `docs/15`, `docs/23`, `web/PAGES.md` (the other
pairs behind the audit's drift warn). Only facts I checked against the source went in, as
**As built** blocks; the original design text is untouched apart from flat contradictions.
`perception/segment.py`, `pipeline.py` and `web/` were being edited while I did it — owners,
please read your block once and correct anything that moved.
