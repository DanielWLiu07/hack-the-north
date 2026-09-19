#!/usr/bin/env bash
# scripts/named_tunnel.sh — a STABLE public URL: a named Cloudflare tunnel from a hostname we own
# to the web server on this laptop, run by launchd so it survives restarts. docs/19 has the why
# and the one-time manual steps (register the domain, put it on Cloudflare, `cloudflared tunnel
# login`). After those, this does the rest, idempotently:
#
#   scripts/named_tunnel.sh repr.ink                # tunnel + config + DNS + launchd + verify + uptime
#   scripts/named_tunnel.sh repr.ink gitirl.ink     # several hostnames, one tunnel
#   scripts/named_tunnel.sh --status | --stop
#
# The tunnel is named `gitspace`. Its credentials JSON (a secret) and config live in ~/.cloudflared,
# never in the repo. Moving the web tier to AWS later = run the same tunnel there: same hostname.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
NAME=gitspace
CFD=$HOME/.cloudflared
CONFIG=$CFD/$NAME.yml
LABEL=ink.repr.$NAME-tunnel
PLIST=$HOME/Library/LaunchAgents/$LABEL.plist
PORT=${WEB_PORT:-8000}
LOG=${XDG_STATE_HOME:-$HOME/.local/state}/gitspace/named-tunnel.log

case "${1:-}" in
  ""|-h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
  --status) launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E 'state|pid' || echo "not loaded"
            cloudflared tunnel info "$NAME" 2>/dev/null | tail -n +2 || true; exit 0 ;;
  --stop)   launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "stopped (tunnel + DNS kept)" || echo "not running"
            exit 0 ;;
esac
DOMAINS="$*"

[ -f "$CFD/cert.pem" ] || { cat >&2 <<MSG
not logged in to Cloudflare. One time, in a browser, AFTER the domain is Active on Cloudflare:
    cloudflared tunnel login        # pick $1 — writes ~/.cloudflared/cert.pem
then re-run: $0 $DOMAINS
MSG
exit 2; }
curl -fsS -m 3 -o /dev/null "http://127.0.0.1:$PORT/api/health" || { echo "nothing on :$PORT — start web/server.py first" >&2; exit 1; }

# 1. the tunnel (a UUID + a credentials JSON in ~/.cloudflared)
UUID=$(cloudflared tunnel list --output json 2>/dev/null | python3 -c "
import json, sys
print(next((t['id'] for t in json.load(sys.stdin) if t['name'] == '$NAME' and not t.get('deleted_at')), ''))")
if [ -z "$UUID" ]; then
  cloudflared tunnel create "$NAME" >/dev/null
  UUID=$(cloudflared tunnel list --output json | python3 -c "
import json, sys; print(next(t['id'] for t in json.load(sys.stdin) if t['name'] == '$NAME'))")
  echo "created tunnel $NAME ($UUID)"
fi
[ -f "$CFD/$UUID.json" ] || { echo "credentials $CFD/$UUID.json missing — delete the tunnel and re-run" >&2; exit 1; }

# 2. the config: every hostname -> the local web server; anything else 404s
{
  printf 'tunnel: %s\ncredentials-file: %s\n' "$UUID" "$CFD/$UUID.json"
  printf 'originRequest:\n  connectTimeout: 10s\n  keepAliveTimeout: 90s\n'   # SSE: long-lived responses
  printf 'ingress:\n'
  for d in $DOMAINS; do
    printf '  - hostname: %s\n    service: http://127.0.0.1:%s\n' "$d" "$PORT"
    printf '  - hostname: www.%s\n    service: http://127.0.0.1:%s\n' "$d" "$PORT"
  done
  printf '  - service: http_status:404\n'
} > "$CONFIG"
cloudflared tunnel --config "$CONFIG" ingress validate >/dev/null && echo "config $CONFIG: valid"

# 3. DNS: proxied CNAMEs -> <UUID>.cfargotunnel.com (the apex works via CNAME flattening)
for d in $DOMAINS; do
  for h in "$d" "www.$d"; do
    cloudflared tunnel route dns --overwrite-dns "$NAME" "$h" 2>&1 | grep -E 'Added|already|INF' | tail -n 1 || true
  done
done

# 4. launchd: start at login, restart if it dies
mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
cat > "$PLIST" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$(command -v cloudflared)</string><string>tunnel</string><string>--no-autoupdate</string>
    <string>--config</string><string>$CONFIG</string><string>run</string><string>$NAME</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
P
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "launchd: $LABEL (RunAtLoad + KeepAlive), log $LOG"

# 5. verify through a public resolver (the local one may have cached the name as missing)
FIRST=${DOMAINS%% *}
for _ in $(seq 1 45); do
  IP=$(dig +short "$FIRST" @1.1.1.1 | grep -E '^[0-9.]+$' | head -n 1 || true)
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 ${IP:+--resolve "$FIRST:443:$IP"} "https://$FIRST/api/health" || true)
  [ "$code" = 200 ] && break; sleep 2
done
[ "$code" = 200 ] || { echo "https://$FIRST/api/health -> $code (see $LOG)" >&2; exit 1; }
echo "https://$FIRST/api/health -> 200"

# 6. the stable URL becomes THE url: Sentry Uptime + .env
"$ROOT/.venv/bin/python" "$ROOT/scripts/sentry_uptime.py" "https://$FIRST/api/health"
if grep -qE '^WEB_PUBLIC_URL=' "$ROOT/.env"; then
  sed -i.bak -E "s#^WEB_PUBLIC_URL=.*#WEB_PUBLIC_URL=https://$FIRST#" "$ROOT/.env" && rm -f "$ROOT/.env.bak"
fi
echo "WEB_PUBLIC_URL=https://$FIRST — the quick tunnel can now be stopped"
