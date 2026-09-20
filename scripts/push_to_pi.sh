#!/bin/zsh
# push_to_pi.sh — put robot/ on the Pi and (optionally) start it. The Pi has no git remote to pull
# from and no .env of ours: this is how code gets there.
#
#   ./scripts/push_to_pi.sh <user>@<host>            sync code, install deps, report what is missing
#   ./scripts/push_to_pi.sh <user>@<host> --start    ...then (re)start `python -m robot.server --hardware`. First REWRITES the
#                                                    robot's ROBOT_ALLOW line when there is one: loopback + its CIDRs + THIS laptop's
#                                                    current address (old leases dropped); with the boot units in, restarts via systemd
#   ./scripts/push_to_pi.sh <user>@<host> --start --sim     the simulated robot, ON the Pi: proves the
#                                                           link end to end before any camera works
#   ./scripts/push_to_pi.sh <user>@<host> --stop | --log
#   ./scripts/push_to_pi.sh <user>@<host> --adapter          also (re)start robot/adapter.py — the Housebot Edge contract on
#                                                            the robot's 127.0.0.1:8765 (loopback there, bearer token). On
#                                                            hardware it REFUSES every motion by design: starting it moves nothing
#   ./scripts/push_to_pi.sh <user>@<host> --install-units    make robot.server (+ the adapter) survive a REBOOT: two systemd
#                                                            --user units + `loginctl enable-linger`. A PERSISTENT change to the
#                                                            robot — run it only when its owner has said yes. --remove-units undoes it
#
# <host> is PI_HOST once the link is up (python scripts/pi_link.py status). Re-run after every change
# to robot/ — it is an rsync, it takes a second.
#
# What goes:   robot/  obs.py  roomctl/{__init__,frames}.py  tests/test_frames.py  scripts/requirements-pi.txt   -> ~/gitspace/
# What never goes:   .env. It holds the Elasticsearch and OpenAI keys, and only the laptop talks to
# those (docs/16 §4). The Pi's settings are ~/gitspace/.env ON the Pi — ROBOT_* and a SENTRY_DSN,
# written by hand, once. This script never overwrites it.
#
# The venv is --system-site-packages ON PURPOSE: cv2 and pyrealsense2 come from Bracket Bot's image
# (pyrealsense2 has no aarch64 wheel; their setup_realsense.sh builds it). A sealed venv would hide them.
set -e
G="${0:A:h}/.."
die() { print -u2 -- "\033[31m✗\033[0m $*"; exit 1 }
say() { print -- "\033[1m==>\033[0m $*" }

TARGET="" ACTION=sync MODE=--hardware ADAPTER=0
while (( $# )); do
  case "$1" in
    --adapter) ADAPTER=1; [[ $ACTION == sync ]] && ACTION=start; shift ;;
    --install-units) ACTION=units; shift ;;
    --remove-units) ACTION=rmunits; shift ;;
    --start) ACTION=start; shift ;;
    --stop)  ACTION=stop; shift ;;
    --log)   ACTION=log; shift ;;
    --sim)   MODE=--sim; shift ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *@*) TARGET="$1"; shift ;;
    *) die "don't know what '$1' is (-h for help)" ;;
  esac
done
[[ -n "$TARGET" ]] || die "give me <user>@<host>"
PORT=$(grep -E '^PI_PORT=' "$G/.env" 2>/dev/null | cut -d= -f2 | awk '{print $1}'); PORT=${PORT:-8080}
HOST="${TARGET#*@}"
CTL="/tmp/gs-pi-%C"
SSH=(ssh -o ConnectTimeout=8 -o ControlMaster=auto -o ControlPath=$CTL -o ControlPersist=120 -o StrictHostKeyChecking=accept-new)
# The shared connection forks a master into the background, and it INHERITS our stdout. Left alive, anything
# reading this script through a pipe (| tail, a CI log) hangs for ControlPersist seconds after we are done.
TRAPEXIT() { ssh -o ControlPath=$CTL -O exit "$TARGET" >/dev/null 2>&1 }
"${SSH[@]}" "$TARGET" true || die "cannot SSH to $TARGET.  python scripts/pi_link.py status"

