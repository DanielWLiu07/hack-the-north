"""Fused cloud -> occupancy grid -> octree keys -> room-voxels. Stage 9 of docs/20-perception-logic.md.

The grid IS the octree's leaf level. Its cube (origin, size, levels) comes from .env's
ROOM_ORIGIN_X/Y/Z, ROOM_CUBE_SIZE, OCTREE_LEVELS -- 8 m, 7 levels, 6.25 cm cells -- and is
PINNED FOREVER: every voxel_key in the index is relative to it, so moving it silently
invalidates all history (docs/11). index_voxels() refuses to write if the cube disagrees
with the room repo's room.yaml.

Consumers: costmap.py projects the grid for driving, raycast.py walks it for line of sight,
and index_voxels() puts it in Elasticsearch, one document per occupied voxel.
Frame and units: F_world (X fwd, Y left, Z up, floor at z = 0), metres.

Elasticsearch may be away -- down, slow, or its key parked to save quota. index_voxels()
retries what is transient, and otherwise SPOOLS the commit's documents to disk instead of
losing them or stalling the pipeline (docs/11: indexing can fail at 4am without the robot
caring). flush_spool() replays them once the cluster is back; natural _ids make that safe.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def _sibling(name: str):
    """A perception/ sibling as ONE module object, however this file was imported.

    voxelize is loaded flat (`voxelize`: pipeline, roomctl/publish._import, tests) AND as
    `perception.voxelize` (elastic/records.py), often in the same process: the commit hook
    does both. A flat `import es_sink` crashed the package style (test_publish 11/18, docs/10);
    relative-then-flat gave each style its OWN es_sink: two IndexResult classes and two
    Offline exceptions, so `except es_sink.Offline` in one missed the other's raise. Load it
    by path -- no sys.path needed from the importer -- and register it under both names.
    """
    import importlib.util

    mod = sys.modules.get(name) or sys.modules.get(f"perception.{name}")
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            del sys.modules[name]       # a real error inside it surfaces as itself, never masked
            raise
    sys.modules.setdefault(name, mod)
    sys.modules.setdefault(f"perception.{name}", mod)
    return mod


es_sink = _sibling("es_sink")
import obs  # noqa: E402

IndexResult = es_sink.IndexResult
from roomctl.repo import default_path, octree_cube  # noqa: E402

MIN_PTS = 3                  # fewer points than this in a voxel is stereo speckle, not a surface
Z_PCT = (0.10, 0.50, 0.90)   # per-voxel heights: low, median, high. A stray point moves none
INDEX = "room-voxels"


def pinned_cube(env_file: Path = ROOT / ".env") -> tuple[tuple[float, float, float], float, int]:
    """(origin, size, levels) from .env; a real environment variable wins, as everywhere else.
    Read here rather than trusted to os.environ, so a process that never loaded .env can't
    quietly fall back to defaults."""
    env = {**dotenv_values(env_file), **os.environ}
    o, s, lv = octree_cube()                       # roomctl's defaults, for keys .env lacks
    origin = tuple(float(env.get(f"ROOM_ORIGIN_{a}", d)) for a, d in zip("XYZ", o))
    return origin, float(env.get("ROOM_CUBE_SIZE", s)), int(env.get("OCTREE_LEVELS", lv))


def octree_key(x, y, z, origin, size, levels=7):
    """docs/11's encoder, verbatim. origin: (x0,y0,z0) corner of a CUBE of side `size`
    containing the room. Cell size = size / 2**levels (8 m / 128 = 6.25 cm at levels=7)."""
    f = [(v - o) / size for v, o in zip((x, y, z), origin)]
    if any(c < 0.0 or c >= 1.0 for c in f):
        return None                      # outside the indexed volume -- clamp or drop
    digits = []
    for _ in range(levels):
        f = [c * 2 for c in f]
        bits = [int(c) for c in f]       # 0 or 1 per axis
        f = [c - b for c, b in zip(f, bits)]
        digits.append(str((bits[0] << 2) | (bits[1] << 1) | bits[2]))
    return "".join(digits)


def keys_of(ijk: np.ndarray, levels: int) -> list[str]:
    """octree_key for whole voxels at once, from their integer indices: digit l is
    (bit l of i)<<2 | (bit l of j)<<1 | (bit l of k), most significant first -- which is
    the interleaved index written in octal."""
    ijk = np.asarray(ijk, np.int64).reshape(-1, 3)
    code = np.zeros(len(ijk), np.int64)
    for b in range(levels - 1, -1, -1):
        bits = (ijk >> b) & 1
        code = code * 8 + (bits[:, 0] << 2 | bits[:, 1] << 1 | bits[:, 2])
    return [format(int(c), f"0{levels}o") for c in code]


def key_box(key: str, origin, size) -> tuple[np.ndarray, np.ndarray]:
    """The (lo, hi) corners of the cube cell a key -- or any prefix of one -- names."""
    lo, side = np.asarray(origin, float).copy(), float(size)
    for d in key:
        side /= 2
        lo += side * np.array([(int(d) >> 2) & 1, (int(d) >> 1) & 1, int(d) & 1])
    return lo, lo + side


@dataclass
class VoxelGrid:
    origin: np.ndarray    # (3,) corner of the cube, metres
    size: float           # cube side, metres
    levels: int           # octree depth; a voxel is size / 2**levels on a side
    ijk: np.ndarray       # (M,3) int: occupied voxels, x/y/z index
    count: np.ndarray     # (M,)   points in each
    z_lo: np.ndarray      # (M,)   10th-percentile point height in each, metres
    z_mid: np.ndarray     # (M,)   median point height in each, metres
    z_hi: np.ndarray      # (M,)   90th-percentile point height in each, metres
    occ: np.ndarray       # (n,n,n) bool, occ[i, j, k]: the same voxels, for O(1) lookup

    @property
    def n(self) -> int:
        return 2 ** self.levels

    @property
    def leaf(self) -> float:
        return self.size / self.n

    @property
    def cube(self) -> tuple[tuple[float, float, float], float, int]:
        return tuple(float(v) for v in self.origin), self.size, self.levels

    def centres(self) -> np.ndarray:
        """(M,3) F_world centre of each occupied voxel."""
        return self.origin + (self.ijk + 0.5) * self.leaf

    def keys(self) -> list[str]:
        return keys_of(self.ijk, self.levels)

    @classmethod
    def from_points(cls, cloud: np.ndarray, cube=None, min_pts: int = MIN_PTS) -> "VoxelGrid":
        """(N,3) F_world cloud -> grid. Points outside the cube are dropped."""
        origin, size, levels = cube or pinned_cube()
        origin = np.asarray(origin, float)
        n, leaf = 2 ** levels, size / 2 ** levels
        with obs.span("perception.voxelize", n_points=len(cloud)) as sp:
            p = np.asarray(cloud, float).reshape(-1, 3)
            p = p[np.isfinite(p).all(axis=1)]
            f = np.floor((p - origin) / leaf).astype(np.int64)
            inside = ((f >= 0) & (f < n)).all(axis=1)
            p, f = p[inside], f[inside]
            key = (f[:, 0] * n + f[:, 1]) * n + f[:, 2]
            order = np.lexsort((p[:, 2], key))              # by voxel, then height within it
            key, z = key[order], p[order, 2]
            uniq, start, count = np.unique(key, return_index=True, return_counts=True)
            keep = count >= min_pts
            uniq, start, count = uniq[keep], start[keep], count[keep]
            z_lo, z_mid, z_hi = (z[start + np.round((count - 1) * q).astype(np.int64)] for q in Z_PCT)
            ijk = np.column_stack([uniq // (n * n), uniq // n % n, uniq % n])
            occ = np.zeros((n, n, n), bool)
            occ[ijk[:, 0], ijk[:, 1], ijk[:, 2]] = True
            if sp is not None:
                sp.set_data("n_voxels", len(uniq))
        return cls(origin, float(size), int(levels), ijk, count, z_lo, z_mid, z_hi, occ)


    @classmethod
    def from_docs(cls, docs: list[dict], cube=None) -> "VoxelGrid":
        """room-voxels documents -> the grid they were indexed from (docs/24 A5: planning may read
        the voxel grid from Elasticsearch). The voxel_key IS the cell; a doc whose `cell` isn't
        that cell's centre came from another cube and is refused.

        DEPTH comes from the keys, not from the pinned cube. One commit's documents are all one
        depth and `len(voxel_key)` IS that depth, so a commit written before an OCTREE_LEVELS
        change still reads back at its own resolution instead of raising -- which is what makes
        such a change reversible rather than one-way (history and diff views read old commits).
        ORIGIN and SIZE still come from the cube, and the `cell` check below is still the guard
        that catches a document from a different cube. Two things stay errors: documents of
        several depths at once (a query spanning a change would give one grid at two
        resolutions), and a key DEEPER than this reader's cube (the writer is newer than us).
        """
        origin, size, pinned = cube or pinned_cube()
        origin = np.asarray(origin, float)
        depths = {len(d["voxel_key"]) for d in docs}
        if len(depths) > 1:
            raise ValueError(f"room-voxels documents of several depths ({sorted(depths)}): a query "
                             f"spanning an OCTREE_LEVELS change reads as one grid at two resolutions")
        levels = depths.pop() if depths else pinned
        if levels > pinned:
            raise ValueError(f"voxel_key has {levels} levels and this cube has {pinned}: the writer is "
                             f"newer than this reader")
        leaf = size / 2 ** levels
        ijk = np.zeros((len(docs), 3), np.int64)
        for r, d in enumerate(docs):
            for digit in map(int, d["voxel_key"]):
                ijk[r] = ijk[r] * 2 + [(digit >> 2) & 1, (digit >> 1) & 1, digit & 1]
        centres = origin + (ijk + 0.5) * leaf
        cells = np.array([[d["cell"]["x"], d["cell"]["y"]] for d in docs], float).reshape(-1, 2)
        if len(docs) and np.abs(cells - centres[:, :2]).max() > leaf / 2:
            raise ValueError("room-voxels cells don't sit in their voxel_key's cube cell: a different cube")
        z_lo = np.array([d["z_min"] for d in docs], float)
        z_hi = np.array([d["z_max"] for d in docs], float)
        occ = np.zeros((2 ** levels,) * 3, bool)
        occ[ijk[:, 0], ijk[:, 1], ijk[:, 2]] = True
        return cls(origin, float(size), int(levels), ijk, np.array([d.get("density") or 0 for d in docs]),
                   z_lo, (z_lo + z_hi) / 2, z_hi, occ)


def voxels_for_commit(es, commit_sha: str, page: int = 5000) -> list[dict]:
    """Every room-voxels document of one commit, paged with search_after (a commit is 10^3-10^4)."""
    out, after = [], None
    while True:
        r = es.search(index=INDEX, size=page, query={"term": {"commit_sha": commit_sha}},
                      sort=[{"voxel_key": "asc"}], **({"search_after": after} if after else {}))
        hits = r["hits"]["hits"]
        out += [h["_source"] for h in hits]
        if len(hits) < page:
            return out
        after = hits[-1]["sort"]


# ── room-voxels ──────────────────────────────────────────────────────────────

def load_room(room_root: Path | None = None) -> dict:
    """room.yaml from the room repo (roomctl's default: ROOM_GIT_PATH, ./room.git), or {}."""
    p = Path(room_root or default_path()) / "room.yaml"
    return yaml.safe_load(p.read_text()) if p.is_file() else {}


def check_pinned(cube, room: dict) -> None:
    """Refuse a cube that isn't room.yaml's: a key from a moved cube names a different place."""
    oc = room.get("octree")
    if not oc:
        return
    pinned = (tuple(float(v) for v in oc["origin"]), float(oc["size_m"]), int(oc["levels"]))
    got = (tuple(float(v) for v in cube[0]), float(cube[1]), int(cube[2]))
    if got != pinned:
        raise ValueError(f"octree cube {got} is not room.yaml's {pinned}: every voxel_key in "
                         f"{INDEX} is relative to the pinned cube. Fix .env, never room.yaml.")


def zone_of(points: np.ndarray, zones: dict | None) -> list[str | None]:
    """room.yaml's zone boxes: for each F_world point, the first zone BY NAME whose
    [min, max) box holds it, else None. One rule for voxels and for objects, so a mug and
    the voxels under it can't be filed in different zones."""
    p = np.asarray(points, float).reshape(-1, 3)
    out: list[str | None] = [None] * len(p)
    for name in sorted(zones or {}, reverse=True):          # reverse, so the first name wins
        lo, hi = (np.asarray(zones[name][k], float) for k in ("min", "max"))
        for i in np.flatnonzero(((p >= lo) & (p < hi)).all(axis=1)):
            out[i] = name
    return out


def voxel_docs(grid: VoxelGrid, commit_sha: str, parent_sha: str | None, branch: str,
               timestamp: str, zones: dict | None = None,
               claims: dict[str, np.ndarray] | None = None,
               owners: dict[int, str] | None = None) -> list[dict]:
    """One room-voxels document per occupied voxel (docs/11, docs/13 shape).

    z_min/z_max are the voxel's 10th/90th-percentile point heights. zone: the first zone box
    in room.yaml (by name) holding the voxel centre. object_id: of the instance in `claims`
    (object_id -> its (N,3) F_world points) with the most points in that voxel -- voxels
    that no instance claims (walls, table, clutter) have none (docs/15). Every document
    carries the CURRENT Sentry trace, so call this inside the capture's span. `owners`
    (voxel row -> object_id, as stage() stores them) replaces `claims` when given.
    """
    if not isinstance(commit_sha, str) or not commit_sha:      # elastic/ingest.py natural_id refuses it too:
        raise ValueError(f"commit_sha must be a non-empty str, got {commit_sha!r}")   # "None:<key>" never overwrites
    trace = obs.trace_fields()
    if "sentry_trace_id" not in trace and _sentry_live():
        raise RuntimeError("no Sentry trace: every room-voxels doc must carry sentry_trace_id "
                           "(obs.py's link back to the waterfall) -- call this inside the capture's span")
    keys, centres = grid.keys(), grid.centres()
    zone = zone_of(centres, zones)
    owner = owners if owners is not None else _claims(grid, claims or {})
    return [{
        "@timestamp": timestamp, "commit_sha": commit_sha, "parent_sha": parent_sha, "branch": branch,
        "voxel_key": k, "voxel_key_l5": k[:5], "voxel_key_l3": k[:3],
        "cell": {"x": round(float(c[0]), 4), "y": round(float(c[1]), 4)},
        "z_min": round(float(lo), 4), "z_max": round(float(hi), 4), "density": int(n),
        "zone": zone[i], "object_id": owner.get(i), **trace,
    } for i, (k, c, lo, hi, n) in enumerate(zip(keys, centres, grid.z_lo, grid.z_hi, grid.count))]


def _sentry_live() -> bool:
    """Is a Sentry client actually sending? With Sentry off (no SDK, no DSN, a parked one)
    there is no trace to link to, and indexing must not stop for that (docs/11)."""
    try:
        import sentry_sdk
        return sentry_sdk.get_client().is_active()
    except Exception:  # noqa: BLE001 - no SDK, or one too old to ask: not live
        return False


def _claims(grid: VoxelGrid, claims: dict[str, np.ndarray]) -> dict[int, str]:
    """voxel row -> object_id with the most points in it; ties to the smaller id."""
    row = {tuple(v): r for r, v in enumerate(grid.ijk.tolist())}
    best: dict[int, tuple[int, str]] = {}
    for oid in sorted(claims):
        f = np.floor((np.asarray(claims[oid], float).reshape(-1, 3) - grid.origin) / grid.leaf).astype(np.int64)
        cells, n = np.unique(f, axis=0, return_counts=True)
        for c, m in zip(cells.tolist(), n.tolist()):
            r = row.get(tuple(c))
            if r is not None and m > best.get(r, (0, ""))[0]:
                best[r] = (m, oid)
    return {r: oid for r, (_, oid) in best.items()}


# ── scan time -> commit time ─────────────────────────────────────────────────
# A scan has voxels but no commit_sha; `room commit` has the sha but no voxels. The scan that
# wrote the working tree stages its grid beside it, and the commit indexes that.

def staged_path(room_root: Path | None = None) -> Path:
    """Inside .git, so it is never committed, and one per working tree: a rescan replaces it."""
    return Path(room_root or default_path()) / ".git" / "gitspace" / "voxels.npz"


def stage(grid: VoxelGrid, room_root: Path | None = None,
          claims: dict[str, np.ndarray] | None = None) -> Path:
    """Scan side: keep this scan's grid, with each voxel's owning object_id already resolved
    (claims: object_id -> F_world points, after associate gave them their stable ids)."""
    owner = _claims(grid, claims or {})
    path = staged_path(room_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("voxels.tmp.npz")
    np.savez(tmp, origin=grid.origin, size=grid.size, levels=grid.levels, ijk=grid.ijk, count=grid.count,
             z_lo=grid.z_lo, z_mid=grid.z_mid, z_hi=grid.z_hi,
             owner=np.array([owner.get(r, "") for r in range(len(grid.ijk))], dtype=str))
    tmp.replace(path)
    return path


def index_staged(room_root: Path | None, commit_sha: str, parent_sha: str | None, branch: str,
                 timestamp: str, es=None, spool_dir: Path | None = None, sleep=time.sleep) -> IndexResult:
    """Commit side -- roomctl's post-commit hook (docs/10 GAP 1): index the staged scan's
    voxels under the new commit_sha, or spool them. Nothing staged -> nothing to do."""
    path = staged_path(room_root)
    if not path.is_file():
        return IndexResult(0, 0, "nothing staged")
    z = np.load(path)
    n = 2 ** int(z["levels"])
    occ = np.zeros((n, n, n), bool)
    occ[z["ijk"][:, 0], z["ijk"][:, 1], z["ijk"][:, 2]] = True
    grid = VoxelGrid(z["origin"], float(z["size"]), int(z["levels"]), z["ijk"], z["count"],
                     z["z_lo"], z["z_mid"], z["z_hi"], occ)
    owners = {r: str(o) for r, o in enumerate(z["owner"]) if o}
    room = load_room(room_root)
    check_pinned(grid.cube, room)
    with obs.span("es.index_voxels", commit_sha=commit_sha, n_voxels=len(grid.ijk)) as sp:
        docs = voxel_docs(grid, commit_sha, parent_sha, branch, timestamp, room.get("zones"), owners=owners)
        result = deliver(docs, commit_sha, es, spool_dir, sleep)
        if sp is not None:
            sp.set_data("indexed", result.indexed)
            sp.set_data("spooled", result.spooled)
    path.unlink()                    # indexed or safely spooled: either way it's no longer staged
    return result


def index_voxels(grid: VoxelGrid, commit_sha: str, parent_sha: str | None, branch: str, timestamp: str,
                 es=None, room_root: Path | None = None, claims: dict[str, np.ndarray] | None = None,
                 spool_dir: Path | None = None, sleep=time.sleep) -> IndexResult:
    """Write this commit's voxels to room-voxels, or spool them if Elasticsearch is away.
    _id = "<sha>:<voxel_key>" (elastic/ingest.py's rule), so re-indexing overwrites instead of
    duplicating. Commit time only, never per watch-loop frame (docs/11).

    Raises -- these are bugs, not weather -- if the cube isn't room.yaml's, or if Elasticsearch
    REJECTS a document (the mapping is dynamic: strict; retrying can't fix that).
    """
    room = load_room(room_root)
    check_pinned(grid.cube, room)
    with obs.span("es.index_voxels", commit_sha=commit_sha, n_voxels=len(grid.ijk)) as sp:
        docs = voxel_docs(grid, commit_sha, parent_sha, branch, timestamp, room.get("zones"), claims)
        result = deliver(docs, commit_sha, es, spool_dir, sleep)
        if sp is not None:
            sp.set_data("indexed", result.indexed)
            sp.set_data("spooled", result.spooled)
            if result.reason:
                sp.set_data("spool_reason", result.reason)
    return result


def deliver(docs: list[dict], key: str, es=None, spool_dir: Path | None = None, sleep=time.sleep) -> IndexResult:
    """Bulk-write room-voxels documents, or spool ALL of them under `key` (es_sink)."""
    return es_sink.deliver(INDEX, docs, key, es, spool_dir, sleep)


def flush_spool(es=None, spool_dir: Path | None = None, sleep=time.sleep) -> dict[str, IndexResult]:
    """Replay spooled room-voxels commits, oldest first, as {commit: result} (es_sink)."""
    out = es_sink.flush_spool(es, spool_dir, sleep, indices=(INDEX,))
    return {k.split("/", 1)[1]: v for k, v in out.items()}


def doc_id(d: dict) -> str:
    return es_sink.doc_id(INDEX, d)                       # == elastic/ingest.py natural_id("room-voxels", d)
