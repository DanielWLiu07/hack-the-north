"""associate.py: the docs/20 Part 4 decision table, run commit after commit through the real
roomctl.state working tree. The Elasticsearch leg is a stand-in with the same interface
(reidentify) that scores by shared words, which is enough to test the DECISIONS; the
query itself is checked separately against its documented shape."""
import re
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import associate  # noqa: E402  (puts the repo root on sys.path)
from associate import ADDED, MOVED, REMOVED, RETURNED, UNCHANGED, UNOBSERVED  # noqa: E402
from cluster import Instance  # noqa: E402
from describe import ViewDescription  # noqa: E402
from merge import MergedObject  # noqa: E402
from roomctl.state import Extents, ObjectRecord, Pose, read_tree  # noqa: E402
from serialize import serialize  # noqa: E402  (the other perception session's stage 11)

STOP = {"a", "an", "the", "with", "and", "of", "on"}


# ── scene helpers ────────────────────────────────────────────────────────────

def _obj(centre, size, label, texts, color, seed=0):
    """A merged object seen by len(texts) cameras, each with its own description."""
    rng = np.random.default_rng(seed)
    views = []
    for k, text in enumerate(texts):
        pts = rng.uniform(-0.5, 0.5, (300, 3)) * size + centre
        views.append(Instance(points=pts, label=label, source="segment", camera=f"cam{k}", score=0.9,
                              color=color, description=ViewDescription(f"cam{k}", None, text, "fake", 1)))
    return MergedObject(views)


MUG_WORDS = ["a blue ceramic mug", "cup with handle, chipped", "cylindrical container, dark"]
MUG_BACK_WORDS = ["blue coffee mug, chipped rim", "ceramic cup with a handle", "dark blue cylinder"]


def mug(at=(0.40, 0.20, 0.80), words=MUG_WORDS, seed=1):
    return _obj(np.array(at), np.array([0.09, 0.09, 0.10]), "cup", words, "#2b4c7e", seed)


def book(at=(0.00, -0.30, 0.77), seed=2):
    return _obj(np.array(at), np.array([0.20, 0.15, 0.04]), "book",
                ["red hardcover book", "a closed book, red cover", "flat red rectangular object"], "#a01818", seed)


def stapler(at=(0.40, 0.20, 0.80)):
    return _obj(np.array(at), np.array([0.15, 0.04, 0.06]), "stapler",
                ["black metal stapler", "office stapler", "long black tool"], "#111111", 3)


# ── stand-ins for the other stages ───────────────────────────────────────────

class FakeHistory:
    """Every committed appearance, like room-objects. reidentify() ranks by shared words
    (overlap coefficient) where ES would use BM25 + Jina + rerank."""

    def __init__(self):
        self.words: dict[str, set[str]] = {}
        self.records: dict[str, ObjectRecord] = {}

    def index(self, assocs, records):
        for a, rec in zip(assocs, records):
            self.records[rec.id] = rec
            words = self.words.setdefault(rec.id, set(_words(rec.cls)))
            if a.obj is not None:
                for d in a.obj.descriptions:
                    words |= _words(d.text)

    def reidentify(self, obj, exclude):
        q = set().union(*(_words(d.text) for d in obj.descriptions), *(_words(l) for l in obj.labels))
        out = []
        for oid, w in self.words.items():
            if oid not in exclude and q and w:
                out.append(associate.Candidate(oid, len(q & w) / min(len(q), len(w)), self.records[oid]))
        return sorted(out, key=lambda c: (-c.score, c.object_id))


def _words(text):
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if w not in STOP}


