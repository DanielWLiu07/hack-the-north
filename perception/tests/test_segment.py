"""segment.py on a ray-traced camera view. No model: the masks are the renderer's ground truth,
so these tests check everything AFTER the segmenter -- the lift, the edge handling, the
fallback residual -- which is the part that is ours."""
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cluster  # noqa: E402
import segment  # noqa: E402
from fuse import Mount  # noqa: E402
from segment import Mask  # noqa: E402

# depth.py output size and a plausible pinhole at that scale
W, H, F, CX, CY = 480, 270, 300.0, 240.0, 135.0
WALL_Z = 2.0
BOOK = dict(x0=-0.10, x1=0.05, y0=-0.05, y1=0.10, z=0.80)      # upright book, face toward camera
MUG = dict(cx=0.09, cz=0.80, r=0.04, y0=-0.02, y1=0.10)        # vertical cylinder; leftmost x = 0.05
BGR = {"wall": (200, 200, 200), "book": (30, 30, 200), "mug": (200, 90, 20)}


def _render():
    """(xyz, valid, left_rect, labels) in F_rect: X right, Y down, Z forward, metres.

    The mug's leftmost edge sits exactly on the book's right edge: in 3-D they are one
    connected surface.
    """
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    xn, yn = (u - CX) / F, (v - CY) / F
    z = np.full((H, W), WALL_Z)
    lab = np.zeros((H, W), np.uint8)                      # 0 wall, 1 book, 2 mug

    zb = BOOK["z"]
    on_book = (xn * zb >= BOOK["x0"]) & (xn * zb <= BOOK["x1"]) & (yn * zb >= BOOK["y0"]) & (yn * zb <= BOOK["y1"])
    z[on_book], lab[on_book] = zb, 1

    # ray (xn t, yn t, t) against (x - cx)^2 + (z - cz)^2 = r^2, nearer root
    a = xn ** 2 + 1
    b = -2 * (xn * MUG["cx"] + MUG["cz"])
    c = MUG["cx"] ** 2 + MUG["cz"] ** 2 - MUG["r"] ** 2
    disc = b * b - 4 * a * c
    t = (-b - np.sqrt(np.maximum(disc, 0))) / (2 * a)
    on_mug = (disc >= 0) & (yn * t >= MUG["y0"]) & (yn * t <= MUG["y1"]) & (t < z)
    z[on_mug], lab[on_mug] = t[on_mug], 2

    xyz = np.stack([xn * z, yn * z, z], axis=-1).astype(np.float32)
    xyz += np.random.default_rng(0).normal(0, 0.001, xyz.shape).astype(np.float32)
    valid = np.ones((H, W), bool)
    img = np.zeros((H, W, 3), np.uint8)
    for k, name in enumerate(["wall", "book", "mug"]):
        img[lab == k] = BGR[name]
    return xyz, valid, img, lab


def _masks(lab):
    return [Mask(lab == 1, "book", 0.91), Mask(lab == 2, "cup", 0.88)]


def test_touching_mug_and_book_come_back_as_two_instances():
    """ACCEPTANCE: a mug against a book is TWO instances. Geometry alone says one."""
    xyz, valid, img, lab = _render()

    # the premise: in 3-D they are one connected blob, so geometric clustering can't split them
    both = xyz[(lab > 0) & valid].astype(np.float64)
    both = cluster.voxel_down_sample(both, cluster.VOXEL)
    labels = cluster.dbscan(both, cluster.DBSCAN_EPS, cluster.DBSCAN_CORE)
    print(f"\n  geometry: DBSCAN on the {len(both)} mug+book voxels -> {labels.max() + 1} cluster(s), "
          f"{(labels == 0).mean():.1%} of points in cluster 0")
    assert labels.max() == 0 and (labels == 0).mean() > 0.95

    instances = segment.lift(xyz, valid, _masks(lab), camera="cam0", image=img)
    print(f"  masks:    segment.lift -> {len(instances)} instances")
    for i in sorted(instances, key=lambda i: i.centroid[0]):
        print(f"            {i.label:<5} {len(i.points):>5} pts  centroid {np.round(i.centroid, 3)}  colour {i.color}")
    assert len(instances) == 2
    book, mug = sorted(instances, key=lambda i: i.centroid[0])
    assert (book.label, mug.label) == ("book", "cup")
    assert np.allclose(book.centroid, [(BOOK["x0"] + BOOK["x1"]) / 2, (BOOK["y0"] + BOOK["y1"]) / 2, BOOK["z"]], atol=0.01)
    assert abs(mug.centroid[0] - MUG["cx"]) < 0.01
    assert MUG["cz"] - MUG["r"] < mug.centroid[2] < MUG["cz"]      # front half of the cylinder
    # no book points leaked into the mug or vice versa
    assert book.points[:, 0].max() < BOOK["x1"] + 0.005
    assert mug.points[:, 0].min() > MUG["cx"] - MUG["r"] - 0.005
    assert all(i.source == "segment" and i.camera == "cam0" for i in instances)


