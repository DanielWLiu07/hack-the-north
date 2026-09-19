# TASK: elastic · the apartment's memory
> **DRAFT: not active.** Activated only when the user approves the roommate reframe (`../README.md`). Until then, keep working on your current TASK file.

**Goal:** the Elastic story is true and demo-able. **No mapping changes are needed** (03 §10); confirm that.
Read: `../03-interfaces.md` §10, `../../PLAN.md` §6.

| # | task | done when |
|---|---|---|
| 1 | Confirm the new `room-events` `event_type` values and the observation / telemetry shapes are accepted by the strict mappings | a dry-run bulk against the `test-` indices is clean |
| 2 | `queries.moved_at(object_id)` (blame: commit, capture, frame) | answers for the mug on real data |
| 3 | `commit_at` production-ready for `room restore --before` (master wires the CLI) | "before dinner" resolves to the right sha |
| 4 | Keep the hybrid search showpiece (`demo_hybrid.py mug`: BM25 misses the cup, the vector finds it) reproducible on the demo data | re-run tonight, output saved |
| 5 | **Pitch hygiene:** remove "Agent Builder" from every Elastic claim (it isn't used); the true line is "an agent whose last tool call moves a real object" | docs/07, 11, 14 fixed (with master) |
