# 35 — Reaching the robot, and what it can actually do right now

Measured on `bracketbot-0183`, **2026-09-19 20:40 EDT**. Everything below was run, not assumed.
Supersedes the addresses in [`33-robot-link.md`](33-robot-link.md); that doc's *reasoning* still stands.

## 1. How to get in — three commands

```bash
ssh bb                      # or: ssh bracketbot        (~/.ssh/config, added 2026-09-19)
curl http://192.168.68.63:8080/healthz          # the robot's HTTP server, direct
python scripts/pi_link.py status                # is the link up, from the laptop's point of view
```

`ssh bb` resolves **`bracketbot-0183.local` over mDNS**, which survives DHCP — the IP does not.
`ssh bracketbot-ip` is the same host pinned to `192.168.68.63` if mDNS is being slow.
Both only work **from the same network as the robot**; there is still no route from anywhere else (§5).

The alias also opens `localhost:8081 -> robot:8080` automatically. That was the way in before the
allow-list was fixed, and it still is the way in whenever the laptop's IP changes:

```bash
ssh -fN bb && curl http://127.0.0.1:8081/healthz     # 127.0.0.1 is permanently in ROBOT_ALLOW
```

**Why a tunnel at all.** The robot's server answers only addresses in `ROBOT_ALLOW` and replies
`403 {"error":"forbidden","detail":"this address is not in ROBOT_ALLOW"}` to everything else. The
list had two dead laptop IPs in it (`10.37.122.164` from the venue wifi, `192.168.0.30` from the
travel router) and not the current one. It now reads:

```
ROBOT_ALLOW=127.0.0.1,192.168.68.83,10.37.122.164,100.64.0.0/10,192.168.0.30
```

`192.168.68.83` is **this laptop today**. On a new network, add the new IP and restart the server
(§6), or just use the tunnel and change nothing.

## 2. Addresses

| what | value | note |
|---|---|---|
| hostname | `bracketbot-0183` | mDNS `bracketbot-0183.local` works on-LAN |
| **LAN (use this)** | `192.168.68.63/22` on `wlan_usb` | default route via `192.168.68.1` |
| laptop | `192.168.68.83` | same subnet — this is why it works today |
| robot's own AP | `10.42.0.1/24` on `wlP1p1s0` | no uplink; joining it costs you the internet |
| USB gadget | `192.168.55.1/24` on `l4tbr0` | only over the USB-C cable |
| tailnet | `100.74.122.43` | **still unusable** — see §5 |
| HTTP server | `:8080`, bound `0.0.0.0` | gated by `ROBOT_ALLOW` |
| bbos adapter | `:8765`, bound **`127.0.0.1`** | localhost only — tunnel to reach it |
| ssh | `:22` | key auth; no password |

`.env` was updated the documented way, not by hand:
`python scripts/pi_link.py set lan 192.168.68.63 --laptop 192.168.68.83`.
`pi_link.py status` now prints **ANSWERS**.

## 3. The hardware

| | |
|---|---|
| board | NVIDIA **Jetson Orin Nano**, L4T R36.4.3, Ubuntu 22.04.5, kernel 5.15.148-tegra, aarch64 |
| python | 3.10.12 (system) · bbos has its own venvs per daemon |
| power mode | 25 W |
| **battery** | **17.74 V**, drive loop 100 Hz, zero errors |
| thermals | 47–48 °C across zones — cool |
| disk | 116 G, **47 G used (42 %)**, 65 G free |
| **memory** | 7.4 G total, **226 M free**, 2.4 G available — 4.5 G in use. Tight. |
| uptime | 2 h 19 m at time of writing |

## 4. What is alive, measured

```
[camera.left.jpeg]    17 Hz      ← WORKING
[camera.right.jpeg]   17 Hz      ← WORKING
[imu.orientation]     20 Hz      ← WORKING
[drive.status]         0 Hz  but 17.74 V, loop_hz 100, errors 0  (publishes on change)
[camera.head.jpeg]    NO WRITER
[mapping.voxels]      NO WRITER
[slam.pose]           NO WRITER
[camera.rect]         0 Hz, all zero
[camera.depth]        0 Hz, all zero
[camera.points]       0 Hz, 0 points
```

bbos daemons all running: `arm_left arm_right base camera dataset depth led mapping mic nav quest
remote_session slam speaker telemetry usb wakeword`. Nothing has crashed — the dead topics are
downstream of one missing device.

**A live picture is available today** from the two arm cameras, 640×480 MJPEG, ~35 KB a frame:

```bash
ssh bb 'PYTHONPATH=/home/bracketbot/bbos /home/bracketbot/bbos/.venv/bin/python3 -' < robot/probe_bbos.py
# or grab a frame: read camera.left.jpeg, cut SOI..EOI out of the zero-padded uint8 buffer
```

bbos messages are **numpy structured arrays** gated by `Reader.ready()` — fields come from
`dtype.names`, not attributes. Reading `r.data` without `ready()` returns nothing and looks
exactly like "no camera".

