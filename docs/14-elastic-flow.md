# 14 — The full Elasticsearch flow

Every ES operation in the system, in the order it actually happens.
Shape-of code — verify syntax against live docs.

---

## Phase 0 — Setup (once, tonight)

```
PUT _inference/text_embedding/jina-embed      jina-embeddings-v3   (or EIS)
PUT _inference/rerank/jina-rerank             jina-reranker-v2

PUT room-objects                              mapping: semantic_text, point, keywords
PUT room-voxels                               mapping: point, octree keywords
PUT room-clouds                               mapping: catalog of .ply files on disk
PUT _index_template/room-observations         TSDS · dims object_id,camera · look_back 7d
PUT _index_template/robot-telemetry           TSDS · dim signal · look_back 7d
PUT _index_template/room-events               plain data stream
```

**Gate:** index 30 objects from the fake scene generator, run one hybrid search, get hits
back. After that the whole Elastic track is unblocked from the robot.

---

## Phase 1 — Continuous ingest (always running)

Two background writers, independent of any command:

| stream | rate | write |
|---|---|---|
| `robot-telemetry` | 50 Hz × 8 signals ≈ **400 docs/s** | buffer 1 s → one `_bulk`, `op_type: create` |
| `room-observations` (watch loop) | 1–2 Hz × ~45 detections ≈ **45–90 docs/s** | one `_bulk` per frame |

The watch loop is the cheap path: single forward camera, stock YOLO, no fusion, no labelling,
no git. It exists so the observation history is *dense* — 40 continuous sightings make the
occlusion decision in Phase 2 far more reliable than 3 snapshots would.

---

## Phase 2 — `room status` / `room commit`

A judge moves the mug. Here is every ES call from that moment to a git diff on screen.

```
 1. capture 3× stereo, latched together (docs/22)          [no ES]
 2. rectify → SGBM → fuse → world frame                     [no ES]
 3. plane removal → Euclidean clustering                    [no ES]
 4. VLM describes each cluster (3 views → 3 descriptions)   [no ES]

 5. ES  ── RE-IDENTIFY ───────────────────────────────────────────────────
    for each cluster: hybrid search over ALL history
      rrf([ BM25 on class, semantic on raw_description ]) → jina rerank
      + filter: within 1.5 m of the cluster centroid (shape query on `cell`)
    → "this is mug_a1b2, last seen 2 commits ago" → stable object_id carried forward
    → without this, an object that left and returned gets a NEW id and its history breaks

 6. ES  ── RESOLVE DISAPPEARANCES ────────────────────────────────────────
    for each object in HEAD that this scan did not see:
      observation_history(object_id, last 40 captures) on room-observations
      + aggregate: which cameras ever saw it, confidence trend, occlusion flags
    → OCCLUDED  → carry forward from HEAD, write nothing
    → DELETED   → write the deletion
    → this is the query that changes what the robot physically does

 7. quantize → deterministic YAML                           [no ES]
 8. git add / git commit                                    [no ES]

 9. ES  ── WRITE THE SNAPSHOT (roomctl/publish.py, after the commit: every _id needs its sha)
    bulk room-objects   ~30 docs    _id = f"{sha}:{object_id}"   from the COMMITTED tree
    bulk room-voxels    ~3000 docs  _id = f"{sha}:{voxel_key}"   voxelize.index_staged
    index room-clouds   1 doc       cloud_uri + coverage + icp_residual   (perception, D9)
    create room-events  1 doc       event_type=commit, commit_sha, parent_sha, branch
    ES away or key parked → spooled in .git/gitspace/spool; `room publish --flush` later

10. git diff → terminal + Rerun                             [no ES]
```

**Note what's local and what's remote.** Steps 1–4 and 7 never touch the network. ES enters
only where the question is *"what do we know from history?"* — which is exactly the read-path
role.

---

## Phase 3 — `room search "where did I leave my hammer"`

```
 1. ES  ── HYBRID SEARCH, NO COMMIT FILTER ───────────────────────────────
    rrf([ BM25 class, semantic raw_description ]) → jina rerank
    collapse: { field: object_id, inner_hits: { sort: @timestamp desc } }
    → one hit per object + its full timeline, in one round trip
    → the VLM called it "mallet" in one commit; BM25 misses that row, the vector catches it

 2. ES|QL ── LIFETIME ───────────────────────────────────────────────────
    FROM room-objects | WHERE object_id == "tool_4f2a"
    | STATS first = MIN(@timestamp), last = MAX(@timestamp),
            appearances = COUNT_DISTINCT(commit_sha)

 3. ES  ── GONE OR HIDDEN? ──────────────────────────────────────────────
    observation_history around the disappearance → all 3 cameras had line of sight
    → removed from the room, not occluded

 4. ACT: goto_and_point(last known pose)
    + Rerun renders the object's voxels from commit a3f9c1 as a ghost
```

