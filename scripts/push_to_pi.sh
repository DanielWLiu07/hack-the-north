#!/bin/zsh
# push_to_pi.sh — put robot/ on the Pi and (optionally) start it. The Pi has no git remote to pull
# from and no .env of ours: this is how code gets there.
#
#   ./scripts/push_to_pi.sh <user>@<host>            sync code, install deps, report what is missing
#   ./scripts/push_to_pi.sh <user>@<host> --start    ...then (re)start `python -m robot.server --hardware`
#   ./scripts/push_to_pi.sh <user>@<host> --start --sim     the simulated robot, ON the Pi: proves the
#                                                           link end to end before any camera works
#   ./scripts/push_to_pi.sh <user>@<host> --stop | --log
#
# <host> is PI_HOST once the link is up (python scripts/pi_link.py status). Re-run after every change
# to robot/ — it is an rsync, it takes a second.
#
# What goes:   robot/  obs.py  scripts/requirements-pi.txt          -> ~/gitspace/ on the Pi
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

TARGET="" ACTION=sync MODE=--hardware
while (( $# )); do
  case "$1" in
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
if [[ $ACTION == stop ]]; then "${SSH[@]}" "$TARGET" 'pkill -f "robot[.]server" && echo stopped || echo "was not running"'; exit 0; fi

say "syncing robot/ -> $TARGET:~/gitspace/"
"${SSH[@]}" "$TARGET" 'mkdir -p ~/gitspace/scripts'
rsync -az --delete --exclude '__pycache__' --exclude '*.pyc' -e "${SSH[*]}" "$G/robot/" "$TARGET:gitspace/robot/"
rsync -az -e "${SSH[*]}" "$G/obs.py" "$TARGET:gitspace/obs.py"
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

say "starting: python -m robot.server $MODE   (log: ~/gitspace/robot.log on the Pi)"
# Stop and start are SEPARATE ssh calls on purpose. `pkill -f` matches whole command lines, and a line
# that also says `-m robot.server` is a line it kills — itself. The [.] keeps the stop from matching its own.
"${SSH[@]}" "$TARGET" "pkill -f 'robot[.]server' ; sleep 1; true"
# The launch sits in a subshell whose OWN stdio is redirected too. Redirecting only python's is not enough: the
# `&` forks a wrapper shell that still holds ssh's stdout, the channel never closes, and this call never returns.
"${SSH[@]}" -n "$TARGET" "cd ~/gitspace && ( nohup setsid .venv/bin/python -m robot.server $MODE --port $PORT > robot.log 2>&1 < /dev/null & ) > /dev/null 2>&1; sleep 0.3; true"
for i in {1..20}; do
  curl -fsS -m 2 "http://$HOST:$PORT/healthz" >/dev/null 2>&1 && { say "it answers:"; curl -s -m 3 "http://$HOST:$PORT/healthz"; print; break }
  sleep 1
  (( i == 20 )) && { print; "${SSH[@]}" "$TARGET" 'tail -n 25 ~/gitspace/robot.log'; die "robot.server did not come up in 20 s — its log is above" }
done
PY="$G/.venv/bin/python"; [[ -x "$PY" ]] || PY=python3
say "now prove the link:  $PY scripts/verify_robot_link.py --host $HOST"
