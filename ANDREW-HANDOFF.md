# Handoff to Andrew (gitirl-agent): the contracts you're waiting on

From Daniel's side of GITSPACE (roomctl · elastic · web · bridge). Your README ends: *"The
WebSocket and camera protocols are provisional until Daniel provides authoritative contracts."*
Your PROTOCOL.md says *"Daniel's backend schema is authoritative once finalized."* **This is
that schema, finalized 2026-09-19** against your `awzheng/gitirl @ 35f4c96` and re-checked against `b4f3e07` (parser unchanged; the transport is HTTP + SSE, §2 and §2b). It agrees with
[`docs/31-agent-panel-contract.md`](docs/31-agent-panel-contract.md), which the agent panel and
its endpoint (`bridge/`, `POST /api/agent/command`) are built on. Background:
[`docs/30`](docs/30-andrew-agent-boundary.md).

**Short version:** keep your six verbs. You decipher text and you drive the robot through a
plan we hand you. We own git, ordering and the final check. `restore` and `revert` are two
different operations and neither one is an alias for the other.

---

## 1. The command set (FINAL)

### Who does what

| | **your middleware** (gitirl-agent) | **our backend** (roomctl · bridge · web) |
|---|---|---|
| owns | deciphering text into `GitIRLCommand`, **for your six verbs only** · running a `gitspace.plan/1` through your `RobotAdapter`: per-op verify, retry ≤ 2, report | every verb's effect on `room.git` · planning (dependency graph, staging spots, where to stand) · the graph-native verbs · the **final** verification (rescan → `git status`) |
| does NOT own | git, named states, diffs, ordering. Your `StateStore` / `DiffEngine` / `DeterministicPlanner` stay as dev scaffolding. In production a named state is a git **tag**, a diff comes from us, and a plan is ours | the robot's motion loop (verify/retry close to the robot is yours: your README's own rule) |

### Every verb

