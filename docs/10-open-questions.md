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
- **The reranker reads only the FIRST of an object's three descriptions** (elastic/NOTES.md,
  "Known limits"): `rerank_position` is decided on one view's words. Retrieval sees all three.

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
- docs/16 §3.1: telemetry has no `from_mono`, hello has no `boot_id`/`t_mono_base`/`t_wall_base`/
  `t_mono_now` — the hub requires them (hub.py:582); a Pi built from docs/16 sends only bad messages.
- docs/11:526-527 lists `odom_residual, is_balanced` as robot-telemetry fields — strict rejects them
  (they are `signal` values). docs/14 Phase 5 `MAX(odom_residual)` / `MAX(ABS(pitch))` assume a wide
  row; it's `WHERE signal == "pitch"`.
- docs/23 §5 downsampling (1 s / 1 min) is impossible on a data stream lifecycle (5 min floor);
  declared 5m / 30m + 7d retention — `elastic/NOTES.md`.


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
| D16 | TASK-cloud items 4 (AWS t4g.small + Caddy + S3 7-day lifecycle) and 5 (`scripts/snapshot.sh`) don't exist; nor does `scripts/demo.sh` from scripts/README. docs/06 and BUILD_ORDER rule 5 both lean on snapshot.sh ("tag a known-good room.git"). | **Open — the cloud session.** |
| D17 | `audit_architecture.py`'s "sentry_sdk.init only in obs.py" FAILs on `web/server.py:56`, which is a *comment* ("…sentry_sdk.init() RAISES BadDsn"). Web initialises through `obs.init("web")`, correctly. | **Resolved 2026-09-18 (cloud):** the check matches code only (`^[^#]*`); audit → 17 ok · 2 warn · 0 FAIL. |
| D18 | `elastic/tests` live-query tests `pytest.fail` (deliberately — no silent skip) when the cluster is unreachable, so the whole suite reads red for the API pause: 19 errors hiding 113 passes. | **Suggestion — elastic:** a `live` marker so `-m "not live"` stays green offline. |
| D19 | **Reach.** With placeholder SO-101 numbers (0.18–0.48 m from the base centre) and docs/24's 0.28 m inflation, nothing near the middle of the fake 0.9 × 1.0 m desk has a base pose from any side — the mug (docs/06's own hero object, ~0.4 m in from the edge), marker, glasses case, scissors. Shelf keys fail line of sight: eye height 0.95 m is 5 cm above the shelf top, inside one 6.25 cm voxel. `room reset --hard --scene messy_bench` now honestly reports 0 of 3. | **Open — needs a human decision:** measured arm numbers (`robot/arm.py`) and the demo table's size/object placement (this doc's "Arm mount geometry" and "Table height"). `--no-route` keeps the fake demo moving meanwhile. |
| D20 | `perception.costmap.solve_base_pose` returns only the pose; which filter rejected the candidates goes to a Sentry span and a log line. So the executor's "cannot apply hunk: nowhere to stand" can't say *why* (blocked / IK / sight / path) — the most useful half of the message. | **Resolved 2026-09-18:** `costmap.solve_base_pose_why()` → `(pose, why)`; the executor prints it: "180 base poses sampled: 180 base fits". |
| D21 | docs/24 A1 contradicts itself: prose says a table's overhanging top doesn't block the base; its `costmap_from_voxels` counts every voxel below the robot's 1 m height, table tops included. | **Resolved in code:** `costmap.py` sets `ROBOT_H = 0.60` — the part of the robot as wide as the inflation — below table-top height. docs/24's snippet should say so. |
| D22 | **Capture ↔ telemetry is joined by time, and nothing put the shutter on the telemetry clock.** `web/store.py` / `telemetry_api.py` take `room-clouds.@timestamp` as the shutter and read `robot-telemetry` ±100 ms / ±2 s around it. docs/16 §3b's `capture_begin` has no `t_capture_mono` (docs/22 §3's does), and whoever writes `room-clouds` would stamp laptop time at processing — seconds late. Every latch-window spike would be read at the wrong instant, silently. | **Contract, built in `telemetry/hub.py`:** the Pi sends `t_capture_mono`; the hub adds `t_capture_wall` + `ts` (ISO) from the SAME mapping as telemetry; `room-clouds.@timestamp` := `ts`, never `time.time()`. **Open — robot/** (send it) **+ D9's pipeline** (write it). |
| D23 | The Pi's `tilt_rate_max` is the peak over ±100 ms of the shutter, and half that window is in the future when the shutter fires — reading it at once under-reads the peak and passes the capture the gate exists to reject. | **API ready:** `tel.peak('tilt_rate', t-0.1, t+0.1, wait_s=0.3)` waits, and returns **None** if the window isn't covered; `obs.capture_quality` now FAILS a None (and doesn't measure it). **Open — robot/capture.py** to call it that way. |
| D24 | **The executor can't wait for the arm.** `roomctl`'s `Robot.pick/place` are synchronous (return, or raise `RobotError(code)`); docs/16 `/arm` returns a `job_id` at once and the outcome arrives on `/stream` — which only the hub reads, in another process. | **Built, and roomctl already speaks it:** `roomctl/robot_client.py` `HttpRobot` calls `jobs.wait(job_id, timeout)` and maps `failed` → `RobotError(error, detail)` — shapes match `telemetry.hub.Jobs` exactly. Production wiring (any process): `HttpRobot.from_env(JobWatcher(PI_STREAM_URL).start().jobs)`, started before the first POST. **Open — robot/:** jobs aren't replayed, so one that ends during a wifi drop times out → add `GET /job/{id}` as the fallback. |
| D25 | **Who reports a robot failure to Sentry?** If the Pi, the executor and the hub each do, one grasp is three issues. | **Conflict, concrete:** `roomctl/robot_client.py:166` reports EVERY `RobotError`, including a `failed` job the hub already reported with telemetry + photo → one slip, two issues. **Proposed split:** the hub reports what arrives on `/stream` (`job failed`, falls); `HttpRobot` reports only what the hub can't see — HTTP-level refusals (`not_balanced`, `busy`, `robot_unreachable`) and `job_timeout`. **Open — master** (a condition around line 166). **Open — D9's pipeline:** `FrameCache().put(capture_id, camera, left_rect)` per camera, or failures arrive without a photo. |
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
