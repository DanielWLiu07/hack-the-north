#!/usr/bin/env bash
# The frontend on Vercel (docs/19 "As built"): web/landing + web/pages' assets from Vercel's CDN,
# every other path — /api/*, SSE, /object/<id>, /capture/<id>, /replay, landing/live/ — proxied
# to the GCP web tier (scripts/gcp_mirror.sh). Same origin, so no CORS and no page changes.
#
#   scripts/deploy_vercel.sh            build + deploy to production (https://gitspace-five.vercel.app)
#
# Vercel checks its own files BEFORE rewrites, so only real static files are served from the CDN.
# Exactly the file types web/server.py's LandingFiles serves are copied (serve.py, devlog.txt stay
# private), and landing/live/ is left out: it changes as captures land, so it always proxies.
# Hobby plan: hard limits, no overage billing.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
BACKEND=${BACKEND:-https://8-234-158-138.sslip.io}
OUT=~/.cache/gitspace/vercel-out

mkdir -p "$OUT"
find "$OUT" -mindepth 1 -maxdepth 1 ! -name .vercel -exec rm -rf {} +    # keep the project link
(cd "$ROOT" && .venv/bin/python - "$OUT" <<'EOF'
import re, shutil, sys
from pathlib import Path
out, web = Path(sys.argv[1]), Path("web")
SERVED = {".html", ".js", ".css", ".glb", ".png", ".jpg", ".webp", ".svg", ".woff2", ".json"}   # LandingFiles
n = 0
for f in (web / "landing").rglob("*"):
    rel = f.relative_to(web / "landing")
    if f.is_file() and f.suffix.lower() in SERVED and rel.parts[0] != "live" and "__pycache__" not in rel.parts:
        (out / rel).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(f, out / rel); n += 1
asset = re.compile(re.search(r'^ASSET = re\.compile\(r"(.+?)"\)', (web / "capture_api.py").read_text(), re.M).group(1))
(out / "pages").mkdir(exist_ok=True)
for f in (web / "pages").iterdir():                       # /pages/{name}: top level only; seer/ proxies
    if f.is_file() and asset.match(f.name):
        shutil.copy2(f, out / "pages" / f.name); n += 1
print(f"{n} static files")
EOF
)
cat > "$OUT/vercel.json" <<EOF
{
  "\$schema": "https://openapi.vercel.sh/vercel.json",
  "cleanUrls": false,
  "rewrites": [ { "source": "/(.*)", "destination": "$BACKEND/\$1" } ],
  "headers": [ { "source": "/(.*)", "headers": [ { "key": "Cache-Control", "value": "public, max-age=0, must-revalidate" } ] } ]
}
EOF
cd "$OUT"
[ -d .vercel ] || vercel link --yes --project gitspace
vercel deploy --prod --yes
curl -s --max-time 20 https://gitspace-five.vercel.app/api/health; echo
