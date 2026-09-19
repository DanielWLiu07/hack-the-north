# 04: Test plan

Three rings: **unit** (no network), **bbsim** (the whole loop against the fake robot, in CI), and
**hardware acceptance** (the gates). The existing suites must stay green at every merge: tests
500 · web 118 · bridge 25 · perception 259 · telemetry 40 · agent 3 · elastic-offline 106.

## Ring 1: unit (offline, `-p nonet`)

| test file | proves |
|---|---|
| `tests/test_frames.py` | golden cases: BB yaw 0 faces +y; `h = θ + φ − π/2` for θ ∈ {0, π/2, π, −π/2} × φ ∈ {0°, 90°, 180°, −90°}; round-trip error < 1e-9; the BB doc's robot-relative formula; `base_pose_to_navigate` |
| `tests/test_registration.py` | a synthetic T recovered from a synthetic tag sighting (noise 5 mm / 1° → residual < 1 cm); the two-point teach; **invalid after a `map_gen` change**; refuses above 0.03 m residual |
| `tests/test_bb_protocol.py` | decode recorded `/heavy` bytes (types 4 and 5; `first=1` clears; removals apply; types 2/3/6/7 ignored); `/ws` state parsing; a job-error → RobotError mapping table |
| `tests/test_bb_source.py` | synthetic voxels of the demo scene → the right candidate count, centroids ≤ 1 cm, extents ≤ 1.5 cm, yaw axis ±5°, colour; the surface plane dropped; touching objects split by height/colour |
| `tests/test_policy.py` | every verdict/action pair; a personal zone ignored; untracked in shared → lost-and-found; Tier B maps tidy → chore |
| `tests/test_watch.py` | **debounce:** one pass never confirms; two fresh passes do. **Freshness:** a stale block never produces moved/deleted. **Line of sight:** a blocked object is carried forward. The heartbeat is `ok` only when confirmed-clean |
| `tests/test_pr.py` | propose → a branch with one record moved to a free spot (no overlap); approve → a `--no-ff` merge with the `Approved-by` trailer; drift after the merge compares to the NEW spot |
| `tests/test_executor_order.py` (+ cases) | `arrive_tol` margin respected; the arm aims from the actual pose; `nav_short` skips honestly; the patrol-resume call is made after every trip |

## Ring 2: bbsim end to end (`tests/test_bbsim_e2e.py`, offline, loopback only)
Start `fake/bbsim.py` on loopback ports, then:
1. **G2 across passes:** an untouched desk over 5 patrol passes → `git status` empty after
   EVERY pass.
2. **Mess:** move the mug in the sim → pending after pass 1, confirmed after pass 2 → the check-in
   `error` → (Tier A) navigate + pick + place in the sim → verified → `ok`. (Tier B) a chore opened →
   the sim "person" fixes it → closed.
3. **Occlusion:** put a box between the robot's viewpoint and the tape measure → no `deleted`,
   ever.
4. **Intent:** open a PR (lamp → shelf) → approve → the robot moves it → knock the lamp over in
   the sim → the restore goes to the **shelf**.
5. **Map reset mid-job:** bbsim bumps `map_gen` → `map_reset` error → re-registration → the job
   retried once.
6. **Nav failure:** bbsim returns `failed: no path` → a `nav_failed` RobotError → the op is
   skipped, reported "2 of 3", and a Sentry issue is filed (mocked transport).
7. **Slow reader:** the test reads `/heavy` slowly → bbsim drops the queue → `first=1` → the
   mirror rebuilds correctly.

## Ring 3: hardware acceptance (the gates)

**Gate 1 (14:30): the robot's truth** (`scripts/bb_check.py` automates 1–4)
1. `GET :8020/health` → `main_py.connected: true`; `/ws` `ready: true`.
2. `POST /map/rectangle` (2 × 2 m, sweep) → `job.error` null in ≤ 4 min; `/map` shows floor and
   obstacles.
3. `POST /navigate` to a point 1 m ahead → `reached`; `/pose` within 0.25 m.
4. Record 60 s of `/heavy` + `/ws` + `/stream` with `scripts/bb_record.py` → the fixtures for
   rings 1 and 2.
5. **The arm:** one commanded motion through bbos (robot team). **Yes/no decides the tier.**
6. **Measure:** BB z = 0 (floor voxels), the camera→base mount, the arm's reach envelope.

**Gate 2 (20:30): one full loop on the real robot**
- Registration from the tag: the residual < 3 cm; a known point on the table lands within 3 cm in
  both frames.
- G2 on the real desk over 3 passes: an empty diff each time.
- Mess → confirmed in ≤ 2 passes → tidied (A) or chore (B) → verified → green; the Sentry trace
  shows `nav.navigate` spans; the room-clean check-in goes red → green.
- **Record backup video v1 the moment this passes.**

**Freeze check (00:00):** the 90 s script runs 3 times in a row, without touching anything
between runs.

## What we measure and show (these numbers go in the stories)
The zero-diff rate over N passes · the mess-to-confirmed latency (in passes and seconds) ·
navigation arrival error (cm) · the registration residual (cm) · the tidy success rate
("n of m") · the heartbeat red→green time.
