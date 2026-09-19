# GITSPACE — system design

> Planning only. No code, no `git init`, no GitHub yet.
> Every folder below exists and carries a `README.md` describing the files that will go in it.

## The system in one picture

```
                         ┌──────────────────────── THE ROOM ────────────────────────┐
                         │   objects on a table · an AprilTag anchor · a human       │
                         └───────▲──────────────────────────────────────┬───────────┘
                                 │ arm picks & places                   │ 3× stereo
                                 │                                      ▼
┌────────────────────── RASPBERRY PI 5 (on the robot) ───────────────────────────────┐
│  balance LQR + Madgwick  (stock BB — DO NOT TOUCH)                                  │
│  robot/server.py ── six HTTP endpoints ─────────────────────────────────────────    │
│    /capture   /pose   /drive   /arm   /say   /led                                   │
│  robot/capture.py  robot/arm.py  robot/nav.py  robot/led.py  robot/audio.py         │
│  robot/telemetry.py ── 50 Hz signal tap ─────────────────────────┐                  │
└──────────────────────────────┬───────────────────────────────────┼──────────────────┘
                               │ JPEG + pose over OUR OWN network  │ bulk, 1 s buffer
                               ▼                                   ▼
┌─────────────────────────── LAPTOP ──────────────────┐   ┌──────────────────────────┐
│  perception/  depth → fuse → segment → describe      │   │      ELASTICSEARCH       │
│               → merge → voxelize → associate         │◄─►│  room-objects            │
│                            │                         │   │  room-voxels             │
│  roomctl/     state → serialize → git → executor     │   │  room-observations  TSDS │
│                            │                         │   │  robot-telemetry    TSDS │
│  agent/       tools: retrieve (ES) + act (robot)     │◄─►│  room-events             │
│                            │                         │   │  room-clouds  (catalog)  │
│  viz/         Rerun blueprint = THE DEMO SCREEN      │   └──────────────────────────┘
│                            │                         │
│  room.git/    the room's own repository ◄────────────┤   ┌──────────────────────────┐
└──────────────────────────────────────────────────────┘   │  disk: clouds/*.ply      │
                                                            └──────────────────────────┘
```

**Bracket Bot already ships daemons for SLAM, mapping and navigation** (their org also forks
`dora`, a dataflow middleware, so the stack is node-based). `robot/` **wraps** them — it is an
adapter exposing our six endpoints, not a robotics stack. The only genuinely new hardware work
on that track is the arm. See [`robot/README.md`](robot/README.md).

**The one rule:** the Pi does I/O, the laptop does thinking. Never put logic on the Pi that
you'd have to redeploy to debug — you'll be debugging at 4am with the robot on its side.

## Module map

| folder | owner | runs on | owns |
|---|---|---|---|
| [`robot/`](robot/) | **Sarah + Ryan** | **Pi 5** | **adapter** over BB's SLAM/nav daemons, plus the arm, cameras, LED, telemetry tap |
| [`perception/`](perception/) | **split 3 ways** | laptop | pixels → object instances → scene graph ([boundary](TEAM.md#where-perception-splits)) |
| [`elastic/`](elastic/) | **Daniel** | laptop | mappings, ingest, every query, Agent Builder tools |
| [`web/`](web/) | **Daniel** | laptop | search · history · analytics · conflict UI — the Elastic story made visible |
| [`roomctl/`](roomctl/) | **Daniel** | laptop | the `room` CLI; owns `room.git`; diff → ordered ops |
| [`agent/`](agent/) | **Andrew** | laptop | LLM loop, retrieve + act tools, voice |
| [`viz/`](viz/) | Daniel | laptop | the Rerun blueprint — the 3D demo screen |
| [`fake/`](fake/) | Daniel | laptop | hand-authored scenes — unblocks two people at hour zero |
| [`tests/`](tests/) | everyone | laptop | `test_idempotent_scan.py` is the one that matters |
| [`scripts/`](scripts/) | Sarah/Ryan | both | bootstrap + demo runner |

Full breakdown, including how `perception/` splits without file collisions: [`TEAM.md`](TEAM.md).

## The tracks

```
Sarah  ── robot/ platform  ── server, capture, nav, pose, telemetry   ── needs the kit
Ryan   ── robot/ arm       ── arm.py, mounting, IK, balance, led      ── needs the kit + arm
Daniel ── elastic/ web/ roomctl/ viz/ fake/                           ── hour zero
Andrew ── agent/ + perception ML (segment, describe, associate)       ── hour zero
```

Daniel and Andrew are unblocked before the robot is even assembled, because
[`fake/scene_gen.py`](fake/) emits exactly the object-record shape the real pipeline will —
disagreeing descriptions and per-camera conflicts included. **Protect that property.** It's
why the Elastic and agent submissions survive even if the arm never lifts anything.

## Data flow, end to end

```
  capture ──► depth ──► fuse ──► segment ──► describe ──► merge ──► voxelize
                                                                       │
                                                       associate ◄─────┘
                                                (ES hybrid search: re-identify)
                                                            │
                                                       serialize
                                                     (quantize → YAML)
                                                            │
                                         ┌──────────────────┼──────────────────┐
                                         ▼                  ▼                  ▼
                                    room.git/          Elasticsearch        Rerun
                                    (the truth)      (the queryable past)  (the screen)
                                         │
                                    git diff
                                         │
                                    executor  ──► ordered ops ──► robot/arm ──► THE ROOM
```

## Interfaces that must be frozen early

Freeze these in hour 2 and the four tracks never block each other again:

**1. The robot HTTP API** — six endpoints, defined in [`robot/README.md`](robot/).
**2. The object record shape** — one YAML schema, in [`roomctl/README.md`](roomctl/).
**3. The Elasticsearch mappings** — [`elastic/mappings/`](elastic/). Immutable once created.

Everything else can churn. These three cannot.

## Reading order for a new teammate
1. [`docs/01-concept.md`](docs/01-concept.md) — what we're building and why
2. [`docs/06-demo.md`](docs/06-demo.md) — the spec; build backwards from it
3. This file — where the code goes
4. [`BUILD_ORDER.md`](BUILD_ORDER.md) — what to do, hour by hour
5. Your own track's `README.md`
