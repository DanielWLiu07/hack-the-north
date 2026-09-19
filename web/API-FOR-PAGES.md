# Read-only endpoints for page builders (`pages/robot.*` and friends)

Everything here is **GET, same-origin, safe to poll**, and already live on `:8000` (and through the
tunnel). Shapes below are copied from live responses on 2026-09-19. Errors everywhere are
`{"error": "<code>", "detail": "<words>", "retryable": <bool>}` with a real HTTP status.

**Not for pages other than the graph:** `POST /api/agent/command`, `POST /api/command`,
`POST /api/resolve`, `POST /api/seer/ask` — command mutations stay with the graph console
(`landing/graph.js`). `POST /api/seer/ask` can bill a Seer run.

**The dashboard moved.** `/` is the locked 3D hero; the dashboard is `/?info`, so deep links are
`/?info#status`, `/?info#search`, `/?info#history`. `/#history` lands on the hero and goes nowhere.

## Bridge / middleware status — `GET /api/agent/bridge`
```json
{"mode": "auto", "live": {"ws": false, "jsonl": true}, "will_serve": "andrew:jsonl"}
```
`will_serve`: `andrew:ws` (his agent connected over WebSocket) · `andrew:jsonl` (his parser, run
locally — live code, not a stub) · `stub` (ours; **label it as a stub**). 404 = the bridge router is
not mounted on this server build.

## Camera / room cloud — `GET /live/latest.json` (a static file; 404 until a capture has arrived)
Written by `web/camera_ingest.py`. Poll it (no-cache is set); `received_at` changes when a new one lands.
```jsonc
{
  "capture_id": "cap_0004",
  "at": "2026-09-19T06:46:41.512Z",        // the sender's shutter time, or null
  "received_at": "2026-09-19T06:46:41.690Z",
  "source": "robot",                       // "robot" | "session_folder"
  "sender": "http://127.0.0.1:8091",
  "sender_mode": "sim",                    // "hardware" is the ONLY value that means real cameras;
                                           // "sim" | "replay" | "recorded" | null otherwise — say so on the page
  "pose": {"x": 0.0, "z": 0.0, "yaw": 0.0}, "pose_source": "sim",
  "quality_ok": true, "skew_ms": 0.002, "tilt_rate_max": 0.0249, "coverage": 0.9752,
  "sentry_trace_id": null,
  "cameras": [{
    "camera": "cam1", "model": "D415",
    "intrinsics": {"fx": 232.8, "fy": 232.8, "ppx": 160.0, "ppy": 120.0, "w": 320, "h": 240,
                   "assumed_from_hfov_deg": 69.0},   // key present ONLY when they were assumed, not measured
    "color": "cap_0004/cam1_color.jpg",    // relative to /live/ ; null if none
    "cloud": "cap_0004/cam1.glb",          // relative to /live/ ; null -> read cloud_reason
    "points": 74892,
    "cloud_reason": null                   // why there is no cloud, in words, when cloud is null
  }],
  "is": "a point cloud: RealSense depth back-projected through the camera's intrinsics, …",
  "is_not": ["a gaussian splat", "registered to the room (no camera->robot extrinsics applied)",
             "fused: each camera is its own cloud"],
  "axes": "glTF: x right, y up, -z forward (camera frame, …)", "units": "metres"
}
```
The `.glb` is one glTF `POINTS` primitive (`POSITION` f32, `COLOR_0` u8 RGBA normalised). With the
vendored three (`/vendor/three/`): `new GLTFLoader().loadAsync('/live/' + cam.cloud)` → traverse for
`o.isPoints`; its material already has `vertexColors`. Set `material.size` / `sizeAttenuation` to taste.
Right now `latest.json` holds a **simulated** capture (`sender_mode: "sim"`, intrinsics assumed).

