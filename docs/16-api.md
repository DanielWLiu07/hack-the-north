# 16 — API, transport, and streams

**The contract between every moving part.** Freeze this in hour 2 and the four tracks never
block each other again.

Written to be readable by someone who hasn't touched the project. If you're picking one
document up cold, this is the one that tells you how the pieces talk.

---

## 1. Why there are three different transports

Not everything moving through this system has the same shape, and using one transport for all
of it would be wrong in both directions — polling a 50 Hz stream over HTTP is absurd, and
running a 5 MB image transfer over a WebSocket is needless complexity.

| shape of the traffic | example | correct transport |
|---|---|---|
| ask once, get one answer, ~seconds | "give me a capture" | **HTTP request/response** |
| continuous one-way firehose | 50 Hz IMU telemetry | **WebSocket (server→client push)** |
| "start this, tell me as it goes" | a 30-second pick-and-place | **HTTP to start + WebSocket for progress** |
| something outside us decides | a GitHub PR gets merged | **inbound webhook** |
| 3D visualization | point clouds to Rerun | **Rerun's own gRPC** (already built) |

So: **one HTTP server and one WebSocket, both on the Pi.** The WebSocket is multiplexed by a
message-type field — do not open three sockets.

**As built, the Pi's one port (8080) carries three streams, split by what they carry** — not three
copies of one thing:

| stream | carries | why this transport |
|---|---|---|
| `ws /stream` | every structured message, multiplexed by `t` | the hub's link: `?since=&boot=` replay joined to live under one lock (§3.3) |
| `ws /frames` | camera frames, **binary** | SSE is UTF-8 only; base64 costs +33% on ~5 MB (§3b) |
| `GET /events` | the same structured messages as **Server-Sent Events** | plain HTTP, reconnects by itself, resumes from `Last-Event-ID`, and `curl -N` is a complete client (§3c) |

---

## 2. The Pi's HTTP API

Base URL: `http://<pi-ip>:8080`. No auth (we're on our own private network; see
[`08-risks.md` R1](08-risks.md)). JSON in, JSON out. All errors use the shape in §2.7.

### 2.1 `POST /capture`
Grab synchronized frames from all stereo pairs. **The single most important endpoint.**

**As built — `robot/server.py` + `robot/capture.py`.** Every field below is asserted in
`tests/test_robot_server.py`.

```jsonc
// request — every field optional; a bare `curl -X POST <pi>:8080/capture` works
{
  "cameras": ["cam0", "cam1", "cam2"],   // omit = every camera that OPENED (see `unavailable` on /healthz)
  "frames": 4,                            // latches per camera, 1..16, for majority voting
  "quality": 85,                          // JPEG quality 1..100. Ignored for a camera whose own MJPG is passed through
  "inline": true                          // pixels in THIS response. Default: true only if nobody is on /frames (§3b)
}

// response  (200)
{
  "capture_id": "cap_0912",               // cap_NNNN, counted ACROSS restarts (room-clouds' _id is this)
  "attempt": 2,                           // 1..3 — attempt 1 failed the gate; it was its own capture (cap_0911)
  "started_at":  "2026-09-19T14:22:07.412Z",
  "finished_at": "2026-09-19T14:22:09.031Z",
  "t_capture_mono": 81234.5519,           // THE SHUTTER: earliest latch, Pi monotonic. Wall times above are
                                          // this clock mapped by the hello pairing — never a second clock
  "pose": { "x": 1.24, "z": 0.85, "yaw": 0.31 },   // BB odometry axes, read WITH the latch, before any decode
  "pose_source": "anchor",                // "anchor" | "odometry" | "sim" | "replay" (the pose the recording carries)
                                          // | "none": HARDWARE TODAY — no pose is read yet; x,z,yaw are a (0,0,0) placeholder
  "cameras": ["cam0", "cam1", "cam2"],
  "rig": [ { "camera": "cam1", "kind": "realsense", "model": "D415", "serial": "816612060665",
             "intrinsics": { "fx": 615.2, "fy": 615.4, "ppx": 321.7, "ppy": 238.9, "w": 640, "h": 480,   // illustrative
                             "model": "distortion.inverse_brown_conrady", "coeffs": [0, 0, 0, 0, 0] } } ],
  "skew_ms": 1.4,                         // the WORST latch of the capture: max - min arrival across cameras
  "tilt_rate_max": 0.031,                 // peak |tilt_rate| from 100 ms before the first latch to 100 ms after
                                          // the last. null = the telemetry ring did not cover it -> REJECTED
  "coverage": 0.91,                       // mean share of pixels with depth, over cameras that HAVE depth on
  "coverage_by_camera": { "cam1": 0.93, "cam2": 0.89 },   // the Pi (RealSense). null on a stereo-only rig
  "gate": "full",                         // "full": obs.capture_quality decided all three
                                          // "latch_only": no depth on the Pi, so skew + tilt were decided here and
                                          //   quality_ok is null — perception.depth.depth_capture finishes the gate
  "latch_ok": true, "quality_ok": true, "rejected_by": [],
  "sentry_trace_id": "...", "sentry_span_id": "...",      // ONLY when Sentry is live on the Pi (§6)
  "frames": [
    { "camera": "cam1", "seq": 0, "kind": "color", "fmt": "mjpg",  "t_mono": 81234.5519, "ts": "...",
      "width": 640, "height": 480, "bytes": 41872, "jpeg_b64": "..." },          // *_b64 only when inline
    { "camera": "cam1", "seq": 0, "kind": "depth", "fmt": "png16", "t_mono": 81234.5519, "ts": "...",
      "width": 640, "height": 480, "bytes": 96011, "png_b64": "..." }
    // ... cameras × frames × kinds. A stereo camera has only "color" (2560×720 side-by-side).
  ],
  "inline": true, "frames_clients": 0
}
```