## 5. The three things that are actually broken

### a) The head stereo camera is not connected — this is the big one

`cam_head` is configured to match a device whose card name contains **`'USB Camera'`**, at
2560×960 @ 60 Hz. Every video node present reports `icspring camera`:

```
cam_head  {'card_substr': 'USB Camera',     'width': 2560, 'height': 960, 'rate': 60}
cam_left  {'card_substr': 'icspring camera','width': 640,  'height': 480, 'rate': 30}
cam_right {'card_substr': 'icspring camera','width': 640,  'height': 480, 'rate': 30}

/dev/video0..3  ->  "icspring camera: icspring camer"   (2× UVC 32e6:5841, on usb1)
```

so the camera daemon loops forever:

```
[!] head: FileNotFoundError: could not find camera 'USB Camera' (capture node index=0);
    none present or all matching devices already in use; reopening in 2s
[.] left:  captured=197100 published=197100 (jpeg=0.3ms/36KB)
[.] right: captured=193500 published=193500 (jpeg=0.2ms/31KB)
```

The two cameras that *are* attached are the **arm wrist cameras** (`sibling_arm:
/dev/ttyARMLEFT` / `RIGHT`) — the sample frame shows the chassis from above, which is the wrist
view, not a head view. **The 2560×960 head stereo unit is physically absent or unpowered.**

This one fault explains `depth`, `points`, `rect`, `mapping.voxels` and `slam.pose` all being
dead: they are all computed from the head stereo pair. **It is not a software fix** — plug the
head camera in. (If it is plugged in and enumerating under a different name, the fix is the
`card_substr` in bbos's `cam_head` config, but four nodes all named `icspring camera` says the
device is not there.)

Knock-on for us: `robot/server.py` only ever reads `camera.head.jpeg`, so `/healthz` reports
`cameras: []` while two cameras stream happily. Worth teaching it to fall back to left/right.

### b) The wifi dongle threw a kernel oops ~1 h before this was written

```
WARNING: CPU: 0 PID: 14 at mt76/usb.c:573 mt76u_alloc_queues+0x4cc/0xa74 [mt76_usb]
mt76x2u 2-1.4:1.0: MAC error detected · tx urb failed: -71
wlan_usb: deauthenticating ... (Reason: 3=DEAUTH_LEAVING)
tegra-xusb: ERROR Transfer event TRB DMA ptr not part of current TD  (×many)
```

Different USB bus from the cameras, so unrelated to (a) — but it is a credible cause of the
connection drops that have been blamed on the venue wifi. If the link dies again, check `dmesg`
before blaming the network.

### c) The tailnet share was still never accepted

The robot is `bracketbot` = `100.74.122.43` under **`danielliuyes@gmail.com`**; this laptop is on
**`anonymous.weeb53@gmail.com`**. Two tailnets cannot see each other, so `100.74.122.43` times
out and `pi_link.py` reports `no node named bracketbot`.

Until someone accepts the share, **every path to the robot requires being on its LAN**. Fix:
admin console of `danielliuyes@` → Machines → `bracketbot` → ⋯ → Share… → open the link signed in
as `anonymous.weeb53@` → `python scripts/pi_link.py discover && python scripts/pi_link.py use tailnet`.
That is the one change that stops this doc going stale every time the room's wifi changes.

## 6. Operating it

```bash
# restart our HTTP server (NOT a bbos daemon — safe)
ssh bb 'cd ~/gitspace && kill -TERM $(pgrep -f "robot.server"); sleep 3;
        nohup .venv/bin/python -m robot.server --hardware --port 8080 > ~/gitspace/robot.log 2>&1 &'

ssh bb 'tail -40 /dev/shm/camera.log'      # camera daemon — per-camera capture/publish counters
ssh bb 'tail -40 ~/gitspace/robot.log'     # our server
ssh bb 'ls /dev/shm'                       # every bbos topic is a file here; also *.log per daemon
ssh bb 'sudo dmesg | tail -40'             # USB / wifi / kernel
```

Our code on the robot lives in `~/gitspace` (`robot/`, `roomctl/`, `scripts/`, `obs.py`, own venv,
own `.env` with `ROBOT_*` + the six `SENTRY_*`; no Elastic or OpenAI keys). A timestamped backup of
`.env` is beside it from the allow-list edit.

Every daemon logs to `/dev/shm/<name>.log` — that is where to look first, and it is *not* obvious
from anywhere else.

## 7. What a demo can honestly claim today

- **Yes**: ssh, the HTTP server, battery and drive telemetry, IMU at 20 Hz, and a live 640×480
  camera image from either arm.
- **No**: depth, point clouds, voxel maps, SLAM pose, and anything built on them — all blocked on
  the head stereo camera in §5a.
- **Only on the robot's own LAN**, until the tailnet share in §5c is accepted.
