"""Primary path: instance masks on left_rect, lifted to 3-D. Stages 5-6 of docs/20.

depth.py's xyz is (H,W,3) and aligned pixel-for-pixel with left_rect, so a 2-D mask is
already a 3-D point selection:

    pts = xyz[mask & valid]

That is the whole lift. What this file adds around it:
  - masks shrink by ERODE_PX first. Mask edges overshoot onto whatever is behind or
    beside the object, and on a mug against a book those pixels are book.
  - points far from the mask's median depth are dropped (background seen through the
    edge, SGBM flying pixels).
  - IGNORE_LABELS (.roomignore at the mask stage): `person` never becomes an object.

Touching objects come back as two instances because the image model separated them,
which geometry (cluster.py) cannot do.

Segmenter: YOLO-seg as in Bracket Bot's examples/example_segmentation.py (yolo11s-seg,
conf 0.25, iou 0.45). SAM 3 fits the same interface, `image -> list[Mask]`.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from cluster import Instance

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))    # obs.py lives at the repo root
import obs  # noqa: E402

WEIGHTS = "yolo11s-seg.pt"           # BB example_segmentation.py. Bare name on purpose: the
                                     # bootstrap points ultralytics at $MODELS_DIR/weights
CONF, IOU = 0.25, 0.45               # BB example_segmentation.py
MIN_POINTS = 150                     # fewer valid depth pixels than this: don't trust it
ERODE_PX_AT_1280 = 5                 # mask shrink, in px of a 1280-wide image: scales with width,
                                     # since YOLO masks are upsampled from model resolution. 480 wide
                                     # (old stereo) -> 2, 960 (stereo at 0.75) -> 4, RealSense 640 -> 2
MAX_DEPTH_SPREAD = 0.30              # m from the mask's median depth (F_rect Z)
IGNORE_LABELS = frozenset({"person"})
MIN_CROP_PX = 100                    # a map object the frame sees less of than this gets no crop...
MIN_CROP_SEEN = 0.5                  # ...or less than this share of its box: a sliver round an occluder
                                     # would crop the OCCLUDER, and the VLM would describe that


@dataclass
class Mask:
    mask: np.ndarray    # (H,W) bool, same size as left_rect
    label: str
    score: float


class YoloSegmenter:
    """image -> list[Mask], with masks at the image's own resolution."""

    def __init__(self, weights: str | None = None):
        from ultralytics import YOLO  # heavy; only when a real model is wanted

        self.model = YOLO(weights or _weights(WEIGHTS))

    def __call__(self, image: np.ndarray) -> list[Mask]:
        r = self.model(source=image, conf=CONF, iou=IOU, retina_masks=True, verbose=False)[0]
        if r.masks is None:
            return []
        h, w = image.shape[:2]
        out = []
        for m, cls, score in zip(r.masks.data.cpu().numpy(), r.boxes.cls.tolist(), r.boxes.conf.tolist()):
            if m.shape != (h, w):     # retina_masks should prevent this; a misaligned mask lifts the wrong points
                m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
            out.append(Mask(m > 0.5, r.names[int(cls)], float(score)))
        return out


def _weights(name: str) -> str:
    """$MODELS_DIR/weights/<name> when the bootstrap put it there (scripts/bootstrap_laptop.sh),
    else the bare name, which ultralytics resolves through its settings -- or DOWNLOADS into the
    working directory when .env's settings aren't loaded (a test run, say)."""
    p = Path(os.path.expanduser(os.getenv("MODELS_DIR", "~/.cache/gitspace/models"))) / "weights" / name
    return str(p) if p.is_file() else name


def roomignore(repo_dir) -> tuple[frozenset[str], tuple[str, ...]]:
    """<repo>/.roomignore -> (labels that never become objects, path globs never committed).

    docs/25 §6. A line with a '/' or a '*' is a path glob (`zones/floor/**`); any other line is
    a segmenter label (`person`, `robot`, `cable`). `person` is always ignored, file or not."""
    p = Path(repo_dir) / ".roomignore"
    labels, paths = set(IGNORE_LABELS), []
    for line in (p.read_text().splitlines() if p.is_file() else []):
        line = line.split("#", 1)[0].strip()
        if line:
            (paths.append(line) if ("/" in line or "*" in line) else labels.add(line.lower()))
    return frozenset(labels), tuple(paths)


