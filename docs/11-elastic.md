# 11 — Elasticsearch: the read path

## The honest argument (not a bolt-on)

**Git is a terrible query engine.** That's not a knock on the design in
[`04-git-semantics.md`](04-git-semantics.md) — it's the reason that design needs a partner.

Git gives us exact history, transactional semantics, three-way merge, and revert. What it
cannot do, at all:

- *"Where did I leave my keys?"* — git has no semantic search. `git log -S` is a string grep.
- *"Where's my mug?"* when the object was labelled `ceramic cup` — no fuzzy/vector matching.
- *"Which zone gets messiest, and at what time of day?"* — no aggregations.
- *"Put the desk back the way it was before dinner"* — git can't resolve a fuzzy time
  expression to a commit sha.
- *"Find everything within 50 cm of the lamp"* — no spatial queries.
- *"Has this object always been this flaky, or is it genuinely gone?"* — git only stores
  the clean committed truth. **It has no memory of the mess.**

So the split is architectural, not decorative:

> **Git is the write path and the source of truth. Elasticsearch is the read path and the
> agent's context layer.**

That maps almost word-for-word onto what their brief asks for ("Elasticsearch as their
context layer", "beyond RAG with a chatbot", "close the loop by taking action").

## The part that makes it load-bearing: git stores the truth, Elastic stores the mess

This is the key move. Our commit pipeline deliberately throws away enormous amounts of
information to keep diffs clean — quantized poses, rejected low-confidence clusters,
per-camera disagreements, occlusion events (see
[`03-perception.md`](03-perception.md#the-real-enemy-phantom-diffs)). That discarded data is
*exactly* the "messy, contradictory, real-world sensor data" Elastic's brief is about, and
it's also **the data the agent needs in order to reason well**.

Two places where querying Elastic makes the *perception itself better* — which is the bar
for "not bolted on":

### 1. Occlusion vs. deletion (our hardest ambiguity, [R8](08-risks.md))
An object vanishes from a scan. Gone, or hidden behind the laptop? Query
`room-observations` for that `object_id`:
- observed by cam0 only, and cam0's line of sight is now blocked → **occluded**, carry forward
- all three cameras had clear view, nothing → **deleted**, write the deletion
- aggregation shows confidence has been oscillating for 40 captures → **chronically flaky**,
  don't trust either reading; flag for a second vantage point

Git can't answer this. An in-memory dict could, badly, for one session. Elastic answers it
across the whole weekend with one aggregation.

### 2. Object re-identification across absences
Today we match new clusters against HEAD only (Hungarian, [`03`](03-perception.md#object-association-between-commits)).
That breaks when an object leaves the room and comes back — it gets a brand-new ID and the
history is severed. Instead: **hybrid search the new cluster's embedding against every
historical appearance of every object.** "This looks like the thing we called `mug_a1b2`
six commits ago." IDs survive absences. History stays continuous.

That's a genuine capability upgrade purchased with vector search, and it's the thing to say
out loud to an Elastic judge.

## Is our data actually "messy" enough? (the honest audit)

Their brief: *"genuinely messy, unstructured, real-world data (transcripts, logs, PDFs,
scraped forums, **sensor streams**, multilingual text and more)."* Sensor streams are on
their list, so the category is right. But there were **two real gaps** in the plan as first
written, and they're worth fixing because fixing them makes the system better, not just the
pitch.

### Gap 1 — we were cleaning the data *before* Elasticsearch ever saw it

This is the serious one. As originally designed, our numpy pipeline did the association,
denoising and disambiguation, and then wrote **tidy rows** to Elasticsearch. A judge looking
at `room-objects` would see 30 clean documents and reasonably ask *"where's the mess?"*
The mess would have been real — and entirely invisible, handled upstream in code Elastic
gets no credit for and can't evaluate.

**The fix, and the sentence to say out loud:**

> **We don't clean the data before Elasticsearch. Elasticsearch is how we clean it.**

Concretely, invert the order:
- Index **raw detections before association** — every cluster, every camera, unquantized,
  including the ones we reject.
- Do the resolution **as Elasticsearch queries**: hybrid search for re-identification,
  aggregations over confidence history for occluded-vs-deleted, spatial queries for
  placement collisions.
- *Then* write the resolved state to git.

Now Elasticsearch is genuinely the thing turning noise into action, which is the entire
brief. This is also just a better architecture — the resolution logic becomes declarative
and inspectable instead of buried in a numpy function.

### Gap 2 — thin text means hybrid search has nothing to chew on

Easy to miss and it's fatal to half the prize. **BM25 and dense vectors need rich text.**
If our documents are numeric poses and one-word labels, then the beautiful four-stage
retriever query from [`12-elastic-primer.md`](12-elastic-primer.md#2f-retrievers--the-whole-thing-in-one-query)
is ranking `"mug"` against `"book"` — and reranking is pure theatre.

The text has to be real and it has to be **naturally messy**. Ours is: the VLM describes the
same object differently from every view and across every call — *"a blue ceramic mug"*,
*"cup with handle, chipped"*, *"cylindrical container, dark"*. Three descriptions, one
object, no agreement. **That is textbook entity resolution**, it's exactly what hybrid
search plus reranking exists to solve, and we produce it for free by running a VLM three
times on three views.

Keep every raw description. Never collapse them to one label before ingest.

### What we should *also* be indexing (all genuinely ours, nothing fabricated)

| source | what makes it messy | volume | cost |
|---|---|---|---|
| **Robot telemetry** — IMU, tilt, wheel encoders, motor current, LQR state | high-rate, noisy, drifting, occasionally garbage during a fall | **~50 Hz × 8 signals → millions of docs** | ~45 min |
| **Raw VLM descriptions** — every label attempt, every view | free text, contradictory across views, inconsistent vocabulary | ~45/capture | ~20 min |
| **Rejected clusters** — the ones we filtered out | the literal discard pile; why we didn't trust them | ~10–30/capture | already planned |
| **Whisper transcripts** — voice commands | misheard words, no punctuation, filler, "transcripts" is first on their list | low | ~20 min |
| **Depth/stereo health** — per-camera coverage %, dropout area, ICP residual | dropouts on textureless surfaces are our #1 perception failure | 3/capture | ~20 min |

**Robot telemetry is the big one**, and it solves the volume question honestly. The balance
loop is *already computing* all of it at high rate — we're currently throwing it away. It's
"logs" and "sensor streams", both named in the brief, at genuine scale.

And it isn't padding — it enables a real cross-index capability:

> *"Why was the diff for commit `a3f9c1` wrong?"*
> → correlate `room-observations` against `robot-telemetry` at that timestamp
> → **the odometry residual spiked 200 ms before the capture; the robot was recovering from
> a tilt, so the whole cloud was captured mid-lean.**

That's an agent reasoning across two noisy sources to explain a failure — precisely what
both Elastic and Rox say they want, and genuinely useful to us at 4am.

Millions of high-rate telemetry docs is also what makes **TSDS downsampling** load-bearing
rather than a flex: roll 50 Hz raw into 1-minute statistics automatically and keep the
cluster small. On a trial, watch ingest volume and downsample aggressively.

### What NOT to do
**Do not bolt on an unrelated corpus** — no scraped Reddit, no PDF pile, no `faker` rows, to
look "big data". It reads as padding instantly, it dilutes the pitch, and
[`06-demo.md`](06-demo.md) has to survive on one coherent story. Every source in the table
above is something this robot genuinely produces and currently discards. That's the bar.

## What actually goes into Elasticsearch (read this before writing any ingest code)

**Raw point cloud points never go to Elasticsearch.** One capture from three stereo pairs is
millions of 3D points. As documents they'd be useless (no meaningful question has the answer
"point #4,812,003"), ruinously slow to ingest, and they'd bury the data that matters.

Three tiers of data, three different homes:

| tier | scale | home | why |
|---|---|---|---|
| **Raw point cloud** | 10⁶ points/capture | **disk** (`.ply` / Rerun `.rrd`), **catalogued in ES** | too big and too meaningless per-row to index; ES stores the pointer + metadata |
| **Occupancy voxels** | 10³–10⁴ /commit | **`room-voxels`** (`point` + occupancy) | *this is the point cloud in Elasticsearch*, at a resolution where a cell means something |
| **Per-object observations** | 10¹–10² /capture | **`room-observations`** (TSDS) ← *this is what streams* | measurement series: dimensions + metrics over time |
| **Committed state** | 10¹ /commit | **`room-objects`** (regular index) | versioned state, searched semantically |
| **Robot telemetry** | 10⁶ total | **`robot-telemetry`** (TSDS + downsampling) | high-rate noisy signals; where the volume honestly lives |
| **Events** | 10⁰ /commit | **`room-events`** (plain data stream) | append-only log |

### The blob/catalog split — the standard answer for big binary data
The full cloud lives on disk as a file. Elasticsearch stores a **document about** that file:
```json
{ "capture_id": "cap_0912", "@timestamp": "...", "commit_sha": "a3f9c1",
  "cloud_uri": "file:///data/clouds/cap_0912.ply",
  "point_count": 2841003, "bounds": {...}, "cameras": ["cam0","cam1","cam2"],
  "coverage_pct": 0.83, "icp_residual_mm": 4.2 }
```
ES is the **catalog and the query layer**; the bytes live where bytes belong. Every serious
system does this, and saying so is a systems-design answer rather than a hack.

### `room-voxels` — how the point cloud genuinely gets into Elasticsearch
Voxel-downsample the fused cloud (one Open3D call) at ~5–10 cm and index only the
**occupied** cells. A room is mostly empty air, so a 5 × 5 × 2 m space yields a few thousand
occupied voxels, not the 50,000 the grid could hold.

```json
{ "commit_sha": "a3f9c1", "@timestamp": "...",
  "cell": {"x": 1.25, "y": 0.85},        // type: point  -> spatial queries
  "voxel_key": "12_08_03",                // type: keyword -> terms aggregation
  "z_min": 0.74, "z_max": 0.81,
  "density": 142, "zone": "desk", "object_id": "mug_a1b2" }
```

**Index it at commit time only**, not every watch-loop frame — ~30 commits × 3k voxels ≈ 90k
docs across the weekend. Trivial for the cluster.

What this buys, and why it isn't decoration:
- **Spatial change detection executed inside Elasticsearch.** Compare occupied cells between
  two `commit_sha` values → cells that flipped occupied↔free → *that's the diff*, computed as
  a query rather than in numpy. A genuinely nice thing to show an Elastic judge.
- **Better collision checks** for the executor's operation ordering than object bounding
  boxes give — a `shape` query against actual occupied space.
- It makes `point`/`shape` usage **substantial** (thousands of spatial docs) instead of
  thirty tidy rows.
- `cartesian_bounds` / `cartesian_centroid` over voxels = where clutter actually sits.

### Voxel keys: the 3D geohash question

Geohash is a **space-filling curve** — it interleaves the bits of lat and lon so that nearby
points share a **prefix**, and prefix length equals precision. Truncate the string, get a
coarser cell, for free.

Two things follow for us:

1. **Geohash itself is unusable here.** `geohash_grid`, `geotile_grid` and `geohex_grid` are
   **geo-only** — they operate on `geo_point`/`geo_shape`, on a sphere, in 2D. They will not
   touch a cartesian `point` field, and a room isn't a planet.
2. **The generalization you want exists and has a name.** The 3D version of "interleave the
   bits so neighbours share a prefix" is a **Morton code (Z-order curve)**, and the string
   form of it is just an **octree path** — at each level, one digit picking which of 8
   octants you descended into. That is, precisely, a geohash for 3D space. It's also exactly
   how [PCL's octree change detection](research-notes.md#perception-building-blocks) works, so
   we'd be aligning with the standard prior art rather than inventing something.

### Recommendation: octree-path strings, not bit-twiddled Morton integers

```
voxel_key = "3705261"     # 7 levels deep, each digit ∈ 0..7
             ││││└┴┴─ finer
             └┴┴┴───── coarser
```

**Prefix = region, exactly as in geohash** — just base-8 (3 bits: one per axis, so cells stay
cubic) instead of geohash's base-32 (5 bits alternating between lat and lon, so its cells
alternate wide and tall). Size ladder for an **8 m** cube:

```
"3"        4 m          "3705"    50 cm
"37"       2 m          "37052"   25 cm      <- voxel_key_l5
"370"      1 m          "370526"  12.5 cm
           ^ voxel_key_l3         "3705261"  6.25 cm   <- voxel_key
```

**The asymmetry that catches people:** prefix → region is exact and free (one filter). But
region → prefix is not — an arbitrary query box rarely aligns to the grid, so it needs a
*cover* of multiple prefixes at mixed levels. Grid-aligned questions use the prefix;
arbitrary geometry uses a `shape` query on the `point` field. Keep both.

A string gives you everything the integer would, with none of the bit-shifting bugs, and it
plays naturally with Elasticsearch `keyword` fields, `terms` aggregations and `prefix`
queries.

**Index truncations as separate fields rather than aggregating on a prefix at query time** —
no scripts, no slow path, aggregations just work:

```json
"voxel_key":    { "type": "keyword" },   // "3705261"  6.25 cm
"voxel_key_l5": { "type": "keyword" },   // "37052"    25 cm
"voxel_key_l3": { "type": "keyword" },   // "370"      1 m
"cell":         { "type": "point" }      // real coordinates, for spatial queries
```

Three extra string fields, computed once at ingest. What it buys:

- **Multi-resolution change detection from one index.** `terms` agg on `voxel_key_l3` finds
  *which corner of the room* changed; drill into `voxel_key` for the exact cells. Coarse-to-
  fine, no reindexing, and it's a genuinely good demo move: zoom from "the desk changed" to
  "these 40 cells changed" with two queries.
- **Cheap set comparison between commits** — occupied keys at commit A minus commit B is a
  set difference on strings.
- **Noise control for free.** Coarse levels are stable where fine levels flicker, so a change
  confirmed at `l5` is real and a change only at full depth is probably registration error.

### The encoder (~10 lines)

Each digit is **one bit of x, one bit of y, one bit of z** — `(bx<<2)|(by<<1)|bz` — so every
level halves the cell along all three axes. Repeatedly doubling and taking the integer part
extracts successive bits of the binary fraction, which avoids integer bit-twiddling entirely:

```python
def octree_key(x, y, z, origin, size, levels=7):
    """origin: (x0,y0,z0) corner of a CUBE of side `size` containing the room.
    Cell size = size / 2**levels  (8 m / 128 = 6.25 cm at levels=7)."""
    f = [(v - o) / size for v, o in zip((x, y, z), origin)]
    if any(c < 0.0 or c >= 1.0 for c in f):
        return None                      # outside the indexed volume — clamp or drop
    digits = []
    for _ in range(levels):
        f = [c * 2 for c in f]
        bits = [int(c) for c in f]       # 0 or 1 per axis
        f = [c - b for c, b in zip(f, bits)]
        digits.append(str((bits[0] << 2) | (bits[1] << 1) | bits[2]))
    return "".join(digits)

key = octree_key(x, y, z, origin=ROOM_ORIGIN, size=8.0, levels=7)
doc["voxel_key"], doc["voxel_key_l5"], doc["voxel_key_l3"] = key, key[:5], key[:3]
```

Use a **cube**, not the room's actual bounding box — unequal axis scales break the "cells are
cubic" property and make cross-level comparisons meaningless. Pick `origin`/`size` once from
the anchor tag, put them in `room.yaml`, and **never change them** — every key in the index
is relative to that cube, so changing it silently invalidates all history.

### Rejected: a 4D space-time hash

Someone will propose adding time as a fourth axis (probably at 3am — it sounds elegant).
**Don't.** Recording the reasons here so the argument only happens once.

**The project-specific reason, which is decisive:** our time is a **DAG, not a dimension.**
`main` and `movie-night` descend from a common ancestor, and two commits on different
branches can share a wall-clock time while describing *different realities*. A 4D hash would
put them in the same cell and silently merge them — corrupting exactly the branch semantics
the project exists to demonstrate. You cannot embed an ancestry graph in a number line; see
[the three time semantics](#time-three-different-time-semantics-and-the-agent-translates-between-them).

Three general reasons, any one sufficient:
- **No common scale.** Lat and lon are commensurable; halving either means half the distance.
  Half of *time* relative to half a metre requires an arbitrary seconds-per-metre constant,
  and that arbitrary choice would determine every cell's shape.
- **It discards a better index.** `@timestamp` on a TSDS is sorted, compressed, downsampled
  and queryable with `date_histogram`/ES|QL. You cannot `date_histogram` a hash digit.
- **Cardinality.** 4 axes → 16 children per level, not 8. At 7 levels that's 268 M possible
  cells instead of 2 M: longer keys, sparser index, fragmented buckets.

Also: space is a fixed, known cube; time is unbounded and one-directional. You'd have to pin
a time window up front, and anything outside it becomes unencodable.

**Do this instead** — composite fields, already in the mapping:
```json
"voxel_key": "3705261", "cell": {point},
"@timestamp": "...", "commit_sha": "a3f9c1", "parent_sha": "9b17e0", "branch": "main"
```
`parent_sha` is what lets the agent walk ancestry — that's how you query a DAG, and it's what
actually powers time travel.

The genuinely useful cross-space-time query is an **aggregation nest**, not a curve: a
`date_histogram` with a `terms` agg on `voxel_key_l3` inside it → *"which region of the room
was most active between 2 and 4am."*

### The trap: don't use key ranges as a substitute for spatial queries
Z-order/Morton locality is **imperfect** — cells that are adjacent in space can sit far apart
in code order at octant boundaries. A bounding box therefore maps to *several* disjoint code
ranges, not one.

So keep both fields and use each for what it's good at:
- **`cell` (`point`/`shape`)** → true spatial predicates: `within`, `intersects`, distance.
- **`voxel_key*` (`keyword`)** → aggregation, grouping, set comparison across commits.

Don't bother with Hilbert curves. Better locality, considerably more code, no payoff at this
scale.

## Voxels serve two consumers with very different latency budgets

Voxelizing the cloud helps the robot **and** the Elastic track — but not via the same path,
and conflating them is how you end up with a robot that stalls when wifi hiccups.

**Voxelize once, in memory. Then fork:**

```
 fused cloud ──voxel_down_sample()──> occupancy grid (in memory, Open3D)
                                        │
                    ┌───────────────────┴───────────────────┐
                    ▼                                        ▼
        LOCAL: collision checks,                  ELASTIC: cross-commit spatial
        placement validation, nav                 diff, analytics, agent queries
        (microseconds, no network)                (50 ms+, network, analytical)
```

Same data structure, two consumers. The robot benefit comes from **having** the voxel grid;
the Elastic benefit comes from **indexing** it. Both are real, and they're independent —
which is good, because it means the Elastic indexing can fail at 4am without the robot caring.

### Which queries may touch Elasticsearch (latency tiers)

| tier | budget | may use ES? | examples |
|---|---|---|---|
| **Control loop** | < 10 ms | **never** | balance, servo updates, drive corrections |
| **Planning** | ~100 ms, once per diff | **yes** | operation ordering, collision graph, target validation |
| **Analytical / agent** | seconds | **yes** | change detection, re-ID, occlusion resolution, search |

The planning tier is the honest version of "Elasticsearch shapes what the robot does": one
query, ~50 ms, computed **before** a 40-second motion sequence begins. That's nothing, and
the claim is true. A per-motion ES call inside the control loop would be architecturally
wrong no matter how good it sounds in a pitch — don't build it, don't claim it.

### Voxel diff is for analytics — object diff stays the truth for git

Voxel-level change detection is **noisy by construction**: every millimetre of registration
error flips boundary cells, which is the [phantom diff problem](03-perception.md#the-real-enemy-phantom-diffs)
at a finer resolution. It's excellent for visualization, heatmaps and "roughly where did the
room change", and it makes a great Rerun overlay.

**Do not let voxel diff drive commits.** Object-level association stays the source of truth
for git. Use voxels as corroborating evidence and as the spatial analytics layer.

### When to build it
This is a **T2-era task (~40 min)** — it needs a fused cloud to exist first. Don't start it
before the core `status`/`diff`/`revert` loop is solid.

## Time: three different time semantics, and the agent translates between them

"Store the time data" isn't one problem — this system has **three distinct notions of time**,
and being able to name them is a strong answer:

| | what it is | where it lives | how you query it |
|---|---|---|---|
| **Version time** | discrete, ordered, **branchable** — `HEAD~1`, `main`, `movie-night` | **git** | `git log`, `git diff a..b` |
| **Measurement time** | continuous wall-clock, high-rate, downsampleable | **TSDS** (`room-observations`, `robot-telemetry`) | `date_histogram`, ES\|QL |
| **Occurrence time** | discrete events — commits, merges, picks, failures | **`room-events`** | ES\|QL, `terms` aggs |

Git's time model has no clock — it has *ancestry*. Elasticsearch's has no branches — it has a
timeline. **Neither can answer a question posed in the other's terms**, which is exactly why
both exist here.

And the translation between them is the agent's most interesting job:

> *"Put the desk back the way it was before dinner"*
> wall-clock phrase → **LLM** → timestamp → **ES|QL over `room-events`** → `commit_sha`
> → **git** → tree → diff → **robot motion**

Three time models and a physical actuator in one chain. That's the sentence for the Elastic
judge, and it's true rather than constructed.

**TSDS is not "the database" — it's one of three indices**, the one holding the high-volume
measurement stream. Don't force the other two into it:

- `room-objects` needs **`semantic_text`** fields for hybrid search, and TSDS is
  append-only with restrictions on field types and dimensions. It's versioned state, not a
  measurement series. **Regular index.**
- `room-events` is append-only but not metric-shaped — there are no dimensions or metrics,
  just typed events. A **plain data stream** is the right fit; TSDS buys nothing.

### The boundary: one document per (object, camera, capture)

A capture produces millions of points → clustering reduces them to ~15 objects → each of the
3 cameras contributes its own view of each → **~45 observation documents.** That's the
compression step, and it's where "sensor stream" becomes queryable.

```json
{ "@timestamp": "2026-09-19T14:22:07.412Z",
  "capture_id": "cap_0912",          "object_id": "mug_a1b2",   // dimension (nullable!)
  "camera": "cam0",                                             // dimension
  "confidence": 0.87,                "point_count": 1420,       // metrics
  "raw_x": 0.4213, "raw_y": 0.1802, "raw_z": 0.7511,            // UNquantized
  "occluded": false, "rejected_reason": null,
  "raw_description": "a blue ceramic mug, handle facing left, slightly chipped",  // semantic_text
  "vlm_model": "gpt-5-vision", "label_attempt": 2 }
```
`raw_description` is the **unreconciled** free text from *this* camera's view — keep all
three, contradictions included. It's the field BM25 and Jina actually operate on, and
reconciling the three is the entity-resolution problem hybrid search exists to solve.
```
```

Note `raw_*` is **unquantized** — git gets the clean quantized truth, Elastic keeps the mess.
And `object_id` is **nullable**: clusters we couldn't match or chose to reject get indexed
too. Those rejected rows are the single most honest "messy real-world data" artifact we have.

### Write with `_bulk`, once per capture
One HTTP request per observation would be 45 round trips per scan. Batch the whole capture
into a single `_bulk` call. That's the natural granularity and it's what "streaming" means
here in practice.

### TSDS gotchas that will bite you at 3am

1. **The look-back window.** A TSDS rejects documents whose `@timestamp` is outside
   `index.look_back_time` / `index.look_ahead_time` relative to *now* (defaults are a couple
   of hours). **The moment you replay a recorded session or backfill test data with
   yesterday's timestamps, ingest silently starts failing.** Set these generously —
   `look_back_time: 7d` — the first time you create the index. You *will* replay data.
2. **Append-only.** You cannot update a TSDS document. Fine for observations; fatal if you
   tried to put committed object state there. Another reason for the split above.
3. **Dimensions must be present and low-cardinality-ish.** `object_id` + `camera` is right.
   Don't make `capture_id` a dimension — it's unique per capture, which defeats the point of
   grouping into series.
4. **Identity collisions.** A TSDS derives document identity from `_tsid` (the dimension set)
   plus `@timestamp`. Two docs with the same dimensions and the same timestamp **overwrite
   each other.** Use millisecond-or-better precision and never reuse a timestamp across a
   burst of frames.
5. Dimension fields are restricted by type (`keyword` and some numerics). Map them explicitly.

## Watch mode — where the "stream" actually comes from

There's a tension worth resolving explicitly. [`01-concept.md`](01-concept.md) argues that
commits are **discrete events, not a video stream**, and that reframing is what makes the
project feasible. So where does a *stream* of sensor data come from?

**Two paths into the system, deliberately different costs:**

- **Commit path (expensive, on demand).** Full 360° capture, 3-way fusion, clustering,
  VLM labeling, association, quantization, git commit. Seconds per run. Discrete.
- **Watch path (cheap, continuous).** Single forward camera, the stock YOLO/segmentation
  detections at ~1–2 Hz, no fusion, no labeling, no git. Just: *what does cam0 see right
  now, with what confidence*. Streams straight into `room-observations`.

The watch loop doesn't break the feasibility argument because it skips everything expensive.
And it earns three things:

1. **Genuine streaming sensor volume** — 45 docs/s rather than 45 docs/scan makes
   aggregations, downsampling and TSDS *obviously* the right tool rather than a flex.
2. **Much better occlusion-vs-deletion evidence** — 40 continuous observations of an object
   beats 3 snapshots, by a lot. This directly improves the decision quality in
   [the ambiguity resolution](#1-occlusion-vs-deletion-our-hardest-ambiguity-r8).
3. **A demo beat that's hard to beat:** a `date_histogram` of room dirtiness showing a **step
   change at the exact second the judge moved the cup.** Git tells you *what* changed;
   the time series tells you *when*, to the second. That's `git blame` with a timestamp, and
   it only exists because the data is a real time series.

Build the watch loop **after** T1 — it's ~1 hour and it is what makes the Elastic story
about streams rather than snapshots.

## Index design

### `room-objects` — committed state, one doc per object per commit
```
object_id, class, description (semantic_text), embedding,
color, extents, pose {x,y,z,yaw}, position (point),  ← cartesian spatial queries
zone, commit_sha, branch, author, timestamp, confidence
```
Powers: hybrid retrieval (BM25 on label/description + dense vector on appearance +
reranking), spatial queries via the `point`/`shape` field types on room coordinates
("within 50 cm of the lamp" is a real query, not a fake one), and `git blame`-by-meaning.

### `room-observations` — the mess, one doc per raw detection per camera per capture
```
capture_id, object_id (nullable — unmatched clusters included!),
camera, point_count, confidence, occluded (bool), rejected_reason,
raw_pose (unquantized), timestamp
```
High-volume time series. This is where **ES|QL and aggregations** earn their place, and
where the "conflicting sources" story lives: cam0 says x=0.42, cam2 says x=0.47, cam1
didn't see it at all. Include the clusters we *rejected* — that's the honest mess.

### `robot-telemetry` — the high-rate mess (TSDS)
```
@timestamp, signal (dimension: pitch|tilt_rate|left_enc|right_enc|motor_current_l|
                              odom_residual|balanced|...),
value (metric: gauge)            # one doc per (signal, sample) — nothing else is mapped
```
~50 Hz. Declare downsampling so raw points roll into 1-minute stats. This is where the
"lots of noisy sensor data" story actually lives, and it's free — the LQR loop already
computes every one of these values and currently drops them on the floor.

### `room-events` — commits, diffs, merges, conflicts, robot actions
```
event_type (commit|revert|checkout|merge|conflict|pick|place|failed_op),
commit_sha, branch, objects_affected[], message, outcome, timestamp
```
Powers time-travel ("before dinner" → sha) and room analytics.

## Searching through time: *"find the hammer"* when the hammer is gone

This is the query only a versioned system can answer, and it's the best demonstration of why
git and Elasticsearch are both here.

### The unlock: we never delete, so the default is omniscience

Every commit writes **all** of its objects as new documents — a full snapshot, exactly like
git storing trees rather than diffs. So an object being removed from the room is **a row that
stops appearing**, never a row that gets deleted.

> **Searching the whole index searches all of time. Filtering by `commit_sha` is what narrows
> you to a moment.** Omniscience is the default; the present is a filter.

That's backwards from a normal database, and it's the entire reason temporal search is easy
here. Storage cost is nothing: 30 objects × 50 commits = 1500 documents.

*(The compact alternative is the classic temporal/SCD-2 model — one row per object per
existence span with `valid_from`/`valid_to`. More elegant, more write-path complexity,
and it buys us nothing at this volume. Snapshot-per-commit is correct for us.)*

### The flow for "find the hammer"

**1 — Search all of history, no commit filter.** Hybrid retrieval over every object that has
ever existed:
```
retriever: text_similarity_reranker( rrf([ BM25 on class, semantic on raw_description ]) )
query: "hammer"
```
This is where [keeping every messy VLM description](#gap-2--thin-text-means-hybrid-search-has-nothing-to-chew-on)
pays off — the object may have been labelled *"hammer"* from one view, *"mallet"* from
another, *"tool with a wooden handle"* from a third. BM25 alone catches the first; the dense
vector catches all three. **This is the moment to point out to a judge that lexical search
would have missed it.**

**2 — Where and when was it last seen?**
```sql
FROM room-objects
| WHERE object_id == "tool_4f2a"
| SORT @timestamp DESC
| LIMIT 1
| KEEP commit_sha, branch, zone, pose.x, pose.y, pose.z, @timestamp
```

**3 — Its whole lifetime:**
```sql
FROM room-objects
| WHERE object_id == "tool_4f2a"
| STATS first_seen = MIN(@timestamp), last_seen = MAX(@timestamp),
        commits = COUNT_DISTINCT(commit_sha), zones = COUNT_DISTINCT(zone)
        BY object_id
```

**4 — Was it removed, or just occluded?** The commit *after* `last_seen` is when it vanished.
Check the raw evidence in `room-observations` — did every camera have clear line of sight, or
did the one camera that ever saw it get blocked? Same
[occlusion-vs-deletion](#1-occlusion-vs-deletion-our-hardest-ambiguity-r8) machinery.

**5 — Answer, and act:**
> *"The hammer was last on the workbench at 14:22 on the 19th, commit `a3f9c1`. It's absent
> from every commit after that. All three cameras had clear line of sight, so it left the
> room — it wasn't hidden."*

...and the robot **drives to the spot where it used to be and points at the empty space**,
while Rerun renders the hammer's voxels *from the historical commit* as a translucent ghost.

**That's the demo beat.** It's strictly better than "where are my keys" (where the object is
still present), because pointing at an absence is something no non-versioned system can do.
Worth upgrading [`06-demo.md`](06-demo.md) to use this if the hardware is cooperating.

### The seven temporal questions, and how each is answered

All of them fall out of two fields — `@timestamp` and `commit_sha`:

| question | how |
|---|---|
| *Where is X **now**?* | filter `commit_sha == HEAD` |
| *Where was X **at time T**?* | resolve T → sha via `room-events`, filter that sha |
| *Where was X **last seen**?* | no commit filter; `SORT @timestamp DESC \| LIMIT 1` |
| ***When did** X disappear?* | last sha it appears in → the commit after it |
| *What has **ever** been in this room?* | `COUNT_DISTINCT(object_id)` over all history |
| *What's here now that **wasn't this morning**?* | set difference on `object_id` between two shas |
| *Everything that's **ever been on the desk**?* | filter `zone`, no commit filter, group by `object_id` |

### Branches complicate "the past" — say so honestly
If the hammer only ever existed on the `movie-night` branch, it's in history but **not in
`main`'s ancestry**. Group by `branch`, or filter to commits reachable from HEAD, depending
on the question. *"It exists on another branch"* is a legitimate and rather funny answer for
a physical object, and it's the kind of honest edge-case handling the serious prize briefs
reward.

## Agent tools — closing the loop

The point of their brief is agents that **act**, not chat. Our agent's tools split cleanly:

**Retrieval (Elasticsearch):**
- `search_objects(query)` — hybrid search over object history
- `observation_history(object_id, window)` — the raw mess, for ambiguity resolution
- `room_analytics(esql)` — aggregations / time-series
- `spatial_query(near, radius)` — cartesian proximity

**Action (the robot):**
- `room_commit(msg)`, `room_checkout(ref)`, `room_revert(ref)`
- `move_object(id, target_pose)`
- `goto_and_point(object_id)`

So the full chain is:

> **user speaks → agent runs an ES|QL time query → resolves a commit sha → calls
> `room_checkout` → a physical arm picks up a physical mug and moves it.**

*The agent's final tool call moves a real object in the real world.*
That is the most literal possible reading of "Workflows that close the loop by taking
action, not just answering questions," and no other Elastic entry will have an actuator at
the end of it. Lead with that sentence.

**We roll our own agent loop — Agent Builder is not used, so don't claim it.** `agent/tools.py`
calls Elasticsearch directly (`search_objects` runs `elastic/queries.py`'s hybrid retriever), and
its last tool, `room_revert`, has the robot move the objects. The true pitch line: *an agent
whose last tool call moves a real object.*

## Setup — do this tonight (verified 2026-09-17)

### Pick **Serverless**, not Hosted. This is the one decision that matters.

The trial gives you **one hosted deployment and three serverless projects, no credit card,
14 days**. It is very easy to click the wrong one.

- **Agent Builder is GA on Elastic Cloud Serverless and enabled by default** — it's the
  default chat experience, nothing to turn on. (We ended up not using it — see "We roll our own
  agent loop" above.)
- On **Elastic Cloud Hosted** it needs **Stack 9.3+** and lands in the **Enterprise tier**.
  On a trial deployment you may find the feature the prize explicitly names is simply not
  there — and you'd discover that at hour 20, on venue wifi.
- Project type: **Elasticsearch** (the Search solution), not Observability or Security.
- 14 days from tonight covers Sep 18–20 comfortably.
- Optional: `agentBuilder:experimentalFeatures` in Kibana advanced settings unlocks
  experimental bits. **Workflows is still tech preview** — plan for tools to work and
  workflows to be flaky, not the reverse.

### Tonight's checklist
- [ ] Sign up at cloud.elastic.co → **Serverless** → **Elasticsearch** project.
- [ ] Confirm **Agents** appears in the global search / nav. If it doesn't, you're on the
      wrong deployment type — fix it now, not Saturday.
- [ ] Create an API key; put it in `.env`; verify from the laptop with a `curl` against
      `_cluster/health` or an ES|QL query.
- [ ] Create the three indices and mappings (`room-objects`, `room-observations`,
      `room-events`) as an **idempotent `setup_elastic.py`**. You will blow these away and
      recreate them a dozen times over the weekend; make that one command.
- [ ] Wire the inference endpoints (below) and smoke-test one embedding call.
- [ ] Index ~10 objects from the **fake scene generator** and run one hybrid search. Once
      this returns, the entire Elastic track is unblocked from robot hardware.
- [ ] Ask at the booth Friday whether there's a sponsor org or credits you should be under.
      No downside to having spun up early — you can move or recreate a project in minutes.

## Embeddings: don't roll your own
Their brief names exactly three things: **BM25, Jina dense vectors, and reranking.** All
three are available without us writing an embedding pipeline:

- The Elasticsearch **open inference API supports Jina AI embeddings _and_ Jina rerank**
  (`jina-embeddings-v3`, `jina-reranker-v2-base-multilingual` / `v3`).
- `jina-embeddings-v3` is served on the **Elastic Inference Service (EIS)**, and
  `semantic_text` is moving to default to it — map a field as `semantic_text` and
  Elasticsearch generates embeddings on ingest for you.

So the prize's exact trifecta is: BM25 on `class`/`description`, a `semantic_text` field
backed by Jina for dense retrieval, and a Jina reranker on the merged result set. That's a
mapping and one retriever query, not a pipeline. **Verify current syntax in live docs** —
this area moves fast. Fallback if EIS fights us: embed crops with whatever VLM we're already
running and index a plain `dense_vector`.

## Demo beats — cheap, and two of them are excellent

Elastic is invisible unless we show it. Three candidate beats, ranked by value per second:

### A. "Where did I leave my keys?" — ~15 s, **best relatable moment in the whole demo**
```
room search "where did I leave my keys"
→ keys_7c2e — last seen zones/shelf, 14:22, commit a3f9c1 (2 commits ago)
```
...and the robot **drives over and points at them**. Every human on earth understands this
problem. It's `git blame` for your keys, it's pure hybrid search over history, and it works
even if grasping never works (fallback rung 2). If we only do one Elastic beat, do this one.

### B. Natural-language time travel — **~10 s marginal cost**
Replace typing `room checkout a3f9c1` with saying:
> *"Put the desk back the way it was before dinner."*

Same robot motion, better line, and it demos OpenAI voice + Rox agent + Elastic ES|QL
simultaneously. Nearly free demo time because it substitutes for a command we were
already going to run.

### C. Room analytics panel — ~20 s, good filler while the robot drives
A few ES|QL aggregations on screen: most volatile zone, busiest hour, objects that have
never moved (the room's static skeleton), most-moved object of the weekend. Put it in a
Rerun text panel. It fills the 30-second pick-and-place silence with something interesting
instead of apologising for the wait.

Recommendation: **B always** (it's free), **A as the dedicated Elastic beat**, C only as
filler. See [`06-demo.md`](06-demo.md).

## Cost, honestly

| piece | cost | notes |
|---|---|---|
| Cloud instance + index mappings | 30 min | |
| Dual-write objects + events from the commit path | 1 h | one function call at the end of `room commit` |
| Observation logging (the mess) | 30 min | just dumping what we already compute |
| Embeddings via `semantic_text`/Jina | 1 h | much less if their inference API behaves |
| Hybrid search + rerank query | 1 h | |
| ES|QL time-travel + analytics queries | 30 min | |
| Agent tools wired to ES | 1.5 h | **shared with the Rox build — near-zero marginal** |

**~5 h standalone, ~3 h marginal** given that the agent exists anyway for Rox.

## The two conditions

Promoting this is right, but only with guardrails, because the failure mode is real:

1. **One person owns "agent + Elastic" as a dedicated track from H+0, and that track never
   touches the robot.** This is the crucial point: perception, arm, and balance work are
   *hardware-serialized* — they bottleneck on one physical robot. Elastic work is pure
   laptop work. It genuinely parallelizes instead of competing for the critical path.
   With 4 people this is close to free; with 2 people it is not, and should be cut.
2. **It must never become a chatbot about the room.** Their brief explicitly rules that
   out, and it would also dilute our pitch. Elastic is the agent's substrate and the
   trigger for physical action — if a beat ends with text on a screen rather than the robot
   moving or pointing, cut the beat.

**Kill criterion:** if by **H+24** the core `status`/`diff`/`revert` loop is not solid,
the Elastic track stops and that person moves to demo polish and rehearsal. The Bracket Bot
prize and the finalist award are worth more than a second sponsor prize, and a shaky core
demo loses both.
