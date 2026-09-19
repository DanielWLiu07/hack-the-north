#!/usr/bin/env python3
"""ONE read-only probe of Sentry's autofix endpoint, to run when the pause lifts (01:00).

    ../.venv/bin/python tools/verify_seer_autofix.py <issue_id>      # a known issue, e.g. 7741490949

It GETs /api/0/issues/<issue_id>/autofix/ once (never POSTs: it starts no run and spends no Seer
credit) and prints the HTTP status and the response's KEYS — enough to confirm or correct the shape
web/sentry_client.py was written against (docs/26-seer-embodied.md: "endpoints and payloads have
moved between versions"). It REFUSES to run while Sentry is parked in ../.env. When the shape is
confirmed, set SEER_VERIFIED = True in web/sentry_client.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

WEB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB))
load_dotenv(WEB.parent / ".env")

import sentry_client  # noqa: E402


def keys(obj, depth=0, prefix=""):
    if isinstance(obj, dict) and depth < 3:
        for k, v in obj.items():
            kind = type(v).__name__ + (f"[{len(v)}]" if isinstance(v, (list, dict, str)) else "")
            print(f"    {prefix}{k}: {kind}")
            keys(v[0] if isinstance(v, list) and v else v, depth + 1, prefix + "  ")


def main() -> int:
    client = sentry_client.SentryClient()
    st = client.state()
    if st["paused"] or not st["configured"]:
        print(f"REFUSING to call Sentry: {st['reason']}")
        return 2
    if len(sys.argv) != 2 or not sentry_client.ISSUE_ID.match(sys.argv[1]):
        print(__doc__)
        return 2
    url = f"{sentry_client.API}/issues/{sys.argv[1]}/autofix/"
    token = sentry_client.usable(os.getenv("SENTRY_AUTH_TOKEN"))
    r = httpx.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=15)
    print(f"GET {url}\n  -> HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        print("  body is not JSON:", r.text[:200])
        return 1
    if r.status_code >= 400:
        print("  detail:", str(body.get("detail") if isinstance(body, dict) else body)[:300])
        print("  404 = no autofix endpoint at this path/plan; 403 = the token needs another scope (the detail says which).")
        return 1
    print("  response keys:")
    keys(body)
    auto = body.get("autofix") if isinstance(body, dict) else None
    print("  status:", (auto or {}).get("status") if isinstance(auto, dict) else "(no `autofix` object — adjust sentry_client.ask_seer)")
    print("  readable text found by sentry_client:", bool(sentry_client._verdict_text(auto)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
