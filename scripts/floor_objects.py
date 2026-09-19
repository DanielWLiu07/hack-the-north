#!/usr/bin/env python3
"""floor_objects.py — things standing on the floor, segmented in IMAGE SPACE on the aligned height map.

    python scripts/floor_objects.py RECORDING --out overlay.jpg            # one line per instance + the overlay
    python scripts/floor_objects.py RECORDING --json                       # ...and the instances as JSON
    python scripts/floor_objects.py RECORDING --confirm-with OTHER         # is each instance there in OTHER too?
    python scripts/floor_objects.py RECORDING --all --out overlay.jpg      # rejected / building components too, with why

WHY THIS EXISTS. A Red Bull can on the hallway floor (5.3 x 13.5 cm, 1.3 m out: cap_0013) is about 8 x 19 px of the
rectified image. perception/cluster.py never sees it — 1 cm voxels, statistical outlier removal and MIN_CLUSTER_PTS = 40
are sized for a desk — YOLO-seg finds nothing that small, and differencing two captures gives ~25 phantom blobs.

WHY IMAGE SPACE. depth.py's xyz is aligned pixel for pixel with the rectified left image, so a pixel mask IS a 3-D point
selection: no voxel grid to fall between, every pixel of a 150-pixel object is kept, neighbours are neighbours, and the
image's own edges are available to say where the matcher measured and where it guessed. That last part carries the method.

WHAT THE HEIGHT MAP REALLY LOOKS LIKE (seven hallway captures, blank grey lino, measured here PER PIXEL — the 11 mm
floor-flatness figure in circulation does not hold per pixel):
    per-PIXEL floor height sigma   2.5 cm under the robot · 2.9 @ 0.5 m · 3.4 @ 0.9 · 4.3 @ 1.3 · 5.8 @ 1.7 · 6.6 @ 2.1 m
    and it is not white: horizontal streaks ~60 x 10 px, +-5 cm — SGBM carrying a neighbour's disparity along the scanline
    where the floor gives it nothing to match (image gradient on that floor: median 0, 99.99 % under 3 grey levels / px).
    The blank floor also reads BOWED: -2 cm under the robot, +2 to +3 cm at 1.3 m. A plane through it says +4.5 to +7 cm
    at 1.3 m, where the floor round the can reads 0 to +2.
So the can (top at 13.5 cm, sigma there 4.3 cm) is a 3-sigma bump in correlated noise. Height alone cannot find it: the
hallway shows several blank-floor blobs as high. What separates it is that the can has EDGES (10-35 grey levels / px
after a 1 px blur), and at edges the matcher measures instead of guessing.

THE METHOD, in find_floor_objects():
  1  height = z above the HIGHER of two floors: the mount's z = 0 and a robust quadratic through the blank floor.
  2  texture support = image gradient >= GRAD_MIN, grown by BLOCK_R (what one SGBM block can see).
  3  sigma(range) = the blank floor's own height noise in 20 cm rings, from THIS capture. Thresholds are multiples of it.
  4  candidate pixels: within MAX_RANGE_M and (textured and higher than K_BODY sigma) or (higher than K_FREE sigma, texture
     or not) or (COLOURED — saturated AND chroma over CHROMA_MIN grey levels — and not below the floor by more than
     K_PACK sigma, under PACK_H_MAX). Seeds: the same with K_SEED, plus the colour pack pixels. 3x3 close, cut at
     range jumps > DISCONT_M (a person is not the wall behind them), 8-connected components, keep those with enough
     seed area.
  5  each component is measured from its own 3-D points and asked: area · wide_enough · base_on_floor · floor_under (floor visible
     under its base) · free_standing (what shows over its top is farther away, not the same wall) · not_border ·
     not_range_cut · stands_alone (not within PART_OF_M of the building or of a large thing, in plan, at its own height).
     Things under 8 cm skip free_standing: a 4 cm bag has no shadow the matcher can see. If pack pixels are
     PACK_SEED_FRAC of the seed, the mask grows from those pixels only while it stays PACK_CORE_FRAC pack BODY --
     coloured or pale (BRIGHT_MARGIN over this capture's floor). A packet's printed end is coloured and its foil
     middle is not, so judging that trim by colour alone cut the packet in half; the grey halo it exists to strip
     is floor-coloured AND floor-bright, so it is still stripped.
  6  kinds: "object" passed everything · "large" is a person / a chair · "structure" is the building (wider than
     STRUCT_W_M, taller than STRUCT_H_M, or large and running past the range limit) · "rejected" carries the tests it failed.
What each part buys, on the seven captures: with the texture gate off (GRAD_MIN 0) the four empty captures give 5 / 4 /
5 / 2 false objects and cap_0013 gives 11; with it, 0. Of the tests only three ever decide alone — base_on_floor (floating
mismatches at the image's right edge), stands_alone (a door frame's foot, a walking person's shoe), not_range_cut; area,
floor_under, free_standing and not_border agree with them here but this data never makes one of them the only reason.
And on all twenty: the chroma gate alone takes the false objects from 6 to 1 and costs nothing, K_PACK -0.5 alone adds
cap_0018's packet, and the two together give 12/12 items with one false object. Neither is redundant — K_PACK -0.5
WITHOUT the chroma gate costs cap_0019's cup (the extra colour-seeded junk beside it swallows it) and leaves 4 false.
Not used, on purpose: a "points stack vertically, not along the ray" test. At 1.3 m one disparity step is 1.7 cm along the
ray and the matcher's halo smears a 5 cm can over 15-20 cm of it (depth_m); that number cannot tell a can from a smear.

THE THRESHOLDS and why each has its value. Each was moved -30 % and +30 % on its own over ALL TWENTY captures now on
disk (the original seven, plus cap_0004/0005/0006/0014/0016/0018..0021/1001/1002/1003): no setting gives more than ONE
false object in any capture. Four lose a real item at one end — K_SEED +30 % (both cans), MAX_RANGE_M -30 % (cap_0012's
can, then 8 cm from the limit, and cap_0018's packet), K_BODY -30 % (cap_0019's cup) and PART_OF_M +30 % (the same cup,
which stands 25 cm from a backpack). The ones that matter are K_SEED, PART_OF_M and CHROMA_MARGIN:
    MAX_RANGE_M 2.2      past it sigma > 6.5 cm, so nothing under 17 cm could be told from floor anyway
    K_SEED 2.5           one-sided 0.6 % of noise pixels; the can survives 1.5-3.0, dies at 3.25 (it IS a 3-sigma object)
    K_BODY 1.0           follows a seeded, textured component down towards its base; 0.7-1.3 changes nothing
    K_FREE 5.0           blank things (a white box, a trouser leg) need no texture once noise cannot explain them
    GRAD_MIN 6           2x the blank floor's 99.99th percentile (3), far under a real edge (10-35). 6-9 changes nothing;
                         4.2 adds one object, a piece of the chair whose corner is cut by cap_0009's border; 3 adds noise
    BLOCK_R 3            half of depth.py's WINDOW_SIZE 7
    MIN_SEED_M2 8 cm2    a 2 x 4 cm patch of significantly raised surface (the can shows 29 and 93 cm2)
    MIN_AREA_M2 32 cm2   4 x 8 cm, ~45 px at 1.3 m: with fewer the component is mostly the matcher's halo, not the thing
    UNDER_FLOOR_MIN .5   half the strip under the base reads as floor (the can: 0.96, 1.00; things on a wall: ~0)
    GAP_MIN_M 0.10       over a free-standing top the view lands its shadow length, height / tan(elevation), behind it
                         (can: 26 and 35 cm); half of that is asked, capped here. Skirting reads -7 to +9 cm
    DISCONT_M 0.15       neighbouring pixels on one surface differ by 1-5 cm of range; a person vs the wall by > 50
    RANGE_EDGE_M 0.05    two disparity steps at the limit
    PART_OF_M 0.20       position noise of a wall and of an object centre, ~10 cm combined, twice. Skirting and door-frame
                         feet measure 0.02-0.12 m from the building, the can 0.30 and 0.50: 0.14-0.26 gives the same
                         answer, 0.10 lets a door-frame foot through, 0.30 swallows the can in cap_0012
    LARGE_M 0.6 · STRUCT_W_M 1.5 · STRUCT_H_M 1.9     nothing you step over is 60 cm; no person is 1.5 m wide or 1.9 m tall
    SAT_MIN 50 / SAT_MARGIN 30 / PACK_H_MAX 0.25
                         colourful packs lying flat: seed coloured pixels on the floor even
                         when they are under K_SEED x sigma. Relative to this capture's blank-floor
                         sat p90, so an orange floor does not seed itself.
    CHROMA_MIN 30 / CHROMA_MARGIN 28   grey levels of max(BGR) - min(BGR), asked for ON TOP of the HSV
                         saturation. HSV S is (max - min) / max, so it is ill-conditioned as a pixel
                         goes dark: at V = 60 an eleven-level channel imbalance already reads S = 50,
                         which JPEG chroma noise and purple fringing supply for free. That is what the
                         foot of a dark door is made of, and it is what put TWO false objects each in
                         cap_0019 and cap_0020 (S 57-65 at V 68-76 — but an absolute chroma of only
                         15-19, against 56-78 for the real packets and blank-floor p90 10.4 / p99 13.9).
                         Chroma is in grey levels, where the noise does not scale with darkness.
                         CHROMA_MARGIN is the one doing the work (the floor's own p90 + 28 = ~38 beats
                         the absolute 30 on every capture here); it holds 12/12 items with <= 1 false
                         object per capture over 20-40, loses the small packet at 50, and lets the dark
                         door back in at 16 and below. 28 is the middle, so +-30 % stays inside.
    BRIGHT_MARGIN 30     grey levels over THIS capture's blank-floor p90 that make a pixel the pale BODY of a
                         pack. A CANDIDATE, never a seed: it can join a component colour or height already
                         seeded and can never start one. Measured, the three snack captures: the mask held
                         24-42 % of its own bounding box against 76 % for the can and 60 % for the cup -- on
                         cap_0018 the bag's mask was a ring around a hole where its white middle should be.
                         With it: 54 / 37 / 30 % on the bags, and the far packet 106 -> 260 px. The figure that
                         matters is that the SAME packet now reads 8.8 / 6.4 / 7.3 cm over cap_0015/0016/0018
                         instead of 8.2 / 5.1 / 2.4: a height that fell with range was a mask artefact. The can
                         and the cup are untouched to the pixel (233 / 254 / 426 / 465 / 619 px), which is the
                         point -- the pale path only ever acts on a component the colour route already owns.
                         Bounded below by cap_1003, whose clear floor grows TWO false objects at 15 and below
                         (16-18 give one); 30 keeps -30 % (21) clear of that, and 21 through 70 are identical.
    K_PACK -0.5          how far BELOW the floor a coloured pixel may read and still seed. It was +0.3
                         — i.e. a wrapper had to prove 0.3 sigma of HEIGHT — but a flat wrapper IS floor
                         height (cap_0018's reads -1.0 cm median against a 5.6 cm sigma), so that asked
                         it to win a coin toss against the noise and cost cap_0018's packet outright.
                         The evidence for a pack is its COLOUR; PACK_H_MAX caps the other end. Measured:
                         anything from -0.1 to -4.0 gives the same 12/12 and the same one false object,
                         so the bound is not load-bearing on this data — -0.5 ("consistent with resting
                         on the floor, within the noise") keeps a guard against a coloured mismatch
                         floating below the floor, which this data does not happen to contain.
    MIN_WIDTH_M 4 cm     across the line of sight, before halo strip. Saturation slivers at a
                         chair foot (cap_0014) are 1–2 cm; a chip bag is 10 cm.
    PACK_SEED_FRAC 0.4   of the component's seed that is pack. Above this the mask grows from
                         the saturated core while it stays PACK_CORE_FRAC colourful — the bag,
                         not the grey stereo halo. Bags measure 0.66 / 0.91; the can measures 0.14.
    PACK_CORE_FRAC 0.55  stop growing the pack mask when colour would fall below this.

WHAT IT CAN AND CANNOT SEE — from the pixel footprint (range / 246 px), one disparity step (1/16 px = range^2 / 253 m
along the ray, x sin(elevation) in height) and the measured sigma. The lens is 1.59 m up.
    ground range                0.5 m    1.0 m    1.3 m    1.7 m    2.2 m
    one pixel spans             0.7 cm   0.8 cm   0.8 cm   0.9 cm   1.1 cm
    one disparity step, height  1.0 cm   1.2 cm   1.3 cm   1.5 cm   1.7 cm
    floor sigma (measured)      2.9 cm   3.5 cm   4.3 cm   5.8 cm   6.7 cm
    shortest thing reported     7 cm     9 cm     11 cm    14 cm    17 cm      (K_SEED x sigma)
    ... without any texture     15 cm    18 cm    22 cm    29 cm    33 cm      (K_FREE x sigma)
    narrowest, about 3 px       2 cm     2.5 cm   2.5 cm   3 cm     3.5 cm     (under half a block the matcher skips it;
                                                                               NOT verified: the one sample is 6 px wide)
Those two rows are the HEIGHT routes. A COLOURED thing is not bound by them at all: the pack route seeds on colour and
asks nothing of height (K_PACK), so the limit on a chip bag is how many coloured pixels it still spans — MIN_SEED_M2
8 cm2 is ~11 px, and the observed bags hold 57-116 coloured pixels at 1.0-1.5 m. Measured: the orange bag is found at
0.97, 0.99 and 1.01 m, the small packet at 1.32, 1.42 and 1.47 m, and the packet's reported height falls 7.5 -> 5.2 ->
2.2 cm over those three as the floor's sigma rises 3.9 -> 4.6 -> 5.6 cm. The box stays right; the HEIGHT of a flat
coloured thing is not a measurement, it is noise, and it should not be believed under about 2 sigma.
So: a 13.5 cm can out to ~1.5 m; a shoe (10 cm) within ~1 m; a colourful chip bag lying flat out to at least 1.5 m
(no sample farther away yet). A cable, a phone, a book the colour of the floor: never, at any range.
Also never: a thing within ~25 cm of a wall or of a person's feet (it becomes part of them), a thing cut by the image
border, a thing the colour of the floor and under K_FREE sigma. Accuracy on the one object with ground truth: centre 0.6 cm
from the hand measurement, height 13.0 and 16.6 cm for a 13.5 cm can (two captures: +-2 disparity steps), width 5.4 and
5.3 cm for 5.3 cm once the halo (2 x BLOCK_R px) is taken off.

ALL TWENTY CAPTURES, scored by hand against the images (an item is "found" when a reported box lands on it; every other
box called "object" is counted false). 12 real floor items in 9 captures; the other 11 captures have a clear floor:
    cap_0012  Red Bull can @ 1.46 m   FOUND  h 16.6 cm  w 5.3 cm   257 px
    cap_0013  Red Bull can @ 1.31 m   FOUND  h 13.0 cm  w 5.3 cm   233 px   (hand-measured 13.5 x 5.3 cm)
    cap_0015  orange chip bag @ 1.01  FOUND  h 11.9 cm  w 13.3 cm  611 px   · small packet @ 1.32 m  FOUND  h 7.5 cm
    cap_0016  orange chip bag @ 0.99  FOUND  h 10.6 cm  w 12.9 cm  374 px   · small packet @ 1.42 m  FOUND  h 5.2 cm
    cap_0018  orange chip bag @ 0.97  FOUND  h  7.3 cm  w 12.3 cm  270 px   · small packet @ 1.47 m  FOUND  h 2.2 cm
    cap_0019  clear lidded cup @ 1.07 FOUND  h 17.0 cm · cap_0020 @ 1.13 FOUND 16.7 · cap_0021 @ 1.15 FOUND 20.8
    cap_1001  the same cup @ 1.28 m   FOUND  but measured badly (25.8 cm: stereo on transparent plastic)
    ONE false object in twenty captures: cap_0006, a 19 x 7 px piece of a dark door's foot at 1.64 m, 22 cm from the
    building — just outside PART_OF_M, so stands_alone lets it through. It is 84 px of a surface that gives the matcher
    nothing; see the dark-pixel note under CHROMA_MIN for why the sigma multiples mean less there than they say.
Before the chroma gate and K_PACK went in, the same 20 captures gave 11/12 items and SIX false objects, two each in
cap_0019 and cap_0020 — i.e. the "no more than one false object in any capture" rule, which holds on the original
seven, was already broken by captures taken after it was written. It holds again now.

IS THE BOX SIZE HONEST? Separately from whether a thing is found. WIDTH is, where there is ground truth: the can
measures 5.3 and 5.3 cm for a hand-measured 5.3 once the halo (2 x BLOCK_R px) is taken off. HEIGHT is only honest
where the mask covers the thing -- see BRIGHT_MARGIN; the packet's height stopped falling with range once it did.
The small packet's WIDTH still does not settle (4.3 / 6.7 / 8.6 cm over the three captures, rising with range): it
is 18-23 px of a foreshortened object and the halo correction is a fixed pixel count, so do not quote it as a size.
The 95th percentile that height_m reports reads high on wide things by about one sigma (cap_0015's bag: height_m 11.9,
height_median_m 6.5) — height_median_m is there too. cap_0014 has nothing small on the floor and ONE false object, the
foot of a chair the image edge cuts off. Two flags cover that last case without hiding anything:
`cut_by_border` on a large thing (its size is a lower bound) and `beside_cut` on an object within PART_OF_M of something
edge-cut (the chair's foot, 3 cm; but also the cup, 14 cm from a bag the edge cuts). Still wrong there: the black frame of
a glass wall comes out "large" (cap_0015) — glass gives no depth, so to this sensor the frame is a post standing free.

--confirm-with. The pose is not tracked and the robot balances, so it MOVES between captures: cap_0012 -> cap_0013 is
24 cm forward and 2.6 deg of yaw. register() finds that motion from the building (ORB matches lifted to 3-D, planar fit
weighting error across the line of sight 4x error along it) and confirm() compares centres after it. What is left between
the can's two positions is 10 cm, so CONFIRM_M is 12 cm, not the 6 cm a fixed robot would allow.

Standalone: needs only numpy and cv2 (scipy for --confirm-with); imports perception/ just to load a recording.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]

# ───────────────── thresholds — every one has a physical reason, given in the module docstring ─────────────────
MAX_RANGE_M = 2.2        # ground range from the robot; past it the floor's own height noise is > 6.5 cm
FLOOR_BAND_M = 0.15      # |z| inside this "could be floor": feeds the floor fit and the noise model, nothing else
K_SEED = 2.5             # a component needs pixels this many floor-noise sigmas above the floor ...
K_BODY = 1.0             # ... and is followed through textured pixels down to this many
K_FREE = 5.0             # this many sigmas up, height alone is evidence: no texture asked for
GRAD_MIN = 6.0           # grey levels / px: an image edge the matcher can lock onto (blank floor: 99.99 % under 3)
BLOCK_R = 3              # px: half of depth.py's SGBM block (WINDOW_SIZE 7) — an edge supports depth this far away
MIN_SEED_M2 = 0.0008     # apparent area of its pixels above K_SEED: a 2 cm x 4 cm patch
MIN_AREA_M2 = 0.0032     # apparent area of the whole component: 4 cm x 8 cm, the smallest thing worth reporting
UNDER_FLOOR_MIN = 0.5    # share of the strip under its base that must read as floor
GAP_MIN_M = 0.10         # what is seen over its top must be this much farther away than the thing itself
DISCONT_M = 0.15         # a range jump this big between neighbouring pixels is an occlusion edge; nothing crosses it
RANGE_EDGE_M = 0.05      # farthest points this close to MAX_RANGE_M: it carries on past the limit, size unknown
PART_OF_M = 0.20         # closer than this to a large thing or to the building: its extremity, not an object
HOST_PX = 20             # ... counting only a host that puts at least this many pixels that close
LARGE_M = 0.60           # wider or taller than this is "large" (a person, a chair): reported, but apart
STRUCT_W_M = 1.50        # wider than this is the building
STRUCT_H_M = 1.90        # taller than this is the building
CONFIRM_M = 0.12         # --confirm-with: the same thing seen again lands within this, after registration
SAT_MIN = 50.0           # HSV saturation: grey lino p90 is 18; chip bags are 100+
SAT_MARGIN = 30.0        # above this capture's blank-floor sat p90, if that is higher than SAT_MIN
K_PACK = -0.5            # floor-sigma bound for colour-seeded pixels. A flat wrapper IS floor height: the evidence
                         # is its colour, so this only asks that it not read well BELOW the floor.
PACK_H_MAX = 0.25        # m; packs are shorter than this. A torso is not a pack.
MIN_WIDTH_M = 0.04       # across the line of sight (raw). A 2 cm sliver is matcher noise, not a bag.
PACK_SEED_FRAC = 0.4     # of a component's seed. Bags are 0.66 / 0.91; a can is 0.14. Above this,
                         # the mask is the colourful pack, not the matcher's grey halo around it.
PACK_CORE_FRAC = 0.55    # grown pack mask must stay this packed-colour. k=5 on the near bag, k=3 on the far one.
CHROMA_MIN = 30.0        # max - min over BGR, grey levels: real colour, at any brightness (blank floor p99 is 14)
CHROMA_MARGIN = 28.0     # ... or this above the blank floor's own p90, whichever is higher
BRIGHT_MARGIN = 30.0     # grey levels above THIS capture's blank-floor p90 that make a pixel the pale BODY of a
                         # pack rather than the matcher's grey halo. A candidate, never a SEED: a pale pixel can
                         # join a component that colour or height already seeded, and can never start one.


def _design(x, y):
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=-1)


def fit_floor(W: np.ndarray, near: np.ndarray, band: float = FLOOR_BAND_M):
    """(6 coefficients, robust sigma) of the floor AS THE MATCHER READS IT: z = quadratic in (x, y), through the near
    pixels within `band` of z = 0, 2-sigma trimmed. Quadratic, not a plane: on a blank floor SGBM's reading bows — about
    -2 cm under the robot, +2 to +3 at 1.3 m, on all seven hallway captures — and a plane through that says +4.5 to +7 cm
    at 1.3 m, where the floor round the can reads 0 to +2. With the quadratic every range band's median is within 1 cm
    of zero out to 1.8 m."""
    x, y, z = W[..., 0], W[..., 1], W[..., 2]
    m = near & (np.abs(z) < band)
    if m.sum() < 5000:
        return np.zeros(6), float("nan")             # no floor in view: trust the mount, z = 0
    A, b = _design(x[m], y[m])[::4], z[m][::4]
    keep = np.ones(len(b), bool)
    coef, s = np.zeros(6), float("nan")
    for _ in range(8):
        coef, *_ = np.linalg.lstsq(A[keep], b[keep], rcond=None)
        res = b - A @ coef
        s = 1.4826 * float(np.median(np.abs(res[keep])))
        keep = np.abs(res) < 2.0 * s
    return coef, s


def noise_model(h: np.ndarray, r: np.ndarray, flat: np.ndarray, max_range: float) -> list[tuple[float, float]]:
    """[(ground range, sigma)] of the floor's own height, in 20 cm rings, from THIS capture's blank floor (MAD). The
    thresholds are multiples of it, so a floor the matcher reads better (carpet, tiles) lowers them by itself."""
    edges = np.arange(0.0, max_range + 1e-6, 0.2)
    bins = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = flat & (r >= lo) & (r < hi)
        if m.sum() < 800:
            continue
        v = h[m]
        bins.append(((lo + hi) / 2, 1.4826 * float(np.median(np.abs(v - np.median(v))))))
    return bins or [(1.0, 0.045)]                     # no blank floor to measure: the hallway's figure at 1.3 m


def _nearest(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """For each row of a, the distance to the nearest row of b (b thinned: this is a 20 cm question)."""
    b = b[::max(len(b) // 400, 1)]
    return np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1)).min(axis=1)


def find_floor_objects(xyz_world, valid, image, *, cam_origin=(0.0, 0.0, 1.59), focal_px=246.0,
                       max_range=MAX_RANGE_M, floor_band=FLOOR_BAND_M, k_seed=K_SEED, k_body=K_BODY, k_free=K_FREE,
                       grad_min=GRAD_MIN, block_r=BLOCK_R, min_seed_m2=MIN_SEED_M2, min_area_m2=MIN_AREA_M2,
                       under_floor_min=UNDER_FLOOR_MIN, gap_min_m=GAP_MIN_M, discont_m=DISCONT_M,
                       range_edge_m=RANGE_EDGE_M, part_of_m=PART_OF_M, large_m=LARGE_M, struct_w_m=STRUCT_W_M,
                       struct_h_m=STRUCT_H_M, sat_min=SAT_MIN, sat_margin=SAT_MARGIN, k_pack=K_PACK,
                       pack_h_max=PACK_H_MAX, min_width_m=MIN_WIDTH_M, pack_seed_frac=PACK_SEED_FRAC,
                       pack_core_frac=PACK_CORE_FRAC, chroma_min=CHROMA_MIN, chroma_margin=CHROMA_MARGIN,
                       bright_margin=BRIGHT_MARGIN, bright_in_cand=True,
                       keep_rejected=False, debug=None) -> list[dict]:
    """Instances of things standing on the floor, from ONE camera's aligned outputs.

        xyz_world  (H,W,3) float · fuse.rect_to_world of depth.py's xyz: x fwd, y left, z up, floor at z = 0, metres
        valid      (H,W) bool    · depth.py's mask
        image      (H,W,3) uint8 · BGR, the rectified left image the other two are aligned with
        cam_origin, focal_px     · where the lens is in that frame, and the focal length at depth.DOWNSAMPLE: only used
                                   to turn pixel counts into square centimetres. main() reads both off the recording.

    Returns a list of dicts, nearest first. `kind` is "object" (passed every test), "large" (a person, a chair: over
    LARGE_M, standing on the floor, clear of the building) and, with keep_rejected, "rejected" / "structure" too — each
    with `tests` {name: passed} saying why. Per instance: centre [x, y, z] (z = half its height), height_m, width_m
    (across the line of sight, the matcher's halo taken off), width_raw_m, depth_m (ALONG the line of sight: smeared,
    do not trust it), pixels, range_m, bbox [x, y, w, h], pixel_centre, and `mask` (H,W) bool — xyz_world[mask] are
    its points. Every threshold is a keyword so a caller, or the sensitivity sweep, can move one at a time."""
    W = np.asarray(xyz_world, np.float32)
    rows, cols = valid.shape
    x, y, z = W[..., 0], W[..., 1], W[..., 2]
    r = np.where(valid, np.hypot(x, y), np.inf)
    near = valid & (r < max_range)
    C = np.asarray(cam_origin, np.float32)

    # 1 · two floors, and the height above the HIGHER of them. The mount's (z = 0: measured, good to +-2 cm with the
    # +-0.5 deg balance wobble) and the matcher's own reading of the blank floor (fit_floor). Where the blank floor reads
    # low a scuff mark would stand 8 cm proud of the fit; where it reads high a can would gain 3 cm on z = 0. Neither may.
    coef, floor_s = fit_floor(W, near, floor_band)
    fit = (_design(np.nan_to_num(x), np.nan_to_num(y)) @ coef).astype(np.float32)
    hfit = np.where(valid, z - fit, 0.0).astype(np.float32)
    h = np.where(valid, z - np.maximum(fit, 0.0), 0.0).astype(np.float32)

    # 2 · where the matcher had something to match. SGBM only MEASURES at image texture; across a blank floor it
    # carries its neighbours' disparity along each scanline, and that is what the +-5 cm streaks are.
    grey = cv2.GaussianBlur(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 1.0)
    grad = np.hypot(cv2.Sobel(grey, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(grey, cv2.CV_32F, 0, 1, ksize=3)) / 8.0
    k = 2 * block_r + 1
    textured = cv2.dilate((grad >= grad_min).astype(np.uint8), np.ones((k, k), np.uint8)) > 0

    # 3 · the floor's own noise by range, from the blank floor well clear of anything tall
    tall = cv2.dilate((valid & (h > 0.30)).astype(np.uint8), np.ones((31, 31), np.uint8)) > 0
    bins = noise_model(hfit, r, near & (np.abs(hfit) < floor_band) & ~textured & ~tall, max_range)
    sigma = np.interp(np.where(np.isfinite(r), r, 0.0), [b[0] for b in bins], [b[1] for b in bins]).astype(np.float32)

    # 3b · colourful packs lying flat. Height at 1.3 m needs ~11 cm to seed (K_SEED x sigma); a
    # chip bag reads ~4 cm. Grey lino is sat ~8; the bags are 100+. Seed saturated pixels that
    # still sit on the floor, using this capture's own blank-floor sat so an orange floor does
    # not seed itself.
    # HSV saturation is (max - min) / max, so it is ill-conditioned as a pixel gets dark: at V = 60 an eleven-level
    # channel imbalance already reads S = 50, which JPEG chroma noise and purple fringing supply for free. That is
    # what a dark door's foot is made of — measured, the four false objects in cap_0019/0020 seeded 100 % on colour
    # with S 57-65 at V 68-76, i.e. an ABSOLUTE chroma of 15-19 grey levels, against 56-78 for the real packets and
    # p90 10.4 / p99 13.9 on the blank floor. So ask for the chroma too, in grey levels, where noise does not scale.
    sat = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[:, :, 1].astype(np.float32)
    chroma = image.max(axis=2).astype(np.float32) - image.min(axis=2).astype(np.float32)
    flat = near & (np.abs(hfit) < floor_band) & ~textured & ~tall
    flat_sat, flat_chroma = sat[flat], chroma[flat]
    enough = len(flat_sat) > 800
    sat_floor = float(np.percentile(flat_sat, 90)) if enough else 0.0
    chroma_floor = float(np.percentile(flat_chroma, 90)) if enough else 0.0
    sat_thr = max(float(sat_min), sat_floor + float(sat_margin))
    chroma_thr = max(float(chroma_min), chroma_floor + float(chroma_margin))
    pack = near & (sat >= sat_thr) & (chroma >= chroma_thr) & (h > k_pack * sigma) & (h < pack_h_max)

    # 3c · the PALE BODY of a pack. A crisp packet is not uniformly coloured: the printed end is, the
    # foil/white middle is not, and the trim below — which exists to strip SGBM's grey halo — cannot tell
    # that middle from the halo by colour, so it used to cut the packet in half (measured: the mask held
    # 24-42 % of its own bounding box, against 76 % for the can and 60 % for the cup). What separates the
    # body from the halo is not colour but BRIGHTNESS: the halo is floor-coloured because it IS floor, and
    # a white wrapper is well above the floor's own bright tail. Capture-relative, like every threshold
    # here, so a white floor does not qualify itself. This only ever KEEPS pixels the coloured core has
    # already reached (it is absent from `seed` and `cand`), so it cannot invent or extend a component.
    grey_floor = float(np.percentile(grey[flat], 90)) if enough else 255.0
    bright = near & (grey > grey_floor + float(bright_margin)) & (h > k_pack * sigma) & (h < pack_h_max)

    # 4 · evidence per pixel, then connected components in the image, never across an occlusion edge
    free = near & (h > k_free * sigma)
    seed = (near & textured & (h > k_seed * sigma)) | free | pack
    cand = (near & textured & (h > k_body * sigma)) | free | pack | (bright if bright_in_cand else False)
    cand = (cv2.morphologyEx(cand.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)) > 0) & valid
    rho = np.where(valid, np.linalg.norm(W - C, axis=2), np.nan)
    jump = np.zeros(valid.shape, bool)
    dv, du = np.abs(np.diff(rho, axis=0)) > discont_m, np.abs(np.diff(rho, axis=1)) > discont_m      # NaN: False, no cut
    jump[:-1] |= dv; jump[1:] |= dv; jump[:, :-1] |= du; jump[:, 1:] |= du
    cand &= ~jump
    dead = ~valid.any(axis=0)                           # columns the matcher can never fill (its left band)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cand.astype(np.uint8), connectivity=8)

    # 5 · measure each component from its own 3-D points, and put the physical questions to it
    out = []
    for i in range(1, n):
        x0, y0, w, hh, area = (int(v) for v in stats[i])
        sl = (slice(y0, y0 + hh), slice(x0, x0 + w))
        m = lab[sl] == i
        P, hv = W[sl][m], h[sl][m]
        cx, cy = float(np.median(P[:, 0])), float(np.median(P[:, 1]))
        top = float(np.percentile(hv, 95))
        cam_range = float(np.linalg.norm([cx - C[0], cy - C[1], top / 2 - C[2]]))
        px_m = cam_range / focal_px                     # what one pixel spans, facing the camera
        nseed = int((m & seed[sl]).sum())
        if nseed * px_m ** 2 < min_seed_m2:
            continue
        # Colourful pack: the component grew into SGBM's grey halo. Grow the saturated
        # core one pixel at a time while the mask stays mostly pack-coloured; a can
        # seeds on height, not pack, so it is left alone.
        npack = int((m & pack[sl]).sum())
        if npack >= pack_seed_frac * nseed:
            seed_pack = (pack[sl] & m).astype(np.uint8)
            body = (pack[sl] | bright[sl]) & m          # the packet: printed end AND pale middle, never the grey halo
            best = seed_pack.astype(bool)
            for rad in range(1, block_r + 1):
                grown = m & (cv2.dilate(seed_pack, np.ones((2 * rad + 1, 2 * rad + 1), np.uint8)) > 0)
                if int((grown & body).sum()) < pack_core_frac * int(grown.sum()):
                    break
                best = grown
            if best.any() and int(best.sum()) < int(m.sum()):
                lab[sl][(lab[sl] == i) & ~best] = 0
                m = best
                area = int(m.sum())
                P, hv = W[sl][m], h[sl][m]
                cx, cy = float(np.median(P[:, 0])), float(np.median(P[:, 1]))
                top = float(np.percentile(hv, 95))
                cam_range = float(np.linalg.norm([cx - C[0], cy - C[1], top / 2 - C[2]]))
                px_m = cam_range / focal_px
                nseed = int((m & seed[sl]).sum())
                if nseed * px_m ** 2 < min_seed_m2:
                    continue
        kth = min(max(len(hv) // 50, 3), 50, len(hv) - 1)
        base = float(np.partition(hv, kth)[kth])        # its lowest pixels, a few outliers aside
        sg = float(np.median(sigma[sl][m]))
        rr = np.hypot(P[:, 0], P[:, 1])
        ray = np.array([cx - C[0], cy - C[1]]) / max(float(np.hypot(cx - C[0], cy - C[1])), 1e-6)
        across, along = (P[:, :2] - [cx, cy]) @ np.array([-ray[1], ray[0]]), (P[:, :2] - [cx, cy]) @ ray
        width_raw = float(np.percentile(across, 95) - np.percentile(across, 5))
        depth_m = float(np.percentile(along, 95) - np.percentile(along, 5))

        # under its base and over its top, column by column, clear of the matcher's halo. Under: the floor must be
        # visible. Over: a thing standing free shows floor (or wall) well BEHIND it; skirting, a door frame's foot, a
        # sign show the same wall at the same range.
        under, over = [], []
        for c in np.where(m.any(axis=0))[0]:
            hit = np.where(m[:, c])[0]
            lo, hi = y0 + int(hit.max()) + block_r + 1, max(y0 + int(hit.min()) - block_r - 1, 0)
            under.append(hfit[lo: lo + 6, x0 + c][valid[lo: lo + 6, x0 + c]])
            over.append(r[max(hi - 6, 0): hi, x0 + c][valid[max(hi - 6, 0): hi, x0 + c]])
        under, over = np.concatenate(under), np.concatenate(over)
        under_floor = float((np.abs(under) < 2 * sg).mean()) if len(under) else 0.0
        gap = float(np.median(over) - np.median(rr)) if len(over) else 9.99
        shadow = top * float(np.hypot(cx - C[0], cy - C[1])) / max(float(C[2]) - top / 2, 0.1)      # height / tan(elevation)

        big = width_raw > large_m or top > large_m
        tests = {
            "area": area * px_m ** 2 >= min_area_m2,
            "wide_enough": width_raw >= min_width_m,
            "base_on_floor": base <= (k_free if big else k_seed) * sg,      # a large thing is mostly texture-free evidence
            "floor_under": under_floor >= under_floor_min,
            "free_standing": True if top < 0.08 else gap >= min(gap_min_m, 0.5 * shadow),
            "not_border": not (x0 <= 1 or y0 <= 1 or x0 + w >= cols - 1 or y0 + hh >= rows - 1 or dead[max(x0 - 2, 0): x0 + w + 2].any()),
            "not_range_cut": float(np.percentile(rr, 98)) < max_range - range_edge_m,
        }
        if width_raw > struct_w_m or top > struct_h_m or (big and not tests["not_range_cut"]):
            kind = "structure"
        elif big and tests["base_on_floor"]:
            kind = "large"
        else:
            kind = "object" if all(tests.values()) else "rejected"
        vs, us = np.where(m)
        out.append({"kind": kind, "centre": [round(cx, 3), round(cy, 3), round(top / 2, 3)], "height_m": round(top, 3),
                    "height_median_m": round(float(np.median(hv)), 3), "width_m": round(max(width_raw - 2 * block_r * px_m, px_m), 3), "width_raw_m": round(width_raw, 3),
                    "depth_m": round(depth_m, 3), "base_m": round(base, 3), "pixels": area, "seed_pixels": nseed,
                    "range_m": round(float(np.hypot(cx, cy)), 3), "cam_range_m": round(cam_range, 3),
                    "area_cm2": round(area * px_m ** 2 * 1e4, 1), "sigma_m": round(sg, 4), "under_floor": round(under_floor, 2),
                    "gap_m": round(min(gap, 9.99), 3), "host_dist_m": None, "bbox": [x0, y0, w, hh],
                    "pixel_centre": [round(float(us.mean()) + x0, 1), round(float(vs.mean()) + y0, 1)],
                    "tests": {k_: bool(v_) for k_, v_ in tests.items()}, "_label": i})

    # 6 · does it stand alone? Within PART_OF_M of the building a thing IS the building (skirting under a glass wall,
    # a door frame's foot); within it of a large thing a small one is that thing's extremity (a foot, a chair leg).
    # A small thing is judged by its centre, a large one by its outline.
    def attach(guests, hosts, by_outline):
        for o in guests:
            x0, y0, w, hh = o["bbox"]
            g = int(np.ceil(3 * part_of_m / (o["cam_range_m"] / focal_px)))      # a generous window: the answer must not depend on it
            sl = (slice(max(y0 - g, 0), y0 + hh + g), slice(max(x0 - g, 0), x0 + w + g))
            mine = W[sl][lab[sl] == o["_label"]][:, :2] if by_outline else np.array([o["centre"][:2]], np.float32)
            best = float("inf")
            for b in hosts:
                theirs = W[sl][lab[sl] == b["_label"]]    # in plan, but AT ITS HEIGHT: glass that reads 30 cm near, 70 cm up,
                theirs = theirs[theirs[:, 2] < o["height_m"] + part_of_m][:, :2]          # is not standing next to a can
                if len(theirs) >= HOST_PX:                 # the HOST_PX-th nearest pixel: a few stray ones are not a wall
                    best = min(best, float(np.partition(_nearest(theirs, mine), HOST_PX - 1)[HOST_PX - 1]))
            o["host_dist_m"] = None if not np.isfinite(best) else round(best, 3)
            o["tests"]["stands_alone"] = not best < part_of_m
    attach([o for o in out if o["kind"] == "large"], [o for o in out if o["kind"] == "structure"], True)
    for o in out:
        if o["kind"] == "large" and not o["tests"]["stands_alone"]:
            o["kind"] = "structure"
    attach([o for o in out if o["kind"] in ("object", "rejected")], [o for o in out if o["kind"] in ("large", "structure")], False)
    for o in out:
        o["tests"].setdefault("stands_alone", True)
        if o["kind"] == "object" and not o["tests"]["stands_alone"]:
            o["kind"] = "rejected"
    # FLAGGED, not rejected: an object beside something the image edge cuts off may be that thing's foot (a chair's, in
    # cap_0014) or a cup someone left by their bag (cap_1001). One capture cannot tell; the caller is told instead.
    cut = [o for o in out if o["kind"] == "rejected" and not o["tests"]["not_border"]]
    for o in out:
        o["cut_by_border"] = not o["tests"]["not_border"]
        if o["kind"] == "object":
            keep = (o["tests"]["stands_alone"], o["host_dist_m"])
            attach([o], cut, False)
            o["beside_cut"], o["beside_cut_m"] = not o["tests"]["stands_alone"], o["host_dist_m"]
            o["tests"]["stands_alone"], o["host_dist_m"] = keep

    if debug is not None:
        debug.update(h=h, hfit=hfit, sigma=sigma, textured=textured, cand=cand, seed=seed, labels=lab,
                     floor_coef=coef, floor_s=floor_s, noise=bins, pack=pack, sat_thr=sat_thr, chroma_thr=chroma_thr,
                     bright=bright, grey_floor=grey_floor, flat=flat)
    out = sorted((o for o in out if keep_rejected or o["kind"] in ("object", "large")), key=lambda o: o["range_m"])
    for o in out:
        o["mask"] = lab == o.pop("_label")
    return out


# ───────────────── the second stage: is it still there in another capture? ─────────────────
def register(W_a, valid_a, img_a, W_b, valid_b, img_b):
    """Planar motion (x, y, yaw) taking capture A's robot frame into capture B's, from the building itself. The pose is
    not tracked and a balancing robot does not stand still (cap_0012 -> cap_0013: 24 cm and 2.6 deg), so two captures
    are never "the same pose" until this says so. ORB matches between the two left images, each lifted to 3-D by its own
    depth; then a robust fit in which a match's error ACROSS its line of sight counts 4x its error ALONG it — the
    bearing of a pixel is exact, its range is the stereo noise. Returns (params, inliers, matches) or None."""
    from scipy.optimize import least_squares              # only --confirm-with needs scipy

    orb = cv2.ORB_create(4000, fastThreshold=8)
    ka, da = orb.detectAndCompute(cv2.cvtColor(img_a, cv2.COLOR_BGR2GRAY), None)
    kb, db = orb.detectAndCompute(cv2.cvtColor(img_b, cv2.COLOR_BGR2GRAY), None)
    if da is None or db is None:
        return None
    pa, pb = [], []
    for mt in cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db):
        (ua, va), (ub, vb) = (int(round(c)) for c in ka[mt.queryIdx].pt), (int(round(c)) for c in kb[mt.trainIdx].pt)
        if valid_a[va, ua] and valid_b[vb, ub]:
            pa.append(W_a[va, ua, :2]); pb.append(W_b[vb, ub, :2])
    if len(pa) < 30:
        return None
    pa, pb = np.array(pa, np.float64), np.array(pb, np.float64)
    ub_ = pb / np.maximum(np.linalg.norm(pb, axis=1), 1e-6)[:, None]
    nb_ = np.c_[-ub_[:, 1], ub_[:, 0]]

    def resid(p, sel=slice(None)):
        c, s = np.cos(p[2]), np.sin(p[2])
        d = pa[sel] @ np.array([[c, s], [-s, c]]) + p[:2] - pb[sel]
        return np.r_[(d * nb_[sel]).sum(1) / 0.02, (d * ub_[sel]).sum(1) / 0.08]

    rng, best = np.random.default_rng(0), None
    for _ in range(800):
        i, j = rng.choice(len(pa), 2, replace=False)
        if np.linalg.norm(pa[i] - pa[j]) < 0.4:
            continue
        ea, eb = pa[j] - pa[i], pb[j] - pb[i]
        yaw = np.arctan2(eb[1], eb[0]) - np.arctan2(ea[1], ea[0])
        c, s = np.cos(yaw), np.sin(yaw)
        p = np.r_[(pb[i] + pb[j]) / 2 - ((pa[i] + pa[j]) / 2) @ np.array([[c, s], [-s, c]]), yaw]
        inl = (np.abs(resid(p).reshape(2, -1)) < 2.0).all(axis=0)
        if best is None or inl.sum() > best[0].sum():
            best = (inl, p)
    if best is None or best[0].sum() < 30:
        return None
    sol = least_squares(lambda p: resid(p, best[0]), best[1], loss="soft_l1")
    return sol.x, int(best[0].sum()), len(pa)


def confirm(inst: list[dict], other: list[dict], motion, gate: float = CONFIRM_M) -> None:
    """Mark each instance `confirmed` if the other capture holds one of the same kind within `gate` of it, the other's
    centres first carried into this capture's frame by `motion` (register(other -> this); None = assume no motion)."""
    p = np.zeros(3) if motion is None else motion
    c, s = np.cos(p[2]), np.sin(p[2])
    for o in inst:
        d = [float(np.hypot(*(np.array(q["centre"][:2]) @ np.array([[c, s], [-s, c]]) + p[:2] - o["centre"][:2])))
             for q in other if q["kind"] == o["kind"]]
        o["confirm_dist_m"] = round(min(d), 3) if d else None
        o["confirmed"] = bool(d) and min(d) <= gate


