"""merge.py on hand-placed F_world instances: one object per physical thing, every view kept."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import merge  # noqa: E402
from cluster import Instance  # noqa: E402
from describe import ViewDescription  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SAID = {"cam0": "a blue ceramic mug", "cam1": "cup with handle, chipped", "cam2": "cylindrical container, dark"}


def _blob(centre, size=0.08, n=400, seed=0, side=None):
    """Points filling a cube around `centre`; `side` keeps only the half one camera would see."""
    rng = np.random.default_rng(seed)
    p = rng.uniform(-size / 2, size / 2, (n, 3))
    if side is not None:
        p = p[p[:, side[0]] * side[1] >= -0.01]
    return p + centre


def _inst(cam, centre, label="cup", seed=0, side=None, size=0.08):
    return Instance(points=_blob(centre, size, seed=seed, side=side), label=label, source="segment",
                    camera=cam, score=0.9,
                    description=ViewDescription(cam, label, SAID.get(cam, f"{label} from {cam}"), "fake", 1))


MUG = np.array([0.42, 0.18, 0.80])
BOOK = np.array([0.10, -0.20, 0.78])


def _three_camera_mug():
    # each camera sees a different side, and each camera's centroid is pulled toward its side
    return [_inst("cam0", MUG + [0.004, 0, 0], seed=1, side=(0, -1)),
            _inst("cam1", MUG + [-0.003, 0.002, 0], seed=2, side=(1, 1)),
            _inst("cam2", MUG + [0.030, 0, 0], seed=3, side=(0, 1))]


def test_three_cameras_one_mug_all_three_descriptions_kept():
    objs = merge.merge(_three_camera_mug() + [_inst("cam0", BOOK, "book", 4), _inst("cam1", BOOK, "book", 5)])
    assert len(objs) == 2
    mug = next(o for o in objs if "cup" in o.labels)
    assert mug.cameras == ["cam0", "cam1", "cam2"]
    assert [d.text for d in mug.descriptions] == [SAID["cam0"], SAID["cam1"], SAID["cam2"]]


def test_observations_are_one_row_per_camera_with_its_own_position_and_words():
    """What /capture/<id> renders: cam0 x=..., cam1 x=..., cam2 x=... and three descriptions."""
    (mug,) = merge.merge(_three_camera_mug())
    rows = mug.observations()
    assert [r["camera"] for r in rows] == ["cam0", "cam1", "cam2"]
    assert [r["raw_description"] for r in rows] == [SAID["cam0"], SAID["cam1"], SAID["cam2"]]
    xs = [r["raw_x"] for r in rows]
    assert len(set(xs)) == 3 and max(xs) - min(xs) > 0.03          # per-camera disagreement survives
    assert all(isinstance(r["raw_x"], float) for r in rows)        # unquantized, JSON-able

    mapping = REPO / "elastic" / "mappings" / "room-observations.json"
    if mapping.exists():                                             # the index is dynamic: strict
        allowed = set(json.loads(mapping.read_text())["template"]["mappings"]["properties"])
        for r in rows:
            assert set(r) <= allowed, set(r) - allowed


def test_touching_objects_from_one_camera_are_never_merged():
    """Two cups touching, same label, centroids 8 cm apart, boxes overlapping: still two."""
    a, b = _inst("cam0", MUG, seed=1), _inst("cam0", MUG + [0.08, 0, 0], seed=2)
    assert len(merge.merge([a, b])) == 2


def test_touching_mug_and_book_seen_by_two_cameras_stay_two_objects():
    """ACCEPTANCE through the merge stage: mug against book -> 2 objects, 2 views each."""
    book_c, mug_c = MUG - [0.09, 0, 0], MUG
    parts = [_inst("cam0", book_c, "book", 1, size=0.10), _inst("cam0", mug_c, "cup", 2),
             _inst("cam1", book_c + [0.005, 0, 0], "book", 3, size=0.10), _inst("cam1", mug_c + [0, 0.004, 0], "cup", 4)]
    objs = merge.merge(parts)
    assert sorted(sorted(o.labels) for o in objs) == [["book", "book"], ["cup", "cup"]]
    assert all(o.cameras == ["cam0", "cam1"] for o in objs)


def test_unknown_cluster_is_absorbed_by_the_labelled_view():
    """The fallback's `unknown` is the absence of a label, not a disagreeing one."""
    objs = merge.merge([_inst("cam1", MUG, "cup", 1), Instance(points=_blob(MUG + [0.01, 0, 0], seed=2))])
    assert len(objs) == 1 and objs[0].labels == ["cup", "unknown"]


def test_disagreeing_labels_need_the_embedding_to_merge():
    a, b = _inst("cam0", MUG, "cup", 1), _inst("cam1", MUG + [0.01, 0, 0], "vase", 2)
    assert len(merge.merge([a, b])) == 2
    same = lambda text: [1.0, 0.0]                                   # an embedder that calls them identical
    assert len(merge.merge([a, b], embed=same)) == 1


def test_far_apart_or_non_overlapping_same_label_do_not_merge():
    a, b = _inst("cam0", MUG, seed=1), _inst("cam1", MUG + [0.20, 0, 0], seed=2)
    assert len(merge.merge([a, b])) == 2


def test_no_chaining_across_three_cameras():
    """cam0~cam1 and cam1~cam2 each within 15 cm, but cam0-cam2 is 24 cm: not one object."""
    parts = [_inst("cam0", MUG, seed=1, size=0.14), _inst("cam1", MUG + [0.12, 0, 0], seed=2, size=0.14),
             _inst("cam2", MUG + [0.24, 0, 0], seed=3, size=0.14)]
    objs = merge.merge(parts)
    assert not any({"cam0", "cam2"} <= set(o.cameras) for o in objs)
    assert sorted(len(o.views) for o in objs) == [1, 2]


def test_merge_is_deterministic():
    parts = _three_camera_mug() + [_inst("cam0", BOOK, "book", 4)]
    a, b = merge.merge(parts), merge.merge(parts)
    assert [o.cameras for o in a] == [o.cameras for o in b]
    assert all(np.array_equal(x.points, y.points) for x, y in zip(a, b))
