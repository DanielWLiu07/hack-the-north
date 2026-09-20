"""Stage 10: carry stable object_ids forward. The decision table in docs/20 Part 4.

For each merged object in this scan, against HEAD:

    matched, moved < 1.5 quanta        -> unchanged    serialize keeps the file byte-identical
    matched, moved >= 1.5 quanta       -> moved
    no HEAD match, history match       -> returned     REUSE the old id
    no match anywhere                  -> added        new_id(), once

and for each HEAD object nobody matched:

    cameras had line of sight          -> removed      file deleted
    occluder in the way                -> unobserved   carried forward unchanged

HEAD matching is Hungarian on distance, hard-gated at 1.5 m. The `returned` row can't be
done that way: an object that left and came back has nothing in HEAD to match, so it
takes a hybrid search over every historical appearance (ESHistory.reidentify, docs/11
"2. Object re-identification across absences"). The same search catches an object that
moved more than 1.5 m within one commit, which would otherwise be a delete plus an add.

class, color and first_seen are set at first sight and carried forward, never
re-measured (roomctl/state.py). Geometry here is unquantized; serialize.stabilize()
quantizes it against `previous`.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

from cluster import ROUND_ASPECT
from keys import credential

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))    # obs.py and roomctl/ live at the repo root
import obs  # noqa: E402
from roomctl.state import (HYST, Q_POS, Q_YAW, YAW_PERIOD, Extents, ObjectRecord, Pose,  # noqa: E402
                           iso_utc, new_id, settle)

log = logging.getLogger(__name__)

GATE = 1.5                 # m. no HEAD match beyond this (docs/20 Part 4)
LABEL_PENALTY = 0.30       # m-equivalent. labels flicker, so disagreeing is a cost, not a veto
EXTENT_RATIO = 2.0         # a thing twice the size in any dimension is a different thing
# Reranker scores are NOT calibrated: live (serverless 9.6, jina-rerank) on real gpt-5 descriptions,
# the right object scored 1.03-1.90 and the best WRONG one 1.07-1.92, and the top1-top2 margins
# overlap too. So the score only RANKS; geometry and colour decide. A candidate needs score > 0
# (the stand-in histories use 0 for "no match at all").
RETURN_MIN_SCORE = float(os.getenv("REID_MIN_SCORE", "0"))
COLOR_VETO = 20.0          # CIELAB dE76. live kitchen views: same object <= 14.1 across +-20%
                           # exposure; different objects median 29 (but dark ones overlap)
YAW_BAND = (1.1, 1.35)     # footprint aspect around ROUND_ASPECT where yaw flips between scans
UNKNOWN = "unknown"
FALLBACK_COLOR = "#808080" # cluster-path objects have no image to take a colour from
ZONE_HYST = 0.03           # m. stay in the committed zone until this far outside its box, or an
                           # object on a boundary renames its file (= a phantom diff) every scan

UNCHANGED, MOVED, ADDED, RETURNED, REMOVED, UNOBSERVED, MISSED = (
    "unchanged", "moved", "added", "returned", "removed", "unobserved", "missed")
CARRIED = (UNOBSERVED, MISSED)   # not seen this scan, file kept byte-identical
MISSES_TO_REMOVE = 2       # consecutive unexplained misses before `removed` (docs/10 P15: a small
                           # object missed in 3 of 11 rescans deleted its file each time; 2 -> 0/11)


@dataclass
class Association:
    verdict: str
    object_id: str
    cls: str
    color: str
    first_seen: str
    zone: str | None                   # carried; None for `added` (zone assignment decides)
    obj: object | None = None          # merge.MergedObject; None for removed / unobserved
    previous: ObjectRecord | None = None   # HEAD's record, or the historical one for `returned`
    centre: np.ndarray | None = None   # unquantized box, F_world
    extents: np.ndarray | None = None
    yaw: float | None = None           # [0, 180), the committed one when the aspect is ambiguous
    note: str | None = None            # set when a verdict rests on less evidence than usual
    occluded: bool = False             # unobserved BECAUSE something blocked the line of sight


@dataclass
class Candidate:
    object_id: str
    score: float
    record: ObjectRecord


class HistoryUnavailable(RuntimeError):
    """The history search can't answer this scan: down, unauthorised, or rejected the query."""


