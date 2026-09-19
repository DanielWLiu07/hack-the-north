# `scripts/`

| file | purpose |
|---|---|
| `bootstrap_pi.sh` | Clone BB quickstart, install deps, copy `robot/`, install the systemd unit, set static IP. **Not written** — `push_to_pi.sh` below is how `robot/` reaches the Pi today. |
| `provision_pi_tailscale.sh` | **The robot link, step 1** (docs/33): join the Pi to the tailnet with an auth key — no browser on the robot. Over SSH, or `--print` a block for a teammate's shell / a keyboard. `--wifi "SSID" "pw"` gives the Pi internet first and runs detached, rolling back if the join fails. Then records the Pi's `100.x` and switches `.env` to it. |
| `pi_link.py` | `PI_HOST` switchable between LAN and tailnet: `.env` keeps both address pairs, `use lan\|tailnet` repoints `PI_HOST` + `LAPTOP_IP` + `RERUN_ADDR` together, `status` probes both, `discover` finds `bracketbot` without DNS, `auto` picks whichever answers. No consumer changed. |
| `push_to_pi.sh` | rsync `robot/` + `obs.py` to the Pi, build a `--system-site-packages` venv (BB's `cv2`/`pyrealsense2` stay visible), install `requirements-pi.txt`, import-check. `--start [--sim]` / `--stop` / `--log`. Never pushes `.env`. |
| `verify_robot_link.py` | **The proof** that the link works: path (direct / DERP-relayed / LAN) · `/healthz` · SSE framing + clock pairing · `Last-Event-ID` resume with no gap or duplicate · reboot detection · keepalives · an N-second soak · `/stream` · optional real capture. Exit `0` only against another machine; `2` = passed but the target was this laptop. |
| `robot_sentry_watch.py` | **Sentry watching the robot from outside** — the failures a process cannot report about itself. Every 5 s: unreachable · server down · camera unavailable · IMU feed null (`telemetry_unfed`: every capture would be rejected) · tap stalled / starved · source errors · dropping · clock skew · restarted. Debounced; ONE issue per incident (`obs.robot_failure`, last 40 health snapshots as breadcrumbs + the last frame the camera sent), and `… recovered after N s` when it clears. No cron monitor — the plan's one belongs to the watch loop. Probes are `sentry-trace …-0` (unsampled). State → `~/.cache/gitspace/robot-watch.json`, which `/telemetry` shows. `--once`, `--dry-run`. Runs in tmux `robot-watch`. |
| `watch_robot_link.sh` | Live tmux pane: both addresses, is `bracketbot` on the tailnet and direct or relayed, SSE event rate, and the one command that fixes whatever is wrong. Read-only. |
| `bootstrap_laptop.sh` | venv, deps, pre-download model weights, `.env` check, then proves the stack runs offline. **Run tonight — venue wifi cannot be trusted for large downloads.** `--env-only` re-checks keys after a booth; `--offline` rebuilds the venv from the uv cache at the venue. |
| `requirements-laptop.txt` | The laptop venv. Tracks add their own `<folder>/requirements.txt` instead of editing this; the bootstrap installs those too. |
| `check_offline.py` | Blocks the network in-process, then loads every YOLO weight **by bare name**, runs SGBM → cloud → plane removal, writes an `.rrd`. Anything that lazily downloads fails here instead of at the venue. |
| `deploy_web.sh` | The AWS web tier (docs/19): t4g.small in us-east-1, Caddy + Let's Encrypt on `<ip>.sslip.io`, IAM role (no keys on the box), S3 bucket with 7-day expiry, then the Uptime monitor. **Plan mode by default — no AWS calls.** `--apply --account <id>` refuses any other account; `--ship` re-syncs code; `--teardown`. |
| `named_tunnel.sh` | The STABLE public URL: named Cloudflare tunnel `gitspace` → `repr.ink` (fallback `gitirl.ink`), config in `~/.cloudflared/gitspace.yml`, launchd so it survives restarts, then Uptime + `WEB_PUBLIC_URL`. Needs the one-time manual steps in docs/19 first. |
| `uptime_tunnel.sh` | Stopgap public URL for Sentry Uptime: a Cloudflare quick tunnel to the laptop's web server. The URL changes on restart — re-run it; it re-points the same monitor. |
| `sentry_uptime.py` | Create or re-point the ONE uptime monitor ("gitspace web") at a URL. Used by both of the above. |
| `gcp_mirror.sh` | **The cloud web tier** (docs/19 "As built"): GCP VM `gitspace-web` in `us-east4`, Caddy on `8-234-158-138.sslip.io`. `status` · `sync-room` · `sync-bg` (what room.git's hooks call; coalesced, never blocks git) · `hooks` · `ship` · `logs` · `stop` / `start`. Auto-stops 2026-09-21 12:00Z; CA$10 budget. |
| `deploy_vercel.sh` | **The frontend**: landing/ + pages/ assets to Vercel (`gitspace-five.vercel.app`), every other path proxied to the GCP tier. Same origin, no page changes. |
| `demo.sh` | Start Rerun viewer, start the agent, open the terminal with big fonts, put the LED in `clean`. One command, so nobody fumbles at judging. |
| `snapshot.sh` | Tag a known-good `room.git` + save the Rerun `.rrd`. **Run it the moment the first end-to-end run works**, and after every improvement. |

`snapshot.sh` is cheap insurance: never let the only working version be the one you're
currently editing.

## Model weights — the contract

Weights live in `$MODELS_DIR` (default `~/.cache/gitspace/models`), **outside the repo**.
The bootstrap drops a `gitspace_env.pth` into the venv that sets `YOLO_CONFIG_DIR` and
`YOLO_OFFLINE=1` for every Python process, so BB-style code works unchanged and offline:

```python
YOLO("yolo11s-seg.pt")   # resolves from $MODELS_DIR/weights — no network, no cwd copy
```

`YOLO_OFFLINE=1` means a weight that was never downloaded **fails fast** instead of hanging on
venue wifi. To fetch a new one on a good network: `YOLO_OFFLINE=0 python -c 'from ultralytics
import YOLO; YOLO("yolo26l-seg.pt")'`, or add it to `YOLO_WEIGHTS` and re-run the bootstrap.

SAM 3 is meant to run on **Baseten**. `--with-sam3` pulls `sam3.pt` (gated, ~3.5 GB) plus the
CLIP tokenizer that Ultralytics would otherwise `pip install` from GitHub at runtime.
