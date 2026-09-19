# `robot/` — runs ON the Raspberry Pi 5

**Branch:** `track/robot` · **Owners:** Sarah + Ryan — see the
[split in `TEAM.md`](../TEAM.md#suggested-split-inside-the-robot-track): one on the platform
(wrapping BB's daemons), one on the arm.

## Read this first: we are wrapping, not reimplementing

Bracket Bot ships **daemons/modules for SLAM, mapping and navigation** already. Their GitHub
org also carries a fork of [`dora`](https://github.com/dora-rs/dora) (a dataflow robotics
middleware) and `rules_ros2`, which means their stack is node-based: daemons publishing on
topics, not a monolith we have to replace.

**So `robot/` is an adapter, not a robotics stack.** Its whole job is to expose the six
endpoints and one WebSocket in [`../docs/16-api.md`](../docs/16-api.md) on top of whatever
already runs. If you find yourself writing a Kalman filter, stop — it exists.

### Task zero, before writing any code: inventory the daemons
Thirty minutes at the Bracket Bot booth, and the answers reshape half this document.

- Which daemons ship and run on boot? SLAM? mapping? navigation? costmaps?
- **Does SLAM give us a global pose in a persistent map** that survives a restart?
- Is it DORA nodes, ROS 2 topics, or a Python API? How do we subscribe?
- Is there an existing `goto(x, y, theta)` we can call?
- Does the map persist across sessions, so commits taken an hour apart share a frame?
- What does the balance loop already publish that `telemetry.py` can just subscribe to?

Write the answers into this file. They change what the other three tracks can assume.

### What their SLAM removes from our plan, if it works
This is a **big de-risk**, and it lands on the two nastiest risks in the project:

| our original plan | if their SLAM is good |
|---|---|
| AprilTag anchor + `solvePnP` + odometry integration | **mostly unnecessary** — SLAM provides the world frame |
| ICP refinement against the committed cloud | still useful as a cross-check, no longer load-bearing |
| [R4 phantom diffs from odometry drift](../docs/08-risks.md) | **substantially reduced** — this was our #1 correctness risk |
| [R6 calibration eating a day](../docs/08-risks.md) | unchanged — camera *extrinsics* are still ours to solve |

Keep **one anchor tag up anyway**. It costs a sheet of paper and gives an independent check on
whether the map has drifted — and drift that nobody notices is exactly how the system becomes
a liar.

---

## Files

| file | build or wrap? | purpose |
|---|---|---|
| `server.py` | **built** | HTTP + two WebSockets (`/stream`, `/frames`). Routes only; every handler is a thin call downward. `python -m robot.server --sim \| --replay DIR \| --hardware`. |
| `capture.py` | **built** (hardware backends not yet run on hardware) | All cameras opened once and held. **`grab()` every camera, read the pose, then `retrieve()` each** — never round-robin ([`22`](../docs/22-camera-sync.md)). The quality gate, retried ×3. Replay of recorded frames. |
| `jobs.py` | **built, simulated** | `/drive` `/arm` `/say` `/led` job lifecycle + the API's own rules (`not_balanced`, `busy`, `job_superseded`). The actuators are **simulated**: on hardware every one answers `503 backend_unavailable` until `nav.py` / `arm.py` / `audio.py` / `led.py` exist. **Replace the `_run_*` methods, keep the rest.** |
| `events.py` | **built** | `GET /events`: the structured stream as **Server-Sent Events** — `id: <boot>:<n>`, `Last-Event-ID` resumption, a Pi restart announced as a `gap`, `: keepalive` every 15 s idle ([`16` §3c](../docs/16-api.md)). `/stream` and `/frames` are unchanged beside it. |
| `bbos.py` | **built, run against the real robot** | The robot's own camera and IMU from Bracket Bot's `bbos` shared memory ([`RUNBOOK.md` §0](RUNBOOK.md)) — the only way in on the robot, where bbos holds every `/dev/video*`. `ROBOT_CAMERAS=cam0=bbos:camera.head.jpeg`, `ROBOT_TELEMETRY_SOURCE=robot.bbos:read`. Read-only; nothing of bbos is touched. |
| `balance_source.py` · `check_source.py` | **built** | The seam for the real balance loop ([`RUNBOOK.md` §1](RUNBOOK.md)): a latest-value holder that expires, a loopback-UDP receiver, and the checker that says whether a source is safe to gate captures on. |
| `sim.py` | built | The robot when there is no robot: a settled-but-knockable balance model, the base pose, a synthetic recording to replay. |
| `nav.py` | **wrap** | `drive_to(x, z, yaw)`. If a nav daemon exists, this is a 30-line adapter over it. If only SLAM exists, a simple controller on SLAM pose. |
| `pose.py` | **wrap** | Subscribe to the SLAM/odometry topic, expose `GET /pose`. Report which source the pose came from. |
| `arm.py` | **build** | Feetech STS3215 bus servos via LeRobot. Top-down IK, trapezoidal profiles, `pick`/`place`/`point`/`stow`. **Nothing upstream provides this — it's the real work on this track.** |
| `telemetry.py` | **wrap** | Subscribe to what the balance loop already publishes; batch 5 samples × 8 signals per 100 ms onto the WebSocket. |
| `led.py` | build | `clean`/`dirty`/`conflict`/`working`/`error`. Twenty minutes. |
| `audio.py` | wrap | Stock Whisper + Kokoro examples. |
| `config.py` | **built** | Everything from the environment: mode, port, cameras (`ROBOT_CAMERAS`; a V4L2 camera bound by index is **refused**), the two RealSense serials, gate thresholds, exposure/WB lock. |

## Status — what runs today, and on what

**Verified with no hardware** (68 tests in `tests/test_robot_{capture,server,events}.py`, plus
real-socket runs — the laptop's actual `telemetry/hub.py` against `/stream`, and `curl` against
`/events` over the Tailscale interface): all six endpoints, both sockets and the SSE stream, the
latch order, the gate and its retry, replay of both recording layouts, Sentry spans and measurements.

**Not yet run on hardware — treat as unproven until it is:**
- `V4L2Camera` and `RealSenseCamera` in `capture.py`. Written against the OpenCV and
  `pyrealsense2` APIs, never executed against a device: `pyrealsense2` is not even installed here.
  The first session with the D415 (`816612060665`) / D435 (`938422076694`) should expect to fix
  something in them.
- docs/22 §7 step 5 — wave a hand across all cameras, check it is in one place in all clouds —
  is the measurement that proves sync. Nothing here substitutes for it.
- Two RealSense on one USB controller is the collector README's most common failure. It will show
  up as `camera_unavailable` at startup; `GET /healthz` → `unavailable` says which and why.

**Needs a teammate — [`RUNBOOK.md` §1](RUNBOOK.md) is the whole procedure:**
`ROBOT_TELEMETRY_SOURCE=module:callable` must point at the balance loop's state (the eight signals
of [`23`](../docs/23-telemetry.md)). Without it, on hardware, tilt is unmeasured and **the gate
rejects every capture** — deliberately: it will not pass on missing evidence. The receiving half
exists (`balance_source.py`: five lines in the balance loop feed it over loopback UDP, and state
that stops arriving **expires**, so a dead loop never reads as a still robot), and
`python -m robot.check_source robot.balance_source:udp` says when it is good enough to gate on.

```bash
python -m robot.server --sim                 # everything, simulated. curl -X POST localhost:8080/capture
curl -X POST localhost:8080/sim/bump         # knock it: the next capture is rejected, then retried in the calm
python -m robot.server --replay session_0001 # Sarah's collector folder (docs/27), or perception recordings
curl -sN 'localhost:8080/events?types=hello,telemetry&limit=2'     # SSE: prints 3 events and exits by itself
curl -sN 'localhost:8080/events?types=job,log,capture_begin,capture_rejected'   # watch the robot work
```

**The API has no auth and includes `/arm` and `/drive`.** `ROBOT_HOST` defaults to `0.0.0.0`: fine
on our own router, not on campus wifi. There, bind to the tailnet only —
`ROBOT_HOST=$(tailscale ip -4) python -m robot.server …` (checked: the wifi-side address then
refuses the connection).

## Build order
1. **Inventory the daemons** (above). Write down what's already there.
2. BB quickstart working: `setup_os.sh` → `calibrate_drive.py` → `example_wasd.py` drives.
3. `config.py` + `server.py` with all six endpoints returning **stubbed** JSON.
   **Freeze the contract here** — the other three tracks unblock the moment you do.
4. `pose.py` — wrap SLAM. Cheapest thing with the biggest downstream effect.
5. `capture.py` against the one stock camera.
6. `nav.py` — wrap their nav, or a controller over SLAM pose.
7. `telemetry.py` — cheap, and it's the whole Elastic volume story.
8. `arm.py` — the genuinely hard one. **Bench-test on a fixed base before mounting.**
9. `led.py`, `audio.py` — fill-in tasks while waiting on something else.

## Acceptance criteria
- [~] `curl -X POST <pi>:8080/capture` → JPEGs + pose in under 2 s — **in sim: 0.21 s** (2 cameras × 4 latches,
      320×240). Not measured on the Pi at 2560×720.
- [ ] `GET /pose` returns a **global** pose that survives driving a loop and returning — needs `pose.py` over BB's SLAM; today it is the simulated base
- [~] `wscat -c ws://<pi>:8080/stream` shows telemetry at ~10 msg/s — yes, from the simulated balance source
- [~] `POST /drive` returns a `job_id` in <50 ms; a `done` job event arrives on the socket — yes, simulated drive
- [ ] `POST /arm {"action":"pick"}` completes without the robot falling over

`[~]` = passes against the simulated robot; the box is ticked when it passes on the Pi.

## Gotchas
- **`robot/telemetry.py`'s `FakeRobot` can never pass the capture gate.** Its `tilt_rate` is the
  finite difference of a noisy pitch: measured, the peak over *any* ±100 ms window is ≥ 0.18 rad/s
  against a 0.05 limit (0 of 139 windows pass). Behind the gate it rejects every capture. It is fine
  for what it was written for — exercising the stream — so the server uses `sim.SimBalance` instead.
- **`grab()` has to return when the frame ARRIVED.** A V4L2 queue of one hands back a frame as old
  as the last capture, and a free-running camera's newest frame is up to a frame period old. Each
  backend keeps its newest frame latched with its arrival time; a frame older than 250 ms is
  `camera_unavailable`, never a stale picture.
- **Bind cameras by `/dev/v4l/by-path/...`, never by integer index.** Indices shuffle between
  boots, and at 4am that looks exactly like a dead camera.
- **Refuse arm commands when `balanced == false`** — return `not_balanced`.
- **Arm acceleration is the disturbance**, not velocity. Gentle profiles.
- Never run two arm jobs at once — return `busy`.
- **Don't fight their daemons.** If SLAM and our odometry disagree, trust SLAM and log the
  residual. Two competing pose sources is a bug factory.