def associate(objects: list, head: dict[str, ObjectRecord], capture_id: str,
              history=None, occluded=None, now: str | None = None,
              zones: dict | None = None, misses: dict[str, int] | None = None,
              fresh=None) -> list[Association]:
    """This scan's merged objects + HEAD's records -> one Association per object, by id.

    history:  has reidentify(obj, exclude) -> [Candidate]; None disables `returned`.
    occluded: (ObjectRecord) -> bool, True when something blocks every camera's view of
              its last pose. None means no occlusion model: every unseen object is removed.
    now:      first_seen for `added` objects, "YYYY-MM-DDTHH:MM:SSZ". Defaults to now.
    zones:    room.yaml's zones, as for_serialize gets them: `moved` includes a zone change.
    misses:   consecutive unexplained misses so far, by id (load_misses). An unseen, unoccluded
              object is `missed` (kept) until MISSES_TO_REMOVE in a row, then `removed`.
              None: removed at the first miss. Save next_misses() for the next scan.
    fresh:    (ObjectRecord) -> bool, True when the map has freshly re-observed the 0.5 m block
              around its last pose (bb_source.fresh_blocks). None: every scan is fresh -- a stereo
              capture IS a fresh look. A miss counts ONLY if the block is fresh AND the pose is in
              line of sight; anything else is `unobserved`, carried forward: hidden is not gone.
              A removal already confirmed (MISSES_TO_REMOVE fresh, visible misses) stays removed.

    unchanged vs moved is roomctl.state.settle()'s call exactly, so the verdict can never
    disagree with the file serialize writes.

    When the history search fails (HistoryUnavailable) the scan still associates against
    HEAD, but nothing can be `returned`, so every `added` object carries a `note` saying
    it may be a returning object with a fresh id. Whether to commit that is the caller's
    call (roomctl): a new id for a returning object breaks its history for good.
    """
    now = now or iso_utc(datetime.now(timezone.utc))
    history_error = None
    heads = sorted(head.values(), key=lambda r: r.id)
    with obs.span("es.associate", "re-identify", objects=len(objects), head=len(heads)) as sp:
        boxes = [o.box() for o in objects]
        pairs = _match_head(objects, boxes, heads)
        claimed = {heads[j].id for j in pairs.values()}
        out: list[Association] = []

        unmatched = []
        for i, obj in enumerate(objects):
            if i in pairs:
                rec = heads[pairs[i]]
                out.append(_carry(obj, rec, boxes[i], None, zones))
            else:
                unmatched.append(i)

        found_by: dict[int, Candidate] = {}
        if history is not None and unmatched:
            try:
                found_by = _reidentify_all(objects, boxes, unmatched, history, set(claimed))
            except HistoryUnavailable as e:
                history_error = str(e)
                log.warning("associate: history search unavailable, no `returned` this scan: %s", e)

        ordinal = 0
        for i in unmatched:
            obj = objects[i]
            found = found_by.get(i)
            if found is not None:
                claimed.add(found.object_id)
                in_head = found.object_id in head
                rec = head[found.object_id] if in_head else found.record
                out.append(_carry(obj, rec, boxes[i], None if in_head else RETURNED, zones))
                continue
            cls = first_sight_class(obj)
            while (oid := new_id(cls, capture_id, ordinal)) in head or oid in claimed:
                ordinal += 1
            ordinal += 1
            claimed.add(oid)
            c, e, y = boxes[i]
            out.append(Association(ADDED, oid, cls, obj.color or FALLBACK_COLOR, now, None, obj,
                                   None, c, e, y % YAW_PERIOD))

        if history_error is not None:
            for a in out:
                if a.verdict == ADDED:
                    a.note = f"history unavailable ({history_error}): may be a returning object"

        for rec in heads:
            if rec.id not in claimed:
                note, hidden = None, False
                if misses is not None and misses.get(rec.id, 0) >= MISSES_TO_REMOVE:
                    verdict = REMOVED     # already shown gone: an occluded scan can't bring it back
                elif fresh is not None and not fresh(rec):
                    verdict, note = UNOBSERVED, "not looked at: its block is stale"
                elif occluded is not None and occluded(rec):
                    verdict, note = UNOBSERVED, "hidden: no line of sight to its last pose"
                    hidden = True
                elif misses is not None and misses.get(rec.id, 0) + 1 < MISSES_TO_REMOVE:
                    n = misses.get(rec.id, 0) + 1
                    verdict, note = MISSED, f"not seen ({n}/{MISSES_TO_REMOVE}): kept until {MISSES_TO_REMOVE} misses in a row"
                else:
                    verdict = REMOVED
                out.append(Association(verdict, rec.id, rec.cls, rec.color, rec.first_seen,
                                       rec.zone, None, rec, note=note, occluded=hidden))

        out.sort(key=lambda a: a.object_id)
        if sp is not None:
            for v in (UNCHANGED, MOVED, ADDED, RETURNED, REMOVED, UNOBSERVED, MISSED):
                sp.set_data(v, sum(a.verdict == v for a in out))
            sp.set_data("history_error", history_error or "")
    return out


