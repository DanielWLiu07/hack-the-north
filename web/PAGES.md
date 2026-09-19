# Web — what pages we actually need

Seven surfaces. **Two of them are routes; the rest are sections of one scrolling page.**

> **As built this has grown: five page routes beyond `/`** — `/capture/<id>`, `/object/<id>`,
> `/telemetry`, `/replay/<id>`, `/robot` — and the dashboard is **no longer on `/`**: `/` is the locked
> 3D hero, and the three sections (`#status`, `#search`, `#history`, not the six below) live at
> **`/?info`**. Link to `/?info#history`; `/#history` lands on the hero and goes nowhere.
> The full inventory is [at the end](#as-built--every-route-webserverpy-serves).

A judge gets three minutes and will not learn a navigation. So the default is one page —
except where a URL is genuinely the product (something you can *send* someone).

---

## Routes (deep-linkable, because sharing them is the point)

### `/` — the hero, then the dashboard, on one scroll
The GITRL scene up top, then the live room state, search, history and analytics as sections
below it. The scene's animation loop **stops** when the hero scrolls out
([`LANDING-TASK.md`](LANDING-TASK.md) rule 2) — the styled pass must never sit behind body copy.

### `/capture/<capture_id>` — ★ THE PAGE THAT WINS BOTH PRIZES
This is the one to build carefully. It answers *"why was this diff wrong?"* on a single screen:

| block | content |
|---|---|
| the three camera views | with each one's own VLM description underneath — **visibly disagreeing** |
| per-camera disagreement | cam0 `x=0.421` · cam1 `x=0.418` · cam2 `x=0.467` → **49 mm spread, quantum is 10 mm** |
| the quality gate | `skew_ms` · `tilt_rate_max` · `coverage` → PASS / **REJECT**, with the failing value called out |
| telemetry, ±2 s around the shutter | `tilt_rate` and `odom_residual` as a strip chart, the spike marked |
| → **Open in Sentry** | a real link built from `sentry_trace_id` on the document |
| what the diff concluded | and, if it was wrong, *why* |

Both briefs are satisfied by this page and nothing else on the site does it:
**Elastic** — messy contradictory sensor data turned into something actionable.
**Sentry** — observability that changed a physical outcome, with the trace one click away.

Reachable from any anomaly: a failed op, a rejected capture, a Sentry issue.

### `/object/<object_id>` — the full life of one thing
Every appearance, all per-camera descriptions, `first_seen` / `last_seen` / `appearances`,
zone history, and the occlusion verdict if it is currently absent. This is where *"find the
hammer"* lands when the hammer is gone — the answer includes a **"drive there and point"**
button.

---

## Sections of `/`

### Status
Branch, HEAD sha, clean/dirty, and the changed objects. Mirrors the LED. Live over SSE.

**As built (`landing/dash.js`)** — the section is headed *Is the room at main?* and leads with the **room-clean CI badge**:
green `passing — nothing to commit, working tree clean`, red `failing — N things have drifted from main` with the drift
list in git-status words (`modified:` / `untracked:` / `deleted:`). It reads `GET /api/room/ci` when mounted, else
`GET /api/status`, and refreshes on the SSE `status`, `room_state` and `capture` events. One `EventSource` per page
(`window.gitrlEvents`).

### Search — the most persuasive panel on the site
The hybrid query box. It must render **`matched_by: {bm25, vector, rerank_position}`** as a
visible badge, because a result the vector leg found and BM25 missed is the single best
evidence that hybrid search is doing real work. Do not bury that in a tooltip.

**As built** — headed *Where did I leave it?*. Every result card has **[Point at it]** (armed after 600 ms, deaf to
double-clicks; disabled with the reason when the object is absent or has no pose): `POST /api/object-life/{id}/point`,
then the job's state live from the SSE `job` events (and `GET /api/jobs/{id}` for a stored job). With no executor it
says *planned; nothing moved*. For a visual of the roommate the page dispatches `gitrl:point`, `gitrl:job` and
`gitrl:room-state` on `window` (room frame) and keeps an empty `#roommate-stage` under the search box.

### History
Commit graph with branches, and a scrubber. `git log --graph` for a room, in a browser.

**As built (`landing/graph.js` + `landing/roommap.js`, at `/?info#history`)** — the graph is a control surface, and
the control is visible:
- **The room, from above** (`roommap.js`, SVG, to scale, from `GET /api/state`): zones, every object's footprint
  and yaw. Solid = where it is at HEAD; dashed = where it would be; an arrow = the arm carries it; × / + = taken
  away / put back; the accent = an object the command will NOT touch. `revert` and `restore` look different at
  a glance — one mark, or three. Hovering a commit ghosts what going back to it would change; hovering a plan
  row lights its object and the other way round. The map sticks while the plan scrolls beside it.
- **Scrub time**: a range over the first-parent trunk redraws the room as it was at each commit (states are
  prefetched after first paint, so it answers in ~100 ms). Nothing physical moves.
- **Selecting a commit sends its first command at once** — as TEXT, to `POST /api/agent/command` (docs/31; a plan
  is a read). Chips: `restore <sha>` · `revert <sha>` · `checkout <branch>` · `cherry-pick <sha>`, over an
  editable `room>` line.
- **The route, as a rail of five stations** lit in the order the command reached them: this graph → router →
  **Andrew · gitirl-agent** (his parser's intent and `served_by`; a stub is shown as a stub; on a graph verb the
  station reads *bypassed — never sent to him*) → planner (git reads) → executor (*not connected: nothing
  moves*). The station a failed trip ended at takes the accent.
- **Step two is separate**: an armed button (600 ms, ignores double-clicks) posts the verb to `POST /api/command`
  with the preview's `base_sha`; a repeat is the same job (`replayed`), a verb off `WEB_ALLOWED_COMMANDS` says so.
- Narrow screens: the room, then the commits, then the preview.

### Analytics
Named ES|QL only, no arbitrary queries from the browser: zone volatility, the dirtiness
histogram (**the step change at the second the judge touched the mug**), most-moved object,
objects that have never moved, telemetry correlation.

### Conflict resolution
Appears only when there is one. Both candidate positions, `--ours` / `--theirs`, and the robot
executes the choice. Physical merge conflict, resolved in a browser.

### Telemetry strip
A thin always-visible live scope: `pitch`, `tilt_rate`, `balanced`. 2 Hz over SSE, decimated
([`../docs/23-telemetry.md`](../docs/23-telemetry.md)). It is what makes the page feel connected
to a real machine rather than to a database.

---

## Deliberately NOT building
- a login or multi-user anything
- a settings page — `.env` and query params are the settings
- a separate mobile site — one responsive page
- a 3D view anywhere but the hero. **Rerun owns 3D.** Duplicating it costs frames and buys nothing.
  *(As built there is a second one: `/telemetry` runs a three.js scene, served from
  `GET /pages/seer/{path}`. The rule that still holds: no room / point-cloud 3D outside Rerun.)*

---

## Build order
1. `/` status + search *(search is the prize panel)*
2. **`/capture/<id>`** *(the winning page — do this before history or analytics)*
3. analytics + the telemetry strip *(both fill the 30-second silence while the arm works)*
4. `/object/<id>`
5. history
6. conflict *(only if the merge beat survives to T3)*

Note the ordering: `/capture/<id>` comes **second**, before the easier panels. It is the page a
judge should leave remembering, so it should not be the one that ran out of time.

---

## As built — every route `web/server.py` serves

Generated from the source, not remembered — **regenerate it, don't hand-edit it:**
```bash
grep -nE '^@(router|app)\.(get|post|websocket)\("' web/*.py bridge/agent_api.py; grep -n add_api_route web/server.py
```
`server.py` mounts each optional router (`OPTIONAL_ROUTERS`, no prefix) through `mount_router()`, then
mounts `landing/` as static files at `/` last. This table is *which routes exist*; what each page **shows** is the body of
this document, and it has not been re-checked against the pages.

**Pages**

| route | module |
|---|---|
| `GET /` | static mount of `landing/` — the locked 3D hero (ENTER → `/telemetry`, INFO → `/?info`) |
| `GET /?info` | the same `landing/index.html` with the hero hidden: the dashboard — sections `#status` `#search` `#history` (the commit graph + its command console) |
| `GET /robot` · `GET /pages/robot.html` | `server.py` `STANDALONE_PAGES` → `pages/robot.html` (the room page: voxels, agent chat; built by its own session) |
| `GET /capture` → newest · `GET /capture/{capture_id}` | `capture_api.py` |
| `GET /object/{object_id}` | `object_api.py` |
| `GET /telemetry` | `telemetry_api.py` — plus, at the top and OUTSIDE the board's loading gate, **Robot · live** (`pages/telemetry-robot.{js,css}`, link session): the head camera, link / robot / camera / telemetry-tap / event-log / last-capture facts, whether **Sentry is watching the robot** and what it has open, and EVERY signal the robot sends (the old 3-signal strip is retired; the static Sentry product list is folded into a `<details>`) |
| `GET /replay` · `GET /replay/{capture_id}` | `replay_api.py` |
| `GET /pages/{name}` (js/css) · `GET /pages/seer/{path}` | `capture_api.py` · `telemetry_api.py` |
| `GET /live` (exact path) | `robot_view_api.py` → `pages/live.html` — **what the robot's head camera sees, right now** (~2 fps), with pitch / tilt-rate / balanced from `/api/events` overlaid. A preview, not a capture: nothing is recorded. Does not shadow the static files below — those are `/live/…` |
| `GET /live/latest.json` · `/live/<capture>/<cam>.glb` · `…_color.jpg` | static, under `landing/live/` — written by the standalone receiver `camera_ingest.py`; **public through the tunnel** |
| any other path, asked for by a browser | `server.py`: an HTML 404 with the nav (a program, and everything under `/api/`, gets the §2.7 JSON) |

**API**

| module | routes |
|---|---|
| `server.py` | `GET /api/health` · `/api/config` · `/api/status` · `/api/events` (SSE) · `/api/routers` · `POST /api/internal/event` |
| `capture_api.py` | `GET /api/captures` · `/api/capture/{capture_id}` |
| `dash_api.py` | `GET /api/search` · `/api/object/{object_id}` |
| `graph_api.py` | `GET /api/graph` · `/api/diff` · `/api/state` · `/api/merge-preview` · `/api/cherry-pick-preview` · `/api/commands` · `POST /api/command` · `POST /api/resolve` |
| `object_api.py` | `GET /api/object-life/{object_id}` · `POST /api/object-life/{object_id}/point` |
| `replay_api.py` | `GET /api/replay/window` · `/api/replay/{capture_id}` |
| `telemetry_api.py` | `GET /api/telemetry/board` · `/api/telemetry/sentry/{capture_id}` · `/api/seer/status` · `POST /api/seer/ask` |
| `voxel_api.py` | `GET /api/voxels` |
| `robot_view_api.py` (link session, [docs/33](../docs/33-robot-link.md)) | `GET /api/robot/view.mjpg` (multipart stream; an `<img>` plays it) · `/api/robot/view.jpg` (one frame; 503 `no_live_frame` with the reason) · `/api/robot/view/status` · `/api/robot/link` (address · reachable · rtt · the robot's `/healthz` · `watch`: what `scripts/robot_sentry_watch.py` last wrote — stale or absent reads as NOT watching). Proxies the robot's `GET /camera/cam0.jpg` ([docs/16 §2.1b](../docs/16-api.md)): **every browser shares ONE poller**, and it stops 10 s after the last viewer leaves — a camera read costs the machine balancing the robot. Reads `PI_HOST` from the `.env` *file* at poll time, so `scripts/pi_link.py use …` needs no web restart. Touches neither ES nor room.git |
| `roommate_api.py` | `GET /api/room/ci` · `/api/blame/{object_id}` (both real, from git) · `/api/chores` · `/api/prs` (empty, `X-Roommate-Backend: not_connected`) · `POST /api/prs` · `POST /api/prs/{id}/approve` · `GET /api/nav/snapshot` (503 `not_connected` until their backends land) — plan/roommate/03-interfaces.md §8 |
| `jobs.py` (cloud session) | `GET /api/jobs/{job_id}` · `POST /api/jobs/{job_id}/result` (token required from everyone, loopback included) |
| `robot_view_api.py` (link session, docs/33) | `GET /api/robot/view.mjpg` · `/api/robot/view.jpg` · `/api/robot/view/status` · page `GET /live` — the robot's head camera. Not the same thing as the static `/live/…` files `camera_ingest.py` writes |
| `bridge/agent_api.py` (repo root, mounted as `bridge.agent_api`) | `POST /api/agent/command` · `GET /api/agent/bridge` · `WS /ws/gitirl-agent` — [docs/31](../docs/31-agent-panel-contract.md) |

**How `server.py` behaves (2026-09-19 07:31Z) — the parts a page or an operator relies on**
- **Optional routers never take the site down.** A module that is absent reads `not present`; one that raises on
  import or in `init(es)` is logged with its traceback, reads `FAILED: <exception>`, and is skipped. `GET /api/routers`
  → `{started, hot_reload: false, routers{name: state}}`. There is no hot reload: a router mounts on the first
  restart after its file exists. Everything under `landing/` and `pages/*.js|css` is read from disk per request.
- **`GET /api/health` is about THIS process, not just the cluster:** `{ok, elastic{…}, search{ok, detail, python}}`.
  `ok` is false when `elastic/queries.py` or the `elasticsearch` package cannot be imported (the server was started
  with the wrong interpreter) — `/api/search` would 503. Start it with `cd web && ../.venv/bin/python server.py`.
- **Commands.** The History section's console sends TEXT to `POST /api/agent/command` (read / plan only), then, on a
  second armed click, `POST /api/command {command, args{ref}, base_sha}` — verbs `revert` (undo ONE commit;
  `skipped[]` says what it left alone), `restore` / `checkout` (match a state), `cherry-pick`; reads `status` `diff`
  `log`. A stale `base_sha` is 409 `head_moved`; a state name that fits two different commits is 409
  `ambiguous_state`; a verb off `WEB_ALLOWED_COMMANDS` is 403. `executor` is `not_connected`: nothing moves.
  No page calls `POST /api/resolve`.
- **Public exposure.** Requests that arrive through a tunnel carry forwarding headers: for those,
  `POST /api/internal/event` is 403 and `POST /api/seer/ask` may READ an existing Seer run but never START one.
- **Sentry.** `obs.init("web")` only. The SSE request and static files are dropped from tracing; SSE streams end
  when the stop signal arrives, so a restart no longer files a `CancelledError`.
- Page builders: exact response shapes are in [`API-FOR-PAGES.md`](API-FOR-PAGES.md).

**Described above but with no route or section in the code:** *Analytics* (no `#analytics`, no
`/api/analytics/*`) and the *telemetry strip* on `/` (the live scope is on `/telemetry`).
`POST /api/resolve` exists and no page calls it (checked: `grep -rn api/resolve web/landing web/pages`).

**Not the same stream:** `GET /api/events` here is the laptop→browser SSE feed, 2 Hz and decimated.
The Pi's own `GET /events` ([`docs/16` §3c](../docs/16-api.md)) is the full-rate structured stream,
and a page may open an `EventSource` on it directly (it sends `Access-Control-Allow-Origin: *`).
