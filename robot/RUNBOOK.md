# `robot/` runbook — bringing the Pi's server up on the real robot

For Sarah and Ryan. Everything here can be done without the session that wrote `robot/server.py`.
What is built, what is simulated and what has never touched hardware: [`README.md`](README.md) § Status.

---

## 0. This robot runs `bbos` — start here (measured on `bracketbot-0183`, 2026-09-19)

The robot is a **Jetson Orin Nano** (Ubuntu 22.04, python 3.10) running Bracket Bot's `bbos`, not a
Raspberry Pi. Two facts decide everything below:

- **bbos's camera daemon already holds every `/dev/video*`.** Opening one ourselves is `EBUSY`, or
  a fight with their daemon. So the V4L2 / RealSense backends are not the way in here.
- **bbos already publishes the IMU**, so the gate's tilt evidence needs **no change to their
  balance loop at all.** §1's five `sendto()` lines are now the fallback, not the plan.

`robot/bbos.py` reads both from bbos's shared memory through `bbos.Reader` — the same extension
point their own `~/bbapps/examples` use. It reads and commands nothing; no bbos file is touched.
The whole configuration, in `~/gitspace/.env` on the robot:

```bash
ROBOT_MODE=hardware
ROBOT_CAMERAS=cam0=bbos:camera.head.jpeg          # also published: camera.left.jpeg, camera.right.jpeg
ROBOT_TELEMETRY_SOURCE=robot.bbos:read
ROBOT_BBOS_PATH=/home/bracketbot/bbos             # put on OUR sys.path. Never `pip install -e ~/bbos`: it writes into their tree
```
**One dependency:** our venv needs `posix_ipc` (`from bbos import Reader` fails without it — system
python and `~/gitspace/.venv` both lack it today; `~/bbos/.venv` has it). `pip install posix_ipc`
into `~/gitspace/.venv`, nowhere else.

Go / no-go, on the robot, standing still, hands off:
```bash
cd ~/gitspace && .venv/bin/python -m robot.check_source robot.bbos:read --seconds 10
```
Measured with the code as it is: `gate_windows_passing 1.0`, `|tilt_rate|` median 0.010 rad/s,
`source()` p99 0.13 ms; a head-camera latch is a 2560×960 JPEG of ~243 KB, 27–38 ms old, in 20 ms.
Warnings about `motor_current_*` "never changes (0.0)" are expected with the motors idle, and
`odom_residual` is absent because nothing publishes one.

**What `robot/bbos.py` had to get right — check these if bbos is updated:**
| | |
|---|---|
| `imu.orientation.rpy` is **degrees**, `[roll, pitch, yaw]` | `registry.py`'s comment says radians; `base/daemon.py` publishes `math.degrees()`. Measured: d(rpy[1])/dt vs gyro has slope 62 ≈ 57.3. Trusting the comment is a 57× error in pitch |
| pitch rate is **`imu.raw.gyro[1]`**, rad/s | correlation +0.99 with d(pitch)/dt (`gyro[0]` −0.23). A real gyro rate — not a differentiated pitch |
| there is **no published "balanced"** | `base.mode` has no writer. Derived: IMU fresh **and** `|pitch| < ROBOT_BALANCED_PITCH_DEG` (default 20). **This gates `/arm` and is a guess at their fall threshold — check it against the real robot before trusting the arm to it** |
| a camera is read **only when a capture asks** | every `ready()` on a camera topic copies its 4 MB slot twice; a 30 Hz pump would be ~240 MB/s of memcpy on the computer balancing the robot |
| all Readers live on **one thread**, `keeptime=False` | bbos's `Loop` pacing is global and not thread-safe; we never enter it |
| "read-only" means the data | each Reader claims one of 128 slots in bbos's shared timing table, as every bbapp does, and frees it on close |

If the IMU daemon dies: state expires in 100 ms → `tilt_rate` null → captures refused, `/arm`
`not_balanced`. If the camera daemon stalls: `503 camera_unavailable`, never an old frame.

**Open, for perception:** the head camera is **2560×960**, not the 2560×720 the docs assume, and
`perception/depth.py` raises on an eye that is not 1280×720. And `camera.depth` / `camera.points`
are published too — nobody reads them yet, so this camera's gate is `latch_only` (no coverage).

## 1. Any other balance loop — `ROBOT_TELEMETRY_SOURCE` by hand
*(On the bbos robot, §0 replaces this section. Keep reading only if the IMU has to come from
somewhere bbos does not publish.)*

