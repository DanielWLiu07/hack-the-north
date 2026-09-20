# 31 — The agent panel ↔ gitirl-agent contract (AGREED)

**Status:** **agreed 2026-09-19** with the panel / node-graph UI (htn:5, perception-f5), with
three changes from them folded in (restore plans as restore; `base_sha`/`target_sha`; the op
shape). **Built:** `bridge/` (19 tests), registered in `web/server.py` — live once web restarts. Andrew's side is quoted from **awzheng/gitirl @ `35f4c96`**
(2026-09-19 05:01Z): `docs/PROTOCOL.md`, `commands/models.py`, `commands/parser.py`. His
PROTOCOL.md: *"Daniel's backend schema is authoritative once finalized."* This is that schema.

**Re-checked at `b4f3e07` (2026-09-19 07:05Z, "Add HTTP and SSE edge integration boundaries").**
His parser, `commands/models.py` and the six verbs are **unchanged** from `35f4c96`, so the stub
needs no grammar change and now says `b4f3e07`. What changed is transport: `transport/websocket_client.py`
is **deleted**, and he talks to us over HTTP + SSE instead (§3b, §7.1). `/ws/gitirl-agent` still
exists here, but nothing of his dials it any more. The panel is served by his real parser over
jsonl (`served_by: "andrew:jsonl"`).

## 1. Two paths, and the UI always says which one served

| the panel's text | path | who deciphers | where it ends |
|---|---|---|---|
| maps to one of **his six verbs**: `add` `commit` `status` `diff` `restore` `log` | **middleware** | Andrew's parser (live) or our stub | our side: read-only answer, or a plan through the executor handoff |
| a **graph-native** verb: `revert` `merge` `cherry-pick` `branch` `checkout` `reset` (with or without `room`/`git` in front) | **graph** | nobody. It is **never sent to his middleware** | the same executor handoff as the node graph: `POST /api/command` planning |

**`revert` is not `restore`, and neither is `checkout`.** `revert <commit>` undoes that ONE
commit (its inverse, applied only to objects nothing has changed since — the rest come back as
`conflicts`). `restore <state>` makes the room match a state **on top of HEAD, as a new commit**
(`git restore --source=<state> --staged --worktree -- zones` + commit; history keeps the undo).
`checkout <ref>` moves HEAD. Nothing maps one onto another. If his parser ever answered `restore` for text that says
`revert`, we reject it (`intent_mismatch`) rather than move the robot the wrong way.

**Command text never reaches the robot.** Every path ends at a plan of object ops computed
from git refs by our planner (`executor: "not_connected"` until roomctl's executor is wired,
exactly like `POST /api/command`). His `command_result` from his own orchestrator is **not**
acted on: it is returned under `ignored` so the UI can show it and say why.

## 2. Request — the panel sends Andrew's envelope, verbatim

`POST /api/agent/command`

```json
{"type": "user_command", "request_id": "c7f2…", "timestamp": "2026-09-19T06:40:00Z",
 "payload": {"text": "set my room back to study mode"}}
```

- `type` must be `user_command`. We invent no new type values.
- `request_id` is required and non-empty; the panel makes it (a uuid). Every message the request
  causes carries it. A repeated `request_id` returns the first response and **never plans twice**.
- `timestamp` is optional on input.

## 3. Response — `200` for anything a person can TYPE; an error status only for outages

**Status rule (2026-09-19, after black-box testing):** every outcome a person can cause by typing
is **HTTP 200** with `"ok": false` and a structured `error` — `unknown_command`, `not_found`,
`intent_mismatch`, a refused or invalid plan. A judge's "put the mug back on the shelf" must render
as "I didn't understand", never as a broken page. **4xx** only for a malformed envelope
(`bad_request`); **5xx** only for outages (`bridge_unavailable`, `room_unavailable`,
`frame_mismatch`). Every body carries `"ok"`.

