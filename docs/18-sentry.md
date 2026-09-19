# 18 — Winning the Sentry track

## ⚠ FIRST: claim the education plan — it unlocks tracing

From Sentry's own Hack the North starter pack (`~/Downloads/…Sentry-Hack-the-North-Starter-Pack.pdf`):

> **Free for a year, on your .edu email.**
> 50K errors/mo · **5 GB logs/mo** · **5M spans/mo** · **500 replays/mo**
> $20 Seer AI credits/mo · 1 GB attachments · 1 cron monitor · ∞ team members

**5M spans/month is tracing, included.** That answers the open question about whether the
Performance tab is plan-gated — on the education plan it is not.

**How to claim, in their stated order:**
1. **Activate the GitHub Student Pack first.** Their wording: *"Your .edu email makes you
   eligible. Activate that first and your free Sentry education account unlocks automatically."*
2. Sign up / sign in at **sentry.io/for/education** with the **@uwaterloo.ca** address.
3. Then add Sentry — five lines.
4. Submit on the HTN Devpost and **select the Sentry track**, naming the products used and
   what you found with them.

> **Our current org `na-alh` was created with a personal email.** Either upgrade it or create
> the org under the .edu account and re-point the DSNs. Do this before building further on it —
> re-pointing later means reissuing DSNs on three machines.

Support: **university@sentry.io** (they answer architecture questions during the event) ·
`discord.gg/sentry` · interactive sandbox at `sandbox.sentry.io`.

**The prize:** every member of the winning team gets a **guaranteed interview** with the Sentry
team for internship or new grad. Their words: *"Not a raffle, not a swag bag."*

---

## The three axes they actually judge on

Their sheet names them explicitly. Write the submission against these, in these words:

| axis | their wording | our answer |
|---|---|---|
| **Creativity** | *"What you built, and whether Sentry let you build something you **couldn't have**."* | A distributed trace whose spans are **physical robot motion**. Without it, a 94-second `revert` is a black box — you cannot see that the 11 seconds was `arm.pick` and not planning. |
| **Depth of integration** | *"How far past the default setup you went: custom spans, tags, context, **instrumented agent calls**."* | `obs.py`: custom spans per perception stage, `capture_id`/`commit_sha`/`camera` tags joining to Elasticsearch, telemetry breadcrumbs on robot failures, and AI agent monitoring on the LLM tool-calling loop. |
| **Influence on the project** | *"How meaningfully Sentry data shaped your decisions **during** the build, not after it."* | `SENTRY_STORY.md`, written live. The capture quality gate exists **because** the trace showed `tilt_rate_max` at the shutter. |

> *"We are **not** judging on whether the SDK is installed. Tell us the debugging story, the
> 4am one. **That's the submission.**"*

---

## All six products — pick at least two, we can honestly use all six

Their sheet, with their own one-line framing:

| product | their framing | our honest use |
|---|---|---|
| **tracing** | *See the slow endpoint* | one waterfall across Pi + laptop, with `arm.pick` as a span |
| **logs** | *Find out where to look* | structured, tagged `camera` — found a USB brownout, not a perception bug |
| **profiling** | *Find the hot code path* | SGBM vs segmentation: names the **function**, not just the stage |
| **session_replay** | *Watch the flow that broke* | the GITRL dashboard — watch a judge use the search panel, fix the confusing bit before the next one arrives |
| **uptime_monitoring** | *Know before the judges do* | the AWS web tier's public URL. 30 seconds to configure |
| **ai_agent_monitoring** | *Trace your **MCP** & LLM calls* | **we are an MCP project** — `bracketbot-mcp` for actions, Elastic Agent Builder tools for retrieval. Instrument the agent loop and every tool call is a span |

**`ai_agent_monitoring` is the one to prioritise after tracing.** Their framing names MCP
specifically, and we have MCP on *both* sides of the agent — retrieval and actuation. A trace
showing `agent.tool_call → es.search → agent.decide → mcp.room_revert → arm.pick` is depth of
integration *and* creativity in one screenshot.