def commit(repo, objects, n, history, occluded=lambda r: False):
    """One scan -> associate against HEAD -> the REAL serialize.py -> index the snapshot."""
    head = read_tree(repo)
    assocs = associate.associate(objects, head, capture_id=f"cap_{n:04d}", history=history,
                                 occluded=occluded, now=f"2026-09-19T14:{n:02d}:00Z")
    measured, unobserved = associate.for_serialize(assocs, default_zone="desk")
    records = serialize(repo, measured, head, unobserved)
    if history is not None:
        by_id = {a.object_id: a for a in assocs}
        history.index([by_id[r.id] for r in records], records)
    return {a.object_id: a for a in assocs}


def _show(n, assocs, repo):
    files = sorted(p.name for p in repo.rglob("*.yaml"))
    print(f"  commit {n}: " + ", ".join(f"{a.cls} {a.object_id} {a.verdict}" for a in assocs.values())
          + f"   | files: {files}")


def _id_of(assocs, cls):
    (a,) = [a for a in assocs.values() if a.cls == cls]
    return a


# ── the acceptance test ──────────────────────────────────────────────────────

def test_object_that_leaves_for_three_commits_and_returns_keeps_its_id(tmp_path):
    """ACCEPTANCE: gone for 3 commits, back somewhere else, described in different words ->
    the ORIGINAL object_id, first_seen, class and colour. That is the `returned` row, and it
    needs the history search: nothing in HEAD is left to match against."""
    history = FakeHistory()

    c0 = commit(tmp_path, [mug(), book()], 0, history)
    print()
    _show(0, c0, tmp_path)
    first = _id_of(c0, "cup")
    book_id = _id_of(c0, "book").object_id
    assert first.verdict == ADDED
    assert (tmp_path / f"zones/desk/{first.object_id}.yaml").exists()

    for n in (1, 2, 3):                                  # the mug is gone for three commits
        cn = commit(tmp_path, [book()], n, history)
        _show(n, cn, tmp_path)
        assert first.object_id not in read_tree(tmp_path)
        assert cn[book_id].verdict == UNCHANGED
        if n == 1:
            assert cn[first.object_id].verdict == REMOVED

    back = commit(tmp_path, [book(), mug(at=(1.10, 0.60, 0.80), words=MUG_BACK_WORDS, seed=9), stapler()], 4, history)
    _show(4, back, tmp_path)
    a = _id_of(back, "cup")
    print(f"  original {first.object_id} first_seen {first.first_seen}  ->  back as {a.object_id} "
          f"first_seen {a.first_seen} colour {a.color}")
    assert a.verdict == RETURNED
    assert a.object_id == first.object_id
    assert (a.first_seen, a.cls, a.color) == (first.first_seen, "cup", "#2b4c7e")   # carried, not re-measured
    assert read_tree(tmp_path)[first.object_id].first_seen == "2026-09-19T14:00:00Z"
    # the newcomer at the mug's OLD spot does not inherit its id
    s = _id_of(back, "stapler")
    assert s.verdict == ADDED and s.object_id != first.object_id


def test_without_the_history_search_the_returning_object_gets_a_new_id(tmp_path):
    """The control: same story, no Elasticsearch leg. Proves the id above wasn't luck."""
    c0 = commit(tmp_path, [mug(), book()], 0, None)
    for n in (1, 2, 3):
        commit(tmp_path, [book()], n, None)
    back = commit(tmp_path, [book(), mug(at=(1.10, 0.60, 0.80), words=MUG_BACK_WORDS, seed=9)], 4, None)
    assert _id_of(back, "cup").verdict == ADDED
    assert _id_of(back, "cup").object_id != _id_of(c0, "cup").object_id


# ── the rest of the decision table ───────────────────────────────────────────

def test_rescan_of_an_unchanged_room_is_unchanged_and_byte_identical(tmp_path):
    commit(tmp_path, [mug(), book()], 0, FakeHistory())
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.yaml")}
    again = commit(tmp_path, [mug(seed=5), book(seed=6)], 1, FakeHistory())
    assert {a.verdict for a in again.values()} == {UNCHANGED}
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.yaml")} == before


