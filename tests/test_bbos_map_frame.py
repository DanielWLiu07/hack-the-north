"""Which way does heading 0 point? Pinned on a REAL pair: one pull of the robot's map and one capture taken from where
that map says the robot stood (docs/20, next to Fact 3). scripts/bbos_map.py gates every object name on this alignment,
so if a bbos update ever flips the heading convention this fails HERE, instead of the gate quietly refusing every frame
and every new object staying `unknown_…` with no error anywhere.

OPT-IN: the pair holds a camera frame of a real place with real people in it, so it lives under ~/.cache and never in a
repository. Absent, the test skips.    BBOS_FRAME_MAP=<map dir>  BBOS_FRAME_CAPTURE=<capture dir>  pytest tests/test_bbos_map_frame.py
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAP = Path(os.getenv("BBOS_FRAME_MAP", "~/.cache/gitspace/maps/20260919-175110")).expanduser()
CAPTURE = Path(os.getenv("BBOS_FRAME_CAPTURE", "~/.cache/gitspace/datasets/now/cap_1001")).expanduser()

pytestmark = pytest.mark.skipif(not ((MAP / "map.npz").is_file() and (CAPTURE / "cam0.jpg").is_file()),
                                reason="no real map + capture pair on this machine (see the module docstring)")


@pytest.fixture(scope="module")
def pair():
    for p in (ROOT / "scripts", ROOT / "perception", ROOT):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import bbos_map
    src = bbos_map.MapSource.load(MAP)
    reg = bbos_map.registration(src)
    got = bbos_map.head_frame(reg, recorded=((CAPTURE / "cam0.jpg").read_bytes(), (src.state.x, src.state.y, src.state.h)),
                              say=lambda *_: None)
    assert got is not None
    frame, facts = got
    return bbos_map, src, reg, frame, facts


def turned(bbos_map, src, reg, frame, facts, quarter_turns: int):
    import bb_source
    import capture_to_recording as c2r
    import fuse
    p = facts["pose_bb"]
    f2 = bb_source.camera_frame(frame.image, fuse.Mount(**c2r.MOUNT), (p["x"], p["y"], p["heading"] + quarter_turns * math.pi / 2),
                                reg, tuple(facts["intrinsics"]))
    return bbos_map.alignment(f2, src, *facts["stereo"])[0]


def test_heading_as_bbos_gives_it_lines_the_map_up_with_the_camera(pair):
    bbos_map, src, reg, frame, facts = pair
    a = bbos_map.alignment(frame, src, *facts["stereo"])[0]
    assert a["aligned"], a
    assert a["standing_pixels"] >= bbos_map.AGREE_PX and a["standing_within_15cm"] >= bbos_map.AGREE_MIN, a
    assert a["standing_gap_median_m"] < 0.10, a                  # measured 0.034: a lens offset bigger than this would show


@pytest.mark.parametrize("quarter_turns", [1, 2, 3])
def test_any_other_quarter_turn_is_refused(pair, quarter_turns):
    a = turned(*pair, quarter_turns)
    assert not a["aligned"], (quarter_turns, a)                  # measured 23 %, 12 %, 8 % against the 60 % gate
