# 09 — Schedule

Hack the North 2026 runs **Fri Sep 18 → Sun Sep 20** at E5/PSE, Waterloo. Hacking is
~36 hours. Times below are relative to hacking start (`H+0`); adjust once the real schedule
drops.

## Tonight (Thu Sep 17) — before you're anywhere near the venue

This is the highest-value block of the whole project, because everything here is a thing
that would otherwise cost double at 3am on hackathon wifi.

- [ ] **Buy / borrow**: 2× matching USB stereo camera modules, powered USB3 hub, travel
      router, gaffer tape, textured tablecloth, the object set. See
      [`02-hardware.md`](02-hardware.md#bring--buy-list-do-this-today-2026-09-17).
- [ ] **Print** the ChArUco board + AprilTags on rigid board. Multiple sizes.
- [ ] **Pre-download everything**: model weights (YOLO, SAM 3, whatever VLM), pip wheels
      for the Pi (`opencv`, `open3d`/`pcl`, `rerun-sdk`, `scipy`, `lerobot`,
      `feetech-servo-sdk`), and clone `BracketBotCapstone/quickstart` locally. **Assume
      the venue network cannot be relied on for large downloads.**
- [ ] **Install Rerun** on the laptop and get *any* 3D thing rendering, so we know it works.
- [ ] **Read** `examples/example_depth.py` and `examples/example_localization.py` properly.
      They're the two files we're building on.
- [ ] **Skeleton the code** — the six robot endpoints and the `room` CLI with all commands
      stubbed. Writing argparse at hour 3 is a waste of hour 3.
- [ ] **Write a fake scene generator**: hand-authored object YAML → git repo → `git diff`.
      Proves the git layer end-to-end **before we have a robot at all.**
- [ ] Split roles. Suggested four-way: **[Perception]**, **[Robot: drive + arm]**,
      **[Git/CLI + demo screen]**, **[Agent + Elasticsearch]**. The last track is pure
      laptop work and must never touch the robot — that's what makes it free (see
      [`11-elastic.md`](11-elastic.md#the-two-conditions)). With only 2–3 people, cut it
      and fold the CLI into perception. Whoever floats owns the Devpost + video.
- [ ] **Spin up Elastic tonight — Serverless, Elasticsearch project type.** Agent Builder
      is GA and on-by-default on Serverless; on Hosted it needs Stack 9.3+/Enterprise tier
      and may simply not be there. Full checklist:
      [`11-elastic.md`](11-elastic.md#setup--do-this-tonight-verified-2026-09-17).

## H+0 → H+3 — Foundations, in parallel
| track | task |
|---|---|
| all | own network up, static IPs, Pi reachable, Rerun streaming from Pi → laptop |
| robot | BB setup: `setup_os.sh`, `calibrate_drive.py`, `example_wasd.py` driving |
| perception | `example_depth.py` running; point cloud in Rerun; tune SGBM on the real table |
| git | `room init/status/diff/commit/log` working against **fake** scene YAML |
| agent/ES | indices + mappings created; dual-write the **fake** objects; hybrid search returns something |

**H+3 gate:** robot drives, a point cloud renders, `room diff` prints a real git diff of
made-up objects. *Three independent things working beats one integrated thing half-working.*

**Note for the agent/ES track:** you are unblocked from hour 0 because the fake scene
generator produces the same YAML shape the real pipeline will. Build the whole retrieval
and agent-tool layer against fake data and swap the source later. Do not wait for the robot.

## H+3 → H+9 — First real objects
- Plane removal + clustering + bboxes from the real cloud.
- AprilTag anchor + `solvePnP` relocalization; fuse with wheel odometry.
- Serialization + quantization + stable IDs.
- **Write `test_idempotent_scan.py` here.** Scan twice, expect an empty diff. Iterate on
  quantization/hysteresis until it's green. Do not proceed until it is.

**H+9 gate:** scan the table twice without touching it → `git diff` is empty. Move one
object → diff shows exactly one modified file. **This is the most important gate in the
schedule.** If this isn't green, everything downstream is theatre.

## H+9 → H+14 — T0 complete
- Object labeling (start with the stock YOLO example).
- Association/Hungarian matching; added / removed / moved / unobserved.
- Robot navigates to a changed object and announces it.
- Rerun blueprint v1: cloud + boxes + diff colours + text panel.
- LED status colours.

**H+14 gate: demo T0 to a stranger at another table.** Watch where they get confused.
That's your pitch feedback, for free, 30 hours before judging.

## H+14 → H+22 — T1: the arm writes
- SO-101 comms via LeRobot; calibrate; stow pose.
- Top-down IK; grasp + place primitives; test on a fixed base first.
- Mount on the robot; balance tuning; slow profiles.
- Executor: diff → ordered ops (topological sort + staging position for cycles) → motion.
- `revert` and `checkout` execute physically. Verify-and-retry via rescan.

**H+18 balance kill-check** and **H+22 grasping kill-check** — see
[`08-risks.md`](08-risks.md). Be disciplined about these; the fallbacks are good.

## H+22 → H+28 — T2 + T3, pick by what's healthy
- Mount cams 2 and 3; extrinsic calibration (**timeboxed to 2 h**); single-shot 360°.
- Multi-zone repo layout.
- Branch/checkout/merge; the staged conflict beat; ghost rendering in Rerun.
- Voice control via the existing Realtime example.
- Elastic: observation logging (the mess), occlusion-vs-deletion query, the
  `room search "where are my keys"` beat, ES|QL time travel.
- Cheap prize bolt-ons **only if** T2 is done.

**Saturday ~12:00 — lock the sponsor prize list** (submission closes 2:00 PM EDT).

## H+28 → H+34 — Freeze, rehearse, record
**No new features after H+28.** Non-negotiable.
- Tag a known-good `room.git` + save a known-good Rerun `.rrd`.
- Run the full demo end-to-end **five times**. Log every failure; fix only the ones that
  break the demo.
- Record the 60-second video in one unbroken take, judge's-eye framing.
- Write the Devpost: source links, the room repo as an artifact, badge IDs, prize selections.
- Rehearse the 3-minute and 60-second pitches out loud. Decide who speaks.

## H+34 → judging — Sleep, then perform
The pitch is a live performance. The person delivering it should be the best-rested person
on the team. Set up the table early: tablecloth, objects at committed positions, anchor
tag taped, LED on, Rerun and terminal in big fonts, command table printed and laid out.

## Standing rules
1. **Every tier must be independently demo-able before the next one starts.**
2. **Anything that doesn't make the demo better is a distraction** (unless <1 h and it wins
   a prize).
3. **Commit the working version before you try the risky thing.** Yes, the irony is noted.
4. **When stuck for 45 minutes, switch tasks or ask the Bracket Bot booth.** They want a
   great demo built on their hardware as much as we do — talk to them early, not at hour 30.
