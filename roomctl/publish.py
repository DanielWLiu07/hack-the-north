"""After a commit, the snapshot to Elasticsearch (docs/14 Phase 2 step 8; docs/10 GAP 1).

git is the write path and the truth; this is what lets the read path see it. Without it the
cluster only ever holds fake data, and every panel returns nothing the moment real
perception runs — silently.

  objects + commit event   elastic/ingest.commit_actions — records.to_es_doc translates our
                           `id` to Elasticsearch's `object_id` (GAP 2) and pins the key set
  voxels                   perception/voxelize.index_staged, from the grid the scan staged (P6)
  what only the scan knows <repo>/.git/gitspace/scan.json, staged by the scanner:
                           {capture_id, at, meta_by_id: {object_id: {confidence, point_count,
                           observed_by, raw_description}}} — MergedObject.object_fields()

The documents are built from the COMMITTED tree (`repo.records(sha)`), never the working
tree: after `room add zones/desk/` the working tree holds changes the commit doesn't. So a
committed object can be one this scan never saw (a revert put it back in the tree, the robot
couldn't bring it back to the room): its words are carried forward at delivery (D44).

It never blocks a commit and never sends a parked key. No usable ELASTIC_URL / ELASTIC_API_KEY
(unset, or parked as `KEY=# …`) means no network at all: the actions go to the spool
(<repo>/.git/gitspace/spool/<sha>.json) and `room publish --flush` sends them later. So does a
cluster that doesn't answer. A document the cluster REJECTS is reported, not spooled — it's a
mapping bug and would fail again.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from roomctl.repo import Commit, Repo, default_path

ROOT = Path(__file__).resolve().parents[1]


def usable(value: str | None) -> str:
    """web/server.py's rule, shared: a parked key (`KEY=# parked …`) or an empty one is none."""
    v = (value or "").strip()
    return "" if (not v or v.startswith("#") or any(c.isspace() for c in v)) else v


def _import(folder: str, module: str):
    """elastic/ and perception/ use flat imports (`from records import …`)."""
    d = str(ROOT / folder)
    if d not in sys.path:
        sys.path.insert(0, d)
    return __import__(module)


def is_the_room(repo: Repo) -> bool:
    """Only the room's own repository ($ROOM_GIT_PATH) publishes by default. A scratch repo — a
    self-test, a CI run, a synthetic recording — must never land in the indices the demo
    reads: its commit would become the answer to "the way it was before dinner"."""
    return repo.path == default_path().resolve() or os.getenv("ROOM_PUBLISH_ANY") == "1"


def not_the_room(repo: Repo) -> str:
    return (f"not published — {repo.path} isn't the room ($ROOM_GIT_PATH = {default_path()}); "
            f"ROOM_PUBLISH_ANY=1 to publish it anyway")


def es_from_env():
    """A client, or None — and None means this process sends nothing."""
    url, key = usable(os.getenv("ELASTIC_URL")), usable(os.getenv("ELASTIC_API_KEY"))
    if not (url and key):
        return None
    from elasticsearch import Elasticsearch
    return Elasticsearch(url, api_key=key, request_timeout=30)


# ── the scan's half, staged for the commit ───────────────────────────────────

def _dir(repo: Repo) -> Path:
    return repo.path / ".git" / "gitspace"


def stage_scan(repo: Repo, capture_id: str, at: str, meta_by_id: dict[str, dict] | None = None,
               trace: dict | None = None, cloud: dict | None = None) -> Path:
    """Scanner side: what this scan knows that the YAML doesn't. A rescan replaces it.
    `trace` is obs.trace_fields() INSIDE the capture's transaction: the commit runs later,
    outside it, and its documents should still join the capture's waterfall (docs/10 P17)."""
    p = _dir(repo) / "scan.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"capture_id": capture_id, "at": at, "meta_by_id": meta_by_id or {},
                             "trace": trace or {}, "cloud": cloud}))
    return p


def staged_scan(repo: Repo) -> dict:
    p = _dir(repo) / "scan.json"
    return json.loads(p.read_text()) if p.is_file() else {}


# ── the commit's documents ───────────────────────────────────────────────────

def commit_time(repo: Repo, sha: str) -> str:
    """The commit's own date — room-events' @timestamp is the wall-clock -> sha bridge."""
    return repo.git("show", "-s", "--format=%cI", sha).stdout.strip()


def commit_actions(repo: Repo, c: Commit, scan: dict | None = None) -> list[dict]:
    import obs
    ingest = _import("elastic", "ingest")
    scan = scan if scan is not None else staged_scan(repo)
    trace = scan.get("trace") or obs.trace_fields()
    acts = ingest.commit_actions(c, repo.records(c.sha).values(), at=commit_time(repo, c.sha),
                                 capture_id=scan.get("capture_id"), meta_by_id=scan.get("meta_by_id"),
                                 trace=trace)
    if scan.get("cloud"):  # the capture's catalog doc, now that it has a commit (same _id: capture_id)
        acts.append(ingest.action("room-clouds", {**scan["cloud"], "commit_sha": c.sha, **trace}))
    return acts


