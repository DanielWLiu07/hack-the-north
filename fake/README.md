# `fake/` — the unblocker

## Why this exists
Two of the four tracks (`roomctl`, `elastic`/`agent`) would otherwise sit idle until perception
works. This module emits **exactly the object-record shape the real pipeline will**, so they
start at hour zero and swap the data source later.

It fakes the **mess**, not just the answer. Git gets the clean quantized truth; Elasticsearch
gets everything perception would have thrown away.

## Use it

```bash
python fake/scene_gen.py --demo --reset                # the whole story into ./room.git (below)
python fake/scene_gen.py --commit clean_bench          # scan + commit one scene
python fake/scene_gen.py --scan messy_bench            # scan only: a dirty working tree for `git status`
python fake/scene_gen.py --commit movie_night --branch movie-night
python fake/scene_gen.py --index fake/out/demo.ndjson  # (re)send a saved run once ES works
python fake/scene_gen.py --list
```

- Repo: `$ROOM_GIT_PATH` (default `./room.git`, gitignored — a **separate** repository), or `--repo`.
- Every run writes `fake/out/<run>.ndjson` in `_bulk` format — `curl -H 'Content-Type:
  application/x-ndjson' --data-binary @fake/out/demo.ndjson $ELASTIC_URL/_bulk` works as-is.
- It also indexes directly when `ELASTIC_URL`/`ELASTIC_API_KEY` work **and**
  `elastic/setup_elastic.py` has created every target. It refuses to write before the
  mappings exist (docs/13 rule zero). `--es on|off|auto`.
- Timestamps are anchored to *now*, so TSDS accepts them (`look_back_time: 7d`).
- stdlib + `pyyaml` + `python-dotenv`. No ES client needed.

`--demo` builds, ending about three minutes ago:

```
* messy     afternoon: mug moved, marker gone, scissors out     main
| * movie   movie night: mug by the couch, lamp pulled over, snacks out   movie-night
|/
* clean     the bench, tidied                  (the hammer leaves: history only)
* initial   initial scan: the bench as found
```
`git merge movie-night` on main then **conflicts on `zones/desk/mug_a1b2.yaml`**; the other
four changes (lamp slid, speaker shelf→desk rename, notebook removed, bowl added) merge clean.
Between commits it runs the watch loop: ~2 700 observation docs, with a hand in frame at
the moment each change happens, and a laptop that hides the glasses case for ten minutes.

## Scenes
| file | what |
|---|---|
| `scenes/clean_bench.yaml` | The committed "good" state: 7 objects on the desk, 4 on the shelf. Owns the zones, cameras and anchor. |
| `scenes/messy_bench.yaml` | clean_bench + exactly 3 changes: mug moved (x, yaw), marker removed, scissors added. |
| `scenes/movie_night.yaml` | Divergent branch: moves the mug somewhere else → the merge conflict. |
| `scenes/bench_with_hammer.yaml` | clean_bench + a hammer (`tool_4f2a`) that the VLM can't agree on. |
| `scenes/occluded_bench.yaml` | A laptop hides the glasses case from all 3 cameras → carried forward, **not** deleted. |

Derived scenes use `extends:` + `move:` / `remove:` / `add:` / `occlude:`. The loader rejects
objects that overlap, float above their surface, or sit outside their zone.

## What it fakes, and where each piece lands
| the mess | how | where to look |
|---|---|---|
| three disagreeing descriptions | every capture draws 3 different lines from a per-object pool, one per camera view (a second `label_attempt` if a view is missing) | `room-observations.raw_description`, `room-objects.raw_description` (array of 3) |
| the "mug" vs "cup" gap | `cup_7e21` never says "mug" anywhere; half its descriptions say "ceramic cup" | hybrid search for "mug" |
| per-camera coordinate conflict | per-camera bias + noise; cam2 is ~4.5 cm off | `raw_x/y/z` per camera, same `capture_id` |
| rejected clusters | `too_small` `plane_fragment` `no_depth` `low_confidence` `single_frame` `roomignore:{person,robot,cable}` `unassociated` | `object_id: null`, `rejected_reason` |
| wobbling confidence | slow sinusoid per object; `keys_7c2e` is chronically flaky (0.2–0.8, frequently dropped) | `confidence` over the watch loop |
| occlusion | partial (one camera blocked: still committed) and total (carried forward) | `occluded: true`, `point_count` ≈ 0 |
| YOLO's own opinion | stock COCO label per object — the mug is a "cup", the marker a "toothbrush" | `raw_label` |

