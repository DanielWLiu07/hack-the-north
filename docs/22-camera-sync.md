# 22 — Three cameras: synchronisation and streaming

> **This doc corrects [`02-hardware.md`](02-hardware.md).** That said "round-robin capture —
> open one camera, grab, close, next" to solve USB bandwidth. **That is wrong once you care
> about sync**, because it *maximises* the skew between cameras. The reasoning below replaces
> it.

---

## 1. Why skew matters here specifically

Three cameras with no hardware trigger each capture at their own instant. Fuse clouds from
different moments and you get smeared, ghosted geometry.

You would normally shrug at this — our commits happen with the robot stationary. Except:

**Bracket Bot is a wheeled inverted pendulum. It is never still.** It is continuously making
balance corrections, so "stationary" means "oscillating by a degree or two about the wheel
axis" rather than "not moving".

Do the arithmetic, because it decides the whole design:

| capture skew | robot tilt rate | angular error | lateral error at 2 m |
|---|---|---|---|
| **1500 ms** (round-robin) | 10 °/s | 15° | **520 mm** |
| 200 ms | 10 °/s | 2° | 70 mm |
| **10 ms** (grab/retrieve) | 10 °/s | 0.1° | **3.5 mm** |

Our quantum is **10 mm** ([`20-perception-logic.md`](20-perception-logic.md)). So:

- round-robin skew is **50× the quantum** — every fused commit is garbage
- millisecond skew is **below the quantum** — sync stops being a problem at all

> **Fix the sync and motion compensation becomes unnecessary.** That is worth far more than
> compensating for a skew you chose to create.

---

## 2. The fix: `grab()` then `retrieve()`

OpenCV splits frame acquisition into two calls precisely for multi-camera rigs:

```python
ts = []
for c in cams:            # FAST: dequeues an already-DMA'd V4L2 buffer.
    c.grab()              # microseconds — this is the latch
    ts.append(time.monotonic())

frames = [c.retrieve()[1] for c in cams]   # SLOW: MJPG decode, order irrelevant
```

`grab()` does pointer work on a buffer the kernel already filled; `retrieve()` does the
expensive JPEG decode. **All three latch within milliseconds of each other**, and the decode
cost no longer sits between them.

This only works if **all three cameras are open and streaming continuously** — which is
exactly what round-robin gave up.

### So what about the USB bandwidth argument?

It was overstated for a Pi 5. Three 2560×720 MJPG streams at 30 fps:

```
3 cams × 30 fps × ~400 KB  ≈  36 MB/s  ≈  288 Mbps
```

- **On USB 2 (480 Mbps shared): over budget.** This is where the original worry came from.
- **On USB 3 (5 Gbps, PCIe-attached on Pi 5): ~6% of the link.** Not a problem.

**So: put the cameras on the USB 3 ports.** Two are onboard; the third goes on a powered
USB 3 hub. If it still misbehaves, drop the aux cameras to 15 fps — that is 24 MB/s and sync
is unaffected, because `grab()` latency does not depend on frame rate.

Keep `CAP_PROP_BUFFERSIZE = 1` (their `StereoCamera` already sets it) so `grab()` latches the
newest frame rather than one queued behind five stale ones.

---

## 3. Timestamps: one clock, and it lives on the Pi

- **Never timestamp sensor data on the laptop.** The Pi and laptop clocks differ by an
  unknown offset, and mixing them silently corrupts every time-based query.
- `CAP_PROP_POS_MSEC` is unreliable on V4L2 cameras. Use `time.monotonic()` taken
  immediately after each `grab()`.
- Every frame, every telemetry sample and every capture carries a **Pi monotonic timestamp**,
  converted once to wall-clock at the boundary for Elasticsearch.
- Put `t_mono_base` and its wall-clock pairing in the WebSocket `hello` message, so the laptop
  can map one to the other without guessing.

```jsonc
{ "capture_id": "cap_0912", "t_capture_mono": 81234.5521,
  "frames": [ { "camera": "cam0", "t_mono": 81234.5519 },
              { "camera": "cam1", "t_mono": 81234.5526 },
              { "camera": "cam2", "t_mono": 81234.5533 } ],
  "skew_ms": 1.4,                          // max - min. LOG THIS.
  "tilt_rate_max": 0.031 }                 // rad/s during the latch window
```

