"""ingest.write()/action() against a stand-in _bulk endpoint. Offline: the real client class
is used, with only its bulk() replaced, so the helper's chunking and retry paths are real."""
from __future__ import annotations

import json
from unittest import mock

import pytest
from elastic_transport import ApiResponseMeta, HttpHeaders, NodeConfig, ObjectApiResponse
from elasticsearch import Elasticsearch

import ingest


class FakeBulk:
    """Answers _bulk like the cluster: data-stream `create` 409s on a repeat, `_index` in a body
    is a 400, and the first attempt of anything in `throttle` gets a 429."""

    def __init__(self, throttle=()):
        self.seen, self.calls, self.throttle = set(), 0, set(throttle)

    def __call__(self, operations=None, **kw):  # patched onto the class unbound: no client arg
        self.calls += 1
        ops = [json.loads(o) if isinstance(o, (bytes, str)) else o for o in operations]
        items = []
        for meta, src in zip(ops[::2], ops[1::2]):
            (op, m), = meta.items()
            key = (m["_index"], m.get("_id") or json.dumps(src, sort_keys=True))
            if "_index" in src:
                status, err = 400, "document_parsing_exception"
            elif src.get("object_id") in self.throttle:
                self.throttle.discard(src["object_id"])
                status, err = 429, "es_rejected_execution_exception"
            elif op == "create" and key in self.seen:
                status, err = 409, "version_conflict_engine_exception"
            else:
                self.seen.add(key)
                status, err = 201, None
            items.append({op: {"_index": m["_index"], "status": status,
                               **({"error": {"type": err, "reason": err}} if err else {})}})
        body = {"errors": any(next(iter(i.values()))["status"] > 299 for i in items), "items": items}
        return ObjectApiResponse(body=body, meta=ApiResponseMeta(200, "1.1", HttpHeaders(), 0.0,
                                                                 NodeConfig("http", "x", 9200)))


@pytest.fixture
def es():
    return Elasticsearch("http://stand-in:9200")


def obs(i: int) -> dict:
    return {"@timestamp": f"2026-09-19T14:22:07.{i:03d}Z", "capture_id": "cap_1", "camera": "cam0",
            "object_id": f"mug_a1b{i}", "confidence": 0.9}


def test_resend_is_idempotent(es):
    fake = FakeBulk()
    actions = [ingest.action("room-observations", obs(i)) for i in range(3)] + \
              [ingest.action("room-clouds", {"capture_id": "cap_1", "@timestamp": "2026-09-19T14:22:07Z"})]
    with mock.patch.object(Elasticsearch, "bulk", fake):
        first, again = ingest.write(es, actions), ingest.write(es, actions)
    assert first == {"room-observations": [3, 0, []], "room-clouds": [1, 0, []]}
    assert again == {"room-observations": [0, 3, []], "room-clouds": [1, 0, []]}, \
        "create: already there; index with a natural _id: overwritten"


def test_a_bad_doc_is_reported_not_raised(es):
    bad = {"_op_type": "create", "_index": "room-observations", "_source": {**obs(1), "_index": "x"}}
    with mock.patch.object(Elasticsearch, "bulk", FakeBulk()):
        tally = ingest.write(es, [bad, ingest.action("room-observations", obs(2))])
    written, dup, errors = tally["room-observations"]
    assert (written, dup) == (1, 0) and errors == ["document_parsing_exception: document_parsing_exception"]


def test_429_is_retried_until_written(es, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)  # the backoff, without the wait
    fake = FakeBulk(throttle={"mug_a1b1"})
    with mock.patch.object(Elasticsearch, "bulk", fake):
        tally = ingest.write(es, [ingest.action("room-observations", obs(i)) for i in range(3)])
    assert tally == {"room-observations": [3, 0, []]} and fake.calls == 2


def test_index_is_on_the_action_never_in_the_body():
    for index, doc in (("room-observations", obs(1)),
                       ("room-clouds", {"capture_id": "cap_1"}),
                       ("room-events", {"event_type": "commit", "commit_sha": "c1" * 20})):
        act = ingest.action(index, doc)
        assert act["_index"] == index and "_index" not in act["_source"]
    assert ingest.action("room-events", {"event_type": "commit", "commit_sha": "c1" * 20})["_id"] == "c1" * 20 + ":commit"


def test_unknown_index_is_refused():
    with pytest.raises(ValueError, match="unknown index"):
        ingest.action("room-object", {})