Git never sees any of that: fused poses get sub-quantum jitter and go through
`stabilize()` (1 cm / 5°, 1.5-quanta hysteresis), so rescanning an unchanged scene is
byte-identical. Verified: 25 consecutive rescans, `git diff --exit-code` clean.

## The documents (field names are the contract — elastic/ maps these)
Anything synthetic is labelled: `vlm_model: "fake/scene_gen"`, `cloud_uri: null`. Every doc of a
capture carries the same synthetic `sentry_trace_id` / `sentry_span_id` (as `obs.trace_fields()`
would); `sentry_url` is left out on purpose — a link to a trace that never existed is dead.

**`room-objects`** — one per object per commit, `_id = "<sha>:<object_id>"`, full snapshot.
`@timestamp commit_sha parent_sha branch author capture_id object_id class zone`
`pose{x,y,z,yaw} position{x,y} (point) extents{x,y,z} color first_seen confidence point_count`
`observed_by[] raw_description[] vlm_model voxel_key voxel_key_l5 voxel_key_l3`
No `message` on object docs — it would make every object in a commit match its text. Join
`room-events` on `commit_sha` for it.

**`room-observations`** (TSDS, `create`, no `_id`) — one per (object, camera, capture, label attempt).
`@timestamp capture_id object_id (nullable) camera confidence point_count raw_x raw_y raw_z`
`occluded rejected_reason raw_description raw_label vlm_model label_attempt`.
Commit-path captures are `cap_NNNN` (3 cameras + VLM); watch-loop captures are `watch_NNNNN`
(cam0, no VLM, cheap 10 cm association → `unassociated` when something moved).
Every doc in a capture has a distinct millisecond timestamp — rejected rows share
`object_id: null`, and in a TSDS the same dimensions + `@timestamp` overwrite each other.

**`room-events`** (data stream, `create`, `_id = "<sha>:commit"`) — `@timestamp event_type commit_sha
parent_sha branch message author capture_id outcome objects_affected[] objects_added[]
objects_removed[] objects_moved[] zone[]`. `@timestamp` equals the git commit date.

**`room-voxels`** — `_id = "<sha>:<voxel_key>"`, exactly the docs/13 shape: `@timestamp commit_sha
parent_sha branch voxel_key voxel_key_l5 voxel_key_l3 cell{x,y} (point) z_min z_max density zone
object_id`. Surfaces + object boxes at 6.25 cm; object-boundary cells flicker between commits.

**`room-clouds`** — `_id = capture_id`: `@timestamp capture_id commit_sha (null for a scan or a
rejected capture) cloud_uri point_count bounds{min,max} cameras[] coverage_pct icp_residual_mm`
plus the docs/22 §4 quality gate: `skew_ms tilt_rate_max quality_ok`.

**`robot-telemetry`** (TSDS, `create`) — `@timestamp signal value`, 50 Hz, ±2 s around every
commit-path shutter: `pitch` (rad), `tilt_rate` (rad/s), `odom_residual` (m). Same trace ids as
the capture. `tilt_rate_max` on the cloud doc is the peak |tilt_rate| within ±100 ms of the latch.

**The rejected capture.** `--demo`'s messy commit is knocked ~200 ms before its first shutter
(`--bump` does it on any scan/commit): `tilt_rate` rings to ~0.13 rad/s, `odom_residual` spikes
to ~2 cm, and that capture — **`cap_0004`** in `--demo` — gets `quality_ok: false`, no commit,
and a `room-events` doc `event_type: "capture_rejected"` (`_id "<capture_id>:rejected"`) whose
`objects_moved` is the diff it *would* have committed: every object shifted by the lean, ten
"moved" where one did. Its observations show the shifted `raw_x/y/z`. The retry five seconds
later (`cap_0005`) passes and commits the real three changes.

Cameras latch together (docs/22): a capture's three cameras are a few ms apart, not 550 ms.

## From Python (tests, roomctl)
```python
from fake.scene_gen import FakeRoom
room = FakeRoom(tmp_path / "room.git", quiet=True)
room.commit("clean_bench")           # -> Result(sha, verdicts, changes, ...)
room.scan("clean_bench")             # writes the working tree only
room.actions                         # the pending (bulk action, doc) pairs; room.flush(...) sends them
```

## Acceptance criteria
- [x] `scene_gen.py --commit clean_bench` creates a real git commit and ES documents
- [x] `scene_gen.py --commit messy_bench` produces a `git diff` with exactly 3 changes
- [x] Hybrid search for "mug" hits an object whose only matching description says "cup" —
      verified live (9.6 serverless): BM25 on `class` + `raw_description.text` returns only
      `mug_a1b2`; the semantic leg returns `cup_7e21` second
