# GITSPACE-1N — KeyError: 'query'

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7743698372/ |
| seen | 1 times, 2026-09-20 08:59 → 2026-09-20 08:59 |
| level | error |
| culprit | `telemetry_search_api in search` |
| Seer run | `16892146` · COMPLETED · stopped at `root_cause` · 6 turn(s), 6 tool call(s) |
| collected | 2026-09-20T09:12:10+00:00 by `scripts/seer_sweep.py` |

**Files Seer named:** `telemetry_search_api.py`

## Root cause, in Seer's words

**Root cause identified.**

In `telemetry_search_api.py` line 87, the `search` function does:
```python
body = r.pop("request")
...
"query": body["query"],  # KeyError!
```

But the `body` dict (popped from `r["request"]`) has this structure:
```python
{
    "index": "'room-events'",
    "body": {"query": {...}, "highlight": {...}, ...}  # ← query is nested here
}
```

So `body["query"]` raises a `KeyError` because `query` lives at `body["body"]["query"]`, not at the top level. The same applies to `body.get("highlight")` — it should be `body["body"].get("highlight")`.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-1N.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
