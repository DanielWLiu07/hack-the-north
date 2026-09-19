#!/usr/bin/env python3
"""Every Elasticsearch read in GITSPACE, in one file (docs/14-elastic-flow.md).

    q = Queries(es)                      # the real indices
    q = Queries(es, prefix="test-")      # tests/ builds isolated copies under a prefix

Each public method is one query and has a live-cluster test in tests/test_queries.py with the
same name (test_<method>). The "why" for each lives in docs/11 and docs/14; the comments here
only say what the query shape is doing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from elasticsearch import Elasticsearch

from setup_elastic import DATA_STREAMS, INDICES, RERANK_ID

VOXEL_LEVELS = {"l3": "voxel_key_l3", "l5": "voxel_key_l5", "full": "voxel_key"}


def esql_ts(ts: datetime | str) -> str:
    """ES|QL's TO_DATETIME only parses yyyy-MM-dd'T'HH:mm:ss.SSS'Z' -- normalise to UTC millis."""
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise ValueError(f"timestamp {ts} has no timezone")
    ts = ts.astimezone(timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


def envelope(x0: float, y0: float, x1: float, y1: float) -> dict:
    """Axis-aligned box in room metres, as a cartesian `shape` (top-left, bottom-right)."""
    return {"type": "envelope", "coordinates": [[min(x0, x1), max(y0, y1)], [max(x0, x1), min(y0, y1)]]}


class Queries:
    def __init__(self, es: Elasticsearch, prefix: str = ""):
        self.es = es
        self.objects, self.voxels, self.clouds = (prefix + n for n in INDICES)
        self.observations, self.telemetry, self.events = (prefix + n for n in DATA_STREAMS)
        self.all = [prefix + n for n in INDICES + DATA_STREAMS]

    def _esql(self, query: str, **params) -> list[dict]:
        """ES|QL with named params (?name); rows come back as dicts. Every query states its own
        LIMIT: without one ES silently truncates at 1000 rows."""
        r = self.es.esql.query(query=query, params=[{k: v} for k, v in params.items()])
        cols = [c["name"] for c in r["columns"]]
        return [dict(zip(cols, row)) for row in r["values"]]

    # ── search: hybrid + rerank + collapse ───────────────────────────────────

    def _filters(self, commit_sha: str | None, near: tuple[float, float] | None, radius: float,
                 branch: str | None = None) -> list:
        f = []
        if commit_sha:
            f.append({"term": {"commit_sha": commit_sha}})
        if branch:
            f.append({"term": {"branch": branch}})
        if near:
            x, y = near
            f.append({"shape": {"position": {"shape": envelope(x - radius, y - radius, x + radius, y + radius),
                                             "relation": "intersects"}}})
        return f

    def _lexical(self, text: str) -> dict:
        # BM25 over the label AND all three descriptions (raw_description.text, the english-analyzed
        # sub-field), plus an exact hit on the id ("keys_7c2e" must match; vectors can't)
        return {"bool": {"should": [
            {"multi_match": {"query": text, "fields": ["class", "raw_description.text"]}},
            {"term": {"object_id": text}}]}}

    def _semantic(self, text: str) -> dict:
        # Jina dense vectors over all three descriptions (semantic_text embeds the query too)
        return {"semantic": {"field": "raw_description", "query": text}}

    def search_objects(self, text: str, commit_sha: str | None = None,
                       near: tuple[float, float] | None = None, radius: float = 1.5,
                       size: int = 10, branch: str | None = None) -> list[dict]:
        """"Where's my mug?" over ALL history (or one commit): rrf(BM25 class + descriptions,
        semantic raw_description) -> Jina rerank -> one hit per object with its timeline, newest first.
        `near` + `radius` is the re-identification filter: only objects that were within
        `radius` m of a new cluster's centroid; `branch` keeps a re-id from resurrecting an id
        that only ever existed on another branch."""
        r = self.es.search(index=self.objects, **self.hybrid_request(text, commit_sha, near, radius, size, branch))
        out = []
        for h in r["hits"]["hits"]:
            timeline = [t["_source"] for t in h["inner_hits"]["timeline"]["hits"]["hits"]]
            src = h["_source"]  # the best-scoring doc of this object: what the result card shows
            out.append({"object_id": src["object_id"], "class": src["class"],
                        "score": h["_score"], "raw_description": src["raw_description"],
                        "commit_sha": src.get("commit_sha"), "capture_id": src.get("capture_id"),
                        "vlm_model": src.get("vlm_model"),  # who wrote the descriptions: provenance
                        "latest": timeline[0] if timeline else None, "timeline": timeline})
        return out

    def hybrid_request(self, text: str, commit_sha: str | None = None,
                       near: tuple[float, float] | None = None, radius: float = 1.5,
                       size: int = 10, branch: str | None = None) -> dict:
        """The ONE request search_objects sends (the _search body): BM25 + Jina dense fused by
        RRF, reranked by Jina, collapsed to one hit per object. Public so the demo can print
        exactly what runs."""
        filters = self._filters(commit_sha, near, radius, branch)
        legs = [self._lexical(text), self._semantic(text)]
        window = max(50, size * 5)
        return {
            "size": size,
            "retriever": {"text_similarity_reranker": {
                "retriever": {"rrf": {"rank_window_size": window, "retrievers": [
                    {"standard": {"query": {"bool": {"must": [leg], "filter": filters}}}} for leg in legs]}},
                # the object's class + ALL its camera descriptions in one string (ingest pipeline
                # room-objects-rerank-text): the reranker reads only the first value of a field
                "field": "rerank_text",
                "inference_id": RERANK_ID,
                "inference_text": text,
                "rank_window_size": window,
            }},
            "collapse": {"field": "object_id", "inner_hits": {
                "name": "timeline", "size": 100, "sort": [{"@timestamp": "desc"}],
                "_source": ["commit_sha", "branch", "zone", "pose", "capture_id", "@timestamp"]}},
        }

    def lexical_only(self, text: str, commit_sha: str | None = None, size: int = 50,
                     branch: str | None = None) -> list[str]:
        """Object ids the BM25 leg matches at all, over class and every description. Exists to
        show what hybrid adds: BM25 reads "white ceramic cup" and still can't match "mug"."""
        r = self.es.search(index=self.objects, size=0,
                           query={"bool": {"must": [self._lexical(text)],
                                           "filter": self._filters(commit_sha, None, 0, branch)}},
                           aggs={"ids": {"terms": {"field": "object_id", "size": size}}})
        return [b["key"] for b in r["aggregations"]["ids"]["buckets"]]

    def semantic_only(self, text: str, commit_sha: str | None = None, size: int = 50,
                      branch: str | None = None) -> list[tuple[str, float]]:
        """(object_id, best score) from the semantic leg alone -- same clause, same filters as
        search_objects -- best first. Scores, not ids: kNN always returns neighbours, so
        "the vector leg found it" needs a cut on the score, and that cut is the caller's."""
        r = self.es.search(index=self.objects, size=size, _source=False, fields=["object_id"],
                           query={"bool": {"must": [self._semantic(text)],
                                           "filter": self._filters(commit_sha, None, 0, branch)}},
                           collapse={"field": "object_id"})
        return [(h["fields"]["object_id"][0], h["_score"]) for h in r["hits"]["hits"]]

    # ── an object through time (ES|QL) ───────────────────────────────────────

    def last_seen(self, object_id: str) -> dict | None:
        rows = self._esql(f"""
            FROM {self.objects}
            | WHERE object_id == ?id
            | SORT @timestamp DESC
            | LIMIT 1
            | KEEP object_id, commit_sha, branch, zone, pose.x, pose.y, pose.z, @timestamp""", id=object_id)
        return rows[0] if rows else None

    def lifetime(self, object_id: str) -> dict | None:
        rows = self._esql(f"""
            FROM {self.objects}
            | WHERE object_id == ?id
            | STATS first_seen = MIN(@timestamp), last_seen = MAX(@timestamp),
                    commits = COUNT_DISTINCT(commit_sha), zones = COUNT_DISTINCT(zone)
                    BY object_id
            | LIMIT 1""", id=object_id)
        return rows[0] if rows else None

    def commit_at(self, ts: datetime | str, branch: str | None = None,
                  strictly_before: bool = False) -> dict | None:
        """Wall-clock -> version time: the last commit at or before `ts`; `strictly_before=True`
        is `room restore --before T` ("the way it was before dinner": a commit made AT T is not
        before it). Pass the branch being restored -- another branch's commits are another room.
        Only event_type "commit" counts (not captures, chores, PRs, tidies). `ts` must carry a
        timezone. Ties on the timestamp resolve by sha, so the answer never flips between calls."""
        where = f"event_type == \"commit\" AND @timestamp {'<' if strictly_before else '<='} TO_DATETIME(?ts)"
        params = {"ts": esql_ts(ts)}
        if branch:
            where += " AND branch == ?branch"
            params["branch"] = branch
        rows = self._esql(f"""
            FROM {self.events}
            | WHERE {where}
            | SORT @timestamp DESC, commit_sha DESC
            | LIMIT 1
            | KEEP commit_sha, parent_sha, branch, message, author, @timestamp""", **params)
        return rows[0] if rows else None

    def moved_at(self, object_id: str, branch: str | None = None) -> dict | None:
        """Blame: the commit where `object_id`'s pose (or zone) last changed, walked back through
        parent_sha from its newest snapshot on `branch` (any branch if None) -- snapshot to
        snapshot, because a git "M" also fires on re-measured extents. `from` is None when the
        object appeared in that commit. `frame` is what the capture saw: the capture document
        and each camera's view of this object (the index stores no image URLs: frame_url is
        built from capture_id by whoever serves frames)."""
        docs = self.es.search(index=self.objects, size=1000, query={"term": {"object_id": object_id}},
                              _source=["commit_sha", "parent_sha", "branch", "author", "capture_id",
                                       "zone", "pose", "@timestamp"])["hits"]["hits"]
        by_sha = {d["_source"]["commit_sha"]: d["_source"] for d in docs}
        heads = sorted((d for d in by_sha.values() if not branch or d.get("branch") == branch),
                       key=lambda d: datetime.fromisoformat(d["@timestamp"].replace("Z", "+00:00")),
                       reverse=True)  # parsed: writers mix "…Z" and "….000Z"
        if not heads:
            return None
        where = lambda d: {"zone": d.get("zone"), "pose": d.get("pose")}  # noqa: E731
        cur, seen = heads[0], set()
        while True:
            seen.add(cur["commit_sha"])
            prev = by_sha.get(cur.get("parent_sha"))
            if prev is None or prev["commit_sha"] in seen or where(prev) != where(cur):
                break
            cur = prev
        event = self.es.search(index=self.events, size=1, _source=["message", "author"], query={"bool": {"filter": [
            {"term": {"commit_sha": cur["commit_sha"]}}, {"term": {"event_type": "commit"}}]}})["hits"]["hits"]
        return {"object_id": object_id,
                "moved_in": {"sha": cur["commit_sha"], "at": cur["@timestamp"], "capture_id": cur.get("capture_id"),
                             "branch": cur.get("branch"), "author": cur.get("author"),
                             "message": event[0]["_source"].get("message") if event else None},
                "from": where(prev) if prev is not None and prev["commit_sha"] not in seen else None,
                "to": where(cur),
                "frame": self._capture_frame(object_id, cur.get("capture_id"))}

    def _capture_frame(self, object_id: str, capture_id: str | None) -> dict | None:
        if not capture_id:
            return None
        cloud = self.es.search(index=self.clouds, size=1, query={"term": {"capture_id": capture_id}},
                               _source=["cloud_uri", "pose", "quality_ok", "cameras"])["hits"]["hits"]
        views = self.es.search(index=self.observations, size=20, sort=[{"camera": "asc"}], query={"bool": {"filter": [
            {"term": {"object_id": object_id}}, {"term": {"capture_id": capture_id}}]}},
            _source=["camera", "raw_x", "raw_y", "raw_z", "raw_description", "occluded", "confidence"])["hits"]["hits"]
        return {"capture_id": capture_id, "capture": cloud[0]["_source"] if cloud else None,
                "views": [v["_source"] for v in views]}

    # ── the mess: occluded or deleted? ───────────────────────────────────────

    def observation_history(self, object_id: str, window: str = "6h") -> dict:
        """Per-camera evidence for one object over the last `window`: how often each camera saw
        it, how often it was occluded, and how much its confidence wobbles."""
        r = self.es.search(
            index=self.observations, size=0,
            query={"bool": {"filter": [{"term": {"object_id": object_id}},
                                       {"range": {"@timestamp": {"gte": f"now-{window}"}}}]}},
            aggs={"captures": {"cardinality": {"field": "capture_id"}},
                  "cameras": {"terms": {"field": "camera", "size": 10}, "aggs": {
                      "occluded": {"filter": {"term": {"occluded": True}}},
                      "confidence": {"extended_stats": {"field": "confidence"}},
                      "last_seen": {"max": {"field": "@timestamp"}}}}})
        aggs = r["aggregations"]
        cameras = {}
        for b in aggs["cameras"]["buckets"]:
            c = b["confidence"]
            cameras[b["key"]] = {"sightings": b["doc_count"], "occluded": b["occluded"]["doc_count"],
                                 "confidence_min": c["min"], "confidence_max": c["max"],
                                 "confidence_std": c["std_deviation"],
                                 "last_seen": b["last_seen"]["value_as_string"]}
        return {"object_id": object_id, "captures": aggs["captures"]["value"], "cameras": cameras}

    # ── space ────────────────────────────────────────────────────────────────

    def objects_near(self, x: float, y: float, radius: float, commit_sha: str) -> list[dict]:
        """"What's within 50 cm of the lamp" at one commit, nearest first (cartesian metres)."""
        return self._esql(f"""
            FROM {self.objects}
            | WHERE commit_sha == ?sha
            | EVAL distance = ST_DISTANCE(position, TO_CARTESIANPOINT(?p))
            | WHERE distance <= ?r
            | SORT distance ASC
            | KEEP object_id, class, zone, distance
            | LIMIT 1000""", sha=commit_sha, p=f"POINT ({x} {y})", r=radius)

    def placement_collisions(self, commit_sha: str, box: dict, ignore: str | None = None) -> dict[str, int]:
        """Planning-tier check, once per diff: which objects occupy voxels inside the target
        footprint at `commit_sha`. Surface voxels (no object_id) are where things go, not
        obstacles. `ignore` is the object being moved."""
        must_not = [{"term": {"object_id": ignore}}] if ignore else []
        r = self.es.search(
            index=self.voxels, size=0,
            query={"bool": {"filter": [{"term": {"commit_sha": commit_sha}},
                                       {"exists": {"field": "object_id"}},
                                       {"shape": {"cell": {"shape": box, "relation": "intersects"}}}],
                            "must_not": must_not}},
            aggs={"objects": {"terms": {"field": "object_id", "size": 100}}})
        return {b["key"]: b["doc_count"] for b in r["aggregations"]["objects"]["buckets"]}

    def voxel_changes(self, sha_a: str, sha_b: str, level: str = "l3") -> dict[str, list[str]]:
        """Occupied cells that flipped between two commits, at 1 m (l3), 25 cm (l5) or 6.25 cm
        (full). Coarse first, then drill in: "the desk changed" -> "these 40 cells changed"."""
        field = VOXEL_LEVELS[level]
        keys = {"terms": {"field": field, "size": 20000}}
        r = self.es.search(index=self.voxels, size=0, aggs={
            "a": {"filter": {"term": {"commit_sha": sha_a}}, "aggs": {"k": keys}},
            "b": {"filter": {"term": {"commit_sha": sha_b}}, "aggs": {"k": keys}}})
        a = {b["key"] for b in r["aggregations"]["a"]["k"]["buckets"]}
        b = {b["key"] for b in r["aggregations"]["b"]["k"]["buckets"]}
        return {"added": sorted(b - a), "removed": sorted(a - b)}

    # ── analytics ────────────────────────────────────────────────────────────

    def zone_volatility(self) -> list[dict]:
        return self._esql(f"""
            FROM {self.events}
            | WHERE event_type == "commit"
            | MV_EXPAND zone
            | STATS commits = COUNT(*) BY zone
            | SORT commits DESC, zone ASC
            | LIMIT 1000""")

    def most_moved(self, limit: int = 10) -> list[dict]:
        return self._esql(f"""
            FROM {self.events}
            | WHERE event_type == "commit"
            | MV_EXPAND objects_moved
            | WHERE objects_moved IS NOT NULL
            | STATS moves = COUNT(*) BY object_id = objects_moved
            | SORT moves DESC, object_id ASC
            | LIMIT {int(limit)}""")

    def static_skeleton(self) -> list[str]:
        """Objects that have never moved: one distinct position across every commit they're in."""
        rows = self._esql(f"""
            FROM {self.objects}
            | STATS xs = COUNT_DISTINCT(pose.x), ys = COUNT_DISTINCT(pose.y),
                    commits = COUNT_DISTINCT(commit_sha) BY object_id
            | WHERE xs == 1 AND ys == 1 AND commits > 1
            | SORT object_id ASC
            | LIMIT 10000""")
        return [r["object_id"] for r in rows]

    def messiness_over_time(self, minutes: int = 30) -> list[dict]:
        """Detections, rejects and occlusions per time bucket: the step change when a judge
        moves the mug."""
        return self._esql(f"""
            FROM {self.observations}
            | EVAL occ = CASE(occluded, 1, 0)
            | STATS detections = COUNT(*), rejected = COUNT(rejected_reason), occluded = SUM(occ)
                    BY bucket = DATE_TRUNC({int(minutes)} minutes, @timestamp)
            | SORT bucket ASC
            | LIMIT 10000""")

    def telemetry_window(self, ts: datetime | str, seconds: float = 2.0) -> dict[str, dict]:
        """"Why was this diff wrong?": every signal's min/max/peak in the `seconds` before a
        capture. Peaks, not averages — the max tilt_rate is what rejects a capture.
        Only MIN/MAX touch `value`: once downsampled it is an aggregate_metric_double
        (min/max/sum/count per bucket), where MIN/MAX stay exact and an EVAL on it may not
        work. So the peak |value| is taken from low/high here, not ABS() in the query.
        `rows` is raw samples, or buckets once downsampled."""
        end = datetime.fromisoformat(esql_ts(ts).replace("Z", "+00:00"))
        rows = self._esql(f"""
            FROM {self.telemetry}
            | WHERE @timestamp > TO_DATETIME(?start) AND @timestamp <= TO_DATETIME(?end)
            | STATS low = MIN(value), high = MAX(value), rows = COUNT(*) BY signal
            | SORT signal ASC
            | LIMIT 1000""",
            start=esql_ts(end - timedelta(seconds=seconds)), end=esql_ts(end))
        return {r.pop("signal"): {**r, "peak": max(abs(r["low"]), abs(r["high"]))} for r in rows}

    # ── the Sentry join ──────────────────────────────────────────────────────

    def _across(self, field: str, value: str, size: int) -> dict[str, list[dict]]:
        searches = []
        for index in self.all:
            searches += [{"index": index, "ignore_unavailable": True},
                         {"query": {"term": {field: value}}, "size": size, "sort": [{"@timestamp": "asc"}]}]
        out = {}
        for index, resp in zip(self.all, self.es.msearch(searches=searches)["responses"]):
            if "error" in resp:  # a failed leg must not read as "no documents"
                raise RuntimeError(f"{index}: {resp['error']}")
            if resp["hits"]["hits"]:
                out[index] = [h["_source"] for h in resp["hits"]["hits"]]
        return out

    def trace_docs(self, trace_id: str, size: int = 100) -> dict[str, list[dict]]:
        """Sentry -> Elastic: every document the traced run wrote, grouped by index."""
        return self._across("sentry_trace_id", trace_id, size)

    def capture_docs(self, capture_id: str, size: int = 100) -> dict[str, list[dict]]:
        """A Sentry span's capture_id tag -> the exact documents that capture produced."""
        return self._across("capture_id", capture_id, size)
