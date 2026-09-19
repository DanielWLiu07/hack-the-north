# TASK: cloud · Sentry as the room's CI + the cloud path
> **ACTIVE since Sat 12:45 EDT.** The caretaker-roommate plan is the team's goal (`../../../PLAN.md` §0 first).
> **Shared rules:** develop and test on **localhost only** (web http://localhost:8000, landing :8124, devgraph :8125,
> bbsim on loopback ports); bind servers to 127.0.0.1; don't point work at the Vercel/GCP/Tailscale URLs (master deploys).
> Don't commit or push (master batches commits). Don't put assistant or tool names in any file.
> **Open localhost pages in Chrome, never Safari**: `open -a "Google Chrome" http://localhost:8000`; browser tooling uses Chrome/Chromium.

**Goal:** the Sentry story as built, and the laptop → cloud push.
Read: `../03-interfaces.md` §8–9.

| # | task | done when |
|---|---|---|
| 1 | The **room-clean** cron as the CI badge (after the user frees the seat by deleting `watch-loop`); fed by the watch loop's verdict (`robot_sentry.IssueMirror`) | red within 2 passes of a mess; green after the tidy |
| 2 | `room tidy` transactions: `nav.navigate` spans; `nav_failed` / `nav_short` / `map_reset` issues with pose/path context | one real nav failure visible as an issue |
| 3 | `telemetry/hub.py`: optionally consume BB `/ws` → `nav_x/nav_y/nav_yaw` signals | the replay page can draw the SLAM path |
| 4 | `POST /api/edge/event` (bearer token) for laptop → cloud pushes; the `tidy` / `pr_merge` job kinds in `web/jobs.py` | the Vercel dashboard shows live room state |
| 5 | Bridge: "move/put X on/to Y" → a PR proposal (graph-side; Andrew's six verbs stay six) | a sentence becomes an open PR |
| 6 | docs/29: `server_name` is `robot`, not `pi`; the room-clean section matches what's built | a judge's question is answered accurately |
