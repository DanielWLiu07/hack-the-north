# GITSPACE-19 — ValueError: '192.168.0.30   # the laptop (wifi + tailnet) and this robot itself' does not appear to be an IP…

| | |
|---|---|
| Sentry | https://na-alh.sentry.io/issues/7743074453/ |
| seen | 60 times, 2026-09-19 22:58 → 2026-09-19 22:59 |
| level | error |
| culprit | `http://192.168.0.124:8080/camera/cam0.jpg` |
| Seer run | `16890736` · COMPLETED · stopped at `root_cause` · 7 turn(s), 9 tool call(s) |
| collected | 2026-09-20T07:09:26+00:00 by `scripts/seer_sweep.py` (read from a run that already existed — not re-bought) |

> ## ALREADY FIXED
>
> Seer says the fix has already landed — *“Both fixes are already in the codebase (the `parse()` function in `robot/allow.py` now strips comments; `push_to_pi.sh` now moves inline comments to a separate line before writing the `.env`) — they just weren't deployed yet when the incident occurred on 2026-09-19.”*
>
> **Resolve the issue in Sentry rather than editing the file.** Check the commit it names first.

**Files Seer named:** `robot/allow.py`

## Root cause, in Seer's words

**Root cause identified.**

The bug has two cooperating causes, both documented in the repo's own comments:

1. **The `.env` file had an inline `#` comment on the `ROBOT_ALLOW` line** (e.g. `192.168.0.30   # the laptop (wifi + tailnet) and this robot itself`). This is harmless when the server is hand-started, because python-dotenv strips inline comments. But when started via **systemd's `EnvironmentFile=`**, inline comments are passed verbatim into the environment variable value.

2. **The old `PeerAllowList.__init__` did no comment stripping** — it passed each entry directly to `ipaddress.ip_network()`. The entry `'192.168.0.30   # the laptop ...'` is not a valid network, so it raised `ValueError`.

Because FastAPI/Starlette builds the middleware stack lazily on the first request, this crash happened on **every single request** (60 errors in ~80 seconds), while the process appeared alive and healthy from the outside.

Both fixes are already in the codebase (the `parse()` function in `robot/allow.py` now strips comments; `push_to_pi.sh` now moves inline comments to a separate line before writing the `.env`) — they just weren't deployed yet when the incident occurred on 2026-09-19.

---

Raw run: `~/.cache/gitspace/seer-sweep/raw/GITSPACE-19.json`. Nothing here was applied to the repository: the sweep stops Seer at `root_cause` and never asks it for code changes or a pull request.
