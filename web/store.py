"""store.py — the reads behind the pages. Elasticsearch first; the fixture file only as a
LABELLED fallback.

These pages are evidence ("why was this diff wrong?"), so this module never invents a
number: a field that is not on the document comes back as None and the page says
"not recorded". Every payload carries `source: "elasticsearch" | "fixture"` so the UI
can say which it is looking at.

    import store; store.init(es)          # es = server.py's Elastic client
    await store.capture("cap_0003")       # -> dict, or raises NotFound
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURE = Path(os.getenv("GITSPACE_FIXTURE") or HERE.parent / "fake" / "out" / "demo.ndjson")

# docs/22-camera-sync.md §4 — the same rule as obs.capture_quality
GATE = {
    "skew_ms": {"op": "<", "value": 25.0, "unit": "ms"},
    "tilt_rate_max": {"op": "<", "value": 0.05, "unit": "rad/s"},
    "coverage": {"op": ">", "value": 0.60, "unit": ""},
}
TELEMETRY_SIGNALS = ("tilt_rate", "odom_residual")
TELEMETRY_WINDOW_S = 2.0

# ES being absent is a reason to fall back; ES answering with a real query error is not
# elastic_auth is deliberately NOT here: a rejected key is a misconfiguration to SHOW, and serving
# fixtures for it would hide it behind plausible data (elastic-09's audit, 2026-09-18).
_FALLBACK_CODES = {"elastic_unconfigured", "elastic_paused", "elastic_unreachable", "elastic_timeout"}
_STOP = set("a an the of on in at to with and or its it is by for from near next some".split())

_es = None
_fixture_cache: dict[str, Any] = {"mtime": None, "docs": {}}


class NotFound(Exception):
    pass


def init(es) -> None:
    global _es
    _es = es


# ── the two backends ──────────────────────────────────────────────────────────

def _fixture() -> dict[str, list[dict]]:
    """demo.ndjson is _bulk format: an action line, then the document. Reloaded when the
    file changes (scene_gen regenerates it, and every sha with it)."""
    try:
        mtime = FIXTURE.stat().st_mtime
    except OSError:
        return {}
    if _fixture_cache["mtime"] != mtime:
        docs: dict[str, list[dict]] = {}
        lines = FIXTURE.read_text().splitlines()
        for action, doc in zip(lines[0::2], lines[1::2]):
            index = next(iter(json.loads(action).values())).get("_index")
            docs.setdefault(index, []).append(json.loads(doc))
        _fixture_cache.update(mtime=mtime, docs=docs)
    return _fixture_cache["docs"]


def _use_fixture(err: Exception) -> bool:
    code = getattr(err, "code", None)
    if code in _FALLBACK_CODES:
        return True
    return code == "elastic_error" and "index_not_found" in str(getattr(err, "detail", err))


async def _find(index: str, filters: dict[str, Any], *, time_range: tuple[str, str] | None = None,
                prefix: dict[str, str] | None = None,
                size: int = 500, newest_first: bool = False, label: str) -> tuple[list[dict], str]:
    """Documents of `index` matching every term filter (and keyword `prefix`) -> (docs, source).
    `size` TRUNCATES: ask `newest_first` whenever the newest documents are the ones that matter."""
    if _es is not None:
        must: list[dict] = []
        for k, v in filters.items():
            must.append({"terms": {k: list(v)}} if isinstance(v, (list, tuple)) else {"term": {k: v}})
        for k, v in (prefix or {}).items():
            must.append({"prefix": {k: v}})
        if time_range:
            must.append({"range": {"@timestamp": {"gte": time_range[0], "lte": time_range[1]}}})
        body = {"size": size, "query": {"bool": {"filter": must}} if must else {"match_all": {}},
                "sort": [{"@timestamp": "desc" if newest_first else "asc"}]}
        try:
            res = await _es.search(index, body, label=label)
            return [h["_source"] for h in res.get("hits", {}).get("hits", [])], "elasticsearch"
        except Exception as e:  # noqa: BLE001 — ApiError lives in server.py; go by its .code
            if not _use_fixture(e):
                raise
    docs = []
    for d in _fixture().get(index, []):
        if any((d.get(k) not in v) if isinstance(v, (list, tuple)) else (d.get(k) != v) for k, v in filters.items()):
            continue
        if any(not str(d.get(k, "")).startswith(v) for k, v in (prefix or {}).items()):
            continue
        if time_range and not (when(time_range[0]) <= when(d) <= when(time_range[1])):
            continue
        docs.append(d)
    docs.sort(key=when, reverse=newest_first)
    return docs[:size], "fixture"


# ── helpers ───────────────────────────────────────────────────────────────────

def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def when(x: Any) -> datetime:
    """A document (its @timestamp) or an ISO string -> an aware datetime, for ORDERING and comparing.
    Never compare these as strings: "…:29Z" and "…:29.000Z" are the same instant and sort differently."""
    s = x.get("@timestamp") if isinstance(x, dict) else x
    try:
        d = _ts(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, AttributeError):
        return _EPOCH


def _metric(v: Any) -> float | None:
    """A telemetry `value`: a number — or, once the TSDS lifecycle has downsampled it (after 1 d),
    an aggregate {min, max, sum, value_count}. Take the signed EXTREME: a spike must survive."""
    if isinstance(v, dict):
        lo, hi = _num(v.get("min")), _num(v.get("max"))
        ends = [e for e in (lo, hi) if e is not None]
        return max(ends, key=abs) if ends else None
    return _num(v)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _text(v: Any) -> str:
    """`raw_description` is mapped semantic_text: depending on the ES version `_source` holds
    the plain string or the legacy {"text": ..., "inference": ...} object."""
    if isinstance(v, dict):
        v = v.get("text")
    if isinstance(v, list):
        v = " ".join(str(x) for x in v)
    return v if isinstance(v, str) else ""


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w not in _STOP and len(w) > 1]


def gate(skew_ms: float | None, tilt_rate_max: float | None, coverage: float | None,
         recorded_ok: bool | None = None) -> dict:
    """Evaluate the quality gate on whatever was recorded. `pass` is True/False only when
    that is knowable: any recorded value failing is a REJECT; every value present and
    passing is a PASS; otherwise None (incomplete), never a guess."""
    values = {"skew_ms": skew_ms, "tilt_rate_max": tilt_rate_max, "coverage": coverage}
    failing, missing = [], []
    for name, v in values.items():
        rule = GATE[name]
        if v is None:
            missing.append(name)
        elif not (v < rule["value"] if rule["op"] == "<" else v > rule["value"]):
            failing.append(name)
    ok = False if failing else (True if not missing else None)
    return {"values": values, "thresholds": GATE, "pass": ok, "failing": failing,
            "missing": missing, "recorded_ok": recorded_ok}


def disagreement(cameras: list[dict], quantum_mm: float) -> dict | None:
    """How far apart the cameras put one object. Needs two positions to mean anything.

    Per AXIS, over the cameras that reported that axis: real documents do not always carry all
    three (the first live captures had raw_x only), and demanding x, y AND z hid a 49 mm
    disagreement behind "nothing to compare". An axis fewer than two cameras reported is None.
    """
    spreads: dict[str, float | None] = {}
    for a in "xyz":
        vals = [c[f"raw_{a}"] for c in cameras if c.get(f"raw_{a}") is not None]
        spreads[a] = round((max(vals) - min(vals)) * 1000, 1) if len(vals) >= 2 else None
    known = {a: v for a, v in spreads.items() if v is not None}
    if not known:
        return None
    axis = max(known, key=known.get)
    pos = [c for c in cameras if c.get(f"raw_{axis}") is not None]
    median = statistics.median(c[f"raw_{axis}"] for c in pos)
    offsets = {c["camera"]: round((c[f"raw_{axis}"] - median) * 1000, 1) for c in pos}
    outlier = max(offsets, key=lambda k: abs(offsets[k]))
    worst = spreads[axis]
    return {"spread_mm": spreads, "max_spread_mm": worst, "axis": axis, "offsets_mm": offsets,
            # two cameras can disagree, but neither is "the" outlier
            "outlier_camera": outlier if worst > quantum_mm and len(pos) >= 3 else None,
            "quantum_mm": quantum_mm, "exceeds_quantum": worst > quantum_mm,
            "ratio": round(worst / quantum_mm, 1) if quantum_mm else None}


def _mark_unique_words(cameras: list[dict]) -> None:
    """A word only ONE camera used is where the descriptions disagree."""
    per_cam = {c["camera"]: {w for d in c["descriptions"] for w in _words(d["text"])} for c in cameras}
    for c in cameras:
        others = set().union(*[ws for cam, ws in per_cam.items() if cam != c["camera"]]) if len(per_cam) > 1 else set()
        for d in c["descriptions"]:
            d["unique"] = sorted({w for w in _words(d["text"]) if w not in others}) if others else []


# elastic/NOTES.md "What is real and what is generated": who wrote the words a judge is reading?
SCRIPTED_BY = ("fake/", "scripts/", "tests/")
PROVENANCE_LEGEND = {
    "real": "the git commits, and the search itself — BM25, Jina embeddings, RRF fusion and the Jina rerank ran live on this query",
    "generated": "the descriptions, poses, per-camera noise, voxels and cloud numbers — scripted by fake/scene_gen, standing in for the cameras and the VLM",
}


def provenance(vlm_model: Any, capture_id: Any = None) -> dict:
    """One rule, shared with elastic/demo_hybrid.py: text is SYNTHETIC when `vlm_model` is missing or starts
    with fake/, scripts/ or tests/. Separately, a `synth_` capture ran the real pipeline over frames
    RENDERED by perception/synthetic.py — real documents, real trace, no camera. Both are said out loud."""
    model = vlm_model.strip() if isinstance(vlm_model, str) and vlm_model.strip() else None
    scripted = model is None or model.startswith(SCRIPTED_BY)
    rendered = isinstance(capture_id, str) and capture_id.startswith("synth_")
    why = []
    if scripted:
        why.append(f"text scripted by {model}" if model else "no vlm_model recorded — treated as scripted text")
    if rendered:
        why.append("frames rendered by perception/synthetic.py, not a camera")
    return {"synthetic": scripted or rendered, "scripted_text": scripted, "rendered_input": rendered,
            "vlm_model": model, "why": " · ".join(why) or None}


def sentry_link(docs: list[dict | None], *, real: bool) -> dict:
    """The way into the Sentry waterfall. `real` is False for fixture / synthetic captures:
    their trace ids never existed in Sentry, so no URL is built from them — a dead link on
    this page would be worse than none. For real documents: `sentry_url` as written by
    obs.trace_fields(), else rebuilt the same way from SENTRY_ORG_SLUG + the trace id."""
    for d in docs:
        tid = (d or {}).get("sentry_trace_id")
        if isinstance(tid, str) and re.fullmatch(r"[0-9a-f]{32}", tid):
            url = None
            if real:
                url = d.get("sentry_url")
                if not (isinstance(url, str) and re.match(r"https://[a-z0-9.-]+\.sentry\.io/", url)):
                    org = os.getenv("SENTRY_ORG_SLUG", "").strip()
                    url = (f"https://{org}.sentry.io/performance/trace/{tid}/"
                           if re.fullmatch(r"[a-z0-9-]+", org) else None)
            return {"trace_id": tid, "span_id": d.get("sentry_span_id") or None, "url": url,
                    "why_no_link": None if url else ("synthetic trace — not recorded in Sentry" if not real
                                                     else "SENTRY_ORG_SLUG is not set, so the link cannot be built")}
    return {"trace_id": None, "span_id": None, "url": None,
            "why_no_link": "no sentry_trace_id on this capture's documents"}


# ── reads ─────────────────────────────────────────────────────────────────────

async def captures(limit: int = 50) -> dict:
    docs, source = await _find("room-clouds", {}, size=max(1, min(limit, 500)), newest_first=True,
                               label="captures.list")
    return {"source": source, "captures": [{
        "capture_id": d.get("capture_id"), "ts": d.get("@timestamp"), "commit_sha": d.get("commit_sha"),
        "cameras": d.get("cameras") or [], "coverage": _num(d.get("coverage_pct")),
        "gate_pass": gate(_num(d.get("skew_ms")), _num(d.get("tilt_rate_max")), _num(d.get("coverage_pct")))["pass"],
    } for d in docs]}


async def capture(capture_id: str) -> dict:
    by_capture = {"capture_id": capture_id}
    (clouds, source), (obs_docs, _), (events, _), (committed, _) = await asyncio.gather(
        _find("room-clouds", by_capture, size=1, label="capture.doc"),
        _find("room-observations", by_capture, size=1000, label="capture.observations"),
        _find("room-events", by_capture, size=1, label="capture.event"),
        _find("room-objects", by_capture, size=500, label="capture.objects"))
    if not clouds and not obs_docs:
        raise NotFound(capture_id)
    doc = clouds[0] if clouds else {}
    event = events[0] if events else None
    committed_by_id = {o["object_id"]: o for o in committed}
    quantum_mm = round(float(os.getenv("QUANT_POSITION_M", "0.01")) * 1000, 3)
    # fake/scene_gen labels what it synthesises; such a capture never ran, so Sentry never saw it
    synthetic = any(o.get("vlm_model") == "fake/scene_gen" for o in obs_docs)

    shutter = doc.get("@timestamp") or min(o["@timestamp"] for o in obs_docs)
    cameras_seen = sorted(set(doc.get("cameras") or []) | {o["camera"] for o in obs_docs if o.get("camera")})

    # per object -> per camera (a camera may have several label attempts: keep them all)
    grouped: dict[str, dict[str, dict]] = {}
    rejected = []
    for o in obs_docs:
        desc = {"text": _text(o.get("raw_description")), "label": o.get("raw_label"),
                "attempt": o.get("label_attempt"), "model": o.get("vlm_model")}
        if o.get("object_id") is None:
            rejected.append({"camera": o.get("camera"), "reason": o.get("rejected_reason"),
                             "description": desc["text"], "confidence": _num(o.get("confidence")),
                             "point_count": o.get("point_count"),
                             "raw_x": _num(o.get("raw_x")), "raw_y": _num(o.get("raw_y")), "raw_z": _num(o.get("raw_z"))})
            continue
        cam = grouped.setdefault(o["object_id"], {}).setdefault(o["camera"], {
            "camera": o["camera"], "raw_x": _num(o.get("raw_x")), "raw_y": _num(o.get("raw_y")),
            "raw_z": _num(o.get("raw_z")), "confidence": _num(o.get("confidence")),
            "point_count": o.get("point_count"), "occluded": bool(o.get("occluded")), "descriptions": []})
        if desc["text"]:
            cam["descriptions"].append(desc)

    objects = []
    for object_id, by_cam in grouped.items():
        cams = [by_cam[c] for c in sorted(by_cam)]
        _mark_unique_words(cams)
        c = committed_by_id.get(object_id, {})
        labels = sorted({d["label"] for cam in cams for d in cam["descriptions"] if d["label"]})
        objects.append({
            "object_id": object_id, "class": c.get("class"), "zone": c.get("zone"),
            "committed_pose": c.get("pose"), "cameras": cams,
            "missing_cameras": [x for x in cameras_seen if x not in by_cam],
            "labels": labels, "labels_disagree": len(labels) > 1,
            "disagreement": disagreement(cams, quantum_mm)})
    # a capture that was never committed has no room-objects of its own: name its objects
    # from their most recent appearance in any commit
    unnamed = [o["object_id"] for o in objects if not o["class"]]
    if unnamed:
        seen, _ = await _find("room-objects", {"object_id": unnamed}, size=500, newest_first=True, label="capture.known_as")
        latest: dict[str, dict] = {}
        for k in seen:
            latest.setdefault(k["object_id"], k)
        for o in objects:
            k = latest.get(o["object_id"])
            o["known_class"], o["known_zone"] = (k.get("class"), k.get("zone")) if k else (None, None)
    objects.sort(key=lambda o: -(o["disagreement"] or {}).get("max_spread_mm", -1))

    per_camera = [{
        "camera": cam,
        "frame_uri": None,       # no index maps a frame reference yet (mappings are strict): the page shows a placeholder
        "detections": sum(1 for o in obs_docs if o.get("camera") == cam and o.get("object_id") is not None),
        "rejected": sum(1 for o in obs_docs if o.get("camera") == cam and o.get("object_id") is None),
        "mean_confidence": (lambda v: round(sum(v) / len(v), 3) if v else None)(
            [o["confidence"] for o in obs_docs if o.get("camera") == cam and o.get("object_id") is not None
             and _num(o.get("confidence")) is not None]),
    } for cam in cameras_seen]

    g = gate(_num(doc.get("skew_ms")), _num(doc.get("tilt_rate_max")), _num(doc.get("coverage_pct")),
             doc.get("quality_ok") if isinstance(doc.get("quality_ok"), bool) else None)

    # telemetry ±2 s around the shutter
    t0 = _ts(shutter)
    window = (_iso(t0 - timedelta(seconds=TELEMETRY_WINDOW_S)), _iso(t0 + timedelta(seconds=TELEMETRY_WINDOW_S)))
    samples, _ = await _find("robot-telemetry", {"signal": TELEMETRY_SIGNALS}, time_range=window, size=2000,
                             label="capture.telemetry")
    signals: dict[str, list] = {}
    for s in samples:
        v = _metric(s.get("value"))
        if v is not None:
            signals.setdefault(s["signal"], []).append([round((_ts(s["@timestamp"]) - t0).total_seconds() * 1000, 1), v])
    spike = None
    tilt = signals.get("tilt_rate") or []
    if tilt:
        at_ms, value = max(tilt, key=lambda p: abs(p[1]))
        if abs(value) >= GATE["tilt_rate_max"]["value"]:
            spike = {"signal": "tilt_rate", "at_ms": at_ms, "value": value,
                     "threshold": GATE["tilt_rate_max"]["value"]}
    telemetry = {"recorded": bool(signals), "window_s": TELEMETRY_WINDOW_S, "shutter_ts": shutter,
                 "signals": signals, "spike": spike,
                 "units": {"tilt_rate": "rad/s", "odom_residual": "m"}}

    # neighbours in time; a REJECTED capture followed within a minute is "retried as" the next
    timeline = (await captures(500))["captures"][::-1]                       # oldest first
    ids = [c["capture_id"] for c in timeline]
    i = ids.index(capture_id) if capture_id in ids else -1
    near = lambda a, b: abs((_ts(a["ts"]) - _ts(b["ts"])).total_seconds()) <= 60
    prev_c = timeline[i - 1] if i > 0 else None
    next_c = timeline[i + 1] if 0 <= i < len(ids) - 1 else None
    rejected_capture = g["pass"] is False or (event or {}).get("event_type") == "capture_rejected"
    retry = next_c["capture_id"] if rejected_capture and next_c and near(next_c, timeline[i]) else None
    retry_of = (prev_c["capture_id"] if prev_c and prev_c["gate_pass"] is False and i >= 0
                and near(prev_c, timeline[i]) else None)
    nav = {"prev": prev_c and prev_c["capture_id"], "next": next_c and next_c["capture_id"],
           "retry": retry, "retry_of": retry_of}

    diff = None
    if event:
        diff = {k: event.get(k) for k in ("commit_sha", "parent_sha", "branch", "message", "outcome", "event_type")}
        for k in ("objects_moved", "objects_added", "objects_removed", "objects_affected"):
            diff[k] = event.get(k) or []
        diff["rejected"] = rejected_capture
        diff["retry"] = None
    baseline_sha = (diff or {}).get("parent_sha")
    if diff and retry:
        retry_events, _ = await _find("room-events", {"capture_id": retry}, size=1, label="capture.retry_event")
        if retry_events:
            r = retry_events[0]
            diff["retry"] = {"capture_id": retry, "commit_sha": r.get("commit_sha"), "message": r.get("message"),
                             **{k: r.get(k) or [] for k in ("objects_moved", "objects_added", "objects_removed")}}
            baseline_sha = baseline_sha or r.get("parent_sha")     # what HEAD was when this one was shot
            real_moves = set(diff["retry"]["objects_moved"])
            diff["phantom_moves"] = [o for o in diff["objects_moved"] if o not in real_moves]

    # why the diff may be wrong — every reason is derived from a recorded value above
    reasons = []
    for name in g["failing"]:
        rule = GATE[name]
        reasons.append({"kind": "gate", "field": name,
                        "text": f"{name} = {g['values'][name]:g} {rule['unit']}".strip()
                                + f" — the gate needs {rule['op']} {rule['value']:g}"})
    if spike:
        when = "before" if spike["at_ms"] < 0 else "after"
        reasons.append({"kind": "telemetry", "field": "tilt_rate",
                        "text": f"tilt_rate peaked at {spike['value']:.3f} rad/s, {abs(spike['at_ms']):.0f} ms {when} the shutter"})
    # a "moved" verdict is only as good as the cameras' agreement: compare how far each object
    # was read to have moved (against HEAD at the time) with how far apart the cameras put it
    moved = list((diff or {}).get("objects_moved") or [])
    if moved and baseline_sha:
        before, _ = await _find("room-objects", {"commit_sha": baseline_sha, "object_id": moved},
                                size=500, label="capture.parent_poses")
        before_by_id = {b["object_id"]: b.get("pose") or {} for b in before}
        phantom = set((diff or {}).get("phantom_moves") or [])
        for o in objects:
            prev, d = before_by_id.get(o["object_id"]), o["disagreement"]
            if o["object_id"] not in moved or not prev:
                continue
            now = o["committed_pose"] or {a: statistics.median(c[f"raw_{a}"] for c in o["cameras"]
                                                               if c.get(f"raw_{a}") is not None) for a in "xyz"}
            if any(prev.get(a) is None or now.get(a) is None for a in "xyz"):
                continue
            o["moved_mm"] = round(sum((now[a] - prev[a]) ** 2 for a in "xyz") ** 0.5 * 1000, 1)
            o["phantom"] = o["object_id"] in phantom
            if d and d["max_spread_mm"] >= o["moved_mm"] and not o["phantom"]:
                reasons.append({"kind": "disagreement", "field": o["object_id"],
                                "text": f"{o['object_id']} was read as moved by {o['moved_mm']:g} mm, but its cameras "
                                        f"disagree by {d['max_spread_mm']:g} mm — the move is inside the noise"})
    if diff and diff.get("phantom_moves"):
        n, real = len(diff["objects_moved"]), len(diff["retry"]["objects_moved"])
        reasons.append({"kind": "retry", "field": retry,
                        "text": f"it would have moved {n} objects; the retry {retry} found {real} — "
                                f"{len(diff['phantom_moves'])} of those moves were the robot's, not the room's"})

    return {
        "source": source, "capture_id": capture_id, "ts": shutter, "commit_sha": doc.get("commit_sha"),
        "cameras": per_camera, "point_count": doc.get("point_count"),
        "icp_residual_mm": _num(doc.get("icp_residual_mm")), "cloud_uri": doc.get("cloud_uri"),
        "gate": g, "quantum_mm": quantum_mm, "objects": objects, "rejected": rejected,
        "telemetry": telemetry, "synthetic": synthetic, "nav": nav,
        # `synthetic` above answers "did this capture ever run?" (it gates the Sentry link);
        # `provenance` answers "who wrote what you are reading?" — a scripted demo can have a real trace
        "provenance": provenance(next((o.get("vlm_model") for o in obs_docs if o.get("vlm_model")), None)
                                 or (committed[0].get("vlm_model") if committed else None), capture_id),
        "sentry": sentry_link([doc, event, *obs_docs[:1]], real=source == "elasticsearch" and not synthetic),
        "diff": diff, "suspect": {"is_suspect": bool(reasons), "reasons": reasons},
    }

