# 10 — Open questions

## Ask the Bracket Bot booth, Friday, first thing

They want a great demo on their hardware. Go early, be specific.

1. **What exactly does "Bracket Bot with arms" ship as?** One SO-101 or two? What's the
   mount? Is there a bracket already, or are we fabricating one?
2. **How are the arm servos wired?** Feetech bus-servo USB adapter, or something custom?
   Is LeRobot already installed/working on the Pi image?
3. **Has anyone run the arm while the bot is balancing?** Is there existing CoM
   feedforward, or does the LQR just eat the disturbance? Any tuning advice?
4. **Is `docs.bracket.bot` live somewhere else?** The domain didn't resolve for me on
   2026-09-17 and the repo README points at it.
5. **Extra stereo camera modules** — do they have spares, and what's the exact part so we
   can match the stock calibration model (fisheye, 2560×720 MJPG)?
6. **Any RealSense in the building?** Their setup scripts support it and it would
   substantially de-risk depth.
7. **Payload limit** on the chassis — can it carry an arm plus two extra cameras plus a hub
   and still balance well?
8. **Is there a charging/hot-swap battery situation?** Runtime per charge matters a lot for
   a 36-hour build with continuous testing.

## Decisions we owe ourselves — before H+0

- [ ] **Project name.** GITSPACE vs `HEAD~1` vs WORKTREE. Affects the Devpost and the
      first line of the pitch. Pick tonight.
- [ ] **Arm mount geometry.** Low and near the wheel axis is the physics answer; check it
      against reach — the arm still has to reach a table top. There may be a conflict here,
      and it's better to discover it tonight with a tape measure than Saturday with a drill.
- [ ] **Table height for the demo.** Follows directly from the above. Then bring/borrow a
      table of that height, or plan to demo on whatever the venue has and verify reach on
      Friday.
- [ ] **Roles.** Perception / robot / git-CLI-agent / float+Devpost.
- [ ] **Object set** — buy tonight, and photograph the committed layout so we can reset the
      table between judging runs.
- [ ] **Do we demo on a table, or a floor region, or both?** Recommendation: **table only**.
      SO-101 is a tabletop arm and floor manipulation from a balancing base is a different
      project.

## Technical unknowns to resolve early (cheap experiments)

- [ ] **How good is the stock stereo depth, really?** Run `example_depth.py` on the actual
      demo table in hour 1. Everything downstream is built on this answer. If depth on the
      demo surface is poor, we find out while there's still time to change surfaces.
- [ ] **How much does wheel odometry drift** over a 3-minute demo with 4–5 drives? Measure
      it. Determines how hard the anchor has to work.
- [ ] **What quantization actually makes `test_idempotent_scan` green?** Start at 1 cm / 5°
      and loosen until stable. Record the number; it's a real result worth mentioning.
- [ ] **Can the Pi handle round-robin capture from 3 cams** in under 2 s total? Test with
      two cams the moment we have them.
- [ ] **What does SGBM do with our specific objects?** Test the object set before committing
      to it. Swap out anything that produces a hollow or noisy cluster.

## Design questions that are genuinely open

