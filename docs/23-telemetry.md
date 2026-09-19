# 23 — The telemetry system

One producer, four consumers, one ring buffer. That shape is the whole design.

```
                      ┌──────────────── the Pi ────────────────┐
  balance loop 50 Hz  │  telemetry.py — tap · batch · timestamp │
  ODrive encoders     │  RING BUFFER: last 10 s, always        │
  VL53L5CX ToF        └───────────────┬────────────────────────┘
  capture quality                     │  ws://pi:8080/stream   "telemetry"
                                      │  10 msg/s × 5 samples × N signals
                      ┌───────────────▼─────── laptop: telemetry/hub.py ───────┐
                      │  one asyncio consumer, four fan-outs, no shared state  │
                      └──┬──────────┬──────────────┬──────────────┬────────────┘
                         │          │              │              │
              ES TSDS ◄──┘   Sentry ◄┘      SSE ───┘      Rerun ◄─┘
            (history,      (span data,     (live web      (the demo
             analytics)     breadcrumbs)    dashboard)      screen)
```

---

## 1. The signals

Eight, because they are what the balance loop already computes. Nothing here needs new hardware.

| signal | unit | why it earns a slot |
|---|---|---|
| `pitch` | rad | the robot's lean — the single most diagnostic value |
| `tilt_rate` | rad/s | **the capture quality gate reads this** ([`22`](22-camera-sync.md)) |
| `left_enc` / `right_enc` | turns | odometry source |
| `motor_current_l` / `_r` | A | spikes when the arm shifts the CoM, or when a wheel is fighting something |
| `odom_residual` | m | disagreement between odometry and the SLAM/anchor fix — **drift, made visible** |
| `balanced` | 0/1 | gates arm commands; `not_balanced` refuses them |

Derived per capture, not per sample: `skew_ms`, `coverage_pct`, `icp_residual_mm`.

---

## 2. The ring buffer is the point

`telemetry.py` keeps the **last 10 seconds in memory, always**, regardless of whether anything
is listening. Three things depend on it:

1. **Failure context.** `obs.robot_failure()` attaches the preceding 40 samples as Sentry
   breadcrumbs. "The robot fell over" with a tilt graph attached is a diagnosis; without the
   buffer it is a sentence.
2. **Reconnect gaps.** The laptop drops off wifi for three seconds; on reconnect the Pi
   replays the buffer, so the time series has no hole.
3. **Retroactive capture annotation.** The quality gate needs `tilt_rate` *around* the shutter
   instant, which is only knowable after the fact.

A ring buffer is ~15 lines and it is the difference between telemetry you can debug with and
telemetry you can only chart.

---

## 3. Batching: 400/s becomes 10/s

50 Hz × 8 signals = 400 samples/s. Sent individually that is 400 messages a second down a
socket that also has to stay responsive, on a CPU that is balancing a robot.

**One message per 100 ms carrying 5 samples per signal.** 40× fewer messages, zero loss of
fidelity, and the laptop unpacks it back to 400 docs/s for the bulk write.

```jsonc
{ "t": "telemetry", "from_mono": 81234.400, "hz": 50,
  "signals": { "pitch": [0.021,0.019,0.024,0.022,0.018],
               "tilt_rate": [0.004,0.003,0.006,0.005,0.002], … } }
```

Timestamps are **Pi monotonic**, always. See [`22-camera-sync.md` §3](22-camera-sync.md) — the
laptop never timestamps sensor data.

---

## 4. The four consumers, and why each is separate

`telemetry/hub.py` owns the socket and fans out. Each sink is independent and **a failing sink
must never stall the others** — wrap each in its own task with its own queue and drop-oldest
policy.

| sink | rate | on failure |
|---|---|---|
| **ES TSDS** `robot-telemetry` | bulk 1×/s, ~400 docs | buffer to disk, backfill later (`look_back_time: 7d` exists for this) |
| **Sentry** | not streamed — pulled | span data at capture, breadcrumbs on failure |
| **SSE** → web `/api/events` | 2 Hz, decimated | drop; a dashboard missing a frame is fine |
| **Rerun** | 10 Hz scalars | drop |

