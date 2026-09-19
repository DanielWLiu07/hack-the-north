# 34 — The live room: the real robot, a room you can `git log`

**One flow, run every time anything looks at the room: capture → scan → git.** The robot takes one gated stereo
picture; the laptop turns it into depth, a point cloud and the objects inside `room.yaml`'s zones; the room
repository's working tree then says what is there, and `git status` / `git commit` do the rest.
`scripts/room_live.py` is the front door. [`33`](33-robot-link.md) is how the laptop reaches the robot; this doc starts
where that one ends. Before it (2026-09-19) the only recordings the pipeline had ever read were
`perception/synthetic.py`'s.

```
   robot  bracketbot-0183                       laptop  scripts/room_live.py
  ┌───────────────────────────┐   1 CAPTURE    ┌───────────────────────────────────────────────┐
  │ head stereo cam 2560×960  │ ─────────────► │ capture_to_recording.py                       │
  │ POST /capture   (gated:   │   one JPEG,    │   the robot's bytes + calibration + the MOUNT,│
  │ skew + tilt, docs/22 §8)  │   ~175 KB      │   self-levelled on the near floor             │
  └───────────────────────────┘                └──────────────┬────────────────────────────────┘
                                                              │  <name>.recordings/cap_NNNN/
                                   2 SCAN      ┌──────────────▼────────────────────────────────┐
                                               │ perception/pipeline.py   (via the `room` CLI) │
                                               │ depth → fuse + floor check → objects INSIDE   │
                                               │ room.yaml's zones → voxels → associate        │
                                               └───────┬───────────────────────────┬───────────┘
                                   3 GIT               │                           │ same depth → fuse
                                       ┌───────────────▼─────────────┐  ┌──────────▼───────────────────┐
                                       │ <name>/    the room repo    │  │ <name>.scene/                │
                                       │ zones/<zone>/<object>.yaml  │  │ cap_NNNN.ply · cap_NNNN.png  │
                                       │ cloud/current.ply  (2 cm)   │  │ latest.ply · latest.png      │
                                       │ `git status` = what changed │  │ the FULL-resolution model,   │
                                       │ `git log` = the room in time│  │ BESIDE the repo              │
                                       └─────────────────────────────┘  └──────────────────────────────┘
```

> **The one operating rule: within an instance, the robot does not move.** Its pose is not tracked
> (`pose_source: "none"`), so every capture is assumed to be from the same spot. Turn it or roll it and every object
> "moved" (§4 has the measurement). Moved it? `new` again, under another name.

Measured vs assumed, as everywhere in these docs: every number below was measured on `bracketbot-0183` on 2026-09-19
or is read from the named source file. Where something is reasoning, it says so. **These tools were still changing
while this was written** — if an output line below differs from what you see, `--help` and the script's docstring win.

## 1. Start here — from nothing to a live instance

All commands from the repo root. Roughly, on this laptop: a capture ~1 s, a scan 10–20 s, the 3D model a few seconds
more. `<PI_HOST>` is the robot's address in `.env` ([`33` §3](33-robot-link.md)); the SSH user on this robot is
`bracketbot`. Every `room_live.py` run starts with a dim `sentry trace <id> …` line: that id finds the run's waterfall
in Sentry (capture on the robot → scan → commit are one trace).

**1 · Is the link up?**
```bash
.venv/bin/python scripts/pi_link.py status
```
```
  PI_LINK=lan   PI_HOST=<PI_HOST>   LAPTOP_IP=<laptop>

   lan      <PI_HOST>        ANSWERS   mode=hardware fw=… cameras=[…]  <- selected
   tailnet  (not set)        no        …
```
`ANSWERS` on the `<- selected` row with `mode=hardware` = `robot.server` is up with the real camera: go to step 3.
`PORT OPEN` or `no` = step 2. `ANSWERS` on the *other* row = `python scripts/pi_link.py use <that one>`.
`mode=sim` = somebody started the simulated robot; its pictures are not the room — step 2 without `--sim`.
The full proof (about 40 s) is `.venv/bin/python scripts/verify_robot_link.py`; its last line is
`LINK VERIFIED to … at <host>:<port>`, exit `0` ([`33` §5](33-robot-link.md)).