**Depth is on the wire because the RealSense cameras measure it** ([`27`](27-realsense-integration.md)).
A `depth` frame is a **16-bit PNG of uint16 MILLIMETRES, aligned to colour** — the same unit as the
collector's `depth_raw.npy`, lossless, whatever the sensor's own depth scale. It is *not* metres:
divide by 1000 exactly once. `rig[].intrinsics` is what deprojects it; a replayed recording has
none on disk, so it is absent there.

**The quality gate runs on the Pi, before any pixel leaves it** ([`22` §4](22-camera-sync.md)). A
failed attempt is retried in the next quiet window (200 ms of `|tilt_rate| < 0.05`, waited for up
to 2 s), up to three attempts. Each attempt is its own `capture_id`; a rejected one is announced
as `capture_rejected` on `/stream` with its numbers and **ships no pixels**. If all three fail:
`409 capture_rejected` (§2.7) carrying every attempt. One capture at a time — a second `POST`
while one is running is `409 busy`, never queued behind it.

**Latch together, decode separately** — this replaced round-robin. Opening one camera at a
time costs ~0.5 s between rigs, and on a balancing robot that skew is ~50× the 1 cm quantum:
every fused commit would be wrong ([`22-camera-sync.md`](22-camera-sync.md)). Instead `grab()`
all three (microseconds apart), then `retrieve()` and JPEG-encode each. USB bandwidth is
spent on the decode, not the latch. The response carries `skew_ms` and `tilt_rate_max` for
the quality gate (docs/22 §4); a capture that fails it is retried, not committed.

**Why `pose` is in the response.** The cloud is meaningless without knowing where the robot
was standing. Returning it *with* the frames makes the capture atomic — you can never
accidentally pair frames with a pose read a second later.

**Why base64 JPEG and not raw.** Raw is 5.5 MB per stereo frame; JPEG at q85 is ~400 KB.
Base64 adds 33% and keeps everything one JSON document, which is worth it at this scale. If
transfer becomes the bottleneck, switch to `multipart/form-data` — the shape stays the same.

### 2.1b `GET /camera/{name}.jpg` — the latest picture, for a live view
**Not a capture.** No `capture_id`, no quality gate, no retry, nothing on `/stream`, and the replay
position does not move. Polling `POST /capture` for a video would burn an id, run the gate and
announce a capture to the hub for every frame.
```
200  Content-Type: image/jpeg      body: the camera's colour JPEG (bbos's bytes as published; never re-encoded)
     Cache-Control: no-store       X-T-Mono: 81234.551900 (the frame's arrival, Pi monotonic)
     X-Frame-Age-Ms: 38            X-Boot-Id: <hello.boot_id>      Access-Control-Allow-Origin: *
404  not_found            no such camera        503  camera_unavailable   no frame yet, or the newest is > 1 s old
```
**The cap is on the robot:** a camera is re-read at most once per `ROBOT_PREVIEW_MIN_INTERVAL_MS`
(default 250 → 4 fps) *however many clients ask*; inside the interval everyone gets the cached
bytes (measured: 41 requests → 3 reads). On the robot each read copies a 4 MB bbos slot twice, on
the computer that is balancing it. **It never contends with a capture:** the cameras are taken only
if free at that instant — while a capture holds them the cached frame is served, and a capture
waits at most 150 ms for a preview read (~20 ms) to finish. Every capture also refreshes the cache
for free. `GET /healthz` → `preview: {served, reads, last_age_ms}`.
`GET /healthz` also carries `sentry: {"live": true, "rate_limited": {}}` — `live` is `obs.init()`'s
answer, and `rate_limited` names the event categories Sentry is **refusing** right now and for how
many seconds (over quota it answers 429 and the SDK drops them silently: "no spans from the robot"
with a perfect DSN). On the robot the transaction is the FastAPI integration's, named for the route
(`/capture`, op `http.server`, `server_name: robot`); `robot.capture` / `robot.latch` /
`robot.capture_gate` are **spans inside it**, not transactions — search for them as spans.

### 2.1c `GET /map/voxels` — bbos's fused SLAM map
On the robot, Bracket Bot's `mapping` daemon already keeps a fused, SLAM-registered voxel map. This
hands it over as **`application/x-npz`** (`numpy.load`): `coords` float32 (n,3) metres · `colors`
uint8 (n,3) · `labels` int8 (n,) (−1 floor, 1 not floor) · `meta`, one JSON string: `frame`,
`voxel_size_m` 0.03, `origin`, `robot_pos`, `robot_heading`, `stamp_ns`, and `slam` (§2.2's
`pose_bb`). Frame: **bbos's world frame** — the frame of `pose_bb` and of nav goals; crossing to the
room frame is `roomctl/frames.py`'s job, with the measured registration. Headers: `X-Map-Voxels`,
`X-T-Mono`, `X-Map-Age-Ms`, `X-Boot-Id`. `404` off a bbos robot, `503 map_unavailable` if the daemon
is quiet. **Read on demand and cached 2 s** however many ask: the shared-memory slot is 36 MB and
one read costs 243 ms on the robot (measured); it is not compressed because deflate cost another
312 ms there to save 600 KB.

### 2.2 `GET /pose`
Cheap, no camera work. For the executor to check arrival.
```jsonc
{ "x": 1.24, "z": 0.85, "yaw": 0.31, "source": "anchor",      // "anchor" | "odometry" | "sim" | "none" (§2.1)
  "odom_residual_m": 0.004, "balanced": true, "ts": "...", "t_mono": 81234.61 }
```
`balanced` is the newest telemetry sample's; with no sample yet, or a NaN one, it is `false` —
the arm is refused on missing evidence, not allowed on it.

