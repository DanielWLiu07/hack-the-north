"""voxelize: FRAME (F_world on the room's pinned octree cube) and UNITS (metres, 6.25 cm cells).
The live-cluster check is test_voxelize_live.py."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voxelize  # noqa: E402  (puts the repo root on sys.path)
import obs  # noqa: E402
from voxelize import VoxelGrid, key_box, keys_of, octree_key  # noqa: E402

CUBE = ((-4.0, -4.0, 0.0), 8.0, 7)     # .env.example's cube: 8 m / 2**7 = 6.25 cm


def test_cell_is_the_octree_leaf_in_metres():
    g = VoxelGrid.from_points(np.tile([1.0, -0.5, 0.75], (5, 1)), CUBE)
    assert g.leaf == 0.0625 and g.n == 128
    assert g.ijk.tolist() == [[80, 56, 12]]            # floor((p - origin) / 0.0625), x/y/z in order
    assert np.all(np.abs(g.centres()[0] - [1.0, -0.5, 0.75]) <= g.leaf / 2)
    assert g.occ[80, 56, 12] and g.occ.sum() == 1
    assert g.z_lo[0] == g.z_hi[0] == 0.75               # heights in metres, not cells


def test_default_cube_is_the_pinned_one():
    g = VoxelGrid.from_points(np.tile([0.0, 0.0, 1.0], (5, 1)))
    assert g.cube == voxelize.pinned_cube() == CUBE


def test_speckle_and_outside_are_dropped():
    pts = np.vstack([np.tile([0.5, 0.5, 0.5], (3, 1)),      # 3 points: a surface
                     np.tile([1.5, 0.5, 0.5], (2, 1)),      # 2 points: speckle
                     np.tile([0.5, 0.5, -0.2], (9, 1)),     # below the cube (under the floor)
                     np.tile([4.5, 0.5, 0.5], (9, 1)),      # beyond it
                     [[np.nan, 0, 0]]])
    g = VoxelGrid.from_points(pts, CUBE)
    assert len(g.ijk) == 1 and np.allclose(g.centres()[0], [0.53125, 0.53125, 0.53125])


def test_height_extent_ignores_a_stray_point():
    z = np.r_[np.linspace(0.755, 0.765, 20), 0.81]           # one stray point in the same voxel
    g = VoxelGrid.from_points(np.column_stack([np.full(21, 1.02), np.full(21, 0.02), z]), CUBE)
    assert len(g.ijk) == 1
    assert 0.755 <= g.z_lo[0] < g.z_hi[0] <= 0.766


# ── octree keys (docs/11): FRAME is the digit's bit order, UNITS the cell sizes ──

def test_a_point_inside_cell_370_has_a_key_starting_370():
    """docs/20 Part 7, step 6's check."""
    lo, hi = key_box("370", *CUBE[:2])
    assert np.allclose(hi - lo, 1.0)                                   # level 3 = 1 m cells
    rng = np.random.default_rng(0)
    for p in rng.uniform(lo, hi, (200, 3)):
        assert octree_key(*p, *CUBE).startswith("370")
    assert not octree_key(*(hi + 0.01), *CUBE).startswith("370")


def test_digit_bits_are_x_y_z():
    """Each digit is (x bit)<<2 | (y bit)<<1 | (z bit): crossing the cube's midplane in X
    sets 4, in Y sets 2, in Z sets 1. An axis swap here files every voxel somewhere else."""
    o = np.array(CUBE[0])
    assert octree_key(*(o + 0.01), *CUBE) == "0000000"
    assert octree_key(*(o + [4.01, 0.01, 0.01]), *CUBE)[0] == "4"      # +X, half the 8 m cube
    assert octree_key(*(o + [0.01, 4.01, 0.01]), *CUBE)[0] == "2"      # +Y (left)
    assert octree_key(*(o + [0.01, 0.01, 4.01]), *CUBE)[0] == "1"      # +Z (up)
    assert octree_key(-4.01, 0, 1, *CUBE) is None and octree_key(0, 0, -0.01, *CUBE) is None


