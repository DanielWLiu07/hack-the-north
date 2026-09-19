# GITIRL: the plan to win (Sat 2026-09-19; re-timed 12:30 EDT)

**Deadline: Sun 2026-09-20 08:00 EDT, 19 h 30 m from 12:30.** Submit by 07:30.
Prizes in play: main prize, **Bracket Bot** (1st: 4× Bambu A1 Mini + 4× SO-101 leader-follower kits),
**Sentry** (guaranteed internship interviews), **Elastic "Find the Signal"** (Quest 3S / Bose QC).

---

## 0. Aligned with Andrew's Housebot Edge (awzheng/gitirl `9582081`, Sat 11:57)

Andrew refocused his edge on a **"roommate/caretaker robot"** (working name Housebot Edge). We adopt
that vibe, and git stays the reason the caretaker is trustworthy: **the caretaker remembers where
everything belongs (`main`), finds what you lost, and keeps the shared room the way everyone
agreed.**

**His chain, which is now ours:**
```
our web/cloud (search, object history, UI)  --POST /v1/jobs (bearer)-->  Housebot Edge (Andrew's laptop, :8780)
  --POINT_AT_OBJECT / MOVE_OBJECT over HTTP-->  Ryan/Sarah RobotAdapter (the robot, :8765)  --BB nav + arm-->
  terminal result back up the same chain
```
- **Our `point` job already matches his parser, field for field** (`job_id`, `command: "point"`,
  `object_id`, `target_pose`, `zone`, `pointing_at` from `POST /api/object-life/{id}/point`). His edge
  is the executor that endpoint has been waiting for (`executor: not_connected`).
- **Missing on our side:** a LAN-side dispatcher that POSTs the job to his `/v1/jobs` and returns the
  terminal result. The laptop web on :8000 is the natural place: `HOUSEBOT_EDGE_URL` +
  `HOUSEBOT_EDGE_TOKEN`. The cloud can't reach a private laptop, and his doc agrees.
- **Missing on the robot:** `POINT_AT_OBJECT` behind Ryan/Sarah's `RobotAdapter`, which is his first
  checklist item.