def test_small_move_and_far_move_both_keep_the_id(tmp_path):
    history = FakeHistory()
    mug_id = _id_of(commit(tmp_path, [mug(), book()], 0, history), "cup").object_id
    near = commit(tmp_path, [mug(at=(0.46, 0.20, 0.80)), book()], 1, history)
    assert near[mug_id].verdict == MOVED
    # 2 m in one commit: past the 1.5 m gate, so the history search has to find it
    far = commit(tmp_path, [mug(at=(2.40, 0.40, 0.80), words=MUG_BACK_WORDS), book()], 2, history)
    assert far[mug_id].verdict == MOVED
    assert len(far) == 2                                   # not a delete plus an add


def test_a_returning_lookalike_of_another_colour_does_not_take_the_id(tmp_path):
    """Live, the reranker can't tell a return from a newcomer by score (see RETURN_MIN_SCORE),
    so colour vetoes: same words, same size, red instead of blue -> a new object."""
    history = FakeHistory()
    mug_id = _id_of(commit(tmp_path, [mug(), book()], 0, history), "cup").object_id
    commit(tmp_path, [book()], 1, history)
    red = _obj(np.array([1.1, 0.6, 0.8]), np.array([0.09, 0.09, 0.10]), "cup", MUG_BACK_WORDS, "#b02020", 9)
    back = commit(tmp_path, [book(), red], 2, history)
    assert associate.delta_e("#b02020", "#2b4c7e") > associate.COLOR_VETO
    assert _id_of(back, "cup").verdict == ADDED and _id_of(back, "cup").object_id != mug_id


def test_a_different_object_in_the_same_spot_does_not_steal_the_id(tmp_path):
    history = FakeHistory()
    mug_id = _id_of(commit(tmp_path, [mug()], 0, history), "cup").object_id
    swap = commit(tmp_path, [book(at=(0.40, 0.20, 0.77))], 1, history)
    assert swap[mug_id].verdict == REMOVED
    assert _id_of(swap, "book").verdict == ADDED


def test_occluded_object_is_unobserved_and_carried_forward(tmp_path):
    history = FakeHistory()
    mug_id = _id_of(commit(tmp_path, [mug(), book()], 0, history), "cup").object_id
    before = (tmp_path / f"zones/desk/{mug_id}.yaml").read_bytes()
    c1 = commit(tmp_path, [book()], 1, history, occluded=lambda r: r.id == mug_id)
    assert c1[mug_id].verdict == UNOBSERVED
    assert (tmp_path / f"zones/desk/{mug_id}.yaml").read_bytes() == before


def test_near_square_object_keeps_its_committed_yaw():
    """A 10 x 8 cm footprint sits on the round/not-round line and flips between yaw 0 and its
    real yaw scan to scan. Matched to HEAD, it keeps the committed axis instead."""
    rng = np.random.default_rng(0)
    p = rng.uniform(-0.5, 0.5, (3000, 3)) * [0.10, 0.08, 0.10]
    c, s = np.cos(np.radians(40)), np.sin(np.radians(40))
    p = p @ np.array([[c, s, 0], [-s, c, 0], [0, 0, 1]]) + [0.4, 0.2, 0.8]
    obj = MergedObject([Instance(points=p, label="box", camera="cam0", color="#886644")])
    assert associate.YAW_BAND[0] <= obj.footprint_aspect() < associate.YAW_BAND[1]
    head = {"box_0001": ObjectRecord("box_0001", "box", "desk", Pose(0.40, 0.20, 0.80, 40),
                                     Extents(0.10, 0.08, 0.10), "#886644", "2026-09-19T14:00:00Z")}
    (a,) = associate.associate([obj], head, "cap_x", occluded=lambda r: False)
    assert a.object_id == "box_0001" and a.verdict == UNCHANGED
    assert a.yaw == pytest.approx(40.0)
    assert a.extents[:2] == pytest.approx([0.10, 0.08], abs=0.01)   # measured along the kept axis