def lift(xyz: np.ndarray, valid: np.ndarray, masks: list[Mask], camera: str,
         image: np.ndarray | None = None, ignore: frozenset[str] = IGNORE_LABELS) -> list[Instance]:
    """Stage 6: masks -> per-camera instances, points in F_rect.

    `xyz` must be depth.py's F_rect array, NOT fuse's world array: the edge filter reads
    column 2 as range from the camera, which in F_world is height. For F_world instances
    call run(..., mount=...), which lifts here and then applies fuse.rect_to_world.
    `image` is left_rect (BGR); when given, each instance gets its dominant colour.
    """
    if xyz.shape[:2] != valid.shape:
        raise ValueError(f"xyz {xyz.shape} and valid {valid.shape} are not aligned")
    e = erode_px(valid.shape[1])
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * e + 1, 2 * e + 1))
    out = []
    for m in masks:
        if m.mask.shape != valid.shape:
            raise ValueError(f"mask {m.mask.shape} is not aligned with xyz {valid.shape}")
        if m.label.lower() in ignore:
            continue
        core = cv2.erode(m.mask.astype(np.uint8), kernel).astype(bool)
        sel = core & valid
        if sel.sum() < MIN_POINTS:            # thin object: erosion ate it, use the raw mask
            sel = m.mask & valid
        pts = xyz[sel]
        pts = pts[np.abs(pts[:, 2] - np.median(pts[:, 2])) < MAX_DEPTH_SPREAD] if len(pts) else pts
        if len(pts) < MIN_POINTS:
            continue
        out.append(Instance(points=pts.astype(np.float64), label=m.label, source="segment",
                            camera=camera, mask=m.mask, score=m.score,
                            color=_dominant_color(image, core if core.any() else m.mask)
                            if image is not None else None))
    return out


def erode_px(width: int) -> int:
    return max(1, round(ERODE_PX_AT_1280 * width / 1280))


def _dominant_color(image: np.ndarray, mask: np.ndarray) -> str:
    """Median BGR under the mask as "#rrggbb". Median, so a specular highlight can't move it."""
    b, g, r = np.median(image[mask].reshape(-1, image.shape[2])[:, :3], axis=0).astype(int)
    return f"#{r:02x}{g:02x}{b:02x}"


def residual(xyz: np.ndarray, valid: np.ndarray, masks: list[Mask]) -> np.ndarray:
    """Valid points under no mask, F_rect: the fallback path's input (fuse, then cluster()).

    Uses the full masks, not the eroded ones, so an object's edge pixels don't come back
    as a second, `unknown` object.
    """
    claimed = np.zeros(valid.shape, bool)
    for m in masks:
        claimed |= m.mask
    return xyz[valid & ~claimed]


def run(xyz: np.ndarray, valid: np.ndarray, left_rect: np.ndarray, camera: str,
        segmenter=None, mount=None, *, robot_pose=None,
        ignore: frozenset[str] = IGNORE_LABELS, keep=None) -> tuple[list[Instance], np.ndarray]:
    """One camera: segment left_rect, lift. -> (instances, residual points).

    Both are F_rect as given, or F_world when `mount` (this rig's fuse.Mount) is passed --
    the frame merge.py and cluster.cluster() expect. Then `robot_pose` is required: the
    SAME F_world (x, y, yaw) that fuse.fuse() got, fuse.odom_to_world(capture["pose"]).
    Defaulting it to the origin would agree with the fused cloud only until the robot
    moves. The masks stay on the instances in pixels either way, for describe.py.

    `keep(instance) -> bool`, applied after the move to F_world, rejects instances (the
    pipeline passes cluster's size window). A rejected mask hands its pixels BACK to the
    residual: YOLO's "dining table" mask covers everything standing on the table, and
    claiming those pixels would hide the book from the fallback. Only kept instances and
    ignored labels (people, the arm: never objects by any path) are cut from the residual.
    """
    if left_rect.shape[:2] != valid.shape:
        raise ValueError(f"left_rect {left_rect.shape} and xyz {valid.shape} are not aligned")
    if mount is not None and robot_pose is None:
        raise ValueError("segment.run(mount=...) needs robot_pose: pass fuse.odom_to_world(capture['pose']), "
                         "the pose fuse.fuse() used, or instances and cloud disagree once the robot moves")
    segmenter = segmenter or YoloSegmenter()
    with obs.span("perception.segment", f"masks {camera}", camera=camera) as sp:
        masks = segmenter(left_rect)
        instances = lift(xyz, valid, masks, camera, left_rect, ignore)
        if mount is not None:
            from fuse import rect_to_world   # the one place frames change (docs/20 Part 2)

            for inst in instances:
                inst.points = rect_to_world(inst.points, mount, robot_pose)
        if keep is None:
            rest = residual(xyz, valid, masks)
        else:
            instances = [i for i in instances if keep(i)]
            claimed = [Mask(i.mask, i.label, i.score or 0.0) for i in instances]
            rest = residual(xyz, valid, claimed + [m for m in masks if m.label.lower() in ignore])
        if mount is not None:
            rest = rect_to_world(rest, mount, robot_pose)
        if sp is not None:
            sp.set_data("masks", len(masks))
            sp.set_data("instances", len(instances))
            sp.set_data("ignored", sum(m.label.lower() in ignore for m in masks))
            sp.set_data("residual_points", len(rest))
    return instances, rest


# ── the robot's map (bb_source): name voxel clusters from the robot's own camera frame ──────
LABEL_MIN_IOU = 0.3      # a mask names a candidate only if it covers this much of its visible footprint


