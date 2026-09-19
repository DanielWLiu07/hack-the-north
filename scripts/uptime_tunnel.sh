#!/usr/bin/env bash
# scripts/uptime_tunnel.sh — STOPGAP public URL for Sentry Uptime until the AWS tier exists:
# a Cloudflare quick tunnel (no account) to the laptop's web server, then the monitor re-pointed.
#
#   scripts/uptime_tunnel.sh            start (or reuse) the tunnel and point the monitor at it
#   scripts/uptime_tunnel.sh --stop
#
# Honest limits: the URL changes every time the tunnel restarts (re-run this), and a sleeping
# laptop is "down" — docs/19 is why the real home is AWS (scripts/deploy_web.sh), which re-points
# the same monitor. web/server.py refuses its loopback inlet to anything carrying
# cf-connecting-ip / x-forwarded-for, so the tunnel cannot reach it.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
STATE=${XDG_STATE_HOME:-$HOME/.local/state}/gitspace
LOG=$STATE/uptime-tunnel.log PID=$STATE/uptime-tunnel.pid
PORT=${WEB_PORT:-8000}
mkdir -p "$STATE"

running() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }
if [ "${1:-}" = --stop ]; then running && kill "$(cat "$PID")" && echo "tunnel stopped"; rm -f "$PID"; exit 0; fi

curl -fsS -m 3 -o /dev/null "http://127.0.0.1:$PORT/api/health" || { echo "nothing answering on :$PORT — start web/server.py first" >&2; exit 1; }
if ! running; then
  : > "$LOG"
  nohup cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >"$LOG" 2>&1 &
  echo $! > "$PID"
fi
URL=""
for _ in $(seq 1 60); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | tail -n 1 || true)
  [ -n "$URL" ] && break; sleep 1
done
[ -n "$URL" ] || { echo "no tunnel URL after 60 s — see $LOG" >&2; exit 1; }
# Resolve via 1.1.1.1: the local resolver negatively caches the brand-new hostname for a while
# after the first lookup races the tunnel's registration. Sentry's checkers don't have that cache.
HOST=${URL#https://}
for _ in $(seq 1 30); do
  IP=$(dig +short "$HOST" @1.1.1.1 | grep -E '^[0-9.]+$' | head -n 1 || true)
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 ${IP:+--resolve "$HOST:443:$IP"} "$URL/api/health" || true)
  [ "$code" = 200 ] && break; sleep 2
done
[ "$code" = 200 ] || { echo "$URL/api/health -> $code" >&2; exit 1; }
echo "tunnel $URL -> 127.0.0.1:$PORT (pid $(cat "$PID")), /api/health 200"
"$ROOT/.venv/bin/python" "$ROOT/scripts/sentry_uptime.py" "$URL/api/health"
