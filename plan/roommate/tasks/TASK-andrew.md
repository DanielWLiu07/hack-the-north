# TASK: Andrew · the words and the edge
> **DRAFT: not active.** Activated only when the user approves the roommate reframe (`../README.md`). Until then, keep working on your current TASK file.

**Goal:** a sentence becomes a reviewed change; the edge can run jobs if it's ready.
Read: `../03-interfaces.md` §7–8, `../../ANDREW-HANDOFF.md`.

| # | task | done when |
|---|---|---|
| 1 | Keep the six verbs; "move/put X on/to Y" is handled graph-side by the bridge (a PR proposal), so your enum doesn't change | agreed |
| 2 | `restore` / `status` / `diff` / `log` keep working against the roommate backend (the same endpoints) | your integration test 1 passes on the Vercel URL |
| 3 | Optional: your edge runs `tidy` jobs via bbapps/nav `/navigate` (roomctl's `BBNavRobot` is the reference and the fallback) | one job with one `job_id` end to end, no double execution |

**Update (your `9582081`, Housebot Edge):** your chain is the primary motion path. Our side will POST
complete `point` jobs (already field-compatible with `point_action_from_daniel_job`) and single-op
`move` jobs to your `/v1/jobs` from the laptop web (LAN), with `HOUSEBOT_EDGE_TOKEN`. Shared asks:
(1) the terminal result shape back to us (your `CaretakerJobResult.to_dict`) is the contract; we render it;
(2) job ids: ours become deterministic per request (as D46's jobs endpoints are), so your in-memory
cache and our ledger agree; (3) your stop gate, the point demo 5× before features, is our demo's
first beat.
