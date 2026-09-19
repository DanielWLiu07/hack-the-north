# 12 — Elasticsearch from zero, and how to win the track

Assumes you know HTTP, JSON and SQL, and have never touched Elasticsearch.
Code below is **shape-of** — verify exact syntax against live docs, this area moves fast.

---

# Part 1 — What Elasticsearch actually is

**A document store with a search engine bolted to the front.** You throw JSON objects at
it; it indexes every field so you can search them fast. It is not relational — no joins,
no transactions, no foreign keys. You denormalize: each document carries everything it
needs.

The core trick is the **inverted index**. A normal database index maps *row → values*. An
inverted index maps *value → documents that contain it*:

```
"mug"   → [doc3, doc7, doc12]
"desk"  → [doc1, doc3, doc9]
```

So "find every document containing 'mug'" is a hash lookup, not a table scan. That's why
it's fast, and why it's shaped for *"find me the relevant things"* rather than *"give me
exactly row 47."*

## Vocabulary

| term | means | SQL analogue |
|---|---|---|
| **document** | one JSON object you store | a row |
| **index** | a named collection of documents | a table |
| **field** | one key in the document | a column |
| **mapping** | the schema: which field is what type | `CREATE TABLE` |
| **field type** | `keyword`, `text`, `date`, `float`, `point`, `dense_vector`… | column type |
| **analyzer** | how `text` gets chopped into searchable tokens | — |
| **query DSL** | the big JSON query language | SQL |
| **ES\|QL** | the newer piped query language | SQL, but nicer |
| **aggregation** | grouping + math over matching docs | `GROUP BY` |
| **shard** | a physical piece of an index | a partition |

### The one gotcha that bites everyone: `text` vs `keyword`

- **`text`** is *analyzed*: `"Blue Ceramic Mug"` is lowercased and split into
  `["blue","ceramic","mug"]`. Good for **searching**. You cannot group or sort on it.
- **`keyword`** is stored whole: `"Blue Ceramic Mug"` stays one atomic string. Good for
  **filtering, grouping, sorting**. Bad for searching inside.

You usually want **both**, which is the standard multi-field pattern:

```json
"class": {
  "type": "text",
  "fields": { "keyword": { "type": "keyword" } }
}
```
Now `class` searches and `class.keyword` groups. If an aggregation ever returns nothing,
this is why: you aggregated on the `text` version.

---

# Part 2 — The four kinds of search (this is the whole prize)

The brief names *"hybrid search combining BM25, Jina dense vectors, and reranking."*
Those are three of the four stages below. Understand each one separately, then combine.

## 2a. BM25 — lexical / keyword search

The classic relevance formula (a descendant of TF-IDF). For a query term in a document it
scores on three intuitions:

1. **Term frequency** — the more often "mug" appears in this doc, the more relevant. But
   with diminishing returns: the 10th "mug" adds far less than the 2nd.
2. **Inverse document frequency** — a term appearing in *every* document ("the") tells you
   nothing; a rare term ("soldering") is highly informative. Rare terms score higher.
3. **Field length normalization** — matching "mug" in a 5-word field beats matching it in a
   500-word field.

**Strength:** exact terms, IDs, names, rare words. If you search `keys_7c2e`, BM25 nails it.
**Weakness:** zero understanding of meaning. Search "mug", and a document saying
"ceramic cup" scores **zero**. It has no idea they're the same thing.

## 2b. Dense vectors — semantic / kNN search

An **embedding model** turns text (or an image) into a list of numbers — a vector, maybe
1024 of them. The model is trained so that *things that mean similar things land near each
other* in that 1024-dimensional space. "mug" and "ceramic cup" end up close. "mug" and
"soldering iron" end up far apart.

- **`dense_vector`** is the field type that stores them.
- **kNN search** = "embed my query, find the k nearest stored vectors."
- Distance is usually **cosine similarity** — the angle between vectors, ignoring length.
- Exact kNN is O(n). Elasticsearch uses **HNSW**, a graph structure that gets approximate
  nearest neighbours in roughly log time. "Approximate" means it can occasionally miss a
  true neighbour; in practice you never notice.

**Strength:** meaning, synonyms, paraphrase, cross-lingual.
**Weakness:** exact identifiers. Ask a vector model for `keys_7c2e` and it returns things
that are *vibe-similar to a random string*. Useless.

