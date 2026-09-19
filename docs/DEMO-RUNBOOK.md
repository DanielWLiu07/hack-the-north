# DEMO-RUNBOOK — what to click, in what order, and what to do when it breaks

**Rehearsed 2026-09-19 06:40–07:20Z** by perception (htn:5). Every CLI beat below was run on a
**copy** of `room.git` (`ROOM_GIT_PATH=<copy> ROOM_ES=off ROOM_EVENTS=off`), never the real one.
Every web beat was clicked on `:8000` and on the public URL. Output quoted here was
**observed, not expected**. Anything marked *not rehearsed* was not rehearsed.

[`06-demo.md`](06-demo.md) is the pitch. **Don't run it as written**: its judge-moves-an-object,
`revert HEAD`, voice, `merge` and `push` beats all fail today, and there is no robot, LED or Rerun
screen for the rest (§1). §3 is the version that runs.

---

## 0. What is real on the table right now

| piece | state | evidence |
|---|---|---|
| git layer (`room status/diff/reset/restore/checkout/search/log`) | **real, works** | rehearsed, §3 |
| planner + stances (costmap, DDA line-of-sight) | **real**; the arm numbers are placeholders | `room reset --hard` prints `stand at (-0.16, -0.22) facing 340°` |
| scans | **fake**: `fake/scene_gen.py` reads a scene YAML. **No camera reaches the laptop** | `ROOM_SCANNER=fake:<scene>`; the RealSense wire half is unbuilt (docs/27 §2) |
| robot | **simulated**: `MockRobot` prints `[robot] pick …`. The Pi at `192.168.2.10:8080` does not answer | `curl /healthz` → no answer |
| LED, speech, Rerun screen | **don't exist**: `[robot] led clean` is printed text. `viz/blueprint.py`, `viz/logging.py`, `scripts/demo.sh` and `scripts/snapshot.sh` are not written, and there is no `.rrd` | `ls` |
| voice | **doesn't exist**: `agent/voice.py` is not written. The text agent `agent/loop.py` works | §3 beat 7 |
| Elasticsearch | **live**: 9.6.0 serverless | `/api/health` |
| captures on `/capture/<id>` | **all 15 synthetic** (`vlm_model: fake/scene_gen`). **None has a Sentry link** | `/api/telemetry/sentry/cap_0013` → `"synthetic trace — not recorded in Sentry"` |
| public URL | **up**, stable: `https://daniels-macbook-pro.tailaa0f4f.ts.net:8443` (Tailscale Funnel). The cloudflared quick tunnel is superseded | all pages 200 in 0.4–1.3 s |
| agent panel (docs/31) | **live on :8000**, new contract since the 07:13Z restart: typed input answers 200 with `"ok"`, and a missing state lists the known ones | `restore party-mode` → `ok: false`, `known states: live-check, main, movie-night, study` |

---

## 1. What is broken, ranked by how likely a judge is to hit it

