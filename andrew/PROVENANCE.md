# Where this came from

Everything in this directory except this file is **Andrew Zheng's work**, copied from his
repository so the whole chain can be deployed from one place. Nothing of his has been edited,
renamed or re-authored, and his own README and docs are as he wrote them.

| | |
|---|---|
| upstream | https://github.com/awzheng/gitirl |
| commit | `7a31596` — "Add outbound Daniel cloud client and live contract guards" |
| copied | 2026-09-20, into `andrew/` |
| excluded | `.git`, `__pycache__`, `*.pyc` — nothing else |

**It is a copy, not a fork we intend to change.** If his side moves, re-copy rather than patch
here: a fix made in this directory is a fix he will never see, and the contract between us
(`ANDREW-HANDOFF.md`, `plan/roommate/03-interfaces.md` §12) only holds while both sides run the
same code. If something of his needs changing, it goes to him.

## What it is

The **Housebot Edge**: the service our dispatcher posts a job to (`POST /v1/jobs`, one operation
per job, bearer token), which then drives the robot's adapter. `web/housebot.py` is our side of
that call; his `src/gitirl_agent/` is his. He also carries `docs/CLOUD_CONTRACT.md`, which is our
contract written from his side.

It needs no third-party packages — his edge and robot HTTP are standard library — so it runs
under the same interpreter as everything else here.

## How it runs in the deployment

On the public box it runs on loopback only, with our **simulated** robot adapter behind it, so a
job dispatched from the public panel is carried out by a simulation and says so in its answer.
The real robot is never reachable from that machine: it lives on a home network, and a public URL
that can move a physical robot is not something we want to exist.