**Named states as people say them:** `study`, `study-mode`, `Study mode` all resolve to the tag
`study`, and in the other direction `study` finds a tag named `study-mode`. `action.ref` is what
was **said**; `result.ref_resolved` is the ref that **exists**, and `git_equivalent` prints that
one (a command naming `--source=study-mode` would fail in a room whose tag is `study`). Two
spellings on two different commits are never guessed between: that is `not_found` with the
candidates listed. A state that doesn't exist answers `not_found` with
`error.details.known_states` (the repo's tags and branches) and a `hint`. `room status` / `git log`
typed CLI-style reach his parser without the prefix (his grammar strips only `gitirl `);
`intent.raw_text` keeps what was typed.

```jsonc
{
  "request_id": "c7f2…",
  "path": "middleware",               // "middleware" | "graph"
  "served_by": "andrew:jsonl",        // "andrew:ws" | "andrew:jsonl" | "stub" | "gitspace"
  "intent": {                         // his GitIRLCommand, as data; null on the graph path
    "command": "restore", "target_state": "study", "message": null,
    "raw_text": "set my room back to study mode", "metadata": {}
  },
  "action": {                         // what OUR side did with it
    "kind": "plan",                   // "plan" | "read" | "refused"
    "as": "restore",                  // the git operation: restore | checkout | revert | cherry-pick | status | diff | log
    "ref": "study",
    "base_sha": "1a668ec…",           // HEAD when planned — the panel's Stage refuses if HEAD moved since
    "target_sha": "b3691ea…",         // the resolved ref (for revert: the commit being undone)
    "result": {
      "ops": [{"object_id": "mug_a1b2", "class": "mug", "kind": "move",        // move | add | remove
               "from": {"zone": "desk", "pose": {"x": 0.61, "y": 0.18, "z": 0.75, "yaw": 40.0}},
               "to":   {"zone": "desk", "pose": {"x": 0.42, "y": 0.18, "z": 0.75, "yaw": 15.0}},
               "base_pose": null,     // where the robot STANDS — null until roomctl routes it; when set:
                                      // {"pick": {x, y, yaw_deg} | {"unreachable": {filter: n}}, "place": {…}}
                                      // (a move needs two stances; the panel fills it from the costmap)
               "delta_m": 0.19, "frame": "world_z_up"}],
      "conflicts": [],                // revert / cherry-pick ops that no longer apply, with why
      "summary": {"move": 1, "add": 0, "remove": 0}, "estimated_s": 28,
      "base_sha": "…", "target_sha": "…", "working_tree_dirty": false,
      "applied": false,               // ALWAYS: nothing is applied here — the panel's backend stages
      "executor": "not_connected",    // and commits on a SECOND click, and is the only writer
      "git_equivalent": "git restore --source=study --staged --worktree -- zones && git commit"   // restore only
    },
    "frame": "world_z_up"             // every pose: metres, X fwd, Y left, Z UP (docs/20)
  },
  "messages": [                       // his protocol's envelopes, types from HIS enum only:
    {"type": "parsed_command", "request_id": "c7f2…", "payload": {"command": "restore", "target_state": "study"}},
    {"type": "command_result", "request_id": "c7f2…", "payload": {"status": "PLANNED", "message": "…", "attempts": 0}}
  ],
  "ignored": [                        // what the bridge sent that we did NOT act on, and why
    {"message": {"type": "command_result", "…": "…"}, "why": "applied by gitspace's executor, not the bridge"}
  ],
  "trace": [                          // the node graph: one node per hop, in order
    {"node": "panel",    "label": "set my room back to study mode"},
    {"node": "route",    "label": "middleware", "why": "matches his verb 'restore'"},
    {"node": "decipher", "label": "restore study", "served_by": "andrew:jsonl", "ms": 41},
    {"node": "executor", "label": "restore study: 2 op(s), executor not_connected", "ms": 18, "executor": "not_connected"}
  ],
  "sentry_trace_id": "3b7a…"          // the whole round trip as gen_ai spans (obs.agent_turn / agent_tool)
}
```

Errors keep the same body with `"error": {"code", "message"}`, a `messages` entry of his type
`error`, and a `trace` ending in **the node that failed** (a restore to a state that doesn't exist
deciphers fine and fails at `executor`: `not_found`). Trace order implies the edges; a `parent`
field appears only if a trace ever branches. Codes: `unknown_command` (his parser:
not one of the six), `intent_mismatch`, `bridge_unavailable` (live bridge asked for, none
answering), `frame_mismatch`, `bad_request`, plus `POST /api/command`'s own codes on the
graph path.

`GET /api/agent/bridge` → `{"mode": "auto", "live": {"ws": false, "jsonl": true}, "will_serve": "andrew:jsonl"}`
so the panel can show which decipherer is armed before anything is sent.

## 3b. His HTTP client (`DanielAPIClient`, b4f3e07) on our other doors

| his call | our route | what it gets |
|---|---|---|
| `plan_command(command, args)` | `POST /api/command` | graph verbs make a **job** (`202`; asking again in the same room is the same job, `200` + `replayed: true`). The job carries the ordered `gitspace.plan/1` he executes (ANDREW-HANDOFF.md §2b). **`status`, `diff`, `log` are READS here too** (2026-09-19): `200` with `{"kind": "read", "command", "ref", "result"}`, never queued, no `job_id`. Reads still obey `WEB_ALLOWED_COMMANDS`. `diff` with a `ref` returns the ops HEAD → ref; bare `diff`/`status` return the working-tree summary; `log` the last 15 commits. His `add`/`commit` stay `400 unsupported_command`: roomctl writes room.git, the web tier never does. Every answer carrying a pose has top-level `frame: "world_z_up"` + `units`. |
| (new) poll / report | `GET /api/jobs/{id}` · `POST /api/jobs/{id}/result` | the job's state, plan, progress and result; the write needs `Authorization: Bearer $GITIRL_CLOUD_TOKEN`. The full contract is ANDREW-HANDOFF.md §2b. |
| `get_state(ref)` | `GET /api/state?ref=` | unchanged: every object at that commit, plus the zones. |
| `events(last_event_id)` | `GET /api/events` (SSE) | unchanged. `Last-Event-ID` resumes. |
| `publish_event(event, data)` | `POST /api/internal/event` | his three names are **mapped, not rejected** (below). |

**His event names → ours.** Our inlet speaks `status job capture conflict telemetry`; his
protocol names `robot_status robot_observation robot_action_result`. Each of his lands as our `job`
event, and the response says so: `{"published": "job", "from": "robot_status"}`.

| his event (payload per his PROTOCOL.md) | our `job` data |
|---|---|
| `robot_status` `{status, message?, metadata?}` | `{id, kind: "robot_status", state: <status>, detail: <message>, metadata}` |
| `robot_observation` `{observation}` | `{id, kind, state: "observed", observation}`. **Frame asserted first:** any pose not declaring `world_z_up` → `422 frame_mismatch`, nothing published |
| `robot_action_result` `{result: {status, message?, observations?}}` | `{id, kind, state, result}`. `success` → `done`, `retryable` → `retrying` (not an ending: he retries), `failed` → `failed` |

`id` is the first of `id`, `job_id`, `request_id`, `metadata.job_id`, else `"robot"`. Any other
name is still `400`. His PROTOCOL.md calls these three "modeled for fixtures but … not a network
transport", so nothing of his posts them yet; this is ready for when he does.

**The inlet is loopback-only**, including through the public URL (Funnel's forwarding headers mark
the request as not local → `403`). His `publish_event` works only from a process on this laptop.
If his edge runs elsewhere and needs to publish, that needs a token-gated door. It is not opened
by default.

## 3c. The caretaker path (Sat 13:00 split: we parse and decide, his layer understands, his edge executes)

`plan/roommate/03-interfaces.md` §12 is the contract. Routing for the panel's text, in order:
1. graph verbs → `path: "graph"` (unchanged);
2. **our caretaker grammar** (`bridge/intents.py`) → an Intent validated by `bridge/intent.schema.json`
   → `path: "caretaker"`, `served_by: "gitspace:grammar"`;
3. the six verbs → **our** six-verb grammar (`stub_parse`, now production: `served_by: "gitspace:grammar"`).
   His jsonl/ws parsers are test doubles only (`ANDREW_BRIDGE=jsonl|ws`);
4. nothing matched → **the understanding layer** (`INTENT_URL`, or `ANDREW_INTENT_URL`; `POST /v1/intent`)
   → an Intent we validate the same way (`served_by: "andrew:intent"`). Ours is
   `scripts/intent_service.py` (OpenAI, structured output) until his exists; either drops into the same
   hook. An invalid answer → `intent_invalid`. Never repaired, never guessed.
5. still nothing → before answering `unknown_command`, ask the RESOLVER whether the sentence even names
   something the room has. If it doesn't: the same honest `no_match` (this is what the cloud tier, which
   has no OpenAI key, answers for "pick up the trash"). If it does: `unknown_command` naming the object —
   the sentence was the problem, not the thing.

