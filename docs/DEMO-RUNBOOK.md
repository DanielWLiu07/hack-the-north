# DEMO-RUNBOOK — what to click, in what order, and what to do when it breaks

**Re-rehearsed 2026-09-19 21:35–21:50Z** by perception (htn:5) against the roommate MVP
([`../plan/roommate/tasks/MVP-NOW.md`](../plan/roommate/tasks/MVP-NOW.md)), with the robot off
the network since ~18:00Z. Method, unchanged: CLI beats on a **copy** of the room
(`ROOM_ES=off`), web beats read-only against the running sites, and **every line quoted below was
observed**. Anything marked **NOT REHEARSED** was not.

All five beats ran. Beats 2 and 3 were re-run at 01:30Z on the restarted stack (gitspace-22),
which carries the association fix and the reach margin; where other sessions' numbers differ,
both are quoted as single samples.

---

## 0. What is real right now

| piece | state | evidence |
|---|---|---|
| sim site `:8001`, bbsim, simulated adapter `:8765` | **up** | `demo_sim status`: bbsim up, site up, watch window up; `/health` on 8765: `"simulated": true` |
| the room as `main`, live map, chores, PRs | **real** | `/api/nav/snapshot`: pose (1.252, −0.696), yaw 1.3535, grid 0.03 m, 5 path points, `frame: world_z_up`; `room chores` → none; `room pr list` → none |
| nothing simulated reaches Elastic or Sentry | **enforced** | `:8001/api/health` → `elastic.configured: false` ("ELASTIC_URL / ELASTIC_API_KEY are not set"), by design |
| **beat 2** mess → confirm → tidy → verify | **works**, ~half a minute; stalls about one run in four (§1 #1) | pending 0.2 s → confirmed 6.5 s → in hand 9.6 s → verified 12.7 s |
| **beat 3** "I meant that" → PR → approve | **works**, with a click race (§3) | `PR #1 opened as seen and approved (1e39a9f)`, clean at 33 s |
| search → resolve → point job (beat 5's first half) | **real** | "where are my keys" → `keys_7c2e` via elasticsearch, score 1.454, margin 0.45, `job_point` with target pose (0.62, 0.78, 0.91) yaw 140, `estimated_s` 40 |
| the job reaching Andrew's edge (beat 5's second half) | **wired** | `POST /api/object-life/keys_7c2e/point` → `executor: "housebot-edge"`, `dispatch: {dispatched: true, edge: "http://127.0.0.1:8780"}`, his edge `/health` ok. The panel sentence dispatches too — what I read as `not_connected` at 21:46Z and 21:55Z was the job object's BUILD-time fields inside the answer (d2, since fixed, with a test that the two agree). **Re-rehearse after the next `:8000` restart** |
| `room why` (beat 4's third question) | **real and good** | on `:8000`'s room: `cap_0005: quality gate PASSED (skew 2.12 ms, limit 25; tilt rate 0.0051, limit 0.05)`, telemetry peaks, `verdict: trustworthy`, a trace id |
| "before dinner" time travel | **resolves, but see #2** | `--before "yesterday 7:15pm"` → `e51a75a initial scan` "found by elasticsearch" |
| Seer reading our code, unprompted | **real**, `docs/seer/` | `scripts/seer_sweep.py` in tmux `seer-sweep`. GITSPACE-15: *"the head stereo camera is physically off the USB bus"*; GITSPACE-6: motor current reads 0 because `robot/bbos.py:209` uses `d["iq"]`, the bug our own `robot/NOTES.md:49` records; GITSPACE-1J: named the commit that caused it AND the one that fixed it 52 min later. Capped in code, stops at `root_cause`, applies nothing |
| the robot | **live again 2026-09-20 08:37Z** — hardware mode, `cam0` on the bus, SLAM up, 17.0 V, 27 ms away | The frame gate decides whether names are trusted: a frame may name objects only when ≥ 60% of its standing pixels agree with the map within 15 cm. One definition, one code path, `alignment()` in `scripts/bbos_map.py`, reproducible with `bbos_map.py frame-check`. Real frames 67 to 85% (`cap_1001` 83.5% of 90,662 px venue · `cap_1004` 84.8% of 200,919 px venue team room · `cap_1006` 66.7% of ~203.5k px **the same team room table** (203,507 under the repo `.venv`, 203,426 under system python — the pixel count is stack-dependent, the 66.7% is not) — cluttered, close, people in frame. Both were called "home desk" in earlier notes; that was a mislabel, corrected by c6, and the instances have been renamed); **controls**: wrong heading 8 to 23%, a seven minute stale map 43.2%. Quote the gate and its controls, never a single frame's score. **If the robot is up at the venue:** `room_live.py zone` measures the table zone from the map, then ONE `add --name` facing the table does the scan with the head frame and the commit (c6). **Run EVERYTHING under the repo `.venv`, never system python** — this bites more than the naming path, and it degrades quietly rather than failing loudly (perception-02 found the web server on system python with `elasticsearch` missing: every semantic lookup silently fell back to literal word matching, the page looked fine and the answers were merely worse). Measured deltas on this laptop: system python is missing `elasticsearch` and `open3d` entirely, and carries cv2 **5.0.0** against the venv's 4.14.0, ultralytics 8.3.232 against 8.4.155, torch 2.9.1 against 2.14.0, openai 2.28.0 against 3.16.1. Which entry points TELL you: `room_live.py` and `demo_sim.py` do (demo_sim re-execs itself under the venv). `bbos_map.py` and `capture_to_recording.py` do NOT — they run silently under whatever interpreter you typed, and `bbos_map.py scan` is the ad-hoc command most likely to be reached for. Type the venv path for those two. A guard that compares `os.path.realpath` of the interpreters silently never fires on this laptop, because `.venv/bin/python` is a symlink to the system binary — verified: the real paths are identical while the site-packages are not. `sys.prefix` is the check that works (gitspace-22). **The one-command check is `curl -s localhost:8000/api/health` — read `search.python`.** Verified live: it returns the interpreter path of the process actually serving (`…/gitspace/.venv/bin/python`), and when search is broken `search.detail` carries the fix. A suite tells you about the tree; this tells you about the process that is serving, which is what a judge will hit. It exists because someone lost 07:13Z on a demo night to exactly this: system Python, no `elasticsearch`, `/api/search` 503, and `/api/health` said ok throughout because it only asked whether the cluster was up. **Do not trust a green test suite to tell you instead** — the two suites fail in OPPOSITE directions, and in both cases the cause is the interpreter, not the code. `perception/tests` HIDES it: 386 passed / 12 skipped under `.venv` against 384 / 14 under system python, the three that vanish all Elasticsearch (`test_boundaries.py:311`, `test_voxelize_es.py:77` and `:176`), both runs green. `web/tests` + `bridge/` SHOUT: 359 passed / 0 skipped under `.venv` against 353 passed / **6 failed** under system python, five in `test_search_adapter.py` (`AttributeError: 'NoneType' … 'Queries'`) and one named `test_health_goes_red_when_this_process_cannot_run_the_search` (perception-02). So six web failures are not a broken tree, and a green perception run is not a working one — check `search.python` and move on. If a measurement is ever compared across interpreters, **run the same interpreter twice first** — if the two runs are not identical, the difference you go on to measure is not the one you think it is (both of tonight's numbers needed that control before they meant anything: same stack twice gave byte-identical output on both paths, so the differences are cv2's and not RANSAC luck). Then compare PER PIXEL or per object, never by percentiles: two range maps off the same frame had identical p5-p95 while only 68% of their pixels agreed (cv2 5.0.0 vs 4.14.0, p99 of the disagreement 14 mm at 1 m and 65 mm at 2 m). Percentiles were the wrong instrument and nearly cost us a wrong all-clear. Any capture pair meant to be compared — notably the 1.0 m / 2.0 m packet pair — must be taken in ONE session under ONE interpreter, named in the capture note, or a library difference lands inside the quantity being measured. For the naming path specifically: same weights file, but ultralytics 8.4.155 / torch 2.14.0 against 8.3.232 / 2.9.1 moves a 0.25-confidence mask across the threshold and changes WHICH objects get named (measured on one pair: identical 85 candidates, 7 of 8 names shared, the 8th differs). **Do not quote a naming rate**, before or after the fix: several names still sit on the wrong geometry. The cause of the slab is measured and upstream of the model, and it is HEIGHT, not colour: the surface is where columns end (0.78 m in that zone) but `room.yaml` says 0.70, because `measure_surface` reports the middle of the 6 cm slab it won and the apron and legs under the top drag that middle down. `_plane` could only search ±6 cm from that hint, locked onto 0.735, dropped it, and the real tabletop survived as "objects" with everything on the table glued on. Not viewpoint (two very different poses gave the same rate) and not the VLM. A per-tile colour reference was built first and moved the slab 3,517 → 3,438 cells, i.e. nothing — the colour test was never reached. Fix is per tile applied to the surface's HEIGHT, re-anchored on the map's own answer instead of the hint: the 2.81 m tabletop candidate is gone (largest now 2.16 × 0.34 × 0.36 m, and that one is people and chairs behind the table, a zone-width question). Same pair `cap_1006` + map `20260920-085105`, fresh repos, the before run against git HEAD: 64 objects / 2 named / 39 of 64 seen → 85 objects / 8 named / 63 of 85 seen; alignment 66.7% either side; suite 384 passed (c6 reproduced the after-count independently; `demo_sim.py check` 4 of 4 PASS against this tree, zero phantoms). **`perception/bb_source.py` is UNCOMMITTED — if master's batch has not landed it, the bench-beside-a-table geometry still fails and the demo table should be checked against `room_live.py zone` first.** **Which table, settled:** the bad geometry IS a venue table — the team room's wooden workbench butted against the grey table, contiguous at the same height, 3.7 m². The three clean maps (0.67 to 0.79 m², single rectangles) are the **E5 hallway**, Saturday, a different room; nobody can say from here whether those tables are free tomorrow. So: one venue table trips it, three others do not. **Check the table you are about to shoot on** with `room_live.py zone <instance>` — clean single rectangle under ~1 m² good, 3 to 4 m² or a visibly two-material top bad. Two independent flows (f5's and c6's) agree exactly on the after count, 85 / 63 in view / 8 named |

---

## 1. What is broken, ranked by how likely a judge is to hit it

| # | a judge hits it when… | what they see | who |
|---|---|---|---|
| **1** | **beat 2 does not always finish** | the badge goes red and stays red: `tidy-N` minted, `last_verified_job` null, bbsim never leaving `patrol`. At 01:30Z on the restarted stack, two of my three runs fell short (one tidy never started; one put the mug back but never turned the badge green), while gitspace-22 measured 5 of 5 green an hour earlier and my third run finished in 14.3 s. So it is a flake, not a break, and neither the disk (17 GB free) nor the reach margin explains it. **If it stalls, `demo_sim reset` and go again**; `check`'s stall dump names the job events, the loop verdict, bbsim's job and both poses against `main` | gitspace-22 |
| **2** | **beat 4's middle sentence, as scripted** | "put **it** back the way it was before dinner" → `ok: false`, `unknown_command`: the pronoun is the problem (web-64 found the gap between two bridge rules — one takes "back" without "it", the other "it" without "back"). "put **the room** back …" parses. Then it fails for a second, honest reason: "before dinner" means **yesterday** 18:00 (today's hasn't happened at 17:42 local) and `room.git` starts at 23:02Z, so `no commit on main before 2026-09-18T18:00-04:00`. **Decision (master): do not demo "before dinner" — say "2 hours ago"**, which answers with the commit. d2 has the one-line regex fix for the pronoun | bridge |
| **3** | beat 3 | never reached while #1 stands | gitspace-22 |
| **4** | beat 5, until `:8000` is restarted | the panel's answer contradicted itself: the job dispatched, but the job OBJECT inside the answer still carried its build-time `executor: "not_connected"`. d2 fixed it (a dispatched job now says `housebot-edge` / `dispatching`, and one that was not sent says why), but the fix is not in the running process. **Look at the trace, not just the job fields, until it is restarted** | gitspace-d2 |
| **5** | any restore that actually plans | the plan resolves, then reach bites: `nowhere to stand to pick up 'mug_a1b2' … 167 base fits, 1 ik, 12 path`, same for the marker and the hammer. **The bin is fixed** — `room.yaml` HEAD (`24c4b447`) has `bin: {pose: [0.30, -0.75, 0.45]}`, so "no bin in room.yaml" is gone and removals plan. What is left is the placeholder arm: with r_max 0.48, a 0.28 m base and the 0.9 margin, only objects within ~0.17 m of a table edge can be stood in front of at all | robot (measure the arm) |
| 6 | `room why` on the **sim** room | `! no capture is recorded for this commit … verdict: don't trust this commit's picture` — correct (the sim indexes nothing) but it reads as a failure. Ask it on `:8000`, where it is rich | — |
| 7 | the CI heartbeat while dirty | `heartbeat: {"last": "error"}` — that IS the badge working, but "error" reads as broken | — |
| **9** | **any beat, right after something moves** | the room churns with **phantom objects**: the map keeps an object's cells at its old pose until the robot looks there again, so there is briefly one blob more than there are records. Since the association fix, what survives is an UNTRACKED phantom near the moved object with a FRESH id every pass, so it cannot accumulate the two passes a chore needs — measured after the fix: 3 samples of ~20, all `pending`, none confirmed, nothing minted. Before it: 2-4 at a time for ~30 s, and one CONFIRMED under a neighbour's name. ⚠ **On the REAL robot the window is far longer**: bbos revises cells in the camera's view with a median lifetime of **40 s (p90 275 s)**, and never while the spot is out of view (c6, measured). So on hardware expect the badge to report an untracked arrival first and the move itself only after the old cells clear — "about half a minute" (beat 2) is a bbsim number, not a robot one | perception (held) + bbos carving |
| 8 | nothing visible | **a full disk shows up as a 6–20× slowdown, not an error**: my test file 4m43s vs 18s, the suite 13m vs 61s, perception-02's test_pipeline 181s vs 30s for four files. It also crashed the watch loop once (`Errno 28` writing `misses.tmp`). Cleared at ~22:00Z (17 GB free); the lesson stands. **`df -h` first** | everyone |

Operator traps (a judge never sees these; each one silently breaks the run):

- **T1.** `room` is not a command: `alias room="$PWD/.venv/bin/python -m roomctl"` from the repo root.
- **T2.** `demo_sim check` does **not** reset at the end — it leaves the lamp moved and approved on `main` (beat 3's end state). Run `demo_sim reset` before a clean run. A good run is ~3–6 min (gitspace-22).
- **T3.** The sim room is `~/.cache/gitspace/rooms/sim-demo`, **never** `room.git`; its site is `:8001`, and `:8000` is the real room. Rehearse on a copy of whichever one you mean.
- **T4.** `room --help`'s prose still lists only the old verbs; `watch`, `chores`, `pr` and `why` appear in the usage line above it.
- **T5.** Driving beats 2/3 changes shared state. Say so in the team channel first — web-64 may be clicking the same beats.
- **T6.** Chrome, not Safari, for every page ([`MVP-NOW`](../plan/roommate/tasks/MVP-NOW.md)).
- **T7.** On `/?info#history` the agent input only appears once a commit is **picked from the
  "preview a moment" dropdown**. Clicking a node in the rail does nothing visible, so a presenter
  who clicks the graph and waits is standing in front of a console that never appears (web-64 lost
  three attempts to it; re-verified after the 01:20:58Z restart, unchanged). The page's empty state
  now names the control, so this bites only someone who reads neither. Whether the film strip drives
  the graph's selection is untraced — the dropdown is the path verified end to end.
  ⚠ Its first option is the CURRENT HEAD, so picking blind lands on `24c4b44 room: give the room a
  bin`, not the story commit. Beat 4 wants `1a668ec` if it wants a particular moment.

---

## 2. Pre-flight

```bash
cd ~/Dev/projects/2026/gitspace
alias room="$PWD/.venv/bin/python -m roomctl"        # T1
df -h /                                              # #8: anything under a GB, stop and clear
python scripts/demo_sim.py status                    # bbsim up · site up · watch window up
python scripts/demo_sim.py reset                     # T2: known start state
./scripts/check_dispatch.py                          # the chain a job travels — see below
# and if web-64's `job` event fix is not live yet, :8001 needs a restart to show the tidy in flight
```

**`check_dispatch.py` is not optional, and it is new because we needed it.** On 2026-09-20 nothing was
running `robot/adapter.py`; :8765 refused every connection for six hours and thirty-four commands while
the web tier and Andrew's edge both reported healthy. Beat 5 was dead and the only trace was a Sentry
issue nobody was reading. This walks the chain hop by hop — web tier, dispatcher (on? pointing where?
which kinds?), edge, adapter — and exits 1 naming the hop that is down, with the command to start it. It
GETs health endpoints and nothing else: a pre-flight that dispatches a motion is not a pre-flight, so it
proves each hop is alive, not that a job flows. For that, press the button and watch the adapter log
`POST /v1/actions`. It also says loudly if the adapter is in HARDWARE mode rather than simulated.

Then, in Chrome: `http://127.0.0.1:8001` (the sim site) and `http://127.0.0.1:8000` (the real
room, for beat 4). Warm both once. `curl -s :8001/api/health` must say
`elastic.configured: false` — that is the proof that nothing simulated reaches the indices.

---

## 3. The run

**Beat 1 · the room is `main`.** On `:8001`: the CI badge green, the live map with the robot
patrolling, no chores.
observed: `/api/room/ci` → `state`, `watch.clean`, `watch.passes`; `/api/nav/snapshot` → pose,
0.03 m grid, a 5-point path, `frame: "world_z_up"`; `/api/chores` → `[]`; `/`, `/robot`, `/scene` → 200.
fallback: if the map is empty, bbsim is not sweeping — `demo_sim up` again (gitspace-22's window).

**Beat 2 · a roommate makes a mess.** `python scripts/demo_sim.py mess mug_a1b2`.
**Call it "about half a minute"** — the debounce counts whole scan passes, not seconds, so the
number moves: 14.3 s and 12.7 s here, 26 s for web-64's clicked run, 29-174 s across
gitspace-22's five. ⚠ All of those are **bbsim**. On the real robot the map itself is the floor:
old cells live a median 40 s in view and indefinitely out of view (#9), so do not promise a
number on hardware.
observed (01:30Z, sampling `/api/room/ci` every 1.5 s from a reset room):
```
3.2s  pending  mug_a1b2                            badge still GREEN
8.1s  CONFIRMED mug_a1b2 tidy-1                    badge RED, bbsim job -> navigate
11.2s the mug is IN THE ARM'S HAND (gone from /sim/truth)
12.8s the arm has put it back at (0.42, 0.18)
14.3s clean, last_verified_job = tidy-1
```
⚠ **it does not always finish.** Three runs at 01:30Z: one exactly as above; one where the tidy
never started (confirmed, then nothing for 90 s, bbsim still on `patrol`); one where the arm DID
put the mug back but the badge never went green within `check`'s window, which is the only FAIL
in an otherwise green `check`. gitspace-22 measured 5 of 5 green an hour earlier, so it is a
flake, not a break. If it stalls, `demo_sim reset` and go again — and `check`'s stall dump now
prints the job events, the loop verdict, bbsim's job and both poses against `main`.
fallback: narrate pending → confirmed → red and move on; the first half has never failed.

**Beat 3 · "I meant that."** `python scripts/demo_sim.py decide lamp_2d9b` (or the button).
**Works — eight of eight assertions green at 01:30Z**, including the one that matters: `the robot
left lamp_2d9b where it was put (0.62, 0.35)` and ``main` now says the lamp lives there`. Clean
and verified at 33 s; web-64's clicked run took 56 s.
⚠ **the click race** (master): the loop confirms the drift ~10 s in and can tidy it back inside a
minute, so click "I meant that" within about twenty seconds. Later still works, but what you
approve is the few centimetres the tidy left behind rather than the move you made (web-64
approved 5.0 cm of an 18 cm move). Either end state is correct; know which one you are accepting.
⚠ expect the room to churn while this runs (#9).

**The octree layer (Elastic's strongest artefact on the page).** It is OFF by default — the
user's instruction about dense layers hiding the room — so open it by URL rather than hunting a
checkbox in Settings on stage, and PIN the commit:

    http://127.0.0.1:8000/robot?octree=1&commit=1a668ec0a5aa7131cd9593991a693b14deb54a30

Why pinned, in one sentence if a judge asks why the page's commit differs from HEAD: `12dd252`
changed the octree configuration and saw nothing, so `1a668ec0` is the newest commit that
actually looked at the room. Unpinned, the page falls back to the newest INDEXED commit, which
is `a2b27037` — the revert of a live check, on a branch that is not even an ancestor of main.
Pinned, it opens on the commit the other beats already name: blame says the mug moved there, and
time travel resolves to it.
⚠ **Three ways this link fails silently, and one field that catches all of them.**
- a **short** sha: the page tests `commit` against `^[0-9a-f]{40}$` and ignores anything else;
- a valid but **unindexed** sha: an empty octree with no error at all;
- a **misspelled parameter**: the API takes `commit_sha`, and an unknown parameter is ignored, so
  `?commit=…` returns the latest snapshot and looks like a real answer. Both web-64 and I were
  fooled by this one, separately, and each "confirmed" a pin that had not been honoured.

**The tell is `snapshot_source`**: `"requested"` means the pin was honoured, `"latest_indexed"`
means it was not. Check that one field rather than counting cells. Measured, twice, independently:
`1a668ec0…` → requested, **4,970 cells**, and an ancestor of main (master's pin); `a2b2703754…` →
requested, 5,827 cells, but it is the live-check revert and is NOT on main; **HEAD `24c4b447…` →
requested, no cells**. If the panel says "No stored octree cells", the COMMIT is wrong, not the
page. Note what the API can and cannot tell you: it answers what is INDEXED, never what belongs in
the story — `latest_indexed` is the revert, and blame and time travel both name `1a668ec0`.
Measured either side of tonight's change: the indexed room went from 0.94 x 1.50 m of footprint
to 4.00 x 4.00, 6 occupied metre-cubes to 26, 1,978 cells to 5,977. ⚠ Say honestly that the cells
are still 6.25 cm (observed on that commit): the cube is pinned at 3.125 cm now, but every
existing document was written before the flip, so the finer cells appear from the next capture
onwards. There is no re-index and should not be: that commit holds real pipeline output, and the
generator cannot be replayed faithfully, so regenerating it would replace measured data with
synthetic. *(The page's own status line showing that sha is gitspace-68's check in a browser, not
mine — I verified the API and the page's URL handling, not the rendering.)*
⚠ **If a judge asks how big something on the floor is**, say: *the box's position is measured, the
small packet's width is not.* Position is the strong claim — every item's centre lands within about
a centimetre, and the can's width is ground truth (5.3 cm reported for a hand-measured 5.3). But the
smallest packet reads 4.3 / 6.7 / 8.6 cm across three captures as it gets farther away: it is 18-23
px across, against a halo correction that is a fixed number of pixels. Quote presence and position
for that one, not centimetres. Same for its height on a single capture — a flat wrapper's height is
noise until it is seen twice (`height_median_m` is in the JSON for this reason).

**Beat 4 · ask the room** (on `:8000`, the real history):
- "where are my keys" → observed: resolved `keys_7c2e` (`how: elasticsearch`, score 1.454,
  margin 0.45, five candidates), a point job with a target pose and `estimated_s: 40`, 0.7 s.
- **"why was this diff wrong"** → observed: `ok`, `kind: read`, `as: why`. At the terminal it is
  the best line in the demo: `room why` → `cap_0005: quality gate PASSED (skew 2.12 ms, limit 25;
  tilt rate 0.0051 rad/s, limit 0.05)`, the telemetry peaks, `verdict: this commit's picture of
  the room is trustworthy`, and the trace id.
- **"put the room back the way it was 2 hours ago"** → observed: `ok`, `kind: plan`, `as: restore`,
  `ref_resolved: 1a668ec`, `ops: []` — it finds the commit and reports nothing to move. ⚠ Not
  "put **it** back …" (that is #2), and **not "before dinner"** on this history.
  At the terminal, with a time that lands between commits:
  `room restore --before "yesterday 7:15pm" --plan-only` → observed: `before 'yesterday 7:15pm' =
  before 2026-09-18T19:15-04:00: e51a75a initial scan … (found by elasticsearch)`. Drop
  `--plan-only` only on a room you are willing to change, and expect #5.

**Beat 4b · ask for something that is NOT in the room.** **Works** (observed on the 01:20:58Z
build). Say "pick up the trash" →
```
ok: false   no_match
there is nothing in the room that matches 'trash'; the nearest are bowl, plant and cup
```
⚠ one correction to the script: **"tidy up" does NOT refuse** — it is a whole-room command and
comes back `kind: jobs`, `as: tidy`. Use "pick up the trash" for this beat.
Say it like this: judges expect retrieval to FIND things, not to admit it cannot. A vector search
always returns its nearest neighbour, so before this, asking a robot with a gripper to pick up
the trash in a room with no trash pointed it at a ceramic cup, and nothing anywhere reported a
problem. Two honest lines to say beside it, both elastic-09's:
- it is a floor against absurdity, not a correctness proof — "the banana" still resolves to the
  plant, and anything that MOVES an object is confirmed with the person first;
- an unconfident ask is filed as a warning with the three nearest objects, so the misses become a
  standing list of what the room should learn.

**Beat 4c · when it is not sure, it asks.** A vector search has no "not found" — a nearest
neighbour always exists, only a score — so there are three outcomes, not two (d2, measured on this
room: absurd phrases score 1.056-1.101, vague-but-real ones 1.126-1.169): **under 1.11** it refuses
and names the nearest; **1.11-1.20** it ASKS; **1.20 and over** it acts. A too-close pair asks
rather than refusing. On screen: the question, the object it means, the runner-up it does not, why
it is asking, and "Yes, that one" / "No".
Say the safety property out loud, because it is the point: **the question plans and dispatches
nothing.** "Yes" is a whole second request and only that acts; "No" simply never sends it; there
is no timer, so an unanswered question cannot become an action.
Use a vague sentence — **"something to drink from"**. Observed: `kind: confirm`, score **1.169**
(inside the 1.11-1.20 band), asking *"I think you mean the mug on the desk, not the bowl — shall I
point at it?"* with the runner-up named. "where are my keys" scores 1.454 and acts without asking,
so it demonstrates nothing here. The card itself is web-64's Chrome verification; the answer above
is mine.

**Beat 5 · Andrew's part.** Say it in the panel and the robot points. **Works from the panel**
as of the 20:20Z build; the earlier "not_connected" was the job object's build-time fields, since
fixed (d2).
observed: the sentence → an intent (`intent: find`, `object_query: "keys"`, confidence 1.0) → a
resolve (`keys_7c2e`, `how: elasticsearch`, score 1.454, margin 0.45) → a point job →
`executor: "housebot-edge"`, `state: "dispatching"`, `dispatch: {dispatched: true, edge:
"http://127.0.0.1:8780"}`, the answer arriving as the SSE `job` event. "the thing I cut paper
with" resolves `scissors_9f3a` (score 1.266, margin 0.185) and dispatches the same way — a good
second sentence, because it names nothing the index could match literally.
`/api/agent/bridge` names what is connected if a judge asks.

---

## 4. Fallback ladder

| if… | do |
|---|---|
| the tidy loop is still broken | beats 1, 4, 5-first-half only, and say the arm half is simulated and being fixed |
| `:8001` is wedged | beat 4 on `:8000` alone; it needs nothing from the sim |
| Elastic is down | `room why` and the local git beats still work; search, resolve and `--before` do not |
| everything is slow | `df -h` before you debug anything (#8) |
| the room is in a bad state | §5 |

---

## 5. Recovery

```bash
python scripts/demo_sim.py reset          # the sim scene back to `main`, room re-seeded
git -C room.git status -sb                # the REAL room should be untouched: `## main`, clean
```
The sim room is disposable (`demo_sim up --reseed` rebuilds it from the scene). `room.git` is not:
never point a sim process at it.

### Between demos: put the real room back

```bash
./scripts/demo_state.py list              # every saved state, newest first, and where the room is now
./scripts/demo_state.py restore tidy      # the room back, and the job ledger cleared
```

`tidy` is the room as it runs: `main` @ `24c4b44`, clean, 11 objects, the mug on the desk at `x=0.61`.
Restore puts back every branch and tag, which branch `HEAD` was on, and the working tree — the room as
last scanned, dirty or clean, because "the mug is out of place" lives in the tree and not in a commit.

**Clearing the ledger is the reason this is not `git reset --hard`.** A job id is a hash of what the job
would DO (command + target + `HEAD` + the room as scanned), so the second demo of the same sentence
returns the FIRST job, replayed, and the robot does not move. A room reset without its ledger cleared
gives you a demo that works exactly once. `--keep-ledger` if you are demonstrating replay on purpose.

Nothing is lost by restoring: the room as it was is auto-saved first as `before-<name>-<time>`, and every
branch tip is kept at `refs/demo-backup/<time>/<branch>`, so no commit becomes unreachable. Save a state
of your own any time with `./scripts/demo_state.py save <name> -n "what it is"` — take one right before
you go on, so the fallback is the room five minutes ago and not the room this morning.

Elasticsearch is never touched. It is append-only history and the demo READS it; a restored room asking
"what changed since this morning" still has this morning to answer with.

The one state flip a judge can SEE is `first-scan` ↔ `tidy`: `/robot` draws 12 objects at `first-scan`
(the marker and the tool are still on the desk) and 11 at `tidy`. The page reads `HEAD`'s commit, so an
uncommitted change to the tree will NOT show there — if you want the room on screen to change, the state
has to move `HEAD`, which restore does.

---

## 6. What this rehearsal proved, verbatim

- `demo_sim status` → `bbsim up` · `site up` · `watch window up`.
- `:8001/api/health` → `"elastic": {"configured": false …}` — simulated data cannot reach the indices.
- `/api/room/ci` → `state: "dirty"`, one change `mug_a1b2` `delta_m: 0.233`, `last_verified_job: null`.
- watch verdict → `mug_a1b2`, `mess → tidy`, `passes: 65`, `job_id: tidy-4` (was `tidy-3` 30 s earlier).
- bbsim `/health` → `job: patrol` three times, 8 s apart; `/sim/truth` → mug at (0.25, 0.34), unchanged.
- On a copy of the sim room: `room status --no-scan` → `modified: zones/desk/mug_a1b2.yaml (moved 0.23 m)`;
  `room chores` → `no open chores`; `room pr list` → `no open pull requests`; `room log` → `90f6b32`;
  `room why` → `no capture is recorded for this commit`.
- On a copy of `room.git`: `room why` → the `cap_0005` gate, telemetry and verdict above;
  `restore --before dinner` → `fatal: no commit on main before 2026-09-18T18:00-04:00`;
  `--before "yesterday 7:15pm"` → `e51a75a`, found by elasticsearch.
- Panel on `:8000`: keys → ok; why → ok; "put the room back … 2 hours ago" → `ref_resolved: 1a668ec`, 0 ops;
  "put **it** back … before dinner" → `unknown_command` (d2 has since fixed the pronoun; not in the running process at the time of writing).
- `POST /api/object-life/keys_7c2e/point` → `executor: "housebot-edge"`, `dispatched: true`, edge `:8780`;
  the same job from the panel → `executor: "not_connected"`, at 21:46Z and again at 21:55Z.
- `demo_sim check` from a reset room: beat 1 six of six ok; beat 2 ok to "tidy-1 started", then
  `timed out after 450 s waiting for tidy-1 to be verified`; beat 3 unreached. Three later runs were clean.
- Beat 3 sampled: 2-4 phantom `unknown_*` objects at a time for ~30 s around the lamp's old pose,
  new ids every pass, all pending as `lost_and_found`, all gone by 33 s.

## 7. Fixes that would retire a trap

| # | fix | owner |
|---|---|---|
| 1 | the Tier A tidy never reaching `/navigate` | gitspace-22 |
| 2 | a time grammar in the panel (`before <phrase>` → `restore --before`), and a demo phrase inside the room's history | bridge + whoever writes the script |
| 4 | dispatch the point job to his edge over HTTP+SSE, now that his WebSocket is retired | gitspace-d2 |
| 5 | `bin:` in `room.yaml`, and measured arm numbers | master + robot |
| 8 | a disk check in `demo_sim up`: refuse to start under ~1 GB | gitspace-22 |
| 9 | hold a candidate that appears where a committed object just left until a second fresh pass confirms it (and ask whether bbos carves stale cells faster than bbsim) | perception (this session) |