**On the robot the real pose is `pose_bb`, beside `x/z/yaw`, not inside them** — on `/pose` and on
every capture (read with the latch):
```jsonc
"pose_bb": { "x": -1.0317, "y": 0.3848, "heading": 0.3807,        // metres, radians — bbos's WORLD frame,
             "frame": "bbos_world", "source": "slam",              // the frame of /map/voxels and nav goals
             "ok": true, "age_ms": 45, "pgo_count": 249,
             "localized": true, "vo_lost": false, "degraded": false, "stalled": false }
```
`x/z/yaw` are the *old* quickstart's odometry axes (x forward, z left) that
`perception.fuse.odom_to_world` relabels; bbos's world frame is a different convention, and putting
it in those fields would rotate every fused cloud with no error anywhere. So they stay the labelled
placeholder (`source: "none"`) and the real pose travels under its own name. **Use it only when
`ok` is true**: `ok` is false when the tracker is lost or stalled *or the sample is older than
500 ms* — the numbers stay for debugging. Measured: `heading` is the yaw of `slam.pose`'s
quaternion read **scalar-last** `[x,y,z,w]`, and agrees with `mapping.voxels`' `robot_heading` to
0.009 rad, position to 3.7 cm. To the room frame: `roomctl.frames.bb_to_room` /
`bb_yaw_to_heading_room` with the measured registration.

### 2.3 `POST /drive`
Long-running. **Returns immediately with a job id**; progress arrives on the WebSocket.
```jsonc
// request
{ "target": { "x": 1.8, "z": 0.4, "yaw": 1.57 }, "speed": 0.25, "timeout_s": 30 }
// response (202 Accepted, < 50 ms)
{ "job_id": "job_4f2a", "accepted": true }
```

### 2.4 `POST /arm`
Also long-running, also returns a job id. One action per call.
```jsonc
{ "action": "pick",  "pose": { "x": 0.42, "y": 0.18, "z": 0.75, "yaw": 15 },
  "approach": "top_down", "speed": 0.15 }
{ "action": "place", "pose": { ... } }
{ "action": "stow" }
{ "action": "point", "pose": { ... } }     // for the "where are my keys" beat
```
```jsonc
// response (202)
{ "job_id": "job_51bc", "accepted": true, "estimated_s": 28 }
```

