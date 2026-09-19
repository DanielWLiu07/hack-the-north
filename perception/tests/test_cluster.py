"""Synthetic-scene tests for the fallback path (cluster.py). No model, no camera."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cluster  # noqa: E402

TABLE_Z = 0.75
NOISE = 0.002   # m


def _box(rng, lo, hi, per_m2=40000):
    """Points on all six faces of an axis-aligned box."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    size = hi - lo
    out = []
    for ax in range(3):
        a, b = [i for i in range(3) if i != ax]
        n = max(50, int(per_m2 * size[a] * size[b]))
        for side in (lo[ax], hi[ax]):
            p = rng.uniform(lo, hi, size=(n, 3))
            p[:, ax] = side
            out.append(p)
    return np.vstack(out)


def _cylinder(rng, cx, cy, r, z0, h, per_m2=40000):
    n_side = int(per_m2 * 2 * np.pi * r * h)
    t, z = rng.uniform(0, 2 * np.pi, n_side), rng.uniform(z0, z0 + h, n_side)
    side = np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t), z])
    n_top = int(per_m2 * np.pi * r * r)
    rr, tt = r * np.sqrt(rng.uniform(0, 1, n_top)), rng.uniform(0, 2 * np.pi, n_top)
    top = np.column_stack([cx + rr * np.cos(tt), cy + rr * np.sin(tt), np.full(n_top, z0 + h)])
    return np.vstack([side, top])


def _slab(rng, x0, x1, y0, y1, z, per_m2=40000):
    n = int(per_m2 * (x1 - x0) * (y1 - y0))
    return np.column_stack([rng.uniform(x0, x1, n), rng.uniform(y0, y1, n), np.full(n, z)])


def _scene(rng, *objects):
    """Floor at z=0, a 1.2 x 0.8 m table top at TABLE_Z, the given objects, 2 mm noise."""
    parts = [_slab(rng, -1.0, 1.0, -1.0, 1.0, 0.0, per_m2=15000),
             _slab(rng, -0.6, 0.6, -0.4, 0.4, TABLE_Z), *objects]
    pts = np.vstack(parts)
    return pts + rng.normal(0, NOISE, pts.shape)


def _two_objects(rng):
    box = _box(rng, [0.15, 0.05, TABLE_Z], [0.25, 0.13, TABLE_Z + 0.15])     # 10 x 8 x 15 cm
    mug = _cylinder(rng, -0.20, -0.10, 0.04, TABLE_Z, 0.10)                   # r 4 cm, h 10 cm
    return _scene(rng, box, mug), {"box": (0.20, 0.09), "mug": (-0.20, -0.10)}


def _nearest(instances, xy):
    return min(instances, key=lambda i: np.linalg.norm(i.centroid[:2] - xy))


def test_objects_on_table_come_back_separately_and_planes_are_kept():
    pts, truth = _two_objects(np.random.default_rng(0))
    planes, instances = cluster.cluster(pts)

    assert sorted(p.kind for p in planes) == ["floor", "surface"]
    table = next(p for p in planes if p.kind == "surface")
    assert abs(table.height - TABLE_Z) < 0.01
    assert table.normal[2] > 0.99

    assert len(instances) == 2
    assert all(i.label == "unknown" and i.source == "cluster" for i in instances)
    for name, xy in truth.items():
        inst = _nearest(instances, xy)
        assert np.linalg.norm(inst.centroid[:2] - xy) < 0.02, name
    box_ext = _nearest(instances, truth["box"]).box()[1]
    assert np.allclose(box_ext[:2], [0.10, 0.08], atol=0.015)
    # bottom ~PLANE_DIST of every object goes with the table -- documented, expected
    assert 0.12 < box_ext[2] < 0.16


def test_without_plane_removal_objects_do_not_come_back(monkeypatch):
    """The reason plane removal is non-negotiable: objects fuse with the tabletop."""
    pts, _ = _two_objects(np.random.default_rng(0))
    monkeypatch.setattr(cluster, "MAX_PLANES", 0)
    planes, instances = cluster.cluster(pts)
    assert planes == []
    assert instances == []   # table+objects is one 1.2 m blob, rejected as too large


def test_same_input_gives_identical_output():
    pts, _ = _two_objects(np.random.default_rng(0))
    a_planes, a_inst = cluster.cluster(pts)
    b_planes, b_inst = cluster.cluster(pts)
    assert len(a_planes) == len(b_planes) and len(a_inst) == len(b_inst)
    for p, q in zip(a_planes, b_planes):
        assert np.array_equal(p.normal, q.normal) and p.d == q.d
    for i, j in zip(a_inst, b_inst):
        assert np.array_equal(i.points, j.points)


def test_rescan_with_fresh_noise_is_stable():
    """Two scans of the same scene (different sensor noise) agree to well under a quantum."""
    runs = [cluster.cluster(_two_objects(np.random.default_rng(seed))[0]) for seed in (1, 2)]
    (_, a), (_, b) = runs
    assert len(a) == len(b) == 2
    for inst in a:
        other = _nearest(b, inst.centroid[:2])
        (ca, ea, ya), (cb, eb, yb) = inst.box(), other.box()
        assert np.linalg.norm(ca - cb) < 0.005       # half of Q_POS
        assert np.abs(ea - eb).max() < 0.005
        assert abs(ya - yb) < 2.5                    # half of Q_YAW


def test_touching_objects_are_one_blob_in_the_fallback():
    """Known weakness of Approach B (docs/15 failure mode 1). segment.py is the fix."""
    rng = np.random.default_rng(0)
    book = _box(rng, [0.00, 0.00, TABLE_Z], [0.15, 0.20, TABLE_Z + 0.04])
    mug = _cylinder(rng, 0.19, 0.10, 0.04, TABLE_Z, 0.10)                     # touching the book
    _, instances = cluster.cluster(_scene(rng, book, mug))
    assert len(instances) == 1