| # | a judge hits it when… | what they see | fix / dodge | owner |
|---|---|---|---|---|
| **1** | you follow docs/06 and **hand them an object** | the terminal reports whatever the scene YAML says, **not what they moved**. If they move the lamp, the screen says the cup moved. That is fatal to trust | **Never hand a judge an object on a fake scanner.** Move it yourself, to match the scene (§3 beat 2), and say the scan is simulated | fake/ + robot/ (a live scan path) |
| **2** | anything involves "the robot" | printed `[robot] …` lines; no motion, no LED, no voice | Say it up front (§3 beat 0). docs/06 fallback rungs 4/5 are where we actually are | robot/ |
| **3** | you type docs/06's `room revert HEAD` after the change | `fatal: the room has uncommitted changes, and revert would destroy them — … room reset --hard …` (exit 128) | **`room reset --hard`** puts the room back to HEAD. `revert` undoes the last *commit*, which is a different operation (docs/31 §1) | docs/06 |
| **4** | the put-back involves the **mug, keys, scissors or glasses case**, or anything added or removed | `cannot apply hunk: nowhere to stand to pick up 'mug_a1b2' … 166 base fits, 3 ik, 11 path`, `nowhere to put … (no bin in room.yaml)`, `object 'scissors_9f3a' not present in room`, then `I put back 0 of 4. Human intervention required` | Use **`docs/demo-scenes/cup_nudged.yaml`**: one move, both poses reachable, **green end to end** (§3). Or pass `--no-route`, which skips stances: 1 of 4 | see §8 |
| **5** | Sentry or Elastic judges click into **`/capture/<id>`** (the prize page) | every capture is synthetic; there is **no "Open in Sentry"** on any of them | Say "synthetic capture" out loud. For a real trace, use the agent's (§3 beat 7) | perception + fake |
| **6** | you run `room status` again after the robot acted, on any scene other than cup_nudged | the room is **dirty again**: the rescan reads the static scene file, so the robot's work evaporates | In §3 it's safe (`messy_bench` == HEAD). Elsewhere use `room status --no-scan` after an apply | fake/ |
| **7** | you **say** "put the desk back the way it was before dinner" | no voice at all. Typed to `agent/loop.py`, it runs 27 s, picks `a83a257` (movie-night, another branch), and `git revert` conflicts: "nothing was moved" | Don't use this line. Use the read-only keys question (§3 beat 7), or `restore study` on the dev page | agent/ |
| **8** | the **merge conflict** beat: `room merge movie-night` | `room merge changes the physical room and isn't wired to the executor yet` (exit 2) | Show the conflict read-only: `/` Conflict section or the dev page's merge preview (§3 beat 6) | roomctl/ |
| **9** | anyone looks at the **graph** or asks the agent a question | a leftover **`live-check`** branch (`174302b`, `a2b2703` "Revert live check…"). It's pushed, and **GitHub's default branch is `live-check`** (`origin/HEAD -> origin/live-check`). Its commits are also in ES, so the agent answers "keys … last commit **a2b2703**", a commit that isn't on `main` | Before judging, whoever made it deletes the branch locally and on GitHub, resets GitHub's default branch to `main`, and deletes its ES docs. **Not done here**: it isn't ours | master |
| **10** | a judge types an unknown state into the agent panel | `did not work: not_found` plus `known states: live-check, main, movie-night, study`. It works, but it **advertises `live-check`** (#9) | fixed by the #9 cleanup. (Before the 07:13Z restart this was a bare 404 with no list) | master |
| 11 | first click on `/replay/<id>` | 1.3–2.7 s blank before the chart | click it once during pre-flight to warm it | web/ |
| 12 | "robot, point at them" (`/object/keys_7c2e` → point) | `202 … executor: not_connected` | honest; say it | roomctl/ |
| 13 | the `room push` flourish | `invalid choice: 'push'` | skip it; the GitHub repo lands on `live-check` anyway (#9) | roomctl/ |
| 14 | someone reads the **dev page's stances** | `restore study` puts the robot at `(0.36, 0.22)` to pick up the mug, **inside the desk**. The CLI says that mug is unreachable. The page's warning explains it: HEAD's indexed `room-voxels` (745) hold **0 obstacle cells** (they predate the pedestals), so its stances are **not collision-checked** | don't narrate the dev page's stances; narrate the CLI's (beat 4) | fake/ + master: re-index HEAD's voxels with the pedestal generation |

Traps only the **operator** hits (a judge never sees them, but each one silently breaks the run):

- **T1.** `room` isn't a command. You need `alias room="$PWD/.venv/bin/python -m roomctl"` from the repo root.
- **T2.** Without `ROOM_SCANNER` set, every apply verb fails: `fatal: room reset moves objects and must verify by rescanning — pass --scene or set ROOM_SCANNER`.
- **T3.** With `ROOM_ES` on, each fake scan bulk-indexes and waits on refresh: **~15 s per `room status`** (measured 14.7 s, versus 0.3 s with `ROOM_ES=off`).
- **T4. Fixed by master (D45).** Rehearsing on a scratch repo with `ROOM_ES` on used to write **live** captures with IDs that collide with the real room's next ones. It happened once during this rehearsal (`cap_0015`, 43 docs, which I deleted). `FakeRoom.flush` now asks `publish.is_the_room`. `ROOM_ES=off` for rehearsals is still the habit.
- **T5.** The dev page Stage writes to the real `room.git` (index plus worktree). If you leave the page without **Abort** or **Commit**, `room.git` stays mid-revert/merge and the next `room status` shows staged changes.
- **T6.** The dev page Commit makes a **real commit** that no robot executes and ES never sees (publish pending). The table and HEAD then disagree until you recover (§6).
- **T7.** The dev page only exists on this laptop: `127.0.0.1:8124` (serve.py) plus `perception/devgraph.py` on `127.0.0.1:8125`. A judge's phone can't open it.

---

## 2. Pre-flight — T−15 min, in this order

```bash
cd ~/Dev/projects/2026/gitspace
alias room="$PWD/.venv/bin/python -m roomctl"                  # T1
export ROOM_SCANNER=fake:messy_bench ROOM_ROBOT=mock ROOM_ES=off # T2, T3 (the web pages don't need the CLI's scans)
```

1. **Room state.** `git -C room.git status -sb && git -C room.git log --oneline -1` → `## main`, `1a668ec afternoon: …`, no other lines. If not, go to §6.
2. **Back it up:** `rm -rf /tmp/room.git.bak && cp -R room.git /tmp/room.git.bak`.
3. **Web:** `curl -s localhost:8000/api/health` → `"reachable":true`, then
   `curl -s localhost:8000/api/agent/bridge` → `"will_serve":"andrew:jsonl"`. (If you restart it: `cd web && python3 server.py`.)
4. **Public URL:** open `https://daniels-macbook-pro.tailaa0f4f.ts.net:8443/` on a phone **off the laptop's wifi**.
5. **Warm the pages** (#11): `/capture` (redirects to the newest, `cap_0013`), `/replay/cap_0013`, `/object/keys_7c2e`.
6. **Dev page** (optional beat): `python3 perception/devgraph.py &`, then open `http://127.0.0.1:8124/dev-graph.html`. The status chips must say `room.git`, `web`, `ES` and `agent bridge: andrew:jsonl`, not `PENDING`.
7. **Dry run:** `room status` → `nothing to commit, working tree clean`. If it's dirty, the scene doesn't match HEAD: check the step-1 output and `echo $ROOM_SCANNER`.
8. Terminal font large, and one browser tab each for `/`, `/capture/cap_0013`, the dev page and the Sentry trace from step 9.
9. **Pre-make the Sentry trace** (beat 7 is 11 s live, so have one ready):
   `rm -rf /tmp/room.git.agent && cp -R room.git /tmp/room.git.agent`, then
   `.venv/bin/python agent/loop.py --repo /tmp/room.git.agent "Where did I leave my keys?"` → the last line is `trace: https://na-alh.sentry.io/performance/trace/…`. Open it in a tab.
   Use a throwaway copy, never the real room or the §2 backup: the agent has a `room_revert` tool and the model decides when to call it. *(The trace URL was printed in rehearsal; I didn't open it in Sentry.)*

---

## 3. The run — ~3 minutes, the sequence that works today

Every beat has what to **do**, what you'll **see** (observed), and a **fallback** if it fails.

**0 · The hook (0:00).** Say docs/06's line, then add: *"The robot and the cameras aren't live on
this table, so the scans and the robot's moves are simulated. The git, the planner and the search
are real."* Say it before a judge asks, because #1, #2 and #5 are all things a judge would find.

**1 · `room status` (0:15)**
see: `On branch main` / `nothing to commit, working tree clean` (0.3 s).
fallback: if it's dirty, the scanner doesn't match HEAD. Run `room status --no-scan`, which shows the tree as last scanned.

**2 · The change (0:25).** Slide the **cup** toward the desk's front-left corner **yourself**, then:
`room status --scene docs/demo-scenes/cup_nudged.yaml`
see: `modified:   zones/desk/cup_7e21.yaml   (moved 0.16 m)`.
⚠ **Magic:** the scene file *is* the change. Don't invite a judge to move anything (#1).

**3 · `room diff` (0:45)**
see: a real unified diff, `-  x: 0.30` / `+  x: 0.22`, `-  y: -0.22` / `+  y: -0.36`. Land docs/06's line: *"That's `git diff`."*

**4 · Put it back (1:00):** `room reset --hard --scene docs/demo-scenes/cup_nudged.yaml`
see (0.7 s):
```
plan: 1 operation, dependency-ordered
  1. move    cup_7e21   desk (0.22, -0.36, 0.74) yaw 0  ->  desk (0.30, -0.22, 0.74) yaw 0
                        stand at (-0.16, -0.22) facing 340°, then (-0.17, -0.14) facing 350°
  [robot] drive / pick / drive / place …
verify: rescanned — the room matches HEAD.
  [robot] say    "Done. The room matches HEAD."
```
Narrate the stance line: *"it chose where to stand from the costmap: the table's pedestal
blocks the base, its top doesn't."*
⚠ **Not `room revert HEAD`** (#3). ⚠ **Not the mug** (#4).
fallback: if it prints `nowhere to stand`, add `--no-route` and say that stance solving is off.

**5 · `room status` (1:20)** → clean. (`messy_bench` == HEAD, so this rescan is honest. See #6 for when it isn't.)

**6 · The conflict, read-only (1:30).** Open `/`, go to the **Conflict** section (merge-preview
`movie-night`): 1 conflict (`mug_a1b2`, moved in both branches) and 6 clean.
⚠ Not `room merge` (#8). Say *"resolving it with the robot isn't wired yet."*
alt: on the dev page, click the `movie-night` node → **Merge movie-night** (a preview, nothing written) →
the `mug_a1b2` conflict plus 4 clean ops (bowl added, lamp and speaker moved, notebook removed).
**Stage stays disabled** while there's a conflict (by design), so there's nothing to abort. *(The `/`
Conflict section's data, `/api/merge-preview`, was verified; its UI was not clicked in rehearsal.)*

**7 · "Where did I leave my keys?" (1:50)**
`room search "where did I leave my keys"` (4 s) →
`keys_7c2e — last seen zones/shelf, 00:37, commit 1a668ec (now)` and `"a set of house keys on a red lanyard"`.
Other hits carry `[vector only — keyword search would have missed it]`: point at that badge, it's the Elastic story.
Then switch to the **pre-made Sentry trace tab** (§2 step 9): the agent's turn, the gen_ai spans and the `es.search`.
fallback: if search errors, ES is down. Use the `/` Search panel on the public URL (same query, `bm25 + vector`, rerank 1).
⚠ Don't click "drive there and point" expecting motion (#12).

**8 · The prize page (2:15).** Public URL `/capture` → `cap_0013`: the three per-camera
descriptions disagreeing, the quality gate (`skew_ms 2.99` / `tilt_rate_max 0.0095` / `coverage 0.838`: PASS),
then **Replay** at 50 Hz.
⚠ Say **"this capture is synthetic"** before a judge reads the label, and don't look for "Open in Sentry" (#5).

**9 · Close (2:40).** docs/06's line. Skip `room push` (#13).

**Optional 6-minute extra: the dev graph page** (local only, T7). Click node `b3691ea (study)`,
then **Preview restore**. You get the ghosted top-down diff and the op list with `git_equivalent` and
a `base_pose` for each op. ⚠ Those stances carry the page's **"0 obstacle cells … NOT
collision-checked"** warning and contradict the CLI (#14): point at the diff, not the stances.
**Stage (step 1 of 2)** arms after 600 ms. Then **Abort**, not **Commit (step 2 of 2)** (T6). In the agent box:
- `set my room back to study mode` → `served by: andrew:jsonl · path: middleware` → `plan: restore study → 3 op(s), applied: no`.
- `revert HEAD` → `served by: gitspace · path: graph`: graph-native, never sent to Andrew.

---

## 4. Fallback ladder, mapped to what exists

| if… | do |
|---|---|
| the terminal is wedged | everything in beats 1–7 has a web twin on the public URL: `/` Status, Search and Conflict, plus `/capture` and `/replay` |
| `:8000` is down | `cd web && python3 server.py` (binds `WEB_BIND=0.0.0.0:8000`). The Tailscale Funnel URL survives a restart. The CLI beats don't need the web |
| ES is down | CLI beats 1–5 work with `ROOM_ES=off`. Search (7), captures (8) and the graph enrichment die: say so, and show the pre-made Sentry trace |
| the laptop's wifi is down | the public URL dies. Run everything locally at `http://localhost:8000` |
| `room.git` is in a bad state | §6, 20 s |

---

## 5. Magic-order index (every hidden precondition, in one place)

1. You must be in the repo root with the `room` alias set (T1).
2. `ROOM_SCANNER` must be exported, or every apply verb fatals (T2).
3. `ROOM_ES=off`, or every scan costs ~15 s (T3).
4. The "change" beat's `--scene` must be `cup_nudged.yaml`. Any stock scene makes the put-back fail (#4).
5. Put back with `reset --hard`, never `revert HEAD`, while the tree is dirty (#3).
6. Only `room status` after an apply is honest when the base scene equals HEAD (#6).
7. `room.git` must be on `main` at `1a668ec` with nothing staged. The dev page must be Aborted or Committed before you go back to the terminal (T5, T6).
8. If `:8000` was restarted, re-warm the pages (§2 step 5).
9. The Sentry trace must be pre-made; no capture page has one (#5).
10. The dev page needs `devgraph.py` running and a browser **on this laptop** (T7).

---

## 6. Recovery — put `room.git` back (20 s)

```bash
for op in merge revert cherry-pick; do git -C room.git $op --abort 2>/dev/null; done
rm -f room.git/.git/gitspace/devgraph-staged.json
git -C room.git checkout -qf main && git -C room.git reset -q --hard 1a668ec && git -C room.git clean -fdq -- zones
room status   # → clean
```
If that fails: `rm -rf room.git && cp -R /tmp/room.git.bak room.git` (the backup from §2 step 2).

---

## 7. What rehearsal proved, verbatim

- `room revert HEAD` on a dirty tree → `fatal: the room has uncommitted changes, and revert would destroy them` (exit 128).
- `room reset --hard` after `bench_with_hammer` → 0 of 4 put back. With `--no-route`: 1 of 4.
- `room restore study` → makes commit `Restore study`, then 0 of 3 put back (mug: nowhere to stand; marker: not present; scissors: no bin). The room is left dirty **with a new commit on main**. Never run this on the real room.
- Stances over HEAD's 11 objects: **7 reachable**. Unreachable: `mug_a1b2`, `keys_7c2e`, `scissors_9f3a`, `glasses_case_d04f` (`base fits` rejects 165–173 of 180 candidates).
- `room merge movie-night` → exit 2, not wired. `room checkout movie-night --plan-only` → 1 op routable (the lamp), 6 unapplied hunks.
- `agent/loop.py "Put the desk back the way it was before dinner."` → reverts `a83a257`, conflict, nothing moved, 27 s.
- `agent/loop.py "Where did I leave my keys?"` → correct object, a real Sentry trace, 11 s, but it cites `a2b2703` (`live-check`, #9).
- `room search "where did I leave my keys"` → `keys_7c2e` first, 4 s.
- `docs/demo-scenes/cup_nudged.yaml`: `status` → `diff` → `reset --hard` → `the room matches HEAD` → `status` clean. **Green.**

---

## 8. Fixes that would retire a trap (none made here, and each is someone else's file)

| trap | fix | owner | size |
|---|---|---|---|
| #4 | measured arm numbers (`r_max 0.48` is a placeholder, `roomctl/executor.py` ArmModel), a `bin:` in `room.yaml`, or hero objects within ~0.30 m of the desk's front edge (0.48 reach − 0.18 inflation past the pedestal inset) | robot/ + master | S |
| #1, #6 | move `docs/demo-scenes/cup_nudged.yaml` into `fake/scenes/`; make the fake scanner remember the mock robot's moves between commands | fake/ | S |
| #3 | docs/06 beat [1:20]: `room reset --hard`, not `room revert HEAD` | docs/06 | XS |
| #8 | wire `room merge` to the executor | roomctl/ | M |
| #9 | delete `live-check` (local, GitHub, ES) and reset GitHub's default branch | master | XS |
| #14 | re-index HEAD's `room-voxels` from the pedestal-generation cloud (`stage_fake_voxels` + publish) so the dev page's costmap has obstacles | fake/ + master | XS |
| #5 | index one real capture through `perception/pipeline.scan_into` with a live DSN, so `/capture/<id>` has a real Sentry link | perception | S (needs a recording and ES on) |