# ── the Elasticsearch leg ────────────────────────────────────────────────────

def test_es_query_is_the_documented_hybrid_retriever():
    class ES:
        def search(self, **kw):
            self.kw = kw
            src = {"object_id": "cup_1a2b", "class": "cup", "zone": "desk", "color": "#2b4c7e",
                   "first_seen": "2026-09-19T14:00:00.000Z", "commit_sha": "abc",
                   "pose": {"x": 0.4, "y": 0.2, "z": 0.8, "yaw": 185.0},
                   "extents": {"x": 0.09, "y": 0.09, "z": 0.1}}
            return {"hits": {"hits": [{"_score": 0.9, "_source": src}, {"_score": 0.7, "_source": src}]}}

    es = ES()
    got = associate.ESHistory(es).reidentify(mug(), exclude={"book_0001"})
    r = es.kw["retriever"]["text_similarity_reranker"]
    assert r["field"] == "raw_description" and r["inference_id"] == "jina-rerank"
    legs = r["retriever"]["rrf"]["retrievers"]
    # found live (serverless 9.6): rrf defaults its window to 10 and ES then rejects the query
    assert r["retriever"]["rrf"]["rank_window_size"] >= r["rank_window_size"] >= es.kw["size"]
    assert legs[0]["standard"]["query"]["bool"]["must"][0] == {"match": {"class": "cup"}}
    sem = legs[1]["standard"]["query"]["bool"]["must"][0]["semantic"]
    assert sem["field"] == "raw_description" and all(t in sem["query"] for t in MUG_WORDS)
    assert legs[1]["standard"]["query"]["bool"]["must_not"] == [{"terms": {"object_id": ["book_0001"]}}]
    assert es.kw["index"] == "room-objects"
    assert [c.object_id for c in got] == ["cup_1a2b"]                  # best hit per object
    assert got[0].record.first_seen == "2026-09-19T14:00:00Z" and got[0].record.pose.yaw == 5


def test_two_returning_objects_are_assigned_jointly_not_first_come():
    """Live, a returning metal cup (queried first) took a returning spoon's id, and the spoon,
    its id gone, became `added`. The cup's best guess is the spoon's id at 0.90, but the
    spoon matches it at 0.95 and the cup matches its own at 0.80: jointly, both get their own."""
    rec = lambda oid, cls: ObjectRecord(oid, cls, "desk", Pose(0.4, 0.2, 0.8, 0), Extents(0.09, 0.09, 0.10),
                                        "#2b4c7e", "2026-09-19T14:00:00Z")
    spoon_rec, cup_rec = rec("spoon_aaaa", "spoon"), rec("cup_bbbb", "cup")
    cup_back, spoon_back = mug(seed=1), mug(at=(0.9, 0.2, 0.8), seed=2)
    scores = {id(cup_back): [(spoon_rec, 0.90), (cup_rec, 0.80)],
              id(spoon_back): [(spoon_rec, 0.95), (cup_rec, 0.10)]}

    class Scripted:
        def reidentify(self, obj, exclude):
            return [associate.Candidate(r.id, sc, r) for r, sc in scores[id(obj)] if r.id not in exclude]

    got = {id(a.obj): a for a in associate.associate([cup_back, spoon_back], {}, "cap_x", history=Scripted(),
                                                     occluded=lambda r: False)}
    assert (got[id(cup_back)].verdict, got[id(cup_back)].object_id) == (RETURNED, "cup_bbbb")
    assert (got[id(spoon_back)].verdict, got[id(spoon_back)].object_id) == (RETURNED, "spoon_aaaa")


# ── G2 on the real chain: debounced removal, and `moved` == what settle() writes ──

from associate import MISSED  # noqa: E402

ZONES2 = {"desk": {"min": [-0.2, -0.6, 0.6], "max": [1.2, 0.6, 1.2]},
          "shelf": {"min": [-0.2, 0.6, 0.6], "max": [1.2, 1.4, 1.2]}}