## The agent chat contract — `POST /api/agent/command` for READ / PLAN only (owner: bridge/, docs/31)
A chat page may call this: it **never writes room.git, never queues a job, never moves the robot**. It may NOT
call `POST /api/command` (that is the queue step, and it belongs to the graph console). Verified live 07:33Z:
```jsonc
// request — Andrew's envelope, verbatim. request_id is REQUIRED and must be NEW per send: a repeated id returns
// the FIRST response again (idempotent by design — it looks like "the bridge ignores my edits").
{"type": "user_command", "request_id": "<uuid>", "timestamp": "<iso, optional>", "payload": {"text": "restore study"}}

// response — ALWAYS read the body; typed outcomes are HTTP 200 with ok:false. 5xx only when something is down.
{"request_id": "…", "ok": true,
 "path": "middleware" | "graph",                 // middleware = his six verbs (add commit status diff restore log); graph = revert checkout cherry-pick merge …
 "served_by": "andrew:jsonl" | "andrew:ws" | "stub" | "gitspace",   // SHOW THIS. "stub" must be labelled a stub.
 "intent": {"command": "restore", "target_state": "study", "message": null, "raw_text": "…", "metadata": {}} | null,   // null on the graph path
 "action": {"kind": "plan" | "read" | "refused", "as": "restore", "ref": "study" /* as SAID */, "frame": "world_z_up",
            "result": { /* plan: */ "base_sha", "target_sha", "ref_resolved" /* the ref that EXISTS */, "ops": [{"object_id", "class", "kind": "move"|"add"|"remove",
                        "from": {"zone", "pose"} | null, "to": {"zone", "pose"} | null, "base_pose": null, "delta_m", "frame"}],
                        "conflicts": [{"object_id", "why"}], "summary": {"move", "add", "remove"}, "estimated_s", "working_tree_dirty",
                        "applied": false, "executor": "not_connected", "git_equivalent" /* restore only */
                        /* read (status): {"clean", "changes", "conflicts", "branch", "head", "rev"}; log: {"commits": [{sha, subject}]}; diff: {"a", "b", "ops"} */
                        /* refused: {"detail": "…why, in words"} */ }} | null,
 "messages": [ /* his protocol's envelopes: parsed_command, command_result | error */ ],
 "ignored": [{"message": {…}, "why": "…"}],      // what his orchestrator said that we did NOT act on — worth showing as a collapsed "tool trace"
 "trace": [{"node": "panel" | "route" | "decipher" | "executor", "label": "…", "ms": 155.9, "served_by": "…", "why": "…", "error": "…"}],   // last node = where it failed, when ok is false
 "error": {"code": "unknown_command" | "not_found" | "intent_mismatch" | "bad_request" | "bridge_unavailable" | "frame_mismatch" | "room_unavailable",
           "message": "…", "details": {"known_states": ["live-check", "main", "movie-night", "study"], "hint": "…"}},   // present only when ok is false
 "sentry_trace_id": "d2912c4d…"}
```
State names are forgiving since 07:25Z (web/graph_api.resolve_state): `study`, `study-mode`, `Study Mode`, `study_mode`
are one state whichever way the tag or branch was named; two spellings on two DIFFERENT commits resolve to
nothing rather than a guess. His parser itself only understands ONE-word states in natural phrasing
("set my room back to study mode" works; "…to movie night" is `unknown_command` — say `restore movie-night`).
It is a command parser, not a conversation: no memory between sends. Room QUESTIONS ("where is my mug") belong to `/api/search`.

## Is my router mounted? — `GET /api/routers` (live after the next restart of :8000)
```json
{"started": "2026-09-19T06:52:35Z", "hot_reload": false,
 "routers": {"capture_api": "loaded (5 routes)", "voxel_api": "not present", "bridge.agent_api": "loaded (3 routes)"}}
```
There is **no hot-loading**: `server.py` runs plain `uvicorn.run(app)` with no reloader, and routers are
imported once, at process start. A router module mounts on the first restart AFTER its file exists
(`router = APIRouter()` + `init(es)`; `voxel_api` is already in the list). A module that is missing reads
`"not present"`; one that is broken reads `"FAILED: <exception>"` and is skipped — the rest of the site
stays up — so check this endpoint after a restart instead of assuming. 404 here = the process is older
than this endpoint. **No restart needed** for anything under `web/landing/**` or `web/pages/*.js|css`
(and `pages/robot.html`): those are read from disk on every request, `Cache-Control: no-cache`.

## Health and room status
- `GET /api/health` → `{"ok": true, "elastic": {"configured": true, "reachable": true, "version": "9.6.0", "flavor": "serverless"}}`
  (when Elasticsearch is parked/unreachable: `elastic.reachable: false` plus `code` / `paused`).
- `GET /api/status` → `{"branch": "main", "head": "1a668ec", "clean": true, "changes": [], "last_capture": "2026-09-19T06:29:38Z", "conflicts": [], "merging": null}`
- `GET /api/events` — SSE. Event names: `status`, `job`, `capture`, `conflict`, `telemetry`, and the roommate plan's
  `room_state`, `chore`, `pr`, `nav` (`nav` and `telemetry` are never replayed after a reconnect)
  (`telemetry` data: `{ts, pitch, tilt_rate, …, tilt_rate_peak}` at ~2 Hz when a hub is connected; plot `tilt_rate_peak`).
  Reuse `window.gitrlEvents` if it exists rather than opening a second stream.