| verb | who deciphers | git effect (ours) | robot? |
|---|---|---|---|
| `status` | **your parser** | read: scan, then `git status` (`room status`) | no |
| `diff [<state>]` | **your parser** | read: `git diff <state>` (default HEAD). The CLI compares the last scan and the panel endpoint compares HEAD (docs/31) | no |
| `log` | **your parser** | read: `git log` | no |
| `add` | **your parser** | index only: stage the scanned room (`room add zones/`; the panel's **Stage**) | no |
| `commit <name>` | **your parser** | commit the scanned room, then tag `<name>` at that commit. A name that already exists moves to the new commit; the old one stays in history. CLI today: `room commit -m "…" && room tag -f <name>` | no |
| `restore <state>` | **your parser** | `git restore --source=<state> --staged --worktree -- zones` + a commit **on top of HEAD**, `Restore <state>`. **Built:** `room restore <state>` | **yes**: plan → you |
| `restore` (no state) | our CLI (your grammar needs a state) | nothing: HEAD is already the target | **yes**: puts back what moved since the last commit |
| `revert <commit>` | **never sent to you** | `git revert`: a new commit that undoes **that one commit** | yes: plan → you |
| `checkout <ref>` | never sent to you | moves HEAD | yes |
| `reset --hard <ref>` | never sent to you | moves the branch tip back | yes |
| `cherry-pick` · `merge` · `resolve` | never sent to you | graph-native: previews and plans in web/bridge; roomctl refuses them until they're wired | yes, once wired |
| `branch` · `tag` · `stash` | never sent to you | refs only / not wired | no |
| "where are my scissors" | never sent to you | our agent (Elasticsearch) | points, later |

**Your enum stays at six.** Please don't add `revert`, `merge`, `cherry-pick`, `branch`,
`checkout` or `reset`. They're graph-native, they're ours, and the panel never sends them to you.
For that text your parser should keep answering `unknown_command`.

### `restore` is not `revert` (and neither is `checkout`)

Take this history, with the room standing at c3:

```
c1  room init          mug 0.42, cup (0.30, -0.22)
c2  mug moved          mug 0.61
c3  cup moved          cup (0.18, -0.12)                     ← HEAD (main)
```

| command | new history | the room afterwards |
|---|---|---|
| `restore c1` | `c4 "Restore c1"` on top of c3; main stays on main | **both** mug and cup at c1's poses (the whole room goes to that state) |
| `revert c2` | `c4 'Revert "mug moved"'` on top of c3 | **only** the mug goes back. The cup stays where c3 put it |
| `checkout c1` | none. HEAD moves to c1 (detached) | both at c1, but later commits land off the branch |

`restore` means **make the room look like that state**. It's your verb and the "set my room
back to study mode" beat. `revert` means **undo that one change**. That verb is ours and it
only comes from the graph. The two produce different rooms from the same history, so aliasing
them would put the robot in the wrong place. Our side enforces this:
- if your parser answers `restore` for text that says "revert", we refuse with `intent_mismatch`;
- the endpoint's `action.as` reports what actually ran (`"restore"`, never `"checkout"`);
- `tests/test_cli.py::test_restore_puts_the_whole_room_at_a_state_as_a_new_commit` and
  `::test_revert_is_not_restore` pin the table above against real git.

### `target_state` is any git ref

A named state like `study` is a **tag**, and a ref can also be a branch, a sha, `HEAD~2` or
`a3f9c1^`. Please widen `[\w-]+` in your restore/commit/diff patterns to `[\w./~^-]+`.
Resolution order is git's own. For "before dinner" style time phrases, *we* resolve a
wall-clock time to a sha through `room-events` (docs/14 Phase 4). You never see time.

---

## 2. The envelope (FINAL)

Your envelope is unchanged: `{"type", "request_id", "timestamp", "payload"}`. Once you're on
this contract, add `"version": 1`. Every `type` below comes from **your** `MessageType` enum,
and we add no new ones.

**Transport: HTTP + SSE (revised for your `b4f3e07`).** You retired the WebSocket client and
now talk to web over HTTP: `GET /api/state`, `POST /api/command` and `GET /api/events` (SSE),
via `protocol/daniel.py` and `transport/daniel_api.py`. We follow you, and `/ws/gitirl-agent`
is legacy. The message table below is unchanged, except that it rides on HTTP instead of
socket frames. Your INTEGRATION.md asks are right. **Both endpoints are now built (docs/10
D46): `GET /api/jobs/{id}` and an authenticated `POST /api/jobs/{id}/result`. §2b is the
complete contract**: auth, the job shape, the fixture, the frame, and idempotency.
`POST /api/internal/event` stays loopback-only on purpose.

A job carries two op lists. `ops` is web's git-level preview (`moved`/`added`/`removed`, no
order, no staging, no base poses), for drawing. `plan` is `gitspace.plan/1` (§4), and it is the
only thing you execute. Public base URL: `https://gitspace-five.vercel.app` (the API is
proxied to our GCP tier; docs/19 "As built").

### Messages, by direction

| # | direction | `type` | `payload` | when |
|---|---|---|---|---|
| 1 | us → you | `user_command` | `{"text": "set my room back to study mode"}` | only for text routed to your six verbs |
| 2 | you → us | `parsed_command` | `{"command", "target_state", "message", "raw_text", "metadata"}`: your `GitIRLCommand` minus `request_id`, which is in the envelope. `raw_text` and `metadata` are optional | answer to 1 |
| 2′ | you → us | `error` | `{"code": "unknown_command", "message", "details"}` | answer to 1 when it isn't one of the six |
| 3 | us → you | `robot_action` | `{"plan": <gitspace.plan/1>}`. **One message per plan, never one per op** (§4) | only after the git side is committed |
| 4 | you → us | `command_status` | `{"status": "op_started" \| "op_done" \| "op_retry" \| "op_failed" \| "op_skipped", "message", "seq", "object_id", "attempt"}` | while a plan runs |
| 5 | you → us | `command_result` | `{"status": "RESTORE_COMPLETE" \| "CONFLICT" \| "FAILED", "message", "attempts", "ops": [{"seq", "object_id", "status": "done" \| "failed" \| "skipped", "attempts", "error"}]}` | **only** in reply to 3 |
| 6 | you → us | `robot_observation` | `{"observation": {"objects": [...], "metadata": {"frame": "world_z_up"}}}` | optional: what your per-op verify saw |