def scan(repo, objects, n, misses=None, occluded=lambda r: False, head=None):
    """One scan the way pipeline.scan_into does it, plus the persisted miss counts. `head` is the
    COMMITTED state; by default the tree as last written (i.e. every scan commits)."""
    head = read_tree(repo) if head is None else head
    assocs = associate.associate(objects, head, f"cap_{n:04d}", occluded=occluded, zones=ZONES2,
                                 misses=misses, now=f"2026-09-19T15:{n:02d}:00Z")
    measured, carried = associate.for_serialize(assocs, zones=ZONES2)
    serialize(repo, measured, head, carried)
    return {a.object_id: a for a in assocs}, associate.next_misses(assocs, misses)


def test_one_missed_scan_keeps_the_file_two_in_a_row_remove_it(tmp_path):
    """docs/10 P15: a small object the segmenter missed in 3 of 11 scans lost its file each time."""
    c0, m = scan(tmp_path, [mug(), book()], 0, misses={})
    mug_id = _id_of(c0, "cup").object_id
    before = (tmp_path / f"zones/desk/{mug_id}.yaml").read_bytes()

    c1, m = scan(tmp_path, [book()], 1, misses=m)                  # missed once
    assert c1[mug_id].verdict == MISSED and "1/2" in c1[mug_id].note
    assert (tmp_path / f"zones/desk/{mug_id}.yaml").read_bytes() == before and m == {mug_id: 1}

    c2, m = scan(tmp_path, [book()], 2, misses=m)                  # missed again: really gone
    assert c2[mug_id].verdict == REMOVED
    assert not (tmp_path / f"zones/desk/{mug_id}.yaml").exists()


def test_a_removal_stays_removed_until_the_object_is_seen(tmp_path):
    """G2 seed 7: once removed, the count reset, so the next scan said `missed` (file back),
    then `removed` (file gone) -- and an occluded scan resurrected it. The tree flip-flopped
    against an unchanged HEAD. Removed now sticks until the object is seen again."""
    c0, m = scan(tmp_path, [mug(), book()], 0, misses={})
    mug_id = _id_of(c0, "cup").object_id
    head = read_tree(tmp_path)                                      # committed once; scans don't commit
    for n in (1, 2):
        c, m = scan(tmp_path, [book()], n, misses=m, head=head)
    assert c[mug_id].verdict == REMOVED
    for n, occl in ((3, False), (4, True), (5, False)):             # still gone, even when hidden
        c, m = scan(tmp_path, [book()], n, misses=m, head=head,
                    occluded=(lambda r: r.id == mug_id) if occl else (lambda r: False))
        assert c[mug_id].verdict == REMOVED, n
        assert not (tmp_path / f"zones/desk/{mug_id}.yaml").exists()
    c, m = scan(tmp_path, [mug(seed=7), book()], 6, misses=m, head=head)   # back where it was
    assert c[mug_id].verdict == UNCHANGED and m == {}
    assert (tmp_path / f"zones/desk/{mug_id}.yaml").exists()


def test_seen_again_after_one_miss_clears_the_count(tmp_path):
    c0, m = scan(tmp_path, [mug(), book()], 0, misses={})
    mug_id = _id_of(c0, "cup").object_id
    _, m = scan(tmp_path, [book()], 1, misses=m)
    c2, m = scan(tmp_path, [mug(seed=4), book()], 2, misses=m)
    assert c2[mug_id].verdict == UNCHANGED and m == {}


def test_occlusion_is_not_a_miss(tmp_path):
    c0, m = scan(tmp_path, [mug(), book()], 0, misses={})
    mug_id = _id_of(c0, "cup").object_id
    _, m = scan(tmp_path, [book()], 1, misses=m)                   # one real miss
    c2, m2 = scan(tmp_path, [book()], 2, misses=m, occluded=lambda r: r.id == mug_id)
    assert c2[mug_id].verdict == UNOBSERVED and m2 == m            # explained: count unchanged


