# YOUR JOB — elastic/

You own **only** `gitspace/elastic/`. Do not edit any other folder. If you need something
from another folder, write it down in `NOTES.md` here and keep going.

Read first, in order:
- `../docs/11-elastic.md` — the design (read-path split, indices, octree keys)
- `../docs/13-ingest.md` — mappings, `_bulk`, TSDS gotchas
- `../docs/14-elastic-flow.md` — every ES call in causal order
- `../.env` — `ELASTIC_URL` / `ELASTIC_API_KEY` are already filled in

Build, in this order. Stop and report after each.
1. `setup_elastic.py` — **idempotent**. Two inference endpoints (jina-embed, jina-rerank),
   five index mappings, two TSDS templates. Must run twice cleanly.
2. `mappings/*.json` — one file per index, exactly as specified in `13-ingest.md`.
   `cell` is type `point` (cartesian, NOT geo_point). `voxel_key*` are `keyword`.
   `look_back_time: 7d` on both TSDS templates.
3. `ingest.py` — bulk writers. Explicit `_id` on snapshot indices, `op_type: create`
   on data streams.
4. `queries.py` — every query in one file: hybrid+rerank+collapse, occlusion history,
   ES|QL time→sha, spatial collision, the analytics set.

Acceptance: `setup_elastic.py` runs twice with no error and no duplicate indices; a
hybrid search for "mug" returns a doc whose only description says "ceramic cup".

Until `fake/scene_gen.py` exists, hand-write 30 objects to test against. Include three
DISAGREEING descriptions per object — that is the whole point of the hybrid search.