---

## 4. The quality gate — cheaper than compensation

We already stream 50 Hz telemetry. Use it to *reject* bad captures rather than to correct them:

```python
def capture_ok(skew_ms, tilt_rate_max, coverage):
    return (skew_ms       < 25      and     # cameras latched together
            tilt_rate_max < 0.05    and     # rad/s — robot genuinely settled
            coverage      > 0.60)           # enough valid depth to be worth it
```

On failure: wait for a quiet window and retry, up to three times. A balancing robot has
moments of relative calm between corrections — **take the picture in one of them.**

Surface the numbers rather than hiding them. `skew_ms` and `tilt_rate_max` belong on the
capture document in Elasticsearch, so when a diff looks wrong you can ask *"was the robot
moving?"* and get an answer instead of a theory. That is the same cross-index query as
[`18-sentry.md`](18-sentry.md)'s "why was this commit bad".

---

## 5. Two settings that matter more than they sound

**Lock exposure and white balance. On all three. Manually.**

```python
c.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)      # 1 = manual on most V4L2 backends
c.set(cv2.CAP_PROP_EXPOSURE, EXPOSURE_US)
c.set(cv2.CAP_PROP_AUTO_WB, 0)
c.set(cv2.CAP_PROP_WB_TEMPERATURE, 4600)
```

Two independent reasons, both of which cost hours if missed:

1. **Auto-exposure makes the same object a different colour to each camera**, which breaks
   the colour term in cross-camera merging and the dominant-colour field on every object
   document. Three cameras disagreeing about *geometry* is signal; three disagreeing about
   *white balance* is noise we chose to create.
2. **Auto-exposure varies exposure TIME**, which varies rolling-shutter readout, which
   varies motion smear per camera. A short fixed exposure freezes the wobble.

**Rolling shutter is real at this resolution.** A 2560×720 sensor reads out over roughly
10–30 ms, so the top and bottom of one frame are different moments. During a tilt correction
that shears the image and the cloud with it. Short exposure plus the tilt-rate gate handles
it; nothing else practical will.

---

## 6. Where the data actually goes

| path | rate | payload | transport |
|---|---|---|---|
| **commit capture** | on demand | 3 cams × 4 frames × ~400 KB ≈ **4.8 MB** | trigger on HTTP, pixels on a **separate** `/frames` WebSocket — binary, see [`16-api.md` §3b](16-api.md) |
| **watch loop** | 1–2 Hz | cam0 only, downscaled, ~60 KB | WebSocket `detection` (results, not pixels) |
| **telemetry** | 50 Hz → 10 msg/s batched | ~2 KB/s | WebSocket |

Three decisions inside that table:

- **JPEG, never raw.** Raw is 5.5 MB per stereo frame; MJPG at q85 is ~400 KB. The cameras
  already deliver MJPG — do not decode and re-encode on the Pi.
- **SGBM runs on the laptop, not the Pi.** Three disparity computations on a Pi 5 CPU is
  seconds; shipping 4.8 MB over our own network is under a second. Transfer wins, and the
  Pi's CPU stays on the balance loop.
- **The watch loop ships detections, not frames.** It runs continuously; streaming pixels at
  2 Hz forever would swamp the link that the commit capture needs to be fast on.

---

## 7. What this changes in the build

For [`perception/`](../perception/) and [`robot/`](../robot/):

1. `robot/capture.py` — **all three cameras opened once at startup and held open**, not
   opened per capture. `grab()` × 3, then `retrieve()` × 3. Return per-frame `t_mono` and
   the computed `skew_ms`.
2. `robot/config.py` — bind by `/dev/v4l/by-path/...`, and record which physical USB port
   each camera is on. A camera silently renegotiating to USB 2 is a bandwidth bug that
   presents as dropped frames.
3. Fixed exposure and WB at open time, same values on all three.
4. The quality gate in §4, with `skew_ms` and `tilt_rate_max` on the capture document.
5. **Verify sync empirically before trusting any of this:** wave a hand across all three
   fields of view and check the hand is in the same place in all three clouds. A stopwatch
   or a phone showing milliseconds works too — photograph it with all three and read the
   digits back.

Step 5 is the one that actually proves it. Everything above is reasoning; that is measurement.
