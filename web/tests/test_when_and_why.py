"""A moment instead of a name, and why a commit's picture was trusted.

"put it back the way it was before dinner" and "why was this diff wrong" are the two questions the
room can answer that git alone cannot: one needs wall-clock time turned into a version, the other needs
the capture behind a commit, its quality gate and the telemetry of that second. roomctl owns both
(`roomctl.when`, `roomctl.why`); web only exposes them, so these tests pin the EXPOSURE — that a state
name is never read as a time, that a missing cluster says so instead of guessing, and that the verdict
is passed through rather than re-decided here.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import graph_api  # noqa: E402
import server  # noqa: E402


def git(repo: Path, *args: str, at: str | None = None) -> str:
    env = {"GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at} if at else {}
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          check=True, capture_output=True, text=True,
                          env={**__import__("os").environ, **env}).stdout.strip()


@pytest.fixture()
def dated_room(tmp_path, monkeypatch):
    """Three commits at known times today, and a tag, so "2 hours ago" has a right answer."""
    repo = tmp_path / "room.git"
    (repo / "zones" / "desk").mkdir(parents=True)
    git(tmp_path, "init", "-q", "-b", "main", str(repo))
    now, shas = datetime.now(timezone.utc), {}
    for hours, label in ((6, "morning"), (3, "afternoon"), (0.25, "just now")):
        (repo / "zones" / "desk" / "mug_a1b2.yaml").write_text(
            f"object_id: mug_a1b2\nclass: mug\nzone: desk\npose: {{x: 0.{int(hours * 10):02d}, y: 0.1, z: 0.75, yaw: 0}}\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", label, at=(now - timedelta(hours=hours)).isoformat())
        shas[label] = git(repo, "rev-parse", "HEAD")
    git(repo, "tag", "study", shas["morning"])
    monkeypatch.setenv("ROOM_GIT_PATH", str(repo))
    return repo, shas


@pytest.fixture()
def api():
    return TestClient(server.app)


def test_a_phrase_becomes_the_commit_the_room_was_at_then(api, dated_room):
    _, shas = dated_room
    j = api.get("/api/when", params={"phrase": "2 hours ago"}).json()
    assert j["sha"] == shas["afternoon"], "the last commit strictly before then, not the newest"
    assert j["message"] == "afternoon" and j["when"].startswith("20")
    assert j["restore"] == {"command": "restore", "args": {"ref": shas["afternoon"]}}, "ready to run, by sha"
    assert "git" in j["source"], "no cluster in tests: it fell back to git AND said so"
    assert api.get("/api/when", params={"phrase": "4 hours ago"}).json()["sha"] == shas["morning"]


def test_a_state_name_is_never_read_as_a_time(api, dated_room):
    for name in ("study", "main", "HEAD"):
        r = api.get("/api/when", params={"phrase": name})
        assert r.status_code == 422 and r.json()["error"] == "not_a_time", f"{name} is a name"
        assert "state name" in r.json()["detail"]
    for nonsense in ("wibble", "the fourth of never"):
        assert api.get("/api/when", params={"phrase": nonsense}).status_code == 422
    assert api.get("/api/when", params={"phrase": "x" * 80}).status_code == 422
    assert api.get("/api/when").status_code == 422, "a phrase is required"


def test_a_time_before_the_room_existed_is_a_plain_not_found(api, dated_room):
    r = api.get("/api/when", params={"phrase": "2 days ago"})
    assert r.status_code == 404 and "no commit" in r.json()["detail"], "never the oldest commit as a guess"


def test_resolve_state_marks_a_time_as_a_time(dated_room):
    _, shas = dated_room
    got = graph_api.resolve_state("4 hours ago")       # between the 6 h and 3 h commits, so the clock cannot drift into a tie
    assert got["how"] == "time" and got["sha"] == shas["morning"] and got["when"]
    assert graph_api.resolve_state("study")["how"] == "exact", "a real ref wins over any reading of the words"
    assert graph_api.resolve_state("wibble")["how"] is None


def test_restoring_to_a_moment_plans_from_that_commit(api, dated_room, monkeypatch):
    """The point of the whole thing: the words go straight into the command the graph already runs."""
    _, shas = dated_room
    monkeypatch.setenv("WEB_ALLOWED_COMMANDS", "restore")
    r = api.post("/api/command", json={"command": "restore", "args": {"ref": "2 hours ago"}})
    assert r.status_code in (202, 200), r.text
    body = r.json()
    assert body["resolved"]["how"] == "time" and body["target"].startswith(shas["afternoon"][:7])
    assert body["executor"] == "not_connected", "planned only: nothing moved"


def test_why_says_where_the_answer_lives_when_there_is_no_cluster(api, dated_room):
    r = api.get("/api/why/HEAD")
    assert r.status_code == 503 and r.json()["error"] == "search_unavailable"
    assert "Elasticsearch" in r.json()["detail"], "it names what is missing instead of a verdict"
    assert api.get("/api/why/nope-nope").status_code in (404, 422), "an unknown ref is not a 503"


def test_why_passes_roomctls_verdict_through_untouched(api, dated_room, monkeypatch):
    """The gate's numbers and the verdict belong to roomctl (which reads the robot's own thresholds).
    This endpoint must not re-decide them — it adds links and says how the ref was resolved."""
    seen = {}

    def fake_explain(repo, sha, es, seconds):
        seen.update(sha=sha, seconds=seconds)
        return {"commit": sha, "message": "afternoon", "capture_id": "cap_0005", "at": "2026-09-19T00:37:34Z",
                "gate": {"passed": False, "skew_ms": 31.0, "max_skew_ms": 25.0},
                "telemetry": {"tilt_rate": {"peak": 0.08}}, "findings": ["the cameras were 31.0 ms apart"],
                "trustworthy": False, "trace": {"id": "d548d471", "url": None}}

    monkeypatch.setattr(graph_api, "_es_client", lambda: object())
    # patch the ATTRIBUTE on the package, not sys.modules: `from roomctl import why` reads the attribute first, so
    # once anything else in the run has imported roomctl.why a sys.modules patch is silently ignored and this test
    # would call the real join with a dummy client. raising=False because nothing may have imported it yet.
    import roomctl
    monkeypatch.setattr(roomctl, "why", type("m", (), {"explain": staticmethod(fake_explain)}), raising=False)
    j = api.get("/api/why/2 hours ago", params={"seconds": 3}).json()
    assert seen["seconds"] == 3 and seen["sha"] == dated_room[1]["afternoon"], "the phrase resolved before the join"
    assert j["trustworthy"] is False and j["findings"] == ["the cameras were 31.0 ms apart"]
    assert j["gate"]["skew_ms"] == 31.0, "the gate's own numbers, not a restatement"
    assert j["capture_url"] == "/capture/cap_0005" and j["replay_url"] == "/replay/cap_0005"
    assert j["resolved"]["how"] == "time" and j["resolved"]["when"]
    assert j["frame"] == "world_z_up", "every answer declares its frame"
