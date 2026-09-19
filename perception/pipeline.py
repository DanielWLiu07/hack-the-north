"""One capture, end to end: a recording -> the room repo's working tree. docs/20 stages 2-11.

    depth (+ capture-quality gate) -> fuse (+ floor assertion) -> cluster, inside room.yaml's
    zones -> merge -> voxelize -> associate (+ occlusion by raycast, zones, the miss debounce,
    and .roomignore's path globs) -> serialize, then stage the voxels and the scan's metadata for roomctl's
    post-commit publish. A rejected capture changes nothing, the miss counts included.

With indexing on (`es="env"` or GITSPACE_INDEX_CAPTURES=1) every capture -- quality-rejected
ones too -- also writes what /capture/<id> reads: one room-clouds catalog doc, one
room-observations row per object per camera, and one row per discarded cluster
(`object_id: null`, `rejected_reason` set: docs/11 Gap 1). Both carry the capture's Sentry
trace, and the fused cloud goes to clouds/<capture_id>.ply (its cloud_uri). Off by default,
so tests replay without a network.

All of it runs in one Sentry transaction per capture (inside obs.capture_scope), so
capture_quality's skew_ms / tilt_rate_max / coverage, fuse's floor_z and serialize's
n_changed land as MEASUREMENTS on it -- chartable across every capture -- and each stage is
its own span. Segmentation: per camera, YOLO-seg masks lifted to 3-D (docs/15 Approach A), then
cluster on what no kept mask claimed (Approach B); cluster alone without the model or with
GITSPACE_SEGMENTER=off. VLM descriptions only with GITSPACE_DESCRIBE=1 (API calls).

    python perception/pipeline.py RECORDING --repo ROOM_REPO     # scan into the working tree
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import depth  # noqa: E402  (puts the repo root on sys.path)
import es_sink  # noqa: E402
import fuse  # noqa: E402
import obs  # noqa: E402
import raycast  # noqa: E402
import serialize  # noqa: E402
import voxelize  # noqa: E402
from roomctl import publish  # noqa: E402
from roomctl.repo import Repo  # noqa: E402


@dataclass
class Recording:
    path: Path
    capture_id: str
    at: str                          # "YYYY-MM-DDTHH:MM:SSZ"
    pose: dict                       # BB odometry {x, z, yaw}
    skew_ms: float | None
    tilt_rate_max: float | None
    frames: dict[str, Path]          # camera -> 2560x720 side-by-side image
    calib: dict[str, Path]           # camera -> stereo_calibration_fisheye.yaml
    mounts: dict[str, fuse.Mount]
    source: str = "robot"


def load_recording(path) -> Recording:
    path = Path(path)
    m = json.loads((path / "capture.json").read_text())
    return Recording(path, m["capture_id"], m["at"], m["pose"], m.get("skew_ms"), m.get("tilt_rate_max"),
                     {f["camera"]: path / f["file"] for f in m["frames"]},
                     {c: path / r["calib"] for c, r in m["rig"].items()},
                     {c: fuse.Mount(**r["mount"]) for c, r in m["rig"].items()}, m.get("source", "robot"))


@dataclass
class ScanResult:
    capture_id: str
    ok: bool                                     # passed the capture-quality gate
    verdicts: dict[str, int] = field(default_factory=dict)
    objects: int = 0
    voxels: int = 0
    floor_z: float | None = None
    rejected: int = 0                            # discard-pile rows indexed (docs/11 Gap 1)


@functools.lru_cache(maxsize=8)
def _rig(calib: str) -> depth.StereoDepth:
    return depth.StereoDepth(calib)             # maps + matcher built once per calibration


def open_room(repo_dir, rec: Recording) -> Repo:
    """The room repo; a fresh one is initialised from the room files the recording carries
    (room.yaml -- the pinned cube and zones -- anchors/, .roomignore), as roomctl's init writes."""
    repo = Repo(Path(repo_dir))
    if not repo.exists:
        room = rec.path / "room"
        if not (room / "room.yaml").is_file():
            raise FileNotFoundError(f"{repo.path} is not a room repo and {rec.path} carries no room/room.yaml")
        repo.path.mkdir(parents=True, exist_ok=True)
        repo.git("init", "-q", "-b", "main")
        shutil.copytree(room, repo.path, dirs_exist_ok=True)
    return repo