**2 · Robot server up** — only if step 1 did not say `ANSWERS`, and **always after the robot was switched off and on**:
`robot.server` is not installed as a service and does not come back by itself.
```bash
./scripts/push_to_pi.sh bracketbot@<PI_HOST> --start
```
```
==> syncing robot/ -> bracketbot@<PI_HOST>:~/gitspace/
==> python deps (first run takes a minute; after that it is a no-op)
   …                                  (what is set and what is missing on the robot — read it once)
==> starting: python -m robot.server --hardware   (log: ~/gitspace/robot.log on the Pi)
==> it answers:
{"mode":"hardware", … }
==> now prove the link:  .venv/bin/python scripts/verify_robot_link.py --host <PI_HOST>
```
`it answers:` is the line you want. `robot.server did not come up in 20 s` prints the robot's own log above it —
read that. `… --log` tails the log, `… --stop` stops the server.

**3 · Park at the desk — or accept a hallway.** The scan only looks for objects **inside `room.yaml`'s zones**, and with
no pose those zones are measured *from the robot*. Parking is [`robot/RUNBOOK.md` §7](../robot/RUNBOOK.md) (a person
can do it with a tape measure). Then let the check tell you:
```bash
.venv/bin/python scripts/capture_to_recording.py --check-desk
```
```
  DESK CHECK  cap_NNNN   room.yaml desk zone: x [0.08, 1.0] y [-0.5, 0.5] z [0.68, 1.3] surface 0.7  (metres, from the robot)
    found a flat surface at z = … m · from x = … to … m ahead · y […] · … points
    points inside the zone: …
    OK — the desk is where room.yaml expects it. Do not move the robot; take the captures.
```
`OK` (exit 0) = stop adjusting, hands off the robot. `NOT OK —` (exit 1) says the one thing that is off and by how much:
`the desk starts 0.34 m ahead; the zone starts at 0.08: roll the robot 0.26 m FORWARD` · `the desk top is at 0.75 m,
room.yaml says 0.70` · `the desk is centred … m to the robot's LEFT` · `… the robot is not facing a desk`. It passes
when the top is within 3 cm of `surface`, the near edge within 15 cm of the zone's start, the desk reaches at least
30 cm into the zone, and it is centred within 15 cm. Fix that one thing, run it again.
**A hallway is a legitimate instance**: the scan correctly reports 0 objects and `status` is trivially clean, and you
still get the captures, the point cloud in every commit and the noise floor. It just cannot show you an object moving.

**4 · `new`** — a fresh room repo, the first capture, the first commit.
```bash
.venv/bin/python scripts/room_live.py new desk-demo
```
```
[1/4] capture — the robot at <PI_HOST> takes the first picture
  cap_0007  ->  ~/.cache/gitspace/rooms/desk-demo.recordings/cap_0007   (178 KB · tilt 0.020466 · pose_source none)
  levelled: pitch … deg, height … cm (roll … deg left as is) · floor flat to … mm over … px
[2/4] create the room repository  ~/.cache/gitspace/rooms/desk-demo
[3/4] scan it and make the first commit   (depth -> point cloud -> objects in room.yaml's zones -> git)
[main 7e3b29e] first scan (cap_0007)
 no objects changed
[4/4] the 3D model of what it saw
  3D model: ~/.cache/gitspace/rooms/desk-demo.scene/cap_0007.ply  (488,702 coloured points, 7.3 MB) · latest.ply / latest.png beside it

instance `desk-demo` is live.  It is a normal git repo: ~/.cache/gitspace/rooms/desk-demo
  next:  …
```
The numbers are the real first run (the instance was called `hallway-test`, and it was a hallway — hence `no objects
changed`; at a desk the line reads `3 added`, or however many it found). `tilt` is the robot's peak tilt rate during
the latch, rad/s; the gate is 0.05 ([`22` §4](22-camera-sync.md)). `levelled:` is §5's per-capture floor correction — on
the two levelled captures so far it read −1.88° / −1.4 cm and −1.70° / −1.3 cm, floor flat to 14 mm over ~30,000 px.
`pose_source none` is the reminder of the one rule. `instance … is live.` is the only line that means it worked; §6
has the other ending.

**5 · Change something.** Move an object on the desk **at least 5 cm**, add one, or take one away. Then get out of the
picture: no hands on the desk, nobody between robot and desk, nobody touching the robot.

**6 · `status`** — capture + scan, then the room's `git status`.
```bash
.venv/bin/python scripts/room_live.py status
```
```
[1/3] capture
  cap_NNNN  ->  …/desk-demo.recordings/cap_NNNN   (… KB · tilt … · pose_source none)
[2/3] scan + status   (…/rooms/desk-demo)
On branch main
Changes not staged for commit:
	modified:   zones/desk/<object>.yaml   (moved 0.12 m)
	deleted:    zones/desk/<object>.yaml   (<class> gone)
Untracked objects:
	zones/desk/<object>.yaml   (new <class>)
[3/3] 3D model
  3D model: …/desk-demo.scene/cap_NNNN.ply  (… coloured points, … MB) · latest.ply / latest.png beside it
```
Moved = `modified`, new = `Untracked objects`, gone = `deleted`. Untouched = `nothing to commit, working tree clean`.
Exit `0` clean · `1` dirty · `2` the scan crashed (`the scan crashed — the room's state was NOT updated; the capture is
kept: …`, with the traceback above it). `status` is about **objects**: it does not touch `cloud/`.

