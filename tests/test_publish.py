"""The post-commit hook (docs/10 GAP 1): every commit reaches Elasticsearch, or the spool.

The boundary is mocked: a fake client and a fake bulk writer. Nothing here opens a socket —
and with a parked key the hook must not even build a client.
"""
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("elasticsearch")  # elastic/ingest.py imports it; the venv has it
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fake.scene_gen import FakeRoom  # noqa: E402
from roomctl import publish  # noqa: E402
from roomctl.cli import main  # noqa: E402
from roomctl.repo import Repo  # noqa: E402

TRACE = {"sentry_trace_id": "a" * 32, "sentry_span_id": "b" * 16}


class FakeES:
    def __init__(self, up=True):
        self.up, self.pings = up, 0

    def info(self):
        self.pings += 1
        if not self.up:
            raise ConnectionError("no route to host")
        return {"version": {"number": "9.6.0"}}


class FakeWrite:
    def __init__(self, reject=()):
        self.reject, self.calls = set(reject), []

    def __call__(self, es, actions):
        self.calls.append(actions)
        tally = {}
        for a in actions:
            t = tally.setdefault(a["_index"], [0, 0, []])
            if a["_source"].get("object_id") in self.reject:
                t[2].append("strict_dynamic_mapping_exception: nope")
            else:
                t[0] += 1
        return tally


@pytest.fixture
def repo(tmp_path, monkeypatch):
    import obs
    monkeypatch.setattr(obs, "trace_fields", lambda: dict(TRACE))
    room = FakeRoom(tmp_path / "room.git", quiet=True)
    room.commit("clean_bench")
    room.scan("messy_bench")  # a dirty tree: three changes on disk
    return Repo(tmp_path / "room.git")


def objects(actions):
    return [a for a in actions if a["_index"] == "room-objects"]


def test_docs_come_from_the_committed_tree_not_the_working_tree(repo):
    repo.git("add", "zones/desk/mug_a1b2.yaml")          # stage only the mug
    c = repo.commit("just the mug")
    acts = publish.commit_actions(repo, c, scan={})
    ids = {a["_source"]["object_id"] for a in objects(acts)}
    assert "scissors_9f3a" not in ids, "untracked in the commit, so absent from its snapshot"
    assert "marker_c3d4" in ids, "deleted on disk but not in this commit: still in the snapshot"
    mug = next(a["_source"] for a in objects(acts) if a["_source"]["object_id"] == "mug_a1b2")
    assert mug["pose"]["x"] == 0.61 and mug["commit_sha"] == c.sha


def test_git_id_becomes_object_id_and_every_doc_carries_the_trace(repo):
    c = repo.commit("afternoon")
    acts = publish.commit_actions(repo, c, scan={"capture_id": "cap_0042"})
    for a in objects(acts):
        src = a["_source"]
        assert "id" not in src and src["object_id"] and a["_id"] == f"{c.sha}:{src['object_id']}"
        assert src["sentry_trace_id"] == TRACE["sentry_trace_id"] and src["capture_id"] == "cap_0042"
    ev = next(a for a in acts if a["_index"] == "room-events")
    assert ev["_op_type"] == "create" and ev["_source"]["commit_sha"] == c.sha
    assert set(ev["_source"]["objects_moved"]) == {"mug_a1b2"}
    assert ev["_source"]["sentry_trace_id"] == TRACE["sentry_trace_id"]


def test_what_only_the_scan_knows_rides_along(repo):
    publish.stage_scan(repo, "cap_0099", "2026-09-18T22:00:00Z",
                       {"mug_a1b2": {"confidence": 0.9, "observed_by": ["cam0", "cam2"],
                                     "raw_description": ["a blue ceramic mug", "cup with handle"],
                                     "point_count": 4200}})
    c = repo.commit("with meta")
    mug = next(a["_source"] for a in objects(publish.commit_actions(repo, c))
               if a["_source"]["object_id"] == "mug_a1b2")
    assert mug["confidence"] == 0.9 and mug["observed_by"] == ["cam0", "cam2"]
    assert mug["capture_id"] == "cap_0099" and len(mug["raw_description"]) == 2