def test_misses_persist_next_to_scan_json(tmp_path):
    assert associate.load_misses(tmp_path) == {}
    associate.save_misses(tmp_path, {"mug_a1b2": 1})
    assert (tmp_path / ".git" / "gitspace" / "misses.json").is_file()
    assert associate.load_misses(tmp_path) == {"mug_a1b2": 1}


@pytest.mark.parametrize("dx, want", [(0.03, UNCHANGED), (0.06, MOVED)])
def test_moved_verdict_is_settles_call(tmp_path, dx, want):
    """roomctl.state.MOVE_M = 5 cm: untouched objects wander <= 3 cm between single-view scans."""
    c0, _ = scan(tmp_path, [mug(), book()], 0)
    mug_id = _id_of(c0, "cup").object_id
    before = (tmp_path / f"zones/desk/{mug_id}.yaml").read_bytes()
    c1, _ = scan(tmp_path, [mug(at=(0.40 + dx, 0.20, 0.80), seed=5), book()], 1)
    assert c1[mug_id].verdict == want
    changed = (tmp_path / f"zones/desk/{mug_id}.yaml").read_bytes() != before
    assert changed == (want == MOVED)                              # verdict and file agree


def test_a_zone_change_is_moved_even_when_short(tmp_path):
    c0, _ = scan(tmp_path, [mug(at=(0.40, 0.59, 0.80)), book()], 0)
    mug_id = _id_of(c0, "cup").object_id
    # 4.4 cm: under MOVE_M, but past the desk's edge (0.60) plus ZONE_HYST (3 cm): onto the shelf
    c1, _ = scan(tmp_path, [mug(at=(0.40, 0.634, 0.80), seed=5), book()], 1)
    assert c1[mug_id].verdict == MOVED
    new = read_tree(tmp_path)[mug_id]
    assert new.zone == "shelf" and (tmp_path / f"zones/shelf/{mug_id}.yaml").exists()
    assert abs(new.pose.y - 0.59) < 0.05                             # the zone, not the distance


def test_a_short_move_inside_the_zone_hysteresis_stays_put(tmp_path):
    """4 cm across the desk/shelf line but within ZONE_HYST: no rename, no phantom diff."""
    c0, _ = scan(tmp_path, [mug(at=(0.40, 0.58, 0.80)), book()], 0)
    mug_id = _id_of(c0, "cup").object_id
    c1, _ = scan(tmp_path, [mug(at=(0.40, 0.62, 0.80), seed=5), book()], 1)
    assert c1[mug_id].verdict == UNCHANGED and read_tree(tmp_path)[mug_id].zone == "desk"


def test_a_yaw_only_change_is_unchanged_like_the_file(tmp_path):
    """settle() ignores yaw: the verdict must too, or it says `moved` over a byte-identical file."""
    long_box = lambda deg, seed: MergedObject([Instance(
        points=(np.random.default_rng(seed).uniform(-0.5, 0.5, (400, 3)) * [0.20, 0.05, 0.05])
        @ np.array([[np.cos(np.radians(deg)), np.sin(np.radians(deg)), 0],
                    [-np.sin(np.radians(deg)), np.cos(np.radians(deg)), 0], [0, 0, 1]]) + [0.4, 0.2, 0.8],
        label="ruler", camera="cam0", color="#886644")])
    c0, _ = scan(tmp_path, [long_box(0, 1)], 0)
    rid = _id_of(c0, "ruler").object_id
    before = (tmp_path / f"zones/desk/{rid}.yaml").read_bytes()
    c1, _ = scan(tmp_path, [long_box(30, 2)], 1)
    assert c1[rid].verdict == UNCHANGED
    assert (tmp_path / f"zones/desk/{rid}.yaml").read_bytes() == before
