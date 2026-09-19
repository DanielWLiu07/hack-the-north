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
  `after 1h → 5m`, `after 6h → 30m`. min/max survive downsampling either way (gauges become
  `aggregate_metric_double`: min, max, sum, value_count). Raw 50 Hz stays queryable until the
  first rollover (~24 h in); after that, "why was this diff wrong" gets 5-min min/max, not 20 ms.
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
  story_demo captures (`cap_78072`, `cap_82093`) carry `raw_x` only (from web-64, which now
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

## Search fields (web/ and agent/ read these)

- Semantic leg: `raw_description` (semantic_text, jina-embed).
- BM25 leg: `class` + `raw_description.text` — a `text` multi-field (english analyzer) under the
  same field, filled by ES from the same value. **Writers send nothing new.** A `match` on a
  semantic_text field is rewritten to a semantic query, so without the sub-field BM25 never saw
  the descriptions. `setup_elastic.py --check` now refuses a semantic_text field without one.

## Known limits of the current queries

- **The reranker sees one description, not three.** `text_similarity_reranker` reads
  `docField.getValue()` — the first value of `raw_description` — so the final pass judges each
  object on one of its three VLM descriptions. Retrieval (BM25 + semantic) still uses all three.
  Fix if it matters: a server-side ingest pipeline joining the three into one `rerank_text`
  field (no change for writers), or `chunk_rescorer` (GA on Serverless; untested on arrays).

## Still unverified

- Serverless rollover defaults (the 30d/1d numbers above are the stateful defaults) — i.e.
  whether telemetry actually downsamples ~24 h in. Check `GET _data_stream/robot-telemetry` then.

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