> **This is why hybrid exists.** BM25 and vectors fail in exactly opposite directions.

## 2c. Hybrid search + RRF — merging two ranked lists

You run BM25 and kNN, get two ranked lists, and now you have a problem: **the scores aren't
comparable.** BM25 might return 14.2; cosine returns 0.87. You can't add them.

**Reciprocal Rank Fusion (RRF)** solves this by throwing away the scores and using only the
**ranks**:

```
rrf_score(doc) = Σ  1 / (k + rank_in_list_i)          k is typically 60
                 i
```

A doc ranked #1 in BM25 and #3 in kNN scores `1/61 + 1/63`. A doc appearing in only one
list still scores, just lower. **Anything both methods like floats to the top.** It needs no
tuning and no comparable scales, which is why it's the default answer.

## 2d. Reranking — the expensive second opinion

Stages 2a–2c are **bi-encoders**: the query and the document are embedded *separately*, so
the model never sees them together. Fast (you can precompute doc vectors) but shallow.

A **cross-encoder / reranker** takes `(query, document)` as one input and scores the pair
directly. Far more accurate, far too slow to run on a million docs.

So the standard pattern is **two-stage retrieval**:

```
1M docs ──BM25+kNN+RRF──> top 100 ──Jina reranker──> top 10 ──> the agent
         (fast, approximate)        (slow, accurate)
```

Retrieve broadly and cheaply, then rerank narrowly and expensively. Jina's rerankers are
available through Elasticsearch's inference API, which is exactly what the brief names.

## 2e. `semantic_text` — the shortcut that saves you hours

Normally you'd: pick a model → run every document through it → store vectors → embed the
query at search time → kNN. That's a pipeline you have to build and keep in sync.

**`semantic_text` collapses all of it into a field type.** Map a field as `semantic_text`,
point it at an inference endpoint, and Elasticsearch chunks and embeds **on ingest**
automatically, and embeds your query automatically at search time.

```json
"description": { "type": "semantic_text", "inference_id": "jina-embed" }
```

You write a mapping instead of a pipeline. **Do this.** Rolling your own embedding
pipeline at a hackathon is strictly more work for a strictly worse prize story.

## 2f. Retrievers — the whole thing in one query

**Retrievers** are the modern way to compose search in Elasticsearch: each is a node that
produces ranked results, and they nest. The single query below contains BM25 **and** Jina
dense vectors **and** reranking — the exact trifecta the brief names:

```json
{
  "retriever": {
    "text_similarity_reranker": {              // stage 2: cross-encoder
      "retriever": {
        "rrf": {                                // stage 1c: fuse the two lists
          "retrievers": [
            { "standard": { "query": { "match": { "class": "mug" } } } },   // BM25
            { "standard": { "query": { "semantic": {                        // Jina dense
                  "field": "description", "query": "blue ceramic cup" } } } }
          ]
        }
      },
      "field": "description",
      "inference_id": "jina-rerank"
    }
  }
}
```

**Screenshot this query for the judges.** It is the entire prize brief in 15 lines.

---

# Part 3 — Aggregations: asking questions about the whole set

Search returns documents. **Aggregations return answers about documents.** It's `GROUP BY`
plus statistics, and it nests arbitrarily.

Two kinds:
- **Bucket aggregations** split docs into groups: `terms` (group by value), `date_histogram`
  (group by time interval), `range`, `filters`.
- **Metric aggregations** compute numbers per bucket: `avg`, `max`, `sum`, `cardinality`
  (distinct count), `percentiles`.

```json
{ "aggs": { "by_zone": { "terms": { "field": "zone" },
            "aggs": { "moves": { "value_count": { "field": "object_id" } } } } } }
```
→ *"how many object movements per zone"* → **which zone is messiest.**

Aggregations are where "we indexed sensor data" becomes "we learned something about the
room." They're cheap to write and they're on the judges' checklist.

---

# Part 4 — ES|QL

The older Query DSL is deeply nested JSON. **ES|QL** is a piped language, read left to right:

```sql
FROM room-events
| WHERE @timestamp > NOW() - 6 hours AND event_type == "commit"
| STATS moves = COUNT(*) BY zone, hour = DATE_TRUNC(1 hour, @timestamp)
| SORT moves DESC
| LIMIT 10
```