def test_key_lengths_are_cell_sizes_in_metres():
    for level, side in ((7, 0.0625), (5, 0.25), (3, 1.0)):
        lo, hi = key_box(octree_key(1.0, -0.5, 0.75, *CUBE)[:level], *CUBE[:2])
        assert np.allclose(hi - lo, side)
        assert np.all(lo <= [1.0, -0.5, 0.75]) and np.all([1.0, -0.5, 0.75] < hi)


def test_grid_keys_match_the_docs_encoder():
    """keys_of() (from integer indices) and docs/11's float encoder agree everywhere,
    including points exactly on cell boundaries."""
    rng = np.random.default_rng(1)
    pts = np.vstack([rng.uniform([-3.9, -3.9, 0], [3.9, 3.9, 2.5], (2000, 3)),
                     np.round(rng.uniform([-3.9, -3.9, 0], [3.9, 3.9, 2.5], (500, 3)) / 0.0625) * 0.0625])
    g = VoxelGrid.from_points(pts, CUBE, min_pts=1)
    assert g.keys() == [octree_key(*c, *CUBE) for c in g.centres()]
    rows = {tuple(v): k for v, k in zip(g.ijk.tolist(), g.keys())}
    for p in pts:
        assert rows[tuple(np.floor((p - CUBE[0]) / g.leaf).astype(int))] == octree_key(*p, *CUBE)
    assert keys_of(np.array([[127, 127, 127]]), 7) == ["7777777"]


# ── the pinned cube ──────────────────────────────────────────────────────────

REPO = Path(__file__).resolve().parents[2]


def test_pinned_cube_is_envs_and_room_yamls():
    assert voxelize.pinned_cube() == ((-4.0, -4.0, 0.0), 8.0, 7)
    room = voxelize.load_room(REPO / "room.git")
    voxelize.check_pinned(voxelize.pinned_cube(), room)                 # agrees: no raise