def test_mask_overshoot_does_not_drag_the_instance():
    """Real masks bleed a few px past the object, onto the book and the wall 1.2 m behind."""
    import cv2

    xyz, valid, img, lab = _render()
    fat = cv2.dilate((lab == 2).astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
    assert (fat & (lab == 0)).sum() > 50                               # it really reaches the wall

    mug = segment.lift(xyz, valid, [Mask(fat, "cup", 0.9)], "cam0")[0]
    assert abs(mug.centroid[2] - (MUG["cz"] - 0.03)) < 0.02           # not pulled toward 2 m
    assert mug.points[:, 2].max() < MUG["cz"] + 0.05


def test_ignored_labels_never_become_objects_or_residual():
    xyz, valid, img, lab = _render()
    person = Mask(lab == 1, "person", 0.99)
    discarded = []
    instances = segment.lift(xyz, valid, [person, Mask(lab == 2, "cup", 0.9)], "cam0", rejects=discarded)
    assert [i.label for i in instances] == ["cup"]
    assert [i.rejected_reason for i in discarded] == ["roomignore:person"]
    rest = segment.residual(xyz, valid, [person])
    assert len(rest) == valid.sum() - (lab == 1).sum()                 # person pixels claimed, not "unknown"


def test_residual_is_everything_no_mask_claimed():
    xyz, valid, img, lab = _render()
    rest = segment.residual(xyz, valid, _masks(lab))
    assert len(rest) == (lab == 0).sum()
    assert np.allclose(rest[:, 2], WALL_Z, atol=0.01)


def test_colour_comes_from_under_the_mask():
    xyz, valid, img, lab = _render()
    book, mug = sorted(segment.lift(xyz, valid, _masks(lab), "cam0", image=img), key=lambda i: i.centroid[0])
    assert book.color == "#c81e1e"                                    # BGR (30,30,200) -> #rrggbb
    assert mug.color == "#145ac8"


def test_too_few_valid_points_is_dropped():
    xyz, valid, img, lab = _render()
    valid = valid & ~(lab == 2)                                       # stereo dropout over the mug
    valid[lab == 2] = np.random.default_rng(0).random((lab == 2).sum()) < 0.05
    discarded = []
    instances = segment.lift(xyz, valid, _masks(lab), "cam0", rejects=discarded)
    assert [i.label for i in instances] == ["book"]
    assert discarded and discarded[0].label == "cup"
    assert discarded[0].rejected_reason in ("no_depth", "too_small")


def test_misaligned_mask_is_an_error_not_a_wrong_answer():
    xyz, valid, img, lab = _render()
    with pytest.raises(ValueError):
        segment.lift(xyz, valid, [Mask(np.zeros((H * 2, W * 2), bool), "cup", 0.9)], "cam0")


def test_run_uses_the_segmenter_on_left_rect():
    xyz, valid, img, lab = _render()
    seen = []

    def fake(image):
        seen.append(image.shape)
        return _masks(lab)

    instances, rest = segment.run(xyz, valid, img, "cam2", segmenter=fake)
    assert seen == [(H, W, 3)]
    assert sorted(i.label for i in instances) == ["book", "cup"]
    assert all(i.camera == "cam2" and i.color for i in instances)
    assert len(rest) == (lab == 0).sum()


# ── small things standing on the floor (scripts/floor_objects.py, LINK) ───────────────────

MOUNT = Mount(pitch_down_deg=38.1, height_m=1.59)
RECORDINGS = Path(os.getenv("RECORDINGS_DIR", "~/.cache/gitspace/recordings")).expanduser()
DATASETS = Path(os.getenv("DATASETS_DIR", "~/.cache/gitspace/datasets")).expanduser()


def _patch(shape, r0, c0, h, w):
    m = np.zeros(shape, bool)
    m[r0:r0 + h, c0:c0 + w] = True
    return m


def _stub_finder(*objects):
    """Stands in for find_floor_objects: (kind, mask) pairs, in its output shape."""
    def finder(world, valid, image, **kw):
        assert world.shape == image.shape and world.shape[:2] == valid.shape
        return [{"kind": k, "mask": m, "centre": [0, 0, 0], "height_m": 0.13} for k, m in objects]
    return finder


def test_a_can_is_too_small_for_the_mask_path_until_the_floor_finder_lifts_it():
    """A Red Bull can 1.3 m out is ~220 px, and YOLO-seg finds nothing that small. Lifted as a
    floor object it survives: erosion eats it, so the raw mask is used, and FLOOR_MIN_POINTS
    admits what MIN_POINTS = 150 (sized for a desk) would throw away."""
    xyz, valid, img, lab = _render()
    can = _patch(valid.shape, 40, 40, 10, 12)                     # 120 px of wall, smaller than MIN_POINTS
    assert segment.lift(xyz, valid, [Mask(can, "unknown", None)], camera="cam0") == []

    instances, rest = segment.run(xyz, valid, img, "cam0", segmenter=lambda im: _masks(lab),
                                  mount=MOUNT, robot_pose=(0.0, 0.0, 0.0),
                                  floor=_stub_finder(("object", can)))
    small = [i for i in instances if i.source == "floor"]
    assert len(small) == 1 and len(small[0].points) == 120       # every pixel: no erosion bite
    assert small[0].label == "unknown" and small[0].camera == "cam0" and small[0].score is None
    assert small[0].color is not None                              # a crop for describe.py
    assert len(instances) == 3                                     # book, mug, can


def test_the_floor_finders_pixels_leave_the_residual():
    """Its objects are lifted, so cluster() must not see them twice. What it calls
    "large" at floor height (a person, a chair) is withheld; desk-height large is not
    -- that used to hide every mug."""
    from fuse import rect_to_world

    xyz, valid, img, lab = _render()
    can, person = _patch(valid.shape, 40, 40, 10, 12), _patch(valid.shape, 100, 300, 60, 60)
    finder = _stub_finder(("object", can), ("large", person))
    _, rest = segment.run(xyz, valid, img, "cam0", segmenter=lambda im: [],
                          mount=MOUNT, robot_pose=(0.0, 0.0, 0.0), floor=finder)
    plain, _ = segment.run(xyz, valid, img, "cam0", segmenter=lambda im: [],
                           mount=MOUNT, robot_pose=(0.0, 0.0, 0.0))[1], None
    world = rect_to_world(xyz, MOUNT, (0.0, 0.0, 0.0))
    floor_large = person & (world[..., 2] < segment.FLOOR_Z_MAX)
    assert len(rest) == len(plain) - int(can.sum()) - int(floor_large.sum())


def test_a_floor_object_the_segmenter_already_named_is_not_lifted_twice():
    """The floor finder doesn't know what YOLO found. A bottle it and YOLO both see is ONE
    instance, the named one."""
    xyz, valid, img, lab = _render()
    inside = (lab == 1) & _patch(valid.shape, 120, 220, 20, 20)    # a piece of the book's mask
    assert inside.sum() > 100
    instances, _ = segment.run(xyz, valid, img, "cam0", segmenter=lambda im: _masks(lab),
                               mount=MOUNT, robot_pose=(0.0, 0.0, 0.0),
                               floor=_stub_finder(("object", inside)))
    assert [i.source for i in instances] == ["segment", "segment"]
    assert sorted(i.label for i in instances) == ["book", "cup"]


def test_the_real_can_comes_through_the_mask_path():
    """cap_0013: a 5.3 x 13.5 cm can on the hallway floor, 1.3 m out, with no model involved.
    LINK measured its centre by hand at (1.24, -0.43); cluster.py erases it entirely."""
    cap = DATASETS / "hallway-untouched" / "cap_0013"
    if not (cap / "capture.json").is_file():
        pytest.skip(f"no recording {cap}")
    import difference

    cam, view = difference.load_view(cap)
    instances, rest = segment.run(view.xyz, view.valid, view.image, cam, segmenter=lambda im: [],
                                  mount=view.mount, robot_pose=view.pose, floor=True)
    assert len(instances) == 1
    can = instances[0]
    assert can.source == "floor" and can.label == "unknown"
    assert can.centroid[:2] == pytest.approx((1.24, -0.43), abs=0.08)
    assert len(can.points) > 150                                   # every pixel: 222 of them
    _, ext, _ = can.box()
    assert 0.08 < ext[2] < 0.18                                    # 13.5 cm tall, read as ~10
    # KNOWN LIMIT, pinned so it can't drift silently: SGBM's halo fattens a small object in the
    # image, so the footprint reads ~19 x 11 cm for a 5.3 cm can. The centre is good to a few cm
    # (LINK measured 1.24, -0.43 by hand); the SIZE is not. Trimming along the ray trades the
    # height away for it (13.8 x 11.6 at +-6 cm, but the height falls to 6.9 at +-4).
    assert 0.10 < ext[0] < 0.25 and 0.05 < ext[1] < 0.20
    assert len(rest) < len(view.xyz[view.valid])                   # its pixels left the residual