### Naming a thing is not the same as being sure which thing

A vector search has no "not found": a nearest neighbour always exists, so "pick up the trash" in a room
with no trash comes back as a ceramic cup at a plausible score. Every intent that NAMES an object
(`find`, `point`, `tidy`, `move`, `blame`, `restore_time`) therefore resolves it through
`bridge/caretaker.py` before anything is planned, and the score decides which of three things happens.
Measured on this room, jina-reranker-v3.5, 2026-09-19 — **these are measurements of an object set, not
constants of the model** (the bowl arriving mid-evening moved "the trash" from 1.029 to 1.095):

| band | score | what happens |
|---|---|---|
| refuse | `< 1.11` (`RESOLVE_CONFIRM_SCORE`) | `no_match`, naming the nearest: *"there is nothing in the room that matches 'trash'; the nearest are bowl, plant and cup"*. The absurd cluster lives here: "banana" 1.056, "the trash" 1.065–1.095, "television remote" 1.101 |
| **ask** | `1.11 – 1.20` (`RESOLVE_MIN_SCORE`), **or** the top two within `0.05` of each other | `action.kind: "confirm"` — the question, the candidate, the runner-up. **Nothing is planned or dispatched.** The vague-but-real cluster lives here: "something to write with" 1.126, "something to drink from" 1.169 |
| act | `>= 1.20` | as before: a job, dispatched if the edge is on |

