#!/usr/bin/env bash
# scripts/bootstrap_laptop.sh — set up the laptop brain. Run it the night before, on a
# network you trust: venue wifi cannot be relied on for multi-GB downloads (risk R1).
#
#   1. preflight   git, curl, uv/python, disk, network
#   2. .env        create/complete from .env.example, validate. Values are never printed.
#   3. venv        .venv on Python 3.11 (uv if present, else pythonX.Y -m venv)
#   4. deps        scripts/requirements-laptop.txt + any <track>/requirements.txt
#   5. weights     YOLO into $MODELS_DIR (outside the repo); SAM 3 only with --with-sam3
#   6. offline     scripts/check_offline.py — the critical path, with the network blocked
#
# Usage: scripts/bootstrap_laptop.sh [--env-only] [--offline] [--with-sam3] [--recreate]
#   --env-only   only the .env check. Re-run after collecting keys at a booth.
#   --offline    no network: rebuild from the uv cache, skip downloads and live key checks.
#   --with-sam3  also fetch facebook/sam3 (gated, ~3.5 GB, HF_TOKEN with access granted).
#                SAM 3 is meant to run on Baseten; this is the local fallback only.
#   --recreate   delete and rebuild .venv
#
# Env overrides: PYTHON_VERSION (default 3.11), YOLO_WEIGHTS (space-separated asset names)
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VENV=$ROOT/.venv
PY=$VENV/bin/python
ENV_FILE=$ROOT/.env
ENV_EXAMPLE=$ROOT/.env.example
# 3.11: already on this laptop, and torch/open3d/rerun/ultralytics all ship 3.11 wheels.
PYTHON_VERSION=${PYTHON_VERSION:-3.11}
# BB's example_yolo.py / example_segmentation.py load the first two by bare name.
# yolo26 is the drop-in upgrade; m-seg is affordable on the laptop, not on the Pi.
YOLO_WEIGHTS=${YOLO_WEIGHTS:-"yolo11n.pt yolo11s-seg.pt yolo11n-seg.pt yolo26n.pt yolo26n-seg.pt yolo26s-seg.pt yolo26m-seg.pt"}
TRACK_DIRS="perception roomctl viz agent elastic web fake tests"   # robot/ runs on the Pi
DEFAULT_MODELS_DIR='${HOME}/.cache/gitspace/models'

# Must be set for the capture→perception→git→Rerun→robot path to work at all, offline.
CRITICAL_KEYS="PI_HOST PI_PORT LAPTOP_IP RERUN_ADDR ROOM_GIT_PATH CLOUD_CACHE_DIR CALIB_PATH
  MODELS_DIR ROOM_ORIGIN_X ROOM_ORIGIN_Y ROOM_ORIGIN_Z ROOM_CUBE_SIZE OCTREE_LEVELS
  QUANT_POSITION_M QUANT_YAW_DEG ANCHOR_TAG_ID"
NUMERIC_KEYS="PI_PORT ROOM_ORIGIN_X ROOM_ORIGIN_Y ROOM_ORIGIN_Z ROOM_CUBE_SIZE OCTREE_LEVELS
  QUANT_POSITION_M QUANT_YAW_DEG ANCHOR_TAG_ID"
TONIGHT_KEYS="ELASTIC_URL ELASTIC_API_KEY SENTRY_DSN HF_TOKEN"   # KEYS.md "get these tonight"
BOOTH_KEYS="OPENAI_API_KEY BASETEN_API_KEY"

ENV_ONLY=0 OFFLINE=0 WITH_SAM3=0 RECREATE=0
for arg in "$@"; do
  case $arg in
    --env-only)  ENV_ONLY=1 ;;
    --offline)   OFFLINE=1 ;;
    --with-sam3) WITH_SAM3=1 ;;
    --recreate)  RECREATE=1 ;;
    -h|--help)   sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
    *)           echo "unknown flag: $arg (see --help)" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then B=$'\033[1m' G=$'\033[32m' Y=$'\033[33m' R=$'\033[31m' D=$'\033[2m' N=$'\033[0m'
