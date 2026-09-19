#!/bin/zsh
# watch_robot_link.sh — live view of the robot link. Leave it open in a tmux pane.
#
#   ./scripts/watch_robot_link.sh [seconds]        default: every 5 s
#
# Shows BOTH address pairs whichever is selected, so the moment the Pi appears — on the tailnet
# or on our own router — it is visible here, and says which command makes it live.
# It reads; it never writes .env. Proof is verify_robot_link.py, not this: a green row here means
# "answers", not "carries a stream without losing events".
G="${0:A:h}/.."
EVERY=${1:-5}
TS=$(command -v tailscale || echo /opt/homebrew/bin/tailscale)
# every probe says "unsampled" to the robot's Sentry SDK: a status pane open all day must not be 17k transactions
NOTRACE="sentry-trace: 00000000000000000000000000000001-0000000000000001-0"
g=$'\033[32m' y=$'\033[33m' r=$'\033[31m' d=$'\033[2m' x=$'\033[0m'

env_get() { grep -E "^$1=" "$G/.env" 2>/dev/null | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//' }

row() {  # label host port selected?
  local label=$1 host=$2 port=$3 sel=$4 hz mode
  [[ -z "$host" ]] && { printf "  %-8s %-16s ${d}not set${x}\n" "$label" "—"; return }
  # cheapest question first: a dead address must cost ~2 s a refresh, not 5
  if nc -z -G 1 "$host" "$port" >/dev/null 2>&1; then
    hz=$(curl -s -m 2 -H "$NOTRACE" "http://$host:$port/healthz" 2>/dev/null)
    if [[ "$hz" == *boot_id* ]]; then
      mode=$(print -r -- "$hz" | sed -E 's/.*"mode":"([^"]*)".*/\1/')
      boot=$(print -r -- "$hz" | sed -E 's/.*"boot_id":"([^"]*)".*/\1/')
      last=$(print -r -- "$hz" | sed -nE 's/.*"last_id":"([^"]*)".*/\1/p')
      printf "  %-8s %-16s ${g}ANSWERS${x}  mode=%s boot=%s events=%s %s\n" "$label" "$host" "$mode" "$boot" "${last:-?}" "$sel"
      ANSWERS+=("$label")
    else
      printf "  %-8s %-16s ${y}PORT OPEN${x} but no /healthz — something other than robot.server is on :%s %s\n" "$label" "$host" "$port" "$sel"
    fi
  elif ping -c 1 -W 900 "$host" >/dev/null 2>&1; then
    printf "  %-8s %-16s ${y}PINGS${x}, :%s closed — the Pi is there, robot.server is not running %s\n" "$label" "$host" "$port" "$sel"
  else
    printf "  %-8s %-16s ${r}no route${x} %s\n" "$label" "$host" "$sel"
  fi
}

while true; do
  clear
  PP=$(env_get PI_PORT); PP=${PP:-8080}
  LINK=$(env_get PI_LINK); LIVE=$(env_get PI_HOST)
  LAN=$(env_get PI_HOST_LAN); TNET=$(env_get PI_HOST_TAILNET)
  ANSWERS=()
  print "ROBOT LINK   $(date '+%H:%M:%S')     selected: ${LINK:-?}  ->  PI_HOST=${LIVE:-unset}:$PP"
  print "──────────────────────────────────────────────────────────────────────────────"
  print "laptop   wifi $(ipconfig getifaddr en0 2>/dev/null || print -n '(none)')    tailnet $("$TS" ip -4 2>/dev/null | head -1)"
  print
  row lan     "$LAN"  "$PP" "$([[ $LINK == lan ]] && print '<- selected')"
  row tailnet "$TNET" "$PP" "$([[ $LINK == tailnet ]] && print '<- selected')"
  print
  # the tailnet's own view: is the Pi a member, and is the path direct or relayed?
  peer=$("$TS" status 2>/dev/null | awk '$2 ~ /^bracketbot/')
  if [[ -z "$peer" ]]; then
    print "tailnet  ${y}no node named bracketbot${x} — the Pi has not joined:  ./scripts/provision_pi_tailscale.sh -h"
  else
    print -r -- "$peer" | while read -r ip name _ _ rest; do
      case "$rest" in
        *offline*) print "tailnet  $name $ip  ${r}offline${x}  ${d}$rest${x}  — powered off, or the Pi lost its internet" ;;
        *relay*)   print "tailnet  $name $ip  ${y}relayed${x}  ${d}$rest${x}  — works; same wifi on both ends lets it go direct" ;;
        *direct*)  print "tailnet  $name $ip  ${g}direct${x}   ${d}$rest${x}" ;;
        *)         print "tailnet  $name $ip  ${g}online${x}, idle ${d}(the path is chosen on first traffic)${x}" ;;
      esac
      [[ -z "$TNET" ]] && print "         ${y}not recorded in .env yet:${x}  python scripts/pi_link.py discover"
    done
  fi
  print
  if (( ${#ANSWERS} )) && [[ ${ANSWERS[(I)$LINK]} -eq 0 ]]; then
    print "${y}${ANSWERS[1]} answers but '$LINK' is selected:${x}  python scripts/pi_link.py use ${ANSWERS[1]}"
  elif (( ${#ANSWERS} )); then
    n=$(curl -sN -m 2 -H "$NOTRACE" "http://$LIVE:$PP/events?types=telemetry" 2>/dev/null | grep -c '^data:')
    (( n > 0 )) && print "SSE      ${g}$n telemetry events in 2 s${x} on /events   ${d}curl -N http://$LIVE:$PP/events${x}" \
                || print "SSE      ${r}/healthz answers but /events sent nothing in 2 s${x} — old robot/ on the Pi? ./scripts/push_to_pi.sh"
    print "prove it: .venv/bin/python scripts/verify_robot_link.py"
  else
    print "${r}the robot answers nowhere.${x}  docs/33-robot-link.md §1 is the way in."
  fi
  sleep "$EVERY"
done
