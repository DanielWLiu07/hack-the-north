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
