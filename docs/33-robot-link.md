# 33 — The robot link: getting the Pi and the laptop on speaking terms

**One connection, everything rides it.** Camera frames (`POST /capture`, `/frames`), telemetry
(`/stream`), the SSE event stream (`/events`), commands (`/drive` `/arm`) — all of it is TCP to the
Pi on port 8080 ([`16` §8](16-api.md)). This doc is how that TCP connection comes to exist, and how
we know it works. It replaces the "static IPs on our own router" assumption in `16` §8 and
[`02`](02-hardware.md), which held only while both machines sat on a router we owned.

```
   laptop (any wifi) ──┐                                  ┌── Pi (any wifi WITH INTERNET)
   100.117.116.94      ├──  Tailscale: WireGuard, direct  ┤   100.x.y.z  = PI_HOST
                       └──  or relayed (DERP) if NAT'd  ──┘   hostname `bracketbot`
```

## As connected (2026-09-19, 03:55) — read this first

**The link is up and verified against the real robot** (`verify_robot_link.py` exit 0, `mode=hardware`, 9/9
stages incl. a real capture in 0.45 s). What is actually there differs from what §0–§2 assumed:

| assumed | found |
|---|---|
| a Raspberry Pi | **NVIDIA Jetson Orin Nano**, Ubuntu 22.04 aarch64, python 3.10 — `bracketbot@10.37.101.235`, hostname `bracketbot-0183`, passwordless sudo |
| no route to it | both machines are on the **HackTheNorth** venue wifi (WPA2-PSK, no client isolation). `PI_LINK=lan`, `PI_HOST=10.37.101.235`. The robot ALSO runs its own hotspot (`10.42.0.1`) on a second radio |
| we open the cameras | **Bracket Bot's `bbos` camera daemon holds every `/dev/video*`.** We never open them. `robot/bbos.py` READS `bbos` shared memory (`camera.head.jpeg`, `imu.raw`, `imu.orientation`) exactly as their own bbapps do. Nothing of theirs is modified, restarted or installed into |
| Tailscale to be installed | already installed, was logged out. Now joined as `bracketbot` = `100.74.122.43` — **but under `danielliuyes@gmail.com`, while this laptop is on `anonymous.weeb53@gmail.com`.** Two tailnets cannot see each other, so the tailnet path is NOT live yet. Fix: admin console of `danielliuyes@` → Machines → `bracketbot` → ⋯ → Share… → open the link signed in as `anonymous.weeb53@` → `python scripts/pi_link.py discover && python scripts/pi_link.py use tailnet` |

On the robot, everything of ours is in `~/gitspace` (own venv, own `.env` with `ROBOT_*` + the six `SENTRY_*`
settings `obs.py` reads — no tokens, no Elastic/OpenAI keys). On the laptop the hub runs in tmux window `hub`
(`python -m telemetry.hub --no-rerun`): robot → Elasticsearch + Sentry + the dashboard's `/api/events`.

**Until the share is accepted the link depends on the venue wifi.** If the robot's address changes (DHCP):
`hostname -I` on it, put the new value in `PI_HOST_LAN`, `python scripts/pi_link.py use lan`, restart the hub.

**What happened next, and the lesson (2026-09-19 04:17 → 08:50).** The laptop left the HackTheNorth wifi (first a
`192.168.2.x` router, then campus `10.36.x`). The robot's `10.37.101.235` is only routable from that one wifi, the tailnet
share above was never accepted, and the link was DOWN for hours: hub `connected:false`, no telemetry, no camera. Nothing
was broken — this is exactly the dependency the share removes. `scripts/robot_sentry_watch.py` (tmux `robot-watch`) now
files that outage to Sentry as `robot_unreachable` within 15 s and `/telemetry` says so on its first card.

## 0. The problem, as found (2026-09-19)

The laptop is on campus wifi (`10.36.x`), `.env` said `PI_HOST=192.168.2.10`: two private networks
with no route between them — 100% packet loss, whatever the protocol. That `192.168.2.10` is also
byte-identical to `.env.example`, so it may never have been a real address. Tailscale fixes the
general case: each machine gets one `100.x` address that follows it across networks, NATs and
client-isolated wifi. The laptop was already on a tailnet. The Pi was not — and to install Tailscale
on the Pi you must reach the Pi once. That is the whole difficulty; §1 is the way through it.

**The standing requirement, not only for install day: the Pi needs internet of its own.** Tailscale
on the Pi talks to the coordination server and the relays itself. A phone hotspot is enough. The
robot's own access point is not — it has no uplink.

