"""One live-cluster test per query in queries.py, named test_<method>.

test_every_query_has_a_live_test enforces that: add a query without a test and it fails.
"""
from __future__ import annotations

import inspect
import json
import sys

import pytest
from datetime import datetime, timedelta

import queries
from queries import Queries, envelope
from world import AT_DESK_MUG, EMPTY_DESK_SPOT, TRACE


def ids(hits: list[dict]) -> list[str]:
    return [h["object_id"] for h in hits]


def when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_every_query_has_a_live_test():
    wanted = {f"test_{n}" for n, f in inspect.getmembers(Queries, inspect.isfunction)
              if not n.startswith("_")}
    have = {n for n in dir(sys.modules[__name__]) if n.startswith("test_")}
    assert not wanted - have, f"queries without a live test: {sorted(wanted - have)}"


# ── search ───────────────────────────────────────────────────────────────────

def test_search_objects(world):
    w, q = world
    # the acceptance test: "mug" finds the object whose descriptions only ever say "cup"
    cup_docs = [d for d in w.docs["room-objects"] if d["object_id"] == "cup_7e21"]
    assert "mug" not in json.dumps(cup_docs).lower()
    hits = q.search_objects("mug")
    assert "cup_7e21" in ids(hits)[:3], ids(hits)
    assert "cup_7e21" not in q.lexical_only("mug"), "BM25 read its descriptions and still missed it"
    assert "mug_a1b2" in ids(hits)
    assert len(ids(hits)) == len(set(ids(hits))), "collapse: one hit per object"

    mug = next(h for h in hits if h["object_id"] == "mug_a1b2")
    assert [t["commit_sha"] for t in mug["timeline"]] == [w.c3, w.c2, w.c1], "newest first"
    assert [t["capture_id"] for t in mug["timeline"]] == [w.capture[s] for s in (w.c3, w.c2, w.c1)]
    assert mug["latest"]["zone"] == "couch"


def test_search_objects_at_a_commit(world):
    w, q = world
    # class says "tool"; one view said "claw hammer", one "mallet" -- hybrid finds it...
    assert "tool_4f2a" in ids(q.search_objects("hammer", commit_sha=w.c2))[:3]
    # ...and filtering to the commit after it left is what makes it absent
    assert "tool_4f2a" not in ids(q.search_objects("hammer", commit_sha=w.c3))
    # movie-night only has events in the fixture: no object was ever snapshotted on it
    assert q.search_objects("mug", branch="movie-night") == []
    assert "mug_a1b2" in ids(q.search_objects("mug", branch="main"))


def test_search_objects_conversational(world):
    w, q = world
    # both legs rank the scissors first; the reranker must not undo it just because the object's
    # FIRST description ("orange plastic handles, steel blades") never says "scissors"
    for text in ("scissors", "where are my scissors", "where did I leave the scissors"):
        assert ids(q.search_objects(text))[0] == "scissors_9f3a", text
    assert ids(q.search_objects("where did I leave my keys"))[0] == "keys_7c2e"


def test_rerank_text_pipeline(world):
    w, q = world
    got = q.es.ingest.simulate(id="room-objects-rerank-text", docs=[{"_source": {
        "class": "scissors", "raw_description": ["orange plastic handles, steel blades", "a pair of scissors lying open"]}}])
    assert got["docs"][0]["doc"]["_source"]["rerank_text"] == \
        "scissors. orange plastic handles, steel blades. a pair of scissors lying open"
    stored = q.es.search(index=q.objects, size=1, query={"term": {"object_id": "scissors_9f3a"}})["hits"]["hits"][0]
    assert stored["_source"]["rerank_text"].startswith("scissors. orange plastic handles"), "applied at ingest"


def test_search_objects_near(world):
    w, q = world
    lamp_x, lamp_y = 0.80, 0.10
    found = set(ids(q.search_objects("cup", near=(lamp_x, lamp_y), radius=0.45)))
    assert found and found <= {"lamp_9c01", "mug_a1b2"}, found  # cup_7e21 sits outside the box


