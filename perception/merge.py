"""Cross-camera merge in F_world. Stage 8 of docs/20.

Each camera yields its own instances, so one mug arrives up to three times. Two instances
are the same physical object when (docs/15):

    centroid distance < MERGE_DIST
    AND (labels agree OR description embeddings are close)
    AND their boxes overlap

plus two rules of our own:
  - never two instances from the SAME camera. The segmenter already separated those, and
    a mug touching a book must stay two objects.
  - complete linkage: every pair inside a group must pass, so A~B and B~C can't chain
    A and C together when they are 25 cm apart.

A MergedObject keeps every view it was built from, and each view keeps its own
description and its own unquantized centroid. Nothing here picks one description. The
/capture/<id> page renders exactly that per-camera disagreement, from observations().
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cluster import Instance

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))    # obs.py lives at the repo root
import obs  # noqa: E402

MERGE_DIST = 0.15      # m, centroid to centroid
EMBED_COS = 0.85       # description embeddings this close count as agreeing labels
BOX_MARGIN = 0.02      # m. each camera sees a different side, so boxes only just touch
UNKNOWN = "unknown"


@dataclass
class MergedObject:
    """One physical object and every camera view of it. Points are F_world."""
    views: list[Instance]

    @property
    def points(self) -> np.ndarray:
        return np.vstack([v.points for v in self.views])

    @property
    def centroid(self) -> np.ndarray:
        return self.points.mean(axis=0)

    def box(self, yaw: float | None = None) -> tuple[np.ndarray, np.ndarray, float]:
        """Upright box over all views' points; see Instance.box()."""
        return Instance(points=self.points).box(yaw)

    def footprint_aspect(self) -> float:
        return Instance(points=self.points).footprint_aspect()

    @property
    def cameras(self) -> list[str | None]:
        return [v.camera for v in self.views]

    @property
    def labels(self) -> list[str]:
        """Every view's segmenter label, in view order, disagreements included."""
        return [v.label for v in self.views]

    @property
    def descriptions(self) -> list:
        """Every view's describe.ViewDescription (None where a view has none). Not reconciled."""
        return [v.description for v in self.views]

    @property
    def color(self) -> str | None:
        """Dominant colour of the best-covered view that has one."""
        seen = [v for v in self.views if getattr(v, "color", None)]
        return max(seen, key=lambda v: len(v.points)).color if seen else None

    def observations(self) -> list[dict]:
        """One room-observations row per view: this camera's own centroid and description.

        Keys are the contract's (fake/README.md "The documents"; the mapping is dynamic:
        strict) and every one is present, null when unknown: web/object_api reads
        `s["confidence"]` directly. vlm_model / label_attempt only when there is a
        description, as the fake does. associate.observation_docs() adds @timestamp,
        capture_id, object_id and the Sentry join.
        """
        return [observation_row(v) for v in self.views]

    def object_fields(self) -> dict:
        """The room-objects fields only perception knows (the rest -- commit_sha, pose, ... --
        come from the committed record). Same arithmetic as fake/scene_gen.py: observed_by
        sorted, confidence the mean of the views' scores, point_count the sum. raw_description
        is every view's words, unreconciled; vlm_model says who wrote them (elastic/records.py
        META_FIELDS), so real words are never mistaken for scene_gen's scripted ones. Null
        without words; comma-joined in the one case two models described one object."""
        scores = [v.score for v in self.views if v.score is not None]
        worded = [d for d in self.descriptions if getattr(d, "text", None)]
        models = sorted({d.model for d in worded if getattr(d, "model", None)})
        return {
            "observed_by": sorted({v.camera or "fused" for v in self.views}),
            "raw_description": [d.text for d in worded],
            "confidence": round(sum(scores) / len(scores), 3) if scores else None,
            "point_count": int(sum(len(v.points) for v in self.views)),
            "vlm_model": ",".join(models) or None,
        }


