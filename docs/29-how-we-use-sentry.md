# 29 — How GITIRL uses Sentry

The reference for what is instrumented, where, and why. If you are answering a judge's
question, the answer is in here.

**Live as of now (24h):** errors 18 · logs 3,483 · transactions 2,028 · spans 7,292 ·
profiles 1,937 · replays 42 · attachments 13,249 · uptime 1 · crons 1.
**All six products in their required list are producing data.** Their brief asks for two.

---

## The shape of the system, and why it needs observability at all

GITIRL is **distributed across two machines, one of which can fall over.** A Raspberry Pi
balances on two wheels and drives an arm; a laptop does perception, git and planning. A single
`room revert` spans both, takes 90 seconds, and ends with a physical object in a new place.

When that goes wrong, the question is never *"which line threw?"* — it is **"which of the two
machines, at which of eleven pipeline stages, and was the robot even stable at the time?"**
That is the question Sentry answers here.

---

## One module: `obs.py`

Every process — Pi, laptop, AWS web tier — initialises through the same file. Nothing calls
`sentry_sdk.init` directly; `scripts/audit_architecture.py` fails the build if anything does.

```python
obs.init(role)              # "pi" | "laptop" | "web"
obs.capture_scope(id, sha)  # tags everything inside with the ids that join to Elasticsearch
obs.span(op, desc)          # a pipeline stage
obs.measure(**kv)           # chartable numbers, not filterable strings
obs.context(name, data)     # a structured table on the issue
obs.attach(name, bytes)     # a FILE on the event — the camera frame
obs.agent_tool() / agent_turn()   # MCP + LLM calls as gen_ai spans
obs.robot_failure(kind, …)  # a physical failure, with telemetry as breadcrumbs
obs.heartbeat(slug)         # cron check-in
```

---

## What each product does for us

### Tracing — a waterfall with physical motion in it
The laptop opens the trace and propagates `sentry-trace` on `POST /capture`; the Pi continues
it. One waterfall spans both machines. The spans are not all HTTP:

```
room revert HEAD ─────────────────────────────── 94.2 s
├─ esql: resolve ref → sha                          0.2 s
├─ es: collision check (shape query)                0.3 s
├─ execute op 1 · MOVE mug_a1b2                    28.4 s
│  ├─ drive to pick pose                            9.1 s
│  ├─ arm.pick                                     11.2 s   ← physical
│  └─ arm.place                                     3.1 s
└─ verify: rescan + git status                     12.1 s
```

`asyncio` is explicitly enabled — without it, tasks spawned off the event loop lose scope and
their spans detach, which reads as "tracing works except the async parts".

### Logs — structured, and tagged by camera
3,483 in 24h. Tagged `camera`, which is how a hardware fault was found in hardware rather than
chased through perception code.

### Profiling — names the function, not the stage
A span says *segmentation took 3.1 s*. A profile says *which function inside it*. 1,937
profiles across the perception pipeline.

### Session Replay — of the WebGL dashboard
Canvas recording on the 3D scene. See `SENTRY_STORY.md` §1: the first 42 replays were
**black rectangles at HTTP 200**, and segment size was the only symptom.

### AI agent monitoring — MCP on both sides
`gen_ai.chat` · `gen_ai.responses` · `gen_ai.mcp.tool` · `gen_ai.elastic.tool`. Retrieval
through Elastic Agent Builder, action through `bracketbot-mcp` — so one trace reads
`agent.tool_call → es.search → agent.decide → mcp.room_revert → arm.pick`.

### Uptime + crons — the room has a heartbeat
An uptime monitor on the web tier. A **cron monitor on the room being clean**: a room left
dirty stops checking in, and Sentry alerts that *the room failed its heartbeat*.

### Attachments — the camera frame at the moment of failure
A failed grasp carries **the actual frame the target pose was computed from**. You can see why
the arm reached into empty space. Nobody debugging a web app has a reason to put a photograph
on an error.

---

## The join: every span knows its documents, every document knows its trace

```
Sentry span ──carries capture_id──►  the exact documents in Elasticsearch
ES document ──carries trace_id────►  the exact waterfall in Sentry
```

45 of 46 object documents carry `sentry_trace_id`. This is the thing that made the
`grasp_slipped` diagnosis possible (`SENTRY_STORY.md` §4): the stack trace was empty, and the
answer was 40 telemetry samples joined to a separate database by one id.

---

## ★ Robot Session Replay — replaying motion from the logs

**Sentry replays browser sessions. We built the same thing for a robot.**

The data is already there and already complete:

| source | carries |
|---|---|
| `robot-telemetry` TSDS | 50 Hz × 8 signals — pitch, tilt_rate, encoders, motor current, odom residual |
| Sentry breadcrumbs | the last 40 samples before any failure |
| Sentry spans | `arm.pick` / `drive` with real start and end timestamps |
| `room-events` | commits, picks, places, failures, with `capture_id` |
| `room-voxels` | the room's geometry at that commit |

That is enough to **reconstruct the robot's motion and play it back**. Wheel encoders integrate
to a path; `pitch` gives the body's lean; span boundaries say when the arm moved and where to;
the voxel snapshot supplies the room it moved through.

```
GET /api/replay/<capture_id>   →  { path: [[t,x,y,yaw]…], pitch: [[t,rad]…],
                                    arm_spans: [{op,t0,t1,from,to}…],
                                    events: […], trace_url: "…" }
```

Rendered in Rerun (native timeline scrubbing) or as a 2-D scrub on the dashboard, beside the
Sentry trace of the same moment.

**Why this is worth building:**
- It is the **literal robot analogue of Sentry's flagship product**, which makes the idea
  explain itself in one sentence to the people who built that product.
- It is *"watch the flow that broke"* — their own framing for Session Replay — applied to a
  machine in a room.
- The scrubber and the trace waterfall are **the same event, in two views**. Drag the scrubber
  to the tilt spike and the trace shows `robot.capture` at that instant.
- It needs **no new instrumentation.** Every field above is already flowing.

The honest caveat: encoder integration drifts, so a long replay diverges from reality. Anchor
it on the capture poses we already store in `room-clouds`, and render the drift rather than
hiding it — a replay that shows its own uncertainty is more useful than one that pretends.

---

## Rules we hold to
1. `obs.py` is the only `sentry_sdk.init`. The architecture audit enforces it.
2. Every span carries `capture_id` and `commit_sha`; every ES document carries
   `sentry_trace_id`.
3. Numbers that matter are **measurements**, not tags — a tag finds a capture, a measurement
   shows a trend.
4. Attachments are **one per failure**, never per capture. 1 GB/month is a real budget.
5. `SENTRY_STORY.md` is written as things happen, by `scripts/sentry_watch.py`. Sunday-morning
   reconstruction is not a story.
