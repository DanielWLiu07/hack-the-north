# GITSPACE — version control for the room you're standing in

> **Hack the North 2026** · Sep 18–20 · Bracket Bot hardware, Elastic, Sentry
> Live: **https://gitirl.health** (also `gitspace-five.vercel.app`)

**One line:** the room is a git repository, and the robot is `git apply` for physical reality.

![Bracket Bot at the bench, arm holding a crisp packet, among the clutter it is there to tidy](docs/images/robot-and-the-mess.jpg)

```
world  --stereo cams + SLAM-->  point cloud  -->  objects  -->  YAML files  -->  a real git repo
  ^                                                                                     |
  '----  arm picks & places  <--  motion plan  <--  object diff  <--  git diff  <--------'
```

`git commit` records the room. `git status` is what has moved since. `git diff` prints a real diff
of physical space. An approved change is a real `--no-ff` merge; everything else is drift, and the
robot tidies drift and never decisions. Upstream of the robot it is *actual git* — see
[`docs/04-git-semantics.md`](docs/04-git-semantics.md).

**Git is the write path; Elasticsearch is the read path.** Git cannot answer *"where did I leave my
keys"* or *"put it back the way it was two hours ago"*, and it keeps nothing of the sensor data we
discard at commit time. [`docs/11-elastic.md`](docs/11-elastic.md)

---

## What is built

| | |
|---|---|
| **Room CLI** | `roomctl/` — `init status add commit diff log reset checkout revert restore search publish pr why watch`. `room pr open --as-seen` accepts what the room already looks like; `room why <commit>` says whether that commit's capture can be trusted; `room restore --before "2 hours ago"` resolves a phrase to a commit through Elasticsearch, falling back to git and saying which answered. |
| **Perception** | `perception/` — stereo capture → depth → voxels → objects, plus `bb_source` which reads the robot's own fused SLAM map (3 cm voxels) instead of rebuilding one. Objects are grouped across all zones at once, then assigned to a zone, so a zone boundary is a label and not a cut. |
| **The caretaker loop** | `roomctl/watch.py` + `caretaker.py` — two fresh passes confirm a change; Tier A tidies it with the arm, Tier B drives up and files a chore; a job counts as verified only after a clean rescan. A repair it can never make (object gone, nowhere to put it) becomes a chore rather than a retry forever. |
| **Language** | `bridge/` — our grammar first, then an OpenAI intent service (`scripts/intent_service.py`, gpt-5-mini) behind a metered gate. Resolution has three bands: refuse the absurd, **ask** about the vague, act on the clear. A motion refuses an object the room no longer has. |
| **Robot** | `robot/` — worker on the Pi (`/capture /pose /map/voxels /healthz`), a Housebot-Edge adapter on loopback, systemd units that survive a reboot, bus voltage and transport-discard counters on `/healthz`. Motion is refused unless the map generation matches a measured registration. |
| **Web** | `web/` — 14 routers; pages `/` `/robot` `/telemetry` `/scene` `/capture` `/replay` `/object`, and `/live` which never leaves the laptop. |
| **Andrew's edge** | `andrew/` — his service, copied at `7a31596`, unedited; see [`andrew/PROVENANCE.md`](andrew/PROVENANCE.md). |
| **Simulator** | `fake/bbsim.py` — a byte-compatible fake of the robot's nav server; `scripts/demo_sim.py up\|mess\|decide\|check` runs the whole demo without hardware. |

## What it looks like

