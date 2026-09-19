# 13 — How documents actually get into Elasticsearch

Shape-of code — verify exact syntax against live docs before relying on it.

## The model, restated

- A **voxel document is a summary of the points that landed in that cell** — count, z range,
  which object claims it. The individual points are never stored.
- An **object document carries the `voxel_key` of the cell it sits in**, denormalized, so
  semantic search returns a position without a second lookup.
- **Prefix truncation gives regions** for free: `voxel_key_l3` = 1 m, `_l5` = 25 cm.

## Rule zero: create the mappings BEFORE the first write

If you just POST a document, Elasticsearch infers the types — and it infers them wrong in the
two places that matter:

| field | what dynamic mapping does | what breaks |
|---|---|---|
| `cell: {x, y}` | an object with two `float`s | **every spatial query.** It is not a `point` |
| `voxel_key` | `text` + `.keyword` sub-field | aggregating on `voxel_key` returns tokenized garbage |
| `@timestamp` | usually `date`, sometimes `text` | time range queries fail silently |

Worse, **a field's mapping cannot be changed once it exists** — you have to delete the index
and reindex. At 3am that is an hour you don't have. Write `setup_elastic.py`, make it
idempotent, run it first.

```python
from elasticsearch import Elasticsearch, helpers
es = Elasticsearch(ES_URL, api_key=ES_API_KEY)

es.indices.create(index="room-voxels", mappings={"properties": {
    "@timestamp":   {"type": "date"},
    "commit_sha":   {"type": "keyword"},
    "parent_sha":   {"type": "keyword"},
    "branch":       {"type": "keyword"},
    "voxel_key":    {"type": "keyword"},      # full depth, 6.25 cm
    "voxel_key_l5": {"type": "keyword"},      # 25 cm
    "voxel_key_l3": {"type": "keyword"},      # 1 m
    "cell":         {"type": "point"},        # CARTESIAN, not geo_point
    "z_min":        {"type": "float"},
    "z_max":        {"type": "float"},
    "density":      {"type": "integer"},      # how many raw points fell in this cell
    "zone":         {"type": "keyword"},
    "object_id":    {"type": "keyword"},
}}, ignore=400)   # 400 = already exists; makes the script idempotent
```

`point` accepts `{"x": 1.25, "y": 0.85}`, `[x, y]`, or WKT `"POINT (1.25 0.85)"`.

For `room-objects`, the one that needs text:
```python
"class":           {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
"raw_description": {"type": "semantic_text", "inference_id": "jina-embed"},
```
The `text`/`keyword` multi-field is mandatory: `text` searches, `keyword` aggregates. If an
aggregation ever returns nothing, this is why.

## TSDS indices come from a template, not `indices.create`

Data streams are created *by* a template when the first document arrives:

```python
es.indices.put_index_template(
  name="room-observations",
  index_patterns=["room-observations*"],
  data_stream={},
  template={
    "settings": {
      "index.mode": "time_series",
      "index.routing_path": ["object_id", "camera"],   # must match the dimensions
      "index.look_back_time": "7d",                    # SET THIS. see gotchas
    },
    "mappings": {"properties": {
      "@timestamp":  {"type": "date"},
      "object_id":   {"type": "keyword", "time_series_dimension": True},
      "camera":      {"type": "keyword", "time_series_dimension": True},
      "confidence":  {"type": "float",   "time_series_metric": "gauge"},
      "point_count": {"type": "integer", "time_series_metric": "gauge"},
      "raw_x": {"type": "float"}, "raw_y": {"type": "float"}, "raw_z": {"type": "float"},
      "occluded": {"type": "boolean"}, "rejected_reason": {"type": "keyword"},
      "raw_description": {"type": "semantic_text", "inference_id": "jina-embed"},
    }}
  })
```

## The three write paths

| when | what | volume | call |
|---|---|---|---|
| **`room commit`** | objects + voxels + event + cloud catalog | ~3 k docs | one `helpers.bulk` |
| **watch loop**, 1–2 Hz | observations | ~45 docs/s | bulk per capture |
| **balance loop**, 50 Hz | telemetry | ~400 docs/s | **buffer 1 s, then bulk** |

Never write documents one at a time. 400 individual HTTP requests per second will fall over;
400 documents in one bulk request is nothing.

```python
def index_voxels(voxels, commit_sha, parent_sha, branch, ts):
    actions = [{
        "_index": "room-voxels",
        "_id": f"{commit_sha}:{v['voxel_key']}",     # idempotent — see below
        "_source": {
            "@timestamp": ts, "commit_sha": commit_sha,
            "parent_sha": parent_sha, "branch": branch,
            "voxel_key": v["voxel_key"],
            "voxel_key_l5": v["voxel_key"][:5],
            "voxel_key_l3": v["voxel_key"][:3],
            "cell": {"x": v["x"], "y": v["y"]},
            "z_min": v["z_min"], "z_max": v["z_max"],
            "density": v["density"], "zone": v["zone"],
            "object_id": v.get("object_id"),
        },
    } for v in voxels]
    helpers.bulk(es, actions, chunk_size=1000, refresh="wait_for")
```

```python
# data streams accept ONLY op_type "create" — "index" throws
def index_observations(obs):
    helpers.bulk(es, [{"_index": "room-observations",
                       "_op_type": "create", "_source": o} for o in obs])
```

## Document IDs: the 3am feature

For the snapshot indices, set `_id` explicitly to a natural key:

```
room-voxels    _id = f"{commit_sha}:{voxel_key}"
room-objects   _id = f"{commit_sha}:{object_id}"
```

Re-running the ingest then **overwrites instead of duplicating**. You will re-run ingest
constantly while debugging; without this you'd silently accumulate duplicate voxels and every
aggregation would be wrong in a way that looks like a perception bug.

TSDS indices **derive their own `_id`** from dimensions + `@timestamp`, so you cannot set one
— another reason observations and telemetry live in separate indices from committed state.

## The five gotchas, in the order they'll hit you

1. **Refresh.** Elasticsearch is *near*-real-time: a document isn't searchable until a refresh
   (default ~1 s). You'll write, immediately query in the same script, get zero hits, and
   conclude ingest is broken. Pass `refresh="wait_for"` on bulk during development.
2. **Data streams reject `index`.** Use `_op_type: "create"`, or you get a hard error.
3. **TSDS look-back window.** Documents whose `@timestamp` falls outside
   `look_back_time`/`look_ahead_time` relative to *now* are **rejected**. The moment you
   replay a recorded session or backfill test data, ingest starts failing. Set
   `look_back_time: 7d` at template creation. You cannot easily change it later.
4. **Mappings are immutable.** Wrong type = delete the index and reindex. Get
   `setup_elastic.py` right before ingesting anything real.
5. **Partial bulk failures are silent-ish.** `helpers.bulk` raises on errors but
   `raise_on_error=False` hides them. Log `errors` from the response — one malformed doc
   shouldn't look like "Elasticsearch is down".

## Order of operations for tonight
```
1. setup_elastic.py                       create all mappings + templates (idempotent)
2. fake scene generator -> index_objects  ~30 docs, no robot needed
3. one hybrid search query                proves retrieval works end to end
4. (later) swap the fake source for the real perception output
```
Step 3 is the unblock point: after it returns, the entire Elastic track runs without touching
the robot.
