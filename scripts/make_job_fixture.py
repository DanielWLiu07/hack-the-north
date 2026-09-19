#!/usr/bin/env python3
"""Regenerate docs/fixtures/restore-job.json from a REAL restore, never by hand.

It is POST /api/command {"command": "restore", "args": {"ref": <ref>}} through web/server.py's own
app, against the real room.git (git READS only: this path never writes the room), with a throwaway
job ledger so the live one isn't pre-seeded. The body is written exactly as the server answered it.
Andrew's edge and our tests read the same file (ANDREW-HANDOFF.md §2b).

    .venv/bin/python scripts/make_job_fixture.py            # restore study
    .venv/bin/python scripts/make_job_fixture.py movie-night
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "fixtures" / "restore-job.json"


def main() -> int:
    ref = sys.argv[1] if len(sys.argv) > 1 else "study"
    for name in ("SENTRY_DSN", "SENTRY_DSN_WEB", "ELASTIC_API_KEY", "OPENAI_API_KEY"):
        os.environ[name] = ""                                # this reads git; it sends nothing anywhere
    os.environ.setdefault("ROOM_GIT_PATH", str(ROOT / "room.git"))
    os.environ["WEB_ALLOWED_COMMANDS"] = "restore"
    os.environ.pop("JOBS_REAL_MOTION", None)
    with tempfile.TemporaryDirectory(prefix="fixture-jobs-") as ledger:
        os.environ["JOBS_DIR"] = ledger
        sys.path[:0] = [str(ROOT / "web"), str(ROOT)]
        os.chdir(ROOT / "web")
        from fastapi.testclient import TestClient
        import server
        r = TestClient(server.app).post("/api/command", json={"command": "restore", "args": {"ref": ref}})
    if r.status_code != 202:
        print(f"restore {ref}: HTTP {r.status_code} {r.text[:300]}", file=sys.stderr)
        return 1
    job = r.json()
    if not job.get("plan"):
        print(f"restore {ref}: no plan ({job.get('plan_unavailable')})", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(job, indent=2, ensure_ascii=False) + "\n")
    p = job["plan"]
    print(f"{OUT.relative_to(ROOT)}: {job['job_id']} restore {ref} -> {job['target'][:7]} on HEAD {job['head'][:7]}: "
          f"{len(p['ops'])} op(s), {len(p['unapplied'])} unapplied, frame {job['frame']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
