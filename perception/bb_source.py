"""Bracket Bot's colour voxel map -> object candidates -> the records serialize writes.

plan/roommate/03 §4. The robot keeps a live map of 1.5 cm coloured cells in ITS world frame
(bbapps/nav `/heavy`, mirrored by roomctl.bb_nav.VoxelMirror). This turns that map into what
the stereo pipeline produces, then runs pipeline.scan_into's own chain on it:

    candidates    per zone: the cells standing on the zone's surface, the surface itself
                  dropped, 2-D connected components over occupied columns (split where the
                  colour or the height jumps), each boxed by a Fit on BB's own lattice (the
                  box() contract serialize already consumes: yaw is an axis, near-round is 0)
    fresh_blocks  the 0.5 m blocks of BB's area map seen in the last max_age_s
    visible       a ray from the eye to the object's top, through the map minus the surfaces
                  objects stand on (raycast.line_of_sight)
    scan_into_bb  one pass: candidates -> associate (a miss counts only if its block is fresh
                  AND its pose is in sight) -> settle -> serialize -> voxelize.stage ->
                  publish.stage_scan, exactly as a stereo capture commits

Frames: every cell crosses BB world -> room ONCE, in room_points(), through roomctl.frames and
the registration's T_bb<-room. Everything after that is the room frame: metres, Z up, floor
z = 0. The area map's own frame (robot-relative when the area was defined) is reached the same
way, through frames.bb_to_robot_rel.
"""
from __future__ import annotations

import base64
import logging
import math
import os
import sys
import time
import zlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent):                      # flat perception/ imports, and the repo root
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:                                 # as perception.bb_source, or flat with perception/ on sys.path
    from . import raycast, voxelize
    from .cluster import ROUND_ASPECT, Instance
    from .merge import MergedObject
except ImportError:
    import raycast
    import voxelize
    from cluster import ROUND_ASPECT, Instance
    from merge import MergedObject
import obs  # noqa: E402
from roomctl import frames  # noqa: E402

log = logging.getLogger(__name__)

CAMERA = "bb_map"         # room-observations `camera` for a map-derived row (03 §10)
MIN_CELLS = 20            # fewer 1.5 cm cells than this is speckle, not an object (keys: ~40);
                          # scaled by footprint area for coarser maps (3 cm: 5)
BAND = (0.005, 0.40)      # m above the surface: what can stand on it. band[0] only matters where
                          # the map shows no plane (then room.yaml's `surface` is trusted)
COLOUR_TOL = 60.0         # RGB distance: closer cells are one surface (0..441)
HEIGHT_TOL = 0.04         # m: neighbouring columns whose tops differ more are two objects (at
                          # least 1.5 cells, or a 3 cm map's quantized tops split one object)
PLANE_SEARCH = 2          # cells either side of room.yaml's `surface` to look for the real plane
EYE_H = 0.95              # m, room z of the head camera. MEASURE (roomctl.executor.RobotModel)
GRID_LEVELS = 8           # the pinned 8 m cube at 3.125 cm: BB's own 3 cm grid, and the costmap's
LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}   # fake/bbsim.py binds these, by construction
GHOST_M = 0.12            # m, HORIZONTALLY: a candidate this close to the spot a committed object has
                          # just left is the map's stale cells, not a thing (bbsim: ~30 s of them after
                          # a move). Horizontal because what is left behind is a patch on the surface --
                          # a fragment of a 40 cm lamp sits at the table, not at the lamp's centre
OVERSHOOT = 0.75          # a face at an angle to BB's lattice: its outermost occupied cells' centres
                          # sit this fraction of the cell's projected half-width beyond it (measured,
                          # synthetic occupancy; re-measure on the real desk)
ALIGNED_DEG = 5.0         # within this of the lattice there are too few distinct offsets to overshoot
FACE_CELLS = 4.0          # ...and a face shorter than this many cells has too few cells to reach it
THIN_CELLS = 3.0          # a footprint narrower than this many cells has no yaw to report (13 x 3 cm
                          # on a 3 cm map read 0 deg, then 140: master's real-table run)


@dataclass
class Candidate:
    zone: str
    centroid: tuple[float, float, float]      # room frame, m
    extents: tuple[float, float, float]       # along the yaw axis, across it, height; m
    yaw_axis_deg: int                         # [0, 180): the axis of extents[0] (state.py rules)
    color: str                                # "#rrggbb", median of the cells
    cells: int
    block: tuple[int, int] | None = None      # (col, row) of its 0.5 m freshness block; None without an area
    points: np.ndarray | None = field(default=None, repr=False, compare=False)   # its cells, room frame
    fit: "Fit | None" = field(default=None, repr=False, compare=False)


# ── the map, in the room frame ─────────────────────────────────────────────────────────

def room_points(mirror, reg) -> tuple[np.ndarray, np.ndarray]:
    """The mirror's cells -> ((N,3) room-frame centres in metres, (N,3) uint8 colours)."""
    pts, rgb, _, _ = _cells(source_of(mirror), reg)
    return pts, rgb


def source_of(nav_or_source):
    """The voxel source: BBNav's .mirror, or the thing itself. A source is anything with
        .res            cell size, metres (bbapps/nav /heavy 0.015, bbos mapping.voxels 0.03)
        .points()       ((N,3) world metres, (N,3) uint8 rgb) -- cell centres or corners, either
        .floor_mask()   optional: (N,) bool in points()' order, True where the map itself says
        or .floor       floor (scripts/bbos_map.MapMirror has the attribute). Given, floor cells
                        are dropped outright instead of inferred.
    and, on it or on the nav around it, optionally .map_gen (scan_into_bb refuses a mismatch)."""
    return getattr(nav_or_source, "mirror", nav_or_source)