**7 · `commit`** — capture, put the point cloud in the tree, scan, commit.
```bash
.venv/bin/python scripts/room_live.py commit -m "items on the floor"
.venv/bin/python scripts/room_live.py snapshot          # the same thing, message optional: "snapshot <date time>"
```
```
[1/3] capture
  cap_0016  ->  …/desk-demo.recordings/cap_0016   (… KB · tilt 0.026857 · pose_source none)
[2/3] point cloud + scan + commit   (…/rooms/desk-demo)
[main 522cedb] items on the floor  [cap_0016]
 no objects changed
  frozen: commit 522cedb holds the room at 2026-09-19T17:18:02Z — 144,017 points (2.2 MB) in cloud/current.ply
          get it back any time:  python scripts/room_live.py cloud --ref 522cedb
[3/3] full-resolution 3D model
  3D model: …
```
(Real run, same hallway.) The second line of the commit counts `added` / `removed` / `moved` objects. **A commit no
longer means "something moved":** the cloud of a new capture is in practice never byte-identical, so every `commit` /
`snapshot` commits. Read that second line; `no objects changed` = only the cloud did. Exit `0` committed · `2` `no commit was made
— the scan's error is above. The capture is kept: …` (and `cloud/` is put back as it was).
**`commit` takes its own capture** — it does not commit the look `status` just showed you.

**8 · `watch`** — keep looking.
```bash
.venv/bin/python scripts/room_live.py watch --every 15             # print every change
.venv/bin/python scripts/room_live.py watch --every 15 --commit    # …and commit each one, cloud included
```
```
watching `desk-demo` every 15 s — Ctrl-C stops.  …/rooms/desk-demo
  13:06:02  cap_NNNN  clean
  13:06:21  cap_NNNN  1 change: modified <object_id> (0.12 m)
            [main <sha>] watch: 1 change  [cap_NNNN]
  13:06:40  no capture (robot moving, or unreachable) — trying again
```
`--every` is a floor, not a promise: a look that takes longer than it is followed by the next after 1 s. `watch`
commits only when an **object** changed — a clean look commits nothing, cloud or not. Each look also writes a
full-resolution model and prints its `3D model:` line — about 7 MB each, so at a look every 15–25 s a long watch is
1–2 GB an hour. Pass `--no-scene`.

