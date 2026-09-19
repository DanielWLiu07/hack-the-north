"""Difference segmentation: what APPEARED, or went AWAY, between two captures of one room.

segment.py asks "what is in this capture?" through an image model, cluster.py through
geometry. This asks "what is different from the baseline?", which needs neither a model
nor zones: whatever now stands in space the baseline saw to be empty is a new object;
whatever the baseline had that the current capture now sees THROUGH has gone.

The evidence is free space along the ray, per pixel of the current view:

    APPEARED  current depth < min(baseline depth over a WINDOW x WINDOW neighbourhood) - tau(z)
    GONE      the same with the captures swapped: a baseline surface nearer than the
              current one there, so the current capture looked through where it was

A pixel the baseline has no depth around carries no evidence and is never a change. When
the robot moved between captures the baseline is rendered into the current view first
(a z-buffer through fuse.rect_to_world), so the test is always in one camera.

Measured on the real pair cap_0004 / cap_0005 (hallway, untouched, 5 s apart, one frame
each, the head stereo at 960x720, f 246 px): between captures, per-pixel depth differs by
2.4 cm (median) at 1.4-2.0 m and 7 cm at 2.0-2.6 m, and the error is a smooth local warp
(adjacent pixels correlate at 0.94, gone by ~16 px), not speckle. Its tail -- p99.9 ~1 m
-- sits in blobs at the rim of the view, where the fisheye rectification stretches
texture and SGBM mismatches; the stretch is radial and so is the noise. Every untouched
blob over 40 cm^2 lies more than 41 deg off the optical axis or more than RANGE_M out.
Inside both, the largest untouched group covers 16 cm^2; a 20 cm box on the floor 1 m
ahead (19-22 deg off axis), ray-cast into cap_0005, covers ~450. Hence, in order:

  1. tau = TAU_M + TAU_Z2 * z^2 (z = range along the optical axis): stereo depth noise
     grows as z^2 / (f B); TAU_Z2 is half a pixel of disparity on that rig (f B 15.8 px m)
  2. a 3x3 opening of the change mask: single-pixel speckle
  3. crops: less than FOV_DEG off the optical axis; within RANGE_M of the robot and outside
     SELF_M of it (its own arm), horizontally; more than MIN_HEIGHT above the floor
  4. blobs: connected in the image, then split in F_world by cluster.py's DBSCAN, eps the
     larger of EPS and EPS_PX pixel footprints at the blob's range (so a coarser camera
     does not shatter a box into pieces each under the area floor)
  5. each blob GROWS into image-connected pixels changed by TAU_GROW (against the other
     capture's window MEDIAN, not its minimum) within GROW_M of its footprint. Seen from
     above, a box's front face changes by nothing where it meets the floor and by its full
     height at the top; without this the instance is its lid, 1 cm tall, and associate's
     extent gate would call a known box a new one.
  6. blobs whose grown regions meet are one object -- a lid the noise split in three -- and
     an object whose blobs cover less than MIN_AREA_M2 in all, measured facing the camera
     (each pixel covers (z / f)^2 m^2), is noise. Only blob pixels count toward that area:
     growth shapes an object, it never makes one.

Limits, measured rather than hoped:
  - the camera looks down (1.59 m high, 38 deg: the head as measured from the floor), so an
    object's HEIGHT shows as depth change of height / sin(elevation): a 20 cm box at 1 m is
    24 cm along the ray. Floor objects under ~10 cm tall past ~1.5 m sit in the noise.
  - MIN_AREA_M2 is a 10 x 10 cm face: a mug on the floor is below it. With the noise
    above (tests/test_difference.py's renderer is calibrated to it), over 30 noise draws a
    20 cm box is found 30/30 at 1 m (moved: 29/30) and 22/30 at 1.5 m; a 15 cm one at
    1 m, 0/30. Every
    lower tau tried took the real pair's largest untouched blob from 32 to 190+ cm^2: the
    sensitivity left is in the SENSOR. A median over several frames per capture (capture
    `frames: N`) is what would let TAU and the area floor come down.
  - outside FOV_DEG nothing is reported: the robot turns to look. Straight ahead that cone
    meets the floor 0.4 m out; RANGE_M ends it at 1.7 m.
  - one view, no model: a change is "unknown" until segment.py / describe.py name it.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from scipy.spatial import cKDTree

from cluster import Instance, dbscan

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))    # obs.py lives at the repo root
import obs  # noqa: E402
from fuse import Mount, odom_to_world, rect_to_world  # noqa: E402  (the one place frames change)

TAU_M = 0.05             # m, the depth-change floor
TAU_Z2 = 0.03            # m per m^2 of range: 0.5 px of disparity at f B = 15.8 px m
WINDOW = 5               # px: the baseline's nearest surface around a pixel, so an edge that
                         # shifts a pixel between captures is not a change
OPEN = 3                 # px, the change mask's opening
FOV_DEG = 38.0           # trusted cone around the optical axis: the rectified fisheye's rim is noise
RANGE_M = 1.7            # horizontal, from the robot. Past it the real pair's noise groups reach 46 cm^2
SELF_M = 0.35            # horizontal, from the robot: its own body and arm
MIN_HEIGHT = 0.03        # m above the floor (z = 0): the floor's own stereo ripple
EPS, MIN_PTS = 0.03, 10  # DBSCAN, F_world
EPS_PX = 3               # ...eps is at least this many pixel footprints (z / f) at the blob's range
MIN_AREA_M2 = 0.01       # a 10 x 10 cm face toward the camera
TAU_GROW = 0.02          # m: a blob found above grows into connected pixels changed this much...
GROW_M = 0.03            # ...lying within this of its footprint, horizontally: the face below a lid's
                         # edge, not the floor-noise ring around it (5 cm made a 20 cm box 25 cm wide)


@dataclass
class View:
    """One camera's capture, as depth.depth_capture returns it plus where it stood."""
    xyz: np.ndarray                          # (H,W,3) F_rect, NaN where invalid
    valid: np.ndarray                        # (H,W) bool
    mount: Mount
    pose: tuple[float, float, float] = (0.0, 0.0, 0.0)   # F_world (x, y, yaw rad): odom_to_world
    image: np.ndarray | None = None          # left_rect BGR, for colour


