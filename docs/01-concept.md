# 01 — The concept

## The idea in one paragraph

A room has state, and that state changes constantly, and nobody tracks it. Git is the
universal grammar humans already have for *state that changes over time, with history,
branching, blame, and undo*. If you can (a) **observe** room state reliably, (b)
**serialize** it into something git can diff, and (c) **actuate** a diff back onto the
physical world, then the entire git command surface comes along for free — `log`,
`status`, `diff`, `commit`, `branch`, `checkout`, `revert`, `merge`, `blame`, `stash`,
`cherry-pick`, even `push` and pull requests. The robot is the working-tree writer.
We are not building a git-*like* system for rooms. We are making a room into a git
working tree.

## Why this is the right shape for this hackathon

Judging is on **wow factor, technical ability, originality, design**, judged as a **live
demo**. This idea is unusually well-shaped for that:

- **Wow is instant and needs no explanation.** A judge moves a cup. We type `git status`.
  Terminal says the working tree is dirty. We type `git revert HEAD`. The robot drives
  over and puts the cup back. There is no slide that needs to precede this.
- **The technical claim is legible.** Every judge in that room knows exactly how hard
  `git merge` is conceptually and exactly how hard "pick up a cup" is physically. Putting
  them in the same sentence does the pitching for us.
- **Originality is defensible.** The literature has robot change detection (POCD,
  OASIS-Map, 3D VSG, SceneDiff) and virtual scene versioning (SceneGit, MeshGit). Nobody
  has closed the loop with real git plumbing and a physical executor. See
  [`research-notes.md`](research-notes.md#prior-art-and-how-we-differ).
- **It degrades gracefully.** Even if grasping never works, "robot detects the diff, drives
  to the changed object, and announces it" is still a complete demo. See
  [scope tiers](#scope-tiers).

## The non-obvious bit that makes it work

**Commits are discrete events, not a video stream.** We do not need 30 FPS 360° depth.
We need a good snapshot at `commit` and `status` time — roughly once every 30 seconds,
on demand. That single reframing kills most of the compute and bandwidth problems:
we can round-robin the cameras, run heavy models off-board, spend two full seconds on a
capture, and still have a snappy demo. Design everything around **the shutter click**.

## Why 360° / three cameras (the actual argument)

Bracket Bot ships with one forward fisheye stereo pair. We add two more for ~360°.
The reason is not "more pixels" — it's:

1. **A commit should be atomic.** With one camera the robot must spin and stitch, which
   takes time and injects odometry drift into the snapshot, which shows up as *phantom
   diffs* — the single worst failure mode in this project (see
   [`03-perception.md`](03-perception.md#the-real-enemy-phantom-diffs)). One shutter click
   over 360° means one commit = one instant.
2. **The arm needs to see its own workspace** while the drive camera looks where it's
   going. A side/down-tilted pair covering the manipulation envelope is functionally a
   wrist camera we don't have to mount on the arm.
3. It's the thing that makes "scan the whole room" a 3-second operation instead of a
   30-second one, which matters enormously in a 3-minute demo.

## Why it isn't only a toy

Worth having one honest real-use answer ready, because a judge will ask. Strongest first:

- **Film / photo set continuity.** "Revert the set to the state it was in at take 3" is
  a real, expensive, currently-manual job. This is the most persuasive framing; it makes
  the *revert* the product rather than a party trick.
- **Lab, makerspace, and workshop benches.** Commit the bench at end of day; `git status`
  in the morning tells you what walked off; `git blame` tells you which session moved it.
- **Retail planogram compliance / warehouse 5S audits.** "Does the shelf match the
  committed layout?" is literally `git status` on a shelf.
- **Turnover ops** — hotel rooms, Airbnb, hospital OR trays, rental equipment.

Don't oversell these. The Hack the North finalist brief explicitly says projects
*"don't need to become a real startup with a business plan."* Lead with the demo, keep
one real use case in the back pocket.

## What we are NOT building

Stating this now saves arguments at 3am:

- Not a general-purpose grasping system. Curated object set, top-down grasps. See
  [`08-risks.md`](08-risks.md).
- Not a SLAM contribution. We use an AprilTag anchor + wheel odometry + ICP refinement.
  Good enough, boring, works.
- Not real-time tracking. Snapshots at commit time only.
- Not a web app. This is CLI-first and Rerun-first. (Which also happens to be exactly what
  the Warp prize asks for.)

## Scope tiers

Build strictly in this order. Each tier is independently demo-able. **Do not start tier
N+1 until tier N has been demoed to a stranger.**

### T0 — "the loop exists" (must have, target: hour 12)
Single forward stereo cam. One tabletop zone. Real git repo of object YAML files.
`commit`, `status`, `diff`, `log` all working against real git. Rerun showing the point
cloud + detected objects + diff highlighting. Robot **drives to the moved object and
points/announces it** (no grasping). *This alone is a demo we would not be embarrassed by.*

### T1 — "the robot writes" (target: hour 22)
Arm does a top-down pick-and-place for 1–2 curated objects. `revert` and `checkout`
physically execute. This is the moment the project becomes the thing we pitched.

### T2 — "the commit is atomic" (target: hour 28)
Second and third stereo pairs; cross-camera extrinsic calibration; single-shot 360°
capture; multi-zone repo layout (`zones/desk/`, `zones/shelf/`).

### T3 — "the theatre" (target: hour 32)
`branch` / `checkout` between named layouts. A staged **physical merge conflict**. Voice
control via the OpenAI Realtime example that already ships in the BB quickstart. GitHub
pull request against the room. Pick from these by whatever is working; they are all
independent.

### T4 — "if we're somehow ahead"
`git stash` (robot sweeps the table into a bin and remembers what it took).
`git push` of the room repo to GitHub so the arrangement is publicly clonable.
`git clone` a room layout into a different room.

## Naming

Working name **GITSPACE**. Alternates, roughly in order of how much they'd make a judge
smile:

- **`HEAD~1`** — "revert your room one commit." Best tagline, awkward as a folder name.
- **GITSPACE** — clear, brandable, reads like Gitpod/Codespaces (slight confusion risk).
- **WORKTREE** — accurate (the room *is* the working tree), a bit inside-baseball.
- **`git reset --hard`** — great as the demo's last line, bad as a name.

Tagline to put on the Devpost and say out loud first: **"git for the room you're standing in."**
Closing line of the demo: **"`git revert HEAD` — and the room actually changes."**
