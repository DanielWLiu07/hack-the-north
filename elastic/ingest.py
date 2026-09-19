#!/usr/bin/env python3
"""Bulk writers. The two rules from docs/13:

  - snapshot indices get an explicit natural _id, so re-ingest overwrites instead of duplicating
  - data streams get op_type "create" ("index" is rejected); a 409 there means already written

    python ingest.py ../story_docs.json            # replay a JSON array of bulk actions
    python ingest.py ../story_docs.json --dry-run  # print the _bulk body, send nothing

Run setup_elastic.py first: every mapping is dynamic:strict, so nothing here can create an
index or invent a field type.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from elasticsearch import Elasticsearch, helpers

from setup_elastic import DATA_STREAMS, SetupError, connect

# Natural keys for the snapshot indices (docs/13 "Document IDs: the 3am feature"): the _id is
# these fields joined with ":", so re-ingest overwrites instead of duplicating.
SNAPSHOT_ID = {
    "room-objects": ("commit_sha", "object_id"),
    "room-voxels": ("commit_sha", "voxel_key"),
    "room-clouds": ("capture_id",),
}


def natural_id(index: str, doc: dict, fields: tuple[str, ...]) -> str:
    """Refuse rather than guess: a None commit_sha would make the _id "None:mug_a1b2", and a
    git-side record ('id', not 'object_id') has no object_id at all -- both silently break
    idempotent re-indexing."""
    if index == "room-objects" and "object_id" not in doc and "id" in doc:
        raise ValueError("room-objects doc has the git-side 'id', not 'object_id' -- "
                         "build it with records.to_es_doc()")
    bad = [f for f in fields if not isinstance(doc.get(f), str) or not doc[f]]
    if bad:
        raise ValueError(f"{index} doc needs non-empty {bad} for its _id")
    return ":".join(doc[f] for f in fields)


def action(index: str, doc: dict) -> dict:
    """One helpers.bulk action for `doc` bound for `index`."""
    if index in SNAPSHOT_ID:
        return {"_op_type": "index", "_index": index, "_id": natural_id(index, doc, SNAPSHOT_ID[index]),
                "_source": doc}
    if index in DATA_STREAMS:
        act = {"_op_type": "create", "_index": index, "_source": doc}
        if index == "room-events" and doc.get("event_type") == "commit":
            # one commit event per sha, ever
            act["_id"] = natural_id(index, doc, ("commit_sha",)) + ":commit"
        return act
    raise ValueError(f"unknown index {index!r}")


def commit_actions(commit, records, *, at, capture_id: str, meta_by_id: dict | None = None,
                   trace: dict | None = None) -> list[dict]:
    """Everything one commit writes to Elasticsearch (docs/14 phase 2 step 8), minus the voxels
    and cloud catalog, which perception indexes itself: every object as a full snapshot, plus
    the commit event. `records` is the whole tree AT THE COMMIT, not just what changed -- an
    object that stops appearing is how "gone" is recorded. Read it from the commit
    (repo.records(c.sha)), never the working tree: after `room add zones/desk/` + commit, the
    working tree holds changes the commit doesn't. roomctl/publish.py is the real caller.

        c = repo.commit(msg)
        if c: ingest.write(es, ingest.commit_actions(c, repo.records(c.sha).values(), at=when,
                                                     capture_id=cap, meta_by_id=meta,
                                                     trace=obs.trace_fields()))
    """
    from records import commit_event, to_es_doc  # roomctl/perception imports: only when used
    meta_by_id = meta_by_id or {}
    acts = [action("room-objects", to_es_doc(r, commit, at=at, capture_id=capture_id,
                                             meta=meta_by_id.get(r.id), trace=trace))
            for r in records]
    acts.append(action("room-events", commit_event(commit, at=at, capture_id=capture_id, trace=trace)))
    return acts


def actions_from(items: Iterable[dict]) -> list[dict]:
    """A JSON array of bulk actions ({"_index", "_source", ...}, what scripts/story_demo.py
    writes) or of bare docs carrying their own "_index". Either way the id/op_type rules above
    are re-applied, and _index ends up on the action line: ES rejects it inside _source."""
    out = []
    for item in items:
        item = dict(item)
        if "_source" in item:
            out.append(action(item["_index"], dict(item["_source"])))
        else:
            out.append(action(item.pop("_index"), item))
    return out


def write(es: Elasticsearch, actions: list[dict], refresh: str | bool = "wait_for") -> dict[str, list]:
    """Bulk-write and tally per index: [written, already_there, errors]. Never raises on a bad
    doc -- one malformed row must not look like "Elasticsearch is down" (docs/13 gotcha 5)."""
    tally: dict[str, list] = {}
    # max_retries: a 429 (cluster pushing back) is retried per document with backoff 1, 2, 4 s
    results = helpers.streaming_bulk(es, actions, chunk_size=500, refresh=refresh,
                                     max_retries=3, initial_backoff=1,
                                     raise_on_error=False, raise_on_exception=False)
    for act, (ok, item) in zip(actions, results):
        res = next(iter(item.values()))
        t = tally.setdefault(act["_index"], [0, 0, []])
        if ok:
            t[0] += 1
        elif res.get("status") == 409 and act["_op_type"] == "create":
            t[1] += 1
        else:
            err = res.get("error", {})
            t[2].append(f"{err.get('type')}: {err.get('reason')}" if isinstance(err, dict) else str(err))
    return tally


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", type=Path, help="JSON array of bulk actions or of docs with an _index key")
    ap.add_argument("--dry-run", action="store_true", help="print the _bulk body and exit")
    args = ap.parse_args()

    try:
        actions = actions_from(json.loads(args.file.read_text()))
    except (OSError, ValueError, KeyError) as e:
        print(f"error: {args.file}: {e}", file=sys.stderr)
        return 2

    if args.dry_run:
        for a in actions:
            meta = {k: a[k] for k in ("_index", "_id") if k in a}
            print(json.dumps({a["_op_type"]: meta}))
            print(json.dumps(a["_source"]))
        return 0

    try:
        es = connect()
    except SetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    failed = 0
    for index, (ok, dup, errs) in sorted(write(es, actions).items()):
        failed += len(errs)
        print(f"  {index:<19}{ok} written" + (f", {dup} already there" if dup else "")
              + (f", {len(errs)} FAILED -- {errs[0]}" if errs else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