def in_zones(cloud: np.ndarray, zones: dict | None) -> np.ndarray:
    """Points inside any room.yaml zone box. Objects can only be committed inside a zone, and
    everything else -- far walls, the floor's stereo terraces -- is what the fallback
    clusterer mistakes for objects. The zone boxes reach below their support surface, so the
    surface is there for plane removal."""
    keep = np.zeros(len(cloud), bool)
    for z in (zones or {}).values():
        keep |= ((cloud >= np.asarray(z["min"], float)) & (cloud < np.asarray(z["max"], float))).all(axis=1)
    return cloud[keep]


def scan_into(repo_dir, recording, *, es=None, segmenter=None, describe=None) -> ScanResult:
    """Replay one recording through the pipeline into the working tree of `repo_dir`,
    stabilized against its HEAD. A capture the quality gate rejects leaves the tree alone.
    es: None -> index nothing; "env" -> the live cluster from .env; or a client. Defaults to
    "env" when GITSPACE_INDEX_CAPTURES=1.
    segmenter: an `image -> list[segment.Mask]` callable, or None -> GITSPACE_SEGMENTER
    ("yolo" | "off"; unset = yolo when its weights are in $MODELS_DIR/weights, else off). With
    one, each camera's masks are lifted (docs/15 Approach A) and cluster only sees the pixels
    no KEPT mask claimed (Approach B on the residual). Without: cluster on the whole fused
    cloud.
    describe: VLM text per camera view (describe.py) -- API calls, so off unless True or
    GITSPACE_DESCRIBE=1."""
    if es is None and os.getenv("GITSPACE_INDEX_CAPTURES") == "1":
        es = "env"
    import associate
    import cluster
    import merge
    import segment

    rec = load_recording(recording)
    repo = open_room(repo_dir, rec)
    head = repo.records()
    room = voxelize.load_room(repo.path)
    zones = room.get("zones")
    with obs.capture_scope(rec.capture_id), obs.transaction("perception.scan", f"scan {rec.capture_id}"):
        frames = {c: cv2.imread(str(p)) for c, p in rec.frames.items()}
        rigs = {c: _rig(str(p)) for c, p in rec.calib.items()}
        out, ok = depth.depth_capture(frames, rigs, rec.skew_ms, rec.tilt_rate_max)
        cov = {c: rigs[c].coverage(v) for c, (_, v, _) in out.items()}
        if not ok:
            if es is not None:
                index_capture(capture_docs(rec, out, cov, ok=False), rec.capture_id, es)
            return ScanResult(rec.capture_id, False)
        pose = fuse.odom_to_world(rec.pose)
        _, cloud = fuse.fuse([(xyz, valid, rec.mounts[c]) for c, (xyz, valid, _) in out.items()], robot_pose=pose)
        labels, ignored_paths = segment.roomignore(repo.path)                # docs/25 §6: .roomignore
        seg = _segmenter(segmenter)
        floor = _floor(zones, cloud)
        if floor:
            zones = ensure_floor_zone(zones)
            ignored_paths = tuple(g for g in ignored_paths if g not in FLOOR_GLOBS)
        discarded: list = []
        if seg is None and not floor:
            _, instances = cluster.cluster(in_zones(cloud, zones), rejects=discarded)
        else:
            instances = segment_then_cluster(out, rec.mounts, pose, zones, seg, labels,
                                             describe if describe is not None
                                             else os.getenv("GITSPACE_DESCRIBE") == "1", floor=floor,
                                             rejects=discarded)
        objects = merge.merge(instances)
        grid = voxelize.VoxelGrid.from_points(cloud)
        cameras = [raycast.Camera.from_mount(rec.mounts[c], rigs[c].half_fov_deg(), pose) for c in out]
        misses = associate.load_misses(repo.path)            # a removal needs MISSES_TO_REMOVE in a row
        assocs = associate.associate(objects, head, rec.capture_id,
                                     occluded=raycast.occlusion_check(cameras, grid), now=rec.at,
                                     zones=zones, misses=misses)
        measured, carried = associate.for_serialize(assocs, zones, ignore_paths=ignored_paths)
        # carried: unobserved + missed once; an `added` object under an ignored path gets no file
        serialize.serialize(repo.path, measured, head, carried)
        associate.save_misses(repo.path, associate.next_misses(assocs, misses))   # accepted captures only
        seen = [a for a in assocs if a.obj is not None]
        voxelize.stage(grid, repo.path, claims={a.object_id: a.obj.points for a in seen})
        publish.stage_scan(repo, rec.capture_id, rec.at, {a.object_id: a.obj.object_fields() for a in seen},
                           trace=obs.trace_fields())          # the commit's docs join THIS waterfall (P17)
        if es is not None:
            index_capture(capture_docs(rec, out, cov, ok=True, cloud=cloud, assocs=assocs,
                                       cloud_uri=save_cloud(cloud, rec.capture_id),
                                       rejects=discarded), rec.capture_id, es)
    verdicts: dict[str, int] = {}
    for a in assocs:
        verdicts[a.verdict] = verdicts.get(a.verdict, 0) + 1
    return ScanResult(rec.capture_id, True, verdicts, len(measured), len(grid.ijk),
                      fuse.assert_floor(cloud), len(discarded))