else B='' G='' Y='' R='' D='' N=''; fi
WARNS=0 FAILS=0
step() { printf '\n%s== %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
note() { printf '  %s·%s %s\n' "$D" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; WARNS=$((WARNS + 1)); }
bad()  { printf '  %s✗%s %s\n' "$R" "$N" "$*"; FAILS=$((FAILS + 1)); }
die()  { bad "$*"; exit 1; }

# Value of KEY in an env file, inline comment and quotes stripped. Status 1 if absent.
env_get() {
  local line
  line=$(grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" "${2:-$ENV_FILE}" | tail -n 1) || return 1
  printf '%s' "${line#*=}" |
    sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^"(.*)"$/\1/'"; s/^'(.*)'\$/\\1/"
}
# KEY_PARKED is how an API gets paused on purpose (e.g. to save quota before a demo). A parked
# key is not "missing": appending an empty KEY= would sit BELOW the renamed line when it is
# restored, and dotenv's last-wins would keep the API off with no error.
is_parked() { env_get "${1}_PARKED" >/dev/null; }
# Empty, or still the .env.example placeholder.
is_unset() { case $1 in '' | *xxxxx* | *XXXXX* | *'<'*'>'*) return 0 ;; esac; return 1; }
# ~, ${HOME} and $HOME expanded; relative paths are relative to the repo root.
expand_path() {
  local p=$1
  case $p in '~' | '~/'*) p=$HOME${p#\~} ;; esac
  p=${p//'${HOME}'/$HOME}
  p=${p//'$HOME'/$HOME}
  case $p in /*) ;; *) p=$ROOT/${p#./} ;; esac
  printf '%s' "$p"
}
# HTTP status for a GET, with the auth header passed on stdin so no secret reaches argv/ps.
http_status() {
  printf 'url = "%s"\nheader = "Authorization: %s"\n' "$1" "$2" |
    curl -s -o /dev/null -w '%{http_code}' --max-time 10 -K - 2>/dev/null || true
}
free_gb() { df -Pk "$1" | awk 'NR == 2 { printf "%.1f", $4 / 1048576 }'; }

check_env() {
  step ".env"
  if grep -qxE '[[:space:]]*/?\.env[[:space:]]*' "$ROOT/.gitignore" 2>/dev/null; then
    ok ".env is gitignored"
  else
    bad ".env is NOT in .gitignore — fix before anything is committed"
  fi
  if [ ! -f "$ENV_FILE" ]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    warn "no .env — created one from .env.example. Fill in the secrets, then: $0 --env-only"
  fi
  case $(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE") in
    600 | 400) ;;
    *) warn ".env is readable by other users — chmod 600 .env" ;;
  esac

  # Append keys that .env.example has grown since .env was made. Never edits existing lines.
  local key v missing="" parked=""
  for key in $(grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_EXAMPLE" | tr -d =); do
    if is_parked "$key"; then
      parked="$parked $key"
    elif ! env_get "$key" >/dev/null; then
      missing="$missing $key"
    fi
  done
  if [ -n "$parked" ]; then note "parked on purpose (*_PARKED), left alone:$parked"; fi
  if [ -n "$missing" ]; then
    {
      printf '\n# ── added by scripts/bootstrap_laptop.sh %s, defaults from .env.example ──\n' "$(date +%F)"
      for key in $missing; do grep -E "^$key=" "$ENV_EXAMPLE" | head -n 1; done
    } >>"$ENV_FILE"
    warn "appended to .env with example defaults:$missing"
  fi

  local n=0 total=0
  for key in $CRITICAL_KEYS; do
    total=$((total + 1))
    v=$(env_get "$key") || v=""
    if is_unset "$v"; then bad "$key is unset — the offline critical path needs it"; else n=$((n + 1)); fi
  done
  if [ "$n" = "$total" ]; then ok "critical-path keys: $n/$total set"; fi
  for key in $NUMERIC_KEYS; do
    v=$(env_get "$key") || v=""
    if ! printf '%s' "$v" | grep -qE '^-?[0-9]+(\.[0-9]+)?$'; then bad "$key='$v' is not a number"; fi
  done

  local lip addrs
  lip=$(env_get LAPTOP_IP) || lip=""
  case $(env_get RERUN_ADDR || true) in
    *"//$lip:"*) ;;
    *) warn "RERUN_ADDR doesn't point at LAPTOP_IP — the Pi would log to the wrong viewer" ;;
  esac
  addrs=$(ifconfig 2>/dev/null || ip -4 addr 2>/dev/null || true)
  if [ -n "$lip" ] && grep -qwF -- "$lip" <<<"$addrs"; then
    ok "LAPTOP_IP $lip is on this machine"
  else
    note "LAPTOP_IP $lip isn't on this machine yet — expected until you're on the travel router"
  fi

  for key in $TONIGHT_KEYS; do
    v=$(env_get "$key") || v=""
    if is_unset "$v" && ! is_parked "$key"; then warn "$key is empty — a get-it-tonight key (KEYS.md)"; fi
  done
  for key in $BOOTH_KEYS; do
    v=$(env_get "$key") || v=""
    if is_unset "$v" && ! is_parked "$key"; then note "$key is empty — collect at the booth"; fi
  done
  v=$(env_get SENTRY_DSN) || v=""
  if ! is_unset "$v" && ! printf '%s' "$v" | grep -qE '^https://[0-9a-f]{32}@[^/]+/[0-9]+$'; then
    bad "SENTRY_DSN doesn't look like a DSN (https://<32 hex>@<host>/<project id>)"
  fi

  if [ "$OFFLINE" = 1 ]; then
    note "--offline: skipping live key checks"
    return 0
  fi
  # key, url, auth scheme — a 401/403 means the key itself is wrong
  local url scheme code
  while read -r key url scheme; do
    v=$(env_get "$key") || v=""
    is_unset "$v" && continue
    if [ "$key" = ELASTIC_API_KEY ]; then
      url=$(env_get ELASTIC_URL) || url=""
      if is_unset "$url"; then note "can't verify ELASTIC_API_KEY until ELASTIC_URL is real"; continue; fi
    fi
    code=$(http_status "$url" "$scheme $v")
    case $code in
      200) ok "$key accepted" ;;
      401 | 403) bad "$key rejected ($code)" ;;
      000) warn "$key: couldn't reach the service to verify" ;;
      *) warn "$key: unexpected HTTP $code" ;;
    esac
  done <<'CHECKS'
