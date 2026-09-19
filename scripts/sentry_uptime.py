#!/usr/bin/env python3
"""Point Sentry Uptime at the web tier — the sixth Sentry product (docs/18).

    .venv/bin/python scripts/sentry_uptime.py https://<host>/api/health

Idempotent: ONE monitor, named "gitspace web". Run it again with a new URL (a restarted tunnel,
then the AWS box) and the same monitor is re-pointed, so its history and alerts carry over.
/api/health answers 200 whenever the process is up (ES trouble is in the body), and Sentry's
health-check filter keeps those requests out of the span quota.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
NAME = "gitspace web"


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        print(__doc__)
        return 2
    url = sys.argv[1]
    load_dotenv(ROOT / ".env")
    org, token = os.environ["SENTRY_ORG_SLUG"].strip(), os.environ["SENTRY_AUTH_TOKEN"].strip()
    api = httpx.Client(base_url="https://us.sentry.io/api/0", timeout=30,
                       headers={"Authorization": f"Bearer {token}"})
    body = {"name": NAME, "url": url, "intervalSeconds": 60, "timeoutMs": 5000, "method": "GET",
            "environment": os.getenv("SENTRY_ENVIRONMENT", "htn2026")}
    existing = [m for m in api.get(f"/organizations/{org}/uptime/").raise_for_status().json() if m.get("name") == NAME]
    if existing:
        mid = existing[0]["id"]
        r = api.put(f"/projects/{org}/{existing[0].get('projectSlug', 'gitspace')}/uptime/{mid}/", json=body)
        verb = "re-pointed"
    else:
        r = api.post(f"/projects/{org}/gitspace/uptime/", json=body)
        verb = "created"
    if r.status_code >= 300:
        print(f"uptime: HTTP {r.status_code}: {r.text[:300]}")
        return 1
    m = r.json()
    print(f"uptime monitor {verb}: #{m.get('id')} {m.get('name')!r} -> {m.get('url')} every {m.get('intervalSeconds')} s "
          f"(status {m.get('status')}, env {m.get('environment')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
