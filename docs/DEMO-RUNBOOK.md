# DEMO-RUNBOOK — what to click, in what order, and what to do when it breaks

**Re-rehearsed 2026-09-19 21:35–21:50Z** by perception (htn:5) against the roommate MVP
([`../plan/roommate/tasks/MVP-NOW.md`](../plan/roommate/tasks/MVP-NOW.md)), with the robot off
the network since ~18:00Z. Method, unchanged: CLI beats on a **copy** of the room
(`ROOM_ES=off`), web beats read-only against the running sites, and **every line quoted below was
observed**. Anything marked **NOT REHEARSED** was not.

All five beats ran. Beats 2 and 3 are from my own reset runs at 22:05-22:15Z; where web-64's
clicked numbers differ, both are quoted as single samples.

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
| the robot | **offline since ~18:00Z** | everything above is the simulator |

---

## 1. What is broken, ranked by how likely a judge is to hit it

| # | a judge hits it when… | what they see | who |
|---|---|---|---|
| **1** | **beat 2 stalls, about one run in four** | the badge goes red and stays red: `tidy-N` minted, `last_verified_job` null, bbsim never leaving `patrol`. Seen twice — `tidy-3`/`tidy-4` at 21:39Z (the disk was full), and `check` timing out after **450 s** at 22:00Z with 17 GB free, so the disk is not the whole story. It then ran clean three times in a row. **If it stalls, `demo_sim reset` and go again** | gitspace-22 |
| **2** | **beat 4's middle sentence, as scripted** | "put **it** back the way it was before dinner" → `ok: false`, `unknown_command`: the pronoun is the problem (web-64 found the gap between two bridge rules — one takes "back" without "it", the other "it" without "back"). "put **the room** back …" parses. Then it fails for a second, honest reason: "before dinner" means **yesterday** 18:00 (today's hasn't happened at 17:42 local) and `room.git` starts at 23:02Z, so `no commit on main before 2026-09-18T18:00-04:00`. **Decision (master): do not demo "before dinner" — say "2 hours ago"**, which answers with the commit. d2 has the one-line regex fix for the pronoun | bridge |
| **3** | beat 3 | never reached while #1 stands | gitspace-22 |
| **4** | beat 5, until `:8000` is restarted | the panel's answer contradicted itself: the job dispatched, but the job OBJECT inside the answer still carried its build-time `executor: "not_connected"`. d2 fixed it (a dispatched job now says `housebot-edge` / `dispatching`, and one that was not sent says why), but the fix is not in the running process. **Look at the trace, not just the job fields, until it is restarted** | gitspace-d2 |
| **5** | any restore that actually plans | the plan resolves, then the old executor limits bite: `nowhere to put 'marker_c3d4' (no bin in room.yaml)`, `nowhere to stand to pick up 'mug_a1b2' … 167 base fits, 1 ik, 12 path`. Unchanged since this morning: `room.yaml` has no `bin`, and the arm numbers are placeholders | master (room.yaml) + robot |
| 6 | `room why` on the **sim** room | `! no capture is recorded for this commit … verdict: don't trust this commit's picture` — correct (the sim indexes nothing) but it reads as a failure. Ask it on `:8000`, where it is rich | — |
| 7 | the CI heartbeat while dirty | `heartbeat: {"last": "error"}` — that IS the badge working, but "error" reads as broken | — |
| **9** | **any beat, right after something moves** | the room churns with **phantom objects**: after a move, bbsim's map keeps the object's cells at its OLD pose until the robot looks there again, so there is one blob more than there are records. Beat 3 sampled: 2-4 phantoms at a time for ~30 s, clustered around the lamp's old (0.85, 0.35), each pass minting a FRESH id (`unknown_0be8`, `unknown_f579`, `unknown_3f06`…), each pending as `lost_and_found`. Usually they clear in a second or two (beat 2: one phantom at 0.2 s, gone by 1.8 s). Occasionally one survives two fresh passes and is CONFIRMED under a nearby record's name — web-64 saw `glasses_case_d04f:tidy-1` from a `mess mug_a1b2`, 16 cm away, un-confirmed 2 s later. It is transient and self-clearing, but it can mint a chore or a tidy for something nobody touched | perception (this session) + bbsim carving |
| 8 | nothing visible | **a full disk shows up as a 6–20× slowdown, not an error**: my test file 4m43s vs 18s, the suite 13m vs 61s, perception-02's test_pipeline 181s vs 30s for four files. It also crashed the watch loop once (`Errno 28` writing `misses.tmp`). Cleared at ~22:00Z (17 GB free); the lesson stands. **`df -h` first** | everyone |

