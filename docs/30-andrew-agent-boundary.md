# 30 — Compatibility with `gitirl-agent` (Andrew)

Repo: **https://github.com/awzheng/gitirl** — "Thin edge/integration bridge between GitIRL's
cloud backend and robot-side software."

His architecture puts himself between us and the robot:

```
  Daniel's cloud/backend                 ← us (roomctl, elastic, web, agent)
        ↓ high-level command
  gitirl-agent edge bridge               ← Andrew
        ↓ RobotAction through RobotAdapter
  Ryan/Sarah's robot stack
```

---

## 1. He is BLOCKED on us, right now

His README, last line:

> *"The WebSocket and camera protocols are **provisional until Daniel provides authoritative
> contracts**. The camera scaffold is deferred unless the team confirms that media should pass
> through this process."*

**We already wrote both.** Send him:
- [`docs/16-api.md` §3b](16-api.md) — the frames WebSocket: binary framing, `capture_id`
  grouping, atomic captures, two sockets split by latency class
- and the answer to his open question: **no, media does not pass through his process.**
  Frames go Pi → laptop directly ([`22-camera-sync.md`](22-camera-sync.md)); he receives
  structured state, never pixels.

He has a slot for it: `src/gitirl_agent/protocol/`.

---

## 2. The real overlap — our `executor.py` and his planner do the same job

| his README says he owns | our equivalent |
|---|---|
| "deterministic diff → small deterministic plan → RobotAdapter → re-observe → verify → retry at most twice" | `roomctl/executor.py`, 470 lines: diff → ordered ops → **dependency graph → topological sort** → robot → verify by rescan |
| "conservative diff-to-action translation" | the same translation |
| "local restore, diff, commit" | `roomctl/cli.py` — `restore`, `diff`, `commit` (and the graph-native `revert`, `checkout`, `reset --hard`) |

**Two implementations of the same pipeline.** This has to be settled, not discovered on
Sunday.

### The split that respects both specs

Draw the line at **what each side has access to**, which makes it obvious:

```
US (cloud)                              ANDREW (edge)
observe → diff → ORDERED OPS      →     ops → RobotAction → adapter
                                        → re-observe → verify → retry ≤2
```

- **Ordering is ours** because it needs the **voxel occupancy grid in Elasticsearch** — you
  cannot place a mug where a book still sits, and the collision check is a `shape` query on an
  index he explicitly does not own ("does not own: Elastic"). Cycles need a staging position
  ([`24` A2](24-traversal-and-graph.md), [`04`](04-git-semantics.md)).
- **Execution, verification and retry are his** because they belong close to the robot —
  which is exactly the rule his README states: *"Do not move logic into gitirl-agent unless it
  benefits from being close to the robot."* Retry latency does.

So `roomctl/executor.py` keeps the graph and **stops before the adapter**, emitting an ordered
op list instead of calling the robot. That is a small change and it deletes a whole class of
merge conflict.

---

## 3. Two contract mismatches to fix now

### a. Vocabulary — RESOLVED, see [`ANDREW-HANDOFF.md`](../ANDREW-HANDOFF.md) §1
| his | ours |
|---|---|
| `{"command":"restore","target_state":"study"}` | `room restore study`: `git restore --source=study --staged --worktree -- zones` + a commit on HEAD, then the robot |

`restore` is **not** `revert` (undo one commit) and **not** `checkout` (move HEAD): the three
leave different rooms from the same history (`tests/test_cli.py::test_revert_is_not_restore`).
His `target_state` is a **named state**; ours is a **git ref**. Git refs are strictly more
expressive (a sha, a branch, `HEAD~3`, a tag) and named states are a subset — `study` is just
a tag. **Decided: `target_state` accepts any git ref**, and a name resolves as a tag.

### b. ADD and REMOVE become conflicts
> *"Only `MOVED` currently produces `MOVE_OBJECT`. Missing, added, relational, and unknown
> differences become conflicts instead of guessed behavior."*

That is a **good conservative default** and it matches our own
`cannot apply hunk: object not present in room` behaviour. But the demo's `room status` output
contains a `deleted:` and an `untracked:` line, so those paths get exercised on stage.

**Agree explicitly:** ADD/REMOVE return a structured conflict, we render it as an unapplied
hunk, and the robot says so out loud. Honest partial success is a demo beat, not a failure —
but only if both sides expect it.

---

## 4. Type mapping

His `WorldState` / `StateDiff` against our `ObjectRecord` ([`roomctl/state.py`](../roomctl/state.py)):

```
ours                              his
id            (git side)     →    object identifier
class, zone                  →    ---
pose {x,y,z,yaw}  METRES     →    target pose. Confirm units AND frame:
extents {x,y,z}                   we are X-forward, Y-left, Z-UP, floor z=0 (docs/20)
```

**The frame is the trap.** Ours is Z-up because the Elasticsearch `cell`/`z_min`/`z_max`
schema requires it; Bracket Bot's own code is Y-down. If his `RobotAction` poses are in the
robot's native frame, the conversion belongs at **his** adapter boundary — but it must be
written down, because a silent Y/Z swap puts the arm 90° off and looks like an IK bug.

---

## 5. What to send him, in one message
1. `docs/16-api.md` §3b — the authoritative WebSocket contract he is waiting on
2. "media does **not** pass through your process" — the camera scaffold stays deferred
3. The executor split in §2 — we emit ordered ops, he owns adapter/verify/retry
4. `target_state` accepts a git ref
5. The Z-up frame, and that conversion lives at his adapter

None of it is more than a paragraph, and all five are cheaper now than at hour 30.