### Why this is step one
The capture quality gate ([`docs/22` §4](../docs/22-camera-sync.md)) refuses a capture unless the
robot was settled when the cameras latched: peak `|tilt_rate|` under **0.05 rad/s** from 100 ms
before the first latch to 100 ms after the last. That number comes from the balance loop, through
`robot/telemetry.py`'s 50 Hz tap.

**Until a source is set, on hardware the tap records NaN, the gate has no evidence, and `POST
/capture` answers `409 capture_rejected` with `rejected_by: ["tilt_rate_max:unmeasured"]` — every
time. That is deliberate.** A gate that passes on missing evidence passes exactly the capture it
exists to reject, and a smeared capture becomes a wrong commit that nothing downstream can detect.
The server says so in one ERROR line at startup. It is not a bug to work around; this section is
the fix. (`--sim` and `--replay` are unaffected: they use a simulated balance source.)

### What a source is
A Python callable, `module:callable`, that returns **the latest state as a dict, instantly**:

| key | unit | who needs it |
|---|---|---|
| `tilt_rate` | **rad/s** (not deg/s) | **the capture gate.** Missing → every capture rejected |
| `balanced` | `1` or `0` | **`POST /arm`.** Missing or 0 → `409 not_balanced`. Also fall detection → the Sentry issue |
| `pitch` | **rad** | charts, and the tilt graph on a `fell_over` issue |
| `left_enc`, `right_enc` | turns | charts |
| `motor_current_l`, `motor_current_r` | A | charts ("did the arm destabilise it?", docs/23 §6c) |
| `odom_residual` | m | charts; `GET /pose`'s `odom_residual_m` |

Send what you have. Only `tilt_rate` and `balanced` gate anything; an absent key is recorded as
NaN and charted as a hole. Names are exact — `tiltRate` is ignored.

It is called **50 times a second from a thread that must never wait**, on the same Pi that is
balancing the robot. So it must not read a serial port, open a socket, or wait on a lock the
balance loop holds. It returns what it was last told.

### The quickest route: five lines in the balance loop
`robot/balance_source.py` already contains the receiving half. Add the sending half to BB's
balance loop (wherever pitch and the gyro rate are already in scope each tick — `lib/lqr.py` /
the balance example), once at startup and once per tick:

```python
import json, socket
_tel = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); _tel.setblocking(False)   # at startup

# inside the loop, after the controller update — names and UNITS as in the table above:
try:
    _tel.sendto(json.dumps({"pitch": pitch_rad, "tilt_rate": pitch_rate_rad_s, "balanced": int(upright),
                            "left_enc": l_turns, "right_enc": r_turns,
                            "motor_current_l": i_l, "motor_current_r": i_r}).encode(), ("127.0.0.1", 8765))
except OSError:
    pass          # telemetry must never be able to disturb the balance loop
```
Loopback UDP cannot block the sender, never leaves the Pi, and costs microseconds. Port:
`ROBOT_BALANCE_UDP_PORT` (default 8765), same value on both sides.

**`tilt_rate` must be the gyro's pitch rate (or the derivative of a *filtered* pitch) — never the
difference of two raw pitch samples.** 3 mrad of pitch noise over a 20 ms tick is 0.2 rad/s of
"tilt rate", four times the gate's limit, on a robot standing perfectly still. This is not
hypothetical: it is exactly why `robot/telemetry.py`'s `FakeRobot` can never pass the gate
(0 of 139 windows). The Madgwick filter already has the gyro rate — send that.

If BB's loop already publishes its state somewhere (a DORA node, a topic, shared memory), skip
the five lines: write a receiver thread that calls `balance_source.HELD.put(state_dict)` and keep
`robot.balance_source:udp`'s reader — or point `ROBOT_TELEMETRY_SOURCE` at your own
`module:callable`. Use `balance_source.Held` either way; see "frozen" below for why.

### Check it — this tells you when you are done
```bash
# 1. the receiving half alone, no robot needed (a SIMULATED sender). Expect: OK
python -m robot.check_source robot.balance_source:udp --fake-sender

# 2. the real thing: balance loop running, robot standing on the floor, hands off
python -m robot.check_source robot.balance_source:udp --seconds 10
```
It samples the source exactly as the tap does and exits 0 only if it is safe to gate on. What it
says, and what to do:

| it says | cause | fix |
|---|---|---|
| `100% of reads returned nothing` | nothing is arriving on the port | is the balance loop running *with* the five lines? same port both sides? `sudo tcpdump -i lo -c 5 udp port 8765` |
| `N% of reads returned nothing` (partial) | the loop sends slower than 10 Hz, or stalls | send every tick. State older than 100 ms is dropped on purpose |
| `tilt_rate … deg/s, not rad/s` | units | `× math.pi / 180` |
| `no ±100 ms window … every capture would be rejected` | `tilt_rate` is too noisy (see the bold paragraph above), or the robot really is not settling | send the gyro rate; if it *is* the gyro rate, the balance tuning is the problem, not telemetry |
| `only N% of windows would pass` (warning) | works, but captures will retry often | same, less urgent |
| `` `tilt_rate` never changes `` | **frozen**: a hard-coded or stuck value | a live IMU always has noise. A constant reads as a perfectly still robot and would **pass every capture** — the dangerous direction |
| `source() takes N ms … doing I/O` | the callable reads hardware itself | return the latest value; receive in a background thread (`Held`) |
| `` `balanced` present in only N% `` | key missing | send `int(upright)`; without it the arm is always refused |

`gate_windows_passing` in its output is the number to watch: the share of moments at which a
capture would have been accepted. Standing still on a hard floor it should be well above 0.5.

### Turn it on
```bash
export ROBOT_TELEMETRY_SOURCE=robot.balance_source:udp        # or in the systemd unit's Environment=
ROBOT_HOST=$(tailscale ip -4) python -m robot.server --hardware
```
Confirm, from the laptop (`PI` = the Pi's tailnet **address**, `100.x.y.z` — MagicDNS names do not resolve there):
```bash
curl -sN "http://$PI:8080/events?types=telemetry&limit=1"     # "tilt_rate":[…five numbers…], not [null,…]
curl -s -X POST "http://$PI:8080/capture" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error') or (d['capture_id'], d['attempt'], d['tilt_rate_max'], d['quality_ok']))"
```
`tilt_rate_max` is a number under 0.05 and `quality_ok` is `true` (or `null` with
`"gate": "latch_only"` if only the stereo camera is plugged in — then the laptop finishes the
gate). `capture_rejected` with `tilt_rate_max` a **number** over 0.05 means the wiring works and
the robot was moving: that is the gate doing its job — let it settle, or watch it retry
(`curl -sN "http://$PI:8080/events?types=capture_rejected,capture_begin"`).

### If the balance loop dies mid-demo
Nothing to do, and that is the point: within 100 ms the state expires, `tilt_rate` goes to
`null` on the stream, captures are rejected as `tilt_rate_max:unmeasured` and `/arm` answers
`not_balanced`. The robot stops committing rather than committing blind. Restart the balance
loop; the server does not need restarting.

---

## 2. Cameras that bbos does NOT own (the RealSense pair, off-robot)
On the robot itself use §0. `V4L2Camera` and `RealSenseCamera` in `capture.py` have **never run
against a device**. Expect to fix something.
```bash
pip install pyrealsense2                       # not installed on the dev laptop; needed on the Pi
export ROBOT_CAM0_PATH=/dev/v4l/by-path/…      # ls -l /dev/v4l/by-path/ — never an index; indices shuffle between boots
python -m robot.server --hardware --once       # one capture, printed, no server
curl -s localhost:8080/healthz                 # "unavailable": which camera did not open, and why
```
- D415 `816612060665` → `cam1`, D435 `938422076694` → `cam2` (defaults; override with `ROBOT_CAMERAS`).
- Two RealSense on one USB controller is the collector README's most common failure; it shows up
  as `camera_unavailable` at startup. Separate controllers, not a hub.
- A missing camera does not stop the server: it captures with the rest. `POST /capture
  {"cameras": [...]}` naming a dead one is a `503` that lists `available`.
- `skew_ms` will be milliseconds-to-tens, not ~0: free-running 30 fps cameras are up to 33 ms
  apart. If it sits near the 25 ms limit: higher frame rate, or the RealSense sync cable +
  `ROBOT_RS_HW_SYNC=1`. Do not widen the gate.
- Lock exposure and white balance once the venue lighting is known: `ROBOT_EXPOSURE`,
  `ROBOT_WB_TEMPERATURE` ([`docs/22` §5](../docs/22-camera-sync.md)). The server warns while unset.
- **The measurement that proves sync** is still [`docs/22` §7 step 5](../docs/22-camera-sync.md):
  wave a hand across all cameras; it must be in one place in every cloud.

## 3. `/drive` `/arm` `/say` `/led`
Simulated. On `--hardware` each answers `503 backend_unavailable` until its module exists. In
`jobs.py`, replace the `_run_drive` / `_run_arm` / `_run_say` methods and `led()`'s body with calls
into `nav.py` / `arm.py` / `audio.py` / `led.py`, and drop the `_need_backend()` call for that
endpoint. Keep everything else: `not_balanced`, `busy`, `job_superseded` and the job messages on
`/stream` are the API's rules, not the actuator's, and `tests/test_robot_server.py` holds them.

## 4. Reaching the Pi
The Pi API has **no auth** and includes `/arm` and `/drive`. Off our own router, bind it to the
tailnet address (`ROBOT_HOST=$(tailscale ip -4)`), not `0.0.0.0` — this removes the LAN fallback,
so it is a decision, written up in [`docs/33` §4](../docs/33-robot-link.md). The link itself —
provisioning, `PI_HOST`, `scripts/push_to_pi.sh` (how this folder reaches the Pi) and
`scripts/verify_robot_link.py` (the proof) — is [`docs/33-robot-link.md`](../docs/33-robot-link.md).
`push_to_pi.sh --start hardware` must run with `ROBOT_TELEMETRY_SOURCE` set (§1), or every capture
is rejected.

## 5. The pose is not real yet — `pose_source: "none"`
On hardware every capture and `GET /pose` carry `x, z, yaw = 0, 0, 0` with `source: "none"`, and the
server warns at startup. Nothing reads a pose yet. **One capture from one spot is fine; captures
from different places must not be fused or compared until this is fixed** — perception places each
cloud with the capture's pose, and nothing downstream checks `pose_source`.

The fix is `robot/pose.py` reading bbos's `slam.pose` (it is published; add it to the one hub
thread in `robot/bbos.py`, next to the IMU). It was deliberately NOT guessed at:
- its axes, units and yaw sense are unknown, and bbos's own comments were already wrong once
  (`rpy` "in radians" is published in degrees). `/pose` must be BB odometry axes — x forward,
  **z left**, yaw radians, counter-clockwise from above ([`docs/20` Fact 3](../docs/20-perception-logic.md)).
  Getting this wrong rotates every cloud 90° about vertical, and only shows once the robot turns.