def test_a_parked_key_builds_no_client_and_spools(repo, monkeypatch):
    import elasticsearch
    monkeypatch.setattr(elasticsearch, "Elasticsearch", lambda *a, **k: pytest.fail("built a client"))
    monkeypatch.setenv("ELASTIC_URL", "https://example.es.us-east-1.aws.elastic.cloud:443")
    monkeypatch.setenv("ELASTIC_API_KEY", "# parked until 01:00")
    monkeypatch.setenv("ROOM_GIT_PATH", str(repo.path))   # this repo IS the room
    c = repo.commit("afternoon")
    res = publish.publish_commit(repo, c)
    assert res.spooled == 13 and "unset or parked" in res.reason
    assert (repo.path / ".git" / "gitspace" / "spool" / f"{c.sha}.json").is_file()


def test_an_unreachable_cluster_spools_and_flush_sends_it_later(repo):
    c = repo.commit("afternoon")
    write = FakeWrite()
    assert publish.publish_commit(repo, c, es=FakeES(up=False), write=write).spooled == 13
    assert not write.calls
    sent = publish.flush(repo, es=FakeES(), write=write)
    assert [sha for sha, _ in sent] == [c.sha] and sent[0][1].tally["room-objects"][0] == 11
    assert not list((repo.path / ".git" / "gitspace" / "spool").glob("*.json"))


def test_a_rejected_doc_is_reported_not_spooled(repo):
    c = repo.commit("afternoon")
    res = publish.publish_commit(repo, c, es=FakeES(), write=FakeWrite(reject={"mug_a1b2"}))
    assert res.rejected == 1 and not res.spooled and "REJECTED" in res.line()


def test_staged_voxels_are_indexed_under_the_new_sha(repo):
    (repo.path / ".git" / "gitspace").mkdir(parents=True, exist_ok=True)
    (repo.path / ".git" / "gitspace" / "voxels.npz").write_bytes(b"")
    c, seen = repo.commit("afternoon"), []

    class R:
        indexed, spooled, reason = 540, 0, None

    def voxels(root, sha, parent, branch, ts, es=None):
        seen.append((sha, parent, branch))
        return R()
    res = publish.publish_commit(repo, c, es=FakeES(), write=FakeWrite(), voxels=voxels)
    assert seen == [(c.sha, c.parent, "main")] and res.voxels == "540 indexed"


@pytest.mark.parametrize("value,ok", [("abc123==", True), ("", False), ("# parked", False),
                                      ("  ", False), ("two words", False), (None, False)])
def test_usable(value, ok):
    assert bool(publish.usable(value)) is ok


def test_room_commit_publishes_and_room_publish_flushes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ROOM_ES", "auto")
    monkeypatch.setenv("ROOM_EVENTS", "off")
    monkeypatch.setenv("ELASTIC_API_KEY", "")  # nothing usable: spool, no network
    repo = tmp_path / "room.git"
    monkeypatch.setenv("ROOM_GIT_PATH", str(repo))
    FakeRoom(repo, quiet=True).commit("clean_bench")
    FakeRoom(repo, quiet=True).scan("messy_bench")
    assert main(["--repo", str(repo), "commit", "-m", "afternoon", "--no-scan"]) == 0
    out = capsys.readouterr().out
    assert "es: 13 docs spooled" in out and "room publish --flush" in out
    assert main(["--repo", str(repo), "publish", "--flush"]) == 1  # still parked: still spooled
    assert json.loads(next((repo / ".git" / "gitspace" / "spool").glob("*.json")).read_text())


def test_commit_docs_join_the_scans_trace_not_their_own(repo, monkeypatch):
    """docs/10 P17: the commit runs outside the capture's transaction; the staged trace wins."""
    import obs
    monkeypatch.setattr(obs, "trace_fields", lambda: {})          # the commit: no live span
    scan_trace = {"sentry_trace_id": "c" * 32, "sentry_span_id": "d" * 16}
    publish.stage_scan(repo, "cap_0100", "2026-09-19T01:00:00Z", {}, trace=scan_trace)
    c = repo.commit("afternoon")
    acts = publish.commit_actions(repo, c)
    assert all(a["_source"]["sentry_trace_id"] == "c" * 32 for a in acts)