## 1. The way in — pick ONE (the only step that needs a human near the robot)

| | how you get a shell on the Pi | needs | laptop's internet | then run |
|---|---|---|---|---|
| **A** | **someone already has one** (whoever runs the balance loop / collector) | the key, sent to them | kept | `provision_pi_tailscale.sh --print --wifi "SSID" "pw"` → they paste the block |
| **B** | USB keyboard + **micro**-HDMI monitor on the Pi | the peripherals | kept | same `--print` block; or `--qr` and a phone instead of typing the key |
| **C** | ethernet cable laptop ↔ Pi, `ssh <user>@<hostname>.local` | USB-C ethernet adapter + cable | kept (wifi stays the default route) | `provision_pi_tailscale.sh <user>@<hostname>.local --wifi "SSID" "pw"` |
| **D** | join the robot's AP / travel router, `ssh <user>@<its-ip>` | that network to exist | **lost for ~5 min** | same as C — it runs detached, because the Pi changing wifi cuts your SSH |

Recommended: **A** if anyone has a shell, else **B**. Both keep the laptop online and neither depends
on a network we have not seen. **D** works but is the only one that costs the laptop its internet
(Elastic, Sentry, OpenAI) while it runs, and it runs blind: the script rolls the Pi back to the
network it was on if the new wifi fails, and you read `~/provision_tailscale.log` on the Pi after.

Not an option: macOS Internet Sharing from campus wifi to the Pi over ethernet. It would hand the Pi
both an address and internet, but macOS refuses to share an 802.1X (eduroam) connection — *believed,
not tested on this laptop.* Over a phone hotspot or home wifi it works, and then `192.168.2.x` is
exactly the subnet it hands out.

The Pi's SSH user is not written down anywhere in this repo. The scripts make you say it; they do
not guess.

## 2. The auth key — why, and exactly how

A normal `tailscale up` prints a URL to open in a browser. On a headless robot that is a problem, and
in path **D** it is impossible (the session is gone). An **auth key** joins a node with no browser,
and lets a teammate run the join without ever seeing your Tailscale login.

1. <https://login.tailscale.com/admin/settings/keys>, signed in as **the account this laptop's tailnet
   belongs to** — `tailscale status` shows it next to every machine. A key from any other account
   joins the Pi to a *different* tailnet, where the laptop will never see it.
2. **Generate auth key…** → Reusable **off** · Expiration 1 day · Ephemeral **off** · no tags ·
   Pre-approved **on** if the toggle is shown.
   - *Ephemeral off* is the one that matters: an ephemeral node is deleted whenever it goes offline
     and comes back with a **new** `100.x` — `PI_HOST` would rot every time the robot is power-cycled.
   - *Reusable off*: the key dies on first use, so a copy left in a shell history or a chat is inert.
   - The key's expiry is only the deadline for *using* it. Once joined, the Pi stays joined (node keys
     last 180 days; Machines → `bracketbot` → ⋯ → *Disable key expiry* if this outlives the weekend).
3. It is shown once. Save it **outside the repo and outside `.env`** (`deploy_web.sh` ships `.env` to
   the EC2 box):
   ```bash
   mkdir -p ~/.config/gitspace && pbpaste > ~/.config/gitspace/ts-authkey && chmod 600 ~/.config/gitspace/ts-authkey
   ```
4. Run §1's command. What it does on the Pi, in order: fixes the clock if it is wrong (no RTC battery →
   TLS fails as "certificate not yet valid", which looks like a network fault), joins the wifi, checks
   for real internet, installs Tailscale, `tailscale up --auth-key=file:… --hostname=bracketbot
   --accept-dns=false --accept-routes=false`, deletes the key and the wifi password. Already joined →
   it changes nothing and the key is not spent.

A teammate's laptop needs the robot too? Machines → `bracketbot` → ⋯ → **Share…** They see that one
machine from their own account; nothing else of yours.

## 3. `PI_HOST`: one name, two addresses, one command

Everything reads `PI_HOST` / `PI_PORT` from `.env` and nothing else had to change. `.env` now keeps
both pairs, and `scripts/pi_link.py` repoints the live keys:

```bash
python scripts/pi_link.py status          # what is selected; what actually answers, on BOTH
python scripts/pi_link.py discover        # find bracketbot on the tailnet -> PI_HOST_TAILNET
python scripts/pi_link.py use tailnet     # or: use lan.  PI_HOST + LAPTOP_IP + RERUN_ADDR move together
python scripts/pi_link.py auto            # whichever answers, tailnet first
```

