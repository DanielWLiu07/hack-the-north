# Build order and branch plan

Not a git repo yet. When it becomes one, this is the split — **each folder is one branch and
one owner**, chosen so the four tracks never touch the same files.

## Branches

| branch | owner | folder(s) | unblocked at |
|---|---|---|---|
| `track/robot-platform` | **Sarah** | `robot/{server,capture,nav,pose,telemetry,config}`, `scripts/` | when the kit arrives |
| `track/robot-arm` | **Ryan** | `robot/{arm,led}`, mounting, IK | kit + arm |
| `track/perception` | **Andrew** (ML) + Sarah/Ryan (geometry) | `perception/` — [split by file](TEAM.md#where-perception-splits) | recorded captures |
| `track/data` | **Daniel** | `elastic/`, `roomctl/`, `viz/`, `fake/` | **hour zero** |
| `track/web` | **Daniel** | `web/` | **hour zero** |
| `track/agent` | **Andrew** | `agent/` | **hour zero** |

`main` holds `docs/`, `ARCHITECTURE.md`, `TEAM.md` and this file. Merge into `main` at each
gate below, not continuously — a broken `main` at hour 20 costs everyone.

**Sarah and Ryan share the Pi but not the files.** The platform half wraps existing daemons;
the arm half is the only genuinely new hardware work. They barely overlap, so both can move
from hour one — but only one person physically handles the robot at a time, and balance
calibration is not a two-person job.

---

## Hour zero — freeze three interfaces, then scatter

Nothing else matters until these three are written down and agreed. They are the seams
between the branches:

1. **The robot HTTP + WebSocket contract** — [`docs/16-api.md`](docs/16-api.md). Stub every
   endpoint to return fake JSON so the other tracks can build immediately.
2. **The object record schema** — `roomctl/state.py`. One YAML shape, quantized, one field per line.
3. **The Elasticsearch mappings** — `elastic/mappings/`. **Immutable once created.**

Then two things in parallel:
- **`fake/scene_gen.py`** — one hour, unblocks half the team for the whole weekend.
- **Inventory Bracket Bot's daemons** — SLAM? nav? persistent map? The answers decide whether
  `robot/nav.py` is a 30-line adapter or a day of work, and whether
  [R4](docs/08-risks.md) is still our biggest correctness risk. Thirty minutes at the booth.

---

## Gates

Each gate is a demo to a stranger, not a self-assessment.

### G1 · hour 3 — three things work separately
- robot drives (`example_wasd.py`)
- a point cloud renders in Rerun
- `room diff` prints real git output over **fake** objects
- Elasticsearch has indices and returns a hybrid-search hit over **fake** objects

### G2 · hour 9 — **the most important gate in the schedule**
- **Scan an untouched table twice → `git diff --exit-code` is clean.**
- Move one object → exactly one modified file.

If this is red, everything downstream is theatre. Do not proceed.

### G3 · hour 14 — T0 complete, demo it to another team
- real objects detected, labelled, associated
- robot drives to a changed object and announces it
- Rerun blueprint v1 + LED status
- Elastic: occlusion query and `room search` working

### G4 · hour 22 — T1, the robot writes
- top-down pick-and-place
- `revert` and `checkout` physically execute
- executor orders ops correctly and verifies by rescan
- **kill-checks:** balance at h18, grasping at h22 ([`docs/08-risks.md`](docs/08-risks.md))

### G5 · hour 28 — T2/T3, pick by what's healthy
- 360° fusion, voxel index, branch/merge/conflict, voice, Telegram, S3
- **Saturday noon: lock the sponsor prize list** (submission closes 2 PM EDT)

### G6 · hour 28→34 — freeze
**No new features.** Tag a known-good `room.git` and `.rrd`. Run the full demo five times.
Record the 60-second video. Write the Devpost. Rehearse aloud. Sleep.

---

## Friday morning, at the booths

- **OpenAI** — collect API credits. They sponsor the API prize and hand out credits at the
  booth. This matters for our design: with credits, **GPT-5 vision for per-view object
  descriptions is effectively free**, which is what makes the three-disagreeing-descriptions
  story (and therefore hybrid search) work. Get them early, before the queue.
- **Elastic** — ask about a sponsor org or credits, and confirm current `semantic_text` +
  Jina inference syntax. Their booth will know faster than the docs.
- **Bracket Bot** — the eight questions in [`docs/10-open-questions.md`](docs/10-open-questions.md).
- **Baseten / Sentry / ElevenLabs** — only if ahead of schedule.

---

## Standing rules
1. Every tier must be independently demo-able before the next one starts.
2. Anything that doesn't make the demo better is a distraction — unless it's under an hour.
3. **Commit the working version before you try the risky thing.** (Yes, the irony is noted.)
4. Stuck 45 minutes → switch tasks or go ask the sponsor booth.
5. `scripts/snapshot.sh` after every improvement. Never let the only working version be the
   one you're currently editing.
