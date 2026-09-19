# Team and ownership

Four people, five tracks, chosen so nobody edits anyone else's files.

| person | owns | why |
|---|---|---|
| **Sarah + Ryan** | `robot/` + `perception/{depth,calib,fuse}` | hardware. Two people because [the arm is the biggest single risk](docs/08-risks.md#r2--arm-motion-destabilizes-the-balancing-robot) and deserves a dedicated owner |
| **Daniel** | `elastic/` + `web/` + `roomctl/` + `viz/` | the data and browser layer — unblocked at hour zero via `fake/` |
| **Andrew** | `agent/` + `perception/{segment,describe,associate}` | everything that touches a model |

## Suggested split inside the robot track

The two halves barely overlap, so Sarah and Ryan can work in parallel from hour one:

| | files | notes |
|---|---|---|
| **Robot A — platform** | `server.py`, `capture.py`, `nav.py`, `pose.py`, `telemetry.py`, `config.py` | Mostly **wrapping Bracket Bot's existing SLAM/nav daemons**. Task zero: [inventory what already exists](robot/README.md#task-zero-before-writing-any-code-inventory-the-daemons) |
| **Robot B — the arm** | `arm.py`, mounting, IK, balance tuning, `led.py` | The only genuinely new hardware work, and the one with kill-checks at h18 and h22 |

Whoever finishes first takes `perception/depth.py` + `calib.py` — camera work sits naturally
with whoever mounted the cameras.

## Where perception splits

`perception/` is the one folder two people touch, so the boundary is drawn along skills and
enforced by file:

```
perception/
  depth.py      calib.py      fuse.py        ← Sarah/Ryan   (geometry, cameras)
  segment.py    describe.py   associate.py   ← Andrew       (models)
  voxelize.py   serialize.py                 ← Daniel       (feeds his index + schema)
  cluster.py    merge.py                     ← whoever gets there; low churn
```

If that boundary starts causing conflicts, split it into `perception/geo/` and
`perception/ml/` — but don't do it pre-emptively.

## The seams

Three interfaces are the only places tracks meet. **Freeze them in hour two** and nobody
blocks anyone for the rest of the weekend:

| interface | owner | consumers |
|---|---|---|
| **Robot HTTP + WebSocket** ([`docs/16-api.md`](docs/16-api.md)) | Sarah/Ryan | everyone |
| **Object record schema** (`roomctl/state.py`) | Daniel | Andrew, Sarah/Ryan |
| **Elasticsearch mappings** (`elastic/mappings/`) | Daniel | Andrew |
| **Backend ↔ gitirl-agent**: verbs, envelope, `gitspace.plan/1` ([`ANDREW-HANDOFF.md`](ANDREW-HANDOFF.md), [`docs/31`](docs/31-agent-panel-contract.md)) | Daniel | Andrew |

Stub all three on day one with fake data behind them. Real implementations land later.

## Who is blocked by what

```
hour 0 ───────────────────────────────────────────────────────────────►
Daniel   ████████████████████████████████████████  unblocked immediately (fake/)
Andrew   ████████████████████████████████████████  unblocked immediately (fake/)
Sarah    ░░░░░░░░████████████████████████████████  needs the kit
Ryan     ░░░░░░░░████████████████████████████████  needs the kit + the arm
```

Daniel and Andrew being unblocked at hour zero is why **the Elastic and agent submissions
survive even if the arm never lifts anything.** Protect that property — don't let either
track acquire a hardware dependency it doesn't need.

## Daily sync points
Four short check-ins, at the gates in [`BUILD_ORDER.md`](BUILD_ORDER.md): h3, h9, h14, h22.
At each one, the question is the same: *what would we demo right now if judging started?*

## As built — which session actually owns what (2026-09-18)

Ownership shifted from the plan above once six parallel workstreams started building. This is
who to ask, not who was assigned:

| session | owns |
|---|---|
| master / integration | `roomctl/` (state, repo, cli, executor, robot_client, publish) · `fake/` · `tests/` · `docs/` · README · TEAM · BUILD_ORDER · `ANDREW-HANDOFF.md` · **deployment**: the GCP tier + Vercel (`scripts/gcp_mirror.sh`, `scripts/deploy_vercel.sh`, docs/19 "As built") |
| elastic | `elastic/` — mappings, `setup_elastic.py`, `ingest.py`, `queries.py`, `records.py` |
| web | `web/` (except `web/landing/`) — server, capture/object/graph/telemetry pages, SSE |
| perception · pointcloud (htn:5) | `perception/{depth,fuse,voxelize,serialize,costmap,raycast,pipeline}.py` — incl. docs/24 A1–A4 · the agent panel: `perception/devgraph.py` (its backend, the only room.git writer from the UI) + `web/landing/dev-graph.html` |
| perception · segment | `perception/{segment,cluster,describe,merge,associate}.py` |
| cloud / observability (htn:7) | `scripts/`, `obs.py`, `telemetry/`, `robot/telemetry.py`, `SENTRY_STORY.md`, deployment · `bridge/`: `POST /api/agent/command`, `/ws/gitirl-agent` (docs/31) |
| **nobody yet** | `robot/` except telemetry — server, arm, nav (D15) · sending `robot_action` plans to Andrew's agent (ANDREW-HANDOFF.md §2) |