Operator traps (a judge never sees these; each one silently breaks the run):

- **T1.** `room` is not a command: `alias room="$PWD/.venv/bin/python -m roomctl"` from the repo root.
- **T2.** `demo_sim check` does **not** reset at the end — it leaves the lamp moved and approved on `main` (beat 3's end state). Run `demo_sim reset` before a clean run. A good run is ~3–6 min (gitspace-22).
- **T3.** The sim room is `~/.cache/gitspace/rooms/sim-demo`, **never** `room.git`; its site is `:8001`, and `:8000` is the real room. Rehearse on a copy of whichever one you mean.
- **T4.** `room --help`'s prose still lists only the old verbs; `watch`, `chores`, `pr` and `why` appear in the usage line above it.
- **T5.** Driving beats 2/3 changes shared state. Say so in the team channel first — web-64 may be clicking the same beats.
- **T6.** Chrome, not Safari, for every page ([`MVP-NOW`](../plan/roommate/tasks/MVP-NOW.md)).

---

## 2. Pre-flight

```bash
cd ~/Dev/projects/2026/gitspace
alias room="$PWD/.venv/bin/python -m roomctl"        # T1
df -h /                                              # #8: anything under a GB, stop and clear
python scripts/demo_sim.py status                    # bbsim up · site up · watch window up
python scripts/demo_sim.py reset                     # T2: known start state
# and if web-64's `job` event fix is not live yet, :8001 needs a restart to show the tidy in flight
```

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
**Works. Call it "about half a minute"** — the debounce counts whole scan passes, not seconds, so
the number moves. Two samples, ±2 s: mine 12.7 s, web-64's clicked run 26 s.
observed (mine, sampling `/api/room/ci` every 1.5 s from a reset room):
```
0.2s  pending  mug_a1b2 (+ a phantom, see #9)      badge still GREEN
6.5s  CONFIRMED mug_a1b2 tidy-1                    badge RED, bbsim job -> navigate
9.6s  the mug is IN THE ARM'S HAND (gone from /sim/truth)
12.7s clean, last_verified_job = tidy-1, mug back at (0.42, 0.18)
```
⚠ one run in four stalled: `check` sat 450 s at "tidy-1 verified" with bbsim never leaving
`patrol` (22:00Z, after the disk was cleared). If it stalls, `demo_sim reset` and go again.
fallback: narrate pending → confirmed → red and move on; the first half never failed.

**Beat 3 · "I meant that."** `python scripts/demo_sim.py decide lamp_2d9b` (or the button).
**Works.** observed: `lamp_2d9b moved to (0.62, 0.35); waiting for the room to SEE it there…` then
`PR #1 opened as seen and approved (1e39a9f). main now has lamp_2d9b there; the robot leaves it
alone.` Clean and verified at 33 s; web-64's clicked run took 56 s.
⚠ **the click race** (master): the loop confirms the drift ~10 s in and can tidy it back inside a
minute, so click "I meant that" within about twenty seconds. Later still works, but what you
approve is the few centimetres the tidy left behind rather than the move you made (web-64
approved 5.0 cm of an 18 cm move). Either end state is correct; know which one you are accepting.
⚠ expect the room to churn while this runs (#9).

**The octree layer (Elastic's strongest artefact on the page).** It is OFF by default — the
user's instruction about dense layers hiding the room — so open it by URL rather than hunting a
checkbox in Settings on stage: `http://127.0.0.1:8000/robot?octree=1` (same on the public host).
Measured either side of tonight's change: the indexed room went from 0.94 x 1.50 m of footprint
to 4.00 x 4.00, 6 occupied metre-cubes to 26, 1,978 cells to 5,977. ⚠ Say honestly that the cells
are still 6.25 cm: the cube is pinned at 3.125 cm now, but every existing document was written
before the flip, so the finer cells appear from the next capture onwards.
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

**Beat 5 · Andrew's part.** Say it in the panel and the robot points.
Observed: the sentence → an intent (`intent: find`, `object_query: "keys"`, confidence 1.0) → a
resolve (`keys_7c2e`, `how: elasticsearch`, score 1.454, margin 0.45) → a point job →
`executor: "housebot-edge"`, `dispatched: true`, edge `:8780`, and the answer arrives as the SSE
`job` event. ⚠ Until `:8000` is restarted with d2's fix, the job fields inside the panel's answer
still read `not_connected` even though it dispatched (#4) — read the trace, or use the object
page's point action, which has always said the truth. `/api/agent/bridge` names what is
connected if a judge asks.

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