if [[ $ACTION == log ]];  then exec "${SSH[@]}" "$TARGET" 'tail -n 60 -f ~/gitspace/robot.log'; fi
if [[ $ACTION == stop ]]; then "${SSH[@]}" "$TARGET" 'pkill -f "robot[.]server" && echo stopped || echo "was not running"; pkill -f "robot[.]adapter" && echo "adapter stopped" || true'; exit 0; fi
if [[ $ACTION == rmunits ]]; then
  "${SSH[@]}" "$TARGET" 'systemctl --user disable --now gitspace-robot.service gitspace-adapter.service 2>/dev/null; rm -f ~/.config/systemd/user/gitspace-{robot,adapter}.service; systemctl --user daemon-reload; echo "units removed (linger left as it was)"'
  exit 0
fi
if [[ $ACTION == units ]]; then
  say "installing systemd --user units on $TARGET (robot.server + adapter start at boot, restart on failure)"
  # bbos publishes the camera + IMU topics; if our server starts before them the camera opens as unavailable and STAYS so
  # until a restart. So: start late (sleep 25) and let systemd restart us — Restart=always is the whole dependency story.
  "${SSH[@]}" "$TARGET" "bash -s $PORT" <<'UNITS'
set -e
PORT="$1"; mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/gitspace-robot.service <<UNIT_A
[Unit]
Description=gitspace robot.server (capture, telemetry, events) on :$PORT
After=network-online.target
[Service]
WorkingDirectory=%h/gitspace
EnvironmentFile=-%h/gitspace/.env
ExecStartPre=/bin/sleep 25
ExecStart=%h/gitspace/.venv/bin/python -m robot.server --hardware --port $PORT
Restart=always
RestartSec=8
StandardOutput=append:%h/gitspace/robot.log
StandardError=append:%h/gitspace/robot.log
[Install]
WantedBy=default.target
UNIT_A
cat > ~/.config/systemd/user/gitspace-adapter.service <<UNIT_B
[Unit]
Description=gitspace robot.adapter (Housebot Edge contract) on 127.0.0.1:8765
After=gitspace-robot.service
[Service]
WorkingDirectory=%h/gitspace
EnvironmentFile=-%h/gitspace/.env
ExecStart=%h/gitspace/.venv/bin/python -m robot.adapter
Restart=always
RestartSec=8
StandardOutput=append:%h/gitspace/adapter.log
StandardError=append:%h/gitspace/adapter.log
[Install]
WantedBy=default.target
UNIT_B
pkill -f 'robot[.]server' || true; pkill -f 'robot[.]adapter' || true
systemctl --user daemon-reload
systemctl --user enable --now gitspace-robot.service gitspace-adapter.service
sudo -n loginctl enable-linger "$USER" && echo "linger on: the units start at BOOT, not at login"
systemctl --user --no-pager status gitspace-robot.service gitspace-adapter.service | grep -E 'Loaded|Active'
UNITS
  exit 0
fi

say "syncing robot/ -> $TARGET:~/gitspace/"
"${SSH[@]}" "$TARGET" 'mkdir -p ~/gitspace/scripts'
rsync -az --delete --exclude '__pycache__' --exclude '*.pyc' -e "${SSH[*]}" "$G/robot/" "$TARGET:gitspace/robot/"
rsync -az -e "${SSH[*]}" "$G/obs.py" "$TARGET:gitspace/obs.py"
# robot/adapter.py imports the ONE frames module (roomctl/frames.py) and fails loudly without it, by design: the robot
# and the laptop must agree on axes from the same file, not from two copies. Only these two files of roomctl/ go.
"${SSH[@]}" "$TARGET" 'mkdir -p ~/gitspace/roomctl ~/gitspace/tests'
rsync -az -e "${SSH[*]}" "$G/roomctl/__init__.py" "$G/roomctl/frames.py" "$TARGET:gitspace/roomctl/"
[[ -f "$G/tests/test_frames.py" ]] && rsync -az -e "${SSH[*]}" "$G/tests/test_frames.py" "$TARGET:gitspace/tests/test_frames.py"
rsync -az -e "${SSH[*]}" "$G/scripts/requirements-pi.txt" "$TARGET:gitspace/scripts/requirements-pi.txt"