def for_serialize(assocs: list[Association], zones: dict | None = None, default_zone: str | None = None,
                  ignore_paths: tuple[str, ...] = ()):
    """-> (measured, unobserved), the arguments serialize.serialize(root, measured, head,
    unobserved) takes. Removed objects are simply absent.

    zones: room.yaml's `zones` (voxelize.load_room()["zones"]). The zone is where the object
    is NOW -- a moved object can change zone, which is a file rename -- with hysteresis
    against its committed zone. Outside every zone it falls back to default_zone; with no
    fallback that is an error here rather than a malformed file later.

    ignore_paths: .roomignore's path globs (segment.roomignore). Like .gitignore, they keep an
    UNTRACKED object out (an `added` one whose file would match); an object already in HEAD
    stays tracked.
    """
    from fnmatch import fnmatch

    from serialize import Measured

    measured = []
    for a in assocs:
        if a.obj is None:
            continue
        zone = zone_of(a.centre, zones, previous=a.zone) if zones else a.zone
        zone = zone or default_zone
        if zone is None:
            raise ValueError(f"{a.object_id} ({a.verdict}) at {np.round(a.centre, 2).tolist()} is in "
                             f"no zone and there is no default_zone")
        if a.verdict == ADDED and any(fnmatch(f"zones/{zone}/{a.object_id}.yaml", g) for g in ignore_paths):
            continue
        measured.append(Measured(a.object_id, a.cls, zone, tuple(map(float, a.centre)),
                                 tuple(map(float, a.extents)), float(a.yaw), a.color, a.first_seen))
    return measured, tuple(a.object_id for a in assocs if a.verdict in CARRIED)


def next_misses(assocs: list[Association], prior: dict[str, int] | None) -> dict[str, int]:
    """The consecutive-miss counts to save after this scan. Seen -> gone from the map;
    missed or removed -> +1 (capped), so a removal STAYS removed until the object is seen
    again -- resetting it would flip the file back on the next scan; occluded -> unchanged
    (explained, not a miss). A committed removal leaves HEAD, and with it this map."""
    prior, out = prior or {}, {}
    for a in assocs:
        if a.verdict in (MISSED, REMOVED):
            out[a.object_id] = min(prior.get(a.object_id, 0) + 1, MISSES_TO_REMOVE)
        elif a.verdict == UNOBSERVED and a.object_id in prior:
            out[a.object_id] = prior[a.object_id]
    return out


def _misses_path(repo_dir) -> Path:
    return Path(repo_dir) / ".git" / "gitspace" / "misses.json"   # next to roomctl's scan.json


def load_misses(repo_dir) -> dict[str, int]:
    p = _misses_path(repo_dir)
    try:
        return {str(k): int(v) for k, v in json.loads(p.read_text()).items()}
    except FileNotFoundError:
        return {}


def save_misses(repo_dir, counts: dict[str, int]) -> None:
    p = _misses_path(repo_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(sorted(counts.items())), indent=1) + "\n")
    tmp.replace(p)                                            # atomic: a crash leaves the old counts


def zone_of(centre, zones: dict, previous: str | None = None) -> str | None:
    """The room.yaml zone box holding `centre`, by voxelize.voxel_docs' rule (the first zone by
    name wins), except that an object stays in `previous` while it's within ZONE_HYST of
    that box."""
    def inside(name, grow):
        lo, hi = (np.asarray(zones[name][k], float) for k in ("min", "max"))
        return bool(np.all(centre >= lo - grow) and np.all(centre <= hi + grow))

    centre = np.asarray(centre, float)
    if previous in zones and inside(previous, ZONE_HYST):
        return previous
    return next((n for n in sorted(zones) if inside(n, 0.0)), None)