**The confirmation is a second request, not a timer.** `result.yes` is a complete envelope —
`{"type": "user_command", "payload": {"text": <the same sentence>, "object_id": "mug_a1b2"}}` — POSTed
with a **fresh `request_id`** (the endpoint is idempotent per id, so reusing it would replay the
question). `payload.object_id` is the person saying "that one": the Intent then names the object and
`resolve` takes the id path. A "no" is simply never sending it, so an unanswered question can never
become an action, and there is no pending state on the server.

Two things this deliberately does NOT claim. It rules out answers that were never close — not wrong
answers that were: "the banana" would still land on a plant if the plant scored well. And part of the
overlap is labelling rather than model error ("a bottle of water" → the mug is defensible), so the act
floor is not raised until reasonable answers count as wrong. Anything that MOVES an object needs a
person either way: `move` is a proposal requiring approval.

### A motion is not a search, and being careful is not the check

The floors above decide WHICH object. They do not decide whether that object is still in the room, and
on 2026-09-20 that gap was live: Elasticsearch indexes the room's whole history — deliberately, because
"where did my marker go" is the question this product exists to answer — so `room-objects` held 14
objects while the room held 11. Asking "something to write with" resolved to `marker_c3d4` at 1.126,
which is the **ask** band, and the panel offered: *"I think you mean the marker on the desk — shall I
point at it?"* The marker was removed at `1a668ec`, hours earlier. A person saying yes to a
reasonable-sounding question is a real path to a robot driving at a pose where nothing is standing.