- so **measure it**: read `slam.pose`, drive 1 m forward, then turn left 90°. Forward must raise
  `x` only; the turn must raise `yaw` by +π/2. Write down what you saw in `robot/bbos.py` the way
  the IMU findings are, and add the test with those numbers.
- check `slam.health` too: a pose from a lost tracker is worse than `"none"`.

## 6. Depth from the robot — `ROBOT_CAMERAS=cam0=bbos:camera.rect` (built; measured 2026-09-19)
`cam0=bbos:camera.head.jpeg` is colour only. **`cam0=bbos:camera.rect`** ships bbos's rectified left
image **with bbos's own depth and intrinsics**, which is what `web/camera_ingest.py` needs to build
the 3D scene and what makes the capture gate `"full"` on the robot. Per latch, measured there:
512×384 colour JPEG 36 KB + depth PNG 60 KB, **15 ms** to encode, coverage 0.986.

Every fact below was measured with `robot/probe_bbos.py` (read-only, runs over ssh stdin, nothing
deployed) — rerun it after any bbos update:
```bash
ssh bracketbot@<robot> 'PYTHONPATH=/home/bracketbot/bbos /home/bracketbot/bbos/.venv/bin/python3 -' < robot/probe_bbos.py
```
| | |
|---|---|
| `camera.depth.depth` is **uint16 millimetres** | camera-frame z of bbos's own points ÷ depth = **0.00100038** over 60,697 px (points taken back through `Config("depth").camera_to_base_3x4`) |
| aligned with `camera.rect.left`, **one frame** | same shape (384×512) and the **identical bbos timestamp** — that identity is the only thing the code pairs on. A depth image over the wrong colour frame is a cloud that looks right |
| `left` is **RGB** | the daemon fills it from `las2_depth_left_rgb`; confirmed by eye (a red wall stayed red). Converted to BGR before JPEG |
| K at 512×384: **fx = fy = 131.21, ppx = 229.07, ppy = 200.75** | the calibration yaml's `P1` × `downsample` 0.4, **and** fitted from bbos's points (0.02 px residual). Read at every start, never hard-coded |
| the pair is **160–207 ms old** at latch | 136 ms of depth pipeline + a 10 Hz period. This camera declares `max_frame_age_s = 0.6`; the rig's 250 ms "stalled" rule is for 30 Hz cameras. The stamp stays the frame's own, so the tilt gate is judged at the frame's time |
| `camera.points` is in the **base** frame, float16 | z is height above the floor, not depth. Not shipped (5 MB) |

**Two knobs, and a decision that is not the robot session's to make alone:**
- `ROBOT_BBOS_DEPTH_FIELD=depth` (default) ships bbos's **confidence-filtered** depth — the pixels
  its own point cloud uses, ~31–39% of the image. `depth_raw` ships every pixel (98.6%), noisier.
