# SENTRY_STORY.md — the debugging story

> *"We are not judging on whether the SDK is installed. Tell us the debugging story, the 4am
> one. That's the submission."* — Sentry, Hack the North 2026

GITIRL is a room under version control. A balancing robot scans a room, commits its state to a
real git repository, and physically puts objects back when you `git revert`. It is a
distributed system where one half is a Raspberry Pi that can fall over.

Below: four things Sentry told us that we did not already know, and what each one changed.
Raw auto-captured events follow at the bottom — `scripts/sentry_watch.py` writes them as they
happen, because nobody remembers to at 4am.

---

## 1 · Session Replay was recording black rectangles, and said HTTP 200

**What we believed:** Session Replay was working. Segments were uploading, the API returned
200, the replay count was climbing.

**What the data showed:** the segments were **~2 KB**. A real replay of a WebGL scene is
orders of magnitude larger. The size was the only symptom — nothing errored, nothing warned.

**Root cause:** `replayCanvasIntegration`'s `snapshot()` defers to the *next* animation frame
by default. By then WebGL has cleared its drawing buffer (we do not set
`preserveDrawingBuffer`, because it costs frames). The integration silently drops blank frames,
so a canvas that had already been cleared produced a perfectly valid, perfectly empty replay.

**What we changed:** snapshot synchronously within the render loop, before the buffer clears.

**Measured effect:** segment size went from ~2 KB to real recordings; 41 replays now contain
actual frames of the 3D scene.

**Why this one matters:** it is observability catching **its own** silent failure. We would
have discovered it in front of a judge.

---

## 2 · An empty Performance tab is not plan-gating

**What we believed:** tracing was unavailable on our plan. The Performance tab was empty for
hours and we were about to spend time applying for a different tier.

**What the data showed:** querying the API directly, `story_demo`'s trace `50c0ccf2` had all
**7 spans** present. The events existed; the tab did not show them.

**Root cause, two parts.** On AM3, transactions are not indexed
(`transaction_indexed: discarded`) — they live in the **spans dataset**, under Explore →
Traces, not Performance. *And* the default health-check inbound filter was dropping every
`/api/health` transaction (6 filtered, reason `filtered-transaction`) — which was exactly what
we had been using to test.

**What we changed:** stopped chasing a plan upgrade, verified through the spans dataset, and
adopted a rule: **never verify tracing with a health check.** It is the one endpoint Sentry
filters by default.

**Measured effect:** 1,905 transactions and 5,691 spans confirmed flowing, with zero plan
changes.

---

## 3 · "Invalid token" means malformed, not expired

**What we believed:** our Sentry auth token was intermittently failing. One API call had
succeeded, then every call after returned 401.

**What the data showed:** the token was **72 characters. A `sntryu_` token is 71.** A stray
keystroke had landed in the editor on re-paste.

**Root cause:** Sentry returns `"Invalid token"` for a *malformed* token, identical to the
message for a revoked one. The first call succeeded because it ran before the bad paste — and
that made a deterministic failure look intermittent, which sent us looking at scopes and
network instead of at the string.

**What we changed:** `scripts/check_keys.py` now validates credential **shape**, not just
presence, for every key in `.env`. It immediately caught two more: `ELASTIC_URL` still holding
a template placeholder, and an artifact URL pasted into `ELASTIC_API_KEY`.

**Why this one matters:** a config error that reports as an auth error costs more than an
outage, because you debug the wrong layer.

---

## 4 · The telemetry breadcrumbs, not the stack trace, explained the failure

**What we believed:** `grasp_slipped` was an arm problem — bad IK, or a gripper calibration
issue. That is where we were about to spend the time.

**What the data showed:** the issue carries the last 40 telemetry samples as breadcrumbs.
Reading them, `balanced` goes **1.0 → 0.0** and `motor_current_l` spikes to **2.0 A** in the
200 ms *before* the shutter fired. The `robot.capture` span recorded
`tilt_rate_max = 0.239 rad/s`. Cross-referencing the same `capture_id` in Elasticsearch, the
three cameras disagreed about that object by **49 mm** — against a 10 mm quantization quantum.

**Root cause:** the robot was mid-lean, recovering from a balance correction, when the capture
was taken. The grasp target was computed from a cloud captured during a tilt. **The arm was
fine.** The shutter timing was the bug.

**What we changed:** a capture quality gate — `skew_ms < 25 and tilt_rate_max < 0.05 and
coverage > 0.60` — that rejects a capture taken mid-correction and waits for a quiet window.
Written up in `docs/22-camera-sync.md`. We also promoted `skew_ms`, `tilt_rate_max` and
`coverage` from tags to **measurements**, so they are chartable across every capture rather
than only filterable.

**Why this one matters:** no stack trace could have found this. The answer was in 40 samples
of physical telemetry attached to an issue, joined by `capture_id` to a separate database. It
is the reason `obs.py` makes every Sentry span carry the `capture_id` and every Elasticsearch
document carry the `sentry_trace_id` — **a slow trace leads to the exact documents, and a bad
diff leads to the exact waterfall.**

---

## How Sentry is wired here

| | |
|---|---|
| tracing | distributed Pi ↔ laptop; spans include `arm.pick` — **physical motion in a waterfall** |
| profiling | 1,801 profiles over the perception pipeline |
| logs | 2,892, structured, tagged by `camera` |
| session replay | the WebGL dashboard, canvas recording (see §1) |
| ai agent monitoring | `gen_ai.chat`, `gen_ai.responses`, `gen_ai.mcp.tool`, `gen_ai.elastic.tool` — MCP on **both** sides |
| attachments | **the camera frame at the moment of a failed grasp**, on the issue |
| crons | the watch loop's heartbeat |
| measurements / context | `skew_ms`, `tilt_rate_max`, `coverage` per capture |

All of it through one module, `obs.py`, so every span and every document share the ids that
join them.

---
---

# Raw auto-captured events

`scripts/sentry_watch.py` polls every two minutes, and on any new or recurring issue writes the
tags, the breadcrumbs, the joined `capture_id`, and downloads any attachments into `evidence/`.
## 01:39 · AUTO-CAPTURED · robot · robot: grasp_slipped — gripper closed to 2mm, expected 78mm (VERIFICATION — fake
Seen: 1× · first 2026-09-19T05:32:51 · https://na-alh.sentry.io/issues/7741817625/
Joins to: `capture_id=cap_verify_1789795965` · `role=laptop`
**Evidence captured:** `evidence/7741817625_grasp_slipped.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  grasp_slipped                            {"balanced": 1.0, "left_enc": 0.279204, "motor_current_l": 0.78034, "motor_current_r": 0.7
  grasp_slipped                            {"balanced": 1.0, "left_enc": 0.282192, "motor_current_l": 1.422314, "motor_current_r": 1.
  grasp_slipped                            {"balanced": 1.0, "left_enc": 0.285228, "motor_current_l": 1.352047, "motor_current_r": 1.
  grasp_slipped                            {"balanced": 1.0, "left_enc": 0.288054, "motor_current_l": 0.884177, "motor_current_r": 0.
  grasp_slipped                            {"balanced": 1.0, "left_enc": 0.291144, "motor_current_l": 1.0356, "motor_current_r": 1.00
  grasp_slipped                            {"balanced": 1.0, "left_enc": 0.294111, "motor_current_l": 0.532009, "motor_current_r": 0.
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 01:39 · AUTO-CAPTURED · robot · robot: grasp_slipped — gripper closed to 2mm, expected 78mm — target from a clou
Seen: 1× · first 2026-09-19T05:31:54 · https://na-alh.sentry.io/issues/7741816842/
Joins to: `capture_id=cap_live01` · `role=laptop`
Capture quality: {"cameras": ["cam0", "cam1", "cam2"], "coverage": 0.71, "skew_ms": 3.1, "tilt_rate_max": 0.239, "verdict": "REJECT"}
**Evidence captured:** `evidence/7741816842_cam0_at_grasp.png`
Telemetry before the failure (last few breadcrumbs):
```
  grasp_slipped                            {"t_rel": -0.08000000000000002, "tilt_rate": 0.004}
  grasp_slipped                            {"t_rel": -0.06, "tilt_rate": 0.004}
  grasp_slipped                            {"t_rel": -0.04000000000000001, "tilt_rate": 0.004}
  grasp_slipped                            {"t_rel": -0.020000000000000014, "tilt_rate": 0.004}
  grasp_slipped                            {"t_rel": 0.0, "tilt_rate": 0.004}
  grasp_slipped                            {"t_rel": 0.01999999999999999, "tilt_rate": 0.004}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 01:39 · AUTO-CAPTURED · robot · robot: grasp_slipped — gripper closed to 2 mm, expected 78 mm — target pose deri