**The floors are what saved it, and that is exactly why they are not enough.** 1.126 asked rather than
acted; at 1.454 it would have acted outright, and no amount of caution in the bands would have helped,
because the object really was the best match — the room simply does not have it any more. So the check
lives where the MOTION IS BUILT (`object_api.build_point` raises `Gone`), not where the object is
searched for. Search ranges over history; a job does not. It is the check that does not depend on
anybody being careful.

Three fixes were on the table and two were wrong. Filtering the resolver's hits to HEAD would have made
the room's own history unsearchable, which is the thing being sold. Reindexing `room-objects` from HEAD
would have deleted the record of everything that ever left. What is right is the strict boundary at the
job, and leaving search alone.

**The refusal is the demonstration.** `action.kind: "gone"` carries `speech` and `whereabouts`
(`last: {sha, subject, zone}`, `gone: {sha, subject}`, or `on_branches`), so the answer is the room's
history rather than an apology:

> *"the marker is not in the room any more — it was on the desk at e51a75a (initial scan: the bench as
> found), and it was gone by 1a668ec (afternoon: mug moved, marker gone, scissors out)"*

An object that was never removed but lives on another branch gets a different true sentence — *"the bowl
is not in the room on this branch — it is on movie-night"* — because "it was removed" and "it is
somewhere else" are not the same claim. A room that can say where a thing went, and refuses to send a
robot after it, demonstrates history and safety in one sentence. Pinned in
`web/tests/test_whereabouts.py` (the git lookup) and `bridge/test_caretaker_safety.py` (the decision,
at both the ask score and the act score).

`bridge/caretaker.py` turns an Intent into `action.kind`:
| intent | `action.kind` | result |
|---|---|---|
| `find` / `point` | `job` | `resolved` (Elastic hybrid search; room.git when Elastic is parked, labelled `how`), the `point` job, `dispatch` |
| `tidy` | `jobs` | one `move` job per `move` op of roomctl's plan (last scan → HEAD), `skipped` for the rest, sent in order |
| `move` | `proposal` | `approval_required: true`, `job: null`: where a thing belongs changes only through a PR |
| `status`, `blame` | `read` | `room.summary` + changes; `roommate_api.blame_sync` |
| `restore_time` | error `not_built` | a time is not resolved to a commit by guessing |

Jobs go to Andrew's edge through `web/housebot.py` (below the table in docs/10 / PLAN §0): off until
`HOUSEBOT_EDGE_URL` is set, and a kind must be on `WEB_ALLOWED_COMMANDS`. The edge's terminal
`CaretakerJobResult` comes back as the SSE `job` event (with `target_pose`, `zone`, `frame`) and in
`GET /api/jobs/{id}`. A job is sent at most once. Job ids are deterministic per request_id.

## 4. Frames — asserted at his boundary, never converted here

Ours is **Z-up** (X fwd, Y left, Z up, metres, floor z = 0 — docs/20). Bracket Bot's native
frame is **Y-down**. His `ObjectState.position` is `Any` and PROTOCOL.md calls poses
"deliberately opaque". So every pose-carrying message **crossing to or from his side** carries
`"frame"`, and our side **asserts `frame == "world_z_up"`**, refusing a message without one or
with another (`frame_mismatch`). **The Y-down conversion lives in his RobotAdapter.** A silent
swap here would put the arm 90° off and look like an IK bug.

## 5. Stub vs live
`ANDREW_BRIDGE=auto` (default) serves with, in order: his agent connected to our
`/ws/gitirl-agent` → `andrew:ws`; else his repo at `ANDREW_REPO` run as
`scripts/run_dev.py --jsonl` → `andrew:jsonl`; else our stub → `stub`. `ANDREW_BRIDGE=stub|jsonl|ws`
forces one. **The stub is labelled wherever it answers** — `served_by: "stub"` — and mirrors
his parser's grammar at `b4f3e07` (unchanged since `35f4c96`), so a demo on the stub is honest about being one.