Sentry is deliberately **pull, not push**: streaming 400 events/s into Sentry would be absurd
and would blow any quota. Sentry gets telemetry only where it explains something — as span
data on a capture, or breadcrumbs on a failure.

---

## 5. Downsampling is what makes the TSDS worth declaring

Raw: 400 docs/s × 36 h ≈ **50 M documents**. That is real volume, and it is also more than a
trial cluster wants.

TSDS downsampling rolls raw points into interval statistics automatically. Declare it at
template creation:

```
raw  →  5 min buckets after 1 h  →  30 min buckets after 6 h  ·  7 d retention
(min/max/avg/sum per series, per interval — as declared LIVE in the robot-telemetry template)
```

Consequence for anyone querying: **raw 50 Hz samples exist for one hour.** The capture page's
±100 ms latch window only resolves on captures from the last hour; older ones answer at 5-min
granularity, where the peak survives (`max`) but not *when* it happened.

`min` and `max` matter more than `avg` here: the *peak* `tilt_rate` is what rejected a capture,
and an average hides it completely.

This is also the honest answer to "is a TSDS justified at your scale" — dimensions
(`signal`), metrics (`value`), and downsampling are all doing real work, not decorating.

---

## 6. What the telemetry is FOR — three concrete uses

Telemetry you ingest and chart is padding. These three change outcomes:

**a. The capture quality gate.** `tilt_rate_max` over the latch window decides whether a
capture is committed or retried. Telemetry directly gates the perception pipeline.

**b. "Why was this diff wrong?"** The cross-index query — correlate `robot-telemetry` against
the capture's timestamp and find the residual spike 200 ms before the shutter. This is
[`SENTRY_STORY.md`](../SENTRY_STORY.md)'s first real diagnosis and it exists only because the
telemetry is queryable alongside the observations.

**c. Arm-induced instability, measured.** Correlate `motor_current` and `tilt_rate` against
`arm.pick` spans. If arm acceleration is destabilising the balance controller, this is how you
prove it rather than guess — and the fix (gentler trapezoids) is then justified by a number.

---

## 7. Build order

1. `robot/telemetry.py` — tap + ring buffer + batch + WS push. **Works with no consumer attached.**
2. `telemetry/hub.py` — socket consumer, four independent sinks with their own queues.
3. ES bulk writer + the TSDS template with downsampling declared.
4. Wire `capture_quality()` to read the buffer (already in `obs.py`).
5. SSE decimation to the web dashboard.
6. Rerun scalars — nearly free, and it makes the demo screen show the robot's state.

Steps 1 and 4 are the ones the perception pipeline depends on. Do them first.

---

## 8. As built — `robot/telemetry.py` + `telemetry/hub.py`

**Wire format.** Every connection opens with a `hello` carrying the clock pairing, then
telemetry. `from_mono` is authoritative; `from` (ISO) is informational only.

```jsonc
{ "t": "hello", "boot_id": "8ea7249723c2", "hz": 50, "signals": [...],
  "t_mono_base": 81230.123456, "t_wall_base": 1789779300.123, "t_mono_now": 81234.4, "fw": "..." }
{ "t": "telemetry", "from_mono": 81234.400000, "hz": 50, "from": "...", "signals": {...},
  "replay": true }                                     // only on replayed messages
```

**Reconnect.** The hub connects to `/stream?since=<last from_mono seen>&boot=<boot_id>`. Same
boot → the Pi replays the ring after `since` (chunks of 25 samples), then live, with replay and
live joined at one tick under one lock: no gap, no duplicate. Different boot → full ring.