@functools.lru_cache(maxsize=None)
def _yolo():
    import segment
    return segment.YoloSegmenter()      # once per process: the model load is ~2 s


def _segmenter(segmenter):
    """The mask path is ON by default where its model is installed (scripts/bootstrap_laptop.sh
    puts it in $MODELS_DIR/weights). Where it isn't, the fallback runs alone -- said once, in the
    log -- rather than ultralytics downloading weights mid-scan."""
    if segmenter is not None:
        return segmenter
    import segment

    kind = os.getenv("GITSPACE_SEGMENTER", "").strip().lower()
    if kind in ("off", "none"):
        return None
    if kind not in ("", "yolo"):
        raise ValueError(f"GITSPACE_SEGMENTER={kind!r}: want yolo or off")
    if not kind and not Path(segment._weights(segment.WEIGHTS)).is_file():
        _warn_once(f"mask path off: no {segment.WEIGHTS} in $MODELS_DIR/weights -- geometry only")
        return None
    return _yolo()


DEFAULT_FLOOR_ZONE = {"min": [-4.0, -4.0, -0.05], "max": [4.0, 4.0, 0.35], "surface": 0.0}
FLOOR_CLOUD_FRAC = 0.03              # fused-cloud share sitting in desk/shelf: a hallway looking
                                     # at the floor is ~0; the synthetic desk is ~7%. Below this,
                                     # run the floor path even if room.yaml has no floor zone.
FLOOR_GLOBS = frozenset({"zones/floor/**", "zones/floor/*"})


def _reaches_floor(z: dict) -> bool:
    return float(z["min"][2]) <= 0.05 and float(z["max"][2]) > 0


def ensure_floor_zone(zones: dict | None) -> dict:
    """A box that can hold a can. In-memory only: room.yaml stays pinned. Without it keep()
    and for_serialize drop every floor instance (centroid at z ~ 6 cm is in no desk)."""
    zones = dict(zones or {})
    if any(_reaches_floor(z) for z in zones.values()):
        return zones
    zones["floor"] = dict(DEFAULT_FLOOR_ZONE)
    return zones