**Since `b4f3e07` the WS rung is dead on his side** (his WebSocket client is deleted), so `auto`
serves with `andrew:jsonl`. `GET /api/agent/bridge` says so in words:
`"andrew": {"rev": "b4f3e07", "ws": "retired on his side at b4f3e07 …", "jsonl": "his real parser, run with no GITIRL_* variables — it cannot reach a robot"}`.
That last clause matters. At `b4f3e07` his `run_dev.py` **executes what it parses** through
`HTTPRobotAdapter.from_environment()` whenever `GITIRL_ROBOT_BASE_URL` is set. The bridge spawns
it with **every `GITIRL_*` variable stripped**, so his orchestrator only ever runs its mock, and
its `command_result` stays under `ignored` (§1). This is tested with `GITIRL_ROBOT_BASE_URL`
pointed at a port: nothing reaches it.

## 6. Who writes what
- **This endpoint:** git reads only. It never writes `room.git`, never queues a job, never moves
  the robot. Tested against the real `room.git` with a before/after fingerprint.
- **The panel's backend (htn:5):** the ONLY writer — Stage, then Commit, each on an explicit
  click, refusing if HEAD ≠ `base_sha`.
- **The robot:** moves only from roomctl's executor, from committed state — never from text.

## 7. The finalized contract, for Andrew (awzheng/gitirl)

His PROTOCOL.md asks for this ("Daniel's backend schema is authoritative once finalized").
What we need from gitirl-agent, and what we promise back:

1. **Transport — superseded by him at `b4f3e07`: HTTP + SSE.** He is a plain HTTP client of our
   existing routes (`GITIRL_CLOUD_BASE_URL`, optional `GITIRL_CLOUD_TOKEN`). The routes, including
   the read verbs on `/api/command` and his event names on the inlet, are in §3b. The panel's
   deciphering runs his parser over jsonl on this laptop, so he needs no inbound connection from
   us. `/ws/gitirl-agent` stays up but unused. Envelope unchanged. Add `"version": 1` to envelopes
   once he's on this contract.
2. **He deciphers; he does not execute `user_command`.** Answer a `user_command` with
   `parsed_command`, or `error` (`unknown_command`). In production, **no `command_result` for a
   `user_command`** — his dev orchestrator's "RESTORE_COMPLETE" is a mock claiming a physical
   restore that never happened. We ignore it today; he should stop sending it.
3. **The six verbs stay six.** `add commit status diff restore log` — **do not add** `revert`,
   `merge`, `cherry-pick`, `branch`, `checkout`, `reset`: those are graph-native, ours, and never
   sent to him. His parser must keep answering `unknown_command` for them — **never `restore` for
   `revert`** (we refuse that combination as `intent_mismatch` either way).
4. **`restore` means**: make the room match `target_state` as a new commit on HEAD (git restore
   --source + commit). Not checkout, not revert.
5. **`target_state` is any git ref** — a tag (named states like `study` are tags), a branch, a
   sha. Widen his `[\w-]+` to the ref charset `[\w./~^-]+` so `HEAD~2`, `a3f9c1^`,
   `movie-night/v2` parse.
6. **Poses are not opaque.** Anything carrying a pose uses `{"x","y","z","yaw"}` in metres and
   declares `"frame": "world_z_up"` (X fwd, Y left, Z up, floor z = 0). **The conversion to
   Bracket Bot's Y-down lives in his RobotAdapter.** A pose without that frame is refused
   (`frame_mismatch`), never converted by us.
7. **request_id:** the panel makes it; our backend dedupes (a repeat never plans twice), so he
   needn't store ids.
8. **Resolved — see `ANDREW-HANDOFF.md` §2 (final).** His edge runs our plan: one `robot_action`
   `{"plan": <gitspace.plan/1>}` per plan, sent only after the git side is committed; he verifies
   and retries each op (≤ 2), answers with `command_status` ×N then `command_result` (ONLY in reply
   to a `robot_action`); our rescan is final; roomctl's HttpRobot is the fallback. A `user_command`
   is therefore answered by `parsed_command` or `error` alone — the bridge treats those as terminal.
   Sending `robot_action` over `/ws/gitirl-agent` is not built yet.

