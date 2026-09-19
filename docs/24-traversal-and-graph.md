# 24 — Traversing the room, and the git graph as a control surface

Two halves of one loop: the graph is **where you say what the room should be**, the traversal
is **how the robot gets there**.

---

# Part A — Traversing the 3D room

## A0. Scope: we compute WHERE, Bracket Bot's nav computes HOW

BB ships SLAM and navigation daemons ([`robot/README.md`](../robot/README.md)). **Do not
reimplement path planning.** Our job is the part their nav cannot do, because it does not know
what an object is or where an arm can reach:

```
  target object  ──►  OUR JOB: solve a BASE POSE   ──►  their nav: drive there
                      (reachable · collision-free ·
                       line-of-sight · path-reachable)
```

If their nav turns out to be unusable, the costmap in A2 is also a complete A* substrate —
but try theirs first.

## A1. The collision band — the non-obvious bit

The robot is ~1 m tall on two wheels; the arm works at table height. So an obstacle is **not**
"any occupied voxel":

```
 z ∈ [0.02, ROBOT_H]   → BODY BAND. These voxels block the base.
 z >  ROBOT_H          → overhead. Irrelevant to driving.
 z ∈ table top band    → the arm's workspace, NOT an obstacle to the base
```

A table is the case that makes this matter: its **pedestal blocks**, its **overhanging top does
not**. Treat the whole table as an obstacle and the robot can never get close enough to reach
anything on it — which presents as "the arm always reports unreachable" and sends you to debug
IK, which is the wrong file.

```python
def costmap_from_voxels(voxels, robot_h=1.0, cell=0.05, inflate=0.28):
    """Project the BODY BAND to a 2D grid, inflate by the robot's radius."""
    occ = {(round(v.x/cell), round(v.y/cell))
           for v in voxels if 0.02 < v.z_max and v.z_min < robot_h}
    return inflate_obstacles(occ, radius_cells=int(inflate/cell))
```

**As built** (`perception/costmap.py`): the band's top is `ROBOT_H = 0.60 m` — the part of
the robot as wide as the inflation — not the whole ~1 m robot. That is what makes the prose
above true: a 0.70 m table top sits above the band and doesn't block; its pedestal does.
With `robot_h=1.0` as written in the snippet, the top would be inside the band.

`inflate = 0.28 m` — half the 0.425 m wheelbase plus margin. **Inflate once, in the costmap**,
never per-query; a planner that checks robot geometry per node is slow and gets it wrong at
corners.

## A2. Solving a base pose — an annulus, not a point

The SO-101 reaches an **annulus** around its mount, at roughly table height. So "where do I
stand to pick this up" is: sample the annulus, keep what survives four filters.

```python
def solve_base_pose(target_xyz, costmap, arm, robot_pose):
    cands = []
    for theta in np.linspace(0, 2*np.pi, 36):          # 10° steps
        for r in np.linspace(arm.r_min, arm.r_max, 5):
            bx = target_xyz[0] - r*np.cos(theta)
            by = target_xyz[1] - r*np.sin(theta)
            yaw = theta                                 # face the target
            if costmap.occupied(bx, by):          continue   # 1. base fits
            if not arm.reachable(target_xyz, (bx,by,yaw)): continue  # 2. IK exists
            if not line_of_sight(bx,by, target_xyz, costmap): continue # 3. can SEE it
            path = costmap.astar(robot_pose, (bx,by))
            if path is None:                      continue   # 4. can GET there
            cands.append(((bx,by,yaw), cost(path, r, theta, robot_pose)))
    return min(cands, key=lambda c: c[1])[0] if cands else None
```

Rank by: **path length**, then **how centred the target is in the reach annulus** (mid-reach
poses have the most IK slack, so a small pose error still solves), then **least turning**.

Returning `None` is a real answer — it is the `cannot apply hunk` case from
[`04-git-semantics.md`](04-git-semantics.md), and it should be reported, not retried forever.

## A3. Viewpoint planning — this is what resolves occlusion

[`11-elastic.md`](11-elastic.md) says an object that vanished may be *occluded* rather than
*removed*, and that we should "drive to a second vantage point before declaring a deletion."
This is that function, and it is the same annulus solve with a different objective:

```python
def solve_viewpoint(last_known_xyz, costmap, blocked_from):
    """Find a pose with clear line of sight to a point, from a DIFFERENT angle."""
    return best(pose for pose in ring(last_known_xyz, r=0.8..1.6)
                if line_of_sight(pose, last_known_xyz, costmap)
                and angular_separation(pose, blocked_from) > radians(45))
```

