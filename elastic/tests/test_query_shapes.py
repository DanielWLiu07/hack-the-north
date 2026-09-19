"""The request bodies queries.py sends, checked against a recording stand-in for the client.
Offline: this is the API boundary mocked, not hit.

web/ calls lexical_only() and semantic_only() to label which leg found a result, and trusts
that they are the SAME legs search_objects fuses. These tests hold them to that.
"""
from __future__ import annotations

import pytest

from queries import Queries


class Recorder:
    """Stands in for elasticsearch.Elasticsearch: records every search, answers with canned hits."""

    def __init__(self):
        self.calls: list[dict] = []

    def search(self, **kw):
        self.calls.append(kw)
        hit = {"_score": 0.75, "fields": {"object_id": ["mug_a1b2"]},
               "_source": {"object_id": "mug_a1b2", "class": "mug", "raw_description": ["a mug"]},
               "inner_hits": {"timeline": {"hits": {"hits": [{"_source": {"commit_sha": "c3", "capture_id": "cap_0003"}}]}}}}
        return {"hits": {"hits": [hit]}, "aggregations": {"ids": {"buckets": [{"key": "mug_a1b2"}]}}}


@pytest.fixture
def q():
    return Queries(Recorder())


def legs_of(call: dict) -> list[dict]:
    rrf = call["retriever"]["text_similarity_reranker"]["retriever"]["rrf"]
    return [r["standard"]["query"]["bool"] for r in rrf["retrievers"]]


@pytest.mark.parametrize("sha,branch", [(None, None), ("c3" * 20, None), (None, "main")])
def test_the_single_leg_queries_are_the_fused_legs(q, sha, branch):
    q.search_objects("mug", commit_sha=sha, branch=branch)
    q.lexical_only("mug", commit_sha=sha, branch=branch)
    q.semantic_only("mug", commit_sha=sha, branch=branch)
    fused, lexical, semantic = q.es.calls
    bm25, dense = legs_of(fused)
    assert lexical["query"]["bool"] == bm25, "lexical_only must be search_objects' BM25 leg"
    assert semantic["query"]["bool"] == dense, "semantic_only must be search_objects' semantic leg"


def test_the_bm25_leg_reads_descriptions_and_the_semantic_leg_embeds_them(q):
    q.search_objects("mug")
    bm25, dense = legs_of(q.es.calls[0])
    assert bm25["must"][0]["bool"]["should"][0]["multi_match"]["fields"] == ["class", "raw_description.text"]
    assert dense["must"][0] == {"semantic": {"field": "raw_description", "query": "mug"}}


def test_search_collapses_per_object_and_reranks_on_descriptions(q):
    hits = q.search_objects("mug", size=10)
    call = q.es.calls[0]
    assert call["collapse"]["field"] == "object_id"
    assert "capture_id" in call["collapse"]["inner_hits"]["_source"]
    tsr = call["retriever"]["text_similarity_reranker"]
    assert tsr["field"] == "rerank_text" and tsr["inference_text"] == "mug"  # class + every description
    assert tsr["rank_window_size"] >= 10
    assert hits[0]["latest"] == {"commit_sha": "c3", "capture_id": "cap_0003"}


def test_semantic_only_returns_ids_with_scores(q):
    assert q.semantic_only("mug") == [("mug_a1b2", 0.75)]
    call = q.es.calls[0]
    assert call["collapse"] == {"field": "object_id"} and call["_source"] is False


def test_near_filter_is_a_cartesian_box_on_every_leg(q):
    q.search_objects("cup", near=(0.8, 0.1), radius=0.45)
    for leg in legs_of(q.es.calls[0]):
        (shape,) = [f["shape"]["position"] for f in leg["filter"] if "shape" in f]
        assert shape["relation"] == "intersects"
        assert shape["shape"]["type"] == "envelope"
        (x0, y1), (x1, y0) = shape["shape"]["coordinates"]  # top-left, bottom-right
        assert (x0, y1, x1, y0) == pytest.approx((0.35, 0.55, 1.25, -0.35))


def test_branch_filter_reaches_every_leg(q):
    q.search_objects("mug", near=(0.4, 0.2), radius=1.5, branch="main")
    for leg in legs_of(q.es.calls[0]):
        assert {"term": {"branch": "main"}} in leg["filter"]