def _floor(source) -> np.ndarray | None:
    if callable(getattr(source, "floor_mask", None)):
        return source.floor_mask()
    f = getattr(source, "floor", None)
    return None if f is None or callable(f) else np.asarray(f, bool)


def _cells(source, reg):
    """(room points, rgb, (N,3) int lattice index, lattice offset), floor cells already dropped
    when the source labels them. The lattice is the map's own: connectivity is decided there,
    where neighbouring cells really are neighbours."""
    res = float(source.res)
    if not 0.001 <= res <= 0.2:
        raise ValueError(f"source.res = {res}: want metres (BB's /heavy cells are 0.015, bbos's 0.03)")
    bb, rgb = source.points()
    bb = np.asarray(bb, float).reshape(-1, 3)
    rgb = np.asarray(rgb, np.uint8).reshape(-1, 3)
    floor = _floor(source)
    if floor is not None:
        keep = ~np.asarray(floor, bool).reshape(-1)
        bb, rgb = bb[keep], rgb[keep]
    f = bb / res
    # cell centres sit at (k + off) * res; off is 0.5 for floor-binned cells, 0 for rounded ones.
    # A circular mean finds it without assuming which (0.999 and 0.001 are the same offset).
    ang = 2 * np.pi * (f - np.floor(f))
    off = (np.arctan2(np.sin(ang).mean(axis=0), np.cos(ang).mean(axis=0)) / (2 * np.pi)) % 1.0 if len(f) else 0.0
    ijk = np.round(f - off).astype(np.int64)
    return frames.bb_to_room_array(bb, reg.T), rgb, ijk, off


def min_cells_for(res: float) -> int:
    """MIN_CELLS is for 1.5 cm cells; a coarser map covers the same footprint with fewer."""
    return max(4, round(MIN_CELLS * (0.015 / res) ** 2))


class _Lattice:
    """BB's cell lattice as seen from the room: where a column sits (through frames), and which
    way BB's +x cell axis points, in room degrees (read off frames, not re-derived)."""

    def __init__(self, T, off, res: float):
        self.T, self.res = T, res
        self.off = np.broadcast_to(np.asarray(off, float), (3,))[:2]     # where BB's cell centres sit
        o, x = frames.bb_to_room_array(np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]), T)
        self.deg = math.degrees(math.atan2(x[1] - o[1], x[0] - o[0]))

    def to_room(self, col_xy) -> np.ndarray:
        """A column position in lattice units (cell centre = index + 0.5) -> room xy, metres."""
        bb = (np.asarray(col_xy, float) - 0.5 + self.off) * self.res
        return frames.bb_to_room_array(np.array([[bb[0], bb[1], 0.0]]), self.T)[0, :2]


def _plane(pts, ijk, rgb, zone, res):
    """The zone surface as the map shows it -> (plane layer k, plane z, plane colour) or None.
    The densest layers within PLANE_SEARCH cells of room.yaml's `surface`; the HIGHEST of them,
    because a table top seen from both sides has an equally dense underside."""
    (x0, y0, _), (x1, y1, _) = zone["min"], zone["max"]
    inxy = (pts[:, 0] >= x0) & (pts[:, 0] < x1) & (pts[:, 1] >= y0) & (pts[:, 1] < y1)
    near = inxy & (np.abs(pts[:, 2] - zone["surface"]) <= PLANE_SEARCH * res + 1e-9)
    if not near.any():
        return inxy, None
    ks, counts = np.unique(ijk[near, 2], return_counts=True)
    k = int(ks[counts >= 0.5 * counts.max()].max())
    layer = near & (ijk[:, 2] == k)
    return inxy, (k, float(np.median(pts[layer, 2])), np.median(rgb[layer].astype(float), axis=0))


def _hex(c) -> str:
    return "#" + "".join(f"{int(round(v)):02x}" for v in np.clip(c, 0, 255))


# ── 1 · candidates ─────────────────────────────────────────────────────────────────────

def candidates(source, reg, zones: dict, min_cells: int | None = None, band=BAND, area=None) -> list[Candidate]:
    """The voxel map (source_of: a VoxelMirror, a MapSnapshot, a BBNav) -> one Candidate per
    object standing on a zone's surface, room frame. `area` (roomctl.bb_nav.AreaMap, or GET
    /map's JSON) fills each candidate's freshness block. min_cells: min_cells_for(res)."""
    source = source_of(source)
    res = float(source.res)
    pts, rgb, ijk, off = _cells(source, reg)
    with obs.span("perception.bb_candidates", cells=len(pts)) as sp:
        out = _candidates(pts, rgb, ijk, res, zones, min_cells if min_cells is not None else min_cells_for(res),
                          band, _Lattice(reg.T, off, res))
        if area is not None:
            out = [_with_block(c, area, reg) for c in out]
        if sp is not None:
            sp.set_data("candidates", len(out))
    return out


def _with_block(c: Candidate, area, reg) -> Candidate:
    c.block = block_of(c.centroid, area, reg)
    return c