**`speed` defaults low on purpose.** The robot is a wheeled inverted pendulum; arm
*acceleration* is the disturbance the balance controller has to reject. See
[`02-hardware.md`](02-hardware.md#the-balance-problem-read-this-before-mounting-anything).

### 2.5 `POST /say`
```jsonc
{ "text": "Merge conflict. The mug was moved in both branches.", "voice": "elevenlabs" }
→ 202  { "job_id": "job_88de", "duration_s": 3.1 }
```

### 2.6 `POST /led`
```jsonc
{ "state": "clean" }    // clean=green · dirty=amber · conflict=red
                        // working=pulsing blue · error=red flash
→ 200  { "state": "clean" }
```
Costs twenty minutes, reads across a crowded room, and judges walking past will ask what the
colours mean. Do it.

### 2.7 Errors — one shape everywhere
```jsonc
// 4xx / 5xx
{ "error": "camera_unavailable",
  "detail": "/dev/v4l/by-path/...usb-0:1.2:1.0-video-index0 did not open",
  "retryable": true }
```
| code | HTTP | means | what the caller does |
|---|---|---|---|
| `camera_unavailable` | 503 | a camera didn't open, stalled (newest frame > 250 ms old), or isn't configured. Body adds `camera` and `available: [...]` | retry once, then capture with `cameras: available` and flag reduced coverage |
| `capture_rejected` | 409 | all three attempts failed the quality gate. Body adds `attempts: [...]`, each with its `skew_ms` / `tilt_rate_max` / `coverage` / `rejected_by` | `retryable`. Write the numbers to `room-clouds` anyway — a rejected capture is evidence — then retry |
| `not_balanced` | 409 | robot is recovering or fallen | **refuse arm commands**, alert the operator |
| `busy` | 409 | an arm job, or a capture, is already running | `retryable`. Queue or drop; never run two arm jobs |
| `backend_unavailable` | 503 | `/drive` `/arm` `/say` `/led` on real hardware: `robot/nav.py` `arm.py` `audio.py` `led.py` are not built. Simulated in `sim`/`replay` mode | nothing moved. Do not treat as done |
| `forbidden` | 403 | the caller's address is not in `ROBOT_ALLOW` (§8) | not retryable from there |
| `bad_request` | 400 | body is not a JSON object, or a field is out of range | fix the request |
| `unreachable_pose` | — | IK found no solution (arrives as a `job` `failed`) | report the op as unapplied (see the *cannot apply hunk* beat) |
| `job_superseded` | — | a newer `/drive` replaced this one (a `job` `failed`) | drop it |
| `drive_timeout` | — | the drive needed longer than `timeout_s` (a `job` `failed`); the base stops where it got to | re-plan |

### 2.8 Running the Pi's API with no robot
The cameras live with whoever has them, so the whole server runs from recorded frames:

```bash
python -m robot.server --sim                    # synthetic recording + a simulated, knockable robot
python -m robot.server --replay session_0001/   # Sarah's collector folder, or perception recordings
python -m robot.server --hardware               # cam0 (V4L2 by-path) · D415 816612060665 · D435 938422076694
python -m robot.server --sim --once             # one capture printed, no server
```
`--replay` reads both layouts that exist: `capture_NNNN/<d415|d435>_{color.png,depth_raw.npy}`
([`27`](27-realsense-integration.md); `d415`→`cam1`, `d435`→`cam2`) and perception's
`capture.json` + `cam0.jpg`, whose recorded JPEG is passed through **byte-identical** and whose
recorded `pose` is the capture's pose. In both, skew and tilt are *measured*, not replayed: the gate
is real. Sim-only: `POST /sim/bump` knocks the robot (the next capture is rejected, then retried in
the calm after it), `POST /sim/fall` tips it (`/arm` answers `not_balanced`); both are 404 on
hardware. `GET /healthz` says which cameras opened. Env: `robot/config.py`.

**On the robot itself** (a Jetson running Bracket Bot's `bbos`, whose camera daemon holds every
`/dev/video*`): `ROBOT_CAMERAS=cam0=bbos:camera.head.jpeg` and
`ROBOT_TELEMETRY_SOURCE=robot.bbos:read` — camera and IMU read from bbos's shared memory,
nothing of theirs touched ([`robot/RUNBOOK.md` §0](../robot/RUNBOOK.md)). `rig[].kind` is then
`"bbos"` with a `topic`; frames are bbos's own JPEG bytes, **2560×960**, `gate: "latch_only"`.
**`ROBOT_CAMERAS=cam0=bbos:camera.rect`** instead ships the rectified left image (512×384) **with
bbos's own depth** — a `png16` frame in millimetres, of the *same* bbos frame as the colour — and
`rig[].intrinsics` (fx fy ppx ppy w h), so `gate` is `"full"` and the laptop can deproject it as-is
([`RUNBOOK` §6](../robot/RUNBOOK.md): every unit and alignment there was measured). Its frames are
~0.2 s older than the head camera's. A capture then also carries `camera_meta`:
`{"cam0": {"depth_scale": 0.001, "coverage_raw": 0.986, "coverage_confident": 0.39}}` — `coverage`
is the raw one by default; the confident share is what bbos's own point cloud uses.

**On `--hardware` one variable is not optional: `ROBOT_TELEMETRY_SOURCE=module:callable`**, the
balance loop's state. Without it `tilt_rate` is unmeasured and every `POST /capture` is
`409 capture_rejected` / `tilt_rate_max:unmeasured` — by design, the gate does not pass on missing
evidence. [`robot/RUNBOOK.md` §1](../robot/RUNBOOK.md) is the procedure:
`robot.balance_source:udp` + five lines in the balance loop, checked with
`python -m robot.check_source`. If the loop later dies, its state expires within 100 ms —
`tilt_rate` goes `null` on the stream, captures are refused and `/arm` answers `not_balanced` —
rather than the last value being held and read as a still robot.

---

## 3. The WebSocket

`ws://<pi-ip>:8080/stream` — **one socket, server→client push, multiplexed by `t`.**
Newline-delimited JSON. The laptop connects once at startup and reconnects with backoff.

### 3.1 Message types

```jsonc
// TELEMETRY — batched. 50 Hz sampled, one message per 100 ms carrying 5 samples.
// from_mono is the Pi's MONOTONIC clock (seconds) at the first sample; the laptop turns it
// into wall-clock with the hello's pairing (docs/22 §3). `from` is informational only.
// "replay": true marks samples re-sent from the 10 s ring after a reconnect.
{ "t": "telemetry", "from_mono": 8123.412, "from": "2026-09-19T14:22:07.400Z", "hz": 50,
  "signals": {
    "pitch":          [0.021, 0.019, 0.024, 0.022, 0.018],
    "tilt_rate":      [0.004, 0.003, 0.006, 0.005, 0.002],
    "left_enc":       [1024.1, 1024.4, 1024.9, 1025.2, 1025.6],
    "right_enc":      [1019.8, 1020.1, 1020.5, 1020.9, 1021.2],
    "motor_current_l":[0.42, 0.44, 0.51, 0.48, 0.45],
    "motor_current_r":[0.40, 0.43, 0.49, 0.47, 0.44],
    "odom_residual":  [0.004, 0.004, 0.005, 0.005, 0.004],
    "balanced":       [1, 1, 1, 1, 1]
  }}
```
> **Why batched.** 50 Hz × 8 signals as individual messages is 400 msg/s, which will drown
> both the socket and the Pi's CPU while it's trying to balance. Batching to 10 msg/s is a
> 40× reduction for zero loss of fidelity. The laptop unpacks to 400 documents/s and bulk
> writes them once a second — see [`13-ingest.md`](13-ingest.md).

```jsonc
// DETECTION — the watch loop, 1-2 Hz, single forward camera, stock YOLO
{ "t": "detection", "ts": "...", "camera": "cam0",
  "objects": [ { "label": "mug", "conf": 0.87, "bbox": [412,208,96,118], "depth_m": 0.74 } ] }

// JOB — progress and completion for /drive, /arm, /say
{ "t": "job", "id": "job_51bc", "state": "moving_to_pick", "progress": 0.35 }
{ "t": "job", "id": "job_51bc", "state": "done",
  "result": { "grasped": true, "final_pose": {...}, "duration_s": 26.4 } }
{ "t": "job", "id": "job_51bc", "state": "failed",
  "error": "grasp_slipped", "detail": "gripper closed to 2mm, expected 78mm" }

// LOG — every WARNING/ERROR from the Pi's `gitspace.*` loggers, for the laptop's screen and Sentry
{ "t": "log", "level": "warn", "msg": "cam2 dropped 2 of 4 frames", "ts": "..." }

// CAPTURE_REJECTED — an attempt failed the quality gate (docs/22 §4). Same fields as capture_begin
// (§3b) and NO pixels follow. Published as it happens, before the retry. Write it to room-clouds.
{ "t": "capture_rejected", "capture_id": "cap_0911", "attempt": 1, "t_capture_mono": 81231.90,
  "skew_ms": 1.2, "tilt_rate_max": 0.13, "coverage": 0.91, "quality_ok": false,
  "rejected_by": ["tilt_rate_max"] }     // "skew_ms" | "tilt_rate_max" | "tilt_rate_max:unmeasured" | "coverage"

// HELLO — the first message on every connection. The clock pairing is REQUIRED: the hub
// rejects telemetry that arrives before it (telemetry/hub.py). boot_id changes when the Pi
// restarts, which tells the laptop there is nothing to replay from.
{ "t": "hello", "boot_id": "b7e1…", "hz": 50, "signals": ["pitch", "tilt_rate", "..."],
  "t_mono_base": 8000.0, "t_wall_base": 1789780927.4, "t_mono_now": 8123.5,
  "cameras": ["cam0","cam1","cam2"], "arm": true, "fw": "gitspace-pi-0.4",
  "rig": [ { "camera": "cam1", "kind": "realsense", "model": "D415", "serial": "816612060665" } ],
  "mode": "hardware", "simulated": false }     // mode: hardware | replay | sim. simulated: true = no real robot
```
`cameras` is the cameras that **opened**, not the ones configured. `calib_sha` is not sent yet —
there is no `extrinsics.yaml` to hash.

### 3.2 Why job progress is on the socket, not the HTTP response
A pick-and-place takes ~30 seconds. If `POST /arm` blocked for 30 s you would get: no progress
for the demo narration, a connection that looks hung, and an HTTP timeout tuning problem.
Returning a `job_id` immediately and streaming `state` transitions means **Rerun and the
robot's voice can narrate the motion as it happens** — which is what turns a 30-second silence
into 30 seconds of the audience watching something work.

### 3.3 Reconnection
The laptop reconnects with exponential backoff (0.5 s → 8 s cap). The Pi keeps a 10-second
ring buffer of telemetry and replays it on reconnect, so a brief drop doesn't punch a hole in
the time series. Longer gaps are simply gaps — and because `look_back_time` is 7 d, a laptop
that buffered to disk during an outage can backfill later
([`13-ingest.md` gotcha 3](13-ingest.md#the-five-gotchas-in-the-order-theyll-hit-you)).

---

## 3b. Frames over WebSocket — separate the socket, not just the message type

If frame data moves to a WebSocket (it should — see below), **it must not share a connection
with telemetry.**

### Why: head-of-line blocking
A capture is ~4.8 MB. Telemetry is 10 messages/second that must never stall. TCP delivers
in order, so while a 4.8 MB payload is in flight **every telemetry message queues behind it** —
on venue wifi that is one to three seconds of the robot's state history simply missing, and
it will look like the telemetry stream is broken rather than like a design choice.

Separate **by latency class**, which is the real axis:

| class | carries | rate | transport |
|---|---|---|---|
| **realtime, must never stall** | telemetry · job progress · logs · detections | 10/s, tiny | `ws://…/stream` |
| **bulk, bursty, may take a second** | camera frames | on demand, MB | `ws://…/frames` |
| **request/response, needs a status code** | drive · arm · say · led · pose | on demand | HTTP |

Two WebSockets means two TCP connections, so a big payload on one cannot block the other.
(One socket with chunking does not fix this — TCP still serialises the connection.)

### Binary, not base64
The original `POST /capture` design base64-encodes JPEGs into JSON, which costs **+33%** —
4.8 MB becomes 6.4 MB, plus encode and decode on both ends. A binary WebSocket frame carries
the JPEG bytes as-is. This is the main reason to prefer the socket for frames.

### One self-describing binary frame

```
 ┌────────────┬──────────────────────┬─────────────────────┐
 │ uint32 LE  │  UTF-8 JSON header   │   JPEG payload      │
 │ headerLen  │  (headerLen bytes)   │   (rest of frame)   │
 └────────────┴──────────────────────┴─────────────────────┘
```
```jsonc
{ "t": "frame", "capture_id": "cap_0912", "camera": "cam0", "seq": 0, "kind": "color",
  "t_mono": 81234.5519, "w": 2560, "h": 720, "fmt": "mjpg" }
```
`kind` is `"color"` (`fmt: "mjpg"`) or `"depth"` (`fmt: "png16"`: uint16 **millimetres**, §2.1).
`t_mono` is when that frame **arrived**, not when it was asked for. `robot.capture.unpack_frame(blob)`
parses one; `Frame.pack()` writes one.

One frame is self-contained — no pairing a header text-frame with a payload binary-frame and
hoping they stay adjacent.

### A capture stays ATOMIC — pair by identity, never by arrival
The entire point of [millisecond sync](22-camera-sync.md) is that the three frames are one
instant. So:

- every frame carries `capture_id`, `camera` and `t_mono`
- the laptop assembles by **`capture_id`**, never by arrival order
- a `capture_begin` / `capture_end` envelope on `/stream` brackets the group:

```jsonc
{ "t":"capture_begin", "capture_id":"cap_0912", "cameras":["cam0","cam1","cam2"],
  "frames_expected": 12, "pose": {...}, "skew_ms": 1.4, "tilt_rate_max": 0.031,
  "t_capture_mono": 81234.5519 /* + every other non-pixel field of the §2.1 response */ }
{ "t":"capture_end",   "capture_id":"cap_0912", "frames_sent": 12 }
```
`frames_expected` counts **binary frames** (cameras × latches × kinds), so a RealSense camera
counts twice per latch. `capture_begin` is published only for a capture that **passed** the gate.
`frames_sent: 0` means nobody was on `/frames` and the pixels went inline in the HTTP response
instead. The hub stamps `ts` / `t_capture_wall` onto it from `t_capture_mono` (`telemetry/hub.py`).

The laptop holds a buffer keyed by `capture_id`, completes on `capture_end`, and **drops the
whole capture on timeout** rather than processing a partial one. A capture missing cam2 is not
a capture with less coverage — it is a capture whose fused cloud has a hole exactly where the
quality gate cannot see it.

### Keep `POST /capture` as the trigger
Command in over HTTP; pixels out over `/frames`. Same split as `/arm`: the request is a request,
the payload is a stream. **As built, the response arrives when the capture is done** (not
immediately) because it carries the gate's verdict and, for a lone `curl`, the pixels: whether to
send them inline is decided by `inline`, defaulting to *true only when no client is on `/frames`*.
A `/frames` client that has gone is noticed at once, so a stale laptop cannot switch `curl` to
`inline: false` and leave it with no pixels. Each `/frames` client has a 256-frame queue; past that
the oldest frame is dropped (the laptop then drops that whole capture on timeout, as above).

## 3c. `GET /events` — the structured stream as Server-Sent Events

§3b split the sockets by **latency class**; this splits by **payload type**. Everything that is
JSON is also served as `text/event-stream`, because for structured events SSE is the better tool:
it is plain HTTP, `EventSource` reconnects on its own and tells the server where it stopped, and
it can be debugged with a bare `curl`. The pixels stay on `/frames` — SSE cannot carry bytes. The
hub stays on `/stream`. **Nothing was removed.** (`robot/events.py`; `tests/test_robot_events.py`.)

**Prove it** — this exits by itself after two telemetry events:
```bash
curl -sN 'http://<pi>:8080/events?types=hello,telemetry&limit=2'
```
```
retry: 2000

event: hello
data: {"t":"hello","boot_id":"d13377b6103c","hz":50,"signals":[...],"t_mono_base":...,"transport":"sse"}

id: d13377b6103c:23
event: telemetry
data: {"t":"telemetry","from_mono":537050.768838,"hz":50,"signals":{"pitch":[...5 samples...],...}}

id: d13377b6103c:24
event: telemetry
data: {...}
```
and resumption, with the last `id:` you saw:
```bash
curl -sN -H 'Last-Event-ID: d13377b6103c:24' 'http://<pi>:8080/events?types=telemetry&limit=2'   # -> :25, :26
curl -sN 'http://<pi>:8080/events?types=job,log,capture_begin,capture_rejected'                    # watch the robot work
```

**Framing.** `event:` is the message's `t` — the same names as §3.1 (`hello` `telemetry` `job`
`log` `capture_begin` `capture_end` `capture_rejected` `detection`). `data:` is the **same one-line
JSON `/stream` sends**; telemetry is the same 5-samples-per-100 ms batch, not a decimated copy.
Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`,
`Access-Control-Allow-Origin: *` (a dashboard page may open an `EventSource` on the Pi directly).

**`id:` is `<boot_id>:<n>`**, `n` counting up by exactly one per message, from 1 at boot.
Boot-scoped on purpose: after a Pi restart `n` starts again, and a bare integer would let a client
resume at "1234" of a *different run* and silently skip what it never saw. What a reconnect gets,
by its `Last-Event-ID`:

| `Last-Event-ID` | the client receives |
|---|---|
| this boot, `n` still in the log | events `n+1…`, then live — no gap, no duplicate |
| this boot, `n` older than the log | `event: gap` `{"reason":"log_overrun","missed_from":a,"missed_to":b}`, then everything kept |
| **another boot — the Pi restarted** | the new `hello`, `event: gap` `{"reason":"boot_changed",…}`, then **everything this boot has logged**, from `:1`. What the old run sent after the client's last id is gone, and the `gap` says so |
| absent | live only. `Last-Event-ID: 0` asks for the whole log |

The log is the last **1000 messages** (about a minute); it is the Pi's own, not the 10 s telemetry
ring `/stream` replays from. `hello` and `gap` carry **no `id:`** — they describe the connection,
not the log, so they never move a client's position. `hello` is the first event on every
connection and carries the clock pairing exactly as on `/stream`, plus `"transport":"sse"`.

**Query.** `?types=job,log` filters by event name — ids keep their true numbers, so a filtered
stream has gaps in `n` by design (`hello` is sent only if asked for, or if `types` is absent).
`?limit=N` closes after N id-bearing events, so a probe or a test terminates. `?heartbeat=S`
(1–60). `?last_event_id=` for a client that cannot set the header.

**Heartbeat.** After **15 s** with nothing sent *on that connection* the server writes the comment
`: keepalive` so an idle proxy does not drop it. An unfiltered stream never idles — telemetry
arrives every 100 ms — so keepalives only appear on a filtered one.

**It cannot reach the control loop.** One consumer taps the telemetry once, through
`Telemetry.stream()` — the same seam a `/stream` client uses; `robot/telemetry.py` is unchanged —
and numbers, logs and fans out **on the event loop**. The 50 Hz sampler thread does what it did
before: one `call_soon_threadsafe` per batch. Each client has a 200-event queue with drop-oldest,
so a `curl` that stops reading costs nobody else anything (`/healthz` → `events.dropped`). No
Elasticsearch, no outbound HTTP, anywhere in `robot/`.

**Sentry** (through `obs.py`): `robot.sse.open` (span data `resumed`, `replayed`, `types`,
`clients`; measurement `sse_clients`) and `robot.sse.close` (`events`, `heartbeats`, `seconds`,
`dropped_total`) — two short spans, not one span held open for the hours a connection lives.

## 4. Elasticsearch: HTTPS only, no socket

The laptop talks to Elasticsearch over ordinary HTTPS with an API key. There is **no
streaming connection** and there doesn't need to be:

| operation | call | rate |
|---|---|---|
| telemetry ingest | `_bulk`, `op_type: create` | 1× per second, ~400 docs |
| observation ingest | `_bulk` | 1× per capture / per watch frame |
| snapshot write | `_bulk` | 1× per commit, ~3000 docs |
| search / aggregation | `_search`, `_query` (ES\|QL) | on demand |

**Only the laptop holds ES credentials.** The Pi never talks to Elasticsearch — it pushes
telemetry over the WebSocket and the laptop does the buffering, batching and writing. That
keeps credentials in one place, keeps the Pi's CPU on the balance loop, and means the stream
is inspectable locally before it goes anywhere.

---

## 4b. The web server — `web/server.py` on the laptop, port 8000

Daniel's browser surface. **The browser never holds the Elasticsearch key** — every query is
proxied here. This is also the one place that shapes ES responses for display, and it logs
exactly what the demo ran.

### `GET /api/status`
```jsonc
{ "branch": "main", "head": "a3f9c1", "clean": false,
  "changes": [ { "type": "modified", "object_id": "mug_a1b2",  "zone": "desk",
                 "delta_m": 0.19 },
               { "type": "deleted",  "object_id": "marker_c3d4", "zone": "desk" },
               { "type": "untracked","object_id": "scissors_9f3a", "zone": "desk" } ],
  "last_capture": "2026-09-19T14:22:09Z" }
```

### `GET /api/search?q=<text>&limit=20&all_time=true`
The money endpoint. Runs the four-stage retriever and **returns match provenance**, so the UI
can show *why* each result matched:
```jsonc
{ "results": [
  { "object_id": "tool_4f2a", "class": "hammer", "score": 0.91,
    "last_seen": { "commit_sha": "a3f9c1", "ts": "...", "zone": "desk",
                   "pose": {"x":1.5,"y":0.3,"z":0.74} },
    "present_now": false,
    "matched_by": { "bm25": false, "vector": true, "rerank_position": 1 },   // ← show this
    "descriptions": [ "a claw hammer, wooden handle",
                      "wooden-handled tool, metal head",     // the VLM called it a mallet
                      "hammer lying on the bench" ],
    "timeline": [ {"commit_sha":"a3f9c1","ts":"...","zone":"desk"}, ... ] } ] }
```
`matched_by.bm25: false` with `vector: true` is the single most persuasive thing on the page
for an Elastic judge — **it is proof that lexical search alone would have missed this result.**
Render it as a visible badge, not a tooltip.

### `GET /api/object/{object_id}`
Full record: every appearance, all descriptions per camera, lifetime stats
(`first_seen`/`last_seen`/`appearances`), and the occlusion verdict if it's absent.

### `GET /api/history?branch=main&limit=50`
Commit list with `parent_sha` so the front end can draw the branch graph, plus
`objects_changed` per commit.

### `GET /api/analytics/{name}`
Named, server-side ES|QL. No arbitrary query strings from the browser.
`zone_volatility` · `dirtiness_histogram` · `most_moved` · `never_moved` ·
`telemetry_correlation?commit_sha=…`

### `POST /api/command`
The CLI surface over HTTP — **the same tool names the agent uses**, allow-listed.
```jsonc
{ "command": "revert", "args": { "ref": "HEAD" } }
→ { "job_id": "job_9a2f", "ops": 3, "estimated_s": 84 }
```

### `POST /api/resolve`
```jsonc
{ "object_id": "mug_a1b2", "resolution": "theirs" }   // "ours" | "theirs"
→ { "job_id": "job_b41c", "applying": "movie-night" }
```

### `GET /api/events` — **SSE, not a WebSocket**
Live updates for the dashboard. Server-Sent Events, because the browser only needs
*server→client*, `EventSource` reconnects automatically with no code, and it passes through
proxies that sometimes eat WebSocket upgrades.

```
event: status      data: {"clean": false, "changes": 3}
event: job         data: {"id":"job_9a2f","state":"grasping","progress":0.4}
event: capture     data: {"capture_id":"cap_0912","commit_sha":"a3f9c1"}
event: conflict    data: {"object_id":"mug_a1b2","ours":{...},"theirs":{...}}
```

> **Two different streaming problems, two different answers.** The Pi→laptop link is a
> WebSocket because it's a high-rate binary-ish firehose on a private network. The
> laptop→browser link is SSE because it's low-rate, one-directional, and reconnection is
> free. Using a WebSocket here would be more code for less reliability.

---

## 5. Webhooks (inbound — things that call *us*)

Both are stretch beats, both are genuinely good, and both need a public URL. Run
`cloudflared tunnel` or `ngrok http 9000` on the laptop and point them at
`roomctl`'s webhook server on port 9000.

### 5.1 GitHub → `POST /hooks/github`
The "pull request against physical space" beat. Someone opens a PR against `room.git`
proposing that the coffee machine move; a human reviews the diff **on github.com**; on merge,
GitHub fires a webhook and the robot executes it.

```jsonc
// GitHub sends (abridged)
{ "action": "closed",
  "pull_request": { "merged": true, "number": 7, "title": "Move the mug to the shelf",
                    "merge_commit_sha": "c1f0aa...", "user": { "login": "..." } } }
```
Our handler: verify `X-Hub-Signature-256` against the webhook secret → ignore anything where
`merged != true` → `room checkout <merge_commit_sha>` → robot executes → post the outcome
back as a PR comment.

> A human approving a diff in a browser and a robot then rearranging a physical room is the
> single most demoable webhook in this project.

### 5.2 Elastic Agent Builder workflow → `POST /hooks/action`
This is the literal reading of the Elastic brief's *"Workflows that close the loop by taking
action."* A workflow finishes its retrieval chain and calls out to us:

```jsonc
{ "tool": "room_checkout", "args": { "ref": "a3f9c1" },
  "reason": "resolved 'before dinner' to commit a3f9c1 via ES|QL over room-events",
  "trace_id": "..." }
```
Handler: validate against an allow-list of tool names, execute, return the result
synchronously so the workflow can report it. **Allow-list, always** — never `eval` a tool name
off the wire.

### 5.3 Outbound notification (optional)
On conflict or a failed op, `POST` to a Slack/Discord webhook. Two lines of code, and at 4am
it means you find out the robot stopped without staring at the terminal.

---

## 6. Sentry — where it sits and what it sees

Sentry's prize wants **two products beyond error monitoring**. Tracing and Logs are the two we
genuinely need anyway, because the hardest bugs in this project span two machines.

```
Pi                              Laptop                        sentry.io
├ obs.init("robot")             ├ obs.init("laptop")
├ errors from server.py         ├ errors from every module
├ spans: robot.capture …        ├ spans: depth, fuse, segment, describe,
└ logs ─── WebSocket "log" ────►│         merge, associate, es_query, git
                                ├ logs                          ▲
                                └ traces ──── HTTPS ─────────────┘
```

**Distributed tracing across the boundary.** The laptop starts a trace for `room status`,
propagates `sentry-trace` and `baggage` headers on its `POST /capture` call, and the Pi
continues the same trace. You then get **one waterfall** showing where the 9 seconds went:

```
room status ─────────────────────────────────────────────── 8.9 s
├─ POST /capture (Pi)  ──────────                            1.6 s
├─ sgbm ×3             ──────────────────                    2.8 s
├─ segment (SAM 3)     ────────────────────────              3.1 s
├─ describe (VLM)      ──────                                0.9 s
├─ es.associate        ──                                    0.3 s
└─ git commit          ─                                     0.1 s
```

That waterfall *is* the Sentry submission — it shows observability shaping the build, which is
exactly what they ask for. Instrument it early; you'll be optimizing that pipeline anyway.

**Tag every span** with `capture_id`, `commit_sha`, and `camera` so a failure in Sentry can be
traced straight back to a document in Elasticsearch.

**The Pi's half, as built.** `obs.init("robot")` runs before `FastAPI()` is constructed, so
sentry_sdk's FastAPI integration opens the transaction for each request **and continues the
laptop's trace from its `sentry-trace` / `baggage` headers** — no code of ours does it, and
`robot/capture.py` must not open a second transaction under it (it only does so outside a request:
`--once`, tests). Inside, per attempt: `robot.capture` → `robot.latch` (one per latch; its duration
*is* the cost of latching) → `robot.retrieve` (one per camera per latch, tagged `camera`) →
`robot.capture_gate` (span data `skew_ms` `tilt_rate_max` `coverage` `quality_ok`), and
`robot.wait_quiet` between attempts. Measurements on the transaction: `skew_ms`, `tilt_rate_max`,
`coverage`, `capture_attempts`. A transaction tagged `capture_rejected=true` had **at least one**
rejected attempt; its `capture_id` and measurements are the *last* attempt's — read the gate spans
for each. `sentry_trace_id` goes on the capture only when `obs.init()` returned true: with no DSN
the SDK still hands out ids, of a trace nobody sent. Nothing in `robot/` makes an HTTP request; the
SDK sends from its own worker thread.

---

## 7. Rerun — its own channel, don't reinvent it

Rerun already has a transport: the laptop runs the viewer, processes call
`rr.connect_grpc("rerun+http://<laptop-ip>:9876/proxy")`, and log calls stream over gRPC.
Both the Pi and the laptop can log to the same viewer. Nothing in this document replaces it.

---

## 8. Ports, one table

| port | host | protocol | carries |
|---|---|---|---|
| **8080** | Pi | HTTP | `/capture` `/pose` `/drive` `/arm` `/say` `/led` |
| **8080** | Pi | WebSocket `/stream` | telemetry · detections · job progress · logs · `capture_begin`/`_end`/`_rejected` |
| **8080** | Pi | WebSocket `/frames` | camera frames, binary (§3b) — its own TCP connection |
| **8080** | Pi | **SSE** `GET /events` | the same structured messages as `/stream`, `text/event-stream`, resumable (§3c) |
| **8080** | Pi | HTTP | `GET /map/voxels` (bbos's SLAM map, §2.1c) · `GET /camera/{name}.jpg` (live view, §2.1b) · `GET /healthz` · sim only: `POST /sim/bump` `POST /sim/fall` |
| **8000** | laptop | HTTP | `web/` dashboard API — `/api/status` `/api/search` `/api/object/{id}` `/api/history` `/api/analytics/{name}` `/api/command` `/api/resolve` |
| **8000** | laptop | **SSE** `/api/events` | live dashboard updates — status · job · capture · conflict |
| **8765** | robot, **127.0.0.1** | HTTP | the edge's robot adapter (`robot/adapter.py`): `/health` `/v1/observation` `/v1/actions` `/registration` — bearer `HOUSEBOT_ROBOT_TOKEN`; simulated until Gate 1 ([`robot/RUNBOOK.md` §6b](../robot/RUNBOOK.md)) |
| **9876** | laptop | gRPC | Rerun |
| **9000** | laptop | HTTP | `/hooks/github` `/hooks/action` — or skip it entirely and poll SQS |
| 443 | cloud | HTTPS | Elasticsearch, Sentry, OpenAI, ElevenLabs |

Static IPs on our own router. Put them in `robot/config.py` and `.env`, never hardcode.
"Our own router" is now one of two modes: off it, the link is a Tailscale tailnet —
[`33-robot-link.md`](33-robot-link.md) is that runbook, with `scripts/verify_robot_link.py` as the
proof. `PI_HOST` is then a `100.x` tailnet **address** (MagicDNS names do not resolve on the
laptop; measured). Nothing in `robot/` reads `PI_HOST` — the Pi never dials the laptop. **The Pi API has no auth and includes `/arm`
and `/drive`**: on `ROBOT_HOST=0.0.0.0` it is offered to whatever network the Pi is on. Off our
own router, bind it to the tailnet address instead (`ROBOT_HOST=$(tailscale ip -4)`); verified:
the campus-side address then refuses the connection. **Until that is possible, set `ROBOT_ALLOW`**
— IPs / CIDRs of who may connect, e.g. `127.0.0.1,<the laptop's wifi address>,100.64.0.0/10`.
Everyone else gets `403 {"error":"forbidden"}` on every route, and the WebSockets close `1008`
before they are accepted; it goes by the real TCP peer, and no forwarding header is believed.
Unset = open (as before), and on hardware the server warns at startup that it is. This matters
beyond `/arm`: `/camera/<name>.jpg` and `/capture` are pictures of a room with people in it.

---

## 9. What we deliberately did NOT build

- **No gRPC/protobuf between Pi and laptop.** JSON is fast enough at these rates and is
  debuggable with `curl` at 4am. That matters more than microseconds here.
- **No message broker** (MQTT, Redis, ROS). One producer, one consumer, one socket. A broker
  is infrastructure to debug, not infrastructure that helps.
- **No auth on the Pi API.** Private network. If the venue forces us onto shared wifi, add a
  bearer token in `config.py` — it's a five-line change, and it's in
  [`10-open-questions.md`](10-open-questions.md).
- **No bidirectional WebSocket.** Commands go over HTTP because they need a status code and a
  body. The socket is push-only, which keeps its failure modes trivial.