ELASTIC_API_KEY - ApiKey
HF_TOKEN https://huggingface.co/api/whoami-v2 Bearer
OPENAI_API_KEY https://api.openai.com/v1/models Bearer
BASETEN_API_KEY https://api.baseten.co/v1/models Api-Key
CHECKS
}

summary() {
  step "summary"
  if [ "$FAILS" -gt 0 ]; then
    printf '  %s%d failed%s, %d warnings\n' "$R" "$FAILS" "$N" "$WARNS"
  else
    printf '  %sno failures%s, %d warnings\n' "$G" "$N" "$WARNS"
  fi
  if [ "$ENV_ONLY" = 0 ] && [ -x "$PY" ]; then
    printf '\n  source .venv/bin/activate\n'
    printf '  %s --env-only    after collecting booth keys\n' "scripts/bootstrap_laptop.sh"
    printf '  %s --offline     rebuild at the venue from the uv cache\n' "scripts/bootstrap_laptop.sh"
  fi
  [ "$FAILS" -eq 0 ]
}

# ── 1. preflight ───────────────────────────────────────────────────────────────
step "preflight"
command -v git >/dev/null || die "git not found — roomctl shells out to real git"
command -v curl >/dev/null || die "curl not found"
[ -f "$ENV_EXAMPLE" ] || die "missing $ENV_EXAMPLE"
if command -v uv >/dev/null; then
  UV=1
  ok "uv $(uv --version | cut -d' ' -f2)"