def test_a_scratch_repo_never_publishes_with_the_real_keys(repo, monkeypatch):
    """A self-test's commit landed in the real indices once (docs/10 D33): it became the answer
    to ES|QL's time -> sha. Only $ROOM_GIT_PATH publishes; nothing is built, nothing spooled."""
    import elasticsearch
    monkeypatch.setattr(elasticsearch, "Elasticsearch", lambda *a, **k: pytest.fail("built a client"))
    monkeypatch.setenv("ELASTIC_URL", "https://example.es.us-east-1.aws.elastic.cloud:443")
    monkeypatch.setenv("ELASTIC_API_KEY", "a-real-looking-key==")
    monkeypatch.setenv("ROOM_GIT_PATH", "/somewhere/else/room.git")
    monkeypatch.delenv("ROOM_PUBLISH_ANY", raising=False)
    c = repo.commit("self-test")
    res = publish.publish_commit(repo, c)
    assert not res.spooled and not res.tally and "isn't the room" in res.line()
    assert not (repo.path / ".git" / "gitspace" / "spool").exists()
    assert "isn't the room" in publish.flush(repo)[0][1].line()


def test_publish_any_is_an_explicit_opt_in(repo, monkeypatch):
    monkeypatch.setenv("ROOM_GIT_PATH", "/somewhere/else/room.git")
    monkeypatch.setenv("ROOM_PUBLISH_ANY", "1")
    monkeypatch.setenv("ELASTIC_API_KEY", "")                   # no usable key: it spools instead
    assert publish.publish_commit(repo, repo.commit("opted in")).spooled == 13


def test_the_scans_cloud_doc_goes_out_with_the_commit(repo):
    """GAP 1's fourth document: room-clouds, _id = capture_id, now carrying the commit's sha
    (the scan indexed it with none) and the scan's trace."""
    c = repo.commit("afternoon")
    cloud = [a for a in publish.commit_actions(repo, c) if a["_index"] == "room-clouds"]
    assert len(cloud) == 1
    doc = cloud[0]["_source"]
    assert cloud[0]["_id"] == doc["capture_id"] and doc["commit_sha"] == c.sha
    assert {"skew_ms", "tilt_rate_max", "quality_ok", "coverage_pct"} <= set(doc)


def test_fake_descriptions_say_they_are_fake(repo):
    """elastic maps room-objects.vlm_model: a fake commit's words must be labelled as scripted,
    or a re-index overwrites the provenance elastic backfilled (live, 2026-09-19)."""
    c = repo.commit("afternoon")
    objs = [a["_source"] for a in publish.commit_actions(repo, c) if a["_index"] == "room-objects"]
    assert objs and all(o["vlm_model"] == "fake/scene_gen" for o in objs)


class SearchES(FakeES):
    """FakeES that also answers the D44 lookup: the newest described doc per object_id."""

    def __init__(self, described=None, fail=False, up=True):
        super().__init__(up)
        self.described, self.fail, self.searches = described or {}, fail, []

    def search(self, **kw):
        self.searches.append(kw)
        if self.fail:
            raise RuntimeError("search_phase_execution_exception")
        ids = kw["query"]["bool"]["filter"][0]["terms"]["object_id"]
        return {"hits": {"hits": [{"_source": {"object_id": i, **self.described[i]}}
                                  for i in ids if i in self.described]}}


SCISSORS_WORDS = {"raw_description": ["orange-handled scissors, closed", "a pair of scissors lying open"],
                  "vlm_model": "fake/scene_gen"}


def unseen_scissors(repo):
    """live-check a2b2703 in miniature: the scissors are committed, but the scan that goes with
    the commit didn't see them (it staged meta for the mug only)."""
    repo.git("add", "-A")
    c = repo.commit("afternoon")
    publish.stage_scan(repo, "cap_0100", "2026-09-19T06:30:00Z",
                       {"mug_a1b2": {"confidence": 0.9, "raw_description": ["a mug"], "vlm_model": "fake/scene_gen"}})
    return c