**Time.** Sample `k` is stamped at its scheduled tick `t_mono_base + k/hz`; the pairing is
quantised (wall to 1 ms, mono to 1 µs), so every sample lands on an exact integer-ms
`@timestamp` whether it arrives live or replayed. `robot-telemetry` dedupes on
(`signal`, `@timestamp`), which makes replay idempotent — the ES sink counts those 409s as
`dups`. If the Pi's wall clock is >2 s off the laptop's (no NTP at the venue), the hub keeps the
Pi's monotonic intervals and takes the date from the laptop, once per boot.

**Overruns.** If the sampler thread is starved for more than one tick it **skips** ticks
rather than bursting duplicates; `tel.overruns` counts them and the batch splits so
`from_mono + i/hz` stays exact. A skipped tick is an honest hole, never an invented sample.

**Sinks, as they behave:**

| sink | queue | on failure | pull API |
|---|---|---|---|
| `es` | 600 batches | spool NDJSON to `$TELEMETRY_SPOOL` (default `~/.cache/gitspace/telemetry-spool`, 500 MB cap, drop-oldest); backfill one file per flush once ES answers; queue + buffer spooled on SIGTERM | — |
| `sentry` | 100 | — | `hub.recent(n)`, `hub.peak(signal, t0, t1)`; `balanced` 1→0 → `obs.robot_failure("fell_over")` with the last 2 s decimated to ≤40 breadcrumbs, tagged `fw` + `boot_id`; re-arms after 2 s upright |
| `sse` | 50 | web down/slow: frame dropped (counted) | 2 Hz frames in `web/events.py`'s `telemetry` shape, POSTed to web's loopback `POST /api/internal/event` (`WEB_EVENTS_URL`, default from `WEB_BIND`); includes `tilt_rate_peak` since the last frame. `subscribe()` serves the same frames in-process |
| `rerun` | 100 | drop | `telemetry/<signal>` scalars on the `time` timeline |

Blocking sinks (`es`, `rerun`) run their I/O on their **own** single thread, so a hung call
cannot starve the others of executor threads either.

**Other `/stream` traffic** rides the same socket: Pi side `tel.publish({...})`, laptop side
`hub.on("job", fn)`. Pi `log` messages go to the `gitspace.pi` logger → Sentry Logs.

### 8b. The connections (docs/10 D22–D29)

| to | contract |
|---|---|
| **perception** (capture time) | Pi sends `capture_begin.t_capture_mono`; the hub adds `t_capture_wall` and `ts` — **the** value for `room-clouds.@timestamp` — from the same mapping as telemetry (every `*_mono` key and `frames[].t_mono` gets a `_wall` twin). Nothing on the laptop may stamp a shutter with its own clock. |
| **the gate** | Pi: `tel.peak("tilt_rate", t-0.1, t+0.1, wait_s=0.3)` waits for the future half of the window and returns **None** if it isn't covered; `obs.capture_quality` fails None and doesn't measure it. |
| **roomctl** (jobs) | `JobWatcher(url).start()` in any process → `jobs.wait(job_id, timeout)` → the terminal `job` message. A sinkless hub: it writes nothing and reports nothing. |
| **Sentry** | Only the daemon hub reports: `job failed` and falls → `obs.robot_failure` with 2 s of telemetry and one q70 ≤640 px photo — the frame of the capture the job was planned from (`telemetry/frames.py`, written by the capture assembler). `watch-loop` cron check-in ≤ every 30 s while `detection` messages arrive. |
| **the clock** | The Pi's pairing is trusted within 250 ms (NTP'd Pis are ms-close; un-NTP'd ones are minutes off). Live check: min(arrival − mapped time of newest sample) over 5 s, in stats as `clock_lag_ms`, warns past 250 ms. |

Tests: `pytest telemetry` — every external boundary mocked (fake scope/span/transaction at
`sentry_sdk`, a fake client at `client.bulk()` answering 201/409/400 or raising 401).

**Run it:** `python robot/telemetry.py --fake [--fall-every 30]` and
`python -m telemetry.hub [--pi ws://…/stream]`. Tests: `pytest telemetry` (28, incl. a hung
sink, a throwing sink, and a 1 s mid-stream outage with zero samples lost).