def test_lexical_only(world):
    w, q = world
    assert q.lexical_only("mug") == ["mug_a1b2"]  # BM25 alone never reaches cup_7e21
    assert q.lexical_only("porcelain") == ["cup_7e21"], "BM25 reads the descriptions, not just class"
    assert q.lexical_only("mallets") == ["tool_4f2a"], "english analyzer: mallets -> mallet"
    assert q.lexical_only("keys_7c2e") == ["keys_7c2e"]  # exact id


def test_hybrid_request(world):
    w, q = world
    # the printed request IS what search_objects sends: same body, same answer
    body = q.hybrid_request("mug")
    raw = q.es.search(index=q.objects, **body)["hits"]["hits"]
    assert [h["_source"]["object_id"] for h in raw] == ids(q.search_objects("mug"))


def test_resolve_object(world):
    w, q = world
    r = q.resolve_object("the thing I cut paper with")
    assert r["matches"][0]["object_id"] == "scissors_9f3a"
    assert r["matches"][0]["zone"] == "workbench" and r["matches"][0]["class"] == "scissors"
    assert r["margin"] == pytest.approx(r["matches"][0]["score"] - r["matches"][1]["score"])
    assert len(r["matches"]) == 5 and r["margin"] > 0
    assert q.resolve_object("where are my keys", k=3)["matches"][0]["object_id"] == "keys_7c2e"
    mug = q.resolve_object("mug", k=1)
    assert mug["matches"][0]["zone"] == "couch" and mug["margin"] is None  # current zone, one row


def test_semantic_only(world):
    w, q = world
    scored = q.semantic_only("mug")
    names = [oid for oid, _ in scored]
    assert names[0] == "mug_a1b2" and "cup_7e21" in names[:3], scored
    assert len(names) == len(set(names)), "one entry per object: its best-scoring doc"
    assert [s for _, s in scored] == sorted((s for _, s in scored), reverse=True)
    assert "tool_4f2a" not in [oid for oid, _ in q.semantic_only("hammer", commit_sha=w.c3)]


# ── an object through time ───────────────────────────────────────────────────

def test_last_seen(world):
    w, q = world
    row = q.last_seen("tool_4f2a")
    assert row["commit_sha"] == w.c2 and row["zone"] == "workbench"
    assert q.last_seen("never_existed") is None


def test_lifetime(world):
    w, q = world
    row = q.lifetime("tool_4f2a")
    assert row["commits"] == 2 and row["zones"] == 1
    assert when(row["first_seen"]) == w.t[w.c1]
    assert when(row["last_seen"]) == w.t[w.c2]


def test_commit_at(world):
    w, q = world
    between = w.t[w.c2] + timedelta(minutes=10)
    assert q.commit_at(between)["commit_sha"] == w.c2
    assert q.commit_at(w.t[w.c3])["commit_sha"] == w.c3, "at-or-before is inclusive"
    # the pick event 5 min after C3 is not a commit and must never be the answer
    assert q.commit_at(w.t[w.c3] + timedelta(minutes=6))["commit_sha"] == w.c3
    assert q.commit_at(w.now)["commit_sha"] == w.c4
    assert q.commit_at(w.now, branch="main")["commit_sha"] == w.c3
    assert q.commit_at(w.t[w.c1] - timedelta(seconds=1)) is None
    # `room restore --before T`: a commit made AT T is not "before" it
    assert q.commit_at(w.t[w.c3], branch="main", strictly_before=True)["commit_sha"] == w.c2
    assert q.commit_at(w.t[w.c3], branch="main")["commit_sha"] == w.c3


