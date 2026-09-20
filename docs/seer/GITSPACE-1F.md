# GITSPACE-1F — KeyError: 'detail'

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7743190798/ |
| seen | 1 times, 2026-09-20 00:48 → 2026-09-20 00:48 |
| level | error |
| culprit | `/api/agent/command` |
| Seer run | `16891306` · COMPLETED · stopped at `root_cause` · 8 turn(s), 7 tool call(s) |
| collected | 2026-09-20T07:09:31+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

> ## ALREADY FIXED
>
> Seer says the fix has already landed — *“**Note:** The code in the repo already has this fixed — both a `kind == "confirm"` branch was added (line 239–240) and the catch-all was changed to `r.get("detail")` (line 253, with a comment referencing this very Sentry issue). The error occurred before that fix was deployed.”*
>
> **Resolve the issue in Sentry rather than editing the file.** Check the commit it names first.

**Files Seer named:** `bridge/agent_api.py`

## Root cause, in Seer's words

**Root cause identified.**

When the `confirm` action kind was introduced (to handle ambiguous object matches scoring between 1.11–1.20 in Elasticsearch), the `_outcome()` function in `bridge/agent_api.py` wasn't updated to handle it. The command "pick up the trash" matched multiple objects with a narrow margin (1.065 vs 1.013), triggering a confirmation flow. The resulting action had `kind="confirm"` and a result dict with `question`, `yes`, `no` fields — but no `detail` key. Without a `confirm` branch, `_outcome()` fell through to the catch-all `return "REFUSED", r["detail"]`, which raised `KeyError: 'detail'`.

**Note:** The code in the repo already has this fixed — both a `kind == "confirm"` branch was added (line 239–240) and the catch-all was changed to `r.get("detail")` (line 253, with a comment referencing this very Sentry issue). The error occurred before that fix was deployed.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-1F.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
