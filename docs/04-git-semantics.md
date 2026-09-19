# 04 — Git semantics: the room as a working tree

## The design bet

**We use real git.** Not a git-inspired data model, not a re-implementation — an actual
`.git` directory on disk, driven by actual `git` commands, holding actual text files.

The room's state is serialized to a tree of YAML files. Everything after that is free:
diffing, three-way merge, conflict markers, `log --graph`, `blame`, `bisect`, `stash`,
remotes, GitHub's rendered diffs, pull requests. We write **two** pieces of software —
the thing that turns the world into files (perception), and the thing that turns a file
diff into robot motion (actuation). Git is the entire middle.

Consequences worth internalizing:

- A physical **merge conflict** is not something we implement. It's what git *already does*
  when two branches modify the same lines of `zones/desk/cup_a1b2.yaml`. We just have to
  show it and decide what the robot does about it.
- The room **is pushable to GitHub**, where the diff renders in a browser, and a **pull
  request against physical space** becomes a real, clickable, reviewable artifact.
- We get `git log --graph --oneline` of a room for free, which is a beautiful thing to
  put on a projector.

## Repository layout

```
room.git/
  .roomignore              # like .gitignore: person, robot, cables, zones/floor/**
  room.yaml                # units, quantization, anchor tag id, zone definitions
  anchors/
    tag_0.yaml             # anchor tag pose — the origin of the world frame
  zones/
    desk/
      cup_a1b2.yaml
      marker_c3d4.yaml
    shelf/
      book_e5f6.yaml
    floor/
      ...
  snapshots/
    <commit>.ply           # (optional) the raw fused cloud — see "binary blobs" below
```

An object file, deliberately boring and line-oriented so diffs are readable. **Frozen** —
the schema, its rules and the field table live in [`roomctl/state.py`](../roomctl/state.py),
and `tests/test_state.py` pins these exact bytes:

```yaml
id: mug_a1b2
class: mug
zone: desk
pose:
  x: 0.42          # metres, world frame, bbox centre, 1 cm quanta, always two decimals
  y: 0.18
  z: 0.76
  yaw: 15          # the AXIS of extents.x: integer degrees in [0, 180), 5-degree quanta
extents:           # x = length along the yaw axis, y = width, z = height; metres
  x: 0.12
  y: 0.09
  z: 0.11
color: "#2b4c7e"   # identity field: fixed at first sight, like class and first_seen
first_seen: "2026-09-18T14:12:33Z"
```

**What is deliberately *not* in the file:** `confidence`, `observed_by`, `point_count` and the
VLM descriptions. All four change between two scans of an untouched room, so any of them
here is a phantom diff on every scan ([`20-perception-logic.md`](20-perception-logic.md) Part 5).
They live in Elasticsearch, where the mess belongs.

**Why one file per object:** git's three-way merge is per-file. One object per file means
two branches that move two *different* objects merge cleanly and automatically, while two
branches that move the *same* object conflict. That is precisely the semantics we want, and
we get it purely from the file layout. **Why one field per line:** so `git diff` prints
`-  x: 0.42` / `+  x: 0.61` and a judge reads the physical world in unified-diff format.

**Why zones are directories:** it makes `git add zones/desk/` a *spatial* staging
operation, `git log -- zones/shelf/` the history of one shelf, and `.roomignore` a
spatial filter. The metaphor stays load-bearing instead of decorative.

### Binary blobs
Storing the full point cloud per commit is tempting (time travel! rendering old rooms!)
but it bloats the repo and makes diffs unreadable. Compromise: store a **downsampled**
cloud (or a single stitched panorama JPEG) per commit under `snapshots/`, and keep the
diffable truth in YAML. If the repo gets ugly, drop blobs entirely — the YAML is the product.

## The command table

This is the product surface. Print it and stick it on the table at the demo.

