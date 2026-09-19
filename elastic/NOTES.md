# NOTES — things `elastic/` needs from outside this folder

## Status

- **Live since ~21:40 EDT on 2026-09-18** (serverless 9.6.0, EIS Jina embed + rerank). Verified live:
  setup runs twice clean, 83/83 tests (19 against the cluster), the h00 story docs indexed and
  joined by trace + capture id, `scripts/story_demo.py` exit 0, and the fake room loaded
  (8,855 docs). On the real index: hybrid "mug" ranks cup_7e21 #2, BM25-only "mug" returns just
  mug_a1b2, ES|QL resolves now -> a commit_sha. TASK.md and README.md acceptance: met.

## docs/ corrections (not my folder)

- **docs/23 §5 downsampling can't be declared as written.** Data stream lifecycle (the only
  lifecycle on Serverless) rejects `fixed_interval` under 5m ("A downsampling round must have a
  fixed interval of at least five minutes", `DataStreamLifecycle.DownsamplingRound`), and `after`
  counts from the backing index's **rollover**, not from when a sample arrived. With no retention
  the automatic rollover is `max_age` 30d (`RolloverConfiguration.evaluateMaxAgeCondition`), so
  nothing would be downsampled during the event at all. Declared instead in
  `mappings/robot-telemetry.json`: `data_retention: 7d` (→ 1-day rollover) and rounds
  `after 1d → 5m`, `after 2d → 30m` (was 1h/6h until 2026-09-19 06:1x UTC: moved so /replay keeps
  raw 50 Hz for the whole event -- user's call). min/max survive downsampling either way (gauges
  become `aggregate_metric_double`). Live: one backing index, effective rollover `max_age 1d
  [automatic]`; the first rollover (~01:20 UTC Sep 20) starts the 1d clock, so nothing downsamples
  before the event ends. **Don't POST _rollover on robot-telemetry before judging.**
- docs/14's `MAX(ABS(pitch))`: telemetry is one doc per (signal, sample), so it's
  `WHERE signal == "pitch" | STATS MAX(ABS(value))` — `queries.telemetry_window()` does this.

## For every writer (fake/, perception/, roomctl/, scripts/, robot/)

- **Every mapping is `dynamic: strict`.** A doc with an unmapped field is rejected whole, with
  the field named in the bulk error, instead of dynamic mapping guessing a type we can never
  change. To add a field: edit `elastic/mappings/<index>.json`, re-run `setup_elastic.py`
  (applies additive changes in place).
- **Every index maps `sentry_trace_id`, `sentry_span_id`, `sentry_url`** (keyword), i.e.
  `obs.trace_fields()`. Spread it into every doc: `{**doc, **obs.trace_fields()}`.
- **`_index` must not be inside a document** — ES rejects it as a metadata field. Put it on
  the bulk action line (`elastic/ingest.py action()` does). `scripts/story_demo.py` is fixed:
  it now bulk-indexes that way and reads the docs back by trace id and capture id. The h00
  `story_docs.json` (old shape, real trace `50c0ccf2…`) replays with `ingest.py`.
- **`room-observations`: send all three of `raw_x`, `raw_y`, `raw_z`.** The mapping can't
  require them, and the per-camera disagreement only shows on axes that were sent. The two
  story_demo captures (`cap_78072`, `cap_82093`) carry `raw_x` only (from web, which now
  compares per axis over whichever cameras reported it).
- **`obs.trace_fields()` returns trace ids when Sentry is NOT initialised** (SENTRY_DSN empty):
  the SDK mints spans anyway, so every writer would plant links to traces Sentry never received.
  Reproduced with sentry-sdk 2.69.2. One-line fix in `obs.py` (observability's file, not
  changed): `if not _HAVE or not sentry_sdk.get_client().is_active(): return {}`.
  `story_demo.py` guards against it locally.

## Wiring other modules call (all offline-tested; none needs the cluster to be built against)

- **`records.to_es_doc(record, commit, at=, capture_id=, meta=, trace=)`** — the ONE git `id` ->
  ES `object_id` translation (docs/10 GAP 2). Output key set == `mappings/room-objects.json`,
  pinned by `tests/test_records.py`. `meta` keys are the mapping's names (`confidence`,
  `point_count`, `observed_by`, `raw_description`); unknown keys raise.
