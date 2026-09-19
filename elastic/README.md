# `elastic/` — runs on the LAPTOP

**Branch:** `track/data` · **Owner:** Daniel. **Unblocked from hour zero** via `fake/`. This track never touches the robot.

## What this module owns
Every Elasticsearch mapping, write and query. The agent's tools live in `agent/tools.py`
(`search_objects` calls `queries.py`); Agent Builder is not used.
Design: [`../docs/11-elastic.md`](../docs/11-elastic.md) ·
Concepts from zero: [`../docs/12-elastic-primer.md`](../docs/12-elastic-primer.md) ·
Ingest mechanics: [`../docs/13-ingest.md`](../docs/13-ingest.md) ·
Full flow: [`../docs/14-elastic-flow.md`](../docs/14-elastic-flow.md)

## Files

| file | purpose |
|---|---|
| `setup_elastic.py` | **Idempotent.** Creates inference endpoints, index mappings and TSDS templates. You will run this a dozen times; make it safe to re-run. |
| `mappings/room-objects.json` | `semantic_text` + `point` + octree keywords. The searchable one. |
| `mappings/room-voxels.json` | `point` + octree keywords + density. ~3000 docs/commit. |
| `mappings/room-clouds.json` | Catalog: `cloud_uri` (S3), coverage, ICP residual. |
| `mappings/room-observations.json` | **TSDS template.** dims `object_id`,`camera` · `look_back_time: 7d`. |
| `mappings/robot-telemetry.json` | **TSDS template.** dim `signal` · declare downsampling. |
| `mappings/room-events.json` | Plain data stream. The bridge from wall-clock to `commit_sha`. |
| `ingest.py` | Bulk writers. Explicit `_id` on snapshot indices; `op_type: create` on data streams. |
| `queries.py` | **Every query in one file** — hybrid+rerank+collapse, occlusion history, ES\|QL time→sha, spatial collision, analytics. |

## Build order
1. **Tonight:** Serverless project (Elasticsearch type),
   API key in `.env`, `setup_elastic.py` creating everything.
2. Index 30 objects from `fake/scene_gen.py`.
3. **One hybrid search returning hits.** ← this is the unblock gate for the whole track.
4. `ingest.py` telemetry path (the laptop unpacks the WebSocket batches and bulk writes).
5. `queries.py` — occlusion resolution and time→sha first; they're the two that change robot behaviour.
6. Agent tools: `agent/tools.py` (not here) — `search_objects` calls `queries.py`.
7. Voxel ingest (T2).

## Acceptance criteria
- [ ] `setup_elastic.py` runs twice in a row with no errors and no duplicate indices
- [ ] Hybrid search for "mug" returns a doc whose only description says "ceramic cup"
- [ ] ES|QL resolves a wall-clock time to a `commit_sha`
- [ ] Telemetry sustains ~400 docs/s without falling behind

## Gotchas
- **Create mappings before the first write.** Dynamic mapping turns `cell` into two floats and
  every spatial query dies. Mappings are immutable — wrong type means delete and reindex.
- **`look_back_time: 7d`** or replaying recorded data silently fails to ingest.
- **Refresh:** pass `refresh="wait_for"` while developing or your writes seem to vanish.
- Data streams reject `op_type: index`. Use `create`.
