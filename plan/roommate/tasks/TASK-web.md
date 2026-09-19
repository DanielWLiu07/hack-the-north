# TASK: web · the roommate dashboard
> **ACTIVE since Sat 12:45 EDT.** The caretaker-roommate plan is the team's goal (`../../../PLAN.md` §0 first).
> **Shared rules:** develop and test on **localhost only** (web http://localhost:8000, landing :8124, devgraph :8125,
> bbsim on loopback ports); bind servers to 127.0.0.1; don't point work at the Vercel/GCP/Tailscale URLs (master deploys).
> Don't commit or push (master batches commits). Don't put assistant or tool names in any file.
> **Open localhost pages in Chrome, never Safari**: `open -a "Google Chrome" http://localhost:8000`; browser tooling uses Chrome/Chromium.

**Goal:** a judge can follow the whole story on a phone: is the room at `main`? what drifted? who? what's the robot doing?
Read: `../03-interfaces.md` §8, `../../PLAN.md` §2 (the demo beats).

| # | task | done when |
|---|---|---|
| 1 | `web/roommate_api.py`: `/api/room/ci`, `/api/chores`, `/api/prs` (+ approve), `/api/blame/{id}`, `/api/nav/snapshot`; SSE `room_state`, `nav`, `chore`, `pr` | tests with mocked roomctl calls; the existing 118 stay green |
| 2 | The dashboard: **CI badge** (green `working tree clean` / red with the drift list) | flips live on bbsim |
| 3 | **Live map:** the BB grid + robot + patrol path + freshness heat (room frame, from the snapshot) | follows the robot in bbsim at ≤ 2 Hz |
| 4 | **PR panel:** open ("move X to zone"), preview ops (reuse the graph preview), approve | approve → a tidy job appears |
| 5 | **Blame card:** the move commit + the capture frame | "who moved the mug" answers with a picture |
| 6 | **Chores** (Tier B) and the roommate copy for every page | the 90 s script reads naturally on the page |
| 7 | Redeploy: `scripts/gcp_mirror.sh ship` + `scripts/deploy_vercel.sh` | the Vercel URL shows it |
