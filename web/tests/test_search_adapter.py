"""web's search is an ADAPTER over elastic/queries.py (GAP 3). These tests mock at the API boundary:
a fake Elasticsearch client is handed to the REAL shared `Queries`, so the shared query-building code
and web's shaping both run, and nothing touches the network.

    ../.venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB))
os.environ["SENTRY_DSN"] = ""                  # never initialise Sentry from a test

import dash_api                                 # noqa: E402
import es_shared                                # noqa: E402

HEAD = "1a668ec0a5aa7131cd9593991a693b14deb54a30"
OLD = "e51a75a17aea6866482557f10744da84d513a562"


def _fused(oid, cls, score, shas, descriptions):
    timeline = [{"_source": {"commit_sha": s, "branch": "main", "zone": "desk", "pose": {"x": 0.6, "y": 0.2, "z": 0.75},
                             "capture_id": f"cap_000{i + 1}", "@timestamp": f"2026-09-19T0{i}:00:00Z"}}
                for i, s in enumerate(shas)]
    return {"_score": score, "_source": {"object_id": oid, "class": cls, "raw_description": descriptions},
            "inner_hits": {"timeline": {"hits": {"hits": timeline}}}}


class FakeEs:
    """Answers the three calls the shared search makes, with the scores measured on the live cluster
    (2026-09-18, query "mug"): the semantic leg scores EVERY object, because kNN always returns neighbours."""

    def __init__(self):
        self.calls: list[dict] = []

    def search(self, **kw):
        self.calls.append(kw)
        if "retriever" in kw:                   # Queries.search_objects: fused, reranked, collapsed
            return {"hits": {"hits": [
                _fused("mug_a1b2", "mug", 1.67, [HEAD, OLD], ["a blue ceramic mug", "cup with handle, chipped", "cylindrical container, dark"]),
                _fused("cup_7e21", "cup", 1.34, [HEAD], ["small ceramic cup, cream coloured", "short white cylinder, glazed", "off-white porcelain cup, empty"]),
                _fused("tool_4f2a", "hammer", 1.31, [OLD], ["a claw hammer, wooden handle", "wooden-handled mallet", "heavy steel-headed tool"]),
                _fused("speaker_6b12", "speaker", 1.13, [HEAD], ["small black speaker"] * 3)]}}
        if "aggs" in kw:                        # Queries.lexical_only: BM25 membership
            return {"hits": {"hits": []}, "aggregations": {"ids": {"buckets": [{"key": "mug_a1b2"}]}}}
        return {"hits": {"hits": [              # Queries.semantic_only: best score per object
            {"_score": s, "fields": {"object_id": [o]}} for o, s in
            [("mug_a1b2", 0.750), ("cup_7e21", 0.665), ("speaker_6b12", 0.627), ("tool_4f2a", 0.498)]]}}


@pytest.fixture
def fake(monkeypatch):
    es = FakeEs()
    monkeypatch.setattr(es_shared, "queries", lambda: es_shared.shared.Queries(es))
    monkeypatch.setattr(dash_api, "git_head", lambda: HEAD)
    return es


def run(**kw):
    return asyncio.run(dash_api.search(**{"q": "mug", "limit": 20, "all_time": True, **kw}))


def test_the_demo_claim_cup_found_by_vector_missed_by_bm25(fake):
    out = run()
    by_id = {r["object_id"]: r for r in out["results"]}
    assert by_id["mug_a1b2"]["matched_by"]["bm25"] and by_id["mug_a1b2"]["matched_by"]["vector"]
    cup = by_id["cup_7e21"]["matched_by"]
    assert cup["bm25"] is False and cup["vector"] is True and cup["rerank_position"] == 2
    assert not any("mug" in d for d in by_id["cup_7e21"]["descriptions"])      # nothing about it says "mug"


def test_a_knn_neighbour_is_not_a_result(fake):
    """The hammer and the speaker sit inside the semantic leg's window (everything does, in a small
    room) but under the calibrated cut, and BM25 did not match them: they must not appear at all."""
    ids = [r["object_id"] for r in run()["results"]]
    assert ids == ["mug_a1b2", "cup_7e21"]


def test_it_is_the_shared_query_not_a_second_one(fake):
    run()
    fused = next(c for c in fake.calls if "retriever" in c)
    rrf = fused["retriever"]["text_similarity_reranker"]["retriever"]["rrf"]["retrievers"]
    legs = [leg["standard"]["query"]["bool"]["must"][0] for leg in rrf]
    shared = es_shared.shared.Queries(FakeEs())
    assert legs == [shared._lexical("mug"), shared._semantic("mug")]            # built by elastic/, not by web
    assert es_shared.lexical_fields(shared) == ["class", "raw_description.text"]  # BM25 really reads descriptions
    assert len(fake.calls) == 3                                                 # fused + the two provenance probes


def test_present_now_comes_from_git_head(fake):
    by_id = {r["object_id"]: r for r in run()["results"]}
    assert by_id["mug_a1b2"]["present_now"] is True
    assert by_id["mug_a1b2"]["last_seen"]["capture_id"] == "cap_0001"           # the card's link to /capture/<id>


def test_present_only_pins_heads_commit(fake):
    run(all_time=False)
    for call in fake.calls:
        body = call["retriever"]["text_similarity_reranker"]["retriever"]["rrf"]["retrievers"][0]["standard"]["query"] \
            if "retriever" in call else call["query"]
        assert {"term": {"commit_sha": HEAD}} in body["bool"]["filter"]


def test_vector_cut_is_floor_and_fraction_of_the_top_score():
    assert dash_api.vector_matches([]) == {}
    # nothing relevant in the room ("xylophone" topped out at 0.556 live): under the floor -> no match
    assert dash_api.vector_matches([("tape_measure_91be", 0.556), ("speaker_6b12", 0.555)]) == {}
    got = dash_api.vector_matches([("keys_7c2e", 0.693), ("tape_measure_91be", 0.571)])
    assert list(got) == ["keys_7c2e"] and got["keys_7c2e"][0] == 1
