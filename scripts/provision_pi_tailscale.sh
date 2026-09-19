#!/bin/zsh
# provision_pi_tailscale.sh — put the Bracket Bot Pi on the tailnet, so the laptop reaches it at
# one fixed 100.x address from ANY network (campus wifi, a hotspot, our own router). docs/33.
#
#   ./scripts/provision_pi_tailscale.sh <user>@<host>                      the Pi already has internet
#   ./scripts/provision_pi_tailscale.sh <user>@<host> --wifi "SSID" "pw"   ...or give it some first
#   ./scripts/provision_pi_tailscale.sh --print [--wifi "SSID" "pw"]       no SSH from here: print the
#                                                                          block to paste ON the Pi
#                                                                          (a teammate's shell, a keyboard)
#
# The join is NON-INTERACTIVE: it uses a Tailscale auth key, so nobody opens a browser on a robot
# and a teammate can run it without ever touching your Tailscale account. Make the key first:
#
#   https://login.tailscale.com/admin/settings/keys  ->  Generate auth key
#   signed in as the account THIS LAPTOP's tailnet belongs to (`tailscale status` shows it)
#   Reusable OFF · Ephemeral OFF (an ephemeral node gets a NEW ip every time) · no tags
#   mkdir -p ~/.config/gitspace && pbpaste > ~/.config/gitspace/ts-authkey && chmod 600 ~/.config/gitspace/ts-authkey
#
# Not in .env on purpose: deploy_web.sh ships .env to the EC2 box, and that box has no business
# holding a key that joins our tailnet.
#
# THE PI NEEDS ITS OWN INTERNET, and not only today: Tailscale on the Pi talks to the coordination
# server and the relays itself. A phone hotspot is enough. The robot's own AP is not — it has no uplink.
#
# With --wifi the work runs DETACHED on the Pi: if this SSH session rides the Pi's wifi (its own AP),
# joining another network cuts it mid-command. The Pi carries on alone, rolls back to the network it
# was on if the new one fails, and this script waits for it to appear on the tailnet instead.
set -e
G="${0:A:h}/.."
KEYFILE="${TS_AUTHKEY_FILE:-$HOME/.config/gitspace/ts-authkey}"
PI_NAME=bracketbot

die() { print -u2 -- "\033[31m✗\033[0m $*"; exit 1 }
say() { print -- "\033[1m==>\033[0m $*" }

TARGET="" PRINT=0 SSID="" WPASS=""
while (( $# )); do
  case "$1" in
    --print) PRINT=1; shift ;;
    --wifi)  [[ -n "$2" && -n "$3" ]] || die '--wifi needs "SSID" "password"'; SSID="$2"; WPASS="$3"; shift 3 ;;
    -h|--help) sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *@*) TARGET="$1"; shift ;;
    *) die "don't know what '$1' is. Want <user>@<host> — the user is whatever the Pi's image logs in as; I won't guess it." ;;
  esac
done
(( PRINT )) || [[ -n "$TARGET" ]] || die "give me <user>@<host>, or --print for a block to paste on the Pi.  (-h for help)"

# ── the key ──────────────────────────────────────────────────────────────────────
KEY="${TS_AUTHKEY:-}"
[[ -z "$KEY" && -r "$KEYFILE" ]] && KEY="$(tr -d '[:space:]' < "$KEYFILE")"
[[ -n "$KEY" ]] || die "no auth key. Make one (the header of this script says how) and save it to $KEYFILE"
[[ "$KEY" == tskey-auth-* ]] || die "that is not an auth key (want tskey-auth-…). An API key (tskey-api-…) cannot join a node."

TS=$(command -v tailscale || echo /opt/homebrew/bin/tailscale)
LAPTOP_TS=$("$TS" ip -4 2>/dev/null | head -1) || true
[[ -n "$LAPTOP_TS" ]] || die "tailscale isn't up on this laptop — the Pi would join a tailnet nobody is on"

