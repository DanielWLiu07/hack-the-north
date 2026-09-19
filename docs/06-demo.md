# 06 — The demo

**This document is the spec. Build backwards from it.** Hack the North says explicitly:
*"Your judging pitch should be a live demo, not a slide deck or a product pitch."*

Target: **3 minutes**, with a 60-second version for judges who are drifting past, and a
6-minute version for the one who stays. Rehearse all three out loud on Saturday night.

## The setup on the table

- A table with a **textured tablecloth** (see [`02-hardware.md`](02-hardware.md)).
- 5–6 curated objects at known committed positions.
- Anchor AprilTag on a rigid board, visible to the robot, taped down.
- Laptop screen (or projector) showing **Rerun** split with a **big-font terminal**.
- LED strip on the robot mirroring repo state: green / amber / red.
- The [command table](04-git-semantics.md#the-command-table) printed and laid on the table.

## The 3-minute run

**[0:00] The hook — say this before anything else**
> "This is a room under version control. Not a simulation of one — there's a real git
> repository on this laptop, and the working tree is that table."

**[0:15] `room status`** — LED green, terminal says working tree clean.
Rerun shows the fused point cloud with labelled boxes. Let them look at it for 3 seconds.

**[0:30] Hand a judge an object.**
> "Move something. Anything. Put it wherever you want."

This is the most important five seconds of the demo. It proves there's no script, no
pre-recorded state, no cheating. **Make the judge the one who changes the world.**

**[0:45] `room status`** — LED amber.

```
On branch main
Changes not staged for commit:
        modified:   zones/desk/mug_a1b2.yaml
        deleted:    zones/desk/marker_c3d4.yaml
Untracked objects:
        zones/desk/scissors_9f3a.yaml
```

**[1:00] `room diff`** — the literal unified diff of physical reality:

```diff
--- a/zones/desk/mug_a1b2.yaml
+++ b/zones/desk/mug_a1b2.yaml
 pose:
-  x: 0.42
+  x: 0.61
-  yaw: 15
+  yaw: 40
```

In Rerun, the mug's box turns amber with an arrow from its old pose to its new one.
Land the line: **"That's not a visualization of a diff. That's `git diff`."**

**[1:20] `room revert HEAD`** — or, better, *say it*: **"Put the desk back the way it was
before dinner."** The agent runs an ES|QL time query over `room-events`, resolves it to a
commit sha, and calls `room_checkout`. Same robot motion, better line, and it demos the
OpenAI voice stack, the Rox agent and Elastic in one sentence — at ~10 seconds of marginal
cost, because it replaces a command we were running anyway.

LED pulses, robot speaks, drives over, picks up the mug,
places it back at the committed pose. Narrate while it moves (it takes 30 s; fill it):
> "It's computing the operation order right now — you can't put the mug back if something
> else is in its spot, so it topologically sorts the moves. Same problem as applying a
> patch out of order."

**[2:00] `room status`** — clean. LED green. The room matches HEAD.
*This is the emotional peak. Pause. Let it land.*

**[2:10] "Where did I leave my keys?"** — the most relatable 15 seconds in the demo:
```
room search "where did I leave my keys"
→ keys_7c2e — last seen zones/shelf, 14:22, commit a3f9c1 (2 commits ago)
```
...and the robot drives over and **points at them**. Hybrid search over the room's whole
history. This beat survives even if grasping never works (fallback rung 2), so it's cheap
insurance as well as a crowd-pleaser.

**[2:25] The conflict** (only if it's solid — see fallbacks):
```
room checkout -b movie-night
# (rearrange, commit)
room checkout main
room merge movie-night
CONFLICT (content): both branches moved zones/desk/mug_a1b2.yaml
```
LED red. Robot: *"Merge conflict. The mug was moved in both branches."*
Rerun shows both candidate positions as ghosts. `room merge --theirs` → robot executes.

**[2:40] The close**
> "Rooms have state. Git is how we already think about state that changes. So we gave a
> room a HEAD, a history, and a robot that can write to the working tree."

Optional final flourish if the push works: `room push`, then open GitHub in a browser
and show the room's diff rendered on github.com. **"My room is a repository."**

## The 60-second version
Hook → judge moves an object → `room status` → `room diff` → `room revert HEAD` → clean.
Drop branching, merging, push, voice. Nothing else.

## Fallback ladder

Decide these **before** judging, not during. Each rung is still a good demo:

1. **Full demo** as above.
2. **Grasping is unreliable** → replace `revert` execution with the robot *driving to the
   moved object and pointing the arm at it* while announcing the diff. Reframe the pitch
   around `status`/`diff`/`blame`: "the room tells you what changed." Still novel, still wows.
3. **Navigation is unreliable** → keep the robot stationary; demo on a single table within
   arm's reach. Diff + arm-based revert, no driving.
4. **Live perception is unreliable** → run against a recorded Rerun session (`.rrd`) of a
   good scan, and be *upfront* that it's a recording. Honesty costs less than a crash.
   Still show the live arm doing a pick-and-place from a hand-authored diff.
5. **Everything is on fire** → the git layer alone, driven by hand-authored scene YAML,
   with the robot executing. The git-as-physical-state idea survives on its own.

Keep a **known-good `.rrd` recording and a known-good `room.git`** from the moment the
first end-to-end run works. Tag it. Never let the only working version be the one
you're currently editing.

## Things that will make a judge remember us

- Letting **them** move the object.
- The unified diff of physical space on a big screen.
- The LED going amber the instant the room diverges from HEAD.
- The robot saying **"merge conflict"** out loud.
- `cannot apply hunk: object 'scissors_9f3a' not present in room`.
- The Rerun timeline scrubbing backwards through the room's history.
- Asking where your keys are and having a robot go point at them.

## What to submit on Devpost
- Source link (the code repo **and** a sample `room.git` — the room repo is itself a great
  artifact to link).
- Demo video: the 60-second version, one unbroken take, judge's hands visible.
- **Select sponsor prizes before 2:00 PM EDT Saturday** — hard deadline, easy to miss.
  See [`07-prizes.md`](07-prizes.md) and decide by Saturday noon.