- **`ingest.commit_actions(commit, records, at=, capture_id=, meta_by_id=, trace=)`** — one
  commit's snapshot (every object + the commit event) as bulk actions, for the roomctl hook
  (docs/10 GAP 1): `ingest.write(connect(), ingest.commit_actions(...))`. Voxels and the cloud
  catalog stay with perception.
- **`ingest.action()`** refuses a git-shaped doc (`id`) and any empty/None `_id` field — never
  `"None:mug_a1b2"`.
- **`setup_elastic.connect()`** raises `SetupError` BEFORE any network call when the key is
  missing, parked (`KEY=  # ...` reads as the comment) or a URL; retries timeouts (3x).
  `telemetry/hub.py` already treats that as "spool to disk".
- **`queries.semantic_only()`** (web's "matched by vector" provenance, with scores) and
  `lexical_only()` send exactly the legs `search_objects()` fuses (`tests/test_query_shapes.py`).

## What is real and what is generated (as of 2026-09-19 06:37 UTC)

**No document in the cluster carries real camera-model text.** Real captures are blocked on
hardware: the Pi (`PI_HOST:PI_PORT`) doesn't answer, and no real recording exists on disk
(`perception/pipeline.py` replays recordings; the only generator is `perception/synthetic.py`).
Every indexed commit — main, movie-night and master's `live-check` (174302b, a2b2703) — got its
descriptions from `fake/scene_gen`. Each doc says so: `vlm_model` on room-objects (added and
backfilled, 67/67) and on room-observations.

| data | real or generated |
|---|---|
| git commits, shas, branches, diffs in room.git; object ids (`roomctl.state.new_id`); YAML schema + quantization | **real** (roomctl on real git) |
| the publish path (roomctl -> `records.to_es_doc` -> `ingest.write`) for live-check commits | **real** |
| `raw_description` (objects + observations), `raw_label` | **generated** — scripted by fake/scene_gen, standing in for the VLM (`vlm_model: "fake/scene_gen"`) |
| poses, extents, colours, zones (the scene itself) | **generated** — authored in `fake/scenes/*.yaml` |
| per-camera `raw_x/y/z` disagreement, `confidence`, `point_count`, `occluded`, rejected clusters | **generated** — seeded noise |
| room-voxels (cells from object boxes), room-clouds numbers (`cloud_uri: null` = no real cloud) | **generated** |
| robot-telemetry from the fake (3,015 docs) | **generated**; other writers' telemetry: not verified here |
| `sentry_trace_id` on fake docs | **generated** ids; story_demo + live-check docs carry **real** Sentry traces |
| embeddings (semantic_text / Jina v3), BM25 scores, RRF fusion, Jina rerank scores, `.jina-clip-v2` | **real** — computed live by EIS + Elasticsearch, over the generated text |

Rule for any UI: a hit is SYNTHETIC when `vlm_model` is missing or starts with `fake/`, `scripts/`
or `tests/`. `demo_hybrid.py` prints this banner itself. To replace it with real text: a real
recording (or the Pi) -> `ROOM_SCANNER=perception:<recording>` commit -> the publish hook writes
docs with perception's own `vlm_model` -> `demo_hybrid.py mug --save`.

## Demo artifacts (elastic/artifacts/)

- `demo_hybrid.py mug --save` -> `hybrid_mug.{txt,json}`: the ONE request (BM25 + Jina dense + RRF +
  Jina rerank, collapsed per object) and its live result, with a PROVENANCE banner and a "text by"
  column. cup_7e21: BM25 MISS, dense #2 (0.665, noise floor 0.555), final #2 — on SYNTHETIC text
  (fake/scene_gen), stated on screen. Recapture once real perception commits land.
- `.jina-clip-v2` (EIS, task `embedding`): text + images, 1024-d, one space. Images must be a DATA
  URL (`"value": "data:image/png;base64,…"`) — bare base64 is a 400, despite Elastic's blog example.
  `tests/test_clip_live.py`.
- Backups of docs removed from the real index (both deleted on the user's request, restorable):
  `backup_proof_cup_7e21.json` (hand-injected proof doc) and `backup_synthetic_selftest_551990.ndjson`
  (6,807 docs a perception self-test published through roomctl/publish.py before its real-repo
  guard landed).

## Search fields (web/ and agent/ read these)

- Semantic leg: `raw_description` (semantic_text, jina-embed).
- BM25 leg: `class` + `raw_description.text` — a `text` multi-field (english analyzer) under the
  same field, filled by ES from the same value. **Writers send nothing new.** A `match` on a
  semantic_text field is rewritten to a semantic query, so without the sub-field BM25 never saw
  the descriptions. `setup_elastic.py --check` now refuses a semantic_text field without one.

## The reranker: `jina-reranker-v3.5` on `rerank_text` (2026-09-19)

Endpoint `jina-rerank` now serves `jina-reranker-v3.5` (was v2-base-multilingual). Measured on the
demo queries against the same candidates: top-1 margin "where did I leave my keys" 0.041 -> 0.168,
"have you seen my keys" 0.099 -> 0.245, "i need something to cut paper" 0.063 -> 0.198, same order
everywhere; in the full pipeline every keys phrasing now wins by > 0.3. **Rerank scores changed
scale** — anything thresholding them (not web's semantic_only cut) must recalibrate.

## Blame and time travel (queries.py)

- `moved_at(object_id, branch=None)`: the commit where the pose/zone last changed, walked back
  through `parent_sha` (not the events' objects_moved: git "M" also fires on re-measured extents);
  returns `{object_id, moved_in: {sha, at, capture_id, branch, author, message}, from, to, frame}`,
  `frame` = the capture doc + each camera's view. frame_url: built by web from capture_id.
- `commit_at(ts, branch=None, strictly_before=False)`: `room restore --before T` passes the current
  branch and `strictly_before=True`. Only event_type "commit" counts; ties break by sha.

## The reranker reads `rerank_text` (fixed 2026-09-19, docs/10 D38)

`text_similarity_reranker` reads `docField.getValue()` — only the FIRST value of a multi-valued
field — so on `raw_description` it judged each object on one camera view and demoted the scissors
("orange plastic handles, steel blades") below the hammer. room-objects' default ingest pipeline
(`pipelines/room-objects-rerank-text.json`) now writes `rerank_text` = class + every description,
mapped `index: false` (read by the reranker only, never searched), and `hybrid_request` reranks on
it. Writers send nothing new (`records.SERVER_FIELDS`). An object with no descriptions gets its
class alone (master's live-check a2b2703 published scissors_9f3a with none — a scanner meta gap).

## Keys: runtime vs admin (elastic/ROTATION.md)

Services use `ELASTIC_API_KEY` (least privilege once rotated). `setup_elastic.py` and the live test
fixtures use `ELASTIC_ADMIN_API_KEY` when set (`connect(admin=True)`), else the runtime key.

## Octree depth and coverage (measured 2026-09-19 22:00 UTC)

Two different complaints, one measurement each. They are not the same problem and the fix for one
does nothing for the other.

**Coverage — the cubes only covered a corner.** Not a resolution problem. `room.yaml` declares two
zones (desk, shelf) and `scene_gen.voxels()` filled only those surfaces, their pedestals and the
object boxes, so the whole index was 0.94 x 1.50 x 1.12 m inside an 8 m cube: **6 occupied cells at
1 m, 0.3% of the cube**. `bbsim.py` already had a floor (`rebuild_truth()` over `floor_bounds()` =
furniture extents seeded with +-0.6 m, grown by `--floor-margin` 1.2) and neither `scene_cloud()`
nor `voxels()` ever emitted it. `voxels()` now fills it under the same bounds rule, so the cubes
agree with the grid the robot drives on instead of being a second invented room. Per commit:
**1,828 -> 6,368 docs, 6 -> 26 one-metre cells, footprint 0.94 x 1.50 -> 4.0 x 4.0 m**. Backfilled
into the six existing commits (generator output; re-running `scene_gen` on them reproduces it).

Walls and a ceiling were NOT added: bbsim has no wall geometry, so they would be invented, not
propagated. If we want them they belong in `room.yaml` as declared room bounds, labelled as such.

**Resolution — 6.25 cm vs the robot's 3 cm.** `robot/server.py:167` and `bbsim.py:68` both put BB's
own map at 3 cm, and `costmap.py:93` already anticipated the match ("8: 3.125 cm, BB's own
resolution"). At the pinned 8 m cube, `OCTREE_LEVELS 8` gives a 3.125 cm leaf. Cost, modelled from
the real scene and validated against the indexed count (model 2,297 vs 1,828 actual = 1.26x, the
gap being the 12% edge flicker the model omits):

| depth | leaf | cells/commit | drawn volume | fill | dense `occ` array |
|---|---|---|---|---|---|
| 7 | 6.25 cm | 1,828 | 426 L | 41.6% | 2.1 MB |
| **8** | **3.125 cm** | **~8,000** | **233 L** | **76.0%** | **16.8 MB** |
| 9 | 1.56 cm | ~46,000 | 177 L | 100% | 134.2 MB |

Depth 9 is out on two independent grounds: `VoxelGrid.occ` is a dense `(2**levels)**3` bool array
(134 MB per grid on a Pi), and `scene_cloud`'s 1.5 cm point spacing cannot put `MIN_PTS = 3` in a
1.56 cm cell. Storage and latency are not the constraint: 524 B/doc (~25 MB for six commits at
3.125 cm) and 31-94 ms for every query the page makes.

**Changing depth does not invalidate history.** Origin and size fix the grid; depth only cuts the
leaves finer, so the key at depth n+1 is the key at depth n plus one digit (proved over 20,000
random points, and pinned in `tests/test_octree.py`). Today's `voxel_key` IS tomorrow's
`voxel_key_l7`, byte for byte. So the rungs survive a depth change and only an exact-leaf
comparison across the boundary breaks — `voxel_changes(..., "full")` would read every cell as both
added and removed, which is why it now tells you to name a rung instead.

`pipelines/room-voxels-keys.json` derives every rung from `voxel_key` server-side. They used to be
computed by hand in three writers (`perception/voxelize.py`, `fake/scene_gen.py`,
`elastic/records.py`), which is what made a depth change expensive; now it costs one constant.
Rungs on room-voxels: l3 (1 m), l5 (25 cm), l6 (12.5 cm), l7 (6.25 cm), leaf.

**The keying is total, and that is a test now.** Every point inside the cube gets a key whose cell
contains it (50,000 sampled, 0 unkeyed), outside-the-cube returns None rather than a clamped wrong
cell, and 0 of 31,555 indexed docs are missing a key. Filling the cube's *air* is a different thing
and is not viable: every cell occupied is 1.1 GB/commit at 6.25 cm, 8.8 GB at 3.125 cm — and a map
where everything is occupied is one the robot cannot plan a path through.

**Not done, needs one decision:** flipping `OCTREE_LEVELS` 7 -> 8 must change `.env` AND
`room.git/room.yaml` together (`index_voxels()` refuses to write when they disagree) and restart
every writer. room.yaml is a commit to the room repo, so it is master's call, not mine.

## Still unverified

- Whether the first real downsample (after 1d, ~Sep 21) succeeds on Serverless — nothing has
  downsampled yet. (Rollover default verified live: `max_age 1d [automatic]`.)

Verified live, 2026-09-18: `semantic_text` inside a TSDS; the `raw_description.text` multi-field;
the downsampling lifecycle; EIS `jina-embeddings-v3` (1024 dims) and
`jina-reranker-v2-base-multilingual`; all 16 queries.

## Non-blocking

- Index count: TASK.md says "five index mappings, two TSDS templates"; README.md, 14-elastic-flow.md
  and the system design page all list **six**. Built six.
- Optional env overrides, not in `../.env.example`: `ES_EMBED_MODEL` (default
  `jina-embeddings-v3`), `ES_RERANK_MODEL` (default `jina-reranker-v2-base-multilingual`),
  `ES_INFERENCE_SERVICE` (default `elastic` = EIS; `jinaai` goes direct and needs `JINA_API_KEY`).
  A `JINA_API_KEY` in `.env` no longer switches anything by itself.
- EIS here also offers `.jina-reranker-v3.5` and `.jina-clip-v2` (multimodal: image crops ->
  visual re-identification). Unused so far; switching the reranker is `ES_RERANK_MODEL=` +
  `--recreate jina-rerank`.