def _candidates(pts, rgb, ijk, res, zones, min_cells, band, lattice: "_Lattice") -> list[Candidate]:
    out, claimed = [], np.zeros(len(pts), bool)
    for name, zone in (zones or {}).items():
        inxy, plane = _plane(pts, ijk, rgb, zone, res)
        if plane is None:
            keep = inxy & (pts[:, 2] >= zone["surface"] + band[0]) & (pts[:, 2] <= zone["surface"] + band[1])
        else:
            k, z, colour = plane
            flat = np.linalg.norm(rgb.astype(float) - colour, axis=1) <= COLOUR_TOL
            # the plane's own layer, and the one above if the surface straddles two: surface-coloured
            # cells there ARE the surface; anything else there is a flat object (a notebook, keys)
            surface = flat & ((ijk[:, 2] == k) | ((ijk[:, 2] == k + 1) & _straddles(inxy, ijk, flat, k)))
            keep = inxy & (ijk[:, 2] >= k) & ~surface & (pts[:, 2] <= z + band[1])
        keep &= ~claimed
        claimed |= keep
        idx = np.flatnonzero(keep)
        idx = idx[_not_lone(ijk[idx])]          # a speckle cell over an object would lift its column's top
        for comp in _components(ijk[idx], pts[idx], rgb[idx], res):
            cells = idx[comp]
            if len(cells) < min_cells:
                continue
            fit = Fit(ijk[cells], pts[cells], lattice)
            centre, ext, yaw = fit.box()
            out.append(Candidate(name, tuple(round(float(v), 4) for v in centre),
                                 tuple(round(float(v), 4) for v in ext), int(round(yaw)) % 180,
                                 _hex(np.median(rgb[cells].astype(float), axis=0)), len(cells), None, pts[cells], fit))
    return out


def _not_lone(ijk: np.ndarray) -> np.ndarray:
    """(N,) bool: the cell has at least one occupied neighbour among its 26. A lone cell is
    speckle -- and one floating over an object raises that column's top enough for the height
    rule to cut the column (and the object cells in it) loose as a second object."""
    if len(ijk) == 0:
        return np.zeros(0, bool)
    lo = ijk.min(axis=0) - 1
    span = ijk.max(axis=0) - lo + 2
    key = lambda q: ((q[:, 0] - lo[0]) * span[1] + (q[:, 1] - lo[1])) * span[2] + (q[:, 2] - lo[2])  # noqa: E731
    have = np.sort(key(ijk))
    out = np.zeros(len(ijk), bool)
    for d in np.array([(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1) if (a, b, c) != (0, 0, 0)]):
        k = key(ijk + d)
        pos = np.clip(np.searchsorted(have, k), 0, len(have) - 1)
        out |= have[pos] == k
    return out


def _straddles(inxy, ijk, flat, k) -> bool:
    """Does the surface spill into layer k + 1 (a plane within a hair of a cell boundary)?"""
    base = int((inxy & flat & (ijk[:, 2] == k)).sum())
    return base > 0 and (inxy & flat & (ijk[:, 2] == k + 1)).sum() >= 0.2 * base