*As built (2026-09-19), with Sentry's own op names:* `gen_ai.invoke_agent` → `agent.decide` (the
SDK's `gen_ai.responses`) → `gen_ai.execute_tool search_objects` [tool.type `elastic`] →
`es.search` → `agent.decide` → `gen_ai.execute_tool room_revert` [tool.type `roomctl`] →
`robot.pick arm.pick mug_a1b2` → `robot.place` → `agent.decide`. The screenshot trace:
https://na-alh.sentry.io/performance/trace/6932ef63090640a8866f9c299986e819/ (real gpt-5, real ES,
real git revert + executor; **mock arm**, tagged `robot=mock`). `room_revert` is typed `roomctl`,
not `mcp`: it calls roomctl in-process, and no MCP transport exists yet.

Also on the plan: **$20/mo of Seer AI credits** (Sentry's debugging agent) and **1 cron
monitor** — point the cron monitor at the watch-loop heartbeat, so a dead perception loop
pages us instead of silently producing stale commits.

---

## Read their brief carefully — it is not about integration depth

> *"Show us how observability **actually shaped what you built**: the slow endpoint you found
> in a trace, the broken flow you caught in a replay, the bug you squashed at 4am because your
> logs told you where to look. We'll judge on creativity, depth of integration, and **how
> meaningfully Sentry data influenced your project, not just whether the SDK is installed.**"*

Most teams will install the SDK, catch an error, and say "we used Sentry." That satisfies
nothing in that paragraph. **They are asking for a story with evidence.** Everything below is
in service of having one.

Requirement: **at least two products beyond error monitoring.** We can honestly use five.

---

## 1. The single highest-value action: write the story as it happens

Create `SENTRY_STORY.md` on day one and append to it **every time observability tells you
something you didn't know.** Screenshot the trace or the log at that moment.

```markdown
## h14 — the capture pipeline was 3× slower than it needed to be
The distributed trace showed `room status` at 8.9 s, and SGBM was 2.8 s of it — we'd
assumed segmentation was the bottleneck. Dropped disparity to half-res and re-upsampled:
2.8 s → 0.9 s. Total capture 8.9 s → 6.4 s.  [screenshot: trace-h14.png]

## h19 — phantom diffs traced to a specific camera
Logs tagged `camera=cam2` showed point_count collapsing every ~40 captures. It was the
USB hub browning out under arm current draw, not a perception bug. We'd have spent hours
in the wrong file.  [screenshot: logs-h19.png]
```

**You will not reconstruct these on Sunday morning.** The team that writes them down as they
happen has a submission; the team that doesn't has an SDK install. Same instruction as the
OpenAI Codex anecdote — capture it while it's fresh.

---

## 2. The five products, and why each is honest here

| product | our use | is it real? |
|---|---|---|
| **Tracing** | distributed, Pi → laptop → Elasticsearch, one waterfall per `room status` | **yes — the core** |
| **Logs** | structured, tagged `capture_id`/`camera`/`commit_sha` | yes |
| **Profiling** | the perception pipeline is CPU-bound; profiling shows *which function* | yes |
| **AI agent monitoring** | we have an LLM agent making tool calls | yes |
| **Session Replay** | we have a browser dashboard (`web/`) | yes, now that `web/` exists |
| **Uptime Monitoring** | the AWS web tier has a public URL to watch | yes, once deployed |

That's six available. Use **Tracing + Logs** as the backbone, add **Profiling** (nearly free
once tracing is in), and pick up **Session Replay** and **Uptime** because `web/` and the AWS
instance make them genuine rather than decorative.

---

## 3. The creative angle nobody else will have: trace a physical action

Everyone's traces are HTTP spans. **Ours contain a robot arm moving through space.**

```
room revert HEAD ──────────────────────────────────────────────── 94.2 s
├─ esql: resolve ref → sha              ──                          0.2 s
├─ git diff → 3 ops                     ─                           0.1 s
├─ es: collision check (shape query)    ──                          0.3 s
├─ plan: topological sort               ─                           0.0 s
├─ execute op 1 · MOVE mug_a1b2         ─────────────────          28.4 s
│  ├─ drive to pick pose                ──────                      9.1 s
│  ├─ arm.pick                          ───────                    11.2 s   ← physical
│  ├─ transport                         ────                        5.0 s
│  └─ arm.place                         ──                          3.1 s
├─ execute op 2 · MOVE book_e5f6        ──────────────             24.8 s
├─ execute op 3 · ADD scissors_9f3a     ✗ unreachable_pose          1.4 s   ← span errored
└─ verify: rescan + git status          ────────                   12.1 s
```

