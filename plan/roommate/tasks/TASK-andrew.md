# TASK: Andrew · the words and the edge
> **DRAFT: not active.** Activated only when the user approves the roommate reframe (`../README.md`). Until then, keep working on your current TASK file.

**Goal:** a sentence becomes a reviewed change; the edge can run jobs if it's ready.
Read: `../03-interfaces.md` §7–8, `../../ANDREW-HANDOFF.md`.

| # | task | done when |
|---|---|---|
| 1 | Keep the six verbs; "move/put X on/to Y" is handled graph-side by the bridge (a PR proposal), so your enum doesn't change | agreed |
| 2 | `restore` / `status` / `diff` / `log` keep working against the roommate backend (the same endpoints) | your integration test 1 passes on the Vercel URL |
| 3 | Optional: your edge runs `tidy` jobs via bbapps/nav `/navigate` (roomctl's `BBNavRobot` is the reference and the fallback) | one job with one `job_id` end to end, no double execution |