# ───────────────── the tool ─────────────────
def load(rec_dir: Path):
    """A recording -> (xyz_world, valid, left image, cam_origin, focal_px), through the pipeline's own depth and mount."""
    sys.path.insert(0, str(ROOT / "perception")); sys.path.insert(0, str(ROOT))
    import depth, fuse, pipeline          # noqa: E401 -- perception's modules; find_floor_objects itself needs none of them
    rec = pipeline.load_recording(rec_dir)
    cam = sorted(rec.frames)[0]
    frames = {c: cv2.imread(str(f)) for c, f in rec.frames.items()}
    rigs = {c: pipeline._rig(str(f)) for c, f in rec.calib.items()}
    out, _ = depth.depth_capture(frames, rigs, rec.skew_ms, rec.tilt_rate_max)
    xyz, valid, left = out[cam]
    W = np.full(xyz.shape, np.nan, np.float32)
    W[valid] = fuse.rect_to_world(xyz[valid], rec.mounts[cam])
    origin = fuse.rect_to_world(np.zeros((1, 3)), rec.mounts[cam])[0]
    return W, valid, left, tuple(float(v) for v in origin), float(rigs[cam].Q[2, 3])      # Q[2,3]: focal px at DOWNSAMPLE


def line(o: dict) -> str:
    c, t = o["centre"], " ".join(f"{k}{'+' if v else '-'}" for k, v in o["tests"].items())
    note = "  [cut by the image edge: size is a lower bound]" if o.get("cut_by_border") and o["kind"] == "large" else \
        f"  [beside something the image edge cuts off, {o['beside_cut_m'] * 100:.0f} cm away: may be part of it]" if o.get("beside_cut") else ""
    seen = "" if "confirmed" not in o else ("  CONFIRMED" if o["confirmed"] else "  unconfirmed") + \
        ("" if o["confirm_dist_m"] is None else f" ({o['confirm_dist_m'] * 100:.0f} cm)")
    return (f"  #{o.get('id', '-')} {o['kind']:9s} centre ({c[0]:+.2f}, {c[1]:+.2f}, {c[2]:.2f}) m   height {o['height_m'] * 100:5.1f} cm   "
            f"width {o['width_m'] * 100:5.1f} cm (raw {o['width_raw_m'] * 100:.1f})   {o['pixels']:6d} px   range {o['range_m']:.2f} m{seen}{note}\n"
            f"       {t}")