@dataclass
class Change:
    appeared: list[Instance] = field(default_factory=list)   # the CURRENT capture's points, F_world
    gone: list[Instance] = field(default_factory=list)       # the BASELINE's points, F_world


def difference(baseline: View, current: View, camera: str = "cam0") -> Change:
    """Two captures of one camera -> what appeared and what went away. Instances are
    source "difference", label "unknown", with their pixel mask on THAT capture's image
    (current for appeared, baseline for gone), ready for describe.py and merge.py."""
    for v in (baseline, current):
        if v.xyz.shape[:2] != v.valid.shape:
            raise ValueError(f"xyz {v.xyz.shape} and valid {v.valid.shape} are not aligned")
    with obs.span("perception.difference", f"difference {camera}", camera=camera) as sp:
        out = Change(_changed(current, baseline, camera), _changed(baseline, current, camera))
        if sp is not None:
            sp.set_data("appeared", len(out.appeared))
            sp.set_data("gone", len(out.gone))
    return out


def _changed(this: View, other: View, camera: str) -> list[Instance]:
    """Blobs of `this` standing where `other` saw free space."""
    f, cx, cy = intrinsics(this.xyz, this.valid)
    z = this.xyz[..., 2]
    ref = _depth_in(other, this, (f, cx, cy))
    far = np.where(np.isfinite(ref), ref, np.inf).astype(np.float32)
    near = cv2.erode(far, np.ones((WINDOW, WINDOW), np.uint8), borderType=cv2.BORDER_REPLICATE)
    h, w = z.shape
    v, u = np.mgrid[:h, :w]
    cone = np.hypot(u - cx, v - cy) / f < np.tan(np.radians(FOV_DEG))
    with np.errstate(invalid="ignore"):
        # found: nearer than ANYTHING the other saw around the pixel, by more than noise
        strong = this.valid & cone & np.isfinite(near) & (z < near - (TAU_M + TAU_Z2 * z ** 2))
        # grown: nearer than what it TYPICALLY saw there. The window minimum is ~4 cm low on
        # a noisy floor (the least of 25 samples), which would stop a box's face 7 cm up
        typical = cv2.medianBlur(far, WINDOW)
        weak = this.valid & cone & np.isfinite(typical) & (z < typical - TAU_GROW)
    strong = cv2.morphologyEx(strong.astype(np.uint8), cv2.MORPH_OPEN, np.ones((OPEN, OPEN), np.uint8)).astype(bool)

    rows, cols, pts = _in_reach(this, *np.nonzero(strong))
    area = (z[rows, cols] / f) ** 2
    _, blob = cv2.connectedComponents(strong.astype(np.uint8), connectivity=8)
    blob = blob[rows, cols]
    _, comp = cv2.connectedComponents(weak.astype(np.uint8), connectivity=8)
    wr, wc, wp = _in_reach(this, *np.nonzero(weak))
    wcomp = comp[wr, wc]

    pieces = []                                           # (seed area m^2, flat pixel ids)
    for b in np.unique(blob):
        idx = np.flatnonzero(blob == b)
        eps = max(EPS, EPS_PX * float(np.median(z[rows[idx], cols[idx]])) / f)
        labels = dbscan(pts[idx], eps, MIN_PTS)
        for k in np.unique(labels[labels >= 0]):
            s = idx[labels == k]
            ids = np.unique(comp[rows[s], cols[s]])
            g = np.flatnonzero(np.isin(wcomp, ids[ids > 0]))      # 0 is "not changed", never a region
            g = g[cKDTree(pts[s, :2]).query(wp[g, :2], distance_upper_bound=GROW_M)[0] <= GROW_M]
            pieces.append((float(area[s].sum()), np.union1d(rows[s] * w + cols[s], wr[g] * w + wc[g])))

    out = []
    for group in _touching([pix for _, pix in pieces], h * w):
        if sum(pieces[i][0] for i in group) < MIN_AREA_M2:
            continue
        m = np.zeros(h * w, bool)
        for i in group:
            m[pieces[i][1]] = True
        m = m.reshape(h, w)
        out.append(Instance(points=rect_to_world(this.xyz[m], this.mount, this.pose).astype(np.float64),
                            source="difference", camera=camera, mask=m,
                            color=_colour(this.image, m) if this.image is not None else None))
    return sorted(out, key=lambda i: tuple(np.round(i.centroid, 3)))


