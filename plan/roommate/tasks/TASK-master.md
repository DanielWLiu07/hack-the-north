# TASK: master (integration) · the roommate refactor
> **ACTIVE since Sat 12:45 EDT.** The caretaker-roommate plan is the team's goal (`../../../PLAN.md` §0 first).
> **Shared rules:** develop and test on **localhost only** (web http://localhost:8000, landing :8124, devgraph :8125,
> bbsim on loopback ports); bind servers to 127.0.0.1; don't point work at the Vercel/GCP/Tailscale URLs (master deploys).
> Don't commit or push (master batches commits). Don't put assistant or tool names in any file.
> **Open localhost pages in Chrome, never Safari**: `open -a "Google Chrome" http://localhost:8000`; browser tooling uses Chrome/Chromium.

**Goal:** the critical path, the harness everyone builds against, and integration.
Read: `../01-architecture.md`, `../03-interfaces.md` §1–3, 5–7, 11, `../06-bbsim.md`.

| # | task | done when | phase |
|---|---|---|---|
| 1 | `fake/bbsim.py` (06) + the `scene_gen` coloured-points export | Bracket Bot's own receiver runs unmodified against it; e2e scenarios 1–7 runnable | 1 |
| 2 | `roomctl/frames.py` + `tests/test_frames.py` | golden cases green, including the `h = θ + φ − π/2` table | 1 |
| 3 | `roomctl/bb_nav.py` (mirror, state, area, jobs, `BBNavRobot`) + `tests/test_bb_protocol.py` | decodes recorded fixtures; keeps up with `/heavy`; error mapping table | 1 |
| 4 | `scripts/bb_check.py`, `scripts/bb_record.py` | Gate 1 steps 1–4 automated; 60 s fixtures recorded | 1 |
| 5 | `roomctl/registration.py` + tests | residual < 1 cm on synthetic; re-registers on a `map_gen` change | 2 |
| 6 | `roomctl/policy.py`, `roomctl/watch.py` + tests; `room watch`, `room chores`, `room status --live` | debounce, freshness, heartbeat semantics pinned | 3 |
| 7 | executor → `BBNavRobot` (actual pose, `arrive_tol`, patrol resume, `verify`) | one tidy in bbsim; then **Gate 2** on the real robot | 3 |
| 8 | `roomctl/pr.py` + `room pr …` + tests | propose → approve → tidy; drift then compares to the new spot | 3 |
| 9 | `room restore --before`, `room why` (Elastic showpieces) | both live on real data | 4 |
| 10 | Sentry audit finish + the three stories; the docs sweep (02, bottom); DEMO-RUNBOOK; the 90 s script | only verified claims; Agent Builder removed; `server_name: robot` fixed | 4 |
| 11 | integration: flags, tiers, rehearsal, **backup video** after Gate 2, deployment (`gcp_mirror.sh ship`, `deploy_vercel.sh`) | the 90 s script 3× clean at 00:00 | 4–5 |

**Owns:** roomctl/, fake/, tests/, docs/, PLAN.md, TEAM.md, scripts added here. Merge order: 05.
