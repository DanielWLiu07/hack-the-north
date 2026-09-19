"""Isolated API tests: no server import, network, fixtures or room writes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import voxel_api as api

SHA = "a" * 40
CUBE = {"origin": [-4., -4., 0.], "size_m": 8., "levels": 7, "cell_size_m": .0625}


def doc(key="0000000"):
    return {"voxel_key": key, "cell": {"x": -3.96875, "y": -3.96875},
            "z_min": .01, "z_max": .05, "density": 4, "object_id": "mug_a"}


class Elastic:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    async def search(self, index, body, label):
        self.calls.append((index, body, label))
        return self.results.pop(0)


def result(docs, total=None):
    return {"hits": {"total": {"value": len(docs) if total is None else total},
                     "hits": [{"_source": d, "sort": [d.get("voxel_key", "")]} for d in docs]}}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "_pinned", lambda: CUBE)
    monkeypatch.setattr(api, "_head", lambda: SHA)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as c:
        yield c


def test_full_cube_decode():
    cell = api.decode(doc(), CUBE)
    assert cell["center"] == [-3.96875, -3.96875, .03125]
    assert cell["size"] == .0625
    d = doc("7777777")
    d.update(cell={"x": 3.96875, "y": 3.96875}, z_min=7.95, z_max=7.99)
    assert api.decode(d, CUBE)["center"] == [3.96875, 3.96875, 7.96875]


def test_prefix_is_a_coarser_cube():
    cell = api.decode_prefix("3", CUBE)
    assert cell["size"] == 4 and cell["center"] == [-2.0, 2.0, 6.0]
    nested = api.decode_prefix("370", CUBE)
    assert nested["size"] == 1
    assert all(abs(a - b) < 1e-9 for a, b in zip(nested["center"], [-1.5, 2.5, 6.5]))


@pytest.mark.parametrize("update", [{"voxel_key": "00000000"}, {"voxel_key": "0000008"},
    {"cell": {"x": float("nan"), "y": 0}}, {"z_min": float("inf")},
    {"z_max": 8}, {"cell": {"x": 1, "y": 1}}, {"density": -1}])
def test_invalid_cells(update):
    with pytest.raises(ValueError):
        api.decode(doc() | update, CUBE)


def test_a_shallower_key_is_an_older_commit_not_an_invalid_cell():
    """Depth comes from the key. A commit written before an OCTREE_LEVELS change is all leaves,
    just shallower ones, and it must still decode -- demanding len(key) == cube.levels made every
    document of every older commit invalid, and the page rendered the room as 0 cells. A key
    DEEPER than the cube stays an error: that means the writer is newer than this reader.
    Matches perception/voxelize.py's from_docs. Was: {"voxel_key": "000"} in the list above."""
    cell = api.decode(doc() | {"voxel_key": "000", "cell": {"x": -3.5, "y": -3.5},
                               "z_min": 0.0, "z_max": 0.5}, CUBE)
    assert cell["size"] == 1.0 and cell["center"] == [-3.5, -3.5, 0.5]


def test_requested_filter_limit_and_provenance(client):
    es = Elastic([result([doc()], 3)])
    api.init(es)
    r = client.get(f"/api/voxels?commit_sha={SHA}&object_id=mug_a&limit=1")
    assert r.status_code == 200
    body = r.json()
    assert body["snapshot_source"] == "requested" and body["truncated"]
    assert body["total"] == 3 and body["returned"] == 1
    assert body["provenance"]["kind"] == "unknown"
    assert es.calls[0][1]["query"]["bool"]["filter"] == [
        {"term": {"commit_sha": SHA}}, {"term": {"object_id": "mug_a"}}]


def test_head_and_invalid_skip(client):
    api.init(Elastic([result([{"commit_sha": SHA}]), result([doc(), doc() | {"z_min": float("nan")}])]))
    body = client.get("/api/voxels").json()
    assert body["snapshot_source"] == "head"
    assert body["returned"] == 1 and body["invalid"] == 1 and not body["truncated"]


def test_latest_fallback_is_explicit(client):
    api.init(Elastic([result([]), result([{"commit_sha": "b" * 40}]), result([])]))
    body = client.get("/api/voxels").json()
    assert body["snapshot_source"] == "latest_indexed" and body["commit_sha"] == "b" * 40
    assert body["snapshot"]["head"] == SHA


def test_empty_and_offline(client):
    api.init(Elastic([result([]), result([])]))
    assert client.get("/api/voxels").json()["cells"] == []
    api.init(None)
    r = client.get("/api/voxels")
    assert r.status_code == 503 and "cells" not in r.json()


@pytest.mark.parametrize("query", ["limit=0", "limit=20001", "commit_sha=HEAD", "commit_sha=--bad",
                                   "object_id=", "level=l2", "prefix=abc", "prefix=8"])
def test_validation(client, query):
    assert client.get("/api/voxels?" + query).status_code == 422


def agg(buckets, total=None, other=0):
    return {"hits": {"total": {"value": total if total is not None else sum(b["doc_count"] for b in buckets)},
                     "hits": []},
            "aggregations": {"cells": {"buckets": buckets, "sum_other_doc_count": other}}}


def test_l3_terms_aggregation_is_the_geohash_grid(client):
    es = Elastic([agg([{"key": "370", "doc_count": 12,
                        "objects": {"buckets": [{"key": "mug_a"}]},
                        "density": {"value": 48}}])])
    api.init(es)
    r = client.get(f"/api/voxels?commit_sha={SHA}&level=l3&prefix=37")
    assert r.status_code == 200
    body = r.json()
    assert body["aggregated"] and body["level"] == "l3" and body["prefix"] == "37"
    assert body["returned"] == 1 and body["cells"][0]["voxel_key"] == "370"
    assert body["cells"][0]["size"] == 1 and body["cells"][0]["count"] == 12
    assert body["cells"][0]["object_id"] == "mug_a"
    query = es.calls[0][1]["query"]["bool"]["filter"]
    assert {"prefix": {"voxel_key": "37"}} in query
    assert es.calls[0][1]["aggs"]["cells"]["terms"]["field"] == "voxel_key_l3"


def test_prefix_deeper_than_level_reads_leaves(client):
    es = Elastic([result([doc()])])
    api.init(es)
    body = client.get(f"/api/voxels?commit_sha={SHA}&level=l3&prefix=00000").json()
    assert body["aggregated"] is False and body["cells"][0]["voxel_key"] == "0000000"
    assert "aggs" not in es.calls[0][1]


def test_paging_above_default_es_window(client):
    # Duplicate keys count as invalid, but cannot cause unbounded reads or loops.
    es = Elastic([result([doc()] * 5000, 11000), result([doc()] * 5000, 11000), result([doc()] * 1000, 11000)])
    api.init(es)
    body = client.get(f"/api/voxels?commit_sha={SHA}&limit=11000").json()
    assert len(es.calls) == 3 and es.calls[1][1]["search_after"] == ["0000000"]
    assert body["returned"] == 1 and body["invalid"] == 10999
