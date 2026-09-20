# GITSPACE-1J — NameError: name 'log' is not defined

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7743391408/ |
| seen | 1 times, 2026-09-20 03:55 → 2026-09-20 03:55 |
| level | error |
| culprit | `room commit` |
| Seer run | `16890654` · COMPLETED · stopped at `root_cause` · 8 turn(s), 8 tool call(s) |
| collected | 2026-09-20T06:09:34+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

> ## ALREADY FIXED
>
> Seer says the fix has already landed — *“**Good news:** This was already fixed in commit `eb01a551` (pushed ~52 minutes after the error at 03:55 UTC), which replaced `log.warning()` with the correct `_warn_once()` call.”*
>
> **Resolve the issue in Sentry rather than editing the file.** Check the commit it names first.

**Files Seer named:** `perception/pipeline.py`

**Commits Seer named:** `ba2dc77`, `eb01a551`

## Root cause, in Seer's words

**Root cause:** In `perception/pipeline.py`, the new pose-validation branch introduced in commit `ba2dc77` called `log.warning(...)` to report that a capture couldn't be registered to the room's frame — but `pipeline.py` has no module-level `log` object. The module's existing logging pattern uses a private `_warn_once()` helper that calls `logging.getLogger("pipeline")` internally. Using the undefined `log` name caused a `NameError` that crashed the entire `room commit` transaction whenever a capture's pose was unknown.

**Good news:** This was already fixed in commit `eb01a551` (pushed ~52 minutes after the error at 03:55 UTC), which replaced `log.warning()` with the correct `_warn_once()` call.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-1J.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