def test_moved_at(world):
    w, q = world
    mug = q.moved_at("mug_a1b2")
    assert mug["moved_in"]["sha"] == w.c2 and mug["moved_in"]["capture_id"] == "cap_0002"
    assert mug["moved_in"]["message"] == "afternoon: mug moved to the couch"
    assert mug["from"]["zone"] == "desk" and mug["to"]["zone"] == "couch"
    assert (mug["from"]["pose"]["x"], mug["to"]["pose"]["x"]) == (0.42, -1.2)
    assert mug["frame"]["capture"]["cloud_uri"] is None  # the capture doc exists; no real cloud file
    assert [v["camera"] for v in mug["frame"]["views"]] == ["cam0", "cam1", "cam2"], "each camera's view"
    keys = q.moved_at("keys_7c2e")
    assert keys["moved_in"]["sha"] == w.c3 and (keys["from"]["zone"], keys["to"]["zone"]) == ("shelf", "desk")
    lamp = q.moved_at("lamp_9c01")  # never moved: blame is where it first appeared
    assert lamp["moved_in"]["sha"] == w.c1 and lamp["from"] is None
    assert q.moved_at("mug_a1b2", branch="movie-night") is None  # no snapshot on that branch
    assert q.moved_at("never_existed") is None


# ── the mess ─────────────────────────────────────────────────────────────────

def test_observation_history(world):
    w, q = world
    keys = q.observation_history("keys_7c2e")
    assert keys["captures"] == 3
    assert set(keys["cameras"]) == {"cam0", "cam1", "cam2"}
    assert keys["cameras"]["cam1"]["occluded"] == 1
    assert keys["cameras"]["cam0"]["occluded"] == 0
    assert all(c["sightings"] == 3 for c in keys["cameras"].values())
    assert q.observation_history("tool_4f2a")["captures"] == 2  # gone at C3


# ── space ────────────────────────────────────────────────────────────────────

def test_objects_near(world):
    w, q = world
    rows = q.objects_near(0.80, 0.10, 0.5, commit_sha=w.c1)
    assert [r["object_id"] for r in rows] == ["lamp_9c01", "mug_a1b2"]  # lamp itself at 0 m
    assert abs(rows[1]["distance"] - 0.3883) < 1e-3
    assert [r["object_id"] for r in q.objects_near(0.80, 0.10, 0.5, commit_sha=w.c2)] == ["lamp_9c01"]


def test_placement_collisions(world):
    w, q = world
    x, y, _ = AT_DESK_MUG
    box = envelope(x - 0.05, y - 0.05, x + 0.05, y + 0.05)
    mug_cells = w.count("room-voxels", commit_sha=w.c1, object_id="mug_a1b2")
    assert q.placement_collisions(w.c1, box) == {"mug_a1b2": mug_cells}
    assert q.placement_collisions(w.c1, box, ignore="mug_a1b2") == {}
    assert q.placement_collisions(w.c2, box) == {}, "the mug left the desk at C2"
    ex, ey = EMPTY_DESK_SPOT
    assert q.placement_collisions(w.c1, envelope(ex - 0.05, ey - 0.05, ex + 0.05, ey + 0.05)) == {}, \
        "desk-surface voxels are where things go, not obstacles"


def test_voxel_changes(world):
    w, q = world
    for level, field in queries.VOXEL_LEVELS.items():
        a, b = w.voxel_keys(w.c2, field), w.voxel_keys(w.c3, field)
        got = q.voxel_changes(w.c2, w.c3, level)
        assert got == {"added": sorted(b - a), "removed": sorted(a - b)}, level
    assert q.voxel_changes(w.c1, w.c1, "full") == {"added": [], "removed": []}