**9 · Get a past room back.**
```bash
.venv/bin/python scripts/room_live.py cloud --ref 522cedb          # default --ref HEAD; --out FILE to choose where
```
```
the room at 522cedb  (2026-09-19 17:18:09 +0000 · items on the floor  [cap_0016])
  144,017 points · capture cap_0016 · pose_source none
  -> ~/.cache/gitspace/rooms/desk-demo.scene/at-522cedb.ply
```
A commit from before snapshots existed answers `commit <sha> has no cloud/current.ply (it predates snapshots).`
It is `--ref <sha>`: a bare `cloud 522cedb` is read as an instance name (§2's trap).

## 2. The verbs, and where everything lives

| verb | looks at the room | what it does | exit |
|---|---|---|---|
| `new <name> [-m MSG]` | yes | `git init -b main`, copy `room.yaml` · `.roomignore` · `anchors/` from the capture (it took them from `room.git/`), stage the cloud, scan, first commit, 3D model. Refuses a name that is already a room. **With `-m` the message is yours verbatim — no capture id** | `0` live · `1` no capture, or no commit |
| `status [name]` | yes | scan into the working tree, then `room status --exit-code`. Objects only; `cloud/` is not touched | `0` clean · `1` dirty · `2` scan crashed |
| `commit -m MSG [name]` | yes | write `cloud/current.ply` + `.json`, scan, then `room commit -m "MSG  [cap_NNNN]"` | `0` committed · `2` no commit was made |
| `snapshot [-m MSG] [name]` | yes | `commit` with the message optional (`snapshot <date time>`): freeze the room now | same |
| `cloud [--ref REF] [--out FILE] [name]` | no | `git show REF:cloud/current.ply` → a `.ply` on disk (default `<name>.scene/at-<sha>.ply`), with its metadata printed. `REF` = a sha, `HEAD~2`, a tag; default `HEAD` | `0` · `1` not a commit, or no cloud in it |
| `watch [name] [--every S] [--commit]` | yes, until Ctrl-C | `room status --json` per look, one line each; `--commit` = stage the cloud + `room commit --no-scan` on every look where an object changed. Default 15 s | `0` |
| `log [name] [git log args]` | no | `room log`; bare = `--graph --oneline --decorate --all` | git's |
| `diff [name] [git diff args]` | no | the literal `git diff` of the room | git's |
| `list` | no | every instance, its commit count and last commit; `*` = the current one | `0` |

Every verb but `list` takes `name` (default: the last instance used, remembered in `.current`), `--repo PATH` (any
room repo instead of an instance — §3) and `--no-scene` (skip the full-resolution model). `ROOM_LIVE_DIR` moves the
whole tree.

**One trap, in `log` / `diff` / `cloud`:** the first bare word is taken as the instance name. `log desk-demo --stat`
works; `log --stat` is rejected; `diff HEAD~1` looks for an instance called `HEAD~1`. Name the instance first, or `cd`
in and use plain git.

| where | what | in git |
|---|---|---|
| `~/.cache/gitspace/rooms/<name>/` | the room repo: `room.yaml` · `.roomignore` · `anchors/tag_0.yaml` · `zones/<zone>/<object>.yaml` | yes — text |
| `…/<name>/cloud/` | `current.ply`: this commit's coloured point cloud, **one point per 2 cm cell** (measured: 107,608–149,801 points, 1.6–2.2 MB, from 379,530–466,881 measured points) · `current.json`: which capture, when, how many points, `pose_source`, tilt, bounds | yes — one binary per commit |
| `…/<name>/.git/gitspace/` | `scan.json` (the last scan's capture id and Sentry trace) · `voxels.npz` (its voxel grid, staged for a publish) · `misses.json` (§4's debounce) | inside `.git`: never committed, replaced by every scan |
| `…/<name>.recordings/cap_NNNN/` | `cam0.jpg` (the robot's bytes, not re-encoded) · `cam0.yaml` (the calibration, copied per capture) · `capture.json` (pose, tilt, the mount — levelled, with `mount_nominal` and `levelled` beside it — `pose_source`, the Sentry trace) · `room/` | no — beside |
| `…/<name>.scene/` | `cap_NNNN.ply`: **every** measured point within 5 m (room frame — x forward, y left, z up, floor at 0, metres) with its pixel's colour, 6.9–7.5 MB; opens in MeshLab / CloudCompare / three.js · `cap_NNNN.png` (from above + from the side, a 150,000-point sample) · `latest.ply` · `latest.png` · `at-<sha>.ply` (what `cloud` wrote) | no — beside |
| `…/rooms/.current` | the name of the last instance used | — |
| `~/.cache/gitspace/recordings/` | captures `capture_to_recording.py` takes on its own (`--check-desk`, `--n`) | — |

The tool underneath, when you need it without an instance:

| `capture_to_recording.py …` | does |
|---|---|
| *(bare)* · `--out DIR` · `--camera cam0` · `--host` `--port` | one gated capture → a recording, self-levelled (§5), and recorded in Elasticsearch (`room-clouds`): `  indexed: room-clouds cap_NNNN quality_ok=… coverage=…`, with `(SPOOLED, Elasticsearch away: …)` when it is |
| `--no-index` | the same, nothing sent to Elasticsearch |
| `--n 3 --every 4` | three captures 4 s apart; each is compared with the one before and the agreement table is printed — on an untouched scene that table **is the noise floor** |
| `--compare DIR_A DIR_B` | the same table for two existing recordings; no robot needed. `--json` prints it as JSON too |
| `--check-desk [DIR]` | step 3. With `DIR`: the same question of an existing recording, no capture |

## 3. How it works with git

**In the repo: what diffs, and one cloud.** One small YAML file per object (`id`, `class`, `zone`, `pose`, `extents`,
`color`, `first_seen`) plus the room's constants — text, so `status`, `diff`, `log`, `blame`, branches and remotes come
for free; the scan *writes the working tree* and git does the comparing. And `cloud/current.ply`: the room as the robot
measured it at that commit, thinned to one point per 2 cm cell so git can afford it (about 2 MB a commit; the real
instance's `.git` was 4.7 MB after three of them). So **history is the room over time**:
```bash
cd ~/.cache/gitspace/rooms/desk-demo
git log --stat                                  # what moved, commit by commit
git log -- cloud/current.ply                    # every snapshot
git diff HEAD~1 -- zones/desk/                  # the literal diff of the desk
git show 522cedb:cloud/current.ply > then.ply   # the room as it was (or: room_live.py cloud --ref 522cedb)
```

**Beside the repo: the full resolution, and the evidence.** The full model is ~7 MB of binary per capture (6.9–7.5 MB
measured, 460,000–500,000 points) — what git is bad at. The recordings are kept because they are the evidence: any
commit can be re-scanned from the picture it was built from.

**The commit message is the join.** `commit` / `snapshot` append `  [cap_NNNN]`, `new` writes `first scan (cap_NNNN)`,
`watch --commit` writes `watch: N changes  [cap_NNNN]`. From any commit: `<name>.recordings/cap_NNNN/` is what the
robot saw, `<name>.scene/cap_NNNN.ply` is its full model, `cloud/current.json` says the same id from inside the commit,
and `capture.json`'s `sentry_trace_id` is the capture's trace. Capture ids are counted across robot restarts, so they
do not collide. Commits are authored `gitspace-robot <robot@gitspace.local>`, whoever ran the command.

**What leaves the laptop** — read this before `git push` or `--repo ./room.git`:

| | an instance | `--repo ./room.git` — the real room |
|---|---|---|
| every look is indexed in Elasticsearch: one `room-clouds` doc + the `room-observations` rows (rejected captures and plain `status` looks included), fused cloud → `clouds/<capture_id>.ply` | **yes** — `room_live.py` sets `GITSPACE_INDEX_CAPTURES=1` for every scan of a robot capture ("a capture from the real robot is evidence"). Export `GITSPACE_INDEX_CAPTURES=0` to stop it. Spooled if Elasticsearch is away | yes |
| every **commit** is published: its documents and voxels | no — `ROOM_ES=off`, and it is not `$ROOM_GIT_PATH` | yes. The line after the commit says how: `es: …`, or `es: N docs spooled (…)` — then `room publish --flush` sends them |
| every commit is mirrored to the cloud web tier | no — no hook | yes — `room.git`'s `post-commit` hook bundles every branch and tag and ships it (`scripts/gcp_mirror.sh sync-bg`; `ROOM_MIRROR=off` skips it) |

Three cautions, all from reading the code rather than from having done it:
1. **A push now carries pictures.** `cloud/current.ply` is a coloured point cloud of the room and whoever stood in it.
   `git push` of an instance sends every one of them; the real room's mirror hook sends them to the cloud VM. Decide
   that on purpose.
2. **A look overwrites the working tree with what the robot sees.** `room.git` currently holds the demo scene
   (`cup_7e21`, `mug_a1b2`, …); from a hallway the second look can delete every one of them, and a `commit` then
   publishes that. `git -C room.git status` shows what a look did; `git -C room.git restore zones` undoes an
   uncommitted one (new, untracked object files are yours to delete).
3. **With `--repo ./room.git` the recordings and models land at `./room.git.recordings/` and `./room.git.scene/`** —
   inside the code repo's folder, and **not covered by `.gitignore`** (only `room.git/` and `clouds/*.ply` are).
   Never `git add -A` in the code repo after such a run.

**These tools never commit to the code repo.** They run git only inside a room repository, and `roomctl` refuses a
room path that contains the code (`room.git must be a SEPARATE repository`). Code commits are made by a person, by hand.

**Privacy.** The pictures, the recordings, the clouds and the models contain people. The site's port 8000 is public
through a tunnel; the live camera routes are served to this laptop only (loopback peer **and** no forwarding header —
`web/localonly.py`). The browser view of the models follows the same rule: `http://127.0.0.1:8000/scene`
(`web/scene_api.py` — it only *reads* `<name>.scene/`, polls for new captures, never talks to the robot) serves the
page and every `.ply` to a loopback peer with no forwarding header, and 403 to everyone else. It is mounted as an
optional router: a web server started before it was added needs a restart, and `GET /api/routers` says whether it
loaded. Do not copy a `.ply`, `.png` or `cam0.jpg` under `web/landing/` — the site serves that folder to the world
(only `live/` is guarded).

## 4. What a change is — and what reads as clean

**Only inside the zones.** `room.git/room.yaml` (copied into every instance at `new`; metres, from the robot: x ahead,
y left, z up from the floor):

| zone | ahead (x) | sideways (y) | height (z) | `surface` |
|---|---|---|---|---|
| `desk` | 0.08 → 1.00 | −0.50 → +0.50 | 0.68 → 1.30 | 0.70 |
| `shelf` | 0.10 → 0.95 | +0.60 → +1.00 | 0.88 → 1.40 | 0.90 |

Everything else — the far wall, the floor, a person walking past at 3 m — is never segmented, so it can never be an
object change (it *is* in the cloud). Inside a zone an object is a cluster of at least 40 points whose longest side is
2–60 cm (`perception/cluster.py`), or a detector mask of that size.

**Then four layers keep an untouched room's object files byte-identical**, because a phantom diff is the failure that
makes the whole idea worthless:

| layer | number | source | what it absorbs |
|---|---|---|---|
| quantisation | position 0.01 m · yaw 5° | `room.yaml` `quantization:` | sub-centimetre jitter never reaches the file |
| hysteresis | 1.5 quanta = 1.5 cm · 7.5° | `hysteresis_quanta: 1.5` | a value sitting on a bucket edge (0.425 → 0.42 / 0.43) would otherwise flip every scan |
| the per-object call | centre moved < **5 cm**, same zone → the committed record, whole | `MOVE_M`, `roomctl/state.py` | single-view stereo wanders an untouched object's centre by up to 3 cm (measured, docs/10 P15) — past the deadband |
| the miss debounce | **2** consecutive misses with clear line of sight before `deleted` | `MISSES_TO_REMOVE`, `perception/associate.py` | one bad scan does not delete an object. An object hidden *behind* something is carried, not deleted |

(`room.yaml`'s `quantization:` block states the numbers; the code reads the same values as constants in
`roomctl/state.py`. Editing the yaml alone changes nothing.)

What follows, stated as costs: **a move or a turn under 5 cm is invisible. A removed object reads `deleted` on the
second look, not the first.** Matching against the last commit is hard-gated at 1.5 m, and `scan_into` passes no
history to `associate` — so an object that moved further than that in one go, or that left and came back, gets a new
id: one `deleted` plus one `untracked`, not a `modified`. None of this applies to `cloud/current.ply`: it is a
measurement, not a stabilised record, and differs on every capture.

**The noise floor — two captures, untouched hallway, robot not moved** (`--compare`; voxels of 6.25 cm, matched within
1.5 voxels ≈ 9 cm):

| | |
|---|---|
| exact voxel match | 32.3 % |
| within one voxel (9 cm) | **89.6 %** |
| unmatched within 2 m · beyond 2 m | 3.4 % · 12.1 % |

| distance from the robot | 0–1 m | 1–1.5 m | 1.5–2 m | 2–3 m | 3–6 m |
|---|---|---|---|---|---|
| unmatched after the one-voxel tolerance | **2.4 %** | 3.4 % | 3.6 % | 6.8 % | **18.1 %** |

**When the robot was turned between the two captures the same table collapsed to 40–60 % within one voxel.** Nothing
in the room had changed. That is what an untracked pose costs, and it is why the rule at the top is the rule.

The operating rules that follow:
- **Keep the scene within 2 m, ideally under 1 m.** Noise is 2.4 % inside 1 m and 18.1 % past 3 m. The desk zone ends
  at 1.00 m for this reason.
- **Never move the robot within an instance** — no nudge, no turn, nobody leaning on it. A reboot counts (§6).
- **"Clean on an untouched scene" is the acceptance test, not a given.** If `status` is dirty and nobody touched
  anything, measure before theorising: `capture_to_recording.py --n 2`, and hold the table against the one above.

## 5. The mount and the eye size — measured, not copied

**Nominal mount: pitch 38.1° down, height 1.59 m** (`MOUNT` in `scripts/capture_to_recording.py`). Solved from the
floor itself — level and zero the near floor, 0.25–1.6 m ahead — on three captures:

| capture | pitch | height |
|---|---|---|
| 1 | 38.06° | 1.585 m |
| 2 | 37.60° | 1.586 m |
| 3 | 38.57° | 1.588 m |
| | mean 38.08°, spread ±0.49° — the robot's balance wobble | |

bbos's own config says **33° / 1.55 m**. Those are right *for bbos's rectified frame*, not for ours: through our
rectification (`perception/depth.py`, the calibration's `R1`/`P1`) they leave the floor sloping up about 6° and about
5 cm low. `fuse.assert_floor` wants a plane within 10° of horizontal and the floor within 5 cm of z = 0 — so with the
copied numbers a scan passed or failed **depending on the wobble**, and one capture crashed with `no horizontal plane`
until the mount was corrected. With the measured mount all three captures pass at `floor_z` +0.2 to +0.6 cm.
Re-measure if the head is ever re-mounted. bbos's roll of −1° is not applied (measured residual roll: −0.1°).

**Then every capture levels itself** (`level()` in the same file). The robot balances, so its body pitch at the shutter
is never quite the nominal one: captures taken while it rocked (tilt rate ~0.05 rad/s) carried +2.3° of pitch error,
which at 2.3 m lifts the floor 9 cm — enough to read as an object. So each capture fits the floor just ahead
(0.25–1.6 m, ±0.9 m) and corrects **its own** pitch and height until that floor is level and at z = 0. Three guards:
a correction past ±6° / ±8 cm is not wobble and is **not applied**; too little floor in view and the nominal mount
stands; and the levelled mount is kept only if `fuse.assert_floor` accepts it over the *whole* cloud (seen: a mount right
for the near floor put the far, glossy floor at −5.5 cm). The result is written into `capture.json` — `mount` levelled,
`mount_nominal` and `levelled` beside it. Roll is measured and reported, never applied.

**Eye size: 1280×960, not upstream's 1280×720.** The frame is 2560×960. The calibration
(`perception/calib/stereo_calibration_fisheye.yaml`, copied from the robot) declares `image_width: 1280` /
`image_height: 960` and `perception/depth.py` reads it (`calib_size`). The size is taken from the calibration, never
from the frame — a guard that takes its answer from the thing it guards cannot fail.

## 6. When it goes wrong

| you see | it means | do |
|---|---|---|
| `AssertionError: no horizontal plane: cam_to_world_axes missing or applied twice, or the mount pitch is wrong` — or `lowest horizontal plane is at z=… m, not 0` | the floor is not where the mount says: no plane within 10° of horizontal, or not within 5 cm of z = 0. A wrong or stale mount, a re-mounted head, a capture the leveller could not fix. *Reasoned, not yet seen:* a view with too little floor in it — then the lowest horizontal plane is the desk top and the message says `z=+0.7…`. The check runs on every fuse, so `--check-desk` and `--compare` fail the same way (the clouds and models skip it) | look at the `level:` line of that capture (next row). Check `MOUNT` is 38.1 / 1.59 (§5). The mount is frozen into each `capture.json`: **re-capture, do not replay** an old recording. If it fails every time, re-measure the mount |
| `level: not enough floor in view just ahead of the robot — the nominal mount stands` · `level: the floor asks for … deg / … cm — that is not balance wobble; NOT applied` · `level: near floor asks for …, but the pipeline's whole-cloud floor check rejects it (…) — nominal mount kept` · `level: skipped (…)` | the capture is fine and kept; it was **not** levelled, and says why. The second one is the serious one: more than ±6° / ±8 cm is a mount or a floor problem, not wobble | first and third: nothing, unless the scan then fails the row above. Second: is the robot on a ramp, a mat, a cable? Was the head moved? Re-measure the mount |
| `ValueError: eye is (1280, 960), calibration is (1280, 720)` | the calibration does not declare this robot's eye size, so upstream's 720 was assumed. A yaml freshly copied from the robot may not carry the two lines | `perception/calib/stereo_calibration_fisheye.yaml` must say `image_width: 1280` / `image_height: 960`. Each recording carries its own copy (`cam0.yaml`): re-capture |
| `capture_rejected: … — the robot is still settling; retrying (1/3)` (HTTP 409) | the robot's gate refused the picture — it was tilting faster than 0.05 rad/s, or the latch was skewed — or it was `busy` with another capture. Not a fault. Retried three times, 1.5 s apart, never faked | wait ten seconds, run it again; nobody touching the robot. **Every** capture rejected = the IMU feed is dead (`telemetry_unfed` in Sentry; [`robot/RUNBOOK.md` §0–§1](../robot/RUNBOOK.md)) |
| `the robot REFUSES this laptop (forbidden: …): its address is not in ROBOT_ALLOW` (HTTP 403) | the laptop's address changed (DHCP, another wifi) and is no longer in the robot's allowlist | the message says `--start` refreshes it; **a restart alone does not change the allowlist** — `push_to_pi.sh` never writes the robot's `.env`. On the robot, edit `ROBOT_ALLOW` in `~/gitspace/.env` to include `ipconfig getifaddr en0`'s answer, *then* `./scripts/push_to_pi.sh bracketbot@<PI_HOST> --start` ([`robot/RUNBOOK.md` §4](../robot/RUNBOOK.md)) |
| `robot unreachable at <host>:8080 — …` · in `watch`: `no capture (robot moving, or unreachable) — trying again` | nothing answers: the laptop left the robot's wifi, the robot is off, its address changed, or `robot.server` is down | `python scripts/pi_link.py status`, then [`33` §6](33-robot-link.md). `watch` keeps trying by itself |
| every commit says ` no objects changed`, no `zones/` folder, `status` always clean | **0 objects.** The scan only looks inside the zones. In a hallway that is the correct answer. At a desk it means the robot is parked off the zone — no error anywhere, by construction | `capture_to_recording.py --check-desk` and do what it says. If the desk is right and the objects are small, see §7 |
| `status` dirty, nobody touched anything | in order of likelihood: the robot was nudged or turned · a person, a hand or a chair was inside the zone at the latch · the object sits on the 5 cm line · the scene is past 2 m | `capture_to_recording.py --n 2` and compare with §4's table. 40–60 % within one voxel = the robot moved: **new instance**. Near 90 % = the scene is fine; look again, people out of frame |
| `the first scan did not produce a commit (exit 1) — the error is above. No instance was created.` | the scan crashed — nearly always the first or third row. `room commit` exits 1 both for "nothing to commit" and for a crash, so `new` trusts only an actual commit, and removes the half-made repo | read the traceback above the message. The capture is kept (the message prints where): `capture_to_recording.py --check-desk <that dir>`. Fix, then `new` again — the same name is free |
| `no commit was made — the scan's error is above. The capture is kept: …` (exit 2) | the same, from `commit` / `snapshot`: HEAD did not move. `cloud/` has been put back to the last commit's | the traceback above it; rows one to three |
| `(could not stage the point cloud: …)` · `(no 3D model for this capture: …)` | the cloud or the render failed; the scan is unaffected and can still land, without its cloud | read the reason in the brackets. They share the scan's depth step, so a wrong eye size (row three) fails here too; they do **not** run the floor check |
| it all worked, then the robot was switched off and on | `robot.server` does not survive a reboot — no service is installed. The Sentry watcher (`scripts/robot_sentry_watch.py`, tmux `robot-watch`) files `robot_server_down`, then `… recovered after N s` | `./scripts/push_to_pi.sh bracketbot@<PI_HOST> --start`. A balancing robot that lost power did not stay where it was parked: re-run `--check-desk` and **start a new instance** |
| `not indexed (…) — the recording is kept: …` · `(SPOOLED, Elasticsearch away: …)` | Elasticsearch is away or its key is parked. The capture and the scan are unaffected | nothing now; the recording is on disk and indexing can be redone from it |
| `fatal: no room repository at …/rooms/<word>` | §2's trap: a git argument or a sha was read as an instance name | name the instance first; `cloud` wants `--ref` |

## 7. Limits, honestly

- **The pose is not tracked.** `pose_source: "none"`; `{0, 0, 0}` is a placeholder. Captures from different headings
  or positions cannot be compared or fused — measured: 89.6 % agreement becomes 40–60 %. Nothing downstream checks
  `pose_source`; the discipline is yours. It also means the clouds in `git log` only line up with each other while the
  robot stayed put. The fix is known and deliberately not guessed at ([`robot/RUNBOOK.md` §5](../robot/RUNBOOK.md)).
- **One viewpoint.** One capture from one spot sees the front of things. What is behind an object is unknown, not
  empty; the raycast carries a hidden object forward (`unobserved`) rather than deleting it, which is the honest
  answer and also means a removal behind an occluder is never seen.
- **A single camera.** The head stereo pair, `cam0`, is the only one wired into this flow. No second view to vote, no
  depth sensor to cross-check the stereo.
- **Glass and glossy floors.** Stereo matches texture. Glass has none of its own; a glossy floor shows a reflection,
  which stereo places *under* the floor. `fuse.assert_floor` tolerates 1 % of points more than 30 cm below z = 0 for
  exactly this reason, and fails the scan beyond it; the leveller has already seen a glossy far floor sit 5.5 cm low.
  Beyond that this is [`08` R3](08-risks.md)'s reasoning, **not measured on this robot**: how much a glass wall costs
  here is unknown.
- **Small objects.** Measured: a Red Bull can (5.3 × 13.5 cm) on the floor 1.24 m ahead, 2.08 m from the lens. The
  stereo geometry *resolves it at the right size* — points from −1.3 to +13.4 cm, 6 cm across. But it is about 8 × 19
  pixels: the image detector (YOLO-seg) finds nothing, and the desk-tuned clustering returns 49 specks of floor noise
  and not the can. The depth is good enough; the segmentation is not. A geometry-first floor-object segmenter is in
  progress; **it does not exist in this flow yet**, and the floor is not a zone — today the can is in the cloud and
  in no object file.