say "python deps (first run takes a minute; after that it is a no-op)"
"${SSH[@]}" "$TARGET" 'bash -s' <<'REMOTE' || die "dependency install failed on the Pi (above)"
set -e
cd ~/gitspace
[ -x .venv/bin/python ] || python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -q --disable-pip-version-check -r scripts/requirements-pi.txt
missing=""
for m in fastapi uvicorn websockets anyio numpy cv2; do .venv/bin/python -c "import $m" 2>/dev/null || missing="$missing $m"; done
[ -z "$missing" ] || { echo "MISSING on the Pi:$missing — robot.server cannot start without these"; exit 1; }
.venv/bin/python -c "import pyrealsense2" 2>/dev/null && echo "   pyrealsense2: ok" \
  || echo "   pyrealsense2: NOT importable — the D415/D435 will report as unavailable; cam0 (V4L2) still works. Bracket Bot: setup/extras/setup_realsense.sh"
if [ -f .env ] && grep -qE '^ROBOT_TELEMETRY_SOURCE=.+' .env; then echo "   ROBOT_TELEMETRY_SOURCE: set"
else echo "   ROBOT_TELEMETRY_SOURCE: NOT SET in ~/gitspace/.env — fine for --sim. On --hardware every capture is 409"
     echo "     capture_rejected (tilt unmeasured, by design). Add:  ROBOT_TELEMETRY_SOURCE=robot.balance_source:udp"
     echo "     then robot/RUNBOOK.md §1 (five sendto() lines in the balance loop). Go/no-go: python -m robot.check_source robot.balance_source:udp"; fi
[ -f .env ] && echo "   ~/gitspace/.env: present" || echo "   ~/gitspace/.env: none — the robot runs on defaults (no Sentry, no ROBOT_TELEMETRY_SOURCE: docs/22 §4 rejects every HARDWARE capture without tilt evidence)"
REMOTE

[[ $ACTION == start ]] || { say "synced. Start it with: $0 $TARGET --start"; exit 0 }

# ROBOT_ALLOW (robot/allow.py): when the robot HAS an allowlist, make sure THIS laptop's current address is on it — the
# address the robot sees us from, by the route to it. Adds only: never removes an entry, never creates the line. A laptop
# that DHCP moved would otherwise lock itself out of the server it just started (the watcher shows it as robot_forbidden).
MY_IP=$(python3 -c "import socket,sys; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect((sys.argv[1], 9)); print(s.getsockname()[0])" "$HOST" 2>/dev/null || true)
if [[ -n $MY_IP ]]; then
  "${SSH[@]}" "$TARGET" "bash -s '$MY_IP'" <<'ALLOW'