def test_voxel_changes_is_complete_past_one_aggregation_page(world, monkeypatch):
    """The diff must not silently lose cells when a commit outgrows one page of buckets.

    This was a `terms` agg with size 20000: correct at a 6.25 cm leaf (5,827 cells per commit) and
    WRONG at 3.125 cm (~26,400), where it returned the first 20,000 and reported the rest as
    removed. Proven against a real depth-8 commit: 26,385 cells in, 20,000 buckets out,
    sum_other_doc_count 6,385, and nothing raised. `composite` pages instead.

    Forcing the page size down to 2 makes the fixture's own commits span many pages, so this
    exercises the paging rather than needing 20,000 documents to do it.
    """
    w, q = world
    real = q.es.search

    def small_pages(*a, **kw):
        aggs = kw.get("aggs") or {}
        if "k" in aggs and "composite" in aggs["k"]:
            aggs["k"]["composite"]["size"] = 2      # several pages, same answer
        return real(*a, **kw)

    monkeypatch.setattr(q.es, "search", small_pages)
    a, b = w.voxel_keys(w.c2, "voxel_key"), w.voxel_keys(w.c3, "voxel_key")
    assert len(a) > 2, "the fixture must span more than one page for this to mean anything"
    assert q.voxel_changes(w.c2, w.c3, "full") == {"added": sorted(b - a), "removed": sorted(a - b)}


# ── analytics ────────────────────────────────────────────────────────────────

def test_zone_volatility(world):
    w, q = world
    rows = q.zone_volatility()
    assert [(r["zone"], r["commits"]) for r in rows] == [
        ("desk", 3), ("couch", 2), ("shelf", 2), ("workbench", 2)]


def test_most_moved(world):
    w, q = world
    assert [(r["object_id"], r["moves"]) for r in q.most_moved()] == [("mug_a1b2", 2), ("keys_7c2e", 1)]


def test_static_skeleton(world):
    w, q = world
    assert q.static_skeleton() == ["book_e5f6", "cup_7e21", "lamp_9c01", "scissors_9f3a", "tool_4f2a"]


def test_messiness_over_time(world):
    w, q = world
    rows = q.messiness_over_time(minutes=30)
    obs = w.docs["room-observations"]
    assert sum(r["detections"] for r in rows) == len(obs)
    assert sum(r["rejected"] for r in rows) == sum(o["rejected_reason"] is not None for o in obs)
    assert sum(r["occluded"] for r in rows) == 1
    assert len(rows) == 3, "one bucket per capture: captures are an hour apart"


def test_telemetry_window(world):
    w, q = world
    sig = q.telemetry_window(w.shutter, seconds=2.0)
    assert set(sig) == {"tilt_rate", "pitch", "odom_residual"}
    assert sig["tilt_rate"]["peak"] == 0.2395, "the peak survives; an average would hide it"
    assert sig["pitch"]["low"] == -0.08 and sig["pitch"]["peak"] == 0.08
    assert all(s["rows"] == 100 for s in sig.values()), "2 s at 50 Hz, window (t-2s, t]"


# ── the Sentry join ──────────────────────────────────────────────────────────

def test_trace_docs(world):
    w, q = world
    got = q.trace_docs(TRACE)
    want = {q.objects: w.count("room-objects", sentry_trace_id=TRACE),
            q.clouds: w.count("room-clouds", sentry_trace_id=TRACE),
            q.observations: w.count("room-observations", sentry_trace_id=TRACE),
            q.events: w.count("room-events", sentry_trace_id=TRACE)}
    assert {i: len(d) for i, d in got.items()} == want
    assert all(d["sentry_url"].endswith(f"/{TRACE}/") for docs in got.values() for d in docs)
    assert q.trace_docs("0" * 32) == {}


def test_capture_docs(world):
    w, q = world
    got = q.capture_docs("cap_0003")
    want = {q.objects: w.count("room-objects", capture_id="cap_0003"),
            q.clouds: 1,
            q.observations: w.count("room-observations", capture_id="cap_0003"),
            q.events: w.count("room-events", capture_id="cap_0003")}
    assert {i: len(d) for i, d in got.items()} == want
    assert got[q.clouds][0]["quality_ok"] is False and got[q.clouds][0]["tilt_rate_max"] == 0.2395