def _components(ijk, pts, rgb, res: float = 0.015) -> list[np.ndarray]:
    """2-D connected components over occupied columns (BB's i, j), 8-connected, joining two
    neighbouring columns only when their colours are within COLOUR_TOL and their tops within
    HEIGHT_TOL: two touching objects of different colour or height come apart. A component
    ringed entirely by one other of its colour (a mug's inside) rejoins it. -> per component,
    indices into the given cells."""
    if len(ijk) == 0:
        return []
    cols, inv = np.unique(ijk[:, :2], axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    n = len(cols)
    top = np.full(n, -np.inf)
    np.maximum.at(top, inv, pts[:, 2])
    colour = np.zeros((n, 3))
    np.add.at(colour, inv, rgb.astype(float))
    colour /= np.bincount(inv, minlength=n)[:, None]
    at = {tuple(c): m for m, c in enumerate(cols.tolist())}
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def same(a, b):
        return (abs(top[a] - top[b]) <= max(HEIGHT_TOL, 1.5 * res)
                and float(np.linalg.norm(colour[a] - colour[b])) <= COLOUR_TOL)

    nbrs = [(di, dj) for di in (-1, 0, 1) for dj in (-1, 0, 1) if (di, dj) != (0, 0)]
    for m, (i, j) in enumerate(cols.tolist()):
        for di, dj in ((1, 0), (0, 1), (1, 1), (1, -1)):
            o = at.get((i + di, j + dj))
            if o is not None and same(m, o):
                parent[find(m)] = find(o)
    label = np.array([find(m) for m in range(n)])
    for lab in np.unique(label):                               # holes: a mug's inside rejoins its rim
        members = np.flatnonzero(label == lab)
        around, open_ = set(), False
        for m in members:
            i, j = cols[m]
            for di, dj in nbrs:
                o = at.get((int(i) + di, int(j) + dj))
                if o is None:
                    open_ = True
                    break
                if label[o] != lab:
                    around.add(int(label[o]))
            if open_:
                break
        if not open_ and len(around) == 1:
            other = around.pop()
            mine, theirs = colour[members].mean(axis=0), colour[label == other].mean(axis=0)
            if float(np.linalg.norm(mine - theirs)) <= COLOUR_TOL:
                label[members] = other
    return [np.flatnonzero(label[inv] == lab) for lab in np.unique(label)]


class Fit:
    """An upright box fitted to one object's cells ON BB's LATTICE, where the rasterization
    happened. cluster.Instance.box on cell centres is the wrong model at 1.5 cm: its min-area
    rectangle flips a 12 x 9 cm mug by up to 40 deg between lattice angles, PCA wanders 16 deg,
    and every side reads +12..18 mm long whenever the object sits at an angle to the lattice.

    yaw: the angle whose tightest rectangle -- one touching every occupied column -- predicts
    the occupied columns best (symmetric difference), the middle of the best plateau.
    sides: the column-centre span along each axis minus the lattice overshoot (OVERSHOOT of the
    cell's projected half-width per side, tapered to 0 within ALIGNED_DEG of the lattice).
    height: 1st-99th percentile of the cells, as Instance.box."""

    def __init__(self, ijk: np.ndarray, pts: np.ndarray, lattice: "_Lattice"):
        self.res, self.lattice, self._lat = lattice.res, lattice.deg, lattice
        cols = np.unique(ijk[:, :2], axis=0)
        self.cols = cols.astype(float) + 0.5                  # column centres, lattice units
        z0, z1 = np.percentile(pts[:, 2], [1, 99])
        self.z = ((z0 + z1) / 2, max(float(z1 - z0), self.res))     # one layer: up to a cell tall
        self.alpha = self._best_angle(cols)                   # lattice frame, [0, 90)

    def _best_angle(self, cols) -> float:
        lo, hi = cols.min(axis=0) - 1, cols.max(axis=0) + 2
        gi, gj = np.meshgrid(np.arange(lo[0], hi[0]), np.arange(lo[1], hi[1]), indexing="ij")
        region = np.column_stack([gi.ravel(), gj.ravel()])
        occ = np.zeros(len(region), bool)
        occ[((cols[:, 0] - lo[0]) * (hi[1] - lo[1]) + (cols[:, 1] - lo[1])).astype(int)] = True
        centres = region + 0.5
        angles = np.arange(0.0, 90.0, 0.5)
        score = np.array([int((self._predict(a, centres) ^ occ).sum()) for a in angles])
        best = np.flatnonzero(score == score.min())
        # the plateau's middle, on a circle of 90 deg: unwrap around the first best angle
        rel = (angles[best] - angles[best[0]] + 45.0) % 90.0 - 45.0
        return float((angles[best[0]] + (rel.max() + rel.min()) / 2) % 90.0)

    def _predict(self, a: float, centres: np.ndarray) -> np.ndarray:
        """Unit squares (lattice cells) that the tightest rectangle at angle a touches."""
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        h = 0.5 * (abs(c) + abs(s))                           # a cell's half-width along u (and v)
        pu, pv = self.cols @ (c, s), self.cols @ (-s, c)
        lu, hu = pu.min() + h, pu.max() - h
        lv, hv = pv.min() + h, pv.max() - h
        lu, hu = (lu, hu) if lu <= hu else ((lu + hu) / 2,) * 2
        lv, hv = (lv, hv) if lv <= hv else ((lv + hv) / 2,) * 2
        corners = np.array([[u * c - v * s, u * s + v * c] for u in (lu, hu) for v in (lv, hv)])
        qu, qv = centres @ (c, s), centres @ (-s, c)
        return ((qu + h >= lu) & (qu - h <= hu) & (qv + h >= lv) & (qv - h <= hv)
                & (centres[:, 0] + 0.5 >= corners[:, 0].min()) & (centres[:, 0] - 0.5 <= corners[:, 0].max())
                & (centres[:, 1] + 0.5 >= corners[:, 1].min()) & (centres[:, 1] - 0.5 <= corners[:, 1].max()))

    def _to_room(self, lat_xy) -> np.ndarray:
        return self._lat.to_room(lat_xy)

    def along(self, a: float):
        """Measure along lattice angle a -> (centre xy room, side along a, side across), metres."""
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        pu, pv = self.cols @ (c, s), self.cols @ (-s, c)
        off = min(a % 90.0, 90.0 - a % 90.0)
        su, sv = float(np.ptp(pu)), float(np.ptp(pv))                          # column-centre spans, cells
        # the span along u ends on the two faces across it, which are sv long: the more cells a
        # face has, the closer its outermost one gets to the full projected half-width beyond it
        per = OVERSHOOT * (abs(c) + abs(s)) * min(1.0, off / ALIGNED_DEG)      # both sides, cells
        tu, tv = per * min(1.0, sv / FACE_CELLS), per * min(1.0, su / FACE_CELLS)
        mu, mv = (pu.min() + pu.max()) / 2, (pv.min() + pv.max()) / 2
        centre = self._to_room((mu * c - mv * s, mu * s + mv * c))
        return centre, max(1.0, su - tu) * self.res, max(1.0, sv - tv) * self.res

    def _round(self, u: float, v: float) -> bool:
        """No meaningful yaw: near-round, or sides that differ by under 1.5 cells -- each side is
        only good to about a cell, so a 7 cm square at 3 cm reads 9 x 6 and its "long axis" would
        swing 30 deg or more from one pass to the next. (A 12 x 9 mug: 2 cells at 1.5 cm, a yaw;
        1 cell at 3 cm, round.)"""
        return max(u, v) / min(u, v) < ROUND_ASPECT or abs(u - v) < 1.5 * self.res

    def box(self, yaw: float | None = None):
        """cluster.Instance.box's contract: (centre, (along yaw, across, height), yaw in [-90, 90)).
        Near-round footprints get yaw 0, as serialize expects; a given yaw is measured along."""
        if yaw is None:
            centre, u, v = self.along(self.alpha)
            a = self.alpha + (90.0 if v > u else 0.0)
            if self._round(u, v):
                yaw = 0.0
            else:
                yaw = a + self.lattice
        yaw = (float(yaw) + 90.0) % 180.0 - 90.0
        centre, u, v = self.along(yaw - self.lattice)
        return (np.array([centre[0], centre[1], self.z[0]]), np.array([u, v, self.z[1]]), yaw)

    def yaw_known(self) -> bool:
        """Is there a yaw here at all? Not when the footprint is round (or its sides differ by
        under 1.5 cells), and not when it is under THIN_CELLS across: a 13 x 3 cm thing on a 3 cm
        map is one column wide, and its "axis" is whatever the last cell to drop out says."""
        _, u, v = self.along(self.alpha)
        return not self._round(u, v) and min(u, v) >= THIN_CELLS * self.res

    def aspect(self) -> float:
        """associate reads this to decide whether to hold the committed yaw. An unresolvable
        footprint reports round, so its yaw is 0 every pass rather than held at a guess."""
        _, u, v = self.along(self.alpha)
        return 1.0 if self._round(u, v) else max(u, v) / min(u, v)


@dataclass
class MapObject(MergedObject):
    """A merged object whose box comes from its Fit on BB's lattice, so what associate and
    serialize write is what candidates() measured."""
    fit: Fit | None = None

    def box(self, yaw: float | None = None):
        return self.fit.box(yaw) if self.fit is not None else super().box(yaw)

    def footprint_aspect(self) -> float:
        """associate holds the committed yaw (and measures along it) exactly when this falls in
        its YAW_BAND. A map object whose yaw can't be known says so through that same door: it
        keeps the yaw it was committed with instead of reporting a turn it can't see."""
        if self.fit is None:
            return super().footprint_aspect()
        if not self.fit.yaw_known():
            try:
                from .associate import YAW_BAND
            except ImportError:
                from associate import YAW_BAND
            return (YAW_BAND[0] + YAW_BAND[1]) / 2
        return self.fit.aspect()


# ── 2 · freshness and line of sight ────────────────────────────────────────────────────

def _area(area) -> SimpleNamespace:
    """roomctl.bb_nav.AreaMap, or GET /map's own JSON -> anchor (x, y, yaw), bounds, the 3 cm
    grid (ny, nx) and its resolution, block ages (rows, cols) and block size. Row 0 = ymin,
    col 0 = xmin, in the area frame (robot-relative when defined: +X right, +Y forward)."""
    if isinstance(area, dict):
        a, g, f = area.get("area", area), area["grid"], area["freshness"]
        cells = np.frombuffer(base64.b64decode(g["cells"]), np.uint8).reshape(int(g["ny"]), int(g["nx"]))
        anchor, bounds, res = a["anchor_world"], a["bounds"], float(g["resolution_m"])
        age, block_m = np.asarray(f["age_s"], float), float(f["block_m"])
    else:
        anchor, bounds, cells = area.anchor_world, area.bounds, np.asarray(area.grid)
        res, block_m = float(area.resolution_m), float(area.block_m)
        age = np.asarray(area.freshness, float)
    yaw = anchor["yaw"] if "yaw" in anchor else anchor["h"]
    return SimpleNamespace(anchor=(float(anchor["x"]), float(anchor["y"]), float(yaw)),
                           xmin=float(bounds["xmin"]), ymin=float(bounds["ymin"]),
                           grid=cells, res=res, age=age, block_m=block_m)


def fresh_blocks(area, reg=None, max_age_s: float = 10.0) -> set[tuple[int, int]]:
    """(col, row) of every 0.5 m block BB saw within max_age_s. -1 (never seen) is never fresh."""
    a = _area(area)
    rows, cols = np.nonzero((a.age >= 0) & (a.age <= max_age_s))
    return {(int(c), int(r)) for r, c in zip(rows, cols)}


def block_of(xyz, area, reg) -> tuple[int, int]:
    """The (col, row) freshness block a room-frame point falls in."""
    a = _area(area)
    x, y, _ = frames.room_to_bb(tuple(float(v) for v in xyz), reg.T)
    X, Y = frames.bb_to_robot_rel(x, y, *a.anchor)
    return math.floor((X - a.xmin) / a.block_m), math.floor((Y - a.ymin) / a.block_m)


def fresh_fn(area, reg, max_age_s: float = 10.0):
    """associate(fresh=...): (ObjectRecord) -> was the block around its last pose seen lately?"""
    fresh = fresh_blocks(area, reg, max_age_s)
    return lambda rec: block_of((rec.pose.x, rec.pose.y, rec.pose.z), area, reg) in fresh


def visibility_grid(points_room: np.ndarray, zones: dict | None, res: float = 0.015):
    """The map as a room-frame occupancy grid for line of sight (the pinned cube at 3.125 cm),
    minus each zone's surface layer: the surface an object stands on can't hide it (the eye and
    the object's top are both above it), but at 3 cm a grazing ray would clip its cells."""
    pts = np.asarray(points_room, float).reshape(-1, 3)
    drop = np.zeros(len(pts), bool)
    for zone in (zones or {}).values():
        # a cell of the surface's rim can land a few mm outside the zone once rotated into the
        # room frame; left behind, it's exactly the voxel that clips a grazing ray
        (x0, y0, _), (x1, y1, _) = np.subtract(zone["min"], 2 * res), np.add(zone["max"], 2 * res)
        inxy = (pts[:, 0] >= x0) & (pts[:, 0] < x1) & (pts[:, 1] >= y0) & (pts[:, 1] < y1)
        s = zone["surface"]
        near = inxy & (np.abs(pts[:, 2] - s) <= PLANE_SEARCH * res + 1e-9)
        if not near.any():
            continue
        z = pts[near, 2]
        edges = np.arange(s - PLANE_SEARCH * res, s + PLANE_SEARCH * res + res / 2 + 1e-9, res / 2)
        counts, edges = np.histogram(z, bins=edges)
        top = edges[np.flatnonzero(counts >= 0.5 * counts.max()).max() + 1]
        drop |= inxy & (pts[:, 2] <= top) & (pts[:, 2] > top - res - 1e-9)
    origin, size, _ = voxelize.pinned_cube()
    return voxelize.VoxelGrid.from_points(pts[~drop], cube=(origin, size, GRID_LEVELS), min_pts=1)


def _geometry(obj):
    """Candidate or ObjectRecord -> (x, y, z centre, extents x, y, z)."""
    if isinstance(obj, Candidate):
        return (*obj.centroid, *obj.extents)
    return obj.pose.x, obj.pose.y, obj.pose.z, obj.extents.x, obj.extents.y, obj.extents.z


def visible(candidate_or_record, grid_room, eye_room) -> bool:
    """Is the object's top in line of sight from eye_room (room frame, m)? The last stretch of
    the ray -- the object's own half-diagonal plus a voxel -- doesn't count: it's the object."""
    x, y, z, ex, ey, ez = _geometry(candidate_or_record)
    ignore = math.hypot(ex, ey) / 2 + grid_room.leaf * math.sqrt(3)
    return raycast.line_of_sight(tuple(eye_room), (x, y, z + ez / 2), grid_room, ignore_end=ignore)


def eye_room(nav, reg) -> tuple[float, float, float] | None:
    """Where the head camera is, room frame: BB's live /ws pose, or where a map snapshot says
    the robot was when it was taken. None until SLAM is ready, or with no pose at all."""
    st = getattr(nav, "state", None)
    if st is not None:
        if not getattr(st, "ready", True):
            return None
        xy = (st.x, st.y)
    elif getattr(source_of(nav), "robot_xy", None) is not None:
        xy = source_of(nav).robot_xy
    else:
        return None
    x, y, _ = frames.bb_to_room((float(xy[0]), float(xy[1]), 0.0), reg.T)
    return x, y, EYE_H


# ── bbos's own map: mapping.voxels, pulled by scripts/bbos_map.py ─────────────────────

@dataclass
class MapSnapshot:
    """One pull of bbos's `mapping.voxels` (scripts/bbos_map.py `pull` -> map.npz): 3 cm cells in
    the SLAM world frame (Z up, floor at z ~ 0), the camera's colour, bbos's OWN floor label per
    cell (-1 floor, 1 not), and where the robot stood. A source for candidates/scan_into_bb."""
    coords: np.ndarray                 # (N,3) float, metres, SLAM world
    colors: np.ndarray                 # (N,3) uint8 rgb
    labels: np.ndarray                 # (N,) int8: -1 floor, 1 not floor
    origin: np.ndarray                 # the map's origin: moves only when SLAM re-initialises
    pgo_count: int = -1                # pose-graph optimisations so far; only grows within one map
    robot_xy: tuple[float, float] | None = None
    at: str | None = None
    res: float = 0.03

    @classmethod
    def load(cls, path) -> "MapSnapshot":
        """A snapshot directory (…/maps/<time>/) or its map.npz."""
        p = Path(path).expanduser()
        m = dict(np.load(p / "map.npz" if p.is_dir() else p))
        at = None
        if "timestamp_ns" in m:
            at = datetime.fromtimestamp(int(m["timestamp_ns"]) / 1e9, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rp = m.get("robot_pos")
        return cls(m["coords"], m["colors"], m["labels"], np.asarray(m.get("origin", np.zeros(2))),
                   int(m.get("pgo_count", -1)), (float(rp[0]), float(rp[1])) if rp is not None else None, at)

    def points(self):
        return np.asarray(self.coords, float), np.asarray(self.colors, np.uint8)

    def floor_mask(self) -> np.ndarray:
        return np.asarray(self.labels) < 0

    @property
    def map_gen(self) -> int:
        """Changes exactly when SLAM re-initialises (a new origin), not with pgo_count."""
        return zlib.crc32(np.round(np.asarray(self.origin, float), 3).tobytes())


def identity_registration(source):
    """The demo's registration: the room frame IS the map's SLAM frame (plan/roommate 03 §2 shape).
    Refused when the map's own floor isn't at z ~ 0 -- then it isn't the room frame, register it."""
    gen = getattr(source, "map_gen", None)
    source = source_of(source)
    floor = _floor(source)
    if floor is not None and np.any(floor):
        z = float(np.median(np.asarray(source.points()[0], float)[np.asarray(floor, bool), 2]))
        if abs(z) > float(source.res):
            raise ValueError(f"the map's floor is at z = {z:+.3f} m, not 0: not the room frame, so register it")
    return SimpleNamespace(T=frames.SE2.identity(), map_gen=gen if gen is not None else getattr(source, "map_gen", None), source="identity",
                           residual_m=0.0, at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


# ── the shared indices: what a REAL pass writes ────────────────────────────────────────

def live_source(nav, source=None) -> tuple[bool, str]:
    """May this pass write to the shared Elasticsearch indices? -> (yes, why).

    An ALLOW-list, not a sim blocklist: simulated data must never land in the indices the demo
    reads as if it were real (plan/roommate MVP-NOW), so a source whose provenance we cannot
    name is not written. Real: a pull of the robot's own map (MapSnapshot, scripts/bbos_map),
    or a nav talking to a host that isn't loopback -- fake/bbsim.py binds loopback only.
    """
    source = source if source is not None else source_of(nav)
    # every sim signal first: one of them says no, whatever else the pass looks like
    for thing in (nav, source):
        if getattr(thing, "synthetic", False):
            return False, f"{type(thing).__name__} says it is synthetic"
    host = getattr(nav, "host", None) or getattr(source, "host", None)
    if host and host in LOOPBACK:
        return False, f"a nav on {host}: bbsim binds loopback"
    if isinstance(source, MapSnapshot) or (type(source).__module__ or "").rsplit(".", 1)[-1] == "bbos_map":
        return True, "a pull of the robot's own map"
    if host:
        return True, f"a nav on {host}"
    return False, f"{type(source).__name__} carries no provenance"


def map_scan_docs(assocs, capture_id: str, at: str, points: np.ndarray,
                  cloud_uri: str | None = None) -> tuple[dict, list[dict]]:
    """What /capture/<id> reads for a MAP pass: the room-clouds catalog doc and one
    room-observations row per object, all `camera: "bb_map"` (03 §10). No gate fields: a map
    pass has no shutter, so skew_ms / tilt_rate_max / coverage would be invented. `objects` is
    the count pipeline.capture_docs writes, with the same three states: absent = never scanned,
    0 = scanned and found nothing, N = N objects seen. Built inside the capture's transaction,
    so both carry its Sentry trace."""
    try:
        from . import associate
    except ImportError:
        import associate
    trace = obs.trace_fields()
    when = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    doc = {"@timestamp": at, "capture_id": capture_id, "cameras": [CAMERA],
           "point_count": int(len(points)), "objects": sum(a.obj is not None for a in assocs),
           **({"cloud_uri": cloud_uri} if cloud_uri else {}), **trace}
    if len(points):
        lo, hi = points.min(axis=0), points.max(axis=0)
        doc["bounds"] = {"min": dict(zip("xyz", map(float, lo))), "max": dict(zip("xyz", map(float, hi)))}
    return doc, associate.observation_docs(assocs, capture_id, when, trace=trace, camera=CAMERA)


def index_map_scan(docs: tuple[dict, list[dict]], capture_id: str, es):
    """Write them through es_sink, exactly as pipeline.index_capture does: retries, and the
    spool when the cluster is away."""
    try:
        from . import es_sink
    except ImportError:
        import es_sink
    cloud_doc, rows = docs
    client = None if es == "env" else es
    with obs.span("es.index_capture", capture_id=capture_id, observations=len(rows)) as sp:
        out = {"room-clouds": es_sink.deliver("room-clouds", [cloud_doc], capture_id, client),
               "room-observations": es_sink.deliver("room-observations", rows, capture_id, client)}
        if sp is not None:
            sp.set_data("spooled", sum(r.spooled for r in out.values()))
    return out


def hold_ghosts(assocs, move_m: float):
    """Don't act on the cells an object left behind. -> (assocs, [(held, whose site)]).

    A voxel map keeps an object's cells at its old pose until the robot looks there again and
    carves them (bbsim: seconds to half a minute; bbos's own rate is the robot team's question).
    Until then there is one blob more than there are objects, and the extra one sits exactly
    where something just moved from. Acting on it INVENTS WORK, which is the one thing the
    caretaker must never do: a lost_and_found chore for a phantom, or -- measured on a clicked
    run -- a tidy job for a neighbour 16 cm away that appears to have moved onto the empty spot.

    So: if a record is matched somewhere else this pass, any OTHER candidate still sitting at
    the pose it left is held. An `added` phantom is dropped; a record that would carry it is
    `unobserved` instead, its file kept byte-identical. Both are what the map can prove.
    The hold lasts while the condition does: the stale cells clear themselves, and a real object
    put on a just-vacated spot is reported as soon as the thing that left is seen where it went.
    """
    try:
        from .associate import UNOBSERVED
    except ImportError:
        from associate import UNOBSERVED
    left = [(a.object_id, (a.previous.pose.x, a.previous.pose.y))
            for a in assocs if a.obj is not None and a.previous is not None
            and math.dist(tuple(map(float, a.centre)), (a.previous.pose.x, a.previous.pose.y, a.previous.pose.z)) > move_m]
    if not left:
        return assocs, []
    out, held = [], []
    for a in assocs:
        site = None if a.obj is None else next(
            (oid for oid, p in left if oid != a.object_id and math.dist(tuple(map(float, a.centre))[:2], p) <= GHOST_M), None)
        if site is None:
            out.append(a)
            continue
        held.append((a.object_id, site))
        if a.previous is not None:      # it would have carried a committed object's identity
            out.append(replace(a, verdict=UNOBSERVED, obj=None, centre=None, extents=None, yaw=None,
                               note=f"held: the map still shows {site}'s cells at this pose"))
    return out, held


# ── 3 · one pass into the working tree ─────────────────────────────────────────────────

@dataclass
class CameraFrame:
    """The robot's latest colour frame and where it was taken: what labels and words come from."""
    image: np.ndarray           # (H,W,3) BGR, the image the masks are drawn on
    K: np.ndarray               # 3x3 intrinsics of that image
    room_to_cam: np.ndarray     # 4x4: room frame -> the camera's optical frame (X right, Y down, Z fwd)


def camera_frame(image, mount, pose_bb, reg, intrinsics) -> CameraFrame:
    """The frame provider. pose_bb: BB's (x, y, h) AT THE SHUTTER -- the /ws state stamped
    nearest the frame, not the newest one (the robot moves 0.14 m/s). x, y in metres; h in
    RADIANS, bbapps/nav's convention: h = 0 faces +y, forward = (-sin h, cos h) (frames.py).
    A heading measured from +x is 90 deg off, and every mask lands on the wrong object. mount: the head camera's
    fuse.Mount. intrinsics: (f, cx, cy) of `image` (difference.intrinsics fits them from a
    depth frame). The pose crosses into the room through frames, the camera onto the robot
    through fuse.rect_to_world, and room_to_cam is that map inverted: nothing restated."""
    try:
        from . import fuse
    except ImportError:
        import fuse
    x, y, _ = frames.bb_to_room((float(pose_bb[0]), float(pose_bb[1]), 0.0), reg.T)
    pose = (x, y, math.radians(frames.bb_yaw_to_heading_room(float(pose_bb[2]), reg.T)))
    t = fuse.rect_to_world(np.zeros((1, 3)), mount, pose)[0]
    R = (fuse.rect_to_world(np.eye(3), mount, pose) - t).T            # room = R @ optical + t
    M = np.eye(4)
    M[:3, :3], M[:3, 3] = R.T, -R.T @ t
    f, cx, cy = intrinsics
    return CameraFrame(np.asarray(image), np.array([[f, 0.0, cx], [0.0, f, cy], [0.0, 0.0, 1.0]]), M)


def scan_into_bb(repo_dir, nav, reg, *, frame: CameraFrame | None = None, segmenter=None, describe=None,
                 vlm=None, es=None, at: str | None = None, capture_id: str | None = None):
    """One pass of BB's map -> the room repo's working tree, stabilized against HEAD, and the
    same staging a stereo capture leaves for the commit (voxels + scan metadata). `nav` needs
    .mirror; .area (freshness) and .state (the eye) when BB has them. Without an area every
    block counts as fresh; without a pose nothing is occluded -- the stereo path's defaults.

    frame (camera_frame()): names and words. Each candidate gets the segmenter's label where a
    mask matches it (segment.label_map_objects), so a NEW object is committed as `mug_…`, not
    `unknown_…`; segmenter as pipeline.scan_into resolves it (GITSPACE_SEGMENTER, the weights).
    describe (default GITSPACE_DESCRIBE=1): VLM words for what this pass ADDED only -- a quiet
    room costs no call. Without a frame, both are skipped and classes stay "unknown".

    es (default "env" with GITSPACE_INDEX_CAPTURES=1, and only from the room's own repo --
    roomctl/cli.py's rule, publish.is_the_room): write what /capture/<id> reads -- the
    room-clouds doc and one `camera: "bb_map"` room-observations row per object. Only a REAL
    pass is ever written (live_source): a bbsim run is refused and says so, whatever es says."""
    describe_on = describe if describe is not None else os.getenv("GITSPACE_DESCRIBE") == "1"
    if frame is None and (segmenter is not None or describe is True):
        raise ValueError("labels and words come from the robot's frame: pass frame=camera_frame(...)")
    import associate
    import segment
    import serialize
    from pipeline import ScanResult
    from roomctl import publish
    from roomctl.repo import Repo
    from roomctl.state import MOVE_M

    source = source_of(nav)
    gen = getattr(source, "map_gen", None)
    gen = gen if gen is not None else getattr(nav, "map_gen", None)
    if gen is not None and getattr(reg, "map_gen", None) is not None and gen != reg.map_gen:
        raise ValueError(f"map_gen {gen} != the registration's {reg.map_gen}: SLAM re-initialised, re-register")
    repo = Repo(Path(repo_dir))
    if not repo.exists:
        raise FileNotFoundError(f"{repo.path} is not a room repo: `room init` it first")
    head = repo.records()
    zones = voxelize.load_room(repo.path).get("zones") or {}
    at = at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    capture_id = capture_id or f"bb_{int(time.time() * 1000)}"
    area = getattr(nav, "area", None)
    res = float(source.res)
    with obs.capture_scope(capture_id), obs.transaction("perception.scan", f"scan {capture_id}"):
        pts, rgb, ijk, off = _cells(source, reg)
        with obs.span("perception.bb_candidates", cells=len(pts)):
            cands = _candidates(pts, rgb, ijk, res, zones, min_cells_for(res), BAND, _Lattice(reg.T, off, res))
        labels, ignored_paths = segment.roomignore(repo.path)
        objects = [MapObject(views=[Instance(points=c.points, camera=CAMERA, color=c.color)], fit=c.fit)
                   for c in cands]
        if frame is not None:
            from pipeline import _segmenter
            seg = _segmenter(segmenter)                  # None: no model here -> box crops, no names
            segment.label_map_objects(objects, cands, frame.image, frame.K, frame.room_to_cam,
                                      segmenter=seg or (lambda image: []),
                                      ignore=segment.IGNORE_LABELS | frozenset(labels))
        eye = eye_room(nav, reg)
        vis = visibility_grid(pts, zones, res) if eye is not None else None
        misses = associate.load_misses(repo.path)
        assocs = associate.associate(
            objects, head, capture_id, now=at, zones=zones, misses=misses,
            occluded=None if eye is None else (lambda rec: not visible(rec, vis, eye)),
            fresh=None if area is None else fresh_fn(area, reg))
        assocs, held = hold_ghosts(assocs, MOVE_M)
        if held:
            log.info("%s: held %d candidate(s) on a just-vacated pose: %s", capture_id, len(held),
                     ", ".join(f"{a} on {b}'s" for a, b in held))
        if frame is not None and describe_on:                            # before stage_scan: it carries the words
            import describe as describe_mod
            describe_mod.describe_added(assocs, frame.image, vlm)
        measured, carried = associate.for_serialize(assocs, zones, ignore_paths=ignored_paths)
        serialize.serialize(repo.path, measured, head, carried)
        associate.save_misses(repo.path, associate.next_misses(assocs, misses))
        seen = [a for a in assocs if a.obj is not None]
        grid = voxelize.VoxelGrid.from_points(pts, min_pts=1)            # the pinned cube: ES keys
        voxelize.stage(grid, repo.path, claims={a.object_id: a.obj.points for a in seen})
        publish.stage_scan(repo, capture_id, at, {a.object_id: a.obj.object_fields() for a in seen},
                           trace=obs.trace_fields())
        if es is None and os.getenv("GITSPACE_INDEX_CAPTURES") == "1" and publish.is_the_room(repo):
            es = "env"          # one rule with roomctl/cli.py: only the room's own repo publishes
        if es is not None:
            ok, why = live_source(nav, source)
            if ok:
                from pipeline import save_cloud            # the same blob/catalog split (docs/11)
                index_map_scan(map_scan_docs(assocs, capture_id, at, pts, save_cloud(pts, capture_id)),
                               capture_id, es)
            else:
                log.warning("%s: not indexed -- %s", capture_id, why)
    verdicts: dict[str, int] = {}
    for a in assocs:
        verdicts[a.verdict] = verdicts.get(a.verdict, 0) + 1
    return ScanResult(capture_id, True, verdicts, len(measured), len(grid.ijk))
