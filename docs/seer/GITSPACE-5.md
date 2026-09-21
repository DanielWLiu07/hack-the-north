# GITSPACE-5 — Cancel 1 running task(s), timeout graceful shutdown exceeded

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7741550271/ |
| seen | 17 times, 2026-09-19 01:19 → 2026-09-20 08:34 |
| level | error |
| culprit | `—` |
| Seer run | `16891926` · COMPLETED · stopped at `root_cause` · 10 turn(s), 11 tool call(s) |
| collected | 2026-09-20T08:41:15+00:00 by `scripts/seer_sweep.py` |

**Files Seer named:** `web/events.py`, `web/server.py`

## Root cause, in Seer's words

**Root cause:** `watch_room()` (in `web/events.py`) polls git by calling `await asyncio.to_thread(room.snapshot)` in a tight loop. `room.snapshot()` runs `git status --porcelain=v2 ...` as a blocking subprocess inside that thread.

When uvicorn shuts down:
- It calls `watcher.cancel()` (in the lifespan cleanup at `web/server.py:183`)
- This raises `CancelledError` in the asyncio task, but **the thread running the subprocess cannot be cancelled** — it keeps executing `git status` until it finishes naturally
- The breadcrumbs confirm `git status` invocations continue appearing even after uvicorn logs "Shutting down" and "Waiting for connections to close"
- Uvicorn's shutdown grace period expires with that thread-backed task still "in flight", triggering **"Cancel 1 running task(s), timeout graceful shutdown exceeded"**

A secondary contributing factor: the lifespan cleanup never `await`s the cancelled task — it just calls `.cancel()` and moves on — so there's no opportunity to drain the thread before uvicorn's timeout starts.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-5.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
