"""Fallback segmentation: support-plane removal (RANSAC x4), then DBSCAN.

Catches what the mask model doesn't recognise, so `unknown` things still get tracked.
See ../docs/15-segmentation.md, Approach B.

Input is an (N,3) cloud in F_world -- metres, Z-up, floor at z=0 (docs/20, Part 2). Plane
kinds rely on +Z being up. Output is the removed planes, which are KEPT because they
become zones, and one Instance per surviving cluster.

Plane removal is non-negotiable: leave the table in and every object on it is connected
through the tabletop, so DBSCAN returns one blob.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

log = logging.getLogger(__name__)

# Scene-specific. Tune eps and PLANE_DIST on the actual demo table in hour one.
VOXEL = 0.01              # m
OUTLIER_K = 20            # statistical outlier removal: neighbours...
OUTLIER_STD = 2.0         # ...and std ratio
PLANE_DIST = 0.015        # m. too large: flat objects get eaten with the table
PLANE_ITERS = 1000
PLANE_SCORE_PTS = 20000   # RANSAC hypotheses are scored on a subsample of this size
MAX_PLANES = 4            # floor, table, maybe walls
MIN_PLANE_PTS = 2000      # fewer inliers than this: no big plane left
DBSCAN_EPS = 0.025        # m, ~2-3x voxel. too large: adjacent objects merge
DBSCAN_CORE = 10          # neighbours within eps (self included) to be a core point
MIN_CLUSTER_PTS = 40      # smaller clusters are noise
MIN_EXTENT, MAX_EXTENT = 0.02, 0.60   # m, longest box side. rejects noise and wall fragments
SPLIT_XY, SPLIT_Z = 0.01, 0.05   # m. two clusters are one split object when their footprints touch
                                 # (within SPLIT_XY) and the vertical gap is <= SPLIT_Z: on a table,
                                 # a piece hovering over another is a dropout band, not two things

# DBSCAN_CORE vs MIN_CLUSTER_PTS: docs/15 passes min_points=40 straight to DBSCAN. Stereo
# gives surfaces, not volumes: on a 1 cm grid a point on a clean, one-voxel-thick surface
# has ~22-28 neighbours within 2.5 cm, so with 40 nothing is a core point and the whole
# object is noise. It only works while depth noise smears surfaces two voxels thick, i.e.
# it breaks as depth gets better. The doc's 40 is really a minimum object size, so it is
# applied as one, and core density is set well below the clean-surface count.

HORIZONTAL_DEG = 10.0     # normal within this of +Z: floor or table
VERTICAL_DEG = 80.0       # normal further than this from +Z: wall
FLOOR_TOL = 0.05          # m. a horizontal plane this close to z=0 is the floor
ROUND_ASPECT = 1.2        # footprint closer to round than this has no meaningful yaw


@dataclass
class Plane:
    """A removed support plane. Kept, not discarded: it becomes a zone."""
    normal: np.ndarray    # (3,) unit, largest component positive (floor/table: +Z)
    d: float              # normal . p + d = 0
    points: np.ndarray    # (M,3) inliers
    kind: str             # "floor" | "surface" | "wall" | "slanted"

    @property
    def height(self) -> float:
        return float(np.median(self.points[:, 2]))


@dataclass
class Instance:
    """One object hypothesis. Produced by both paths: here (label "unknown") and segment.py."""
    points: np.ndarray                # (N,3)
    label: str = "unknown"
    source: str = "cluster"           # "cluster" | "segment"
    camera: str | None = None         # None for clusters found in the fused world cloud
    mask: np.ndarray | None = None    # (H,W) bool on left_rect, segment path only
    score: float | None = None        # segmenter confidence. ES only, never the YAML
    color: str | None = None          # "#rrggbb" under the mask. schema `color`, first sight only
    description: Any = None           # describe.ViewDescription for THIS view, never merged

    @property
    def centroid(self) -> np.ndarray:
        return self.points.mean(axis=0)

    def box(self, yaw: float | None = None) -> tuple[np.ndarray, np.ndarray, float]:
        """Upright box in a Z-up frame: (centre, extents (length, width, height), yaw deg).

        This is the frozen object schema (roomctl.state): yaw is the AXIS of extents[0],
        folded into [-90, 90) here and into [0, 180) by serialize. It is an axis, not a
        heading -- geometry can't tell front from back. Near-round footprints get yaw 0.
        Extents use 1st/99th percentiles so a few stray points don't move them.

        Known flip: a footprint whose aspect sits near ROUND_ASPECT (a 10 x 8 cm box) can
        report yaw 0 on one scan and its real yaw on the next. associate.py keeps the
        committed yaw there by passing it in as `yaw`: extents are then measured along it.
        """
        p = self.points
        z0, z1 = np.percentile(p[:, 2], [1, 99])
        xy = p[:, :2] - p[:, :2].mean(axis=0)
        if yaw is None:
            yaw, aspect = _footprint_axis(xy)
            if aspect < ROUND_ASPECT:
                yaw = 0.0
        yaw = (float(yaw) + 90.0) % 180.0 - 90.0
        c, s = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))
        local = xy @ np.array([[c, -s], [s, c]])          # rotate into the box frame
        lo, hi = np.percentile(local, [1, 99], axis=0)
        mid = (lo + hi) / 2
        centre_xy = p[:, :2].mean(axis=0) + np.array([c * mid[0] - s * mid[1], s * mid[0] + c * mid[1]])
        centre = np.array([centre_xy[0], centre_xy[1], (z0 + z1) / 2])
        extents = np.array([hi[0] - lo[0], hi[1] - lo[1], z1 - z0])
        return centre, extents, float(yaw)

    def footprint_aspect(self) -> float:
        """Long/short side of the footprint rectangle. Near ROUND_ASPECT, yaw is unreliable."""
        return _footprint_axis(self.points[:, :2] - self.points[:, :2].mean(axis=0))[1]


def _footprint_axis(xy: np.ndarray, max_pts: int = 4000) -> tuple[float, float]:
    """Long axis of the smallest footprint rectangle -> (yaw deg in [-90, 90), aspect).

    Minimises the 1st-99th percentile rectangle area over rotation. Principal axes (PCA)
    wander by ~12 deg scan to scan on a 13 x 10 cm box, which is more than the yaw
    deadband. cv2.minAreaRect is exact on clean points but 0.5% stray stereo points swing
    it by up to 90 deg. This stays within ~1 deg with 2% stray points.
    """
    if len(xy) < 3:
        return 0.0, 1.0
    xy = xy[::len(xy) // max_pts + 1]           # deterministic stride, not a random sample

    def sides(deg):
        th = np.radians(np.atleast_1d(deg))
        u = xy @ np.stack([np.cos(th), np.sin(th)])       # (N, A): along each candidate axis
        v = xy @ np.stack([-np.sin(th), np.cos(th)])      #          and across it
        return np.ptp(np.percentile(u, [1, 99], axis=0), axis=0), np.ptp(np.percentile(v, [1, 99], axis=0), axis=0)

    coarse = np.arange(0.0, 90.0, 1.0)                    # a rectangle repeats every 90 deg
    lu, lv = sides(coarse)
    fine = coarse[np.argmin(lu * lv)] + np.arange(-1.0, 1.05, 0.1)
    lu, lv = sides(fine)
    k = int(np.argmin(lu * lv))
    yaw = float(fine[k]) + (90.0 if lv[k] > lu[k] else 0.0)
    long, short = max(lu[k], lv[k]), min(lu[k], lv[k])
    return (yaw + 90.0) % 180.0 - 90.0, float(long / short) if short > 0 else np.inf


def cluster(points: np.ndarray, seed: int = 0) -> tuple[list[Plane], list[Instance]]:
    """F_world cloud -> (removed support planes, unknown-class instances).

    Deterministic for a given input: RANSAC draws from a fixed seed, so rescanning an
    unchanged scene can't give a different answer by luck.
    """
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    pts = pts[np.isfinite(pts).all(axis=1)]
    pts = voxel_down_sample(pts, VOXEL)
    pts = remove_statistical_outliers(pts, OUTLIER_K, OUTLIER_STD)

    rng = np.random.default_rng(seed)
    planes: list[Plane] = []
    for _ in range(MAX_PLANES):
        found = segment_plane(pts, PLANE_DIST, PLANE_ITERS, rng)
        if found is None or found[2].sum() < MIN_PLANE_PTS:
            break
        normal, d, inliers = found
        planes.append(Plane(normal, d, pts[inliers], _plane_kind(normal, pts[inliers])))
        pts = pts[~inliers]

    instances: list[Instance] = []
    rejected = 0
    labels = merge_split_clusters(pts, dbscan(pts, DBSCAN_EPS, DBSCAN_CORE))
    for i in range(int(labels.max(initial=-1)) + 1):
        members = pts[labels == i]
        if len(members) < MIN_CLUSTER_PTS:
            rejected += 1
            continue
        inst = Instance(points=members)
        if not MIN_EXTENT < inst.box()[1].max() < MAX_EXTENT:
            rejected += 1
            continue
        instances.append(inst)

    log.info("cluster: %d planes %s, %d instances, %d clusters rejected, %d points left as noise",
             len(planes), [p.kind for p in planes], len(instances), rejected, int((labels == -1).sum()))
    return planes, instances


def merge_split_clusters(pts: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """docs/15 failure mode 2: one object split in two -- a stereo dropout band, or its base
    shaved off by plane removal -- becomes two clusters, and the small one comes and goes
    between scans: a phantom `added`, or a delete, on an untouched room (docs/10 G2, seed 7).
    Clusters whose footprints touch and whose vertical gap is small are one object: relabel them
    together, keeping each group's lowest label so the numbering stays deterministic."""
    k = int(labels.max(initial=-1)) + 1
    if k < 2:
        return labels
    tol = np.array([SPLIT_XY, SPLIT_XY, SPLIT_Z])
    lo = np.array([pts[labels == i].min(axis=0) for i in range(k)])
    hi = np.array([pts[labels == i].max(axis=0) for i in range(k)])
    parent = list(range(k))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a in range(k):
        for b in range(a + 1, k):
            if np.all(lo[a] - tol <= hi[b]) and np.all(lo[b] - tol <= hi[a]):
                ra, rb = root(a), root(b)
                parent[max(ra, rb)] = min(ra, rb)
    roots = sorted({root(i) for i in range(k)})
    renum = {r: n for n, r in enumerate(roots)}
    out = labels.copy()
    for i in range(k):
        out[labels == i] = renum[root(i)]
    return out


