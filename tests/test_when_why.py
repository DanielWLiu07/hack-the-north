"""roomctl/when.py (a phrase -> a commit) and roomctl/why.py (a commit -> its capture's evidence).
Elasticsearch is mocked at the boundary; git is real."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fake.scene_gen import FakeRoom  # noqa: E402
from roomctl import when, why  # noqa: E402
from roomctl.repo import GitError, Repo  # noqa: E402

TZ = timezone(timedelta(hours=-4))                     # EDT
NOW = datetime(2026, 9, 19, 14, 30, tzinfo=TZ)


@pytest.mark.parametrize("text,want", [
    ("before dinner", datetime(2026, 9, 18, 18, 0, tzinfo=TZ)),        # dinner hasn't happened yet today
    ("lunch", datetime(2026, 9, 19, 12, 0, tzinfo=TZ)),
    ("the way it was before lunch", datetime(2026, 9, 19, 12, 0, tzinfo=TZ)),
    ("13:05", datetime(2026, 9, 19, 13, 5, tzinfo=TZ)),
    ("9pm", datetime(2026, 9, 18, 21, 0, tzinfo=TZ)),
    ("2 hours ago", NOW - timedelta(hours=2)),
    ("yesterday lunch", datetime(2026, 9, 18, 12, 0, tzinfo=TZ)),
    ("last night", datetime(2026, 9, 18, 21, 0, tzinfo=TZ)),
])
def test_phrases_become_times(text, want):
    got = when.parse_when(text, now=NOW)
    assert got == want and got.tzinfo is not None


def test_an_unplaceable_phrase_is_refused_not_guessed():
    with pytest.raises(ValueError):
        when.parse_when("when the vibes were right", now=NOW)


@pytest.fixture
def repo(tmp_path):
    room = FakeRoom(tmp_path / "room.git", quiet=True)
    t0 = datetime(2026, 9, 19, 16, 0, tzinfo=timezone.utc)
    room.commit("clean_bench", at=t0)
    room.commit("messy_bench", at=t0 + timedelta(hours=2))
    return Repo(tmp_path / "room.git")


def test_commit_before_falls_back_to_git_and_says_so(repo):
    shas = repo.git("rev-list", "--reverse", "HEAD").stdout.split()
    between = datetime(2026, 9, 19, 17, 0, tzinfo=timezone.utc)
    got = when.commit_before(repo, between, "main", es=None)
    assert got["sha"] == shas[0] and got["source"].startswith("git")
    with pytest.raises(GitError):
        when.commit_before(repo, datetime(2026, 9, 19, 15, 0, tzinfo=timezone.utc), "main")


def test_commit_before_prefers_elasticsearch_strictly_before(repo, monkeypatch):
    seen = {}

    class Q:
        def __init__(self, es): pass

        def commit_at(self, ts, branch=None, strictly_before=False):
            seen.update(ts=ts, branch=branch, strictly=strictly_before)
            return {"commit_sha": "abc1234", "message": "the bench, tidied", "@timestamp": "2026-09-19T16:00:00Z"}
    import roomctl.publish as pub
    monkeypatch.setattr(pub, "_import", lambda folder, mod: type("M", (), {"Queries": Q}))
    got = when.commit_before(repo, datetime(2026, 9, 19, 17, 0, tzinfo=timezone.utc), "main", es=object())
    assert got == {"sha": "abc1234", "message": "the bench, tidied", "at": "2026-09-19T16:00:00Z", "branch": "main",
                   "source": "elasticsearch"}
    assert seen["strictly"] is True and seen["branch"] == "main"


class FakeES:
    def __init__(self, event, cloud):
        self.docs = {"room-events": event, "room-clouds": cloud}

    def search(self, index, size, query, sort):
        d = self.docs.get(index)
        return {"hits": {"hits": [{"_source": d}] if d else []}}


def fake_queries(window):
    class Q:
        events, clouds = "room-events", "room-clouds"

        def __init__(self, es): pass

        def telemetry_window(self, ts, seconds=2.0):
            return window
    return type("M", (), {"Queries": Q})


def test_why_a_clean_capture_is_trustworthy_and_carries_its_trace(repo, monkeypatch):
    import roomctl.publish as pub
    monkeypatch.setattr(pub, "_import", lambda f, m: fake_queries({"tilt_rate": {"low": -0.004, "high": 0.009, "peak": 0.009, "rows": 100}}))
    es = FakeES({"capture_id": "cap_0012", "@timestamp": "2026-09-19T18:00:00Z", "message": "afternoon", "sentry_trace_id": "234f3164"},
                {"capture_id": "cap_0012", "quality_ok": True, "skew_ms": 3.0, "tilt_rate_max": 0.007, "@timestamp": "2026-09-19T18:00:00Z"})
    d = why.explain(repo, "HEAD", es)
    assert d["trustworthy"] and d["capture_id"] == "cap_0012" and d["trace"]["id"] == "234f3164" and d["gate"]["passed"]
    assert "trustworthy" in why.render(d) and "cap_0012" in why.render(d)


def test_why_names_a_leaning_robot_and_a_missing_capture(repo, monkeypatch):
    import roomctl.publish as pub
    monkeypatch.setattr(pub, "_import", lambda f, m: fake_queries({"tilt_rate": {"low": -0.2, "high": 0.13, "peak": 0.2, "rows": 100}}))
    lean = FakeES({"capture_id": "cap_9", "@timestamp": "2026-09-19T18:00:00Z"},
                  {"capture_id": "cap_9", "quality_ok": False, "skew_ms": 2.0, "tilt_rate_max": 0.13, "@timestamp": "2026-09-19T18:00:00Z"})
    d = why.explain(repo, "HEAD", lean)
    assert not d["trustworthy"] and any("leaning" in f for f in d["findings"]) and any(f.startswith("telemetry") for f in d["findings"])
    none = why.explain(repo, "HEAD", FakeES(None, None))
    assert not none["trustworthy"] and "no capture is recorded" in none["findings"][0]
