#!/usr/bin/env python3
"""Rotate the Elasticsearch runtime key with no outage window (elastic/ROTATION.md). Never prints
a key: only ids, names and results.

    1. a person mints the new key in Kibana (an API key can't mint a narrower one) and pastes it
       into ../.env as ELASTIC_API_KEY_NEW=...
    2. rotate_key.py promote   ELASTIC_API_KEY_OLD <- the live key, ELASTIC_API_KEY <- the new one
       (restart the long-running processes: web server, telemetry hub)
    3. rotate_key.py verify    every subsystem against ELASTIC_API_KEY; exit 0 only if all green
    4. rotate_key.py retire --expect-id <old id>
                               re-verifies, invalidates the OLD key, proves it is dead (401), and
                               removes ELASTIC_API_KEY_OLD from .env
       rotate_key.py rollback  any time before retire: ELASTIC_API_KEY <- ELASTIC_API_KEY_OLD
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from elasticsearch import AuthenticationException, Elasticsearch

import ingest
import setup_elastic as S
from queries import Queries

ENV = S.HERE.parent / ".env"
INDICES = S.INDICES + S.DATA_STREAMS
# What the services actually call: search/ES|QL/msearch (read), _mapping + _resolve/index
# (view_index_metadata), bulk index on snapshot indices and create on data streams, GET / from
# web's /api/health and the publish hook (monitor), semantic query + rerank + semantic_text (monitor_inference).
RUNTIME = {"cluster": ["monitor", "monitor_inference"],
           "index": ["read", "view_index_metadata", "index", "create_doc"]}
LONG_RUNNING = {"web server": re.compile(r"web[./]server|uvicorn.*server"), "telemetry hub": re.compile(r"telemetry[./]hub")}


# ── .env, edited in place, values never echoed ──────────────────────────────

def read_env(path: Path = ENV) -> list[str]:
    return path.read_text().splitlines(keepends=True)


def get(lines: list[str], name: str) -> str | None:
    for line in lines:
        if line.startswith(f"{name}="):
            v = line.split("=", 1)[1].strip()
            return None if not v or v.startswith("#") else v
    return None


def set_line(lines: list[str], name: str, value: str | None) -> list[str]:
    """Replace NAME=... (or append it); value None removes the line."""
    out, done = [], False
    for line in lines:
        if line.startswith(f"{name}="):
            if value is not None and not done:
                out.append(f"{name}={value}\n")
            done = True
        else:
            out.append(line)
    if not done and value is not None:
        out.append(f"{name}={value}\n")
    return out


def write_env(lines: list[str], path: Path = ENV) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(lines))
    os.chmod(tmp, path.stat().st_mode & 0o777)
    os.replace(tmp, path)  # atomic: no reader ever sees half a file


def promote(path: Path = ENV) -> str:
    lines = read_env(path)
    new, live = get(lines, "ELASTIC_API_KEY_NEW"), get(lines, "ELASTIC_API_KEY")
    if not new:
        raise SystemExit("promote: no ELASTIC_API_KEY_NEW in .env -- mint the key in Kibana first")
    if new == live:
        raise SystemExit("promote: ELASTIC_API_KEY_NEW is the key already live")
    lines = set_line(set_line(set_line(lines, "ELASTIC_API_KEY_OLD", live), "ELASTIC_API_KEY", new),
                     "ELASTIC_API_KEY_NEW", None)
    write_env(lines, path)
    return "ELASTIC_API_KEY <- new key; the previous one kept as ELASTIC_API_KEY_OLD (rollback)"


def rollback(path: Path = ENV) -> str:
    lines = read_env(path)
    old = get(lines, "ELASTIC_API_KEY_OLD")
    if not old:
        raise SystemExit("rollback: no ELASTIC_API_KEY_OLD in .env (already retired?)")
    write_env(set_line(set_line(lines, "ELASTIC_API_KEY", old), "ELASTIC_API_KEY_OLD", None), path)
    return "ELASTIC_API_KEY <- ELASTIC_API_KEY_OLD; restart the long-running processes"


# ── verification ─────────────────────────────────────────────────────────────

def client(key: str) -> Elasticsearch:
    return Elasticsearch(S.env("ELASTIC_URL"), api_key=key, request_timeout=60, retry_on_timeout=True, max_retries=3)


def started_before(pattern: re.Pattern, when: float) -> list[str]:
    """Processes matching `pattern` that started before `when` -- they still hold the old key."""
    ps = subprocess.run(["ps", "-eo", "lstart=,pid=,command="], capture_output=True, text=True).stdout
    stale = []
    for line in ps.splitlines():
        start, rest = line[:24], line[24:].strip()
        if pattern.search(rest) and "rotate_key" not in rest:
            t = datetime.strptime(start, "%a %b %d %H:%M:%S %Y").timestamp()
            if t < when:
                stale.append(rest.split()[0])
    return stale


def verify(key: str | None = None, web_url: str = "http://127.0.0.1:8000/api/health") -> bool:
    key = key or S.env("ELASTIC_API_KEY")
    es, results = client(key), []

    def check(name: str, fn) -> None:
        try:
            detail = fn()
            results.append((name, True, detail))
        except Exception as e:  # noqa: BLE001 -- every failure is a line in the report
            results.append((name, False, f"{type(e).__name__}: {str(e)[:160]}"))

    def identity():
        k = es.security.authenticate()["api_key"]
        return f"api key id {k['id']} ({k.get('name')})"

    def privileges():
        r = es.security.has_privileges(cluster=RUNTIME["cluster"],
                                       index=[{"names": INDICES, "privileges": RUNTIME["index"]}])
        missing = [c for c, ok in r["cluster"].items() if not ok] + \
                  [f"{i}:{p}" for i, ps in r["index"].items() for p, ok in ps.items() if not ok]
        assert not missing, f"missing {missing}"
        return "cluster " + ", ".join(RUNTIME["cluster"]) + " · index " + ", ".join(RUNTIME["index"])

    def search():
        q = Queries(es)
        hits = [h["object_id"] for h in q.search_objects("mug")]
        assert hits, "hybrid search returned nothing"
        q.lexical_only("mug"), q.semantic_only("mug"), q.trace_docs("0" * 32)
        head = q.commit_at(datetime.now(timezone.utc), branch="main")
        return f"hybrid+rerank {hits[:3]}, ES|QL head {head and head['commit_sha'][:8]}"

    def ingest_index():  # re-write one existing snapshot doc, byte for byte
        doc = es.search(index="room-clouds", size=1, sort=[{"@timestamp": "asc"}])["hits"]["hits"][0]["_source"]
        (ok, dup, errs), = ingest.write(es, [ingest.action("room-clouds", doc)]).values()
        assert ok == 1 and not errs, errs
        return f"index: room-clouds {doc['capture_id']} rewritten unchanged"

    def ingest_create():  # re-send existing data-stream docs: 409 proves the right, writes nothing
        acts = []
        for stream in ("room-events", "robot-telemetry"):
            q = {"term": {"event_type": "commit"}} if stream == "room-events" else {"match_all": {}}
            src = es.search(index=stream, size=1, query=q, sort=[{"@timestamp": "asc"}])["hits"]["hits"][0]["_source"]
            acts.append(ingest.action(stream, src))
        tally = ingest.write(es, acts)
        assert all(not errs and dup == 1 for ok, dup, errs in tally.values()), tally
        return "create_doc: room-events + robot-telemetry answered 409 already-there"

    def publish_client():
        sys.path.insert(0, str(S.HERE.parent))
        from roomctl import publish
        saved = os.environ.get("ELASTIC_API_KEY")
        os.environ["ELASTIC_API_KEY"] = key
        try:
            pc = publish.es_from_env()
            assert pc is not None, "publish.es_from_env() built no client"
            return f"roomctl.publish client: {pc.info()['version']['number']}"
        finally:
            if saved is None:
                os.environ.pop("ELASTIC_API_KEY", None)
            else:
                os.environ["ELASTIC_API_KEY"] = saved

    def restarted():
        stale = {name: started_before(pat, ENV.stat().st_mtime) for name, pat in LONG_RUNNING.items()}
        stale = {n: pids for n, pids in stale.items() if pids}
        assert not stale, f"started before .env changed (still on the old key): {stale} -- restart them"
        return "no web server / telemetry hub older than .env"

    def web():
        import json
        with urllib.request.urlopen(web_url, timeout=10) as r:
            h = json.load(r)
        assert h.get("ok"), h
        return f"{web_url} ok, elastic {h['elastic'].get('version')}"

    for name, fn in (("identity", identity), ("privileges", privileges), ("search", search),
                     ("ingest index", ingest_index), ("ingest create", ingest_create),
                     ("publish hook", publish_client), ("long-running processes", restarted), ("web tier", web)):
        check(name, fn)
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<24} {detail}")
    return all(ok for _, ok, _ in results)


def retire(expect_id: str) -> None:
    lines = read_env()
    old = get(lines, "ELASTIC_API_KEY_OLD")
    if not old:
        raise SystemExit("retire: no ELASTIC_API_KEY_OLD in .env -- nothing to retire")
    es_old = client(old)
    kid = es_old.security.authenticate()["api_key"]["id"]
    if kid != expect_id:
        raise SystemExit(f"retire: ELASTIC_API_KEY_OLD is key {kid}, not {expect_id} -- refusing")
    print("re-verifying the live key before touching the old one:")
    if not verify():
        raise SystemExit("retire: verification failed -- the old key stays live (rollback is available)")
    r = es_old.security.invalidate_api_key(ids=[kid])
    assert kid in r["invalidated_api_keys"] or kid in r.get("previously_invalidated_api_keys", []), r
    try:
        client(old).security.authenticate()
        raise SystemExit(f"retire: key {kid} still authenticates after invalidation -- investigate")
    except AuthenticationException as e:
        proof = f"HTTP {e.meta.status}"
    write_env(set_line(read_env(), "ELASTIC_API_KEY_OLD", None))
    print(f"  invalidated key id {kid}; a request with it now answers {proof} (dead). "
          f"ELASTIC_API_KEY_OLD removed from .env.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("promote"); sub.add_parser("rollback")
    v = sub.add_parser("verify"); v.add_argument("--web", default="http://127.0.0.1:8000/api/health")
    r = sub.add_parser("retire"); r.add_argument("--expect-id", required=True)
    a = ap.parse_args()
    if a.cmd == "promote":
        print(promote())
    elif a.cmd == "rollback":
        print(rollback())
    elif a.cmd == "verify":
        return 0 if verify(web_url=a.web) else 1
    elif a.cmd == "retire":
        retire(a.expect_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