Rules:
- **No `command_result` for a `user_command`, ever.** In production you decipher text and you
  do not execute it. Your dev orchestrator's `RESTORE_COMPLETE` for a text command claims a
  physical restore that never happened. Today we file it under `ignored`; please stop sending
  it. A `command_result` means "I ran the plan you sent me".
- **Nothing moves from text.** The robot moves only from a `robot_action` plan, and we send
  one only after the git side is committed (`Restore study` exists before the arm lifts
  anything). The same holds for the graph verbs.
- **`request_id`.** The panel mints it for text. A plan caused by that text carries the **same**
  id, so one trace covers the whole round trip. A plan from the graph or the CLI gets an id
  from us. Our backend dedupes `user_command` (a repeat returns the first answer and never
  plans twice), so you don't need to store those ids.
- **Idempotency of motion is yours:** keep the ids of `robot_action`s you've accepted for the
  life of the process. A repeat must return the stored result, or `command_status` in-flight,
  and must **never re-execute**. We never resend a plan whose delivery we can't confirm. After
  a dropped socket we rescan and send a **new** plan as `<request_id>/2`. This is the rule our
  own HTTP client follows: never re-send a motion that may have started.
- **Poses carry `"frame": "world_z_up"`.** We refuse a pose-carrying message without it
  (`frame_mismatch`) and never convert it (§5).

