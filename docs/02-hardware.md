# 02 — Hardware

## What Bracket Bot actually is

Sourced from reading [`BracketBotCapstone/quickstart`](https://github.com/BracketBotCapstone/quickstart)
directly (their marketing site is a stub — the company has pivoted to SF and
`docs.bracket.bot` did not resolve for me on 2026-09-17, so **the repo is the spec**).
Confirm all of this at the booth.

| thing | what the code says | where |
|---|---|---|
| Compute | Raspberry Pi 5 | `setup/extras/pwm-pi5.dts`, `setup_hardware_pwm.sh` |
| Drive | Hoverboard motors via **ODrive over UART** | `lib/odrive_uart.py` |
| Balance | **LQR** controller + **Madgwick AHRS** fusion | `lib/lqr.py`, `lib/madgwickahrs.py` |
| IMU | via `lib/imu.py` (model TBC at booth) | `lib/imu.py` |
| **Camera** | **USB fisheye stereo pair, one device, 2560×720 side-by-side, MJPG @30fps** | `lib/camera.py::StereoCamera` |
| Depth | OpenCV **fisheye rectify + StereoSGBM** → reproject via `Q` → point cloud | `examples/example_depth.py` |
| Calibration | `lib/stereo_calibration_fisheye.yaml` (fisheye model, `mtx_l/r`, `dist_l/r`, `R1/R2`, `P1/P2`, `Q`) | `setup/calibrate_stereo_camera.py` |
| ToF | **VL53L5CX** multizone ranging driver bundled | `lib/vl53l5cx_lib/` |
| Optional depth cam | **Intel RealSense** supported | `setup/extras/setup_realsense.sh`, `tests/test_realsense.py` |
| Odometry | differential-drive wheel odometry, **165 mm wheels, 425 mm wheelbase** | `examples/example_localization.py` |
| Audio | mic + speaker, Whisper STT, Kokoro TTS | `tests/test_microphone.py`, `example_whisper.py`, `example_kokoro.py` |
| Voice agent | **OpenAI Realtime over WebRTC, already written** | `examples/example_realtime.py` |
| Servos | hardware PWM on GPIO12 via `rpi_hardware_pwm` (chip=2) | `tests/test_servos.py` |
| LEDs | addressable LED strip | `tests/test_led_strip.py` |
| Viz | **Rerun**, streamed to a laptop over gRPC (`rerun+http://<laptop-ip>:9876/proxy`) | `example_rerun.py`, `example_depth.py` |
| Perception samples | YOLO, segmentation, person-follow | `example_yolo.py`, `example_segmentation.py`, `example_follow.py` |

### Five things in that table that change our plan

1. **We already have a working stereo → point cloud pipeline.** `example_depth.py` is
   fisheye rectification + SGBM + `reprojectImageTo3D` + Rerun logging. Do not rewrite it.
   Fork it into a `capture_cloud()` function on day one.
2. **Rerun is already the house visualizer, streaming to a laptop.** That is our demo
   screen, for free, and it is gorgeous. Build the demo UI as a Rerun *blueprint*
   (3D cloud + object labels + a diff text panel + timeline) rather than any web app.
3. **The OpenAI Realtime voice example already exists.** Voice control ("commit this as
   clean bench") is maybe an hour of glue, not a day. That's the OpenAI prize nearly free.
4. **The LED strip is a free status indicator.** Green = working tree clean, amber = dirty,
   red = merge conflict. Costs 20 minutes, reads across a crowded room, and judges walking
   past the table will ask what the colours mean. Do this.
5. **`rpi_hardware_pwm` is not how we drive SO-101 arms.** Pi 5 has only a couple of
   hardware PWM channels, and SO-101 uses **Feetech STS3215 bus servos on a half-duplex
   serial bus** — daisy-chained, addressed, position-feedback via 360° magnetic encoders.
   They want the Feetech/Waveshare USB bus-servo adapter + `feetech-servo-sdk` (or drive
   them through **LeRobot**, which supports SO-101 natively). Confirm what BB ships with
   the arm variant.

## The arm

SO-101 (the prize pool is literally SO-101 kits, and "Bracket Bot with arms" is what
we've been told we get):

- 6-DOF, 3D printed, fully open source, natively supported by HuggingFace **LeRobot**.
- 6× **STS3215** bus servos, ~30 kg·cm @ 12 V, 1/345 gearing on the follower.
- **This is a tabletop arm.** Small objects, light tools, blocks, cups. Not floor-level,
  not heavy. Plan the entire demo around a table at a height the arm can actually work at.
- Practical path: use LeRobot for servo comms + calibration, write our own simple IK.
  A 6-DOF arm doing **top-down grasps only** collapses to a nearly trivial IK problem
  (position + yaw), which is exactly what we want at 3am.

### The balance problem (read this before mounting anything)

Bracket Bot is a **wheeled inverted pendulum**. Moving an arm on it moves the center of
mass, which is a disturbance the LQR has to reject *after the fact*. This is a known,
studied problem — ETH's approach is to map the arm's CoM position/velocity/acceleration
into predicted body perturbations and feed that forward into the body trajectory
([refs](research-notes.md#balancing--manipulation)).

We will not implement online CoM estimation in 36 hours. Our mitigations, cheapest first:

1. **Move the arm slowly.** Trapezoidal velocity profiles, low accel. Most of the
   disturbance is acceleration-driven.
2. **Mount the arm base as low and as close to the wheel axis as possible**, and
   keep its stowed pose tucked in.
3. **Manipulate from a stop**, with the base commanded to hold position, never during drive.
4. **Consider a physical stop**: a fold-down foot/kickstand or simply letting the chassis
   rest against the table edge while manipulating. Unglamorous, works, saves the demo.
5. Nuclear option if balance + arm never co-exist: **hand-carry the manipulation** — the
   robot navigates and identifies, and a separate fixed-base SO-101 on the table does the
   pick-and-place. Less cool, still a complete demo, and the diff/git story is untouched.

Decide the mount tonight, not Saturday. See [`10-open-questions.md`](10-open-questions.md).

## The camera rig

**Goal:** ~360° horizontal depth coverage from one shutter click.

Layout: the stock forward stereo pair at 0°, plus two more stereo pairs at roughly **±120°**.
Fisheye pairs have wide horizontal FOV, so ±120° gives overlap at the seams, and overlap is
what lets us calibrate them to each other.

### Two constraints that will bite

**USB bandwidth.** Three 2560×720 MJPG streams at 30 fps on one Pi 5 is asking for trouble
(Pi 5: 2× USB3 + 2× USB2, shared controller). Mitigations, in order:
- ~~**Round-robin capture.**~~ **SUPERSEDED — see [`22-camera-sync.md`](22-camera-sync.md).**
  Round-robin does solve bandwidth, but it *maximises inter-camera skew* — on a balancing
  robot that is ~50× our quantization quantum, so every fused commit would be garbage.
  Correct answer: hold all three open on USB 3 and use OpenCV `grab()`/`retrieve()`, which
  latches all three within milliseconds and makes motion compensation unnecessary.
- Failing that: drop to 10 fps / lower resolution for the aux cameras, and put each
  camera on a separate USB controller where possible.
- Do **not** run all three continuously. Nothing in the design needs it.

**Extrinsics between the three rigs.** We need each camera's pose in the robot frame or
the clouds won't fuse. Fastest reliable method: put a **ChArUco board / AprilTag array**
where two rigs can both see it, solve pose per camera, chain the transforms. Single-frame
AprilTag-based extrinsic solves are standard and fast. Refine with ICP between the
overlapping regions of the fused clouds. Budget 2 hours; do it **once, early, and write the
result to a YAML that never changes** — then tape the cameras down hard and never touch them.
*If a camera gets bumped mid-hackathon, all three clouds silently misalign and every commit
becomes garbage. Tape. Everything. Down.*

### If we can get a RealSense
The quickstart already supports it (`setup_realsense.sh`, `lib/camera.py::RealsenseCamera`).
An active-IR depth camera would enormously de-risk the textureless-surface problem (see
[`08-risks.md`](08-risks.md#r3)). Worth asking the BB booth / hardware desk on Friday
whether one exists in the building. Not required — plan assumes stereo only.

## Bring / buy list (do this **today**, 2026-09-17)

- [ ] **2× USB stereo camera modules** matching the stock one (2560×720 MJPG synchronized
      stereo). If unobtainable, fall back: 2× plain USB webcams for RGB-only coverage
      (they contribute detection, not depth) — degrades T2, doesn't kill it.
- [ ] **Powered USB 3 hub** + short high-quality USB cables.
- [ ] **Travel router / your own AP.** Hackathon wifi is the single most likely cause of
      this project failing. Rerun streams to your laptop over the network, and the Pi is
      headless. Own your network. Ethernet + a switch is even better.
- [ ] **Printed ChArUco board + AprilTags** (several sizes), mounted on **rigid foamboard
      or cardboard** — a floppy printed sheet ruins calibration.
- [ ] Camera mounting: 3D-printed brackets if there's a printer, otherwise aluminium
      angle / zip ties / **gaffer tape** (bring gaffer tape).
- [ ] **Object set**: 5–6 curated items that are *textured* (stereo needs texture),
      *graspable by a 30 kg·cm tabletop arm*, and *visually distinct*. Good: a patterned
      mug, a Rubik's cube, a small plush, a coloured marker, a book, a soda can. Bad:
      anything white, glossy, transparent, or heavier than ~200 g.
- [ ] **A tablecloth with pattern/texture** — turns a hostile white table into a
      stereo-friendly surface. Cheap, enormous win.
- [ ] Spare 12 V supply for the arm, multimeter, hex keys, zip ties, velcro.
- [ ] **Pre-downloaded model weights** on the laptop and the Pi (see
      [`09-schedule.md`](09-schedule.md#tonight)).