## The roommate's paperwork (live after the next restart of :8000)
- `GET /api/room/ci` → `{state: clean|dirty|conflict|unknown, branch, head, changes[], conflicts[], last_capture,
  misplaced: [{object_id, is_in, belongs_in}], since, heartbeat{slug, last, at}, last_verified_job, watch, source, frame}`.
  `changes` is git's own rows (one mug in the wrong zone = a `deleted` row AND an `untracked` row); `misplaced` pairs them.
  `last_verified_job` is the ONLY proof that a job worked: the watch loop saw a clean FRESH pass after it ended. `watch` is
  the loop's last verdict `{clean, confirmed[], pending[], blocked, stale_blocks, ignored, passes, head, at, received_at}`,
  null until the loop has pushed one; it survives a restart of the server.
- `GET /api/chores[?status=open|closed]` → `[{id, object_id, zone, type, verdict: mess|decision|untracked_shared, owner, opened_at, status, closed_at, closed_by, frame_url}]`
- `GET /api/prs` → `[{id, branch, title, author, base_sha, head_sha, status: open|merged|closed, ops[{op, object_id, class, from{zone,x,y,z,yaw}, to{…}}], merged_in}]`
- `GET /api/nav/snapshot` → the last pushed `nav` + `{frame, received_at, age_s, stale}`; 503 `not_connected` until one arrives. Draw a `stale` pose as where the robot WAS.
- **Writes — local, or `Authorization: Bearer $GITIRL_CLOUD_TOKEN`; through the tunnel they answer 401/403, so say "from the room's own laptop":**
  `POST /api/prs {object_id, zone, title?}` → 201 PR · `POST /api/prs/{id}/approve` → `{merge_sha, job_id: null, job_reason, pr}` ·
  `POST /api/prs/{id}/close` · `POST /api/edge/event {event: room_state|nav|chore|pr, data}` (roomctl's watch loop: `ROOM_WEB_URL=http://127.0.0.1:8000`).
  404 = no such object / zone / PR · 409 = already there, already merged, closed, conflicts, no free spot.

## Finding things
- `GET /api/search?q=<text>&limit=20&all_time=true` →
  `{query, all_time, head, reranked, retriever, bm25_fields, took_ms, provenance{synthetic_results, of, legend{real, generated}}, results[]}`;
  each result: `{object_id, class, score, last_seen{commit_sha, ts, zone, pose{x,y,z,yaw}, branch, capture_id}, present_now,
  matched_by{bm25, vector, rerank_position, bm25_rank, vector_rank, vector_score}, descriptions[], provenance{synthetic, scripted_text, rendered_input, vlm_model, why}, timeline[]}`.
  Link a result to `/object/<object_id>`, and its `last_seen.capture_id` to `/capture/<id>` and `/replay/<id>`.
  **If `provenance.synthetic` (or provenance is missing) say SYNTHETIC on the card** — the text was scripted, not seen by a camera.
- `GET /api/captures?limit=50` → `{source, captures: [{capture_id, ts, commit_sha, cameras[], coverage, gate_pass}]}` newest first.
- `GET /api/capture/<capture_id>` — everything `/capture/<id>` draws (gate, cameras, per-camera disagreement, telemetry ±2 s, diff, sentry link, `provenance`).
- `GET /api/object-life/<object_id>` — everything `/object/<id>` draws.
- `GET /api/replay/<capture_id>?before=8&after=2` — see `replay_api.py`'s docstring: `path | null`, `pitch`, `signals`, `drift`, `anchors`, `spans`, `events`, `trace_url`; every missing lane carries its reason.
- `GET /api/graph?limit=100` → `{nodes[{sha, parents, refs[{name, kind, head}], ts, subject, changed, quality_ok, capture_id, sentry_trace_id, rejected_before[]}], head, branch, trunk[], source, enriched, executor}`.
- `GET /api/commands` → `{"allowed": [...], "graph": {"revert": true, "restore": false, …}, "executor": "not_connected"}`.
- `GET /api/telemetry/board?limit=12`, `GET /api/seer/status` — the telemetry board's data.

## The one nav bar (every page wears it)
```html
<link rel="stylesheet" href="/pages/sitenav.css">
<nav class="sitenav" aria-label="GITIRL">
  <a class="brand" href="/" aria-label="GITIRL — home">GITIRL</a>      <!-- drawn as the Katie Roze wordmark (/brand-gitirl.svg, a CSS mask) -->
  <a class="sec" href="/?info">Overview</a><a class="sec" href="/robot">Room</a>
  <a class="sec" href="/telemetry">Telemetry</a><a class="sec" href="/live">Live</a>   <!-- aria-current="page" on the one you are -->
</nav>
```
Four places, same order, everywhere. No second bar of page sections. Set `--nav-gutter` on the nav if your page's content
edge is not `clamp(14px, 3vw, 40px)`, so the wordmark sits on the same line as the content under it.

## Pages to link to
`/?info` dashboard · `/telemetry` · `/capture/<id>` · `/object/<id>` · `/replay/<id>` (or `/replay?from=<iso>&to=<iso>`) · `/robot`