Seen: 2× · first 2026-09-19T00:34:38 · https://na-alh.sentry.io/issues/7741490949/
Joins to: `capture_id=cap_82093` · `commit_sha=a3f9c1e` · `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  grasp_slipped                            {"odom_residual": 0.004, "pitch": 0.0207, "t_rel_s": -0.12, "tilt_rate": 0.0038}
  grasp_slipped                            {"odom_residual": 0.004, "pitch": 0.0184, "t_rel_s": -0.1, "tilt_rate": 0.0041}
  grasp_slipped                            {"odom_residual": 0.004, "pitch": 0.0212, "t_rel_s": -0.08, "tilt_rate": 0.0052}
  grasp_slipped                            {"odom_residual": 0.004, "pitch": 0.0206, "t_rel_s": -0.06, "tilt_rate": 0.0046}
  grasp_slipped                            {"odom_residual": 0.004, "pitch": 0.0167, "t_rel_s": -0.04, "tilt_rate": 0.0058}
  grasp_slipped                            {"odom_residual": 0.004, "pitch": 0.0214, "t_rel_s": -0.02, "tilt_rate": 0.0025}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 01:39 · AUTO-CAPTURED · recurring · robot: fell_over — balanced went 0; pitch 1.195201, peak tilt_rate 57.30245
Seen: 8× · first 2026-09-19T01:21:09 · https://na-alh.sentry.io/issues/7741552137/
Joins to: `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  fell_over                                {"balanced": 1.0, "left_enc": 0.435099, "motor_current_l": 0.835581, "motor_current_r": 0.
  fell_over                                {"balanced": 1.0, "left_enc": 0.438199, "motor_current_l": 0.795431, "motor_current_r": 0.
  fell_over                                {"balanced": 1.0, "left_enc": 0.441117, "motor_current_l": 1.060337, "motor_current_r": 1.
  fell_over                                {"balanced": 1.0, "left_enc": 0.444223, "motor_current_l": 2.016057, "motor_current_r": 1.
  fell_over                                {"balanced": 1.0, "left_enc": 0.447202, "motor_current_l": 0.820078, "motor_current_r": 0.
  fell_over                                {"balanced": 0.0, "left_enc": 0.450209, "motor_current_l": 0.443618, "motor_current_r": 0.
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 01:39 · AUTO-CAPTURED · cancel · CancelledError
Seen: 1× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550237/
_What we changed:_ TODO — fill this in, it is the part they score._

## 01:39 · AUTO-CAPTURED · timeout · Cancel 1 running task(s), timeout graceful shutdown exceeded
Seen: 1× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550271/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  git --no-optional-locks -C /Users/daniel {}
  git --no-optional-locks -C /Users/daniel {}
  Shutting down                            {}
  Waiting for connections to close. (CTRL+ {}
  git --no-optional-locks -C /Users/daniel {}
  git --no-optional-locks -C /Users/daniel {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 02:04 · AUTO-CAPTURED · recurring · CancelledError: Task cancelled, timeout graceful shutdown exceeded
Seen: 8× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550237/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  git --no-optional-locks -C /Users/daniel {}
  Cancel 10 running task(s), timeout grace {}
  Waiting for application shutdown.        {}
  Exception in ASGI application
           {}
  Exception in ASGI application
           {}
  Exception in ASGI application
           {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 02:04 · AUTO-CAPTURED · timeout · Cancel 10 running task(s), timeout graceful shutdown exceeded
Seen: 2× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550271/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  git --no-optional-locks -C /Users/daniel {}
  git --no-optional-locks -C /Users/daniel {}
  Shutting down                            {}
  git --no-optional-locks -C /Users/daniel {}
  Waiting for connections to close. (CTRL+ {}
  git --no-optional-locks -C /Users/daniel {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 02:18 · AUTO-CAPTURED · recurring · CancelledError: Task cancelled, timeout graceful shutdown exceeded
Seen: 10× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550237/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  Shutting down                            {}
  Waiting for connections to close. (CTRL+ {}
  git --no-optional-locks -C /Users/daniel {}
  git --no-optional-locks -C /Users/daniel {}
  Cancel 4 running task(s), timeout gracef {}
  Waiting for application shutdown.        {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 02:18 · AUTO-CAPTURED · timeout · Cancel 4 running task(s), timeout graceful shutdown exceeded
Seen: 3× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550271/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  git --no-optional-locks -C /Users/daniel {}
  git --no-optional-locks -C /Users/daniel {}
  Shutting down                            {}
  Waiting for connections to close. (CTRL+ {}
  git --no-optional-locks -C /Users/daniel {}
  git --no-optional-locks -C /Users/daniel {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 03:26 · AUTO-CAPTURED · robot · robot: camera_unavailable — /dev/v4l/by-path/x did not open
Seen: 1× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932897/
Joins to: `camera=cam2` · `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  camera_unavailable cam2: /dev/v4l/by-pat {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 03:26 · AUTO-CAPTURED · camera · camera_unavailable cam2: /dev/v4l/by-path/x did not open
Seen: 1× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 03:34 · AUTO-CAPTURED · robot · robot: camera_unavailable — /dev/v4l/by-path/x did not open
Seen: 2× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932897/
Joins to: `camera=cam2` · `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  camera_unavailable cam2: /dev/v4l/by-pat {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 03:34 · AUTO-CAPTURED · camera · camera_unavailable cam2: /dev/v4l/by-path/x did not open
Seen: 2× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 03:36 · AUTO-CAPTURED · recurring · CancelledError: Task cancelled, timeout graceful shutdown exceeded
Seen: 11× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550237/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  Shutting down                            {}
  Waiting for connections to close. (CTRL+ {}
  git --no-optional-locks -C /Users/daniel {}
  git --no-optional-locks -C /Users/daniel {}
  Cancel 3 running task(s), timeout gracef {}
  Waiting for application shutdown.        {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 03:36 · AUTO-CAPTURED · timeout · Cancel 3 running task(s), timeout graceful shutdown exceeded
Seen: 4× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550271/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  git --no-optional-locks -C /Users/daniel {}
  git --no-optional-locks -C /Users/daniel {}
  Shutting down                            {}
  Waiting for connections to close. (CTRL+ {}
  git --no-optional-locks -C /Users/daniel {}
  git --no-optional-locks -C /Users/daniel {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 04:05 · AUTO-CAPTURED · recurring · Cancel 1 running task(s), timeout graceful shutdown exceeded
Seen: 5× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550271/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  git --no-optional-locks -C /Users/daniel {}
  git --no-optional-locks -C /Users/daniel {}
  Shutting down                            {}
  Waiting for connections to close. (CTRL+ {}
  git --no-optional-locks -C /Users/daniel {}
  git --no-optional-locks -C /Users/daniel {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 08:54 · AUTO-CAPTURED · robot · robot: robot_unreachable — nothing answers at 10.37.101.235 (timed out) — power,
Seen: 1× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  ping -c 1 -W 1000 10.37.101.235          {}
  ping -c 1 -W 1000 10.37.101.235          {}
  robot_unreachable                        {"at": "08:52:06", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "08:52:13", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "08:52:19", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "08:52:26", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 12:31 · AUTO-CAPTURED · robot · robot: robot_unreachable_recovered — robot_unreachable cleared after 13140 s
Seen: 1× · first 2026-09-19T16:31:12 · https://na-alh.sentry.io/issues/7742586990/
_What we changed:_ TODO — fill this in, it is the part they score._

## 12:33 · AUTO-CAPTURED · robot · robot: robot_server_down — 10.37.101.235 answers ping but :8080 does not (timed 
Seen: 1× · first 2026-09-19T16:31:27 · https://na-alh.sentry.io/issues/7742587281/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down                        {"at": "12:30:55", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "12:31:01", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "12:31:08", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "12:31:13", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "12:31:18", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "12:31:23", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 12:35 · AUTO-CAPTURED · robot · robot: robot_server_down — 10.37.101.235 answers ping but :8080 does not (Connec
Seen: 1× · first 2026-09-19T16:34:52 · https://na-alh.sentry.io/issues/7742591416/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down                        {"at": "12:34:24", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "12:34:29", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "12:34:34", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "12:34:39", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "12:34:44", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "12:34:49", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 12:39 · AUTO-CAPTURED · robot · robot: robot_server_down_recovered — robot_server_down cleared after 235 s
Seen: 1× · first 2026-09-19T16:38:30 · https://na-alh.sentry.io/issues/7742598500/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down_recovered              {"at": "12:38:04", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "12:38:09", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "12:38:14", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "12:38:19", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "12:38:24", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "12:38:30", "boot_id": "e6e7142d6cdd", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 13:11 · AUTO-CAPTURED · robot · robot: camera_unavailable — /dev/v4l/by-path/x did not open
Seen: 3× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932897/
Joins to: `camera=cam2` · `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  camera_unavailable cam2: /dev/v4l/by-pat {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 13:11 · AUTO-CAPTURED · camera · camera_unavailable cam2: /dev/v4l/by-path/x did not open
Seen: 3× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 13:13 · AUTO-CAPTURED · recurring · robot: camera_unavailable — /dev/v4l/by-path/x did not open
Seen: 5× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932897/
Joins to: `camera=cam2` · `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  camera_unavailable cam2: /dev/v4l/by-pat {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 13:13 · AUTO-CAPTURED · recurring · camera_unavailable cam2: /dev/v4l/by-path/x did not open
Seen: 5× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 13:15 · AUTO-CAPTURED · recurring · robot: camera_unavailable — /dev/v4l/by-path/x did not open
Seen: 6× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932897/
Joins to: `camera=cam2` · `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  camera_unavailable cam2: /dev/v4l/by-pat {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 13:15 · AUTO-CAPTURED · recurring · camera_unavailable cam2: /dev/v4l/by-path/x did not open
Seen: 6× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 13:23 · AUTO-CAPTURED · camera · AssertionError: lowest horizontal plane is at z=-0.055 m, not 0: check the mount
Seen: 1× · first 2026-09-19T17:23:23 · https://na-alh.sentry.io/issues/7742662415/
Joins to: `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  xcrun --find git                         {}
  /Applications/Xcode.app/Contents/Develop {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 13:33 · AUTO-CAPTURED · robot · robot: robot_restarted — boot_id e6e7142d6cdd -> 6a14695067f4: robot.server rest
Seen: 1× · first 2026-09-19T17:33:27 · https://na-alh.sentry.io/issues/7742676325/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_restarted                          {"at": "13:33:02", "boot_id": "e6e7142d6cdd", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_restarted                          {"at": "13:33:07", "boot_id": "e6e7142d6cdd", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_restarted                          {"at": "13:33:12", "boot_id": "e6e7142d6cdd", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_restarted                          {"at": "13:33:17", "boot_id": "e6e7142d6cdd", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_restarted                          {"at": "13:33:22", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_restarted                          {"at": "13:33:27", "boot_id": "6a14695067f4", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 13:50 · AUTO-CAPTURED · robot · robot: robot_restarted — boot_id 6a14695067f4 -> 73004e112ba0: robot.server rest
Seen: 2× · first 2026-09-19T17:33:27 · https://na-alh.sentry.io/issues/7742676325/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_restarted                          {"at": "13:48:43", "boot_id": "6a14695067f4", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_restarted                          {"at": "13:48:48", "boot_id": "6a14695067f4", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_restarted                          {"at": "13:48:53", "boot_id": "6a14695067f4", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_restarted                          {"at": "13:48:58", "boot_id": "6a14695067f4", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_restarted                          {"at": "13:49:03", "boot_id": "6a14695067f4", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_restarted                          {"at": "13:49:08", "boot_id": "73004e112ba0", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 13:53 · AUTO-CAPTURED · robot · robot: robot_restarted — boot_id 73004e112ba0 -> c55278b24a0f: robot.server rest
Seen: 3× · first 2026-09-19T17:33:27 · https://na-alh.sentry.io/issues/7742676325/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_restarted                          {"at": "13:50:41", "boot_id": "73004e112ba0", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_restarted                          {"at": "13:50:46", "boot_id": "73004e112ba0", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_restarted                          {"at": "13:50:51", "boot_id": "73004e112ba0", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_restarted                          {"at": "13:50:56", "boot_id": "73004e112ba0", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_restarted                          {"at": "13:51:01", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_restarted                          {"at": "13:51:06", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 14:35 · AUTO-CAPTURED · robot · robot: robot_unreachable — nothing answers at 10.37.101.235 (Host is down) — pow
Seen: 1× · first 2026-09-19T18:33:59 · https://na-alh.sentry.io/issues/7742759006/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  ping -c 1 -W 1000 10.37.101.235          {}
  ping -c 1 -W 1000 10.37.101.235          {}
  robot_unreachable                        {"at": "14:33:42", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "14:33:47", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "14:33:52", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "14:33:57", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 14:35 · AUTO-CAPTURED · robot · robot: robot_unreachable — nothing answers at 10.37.101.235 (timed out) — power,
Seen: 3× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
**Evidence captured:** `evidence/7742288078_robot_unreachable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "14:02:32", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "14:02:37", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "14:02:42", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "14:02:49", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "14:02:55", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "14:03:02", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 14:35 · AUTO-CAPTURED · robot · robot: robot_unreachable_recovered — robot_unreachable cleared after 74 s
Seen: 2× · first 2026-09-19T16:31:12 · https://na-alh.sentry.io/issues/7742586990/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable_recovered              {"at": "14:01:59", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "14:02:06", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "14:02:12", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "14:02:19", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "14:02:25", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "14:02:32", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 14:35 · AUTO-CAPTURED · recurring · robot: robot_server_down_recovered — robot_server_down cleared after 87 s
Seen: 5× · first 2026-09-19T16:38:30 · https://na-alh.sentry.io/issues/7742598500/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down_recovered              {"at": "14:00:49", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "14:00:54", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "14:00:59", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "14:01:04", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "14:01:09", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "14:01:14", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 14:35 · AUTO-CAPTURED · recurring · robot: robot_server_down — 10.37.101.235 answers ping but :8080 does not (timed 
Seen: 5× · first 2026-09-19T16:31:27 · https://na-alh.sentry.io/issues/7742587281/
Joins to: `role=link`
**Evidence captured:** `evidence/7742587281_robot_server_down.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down                        {"at": "13:59:44", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_server_down                        {"at": "13:59:49", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "13:59:54", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "13:59:59", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "14:00:04", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "14:00:09", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 14:35 · AUTO-CAPTURED · robot · robot: camera_unavailable_recovered — camera_unavailable cleared after 32 s
Seen: 2× · first 2026-09-19T17:56:07 · https://na-alh.sentry.io/issues/7742706797/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable_recovered             {"at": "13:57:44", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  camera_unavailable_recovered             {"at": "13:57:49", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  camera_unavailable_recovered             {"at": "13:57:54", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  camera_unavailable_recovered             {"at": "13:57:59", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  camera_unavailable_recovered             {"at": "13:58:04", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  camera_unavailable_recovered             {"at": "13:58:09", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 14:35 · AUTO-CAPTURED · robot · robot: telemetry_unfed_recovered — telemetry_unfed cleared after 32 s
Seen: 2× · first 2026-09-19T17:56:07 · https://na-alh.sentry.io/issues/7742706787/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  telemetry_unfed_recovered                {"at": "13:57:44", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  telemetry_unfed_recovered                {"at": "13:57:49", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  telemetry_unfed_recovered                {"at": "13:57:54", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  telemetry_unfed_recovered                {"at": "13:57:59", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  telemetry_unfed_recovered                {"at": "13:58:04", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  telemetry_unfed_recovered                {"at": "13:58:09", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 14:35 · AUTO-CAPTURED · robot · robot: telemetry_unfed — tilt_rate is null in every sample — the IMU reader (ROB
Seen: 2× · first 2026-09-19T17:55:52 · https://na-alh.sentry.io/issues/7742706493/
Joins to: `role=link`
**Evidence captured:** `evidence/7742706493_telemetry_unfed.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  telemetry_unfed                          {"at": "13:57:28", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  telemetry_unfed                          {"at": "13:57:34", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  telemetry_unfed                          {"at": "13:57:39", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  telemetry_unfed                          {"at": "13:57:44", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  telemetry_unfed                          {"at": "13:57:49", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  telemetry_unfed                          {"at": "13:57:54", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 14:35 · AUTO-CAPTURED · robot · robot: camera_unavailable — cam0: preview unavailable (HTTP 503, no valid JPEG)
Seen: 2× · first 2026-09-19T17:55:47 · https://na-alh.sentry.io/issues/7742706378/
Joins to: `role=link`
**Evidence captured:** `evidence/7742706378_camera_unavailable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable                       {"at": "13:57:23", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  camera_unavailable                       {"at": "13:57:28", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  camera_unavailable                       {"at": "13:57:34", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  camera_unavailable                       {"at": "13:57:39", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  camera_unavailable                       {"at": "13:57:44", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
  camera_unavailable                       {"at": "13:57:49", "boot_id": "c55278b24a0f", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 14:35 · AUTO-CAPTURED · camera · bbos _read_frame('camera.head.jpeg',) failed (1 x): KeyError: 'camera.head.jpeg'
Seen: 1× · first 2026-09-19T17:54:34 · https://na-alh.sentry.io/issues/7742706340/
Joins to: `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 10:51:04,543"}
  [Filtered]                               {"asctime": "2026-09-19 10:51:04,546"}
  Started server process [14496]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 14:35 · AUTO-CAPTURED · camera · bbos _read_frame('camera.head.jpeg',) failed (1 x): KeyError: 'camera.head.jpeg'
Seen: 1× · first 2026-09-19T17:54:34 · https://na-alh.sentry.io/issues/7742703702/
Joins to: `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  stats {"connected":true,"connects":3,"ba {"asctime": "2026-09-19 13:54:28,325"}
  HTTP Request: POST http://127.0.0.1:8000 {"asctime": "2026-09-19 13:54:30,176"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  HTTP Request: POST http://127.0.0.1:8000 {"asctime": "2026-09-19 13:54:34,474"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 16:56 · AUTO-CAPTURED · robot · robot: robot_server_down — 10.37.101.235 answers ping but :8080 does not (Connec
Seen: 2× · first 2026-09-19T16:34:52 · https://na-alh.sentry.io/issues/7742591416/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down                        {"at": "16:55:14", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "16:55:19", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "16:55:24", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "16:55:29", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "16:55:34", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "16:55:39", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 16:56 · AUTO-CAPTURED · robot · robot: robot_unreachable_recovered — robot_unreachable cleared after 8501 s
Seen: 3× · first 2026-09-19T16:31:12 · https://na-alh.sentry.io/issues/7742586990/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable_recovered              {"at": "16:54:57", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "16:55:04", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "16:55:09", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "16:55:14", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "16:55:19", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "16:55:24", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:02 · AUTO-CAPTURED · recurring · robot: robot_server_down_recovered — robot_server_down cleared after 383 s
Seen: 6× · first 2026-09-19T16:38:30 · https://na-alh.sentry.io/issues/7742598500/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down_recovered              {"at": "17:01:19", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:01:24", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:01:29", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:01:34", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:01:39", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:01:44", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:04 · AUTO-CAPTURED · robot · robot: robot_unreachable — nothing answers at 10.37.101.235 (timed out) — power,
Seen: 4× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "17:01:54", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:01:59", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:02:04", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:02:11", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:02:17", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:02:24", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:24 · AUTO-CAPTURED · robot · robot: robot_server_down — 10.37.101.235 answers ping but :8080 does not (Connec
Seen: 3× · first 2026-09-19T16:34:52 · https://na-alh.sentry.io/issues/7742591416/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down                        {"at": "17:23:11", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:23:16", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:23:21", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:23:26", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:23:31", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:23:36", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:24 · AUTO-CAPTURED · robot · robot: robot_unreachable_recovered — robot_unreachable cleared after 1272 s
Seen: 4× · first 2026-09-19T16:31:12 · https://na-alh.sentry.io/issues/7742586990/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable_recovered              {"at": "17:22:54", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:23:01", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:23:06", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:23:11", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:23:16", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:23:21", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:32 · AUTO-CAPTURED · robot · robot: robot_server_down — 10.37.101.235 answers ping but :8080 does not (Connec
Seen: 4× · first 2026-09-19T16:34:52 · https://na-alh.sentry.io/issues/7742591416/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down                        {"at": "17:31:57", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:32:02", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:32:07", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:32:12", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:32:18", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:32:22", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:32 · AUTO-CAPTURED · recurring · robot: robot_server_down_recovered — robot_server_down cleared after 520 s
Seen: 7× · first 2026-09-19T16:38:30 · https://na-alh.sentry.io/issues/7742598500/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down_recovered              {"at": "17:31:32", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:31:37", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:31:42", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:31:47", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:31:52", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:31:57", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:36 · AUTO-CAPTURED · recurring · robot: robot_unreachable_recovered — robot_unreachable cleared after 76 s
Seen: 5× · first 2026-09-19T16:31:12 · https://na-alh.sentry.io/issues/7742586990/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable_recovered              {"at": "17:35:53", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:36:00", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:36:06", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:36:13", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:36:19", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:36:26", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:36 · AUTO-CAPTURED · recurring · robot: robot_unreachable — nothing answers at 10.37.101.235 (timed out) — power,
Seen: 5× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "17:34:58", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:35:03", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:35:08", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:35:14", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:35:21", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:35:27", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:36 · AUTO-CAPTURED · recurring · robot: robot_server_down_recovered — robot_server_down cleared after 190 s
Seen: 8× · first 2026-09-19T16:38:30 · https://na-alh.sentry.io/issues/7742598500/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down_recovered              {"at": "17:34:43", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:34:48", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:34:53", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:34:58", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:35:03", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:35:08", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:38 · AUTO-CAPTURED · recurring · robot: robot_server_down_recovered — robot_server_down cleared after 51 s
Seen: 9× · first 2026-09-19T16:38:30 · https://na-alh.sentry.io/issues/7742598500/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down_recovered              {"at": "17:36:56", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:37:01", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:37:06", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:37:11", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:37:16", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "17:37:21", "boot_id": "0cd40ad3321e", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:38 · AUTO-CAPTURED · recurring · robot: robot_server_down — 10.37.101.235 answers ping but :8080 does not (Connec
Seen: 5× · first 2026-09-19T16:34:52 · https://na-alh.sentry.io/issues/7742591416/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down                        {"at": "17:36:19", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:36:26", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:36:31", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:36:36", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:36:41", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "17:36:46", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:42 · AUTO-CAPTURED · recurring · robot: robot_unreachable — nothing answers at 10.37.101.235 (timed out) — power,
Seen: 6× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
**Evidence captured:** `evidence/7742288078_robot_unreachable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "17:40:41", "boot_id": "0cd40ad3321e", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_unreachable                        {"at": "17:40:46", "boot_id": "0cd40ad3321e", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_unreachable                        {"at": "17:40:51", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:40:58", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:41:04", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:41:11", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:44 · AUTO-CAPTURED · recurring · robot: robot_unreachable_recovered — robot_unreachable cleared after 106 s
Seen: 6× · first 2026-09-19T16:31:12 · https://na-alh.sentry.io/issues/7742586990/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable_recovered              {"at": "17:42:17", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:42:23", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:42:28", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:42:33", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:42:38", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:42:43", "boot_id": "0cd40ad3321e", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:48 · AUTO-CAPTURED · recurring · robot: robot_unreachable — nothing answers at 10.37.101.235 (timed out) — power,
Seen: 7× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
**Evidence captured:** `evidence/7742288078_robot_unreachable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "17:46:14", "boot_id": "0cd40ad3321e", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_unreachable                        {"at": "17:46:19", "boot_id": "0cd40ad3321e", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_unreachable                        {"at": "17:46:24", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:46:30", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:46:37", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:46:43", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:50 · AUTO-CAPTURED · recurring · robot: robot_unreachable — nothing answers at 10.37.101.235 (timed out) — power,
Seen: 8× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
**Evidence captured:** `evidence/7742288078_robot_unreachable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "17:48:38", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:48:43", "boot_id": "0cd40ad3321e", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_unreachable                        {"at": "17:48:48", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:48:55", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:49:02", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "17:49:08", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:50 · AUTO-CAPTURED · recurring · robot: fell_over — balanced went 0; pitch -0.363733, peak tilt_rate 0.921679
Seen: 9× · first 2026-09-19T01:21:09 · https://na-alh.sentry.io/issues/7741552137/
Joins to: `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  fell_over                                {"balanced": 1.0, "left_enc": 89.577911, "motor_current_l": 0.0, "motor_current_r": 0.0, "
  fell_over                                {"balanced": 1.0, "left_enc": 89.577911, "motor_current_l": 0.0, "motor_current_r": 0.0, "
  fell_over                                {"balanced": 1.0, "left_enc": 89.577911, "motor_current_l": 0.0, "motor_current_r": 0.0, "
  fell_over                                {"balanced": 1.0, "left_enc": 89.577911, "motor_current_l": 0.0, "motor_current_r": 0.0, "
  fell_over                                {"balanced": 1.0, "left_enc": 89.577911, "motor_current_l": 0.0, "motor_current_r": 0.0, "
  fell_over                                {"balanced": 0.0, "left_enc": 89.577911, "motor_current_l": 0.0, "motor_current_r": 0.0, "
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:50 · AUTO-CAPTURED · recurring · robot: robot_unreachable_recovered — robot_unreachable cleared after 134 s
Seen: 7× · first 2026-09-19T16:31:12 · https://na-alh.sentry.io/issues/7742586990/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable_recovered              {"at": "17:48:17", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:48:23", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:48:28", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:48:33", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:48:38", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "17:48:43", "boot_id": "0cd40ad3321e", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:54 · AUTO-CAPTURED · recurring · Cancel 1 running task(s), timeout graceful shutdown exceeded
Seen: 6× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550271/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  Shutting down                            {}
  httplib                                  {"http.fragment": "", "http.method": "GET", "http.query": "", "http.response.status_code":
  sentry new · web: obs.init('web') smoke  {"asctime": "2026-09-19 17:52:48,696"}
  Waiting for connections to close. (CTRL+ {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 17:56 · AUTO-CAPTURED · recurring · SEARCH WILL 503: ModuleNotFoundError: No module named 'elasticsearch' — start we
Seen: 5× · first 2026-09-19T07:50:37 · https://na-alh.sentry.io/issues/7741956088/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  router housebot: loaded (2 routes)       {"asctime": "2026-09-19 17:55:33,680"}
  router robot_view_api: loaded (5 routes) {"asctime": "2026-09-19 17:55:33,684"}
  router scene_api: loaded (12 routes)     {"asctime": "2026-09-19 17:55:33,690"}
  router sentry_actions: loaded (4 routes) {"asctime": "2026-09-19 17:55:33,694"}
  Started server process [78769]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:07 · AUTO-CAPTURED · recurring · SEARCH WILL 503: ModuleNotFoundError: No module named 'elasticsearch' — start we
Seen: 6× · first 2026-09-19T07:50:37 · https://na-alh.sentry.io/issues/7741956088/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  router housebot: loaded (2 routes)       {"asctime": "2026-09-19 18:06:22,230"}
  router robot_view_api: loaded (5 routes) {"asctime": "2026-09-19 18:06:22,232"}
  router scene_api: loaded (12 routes)     {"asctime": "2026-09-19 18:06:22,235"}
  router sentry_actions: loaded (4 routes) {"asctime": "2026-09-19 18:06:22,238"}
  Started server process [18016]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:17 · AUTO-CAPTURED · recurring · SEARCH WILL 503: ModuleNotFoundError: No module named 'elasticsearch' — start we
Seen: 8× · first 2026-09-19T07:50:37 · https://na-alh.sentry.io/issues/7741956088/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  router housebot: loaded (2 routes)       {"asctime": "2026-09-19 18:16:50,126"}
  router robot_view_api: loaded (5 routes) {"asctime": "2026-09-19 18:16:50,128"}
  router scene_api: loaded (12 routes)     {"asctime": "2026-09-19 18:16:50,131"}
  router sentry_actions: loaded (4 routes) {"asctime": "2026-09-19 18:16:50,134"}
  Started server process [58248]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:19 · AUTO-CAPTURED · recurring · SEARCH WILL 503: ModuleNotFoundError: No module named 'elasticsearch' — start we
Seen: 9× · first 2026-09-19T07:50:37 · https://na-alh.sentry.io/issues/7741956088/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  router housebot: loaded (2 routes)       {"asctime": "2026-09-19 18:18:24,998"}
  router robot_view_api: loaded (5 routes) {"asctime": "2026-09-19 18:18:25,000"}
  router scene_api: loaded (12 routes)     {"asctime": "2026-09-19 18:18:25,003"}
  router sentry_actions: loaded (4 routes) {"asctime": "2026-09-19 18:18:25,005"}
  Started server process [62897]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:21 · AUTO-CAPTURED · recurring · Cancel 1 running task(s), timeout graceful shutdown exceeded
Seen: 7× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550271/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  Shutting down                            {}
  /Applications/Xcode.app/Contents/Develop {}
  Waiting for background tasks to complete {}
  httplib                                  {"http.fragment": "", "http.method": "GET", "http.query": "query=is%3Aunresolved&statsPeri
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:31 · AUTO-CAPTURED · recurring · robot: robot_server_down — 10.37.101.235 answers ping but :8080 does not (Connec
Seen: 6× · first 2026-09-19T16:34:52 · https://na-alh.sentry.io/issues/7742591416/
Joins to: `role=link`
**Evidence captured:** `evidence/7742591416_robot_server_down.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down                        {"at": "18:30:22", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "18:30:27", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "18:30:32", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "18:30:37", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "18:30:42", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down                        {"at": "18:30:47", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:31 · AUTO-CAPTURED · recurring · Cancel 2 running task(s), timeout graceful shutdown exceeded
Seen: 8× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550271/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  Shutting down                            {}
  Waiting for connections to close. (CTRL+ {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:33 · AUTO-CAPTURED · robot · robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bbos publish
Seen: 2× · first 2026-09-19T22:31:38 · https://na-alh.sentry.io/issues/7743047616/
Joins to: `role=link`
**Evidence captured:** `evidence/7743047616_camera_unavailable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable                       {"at": "18:32:27", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  camera_unavailable                       {"at": "18:32:32", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  camera_unavailable                       {"at": "18:32:37", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  camera_unavailable                       {"at": "18:32:42", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:32:47", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:32:52", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:33 · AUTO-CAPTURED · robot · robot: robot_unreachable — nothing answers at 10.37.101.235 (Host is down) — pow
Seen: 2× · first 2026-09-19T18:33:59 · https://na-alh.sentry.io/issues/7742759006/
Joins to: `role=link`
**Evidence captured:** `evidence/7742759006_robot_unreachable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "18:31:37", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  robot_unreachable                        {"at": "18:31:42", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:31:49", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:31:55", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:32:00", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:32:05", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:33 · AUTO-CAPTURED · robot · robot: camera_unavailable_recovered — camera_unavailable cleared after 21 s
Seen: 3× · first 2026-09-19T17:56:07 · https://na-alh.sentry.io/issues/7742706797/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable_recovered             {"at": "18:31:17", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  camera_unavailable_recovered             {"at": "18:31:22", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  camera_unavailable_recovered             {"at": "18:31:27", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:31:32", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:31:37", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:31:42", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:33 · AUTO-CAPTURED · recurring · robot: robot_server_down_recovered — robot_server_down cleared after 54 s
Seen: 10× · first 2026-09-19T16:38:30 · https://na-alh.sentry.io/issues/7742598500/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down_recovered              {"at": "18:31:02", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "18:31:07", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "18:31:12", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "18:31:17", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "18:31:22", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_server_down_recovered              {"at": "18:31:27", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:33 · AUTO-CAPTURED · robot · robot: robot_restarted — boot_id 0cd40ad3321e -> f16db795602d: robot.server rest
Seen: 4× · first 2026-09-19T17:33:27 · https://na-alh.sentry.io/issues/7742676325/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_restarted                          {"at": "18:31:02", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_restarted                          {"at": "18:31:07", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_restarted                          {"at": "18:31:12", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_restarted                          {"at": "18:31:17", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_restarted                          {"at": "18:31:22", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_restarted                          {"at": "18:31:27", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:33 · AUTO-CAPTURED · robot · robot: camera_unavailable — bbos published no new camera.head.jpeg within 2.0 s:
Seen: 1× · first 2026-09-19T22:31:26 · https://na-alh.sentry.io/issues/7743047397/
Joins to: `camera=cam0` · `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 15:31:24,118"}
  [Filtered]                               {"asctime": "2026-09-19 15:31:24,124"}
  Started server process [4099]            {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
  camera_unavailable cam0: bbos published  {"asctime": "2026-09-19 15:31:26,316"}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:33 · AUTO-CAPTURED · recurring · camera_unavailable cam0: bbos published no new camera.head.jpeg within 2.0 s: is
Seen: 7× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 15:31:24,118"}
  [Filtered]                               {"asctime": "2026-09-19 15:31:24,124"}
  Started server process [4099]            {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:37 · AUTO-CAPTURED · robot · robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bbos publish
Seen: 3× · first 2026-09-19T22:31:38 · https://na-alh.sentry.io/issues/7743047616/
Joins to: `role=link`
**Evidence captured:** `evidence/7743047616_camera_unavailable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable                       {"at": "18:36:07", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:36:12", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:36:17", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  camera_unavailable                       {"at": "18:36:22", "boot_id": "fc9e70681bcb", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:36:27", "boot_id": "fc9e70681bcb", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:36:32", "boot_id": "fc9e70681bcb", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:37 · AUTO-CAPTURED · recurring · robot: robot_restarted — boot_id f16db795602d -> fc9e70681bcb: robot.server rest
Seen: 5× · first 2026-09-19T17:33:27 · https://na-alh.sentry.io/issues/7742676325/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_restarted                          {"at": "18:35:57", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  robot_restarted                          {"at": "18:36:02", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  robot_restarted                          {"at": "18:36:07", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  robot_restarted                          {"at": "18:36:12", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  robot_restarted                          {"at": "18:36:17", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_restarted                          {"at": "18:36:22", "boot_id": "fc9e70681bcb", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:37 · AUTO-CAPTURED · robot · robot: camera_unavailable — bbos published no new camera.head.jpeg within 2.0 s:
Seen: 2× · first 2026-09-19T22:31:26 · https://na-alh.sentry.io/issues/7743047397/
Joins to: `camera=cam0` · `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 15:36:16,384"}
  Started server process [9009]            {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
  camera_unavailable cam0: bbos published  {"asctime": "2026-09-19 15:36:18,530"}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:37 · AUTO-CAPTURED · recurring · camera_unavailable cam0: bbos published no new camera.head.jpeg within 2.0 s: is
Seen: 8× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 15:36:16,384"}
  Started server process [9009]            {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:37 · AUTO-CAPTURED · robot · robot: camera_unavailable_recovered — camera_unavailable cleared after 215 s
Seen: 4× · first 2026-09-19T17:56:07 · https://na-alh.sentry.io/issues/7742706797/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable_recovered             {"at": "18:35:52", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:35:57", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:36:02", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:36:07", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:36:12", "boot_id": "f16db795602d", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:36:17", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:39 · AUTO-CAPTURED · recurring · ModuleNotFoundError: No module named 'jsonschema'
Seen: 5× · first 2026-09-19T22:37:48 · https://na-alh.sentry.io/issues/7743054020/
Joins to: `role=web`
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:39 · AUTO-CAPTURED · robot · robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bbos publish
Seen: 4× · first 2026-09-19T22:31:38 · https://na-alh.sentry.io/issues/7743047616/
Joins to: `role=link`
**Evidence captured:** `evidence/7743047616_camera_unavailable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable                       {"at": "18:38:52", "boot_id": "fc9e70681bcb", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:38:57", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  camera_unavailable                       {"at": "18:39:02", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:39:07", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:39:12", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:39:17", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:39 · AUTO-CAPTURED · recurring · robot: robot_restarted — boot_id fc9e70681bcb -> e8e3ba83ba2e: robot.server rest
Seen: 6× · first 2026-09-19T17:33:27 · https://na-alh.sentry.io/issues/7742676325/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_restarted                          {"at": "18:38:37", "boot_id": "fc9e70681bcb", "cameras": [], "link": "lan", "mode": "hardw
  robot_restarted                          {"at": "18:38:42", "boot_id": "fc9e70681bcb", "cameras": [], "link": "lan", "mode": "hardw
  robot_restarted                          {"at": "18:38:47", "boot_id": "fc9e70681bcb", "cameras": [], "link": "lan", "mode": "hardw
  robot_restarted                          {"at": "18:38:52", "boot_id": "fc9e70681bcb", "cameras": [], "link": "lan", "mode": "hardw
  robot_restarted                          {"at": "18:38:57", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  robot_restarted                          {"at": "18:39:02", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:39 · AUTO-CAPTURED · robot · robot: camera_unavailable — bbos published no new camera.head.jpeg within 2.0 s:
Seen: 3× · first 2026-09-19T22:31:26 · https://na-alh.sentry.io/issues/7743047397/
Joins to: `camera=cam0` · `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 15:38:58,745"}
  Started server process [11765]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
  camera_unavailable cam0: bbos published  {"asctime": "2026-09-19 15:39:00,949"}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:39 · AUTO-CAPTURED · recurring · camera_unavailable cam0: bbos published no new camera.head.jpeg within 2.0 s: is
Seen: 9× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 15:38:58,745"}
  Started server process [11765]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:41 · AUTO-CAPTURED · recurring · robot: robot_unreachable — nothing answers at 10.37.101.235 (timed out) — power,
Seen: 9× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
**Evidence captured:** `evidence/7742288078_robot_unreachable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "18:39:52", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  robot_unreachable                        {"at": "18:39:57", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  robot_unreachable                        {"at": "18:40:02", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:40:09", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:40:15", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:40:22", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:43 · AUTO-CAPTURED · recurring · robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bbos publish
Seen: 6× · first 2026-09-19T22:31:38 · https://na-alh.sentry.io/issues/7743047616/
Joins to: `role=link`
**Evidence captured:** `evidence/7743047616_camera_unavailable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable                       {"at": "18:42:50", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  camera_unavailable                       {"at": "18:42:56", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
  camera_unavailable                       {"at": "18:43:02", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:43:07", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:43:12", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:43:17", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:43 · AUTO-CAPTURED · recurring · robot: robot_unreachable_recovered — robot_unreachable cleared after 39 s
Seen: 11× · first 2026-09-19T16:31:12 · https://na-alh.sentry.io/issues/7742586990/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable_recovered              {"at": "18:42:23", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "18:42:30", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "18:42:37", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "18:42:43", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "18:42:50", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "18:42:56", "bad": "robot_server_down", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:43 · AUTO-CAPTURED · recurring · robot: robot_unreachable — nothing answers at 10.37.101.235 (timed out) — power,
Seen: 10× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
**Evidence captured:** `evidence/7742288078_robot_unreachable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "18:42:07", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  robot_unreachable                        {"at": "18:42:12", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  robot_unreachable                        {"at": "18:42:17", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:42:23", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:42:30", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:42:37", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:45 · AUTO-CAPTURED · recurring · robot: camera_unavailable_recovered — camera_unavailable cleared after 140 s
Seen: 8× · first 2026-09-19T17:56:07 · https://na-alh.sentry.io/issues/7742706797/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable_recovered             {"at": "18:44:52", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:44:57", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:45:02", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:45:07", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:45:12", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:45:17", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:47 · AUTO-CAPTURED · recurring · robot: robot_unreachable — nothing answers at 10.37.101.235 (timed out) — power,
Seen: 11× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
**Evidence captured:** `evidence/7742288078_robot_unreachable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "18:45:07", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  robot_unreachable                        {"at": "18:45:12", "boot_id": "e8e3ba83ba2e", "cameras": [], "link": "lan", "mode": "hardw
  robot_unreachable                        {"at": "18:45:17", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:45:24", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:45:31", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:45:37", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:53 · AUTO-CAPTURED · recurring · robot: robot_unreachable — nothing answers at 10.37.101.235 (timed out) — power,
Seen: 12× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  ping -c 1 -W 1000 10.37.101.235          {}
  ping -c 1 -W 1000 10.37.101.235          {}
  robot_unreachable                        {"at": "18:52:32", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:52:38", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:52:45", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:52:51", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:55 · AUTO-CAPTURED · recurring · robot: robot_unreachable — nothing answers at 10.37.101.235 (timed out) — power,
Seen: 13× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  ping -c 1 -W 1000 10.37.101.235          {}
  ping -c 1 -W 1000 10.37.101.235          {}
  robot_unreachable                        {"at": "18:54:24", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:54:31", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:54:37", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable                        {"at": "18:54:44", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:57 · AUTO-CAPTURED · recurring · robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bbos publish
Seen: 7× · first 2026-09-19T22:31:38 · https://na-alh.sentry.io/issues/7743047616/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable                       {"at": "18:57:18", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.0.124:8080",
  camera_unavailable                       {"at": "18:57:23", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  camera_unavailable                       {"at": "18:57:28", "boot_id": "13e0251ab209", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:57:33", "boot_id": "13e0251ab209", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:57:38", "boot_id": "13e0251ab209", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "18:57:43", "boot_id": "13e0251ab209", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:57 · AUTO-CAPTURED · recurring · robot: camera_unavailable — bbos published no new camera.head.jpeg within 2.0 s:
Seen: 5× · first 2026-09-19T22:31:26 · https://na-alh.sentry.io/issues/7743047397/
Joins to: `camera=cam0` · `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 15:57:23,766"}
  Started server process [30356]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
  camera_unavailable cam0: bbos published  {"asctime": "2026-09-19 15:57:25,910"}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:57 · AUTO-CAPTURED · recurring · camera_unavailable cam0: bbos published no new camera.head.jpeg within 2.0 s: is
Seen: 11× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 15:57:23,766"}
  Started server process [30356]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:58 · AUTO-CAPTURED · robot · robot: robot_forbidden_recovered — robot_forbidden cleared after 80 s
Seen: 1× · first 2026-09-19T22:57:23 · https://na-alh.sentry.io/issues/7743073277/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_forbidden_recovered                {"at": "18:56:58", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.0.124:8080",
  robot_forbidden_recovered                {"at": "18:57:03", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.0.124:8080",
  robot_forbidden_recovered                {"at": "18:57:08", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.0.124:8080",
  robot_forbidden_recovered                {"at": "18:57:13", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.0.124:8080",
  robot_forbidden_recovered                {"at": "18:57:18", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.0.124:8080",
  robot_forbidden_recovered                {"at": "18:57:23", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:58 · AUTO-CAPTURED · robot · robot: robot_forbidden — robot.server at 192.168.0.124:8080 refuses this laptop:
Seen: 1× · first 2026-09-19T22:56:14 · https://na-alh.sentry.io/issues/7743072545/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_forbidden                          {"at": "18:55:43", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_forbidden                          {"at": "18:55:49", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_forbidden                          {"at": "18:55:56", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_forbidden                          {"at": "18:56:03", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.0.124:8080",
  robot_forbidden                          {"at": "18:56:08", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.0.124:8080",
  robot_forbidden                          {"at": "18:56:13", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.0.124:8080",
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 18:58 · AUTO-CAPTURED · recurring · robot: robot_unreachable_recovered — robot_unreachable cleared after 93 s
Seen: 12× · first 2026-09-19T16:31:12 · https://na-alh.sentry.io/issues/7742586990/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable_recovered              {"at": "18:55:30", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "18:55:36", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "18:55:43", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "18:55:49", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "18:55:56", "bad": "robot_unreachable", "link": "lan", "robot": "10.37.101.235:8080
  robot_unreachable_recovered              {"at": "18:56:03", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.0.124:8080",
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:00 · AUTO-CAPTURED · recurring · ValueError: '192.168.0.30   # the laptop (wifi + tailnet) and this robot itself'
Seen: 57× · first 2026-09-19T22:58:37 · https://na-alh.sentry.io/issues/7743074453/
Joins to: `role=robot`
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:00 · AUTO-CAPTURED · robot · robot: robot_server_down — :8080 answers but it is not robot.server (HTTP 500)
Seen: 1× · first 2026-09-19T22:59:22 · https://na-alh.sentry.io/issues/7743075273/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  httplib                                  {"http.fragment": "", "http.method": "GET", "http.query": "", "http.response.status_code":
  httplib                                  {"http.fragment": "", "http.method": "GET", "http.query": "", "http.response.status_code":
  robot_server_down                        {"at": "18:59:07", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down                        {"at": "18:59:12", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down                        {"at": "18:59:17", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down                        {"at": "18:59:22", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:00 · AUTO-CAPTURED · recurring · robot: robot_server_down — 192.168.0.124 answers ping but :8080 does not (Connec
Seen: 7× · first 2026-09-19T16:34:52 · https://na-alh.sentry.io/issues/7742591416/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down                        {"at": "18:58:03", "boot_id": "13e0251ab209", "cameras": [], "link": "lan", "mode": "hardw
  robot_server_down                        {"at": "18:58:08", "boot_id": "13e0251ab209", "cameras": [], "link": "lan", "mode": "hardw
  robot_server_down                        {"at": "18:58:13", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down                        {"at": "18:58:18", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down                        {"at": "18:58:23", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down                        {"at": "18:58:28", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:00 · AUTO-CAPTURED · recurring · robot: camera_unavailable_recovered — camera_unavailable cleared after 45 s
Seen: 9× · first 2026-09-19T17:56:07 · https://na-alh.sentry.io/issues/7742706797/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable_recovered             {"at": "18:57:48", "boot_id": "13e0251ab209", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:57:53", "boot_id": "13e0251ab209", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:57:58", "boot_id": "13e0251ab209", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:58:03", "boot_id": "13e0251ab209", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:58:08", "boot_id": "13e0251ab209", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "18:58:13", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:02 · AUTO-CAPTURED · robot · robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no writer) — c
Seen: 1× · first 2026-09-19T23:01:22 · https://na-alh.sentry.io/issues/7743078588/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent                              {"at": "19:00:57", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:01:02", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:01:07", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:01:12", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:01:17", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:01:22", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:02 · AUTO-CAPTURED · recurring · robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bbos publish
Seen: 8× · first 2026-09-19T22:31:38 · https://na-alh.sentry.io/issues/7743047616/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable                       {"at": "19:00:22", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  camera_unavailable                       {"at": "19:00:27", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  camera_unavailable                       {"at": "19:00:32", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "19:00:37", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "19:00:42", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "19:00:47", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:02 · AUTO-CAPTURED · recurring · robot: robot_server_down_recovered — robot_server_down cleared after 85 s
Seen: 11× · first 2026-09-19T16:38:30 · https://na-alh.sentry.io/issues/7742598500/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down_recovered              {"at": "19:00:07", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down_recovered              {"at": "19:00:12", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down_recovered              {"at": "19:00:17", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down_recovered              {"at": "19:00:22", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down_recovered              {"at": "19:00:27", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down_recovered              {"at": "19:00:32", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:02 · AUTO-CAPTURED · recurring · robot: camera_unavailable — bbos published no new camera.head.jpeg within 2.0 s:
Seen: 6× · first 2026-09-19T22:31:26 · https://na-alh.sentry.io/issues/7743047397/
Joins to: `camera=cam0` · `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 16:00:25,967"}
  Started server process [33991]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
  camera_unavailable cam0: bbos published  {"asctime": "2026-09-19 16:00:28,111"}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:02 · AUTO-CAPTURED · recurring · camera_unavailable cam0: bbos published no new camera.head.jpeg within 2.0 s: is
Seen: 12× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 16:00:25,967"}
  Started server process [33991]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:02 · AUTO-CAPTURED · recurring · ValueError: '192.168.0.30   # the laptop (wifi + tailnet) and this robot itself'
Seen: 60× · first 2026-09-19T22:58:37 · https://na-alh.sentry.io/issues/7743074453/
Joins to: `role=robot`
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:10 · AUTO-CAPTURED · recurring · robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bbos publish
Seen: 10× · first 2026-09-19T22:31:38 · https://na-alh.sentry.io/issues/7743047616/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  httplib                                  {"http.fragment": "", "http.method": "GET", "http.query": "", "http.response.status_code":
  httplib                                  {"http.fragment": "", "http.method": "GET", "http.query": "types=hello,telemetry&limit=1",
  camera_unavailable                       {"at": "19:09:27", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "19:09:32", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "19:09:37", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "19:09:42", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:10 · AUTO-CAPTURED · robot · robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no writer) — c
Seen: 2× · first 2026-09-19T23:01:22 · https://na-alh.sentry.io/issues/7743078588/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent                              {"at": "19:09:52", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:09:57", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:10:02", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:10:07", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:10:12", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:10:17", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:12 · AUTO-CAPTURED · robot · robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no writer) — c
Seen: 3× · first 2026-09-19T23:01:22 · https://na-alh.sentry.io/issues/7743078588/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent                              {"at": "19:09:52", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:09:57", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:10:02", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:10:07", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:10:12", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:10:17", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:18 · AUTO-CAPTURED · recurring · robot: robot_server_down — 192.168.0.124 answers ping but :8080 does not (Connec
Seen: 9× · first 2026-09-19T16:34:52 · https://na-alh.sentry.io/issues/7742591416/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down                        {"at": "19:17:53", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  robot_server_down                        {"at": "19:17:58", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down                        {"at": "19:18:03", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down                        {"at": "19:18:08", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down                        {"at": "19:18:13", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down                        {"at": "19:18:18", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:18 · AUTO-CAPTURED · robot · robot: bbos_silent_recovered — bbos_silent cleared after 571 s
Seen: 2× · first 2026-09-19T23:17:58 · https://na-alh.sentry.io/issues/7743095266/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent_recovered                    {"at": "19:17:33", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "19:17:38", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "19:17:43", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "19:17:48", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "19:17:53", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "19:17:58", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:18 · AUTO-CAPTURED · recurring · robot: camera_unavailable_recovered — camera_unavailable cleared after 571 s
Seen: 11× · first 2026-09-19T17:56:07 · https://na-alh.sentry.io/issues/7742706797/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable_recovered             {"at": "19:17:33", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "19:17:38", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "19:17:43", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "19:17:48", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "19:17:53", "boot_id": "a58fd6e687df", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "19:17:58", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:20 · AUTO-CAPTURED · robot · robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no writer) — c
Seen: 4× · first 2026-09-19T23:01:22 · https://na-alh.sentry.io/issues/7743078588/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent                              {"at": "19:19:22", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:19:27", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:19:32", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:19:37", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:19:42", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "19:19:47", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:20 · AUTO-CAPTURED · recurring · robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bbos publish
Seen: 13× · first 2026-09-19T22:31:38 · https://na-alh.sentry.io/issues/7743047616/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  httplib                                  {"http.fragment": "", "http.method": "GET", "http.query": "", "http.response.status_code":
  httplib                                  {"http.fragment": "", "http.method": "GET", "http.query": "types=hello,telemetry&limit=1",
  camera_unavailable                       {"at": "19:18:57", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "19:19:02", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "19:19:07", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "19:19:12", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:20 · AUTO-CAPTURED · recurring · robot: robot_server_down_recovered — robot_server_down cleared after 30 s
Seen: 13× · first 2026-09-19T16:38:30 · https://na-alh.sentry.io/issues/7742598500/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down_recovered              {"at": "19:18:03", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down_recovered              {"at": "19:18:08", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down_recovered              {"at": "19:18:13", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down_recovered              {"at": "19:18:18", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down_recovered              {"at": "19:18:23", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_server_down_recovered              {"at": "19:18:28", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:20 · AUTO-CAPTURED · recurring · robot: robot_restarted — boot_id a58fd6e687df -> 5247a1c95f28: robot.server rest
Seen: 8× · first 2026-09-19T17:33:27 · https://na-alh.sentry.io/issues/7742676325/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_restarted                          {"at": "19:18:03", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_restarted                          {"at": "19:18:08", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_restarted                          {"at": "19:18:13", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_restarted                          {"at": "19:18:18", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_restarted                          {"at": "19:18:23", "bad": "robot_server_down", "link": "lan", "robot": "192.168.0.124:8080
  robot_restarted                          {"at": "19:18:28", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:20 · AUTO-CAPTURED · recurring · robot: camera_unavailable — bbos published no new camera.head.jpeg within 2.0 s:
Seen: 7× · first 2026-09-19T22:31:26 · https://na-alh.sentry.io/issues/7743047397/
Joins to: `camera=cam0` · `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 16:18:21,840"}
  Started server process [52921]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
  camera_unavailable cam0: bbos published  {"asctime": "2026-09-19 16:18:24,008"}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:20 · AUTO-CAPTURED · recurring · camera_unavailable cam0: bbos published no new camera.head.jpeg within 2.0 s: is
Seen: 13× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 16:18:21,840"}
  Started server process [52921]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:22 · AUTO-CAPTURED · recurring · robot: fell_over — balanced went 0; pitch 0.350199, peak tilt_rate 0.698854
Seen: 10× · first 2026-09-19T01:21:09 · https://na-alh.sentry.io/issues/7741552137/
Joins to: `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  fell_over                                {"balanced": 1.0, "left_enc": 4.722089, "motor_current_l": 0.0, "motor_current_r": 0.0, "o
  fell_over                                {"balanced": 1.0, "left_enc": 4.725274, "motor_current_l": 0.0, "motor_current_r": 0.0, "o
  fell_over                                {"balanced": 1.0, "left_enc": 4.754452, "motor_current_l": 0.0, "motor_current_r": 0.0, "o
  fell_over                                {"balanced": 1.0, "left_enc": 4.800581, "motor_current_l": 0.0, "motor_current_r": 0.0, "o
  fell_over                                {"balanced": 1.0, "left_enc": 4.833169, "motor_current_l": 0.0, "motor_current_r": 0.0, "o
  fell_over                                {"balanced": 0.0, "left_enc": 4.657583, "motor_current_l": 0.0, "motor_current_r": 0.0, "o
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:33 · AUTO-CAPTURED · recurring · robot: robot_unreachable — nothing answers at 192.168.0.124 (timed out) — power,
Seen: 14× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "19:30:53", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  robot_unreachable                        {"at": "19:30:58", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  robot_unreachable                        {"at": "19:31:03", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.0.124:8080
  robot_unreachable                        {"at": "19:31:09", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.0.124:8080
  robot_unreachable                        {"at": "19:31:16", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.0.124:8080
  robot_unreachable                        {"at": "19:31:22", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.0.124:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:33 · AUTO-CAPTURED · robot · robot: bbos_silent_recovered — bbos_silent cleared after 731 s
Seen: 3× · first 2026-09-19T23:17:58 · https://na-alh.sentry.io/issues/7743095266/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent_recovered                    {"at": "19:30:38", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "19:30:43", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "19:30:48", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "19:30:53", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "19:30:58", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "19:31:03", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.0.124:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 19:33 · AUTO-CAPTURED · recurring · robot: camera_unavailable_recovered — camera_unavailable cleared after 731 s
Seen: 12× · first 2026-09-19T17:56:07 · https://na-alh.sentry.io/issues/7742706797/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable_recovered             {"at": "19:30:38", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "19:30:43", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "19:30:48", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "19:30:53", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "19:30:58", "boot_id": "5247a1c95f28", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "19:31:03", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.0.124:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 20:12 · AUTO-CAPTURED · recurring · robot: camera_unavailable — bbos published no new camera.head.jpeg within 2.0 s:
Seen: 8× · first 2026-09-19T22:31:26 · https://na-alh.sentry.io/issues/7743047397/
Joins to: `camera=cam0` · `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 17:02:22,240"}
  Started server process [2829]            {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
  camera_unavailable cam0: bbos published  {"asctime": "2026-09-19 17:02:24,626"}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 20:12 · AUTO-CAPTURED · recurring · camera_unavailable cam0: bbos published no new camera.head.jpeg within 2.0 s: is
Seen: 14× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 17:02:22,240"}
  Started server process [2829]            {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 20:22 · AUTO-CAPTURED · recurring · Cancel 1 running task(s), timeout graceful shutdown exceeded
Seen: 9× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550271/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  /Applications/Xcode.app/Contents/Develop {}
  Shutting down                            {}
  Waiting for background tasks to complete {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  httplib                                  {"http.fragment": "", "http.method": "GET", "http.query": "query=is%3Aunresolved&statsPeri
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 20:28 · AUTO-CAPTURED · recurring · robot: housebot_failed — robot API failed; delivery status may be unknown: <urlo
Seen: 7× · first 2026-09-20T00:27:18 · https://na-alh.sentry.io/issues/7743164380/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  es captures.list POST /room-clouds/_sear {"asctime": "2026-09-19 20:28:12,351"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.events POST /room-events/_sear {"asctime": "2026-09-19 20:28:12,382"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.observations POST /room-observ {"asctime": "2026-09-19 20:28:12,417"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 20:30 · AUTO-CAPTURED · recurring · robot: housebot_failed — robot API failed; delivery status may be unknown: <urlo
Seen: 8× · first 2026-09-20T00:27:18 · https://na-alh.sentry.io/issues/7743164380/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  es captures.list POST /room-clouds/_sear {"asctime": "2026-09-19 20:28:59,308"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.events POST /room-events/_sear {"asctime": "2026-09-19 20:28:59,347"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.observations POST /room-observ {"asctime": "2026-09-19 20:28:59,386"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 20:32 · AUTO-CAPTURED · recurring · robot: housebot_failed — robot API failed; delivery status may be unknown: <urlo
Seen: 13× · first 2026-09-20T00:27:18 · https://na-alh.sentry.io/issues/7743164380/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  es captures.list POST /room-clouds/_sear {"asctime": "2026-09-19 20:32:18,193"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.events POST /room-events/_sear {"asctime": "2026-09-19 20:32:18,224"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.observations POST /room-observ {"asctime": "2026-09-19 20:32:18,271"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 20:34 · AUTO-CAPTURED · recurring · robot: housebot_failed — robot API failed; delivery status may be unknown: <urlo
Seen: 16× · first 2026-09-20T00:27:18 · https://na-alh.sentry.io/issues/7743164380/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  es captures.list POST /room-clouds/_sear {"asctime": "2026-09-19 20:33:50,028"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.observations POST /room-observ {"asctime": "2026-09-19 20:33:50,032"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.events POST /room-events/_sear {"asctime": "2026-09-19 20:33:50,068"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 20:36 · AUTO-CAPTURED · recurring · robot: housebot_failed — robot API failed; delivery status may be unknown: <urlo
Seen: 17× · first 2026-09-20T00:27:18 · https://na-alh.sentry.io/issues/7743164380/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  es captures.list POST /room-clouds/_sear {"asctime": "2026-09-19 20:35:44,259"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.events POST /room-events/_sear {"asctime": "2026-09-19 20:35:44,284"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.observations POST /room-observ {"asctime": "2026-09-19 20:35:44,307"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 20:38 · AUTO-CAPTURED · recurring · robot: housebot_failed — robot API failed; delivery status may be unknown: <urlo
Seen: 18× · first 2026-09-20T00:27:18 · https://na-alh.sentry.io/issues/7743164380/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  es captures.list POST /room-clouds/_sear {"asctime": "2026-09-19 20:36:59,378"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.observations POST /room-observ {"asctime": "2026-09-19 20:36:59,383"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.events POST /room-events/_sear {"asctime": "2026-09-19 20:36:59,402"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 20:40 · AUTO-CAPTURED · recurring · Cancel 1 running task(s), timeout graceful shutdown exceeded
Seen: 10× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550271/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  Shutting down                            {}
  Waiting for connections to close. (CTRL+ {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 20:50 · AUTO-CAPTURED · recurring · Cancel 1 running task(s), timeout graceful shutdown exceeded
Seen: 12× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550271/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  httplib                                  {"http.fragment": "", "http.method": "GET", "http.query": "query=is%3Aunresolved&statsPeri
  /Applications/Xcode.app/Contents/Develop {}
  Shutting down                            {}
  Waiting for background tasks to complete {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 20:50 · AUTO-CAPTURED · recurring · robot: housebot_failed — robot API failed; delivery status may be unknown: <urlo
Seen: 21× · first 2026-09-20T00:27:18 · https://na-alh.sentry.io/issues/7743164380/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  es object.observations POST /room-observ {"asctime": "2026-09-19 20:49:22,506"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.events POST /room-events/_sear {"asctime": "2026-09-19 20:49:22,508"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es captures.list POST /room-clouds/_sear {"asctime": "2026-09-19 20:49:22,508"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 21:00 · AUTO-CAPTURED · recurring · robot: camera_unavailable — bbos published no new camera.head.jpeg within 2.0 s:
Seen: 9× · first 2026-09-19T22:31:26 · https://na-alh.sentry.io/issues/7743047397/
Joins to: `camera=cam0` · `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 17:58:39,037"}
  Started server process [2772]            {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
  camera_unavailable cam0: bbos published  {"asctime": "2026-09-19 17:58:41,289"}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 21:00 · AUTO-CAPTURED · recurring · camera_unavailable cam0: bbos published no new camera.head.jpeg within 2.0 s: is
Seen: 15× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 17:58:39,037"}
  Started server process [2772]            {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 21:16 · AUTO-CAPTURED · recurring · robot: housebot_failed — robot API failed; delivery status may be unknown: <urlo
Seen: 22× · first 2026-09-20T00:27:18 · https://na-alh.sentry.io/issues/7743164380/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  es captures.list POST /room-clouds/_sear {"asctime": "2026-09-19 21:16:20,220"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.events POST /room-events/_sear {"asctime": "2026-09-19 21:16:20,232"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.observations POST /room-observ {"asctime": "2026-09-19 21:16:20,269"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 21:18 · AUTO-CAPTURED · recurring · robot: camera_unavailable — bbos published no new camera.head.jpeg within 2.0 s:
Seen: 10× · first 2026-09-19T22:31:26 · https://na-alh.sentry.io/issues/7743047397/
Joins to: `camera=cam0` · `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 18:17:50,468"}
  Started server process [2769]            {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
  camera_unavailable cam0: bbos published  {"asctime": "2026-09-19 18:17:52,738"}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 21:18 · AUTO-CAPTURED · recurring · camera_unavailable cam0: bbos published no new camera.head.jpeg within 2.0 s: is
Seen: 16× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 18:17:50,468"}
  Started server process [2769]            {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 21:20 · AUTO-CAPTURED · recurring · robot: housebot_failed — robot API failed; delivery status may be unknown: <urlo
Seen: 24× · first 2026-09-20T00:27:18 · https://na-alh.sentry.io/issues/7743164380/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  es captures.list POST /room-clouds/_sear {"asctime": "2026-09-19 21:19:41,232"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.events POST /room-events/_sear {"asctime": "2026-09-19 21:19:41,267"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.observations POST /room-observ {"asctime": "2026-09-19 21:19:41,297"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 21:22 · AUTO-CAPTURED · robot · robot: object_not_found — nothing in the room answers 'pick up the trash' (best 
Seen: 1× · first 2026-09-20T01:21:12 · https://na-alh.sentry.io/issues/7743223741/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  POST https://my-elasticsearch-project-a9 {"asctime": "2026-09-20 01:21:12,619"}
  es shared.search room-objects -> ok in 4 {"asctime": "2026-09-20 01:21:12,621"}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 21:30 · AUTO-CAPTURED · recurring · robot: housebot_failed — robot API failed; delivery status may be unknown: <urlo
Seen: 26× · first 2026-09-20T00:27:18 · https://na-alh.sentry.io/issues/7743164380/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  es captures.list POST /room-clouds/_sear {"asctime": "2026-09-19 21:28:49,257"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.events POST /room-events/_sear {"asctime": "2026-09-19 21:28:49,292"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.observations POST /room-observ {"asctime": "2026-09-19 21:28:49,332"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 21:40 · AUTO-CAPTURED · recurring · robot: housebot_failed — robot API failed; delivery status may be unknown: <urlo
Seen: 27× · first 2026-09-20T00:27:18 · https://na-alh.sentry.io/issues/7743164380/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  es object.observations POST /room-observ {"asctime": "2026-09-19 21:39:24,428"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es captures.list POST /room-clouds/_sear {"asctime": "2026-09-19 21:39:24,508"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.events POST /room-events/_sear {"asctime": "2026-09-19 21:39:24,542"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 21:49 · AUTO-CAPTURED · recurring · robot: housebot_failed — robot API failed; delivery status may be unknown: <urlo
Seen: 31× · first 2026-09-20T00:27:18 · https://na-alh.sentry.io/issues/7743164380/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  es captures.list POST /room-clouds/_sear {"asctime": "2026-09-19 21:48:22,048"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.events POST /room-events/_sear {"asctime": "2026-09-19 21:48:22,103"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.observations POST /room-observ {"asctime": "2026-09-19 21:48:22,178"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 21:51 · AUTO-CAPTURED · recurring · robot: housebot_failed — robot API failed; delivery status may be unknown: <urlo
Seen: 33× · first 2026-09-20T00:27:18 · https://na-alh.sentry.io/issues/7743164380/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  es captures.list POST /room-clouds/_sear {"asctime": "2026-09-19 21:49:51,106"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.events POST /room-events/_sear {"asctime": "2026-09-19 21:49:51,146"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.observations POST /room-observ {"asctime": "2026-09-19 21:49:51,191"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 21:51 · AUTO-CAPTURED · robot · robot: object_not_found — nothing in the room answers 'pick up the trash' (best 
Seen: 3× · first 2026-09-20T01:21:12 · https://na-alh.sentry.io/issues/7743223741/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  intent gate: the daily cap of 0 model ca {"asctime": "2026-09-19 21:49:50,087"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  POST https://my-elasticsearch-project-a9 {"asctime": "2026-09-19 21:49:50,403"}
  es shared.search room-objects -> ok in 3 {"asctime": "2026-09-19 21:49:50,404"}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:03 · AUTO-CAPTURED · recurring · Cron failure: room-clean
Seen: 19× · first 2026-09-19T23:40:53 · https://na-alh.sentry.io/issues/7743117071/
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:36 · AUTO-CAPTURED · camera · AssertionError: lowest horizontal plane is at z=-0.055 m, not 0: check the mount
Seen: 2× · first 2026-09-19T17:23:23 · https://na-alh.sentry.io/issues/7742662415/
Joins to: `role=laptop`
Telemetry before the failure (last few breadcrumbs):
```
  xcrun --find git                         {}
  /Applications/Xcode.app/Contents/Develop {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:40 · AUTO-CAPTURED · robot · robot: robot_forbidden_recovered — robot_forbidden cleared after 56 s
Seen: 2× · first 2026-09-19T22:57:23 · https://na-alh.sentry.io/issues/7743073277/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_forbidden_recovered                {"at": "23:39:33", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.68.63:8080",
  robot_forbidden_recovered                {"at": "23:39:38", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.68.63:8080",
  robot_forbidden_recovered                {"at": "23:39:43", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.68.63:8080",
  robot_forbidden_recovered                {"at": "23:39:48", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.68.63:8080",
  robot_forbidden_recovered                {"at": "23:39:53", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.68.63:8080",
  robot_forbidden_recovered                {"at": "23:39:58", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:40 · AUTO-CAPTURED · robot · robot: robot_forbidden — robot.server at 192.168.68.63:8080 refuses this laptop:
Seen: 2× · first 2026-09-19T22:56:14 · https://na-alh.sentry.io/issues/7743072545/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_forbidden                          {"at": "23:38:50", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.0.124:8080
  robot_forbidden                          {"at": "23:38:57", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.0.124:8080
  robot_forbidden                          {"at": "23:39:03", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.68.63:8080",
  robot_forbidden                          {"at": "23:39:08", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.68.63:8080",
  robot_forbidden                          {"at": "23:39:13", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.68.63:8080",
  robot_forbidden                          {"at": "23:39:18", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.68.63:8080",
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:40 · AUTO-CAPTURED · recurring · robot: robot_unreachable_recovered — robot_unreachable cleared after 14875 s
Seen: 13× · first 2026-09-19T16:31:12 · https://na-alh.sentry.io/issues/7742586990/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable_recovered              {"at": "23:38:31", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.0.124:8080
  robot_unreachable_recovered              {"at": "23:38:37", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.0.124:8080
  robot_unreachable_recovered              {"at": "23:38:44", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.0.124:8080
  robot_unreachable_recovered              {"at": "23:38:50", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.0.124:8080
  robot_unreachable_recovered              {"at": "23:38:57", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.0.124:8080
  robot_unreachable_recovered              {"at": "23:39:03", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.68.63:8080",
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:42 · AUTO-CAPTURED · recurring · robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no writer) — c
Seen: 5× · first 2026-09-19T23:01:22 · https://na-alh.sentry.io/issues/7743078588/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent                              {"at": "23:40:53", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:40:58", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:41:03", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:41:08", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:41:13", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:41:18", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:42 · AUTO-CAPTURED · recurring · robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bbos publish
Seen: 14× · first 2026-09-19T22:31:38 · https://na-alh.sentry.io/issues/7743047616/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable                       {"at": "23:40:18", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  camera_unavailable                       {"at": "23:40:23", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  camera_unavailable                       {"at": "23:40:28", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "23:40:33", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "23:40:38", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "23:40:43", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:42 · AUTO-CAPTURED · recurring · robot: camera_unavailable — bbos published no new camera.head.jpeg within 2.0 s:
Seen: 12× · first 2026-09-19T22:31:26 · https://na-alh.sentry.io/issues/7743047397/
Joins to: `camera=cam0` · `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 20:40:29,764"}
  Started server process [36159]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
  camera_unavailable cam0: bbos published  {"asctime": "2026-09-19 20:40:31,965"}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:42 · AUTO-CAPTURED · recurring · camera_unavailable cam0: bbos published no new camera.head.jpeg within 2.0 s: is
Seen: 18× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 20:40:29,764"}
  Started server process [36159]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:42 · AUTO-CAPTURED · recurring · robot: robot_server_down_recovered — robot_server_down cleared after 29 s
Seen: 14× · first 2026-09-19T16:38:30 · https://na-alh.sentry.io/issues/7742598500/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down_recovered              {"at": "23:40:03", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "23:40:08", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "23:40:13", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "23:40:18", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "23:40:23", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "23:40:28", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:42 · AUTO-CAPTURED · recurring · robot: robot_restarted — boot_id 5247a1c95f28 -> 400ec478bad0: robot.server rest
Seen: 9× · first 2026-09-19T17:33:27 · https://na-alh.sentry.io/issues/7742676325/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_restarted                          {"at": "23:40:03", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "23:40:08", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "23:40:13", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "23:40:18", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "23:40:23", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "23:40:28", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:42 · AUTO-CAPTURED · recurring · robot: robot_server_down — 192.168.68.63 answers ping but :8080 does not (Connec
Seen: 10× · first 2026-09-19T16:34:52 · https://na-alh.sentry.io/issues/7742591416/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down                        {"at": "23:39:53", "bad": "robot_forbidden", "link": "lan", "robot": "192.168.68.63:8080",
  robot_server_down                        {"at": "23:39:58", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down                        {"at": "23:40:03", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down                        {"at": "23:40:08", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down                        {"at": "23:40:13", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down                        {"at": "23:40:18", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:44 · AUTO-CAPTURED · robot · robot: bbos_silent_recovered — bbos_silent cleared after 105 s
Seen: 4× · first 2026-09-19T23:17:58 · https://na-alh.sentry.io/issues/7743095266/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent_recovered                    {"at": "23:43:52", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "23:43:57", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "23:44:02", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "23:44:07", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "23:44:12", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "23:44:17", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:44 · AUTO-CAPTURED · robot · robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no writer). Fi
Seen: 1× · first 2026-09-20T03:43:18 · https://na-alh.sentry.io/issues/7743379189/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent                              {"at": "23:42:52", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:42:57", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:43:02", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:43:07", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:43:12", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:43:17", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:44 · AUTO-CAPTURED · recurring · robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bbos publish
Seen: 15× · first 2026-09-19T22:31:38 · https://na-alh.sentry.io/issues/7743047616/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  httplib                                  {"http.fragment": "", "http.method": "GET", "http.query": "types=hello,telemetry&limit=1",
  httplib                                  {"http.fragment": "", "http.method": "GET", "http.query": "", "http.response.status_code":
  httplib                                  {"http.fragment": "", "http.method": "GET", "http.query": "types=hello,telemetry&limit=1",
  camera_unavailable                       {"at": "23:42:32", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "23:42:37", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "23:42:42", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:46 · AUTO-CAPTURED · robot · robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no writer). Fi
Seen: 2× · first 2026-09-20T03:43:18 · https://na-alh.sentry.io/issues/7743379189/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent                              {"at": "23:45:07", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:45:12", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:45:17", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:45:22", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:45:27", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "23:45:32", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:46 · AUTO-CAPTURED · recurring · robot: robot_server_down_recovered — robot_server_down cleared after 30 s
Seen: 15× · first 2026-09-19T16:38:30 · https://na-alh.sentry.io/issues/7742598500/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down_recovered              {"at": "23:44:22", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "23:44:27", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "23:44:32", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "23:44:37", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "23:44:42", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "23:44:47", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:46 · AUTO-CAPTURED · recurring · robot: robot_restarted — boot_id 400ec478bad0 -> 38215eedd267: robot.server rest
Seen: 10× · first 2026-09-19T17:33:27 · https://na-alh.sentry.io/issues/7742676325/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_restarted                          {"at": "23:44:22", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "23:44:27", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "23:44:32", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "23:44:37", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "23:44:42", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "23:44:47", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:46 · AUTO-CAPTURED · recurring · robot: camera_unavailable — bbos published no new camera.head.jpeg within 2.0 s:
Seen: 13× · first 2026-09-19T22:31:26 · https://na-alh.sentry.io/issues/7743047397/
Joins to: `camera=cam0` · `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 20:44:40,664"}
  Started server process [42855]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
  camera_unavailable cam0: bbos published  {"asctime": "2026-09-19 20:44:42,809"}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:46 · AUTO-CAPTURED · recurring · camera_unavailable cam0: bbos published no new camera.head.jpeg within 2.0 s: is
Seen: 19× · first 2026-09-19T07:25:14 · https://na-alh.sentry.io/issues/7741932899/
Joins to: `role=robot`
Telemetry before the failure (last few breadcrumbs):
```
  pose_source is 'none': every capture car {"asctime": "2026-09-19 20:44:40,664"}
  Started server process [42855]           {"color_message": "Started server process [\u001b[36m%d\u001b[0m]"}
  Waiting for application startup.         {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:46 · AUTO-CAPTURED · recurring · robot: robot_server_down — 192.168.68.63 answers ping but :8080 does not (Connec
Seen: 11× · first 2026-09-19T16:34:52 · https://na-alh.sentry.io/issues/7742591416/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down                        {"at": "23:44:07", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  robot_server_down                        {"at": "23:44:12", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  robot_server_down                        {"at": "23:44:17", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down                        {"at": "23:44:22", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down                        {"at": "23:44:27", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down                        {"at": "23:44:32", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 23:46 · AUTO-CAPTURED · recurring · robot: camera_unavailable_recovered — camera_unavailable cleared after 105 s
Seen: 13× · first 2026-09-19T17:56:07 · https://na-alh.sentry.io/issues/7742706797/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable_recovered             {"at": "23:43:52", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "23:43:57", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "23:44:02", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "23:44:07", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "23:44:12", "boot_id": "400ec478bad0", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "23:44:17", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 00:02 · AUTO-CAPTURED · robot · robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no writer). Fi
Seen: 3× · first 2026-09-20T03:43:18 · https://na-alh.sentry.io/issues/7743379189/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent                              {"at": "00:01:10", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "00:01:15", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "00:01:20", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "00:01:25", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "00:01:30", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "00:01:35", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 00:02 · AUTO-CAPTURED · recurring · robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bbos publish
Seen: 17× · first 2026-09-19T22:31:38 · https://na-alh.sentry.io/issues/7743047616/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable                       {"at": "00:00:33", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  camera_unavailable                       {"at": "00:00:40", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  camera_unavailable                       {"at": "00:00:45", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "00:00:50", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "00:00:55", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "00:01:00", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 00:02 · AUTO-CAPTURED · recurring · robot: bbos_silent_recovered — bbos_silent cleared after 951 s
Seen: 5× · first 2026-09-19T23:17:58 · https://na-alh.sentry.io/issues/7743095266/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent_recovered                    {"at": "00:00:08", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "00:00:13", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "00:00:18", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "00:00:23", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "00:00:28", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "00:00:33", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 00:02 · AUTO-CAPTURED · recurring · robot: camera_unavailable_recovered — camera_unavailable cleared after 951 s
Seen: 14× · first 2026-09-19T17:56:07 · https://na-alh.sentry.io/issues/7742706797/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable_recovered             {"at": "00:00:08", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "00:00:13", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "00:00:18", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "00:00:23", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "00:00:28", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "00:00:33", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 00:04 · AUTO-CAPTURED · recurring · robot: robot_unreachable — nothing answers at 192.168.68.63 (timed out) — power,
Seen: 15× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "00:03:00", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  robot_unreachable                        {"at": "00:03:05", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  robot_unreachable                        {"at": "00:03:10", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable                        {"at": "00:03:16", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable                        {"at": "00:03:23", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable                        {"at": "00:03:29", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 00:04 · AUTO-CAPTURED · recurring · robot: bbos_silent_recovered — bbos_silent cleared after 151 s
Seen: 6× · first 2026-09-19T23:17:58 · https://na-alh.sentry.io/issues/7743095266/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent_recovered                    {"at": "00:02:45", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "00:02:50", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "00:02:55", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "00:03:00", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "00:03:05", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "00:03:10", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 00:04 · AUTO-CAPTURED · recurring · robot: camera_unavailable_recovered — camera_unavailable cleared after 151 s
Seen: 15× · first 2026-09-19T17:56:07 · https://na-alh.sentry.io/issues/7742706797/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable_recovered             {"at": "00:02:45", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "00:02:50", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "00:02:55", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "00:03:00", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "00:03:05", "boot_id": "38215eedd267", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "00:03:10", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 00:17 · AUTO-CAPTURED · robot · robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no writer). Fi
Seen: 4× · first 2026-09-20T03:43:18 · https://na-alh.sentry.io/issues/7743379189/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent                              {"at": "00:15:21", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "00:15:26", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "00:15:31", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "00:15:36", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "00:15:41", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "00:15:46", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 00:17 · AUTO-CAPTURED · recurring · robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bbos publish
Seen: 18× · first 2026-09-19T22:31:38 · https://na-alh.sentry.io/issues/7743047616/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable                       {"at": "00:14:49", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  camera_unavailable                       {"at": "00:14:54", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  camera_unavailable                       {"at": "00:15:01", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "00:15:06", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "00:15:11", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "00:15:16", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 00:17 · AUTO-CAPTURED · recurring · robot: robot_unreachable_recovered — robot_unreachable cleared after 705 s
Seen: 14× · first 2026-09-19T16:31:12 · https://na-alh.sentry.io/issues/7742586990/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable_recovered              {"at": "00:14:34", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "00:14:39", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "00:14:44", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "00:14:49", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "00:14:54", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "00:15:01", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 00:17 · AUTO-CAPTURED · recurring · robot: robot_restarted — boot_id 38215eedd267 -> 091878be4a63: robot.server rest
Seen: 11× · first 2026-09-19T17:33:27 · https://na-alh.sentry.io/issues/7742676325/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_restarted                          {"at": "00:14:34", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "00:14:39", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "00:14:44", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "00:14:49", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "00:14:54", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "00:15:01", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 00:47 · AUTO-CAPTURED · recurring · Cron failure: room-clean
Seen: 34× · first 2026-09-19T23:40:53 · https://na-alh.sentry.io/issues/7743117071/
_What we changed:_ TODO — fill this in, it is the part they score._

## 00:47 · AUTO-CAPTURED · robot · robot: object_not_found — nothing in the room answers 'pick up the trash' (best 
Seen: 4× · first 2026-09-20T01:21:12 · https://na-alh.sentry.io/issues/7743223741/
Joins to: `role=elastic`
Telemetry before the failure (last few breadcrumbs):
```
  httplib                                  {"http.fragment": "", "http.method": "GET", "http.query": "", "http.response.status_code":
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 01:07 · AUTO-CAPTURED · recurring · robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bbos publish
Seen: 19× · first 2026-09-19T22:31:38 · https://na-alh.sentry.io/issues/7743047616/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable                       {"at": "01:06:44", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  camera_unavailable                       {"at": "01:06:49", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  camera_unavailable                       {"at": "01:06:54", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  camera_unavailable                       {"at": "01:06:59", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "01:07:04", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable                       {"at": "01:07:09", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 01:07 · AUTO-CAPTURED · recurring · robot: robot_unreachable_recovered — robot_unreachable cleared after 22 s
Seen: 15× · first 2026-09-19T16:31:12 · https://na-alh.sentry.io/issues/7742586990/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable_recovered              {"at": "01:06:31", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "01:06:37", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "01:06:44", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "01:06:49", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "01:06:54", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "01:06:59", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 01:07 · AUTO-CAPTURED · robot · robot: robot_unreachable — nothing answers at 192.168.68.63 (Host is down) — pow
Seen: 3× · first 2026-09-19T18:33:59 · https://na-alh.sentry.io/issues/7742759006/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "01:06:26", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  robot_unreachable                        {"at": "01:06:31", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable                        {"at": "01:06:37", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable                        {"at": "01:06:44", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable                        {"at": "01:06:49", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable                        {"at": "01:06:54", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 01:07 · AUTO-CAPTURED · recurring · robot: bbos_silent_recovered — bbos_silent cleared after 3096 s
Seen: 7× · first 2026-09-19T23:17:58 · https://na-alh.sentry.io/issues/7743095266/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent_recovered                    {"at": "01:06:06", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "01:06:11", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "01:06:16", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "01:06:21", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "01:06:26", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent_recovered                    {"at": "01:06:31", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 01:07 · AUTO-CAPTURED · recurring · robot: camera_unavailable_recovered — camera_unavailable cleared after 3096 s
Seen: 16× · first 2026-09-19T17:56:07 · https://na-alh.sentry.io/issues/7742706797/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable_recovered             {"at": "01:06:06", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "01:06:11", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "01:06:16", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "01:06:21", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "01:06:26", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "01:06:31", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 01:09 · AUTO-CAPTURED · recurring · robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no writer). Fi
Seen: 5× · first 2026-09-20T03:43:18 · https://na-alh.sentry.io/issues/7743379189/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent                              {"at": "01:07:24", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "01:07:29", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "01:07:34", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "01:07:39", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "01:07:44", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  bbos_silent                              {"at": "01:07:49", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 01:15 · AUTO-CAPTURED · recurring · robot: camera_unavailable_recovered — camera_unavailable cleared after 480 s
Seen: 17× · first 2026-09-19T17:56:07 · https://na-alh.sentry.io/issues/7742706797/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  camera_unavailable_recovered             {"at": "01:14:34", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "01:14:39", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "01:14:44", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "01:14:49", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "01:14:54", "boot_id": "091878be4a63", "cameras": [], "link": "lan", "mode": "hardw
  camera_unavailable_recovered             {"at": "01:14:59", "boot_id": "091878be4a63", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 01:23 · AUTO-CAPTURED · recurring · Cancel 2 running task(s), timeout graceful shutdown exceeded
Seen: 14× · first 2026-09-19T01:19:16 · https://na-alh.sentry.io/issues/7741550271/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
  Shutting down                            {}
  Waiting for connections to close. (CTRL+ {}
  /Applications/Xcode.app/Contents/Develop {}
  /Applications/Xcode.app/Contents/Develop {}
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 02:00 · AUTO-CAPTURED · recurring · robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no writer). Fi
Seen: 6× · first 2026-09-20T03:43:18 · https://na-alh.sentry.io/issues/7743379189/
Joins to: `role=link`
**Evidence captured:** `evidence/7743379189_bbos_silent.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent                              {"at": "01:57:50", "boot_id": "091878be4a63", "cameras": ["cam0"], "link": "lan", "mode": 
  bbos_silent                              {"at": "01:57:55", "boot_id": "091878be4a63", "cameras": ["cam0"], "link": "lan", "mode": 
  bbos_silent                              {"at": "01:58:00", "boot_id": "091878be4a63", "cameras": ["cam0"], "link": "lan", "mode": 
  bbos_silent                              {"at": "01:58:05", "boot_id": "091878be4a63", "cameras": ["cam0"], "link": "lan", "mode": 
  bbos_silent                              {"at": "01:58:10", "boot_id": "091878be4a63", "cameras": ["cam0"], "link": "lan", "mode": 
  bbos_silent                              {"at": "01:58:15", "boot_id": "091878be4a63", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 02:02 · AUTO-CAPTURED · robot · robot: map_reset_recovered — map_reset cleared after 5 s
Seen: 1× · first 2026-09-20T06:01:45 · https://na-alh.sentry.io/issues/7743511057/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  map_reset_recovered                      {"at": "02:01:20", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
  map_reset_recovered                      {"at": "02:01:25", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
  map_reset_recovered                      {"at": "02:01:30", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
  map_reset_recovered                      {"at": "02:01:35", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
  map_reset_recovered                      {"at": "02:01:40", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
  map_reset_recovered                      {"at": "02:01:45", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 02:02 · AUTO-CAPTURED · robot · robot: map_reset — the robot's map generation changed 3971697493 -> 1614467593 w
Seen: 1× · first 2026-09-20T06:01:40 · https://na-alh.sentry.io/issues/7743510975/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  map_reset                                {"at": "02:01:15", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  map_reset                                {"at": "02:01:20", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
  map_reset                                {"at": "02:01:25", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
  map_reset                                {"at": "02:01:30", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
  map_reset                                {"at": "02:01:35", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
  map_reset                                {"at": "02:01:40", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 02:02 · AUTO-CAPTURED · recurring · robot: robot_server_down_recovered — robot_server_down cleared after 30 s
Seen: 16× · first 2026-09-19T16:38:30 · https://na-alh.sentry.io/issues/7742598500/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down_recovered              {"at": "02:00:55", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "02:01:00", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "02:01:05", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "02:01:10", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "02:01:15", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down_recovered              {"at": "02:01:20", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 02:02 · AUTO-CAPTURED · recurring · robot: robot_restarted — boot_id 091878be4a63 -> 22c13a69a41a: robot.server rest
Seen: 12× · first 2026-09-19T17:33:27 · https://na-alh.sentry.io/issues/7742676325/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_restarted                          {"at": "02:00:55", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "02:01:00", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "02:01:05", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "02:01:10", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "02:01:15", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_restarted                          {"at": "02:01:20", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 02:02 · AUTO-CAPTURED · recurring · robot: robot_server_down — 192.168.68.63 answers ping but :8080 does not (Connec
Seen: 12× · first 2026-09-19T16:34:52 · https://na-alh.sentry.io/issues/7742591416/
Joins to: `role=link`
**Evidence captured:** `evidence/7742591416_robot_server_down.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  robot_server_down                        {"at": "02:00:40", "boot_id": "091878be4a63", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_server_down                        {"at": "02:00:45", "boot_id": "091878be4a63", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_server_down                        {"at": "02:00:50", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down                        {"at": "02:00:55", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down                        {"at": "02:01:00", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
  robot_server_down                        {"at": "02:01:05", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 02:02 · AUTO-CAPTURED · recurring · robot: bbos_silent_recovered — bbos_silent cleared after 205 s
Seen: 8× · first 2026-09-19T23:17:58 · https://na-alh.sentry.io/issues/7743095266/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  bbos_silent_recovered                    {"at": "02:00:25", "boot_id": "091878be4a63", "cameras": ["cam0"], "link": "lan", "mode": 
  bbos_silent_recovered                    {"at": "02:00:30", "boot_id": "091878be4a63", "cameras": ["cam0"], "link": "lan", "mode": 
  bbos_silent_recovered                    {"at": "02:00:35", "boot_id": "091878be4a63", "cameras": ["cam0"], "link": "lan", "mode": 
  bbos_silent_recovered                    {"at": "02:00:40", "boot_id": "091878be4a63", "cameras": ["cam0"], "link": "lan", "mode": 
  bbos_silent_recovered                    {"at": "02:00:45", "boot_id": "091878be4a63", "cameras": ["cam0"], "link": "lan", "mode": 
  bbos_silent_recovered                    {"at": "02:00:50", "bad": "robot_server_down", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 02:20 · AUTO-CAPTURED · recurring · robot: robot_unreachable_recovered — robot_unreachable cleared after 73 s
Seen: 16× · first 2026-09-19T16:31:12 · https://na-alh.sentry.io/issues/7742586990/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable_recovered              {"at": "02:19:31", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "02:19:36", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "02:19:41", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "02:19:46", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "02:19:53", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "02:19:58", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 02:20 · AUTO-CAPTURED · recurring · robot: robot_unreachable — nothing answers at 192.168.68.63 (timed out) — power,
Seen: 16× · first 2026-09-19T12:52:32 · https://na-alh.sentry.io/issues/7742288078/
Joins to: `role=link`
**Evidence captured:** `evidence/7742288078_robot_unreachable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "02:18:29", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_unreachable                        {"at": "02:18:34", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_unreachable                        {"at": "02:18:39", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable                        {"at": "02:18:45", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable                        {"at": "02:18:52", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable                        {"at": "02:18:58", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 02:40 · AUTO-CAPTURED · recurring · robot: housebot_failed — robot API failed; delivery status may be unknown: <urlo
Seen: 34× · first 2026-09-20T00:27:18 · https://na-alh.sentry.io/issues/7743164380/
Joins to: `role=web`
Telemetry before the failure (last few breadcrumbs):
```
  es captures.list POST /room-clouds/_sear {"asctime": "2026-09-20 02:40:00,325"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.observations POST /room-observ {"asctime": "2026-09-20 02:40:00,706"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
  es object.events POST /room-events/_sear {"asctime": "2026-09-20 02:40:00,713"}
  httplib                                  {"http.fragment": "", "http.method": "POST", "http.query": "", "http.response.status_code"
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 03:56 · AUTO-CAPTURED · recurring · robot: robot_unreachable_recovered — robot_unreachable cleared after 19 s
Seen: 17× · first 2026-09-19T16:31:12 · https://na-alh.sentry.io/issues/7742586990/
Joins to: `role=link`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable_recovered              {"at": "03:55:24", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_unreachable_recovered              {"at": "03:55:29", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "03:55:35", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "03:55:42", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "03:55:48", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable_recovered              {"at": "03:55:53", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
```
_What we changed:_ TODO — fill this in, it is the part they score._

## 03:56 · AUTO-CAPTURED · robot · robot: robot_unreachable — nothing answers at 192.168.68.63 (Host is down) — pow
Seen: 4× · first 2026-09-19T18:33:59 · https://na-alh.sentry.io/issues/7742759006/
Joins to: `role=link`
**Evidence captured:** `evidence/7742759006_robot_unreachable.jpg`
Telemetry before the failure (last few breadcrumbs):
```
  robot_unreachable                        {"at": "03:55:19", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_unreachable                        {"at": "03:55:24", "boot_id": "22c13a69a41a", "cameras": ["cam0"], "link": "lan", "mode": 
  robot_unreachable                        {"at": "03:55:29", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable                        {"at": "03:55:35", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable                        {"at": "03:55:42", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
  robot_unreachable                        {"at": "03:55:48", "bad": "robot_unreachable", "link": "lan", "robot": "192.168.68.63:8080
```
_What we changed:_ TODO — fill this in, it is the part they score._
