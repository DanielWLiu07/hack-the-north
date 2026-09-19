#!/usr/bin/env bash
# The cloud web tier (docs/19 "As built"). GCP project gitspace-htn-2026, VM gitspace-web
# (e2-small, us-east4-a, next to Elastic), Caddy + Let's Encrypt on https://8-234-158-138.sslip.io.
# Vercel (scripts/deploy_vercel.sh) serves the static pages and proxies everything else here.
#
#   scripts/gcp_mirror.sh status      VM state, health through Caddy, the commit the mirror serves
#   scripts/gcp_mirror.sh sync-room   push room.git (branches + tags); the mirror checks out the laptop's branch
#   scripts/gcp_mirror.sh sync-bg     sync-room in the background, coalesced (what room.git's hooks call)
#   scripts/gcp_mirror.sh hooks       install those hooks in room.git (ROOM_MIRROR=off skips them per command)
#   scripts/gcp_mirror.sh ship        re-deploy web/ bridge/ roomctl/ obs.py elastic/ (code only; the VM's .env is untouched;
#                                     web/landing/live/ is never shipped: camera frames stay on the laptop)
#                                     roomctl/ is web/jobs.py's planner (gitspace.plan/1): without it a job has no plan
#   scripts/gcp_mirror.sh logs        the web service's journal
#   scripts/gcp_mirror.sh stop|start  stop the VM (then only the ~CA$0.50/month disk bills) / start it again
#
# Cost guards (none of them need anyone watching): one e2-small, 10 GB pd-standard, no reserved IP,
# no service account on the VM, a Google-enforced STOP at 2026-09-21T12:00Z, a CA$10 budget with
# alerts at 25/50/90/100 %, and no OpenAI / GitHub / AWS keys on the box.
set -euo pipefail

P=gitspace-htn-2026
Z=us-east4-a
VM=gitspace-web
URL=https://8-234-158-138.sslip.io
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOG=~/.cache/gitspace/logs/mirror.log
LOCK=~/.cache/gitspace/mirror.lock

vm() { gcloud compute "$@" --project="$P" --zone="$Z"; }
on_vm() { vm ssh "$VM" --tunnel-through-iap --quiet --command "$1"; }
to_vm() { vm scp --tunnel-through-iap --quiet "$@"; }
room_path() { (cd "$ROOT" && .venv/bin/python -c 'from roomctl.repo import default_path; print(default_path())'); }

sync_room() {
    local room tmp branch rc=0
    room=$(room_path)
    tmp=$(mktemp -d)
    git -C "$room" bundle create "$tmp/room.bundle" --branches --tags 2>/dev/null
    branch=$(git -C "$room" symbolic-ref --short -q HEAD || echo main)
    to_vm "$tmp/room.bundle" "$VM:/tmp/room.bundle" || rc=$?
    rm -rf "$tmp"
    [ "$rc" = 0 ] || return "$rc"
    on_vm "set -e
      sudo install -o gitspace -g gitspace -m 644 /tmp/room.bundle /srv/gitspace/room.bundle
      G='sudo -u gitspace git -C /srv/gitspace/room.git'
      \$G fetch -q --prune --update-head-ok /srv/gitspace/room.bundle '+refs/heads/*:refs/heads/*' '+refs/tags/*:refs/tags/*'
      \$G checkout -q -f '$branch' && \$G reset -q --hard '$branch'
      \$G log -1 --format='mirror now serves %h (%s) on $branch'"
}

case "${1:-status}" in
  status)
    vm instances describe "$VM" --format='value(name,status,scheduling.terminationTime)'
    curl -s --max-time 15 "$URL/api/health"; echo
    curl -s --max-time 15 "$URL/api/status"; echo ;;
  sync-room)
    sync_room ;;
  sync-bg)
    # Coalesce: while a sync runs, later triggers only mark it dirty; the runner loops until clean.
    mkdir -p "$(dirname "$LOG")"
    touch "$LOCK.dirty"
    if mkdir "$LOCK" 2>/dev/null; then
      ( trap 'rmdir "$LOCK"' EXIT
        while [ -e "$LOCK.dirty" ]; do
          rm -f "$LOCK.dirty"
          echo "[$(date -u +%FT%TZ)] sync" >> "$LOG"
          sync_room >> "$LOG" 2>&1 || echo "[$(date -u +%FT%TZ)] sync FAILED (the laptop keeps working; retry: $0 sync-room)" >> "$LOG"
        done ) </dev/null >/dev/null 2>&1 &
    fi ;;
  hooks)
    room=$(room_path)
    for h in post-commit post-checkout post-merge post-rewrite; do
      cat > "$room/.git/hooks/$h" <<EOF
#!/bin/sh
# gitspace: mirror room.git to the cloud web tier, in the background (scripts/gcp_mirror.sh). Never blocks git.
[ "\${ROOM_MIRROR:-on}" = off ] && exit 0
# only the room itself: a test's COPY of room.git carries this hook too, and must stay local
[ "\$(git rev-parse --show-toplevel 2>/dev/null)" = "$room" ] || exit 0
"$ROOT/scripts/gcp_mirror.sh" sync-bg >/dev/null 2>&1 || true
exit 0
EOF
      chmod +x "$room/.git/hooks/$h"
    done
    echo "hooks installed in $room/.git/hooks (post-commit, post-checkout, post-merge, post-rewrite)" ;;
  ship)
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    (cd "$ROOT" && COPYFILE_DISABLE=1 tar -czf "$tmp/app.tgz" --exclude='__pycache__' --exclude='.pytest_cache' \
        --exclude='web/tests' --exclude='bridge/test_*.py' --exclude='node_modules' --exclude='*.pyc' \
        --exclude='web/landing/live' \
        web bridge roomctl obs.py elastic/queries.py elastic/setup_elastic.py elastic/records.py elastic/ingest.py \
        elastic/mappings fake/out/demo.ndjson)
    to_vm "$tmp/app.tgz" "$VM:/tmp/app.tgz"
    on_vm "set -e
      sudo tar --warning=no-unknown-keyword -xzf /tmp/app.tgz -C /srv/gitspace/app
      sudo chown -R gitspace:gitspace /srv/gitspace/app
      sudo -u gitspace sh -c 'grep -v -i pytest /srv/gitspace/app/web/requirements.txt > /srv/gitspace/req.txt'
      sudo -u gitspace /srv/gitspace/.venv/bin/pip install -q -r /srv/gitspace/req.txt
      sudo systemctl restart gitspace-web && sleep 4 && systemctl is-active gitspace-web"
    curl -s --max-time 15 "$URL/api/health"; echo ;;
  logs)
    on_vm "sudo journalctl -u gitspace-web -n 60 --no-pager" ;;
  stop)
    vm instances stop "$VM" ;;
  start)
    vm instances start "$VM"
    echo "started; the external IP can change on start — check: $0 status (Caddy's hostname is IP-based)" ;;
  *)
    sed -n '2,15p' "$0"; exit 2 ;;
esac
