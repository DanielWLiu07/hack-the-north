# GITSPACE — version control for the room you're standing in

> Working name. Alternates in [`docs/01-concept.md`](docs/01-concept.md#naming).
> **Hack the North 2026** · Sep 18–20 · primary target: **Bracket Bot — Best Use of Bracket Bot Hardware**

**One line:** we put a room under version control, and the robot is `git apply` for physical reality.

**The loop:**

```
world  --3x stereo cams-->  point cloud  -->  object scene graph  -->  YAML files  -->  real git repo
  ^                                                                                         |
  '----  arm picks & places  <--  motion plan  <--  object-level diff  <-----  git diff  <---'
```

`git commit` snapshots the room. `git status` tells you what's been moved since.
`git diff` prints real, literal git diff output of the physical world. `git revert HEAD`
makes the robot drive over and **put the objects back**. `git merge` can produce a
physical merge conflict.

Everything upstream of the robot is *actual git* — not a metaphor, not a re-implementation.
That's the design bet: see [`docs/04-git-semantics.md`](docs/04-git-semantics.md).

Git is the **write path and source of truth**. **Elasticsearch is the read path** — git
can't answer *"where did I leave my keys?"* or *"put the desk back the way it was before
dinner"*, and it keeps no memory of the messy sensor data we throw away at commit time.
See [`docs/11-elastic.md`](docs/11-elastic.md).

---

## Status

**Thinking / planning only.** No code, no `git init` here yet, no GitHub — by request.
(Yes, we are aware this repo about git is not a git repo. It will be.)

## Plan

| file | what's in it |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | the system diagram, module map, four parallel tracks, frozen interfaces |
| [`BUILD_ORDER.md`](BUILD_ORDER.md) | branch plan, the six gates, what to collect at which booth |
| [`TEAM.md`](TEAM.md) | **who owns what** — Sarah/Ryan robot, Daniel data+web, Andrew ML/agent |
| [`KEYS.md`](KEYS.md) · [`.env.example`](.env.example) | **every credential to fill in**, what's needed tonight vs at a booth |

Each folder — `robot/` `perception/` `elastic/` `web/` `roomctl/` `agent/` `viz/` `fake/`
`tests/` `scripts/` — carries its own `README.md` listing every file, its build order, and
acceptance criteria. One folder ≈ one branch ≈ one owner.

## Docs

| file | what's in it |
|---|---|
| [`01-concept.md`](docs/01-concept.md) | the product idea, why it's not a gimmick, scope tiers, naming |
| [`02-hardware.md`](docs/02-hardware.md) | what Bracket Bot actually is (from their source), camera rig, arm, BOM |
| [`03-perception.md`](docs/03-perception.md) | 3-cam fusion → point cloud → objects → scene graph → diff |
| [`04-git-semantics.md`](docs/04-git-semantics.md) | real-git-as-database; what every git verb means physically |
| [`05-architecture.md`](docs/05-architecture.md) | processes, network, latency budget, code layout |
| [`06-demo.md`](docs/06-demo.md) | the 3-minute live demo, beat by beat, with fallbacks |
| [`07-prizes.md`](docs/07-prizes.md) | honest prize-stacking assessment, cost per prize |
| [`08-risks.md`](docs/08-risks.md) | what will break, mitigations, kill criteria |
| [`09-schedule.md`](docs/09-schedule.md) | 36h plan + what to do **tonight** before the event |
| [`10-open-questions.md`](docs/10-open-questions.md) | decisions we owe ourselves; questions for the BB booth |
| [`11-elastic.md`](docs/11-elastic.md) | Elasticsearch as the **read path**: why git can't query, index design, agent tools |
| [`12-elastic-primer.md`](docs/12-elastic-primer.md) | **Elasticsearch from zero** — every term explained, spatial + time-series, how to win the track |
| [`13-ingest.md`](docs/13-ingest.md) | how documents actually get indexed: mappings, `_bulk`, TSDS templates, the five gotchas |
| [`14-elastic-flow.md`](docs/14-elastic-flow.md) | **the full flow** — every ES operation in causal order, setup → ingest → query → act |
| [`15-segmentation.md`](docs/15-segmentation.md) | separating objects in the cloud: mask-first vs geometric, plane removal, failure modes |
| [`16-api.md`](docs/16-api.md) | **the contract** — six endpoints, the WebSocket protocol, webhooks, Sentry, ports |
| [`17-messaging-and-aws.md`](docs/17-messaging-and-aws.md) | text your room: Telegram/SNS, S3 for clouds, SQS instead of a tunnel |
| [`18-sentry.md`](docs/18-sentry.md) | **winning Sentry** — tracing a physical action, `SENTRY_STORY.md`, six products |
| [`19-deployment.md`](docs/19-deployment.md) | **what runs where** — AWS web tier, laptop stays the brain, and why |
| [`20-perception-logic.md`](docs/20-perception-logic.md) | **frames, units, stage contracts** — the three convention facts, association logic, quantization |
| [`21-3d-and-meshy.md`](docs/21-3d-and-meshy.md) | 3D assets — why Meshy can't rig an arm, use the real SO-101 CAD, prop prompting, the 100× trap |
| [`22-camera-sync.md`](docs/22-camera-sync.md) | **3-camera sync** — grab/retrieve, one clock on the Pi, the quality gate, fixed exposure |
| [`23-telemetry.md`](docs/23-telemetry.md) | **the telemetry system** — one producer, four sinks, the ring buffer, downsampling |
| [`30-andrew-agent-boundary.md`](docs/30-andrew-agent-boundary.md) | **compatibility with `gitirl-agent`** — the executor overlap, the contracts he's waiting on |
| [`29-how-we-use-sentry.md`](docs/29-how-we-use-sentry.md) | **the Sentry reference** — every product, the join, and Robot Session Replay |
| [`28-sentry-fun.md`](docs/28-sentry-fun.md) | **the fun angle** — a robot that files its own bugs and closes them by tidying up |
| [`27-realsense-integration.md`](docs/27-realsense-integration.md) | **Sarah's RealSense collector** — what it supersedes, the unit traps, who owns what |
| [`26-seer-embodied.md`](docs/26-seer-embodied.md) | Seer as an entity in the scene + a telemetry-panel control surface |
| [`25-identity-and-staging.md`](docs/25-identity-and-staging.md) | **what identifies a thing in a point cloud**, and what `git add` means physically |
| [`24-traversal-and-graph.md`](docs/24-traversal-and-graph.md) | **room traversal** (base-pose solving, viewpoint planning) + **the git graph as a control surface** |
| [`web/PAGES.md`](web/PAGES.md) | which pages the site needs, and which one wins both prizes |
| [`diagrams.html`](docs/diagrams.html) | ten diagrams of the Elastic layer |
| [`research-notes.md`](docs/research-notes.md) | prior art, papers, links, what's already solved |

## Read first

If you read three things: [`01-concept.md`](docs/01-concept.md) →
[`06-demo.md`](docs/06-demo.md) → [`09-schedule.md`](docs/09-schedule.md).
The demo document is the spec. Build backwards from it.