- `ROBOT_BBOS_COVERAGE=raw` (default) gives the gate the share of pixels the **sensor** produced
  depth for (0.986). `confident` gives it the filtered share (~0.3–0.4) — and then
  `obs.capture_quality`'s 0.60, which was set for dense SGBM, **rejects every capture**. Both numbers
  ride on every capture (`camera_meta.cam0.coverage_raw` / `coverage_confident`) so nothing is
  hidden; whether 0.60 is the right bar for this sensor is perception's and obs.py's call.

Privacy: this camera sees the room, people included. Frames leave the robot on `/capture`,
`/frames` and `/camera/<name>.jpg`; keep those off public URLs.

## 7. Parking the robot at the desk — for a person, no laptop knowledge needed

The scan only looks for objects **inside the `desk` zone of `room.git/room.yaml`**, and until the pose is real (§5) that
zone is measured **from the robot**, not from the room. A robot parked 30 cm off gives `objects: 0` and no error
anywhere. So park it to these numbers, then let the check tell you.

**What the zone is** (`room.yaml`, metres; x = ahead of the robot, y = to its left, z = up from the floor):

| | the zone | what that means in the room |
|---|---|---|
| ahead (x) | 0.08 → 1.00 | the desk starts **8 cm in front of the robot** and is looked at up to 1 m deep |
| sideways (y) | −0.50 → +0.50 | a 1 m wide strip, **centred on the robot** |
| height (z) | 0.68 → 1.30, `surface: 0.70` | the desk TOP is **70 cm** from the floor; objects up to 60 cm tall |

"The robot" means the point on the floor **directly under the head camera** (the camera is 1.55 m up, looking 33° down).

**Do this**
1. **Measure the desk top** with a tape: floor to top surface. It must be **70 cm ± 3**. A standard 73–76 cm table is NOT
   this desk — tell whoever owns `room.yaml` the real number rather than parking at the wrong height (the zone starts at
   68 cm, so a taller top still falls inside it, but `surface: 0.70` will be wrong).
2. **Roll the robot up to the desk, square on**: facing the long edge, its centre line on the middle of the desk, the
   front of the desk about **a hand's width (8–10 cm) ahead of the camera** — i.e. nearly touching. Closer is better than
   further: everything past 1 m is ignored, and measurement noise is 2 % inside 1 m but 18 % past 3 m.
3. **Keep the desk to 1 m wide × 0.9 m deep of interest.** Things outside that box are not scanned.
4. **The tag** (`room.git/anchors/tag_0.yaml`: AprilTag `tag36h11`, **10 cm**, at x 0, y 0, **z 0.70**, facing the robot):
   stick it flat on the desk's **front edge, centred, its centre at desk-top height** — that is the room's origin. Be
   honest about what it does today: **nothing reads the tag yet** (there is no tag detector in the repo). It marks where
   the origin is for when §5 lands; the scan works without it.
5. **Check, before anyone takes the real captures** (on the laptop, robot server running):
   ```bash
   .venv/bin/python scripts/capture_to_recording.py --check-desk
   ```
   It takes one capture and measures the flat surface in front of the robot. `OK — the desk is where room.yaml expects
   it` means stop adjusting. Otherwise it says what is off in plain words and by how much — *"the desk starts 0.34 m
   ahead; the zone starts at 0.08: roll the robot 0.26 m FORWARD"*, *"the desk top is at 0.75 m, room.yaml says 0.70"*,
   *"the robot is not facing a desk"*. Fix that one thing, run it again.
6. **Then do not touch the robot or the desk.** The pose is a placeholder (§5): every capture is assumed to be from the
   same spot, so a nudge between captures looks like every object moved. Hands off, nobody leaning on the desk, and do
   not walk between the robot and the desk while it captures (a person is 60 000 points of "change").
7. Three captures, and how well they agree — the last table is the number to quote:
   ```bash
   .venv/bin/python scripts/capture_to_recording.py --n 3 --every 4
   ```
   In a hallway with nothing touched, two captures agreed on 89.6 % of voxels within one voxel (9 cm): 2.4 % unmatched
   inside 1 m, 3.4 % inside 2 m, 18.1 % past 3 m. A parked desk scene should be at the good end of that.

**If the robot will not stand still** the capture is refused (`409 capture_rejected`, tilt over the gate) and retried up
to three times; that is the robot telling you it is still settling, not a fault. Wait ten seconds and run it again.
**If the robot was switched off and on**, its server does not come back by itself:
`./scripts/push_to_pi.sh bracketbot@<robot> --start` (§4, `docs/33`).

