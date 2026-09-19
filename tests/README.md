# `tests/`

One test matters far more than the others.

```bash
.venv/bin/python -m pytest -q tests                     # everything, ~30 s
.venv/bin/python -m pytest -q tests/test_idempotent_scan.py   # THE gate — run it constantly
```
Use the venv: `test_publish.py` needs the `elasticsearch` client (it skips without it).
Nothing in `tests/` opens a socket to a real service — every API boundary is mocked, and
the suite passes with the network blocked in-process.

| file | what it proves | status |
|---|---|---|
| `test_idempotent_scan.py` | **THE test.** Rescan an untouched room 20× → `git status --porcelain -uall` stays empty (≡ `git diff --exit-code` clean *and* nothing untracked). Plus: occluded ≠ deleted, one move = one file, an 8 mm nudge = no diff, and a control proving the gate goes red without hysteresis. | **green on the fake**; perception row skips until recordings exist |
| `test_state.py` | The frozen object record: exact bytes, round-trip, the grid, nothing that wobbles. | green |
| `test_cli.py` | `room init/status/diff/add/commit` against fake scenes; `diff` is byte-identical to `git diff`; `reset --hard` / `revert` / `checkout` plan, move the fake room, and verify by rescan; write verbs refuse without a scanner. | green |
| `test_octree_key.py` | Prefix ⇒ containment; a point inside cell `"370"` has a key starting `"370"`; sizes match the 8 m ladder. | not written (perception/voxelize) |
| `test_executor_order.py` | Never places into an occupied spot (300 random layouts, replayed step by step); a swap stages one object; a 3-cycle needs exactly one stage; a chain needs none; unapplied hunks; a slipped grasp never leads to placing onto the stuck object. | green |
| `test_association.py` | An object that leaves for 3 commits and returns keeps its original `object_id`. | not written (perception/associate) |
| `test_robot_capture.py` | **The latch.** Every camera is `grab()`bed before any is `retrieve()`d and the pose is read between them; three 50 ms decodes make < 10 ms of skew — and the control: the same cameras round-robin make > 80 ms. The quality gate (reject → quiet window → retry ×3; missing tilt evidence rejects; a stereo-only rig decides the latch half), its thresholds held equal to `obs.capture_quality`'s, replay of both recording layouts (a recorded JPEG passes through byte-identical; depth stays uint16 mm), and the **real `sentry_sdk` into an in-memory transport**: the transaction carries the `capture_id`, the spans and the measurements. Nothing in `robot/` imports a network client. | green — no camera needed |
| `test_robot_server.py` | `robot/server.py` against docs/16, whole process in sim behind an in-process client: all six endpoints, the one error shape, `/stream` hello + replay, `/frames` binary frames assembled by `capture_id`, a knocked robot rejected then captured in the next quiet window, a slow capture never blocks the event loop, `roomctl.robot_client.HttpRobot` driving it unchanged, and `GET /camera/<name>.jpg`: a live view costs no capture id, reads the camera once per interval however many ask, and never contends with a capture. | green |
| `test_robot_events.py` | `GET /events` (SSE): framing, ids that count up by one, `Last-Event-ID` resume with no gap and no duplicate, **a Pi restart detected rather than resumed across**, the keepalive comment, and a client that stops reading costing nobody else anything. | green |
| `test_robot_bbos.py` | `robot/bbos.py` against a fake `bbos.Reader` shaped as the real one was **measured**: pitch converted from degrees, `tilt_rate` = `gyro[1]`, `balanced` derived, a dead IMU daemon = no evidence (even while wheels report), every Reader on one thread and closed, a camera read only when a capture asks, a stalled camera daemon = `camera_unavailable`. | green |
| `test_robot_balance_source.py` | The seam for the real balance loop: state that stops arriving **expires** (through the real tap, a dead loop leaves `tel.peak()` = None, never the last value held), the loopback-UDP receiver, and every verdict of `robot/check_source.py` — frozen, deg/s, differentiated-from-raw-pitch, missing `tilt_rate`/`balanced`, I/O in the tick. | green |
| `test_serialize.py` | Quantization and hysteresis: a 4 mm jitter produces no diff; a 20 cm move produces exactly one. | lives in `perception/tests/` (pointcloud session) |

## Pointing the gate at the real pipeline

`test_idempotent_scan.py` runs every check against every scanner that exists. The real one
needs two things, and skips (saying which is missing) until both exist:

1. `GITSPACE_RECORDINGS=/path/to/dir` — two or more recorded captures of **one untouched scene**.
2. `perception.pipeline.scan_into(repo_dir, recording)` — replay one recording through
   depth → fuse → segment → merge → associate → serialize, and write the working tree with
   `roomctl.state.write_tree`, stabilizing against the repo's HEAD.

The first recording becomes the baseline commit; every later one must leave the tree clean.

## Treat red as stop-the-line
It is the regression suite for the whole project. If the gate is red, the system is a liar
and nothing downstream matters. The fake half is only as honest as the fake's jitter — which
is why `test_the_gate_has_teeth` exists.
