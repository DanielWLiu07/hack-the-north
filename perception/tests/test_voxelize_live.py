"""voxelize.index_voxels against the REAL cluster. Indexes a synthetic room under a throwaway
commit sha, reads it back with independent queries, then deletes exactly those documents.
Opt-in -- it spends cluster quota -- and never while the key is parked:

    GITSPACE_LIVE=1 .venv/bin/python -m pytest perception/tests/test_voxelize_live.py
"""
import importlib.util
import os
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voxelize  # noqa: E402  (puts the repo root on sys.path)
import obs  # noqa: E402
from voxelize import INDEX, VoxelGrid  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
ENV = dotenv_values(REPO / ".env")
LIVE = (os.getenv("GITSPACE_LIVE") == "1" and importlib.util.find_spec("elasticsearch") is not None
        and bool(ENV.get("ELASTIC_URL")) and "ELASTIC_API_KEY_PARKED" not in ENV)
pytestmark = pytest.mark.skipif(not LIVE, reason="live cluster test: opt in with GITSPACE_LIVE=1; "
                                                 "never while ELASTIC_API_KEY is parked")


@pytest.fixture(scope="module")
def es():
    return voxelize._elastic()[2]()


@pytest.fixture(scope="module")
def indexed(es):
    """A table top inside room.yaml's desk zone, a floor, a mug that claims its voxels."""
    rng = np.random.default_rng(0)
    top = np.column_stack([rng.uniform(0.2, 0.8, 6000), rng.uniform(-0.3, 0.3, 6000), rng.uniform(0.74, 0.76, 6000)])
    floor = np.column_stack([rng.uniform(-1, 2, 8000), rng.uniform(-1, 1, 8000), rng.uniform(0, 0.01, 8000)])
    mug = np.column_stack([rng.uniform(0.48, 0.52, 500), rng.uniform(-0.02, 0.02, 500), rng.uniform(0.76, 0.86, 500)])
    grid = VoxelGrid.from_points(np.vstack([top, floor, mug]))            # the pinned cube, from .env
    sha = f"selftest-{uuid.uuid4().hex[:12]}"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:                                   # from here on, documents may exist: always clean up
        with obs.span("test.index_voxels_live"):
            trace = obs.trace_fields()
            res = voxelize.index_voxels(grid, sha, None, "perception-selftest", ts, es=es,
                                        room_root=REPO / "room.git", claims={"mug_a1b2": mug})
            assert res.spooled == 0, res.reason
            n = res.indexed
            local = voxelize.voxel_docs(grid, sha, None, "perception-selftest", ts,
                                        voxelize.load_room(REPO / "room.git")["zones"], {"mug_a1b2": mug})
        yield dict(sha=sha, grid=grid, n=n, trace=trace, local=local)
    finally:
        es.delete_by_query(index=INDEX, query={"term": {"commit_sha": sha}}, refresh=True)
        assert es.count(index=INDEX, query={"term": {"commit_sha": sha}})["count"] == 0


def _count(es, sha, *clauses):
    return es.count(index=INDEX, query={"bool": {"filter": [{"term": {"commit_sha": sha}}, *clauses]}})["count"]


def test_every_voxel_landed_with_the_trace(es, indexed):
    sha, n = indexed["sha"], indexed["n"]
    assert n == len(indexed["grid"].ijk) > 100
    assert _count(es, sha) == n
    assert _count(es, sha, {"exists": {"field": "sentry_trace_id"}}) == n
    r = es.search(index=INDEX, size=0, query={"term": {"commit_sha": sha}},
                  aggs={"t": {"terms": {"field": "sentry_trace_id"}}})
    assert [b["key"] for b in r["aggregations"]["t"]["buckets"]] == [indexed["trace"]["sentry_trace_id"]]


def test_id_is_sha_colon_key_and_the_doc_round_trips(es, indexed):
    d = indexed["local"][0]
    got = es.get(index=INDEX, id=f"{indexed['sha']}:{d['voxel_key']}")["_source"]
    assert {k: got[k] for k in ("voxel_key", "voxel_key_l5", "voxel_key_l3", "cell", "z_min", "z_max", "density")} == \
           {k: d[k] for k in ("voxel_key", "voxel_key_l5", "voxel_key_l3", "cell", "z_min", "z_max", "density")}


def test_octree_prefixes_aggregate_as_keywords(es, indexed):
    r = es.search(index=INDEX, size=0, query={"term": {"commit_sha": indexed["sha"]}},
                  aggs={"l3": {"terms": {"field": "voxel_key_l3", "size": 500}}})
    assert {b["key"]: b["doc_count"] for b in r["aggregations"]["l3"]["buckets"]} == \
           Counter(d["voxel_key_l3"] for d in indexed["local"])


def test_cell_is_a_cartesian_point_in_metres(es, indexed):
    """A shape query over the table footprint, in F_world metres, returns exactly the voxels
    whose centres are on it."""
    x0, x1, y0, y1 = 0.2, 0.8, -0.3, 0.3
    got = _count(es, indexed["sha"], {"shape": {"cell": {"relation": "intersects", "shape": {
        "type": "envelope", "coordinates": [[x0, y1], [x1, y0]]}}}})
    want = sum(x0 <= d["cell"]["x"] <= x1 and y0 <= d["cell"]["y"] <= y1 for d in indexed["local"])
    assert got == want > 0


def test_zone_and_object_id(es, indexed):
    local = indexed["local"]
    assert _count(es, indexed["sha"], {"term": {"zone": "desk"}}) == sum(d["zone"] == "desk" for d in local) > 0
    assert _count(es, indexed["sha"], {"term": {"object_id": "mug_a1b2"}}) == \
           sum(d["object_id"] == "mug_a1b2" for d in local) > 0