A waterfall where `arm.pick` is a span is memorable, it is *correct* use of tracing, and it is
immediately legible to a Sentry engineer. **Lead the pitch with this screenshot.**

Implementation: wrap each executor operation in a span, and have the Pi report job-state
transitions as child spans via the WebSocket `job` messages.

*As built:* the timings above are illustrative. Both of roomctl's robots emit one span vocabulary
themselves: `robot.drive` / `robot.pick "arm.pick <id>"` / `robot.place "arm.place <id>"`, tagged
`robot=mock` (`roomctl.executor.MockRobot`) or `robot=pi` (`roomctl.robot_client.HttpRobot`, which
also files a failed motion as `obs.robot_failure`).

---

## 4. Cross-system correlation — tag everything

Tag every span, log and event with the same three keys:

```python
scope.set_tag("capture_id", capture_id)
scope.set_tag("commit_sha", commit_sha)
scope.set_tag("camera", cam_id)          # where applicable
scope.set_tag("branch", branch)
```

Now a slow span in Sentry points at **a specific document in Elasticsearch and a specific
commit in git**. Demonstrating that jump live — "this trace was slow; here's the exact capture
in Elasticsearch; here's the commit it produced; here's why the diff was wrong" — is depth of
integration that no checkbox conveys.

This is also where **Sentry and Elastic reinforce each other** rather than competing for
attention: Sentry explains *why the system was slow or broken*, Elasticsearch explains *what
the robot saw*. Same `capture_id` joins them.

---

## 5. Robot failures as Sentry issues

Novel, genuinely useful, and slightly funny — which is exactly the register this event rewards.

```python
try:
    arm.pick(pose)
except GraspSlipped as e:
    sentry_sdk.capture_exception(e)      # attaches telemetry as context
```

*As built:* nothing outside `obs.py` calls `sentry_sdk` (an audit invariant). It is
`obs.robot_failure(kind, detail, telemetry=None, frame=None, **tags)`: one `error`-level
message `robot: <kind> — <detail>` on a **forked** scope (so fall #2 never carries fall #1's
breadcrumbs or photo), the last 40 telemetry samples as breadcrumbs, `failure_kind` + your tags,
and `frame` (JPEG bytes or a BGR array) as ONE attachment, 480 px q70 via `small_jpeg`, within the
attachment budget below.

Report as Sentry issues:
- **failed grasp** — with gripper closure, target pose, object class
- **robot fell over** — with 2 s of pre-fall telemetry attached
- **unreachable IK pose** — with the target and the arm's reach envelope
- **camera dropout** — with per-camera point counts
- **phantom diff detected** — when `test_idempotent_scan` fails in the field

Attach the last 2 seconds of `robot-telemetry` as breadcrumbs. *"The robot fell over"* arriving
as a Sentry issue with a tilt graph attached is the kind of thing a judge remembers and
repeats to a colleague.

Wire it to the LED too: any Sentry-reported error flips the strip red. **Observability with a
physical indicator** — a small thing that reads as thoughtful.

---

## 6. AI agent monitoring

Sentry monitors LLM agent tool-calling. We have an agent that chooses between retrieval tools
and action tools, and its failure modes are real: calling the wrong tool, looping, deciding an
object was deleted when it was occluded.

Instrument `agent/loop.py` so each tool call is a span with input/output. Then a bad decision
becomes **traceable** rather than mysterious — which matters at 4am, and it's a use of Sentry
that most hackathon projects have no occasion for.

*As built:* `agent/loop.py` opens each turn as a `gen_ai.invoke_agent` transaction
(`gen_ai.agent.name = gitspace`); every model decision is an `agent.decide` span containing the
SDK's own `gen_ai.responses`; every tool call in `agent/tools.py` runs inside `obs.agent_tool` and
sets `gen_ai.tool.call.result`. Only allow-listed tool names are dispatched.

---

## 7. Session Replay + Uptime, courtesy of `web/` and AWS

- **Session Replay** on the dashboard. Genuine value during judging: watch how someone who has
  never seen it uses the search panel, and fix the confusing bit before the next judge arrives.
  That's observability shaping the product, live, which is precisely their brief.
- **Uptime Monitoring** on the AWS instance's public URL. Thirty seconds to configure, and it
  is a real thing to watch now that [the web tier is deployed](19-deployment.md).

---

## 8. Build order

