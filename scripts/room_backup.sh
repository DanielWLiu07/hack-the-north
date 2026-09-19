#!/usr/bin/env bash
# scripts/room_backup.sh — room.git IS the database, and it lived on one laptop disk. This keeps a
# copy on GitHub (private: $ROOM_REPO), pushed continuously, from whichever writer committed.
#
#   scripts/room_backup.sh               sync now: every branch and tag. Safe any time, idempotent.
#   scripts/room_backup.sh --install     launchd: every 60 s from login (offline minutes just retry)
#   scripts/room_backup.sh --status | --uninstall
#
# It never loses history. Branches deleted locally stay on the remote. A branch REWRITTEN locally
# (`room reset --hard`) moves on the remote too — but the remote's old tip is first kept as tag
# backup/<branch>/<sha7>. Tags are never forced: a retagged name is reported, not overwritten.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
env_get() { grep -E "^$1=" "$ROOT/.env" 2>/dev/null | tail -n 1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//'; }
ROOM=${ROOM_GIT_PATH:-$(env_get ROOM_GIT_PATH)}; ROOM=${ROOM:-./room.git}
case $ROOM in /*) ;; *) ROOM=$ROOT/${ROOM#./} ;; esac
REPO=${ROOM_REPO:-$(env_get ROOM_REPO)}
URL=${ROOM_REMOTE_URL:-https://github.com/$REPO.git}      # override: tests use a local bare repo
LABEL=ink.repr.gitspace-room-backup
PLIST=$HOME/Library/LaunchAgents/$LABEL.plist
LOG=${XDG_STATE_HOME:-$HOME/.local/state}/gitspace/room-backup.log
g() { git -C "$ROOM" "$@"; }
say() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

case "${1:-}" in
  --status)    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E '^\s*(state|last exit code|run interval)' || echo "not installed"
               tail -n 5 "$LOG" 2>/dev/null; exit 0 ;;
  --uninstall) launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null; rm -f "$PLIST"; echo "uninstalled (remote kept)"; exit 0 ;;
  --install)
    mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
    cat > "$PLIST" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$ROOT/scripts/room_backup.sh</string></array>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
P
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
    launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "installed: $LABEL every 60 s, log $LOG"; exit 0 ;;
esac

[ -n "$REPO" ] || { say "ROOM_REPO unset in .env"; exit 1; }
g rev-parse --git-dir >/dev/null 2>&1 || { say "no repository at $ROOM"; exit 1; }
g remote get-url origin >/dev/null 2>&1 || g remote add origin "$URL"
if ! g fetch --quiet --prune origin 2>/dev/null; then
  say "offline or unreachable ($URL) — will retry"; exit 0
fi
pushed=0 kept=0
for b in $(g for-each-ref --format='%(refname:short)' refs/heads); do
  here=$(g rev-parse "refs/heads/$b")
  there=$(g rev-parse -q --verify "refs/remotes/origin/$b" 2>/dev/null || true)
  [ "$here" = "$there" ] && continue
  if [ -n "$there" ] && ! g merge-base --is-ancestor "$there" "$here" 2>/dev/null; then
    g push --quiet origin "$there:refs/tags/backup/$b/${there:0:7}" && kept=$((kept + 1))
    g push --quiet --force origin "refs/heads/$b:refs/heads/$b" && pushed=$((pushed + 1))
    say "$b was rewritten locally: remote's old tip kept as backup/$b/${there:0:7}"
  else
    g push --quiet origin "refs/heads/$b:refs/heads/$b" && pushed=$((pushed + 1))
  fi
done
tags_out=$(g push --quiet origin --tags 2>&1) || say "tag push refused (a tag moved locally; tags are never forced): ${tags_out##*!}"
[ "$pushed$kept" = "00" ] && [ -z "${VERBOSE:-}" ] || say "synced $pushed branch(es)$([ $kept -gt 0 ] && echo ", kept $kept old tip(s)") -> $URL"