# ── what runs ON the Pi. bash, not zsh: it is the Pi's shell, not ours ───────────
remote_script() {
cat <<'REMOTE'
#!/usr/bin/env bash
# Runs on the Pi. One argument: a 0600 file of five lines — laptop epoch, laptop tailnet ip, keyfile,
# ssid, password. A FILE, not argv: a wifi password with an apostrophe in it does not survive
# zsh -> ssh -> bash -c '...' quoting, and this runs where nobody is watching it fail.
set -u
ARGS="$1"
{ IFS= read -r NOW; IFS= read -r LAPTOP; IFS= read -r KEYFILE; IFS= read -r SSID; IFS= read -r WPASS; } < "$ARGS"
KEYFILE="${KEYFILE/#\~/$HOME}"
step() { echo "[$(date +%H:%M:%S)] $*"; }
fail() { step "FAILED: $*"; rm -f "$KEYFILE" "$ARGS"; echo "PROVISION_FAILED"; exit 1; }
trap 'rm -f "$KEYFILE" "$ARGS"' EXIT              # the key and the wifi password never outlive the run

sudo -n true 2>/dev/null || fail "sudo wants a password here, and nothing can type it. Run the --print block in an interactive shell instead."

# 1. the clock. A Pi with no RTC battery boots at whenever it last shut down; TLS to tailscale.com
#    then fails as 'certificate not yet valid', which looks like a network fault and is not one.
skew=$(( $(date +%s) - NOW )); [ "${skew#-}" -gt 120 ] && { step "clock is ${skew}s off the laptop's — setting it"; sudo date -s "@$NOW" >/dev/null; }

# 2. internet, if asked to arrange it
if [ -n "$SSID" ]; then
  command -v nmcli >/dev/null || fail "no nmcli on this image: join '$SSID' by hand (wpa_supplicant / raspi-config), then re-run without --wifi"
  PREV=$(nmcli -t -f NAME,TYPE con show --active | awk -F: '$2 ~ /wireless/ {print $1; exit}')
  step "joining '$SSID' (was on: ${PREV:-nothing})"
  sudo nmcli device wifi rescan >/dev/null 2>&1; sleep 3
  if ! sudo nmcli --wait 30 device wifi connect "$SSID" password "$WPASS" >/dev/null 2>&1; then
    [ -n "$PREV" ] && sudo nmcli con up "$PREV" >/dev/null 2>&1
    fail "could not join '$SSID' (wrong password? 5 GHz-only hotspot? turn on 'maximize compatibility'). Back on '${PREV:-nothing}'."
  fi
  sudo nmcli con modify "$SSID" connection.autoconnect yes connection.autoconnect-priority 50 >/dev/null 2>&1
fi
online=0; for _ in 1 2 3 4 5 6 7 8 9 10; do curl -fsS -m 6 -o /dev/null https://pkgs.tailscale.com/ && { online=1; break; }; sleep 3; done
if [ "$online" = 0 ]; then
  [ -n "${PREV:-}" ] && { step "no internet on '$SSID' — going back to '$PREV'"; sudo nmcli con up "$PREV" >/dev/null 2>&1; }
  fail "the Pi has no route to the internet. It needs one of its own (a phone hotspot will do): --wifi \"SSID\" \"password\""
fi
step "internet: ok"

# 3. tailscale
if command -v tailscale >/dev/null 2>&1; then step "tailscale present: $(tailscale version | head -1)"
else step "installing tailscale"; curl -fsSL https://tailscale.com/install.sh | sh >/dev/null 2>&1 || fail "install.sh failed (apt locked? run: sudo apt-get update)"; fi
sudo systemctl enable --now tailscaled >/dev/null 2>&1 || fail "tailscaled would not start: systemctl status tailscaled"

state=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null)
if [ "$state" = "Running" ]; then
  step "already on a tailnet — leaving the login alone (the key is NOT spent)"
  sudo tailscale set --hostname=bracketbot >/dev/null 2>&1
else
  # --accept-dns=false: Tailscale must not rewrite the Pi's resolv.conf on venue wifi.
  # --accept-routes=false: a subnet router someone else advertises must not capture the robot's LAN.
  sudo tailscale up --auth-key="file:$KEYFILE" --hostname=bracketbot --accept-dns=false --accept-routes=false --timeout=60s \
    || fail "tailscale up was refused. The key is expired, already used (one-off keys work once), or from another account."
fi
IP=$(tailscale ip -4 | head -1)
[ -n "$IP" ] || fail "up, but no tailnet address"
step "on the tailnet as bracketbot = $IP"
tailscale ping --c 2 --timeout 5s "$LAPTOP" 2>&1 | tail -1 | sed 's/^/           laptop: /'
echo "PROVISION_OK $IP"
REMOTE
}

# ── --print: the same thing, for a shell we cannot SSH into ──────────────────────
# Built to survive a BLIND PASTE. `sudo -v` goes first and alone, because a sudo password prompt
# eats whatever was pasted after it. Then ONE chained command: it stops at the first thing that
# fails instead of ploughing on, and it never sets the clock from a stale number.
if (( PRINT )); then
  print -- "ON THE PI — three pastes. This contains the auth key: a one-off key dies the moment it is used."
  print -- "════════════════════════════════════════════════════════════════════════"
  print -- "\nPASTE 1 — alone. Type the Pi's password if it asks:\n"
  print -r -- "sudo -v"
  print -- "\nPASTE 2 — only if the Pi has no internet yet (a phone hotspot is enough):\n"
  if [[ -n "$SSID" ]]; then
    print -r -- "sudo nmcli device wifi connect '${SSID//\'/\'\\\'\'}' password '${WPASS//\'/\'\\\'\'}'"
  else
    print -r -- "sudo nmcli device wifi connect \"YOUR-HOTSPOT-NAME\" password \"YOUR-HOTSPOT-PASSWORD\""
  fi
  print -- "\nPASTE 3 — one line, all of it:\n"
  print -r -- "curl -fsS -m 10 -o /dev/null https://pkgs.tailscale.com/ || { echo; echo '### NO INTERNET ON THE PI - do PASTE 2 first. (If the date below is wrong, fix it: sudo date -s \"2026-09-19 15:00\")'; date; false; } && curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up --auth-key=$KEY --hostname=$PI_NAME --accept-dns=false --accept-routes=false && echo && echo \"### DONE. THE PI'S ADDRESS: \$(tailscale ip -4 | head -1)\""
  print -- "\n════════════════════════════════════════════════════════════════════════"
  print -- "It ends with  ### DONE. THE PI'S ADDRESS: 100.x.y.z  — that is success."
  print -- "At a keyboard and cannot paste? Replace  --auth-key=…  with  --qr  and scan the code with a phone"
  print -- "signed in to the same Tailscale account."
  print -- "\nThen, on the laptop:  python scripts/pi_link.py discover && python scripts/pi_link.py use tailnet"
  exit 0