def observation_row(v: Instance) -> dict:
    """One room-observations body (no @timestamp / capture_id / object_id).

    Kept views have `rejected_reason: null`. A discard-pile Instance carries the reason
    (`too_small`, `plane_fragment`, `no_depth`, `roomignore:person`, …) and the pipeline
    indexes it with `object_id: null` so /capture shows the honest mess (docs/11 Gap 1).
    """
    c = v.centroid if len(v.points) else np.zeros(3)
    d = v.description
    text = getattr(d, "text", None)
    return {
        "camera": v.camera or "fused",
        "confidence": None if v.score is None else round(float(v.score), 3),
        "point_count": int(len(v.points)),
        "raw_x": float(c[0]), "raw_y": float(c[1]), "raw_z": float(c[2]),
        "occluded": False,
        "rejected_reason": v.rejected_reason,
        "raw_description": text,
        "raw_label": v.label,
        "vlm_model": getattr(d, "model", None) if text else None,
        "label_attempt": getattr(d, "attempt", None) if text else None,
    }


def merge(instances: list[Instance], embed=None) -> list[MergedObject]:
    """F_world instances from all cameras -> merged objects, in first-member order.

    `embed(text) -> vector` is optional. Without it, labels must agree (or one side is
    `unknown`, the fallback path's absence of a label, which never disagrees).
    """
    n = len(instances)
    with obs.span("perception.merge", "cross-camera merge", instances=n) as sp:
        vecs = _embeddings(instances, embed)
        lo, hi = zip(*[_aabb(i.points) for i in instances]) if n else ((), ())
        cents = [i.centroid for i in instances]

        ok = np.zeros((n, n), bool)
        pairs = []
        for a in range(n):
            for b in range(a + 1, n):
                if _same_object(instances[a], instances[b], cents[a], cents[b],
                                lo[a], hi[a], lo[b], hi[b], vecs[a], vecs[b]):
                    ok[a, b] = ok[b, a] = True
                    pairs.append((float(np.linalg.norm(cents[a] - cents[b])), a, b))

        group = list(range(n))                    # group id per instance
        members = {g: [g] for g in range(n)}
        for _, a, b in sorted(pairs):             # closest first; ties by index
            ga, gb = group[a], group[b]
            if ga == gb:
                continue
            A, B = members[ga], members[gb]
            if {instances[i].camera for i in A} & {instances[i].camera for i in B}:
                continue                          # one view per camera
            if not all(ok[i, j] for i in A for j in B):
                continue                          # complete linkage
            keep, drop = min(ga, gb), max(ga, gb)
            members[keep] = sorted(A + B)
            for i in members.pop(drop):
                group[i] = keep

        out = [MergedObject([instances[i] for i in members[g]]) for g in sorted(members)]
        if sp is not None:
            sp.set_data("objects", len(out))
            sp.set_data("multi_view", sum(len(o.views) > 1 for o in out))
    return out


def _same_object(a: Instance, b: Instance, ca, cb, lo_a, hi_a, lo_b, hi_b, va, vb) -> bool:
    if a.camera == b.camera:
        return False
    if np.linalg.norm(ca - cb) >= MERGE_DIST:
        return False
    if np.any(lo_a - BOX_MARGIN > hi_b) or np.any(lo_b - BOX_MARGIN > hi_a):
        return False
    if UNKNOWN in (a.label, b.label) or a.label == b.label:
        return True
    return va is not None and vb is not None and float(va @ vb) >= EMBED_COS


def _aabb(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = np.percentile(points, [1, 99], axis=0)
    return lo, hi


def _embeddings(instances: list[Instance], embed) -> list[np.ndarray | None]:
    out = []
    for i in instances:
        text = getattr(i.description, "text", None)
        if embed is None or not text:
            out.append(None)
            continue
        v = np.asarray(embed(text), dtype=np.float64)
        out.append(v / (np.linalg.norm(v) or 1.0))
    return out
