# 05 — System architecture

## Where things run

```
  ┌─────────────── Raspberry Pi 5 (on the robot) ───────────────┐
  │  balance/LQR loop  (stock, do not touch)                    │
  │  odrive_uart drive + wheel odometry                         │
  │  capture service  — round-robin 3 stereo pairs, JPEG out    │
  │  arm service      — Feetech bus servos via LeRobot          │
  │  LED status                                                  │
  │  audio in/out     — whisper / kokoro / OpenAI Realtime      │
  └───────────────┬──────────────────────────────────────────────┘
                  │  our own wifi/ethernet (NOT hackathon wifi)
  ┌───────────────┴──────────── Laptop ─────────────────────────┐
  │  perception  — SGBM, fuse, cluster, label, associate         │
  │  roomctl CLI — the `room` command; owns the git repo         │
  │  agent       — LLM planning, conflict resolution, voice NLU  │
  │  Rerun viewer— THE DEMO SCREEN                               │
  └──────────────────────────────────────────────────────────────┘
                  │
           Elasticsearch: objects + observations + events  <- agent's context layer
           (optional) Baseten: SAM3 / VLM / depth model inference
```

**Design principle: the Pi does I/O, the laptop does thinking.** The Pi is a sensor and
actuator server. All perception, all git, all planning lives on the laptop where we can
iterate fast, use a GPU, and — critically — **keep working when the robot is off the
floor being repaired**. Never put logic on the Pi that you'd have to redeploy to debug.

## Interface between them

Keep it dead simple. A small HTTP/JSON or ZeroMQ service on the Pi:

| endpoint | does |
|---|---|
| `POST /capture` | round-robin grab N frames from each stereo cam, return JPEGs + timestamps + odom pose |
| `GET  /pose` | current odometry pose |
| `POST /drive` | go to (x, z, yaw) in world frame; returns when arrived or failed |
| `POST /arm` | `{pick: [x,y,z,yaw]}` / `{place: [x,y,z,yaw]}` / `{stow: true}` |
| `POST /say` | TTS a string |
| `POST /led` | set status colour |

That's the whole robot API. Six endpoints. Write it in hour 2, freeze it, and then the
perception person and the robot person never block each other again. **This is the single
highest-leverage decision in the build** — in a 36-hour project, the cost of two people
editing the same file at 3am dwarfs any architectural elegance.

## Repo layout (for when we do `git init`)

```
gitspace/
  robot/            # runs ON the Pi
    server.py       # the six endpoints
    capture.py      # round-robin stereo grab
    arm.py          # LeRobot/Feetech wrapper, top-down IK
    nav.py          # drive-to-pose using odom + anchor relocalization
  perception/
    depth.py        # forked from BB example_depth.py
    fuse.py         # extrinsics, world frame, ICP refine
    objects.py      # plane removal, clustering, bbox, labels
    associate.py    # Hungarian matching against HEAD
    serialize.py    # quantize -> deterministic YAML
  roomctl/
    cli.py          # the `room` command
    repo.py         # thin wrapper over `git` subprocess calls
    executor.py     # diff -> ordered ops -> robot API calls
    agent.py        # LLM: labeling assist, conflict resolution, voice intent
  viz/
    blueprint.py    # Rerun blueprint — the demo screen
  room.git/         # the room repository itself (separate from the code repo!)
  tests/
    test_idempotent_scan.py   # scan twice -> git diff --exit-code. THE test.
```

**Keep `room.git` separate from the code repo.** Two different histories. Conflating them
will cause exactly one hour of confusion at exactly the wrong time.

## Latency budget for the demo

The audience is watching. Every one of these numbers is a thing a judge experiences:

| step | target | notes |
|---|---|---|
| capture (3 cams × 4 frames, round-robin) | **< 2 s** | say "scanning" + LED pulse so the pause reads as intentional |
| transfer JPEGs to laptop | < 1 s | own network; JPEG not raw |
| SGBM × 3 + fuse | < 3 s | laptop CPU is fine at 1 shot; downsample early |
| cluster + label | < 3 s | cache labels for matched objects — **only label new/changed clusters** |
| associate + serialize + git | < 0.5 s | |
| **total `room status`** | **< 10 s** | acceptable if narrated; under 5 s is much better |
| one pick-and-place | 20–40 s | this is the slow part; narrate it, don't apologize for it |

**Tactic:** never let the audience watch silence. The robot should say what it's doing
(*"Scanning. Comparing against HEAD. Three objects changed."*), and Rerun should be
visibly updating. A 10-second pause with a live 3D point cloud assembling on screen is
impressive. A 10-second pause with a blinking cursor is death.

## Rerun blueprint — the demo screen

Treat this as a designed artifact, not debug output. Judges score **design**, and for a
CLI-first robotics project this screen *is* the design. Panels:

1. **Big 3D view** — fused point cloud, object bounding boxes, coloured by diff status:
   white = unchanged, **amber = moved** (with an arrow from old pose to new), **green =
   added**, **red outline ghost = removed**, grey = unobserved.
2. **Diff text panel** — the actual `git diff` output, monospace.
3. **Timeline** — Rerun's native timeline scrubbed by *commit*, so you can drag backwards
   and watch the room's history replay. This is close to free and looks incredible.
4. **Camera strip** — the three rectified left images, so people can see it's real.
5. **Status** — branch name, clean/dirty, HEAD short sha. Mirror it on the LED strip.

Build the blueprint early and keep it stable; don't redesign it at hour 30.

## The read path: Elasticsearch

Not optional, and not a bolt-on — it fills a real hole. Git is the write path and the
source of truth; it cannot do semantic search, aggregations, spatial queries, or fuzzy
time resolution, and it deliberately discards every messy observation that didn't make it
into a commit. Elasticsearch indexes committed objects, **raw per-camera observations
including rejected ones**, and room events, and serves as the agent's context layer.

Full design, index mappings, agent tools and demo beats: [`11-elastic.md`](11-elastic.md).

It runs entirely on the laptop side, which is why it's affordable: it parallelizes instead
of competing for robot time.

## Optional integrations (only if ahead of schedule)

- **Baseten** — host SAM 3 / the VLM / depth model. Right call architecturally anyway. ~1 h.
- **Sentry** — tracing across the perception pipeline + logs. You will be debugging this
  pipeline regardless; instrumenting it costs ~1 h and satisfies their "two products
  beyond error monitoring" requirement honestly.
- **ElevenLabs** — replace Kokoro TTS for the robot's narration. ~30 min, and a good voice
  materially improves the demo.

See [`07-prizes.md`](07-prizes.md) before adding any of these. The default answer is no.