def observation_docs(assocs: list[Association], capture_id: str, at: datetime,
                     trace: dict | None = None, camera: str | None = None) -> list[dict]:
    """This capture's room-observations documents: one per (object, camera), carrying the
    object_id associate gave it. The contract is fake/README.md "The documents"; bulk them
    with `_op_type: create` (a data stream).

    `camera`: the source name for every row -- "bb_map" when the objects come from the
    robot's voxel map (plan/roommate/03-interfaces.md §4) rather than one camera each.
    A HIDDEN object (unobserved because the raycast found no line of sight) gets a row too:
    `occluded: true` at its last committed pose -- "looked, and something was in the way",
    which is what web/object_api's hidden-vs-gone verdict reads. A STALE one (its block not
    re-observed) gets none: nobody looked, and a row would claim a sighting.

    Every doc gets its own millisecond: in a TSDS, docs with the same dimensions (object_id,
    camera) and @timestamp overwrite each other. `trace` is obs.trace_fields().
    """
    rows = []
    for a in assocs:
        if a.obj is not None:
            rows += [(a.object_id, {**row, **({"camera": camera} if camera else {})}) for row in a.obj.observations()]
        elif a.verdict == UNOBSERVED and a.occluded and a.previous is not None:
            p = a.previous.pose
            rows.append((a.object_id, {"camera": camera or "fused", "confidence": None, "point_count": 0,
                                       "raw_x": float(p.x), "raw_y": float(p.y), "raw_z": float(p.z),
                                       "occluded": True, "rejected_reason": None, "raw_description": None,
                                       "raw_label": None, "vlm_model": None, "label_attempt": None}))
    docs = []
    for oid, row in rows:
        t = at + timedelta(milliseconds=len(docs))
        docs.append({"@timestamp": t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.")
                     + f"{t.microsecond // 1000:03d}Z",
                     "capture_id": capture_id, "object_id": oid, **row, **(trace or {})})
    return docs


def first_sight_class(obj) -> str:
    """The one `class` string the YAML needs, chosen once. The views' labels and
    descriptions all stay in Elasticsearch; this doesn't reconcile them, it names the file.

    Segmenter label most views agree on (ties: higher summed score, then alphabetical),
    else the VLM label from the best-covered view, else "unknown".
    """
    tally: dict[str, tuple[int, float]] = {}
    for v in obj.views:
        if v.label and v.label != UNKNOWN:
            n, s = tally.get(v.label, (0, 0.0))
            tally[v.label] = (n + 1, s + (v.score or 0.0))
    if tally:
        return max(sorted(tally), key=lambda k: tally[k]).strip().lower()
    vlm = [(len(v.points), d.label) for v in obj.views
           if (d := v.description) is not None and getattr(d, "label", None)]
    if vlm:
        return " ".join(max(vlm)[1].split()).lower()
    return UNKNOWN


def _carry(obj, rec: ObjectRecord, box, verdict: str | None, zones: dict | None = None) -> Association:
    """Matched to `rec`: keep its id, colour, first_seen, zone — and its class, unless it never
    had one. UNKNOWN is the ABSENCE of a class (no segmenter label, no VLM, or neither could
    name it), so a later sighting that CAN name it says so; otherwise a thing the room can
    describe perfectly well stays "unknown" for ever. The id does not change with it: the id is
    the identity and `unknown_1231` keeps that name even once it is a snack bag (docs/25).
    A class, once there IS one, is never overwritten — first sight decides."""
    c, e, y = box
    if YAW_BAND[0] <= obj.footprint_aspect() < YAW_BAND[1]:
        c, e, y = obj.box(yaw=rec.pose.yaw)     # hold the committed axis; measure along it
    y = y % YAW_PERIOD
    if verdict is None:
        # Exactly what serialize will write: stabilize_record, then roomctl.state.settle
        # (same zone and centre within MOVE_M -> the committed record, byte-identical).
        from serialize import Measured, stabilize_record

        zone = (zone_of(c, zones, previous=rec.zone) if zones else rec.zone) or ""
        m = Measured(rec.id, rec.cls, zone, tuple(map(float, c)), tuple(map(float, e)), float(y),
                     rec.color, rec.first_seen)
        verdict = UNCHANGED if settle(rec, stabilize_record(m, rec)) == rec else MOVED
    cls = rec.cls
    if cls == UNKNOWN:
        named = first_sight_class(obj)
        if named != UNKNOWN:
            cls = named                      # the verdict above stays geometric: naming is not moving
    return Association(verdict, cls and rec.id or rec.id, cls, rec.color, rec.first_seen, rec.zone, obj, rec,
                       c, e, y)


