"""The colour seed in scripts/floor_objects.py: it must find the snack packets, and nothing else.

Two properties, both established by measurement over the twenty captures on disk (the tables are in
floor_objects.py's docstring), both easy to lose to a well-meant threshold nudge:

  1  a flat coloured packet is found on COLOUR, not on height. Its stereo height is a coin toss against
     the floor's own 4-6 cm noise -- cap_0018's packet reads -1.0 cm median -- so K_PACK must not ask it
     to prove height. It asks only that the pixel not read well BELOW the floor.
  2  the colour seed must use ABSOLUTE chroma, not HSV saturation alone. S is (max - min) / max, so it
     blows up as a pixel goes dark: the unlit foot of a dark door reads S 57-65 at V 68-76 while carrying
     a chroma of only 15-19 grey levels. Without CHROMA_MIN/CHROMA_MARGIN that put two false objects each
     into cap_0019 and cap_0020.

These need the hardware recordings and skip without them. The synthetic side of the same code is covered
by test_segment.py; test_floor_pipeline.py covers cap_0015 through the pipeline.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import difference
import segment

CACHE = Path.home() / ".cache/gitspace"
# (recording, how many things are really on the floor, what they are)
SCENES = [
    (CACHE / "datasets/now/cap_0015", 2, "orange chip bag at 1.0 m, small packet at 1.3 m"),
    (CACHE / "rooms/hallway-test.recordings/cap_0016", 2, "the same two, 0.99 m and 1.4 m"),
    (CACHE / "rooms/hallway-test.recordings/cap_0018", 2, "the same two, 0.97 m and 1.5 m"),
    (CACHE / "rooms/hallway-test.recordings/cap_0019", 1, "the cup only -- a dark door fills the right of frame"),
    (CACHE / "rooms/hallway-test.recordings/cap_0020", 1, "the cup only -- the same dark door, closer"),
]


@pytest.fixture(scope="module")
def finder():
    return segment._floor_finder()


def _objects(path, finder, **kw):
    if not (path / "capture.json").exists():
        pytest.skip(f"hardware recording {path.name} is not installed")
    cam, view = difference.load_view(path)
    import fuse
    import numpy as np
    W = np.full(view.xyz.shape, np.nan, np.float32)
    W[view.valid] = fuse.rect_to_world(view.xyz[view.valid], view.mount)
    origin = tuple(float(v) for v in fuse.rect_to_world(np.zeros((1, 3)), view.mount)[0])
    import pipeline
    rec = pipeline.load_recording(path)
    focal = float(pipeline._rig(str(rec.calib[cam])).Q[2, 3])
    found = finder(W, view.valid, view.image, cam_origin=origin, focal_px=focal, **kw)
    return [o for o in found if o["kind"] == "object"]


@pytest.mark.parametrize("path,n,what", SCENES, ids=lambda v: v.name if isinstance(v, Path) else "")
def test_every_floor_item_and_no_other(path, n, what, finder):
    """One box per real thing on the floor, and no extra box. `what` names what is there."""
    objs = _objects(path, finder)
    assert len(objs) == n, f"{path.name} holds {what}; got {len(objs)} objects: " + \
        ", ".join(f"h{o['height_m'] * 100:.0f}cm w{o['width_m'] * 100:.0f}cm @{o['range_m']:.2f}m" for o in objs)


def test_packet_is_not_asked_to_prove_height(finder):
    """K_PACK is the bound the flat packet cannot meet on height. Put it back the way it was
    (+0.3 sigma of height required) and cap_0018's packet -- median height -1.0 cm against a 5.6 cm
    sigma -- is lost. This is the assertion that stops it being 'tidied' back to a positive number."""
    assert len(_objects(SCENES[2][0], finder)) == 2
    assert len(_objects(SCENES[2][0], finder, k_pack=0.3)) == 1


def _is_cup(o):
    """The lidded cup: the one real thing on the floor in cap_0019/0020. ~17 cm, 400+ px, about 1.1 m out."""
    return o["height_m"] > 0.12 and o["pixels"] > 300


@pytest.mark.parametrize("path", [SCENES[3][0], SCENES[4][0]], ids=lambda p: p.name)
def test_dark_surfaces_need_real_chroma_not_just_hsv_saturation(path, finder):
    """Drop the chroma gate and the unlit foot of the dark door seeds on colour it does not have.

    The damage is not only an extra box. In cap_0019 the junk is close enough to the cup to take it
    over (the cup is reported in neither), so the check is on WHAT is reported, not how many."""
    tight = _objects(path, finder)
    assert len(tight) == 1 and _is_cup(tight[0]), "the cup should be the one object here"

    loose = _objects(path, finder, chroma_min=0.0, chroma_margin=-1e3)
    junk = [o for o in loose if not _is_cup(o)]
    assert junk, "the chroma gate is no longer what keeps the dark door's foot out"
    assert all(o["height_m"] < 0.10 for o in junk), \
        "expected the extra boxes to be low pieces of the door's foot, not something new"


# ───────────────── the pale body of a pack (BRIGHT_MARGIN) ─────────────────
# A crisp packet is not uniformly coloured: a printed end is, the foil/white middle is not. The trim that
# strips SGBM's grey halo judged pixels by colour alone, so it cut the middle out of the packet with the
# halo -- measured over the three snack captures, the mask held 24-42 % of its own bounding box against
# 76 % for the can and 60 % for the cup, and on cap_0018 the bag's mask was a ring round a hole. What
# separates body from halo is BRIGHTNESS, not colour: the halo is floor-coloured because it IS floor.

CAN = CACHE / "datasets/hallway-untouched/cap_0013"          # Red Bull can, 5.3 x 13.5 cm by hand
CLEAR = CACHE / "rooms/hallway-test.recordings/cap_1003"     # nothing on the floor -- sets BRIGHT_MARGIN
OFF = dict(bright_in_cand=False, bright_margin=1e9)          # the code exactly as it was before


def _fill(o):
    """How much of its own bounding box the mask actually holds. A donut scores low."""
    return o["pixels"] / (o["bbox"][2] * o["bbox"][3])


def _bag(objs):
    """The orange chip bag: the near, wide one of the two snacks."""
    return min(objs, key=lambda o: o["range_m"])


def _packet(objs):
    """The small red/white packet: the far one."""
    return max(objs, key=lambda o: o["range_m"])


def test_the_pale_middle_of_the_bag_is_part_of_the_bag(finder):
    """cap_0018's bag was a ring: orange left edge, orange right edge, white body outside the mask.

    The fill ratio is the assertion because it is what a hole costs and a bigger box does not hide."""
    before = _bag(_objects(SCENES[2][0], finder, **OFF))
    after = _bag(_objects(SCENES[2][0], finder))
    assert _fill(before) < 0.40, "cap_0018's bag used to fill a third of its box; this no longer reproduces"
    assert _fill(after) > 0.45, f"the bag's pale middle is being trimmed off again (fill {_fill(after):.0%})"
    assert after["pixels"] > before["pixels"] * 1.4


def test_the_far_packet_is_the_whole_packet_not_its_coloured_corner(finder):
    """At 1.45 m the packet's mask was 106 px -- one red corner, with the white body dropped. That is
    also the capture where its reported height collapsed to 2.4 cm against 8.2 cm at 1.31 m."""
    before = _packet(_objects(SCENES[2][0], finder, **OFF))
    after = _packet(_objects(SCENES[2][0], finder))
    assert after["pixels"] > before["pixels"] * 1.8, "the packet is back to its coloured corner"
    assert after["height_m"] > 0.05, "a packet whose mask is one corner reports a height near zero"


def test_the_same_packet_reads_the_same_height_at_three_ranges(finder):
    """The point of covering the body: the SAME packet read 8.2 / 5.1 / 2.4 cm over cap_0015/0016/0018
    as the floor's sigma rose. A height that falls with range is a mask artefact, not a measurement."""
    hs = [_packet(_objects(p, finder))["height_m"] * 100 for p, _, _ in SCENES[:3]]
    assert max(hs) - min(hs) < 3.5, f"the packet's height still depends on the range it was seen at: {hs}"


