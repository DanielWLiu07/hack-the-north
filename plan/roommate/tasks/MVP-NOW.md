# MVP now, without the robot

Status 2026-09-19 18:40Z. The robot has been off the network since ~18:00Z, so the MVP runs
end to end on what exists: `fake/bbsim.py` stands in for the robot's nav server and arm, and
`robot/adapter.py --sim` stands in for the robot behind Andrew's edge. The moment the robot is
back, the same loop points at it instead (Gate 1/2 in 05-rollout). Nothing here changes a
contract.

Rules as before: localhost only, Chrome (not Safari), no commits (master commits), no assistant
or tool names anywhere, never write simulated data into `room.git` or into the shared Elastic
indices as if it were real.

## Ports

| what | where |
|---|---|
| real site (room.git, real history, Elastic beats) | `http://127.0.0.1:8000` |
| sim site (the sim room instance, bbsim's live map) | `http://127.0.0.1:8001`, `ROOM_GIT_PATH=~/.cache/gitspace/rooms/sim-demo` |
| bbsim | ws `127.0.0.1:18010`, api `127.0.0.1:18020` |
| robot adapter, simulated (`"simulated": true` in /health) | `127.0.0.1:8765` |
| Andrew's Housebot Edge, run locally | loopback, port of its choosing |

## The five beats

1. **The room is `main`.** The sim site shows the CI badge green, the live map with the robot
   patrolling, and no chores. (gitspace-22, web-64)
2. **A roommate makes a mess.** One command moves the mug in bbsim. The loop confirms the move on
   two fresh passes, Tier A tidies it with the sim arm, and a clean rescan verifies the job. The
   badge goes red, then green, and the connection graph lights "verified by rescan".
   (gitspace-22, web-64)
3. **"I meant that."** Move the lamp, click "I meant that" to open an `as_seen` PR, approve it,
   and the robot leaves it alone. (web-64)
4. **Ask the room.** In the Room Agent panel on :8000, "where are my keys" (hybrid search), "put
   it back the way it was before dinner" (`room restore --before`, ES|QL `commit_at`) and
   "why was this diff wrong" (`room why`: the capture gate, telemetry and the Sentry trace). All
   of these run on real room.git history. (elastic-09, web-64)
5. **Andrew's part.** A sentence goes to the intent service, the resolver finds the object in
   Elastic, a `point` job goes through Andrew's edge to the simulated adapter, and the result
   comes back to the panel. Where Andrew's OpenAI service isn't running yet, our grammar path
   produces the same intent, and his service replaces it later without other changes.
   (gitspace-d2, gitspace-5e, elastic-09)

## Owners

- **gitspace-22.** `scripts/demo_sim.py`: one command starts bbsim, seeds `rooms/sim-demo` from
  a scene, starts `room watch --tier A` (ROOM_ARM=sim, ROOM_WEB_URL=:8001) and the :8001 site.
  It also has `mess <object>` / `decide <object>` / `reset` subcommands for the beats, and a
  scripted run that asserts beats 1 to 3 end to end.
- **web-64.** Beats 1 to 4 click through in Chrome on :8001 and :8000. Write the click path
  down in PROGRESS.md. Also review c6's `pages/room-map.js` on /robot.
- **gitspace-d2.** Beat 5: pull awzheng/gitirl, run the edge locally against the simulated
  adapter, and point :8000's `HOUSEBOT_EDGE_URL`/`TOKEN` at it in `.env` (localhost only, never
  on the GCP box). Check "point at the mug" from the panel, with the trace continued into
  Sentry. Also find out why `odom_residual` stopped at 07:07Z.
- **gitspace-5e.** Run `robot/adapter.py --sim` on 127.0.0.1:8765 in its own tmux window, so
  the simulated flag is visible in /health and every result. Write the arm-reach measurement
  steps for when the robot is back. The procedure moves nothing.
- **elastic-09.** `resolve_object(text)` in `elastic/queries.py` for the resolver (hybrid plus
  rerank, returning object ids with scores and margins). This is the Elastic half of Andrew's
  resolver: his service calls it, and we call it until then. Add a smoke check that runs every
  Elastic demo beat against live data and prints pass/fail. Put `robot_failure` in docs/11's
  vocabulary.
- **perception-02 and perception-f5.** Fix the missing room-observations for captures after
  06:30 (the capture path writes clouds only), then add `bb_map` camera docs.
- **gitspace-c6.** Robot link: waiting on the robot, then push with no units and run the
  acceptance scan facing the table. Until then, the real saved map goes on /robot and /scene.
- **gitspace-68.** Dashboard performance in Chrome on the pages the beats use.