def _match_head(objects, boxes, heads: list[ObjectRecord]) -> dict[int, int]:
    big = 1e9
    cost = np.full((len(objects), len(heads)), big)
    for i, obj in enumerate(objects):
        c, e, _ = boxes[i]
        names = _names(obj)
        for j, rec in enumerate(heads):
            d = float(np.linalg.norm(c - [rec.pose.x, rec.pose.y, rec.pose.z]))
            if d >= GATE or not _extents_ok(e, rec.extents):
                continue
            agree = rec.cls == UNKNOWN or UNKNOWN in obj.labels or rec.cls.lower() in names
            cost[i, j] = d + (0.0 if agree else LABEL_PENALTY)
    if not cost.size:
        return {}
    rows, cols = linear_sum_assignment(cost)
    return {int(i): int(j) for i, j in zip(rows, cols) if cost[i, j] < big}


def _plausible(obj, box, cand: Candidate) -> bool:
    """The gates a history candidate must pass: the score only ranks (see RETURN_MIN_SCORE)."""
    return (cand.score > RETURN_MIN_SCORE and _extents_ok(box[1], cand.record.extents)
            and not (obj.color and delta_e(obj.color, cand.record.color) > COLOR_VETO))


def _reidentify_all(objects, boxes, idx: list[int], history, exclude: set[str]) -> dict[int, Candidate]:
    """Unmatched objects -> {object index: the history candidate it IS}, solved JOINTLY.

    One hybrid query per object, then one assignment maximising the total score over the
    plausible pairs. Greedy first-come claiming let a returning metal cup take a returning
    spoon's id live (and the spoon, its id gone, became `added`).
    """
    options = {i: [c for c in history.reidentify(objects[i], exclude=exclude)
                   if c.object_id not in exclude and _plausible(objects[i], boxes[i], c)] for i in idx}
    rows = [i for i in idx if options[i]]
    ids = sorted({c.object_id for i in rows for c in options[i]})
    if not rows:
        return {}
    big = 1e9
    cost = np.full((len(rows), len(ids)), big)
    best: dict[tuple[int, str], Candidate] = {}
    for r, i in enumerate(rows):
        for c in options[i]:
            j = ids.index(c.object_id)
            if -c.score < cost[r, j]:
                cost[r, j], best[(i, c.object_id)] = -c.score, c
    rr, cc = linear_sum_assignment(cost)
    return {rows[r]: best[(rows[r], ids[j])] for r, j in zip(rr, cc) if cost[r, j] < big}


def delta_e(a: str, b: str) -> float:
    """CIELAB dE76 between two "#rrggbb" colours."""
    def lab(h):
        bgr = np.uint8([[[int(h[5:7], 16), int(h[3:5], 16), int(h[1:3], 16)]]])
        L, A, B = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)[0, 0].astype(float)
        return np.array([L * 100 / 255, A - 128, B - 128])
    return float(np.linalg.norm(lab(a) - lab(b)))


def _names(obj) -> set[str]:
    out = {v.label.lower() for v in obj.views if v.label}
    out |= {d.label.lower() for d in obj.descriptions if d is not None and getattr(d, "label", None)}
    return out


def _extents_ok(e, rec: Extents) -> bool:
    a = np.sort(np.maximum(np.asarray(e, float), 0.02))
    b = np.sort(np.maximum([rec.x, rec.y, rec.z], 0.02))
    return bool(np.all(np.maximum(a / b, b / a) < EXTENT_RATIO))


class OfflineHistory:
    """Stands in for ESHistory when the cluster can't be used (key parked or unset). Makes no
    call; every reidentify() says why, so associate() notes each `added` object."""

    def __init__(self, reason: str):
        self.reason = reason

    def reidentify(self, obj, exclude):
        raise HistoryUnavailable(self.reason)


def history_from_env(index: str = "room-objects"):
    """ESHistory on ELASTIC_URL / ELASTIC_API_KEY, or OfflineHistory when either is parked,
    unset or garbage (keys.credential: the same rule web/server.py uses)."""
    url, why_url = credential("ELASTIC_URL")
    key, why_key = credential("ELASTIC_API_KEY")
    if not (url and key):
        reason = why_key or why_url
        log.warning("associate: history offline (%s): no `returned` verdicts", reason)
        return OfflineHistory(reason)
    from elasticsearch import Elasticsearch

    return ESHistory(Elasticsearch(url, api_key=key, request_timeout=30), index=index)


