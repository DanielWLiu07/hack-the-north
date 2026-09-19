# TASK: cloud · Sentry as the room's CI + the cloud path
> **DRAFT: not active.** Activated only when the user approves the roommate reframe (`../README.md`). Until then, keep working on your current TASK file.

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
