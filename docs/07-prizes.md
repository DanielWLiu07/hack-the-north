# 07 — Prize strategy

**Hard deadline: sponsor prize selection closes 2:00 PM EDT Saturday.** Decide by noon.

> **Revised again 2026-09-18:** the team has named its three targets — **Bracket Bot, Elastic
> and Sentry**. Sentry moves from a Tier C bolt-on into Tier A with a dedicated strategy
> ([`18-sentry.md`](18-sentry.md)); it needs ~3.5 h spread across the build, most of which pays
> for itself in debugging. Rox and Warp stay on the list because they cost almost nothing on
> top of work we're already doing — don't drop them, just don't steer for them.
>
> **Revised 2026-09-17:** Elastic moved from "cheap bolt-on" up to Tier A. Reason: the
> read-path argument in [`11-elastic.md`](11-elastic.md) — git genuinely cannot query, the
> agent needs a substrate, and the Elastic build shares most of its work with Rox while
> running on a track that never competes for robot time.

## The honest framing

Prize-chasing is the most common way good hackathon projects die. Every integration is
hours not spent on the demo, and judges can smell a bolted-on SDK. The rule:

> **Anything that doesn't make the demo better is a distraction unless it costs under an hour
> — or it's on a track that isn't competing for the critical path.**

That second clause is what changed. Perception, arm, and balance work are **hardware-
serialized**: they bottleneck on one physical robot, and a third person hovering behind it
adds nothing. Agent + Elastic is pure laptop work. With 4 people, that track is close to free.

Primary target remains **Bracket Bot**.

## Tier A — go all in

### Bracket Bot: Best Use of Bracket Bot Hardware — PRIMARY
*"We're looking for a sick demo where the robot is essential to the idea."*

Worth saying explicitly in the pitch: **the robot isn't using the system, the robot IS the
system's write path.** Remove it and `commit`/`status`/`diff` still work but nothing can
ever change — it becomes a read-only repo. Cleanest possible answer to "is the robot
essential?"
Prizes: 1st = 4× Bambu A1 Mini + 4× SO-101 leader-follower kits.

### Warp: Best Developer Tool
Strong and under-contested. We're literally extending `git` — the developer tool — into a
new domain, CLI-first. Their brief pre-empts our only weakness: *"many developer tools may
not have traditional GUIs — there's no need to add a GUI for the sake of adding one."*
**Cost to qualify: zero. No API requirement.** Take it.

### Rox: Best AI Agent — $10K / $2K
*"agents that handle unstructured information, incomplete datasets, conflicting sources, or
noisy data... multi-source resolution, intelligent error handling, robust decision-making
under uncertainty."*

An unusually exact description of [`03-perception.md`](03-perception.md). Object association
under noise, occlusion-vs-deletion ambiguity, three cameras that disagree, conflict
resolution. **To qualify honestly** the agent must do real work — resolving ambiguous
associations, deciding occluded vs. gone, ordering operations, negotiating merge conflicts —
not writing commit messages. Make that its actual job. Highest cash value on the board.

### Elastic: Find the Signal — Best Use of Elasticsearch
*"genuinely messy, unstructured, real-world data (... sensor streams ...)"*, *"Elasticsearch
as their context layer"*, *"beyond RAG with a chatbot"*, *"Workflows that close the loop by
taking action, not just answering questions."*

Full design in [`11-elastic.md`](11-elastic.md). The one-sentence pitch:

> **An agent whose last tool call moves a real object in the real world.**
> (`agent/tools.py`: `search_objects` over Elasticsearch → `room_revert` → the robot.)

Nobody else's Elastic entry will have a physical actuator at the end of the chain. And the
"git stores the clean truth, Elastic stores the mess" split means the messy-data story is
architecturally true rather than asserted. **~3 h marginal cost** on top of the Rox agent.
2 winners; most entries will be the RAG chatbot their brief explicitly rules out.

### Sentry: Best Use of Sentry
*"Show us how observability **actually shaped what you built**... judged on creativity, depth
of integration, and how meaningfully Sentry data influenced your project, **not just whether
the SDK is installed**."*

Strategy in [`18-sentry.md`](18-sentry.md). Three things make this winnable rather than
merely qualifying:

1. **We're genuinely distributed** — a balancing Pi and a laptop — so one trace spanning both
   machines is honest, not contrived.
2. **Our traces contain a robot arm moving.** A waterfall where `arm.pick` is an 11-second
   span is something no other entry will have.
3. **`SENTRY_STORY.md`, written as it happens.** Their brief asks for the bug you squashed at
   4am. You cannot reconstruct that on Sunday — capture it live, with screenshots.

Six products are honestly available (Tracing, Logs, Profiling, AI agent monitoring, Session
Replay via `web/`, Uptime via the AWS tier); they ask for two beyond errors.

### Hack the North Finalist (the main award)
*"creative and surprise us... playful, experimental, wonderfully weird, or useful... doesn't
need to become a real startup."* Could have been written for this project. Pitch the
weirdness, not a business model.

## Tier B — worth real effort

### OpenAI: API Prizes
`examples/example_realtime.py` (Realtime over WebRTC) already ships in the BB quickstart, so
voice control is glue, not a build. GPT-5 vision does cluster labeling and the agent
reasoning above.

## Tier C — cheap bolt-ons, only if ahead of schedule

| prize | what we'd do | cost | verdict |
|---|---|---|---|
| **Baseten** | host SAM 3 / VLM / depth inference off the Pi | ~1 h | architecturally correct anyway — do it |
| **MLH ElevenLabs** | robot narration voice | ~30 min | do it, it improves the demo |
| **MLH Gemini** | alternative VLM | ~1 h | conflicts with the OpenAI story; probably skip |

## Tier D — do not chase

Shopify, RBC, QNX, Dryft, Tether/QVAC, Intact, Dominion Dynamics WHITEOUT, Expo, CSE,
GPTZero, Huawei, Backboard, Solana/TigerData/Vultr/Snowflake/MongoDB/GoDaddy.
Each requires bending the project away from its center. **Dryft and Dominion Dynamics in
particular are full-weekend challenges in their own right** — alternative projects, not add-ons.

## Recommended commitment

**Steer for three: Bracket Bot, Elastic, Sentry.**
**Also submit to: Warp, Rox, OpenAI** — they cost nearly nothing on top of what we're already
building (Warp requires no API at all; Rox shares its entire agent build with Elastic; OpenAI's
Realtime example already ships in the BB quickstart). Free entries with real odds; just don't
let them change a decision.

Add **Baseten** when you offload SAM 3 / the VLM — which [`19-deployment.md`](19-deployment.md)
says to do anyway instead of an EC2 GPU. ElevenLabs if there's a spare half hour.

One coherent story, told the same way every time: *a robot that reads and writes physical
state through real git, with Elasticsearch as its memory and Sentry as its nervous system.*

## The guardrails on Elastic

Both are load-bearing; see [`11-elastic.md`](11-elastic.md#the-two-conditions).

1. **One person owns agent + Elastic from H+0 and never touches the robot.** With 2 people
   instead of 4, cut this track — it stops being free.
2. **It must never become a chatbot about the room.** If a demo beat ends with text on a
   screen rather than the robot moving or pointing, cut the beat.
3. **Kill criterion H+24:** if the core `status`/`diff`/`revert` loop isn't solid, the
   Elastic track stops and that person moves to demo polish and rehearsal. Bracket Bot +
   finalist are worth more than a second sponsor prize, and a shaky core loses both.