class ESHistory:
    """reidentify() as ONE hybrid query over room-objects, i.e. every historical appearance:
    BM25 on class + Jina dense on raw_description, fused by RRF, then Jina rerank
    (docs/12 "2f. Retrievers"). Best hit per object_id, in rank order."""

    SOURCE = ["object_id", "class", "zone", "pose", "extents", "color", "first_seen", "commit_sha"]
    RETRY_STATUS = (429, 502, 503, 504)

    def __init__(self, es, index: str = "room-objects", size: int = 20,
                 rerank_id: str = os.getenv("ES_INFERENCE_RERANK", "jina-rerank"),
                 request_timeout: float = 10.0, max_retries: int = 2):
        # Transient failures (timeouts, 429, 50x) are retried with backoff by the client;
        # whatever is left after that becomes HistoryUnavailable in reidentify().
        opts = getattr(es, "options", None)
        self.es = opts(request_timeout=request_timeout, max_retries=max_retries, retry_on_timeout=True,
                       retry_on_status=self.RETRY_STATUS) if opts else es
        self.index, self.size, self.rerank_id = index, size, rerank_id

    def query(self, obj, exclude: set[str]) -> dict | None:
        texts = [d.text for d in obj.descriptions if d is not None and getattr(d, "text", None)]
        names = sorted(_names(obj) - {UNKNOWN})
        text = " ; ".join(texts) or " ".join(names)
        if not text:
            return None                       # nothing to search with: it's new
        not_these = [{"terms": {"object_id": sorted(exclude)}}] if exclude else []

        def standard(q):
            return {"standard": {"query": {"bool": {"must": [q], "must_not": not_these}}}}

        legs = [standard({"semantic": {"field": "raw_description", "query": text}})]
        if names:
            legs.insert(0, standard({"match": {"class": " ".join(names)}}))
        # ES rejects the query unless rrf's window >= the reranker's >= size; rrf defaults to 10
        window = max(50, self.size)
        first = {"rrf": {"retrievers": legs, "rank_window_size": window}} if len(legs) > 1 else legs[0]
        return {"text_similarity_reranker": {"retriever": first, "field": "raw_description",
                                             "inference_id": self.rerank_id,
                                             "inference_text": text, "rank_window_size": window}}

    def reidentify(self, obj, exclude: set[str]) -> list[Candidate]:
        retriever = self.query(obj, exclude)
        if retriever is None:
            return []
        try:
            with obs.span("es.search", "re-id hybrid query", index=self.index, excluded=len(exclude)) as sp:
                res = self.es.search(index=self.index, retriever=retriever, size=self.size, source=self.SOURCE)
                if sp is not None:
                    sp.set_data("hits", len(res["hits"]["hits"]))
        except Exception as e:
            if not _is_es_error(e):
                raise                                  # our bug, not the cluster's: stay loud
            reason = _es_reason(e)
            (log.error if "query rejected" in reason else log.warning)("reidentify: %s", reason)
            raise HistoryUnavailable(reason) from e
        out, seen = [], set()
        for h in res["hits"]["hits"]:
            s = h.get("_source") or {}
            oid = s.get("object_id")
            if not oid or oid in seen:
                continue
            try:
                rec = _record(s)
            except (KeyError, TypeError, ValueError) as e:  # one drifted doc must not sink the search
                log.warning("reidentify: skipping %s, malformed history doc: %r", oid, e)
                continue
            seen.add(oid)
            out.append(Candidate(oid, float(h.get("_score") or 0.0), rec))
        return out


def _is_es_error(e: Exception) -> bool:
    """elasticsearch-py's ApiError / TransportError family, duck-typed so it holds across client
    versions and in environments without the package."""
    names = {c.__name__ for c in type(e).__mro__}
    return bool(names & {"ApiError", "TransportError"}) or hasattr(e, "meta")


def _es_reason(e: Exception) -> str:
    """One line on why the history search failed, keyed on the HTTP status when there is one."""
    status = getattr(getattr(e, "meta", None), "status", None) or getattr(e, "status_code", None)
    kind = {401: "unauthorised (ELASTIC_API_KEY)", 403: "forbidden (ELASTIC_API_KEY)",
            404: "index missing", 400: "query rejected, a bug in ESHistory.query"}.get(status)
    if kind is None:
        kind = f"HTTP {status} after client retries" if status else f"unreachable ({type(e).__name__})"
    return f"{kind}: {str(e)[:200]}"


def _record(s: dict) -> ObjectRecord:
    t = datetime.fromisoformat(str(s["first_seen"]).replace("Z", "+00:00"))
    p, e = s["pose"], s["extents"]
    return ObjectRecord(s["object_id"], s["class"], s["zone"],
                        Pose(p["x"], p["y"], p["z"], int(round(p["yaw"])) % YAW_PERIOD),
                        Extents(e["x"], e["y"], e["z"]), s["color"], iso_utc(t))