else
  UV=0
  note "uv not found — using python$PYTHON_VERSION -m venv + pip (then --offline can't rebuild)"
fi

# ── 2. .env ────────────────────────────────────────────────────────────────────
check_env
if [ "$ENV_ONLY" = 1 ]; then summary; exit $?; fi

MODELS_DIR=$(env_get MODELS_DIR) || MODELS_DIR=""
is_unset "$MODELS_DIR" && MODELS_DIR=$DEFAULT_MODELS_DIR
MODELS_DIR=$(expand_path "$MODELS_DIR")
WEIGHTS_DIR=$MODELS_DIR/weights

step "disk + network"
NEED=3
[ "$WITH_SAM3" = 1 ] && NEED=7
FREE=$(free_gb "$ROOT")
if awk "BEGIN { exit !($FREE < $NEED) }"; then
  if [ -x "$PY" ]; then warn "only $FREE GB free (a fresh install needs ~$NEED GB)"
  else die "only $FREE GB free, need ~$NEED GB for the venv$([ "$WITH_SAM3" = 1 ] && echo ' + SAM 3')"; fi
else
  ok "$FREE GB free (need ~$NEED GB)"
fi
if [ "$OFFLINE" = 1 ]; then
  note "--offline: not touching the network"
else
  for host in pypi.org github.com huggingface.co; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "https://$host/" || true)
    [ "$code" != 000 ] || die "$host unreachable. Use a trusted network, or --offline to rebuild from cache"
  done
  ok "pypi.org, github.com, huggingface.co reachable"
fi

# ── 3. venv ────────────────────────────────────────────────────────────────────
step "venv — Python $PYTHON_VERSION at .venv"
UV_OFFLINE=""
[ "$OFFLINE" = 1 ] && UV_OFFLINE="--offline"
if [ -d "$VENV" ] && [ "$RECREATE" = 1 ]; then
  rm -rf "$VENV"
  note "removed old .venv"
fi
if [ -x "$PY" ]; then
  have=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  [ "$have" = "$PYTHON_VERSION" ] || die ".venv is Python $have, want $PYTHON_VERSION — re-run with --recreate"
  ok "reusing .venv (Python $have)"
elif [ -e "$VENV" ]; then
  die ".venv exists but has no python — re-run with --recreate"
elif [ "$UV" = 1 ]; then
  uv venv -q $UV_OFFLINE --python "$PYTHON_VERSION" "$VENV"
  ok "created .venv with uv"
else
  command -v "python$PYTHON_VERSION" >/dev/null ||
    die "python$PYTHON_VERSION not found — install uv (brew install uv) or python@$PYTHON_VERSION"
  "python$PYTHON_VERSION" -m venv "$VENV"
  ok "created .venv"
fi

# ── 4. deps ────────────────────────────────────────────────────────────────────
step "deps"
set -- -r "$ROOT/scripts/requirements-laptop.txt"
used="scripts/requirements-laptop.txt"
for d in $TRACK_DIRS; do
  if [ -f "$ROOT/$d/requirements.txt" ]; then
    set -- "$@" -r "$ROOT/$d/requirements.txt"
    used="$used $d/requirements.txt"
  fi
done
# Ultralytics' SAM 3 loader pip-installs this from git at runtime. Offline, that's a crash.
if [ "$WITH_SAM3" = 1 ] && [ "$OFFLINE" = 0 ]; then
  set -- "$@" "clip @ git+https://github.com/ultralytics/CLIP.git"
  used="$used +CLIP"
fi
note "$used"
if [ "$UV" = 1 ]; then
  uv pip install -q --python "$PY" $UV_OFFLINE "$@"