def dbscan(pts: np.ndarray, eps: float, min_samples: int) -> np.ndarray:
    """DBSCAN labels, -1 = noise. scipy only: the repo .venv has no sklearn.

    Same core/noise semantics as sklearn (min_samples counts the point itself). Clusters
    are numbered by their lowest point index and a border point joins the cluster of its
    lowest-index core neighbour, so the output depends on nothing but the input.
    """
    n = len(pts)
    labels = np.full(n, -1)
    if n == 0:
        return labels
    pairs = cKDTree(pts).query_pairs(eps, output_type="ndarray")
    core = np.bincount(pairs.ravel(), minlength=n) + 1 >= min_samples
    if not core.any():
        return labels

    cc = pairs[core[pairs[:, 0]] & core[pairs[:, 1]]]
    _, comp = connected_components(coo_matrix((np.ones(len(cc)), (cc[:, 0], cc[:, 1])), shape=(n, n)),
                                   directed=False)
    core_idx = np.flatnonzero(core)
    comps, first = np.unique(comp[core_idx], return_index=True)
    lut = np.full(comp.max() + 1, -1)
    lut[comps] = np.argsort(np.argsort(first))                # renumber by first appearance
    labels[core_idx] = lut[comp[core_idx]]

    mixed = pairs[core[pairs[:, 0]] != core[pairs[:, 1]]]
    border = np.where(core[mixed[:, 0]], mixed[:, 1], mixed[:, 0])
    anchor = np.where(core[mixed[:, 0]], mixed[:, 0], mixed[:, 1])
    order = np.lexsort((anchor, border))
    b, first = np.unique(border[order], return_index=True)
    labels[b] = labels[anchor[order][first]]
    return labels