def test_the_cube_comes_from_the_env_file(tmp_path, monkeypatch):
    for k in ("ROOM_ORIGIN_X", "ROOM_ORIGIN_Y", "ROOM_ORIGIN_Z", "ROOM_CUBE_SIZE", "OCTREE_LEVELS"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("ROOM_ORIGIN_X=-2.0\nROOM_ORIGIN_Y=-3.0\nROOM_ORIGIN_Z=0.0\n"
                   "ROOM_CUBE_SIZE=4.0   # metres\nOCTREE_LEVELS=6\n")
    assert voxelize.pinned_cube(env) == ((-2.0, -3.0, 0.0), 4.0, 6)


def test_a_moved_cube_is_refused(monkeypatch):
    monkeypatch.setenv("OCTREE_LEVELS", "6")                            # a real env var beats .env
    assert voxelize.pinned_cube()[2] == 6
    g = VoxelGrid.from_points(np.tile([0.5, 0.0, 0.75], (5, 1)))
    with pytest.raises(ValueError, match="pinned cube"):
        voxelize.index_voxels(g, "abc123", None, "main", "2026-09-18T21:00:00Z", es=object(),
                              room_root=REPO / "room.git")               # refused before any network


# ── room-voxels documents ────────────────────────────────────────────────────

MAPPING = json.loads((REPO / "elastic" / "mappings" / "room-voxels.json").read_text())["mappings"]["properties"]
ZONES = {"desk": {"min": [0.0, -0.5, 0.6], "max": [1.0, 0.5, 1.3]}}


def _docs(claims=None):
    rng = np.random.default_rng(2)
    top = np.column_stack([rng.uniform(0.2, 0.8, 3000), rng.uniform(-0.3, 0.3, 3000), rng.uniform(0.74, 0.76, 3000)])
    floor = np.column_stack([rng.uniform(-1, 2, 4000), rng.uniform(-1, 1, 4000), rng.uniform(0, 0.01, 4000)])
    g = VoxelGrid.from_points(np.vstack([top, floor]), CUBE)
    with obs.span("test"):
        want = obs.trace_fields()
        docs = voxelize.voxel_docs(g, "abc123", "0ff1ce", "main", "2026-09-18T21:00:00Z", ZONES, claims)
    return g, docs, want


def test_docs_fit_the_strict_mapping_and_carry_the_trace():
    g, docs, want = _docs()
    assert len(docs) == len(g.ijk)
    for d in docs:
        assert set(d) <= set(MAPPING), set(d) - set(MAPPING)             # dynamic: strict
        assert d["voxel_key_l5"] == d["voxel_key"][:5] and d["voxel_key_l3"] == d["voxel_key"][:3]
        assert len(d["voxel_key"]) == 7 and set(d["voxel_key"]) <= set("01234567")
        assert isinstance(d["density"], int) and d["density"] >= voxelize.MIN_PTS
        assert set(d["cell"]) == {"x", "y"}
        assert d["sentry_trace_id"] == want["sentry_trace_id"]          # EVERY doc
    assert len({d["voxel_key"] for d in docs}) == len(docs)              # the _id is sha:voxel_key


def test_doc_geometry_is_metres_in_the_world_frame():
    g, docs, _ = _docs()
    for d, c, lo, hi in zip(docs, g.centres(), g.z_lo, g.z_hi):
        assert (d["cell"]["x"], d["cell"]["y"]) == pytest.approx((c[0], c[1]), abs=1e-4)   # the centre
        assert octree_key(d["cell"]["x"], d["cell"]["y"], c[2], *CUBE) == d["voxel_key"]
        assert d["z_min"] <= d["z_max"] and c[2] - g.leaf / 2 <= d["z_min"] and d["z_max"] <= c[2] + g.leaf / 2
    top = [d for d in docs if d["z_max"] > 0.7]
    assert top and all(0.73 < d["z_min"] and d["z_max"] < 0.77 for d in top)   # the table top at 0.75 m


def test_zone_and_object_claims():
    rng = np.random.default_rng(3)
    mug = np.column_stack([rng.uniform(0.48, 0.52, 400), rng.uniform(-0.02, 0.02, 400), rng.uniform(0.76, 0.86, 400)])
    _, docs, _ = _docs(claims={"mug_a1b2": mug, "zine_c3d4": mug[:10]})   # fewer points, sorts last
    by_zone = {d["zone"] for d in docs}
    assert by_zone == {"desk", None}                                     # the floor is in no zone box
    assert all(d["zone"] == "desk" for d in docs if d["z_min"] > 0.7)
    claimed = [d for d in docs if d["object_id"]]
    assert claimed and {d["object_id"] for d in claimed} == {"mug_a1b2"}  # most points wins
    assert all(0.44 < d["cell"]["x"] < 0.56 for d in claimed)


def test_trace_guard_is_for_a_missing_span_not_for_sentry_being_off(monkeypatch):
    """Sentry live and no trace: a bug (called outside the capture's span) -> raise. Sentry
    off (parked DSN, no SDK) and no trace: index anyway -- docs/11, indexing must not depend
    on Sentry being up. Either way nothing here talks to Sentry."""
    monkeypatch.setattr(voxelize.obs, "trace_fields", lambda: {})
    g = VoxelGrid.from_points(np.tile([0.5, 0.0, 0.75], (5, 1)), CUBE)
    monkeypatch.setattr(voxelize, "_sentry_live", lambda: True)
    with pytest.raises(RuntimeError, match="sentry_trace_id"):
        voxelize.voxel_docs(g, "abc123", None, "main", "2026-09-18T21:00:00Z")
    monkeypatch.setattr(voxelize, "_sentry_live", lambda: False)
    [d] = voxelize.voxel_docs(g, "abc123", None, "main", "2026-09-18T21:00:00Z")
    assert "sentry_trace_id" not in d and d["voxel_key"]


def test_sentry_is_not_live_in_tests():
    assert voxelize._sentry_live() is False
