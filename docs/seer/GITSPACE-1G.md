# GITSPACE-1G — robot: object_not_found — nothing in the room answers 'pick up the trash' (best 1.029 < 1.05)

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7743223741/ |
| seen | 4 times, 2026-09-20 01:21 → 2026-09-20 04:17 |
| level | warning |
| culprit | `elastic.read-path-proof` |
| Seer run | `16890897` · COMPLETED · stopped at `root_cause` · 10 turn(s), 12 tool call(s) |
| collected | 2026-09-20T06:09:32+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

**Files Seer named:** `elastic/queries.py`

## Root cause, in Seer's words

**Root cause:** The error is **intentional, working-as-designed behavior**.

The query `"pick up the trash"` is sent to `resolve_object`, which runs a hybrid Elasticsearch search (BM25 + Jina dense reranker) against the `room-objects` index. The best match returned is `cup_7e21` (a ceramic cup) with a reranker score of **1.029**, which falls below the calibrated `MIN_RELEVANCE = 1.05` threshold in `elastic/queries.py`.

This threshold was specifically measured (2026-09-19) to sit in the gap between:
- **Present objects**: score 1.074–1.572
- **Absent phrasings**: score 0.962–1.131 — with `"pick up the trash"` at **1.029**

Since no trash object exists in the room's object index, the system correctly classifies the query as `object_not_found` and reports it as a warning to Sentry. The code comment explicitly notes this is expected: *"so without this, 'pick up the trash' resolves to a ceramic cup"* — which would cause a robot gripper to incorrectly bin the cup.

The Sentry issue exists as a feed of commands the room cannot answer (objects worth teaching it about), not as a bug to fix.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-1G.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