COLOURS = {"object": (60, 255, 60), "large": (0, 165, 255), "rejected": (60, 60, 255), "structure": (170, 170, 170)}


def overlay(image: np.ndarray, inst: list[dict], scale: int = 2) -> np.ndarray:
    """The rectified left image, `scale`x, each instance outlined and labelled. Objects and large things carry their
    number and measurements; rejected and structure components (only present with --all) are outlined thin."""
    vis = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    for o in inst:
        main_ = o["kind"] in ("object", "large")
        cs, _ = cv2.findContours(cv2.resize(o["mask"].astype(np.uint8), None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST),
                                 cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(vis, cs, -1, COLOURS[o["kind"]], 2 if main_ else 1)
        if not main_:
            continue
        x0, y0, w, hh = (v * scale for v in o["bbox"])
        txt = f"#{o.get('id', '-')} {o['kind']}  h {o['height_m'] * 100:.0f} cm  w {o['width_m'] * 100:.0f} cm  @ ({o['centre'][0]:.2f}, {o['centre'][1]:.2f}) m"
        txt += "" if "confirmed" not in o else ("  CONFIRMED" if o["confirmed"] else "  unconfirmed")
        txt += "  (edge-cut)" if o.get("cut_by_border") and o["kind"] == "large" else "  (beside edge-cut)" if o.get("beside_cut") else ""
        tw = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0][0]
        tx, ty = int(max(min(x0 + w + 14, vis.shape[1] - tw - 6), 4)), int(max(y0 - 12, 18))
        if tx == x0 + w + 14:                           # a leader only where the label sits beside its thing
            cv2.line(vis, (x0 + w, y0), (tx - 2, ty - 5), COLOURS[o["kind"]], 1, cv2.LINE_AA)
        cv2.putText(vis, txt, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(vis, txt, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOURS[o["kind"]], 1, cv2.LINE_AA)
    return vis


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording", type=Path, help="a recording folder (capture.json, cam0.jpg, cam0.yaml)")
    ap.add_argument("--out", type=Path, help="write the overlay JPEG here: the rectified left image, 2x, each instance outlined and labelled")
    ap.add_argument("--json", action="store_true", help="print the instances as one JSON document too")
    ap.add_argument("--confirm-with", type=Path, metavar="OTHER", help="a second recording of the same scene: register it onto "
                    "this one on the building, and mark each instance confirmed if it is there too")
    ap.add_argument("--all", action="store_true", help="list and draw rejected and structure components as well, with the tests they failed")
    a = ap.parse_args()

    W, valid, left, origin, focal = load(a.recording.expanduser())
    dbg: dict = {}
    every = find_floor_objects(W, valid, left, cam_origin=origin, focal_px=focal, keep_rejected=True, debug=dbg)
    inst = [o for o in every if o["kind"] in ("object", "large")]
    for i, o in enumerate(inst, 1):
        o["id"] = i                                     # nearest first; the overlay carries the same numbers
    print(f"\n  FLOOR OBJECTS  {a.recording.name}   floor noise {' '.join(f'{r_:.1f}m:{s_ * 100:.1f}' for r_, s_ in dbg['noise'][::2])} cm"
          f"   (fit residual {dbg['floor_s'] * 100:.1f} cm)")

    motion = None
    if a.confirm_with:
        W2, valid2, left2, origin2, focal2 = load(a.confirm_with.expanduser())
        other = find_floor_objects(W2, valid2, left2, cam_origin=origin2, focal_px=focal2)
        reg = register(W2, valid2, left2, W, valid, left)
        if reg is None:
            print(f"  could not register {a.confirm_with.name} onto this capture (too few matches on the building): comparing as if the robot had not moved")
        else:
            motion, n_in, n_all = reg
            print(f"  {a.confirm_with.name} -> this capture: the robot moved ({motion[0]:+.2f}, {motion[1]:+.2f}) m, yaw {np.degrees(motion[2]):+.1f} deg"
                  f"   ({n_in}/{n_all} matches agree)")
        confirm(inst, other, motion)

    for o in inst:
        print(line(o))
    if not inst:
        print("  nothing standing on the floor within range")
    rej = [o for o in every if o["kind"] == "rejected"]
    why: dict[str, int] = {}
    for o in rej:
        for k_, v_ in o["tests"].items():
            if not v_:
                why[k_] = why.get(k_, 0) + 1
    print(f"  not reported: {len(rej)} rejected ({', '.join(f'{k_} {v_}' for k_, v_ in sorted(why.items(), key=lambda kv: -kv[1])) or 'none'}) · "
          f"{sum(o['kind'] == 'structure' for o in every)} pieces of the building")
    if a.all:
        for o in every:
            if o["kind"] in ("rejected", "structure") and o["pixels"] >= 30:
                print(line(o))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(a.out), overlay(left, every if a.all else inst), [cv2.IMWRITE_JPEG_QUALITY, 92])
        print(f"  overlay -> {a.out}")
    if a.json:
        print(json.dumps({"recording": a.recording.name, "floor_noise": [[round(r_, 2), round(s_, 4)] for r_, s_ in dbg["noise"]],
                          "motion_from_other": None if motion is None else [round(float(v), 4) for v in motion],
                          "instances": [{k_: v_ for k_, v_ in o.items() if k_ != "mask"} for o in (every if a.all else inst)]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