| when | what | cost |
|---|---|---|
| **h2** | `sentry_sdk.init()` on Pi + laptop; propagate `sentry-trace` on `POST /capture` | 30 min |
| **h3** | tag `capture_id`/`commit_sha`/`camera` everywhere; start `SENTRY_STORY.md` | 20 min |
| h6 | spans around each perception stage — this is when it starts paying you back | 30 min |
| h10 | enable profiling | 10 min |
| h15 | robot failures as issues, with telemetry breadcrumbs | 40 min |
| h20 | spans around executor ops — **the physical-action waterfall** | 30 min |
| h24 | agent tool-call monitoring | 30 min |
| h26 | Session Replay on `web/`, Uptime on the AWS URL | 20 min |

**Total ~3.5 h, spread out — and most of it pays for itself in debugging time.** Do the first
two rows at hour 2; everything else is additive.

---

## 9. What to say when judging

> *"We're a distributed system — a Raspberry Pi balancing on two wheels and a laptop doing
> perception — so our hardest bugs span two machines. One trace covers both: the laptop opens
> it, the Pi continues it, and the waterfall includes the arm physically moving as a span.*
>
> *Three things Sentry actually changed: the trace showed SGBM was 2.8 of our 9 seconds, not
> segmentation like we assumed — that's a 3× capture speedup. Logs tagged by camera found a USB
> brownout we'd have spent hours chasing in the wrong file. And failed grasps arrive as issues
> with the robot's tilt telemetry attached, which is how we found that arm acceleration was
> destabilising the balance controller.*
>
> *Every span carries the capture_id, so a slow trace points straight at the Elasticsearch
> document and the git commit it produced."*

Then show the waterfall with `arm.pick` in it.

---

## Coverage audit — 2026-09-18

**4 of 6 products live** (tracing 1,407 events · profiling 1,424 · replay 34, verified
non-blank · logs wired). Their brief requires two. So the requirement is met and the
remaining work is on the **scored** axes, not the checklist.

### What was missing, and what each one buys

| added to `obs.py` | axis it serves | why it matters here |
|---|---|---|
| `measure(**kv)` | **Influence** | a tag answers *"show me that capture"*; a **measurement** answers *"is the robot getting less stable over the weekend, and did that correlate with the failed grasps?"* — a question only observability can answer |
| `context(name, data)` | Depth | the capture-quality block as one table on the issue, not a dozen flattened tags |
| `attach(file, bytes)` | **Creativity** | **1 GB/mo unused.** A failed grasp arrives with the actual camera frame the target pose was computed from — you can *see* why the arm reached into empty space |
| `agent_tool()` / `agent_turn()` | **Depth** — the named product | `ai_agent_monitoring`. Their framing is literally *"Trace your MCP & LLM calls"* |
| `heartbeat()` | Creativity | 1 cron monitor on the plan. A watch loop that dies silently keeps serving **stale** commits — worse than crashing, because the room looks clean when nothing is looking at it |

### The attachment idea is the one to lead with

Nobody debugging a web app has a reason to put a **photograph** on an error. A robot does.
`grasp_slipped` with the frame attached is not a log line, it is evidence — and it is exactly
*"whether Sentry let you build something you couldn't have."*

Keep frames small (JPEG q70, downscaled). One per failure is fine; one per capture is not.

### Still open
- ~~**uptime_monitoring**~~ — **live 2026-09-19**: monitor #10384065 on the laptop via a Cloudflare
  quick tunnel (`scripts/uptime_tunnel.sh`) until the AWS tier exists; `scripts/deploy_web.sh`
  re-points the same monitor. First check: 200 in 535 ms from US East. **6 of 6 products live.**
- ~~Wire `measure()` into `capture_quality()`~~ — **done**: the numbers land as typed attributes on
  the transaction (measure) and on the gate span (set_data); a missing value fails the gate.
- ~~Wire `agent_tool()` around every tool call in the agent loop~~ — **done 2026-09-19**:
  `agent/tools.py`, one real turn = one waterfall (§ "All six products" above has the trace).

---

## `obs.py` as built — the API every other session calls (reconciled 2026-09-19)

Checked line by line against `obs.py`. Nothing outside it calls `sentry_sdk` directly.
`_HAVE` = sentry_sdk importable; without it every function below is a no-op returning None,
`{}`, `False` or `True` (capture_quality still computes its verdict).

