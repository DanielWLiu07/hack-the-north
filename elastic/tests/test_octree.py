"""The octree keying, as a contract rather than a comment.

Two properties everything else leans on:

  TOTALITY   every point inside the pinned cube gets a key, and that key's cell contains it.
             If this breaks, points are silently dropped -- occupancy just goes missing, and
             nothing raises.
  NESTING    the key at depth n+1 is the key at depth n plus one digit, because origin and size
             are what fix the grid, not depth. This is what lets OCTREE_LEVELS change without
             invalidating history: today's voxel_key IS tomorrow's voxel_key_l7, byte for byte.

The live tests then check the index actually holds to both.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from perception.voxelize import key_box, octree_key  # noqa: E402

ORIGIN, CUBE = (-4.0, -4.0, 0.0), 8.0
RUNGS = {"voxel_key_l3": 3, "voxel_key_l5": 5, "voxel_key_l6": 6, "voxel_key_l7": 7}


def points(n: int, seed: int = 0):
    rng = random.Random(seed)
    return [(rng.uniform(-4, 4), rng.uniform(-4, 4), rng.uniform(0, 8)) for _ in range(n)]


@pytest.mark.parametrize("levels", [3, 5, 7, 8])
def test_every_point_in_the_cube_is_keyed_and_its_cell_contains_it(levels):
    for p in points(4000, seed=levels):
        key = octree_key(*p, ORIGIN, CUBE, levels)
        assert key is not None and len(key) == levels, f"{p} fell outside the cube"
        lo, hi = key_box(key, ORIGIN, CUBE)
        assert all(lo[i] <= p[i] < hi[i] + 1e-12 for i in range(3)), f"{key} does not contain {p}"


@pytest.mark.parametrize("p", [(-4.0, -4.0, 0.0), (3.999, 3.999, 7.999), (0.0, 0.0, 0.0)])
def test_the_corners_are_inside(p):
    assert octree_key(*p, ORIGIN, CUBE, 7) is not None


@pytest.mark.parametrize("p", [(-4.001, 0, 0), (4.0, 0, 0), (0, 0, -0.001), (0, 0, 8.0)])
def test_outside_the_cube_is_none_not_a_wrong_cell(p):
    """Refusing beats clamping: a clamped point would be indexed in the wrong place."""
    assert octree_key(*p, ORIGIN, CUBE, 7) is None


def test_depth_only_appends_digits():
    """Changing OCTREE_LEVELS re-cuts the leaves, it does not move the grid."""
    for p in points(3000, seed=99):
        keys = [octree_key(*p, ORIGIN, CUBE, n) for n in range(1, 10)]
        for shallow, deep in zip(keys, keys[1:]):
            assert deep[:len(shallow)] == shallow


# ── the live index ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def voxels(es):
    hits, after = [], None
    while True:
        r = es.search(index="room-voxels", size=5000, query={"match_all": {}},
                      _source=["voxel_key", *RUNGS, "cell", "z_min", "z_max"],
                      sort=[{"voxel_key": "asc"}, {"_doc": "asc"}], **({"search_after": after} if after else {}))
        h = r["hits"]["hits"]
        hits += [x["_source"] for x in h]
        if len(h) < 5000:
            return hits
        after = h[-1]["sort"]


def test_no_indexed_voxel_was_dropped_for_being_outside_the_cube(es):
    total = es.count(index="room-voxels")["count"]
    keyed = es.count(index="room-voxels", query={"exists": {"field": "voxel_key"}})["count"]
    assert total == keyed > 0, f"{total - keyed} voxel docs have no key"


def test_every_rung_is_the_prefix_the_pipeline_promises(voxels):
    """room-voxels-keys derives these server-side; a writer sending its own must still agree."""
    assert voxels, "no voxels indexed"
    for d in voxels:
        for field, n in RUNGS.items():
            if len(d["voxel_key"]) >= n:
                assert d.get(field) == d["voxel_key"][:n], f"{field} on {d['voxel_key']}"


def test_open_floor_reads_back_as_free_floor_not_as_an_obstacle(es):
    """costmap.py's body band is z_mid > Z_FLOOR, and from_docs takes z_mid as the midpoint of
    z_min..z_max. A floor slab one CELL thick reads back at z_mid 0.031 -- above the 0.02 band --
    so every floor cell becomes an obstacle and the costmap walls off the room it is meant to
    drive across. The floor is 2 cm thick for that reason; this is the test that says so."""
    from perception.costmap import Z_FLOOR

    r = es.search(index="room-voxels", size=10000, query={"term": {"zone": "floor"}},
                  _source=["cell", "z_min", "z_max"])["hits"]["hits"]
    if not r:
        pytest.skip("no floor voxels indexed")
    zones = {"desk": ((0.08, -0.50), (1.00, 0.50)), "shelf": ((0.10, 0.60), (0.95, 1.00))}

    def under_furniture(c) -> bool:  # a pedestal standing on the floor IS solid, correctly
        return any(lo[0] - 0.07 <= c["x"] <= hi[0] + 0.07 and lo[1] - 0.07 <= c["y"] <= hi[1] + 0.07
                   for lo, hi in zones.values())

    solid = [d["_source"] for d in r
             if (d["_source"]["z_min"] + d["_source"]["z_max"]) / 2 > Z_FLOOR
             and not under_furniture(d["_source"]["cell"])]
    assert not solid, f"{len(solid)} open-floor cells would be obstacles, e.g. {solid[:2]}"


def test_every_indexed_cell_sits_inside_its_own_key(voxels):
    for d in voxels:
        lo, hi = key_box(d["voxel_key"], ORIGIN, CUBE)
        assert lo[0] <= d["cell"]["x"] < hi[0] and lo[1] <= d["cell"]["y"] < hi[1], \
            f"{d['voxel_key']} does not contain its own cell -- a different cube"