The `> 45°` term matters: a second look from nearly the same angle is blocked by the same
object and tells you nothing. **This closes the loop between navigation and the Elastic
occlusion query** — the agent decides it needs another look, and traversal provides it.

## A4. Line of sight is a raycast on the same grid

```python
def line_of_sight(a, b, costmap):
    """Bresenham/DDA across the voxel grid. Also used by the occlusion verdict."""
```

One implementation, two callers: base-pose filtering and occluded-vs-removed. Put it in
`perception/raycast.py` so neither owns it.

## A5. Latency tier
Path and pose solving are **planning tier** — ~100 ms, once per operation, before motion
starts. They may query Elasticsearch for the voxel grid. They must **never** run per control
cycle. See [`11-elastic.md`](11-elastic.md#which-queries-may-touch-elasticsearch-latency-tiers).

---

# Part B — The git graph as the control surface

## B1. The idea

`git log --graph` for a room, rendered in the browser, **where clicking a node moves a robot.**

That is the demo. A judge clicks a commit from twenty minutes ago and the arm starts putting
objects back. No CLI, no explanation needed.

## B2. Data — git is the source, Elasticsearch is the enrichment

```
git log --all --format="%H|%P|%D|%ct|%s"      → sha, parents, refs, time, subject
      │
      └─► per node, enrich from room-events / room-objects:
            objects_added / moved / removed      (what changed)
            zone                                  (where)
            capture_id + sentry_trace_id          (→ the /capture page, → the trace)
            quality_ok                            (was this capture trustworthy?)
```

**`quality_ok` on a graph node is the detail worth building.** A commit built from a rejected
capture renders differently — and clicking through to `/capture/<id>` explains why. The graph
stops being a log and becomes a diagnosis.

## B3. Layout — do not reach for a graph library

A commit DAG is nearly a tree and the standard railroad layout is ~40 lines:

```
lane assignment: walk commits newest→oldest; a commit inherits its first child's
lane; additional parents open a new lane; lanes free when their branch merges.
x = lane * LANE_W        y = row * ROW_H        edges = bezier between (x,y)
```

Inline SVG, no dependency. A general force-directed graph layout will look worse and will not
respect time order, which is the one thing a commit graph must do.

## B4. The manipulations — every one is a real git command

| gesture | git | robot |
|---|---|---|
| **click a node** | `git show` | — preview: ghost-render that state in Rerun |
| **"Revert to here"** | `git revert <sha>` | **executes** — diff → base poses → motion |
| **"Check out this branch"** | `git checkout <ref>` | **executes** |
| **drag node A onto B** | `git cherry-pick <A>` | **executes** — apply one object's move |
| **click two nodes** | `git diff <a> <b>` | — shows the object-level diff between any two moments |
| **"Merge"** | `git merge <ref>` | conflict → both candidate positions ghosted, `--ours`/`--theirs` |
| **scrub the timeline** | — | Rerun scrubs; nothing physical moves |

**Preview before execute, always.** Clicking a node shows the ghost first; a second, explicit
click runs it. A robot that starts moving on a single click is a robot that moves when someone
brushes the trackpad.

## B5. The API

```
GET  /api/graph?limit=100        → {nodes:[{sha,parents,refs,ts,subject,changed,quality_ok,
                                            capture_id,sentry_trace_id}], head, branch}
GET  /api/diff?a=<sha>&b=<sha>   → object-level ops between any two commits
POST /api/command                → {command:"revert"|"checkout"|"cherry-pick", args:{ref}}
                                   → {job_id, ops, estimated_s}   (allow-listed, see 16-api)
```
Live updates over the existing SSE `/api/events`: a `capture` event appends a node, a `job`
event animates the node being applied.

## B6. What makes it worth building
- It is the **only** interface where a version-control graph is also a physical control panel.
- It hands a judge the whole concept in one gesture, with no narration.
- `quality_ok` + `sentry_trace_id` per node means the graph links straight into both prize
  stories: a suspicious commit → the capture page → the Sentry trace.

## B7. Build order
1. `GET /api/graph` from real `git log` (works today against `room.git`)
2. SVG railroad layout, read-only
3. Enrichment: changed objects, `quality_ok`, links to `/capture/<id>`
4. Click → preview (ghost in Rerun)
5. Revert / checkout → `POST /api/command` → robot
6. Cherry-pick by drag, and the merge-conflict panel — **T3, only if it survives**