def _rotated_box(rng, length, width, yaw_deg, noise=NOISE, stray=0.0):
    """Upright box rotated by yaw_deg; `stray` fraction of points thrown ~3 cm sideways."""
    pts = _box(rng, [-length / 2, -width / 2, 0.0], [length / 2, width / 2, 0.10])
    c, s = np.cos(np.radians(yaw_deg)), np.sin(np.radians(yaw_deg))
    pts = pts @ np.array([[c, s, 0], [-s, c, 0], [0, 0, 1]])
    pts += rng.normal(0, noise, pts.shape)
    k = int(stray * len(pts))
    pts[rng.choice(len(pts), k, replace=False), :2] += rng.normal(0, 0.03, (k, 2))
    return pts


def _yaw_err(a, b):
    return abs((a - b + 90) % 180 - 90)


@pytest.mark.parametrize("yaw_deg", [0.0, 30.0, -60.0, 89.0, 90.0, -90.0])
def test_box_yaw_and_extents(yaw_deg):
    rng = np.random.default_rng(0)
    local = _box(rng, [-0.10, -0.025, 0.0], [0.10, 0.025, 0.05])
    c, s = np.cos(np.radians(yaw_deg)), np.sin(np.radians(yaw_deg))
    pts = local @ np.array([[c, s, 0], [-s, c, 0], [0, 0, 1]]) + [1.0, 2.0, 0.8]
    centre, ext, yaw = cluster.Instance(points=pts).box()
    assert np.allclose(centre, [1.0, 2.0, 0.825], atol=0.005)
    assert np.allclose(ext, [0.20, 0.05, 0.05], atol=0.01)   # extents[0] runs along yaw
    assert -90.0 <= yaw < 90.0                                # schema: serialize folds with % 180
    assert _yaw_err(yaw, yaw_deg) < 1.0


@pytest.mark.parametrize("stray", [0.0, 0.02])
def test_near_square_yaw_is_stable_across_rescans(stray):
    """13 x 10 cm at 40 deg: PCA wandered ~12 deg here, minAreaRect ~90 deg with strays."""
    yaws = [cluster.Instance(points=_rotated_box(np.random.default_rng(seed), 0.13, 0.10, 40.0,
                                                 stray=stray)).box()[2] for seed in range(10)]
    assert max(_yaw_err(y, 40.0) for y in yaws) < 2.5       # half of Q_YAW


def test_round_footprint_has_zero_yaw():
    pts = _cylinder(np.random.default_rng(0), 0.0, 0.0, 0.04, 0.0, 0.10)
    inst = cluster.Instance(points=pts)
    assert inst.box()[2] == 0.0
    assert inst.footprint_aspect() < cluster.ROUND_ASPECT


def test_dbscan_matches_sklearn_on_core_and_noise():
    """cluster.dbscan is scipy-only (the repo .venv has no sklearn); prove it's the same DBSCAN."""
    sk = pytest.importorskip("sklearn.cluster")
    pts, _ = _two_objects(np.random.default_rng(3))
    pts = cluster.voxel_down_sample(pts[pts[:, 2] > TABLE_Z + cluster.PLANE_DIST], cluster.VOXEL)
    pts = np.vstack([pts, np.random.default_rng(4).uniform(-0.6, 0.6, (300, 3))])  # add noise points
    ours = cluster.dbscan(pts, cluster.DBSCAN_EPS, cluster.DBSCAN_CORE)
    ref = sk.DBSCAN(eps=cluster.DBSCAN_EPS, min_samples=cluster.DBSCAN_CORE).fit(pts)
    assert np.array_equal(ours == -1, ref.labels_ == -1)
    core = ref.core_sample_indices_
    pairs = set(zip(ours[core].tolist(), ref.labels_[core].tolist()))
    assert len(pairs) == len(set(ours[core].tolist())) == len(set(ref.labels_[core].tolist()))
    assert ours.max() >= 1   # the box and the mug, at least


def test_an_object_split_by_a_dropout_band_is_one_instance():
    """docs/15 failure mode 2, and G2 seed 7's phantom: a stereo dropout band cuts a tall block,
    DBSCAN returns its base as a second cluster, and that fragment comes and goes between scans.
    Overlapping boxes are one object."""
    rng = np.random.default_rng(0)
    block = _box(rng, [0.75, -0.25, TABLE_Z], [0.83, -0.17, TABLE_Z + 0.20])
    block = block[~((block[:, 2] > TABLE_Z + 0.055) & (block[:, 2] < TABLE_Z + 0.09))]   # 3.5 cm band with no depth
    assert cluster.dbscan(cluster.voxel_down_sample(block[block[:, 2] > TABLE_Z + 0.02], 0.01),
                          cluster.DBSCAN_EPS, cluster.DBSCAN_CORE).max() == 1   # the premise: DBSCAN sees two
    _, instances = cluster.cluster(_scene(rng, block))
    assert len(instances) == 1
    assert instances[0].box()[1][2] > 0.15                                       # the whole height, one box


def test_two_separate_objects_are_not_merged_by_the_split_rule():
    rng = np.random.default_rng(0)
    a = _box(rng, [0.00, 0.00, TABLE_Z], [0.08, 0.08, TABLE_Z + 0.10])
    b = _box(rng, [0.12, 0.00, TABLE_Z], [0.20, 0.08, TABLE_Z + 0.10])           # 4 cm apart
    _, instances = cluster.cluster(_scene(rng, a, b))
    assert len(instances) == 2