fi

# ── over SSH ─────────────────────────────────────────────────────────────────────
CTL="/tmp/gs-pi-%C"
SSH=(ssh -o ConnectTimeout=8 -o ControlMaster=auto -o ControlPath=$CTL -o ControlPersist=60 -o StrictHostKeyChecking=accept-new)
# The shared connection forks a master into the background, and it INHERITS our stdout. Left alive, anything
# reading this script through a pipe (| tail, a CI log) hangs for ControlPersist seconds after we are done.
TRAPEXIT() { ssh -o ControlPath=$CTL -O exit "$TARGET" >/dev/null 2>&1 }
say "target: $TARGET   (asks for the robot's password once, unless your ssh key is already on it)"
"${SSH[@]}" "$TARGET" true || die "cannot SSH to $TARGET. The laptop must be on a network the Pi is on RIGHT NOW (docs/33 §1) — or use --print."

RKEY=".ts-authkey.$$" RSCRIPT=".provision_tailscale.$$.sh" RARGS=".provision_args.$$" RLOG="provision_tailscale.log"
print -r -- "$KEY" | "${SSH[@]}" "$TARGET" "umask 077; cat > ~/$RKEY"
remote_script   | "${SSH[@]}" "$TARGET" "umask 077; cat > ~/$RSCRIPT"
print -r -l -- "$(date +%s)" "$LAPTOP_TS" "~/$RKEY" "$SSID" "$WPASS" | "${SSH[@]}" "$TARGET" "umask 077; cat > ~/$RARGS"

if [[ -z "$SSID" ]]; then
  "${SSH[@]}" "$TARGET" "bash ~/$RSCRIPT ~/$RARGS; rc=\$?; rm -f ~/$RSCRIPT ~/$RKEY ~/$RARGS; exit \$rc" || die "the Pi reported a failure (above)"
else
  say "running detached on the Pi — this session may drop when it changes wifi. That is expected."
  "${SSH[@]}" "$TARGET" "nohup setsid bash -c 'bash ~/$RSCRIPT ~/$RARGS; rm -f ~/$RSCRIPT ~/$RKEY ~/$RARGS' > ~/$RLOG 2>&1 < /dev/null &" || true
  say "if the laptop is on the ROBOT'S network, put it back on the internet now (rejoin your wifi)."
  say "waiting for \`$PI_NAME\` to appear on the tailnet (up to 4 min)…"
  ok=0
  for i in {1..48}; do
    "$TS" status 2>/dev/null | awk -v n="$PI_NAME" '$2 ~ "^"n && $0 !~ /offline/ {f=1} END{exit !f}' && { ok=1; break }
    sleep 5; print -n "."
  done; print
  (( ok )) || die "it never appeared. The log is on the Pi: ~/$RLOG. It rolls back to the network it was on when a join fails, so look for it there."
fi

say "recording the address and switching the laptop to it"
PY="$G/.venv/bin/python"; [[ -x "$PY" ]] || PY=python3
found=0
for i in {1..6}; do "$PY" "$G/scripts/pi_link.py" discover >/dev/null 2>&1 && { found=1; break }; sleep 5; done
if (( found )); then "$PY" "$G/scripts/pi_link.py" discover && "$PY" "$G/scripts/pi_link.py" use tailnet
else
  ROBOT_NET=$("${SSH[@]}" "$TARGET" "tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print((json.load(sys.stdin).get(\"CurrentTailnet\") or {}).get(\"Name\",\"?\"))'" 2>/dev/null)
  MY_NET=$("$TS" status --json 2>/dev/null | "$PY" -c 'import json,sys; print((json.load(sys.stdin).get("CurrentTailnet") or {}).get("Name","?"))')
  die "the robot joined tailnet '$ROBOT_NET' but this laptop is on '$MY_NET' — two accounts cannot see each other. Either share the machine (admin console of '$ROBOT_NET' -> Machines -> $PI_NAME -> Share, accept as '$MY_NET'), or make the key under '$MY_NET' (docs/33 §2). PI_HOST was NOT changed."
fi
print
say "done. The laptop can go back to any wifi. Next:"
print -- "    ./scripts/push_to_pi.sh ${TARGET%@*}@\$(grep -E '^PI_HOST=' .env | cut -d= -f2 | awk '{print \$1}') --start    # code + deps onto the Pi, start robot.server"
print -- "    $PY scripts/verify_robot_link.py                                  # prove it"
