# GITSPACE-13 — AttributeError: module 'builtins' has no attribute 'ELASTIC_URL'

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7742967453/ |
| seen | 1 times, 2026-09-19 21:17 → 2026-09-19 21:17 |
| level | error |
| culprit | `es_shared in queries` |
| Seer run | `16891285` · COMPLETED · stopped at `root_cause` · 23 turn(s), 23 tool call(s) |
| collected | 2026-09-20T07:09:31+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

**Files Seer named:** `web/es_shared.py`

## Root cause, in Seer's words

**Root cause identified.**

The bug lives in `web/es_shared.py`'s `init()` function:

```python
def init(es: Any) -> None:
    _server, _queries = sys.modules[type(es).__module__], None
```

This design infers the server module (which holds `ELASTIC_URL`, `ELASTIC_API_KEY`, `ApiError`) by looking up `type(es).__module__` — the `__module__` of whatever class `es` belongs to. 

**What went wrong:** A script run from stdin (not the normal web server) called `es_shared.init()` with a wrong argument — an instance of a Python built-in type (e.g. a module object, dict, etc.). For any built-in type, `type(obj).__module__` is `'builtins'`, so `sys.modules['builtins']` (the Python builtins module) was stored as `_server`. When `queries()` later accessed `S.ELASTIC_URL`, it failed with `AttributeError` since the builtins module has no such attribute.

The fix should make `init()` accept the server module explicitly as a second argument (or use a different, more robust mechanism) rather than inferring it from the runtime type of the ES client object.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-13.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