def _touching(sets: list[np.ndarray], n: int) -> list[list[int]]:
    """Group pixel sets that share a pixel: the pieces of one lid, split where the noise dipped
    under tau, grow into the same changed region and meet there."""
    parent = list(range(len(sets)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner = np.full(n, -1)
    for i, pix in enumerate(sets):
        for j in np.unique(owner[pix]):
            if j >= 0:
                parent[root(j)] = root(i)
        owner[pix] = i
    groups: dict[int, list[int]] = {}
    for i in range(len(sets)):
        groups.setdefault(root(i), []).append(i)
    return list(groups.values())


def _in_reach(view: View, rows: np.ndarray, cols: np.ndarray):
    """Pixels whose points are within RANGE_M of the robot, clear of its body, off the floor
    -> (rows, cols, F_world points)."""
    pts = rect_to_world(view.xyz[rows, cols], view.mount, view.pose)
    horiz = np.hypot(pts[:, 0] - view.pose[0], pts[:, 1] - view.pose[1])
    keep = (horiz < RANGE_M) & (horiz > SELF_M) & (pts[:, 2] > MIN_HEIGHT)
    return rows[keep], cols[keep], pts[keep]


def intrinsics(xyz: np.ndarray, valid: np.ndarray) -> tuple[float, float, float]:
    """(f, cx, cy) of a rectified F_rect array, fitted from its own points: x / z = (u - cx) / f.
    Exact for depth.py's reprojectImageTo3D, and RealSense frames carry none on disk."""
    rows, cols = np.nonzero(valid)
    if len(rows) < 100:
        raise ValueError(f"{len(rows)} valid pixels: too few to fit intrinsics")
    p = xyz[rows, cols]
    a, b = np.polyfit(p[:, 0] / p[:, 2], cols, 1)      # u = f (x/z) + cx
    c, d = np.polyfit(p[:, 1] / p[:, 2], rows, 1)      # v = f (y/z) + cy
    return float((a + c) / 2), float(b), float(d)


def _depth_in(other: View, this: View, k: tuple[float, float, float]) -> np.ndarray:
    """`other`'s depth as `this` camera would see it, (H,W), NaN where it has none."""
    shape = this.valid.shape
    if other.mount == this.mount and np.allclose(other.pose, this.pose) and other.valid.shape == shape:
        return np.where(other.valid, other.xyz[..., 2], np.nan)
    rot, t = _world_from_rect(this.mount, this.pose)
    world = rect_to_world(other.xyz[other.valid], other.mount, other.pose)
    p = (world - t) @ rot                               # F_world -> this camera's F_rect
    f, cx, cy = k
    front = p[:, 2] > 1e-3
    p = p[front]
    u = np.round(f * p[:, 0] / p[:, 2] + cx).astype(int)
    v = np.round(f * p[:, 1] / p[:, 2] + cy).astype(int)
    inside = (u >= 0) & (u < shape[1]) & (v >= 0) & (v < shape[0])
    out = np.full(shape[0] * shape[1], np.inf)
    np.minimum.at(out, v[inside] * shape[1] + u[inside], p[inside, 2])   # z-buffer: nearest wins
    out[np.isinf(out)] = np.nan
    return out.reshape(shape)


def _world_from_rect(mount: Mount, pose) -> tuple[np.ndarray, np.ndarray]:
    """(R, t) with world = R @ rect + t, read off rect_to_world rather than restated."""
    t = rect_to_world(np.zeros((1, 3)), mount, pose)[0]
    rot = rect_to_world(np.eye(3), mount, pose) - t     # row i: the image of rect axis i
    return rot.T, t


def _colour(image: np.ndarray, mask: np.ndarray) -> str:
    b, g, r = np.median(image[mask].reshape(-1, image.shape[2])[:, :3], axis=0).astype(int)
    return f"#{r:02x}{g:02x}{b:02x}"


def load_view(capture_dir, camera: str | None = None) -> tuple[str, View]:
    """A recording folder (pipeline.load_recording's layout) -> (camera, View), depth run."""
    import depth
    import pipeline

    rec = pipeline.load_recording(capture_dir)
    cam = camera or sorted(rec.frames)[0]
    rig = pipeline._rig(str(rec.calib[cam]))
    out, _ = depth.depth_capture({cam: cv2.imread(str(rec.frames[cam]))}, {cam: rig},
                                 rec.skew_ms, rec.tilt_rate_max)
    xyz, valid, left = out[cam]
    return cam, View(xyz, valid, rec.mounts[cam], odom_to_world(rec.pose), left)


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="What changed between two recordings of one room.")
    ap.add_argument("baseline")
    ap.add_argument("current")
    ap.add_argument("--camera")
    a = ap.parse_args(argv)
    cam, base = load_view(a.baseline, a.camera)
    _, cur = load_view(a.current, cam)
    change = difference(base, cur, cam)
    f = intrinsics(cur.xyz, cur.valid)[0]
    for name, insts, view in (("appeared", change.appeared, cur), ("gone", change.gone, base)):
        print(f"{name}: {len(insts)}")
        for i in insts:
            c, e, yaw = i.box()
            area = float(((view.xyz[i.mask][:, 2] / f) ** 2).sum())
            print(f"  at ({c[0]:+.2f}, {c[1]:+.2f}, {c[2]:.2f}) m  extents {e[0]:.2f} x {e[1]:.2f} x {e[2]:.2f} m"
                  f"  yaw {yaw:.0f}  {area * 1e4:.0f} cm^2  {len(i.points)} pts  {i.color}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