def _floor(zones=None, cloud: np.ndarray | None = None) -> bool:
    """The floor-object path is automatic for floor zones, and for a capture whose fused
    cloud is not on the desk (a hallway recording still ships the desk-only room.yaml).
    GITSPACE_FLOOR=off disables it. What it finds -- a can, a packet: things under
    cluster.py's resolution -- can only be committed where a zone reaches the floor
    (`ensure_floor_zone` supplies one for the scan)."""
    kind = os.getenv("GITSPACE_FLOOR", "").strip().lower()
    if kind in ("0", "off", "no"):
        return False
    if kind in ("1", "on", "yes"):
        return True
    if kind not in ("",):
        raise ValueError(f"GITSPACE_FLOOR={kind!r}: want 1 or off")
    if zones is not None and any(_reaches_floor(z) for z in zones.values()):
        return True
    if cloud is not None and len(cloud) and zones:
        elevated = {n: z for n, z in zones.items() if float(z["min"][2]) > 0.05}
        if elevated and len(in_zones(cloud, elevated)) / len(cloud) < FLOOR_CLOUD_FRAC:
            return True
    return zones is None


@functools.lru_cache(maxsize=None)
def _warn_once(msg: str) -> None:
    import logging
    logging.getLogger("pipeline").warning(msg)


def segment_then_cluster(out: dict, mounts: dict, pose, zones: dict | None, seg, ignore, describe_views=False,
                         floor=None, rejects=None):
    """docs/15's recommended pipeline: per camera, masks -> xyz[mask & valid] -> F_world
    (Approach A); then cluster only what no KEPT mask claimed (Approach B, `unknown`).

    Kept = cluster's own size window (MIN_EXTENT..MAX_EXTENT, so YOLO's "dining table" -- the
    desk itself -- is not an object) with its centre inside a zone, the same place rule
    in_zones applies to the fallback. A rejected mask gives its pixels back to the fallback,
    so the book on that table is still found.

    Unless GITSPACE_FLOOR=off, each camera also runs the floor-object path, for things too small for
    both: a 5.3 cm can 1.3 m out is 8 x 19 px, which YOLO does not see and cluster.py's 1 cm
    voxels and MIN_CLUSTER_PTS erase. Same `keep` rule after it, so a floor zone is what lets
    one be committed."""
    import cluster
    import segment

    def keep(inst) -> bool:
        return (cluster.MIN_EXTENT < inst.box()[1].max() < cluster.MAX_EXTENT
                and len(in_zones(inst.centroid[None], zones)) > 0)

    floor = _floor(zones) if floor is None else floor
    # Floor geometry does not depend on an installed image model.
    if seg is None:
        seg = lambda image: []
    found, rest, views = [], [], []
    for cam, (xyz, valid, left) in out.items():
        inst, r = segment.run(xyz, valid, left, cam, seg, mount=mounts[cam], robot_pose=pose,
                              ignore=ignore, keep=keep, floor=floor, rejects=rejects)
        found += inst
        rest.append(r)
        views += [(i, left) for i in inst]
    # Floor pixels rejected by the noise-aware detector must not reappear as desk
    # clusters: blank-floor disparity streaks otherwise become dozens of objects.
    fallback_zones = ({name: z for name, z in (zones or {}).items() if float(z["min"][2]) > 0.05}
                      if floor else zones)
    _, fallback = cluster.cluster(in_zones(np.concatenate(rest) if rest else np.empty((0, 3)), fallback_zones),
                                  rejects=rejects)
    if describe_views and views:
        import describe
        describe.describe(views)
    return found + fallback


CLOUDS = Path(__file__).resolve().parents[1] / "clouds"   # .gitignore: clouds/*.ply


