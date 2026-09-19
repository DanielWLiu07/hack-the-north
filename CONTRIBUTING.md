# How we work — read before changing anything structural

Six sessions build in parallel against 24 documents and two published diagrams. **The docs
are the shared memory.** A session that changes the architecture and leaves the docs behind
silently desynchronises the other five, and nobody finds out until integration.

---

## The rule

> **If you change a contract, you update the doc in the same turn. Not later, not "at the end".**

A *contract* is anything another session builds against:

| if you change… | you MUST update | and tell |
|---|---|---|
| an endpoint, its body, or the WS protocol | `docs/16-api.md` | every session |
| an Elasticsearch mapping or index name | `docs/13-ingest.md` + `docs/11-elastic.md` | perception + web |
| a frame, a unit, or a quantization constant | `docs/20-perception-logic.md` | perception + roomctl |
| the object-record YAML shape | `docs/04-git-semantics.md` + `roomctl/state.py` | **everyone** |
| the telemetry signal set or batching | `docs/23-telemetry.md` | cloud + web |
| a segmentation stage's in/out | `docs/15-segmentation.md` | pointcloud |
| what a web route shows | `web/PAGES.md` | — |
| the Sentry tag schema or `obs.py` | `docs/18-sentry.md` | cloud |

**The system-design diagrams are published artifacts** (`docs/system-design.html`,
`docs/diagrams.html`). You cannot republish them — only the coordinating session can. So:

> **Append a line to `DIAGRAM-DRIFT.md` describing what changed and which diagram is now
> wrong.** That file is the queue for republishing.

---

## Also required, every time

1. **`PROGRESS.md`** — append on every completed numbered step. The `Surprise:` line is the
   one that matters; it is where the Devpost's content comes from.
2. **`python3 scripts/audit_architecture.py`** — run it before you say a step is done. It
   checks the invariants that fail *silently*, including whether your code is now newer than
   the doc describing it.
3. **Stay in your folder.** If you need something from another session's folder, write it in
   `NOTES.md` and keep going. Do not edit across the boundary.

---

## Why this is strict

Every invariant in the auditor is there because violating it produces a system that **keeps
running and quietly gives wrong answers** — a cloud rotated 90°, an aggregation over a
tokenised field, a capture ingested against the wrong clock. None of them throw. The docs are
how we agree on which of those we have already ruled out.
