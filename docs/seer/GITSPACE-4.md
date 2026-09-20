# GITSPACE-4 — CancelledError: Task cancelled, timeout graceful shutdown exceeded

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7741550237/ |
| seen | 11 times, 2026-09-19 01:19 → 2026-09-19 07:35 |
| level | error |
| culprit | `asyncio.locks in wait` |
| Seer run | `16890786` · COMPLETED · stopped at `root_cause` · 5 turn(s), 5 tool call(s) |
| collected | 2026-09-20T06:09:30+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

## Root cause, in Seer's words

**Root cause:** The `/api/events` SSE endpoint returns a `StreamingResponse` with `Connection: keep-alive` that stays open indefinitely, waiting for the client to disconnect. When uvicorn receives a shutdown signal, Starlette's `listen_for_disconnect` coroutine blocks on `asyncio.locks.Event.wait()` with no timeout. After the graceful shutdown period expires with 3 tasks still alive, uvicorn force-cancels them — triggering the `CancelledError`.

The breadcrumbs confirm the sequence: "Shutting down" → "Waiting for connections to close" → "Cancel 3 running task(s), timeout graceful shutdown exceeded". The `Accept: text/event-stream` header in the scope confirms these are live SSE connections from the browser.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-4.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