else
  [ "$OFFLINE" = 0 ] || die "--offline needs uv — pip has no cache-only install"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q "$@"
fi
ok "$("$PY" -c 'import importlib.metadata as m; print(len(list(m.distributions())))') packages installed"

# Every python in this venv finds the weights by bare name, and ultralytics never
# reaches for the network unless you export YOLO_OFFLINE=0.
"$PY" - "$MODELS_DIR" <<'PY'
import os, sys, sysconfig
models = sys.argv[1]
env = {"MODELS_DIR": models, "YOLO_CONFIG_DIR": models, "YOLO_OFFLINE": "1"}
line = "import os; " + "; ".join(f"os.environ.setdefault({k!r}, {v!r})" for k, v in env.items())
path = os.path.join(sysconfig.get_paths()["purelib"], "gitspace_env.pth")
with open(path, "w") as f:
    f.write("# written by scripts/bootstrap_laptop.sh — offline defaults for every python in .venv\n")
    f.write(line + "\n")
PY
ok "venv defaults: YOLO_CONFIG_DIR=$MODELS_DIR, YOLO_OFFLINE=1 (gitspace_env.pth)"

# ── 5. weights ─────────────────────────────────────────────────────────────────
step "model weights → $WEIGHTS_DIR"
mkdir -p "$WEIGHTS_DIR"
if [ "$OFFLINE" = 1 ]; then
  note "--offline: using what's already there"
else
  YOLO_OFFLINE=0 WEIGHTS_DIR="$WEIGHTS_DIR" YOLO_WEIGHTS="$YOLO_WEIGHTS" "$PY" - <<'PY' || bad "YOLO download failed"
import os, sys
from pathlib import Path
from ultralytics import settings
from ultralytics.utils.checks import check_font
from ultralytics.utils.downloads import attempt_download_asset

weights = Path(os.environ["WEIGHTS_DIR"])
settings.update(weights_dir=str(weights), sync=False)  # bare names resolve here; no telemetry
missing = []
for name in os.environ["YOLO_WEIGHTS"].split():
    path = weights / name
    if not path.exists():
        attempt_download_asset(str(path))
    if path.exists() and path.stat().st_size > 1e5:
        print(f"  \033[32m✓\033[0m {name:<16} {path.stat().st_size / 1e6:6.1f} MB")
    else:
        missing.append(name)
check_font("Arial.ttf")  # PIL-mode plotting fetches this lazily otherwise
sys.exit(f"  not downloaded: {' '.join(missing)}" if missing else 0)
PY
fi
if [ "$WITH_SAM3" = 1 ]; then
  if [ -f "$WEIGHTS_DIR/sam3.pt" ]; then
    ok "sam3.pt already present"
  elif [ "$OFFLINE" = 1 ]; then
    warn "--offline: can't fetch sam3.pt"
  else
    tok=$(env_get HF_TOKEN) || tok=""
    if is_unset "$tok"; then
      warn "--with-sam3 needs HF_TOKEN in .env — skipped"
    else
      HF_TOKEN=$tok WEIGHTS_DIR="$WEIGHTS_DIR" "$PY" - <<'PY' || warn "sam3.pt not downloaded — request access at huggingface.co/facebook/sam3 (manual approval)"
import os
from huggingface_hub import hf_hub_download
path = hf_hub_download("facebook/sam3", "sam3.pt", local_dir=os.environ["WEIGHTS_DIR"])
print(f"  \033[32m✓\033[0m sam3.pt {os.path.getsize(path) / 1e9:.1f} GB")
PY
    fi
  fi
fi

# ── 6. prove it works with no internet ─────────────────────────────────────────
step "offline check — network blocked"
if "$PY" "$ROOT/scripts/check_offline.py"; then
  ok "the critical path's software stack runs with no internet"
else
  bad "offline check failed — fix it before you leave a good network"
fi

summary