def test_an_object_the_scan_never_saw_keeps_its_words(repo):
    """D44: descriptions are identity, carried forward from the last doc that had them, with
    vlm_model unchanged (web's SYNTHETIC badge keys on it). Observation fields stay empty."""
    c = unseen_scissors(repo)
    es, write = SearchES({"scissors_9f3a": SCISSORS_WORDS}), FakeWrite()
    out = publish.publish_commit(repo, c, es=es, write=write)
    docs = {a["_source"]["object_id"]: a["_source"] for a in objects(write.calls[0])}
    sc = docs["scissors_9f3a"]
    assert sc["raw_description"] == SCISSORS_WORDS["raw_description"] and sc["vlm_model"] == "fake/scene_gen"
    assert sc["confidence"] is None and sc["observed_by"] is None and sc["point_count"] is None
    assert docs["mug_a1b2"]["raw_description"] == ["a mug"], "what the scan saw is never overwritten"
    assert "mug_a1b2" not in es.searches[0]["query"]["bool"]["filter"][0]["terms"]["object_id"]
    assert len(es.searches) == 1, "one search for every unseen object, not one each"
    assert "descriptions carried forward" in out.line()


def test_a_spooled_commit_gets_its_words_when_flushed(repo):
    c = unseen_scissors(repo)
    publish.publish_commit(repo, c, es=FakeES(up=False), write=FakeWrite())      # cluster away: spooled
    write = FakeWrite()
    publish.flush(repo, es=SearchES({"scissors_9f3a": SCISSORS_WORDS}), write=write)
    sc = next(a["_source"] for a in objects(write.calls[0]) if a["_source"]["object_id"] == "scissors_9f3a")
    assert sc["raw_description"] == SCISSORS_WORDS["raw_description"]


def test_a_failed_lookup_still_sends_the_snapshot_and_says_so(repo):
    c = unseen_scissors(repo)
    write = FakeWrite()
    out = publish.publish_commit(repo, c, es=SearchES(fail=True), write=write)
    assert write.calls and "sent without descriptions (RuntimeError)" in out.line()
    sc = next(a["_source"] for a in objects(write.calls[0]) if a["_source"]["object_id"] == "scissors_9f3a")
    assert sc["raw_description"] is None


def test_the_fake_scanner_never_writes_live_from_a_scratch_repo(tmp_path, monkeypatch, capsys):
    """D45: FakeRoom.flush wrote captures to the live cluster from any repo; a scratch copy of
    room.git reuses its capture ids (cap_0015 …) and overwrote the real ones. Every fake write
    goes through flush, so the gate is there: all three `room` verbs that scan, and scene_gen."""
    import fake.scene_gen as sg
    sent = []
    monkeypatch.setattr(sg, "index_actions", lambda acts, mode, log=print: sent.append(len(acts)) or 0)
    monkeypatch.setenv("ROOM_ES", "auto")
    monkeypatch.setenv("ROOM_EVENTS", "off")
    monkeypatch.setenv("ROOM_SENTRY", "off")
    monkeypatch.setenv("ROOM_ROBOT", "mock")
    monkeypatch.setenv("ROOM_GIT_PATH", str(tmp_path / "the-room.git"))    # the room is elsewhere
    monkeypatch.delenv("ROOM_PUBLISH_ANY", raising=False)
    scratch = str(tmp_path / "scratch.git")
    for argv in (["init", "--scene", "clean_bench"], ["status", "--scene", "messy_bench"],
                 ["reset", "--hard", "--scene", "messy_bench", "--no-route"]):
        main(["--repo", scratch, *argv])
    assert sg.main(["--repo", scratch, "--scan", "messy_bench", "--es", "auto"]) == 0
    assert sg.main(["--repo", scratch, "--scan", "messy_bench", "--es", "on"]) == 1   # asked for, refused
    assert sent == [], "a scratch repo sent captures to the live cluster"
    assert "isn't the room" in capsys.readouterr().out

    monkeypatch.setenv("ROOM_GIT_PATH", scratch)                              # now it IS the room
    main(["--repo", scratch, "status", "--scene", "messy_bench"])
    assert sent, "the room itself still indexes its captures"
