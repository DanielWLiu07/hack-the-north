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

---

## 2. The Pi's HTTP API

Base URL: `http://<pi-ip>:8080`. No auth (we're on our own private network; see
[`08-risks.md` R1](08-risks.md)). JSON in, JSON out. All errors use the shape in §2.7.

### 2.1 `POST /capture`
Grab synchronized frames from all stereo pairs. **The single most important endpoint.**

```jsonc
// request
{
  "cameras": ["cam0", "cam1", "cam2"],   // omit = all configured cameras
  "frames": 4,                            // frames per camera, for majority voting
  "quality": 85                           // JPEG quality
}

// response  (200, typically 1200-2000 ms)
{
  "capture_id": "cap_0912",
  "started_at":  "2026-09-19T14:22:07.412Z",
  "finished_at": "2026-09-19T14:22:09.031Z",
  "pose": { "x": 1.24, "z": 0.85, "yaw": 0.31 },   // odometry at capture time
  "pose_source": "anchor",                          // "anchor" | "odometry"
  "frames": [
    { "camera": "cam0", "seq": 0, "ts": "...", "jpeg_b64": "...", "width": 2560, "height": 720 }
    // ... frames × cameras entries
  ]
}
```

**Why round-robin.** Three 2560×720 MJPG streams on one Pi 5 will saturate USB. The
implementation opens one camera, grabs its frames, closes it, moves to the next. Total ~1.5 s,
which is fine because commits are discrete events, not a video feed. See
[`02-hardware.md`](02-hardware.md#two-constraints-that-will-bite).

**Why `pose` is in the response.** The cloud is meaningless without knowing where the robot
was standing. Returning it *with* the frames makes the capture atomic — you can never
accidentally pair frames with a pose read a second later.

**Why base64 JPEG and not raw.** Raw is 5.5 MB per stereo frame; JPEG at q85 is ~400 KB.
Base64 adds 33% and keeps everything one JSON document, which is worth it at this scale. If
transfer becomes the bottleneck, switch to `multipart/form-data` — the shape stays the same.

### 2.2 `GET /pose`
Cheap, no camera work. For the executor to check arrival.
```jsonc
{ "x": 1.24, "z": 0.85, "yaw": 0.31, "source": "anchor",
  "odom_residual_m": 0.004, "balanced": true, "ts": "..." }
```

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
→ { "job_id": "job_88de", "duration_s": 3.1 }
```

### 2.6 `POST /led`
```jsonc
{ "state": "clean" }    // clean=green · dirty=amber · conflict=red
                        // working=pulsing blue · error=red flash
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
| code | means | what the caller does |
|---|---|---|
| `camera_unavailable` | a camera didn't open | retry once, then capture with the rest and flag reduced coverage |
| `not_balanced` | robot is recovering or fallen | **refuse arm commands**, alert the operator |
| `unreachable_pose` | IK found no solution | report the op as unapplied (see the *cannot apply hunk* beat) |
| `job_superseded` | a newer command replaced this one | drop it |
| `busy` | another job is running | queue or drop; never run two arm jobs |

---

## 3. The WebSocket

`ws://<pi-ip>:8080/stream` — **one socket, server→client push, multiplexed by `t`.**
Newline-delimited JSON. The laptop connects once at startup and reconnects with backoff.

### 3.1 Message types

```jsonc
// TELEMETRY — batched. 50 Hz sampled, one message per 100 ms carrying 5 samples.
{ "t": "telemetry", "from": "2026-09-19T14:22:07.400Z", "hz": 50,
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

// LOG — anything the Pi wants on the laptop's screen and in Sentry
{ "t": "log", "level": "warn", "msg": "cam2 dropped 2 of 4 frames", "ts": "..." }

// HELLO — on connect, so the laptop knows what it's talking to
{ "t": "hello", "cameras": ["cam0","cam1","cam2"], "arm": true,
  "fw": "gitspace-pi-0.3", "calib_sha": "8f2a1c" }
```

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
{ "t": "frame", "capture_id": "cap_0912", "camera": "cam0", "seq": 0,
  "t_mono": 81234.5519, "w": 2560, "h": 720, "fmt": "mjpg" }
```

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
  "frames_expected": 12, "pose": {...}, "skew_ms": 1.4, "tilt_rate_max": 0.031 }
{ "t":"capture_end",   "capture_id":"cap_0912", "frames_sent": 12 }
```

The laptop holds a buffer keyed by `capture_id`, completes on `capture_end`, and **drops the
whole capture on timeout** rather than processing a partial one. A capture missing cam2 is not
a capture with less coverage — it is a capture whose fused cloud has a hole exactly where the
quality gate cannot see it.

### Keep `POST /capture` as the trigger
Command in over HTTP (returns `capture_id` immediately, with a status code when a camera is
unavailable); pixels out over `/frames`. Same split as `/arm`: the request is a request, the
payload is a stream.

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
├ sentry_sdk.init()             ├ sentry_sdk.init()
├ errors from server.py         ├ errors from every module
├ spans: capture, arm, drive    ├ spans: depth, fuse, segment, describe,
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
| **8080** | Pi | WebSocket `/stream` | telemetry · detections · job progress · logs |
| **8000** | laptop | HTTP | `web/` dashboard API — `/api/status` `/api/search` `/api/object/{id}` `/api/history` `/api/analytics/{name}` `/api/command` `/api/resolve` |
| **8000** | laptop | **SSE** `/api/events` | live dashboard updates — status · job · capture · conflict |
| **9876** | laptop | gRPC | Rerun |
| **9000** | laptop | HTTP | `/hooks/github` `/hooks/action` — or skip it entirely and poll SQS |
| 443 | cloud | HTTPS | Elasticsearch, Sentry, OpenAI, ElevenLabs |

Static IPs on our own router. Put them in `robot/config.py` and `.env`, never hardcode.

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