- **Should `commit` store the raw point cloud?** ([`04-git-semantics.md`](04-git-semantics.md#binary-blobs))
  Nice for time travel and the Rerun timeline; bloats the repo. Leaning: downsampled cloud
  or a panorama JPEG, and drop it if it causes trouble.
- **Who is the "author" of a commit?** If we can attribute changes to a person, `git blame`
  on a room becomes genuinely powerful ("who put that there?" is the classic framing) — but
  it's also the point where this stops being playful and starts being surveillance.
  Leaning: **commits are authored by the robot, sessions are anonymous, people are never
  stored.** It's the right call ethically and it's a *better demo line* than the creepy
  version. Worth being able to answer crisply if a judge probes it.
- **Does `checkout` of an old commit try to restore *everything*, or just the diff?** Just
  the diff from the currently-observed state — that's what git does, and it minimizes robot
  motion. Confirm the executor is written that way.
- **What happens when the room contains an object the repo has never seen and can't
  identify?** Currently: untracked, unlabeled, `class: unknown`. Fine. But is an unknown
  object committed at all? Leaning: yes, with `class: unknown` — geometry is still
  trackable and reverting an unknown object is still useful.

## Spec drift — found while integrating

Kept by the integration session. **Resolved** rows record what was decided and where, so
nobody re-litigates them; **open** rows name who owns the answer.

| # | drift | status |
|---|---|---|
| D1 | `04-git-semantics.md`'s object YAML carried `confidence` and `observed_by`; `20-perception-logic.md` Part 5 says they must never be committed (they change every scan). | **Resolved 2026-09-18.** Frozen without them, in `roomctl/state.py`; `04` updated. |
| D2 | The id formula hashed `first_seen_commit`, which doesn't exist at scan time — the id is inside the files the commit is made of. | **Resolved.** Hash the first-seen `capture_id` instead: `roomctl.state.new_id`. `20` updated. |
| D3 | `color` is measured from pixels and jitters, but wasn't in the hysteresis list. | **Resolved.** `color`, `class`, `first_seen` are identity fields: set at first sight, carried forward, never re-measured. |
| D4 | **Yaw.** Perception's yaw is the PCA axis of the footprint: it can't tell front from back (180° ambiguity for *every* object), round footprints have none, square ones have a 90° ambiguity. The frozen schema said [0, 360) — every flip would be a phantom move. | **Schema resolved 2026-09-18:** yaw is an *axis* of `extents.x`, [0, 180); round footprints (aspect < 1.2) are 0 (`cluster.py` already does this). **Resolved in code 2026-09-18:** `serialize.py` compares modulo 180; `associate.py` holds the committed axis inside `YAW_BAND` (the near-round flip). The executor gets an axis, which is all a top-down grasp needs. **Also open — `associate.py`:** a footprint near the round cutoff (a 10×8 cm box measures aspect 1.19–1.25) flips between yaw 0 and its real yaw; keep the committed yaw while the aspect is near 1.2 (`Instance.footprint_aspect()`). `cluster.py`'s yaw itself is now stable to ±1° across rescans. |
| D5 | `elastic/TASK.md` says five index mappings; `14-elastic-flow.md` and `elastic/README.md` list six (three indices + three data streams). | **Resolved** in favour of six — `elastic/` built six. |
| D6 | Copying the commit message onto every `room-objects` doc makes every object in a commit match its text in BM25 ("mug moved" hit the cup). | **Resolved.** No `message` on object docs; it lives on `room-events`, joined by `commit_sha`. |
| D7 | `.env`'s `ELASTIC_API_KEY` holds a URL, not a key. Nothing reaches Elasticsearch until it's replaced. | **Open — needs a human** (Kibana → API keys → Encoded). See `elastic/NOTES.md`. |
| D8 | `room-observations` needs two fields `13-ingest.md` doesn't list: `raw_label` (stock YOLO class) and `label_attempt`. | **Resolved** — `elastic/` maps them. Contract: `fake/README.md` "The documents". |
| D9 | **Nobody owns `perception/pipeline.py`** (`scan_into(repo_dir, recording)`): capture → depth → fuse → segment → merge → associate → serialize, end to end. Both perception TASK files assign stages, not the pipeline; it's the one thing the G2 gate needs to run on real data (`tests/test_idempotent_scan.py` skips until it exists). | **Open — needs an owner (a human call).** The pointcloud session has flagged it too. Notes from them: call `depth_capture` inside `obs.capture_scope(capture_id)`, because `capture_quality()` tags whatever scope is current. |
| D10 | `docs/16-api.md` §2.1 still says capture is **round-robin** (one camera at a time, ~1.5 s); `docs/22-camera-sync.md` says that skew is 50× the quantum and latches all three within milliseconds. | **Resolved in favour of docs/22** — the fake now latches together and carries `skew_ms`. `16` §2.1 needs its "Why round-robin" paragraph rewritten by whoever owns the Pi `/capture` (robot/). |
| D11 | `obs.py` (repo root, no owner listed in TEAM.md): `obs.span` passes `description=`, deprecated in sentry-sdk 2.69 in favour of `name=`. | **Resolved 2026-09-18 (cloud):** `name=` in `span`, `agent_tool`, `agent_turn`; no DeprecationWarning under `-W error`. |
| D12 | `scripts/story_demo.py`'s `room-observations` docs carry `raw_x` only; the contract (`fake/README.md` "The documents", docs/11) is `raw_x raw_y raw_z`. web/ copes with both, but the per-camera spread and anything spatial needs all three. | **Resolved 2026-09-18 (cloud):** per-camera `raw_x/raw_y/raw_z`; the x spread (the 49 mm story) is unchanged. Not re-run — Sentry is paused. |

---

# Integration gaps found by the connection audit — 2026-09-18

Six modules were built against documents. The seams were never wired. Import graph as built:

```
  perception  →  roomctl, obs          ✓ correct — roomctl owns the schema
  web         →  obs                   ✗ has its OWN ES client
  telemetry   →  obs, telemetry        ✓
  roomctl     →  (nothing)             ✗ never reaches Elasticsearch
  elastic     →  (nothing)             — leaf, fine
```

## GAP 1 — nothing indexes a commit into Elasticsearch  ·  **blocks the demo**

`docs/14-elastic-flow.md` Phase 2 step 8 says the commit path bulk-writes the snapshot
(objects, voxels, cloud doc, event). It does not happen:

- `roomctl/cli.py` commits to git and stops. No import of `elastic`, no call to `ingest.py`.
- `elastic/ingest.py` is only reached by `telemetry/hub.py`.
- **`fake/scene_gen.py` is the only thing that writes objects to the cluster.**

So everything Elastic-facing — search, `/capture/<id>`, history, the whole read path — works
against fake data only. The moment real perception runs, **the cluster stays empty and every
panel silently returns nothing.** Nothing throws.

**Fix:** a `roomctl → elastic` hook after a successful commit. It must translate `id` →
`object_id` (see GAP 2) and attach `obs.trace_fields()`. Owner: **master**, with elastic.

**Status (elastic side):** `ingest.commit_actions` = every object via `records.to_es_doc` + the
commit event via `records.commit_event` (from `Commit.changes`). Its docstring example used the
working tree; fixed 22:27 to `repo.records(c.sha)`, per master's note below.

**Status 2026-09-18 ~23:30 — BUILT (master), offline-tested; first live send after 01:00.**
`roomctl/publish.py publish_commit(repo, commit)` runs after every commit `room` makes outside
the fake — `room commit`, `room init`, and `room revert` (a revert is a commit):
- objects + commit event through `ingest.commit_actions` → `records.to_es_doc` (GAP 2's
  `id` → `object_id`), `trace=obs.trace_fields()` on every doc;
- voxels through `voxelize.index_staged` when a scan staged them (P6, now called);
- what only the scan knows comes from `<repo>/.git/gitspace/scan.json`
  (`publish.stage_scan(repo, capture_id, at, meta_by_id)` — **the D9 pipeline must call it**,
  with `MergedObject.object_fields()` per object);
- built from the **committed** tree (`repo.records(sha)`), not `read_tree(repo.path)` as
  `commit_actions`' own docstring shows: after `room add zones/desk/`, the working tree holds
  changes the commit doesn't, and the snapshot would record objects that aren't in it;
- a parked or unset key builds **no client at all** (web's `usable()` rule) and the actions
  go to `<repo>/.git/gitspace/spool/<sha>.json`; so does a cluster that doesn't answer.
  `room publish --flush` sends the spool. A rejected doc is reported, not spooled.
`tests/test_publish.py` (14, boundary mocked, run with the network blocked). The fake keeps
writing its own, richer documents for fake commits.

## GAP 2 — `id` on the git side, `object_id` on the Elasticsearch side

| side | field |
|---|---|
| `roomctl/state.py` `ObjectRecord`, `perception/serialize.py`, the YAML on disk | **`id`** |
| `elastic/mappings/*.json`, all 16 queries in `queries.py`, `ingest.py` | **`object_id`** |

Both are defensible in isolation (`id` is confusable with ES `_id`), so **keep both and make
the translation explicit and tested** — do not rename either side.

`fake/scene_gen.py:761` already does it (`"object_id": r.id`). The real path has no equivalent,
and `ingest.py:27` computes `room-objects` document ids from `d['object_id']` — so a doc
arriving with `id` gets a **wrong `_id`**, silently breaking idempotent re-indexing.

**Fix:** one `to_es_doc(record)` function, one test asserting the key set matches the mapping.

**Status: done on the elastic side.** `elastic/records.py to_es_doc(record, commit, at=,
capture_id=, meta=, trace=)` — every mapped field always present (None when unknown), key set
pinned to `mappings/room-objects.json` by `elastic/tests/test_records.py` (21 tests, offline;
also: voxel_key from perception's own encoder + pinned cube, roomctl's `validate()` first,
unknown meta/trace keys raise). `ingest.action()` now REFUSES a git-shaped doc (`id` without
`object_id`) and any empty/None id field. Correction to the note above: a doc with `id` hit a
bare `KeyError` at ingest.py:27 (loud, not a wrong `_id`) — the silent case was a `None`
`commit_sha`, which made `_id` `"None:mug_a1b2"`. Both are refused now.

## GAP 3 — the hybrid retriever is implemented twice

`elastic/queries.py` and `web/dash_api.py` both build rrf + rerank + semantic queries
independently. Two implementations of the search that is the centrepiece of the Elastic
submission. They will drift, and the one the judge sees is whichever the dashboard calls.

**Fix:** `web` imports `elastic/queries.py` rather than rebuilding it. If web needs response
shaping, that belongs in a thin adapter over the shared query, not a second query.

## Not a gap — verified correct
- `perception` imports `roomctl.state` for the schema and quanta. Right direction.
- `ingest.py` puts `_index` on the **action line**, not in `_source` — the earlier bug is fixed.
- `scene_gen.py` emits correct `_bulk` format and translates `id` → `object_id`.

---

# web — connection audit, 2026-09-18 22:30 (all external APIs parked)

## GAP 3 — RESOLVED: web runs `elastic/queries.py`, it no longer rebuilds the retriever
`web/dash_api.py` lost its own rrf + rerank + semantic builder (75 lines). `/api/search` is now a
thin adapter: `Queries.search_objects()` for the ranked list, `Queries.lexical_only()` and
`Queries.semantic_only()` for provenance, all through `web/es_shared.py` (the official client
behind a proxy that logs, traces and maps errors; no request translation). elastic-09 added
`semantic_only()` + a shared `_semantic()` and put `capture_id` in the timeline at web's request;
`elastic/tests/test_query_shapes.py` asserts the two probes send exactly the fused legs' clauses.
Web keeps ONE thing: the cut that turns semantic scores into "the vector leg found it"
(`VECTOR_FLOOR 0.62`, `VECTOR_REL 0.85`, calibrated on live scores: noise floor 0.556).
Tests: `web/tests/test_search_adapter.py` — a fake client handed to the REAL `Queries`, 6 passing,
no network. **Consequence: web must run from the repo-root `.venv`** (it has `elasticsearch`;
the system python web had been started with does not).

## GAP 4 — parking a key with `KEY=# comment` does NOT unset it  ·  **crashes on restart**
python-dotenv reads `SENTRY_DSN=# parked…` (no space before `#`) as the literal VALUE
`"# parked…"`. So while keys are parked:
- `obs.init()` sees a non-empty DSN, calls `sentry_sdk.init(dsn="# parked…")`, and that RAISES
  `BadDsn: Unsupported scheme ''`. It does not return False. Every process that calls
  `obs.init()` at import (web did; the Pi server, the laptop pipeline, `telemetry/hub.py` will)
  **dies on its next restart**. Verified with sentry's pure DSN parser, no init, no network.
- `ELASTIC_API_KEY` / `OPENAI_API_KEY` are non-empty garbage, so any `if key:` check says
  "configured" and the garbage is SENT to the real service on every call (a 401 each, not a
  no-op). `fake/scene_gen.py:996` reads the key this way.
- `SENTRY_DSN_WEB` was left in place: the browser SDK kept sending sessions and replays.
**Fix, either:** park as `KEY=` (empty) with the comment on its own line; or make `obs.init()`
return False unless the DSN parses (owner: obs.py). web no longer depends on it: `usable()` /
`parked()` in `web/server.py` discard any value that starts with `#` or contains whitespace,
make NO call while `<KEY>_PARKED` exists, serve fixtures (`elastic_paused`), and turn the
browser DSN off whenever the server DSN is parked.
**Also:** a process started before parking keeps the live keys IN MEMORY and keeps calling —
web's server did until it was restarted. Parking needs a restart of every long-running process.
**elastic (22:15):** `setup_elastic.connect()` now treats a leading `#` as unset and raises
`SetupError` before any client exists when `ELASTIC_API_KEY_PARKED` is set
(`elastic/tests/test_connect.py`). `telemetry/hub.py` and `scripts/story_demo.py` go through it, so
they spool / fail without a network call.

## GAP 5 — `store.py` still duplicates reads that `queries.py` owns (GAP 3's smaller sibling)
`web/store.py` (captures, objects, graph enrichment, telemetry board) issues its own `_search`
bodies for things `Queries` already has with live tests: `capture_docs`, `telemetry_window`,
`trace_docs`, `last_seen`, `lifetime`, `observation_history`. Same drift risk, lower stakes.
Plan: move `store.py`'s ES path onto `Queries` via `es_shared`, keep its labelled fixture
fallback. Owner: web. Not started.

## Verified correct — web's other connections
- **telemetry hub → web.** `telemetry/hub.py` `SSESink` POSTs `{"event":"telemetry","data":{…}}`
  to `WEB_EVENTS_URL` (default loopback from `WEB_BIND`), exactly what `POST /api/internal/event`
  accepts. One drift: the hub sends `tilt_rate_peak` (the peak since the last frame — the value
  that survives 2 Hz decimation) and every signal, while `web/events.py` documents only
  `ts pitch tilt_rate balanced odom_residual`. The live strip must plot `tilt_rate_peak`.
- **git → web.** `web/room.py` reads `room.git` with `git --no-optional-locks` only; `/api/status`,
  `/api/graph`, `/api/diff` handle `id` (git side) vs `object_id` (ES side) per GAP 2.

## Still open on web's edges (each needs the OTHER side)
- **roomctl → web, `job` events and `POST /api/command`.** web validates, plans the ops, returns
  `202 {executor: "not_connected"}` and publishes `job … queued`. Nothing consumes it:
  `roomctl/executor.py` does not exist. Contract it should meet: read the command, POST `job`
  progress to `/api/internal/event` as `{"event":"job","data":{"id","state","progress"}}`.
- **roomctl commits carry no capture id.** web's `capture` SSE event therefore has `commit_sha`
  only. A `Capture-Id: cap_NNNN` commit trailer would let the graph link a commit to its capture
  without Elasticsearch.
- **story_demo vs scene_gen documents disagree on completeness.** `scripts/story_demo.py`
  observations carry `raw_x` only; `fake/scene_gen.py`'s carry x, y, z. web compares per axis
  now (it used to demand all three and hid a 49 mm spread). The real ingest should send all three.
- ~~**The reranker reads only the FIRST of an object's three descriptions**~~ — fixed 2026-09-19:
  it now scores `rerank_text` (class + all three), built server-side by an ingest pipeline (D38).

---

# Perception (pointcloud) seams — audit, 2026-09-18 ~22:30

Checked against the other modules' **code**, not their docs: field names, frames, units.
"Built" means the perception side of the seam now exists and is tested; the other side may not.

| # | seam | mismatch | status |
|---|---|---|---|
| P1 | `segment` → `merge` | `segment.lift()` returns instances in **each camera's F_rect**; `merge` compares in F_world; nothing did the move. | **Built (segment session):** `segment.run(..., mount=)` lifts in F_rect, then calls `fuse.rect_to_world` (lift's edge filter needs F_rect: column 2 is range there, height in F_world). `fuse.instances_to_world` was removed — a second path that could transform twice. **Resolved:** `segment.run(..., mount=, robot_pose=)` — `robot_pose` required with `mount` (ValueError otherwise, never a silent origin); pinned by the segment session's tests. |
| P2 | robot pose ↔ F_world | `/capture`, `/pose`, `/drive` speak BB odometry `{x, z, yaw}` (z = LEFT, yaw rad in [0, 2π)); perception, costmap and `/arm` speak F_world `(x, y, yaw)`. | **Built:** `fuse.odom_to_world` / `world_to_odom` — a relabel z↔y, **no sign flip**, verified against BB's `example_localization.py` update maths. `fuse()` and `rect_to_world()` now take `robot_pose`. Must NOT reuse `cam_to_world_axes` (it swaps X and Z). |
| P3 | `docs/16` units | `yaw` is **radians** in `/drive` and `/pose` (1.57, 0.31) but **degrees** in `/arm`'s pose (15) — same key, two units, one API. | **Open — robot/ owners.** Suggest `yaw_deg` on `/arm`, or radians everywhere. costmap returns radians. |
| P4 | `obs.init()` × parked DSN | Parked `SENTRY_DSN` is non-empty garbage (`# parked…`); `obs.init` only checks emptiness, so `sentry_sdk.init` **raises `BadDsn`** (confirmed in the SDK source: `transport.py` parses the DSN at init). `web/server.py` guards this; `telemetry/hub.py` and `scripts/story_demo.py` don't. | **Open — obs.py's owner:** apply `web/server.py`'s `usable()` rule inside `obs.init`. `perception/depth.py`'s CLI (whose `obs.init` call another session added) is now guarded. |
| P5 | parked `ELASTIC_API_KEY` | `elastic/setup_elastic.connect()` has no `usable()` check: with the key parked it still sends `# parked…` to the real cluster and gets a 401 (a round trip per call). The parked-key rule lives only in `web/server.py`. | **Open — elastic/ + obs.py owners:** one shared `usable()`/`parked()` helper. `perception/voxelize.py` short-circuits already (spools, no network). |
| P6 | voxels ↔ commit (GAP 1) | `room-voxels` `_id` needs `commit_sha` (`ingest.natural_id` refuses an empty one), but the scan runs before `room commit` exists — the scan has voxels, the commit has the sha. | **Built:** scan side `voxelize.stage(grid, room, claims)` → `<room>/.git/gitspace/voxels.npz`; commit side `voxelize.index_staged(room, sha, parent, branch, ts)`. **Resolved:** `roomctl/publish.py` calls `voxelize.index_staged(repo.path, sha, parent, branch, commit_time, es=client)` after every non-fake commit when `.git/gitspace/voxels.npz` exists. **Pipeline (D9):** stage with `voxelize.stage()` next to `roomctl.publish.stage_scan()`. |
| P7 | ES down / parked | Nothing on the write path survived Elasticsearch being away. | **Built for room-voxels:** retries with backoff (connection, timeout, 408/429/5xx, per-document 429s resent alone), 401/403 → no retry, parked key → no network at all; all of these **spool** to `.spool/room-voxels/<sha>.jsonl` (self-git-ignored); `voxelize.flush_spool()` replays. A rejected document raises (a mapping bug, not weather). |
| P8 | zones | Two definitions: `room.yaml` zone **boxes** (fake, roomctl, voxel docs) vs detected support **planes** "become zones" (`cluster.py`, `perception/README.md`, docs/03). `associate` leaves `added` objects' `zone` None and `for_serialize` then raises. | **Open — needs a decision.** Proposal: assign by `voxelize.zone_of(centre, room.yaml zones)`, the rule voxel docs already use, so an object and the voxels under it can't disagree; planes stay cluster's geometry. |
| P9 | `room-clouds` | No real-capture writer. Its fields come from perception: `coverage_pct` ← mean `depth.coverage()` (a **0–1 fraction** despite `_pct` — the fake writes fractions too), `skew_ms`/`tilt_rate_max` ← `capture_begin`, `quality_ok` ← `obs.capture_quality`, `point_count`/`bounds` ← the `fuse()` cloud. Coverage counts **matchable** pixels (SGBM can't match the leftmost 112 of 480 columns; over the whole image the > 0.60 gate would reject most scenes). | **Open — pipeline owner (D9).** |
| P10 | multi-frame captures | `/capture` sends 3–5 frames per camera (`seq`); `depth_capture` takes one per camera; the docs/03 majority vote across frames has no implementation or owner. | **Open — pipeline owner (D9).** |
| P11 | voxel `z_min`/`z_max` | Fake: the box extent clipped to the cell. Real: 10th/90th-percentile point heights in the cell. Every current query (`placement_collisions`, `voxel_changes`) uses `cell` and the keys only. | Note — no consumer affected. |
| P12 | trace links during the pause | Docs written while Sentry is paused still carry a `sentry_trace_id` (spans exist without a client), but those traces were never sent: dead links for anything indexed before 01:00. | Note. |
| P13 | occluded vs removed | `raycast.occlusion_check` took one camera list for every object, so a camera facing AWAY (clear line, no view) made a hidden object REMOVED and deleted its file; and "no camera" answered REMOVED. (Found by the segment session.) | **Fixed:** cameras are `raycast.Camera(position, forward, half_fov_deg, max_range)` (`Camera.from_mount`, `depth.StereoDepth.half_fov_deg()`); per object only cameras whose cone and depth range hold it count; none covering → UNOBSERVED. Bare positions raise. `test_boundaries.py`'s occlusion test needs the Camera form (segment session told). |
| P14 | voxel docs × Sentry off | If `obs.trace_fields()` returns `{}` when Sentry is inactive (the fix for P12's dead links), `voxel_docs` used to raise and indexing would stop with Sentry. | **Fixed:** it raises only when a Sentry client is live (`get_client().is_active()`, no network); with Sentry off it indexes without trace fields. Dead-link ids vs none during the pause: **open — obs.py's owner.** |
| P15 | **G2 on the real chain** | `perception/pipeline.py` `scan_into(repo, recording)` now exists (D9's recipe, cluster path). On synthetic fisheye recordings of an untouched desk (`perception/synthetic.py`: real code after the JPEGs; 2-level sensor noise, 1 mm / 0.05° pose jitter per scan) **G2 FAILS**: `tests/test_idempotent_scan.py` → "PHANTOM DIFF on rescan 1"; `git diff --exit-code` = 1 on every rescan. Cause, measured: from ONE stereo view a narrow object's footprint smears along the camera ray (an 8×8 cm block measures 13–21 cm long; 1 px of disparity ≈ 10 cm at 1.35 m with a 6 cm baseline, and SGBM pixel-locks), so `Instance.box()`'s x / length / yaw follow the noise by 3–6 cm and 15–30° — far past a 1.5 cm / 7.5° deadband. Not fixed by DOWNSAMPLE 0.375→1.5, uniqueness, a disparity-edge filter, or scanning from 0.3 m. The fake passes because its jitter is sub-quantum by construction. | **Open — the depth source (see P16: active IR should cut depth noise ~10×) and `cluster`/`Instance.box()` (single-view boxes). Re-run G2 on real RealSense sessions the moment one exists.** Depth DOWNSAMPLE is now 0.75 (floor 13→8 mm std, desk top 9→3 mm). |
| P16 | RealSense (docs/27) vs Sarah's code | Read against `sarahyoo011725/depth-camera`: `metadata.json` is per SESSION, not per capture; **no intrinsics and no depth scale are saved** (the point cloud is the only geometry; depth_raw can't be deprojected offline); **one timestamp per capture and the two cameras are read one after the other** → `skew_ms` can't be known, and `obs.capture_quality` FAILS a None, so every replayed session would be rejected; no robot pose. | **Built:** `depth.RealSenseDepth` + `RealSenseFrame.load(capture_dir, cam)` — same contract as StereoDepth (xyz H×W×3 metres, NaN-invalid, valid, BGR colour); refuses a filtered cloud; both unit traps asserted on load, both directions (pointcloud must be metres; depth_raw/1000 must equal its z pixel for pixel). `depth_capture` now takes either source. **Open — Sarah:** save per-camera frame timestamps (or use hardware sync) and the colour intrinsics + depth scale; the extrinsic calibration becomes each camera's `fuse.Mount`. |
| P17 | commit docs × Sentry | Live run: `room-voxels` for commit `551990435d` all carry `sentry_trace_id` (6803/6803); the `room-objects` (3) and `room-events` (1) written by `roomctl.publish.publish_commit` carry none — the commit runs outside the scan's transaction. | **Open — master:** attach `obs.trace_fields()` in `publish`, or carry the scan's trace id through `stage_scan`. |
| P18 | G2 after `settle` | `serialize.serialize` now calls `roomctl.state.settle()` after `stabilize()` (docs/20 Part 4's per-OBJECT call, MOVE_M 5 cm; extents are identity) and the pipeline passes `trace=` to `stage_scan` (closes P17's scan half). Result on synthetic recordings: **`tests/test_idempotent_scan.py -k perception` → 8 passed** (4 recordings × 20 rescans per row); a 5-rescan close-range set clean too. **Residual:** a 12-scan seed-7 set → 1 of 11 rescans dirty with a one-scan SPURIOUS `added` object (master's run saw the mirror image: a one-scan MISS deleting a file). `associate` still reports `moved` for objects `settle` keeps byte-identical (its own 1.5-quanta test). | **Resolved 2026-09-19 (segment session):** cluster.py merges a split cluster (footprints touch, vertical gap ≤ 5 cm — seed 7's phantom was the block's base fragment) and associate's removal sticks until seen: G2 0/11 dirty on seeds 0/1/3/7/11. Superseded: docs/03's majority vote over the 3–5 frames of ONE capture (P10) is the fix for both flickers; a 2-consecutive-scan debounce is the stopgap. **Open — associate:** align `moved` with `MOVE_M`. |
| P19 | the judge's click: `/capture/<id>` → `/replay` | Audited against the running server + live cluster. **Before:** no real capture could render — the page reads `room-clouds`/`room-observations` by `capture_id`, and nothing but `fake/scene_gen` wrote them; `synth_00_000`-style ids fail the web's `^[a-z]+_[0-9]+$` (422). Every capture in the index (cap_0004 included) is `scene_gen`: observations, gate values, telemetry, and a trace Sentry never recorded. **Still synthetic or absent everywhere:** `room.git` + its room-objects/room-voxels (scene_gen commits); ALL `robot-telemetry` (scene_gen ±2 s + `robot/telemetry.py --fake`); camera frames (`frame_uri` is hard-coded None: no mapping field); capture poses (room-clouds has no `pose`, so replay's path is never pinned). The live room-voxels hold only surfaces + object boxes — no floor, legs or walls — so the live costmap has **0 obstacle cells** and traversal's collision filter is never exercised. `docs/16`'s `/api/history` is `/api/graph`. | **Built:** `pipeline.scan_into(..., es=)` writes the capture's room-clouds doc (gate, coverage, points, bounds, cloud_uri → `clouds/<id>.ply`, trace) + one room-observations row per object per camera (merge's rows, same trace), rejected captures too, through `perception/es_sink.py` (retries/spool); synthetic ids now pass the web. `VoxelGrid.from_docs` + `voxels_for_commit` put traversal on the live indices. **Verified live** (`GITSPACE_LIVE=1 pytest perception/tests/test_capture_live.py`, 6 passed): a fresh capture renders, every block is that run (gate values, 3 objects, point_count = the .ply, its own Sentry trace link); replay == raw robot-telemetry sample-for-sample at 20 ms for every capture in room-clouds; traversal on live HEAD (10/11 objects stand, keys_7c2e refused by line of sight/IK with the tally); `/api/graph` == `git log`. **Open — master:** `ROOM_SCANNER=perception` should pass `es="env"` (or set GITSPACE_INDEX_CAPTURES=1); executor `route()` could use `from_docs` instead of `fake_costmap`. **Open — web:** frames need a mapping field; the page can't tell rendered input from a camera (flag is `vlm_model == fake/scene_gen`). |

Verified correct, not gaps: `associate.for_serialize()` builds `serialize.Measured` positionally in exactly its field order; `segment._dominant_color` reads `left_rect` as BGR and writes lowercase `#rrggbb` (what `roomctl.state` validates); `raycast.occlusion_check` is `associate`'s `occluded` callback (agreed with the segment session: "every camera blocked"); voxel docs' `object_id`/`cell`/`voxel_key*` are what `elastic/queries.py` reads; the voxel `_id` equals `ingest.action("room-voxels", d)["_id"]` (tested).

---

# elastic — connection audit of the producers and web, 2026-09-18 22:15 (APIs parked)

Read-only, file:line checked by hand; nothing below was run against a live API. Items already
listed above (GAP 1–5) are not repeated.

## GAP 6 — `obs.trace_fields()` invents trace ids while Sentry is off
With no DSN the SDK still creates spans, so `trace_fields()` returns a `sentry_trace_id` Sentry
never received (reproduced, sentry-sdk 2.69.2). During the pause every writer that stamps it
(`perception/voxelize.py:171`, `associate.py`, the hub) plants links to nothing — in the join both
prizes score. **Fix (obs.py, one line):** `if not _HAVE or not sentry_sdk.get_client().is_active():
return {}`. `scripts/story_demo.py` strips them itself. Owner: observability.

## GAP 7 — the hybrid search exists a THIRD time: `perception/associate.py ESHistory`
BM25 on `class` only (associate.py:351 — misses `raw_description.text` and the exact `object_id`),
no collapse on `object_id` (20 raw hits can be 1–3 objects, since every commit re-snapshots
every object), no branch filter (can re-identify as an id that only lived on another branch),
no spatial filter (docs/14 Phase 2 step 5 asks for one; it names `cell`, the field is `position`).
**Fix:** call `Queries.search_objects(text, near=(x, y), radius=1.5, branch=current)` — elastic
added `branch=` to `search_objects`/`lexical_only`/`semantic_only` for exactly this
(`tests/test_query_shapes.py`). Owner: perception.

## GAP 8 — perception re-implements elastic's write rules
- `perception/voxelize.py:297` builds `f"{commit_sha}:{voxel_key}"` itself — no `natural_id`
  guard, so a None sha writes `"None:…"`. **Fix:** `ingest.action("room-voxels", d)`.
- `perception/associate.py:224` stamps observations `at + len(docs) ms`: position-dependent, so
  re-ingesting a capture with one view more or less writes DUPLICATES instead of 409s, and `at`
  is a laptop clock (docs/22 §3). (object_id, camera) is already unique per capture. **Fix:** every
  row gets the capture's `t_capture_wall`. Owner: perception.

## GAP 9 — the telemetry hub loses documents it should spool
- `telemetry/hub.py:246` "bad docs would fail again: count, don't spool" also drops per-document
  429/503s inside a successful bulk (its `streaming_bulk` has no `max_retries`). docs/23 §4 says
  buffer to disk. **Fix:** spool retryable items, or bulk through `elastic/ingest.write()` (retries
  429 ×3 with backoff since 22:15).
- `_backfill_one` unlinks the spool file even when some of its documents failed.
- UNSURE (needs the cluster): backfilled samples older than the current backing index land in a
  rolled-over, downsampled (read-only) one and are rejected — then deleted by the line above.
- Restart without NTP re-pairs the clock (hub.py:89-90, in memory only) while the Pi replays 10 s:
  up to 4k telemetry docs a few ms off → stored as duplicates, not 409s. **Fix:** persist the
  boot_id → offset pairing. Owner: telemetry.

## GAP 10 — nothing indexes the watch loop
`telemetry/hub.py:563` treats `detection` as a heartbeat only (`_watch_beat()`). docs/14 Phase 1's
45–90 docs/s into room-observations — the dense history the occluded-vs-deleted call relies on —
has no writer. Owner: telemetry + perception.

## GAP 11 — the placement collision check never asks Elasticsearch
`roomctl/executor.py:151,166` is a local box overlap on YAML poses. docs/14 Phase 4 step 3 (and the
"Elasticsearch shapes what the robot does" claim) is a shape query on room-voxels at the observed
sha — `Queries.placement_collisions()` exists and is live-tested. **Fix:** call it, or change
docs/14 to say the check is local. Owner: roomctl/master.

## web — verified bugs not in GAP 3–5 (web's audit list; owner web)
**Status:** items 1–5 FIXED by web, re-verified by elastic (code read + `web/tests/test_store_shapes.py`,
`test_search_adapter.py`: 10 passed, offline). The `telemetry_api` mount (last item) is still open —
web's board builder mounts it as its final step.
- Downsampled telemetry: `store.py:301-305` / `telemetry_api.py` read `value` as a number; once the
  lifecycle downsamples (5m buckets after rollover + 1h) it is `{min,max,sum,value_count}` and the
  capture page says "not recorded". (`Queries.telemetry_window` now uses only MIN/MAX — works on both.)
- `graph_api.py:168-169`, `object_api.py:202`: `size=1000`, oldest first → past 1000 events/clouds the
  NEWEST are dropped. `object_api.py:164`: watch window has no `capture_id` filter, truncates silently.
- Timestamps compared as strings (`object_api.py:168,286`, `graph_api.py:209`); writers mix `…Z` and `….000Z`.
- `store.py:287` reads room-clouds `frames`: no such field (strict mapping would reject it) → always None.
- `store.py:35` treats a 401 (`elastic_auth`) as "fall back to fixtures": a wrong key shows fake data.
- `server.py` never mounts `telemetry_api` (its docstring says `include_router(telemetry_api.router)`;
  `pages/telemetry.js` exists) → `/api/telemetry/*` 404.

## docs drift that would lead a producer into rejected writes (owner: docs/master)
**Fixed 2026-09-19 (master):** docs/16 §3.1 now shows `from_mono`/`replay` and the hello's
`boot_id`/`t_mono_base`/`t_wall_base`/`t_mono_now` as `robot/telemetry.py` sends them; docs/11
lists `odom_residual`/`balanced` as `signal` values; docs/14's query is `WHERE signal IN (…)`;
docs/23's downsampling was already fixed by its owner. Also D10 (docs/16 §2.1 round-robin →
latch together) and D21 (docs/24 A1 notes `ROBOT_H = 0.60`).
- docs/16 §3.1: telemetry has no `from_mono`, hello has no `boot_id`/`t_mono_base`/`t_wall_base`/
  `t_mono_now` — the hub requires them (hub.py:582); a Pi built from docs/16 sends only bad messages.
- docs/11:526-527 lists `odom_residual, is_balanced` as robot-telemetry fields — strict rejects them
  (they are `signal` values). docs/14 Phase 5 `MAX(odom_residual)` / `MAX(ABS(pitch))` assume a wide
  row; it's `WHERE signal == "pitch"`.
- docs/23 §5 downsampling (1 s / 1 min) is impossible on a data stream lifecycle (5 min floor);
  declared 5m / 30m + 7d retention, rounds `after 1d` / `after 2d` so /replay stays raw for the
  whole event (set 2026-09-19 on the user's call) — `elastic/NOTES.md`.


---

# perception/segment — connection audit, 2026-09-18 (all external APIs parked)

Scope: `segment` `cluster` `describe` `merge` `associate` against the code the other sessions
actually wrote. Checks live in `perception/tests/test_boundaries.py`. Every API is mocked at
its boundary: 80 passed, and nothing reached OpenAI, Elasticsearch or Sentry. Error handling
was also checked in the project `.venv` against the real exception classes
(`elasticsearch` 9.5, `openai` 3.16), and against the real parked `.env`: no client built.

## Fixed on perception's side

| # | seam | was | now |
|---|---|---|---|
| P1 | `merge.observations()` → `web/object_api.py` | dropped `None` keys; `_history_stats` reads `s["confidence"]` → KeyError (a 500 on `/object/<id>`) for any fallback-path object | every contract key present, explicit `null`s, as the fake does. The test parses `fake/README.md` "The documents" and the strict mapping and requires equality |
| P2 | `associate.for_serialize` → `serialize.Measured.zone` | carried HEAD's zone forever; a move across zones never renamed the file | `zone_of(centre, room.yaml zones)`, same rule as `voxelize.voxel_docs` (first zone by name), checked voxel by voxel against the real `voxel_docs`. 3 cm hysteresis so a boundary object doesn't rename its file every scan |
| P3 | `segment.lift` ↔ `fuse.rect_to_world` | fuse's docstring invites lifting masks from the WORLD array; lift's edge filter reads column 2 as range from the camera, which in F_world is height, so the filter silently stops working | `segment.run(..., mount=)` lifts in F_rect, then applies `fuse.rect_to_world`. The lift docstring says why |
| P4 | GAP 4, perception's copy | `OpenAI()` / an ES client would be built with `KEY=# parked…` garbage and send a 401 per call | `perception/keys.py` uses web's `usable`/`parked` rule. `describe` goes offline (null descriptions, capture continues). `associate.history_from_env()` returns `OfflineHistory`, which makes no call and puts a `note` on every `added` object |
| P5 | `describe` → OpenAI | SDK default retries (2) on top of ours, 600 s timeout | `max_retries=0`, 30 s timeout. 401/403/404 → circuit breaker (one call, not 45×3). 408/409/429/5xx → backoff, honouring `Retry-After` up to 8 s. Other 4xx and truncated replies → fail that view only |
| P6 | `describe` → Sentry AI monitoring | — | each gpt-5 call is `obs.agent_turn(PROMPT, model)` with `gen_ai.usage.*`. The thread pool copies contextvars per task; a bare `pool.map` would orphan every `gen_ai.chat` span from `perception.describe` |
| P7 | `associate.ESHistory` → ES | no timeout or retry policy; any error crashed the scan | `es.options(request_timeout=10, max_retries=2, retry_on_timeout, retry_on_status=429/502/503/504)`. What's left → `HistoryUnavailable(reason)`, and associate degrades with a `note` on `added` objects. A malformed history doc is skipped. Our own bugs (TypeError…) stay loud. Search wrapped in `obs.span("es.search")` |

## Open — each needs the OTHER side

- **D9 (pipeline), what perception's stages need from `scan_into`:** run `depth_capture` inside
  `obs.capture_scope`. Then `segment.run(xyz, valid, left_rect, cam, mount=)` per camera, giving
  F_world instances and residual. Pass `robot_pose=fuse.odom_to_world(capture["pose"])`, the same
  pose `fuse.fuse()` gets; it's required with `mount=`, because the origin default would disagree
  with the fused cloud once the robot moves. Then `cluster.cluster(residuals)` → `merge.merge(all)` →
  `describe.describe([(inst, left_rect)…])`. The instances keep their pixel masks, so describe
  can run before or after merge. Then `associate.associate(objects, head=Repo(root).records())`.
  `head` must be the COMMITTED state, not the working tree (serialize's docstring). Pass
  `history=associate.history_from_env()` and `occluded=raycast.occlusion_check([raycast.Camera.from_mount(
  mount, sd.half_fov_deg(), robot_pose) per rig], grid)`.
  Then `associate.for_serialize(assocs, zones=voxelize.load_room()["zones"])` →
  `serialize.serialize(root, measured, head, unobserved)`. ES: `associate.observation_docs(assocs,
  capture_id, at, obs.trace_fields())` goes to room-observations. `MergedObject.object_fields()`
  gives the perception-only half of each room-objects doc (GAP 1).
- ~~**raycast:** one camera list for every object meant a camera facing away declared an unseen
  object REMOVED and deleted its file.~~ **Resolved by the pointcloud session:**
  `occlusion_check` takes `raycast.Camera(position, forward, half_fov_deg, max_range)` and counts,
  per object, only the cameras whose cone and range hold its pose. No covering camera →
  UNOBSERVED. Bare tuples raise TypeError. `test_boundaries.py` pins all three verdicts through
  `associate()`.
- **elastic, GAP 3's last duplicate:** the re-id hybrid query exists in `elastic/queries.py`
  (`search_objects`) and `perception/associate.py` (`ESHistory`). ESHistory will call
  `search_objects` once it returns each hit's `extents color first_seen pose` (associate needs
  them to carry identity) and accepts an `exclude` (ids already matched this scan).
- **elastic, decision:** reranker scores are uncalibrated. Live: the right match scored
  1.03–1.90 and the best wrong one 1.07–1.92, and the top1−top2 margins overlap too. So
  `returned` can't be decided on the score; associate uses it to rank and vetoes on size +
  colour. Proposal: a `crop_embedding` dense_vector (1024, `.jina-clip-v2` on EIS, ≤ 16 inputs
  per request) on room-observations and room-objects. 10/13 objects were nearer their own other
  view than any other object; text separated none. Mapping change is elastic's.
- **elastic + master:** `returned` is invisible downstream. git sees a file re-added, and
  room-events has `objects_added/removed/moved` but no `objects_returned`, so "ids survive
  absences" isn't queryable. The commit hook needs associate's verdicts, not only git's diff.
- **roomctl:** when history is offline, `added` objects carry a `note`. A new id for a returning
  object is permanent, so `room commit` should refuse or warn when any `added` has a note.
  roomctl needs the associations to see it.
- **room.yaml (a human call):** objects outside every zone box — the floor — have no zone.
  room.yaml has `desk` and `shelf` only; `for_serialize` raises unless given `default_zone`.
  Add a `floor` zone, or name the default.
- **obs:** with no DSN, spans still get trace ids. So while Sentry is paused, every ES doc carries
  a `sentry_trace_id` for a trace that was never sent: the dead link the fake avoids on purpose.
  `trace_fields()` could return `{}` when `sentry_sdk.get_client().is_active()` is False.
  `voxelize.voxel_docs` only demands a trace when a client IS live, so indexing would keep
  working (confirmed by its owner). Owner's call whether no id beats a dead one.
- **perception/segment (mine, next):** the real path emits no rejected rows yet (`rejected_reason`:
  masks with too few points, ignored labels, clusters out of size range). The fake does, and web
  shows them.

### Audit, 2026-09-18 ~22:30 — every session against its TASK.md, and every seam between them

Suites run with the network blocked in-process (nothing reached ES/OpenAI/Sentry):
`perception/tests` 183 passed · `telemetry` 27 passed · `elastic/tests` 113 passed + 19 live-only
errors (see D18) · `tests/` 357 passed. `setup_elastic.py --check` valid. `audit_architecture.py`:
16 ok · 2 warn · 1 FAIL (a false positive, D17).

Seams checked and **matching**: perception's `room-observations` rows (`merge.observations()` +
`associate.observation_docs()`) and `voxelize.voxel_docs()` against the strict mappings, field
for field; `serialize` → `roomctl.state`; `obs.trace_fields()` keys ↔ every mapping; telemetry
hub docs (`@timestamp` epoch-ms, `signal`, `value`) ↔ `robot-telemetry`; web holds no ES
credential client-side.

| # | drift | status |
|---|---|---|
| D13 | Two `git status` parsers serve `/api/status`: `roomctl.cli.status_dict` and `web/room.py`. Both meet docs/16 §4b's base fields, but diverge beyond it — web lists conflicts separately and has `rev`; roomctl has conflicts inline plus `class`, `yaw`, `staged`, `merging`. Two implementations of "what moved" will disagree eventually. | **Open — web + integration.** Suggest web imports `roomctl.cli.status_dict` (read-only; it never writes). |
| D14 | **The real pipeline indexes no rejected clusters.** `cluster.py` counts its rejects and drops them; `merge` sets `rejected_reason: null` on every row. docs/11 Gap 1 is explicit that the discard pile goes to ES — it's the "honest mess". Today only the fake produces rejected rows, so the capture page will show none on real data. | **Open — the segment session** (`cluster.py`/`merge.py`). |
| D15 | **Robot API frames disagree within docs/16.** `/drive` and `/pose` use `{x, z, yaw}` with yaw in **radians** — BB's odometry axes (X fwd, Z left; docs/20 Fact 3). `/arm` uses world `{x, y, z, yaw}` with yaw in **degrees**. And nothing says whether `/drive`'s target is anchor-registered (world) or odometry-origin. `roomctl/robot_client.py` assumes world-anchored with `z = world y`, yaw radians, and says so. | **Open — robot/** (no active session: needs a human). |
| D16 | TASK-cloud items 4 (AWS t4g.small + Caddy + S3 7-day lifecycle) and 5 (`scripts/snapshot.sh`) don't exist; nor does `scripts/demo.sh` from scripts/README. docs/06 and BUILD_ORDER rule 5 both lean on snapshot.sh ("tag a known-good room.git"). | **Partly done 2026-09-19 (cloud):** `scripts/deploy_web.sh` is ready (plan mode makes no AWS calls; `--apply --account <id>` refuses a mismatched account) — **waiting on a human: which AWS account** (the only local credentials belong to IAM user `curve-guard-dev`). Uptime is live meanwhile via `scripts/uptime_tunnel.sh` (monitor #10384065). **Still open:** `snapshot.sh`, `demo.sh`. |
| D17 | `audit_architecture.py`'s "sentry_sdk.init only in obs.py" FAILs on `web/server.py:56`, which is a *comment* ("…sentry_sdk.init() RAISES BadDsn"). Web initialises through `obs.init("web")`, correctly. | **Resolved 2026-09-18 (cloud):** the check matches code only (`^[^#]*`); audit → 17 ok · 2 warn · 0 FAIL. |
| D18 | `elastic/tests` live-query tests `pytest.fail` (deliberately — no silent skip) when the cluster is unreachable, so the whole suite reads red for the API pause: 19 errors hiding 113 passes. | **Suggestion — elastic:** a `live` marker so `-m "not live"` stays green offline. |
| D19 | **Reach.** With placeholder SO-101 numbers (0.18–0.48 m from the base centre) and docs/24's 0.28 m inflation, nothing near the middle of the fake 0.9 × 1.0 m desk has a base pose from any side — the mug (docs/06's own hero object, ~0.4 m in from the edge), marker, glasses case, scissors. Shelf keys fail line of sight: eye height 0.95 m is 5 cm above the shelf top, inside one 6.25 cm voxel. `room reset --hard --scene messy_bench` now honestly reports 0 of 3. | **Open — needs a human decision:** measured arm numbers (`robot/arm.py`) and the demo table's size/object placement (this doc's "Arm mount geometry" and "Table height"). `--no-route` keeps the fake demo moving meanwhile. |
| D20 | `perception.costmap.solve_base_pose` returns only the pose; which filter rejected the candidates goes to a Sentry span and a log line. So the executor's "cannot apply hunk: nowhere to stand" can't say *why* (blocked / IK / sight / path) — the most useful half of the message. | **Resolved 2026-09-18:** `costmap.solve_base_pose_why()` → `(pose, why)`; the executor prints it: "180 base poses sampled: 180 base fits". |
| D21 | docs/24 A1 contradicts itself: prose says a table's overhanging top doesn't block the base; its `costmap_from_voxels` counts every voxel below the robot's 1 m height, table tops included. | **Resolved in code:** `costmap.py` sets `ROBOT_H = 0.60` — the part of the robot as wide as the inflation — below table-top height. docs/24's snippet should say so. |
| D22 | **Capture ↔ telemetry is joined by time, and nothing put the shutter on the telemetry clock.** `web/store.py` / `telemetry_api.py` take `room-clouds.@timestamp` as the shutter and read `robot-telemetry` ±100 ms / ±2 s around it. docs/16 §3b's `capture_begin` has no `t_capture_mono` (docs/22 §3's does), and whoever writes `room-clouds` would stamp laptop time at processing — seconds late. Every latch-window spike would be read at the wrong instant, silently. | **Contract, built in `telemetry/hub.py`:** the Pi sends `t_capture_mono`; the hub adds `t_capture_wall` + `ts` (ISO) from the SAME mapping as telemetry; `room-clouds.@timestamp` := `ts`, never `time.time()`. **Open — robot/** (send it) **+ D9's pipeline** (write it). |
| D23 | The Pi's `tilt_rate_max` is the peak over ±100 ms of the shutter, and half that window is in the future when the shutter fires — reading it at once under-reads the peak and passes the capture the gate exists to reject. | **API ready:** `tel.peak('tilt_rate', t-0.1, t+0.1, wait_s=0.3)` waits, and returns **None** if the window isn't covered; `obs.capture_quality` now FAILS a None (and doesn't measure it). **Open — robot/capture.py** to call it that way. |
| D24 | **The executor can't wait for the arm.** `roomctl`'s `Robot.pick/place` are synchronous (return, or raise `RobotError(code)`); docs/16 `/arm` returns a `job_id` at once and the outcome arrives on `/stream` — which only the hub reads, in another process. | **Built, and roomctl already speaks it:** `roomctl/robot_client.py` `HttpRobot` calls `jobs.wait(job_id, timeout)` and maps `failed` → `RobotError(error, detail)` — shapes match `telemetry.hub.Jobs` exactly. Production wiring (any process): `HttpRobot.from_env(JobWatcher(PI_STREAM_URL).start().jobs)`, started before the first POST. **Open — robot/:** jobs aren't replayed, so one that ends during a wifi drop times out → add `GET /job/{id}` as the fallback. |
| D25 | **Who reports a robot failure to Sentry?** If the Pi, the executor and the hub each do, one grasp is three issues. | **Conflict, concrete:** `roomctl/robot_client.py:166` reports EVERY `RobotError`, including a `failed` job the hub already reported with telemetry + photo → one slip, two issues. **Proposed split:** the hub reports what arrives on `/stream` (`job failed`, falls); `HttpRobot` reports only what the hub can't see — HTTP-level refusals (`not_balanced`, `busy`, `robot_unreachable`) and `job_timeout`. **Open — master** (a condition around line 166). **Open — D9's pipeline:** `FrameCache().put(capture_id, camera, left_rect)` per camera, or failures arrive without a photo. **roomctl side done 2026-09-19:** `HttpRobot` no longer files failures the Pi reported on `/stream` (the hub files those, with lean + frame); what it does file carries `capture_id` (`robot.context`). Self-heal wired in `room reset/checkout/revert` behind `ROOM_SELF_HEAL` (default: real robot only — the mock never touches Sentry's API unless `=1`). **Real-path caveat:** the capture_id `cmd_apply` hands the self-healing robot must be the Pi's own (from `POST /capture`, via `scan_into`'s result) — the hub tags its issues with that id, and a laptop-minted one would never match. |
| D26 | **Sentry Traces is mostly noise:** every static asset the web server serves is a transaction (~20 per page load), and `POST /api/internal/event` isn't `_untrace()`d — 2 Hz telemetry = ~7,200 transactions/h. Robot traces get buried and the span quota burns. | **Open — web** (`_untrace()` in `push_event`) **+ obs.py** (a `traces_sampler` dropping static files). Measure before/after at 01:00 — a SENTRY_STORY entry. |
| D27 | docs/16 §3.1's telemetry message carries `from` (wall ISO); docs/23 says `from_mono` (Pi monotonic, docs/22 §3). | **Resolved in code:** the Pi sends both; `from_mono` + the hello pairing is authoritative, `from` is informational. **Open — docs/16's owner** to say so. |
| D28 | **obs.py hazards found while wiring:** (a) `capture_quality`'s `sentry_sdk.set_tag('capture_rejected')` hit the process-wide scope — after one reject, every later event was tagged rejected; (b) `obs.attach()` on its own does the same with photos; (c) `measure()` uses `Transaction.set_measurement`, deprecated since sentry-sdk 2.28 ("use `set_data()`"). | (a) **Resolved** — current scope. (b) `robot_failure(frame=)` attaches on its forked scope; any other `attach()` caller must wrap it in `new_scope()`. (c) **Open — verify at 01:00** whether Explore charts the span `set_data` values; if so, drop `measure()`. |
| D29 | **The watch loop has no implementation or owner**, yet it's what the one cron monitor watches. | **Built:** the hub checks in `watch-loop` (≤ every 30 s, monitor config upserted) while `detection` messages arrive — a dead loop, Pi or link stops the check-ins and Sentry pages. **Open — robot/:** if the watch loop moves to the laptop, heartbeat there and remove the hub's (one source). |

## GAP 4 — the CLI is missing two of the three demo commands  ·  **found offline, 2026-09-18**

`docs/06-demo.md` is the spec, and its spine is three commands:

```
room status      → ✓ exists
room diff        → ✗ NOT IMPLEMENTED
room revert HEAD → ✗ NOT IMPLEMENTED
```

`room --help` offers only `{init, status, commit, help}`.

**The logic mostly exists** — `roomctl/executor.py` is 470 lines and does diff → ordered ops →
dependency graph → motion. It is simply **not reachable from the command line**, which is the
only interface the demo uses.

This is the highest-risk gap in the project right now, because:
- `room diff` is the beat that prints the physical world as a unified diff — the single most
  quoted moment in the demo script
- `room revert HEAD` is the beat where the robot moves
- everything downstream (web `/api/command`, the git-graph control surface, the agent's action
  tools) calls the same verbs

Also missing and in the demo: `log`, `add`, `checkout`, `merge`, `search`, `blame`, `stash`.

**Fix:** wire the existing `executor.py` and `repo.py` to CLI verbs. This is plumbing, not new
logic. Owner: **master**. Priority: above everything else on that track.

**Status — NOT A GAP (master, 2026-09-18 ~23:30): the verbs exist and are tested.** They are
dispatched *before* argparse, so the parser's subcommand list (`{init, status, commit, help}`)
was all `room --help` printed — which is what this audit read. Evidence, all in `tests/`:
- `room diff` — `roomctl/cli.py` passes through to `git diff`; `test_cli.py::test_diff_is_literally_git_diff`
  byte-compares it with `git diff` and checks `-  x: 0.42 / +  x: 0.61`.
- `room revert HEAD` — `git revert`, then `executor.plan` → `route` → `execute` → rescan;
  `test_cli.py::test_revert_is_real_git_revert_and_refuses_a_dirty_room`.
- `room reset --hard`, `room checkout <ref>` — same path; `test_reset_hard_puts_the_room_back…`,
  `test_checkout_of_a_swap_stages_and_verifies_clean` (stage → move → unstage, rescan clean).
- `room log`, `room add`, `show`, `blame`, `branch`, `tag` — pass straight through to git;
  `test_spatial_staging_commits_only_what_was_added` covers `add`.
**Fixed the real defect:** `room --help`'s usage line now names every verb.
**Actually missing:** `merge`, `cherry-pick`, `stash` (refused, exit 2) and `search` (needs
`elastic/queries.py`'s `search_objects`; a CLI wrapper is small once the key is back).
| D30 | **The HTTP robot can't learn how a motion ended.** `roomctl/robot_client.HttpRobot` posts `/drive`/`/arm` (202 + job_id) and must then `jobs.wait(job_id)` — but the only `/stream` consumer is `telemetry/hub.py` (one socket, docs/16 §3), a separate process whose `Jobs` the CLI can't reach. So `ROOM_ROBOT=http` stops with `no_job_stream` before any motion. | **Hub side resolved 2026-09-19 (cloud):** a third option, already built and tested — `telemetry.hub.JobWatcher(url).start()` runs a SINKLESS `/stream` reader inside the CLI's own process (the Pi serves several clients, each with its own queue; the watcher writes and reports nothing, so the daemon hub stays the only writer). `test_job_watcher_over_a_real_local_stream` unblocks on a published job. **Open — master, one line:** `HttpRobot.from_env(JobWatcher(f"ws://{PI_HOST}:{PI_PORT}/stream").start().jobs)` when `ROOM_ROBOT=http`, started before the first POST. |

## perception/segment — Sentry AI monitoring, checked LIVE 2026-09-19

- **Double counting (fixed in `describe.py`).** sentry_sdk's OpenAI integration auto-instruments
  the Responses API. Wrapping each call in `obs.agent_turn` as well gave, on trace
  `55129dfdf91e4d1aac1daac1828db376`, 35 SDK `gen_ai.responses` spans nested under our 35
  `gen_ai.chat` spans, both carrying tokens: 70 LLM calls and 26,040 input tokens for 35 calls and
  13,020 tokens. `describe` now uses a plain `perception.vlm_call` span when the SDK integration
  is live. Verified on `1e3dadc85fbd4becaa5a7692b92b29b3`: 3 calls → 3 gen_ai spans, tokens once.
  **`agent_turn` is only for LLM calls the SDK can't see** (the Realtime/WebRTC voice path).
- **Open — obs.py's owner:** `agent_tool` emits op `gen_ai.{kind}.tool`. Sentry's AI Agents module
  keys tool calls on op `gen_ai.execute_tool` with required `gen_ai.operation.name="execute_tool"`,
  plus `gen_ai.tool.name`, `gen_ai.tool.call.arguments` and `gen_ai.tool.call.result`
  (docs.sentry.io/platforms/python/agent-tracing/manual-instrumentation). `agent_turn` lacks the
  required `gen_ai.operation.name` / `gen_ai.response.model`; `describe` sets them itself.
- ~~The target trace has no code to wrap.~~ **Built 2026-09-19:** `agent/loop.py` + `agent/tools.py`
  (TEAM.md gives `agent/` to the describe/associate owner). A correction: I also wrote that
  `roomctl/executor.py` didn't exist. It did, and the `room_revert` tool runs it. One live turn is
  one `gen_ai.invoke_agent` transaction: decide → `search_objects` (es.search) → decide →
  `room_revert` (robot.pick / robot.place, mock arm) → decide.
  https://na-alh.sentry.io/performance/trace/6932ef63090640a8866f9c299986e819/ (post-D31 ops). `room_revert` is
  labelled kind `roomctl`, not `mcp`: it calls roomctl in-process; no MCP transport is involved yet.
- **Open — whoever wrote it:** the REAL `room-objects` holds `_id "proof:cup_7e21"`
  (`commit_sha "proof"`, 8 fields, no `first_seen`/`color`/`extents`). No checked-in script
  writes it. It appears in search and history for `cup_7e21`. `associate.ESHistory` skips it
  with a warning.
- **Fixed — `associate` re-id was greedy.** A returning metal cup took a returning spoon's id live;
  the spoon then became `added`. It's now one joint assignment over the size- and colour-gated
  candidates. On the live cluster: 13/13 returning objects got their own id (was 11/13), and
  0/13 newcomers took an id from the real `room-objects`.
| D31 | **`obs.py`'s gen_ai spans don't match Sentry's AI Agents schema** (found by the segment session). `obs.agent_tool` emits op `gen_ai.<kind>.tool` with `gen_ai.tool.input.*`; Sentry recognises op `gen_ai.execute_tool` with `gen_ai.operation.name="execute_tool"`, `gen_ai.tool.name`, `gen_ai.tool.call.arguments`/`.result` (JSON strings). `obs.agent_turn` around OpenAI Responses calls **double-counts**: the SDK's OpenAI integration already instruments them — trace 55129dfd…: 35 calls → 70 LLM spans, 13,020 input tokens counted as 26,040. `describe.py` now avoids it by wrapping with a plain span when the integration is active. | **Resolved 2026-09-19 (cloud):** `agent_tool` → op `gen_ai.execute_tool` with the schema's attributes; `agent_turn(…, sdk_visible=None)` emits a plain `agent.turn` span while the OpenAI integration is active, a real `gen_ai.chat` only when `sdk_visible=False`. Checked against sentry_sdk 2.69's own OP/SPANDATA consts. |
| D32 | **A stray document in the LIVE `room-objects`:** `_id "proof:cup_7e21"`, `commit_sha "proof"`, 8 fields (no `first_seen`/`color`/`extents`). No checked-in code writes it — an ad-hoc proof run without a `test-` prefix. It shows up in search and history for `cup_7e21`, the object the hybrid-search acceptance is built on. | **Open — needs a human:** it's shared data, so nobody has deleted it. `DELETE room-objects/_doc/proof:cup_7e21` once you've confirmed it's disposable. Same for the pointcloud session's live self-test, commit `551990435d…` on branch `synthetic-selftest` (6,803 room-voxels, 3 room-objects, 1 room-events): `delete_by_query` on `commit_sha` in those three indices — full sha from that scratch repo's `git log`. |
| D33 | **Sentry could not be searched by `capture_id`.** `obs.capture_scope()` tagged a forked scope; a transaction started outside it (story_demo's nesting, the web's request transactions) never saw the tags, and child spans never read scope tags. Live: `capture_id:<id>` → 0 spans, and h00's own `story_demo` trace had it on 0 of 7 — the Sentry→Elasticsearch half of the join was a dead end. | **Resolved 2026-09-19 (cloud):** `capture_scope()` stamps the ids on the enclosing span + transaction and carries them in a contextvar, so every `obs.span()` / `agent_tool()` inside gets `capture_id`/`commit_sha` (tag + attribute); same for `capture_rejected`. Live: 3 of 3 spans (trace `526a2850…`). SENTRY_STORY h05. |
| D34 | **The LED strip has two writers.** `roomctl/cli.py` sets `working` → `clean`/`dirty` around `room revert`; `robot_sentry.IssueMirror` sets `clean`/`dirty`/`error` from unresolved Sentry issues + room.git (docs/28 §3). The mirror writes on change only, but a change mid-motion overwrites `working`. | **Open — master + cloud.** Proposal: one owner — the mirror computes the worst of (Sentry, room, job running) and the CLI stops calling `led()`; or the CLI keeps `working` and the mirror skips ticks while a job is in flight. |
| D35 | **One cron monitor on the education plan, two candidates.** `watch-loop` (created by the h05 verification; muted, no watch loop exists) and `room-clean` (docs/28 §2 — a messy room fails its heartbeat). | **Decided by docs/28: room-clean.** The hub's watch-loop beat is now opt-in (`WATCH_HEARTBEAT=1`). **Open — a human:** delete the `watch-loop` monitor before the trial ends (2026-10-02) to free the seat. |
| D36 | **'Resolved by robot' shows a person as the actor.** Sentry names whoever owns the token that resolves; `SENTRY_AUTH_TOKEN` is Daniel's. The internal integration **gitspace robot** (`gitspace-robot-cc09ba`, scopes event:read/write, project:read, org:read) now exists, but its token can't be read with the current token (403). | **Open — a human, 1 min:** Settings → Developer Settings → Custom Integrations → gitspace robot → copy the token to `.env` as `SENTRY_ROBOT_TOKEN`. `robot_sentry.py` then writes as the robot. |


### web · 2026-09-19 · docs/26's autofix path is wrong on the live API (verified read-only)

- `GET /api/0/issues/<id>/autofix/` → **404, empty body**. The endpoint is org-scoped now:
  `GET|POST /api/0/organizations/<org>/issues/<id>/autofix/` (GET → `{"autofix": null}` until a run exists) and
  `GET …/autofix/setup/` (→ `integration{ok,reason}`, `seerReposLinked`, `autofixEnabled`, `billing.hasAutofixQuota`).
  `web/sentry_client.py` and `web/tools/verify_seer_autofix.py` use these. docs/26 should be corrected by its owner.
- Our Sentry org reports `integration_missing` and `seerReposLinked: false`: Seer can run (enabled, quota present) but
  cannot read our repository. Linking GitHub + the repo in Sentry → Settings → Seer is a 2-minute job and is the
  difference between a verdict about the telemetry only and one that can point at `capture/quality.py`. **Owner: Daniel.**
- The POST that starts a run is still unpressed (it bills a run). `SEER_VERIFIED` flips after the first real one.
- No capture has BOTH full telemetry and a real Sentry issue: `cap_0004` (the story) is synthetic with no issue;
  `cap_82093` / `cap_78072` have real traces and resolve to GITSPACE-2 (`grasp_slipped`) but carry no telemetry samples.
  Decision recorded by master: fixtures stay synthetic; the real one comes from a real robot capture.
- **RealSense (docs/27) — ready on perception/segment's side, waiting for data.**
  `describe.describe_capture(dir)` / `python perception/describe.py <session_*/capture_NNNN>` runs
  `depth.RealSenseDepth` → `segment.run` → gpt-5 per camera. It's tested on a folder in Sarah's exact
  format; a millimetre point cloud is refused on load. **Open — a human:** no `session_*` folder exists
  on this machine, and Sarah's repo is code only. Copy one capture here to run it for real.
- **Open — mine, after real RealSense data:** P15, the single-view box smears along the camera ray
  (pointcloud session: an 8×8×20 cm block measured 13–21 cm, yaw 20°→50° on an untouched desk).
  Measure it on RealSense first. If it persists: stamp each instance with its camera as
  `raycast.Camera.from_mount(mount, half_fov, robot_pose)` (`.position` is the F_world origin,
  `.forward` the optical axis; built on `rect_to_world`, so frames still change in one place). The
  view ray is `centre - cam.position`. Take along-ray length from the across-ray silhouette, and hold
  the committed yaw when the angle between the ray's XY part and the box's yaw axis is under ~15°.

### G2 on the real chain (P15) — master's diagnosis, 2026-09-19 ~02:30

Reproduced P15 on `perception/synthetic.py` recordings (12 scans, seed 7), network blocked,
by wrapping `serialize.write_tree` in a scratch harness (perception/ untouched):

| rule added | dirty rescans (of 11) |
|---|---|
| none — per-field 1.5-quantum hysteresis only | 8 |
| object-level "moved?" at MOVE_M = 0.03 / 0.05 / 0.08 m | 4 / 3 / 3 |
| MOVE_M = 0.05 **+** a removal needs 2 consecutive misses | **0** |

Untouched objects' centres wandered ≤ 3 cm (max 0.030 m); the rest of the dirt was ONE 8×4×6 cm
object undetected in 3 of 11 scans, each miss deleting its file. Two fixes, two owners:

- **Pose noise — built (master):** `roomctl.state.settle(prev, measured)` + `MOVE_M = 0.05`.
  docs/20 Part 4's "matched, ‖Δp‖ < threshold → unchanged, byte-identical", per object: not
  moved → the committed record whole; moved → fresh pose/yaw with identity carried, now
  **including extents** (never re-measured after first sight). The fake uses it;
  `tests/test_state.py` pins it; `test_settle_alone_holds_an_untouched_room` shows it is
  load-bearing by itself. **Open — pointcloud:** `serialize.stabilize_record()` → then
  `settle(head.get(id), rec)` before `write_tree`. Cost, stated in state.py: a move < 5 cm is invisible.
- **Missed detections — open, pipeline/segment:** P10's multi-frame vote inside one capture is
  the real fix (removal still shows in ONE `room status`); a 2-consecutive-miss debounce in
  `associate` is the stopgap that made the table's last row clean.

Also wired: `ROOM_SCANNER=perception:<recording dir>` → `pipeline.scan_into` for `room status` /
`room commit` (motion verbs `--plan-only`: a recording can't show what the robot did); and
P17 — `publish.stage_scan(…, trace=)` carries the capture's trace to the commit's docs.

- **P15, associate's half — fixed; pipeline wiring open.** (1) Debounced removal: an unseen,
  unoccluded object is `missed` (file kept) until `MISSES_TO_REMOVE`=2 in a row, via
  `associate(..., misses=)`, `next_misses()` and `load_misses/save_misses` at
  `<repo>/.git/gitspace/misses.json`. Occlusion isn't a miss. The master measured 3/11 dirty →
  0/11. (2) `moved` is now `serialize.stabilize_record` + `roomctl.state.settle` itself (MOVE_M
  5 cm or a zone change; yaw alone is `unchanged`), so verdict and file can't disagree.
  **Wired by the pointcloud session** (`pipeline.scan_into`, gated captures only). Synthetic G2:
  seed 0 → 0/3 dirty, close range → 0/5, seed 7 → 1/11. The last one is a one-scan spurious
  `added` (`unknown_3028`), which the removal debounce can't touch. It's P10's to fix (pointcloud
  session).
  The better fix is still P10's multi-frame vote within one capture: a real removal would then
  show in ONE `room status`, not two.
| D37 | **A scratch repo published into the real indices.** A self-test commit (`551990435d…`, branch `synthetic-selftest`, 3 room-objects + 6,803 room-voxels + 1 room-events) went live through `roomctl/publish.py` once the keys came back — so ES\|QL's time → sha ("the way it was before dinner") answered with a commit that doesn't exist in room.git. | **Guard landed (master, 2026-09-19):** `publish_commit`/`flush` with the env client only publish `$ROOM_GIT_PATH` (or `ROOM_PUBLISH_ANY=1`); anything else is neither sent nor spooled, and says so. `tests/test_publish.py` pins it. **Resolved 2026-09-19:** elastic's user approved; the 6,807 docs are deleted (live count matched the backup first, 0 failures) and `commit_at(now)` resolves to main's `1a668ec0` again. Backup kept at `elastic/artifacts/backup_synthetic_selftest_551990.ndjson`. |


### web · 2026-09-19 · Robot Session Replay: what docs/29 assumes vs. what is indexed

- **room-clouds has no capture pose.** docs/29: "anchor on the capture poses we already store in `room-clouds`". The
  mapping has `bounds`, `cameras`, `cloud_uri`, `coverage_pct`, … and no pose; `pose` exists only on `room-objects`.
  Until a `pose {x, y, yaw}` is written per capture, the replayed path starts at (0,0) and cannot be laid over
  `room-voxels`. `web/replay_api.py` already reads `pose | robot_pose | capture_pose | base_pose` if one appears.
  **Owner: whoever writes room-clouds (perception/pointcloud + elastic/mappings).**
- **No capture has encoder samples near its shutter.** `left_enc` / `right_enc` / `motor_current_*` / `balanced`
  exist only in two telemetry-hub bursts (01:43:34–56Z, 05:32:40–58Z); `fake/scene_gen.py` writes only `pitch`,
  `tilt_rate`, `odom_residual` (±2 s). So every capture replays without a path. **Owner: fake/ (asked).**
- **Sentry `events-trace/` is empty where `trace/` is not.** `GET organizations/<org>/events-trace/<id>/` →
  `{"transactions": [], "orphan_errors": []}`; `GET organizations/<org>/trace/<id>/` → the full span tree with
  `start_timestamp` / `end_timestamp`. `web/sentry_client.trace_spans()` uses the second, falls back to the first.
- **Hub start-up transient.** The first sample of each hub burst reads tilt_rate +2.84 rad/s and 8.9 A — a derivative
  taken against zero. It is recorded data, so the replay shows it (marked off-scale), but the hub should drop or
  flag its first tick. **Owner: telemetry/.**

**G2 on the real chain — green on seed 0, RED on seed 7 (P18). Corrected 2026-09-19.** My
earlier "green on both" was wrong: the harness replayed every recording and checked `git status`
once at the end, so a phantom that one scan adds and the next clears never reached the assert
(caught by the pointcloud session). The harness now checks after EACH recording. Honest result,
`tests/test_idempotent_scan.py -k perception`, network blocked:
- seed 0 × 4 scans: **clean after every scan** (`serialize` → `state.settle` + `associate`'s miss debounce).
- seed 7 × 12 scans: **PHANTOM DIFF on rescan 8** — `?? zones/desk/unknown_3028.yaml`, a
  one-scan spurious `added` that scan 9 removes. P18, the pointcloud session's: P10's
  multi-frame vote inside one capture (docs/03: an object seen in one of N frames isn't
  committed); a 2-consecutive-scan debounce on *adds* is the single-frame fallback.
The perception row runs once (it replays the same recordings for every scene/seed).
Real RealSense/stereo sessions still to come.

**Replay and downsampling (web /replay, docs/29) — corrected by elastic.** My first note here
said raw telemetry lives one hour; wrong. `downsampling.after` counts from the backing index's
ROLLOVER, and Serverless rolls over at ~1 d. Live: one backing index, never rolled over, oldest
doc (~7 h) still raw. First rollover ≈ 01:20 UTC Sep 20 (~21:20 EDT Saturday); an hour after
it, everything written before it becomes 5-min buckets. So a fixture loaded Sunday morning is
raw through judging, while real captures from before ~21:20 Saturday won't be. Whether to
push round 1 to `after: 1d` — **decided: yes** (live lifecycle `after 1d → 5m, after 2d → 30m`, applied without a rollover; raw telemetry for the whole event). **The hazard is a ROLLOVER, not age:**
the write index is never downsampled, so nothing is lost until `robot-telemetry` rolls — and a
manual `POST _rollover` (or anything that rolls the stream) starts the 1 h clock on every
capture already in it. **Do not roll `robot-telemetry` before judging.** (web verified live:
cap_0004's samples, 5.6 h old, still raw at 20 ms.) A backfill of the new signals for the
existing demo captures would land raw too — not done; it's the user's call. The fake now emits what /replay needs
(−8 s → +2 s; left/right_enc in cumulative turns, motor currents, balanced; a drive that stops
before the shutter) and puts the capture `pose` {x, y, yaw°} on room-clouds — mapped live now.
Nothing re-indexed.
| D38 | **Search ranking loses exact matches (live, 2026-09-19).** `room search scissors` ranks `tool_4f2a` (the hammer) FIRST and `scissors_9f3a` second, though BM25 matches the scissors by class; `room search "where are my scissors"` doesn't return the scissors in the top 3 at all (tape measure, speaker, marker). "where did I leave my keys" works. The rerank (`text_similarity_reranker` on `raw_description`) appears to override an exact class hit, and a conversational query dilutes the lexical leg. Demo-relevant: docs/06's search beat is phrased conversationally. | **Resolved — elastic, 2026-09-19.** Diagnosis: BM25, the Jina vector AND RRF all ranked the scissors #1 for both queries (the filler didn't dilute anything); the reranker demoted it because `text_similarity_reranker` reads only the FIRST value of `raw_description` — for the scissors "orange plastic handles, steel blades", never "scissors". Fix, no query special-casing: an ingest pipeline (`elastic/pipelines/room-objects-rerank-text.json`, room-objects' `default_pipeline`) writes `rerank_text` = class + every description, and the reranker scores that; backfilled 67/67, writers unchanged. Live, main: "scissors" and "where are my scissors" → scissors #1; "have you seen the scissors", "i need something to cut paper" → scissors #1; keys, mug, tape measure, hammer #1; the mug→cup_7e21 BM25-miss demo unchanged. Pinned by `test_search_objects_conversational`. |
| D39 | **The shared `room.git` has no `bin` or `home` in `room.yaml`** — it was written (once, pinned) before either existed. So `room reset/revert` there can't clear an object that shouldn't be there: live, "cannot apply hunk: nowhere to put 'marker_c3d4' (no bin in room.yaml)". The demo's revert beat needs the untracked scissors gone. | **Open — a human call:** add `bin`/`home` to room.git's room.yaml (a commit on main; the octree cube is untouched), or regenerate the demo room with `scene_gen.py --demo --reset` (writes both). |
| D40 | **`graph_api` POST /api/command's `revert` is only right for HEAD.** For `revert <sha≠HEAD>` it plans `_ops(head, target)` — back to that commit, undoing everything after it — instead of that one commit's inverse. A wrong physical action on stage. | **Open — web.** The panel path (`bridge/agent_api._plan_sync`) plans a true revert (inverse of that commit, only objects untouched since; the rest as `conflicts`), with a test. Port it or call it. |
| D41 | **The demo's line hits a missing state.** "set my room back to study mode" deciphers (Andrew's real parser) to `restore study`, but `room.git` has no `study` tag, so the panel answers 404 `not_found`. | **Open — whoever writes room.git (roomctl / the panel's backend):** tag the tidied commit `study` (`git -C room.git tag study b3691ea` is the obvious candidate — a human's call). |
| D42 | **gitirl-agent's own orchestrator claims physical results for `user_command`** (`RESTORE_COMPLETE — Desired state restored and verified` from his mock). Our executor applies intents; his claim is returned under `ignored`. | **Open — Andrew:** decipher only for `user_command` (docs/31 §7.2). Plus §7's other items: six verbs stay six, `target_state` = any git ref, poses declare `world_z_up`, the Y-down conversion in his adapter. |
| D43 | **`restore` had three meanings.** docs/30 §3a paired Andrew's `restore` with our `revert HEAD` / `checkout <ref>`; docs/31's first draft planned `restore X` as `checkout X` (moves HEAD, detaches on a tag); `room restore` was refused (exit 2); and docs/31 §7.8 left who verifies/retries open. `revert c2` and `restore c1` leave DIFFERENT rooms from the same history, so an alias moves the robot wrong. | **Resolved — master, 2026-09-19** (ANDREW-HANDOFF.md §1–§2, agrees with docs/31 as AGREED): `restore <state>` = `git restore --source=<state> --staged --worktree -- zones` + a commit on HEAD, then the robot; `room restore` built (no ref = the robot alone, back to HEAD); `revert`/`checkout`/`reset` stay ours and are never sent to him; his edge runs `gitspace.plan/1` (one `robot_action` per plan, per-op verify + retry ≤2, our rescan is final), roomctl's HttpRobot is the fallback when his agent isn't connected. The plan's `frame` is now the token `world_z_up` (prose in `frame_def`) so bridge's `assert_frame` accepts our own plans. Pinned: `tests/test_cli.py::test_revert_is_not_restore` and 4 more restore tests. |
| D44 | **A committed object the rescan never saw is published with no `raw_description`.** Live: `live-check` 174302b ("scissors away") was committed from the tidy scene; `revert` a2b2703 put `scissors_9f3a` back in the TREE, but the robot can't conjure an object that left the room (unapplied), so the verifying rescan staged no meta for it and `publish_commit` wrote the doc from the tree alone: class and pose, no words, invisible to the vector leg. Honest (nothing was observed) but lossy. Not on main: `room search` defaults to the current branch, and main's 1a668ec doc has its descriptions. | **Resolved — master, 2026-09-19** (elastic agreed: rerank_text builds from whatever arrives). `publish.carry_descriptions`, at DELIVERY so a spooled commit gets them on `--flush`: one search (terms on object_id, `exists raw_description.text`, collapse, newest first) fills `raw_description` + `vlm_model` unchanged; `confidence`/`observed_by`/`point_count` stay empty ("described earlier, not seen this scan"); a failed lookup still sends the snapshot and says so. Verified read-only on the live cluster: scissors_9f3a and mug_a1b2 filled (fake/scene_gen), an unknown id left blank. The old a2b2703 doc is not rewritten (live-check, not main). Pinned: 3 tests in tests/test_publish.py. |
| D45 | **The fake scanner wrote to the LIVE cluster from any repo.** D37's guard covered the commit hook (`publish_commit`/`flush`) and the real scanner (`scan_into`), but not `FakeRoom.flush`, which every fake capture goes through (`room status/commit/reset/checkout/revert/restore --scene`, and scene_gen's own CLI). A scratch copy of room.git reuses the room's capture ids, so its room-observations/room-clouds landed on top of the real ones: perception-f5 hit it once (cap_0015, 43 docs, deleted by them). Found by perception-f5, relayed by cloud. | **Resolved — master, 2026-09-19:** `FakeRoom.flush` asks `publish.is_the_room` before any ES write. A scratch repo writes its .ndjson file only and says why (`--es on` exits 1). `ROOM_PUBLISH_ANY=1` is still the one explicit opt-in. Pinned by `test_the_fake_scanner_never_writes_live_from_a_scratch_repo`, which fails with the guard removed. |
| D46 | **Andrew moved to HTTP + SSE (awzheng/gitirl `b4f3e07`, 07:05Z).** His edge now reads web directly (`GET /api/state`, `POST /api/command`, SSE `GET /api/events`) through `protocol/daniel.py`, and retired the WebSocket client, so nothing dials `/ws/gitirl-agent`. His INTEGRATION.md "Need from Daniel" lists what is still missing: (1) a way for an executable job to reach a REMOTE edge (full ops in the SSE `job` event, or an authenticated `GET /api/jobs/{id}`); (2) an authenticated non-loopback endpoint for acks, progress and results; (3) the job/result schema, ids, idempotency, replay, cancellation. And his translator executes web's job `ops` (`moved` only): a git-level preview, not `gitspace.plan/1` (no order, staging or base poses; D40 for revert). | **Built: cloud, 07:55Z, on the user's direct instruction** ("Build them", ~07:35Z, newer than the 07:25Z hold). `web/jobs.py`: `GET /api/jobs/{id}` (public read) and `POST /api/jobs/{id}/result` (`Authorization: Bearer $GITIRL_CLOUD_TOKEN`, constant-time, fails closed). It's `/result`, as Andrew and the user named it, not master's proposed `/events`. The job carries `gitspace.plan/1` from roomctl's planner (restore/checkout; revert/cherry-pick/resolve say `plan_unavailable`). The id is deterministic (command, target, HEAD, observed room); a repeat is `replayed`; the first report claims it for one `run_id`; the first terminal result stands. Every pose-bearing answer declares `frame` + `units`. **Real motion stays gated:** `motion: "mock_only"` until `JOBS_REAL_MOTION=1`. Contract: ANDREW-HANDOFF.md §2b. Fixture: `docs/fixtures/restore-job.json`. Tests: `web/tests/test_jobs.py`. **Live on `https://gitspace-five.vercel.app` since 07:58Z** (master shipped: roomctl in the tarball, token + `JOBS_DIR=/srv/gitspace/jobs` on the VM; Vercel forwards `Authorization`; verified by cloud through the public URL). **Open:** (b) `restore` is on neither allow-list (the user's call); (c) no cancel endpoint (a `failed` report says `cancelled`). |
| D47 | **The web tier was restarted by a session from ANOTHER project, under the wrong interpreter.** At 07:13Z a `minecraft` project session restarted `web/server.py` with the system Python 3.11, which has `sentry_sdk` but not `elasticsearch`, logging into its own scratchpad. `/api/search` returned 503 `search_unavailable`, locally and on the public URL. | **Fixed: master, 07:20Z.** Restarted with the repo `.venv` (`cd web && ../.venv/bin/python server.py`), log at `~/.cache/gitspace/logs/web.log`. Search is 200 locally and through Funnel, and the new process's transactions reach Sentry. **Rule:** start web only with `.venv/bin/python`. |
| D48 | **The Tailscale Funnel URL (the Devpost URL) went down at ~07:25Z.** TLS is dropped at Tailscale's ingress on both :443 and :8443, from the laptop and from a GCP VM. The node is online, `ShieldsUp` is off, the funnel capability and config are intact (re-applied), and the cert is valid until 2026-12-16. It started when venue wifi moved the laptop between networks (10.36.20.56 → 10.37.107.86). Tailscale was deliberately NOT restarted: the robot sim and the LINK worker are on the tailnet. | **Worked around by master, 07:50Z:** the public URL is now **`https://gitspace-five.vercel.app`** (Vercel frontend → GCP backend, docs/19 "As built"), and Sentry Uptime #10384065 is re-pointed there. **Open, a human:** put the Vercel URL on Devpost. The Funnel is a secondary door if it recovers. |

**Live run, 2026-09-19 ~06:30 UTC (master):** on room.git branch `live-check` (main and its
movie-night conflict untouched): `room checkout -b` → `status` → `diff` → `commit` (174302b) →
`revert HEAD` (a2b2703, mock robot) → `search` → `checkout main` → `status` clean. Both commits
published through the hook: 11 room-objects + 1 room-events + 1 room-clouds + ~1,826 room-voxels
each, every doc on a real Sentry trace (e.g. 782faf40…, sentry_url set). Those docs carry
`branch: live-check`; `room search` now defaults to the current branch, so they don't leak into
the demo's "last seen". `queries.commit_at` WITHOUT a branch filter will now answer with a2b2703.

## Real camera text is blocked on hardware — the demo runs on labelled synthetic text  ·  elastic, 2026-09-19
The hybrid-search demo (cup_7e21: BM25 misses, the Jina vector finds) holds, but every description
it ranks is scripted by `fake/scene_gen` — including master's real `live-check` commits, which used
the fake scanner. Nothing in the cluster has real VLM text: the Pi doesn't answer and no real
recording exists. **Not left implicit:** room-objects now maps `vlm_model` (backfilled 67/67
`fake/scene_gen`, verified per commit against its observations); `records.META_FIELDS` includes it,
so the fake scanner's `meta_by_id` and perception's real pipeline must pass it (real text: the
describe model's id); `demo_hybrid.py` prints a PROVENANCE banner; field-by-field table in
`elastic/NOTES.md` "What is real and what is generated". Open: web shows the SYNTHETIC badge (rule:
`vlm_model` missing or `fake/`/`scripts/`/`tests/`); scene_gen's own room-objects docs emit `vlm_model`
(else a re-index drops the label); a real recording, then `demo_hybrid.py mug --save`.

**Also found:** `perception/voxelize.py` (02:27) does `from es_sink import IndexResult` — a flat import
that only works with `perception/` on sys.path. Imported as `perception.voxelize` (records.py → the
publish hook) it raised ModuleNotFoundError: `tests/test_publish.py` went 11/18 failing. elastic
appended `perception/` to sys.path as a stopgap. **Resolved (perception):** voxelize loads es_sink
by file path and registers it under both names, so one process never holds two copies (the
try-relative-except-flat interim did); the stopgap is removed. Verified by elastic: elastic 146 passed
live, test_publish + perception/tests/test_imports.py 25 passed. **Fixed at the source (perception/segment):** 02:40's
try-relative-except-flat stopped the crash, but a process that imports both styles (the commit hook:
`publish._import` flat + records.py's `perception.voxelize`) got TWO es_sink modules. An `Offline`
raised by one wasn't caught by the other's `except` (shown live). voxelize now loads es_sink by
path and registers it under both names. elastic's sys.path lines are REMOVED from records.py and
tests/conftest.py. Pinned by `perception/tests/test_imports.py`. Green without them: test_publish
19, elastic offline 115; elastic's live suite is theirs to re-run.

- **Open — perception/segment (mine) + web:** every fallback-path object has `confidence = None`.
  `cluster.py` has no detection score, so the real pipeline indexes `confidence: null` on every
  room-objects and room-observations doc, and web/object_api's confidence stats and "flaky" verdict
  have nothing to work with. Measured instead (synthetic, 5 seeds): each object seen 11/11, centre
  spread 5–26 mm. Proposal: a geometric score for fallback clusters (sighting rate over the last N
  scans, or points vs the count expected for its size and range). The number has to come from
  somewhere real, so this waits for real captures before choosing.

### web · 2026-09-19 · D40 fixed in web/graph_api.py — and what the landing restructure broke

- **D40 (revert ≠ restore): fixed at the source.** `graph_api._revert(commit, onto, onto_name)` is the inverse of that ONE
  commit's ops, each tried against `onto` with `applies | already_applied | conflict` + the reason (it shares `_try_ops`
  with `_cherry_pick`). `POST /api/command` uses it for `revert`, plans `restore` / `checkout` as `_ops(HEAD, ref)`,
  returns `skipped[]`, and refuses a stale preview (`base_sha` ≠ HEAD → 409 `head_moved`). bridge/agent_api.py's own
  revert marks every object "changed since" as a conflict; `_revert` also tells `already_applied` apart — the cloud
  session can call it. **Needs one restart of :8000 to be live.**
- **`/` is no longer the dashboard.** landing/scene.js now locks `/` to the hero (`overflow: hidden`, `#dashboard`
  `display: none`); the dashboard is `/?info`. Every `/#status | #search | #history` link on web's pages was dead.
  Fixed in web/pages; anyone else linking to the dashboard must use `/?info#…` (web/API-FOR-PAGES.md).
- **`WEB_ALLOWED_COMMANDS` has no `restore` or `cherry-pick`.** The graph console previews both through the bridge but
  cannot queue them. One line in `.env` — **owner: Daniel**.

### perception/segment · 2026-09-19 · the real recording's floor is tilted ~6 cm/m (mount or depth scale) — RESOLVED
Found while building `perception/difference.py` on LINK's `cap_0004`. The capture uses the
nominal mount (`pitch_down_deg 33`, `height_m 1.55`, pose `{0,0,0}`, `pose_source: none`).
Measuring the median world z of floor points straight ahead gives:

| distance | floor z |
|---|---|
| 0.6 m | +0.2 cm |
| 0.9 m | +3.0 cm |
| 1.2 m | +7.0 cm |
| 1.5 m | +9.2 cm |

That is a floor rising about 6 cm per metre: the pitch is about 5–6° steeper than 33°, or the
stereo depth scale is off. Nothing fails today, because `fuse.assert_floor` allows ±5 cm and
`difference.py` compares depth against depth, not height against z = 0. Still:
- `MIN_HEIGHT`-style floor cuts are wrong past about 1 m;
- zone boxes pinned in `room.yaml` would be off by that much;
- objects on the floor at 1.5 m would commit about 9 cm too high.

**Owner: fuse/mount (pointcloud) + LINK** (the bbos `Config('depth')` pitch). Suggested check: fit
the floor plane per capture and log its tilt next to `mount_source`. **Resolved.** The measurement above
was on the old 33° / 1.55 m mount; the 13:05 recalibration came first (reply below). Re-measured on
38.1° / 1.59 m, the floor straight ahead reads +0.2 / −0.9 / −1.3 cm at 0.6 / 0.9 / 1.2 m. The
difference segmenter's crops were re-tuned on that mount (docs/15, Approach C).

**pointcloud reply (2026-09-19): resolved by the 13:05 recalibration, and now measured on every capture.**
- The recordings no longer carry 33° / 1.55 m. Their `mount_source` says *"measured from the floor on 3 captures:
  pitch 38.08 ± 0.49°, height 1.587 m"*, and with that mount cap_0004's floor is flat to ±1 cm from 0.4 to 1.3 m
  (per-0.2 m medians: −0.0, +0.2, −0.4, +2.5, −0.6 cm). Past 1.5 m it scatters −10…+3 cm between two frames of the
  still hallway: stereo noise at range, not a rise.
- The table above, under 33°, fits a 5.9° tilt **plus** a −6 cm offset at the robot (residual 0.5 cm). Pitch alone
  can't make the offset (residual 2.9 cm), so the old mount was wrong in height or depth scale too. The new mount
  fixes both.
- `fuse.floor_fit(cloud, robot_pose)` → `tilt_ahead_deg` (+ = pitched steeper than the Mount says), `tilt_side_deg`,
  `z_at_robot`. It's a line through per-10 cm medians over 0.4–1.5 m: a plane through every point let the dense
  strip at the bottom of the image set the tilt (a plane read 4.9° on cap_0004). `fuse()` charts all three as Sentry
  measurements on every capture (`floor_tilt_ahead_deg`, …). cap_0004 / cap_0005: −1.11° / −1.26°, roll 0.0°,
  +2.2 / +1.9 cm: consistent frame to frame, inside tolerance. Tests: a synthetic 5.9° pitch error reads +5.9°
  with ~0 under the robot; a 3 cm height error reads 0° and −3 cm.