| command | physical meaning | who acts |
|---|---|---|
| `room init` | scan, define zones, place anchor tag, first commit | robot observes |
| `room status` | diff(observed now, HEAD) — "what's been moved since the last commit" | robot observes |
| `room diff` | the literal `git diff`, rendered with object names | — |
| `room add zones/desk/` | stage only the desk's changes | — |
| `room commit -m "clean bench"` | record the room as it is now | robot observes |
| `room log --graph` | history of the room | — |
| `room checkout <branch>` | **make the room match that branch** | **robot acts** |
| `room revert HEAD` | **undo the last change to the room** | **robot acts** |
| `room reset --hard` | **discard all uncommitted changes to the room** | **robot acts** |
| `room merge <branch>` | combine two layouts; conflict if both moved one object | **robot acts** |
| `room cherry-pick <c>` | apply one object's move from another timeline | **robot acts** |
| `room blame zones/desk/cup_a1b2.yaml` | when did this cup last move, and in which commit | — |
| `room stash` | robot sweeps loose objects into a bin, remembers where they were | **robot acts** |
| `room tag v1.0-clean` | name a layout | — |
| `room push` | publish the room's state to GitHub | — |
| PR against `main` | *"please move the coffee machine"*, reviewed by a human, executed on merge | **robot acts** |

The asymmetry in that table is the product: **read commands are perception; write
commands are motion.** Say that sentence in the demo.

## Applying a diff to physical space

`git` produces a target tree. We produce the room. The executor is a loop:

1. `git diff <observed-tree> <target-tree>` → a list of object-level operations.
2. Classify each op: `MOVE(id, from, to)`, `ADD(id, to)`, `REMOVE(id, from)`.
3. **Order them.** Naively executing in file order will fail: you can't place a mug where
   a book currently sits. Build a dependency graph — if target(A) overlaps current(B),
   B must move first. Cycles (A→B's spot, B→A's spot) need a **temporary staging
   position**, which is delightfully exactly what `git stash` means. Getting this right is
   real, legible technical depth; call it out to judges.
4. For each op: navigate to a reachable base pose → top-down grasp → transport → place →
   **verify by rescanning**.
5. **Verify-and-retry:** after the batch, rescan and `git status`. If it's not clean,
   report honestly and optionally retry once. *A robot that says "I moved 2 of 3 objects,
   the third slipped" is more impressive than one that claims success and is wrong* — and
   it's the "honest handling of ambiguity" that serious judges reward.

### Ops we cannot perform
`ADD` of an object that isn't in the room, or `REMOVE` of an object with nowhere to put
it, may be physically impossible. Don't fail silently — surface it as an unmergeable /
unapplied hunk, exactly like git refusing to apply a patch:

```
error: cannot apply hunk: object 'scissors_9f3a' not present in room
hint: 1 hunk could not be applied. Human intervention required.
```

That output is *funny and correct at the same time*, which is the best kind of demo line.

## Merge conflicts, physically

Two branches, `main` and `movie-night`, both move `cup_a1b2`. `git merge` conflicts on
that file. The robot:

1. Stops. LED strip goes **red**.
2. Announces (ElevenLabs/Kokoro): *"Merge conflict. The mug was moved in both branches."*
3. Prints the actual conflict markers, and shows **both candidate positions in Rerun**
   as two ghosted boxes — this is the money shot; make sure the Rerun blueprint has a
   dedicated view for it.
4. Waits for `--ours` / `--theirs` (or a voice answer), then executes the resolution.

Everything except the ghost rendering is stock git behaviour. Stage this beat carefully;
it is the single most memorable thing in the demo.

## Semantics worth arguing about (and our answers)

- **Is `commit` an observation or an instruction?** Observation. `commit` records reality.
  Only `checkout`/`revert`/`reset`/`merge` change reality. Keeping this strict is what
  makes the whole thing coherent.
- **What is the "working tree"?** The room itself. The scan is how we `stat` it. This means
  the working tree can change without us touching it — the room is a working tree that
  other people edit with their hands. `git status` becomes genuinely informative.
- **Dirty vs. untracked:** a moved known object is *modified*; a brand-new object nobody
  has committed is *untracked* and shows in `status` under "untracked objects". Matches
  git exactly. Free correctness.
- **What about `.roomignore`?** Things that move constantly and shouldn't count: people,
  the robot itself, chairs, cables, anything in `zones/floor/`. This is genuinely necessary
  to stop `status` being permanently dirty — and it's a nice concept to name out loud.