def voxel_down_sample(pts: np.ndarray, size: float) -> np.ndarray:
    """Average the points in each occupied voxel (Open3D's voxel_down_sample)."""
    if not len(pts):
        return pts
    k = np.floor(pts / size).astype(np.int64) + (1 << 20)   # 21 bits per axis: +-10 km at 1 cm
    keys = (k[:, 0] << 42) | (k[:, 1] << 21) | k[:, 2]
    _, inv, counts = np.unique(keys, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    sums = np.stack([np.bincount(inv, weights=pts[:, a], minlength=len(counts)) for a in range(3)], axis=1)
    return sums / counts[:, None]


def remove_statistical_outliers(pts: np.ndarray, k: int, std_ratio: float) -> np.ndarray:
    """Drop points whose mean distance to their k neighbours is far above the average."""
    if len(pts) <= k:
        return pts
    dist, _ = cKDTree(pts).query(pts, k=k + 1, workers=-1)
    mean_d = dist[:, 1:].mean(axis=1)
    return pts[mean_d <= mean_d.mean() + std_ratio * mean_d.std()]


def segment_plane(pts: np.ndarray, dist: float, iters: int, rng: np.random.Generator):
    """RANSAC for the dominant plane -> (normal, d, inlier mask), or None.

    The winning 3-point hypothesis is refit by least squares on its inliers, which is
    much steadier scan-to-scan than the raw hypothesis.
    """
    n = len(pts)
    if n < 3:
        return None
    sub = pts if n <= PLANE_SCORE_PTS else pts[rng.choice(n, PLANE_SCORE_PTS, replace=False)]
    tri = pts[rng.integers(0, n, size=(iters, 3))]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    norms = np.linalg.norm(normals, axis=1)
    ok = norms > 1e-12
    if not ok.any():
        return None
    normals = normals[ok] / norms[ok, None]
    ds = -np.einsum("ij,ij->i", normals, tri[ok, 0])

    best, best_count = 0, -1
    for s in range(0, len(normals), 64):
        counts = (np.abs(sub @ normals[s:s + 64].T + ds[s:s + 64]) < dist).sum(axis=0)
        j = int(counts.argmax())
        if counts[j] > best_count:
            best, best_count = s + j, int(counts[j])

    inliers = np.abs(pts @ normals[best] + ds[best]) < dist
    normal, d = _fit_plane(pts[inliers])
    return normal, d, np.abs(pts @ normal + d) < dist


def _fit_plane(p: np.ndarray) -> tuple[np.ndarray, float]:
    c = p.mean(axis=0)
    normal = np.linalg.svd(p - c, full_matrices=False)[2][-1]
    if normal[np.argmax(np.abs(normal))] < 0:             # canonical sign, so it can't flip between scans
        normal = -normal
    return normal, float(-normal @ c)


def _plane_kind(normal: np.ndarray, inliers: np.ndarray) -> str:
    tilt = np.degrees(np.arccos(min(1.0, abs(normal[2]))))
    if tilt < HORIZONTAL_DEG:
        return "floor" if abs(np.median(inliers[:, 2])) < FLOOR_TOL else "surface"
    if tilt > VERTICAL_DEG:
        return "wall"
    return "slanted"
