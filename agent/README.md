# `agent/` — LAPTOP

**Branch:** `track/agent` · **Owner:** Andrew — with the ML half of `perception/` (`segment`, `describe`, `associate`).

## What this module owns
The LLM loop and **one tool surface shared by every front end**. CLI, voice, and text messaging
are three doors into the same brain — never build a second command parser.

```
SMS / Telegram ─┐
voice (Realtime)─┼──► agent/loop.py ──► tools ──► retrieve (ES) · act (robot)
CLI (roomctl) ──┘
```

## Files

| file | purpose |
|---|---|
| `loop.py` | The agent loop: tool selection, multi-step reasoning, honest failure reporting. |
| `tools.py` | Tool definitions. **Retrieve:** `search_objects`, `observation_history`, `room_analytics`, `spatial_query`. **Act:** `room_commit`, `room_checkout`, `room_revert`, `move_object`, `goto_and_point`. |
| `voice.py` | Glue over BB's `example_realtime.py` (OpenAI Realtime over WebRTC — already written). |
| `messaging.py` | Telegram two-way + AWS SNS outbound alerts. See [`../docs/17-messaging-and-aws.md`](../docs/17-messaging-and-aws.md). |
| `prompts.py` | System prompts. The occlusion-resolution and conflict-negotiation prompts are the ones that matter. |

## Build order
1. `tools.py` with the retrieval tools against `fake/` data. Hour 4.
2. `loop.py` — get it answering "where is the mug" from Elasticsearch alone.
3. The **occlusion-vs-deletion** decision — the one that changes robot behaviour. Highest value.
4. Action tools wired to `roomctl`.
5. `messaging.py` — Telegram first (10 min, no number provisioning), SNS alerts second.
6. `voice.py` — mostly glue, the example already exists.

## Acceptance criteria
- [ ] "Where did I leave my hammer" → correct object, correct last-seen commit, robot points
- [ ] "Put the desk back the way it was before dinner" → correct `commit_sha`, robot executes
- [ ] Given an ambiguous disappearance, the agent queries observation history **before** deciding
- [ ] The agent reports partial success honestly rather than claiming completion

## Gotchas
- **The agent must do real work** or the Rox prize claim is hollow: resolve ambiguous
  associations, decide occluded vs gone, order operations, negotiate conflicts. Not commit messages.
- **Never let a beat end with text on a screen.** If the robot doesn't move or point, cut it.
- Allow-list tool names on any inbound channel. Never dispatch a name off the wire.
