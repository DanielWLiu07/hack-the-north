#!/usr/bin/env python3
"""A read-only probe of Sentry's Seer (autofix) endpoints.

    ../.venv/bin/python tools/verify_seer_autofix.py <issue_id>      # a known issue, e.g. 7741490949

Three GETs, never a POST: it starts no run and spends no Seer credit. It prints each HTTP status and
the response's KEYS — enough to confirm or correct the shape web/sentry_client.py reads
(docs/26-seer-embodied.md: "endpoints and payloads have moved between versions"). It REFUSES to run
while Sentry is parked in ../.env.

Result on 2026-09-19 against sentry.io:
    GET /issues/<id>/autofix/                              404, empty body   <- the path docs/26 assumed
    GET /organizations/<org>/issues/<id>/autofix/          200 {"autofix": null}
    GET /organizations/<org>/issues/<id>/autofix/setup/    200 integration.ok=false (integration_missing),
                                                               seerReposLinked=false, autofixEnabled=true, quota=true
SEER_VERIFIED in web/sentry_client.py becomes True only after ONE real run has been started from the
board and read back; this probe cannot show that, by design.
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
    token = sentry_client.usable(os.getenv("SENTRY_AUTH_TOKEN"))
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    issue, org, ok = sys.argv[1], st["org"], True
    for label, path in (("legacy path (docs/26)", f"/issues/{issue}/autofix/"),
                        ("org-scoped, what sentry_client uses", f"/organizations/{org}/issues/{issue}/autofix/"),
                        ("setup", f"/organizations/{org}/issues/{issue}/autofix/setup/")):
        r = httpx.get(sentry_client.API + path, headers=headers, timeout=15)
        print(f"GET {path}   [{label}]\n  -> HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError:
            print("  body is not JSON:", repr(r.text[:200]))
            ok = ok and label.startswith("legacy")
            continue
        if r.status_code >= 400:
            print("  detail:", str(body.get("detail") if isinstance(body, dict) else body)[:300])
            print("  404 = nothing at this path; 403 = the token needs another scope (the detail says which).")
            ok = ok and label.startswith("legacy")
            continue
        print("  response keys:")
        keys(body)
        if label.startswith("org"):
            auto = sentry_client._autofix(body)
            print("  run:", f"status {auto.get('status')}" if auto else "none yet (autofix is null)")
            print("  readable text found by sentry_client:", bool(sentry_client._verdict_text(auto)))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
