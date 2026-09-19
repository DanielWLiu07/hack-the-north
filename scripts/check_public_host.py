#!/usr/bin/env python3
"""scripts/check_public_host.py — does a public host behave EXACTLY like the one we already trust?

    .venv/bin/python scripts/check_public_host.py gitirl.health
    .venv/bin/python scripts/check_public_host.py gitirl.health --against gitspace-five.vercel.app

A new domain in front of the same deployment is a new ORIGIN, and an origin is where guards get lost:
a rewrite that doesn't carry over, a route that answers from the CDN instead of the box, a loopback-only
inlet that suddenly isn't. So this checks the guards themselves, not just that the site loads, and
compares the answers against the host we already verified. Read-only: every call is a GET, or a POST
that is REFUSED by design.
"""
from __future__ import annotations

import argparse
import json
import socket
import ssl
import sys
import urllib.error
import urllib.request

TIMEOUT = 20


def get(url: str, method: str = "GET", body: bytes | None = None, headers: dict | None = None) -> tuple[int, str, dict]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read(4000).decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, (e.read(2000) or b"").decode("utf-8", "replace"), dict(e.headers or {})
    except (urllib.error.URLError, OSError, ssl.SSLError) as e:
        return 0, f"{type(e).__name__}: {e}", {}


def cert_of(host: str) -> dict:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=TIMEOUT) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:      # verifies: a bad cert raises
            c = tls.getpeercert()
    names = sorted({v for k, v in c.get("subjectAltName", ()) if k == "DNS"})
    return {"names": names, "until": c.get("notAfter"), "issuer": dict(x[0] for x in c.get("issuer", ()))
            .get("organizationName")}


CHECKS = [
    # (label, path, method, body, headers, what must be true)
    ("health", "/api/health", "GET", None, None, lambda s, b: s == 200 and json.loads(b).get("ok") is True),
    ("routers", "/api/routers", "GET", None, None, lambda s, b: s == 200 and "jobs" in json.loads(b)["routers"]),
    ("state (poses)", "/api/state?ref=HEAD", "GET", None, None,
     lambda s, b: s == 200 and json.loads(b).get("frame") == "world_z_up"),
    ("live guard", "/live", "GET", None, None, lambda s, b: s in (401, 403, 404)),
    ("inlet guard", "/api/internal/event", "POST", b'{"event":"status","data":{}}',
     {"Content-Type": "application/json"}, lambda s, b: s in (401, 403, 404)),
    ("job write guard", "/api/jobs/job_0000000000000000/result", "POST", b'{"run_id":"probe","status":"running"}',
     {"Content-Type": "application/json"}, lambda s, b: s == 401),
    ("job read", "/api/jobs/job_0000000000000000", "GET", None, None,
     lambda s, b: s == 404 and json.loads(b).get("error") == "not_found"),
]


def run(host: str) -> dict:
    base = f"https://{host}"
    out: dict = {"host": host}
    try:
        out["cert"] = cert_of(host)
        out["cert_ok"] = host in out["cert"]["names"] or f"*.{host.split('.', 1)[1]}" in out["cert"]["names"]
    except Exception as e:  # noqa: BLE001
        out["cert"], out["cert_ok"] = {"error": f"{type(e).__name__}: {e}"}, False
    for label, path, method, body, headers, ok in CHECKS:
        status, text, _ = get(base + path, method, body, headers)
        try:
            passed = bool(ok(status, text))
        except Exception:  # noqa: BLE001 — a shape we did not expect is a failure, not a crash
            passed = False
        out[label] = {"status": status, "ok": passed, "body": text[:120]}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("host")
    ap.add_argument("--against", help="a host already trusted: every check must agree with it")
    a = ap.parse_args()
    new = run(a.host)
    old = run(a.against) if a.against else None
    print(f"\n{a.host}")
    print(f"  cert     {'OK ' if new['cert_ok'] else 'NO '} {new['cert'].get('names') or new['cert'].get('error')} "
          f"until {new['cert'].get('until')} ({new['cert'].get('issuer')})")
    bad = [] if new["cert_ok"] else ["cert"]
    for label, *_ in [(c[0],) for c in CHECKS]:
        r, o = new[label], (old or {}).get(label)
        agree = "" if o is None else ("  = " if o["status"] == r["status"] else f"  != {a.against} said {o['status']}")
        print(f"  {label:16} {'OK ' if r['ok'] else 'NO '} HTTP {r['status']}{agree}")
        if not r["ok"] or (o is not None and o["status"] != r["status"]):
            bad.append(label)
    print(f"\n{'ALL GOOD: ' + a.host + ' behaves like ' + (a.against or 'expected') if not bad else 'PROBLEMS: ' + ', '.join(bad)}\n")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