`FROM` picks the index, `WHERE` filters, `STATS ... BY` aggregates, `SORT`/`LIMIT` finish.
If you know SQL you already know it.

**Why it matters for the prize:** the brief names ES|QL explicitly, it's readable enough to
put **on screen during the demo** (nested JSON is not), and it's what the agent should emit
when it needs to reason over time. An LLM writes ES|QL far more reliably than Query DSL.

---

# Part 5 — Geospatial, and why it applies to a room

## The four spatial field types

| type | coordinate system | stores |
|---|---|---|
| `geo_point` | Earth (lat/lon) | a point on the globe |
| `geo_shape` | Earth (lat/lon) | polygons, lines, circles on the globe |
| **`point`** | **arbitrary cartesian (x, y)** | **a point in any coordinate space** |
| **`shape`** | **arbitrary cartesian** | **polygons, boxes, geometry in any space** |

The first two assume a sphere and do spherical-geometry maths. **The second two are for
exactly our situation: a coordinate system that isn't Earth.** A room in metres is a
cartesian plane, so `point` and `shape` are the *correct* types, not a workaround.

This is a genuinely distinctive thing to use. Most hackathon teams index text. A team doing
**cartesian spatial queries over a physical room** is doing something an Elastic engineer
will find interesting, because it's using the geo engine for what it's actually for —
coordinates — without pretending a room is a planet.

## Our mapping

```json
"position":  { "type": "point" },     // object centroid: {"x": 0.42, "y": 0.18}
"footprint": { "type": "shape" }      // its bounding box on the support surface
```

Store metres directly. `z` (height) rides along as a plain `float` — the spatial types are
2D, and for "what's on the desk" a floor-plan projection is the right abstraction anyway.

## The queries this unlocks

A **`shape` query** matches using a spatial **relation** between your query geometry and the
indexed geometry:

- **`intersects`** — they overlap at all (the default)
- **`within`** — the document's shape is entirely inside your query shape
- **`contains`** — the document's shape entirely contains your query shape
- **`disjoint`** — they don't touch

```json
{ "query": { "shape": { "position": {
    "shape": { "type": "envelope", "coordinates": [[0.0,0.0],[1.2,0.8]] },
    "relation": "within" } } } }
```
→ *"every object inside the desk rectangle."* **That is `git status` for one zone,
computed spatially rather than from a directory name** — which is a nice independent
cross-check on our zone assignment, and a good thing to mention.