**The demo ladder, merged with his stop gate** ("do not add features until the point demo works five
times in a row"):
1. **"Where are my keys?"** → Elastic hybrid search → the object's last pose → a `point` job → the
   robot drives over and points. **5 in a row.** (Bracket Bot + Elastic in one beat.)
2. **"The room drifted."** The watch loop confirms a mess → the caretaker goes over, points, says it,
   and files a chore (Tier B) → the room-clean check goes red → green when it's fixed.
3. **One curated move** (`move` job, one confirmed `moved` op) → re-observation verifies it (Tier A).
4. **Intent:** the PR beat (the lamp to the shelf), if 1–3 are solid.

**One transform owner.** His doc gives **world-to-robot transforms to Ryan/Sarah's adapter**; our
refactor plan had `roomctl/frames.py`. Resolution: everything **we** send stays in the room frame
(`world_z_up`, metres), and the conversion to Bracket Bot's frame happens **once, in their adapter**
(docs/30's rule). Our perception still reads BB's voxel map, so it needs the same `T_bb←room`: the
adapter **publishes** the registration (e.g. `GET :8765/registration`) and we consume it, instead
of estimating our own. `frames.py` shrinks to a reader of that, with the golden tests. Two
estimates of one transform would be the bug.

## 1. The reframe

> **GITIRL: git for the people you live with.**
> A robot roommate that keeps your shared space at `main`. It cleans up the mess, not your
> decisions, until your room says `nothing to commit, working tree clean`.

**The insight that makes it not "too easy":** every cleaning robot has the same bug. It can't
tell a mess from a decision. You move the lamp on purpose, and it "tidies" the lamp back. A
change you *meant* goes through a pull request. Everything else is drift, and the roommate
reconciles it back to `main`.

**Why git is the right frame, not a gimmick:**

| git gives you | shared living needs |
|---|---|
| `main`: one agreed state | an agreed meaning of "clean" |
| commit / PR vs uncommitted | decision vs accident, the line a cleaning robot can't draw |
| `log`, `blame` | "who left this here, and when?" without an argument |
| `restore`, `revert` | put it back without erasing what happened |
| branches, merges, conflicts | many people editing one space; two roommates, one lamp |
| CI status | the room-clean heartbeat: green at `main`, red when it drifts |

**The hard problems, said out loud** (lead the pitch with these, not the robot):
1. **Git needs a deterministic working tree, and sensors aren't deterministic.** An untouched
   room must give zero diff. Single-view centres wander ~3 cm and detectors miss things. Our
   first real run was 8/11 rescans dirty; it's 0/11 now (quantize + hysteresis + a 5 cm "did it
   move" rule + two-miss removal).
2. **Identity.** The same mug across scans, occlusions and new wording. "white ceramic cup"
   stays `cup_7e21`. Live: BM25 misses it for "mug", and the Jina vector finds it at #2.
3. **Hidden ≠ gone.** An object you can't see is not a deleted file.
4. **A diff is not a motion plan.** Order dependencies, swaps that need a parking spot, spots
   blocked by things that aren't moving, "where do I stand to reach it", and honest partial
   success.
5. **Two frames that disagree.** Our versioned room frame must stay stable across sessions,
   while the robot's SLAM map can reset (§3.2).

---

## 2. The demo (90 s at the table; the same beats on stage)

**Tier A: the arm works** (target)
1. *(Pre-mapped; robot patrolling.)* "This is our room. Git says `working tree clean`." The
   dashboard's CI badge for the room is green.
2. A "roommate" messes up the desk: moves the mug, leaves scissors out. The robot's patrol
   comes round. `room status` shows `modified: mug (moved 0.19 m)`, `untracked: scissors`.
   **The Sentry room-clean heartbeat goes red.**
3. `room blame mug` shows the camera frame of the moment it moved, with the culprit in it.
4. The roommate tidies: it navigates to the mug, picks it up, places it at `main`'s pose, and
   bins the scissors to lost-and-found. It rescans. **Green.** If it can't do something: "I put
   back 2 of 3."
5. **The beat that proves intent:** open a PR, "move the lamp to the shelf". Approve it on a
   phone, and the robot moves the lamp. Now knock the lamp over: the robot restores it to the
   **shelf** (the new `main`), not the old spot.
6. Close on the Sentry trace and the Elastic query of that same moment (§6).

**Tier B: navigation works, the arm doesn't.** Beats 1–3 as above. At 4, the robot drives to
the object, faces it, and says out loud "Daniel, your mug is 19 cm off `main`", and it files a
chore (an issue) in the dashboard. A person fixes it; the next patrol verifies; green. It's a
nagging roommate, which is funny and honest. Beat 5 works the same with a person moving.

**Tier C: nothing moves reliably.** The robot is a stationary scanner; the rest is identical.
Play the backup video for the motion.

**Rules for every tier:** 4–6 visually distinct, graspable objects; one table; a 2 × 2 m
mapped area; the AprilTag on the table; the map built before judges arrive. Record a backup
video the moment one full loop works.

---

## 3. What Bracket Bot's nav stack gives us (bbapps/nav, on the robot)

`main.py` (:8010) and `nav_api.py` (:8020) run **on the robot**. **We are "the server":** we
connect in, and the robot never connects out. There's no auth, so the robot network only; never
expose 8010/8020 publicly.

| we get | endpoint | we use it for |
|---|---|---|
| live SLAM pose ~8 Hz: `rx, ry, rh`, `ready`, `status`, `map_gen`, route | `ws :8010/ws` | where the robot is; job status; **map resets** |
| live **colour voxel map**, 1.5 cm cells, below 1.5 m, full copy then deltas ~2 Hz | `ws :8010/heavy` | **scene geometry for `git status`** |
| 2D floor grid of the area, 3 cm (1 floor, 2 obstacle, 0 unknown) + **per-0.5 m-block "seconds since seen"** | `GET :8020/map`, `ws :8020/stream` | **where to stand** (costmap) and **hidden vs gone** (freshness) |
| map a rectangle; go to (x, y, heading); patrol the stalest block; stop | `POST :8020/map/rectangle` · `/navigate` · `/patrol` · `/stop` | mapping, **tidy trips**, **the roommate's rounds** |

**Facts that shape the design:**
- It cruises at **~0.14 m/s**, so a 3 × 3 m sweep takes 3–4 minutes. Keep the area 2 × 2 m and
  pre-map it.
- **One job at a time.** `/navigate` ends a patrol, and patrol does **not** resume by itself.
  The executor re-issues `/patrol` after every trip.
- **Arrival tolerance is 0.25 m.** After `reached`, read `GET /pose` and aim the arm from the
  **actual** pose. The base-pose solver keeps a ≥ 0.25 m margin inside the arm's reach.
- **An unreachable target still reports "reached"** (the nearest mapped floor). Always check the
  actual pose.
- **Receivers must keep up.** The robot queues 8 messages; a slow reader gets a fresh full copy
  (`first = 1`, meaning "clear and rebuild"). Store cells in a dict keyed by the integer cell.
- **`map_gen` changes** on a map reset or re-anchor. Drop everything derived from the old map,
  re-register (§3.2), and redefine the rectangle. Never press Start/Stop in the robot's browser
  UI during a demo; watching is fine.

### 3.1 Frames (write this once, in one module, with tests)

| frame | axes | yaw |
|---|---|---|
| **BB world** (SLAM) | x, y on the floor, z up; **at yaw 0 the robot faces +y**, +x to its right | radians, CCW; forward = (−sin h, cos h) |
| **room** (ours, versioned; docs/20) | X forward from the anchor tag, Y left, Z up, floor z = 0 | degrees about +Z from +X |

Both are Z-up, so it's an SE(2) transform plus a height offset: `p_bb = R(θ)·p_room + t`,
`z_bb = z_room + dz`. A heading angle measured from +x is `φ = h + π/2` in BB's convention, so
BB yaw `h = θ + φ_room − π/2`. That one line is what turns a correct plan into the robot facing
the wrong way.

### 3.2 Registration: stable history on a map that can reset
git history must stay in **our** tag frame across sessions. BB's frame is arbitrary and changes
with `map_gen`. So `T_bb←room` is **estimated, not configured**:
- **Primary:** when the robot's camera sees the AprilTag, the tag pose in the camera, the fixed
  camera→base mount, and the live `rx, ry, rh` give the tag in BB world, and so `T`.
- **Fallback:** a two-point teach, driving the robot's nose to two marks on the table edge.
- Re-estimate on every `map_gen` change; refuse to plan while `ready = false` or before `T` is
  known. **Gate-1 measurement:** what BB's z = 0 is (floor?), from floor voxels.

---

## 4. Scene understanding: "what is what", and is it allowed?

**Perception answers "what is where". Git answers "is that allowed?"**

1. **Geometry from the robot's live voxel map** (`/heavy`), transformed into the room frame and
   cropped per zone (`room.yaml`: desk, shelf) above each surface's height.
2. **Objects** = connected clusters of voxels on a surface: centroid, extents, dominant colour.
   At 1.5 cm cells a mug is ~8 cells across, and the centroid of many cells beats the cell size.
3. **Semantics** from camera frames (robot capture): YOLO segmentation locally, projected onto the
   clusters, plus a VLM description.
4. **Identity** against HEAD: position + class + colour, with Elastic hybrid search as the backup
   (re-identification across wording).
5. **Hidden vs gone, from real evidence.** Only report `moved`/`deleted` if the 0.5 m block was
   **freshly seen** (BB `age_s` small) **and** a raycast through the voxel map says the spot was
   in line of sight (`perception/raycast.py`). Otherwise carry the object forward: stale ≠ gone.
6. **Debounce:** a change must persist across **two fresh patrol passes** before it is mess.
7. **Stabilise:** `state.settle` + quantize → one YAML per object → the git working tree. The G2
   gate (zero diff on an untouched room) must hold **across patrol passes**, not just rescans.

**Policy: mess or decision?**
- Differs from `main`, no PR → **mess** → tidy (Tier A) or chore (Tier B).
- Changed by an approved PR → **decision** → becomes `main`.
- **Untracked** object: in a **shared** zone → lost-and-found (the bin) + a chore for its owner;
  in a **personal** zone ("your side of the room") → left alone.
- `.roomignore`: people, the robot, cables, the floor.

---

## 5. Who decides what: robot · laptop · cloud

| layer | decides | where | rule |
|---|---|---|---|
| **Robot** | "is this safe right now?": balance, collisions on the route, stop, refusing a pick while leaning | on the robot (BB nav + bbos) | reflexes in milliseconds; **it can always refuse** |
| **Laptop** (the brain) | "what is where" and "what to do": perception, git, the planner, verification by rescan | local, on the robot's network | **the whole tidy loop works with zero internet** |
| **Cloud** | "what should the room be": PRs and approvals from phones; memory (Elastic), observability (Sentry), the dashboard, language (LLM) | Vercel + GCP + Elastic + Sentry | not in the motion path; approvals are pulled outbound |

**The plan is deterministic:** the same room and the same `main` always give the same
inspectable op list. **No LLM output moves the robot** (docs/31). If the cloud dies, you lose
phone approvals and search; `status` and tidying still work.

---

## 6. The three sponsor stories (only claims we can show)

**Bracket Bot: the roommate's rounds.** SLAM mapping of the room; **patrol** drives to the
stalest block so `git status` is always fresh; **navigate** to each mess with a solved standoff;
the **arm** tidies (Tier A); freshness decides hidden vs gone. We use their stack exactly as
designed ("your server connects to the robot"), and add git on top.

**Sentry: the room has CI.**
- **The room-clean cron is the apartment's CI badge:** red when the room drifts from `main`,
  green when the robot has reconciled it. (It needs the muted `watch-loop` monitor deleted to
  free the plan's one cron seat; that's the user's call.)
- One trace per tidy: laptop `room restore` → nav job spans (`navigate`, with BB's
  `failed: …` reasons as issues) → arm spans → verification rescan. Real traces already span two
  machines, and `robot.pick`/`robot.place` sit inside agent turns.
- **The robot resolves its own issues:** a slipped grasp is filed, retried, and **resolved only
  after the rescan verifies it** (`robot_sentry.py`).
- Debugging stories written as they happened (`SENTRY_STORY.md`), plus every Elastic document
  carries its trace id.
- ⚠ docs/29 says the robot's `server_name` is `pi`; the real value is `robot`. Fix it before
  judging.

**Elastic: the apartment's memory. Find the signal in a messy room.**
- Every commit, observation, voxel snapshot (now from real SLAM) and telemetry sample, all joined
  by commit sha, capture id and Sentry trace id.
- "Where are my scissors?": hybrid BM25 + Jina + RRF + rerank. **The cup the keywords miss,
  the vector finds** (live, reproducible: `elastic/demo_hybrid.py mug`).
- "Who moved it?": blame from observations plus the capture frame.
- "Put it back the way it was before dinner": ES|QL wall-clock → commit. **The query exists; no
  production caller yet.** To build.
- Hidden vs gone from observation history (the web object page, live).
- ⚠ **Drop "Agent Builder" from every pitch.** It is used nowhere. The true line: "an agent whose
  last tool call moves a real object" (agent/tools.py: search → restore → robot).

---

## 7. Work plan (owners · tasks · done-when)

**Gate 1 (now → 14:30): hardware truth. Ryan + Sarah, with the robot session.**
- [ ] Laptop ↔ robot reachable (robot router or tailnet). Today the laptop has **no route** to 10.37.101.235.
- [ ] `main.py` + `nav_api.py` running; `GET :8020/health` shows `main_py.connected: true`; `/ws` shows `ready: true`.
- [ ] `POST /map/rectangle` of the demo area (2 × 2 m, sweep); then one `POST /navigate` that ends `reached`.
- [ ] **One commanded arm motion through bbos** (the arm belongs to bbos daemons; don't open the servo bus ourselves).
- [ ] Measure: BB's z = 0; the camera→base mount; the arm's reach envelope from the base.
- **→ 14:30 decision: Tier A / B / C.**

**Software tracks (start now, in parallel):**

| owner | task | done when |
|---|---|---|
| **master** | `roomctl/bb_nav.py`: `/heavy` voxel mirror, `/ws` pose + `map_gen`, the `:8020` job client, **frames + registration** (§3.1–3.2), unit-tested against recorded fixtures | tests green; drives the robot sim, then the real one |
| **master** | `room watch`: patrol + freshness-aware status + two-pass debounce → the heartbeat | a messy desk turns red within 2 passes; a clean one stays green |
| **master** | executor → BB: base pose from BB's 2D grid → `/navigate` (heading) → actual pose → arm → `/patrol` again; verify by fresh pass | one tidy on the real robot |
| **master** | Elastic showpieces: `room restore --before "…"` (ES\|QL `commit_at`), `room why <commit>` (telemetry window + trace) | both live on real data |
| **perception** (pointcloud) | a BB-voxel source: clusters on zone surfaces, colour, extents → the same records serialize writes; G2 across passes | zero diff over 5 passes of an untouched desk |
| **perception** (segment) | semantics: YOLO + VLM from robot frames projected onto the clusters; raycast occlusion | the right classes on the demo objects |
| **web** | the roommate dashboard: room CI badge, **live map** (BB grid + robot + patrol path), **PRs** (approve → job), **blame card with frame**, **chores** (Tier B) | a judge can follow the demo on a phone |
| **elastic** | index the BB voxel snapshots per commit + observations with freshness; the blame query (who/when/frame) | "who moved the mug" answers with a frame |
| **cloud** | Sentry: the room-clean heartbeat (after the decision), nav job spans + `failed:` issues, one alert rule; keep the Vercel/GCP deploy current | the heartbeat red/green live; one nav failure shows as an issue |
| **Andrew** | natural language → PR ("move the lamp to the shelf") through his parser → the jobs endpoint; his edge may run jobs via BB nav (roomctl stays the fallback) | a sentence becomes a reviewed PR |
| **Daniel** (human) | the demo table, objects, tag, the 90 s script, the Devpost | rehearsed 5× |

**Timeline (EDT):**

| time | milestone |
|---|---|
| 14:30 | **Gate 1 decision: Tier A/B/C** |
| 17:30 | bb_nav + registration + voxel perception working against the real robot |
| 20:30 | **Gate 2: one full loop on the real robot** (patrol → detect → tidy/chore → verify → green). **Record backup video v1 immediately** |
| 20:30–00:00 | PR beat, blame frame, dashboard, sponsor beats; re-record the video after each improvement |
| 00:00 | **feature freeze** |
| 00–03 | Devpost, the three stories, final video; rehearse 5×; sleep in shifts |
| 07:00 | final checks on the demo table |
| **07:30** | **submit** (deadline 08:00) |

---

## 8. Risks and what we do about them

| risk | mitigation |
|---|---|
| The arm never moves through bbos | Tier B (a nagging roommate), still a full story; decided at 14:30, not at 03:00 |
| Laptop ↔ robot link (venue wifi) | the robot's own router or tailnet; everything critical is local; no public exposure of :8010/:8020 |
| SLAM map resets mid-demo | watch `map_gen`, re-register automatically, redefine the rectangle; nobody touches the robot's UI |
| The robot is slow (0.14 m/s) | a 2 × 2 m area, pre-mapped; a table-centric demo; short patrol legs |
| Stale or ghost voxels | freshness gating + raycast + two-pass debounce; G2 across passes |
| "Too easy" | open with the hard problems (§1), show the jitter while status stays clean, the PR beat proves intent |
| Rules (Hack the North: no pre-existing projects) | everything here was written this weekend; bbapps/nav is Bracket Bot's sponsor software, and public libraries are allowed. **Nothing from the July SplatCraft project.** |

---

## 9. Decisions only the user can make
1. **Adopt this plan** and re-task the sessions (master writes a TASK file for each and briefs them).
2. **Delete the muted `watch-loop` Sentry monitor** so `room-clean` can be the room's CI badge.
3. Add `restore` to `WEB_ALLOWED_COMMANDS`; flip `JOBS_REAL_MOTION=1` only when the robot is ready.
4. Put **`https://gitspace-five.vercel.app`** on Devpost (the Tailscale URL is down, D48).
5. **Send Ryan and Sarah to Gate 1 now.**