def save_cloud(cloud: np.ndarray, capture_id: str) -> str:
    """The fused F_world cloud as binary PLY (float32 x y z) -> its file:// cloud_uri. The bytes
    live on disk; room-clouds is the catalog (docs/11 "the blob/catalog split")."""
    CLOUDS.mkdir(exist_ok=True)
    path = CLOUDS / f"{capture_id}.ply"
    pts = np.ascontiguousarray(cloud, dtype="<f4")
    header = (f"ply\nformat binary_little_endian 1.0\nelement vertex {len(pts)}\n"
              "property float x\nproperty float y\nproperty float z\nend_header\n")
    with open(path, "wb") as f:
        f.write(header.encode("ascii"))
        f.write(pts.tobytes())
    return path.as_uri()


def capture_docs(rec: Recording, out: dict, cov: dict, ok: bool, cloud: np.ndarray | None = None,
                 assocs=None, cloud_uri: str | None = None, rejects=()) -> tuple[dict, list[dict]]:
    """What /capture/<id> reads: the room-clouds catalog doc and the room-observations rows
    (elastic/mappings, dynamic: strict; fake/README "The documents"). Built INSIDE the capture's
    transaction, so both carry its Sentry trace -- the page's "Open in Sentry" is this scan.

    `rejects` are Instance rows with rejected_reason set. Indexed with object_id null (the
    discard pile); each row gets its own millisecond so TSDS identity does not collide
    (rejected rows all share object_id null + camera).

    `assocs` None means NOBODY SCANNED this capture -- it was catalogued (index_recording) or
    rejected by the gate -- and the doc then carries no `objects` at all. A scan passes its
    list, empty included: `objects: 0` is "I looked and found nothing", which a cloud doc
    could not say before, so four honest hallway captures read as a broken writer instead.
    """
    import merge
    trace = obs.trace_fields()
    doc = {"@timestamp": rec.at, "capture_id": rec.capture_id, "cameras": sorted(out),
           "coverage_pct": round(float(np.mean(list(cov.values()))), 4) if cov else None,
           "skew_ms": rec.skew_ms, "tilt_rate_max": rec.tilt_rate_max, "quality_ok": bool(ok),
           "cloud_uri": cloud_uri, **trace}
    if cloud is not None and len(cloud):
        lo, hi = cloud.min(axis=0), cloud.max(axis=0)
        doc["point_count"] = int(len(cloud))
        doc["bounds"] = {"min": dict(zip("xyz", map(float, lo))), "max": dict(zip("xyz", map(float, hi)))}
    else:
        doc["point_count"] = int(sum(int(v.sum()) for _, v, _ in out.values()))
    if assocs is not None:
        doc["objects"] = sum(a.obj is not None for a in assocs)
    assocs = assocs or ()
    bodies = [(a.object_id, row) for a in assocs if a.obj is not None for row in a.obj.observations()]
    bodies += [(None, merge.observation_row(i)) for i in rejects if i.rejected_reason]
    t0 = datetime.fromisoformat(rec.at.replace("Z", "+00:00"))
    rows = []
    for i, (oid, row) in enumerate(bodies):
        t = t0 + timedelta(milliseconds=i)
        ts = t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"
        rows.append({"@timestamp": ts, "capture_id": rec.capture_id, "object_id": oid, **row, **trace})
    return {k: v for k, v in doc.items() if v is not None}, rows


def index_capture(docs: tuple[dict, list[dict]], capture_id: str, es) -> dict[str, es_sink.IndexResult]:
    """Write the capture's catalog doc and observations; spooled if the cluster is away."""
    cloud_doc, rows = docs
    client = None if es == "env" else es
    with obs.span("es.index_capture", capture_id=capture_id, observations=len(rows)) as sp:
        out = {"room-clouds": es_sink.deliver("room-clouds", [cloud_doc], capture_id, client),
               "room-observations": es_sink.deliver("room-observations", rows, capture_id, client)}
        if sp is not None:
            sp.set_data("spooled", sum(r.spooled for r in out.values()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording", type=Path)
    ap.add_argument("--repo", type=Path, required=True, help="the room repository (created if absent)")
    a = ap.parse_args()
    r = scan_into(a.repo, a.recording)
    print(json.dumps(vars(r)))
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