IP="$1"; f=~/gitspace/.env
if [ -f "$f" ] && grep -q '^ROBOT_ALLOW=' "$f"; then
  # An inline comment on this line is moved to the line above it first: python-dotenv (hand start) strips "  # ..." from a
  # value, systemd's EnvironmentFile (the boot units) does NOT — the comment became part of ROBOT_ALLOW and the unit's
  # server answered 500 to everyone (2026-09-19). Then the address is appended to the value.
  sed -i -E "s|^(ROBOT_ALLOW=[^#]*[^# ]) *#(.*)$|#\2\n\1|" "$f"
  # REPLACE, do not append: a list that only grows keeps every DHCP lease the laptop ever held on every network it has
  # left — on a venue wifi that address belongs to someone else's laptop an hour later, and it could POST /capture.
  # Kept: loopback, every CIDR (deliberate ranges such as the tailnet 100.64.0.0/10), and THIS laptop's address now.
  # Dropped: every other bare host address (old leases). 2026-09-19: five entries, three of them networks we had left.
  old=$(sed -n 's/^ROBOT_ALLOW=//p' "$f" | head -1); keep="127.0.0.1"
  for e in $(echo "$old" | tr ', ' '\n\n'); do case "$e" in ""|127.0.0.1|"$IP") ;; */*) keep="$keep,$e" ;; *) dropped="$dropped $e" ;; esac; done
  keep="$keep,$IP"
  if [ "$keep" = "$old" ]; then echo "   ROBOT_ALLOW: $keep (unchanged)"
  else sed -i -E "s|^ROBOT_ALLOW=.*$|ROBOT_ALLOW=$keep|" "$f" && echo "   ROBOT_ALLOW: now $keep${dropped:+   (dropped old leases:$dropped)}"; fi
fi
ALLOW
fi

# The boot units (--install-units, RUNBOOK §9): when they are in, two servers cannot share :$PORT, so restart THROUGH
# systemd — the synced code and the refreshed .env are picked up the same way — and skip the hand launch below.
if "${SSH[@]}" "$TARGET" 'systemctl --user is-enabled -q gitspace-robot.service 2>/dev/null'; then
  say "the boot units are installed: restarting robot.server through systemd (log: ~/gitspace/robot.log on the Pi)"
  "${SSH[@]}" "$TARGET" 'systemctl --user restart gitspace-robot.service'
  UNITS_IN=1
else
  UNITS_IN=0
  say "starting: python -m robot.server $MODE   (log: ~/gitspace/robot.log on the Pi)"
  # Stop and start are SEPARATE ssh calls on purpose. `pkill -f` matches whole command lines, and a line
  # that also says `-m robot.server` is a line it kills — itself. The [.] keeps the stop from matching its own.
  "${SSH[@]}" "$TARGET" "pkill -f 'robot[.]server' ; sleep 1; true"
  # The launch sits in a subshell whose OWN stdio is redirected too. Redirecting only python's is not enough: the
  # `&` forks a wrapper shell that still holds ssh's stdout, the channel never closes, and this call never returns.
  "${SSH[@]}" -n "$TARGET" "cd ~/gitspace && ( nohup setsid .venv/bin/python -m robot.server $MODE --port $PORT > robot.log 2>&1 < /dev/null & ) > /dev/null 2>&1; sleep 0.3; true"
fi
WAIT_S=20; (( UNITS_IN )) && WAIT_S=50          # the unit sleeps 25 s before it starts (bbos first)
for i in $(seq 1 $WAIT_S); do
  curl -fsS -m 2 "http://$HOST:$PORT/healthz" >/dev/null 2>&1 && { say "it answers:"; curl -s -m 3 "http://$HOST:$PORT/healthz"; print; break }
  sleep 1
  (( i == WAIT_S )) && { print; "${SSH[@]}" "$TARGET" 'tail -n 25 ~/gitspace/robot.log'; die "robot.server did not come up in $WAIT_S s — its log is above" }
done
if (( ADAPTER )); then
  if (( UNITS_IN )); then
    say "restarting the adapter through systemd   (robot's 127.0.0.1:8765; log: ~/gitspace/adapter.log)"
    "${SSH[@]}" "$TARGET" 'systemctl --user restart gitspace-adapter.service'
  else
    say "starting: python -m robot.adapter   (robot's 127.0.0.1:8765; log: ~/gitspace/adapter.log)"
    "${SSH[@]}" "$TARGET" "pkill -f 'robot[.]adapter' ; sleep 1; true"
    "${SSH[@]}" -n "$TARGET" "cd ~/gitspace && ( nohup setsid .venv/bin/python -m robot.adapter > adapter.log 2>&1 < /dev/null & ) > /dev/null 2>&1; sleep 0.3; true"
  fi
  for i in {1..15}; do
    out=$("${SSH[@]}" "$TARGET" 'curl -fsS -m 2 http://127.0.0.1:8765/health' 2>/dev/null) && { say "adapter answers on the robot: $out"; break }
    sleep 1
    (( i == 15 )) && { "${SSH[@]}" "$TARGET" 'tail -n 20 ~/gitspace/adapter.log'; die "robot.adapter did not come up in 15 s — its log is above" }
  done
  "${SSH[@]}" "$TARGET" 'grep -qE "^HOUSEBOT_ROBOT_TOKEN=.+" ~/gitspace/.env 2>/dev/null && echo "   HOUSEBOT_ROBOT_TOKEN: set" || echo "   HOUSEBOT_ROBOT_TOKEN: NOT SET in ~/gitspace/.env — /v1/* will refuse every caller until it is (health needs none)"'
fi
PY="$G/.venv/bin/python"; [[ -x "$PY" ]] || PY=python3
say "now prove the link:  $PY scripts/verify_robot_link.py --host $HOST"