| call | what it actually does |
|---|---|
| `init(role)` | **No-op (returns False) unless `SENTRY_DSN` starts with `http`**: absent, blank, parked (`KEY=# …`) or garbage never raises (sentry_sdk would raise `BadDsn`, which would take the importing process down). Otherwise: `AsyncioIntegration` (so spans in tasks don't detach), `enable_logs`, `environment`/`release`/`traces_sample_rate`/`profiles_sample_rate` from `SENTRY_*` env (defaults htn2026, gitspace@0.1.0, 1.0, 1.0), `server_name=role`, `send_default_pii=False`, tag `role`. sentry_sdk's default integrations stay on, **including OpenAI**: every Responses/Chat call is already a `gen_ai.*` span with tokens. |
| `trace_fields()` | `{sentry_trace_id, sentry_span_id[, sentry_url]}` of the current span, for every ES document. The URL needs `SENTRY_ORG_SLUG` **at import**. Note: spans carry trace ids even with no DSN, so documents written while Sentry is parked still get ids of traces that were never sent (docs/10, obs owner's call). |
| `capture_scope(capture_id, commit_sha="", branch="main")` | Forks a scope and tags it; **also** stamps the same tags (as tag + span data) on the current span AND its transaction, and, via a contextvar, on every `obs.span`/`obs.agent_tool` opened inside (docs/10 D33: before this, capture_id reached no span). Yields `trace_fields()`. |
| `span(op, desc="", **data)` | `start_span(op, name=desc or op)` + your data + the capture_scope tags + `duration_ms`. |
| `transaction(op, name)` | `start_transaction`; a null context without the SDK. |
| `robot_failure(kind, detail, telemetry=None, frame=None, **tags)` | An **issue** (`capture_message`, level error) on a forked scope: last 40 telemetry breadcrumbs, `failure_kind` + tags, and `frame` as one ≤480 px q70 JPEG attachment if the budget allows (else tag `attachment_skipped=<why>`, issue still sent). |
| `capture_quality(skew_ms, tilt_rate_max, coverage)` | docs/22's gate: ok iff skew < 25 ms, tilt_rate_max < 0.05, coverage > 0.60; **a None value fails** (missing evidence never passes). Values as span data and `measure()`; a rejection tags `capture_rejected=true` on the current scope, span and transaction. |
| `measure(**kv)` | `set_measurement` on the current transaction (numbers Sentry charts). |
| `context(name, data)` | A table on the **current** scope (not process-wide). |
| `attach(filename, data, content_type)` | **Refused outside `capture_scope`** (a scope re-sends its attachments with every later event). Budget, laptop-wide via a locked ledger `~/.cache/gitspace/attach-ledger.json` (`OBS_ATTACH_LEDGER`): ≤20 per rolling hour, ≤1 MB/hour, ≤100 KB each (`OBS_MAX_ATTACHMENTS_PER_HOUR`, `OBS_MAX_ATTACHMENT_BYTES_PER_HOUR`, `OBS_MAX_ATTACHMENT_BYTES`). Returns whether it attached. |
| `small_jpeg(frame, max_side=480, quality=70)` | JPEG bytes or BGR array → downscaled JPEG, or None. |
| `agent_tool(name, kind="mcp", **args)` | op **`gen_ai.execute_tool`**, name `execute_tool <name>`, `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`, `gen_ai.tool.type=<kind>`, `gen_ai.tool.call.arguments` (JSON, ≤2000 chars) — sentry_sdk's own op/attribute names, so the AI Agents view counts them (docs/10 D31). Status `internal_error` if the body raises. **The caller sets `gen_ai.tool.call.result`.** |
| `agent_turn(prompt, model="gpt-5", sdk_visible=None)` | For LLM calls the SDK **can't** see (the Realtime/WebRTC voice path). While the OpenAI integration is active it is a plain `agent.turn` span (wrapping an SDK-traced call double-counted live: 35 calls → 70 spans, 13,020 → 26,040 tokens). `sdk_visible=False` forces a real `gen_ai.chat` span (`gen_ai.operation.name=chat`, `gen_ai.system=openai`, `gen_ai.request.model`); the caller then sets `gen_ai.response.model` and `gen_ai.usage.*`. |
| `heartbeat(slug="watch-loop", status="ok", duration=None, monitor_config=None)` | A cron check-in; with `monitor_config` the monitor upserts itself. |
| `flush(timeout=5.0)` | `sentry_sdk.flush`. |