- `LAPTOP_IP` / `RERUN_ADDR` switch with it: the Pi streams Rerun *to the laptop* over the same link.
  The Pi's own `.env` needs the matching `RERUN_ADDR`; `status` prints the line.
- **`PI_HOST` is an IP, never `bracketbot`.** This laptop runs the open-source `tailscaled`, and macOS
  does not route `*.ts.net` to Tailscale's resolver: `bracketbot` fails to resolve, and the laptop's
  own name resolves to a *public* Funnel address (measured: `199.38.181.54`). `discover` asks
  `tailscaled` directly, which needs no DNS. `scripts/check_keys.py` still wants a dotted quad — correct.
- Processes read `.env` once, at start. After a switch, restart the hub and anything else running.

## 4. Code onto the Pi

There was no way for `robot/` to reach the Pi. Now:

```bash
./scripts/push_to_pi.sh <user>@<PI_HOST>                 # rsync robot/ + obs.py, venv, deps, import check
./scripts/push_to_pi.sh <user>@<PI_HOST> --start --sim   # FIRST: the simulated robot, on the Pi
./scripts/push_to_pi.sh <user>@<PI_HOST> --start         # then the real cameras (--hardware)
./scripts/push_to_pi.sh <user>@<PI_HOST> --log | --stop
```

Start with `--sim` on the Pi: it proves the link with nothing else in the way. If that verifies and
`--hardware` does not, the fault is cameras or `pyrealsense2`, not the network. `.env` is never
pushed — it holds the Elastic and OpenAI keys, and only the laptop talks to those (`16` §4).

**Decision for whoever runs the demo:** the Pi's API has no auth and includes `/drive` and `/arm`
(`16` §9). On `0.0.0.0` it is offered to every device on whatever wifi the Pi joined. Once the
tailnet link is verified, put `ROBOT_HOST=<the Pi's 100.x>` in the Pi's `.env` so only tailnet members
reach it. The cost: no LAN fallback until you set it back.

## 5. Proof

```bash
.venv/bin/python scripts/verify_robot_link.py            # ~40 s.  --soak 120 for a real hold; --capture to move MB
./scripts/watch_robot_link.sh                            # live pane: both addresses, direct vs relayed, SSE rate
```

Stages, in the order a link fails: **path** (direct WireGuard / relayed via DERP / LAN, with RTT) ·
**healthz** (which robot: hardware / sim) · **sse** (framing, `hello` first with the clock pairing,
ids `<boot>:<n>` contiguous, Pi clock vs ours) · **resume** (`Last-Event-ID n` → next is `n+1`, replay
joins live, no gap, no duplicate) · **restart** (an id from another boot gets `gap boot_changed`,
never a silent resume) · **idle** (a quiet stream still carries keepalives) · **soak** (held N
seconds, every id accounted for, no silence > 1.5 s) · **ws** (`/stream`, what `telemetry/hub.py`
consumes) · **capture** (opt-in: one real `POST /capture`).

The verdict is about the **link**, not the endpoint: exit `0` = verified against another machine ·
`2` = every check passed but the target was this laptop, so nothing crossed a network · `1` = failed,
read the first failure. The verifier was itself tested against a stand-in that breaks the contract one
way at a time (duplicate / gap on resume, dead socket, wrong content type, silent resume across a
reboot, no clock pairing, no keepalive, bare-integer ids, a 2.6 s mid-stream silence): 9 of 9 caught,
each for the right reason.

**Relayed is fine for events, slow for pictures.** `path` says `RELAYED via DERP(...)` when neither
NAT lets a direct WireGuard path form (campus wifi ↔ cellular hotspot often). Telemetry and SSE do not
care. A 5 MB capture does: if `--capture` is slow, put the laptop on the **same** wifi as the Pi — the
addresses do not change, Tailscale just finds the short path.

## 6. When it breaks at 4am

| `watch_robot_link.sh` says | it means | do |
|---|---|---|
| `no node named bracketbot` | the Pi never joined | §1 |
| `bracketbot … offline` | Pi is off, or lost its internet (hotspot asleep? phone left the room?) | wake the hotspot; the Pi rejoins by itself |
| `PINGS, :8080 closed` | link is fine, `robot.server` is not running | `push_to_pi.sh … --start`, then `--log` |
| `ANSWERS` on one row, the other selected | `.env` points at the wrong pair | `pi_link.py use <that one>` |
| `/healthz answers but /events sent nothing` | the Pi's `robot/` predates `/events` | `push_to_pi.sh` |
| verify: `tailscale up was refused` (in the Pi log) | key expired, used, or from another account | §2, new key |