def _box_corners(centre, extents, yaw_deg) -> np.ndarray:
    """The 8 corners (room frame, Z up) of an upright box: extents[0] along the yaw axis."""
    c, s = np.cos(np.radians(yaw_deg)), np.sin(np.radians(yaw_deg))
    hx, hy, hz = np.asarray(extents, float) / 2
    local = np.array([[x, y, z] for x in (-hx, hx) for y in (-hy, hy) for z in (-hz, hz)])
    rot = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    return local @ rot.T + np.asarray(centre, float)


def project_footprints(candidates, shape, K: np.ndarray, room_to_cam: np.ndarray) -> np.ndarray:
    """(H,W) int map: which candidate each pixel SEES, -1 for none. Candidates are painted far to
    near, so a nearer one hides what stands behind it -- the thing behind the book must not
    inherit the book's mask. `room_to_cam` is 4x4, room frame -> the camera's optical frame
    (X right, Y down, Z fwd); `K` the 3x3 intrinsics of the image the masks come from."""
    h, w = shape
    owner = np.full((h, w), -1, int)
    depth = []
    for k, cand in enumerate(candidates):
        pts = np.c_[_box_corners(cand.centroid, cand.extents, getattr(cand, "yaw_axis_deg", 0)), np.ones(8)]
        cam = (room_to_cam @ pts.T).T[:, :3]
        depth.append(np.median(cam[:, 2]))
        if (cam[:, 2] <= 0.05).any():
            depth[-1] = -1.0                                      # behind or through the lens: skip
    for k in sorted(range(len(candidates)), key=lambda k: -depth[k]):
        if depth[k] <= 0:
            continue
        cand = candidates[k]
        pts = np.c_[_box_corners(cand.centroid, cand.extents, getattr(cand, "yaw_axis_deg", 0)), np.ones(8)]
        cam = (room_to_cam @ pts.T).T[:, :3]
        uv = (K @ cam.T).T
        uv = uv[:, :2] / uv[:, 2:3]
        hull = cv2.convexHull(np.round(uv).astype(np.int32))
        paint = np.zeros((h, w), np.uint8)
        cv2.fillConvexPoly(paint, hull, 1)
        owner[paint.astype(bool)] = k
    return owner


def label_candidates(candidates, image: np.ndarray, K: np.ndarray, room_to_cam: np.ndarray, segmenter=None,
                     ignore: frozenset[str] = IGNORE_LABELS) -> list[tuple[str, float | None, Mask | None]]:
    """plan/roommate/03-interfaces.md §4 step 2: labels for bb_source candidates from the robot's
    latest frame. -> one (label, score, mask) per candidate, in order; ("unknown", None, None)
    when no mask covers enough of what the camera sees of it.

    Each candidate's upright box is projected (far to near, so occluders win), and candidates
    and masks are matched one-to-one on IoU of the VISIBLE footprint. An ignored label
    (.roomignore: person, robot, cable) names nothing."""
    from scipy.optimize import linear_sum_assignment

    out = [("unknown", None, None)] * len(candidates)
    if not candidates:
        return out
    masks = [m for m in (segmenter or YoloSegmenter())(image) if m.label.lower() not in ignore]
    if not masks:
        return out
    owner = project_footprints(candidates, image.shape[:2], K, room_to_cam)
    iou = np.zeros((len(candidates), len(masks)))
    for k in range(len(candidates)):
        seen = owner == k
        n = seen.sum()
        if not n:
            continue
        for j, m in enumerate(masks):
            inter = np.logical_and(seen, m.mask).sum()
            iou[k, j] = inter / (n + m.mask.sum() - inter)
    rows, cols = linear_sum_assignment(-iou)
    for k, j in zip(rows, cols):
        if iou[k, j] >= LABEL_MIN_IOU:
            out[k] = (masks[j].label, masks[j].score, masks[j])
    return out


def label_map_objects(objects, candidates, image: np.ndarray, K: np.ndarray, room_to_cam: np.ndarray,
                      segmenter=None, ignore: frozenset[str] = IGNORE_LABELS) -> None:
    """label_candidates, applied: each map object's one view (objects[k] is candidates[k]) gets
    the frame's label and score, and a pixel mask on `image` for describe.py to crop -- the
    segmenter's mask when one matched, else the part of its box the camera sees, so an object
    the model can't name still gets words. One the frame doesn't see well enough (mostly behind
    something, out of view: under MIN_CROP_PX or MIN_CROP_SEEN of its box) keeps "unknown" and
    no mask. The view's camera stays the map's."""
    named = label_candidates(candidates, image, K, room_to_cam, segmenter, ignore)
    shape = image.shape[:2]
    seen = project_footprints(candidates, shape, K, room_to_cam)
    for k, (obj, cand, (label, score, m)) in enumerate(zip(objects, candidates, named)):
        view = obj.views[0]
        view.label, view.score = label, score
        own = seen == k
        whole = (project_footprints([cand], shape, K, room_to_cam) == 0).sum()
        crop_ok = own.sum() >= MIN_CROP_PX and own.sum() >= MIN_CROP_SEEN * whole
        view.mask = m.mask if m is not None else (own if crop_ok else None)