@dataclass
class Published:
    tally: dict = field(default_factory=dict)   # index -> [written, already there, errors]
    spooled: int = 0
    reason: str = ""
    voxels: str = ""
    carried: str = ""                           # D44: descriptions carried forward, or why not

    def line(self) -> str:
        if self.reason and not self.spooled and not self.tally:
            return f"es: {self.reason}"
        if self.spooled:
            return f"es: {self.spooled} docs spooled ({self.reason}) — `room publish --flush` sends them"
        parts = [f"{w} {idx}" + (f" ({a} already there)" if a else "") + (f", {len(e)} REJECTED: {e[0]}" if e else "")
                 for idx, (w, a, e) in sorted(self.tally.items())]
        return ("es: " + ("; ".join(parts) or "nothing to write") + (f"; voxels: {self.voxels}" if self.voxels else "")
                + (f"; {self.carried}" if self.carried else ""))

    @property
    def rejected(self) -> int:
        return sum(len(e) for _, _, e in self.tally.values())


def _spool(repo: Repo, sha: str, actions: list[dict], reason: str) -> Published:
    p = _dir(repo) / "spool" / f"{sha}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(actions))
    return Published(spooled=len(actions), reason=reason)


def _deliver(repo: Repo, sha: str, actions: list[dict], es, write: Callable | None) -> Published:
    if es is None:
        return _spool(repo, sha, actions, "no usable ELASTIC_URL / ELASTIC_API_KEY — unset or parked")
    try:
        es.info()  # one cheap round trip: a cluster that doesn't answer is weather, not a bug
    except Exception as e:  # noqa: BLE001 — transport errors come in many classes
        return _spool(repo, sha, actions, f"cluster unreachable: {type(e).__name__}")
    carried = carry_descriptions(es, actions)
    write = write or _import("elastic", "ingest").write
    return Published(tally=write(es, actions), carried=carried)


def carry_descriptions(es, actions: list[dict]) -> str:
    """D44. An object in the commit that this scan didn't see has no words, and without them the
    vector leg can't find it. Descriptions are identity, like class and first_seen, so take
    raw_description + vlm_model (who wrote them, unchanged) from the object's newest room-objects
    doc that has them. confidence / observed_by / point_count stay empty: that is how a reader
    tells "described earlier" from "seen this scan". One search, at delivery, so a spooled
    commit gets them when it's flushed. Never stops the commit's own docs going out."""
    blank = {}
    for a in actions:
        src = a.get("_source") or {}
        if a.get("_index") == "room-objects" and src.get("object_id") and not src.get("raw_description"):
            blank.setdefault(src["object_id"], []).append(src)
    if not blank:
        return ""
    try:
        r = es.search(index="room-objects", size=len(blank), source=["object_id", "raw_description", "vlm_model"],
                      query={"bool": {"filter": [{"terms": {"object_id": sorted(blank)}},
                                                 {"exists": {"field": "raw_description.text"}}]}},
                      collapse={"field": "object_id"}, sort=[{"@timestamp": "desc"}])
        hits = r["hits"]["hits"]
    except Exception as e:  # noqa: BLE001 — old words are a nicety; the snapshot is not
        return f"{len(blank)} unseen object(s) sent without descriptions ({type(e).__name__})"
    n = 0
    for h in hits:
        src = h.get("_source") or {}
        if src.get("raw_description") and src.get("object_id") in blank:
            for doc in blank.pop(src["object_id"]):
                doc["raw_description"], doc["vlm_model"] = src["raw_description"], src.get("vlm_model")
            n += 1
    return f"{n} unseen object(s)' descriptions carried forward" + (f", {len(blank)} never described" if blank else "")


def publish_commit(repo: Repo, c: Commit, *, es="env", write: Callable | None = None,
                   voxels: Callable | None = None) -> Published:
    """The post-commit hook. `es="env"` builds the client from .env (None when parked) — and
    only for the room's own repository; a caller passing a client has chosen explicitly."""
    if es == "env" and not is_the_room(repo):
        return Published(reason=not_the_room(repo))
    client = es_from_env() if es == "env" else es
    out = _deliver(repo, c.sha, commit_actions(repo, c), client, write)
    if (_dir(repo) / "voxels.npz").is_file():
        index_staged = voxels or _import("perception", "voxelize").index_staged
        r = index_staged(repo.path, c.sha, c.parent, c.branch, commit_time(repo, c.sha), es=client)
        out.voxels = (f"{r.indexed} indexed" + (f", {r.spooled} spooled ({r.reason})" if r.spooled else "")
                      if hasattr(r, "indexed") else str(r))
    return out


def flush(repo: Repo, *, es="env", write: Callable | None = None) -> list[tuple[str, Published]]:
    """Send every spooled commit; a delivered one leaves the spool."""
    if es == "env" and not is_the_room(repo):
        return [("-", Published(reason=not_the_room(repo)))]
    client = es_from_env() if es == "env" else es
    done = []
    for p in sorted((_dir(repo) / "spool").glob("*.json")):
        res = _deliver(repo, p.stem, json.loads(p.read_text()), client, write)
        if not res.spooled:
            p.unlink()
        done.append((p.stem, res))
    return done
