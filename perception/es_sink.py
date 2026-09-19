"""perception's one door to Elasticsearch: bulk with retries, spool when away, replay later.

Every index perception writes goes through here, with elastic/ingest.py's rules: a snapshot
index gets a natural _id, so re-indexing overwrites; a data stream is op_type create, and a
409 there means "already written". A transient failure (connection, timeout, 408/429/5xx --
also per document) is retried with backoff, resending only what failed. An auth failure, a
parked key, or retries running out SPOOL the documents to disk instead of losing them or
stalling the pipeline (docs/11: indexing can fail at 4am without the robot caring), and
flush_spool() replays them. A document Elasticsearch REJECTS raises: that is a mapping bug,
not weather, and retrying can't fix it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger(__name__)

# index -> (op_type, the fields its natural _id is joined from) -- elastic/ingest.py's table
RULES = {
    "room-voxels": ("index", ("commit_sha", "voxel_key")),
    "room-clouds": ("index", ("capture_id",)),
    "room-observations": ("create", None),          # TSDS: _id comes from its dimensions + @timestamp
}
CHUNK = 500                  # documents per _bulk request
RETRIES = 3                  # attempts per chunk on a transient failure...
BACKOFF_S = 0.5              # ...sleeping this, doubled each time
TRANSIENT = {408, 429, 502, 503, 504}


@dataclass(frozen=True)
class IndexResult:
    indexed: int                # documents Elasticsearch acknowledged
    spooled: int = 0            # documents written to the spool instead
    reason: str | None = None   # why they were spooled


class Offline(Exception):
    """Elasticsearch can't take documents right now. Spool, don't crash."""


def doc_id(index: str, d: dict) -> str | None:
    """elastic/ingest.py natural_id: refuse rather than guess ("None:mug_a1b2" never overwrites)."""
    fields = RULES[index][1]
    if fields is None:
        return None
    bad = [f for f in fields if not isinstance(d.get(f), str) or not d[f]]
    if bad:
        raise ValueError(f"{index} doc needs non-empty {bad} for its _id")
    return ":".join(d[f] for f in fields)


def deliver(index: str, docs: list[dict], key: str, es=None, spool_dir: Path | None = None,
            sleep=time.sleep) -> IndexResult:
    """Bulk-write `docs` to `index`; if Elasticsearch is away, spool ALL of them under `key`."""
    if not docs:
        return IndexResult(0)
    try:
        es = es or _connect()
        _warn_id_reuse(es, index, docs)
        return IndexResult(_bulk(es, index, docs, sleep))
    except Offline as e:
        return _spool(index, docs, key, spool_dir, str(e))


REUSE_TOLERANCE_S = 5.0      # the same capture re-indexed (a replay, a rescan) carries the same @timestamp


def _warn_id_reuse(es, index: str, docs: list[dict]) -> int:
    """room-clouds is written with `index` and _id = capture_id, so a capture id issued TWICE overwrites the first
    capture's document without a sound. It happened: a simulated sender on the laptop and the real robot each count
    cap_NNNN from their own state file, and the index held sim cap_0010..0013 when the robot issued the same ids
    (2026-09-19). Re-indexing the SAME capture is normal and stays quiet; a DIFFERENT capture under a used id is data
    loss, so it is said out loud — an ERROR in the log and an issue in Sentry, tagged with both timestamps — and then
    the write proceeds (the newer capture is the one being asked for). -> how many collisions were found.
    Never raises: a failed lookup must not stop a capture being recorded."""
    if index != "room-clouds":
        return 0
    try:
        ids = [doc_id(index, d) for d in docs]
        got = es.mget(index=index, ids=ids, source_includes=["@timestamp", "capture_id", "point_count", "sentry_trace_id"])
        found = 0
        for d, hit in zip(docs, got.get("docs", [])):
            if not hit.get("found"):
                continue
            old = hit.get("_source") or {}
            if _same_moment(old.get("@timestamp"), d.get("@timestamp")):
                continue
            found += 1
            msg = (f"capture id REUSED: {d.get('capture_id')} already names a capture from {old.get('@timestamp')} "
                   f"({old.get('point_count')} points); it is being OVERWRITTEN by one from {d.get('@timestamp')} "
                   f"({d.get('point_count')} points). Two senders are counting from separate state — give one a prefix or move its counter")
            log.error(msg)
            try:
                import sentry_sdk
                with sentry_sdk.new_scope() as scope:
                    scope.set_tag("capture_id", str(d.get("capture_id")))
                    scope.set_tag("failure_kind", "capture_id_reused")
                    scope.set_context("collision", {"existing_at": old.get("@timestamp"), "existing_trace": old.get("sentry_trace_id"),
                                                    "incoming_at": d.get("@timestamp"), "existing_points": old.get("point_count"),
                                                    "incoming_points": d.get("point_count")})
                    sentry_sdk.capture_message(msg, level="error")
            except Exception:  # noqa: BLE001 -- no Sentry, the log line stands
                pass
        return found
    except Exception as e:  # noqa: BLE001
        log.debug("id-reuse lookup skipped: %s", e)
        return 0


def _same_moment(a, b) -> bool:
    from datetime import datetime
    try:
        ta, tb = (datetime.fromisoformat(str(x).replace("Z", "+00:00")) for x in (a, b))
        return abs((ta - tb).total_seconds()) <= REUSE_TOLERANCE_S
    except (TypeError, ValueError):
        return a == b


def flush_spool(es=None, spool_dir: Path | None = None, sleep=time.sleep,
                indices: tuple[str, ...] | None = None) -> dict[str, IndexResult]:
    """Replay spooled documents, oldest first, as {"<index>/<key>": result}. A file is deleted
    only once all of it is written; the first offline failure stops the run."""
    root = _spool_dir(spool_dir)
    files = sorted((f for i in (indices or tuple(RULES)) for f in (root / i).glob("*.jsonl")),
                   key=lambda f: f.stat().st_mtime)
    out: dict[str, IndexResult] = {}
    if not files:
        return out
    try:
        es = es or _connect()
    except Offline as e:
        return {f"{f.parent.name}/{f.stem}": IndexResult(0, len(_read(f)), str(e)) for f in files}
    for f in files:
        docs = _read(f)
        try:
            out[f"{f.parent.name}/{f.stem}"] = IndexResult(_bulk(es, f.parent.name, docs, sleep))
        except Offline as e:
            out[f"{f.parent.name}/{f.stem}"] = IndexResult(0, len(docs), str(e))
            break
        f.unlink()
    return out


def _bulk(es, index: str, docs: list[dict], sleep) -> int:
    op = RULES[index][0]
    written = 0
    for i in range(0, len(docs), CHUNK):
        pending = docs[i:i + CHUNK]
        for attempt in range(RETRIES):
            last = attempt == RETRIES - 1
            try:
                resp = es.bulk(operations=[x for d in pending for x in (_action(op, index, d), d)],
                               refresh="wait_for")
            except Exception as e:  # noqa: BLE001 - classified below; anything unknown re-raises
                kind = _failure(e)
                if kind is None:
                    raise
                if kind == "auth" or last:
                    raise Offline(f"{kind}: {type(e).__name__}: {e}") from e
                sleep(BACKOFF_S * 2 ** attempt)
                continue
            retry, rejected = [], []
            for d, item in zip(pending, resp["items"]):
                r = next(iter(item.values()))
                status = r.get("status", 0)
                if 200 <= status < 300 or (status == 409 and op == "create"):   # 409 on create: already there
                    written += 1
                elif status in TRANSIENT or status >= 500:
                    retry.append(d)
                else:
                    rejected.append(f"{doc_id(index, d) or d.get('capture_id')}: {r.get('error')}")
            if rejected:
                raise RuntimeError(f"{len(rejected)} {index} docs rejected; first: {rejected[0]}")
            if not retry:
                break
            if last:
                raise Offline(f"transient: {len(retry)} docs still refused after {RETRIES} attempts")
            pending = retry
            sleep(BACKOFF_S * 2 ** attempt)
    return written


def _action(op: str, index: str, d: dict) -> dict:
    meta = {"_index": index}
    if (i := doc_id(index, d)) is not None:
        meta["_id"] = i
    return {op: meta}


def _failure(e: Exception) -> str | None:
    """"auth" (don't retry), "transient" (retry, then spool) or None (a real error: raise).
    Duck-typed on status_code, so it holds for elasticsearch's ApiError and for test fakes."""
    status = getattr(e, "status_code", None)
    if isinstance(status, int):
        return "auth" if status in (401, 403) else "transient" if status in TRANSIENT or status >= 500 else None
    if isinstance(e, (ConnectionError, TimeoutError)):
        return "transient"
    try:
        from elastic_transport import TransportError        # the client's connection/timeout errors
    except ImportError:
        return None
    return "transient" if isinstance(e, TransportError) else None


def _usable(value: str | None) -> str:
    """web/server.py's rule: a parked key is `# parked...` -- non-empty, and python-dotenv
    hands it over as a literal value. A credential never starts with '#' or holds spaces."""
    v = (value or "").strip()
    return "" if (not v or v.startswith("#") or any(c.isspace() for c in v)) else v


def _connect():
    """elastic/setup_elastic.connect(), but never with a parked or missing key: that would
    spend a round trip to learn what .env already says."""
    env = {**dotenv_values(ROOT / ".env"), **os.environ}
    if not _usable(env.get("ELASTIC_API_KEY")) or not _usable(env.get("ELASTIC_URL")):
        raise Offline("ELASTIC_API_KEY/ELASTIC_URL parked or unset")
    try:
        sys.path.insert(0, str(ROOT / "elastic"))
        from setup_elastic import SetupError, connect         # loads .env; needs the client package
    except ImportError as e:
        raise Offline(f"no elasticsearch client: {e}") from e
    try:
        return connect()
    except SetupError as e:
        raise Offline(f"connect: {e}") from e


def _spool_dir(spool_dir: Path | None) -> Path:
    return Path(spool_dir or os.getenv("GITSPACE_SPOOL") or ROOT / ".spool")


def _spool(index: str, docs: list[dict], key: str, spool_dir: Path | None, reason: str) -> IndexResult:
    root = _spool_dir(spool_dir)
    (root / index).mkdir(parents=True, exist_ok=True)
    if not (root / ".gitignore").exists():
        (root / ".gitignore").write_text("*\n")                 # a spool is never committed
    path = root / index / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', key)}.jsonl"
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(d, sort_keys=True) + "\n" for d in docs))
    tmp.replace(path)                                         # never a half-written file
    log.warning("%s: %d docs spooled to %s (%s)", index, len(docs), path, reason)
    return IndexResult(0, len(docs), reason)


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]