---

## Phase 4 — `room revert` / *"put the desk back the way it was before dinner"*

```
 1. ES|QL ── RESOLVE FUZZY TIME ─────────────────────────────────────────
    LLM: "before dinner" → 2026-09-19T18:00:00Z
    FROM room-events | WHERE event_type == "commit" AND @timestamp < <ts>
    | SORT @timestamp DESC | LIMIT 1 | KEEP commit_sha, message
    → a3f9c1

 2. git diff <observed> a3f9c1 → MOVE / ADD / REMOVE ops     [no ES]

 3. ES  ── PLANNING-TIER COLLISION CHECK ────────────────────────────────
    for each target placement: shape query on room-voxels at the observed sha
      { shape: { cell: { shape: <target envelope>, relation: "intersects" } } }
    → is that spot currently occupied? by which object?
    → build the dependency graph; cycles need a staging position (= git stash)
    ONE query, ~50 ms, BEFORE a 40-second motion sequence. Never per-motion.

 4. topological sort → ordered ops                           [no ES]

 5. robot executes — navigate, grasp, transport, place
    robot-telemetry streaming to ES throughout (Phase 1)
    create room-events per pick / place / failed_op

 6. rescan → Phase 2 → git status
    → clean, or an honest "2 of 3 moved, the third slipped"
```

---

## Phase 5 — Analytics (demo filler + debugging)

Runs any time; fills the 30-second silence while the arm works.

```
ES|QL  which zone is most volatile
  FROM room-events | WHERE event_type == "commit"
  | STATS moves = COUNT(*) BY zone | SORT moves DESC

ES|QL  when does the room get messy
  FROM room-observations | STATS dirty = COUNT(*)
       BY bucket = DATE_TRUNC(30 minutes, @timestamp)
  → the step change at the exact second the judge touched the mug

ES|QL  WHY WAS THIS DIFF WRONG   ← the cross-index query, the best one
  FROM robot-telemetry
  | WHERE @timestamp > <commit_ts> - 2s AND @timestamp < <commit_ts>
  | WHERE signal IN ("odom_residual", "pitch")      # one doc per (signal, sample)
  | STATS peak = MAX(ABS(value)) BY signal
  → "residual spiked 200 ms before capture; the robot was mid-lean"

shape  what's within 50 cm of the lamp
terms  multi-resolution change: voxel_key_l3 → drill to voxel_key
```

---

## Every ES operation, one table

| # | phase | operation | index | why it's not decoration |
|---|---|---|---|---|
| 1 | setup | inference endpoints | — | Jina embed + rerank, no pipeline to build |
| 2 | continuous | bulk create | `robot-telemetry` | the volume + the failure-explanation query |
| 3 | continuous | bulk create | `room-observations` | dense history → reliable occlusion calls |
| 4 | status | hybrid + rerank | `room-objects` | **re-identification across absences** |
| 5 | status | aggregation | `room-observations` | **occluded vs deleted — changes robot behaviour** |
| 6 | status | bulk index | `room-objects`, `room-voxels` | the snapshot |
| 7 | status | index | `room-clouds`, `room-events` | catalog + the time bridge |
| 8 | search | hybrid + rerank + collapse | `room-objects` | find things that no longer exist |
| 9 | search | ES\|QL stats | `room-objects` | lifetime: first seen, last seen |
| 10 | revert | ES\|QL time → sha | `room-events` | **wall-clock → version time** |
| 11 | revert | shape query | `room-voxels` | **placement collision → operation order** |
| 12 | revert | create | `room-events` | the action log |
| 13 | analytics | date_histogram | `room-observations` | *when* did it change, to the second |
| 14 | analytics | cross-index | `robot-telemetry` + events | explain a bad commit |
| 15 | analytics | terms on voxel_key_l3/l5 | `room-voxels` | coarse→fine spatial change |

**Four of these change what the robot physically does** (#5, #10, #11, and #4 indirectly).
Those are the ones to show a judge — the rest are supporting evidence.