Error codes you may see from us: `bad_request`, `unknown_command`, `intent_mismatch`,
`frame_mismatch`, `not_found` (a state that doesn't exist deciphers fine and fails at planning),
`bridge_unavailable`, `room_unavailable`.

### One round trip: "set my room back to study mode"

```
panel ──user_command──▶ bridge ──user_command──▶ YOU
                        bridge ◀─parsed_command── YOU      {command: restore, target_state: study}
                        bridge: plan preview (git reads only), shown in the panel
panel "Stage" + "Commit" (the panel's backend, the only writer; refuses if HEAD moved)
                        roomctl: HEAD = "Restore study" → plan observed room → HEAD
                        roomctl ──robot_action {plan}──▶ YOU → RobotAdapter → verify → retry ≤ 2
                        roomctl ◀─command_status ×N──── YOU
                        roomctl ◀─command_result─────── YOU
                        roomctl: rescan → `git status`: clean, or an honest "2 of 3"
```

What's built and what isn't: the text path (1, 2, 2′) and the preview are built and tested in `bridge/` (25 tests), served today by your real parser run as `--jsonl`. `room restore` is built (tests/test_cli.py). The job and result endpoints for a remote edge (D46) are **built** (§2b, `web/jobs.py`, 10 tests plus a live run). Over HTTP, rows 3–5 above become: `robot_action` = a job whose `plan` you fetch, `command_status` = a `running` report, and `command_result` = the terminal report. roomctl driving the robot directly (`ROOM_ROBOT=http`, `roomctl/robot_client.py`, docs/16) stays as the fallback.

## 2b. Jobs over HTTP: your five asks, answered (built 2026-09-19)

Everything here runs in `web/jobs.py` and is tested in `web/tests/test_jobs.py`. Base URL: §2.
**A job lives on the server that made it.** Make it and poll it through the same base URL:
`POST /api/command` there, then `GET /api/jobs/{id}` there.

### Auth: one header, on the one write that needs it

```
Authorization: Bearer <GITIRL_CLOUD_TOKEN>
```

This is exactly what your `DanielAPIClient` already sends when `GITIRL_CLOUD_TOKEN` is set.
Daniel sends you the value privately; it is never in a doc or a commit.
- **`POST /api/jobs/{id}/result` requires the token, from everywhere, loopback included.** A result
  is the robot saying a physical action happened; an unauthenticated write would let anything
  claim that. Missing or wrong → `401 unauthorized` (checked before anything about the job is
  revealed). The server has no token configured → `503 edge_auth_unconfigured`: it fails closed.
- Reads (`GET /api/jobs/{id}`, `/api/state`, `/api/command`, `/api/events`) need no token. They
  carry the same poses `/api/state` already serves publicly. A token you send on a read is ignored.

### 1. `GET /api/jobs/{job_id}`: poll a job you were handed

You learn a job id from the SSE `job` event (`{"id", "state", "executable", …}`) or from
`POST /api/command`'s answer. The SSE stream is a **hint**: it replays up to 256 missed events
via `Last-Event-ID`, but **nothing across a server restart**. `GET /api/jobs/{id}` is the truth
and survives restarts (the ledger is files).

| field | meaning |
|---|---|
| `job_id`, `contract: "gitspace.job/1"` | see §5 below for how the id is made |
| `state` | `planned` → `running` → `succeeded` \| `failed` |
| `terminal` | `true` once `succeeded`/`failed`. Nothing changes a terminal job |
| `executable`, `why_not` | may an edge **start** it now: `false` when terminal, already claimed, no plan (revert, cherry-pick, resolve: roomctl plans those after it commits), nothing to move, or `head_moved` |
| `motion` | `"mock_only"` until Daniel sets `JOBS_REAL_MOTION=1`. While it says `mock_only`, run the mock backend only |
| `plan` | **`gitspace.plan/1`: the ordered op list you execute**, `seq` 1..n, never reordered (§4). `plan.routed: false` means `base` is null (no stance solved) |
| `ops` | web's git-level preview. **Do not execute** |
| `op_status` | per plan op: `{seq, object_id, kind, status, attempts, error}`, `status` ∈ `pending running success retryable failed skipped` |
| `progress` | `{ops_total, ops_done, ops_failed, ops_skipped, ops_pending, fraction}` |
| `result` | `null`, or the first terminal report: `{status, message, run_id, at, report}` |
| `claimed_by`, `executor` | your `run_id` once you've claimed it; `executor: "edge:<run_id>"` |
| `head`, `target`, `observed_room` | the three inputs the id is made from (with `command`) |
| `frame`, `units`, `frame_def` | §4 below |

`404 not_found` for an unknown id, `422 bad_request` for a malformed one.

### 2. `POST /api/jobs/{job_id}/result`: report what the robot DID

```json
{"run_id": "edge-7f3a",                    // yours; the SAME for every report of one run
 "status": "running" | "success" | "failed",
 "message": "mug placed",                  // optional
 "ops": [{"seq": 1, "object_id": "mug_a1b2", "status": "success", "attempts": 1, "error": null}],
 "frame": "world_z_up",                    // required if anything below carries a pose
 "observations": {"objects": [{"object_id": "mug_a1b2", "pose": {"x": 0.42, "y": 0.18, "z": 0.75, "yaw": 15}}]}}
```

- `running` is progress (an ack, or an op update). `success` and `failed` are terminal.
- `success` means **every** op in the plan reported `success`. Anything less is `failed`, and the
  ops say which. Honest partial ("I put back 2 of 3") is a `failed` job with two `success` ops.
- `ops[].status` uses your `ActionStatus` (`success`, `failed`, `retryable`) plus `pending`,
  `running` and `skipped`. A `seq` or `object_id` that isn't in the plan → `422`.
- Answers: `200` with the updated job (`replayed: false`), or see §5 for every refusal.
- **A report is a claim, recorded. It is not the truth about the room.** This server never
  writes room.git. After you report, our rescan decides what the room *is* (§4).

### 3. One real fixture: `docs/fixtures/restore-job.json`

This is the exact body `POST /api/command {"command": "restore", "args": {"ref": "study"}}` returned
against the real room.git: HEAD `1a668ec` → `study` (`b3691ea`). It has one `move` for the mug in
the plan, and two honest `unapplied` entries (the marker isn't in the room; there's no bin for the
scissors). **It is generated, never hand-written**: `scripts/make_job_fixture.py` regenerates it
through the server's own code path (git reads only). `web/tests/test_jobs.py` checks that it
is a job this server would make (the id recomputes, the frame is declared, the plan is ordered
1..n). Test your edge against these exact bytes.

### 4. The pose frame: declared on every response, never inferred

Every response that carries a pose (`/api/command` jobs and reads, `/api/jobs/{id}`,
`/api/state`, `/api/diff`, `/api/merge-preview`, `/api/cherry-pick-preview`) has, at the top level:

```json
"frame": "world_z_up",
"units": {"position": "m", "yaw": "deg", "duration": "s"}
```

`world_z_up`: X forward from the anchor tag, Y left, **Z up**, floor at z = 0, metres. `yaw` is
degrees; a plan's `pose.yaw` is the **axis** of `extents.x` in [0, 180) about +Z (perception
can't tell front from back), and `delta_yaw_deg` is a signed difference. `plan` repeats
`frame` and adds a `frame_def` sentence. **Please replace your hard-coded
`DANIEL_POSE_METADATA` (`protocol/daniel.py`) with a check that reads `frame` and refuses any
other value.** Bracket Bot is Y-down, and the conversion happens at **your** adapter (§5). Poses
you send back declare `"frame": "world_z_up"`; one that doesn't → `422 frame_mismatch`.

### 5. Stable job ids, idempotency and replay

```
job_id = "job_" + sha256("gitspace.job/1" \x1f command \x1f target_sha \x1f head_sha \x1f observed_room)[:16]
```

The same command against the same room is **the same job**, on any machine. `study`,
`study-mode` and `b3691ea` resolve to one commit, so they name one job. `observed_room` hashes
the room as last scanned (room.git's working tree). When it is clean, the id is effectively
command + ref + HEAD.

| what happens | what you get |
|---|---|
| the same `POST /api/command`, same room | the **stored** job with its current state, `200`, `replayed: true`. There is no second job and no second SSE event, and a finished job comes back finished |
| the room changed (new commit, or a rescan) | a **new** `job_id`. A new attempt always needs a new room: rescan first |
| your first report | **claims** the job for its `run_id` (only if `executable`) |
| a report from another `run_id` | `409 claimed`: a job runs once |
| starting a job whose HEAD has moved | `409 head_moved`: its plan is for a room that no longer exists. Ask again for a new job |
| starting a job with no plan | `409 not_executable` |
| the same terminal report again (HTTP retry) | `200`, `replayed: true`, nothing changes |
| a different terminal report | `409 result_conflict` with the stored result: **the first stands** |
| changing an op that already ended | `409 op_terminal` |

**The rule for a robot that lost its connection:** keep one `run_id` per run. After a drop or a
restart, `GET` the job. If `claimed_by` is yours and it is `running`, continue from the first
`pending` op. **An op you started but can't confirm finished is never run again.** Report it
`failed` (`"error": "interrupted"`) and let our rescan decide. Never start a job whose
`executable` is `false`, and never drive the real robot while `motion` is `mock_only`. There is
no cancel endpoint yet: to abandon a job, report `failed` with `"message": "cancelled"`.

Error codes on these routes: `bad_request` (422), `unauthorized` (401), `edge_auth_unconfigured`
(503), `not_found` (404), `claimed`, `head_moved`, `not_executable`, `result_conflict`,
`op_terminal` (all 409), `frame_mismatch` (422).

```bash
BASE=https://gitspace-five.vercel.app
curl -s $BASE/api/jobs/job_aba556d557b65301
curl -s -X POST $BASE/api/jobs/job_aba556d557b65301/result \
  -H "Authorization: Bearer $GITIRL_CLOUD_TOKEN" -H 'Content-Type: application/json' \
  -d '{"run_id":"edge-7f3a","status":"running","ops":[{"seq":1,"object_id":"mug_a1b2","status":"running","attempts":1}]}'
```

---

## 3. Camera frames: media does NOT pass through your process

Frames go **Pi → laptop directly** (docs/22-camera-sync.md). Your process receives **structured
state, never pixels**, so the camera scaffold stays deferred. The authoritative socket
contract, **verbatim** from `docs/16-api.md` §3b:

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

---

## 4. Where our executor STOPS and yours STARTS

```
US (cloud) ─ observe → diff → ORDERED OPS  │  YOU (edge) ─ ops → RobotAction → adapter
             dependency graph, staging,    │               → re-observe → verify → retry ≤ 2
             where-to-stand (base poses)   │
```

- **Ordering is ours.** It needs the voxel occupancy in Elasticsearch: you can't place a mug
  where a book still sits, cycles (A↔B) need a staging spot, and "where do I stand to reach
  this" samples a costmap built from those voxels (docs/24 A2). You don't own Elastic, so the
  collision graph can't live on your side.
- **Execution, verification and retry are yours.** They belong close to the robot, which is
  your README's own rule.
- `roomctl/executor.py` keeps the graph and stops at an ordered op list. The contract is
  **`gitspace.plan/1`**. Get one with `room restore <state> --plan-only --json` (also
  `reset --hard`, `checkout`). This is a real one, the plan for `restore study` after the
  afternoon's changes:

```json
{
  "contract": "gitspace.plan/1",
  "ref": "study",
  "target_sha": "39f7cd6d639f88fba64443dce93c031f4a031c18",
  "frame": "world_z_up",
  "frame_def": "X forward from the anchor tag, Y left, Z UP, floor z=0; metres. pose.yaw = the AXIS of extents.x in degrees [0,180) about +Z. base.yaw = heading, degrees about +Z. Bracket Bot is Y-DOWN: convert at the adapter (docs/20, ANDREW-HANDOFF.md §5).",
  "ops": [
    {
      "seq": 1, "kind": "remove", "object_id": "scissors_9f3a",
      "from": {"zone": "desk", "pose": {"x": 0.7, "y": -0.01, "z": 0.71, "yaw": 75}, "extents": {"x": 0.2, "y": 0.08, "z": 0.01}},
      "to":   {"zone": "bin", "pose": {"x": 0.3, "y": -0.75, "z": 0.45, "yaw": 0}, "extents": {"x": 0.2, "y": 0.08, "z": 0.01}},
      "base": {"pick": null, "place": null}
    },
    {
      "seq": 2, "kind": "move", "object_id": "mug_a1b2",
      "from": {"zone": "desk", "pose": {"x": 0.61, "y": 0.18, "z": 0.75, "yaw": 40}, "extents": {"x": 0.12, "y": 0.09, "z": 0.11}},
      "to":   {"zone": "desk", "pose": {"x": 0.42, "y": 0.18, "z": 0.75, "yaw": 15}, "extents": {"x": 0.12, "y": 0.09, "z": 0.11}},
      "base": {"pick": null, "place": null}
    }
  ],
  "unapplied": [
    {"object_id": "marker_c3d4", "reason": "cannot apply hunk: object 'marker_c3d4' not present in room"}
  ]
}
```

  - `kind`: `move` · `remove` (to the room's bin) · `stage` / `unstage` (a cycle broken by
    parking one object). **Run ops in `seq` order and never reorder them.**
  - Each op is one `MOVE_OBJECT` for you: `object_id`, `source` = `from`, `target` = `to`.
    `extents` is the object's box in metres. This is the only op shape you receive. The
    `ops` in the panel's response (docs/31 §3) are a git-level preview for the UI, not this.
  - `base.pick` / `base.place`: where the base stands, from our costmap. It is `null` when
    routing was skipped. We say WHERE, and Bracket Bot's nav says HOW (docs/24 A0).
  - `unapplied`: hunks we could not plan, such as an ADD of an object that isn't in the room, a
    spot blocked by something that isn't moving, or nowhere to stand. These are **structured
    conflicts, not guesses**, the same conservative default as your "only MOVED produces
    MOVE_OBJECT". Have the robot say so out loud: honest partial success ("I put back 2 of 3")
    is a demo beat.
- **When an op fails after its retries,** its object stays where it was. Skip every later op
  for that object, and every later op whose `to` box overlaps a stuck object's `from` box. Run
  the rest, and report the skipped ones as `skipped`. Stopping the whole plan at the first
  failure is also acceptable. Our reference implementation is `execute()` in
  `roomctl/executor.py`.
- **After you report,** we rescan, and `git status` tells the truth either way. Your per-op
  verification decides retries. Our rescan decides what the room *is*.

## 5. The frame: the trap that looks like an IK bug

**Our world frame is Z-UP**: X forward from the anchor tag, Y left, Z up, floor at z = 0,
metres (docs/20). It has to be, because the Elasticsearch voxel schema stores `cell {x, y}` on
the floor plane with `z_min / z_max` as height. The plan says so as `"frame": "world_z_up"`
(the token our side asserts) plus a `frame_def` sentence. **Bracket Bot's native frames are
Y-DOWN**, and they don't even agree with each other:

| | X | Y | Z |
|---|---|---|---|
| **ours (world)** | forward | left | **up** |
| BB camera (`example_depth.py`) | right | **down** | forward |
| BB odometry (`localization.py`, `/pose`, `/drive`) | forward | **down** | left |

**The conversion belongs at YOUR adapter boundary, written down in one place.** A world point
`(x, y, z)` becomes odometry-frame `(x, −z, y)`: X stays forward, "left" moves to Z, and "up"
becomes −Y. A silent Y/Z swap puts the arm **90° off** and presents as "IK can't solve", which
sends you to the wrong file. Anything you send back with a pose (`robot_observation`) is in
**our** frame and says `"frame": "world_z_up"`.

Units in the Pi API (docs/16): `/drive` and `/pose` yaw is in **radians**, and `/arm` pose yaw
is in **degrees**. Our plan's `pose.yaw` is an **axis** in degrees [0, 180), because
perception can't tell front from back. `base.yaw` is a heading in degrees. `to_drive_target` /
`to_arm_pose` in `roomctl/robot_client.py` show the exact mapping we use today.

## 6. Everything else you might touch

- Object record (git side): `roomctl/state.py`, with fields `id`, `class`, `zone`,
  `pose {x,y,z,yaw}`, `extents`, `color`, `first_seen`. Elasticsearch calls the id `object_id`.
- The panel endpoint and its response shape: `docs/31-agent-panel-contract.md`,
  `bridge/agent_api.py`.
- Robot HTTP/WebSocket API: `docs/16-api.md` §2–§3 (`/capture`, `/drive`, `/arm`, `/stream`).
- Sentry: robot spans are `robot.drive` / `robot.pick` / `robot.place`, tagged `robot=…`.
  Failures are tagged `failure_kind` + `capture_id`, the Pi's own capture id (docs/10 D25).
- Open items that touch you: docs/10 D15 (frames/units in docs/16) and D30 (job completions:
  the laptop's telemetry hub is the one `/stream` consumer).