| | |
|---|---|
| ![the room page: the robot's point cloud with the indexed octree cells over the floor](docs/images/page-room-octree.jpg) | **`/robot`** — the room as the robot has it: its own point cloud, with Elasticsearch's occupied cells drawn over the floor. Every cell is a document; a prefix of its key is a region, so zooming out is a keyword query. |
| ![the commit history drawn as a graph with coloured lanes, refs and short hashes](docs/images/page-history-graph.png) | **The history** — the room's commits as a graph. Branches fork and an approved decision is a real merge, because that is what it is in git. Click a commit and the console acts on it. |
| ![the telemetry page mid-animation: the robot's eye charging a beam locked on a resolved issue card](docs/images/page-sentry-laser.jpg) | **`/telemetry`** — the Sentry board. Marking an issue fixed really resolves it in Sentry and is read back before the page believes it; the card stays until you remove it, and then the robot destroys it. |
| ![the robot's head camera, labelled: a small box and a crumpled snack wrapper on the corridor floor with their sizes and positions](docs/images/floor-objects.jpg) | **Floor objects** — a crisp packet is about four centimetres tall against six of floor noise, so height cannot find it and colour can. Measured across twenty captures: 12 of 12 real items, 1 false. |
| ![a frame from the robot's own head camera looking down a corridor](docs/images/robot-head-camera.jpg) | **What the robot sees** — one rectified head frame. The names only go on when the frame lines up with the map: at least 60% of the standing pixels within 15 cm, measured at 84.8%. |

## The stack

- **Elastic Cloud Serverless 9.6.0** (`us-east4`), no instance to manage. Indices `room-objects`,
  `room-voxels`, `room-clouds`, `room-observations`, `room-events`, `robot-telemetry`. Hybrid search
  is BM25 + `jina-embed` fused with RRF and reranked by `jina-rerank`, all inside Elastic.
  Octree keys give free spatial rungs; an ingest pipeline derives the coarse ones.
- **Sentry**, seven products with real data: tracing (distributed laptop → Pi), profiling, logs,
  uptime, crons (the room-clean badge), session replay incl. canvas, and AI agent monitoring.
  `scripts/seer_sweep.py` works Seer through open issues on a hard budget and never applies a patch.
- **Bracket Bot** — bbos daemons publish camera, SLAM pose and a fused voxel map; we read them and
  never restart them, because that restarts a balancing base.
- **GCP** `e2-small` + Caddy behind **Vercel**; static from the CDN, everything else proxied.

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -r web/requirements.txt
cp .env.example .env                      # keys: Elastic, Sentry, OpenAI
cd web && ../.venv/bin/python server.py   # http://127.0.0.1:8000
python scripts/demo_sim.py up             # simulator + room + loop + a site on :8001
.venv/bin/python -m pytest -q tests perception/tests web/tests bridge elastic/tests telemetry
```

~1,130 tests. `scripts/gcp_mirror.sh ship` deploys the backend, `scripts/deploy_vercel.sh` the front.

## Rules that are not negotiable

- **The robot moves only when a person asks for it in that moment**, with people beside it. No
  scheduler, no retry, no page may dispatch motion on its own.
- **Never invent a number.** A pose that was not recorded is reported as absent, not converted; a
  capture that saw nothing says so; a threshold is measured where it is enforced or it does not ship.
- **Nothing simulated is ever labelled real.** `simulated: true` comes from the answer, never assumed.
- **No test may reach the live Sentry project or write to the shared indices** — every suite has a
  guard, because a suite once filed six fictional issues into the live list.
- **Camera frames stay on the laptop.** `/live` is loopback-only and is not in the repo.

## Docs

Start with [`docs/01-concept.md`](docs/01-concept.md), then
[`docs/DEMO-RUNBOOK.md`](docs/DEMO-RUNBOOK.md) — every claim in the runbook is either observed, with
the server build named, or marked as not rehearsed.

`docs/` (42 files) covers hardware `02`, perception `03` `15` `20`, git semantics `04`, the API `16`,
Elastic `11`–`14`, Sentry `18` `26` `29`, deployment `19`, camera sync `22`, telemetry `23`,
traversal and the graph `24`, identity `25`, the robot link `33` `35`, and the live room `34`.
[`docs/10-open-questions.md`](docs/10-open-questions.md) is the decision log,
[`PROGRESS.md`](PROGRESS.md) the hour-by-hour record with what surprised us,
[`plan/roommate/`](plan/roommate/) the caretaker design, and each folder carries its own README.