Useful queries for us:
- *"what's on the desk right now"* → `within` the desk envelope
- *"what's within 50 cm of the lamp"* → `intersects` a small box/circle around it
- *"what's blocking the robot's path"* → `intersects` the planned path polygon
- *"is the target placement spot occupied?"* → the executor in
  [`04-git-semantics.md`](04-git-semantics.md#applying-a-diff-to-physical-space) must order
  operations so it never places a mug where a book still sits. **This runs once as a
  planning step, not per-motion** — see the latency tiers in
  [`11-elastic.md`](11-elastic.md#which-queries-may-touch-elasticsearch-latency-tiers).
  Elasticsearch genuinely shapes the robot's plan; it must never sit inside the control loop.

And the cartesian aggregations:
- **`cartesian_centroid`** — the centre of mass of everything in a zone. Track it over time
  and you can literally watch clutter drift across a desk.
- **`cartesian_bounds`** — the bounding box of the mess. *"The desk's used area grew 40%
  since morning."*

> **Alternative you'll see suggested:** cram room metres into `geo_point` by pretending
> they're degrees, to unlock `geo_distance` and Kibana Maps. It works, and it's a hack.
> `point`/`shape` is correct and reads better to an Elastic judge. Use cartesian.

---

# Part 6 — Time series

## Field type and the basics
`date` is the field type; `@timestamp` is the conventional name. Every document we write
gets one — objects, observations, events.

## Data streams
For append-only time-ordered data, you don't write to an index directly — you write to a
**data stream**, which is a named thing backed by a series of hidden rolling indices.
Elasticsearch rolls to a new backing index on size/age, so one index never grows unbounded
and old data can be dropped or frozen by an **ILM policy**. You still just write to
`room-observations` and query `room-observations`; the rolling is invisible.

## TSDS — time series data streams
A **time series data stream** is a data stream with `index.mode: time_series`, where you
declare:

- **dimensions** (`time_series_dimension: true`) — the fields identifying *which series a
  measurement belongs to*: for us **`object_id`** and **`camera`**.
- **metrics** (`time_series_metric: gauge|counter`) — the measured values: **`confidence`**,
  **`point_count`**.

Elasticsearch then sorts and compresses by series, which dramatically reduces storage and
enables **downsampling** (roll raw points into hourly statistics automatically).

**This genuinely fits `room-observations`.** Three cameras × N objects × every capture, each
emitting a confidence and a point count, is textbook sensor time-series. Declaring it as a
proper TSDS rather than a pile of JSON is a real engineering choice and demonstrates you
knew the difference. Cheap to do: it's mapping parameters.

## `date_histogram` — the time aggregation
Buckets documents by fixed interval:
```json
{ "aggs": { "over_time": { "date_histogram":
    { "field": "@timestamp", "fixed_interval": "30m" } } } }
```
→ *"room volatility every 30 minutes"* → **when does this room get messy?**

Nest a metric inside and you get trend lines. Add **pipeline aggregations**
(`derivative`, `moving_avg`) for rate-of-change: *"clutter is accumulating 3× faster than
this morning."*

## Fuzzy time → a commit sha (our best agent beat)
*"Put the desk back the way it was before dinner"* resolves like this:

```sql
FROM room-events
| WHERE event_type == "commit" AND @timestamp < "2026-09-19T18:00:00Z"
| SORT @timestamp DESC
| LIMIT 1
| KEEP commit_sha, message, @timestamp
```

The LLM's whole job is turning *"before dinner"* into that timestamp. Elasticsearch does
the rest, returns a sha, and the agent calls `room_checkout(sha)` — **and a robot arm
moves.** That chain, natural language → time-series query → physical motion, *is* the
submission.

---

# Part 7 — Agent Builder

Three layers, in the order you'll build them:

1. **Tools** — a saved Elasticsearch query (ES|QL or DSL) exposed as a callable function
   with typed parameters and a description. The description is what the LLM reads to decide
   whether to call it, so **write descriptions like you're writing for the model, because
   you are.**
2. **Agents** — an LLM plus a set of tools plus instructions. Elastic ships a default agent;
   you add yours.
3. **Workflows** — deterministic multi-step orchestration. **Still tech preview** — plan for
   tools to work reliably and workflows to be flaky, not the other way round.

Agent Builder also exposes tools over **MCP**, which matters for us: Bracket Bot already
ships [`bracketbot-mcp`](https://github.com/BracketBotCapstone/bracketbot-mcp), an MCP
server for controlling the robot. So both halves of the agent speak the same protocol:

```
        ┌──────────── agent ────────────┐
   MCP ─┤                               ├─ MCP
        │  Elastic Agent Builder tools  │  bracketbot-mcp
        │  = RETRIEVE / REASON          │  = ACT
        └───────────────────────────────┘
```

**Retrieval tools and action tools on the same bus.** That's a clean architecture *and* it
satisfies both the Elastic brief and the Bracket Bot brief with one design. Verify the MCP
endpoint details in live docs.

---

# Part 8 — Every feature, mapped to GITSPACE

| Elastic feature | what it does in our project | why it's not decoration |
|---|---|---|
| **BM25** | find objects by exact id / label | `keys_7c2e` must match exactly; vectors can't |
| **Jina dense vectors** | *"where's my mug"* finds `ceramic cup` | labels come from a VLM and are inconsistent by nature |
| **`semantic_text`** | auto-embed object descriptions on ingest | no pipeline to build or keep in sync |
| **RRF** | merge lexical + semantic results | the two fail in opposite directions |
| **Jina reranker** | final precision pass on top ~50 | *"the red thing I left by the window"* needs pair-wise scoring |
| **`point` / `shape`** | object positions and footprints in room metres | **collision checks in the executor's ordering loop** |
| **`shape` query (`within`)** | "everything on the desk" | spatial cross-check on zone assignment |
| **`cartesian_centroid/bounds`** | where clutter accumulates, how far it spreads | genuine room analytics, not a count |
| **TSDS + data streams** | `room-observations`, dims `object_id`+`camera` | real sensor time-series, correctly modelled |
| **`date_histogram`** | when the room gets messy | the analytics panel |
| **Aggregations** | volatility per zone, most-moved object | fills demo dead air with insight |
| **ES\|QL** | *"before dinner"* → commit sha | readable on screen; LLMs emit it reliably |
| **Agent Builder tools** | retrieval side of the agent | |
| **MCP → bracketbot-mcp** | action side of the agent | **the loop closes on an actuator** |

---

# Part 9 — How to actually win

## What you're up against
Most entries will be a RAG chatbot over a document pile. **The brief rules that out in one
sentence:** *"That means going beyond RAG with a chatbot."* Beating it needs three things.

### 1. The loop must close on something physical
Their words: *"Workflows that close the loop by taking action, not just answering
questions."* Everyone else's "action" will be sending a Slack message or writing a row.
Ours picks up a mug. **Say this in the first fifteen seconds:**

> *"The last tool call in our agent's workflow is a robot arm moving a physical object."*

Then show it. That's the whole pitch; everything else is supporting evidence.

### 2. The mess has to be real, and you have to show it
*"genuinely messy, unstructured, real-world data... sensor streams."* We have it honestly:
three cameras that **disagree**, rejected clusters, confidence that oscillates, objects that
are occluded rather than gone.

**Do not just claim this — put it on screen.** A single view showing cam0 saying `x=0.42`,
cam2 saying `x=0.47`, and cam1 not seeing the object at all, and then the agent *resolving
it*, is worth more than any amount of describing your pipeline.

### 3. Retrieval must change a decision
*"agents that can reason over your data, decide what to retrieve, call tools."* The
occlusion-vs-deletion query is the perfect demonstration, because the answer **changes what
the robot physically does**:

```
object vanished from scan
  → agent queries room-observations (TSDS, last 40 captures)
  → "seen by cam0 only; cam0's line of sight is now blocked by the laptop"
  → decision: OCCLUDED, not deleted
  → carries it forward instead of writing a deletion into git
```

Without Elasticsearch, git writes a wrong deletion and the robot does the wrong thing. **The
database changed the physical outcome.** That is the single strongest sentence available to
us for this prize — build the demo so you can show it, not just assert it.

## The judge's checklist — make ticking it effortless
They will be listening for named features. Have a Rerun panel (or one printed page) that
shows each one in use, so they don't have to dig:

- [ ] BM25 — lexical retrieval on `class` / `object_id`
- [ ] **Jina** dense vectors — `semantic_text`, EIS inference endpoint
- [ ] Reranking — `text_similarity_reranker` with a Jina reranker
- [ ] Hybrid — `rrf` retriever fusing both *(one screenshot covers all four)*
- [ ] Aggregations — volatility per zone, most-moved object
- [ ] **ES|QL** — the "before dinner" time query, visible on screen
- [ ] Geo/spatial — `point`/`shape` + a `within` query in the executor
- [ ] Time series — TSDS with declared dimensions and metrics
- [ ] Agent Builder — tools defined, agent calling them
- [ ] Workflows — if the tech preview cooperates; don't bet the demo on it

## Three things that lose it
1. **A chatbot.** If a beat ends with text on a screen instead of the robot moving or
   pointing, cut the beat.
2. **Obviously synthetic data.** `faker`-generated rows read as fake instantly. Ours is real
   sensor output — make sure that's unmistakable (show the raw per-camera disagreement).
3. **Features named but not shown.** Saying "we use hybrid search" is worth nothing. Showing
   the retriever query, and a result BM25 alone would have missed, is worth everything.

## The 45 seconds, if that's all you get
> *"Git is our source of truth, but git can't answer 'where did I leave my keys' — so
> Elasticsearch is the read path. Every raw detection from all three cameras lands in a time
> series data stream, including the ones we throw away at commit time. When an object
> vanishes, the agent queries that history to decide whether it's gone or just occluded —
> and that decision changes what the robot physically does. Watch:*
> `room search "where did I leave my keys"` *— hybrid BM25 plus Jina vectors plus a
> reranker, and now the robot is driving over to point at them."*