def test_pale_body_leaves_a_textured_object_untouched(finder):
    """The pale path may only ever act on a colour-seeded pack. The can is found on texture and height
    (it seeds 14 % on colour), and its width is the one figure here with a hand measurement: 5.3 cm."""
    before = _objects(CAN, finder, **OFF)
    after = _objects(CAN, finder)
    assert len(before) == len(after) == 1
    assert after[0]["pixels"] == before[0]["pixels"], "the pale path is reaching a textured object"
    assert abs(after[0]["width_m"] - 0.053) < 0.01


def test_bright_margin_sits_clear_of_the_cliff_that_sets_it(finder):
    """A pale pixel is a CANDIDATE, never a seed -- it can join a component that colour or height
    already seeded, and can never start one. It still changes a component's shape, so it is bounded by
    a real capture: cap_1003's floor is clear, and below ~16 grey levels its own bright patches grow
    into two false objects. BRIGHT_MARGIN is 30 so that -30 % (21) is still clear of that."""
    assert _objects(CLEAR, finder) == []
    assert _objects(CLEAR, finder, bright_margin=21.0) == []
    assert _objects(CLEAR, finder, bright_margin=39.0) == []
    assert len(_objects(CLEAR, finder, bright_margin=15.0)) == 2, \
        "the cliff that justifies BRIGHT_MARGIN = 30 no longer reproduces; re-measure before moving it"
