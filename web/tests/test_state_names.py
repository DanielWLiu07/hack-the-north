"""A state has one name however it is said. Andrew's parser turns "set my room back to study mode" into
target_state `study` and leaves "restore study-mode" as `study-mode`; whichever way the ref was named, one
phrasing used to miss it. Own throwaway repo: the shared snapshot's refs are fingerprinted by other tests."""
from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import graph_api  # noqa: E402
import server  # noqa: E402


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def room_with_states(tmp_path, monkeypatch):
    repo = tmp_path / "room.git"
    (repo / "zones" / "desk").mkdir(parents=True)
    git(tmp_path, "init", "-q", "-b", "main", str(repo))
    shas = {}
    for i, (x, label) in enumerate([(0.30, "study"), (0.45, "movie"), (0.60, "focus-a"), (0.75, "focus-b")]):
        (repo / "zones" / "desk" / "mug_a1b2.yaml").write_text(f"object_id: mug_a1b2\nclass: mug\nzone: desk\npose: {{x: {x}, y: 0.1, z: 0.75, yaw: 0}}\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", label)
        shas[label] = git(repo, "rev-parse", "HEAD")
    git(repo, "tag", "study-mode", shas["study"])                      # named the LONG way
    git(repo, "tag", "-a", "Movie_Night", "-m", "annotated", shas["movie"])   # annotated, odd case, underscore
    git(repo, "tag", "focus", shas["focus-a"])                         # two spellings of one name ...
    git(repo, "tag", "focus-mode", shas["focus-b"])                    # ... on two DIFFERENT commits
    monkeypatch.setenv("ROOM_GIT_PATH", str(repo))
    return repo, shas


def test_both_phrasings_land_on_the_same_commit_whichever_way_the_ref_was_named(room_with_states):
    _, shas = room_with_states
    for said in ("study", "study-mode", "Study Mode", "study_mode", "STUDY"):          # `study` is what Andrew's parser emits
        got = graph_api.resolve_state(said)
        assert got["sha"] == shas["study"] and got["ref"] == "study-mode", (said, got)
    assert graph_api.resolve_state("study-mode")["how"] == "exact" and graph_api.resolve_state("study")["how"] == "alias"
    for said in ("movie night", "movie-night", "Movie_Night", "movie night mode"):     # an annotated tag peels to its commit
        assert graph_api.resolve_state(said)["sha"] == shas["movie"], said
    assert graph_api.resolve_state("movie night")["ref"] == "Movie_Night", "and it says which REAL ref that was"
    assert graph_api._resolve("main") == shas["focus-b"] and graph_api._resolve(shas["movie"][:9]) == shas["movie"]
    assert graph_api._resolve("HEAD^") == shas["focus-a"], "git's own expressions still resolve exactly as git would"
    assert graph_api._resolve("study") is None, "_resolve stays strict: a name it resolves is a ref that exists"


def test_nothing_is_guessed(room_with_states):
    _, shas = room_with_states
    assert graph_api.resolve_state("focus")["sha"] == shas["focus-a"], "an exact name always wins"
    vague = graph_api.resolve_state("Focus Mode")                                      # not exact; two spellings, two commits
    assert vague["sha"] is None and vague["how"] == "ambiguous" and vague["candidates"] == ["focus", "focus-mode"]
    for said in ("nonsense", "nonsense mode", "", "   ", "--upload-pack=x", "deadbeefcafe", "stud"):
        assert graph_api.resolve_state(said)["sha"] is None, said


def test_the_command_endpoint_says_how_the_words_became_a_commit(room_with_states, monkeypatch):
    _, shas = room_with_states
    monkeypatch.setenv("WEB_ALLOWED_COMMANDS", "restore,checkout")
    api = TestClient(server.app)
    ids = set()
    for said in ("study", "study mode", "study-mode"):
        j = api.post("/api/command", json={"command": "restore", "args": {"ref": said}})
        # three phrasings, ONE commit, one room: one job. The first asker makes it (202); the rest replay it (200)
        assert j.status_code == (202 if said == "study" else 200) and j.json()["target"] == shas["study"], (said, j.text)
        assert j.json()["replayed"] is (said != "study")
        assert j.json()["asked"]["resolved"] == {"ref": "study-mode", "how": "exact" if said == "study-mode" else "alias"}
        ids.add(j.json()["job_id"])
    assert len(ids) == 1
    vague = api.post("/api/command", json={"command": "restore", "args": {"ref": "Focus Mode"}})
    assert vague.status_code == 409 and vague.json()["error"] == "ambiguous_state" and "focus or focus-mode" in vague.json()["detail"]
    assert api.post("/api/command", json={"command": "restore", "args": {"ref": "nowhere mode"}}).json()["error"] == "not_found"


def test_the_natural_phrase_and_the_explicit_one_both_plan_through_the_middleware(room_with_states, monkeypatch):
    """The whole path, with the bridge's labelled STUB of Andrew's grammar (hermetic: no repo of his needed)."""
    _, shas = room_with_states
    monkeypatch.setenv("ANDREW_BRIDGE", "stub")
    api = TestClient(server.app)
    if api.get("/api/agent/bridge").status_code == 404:
        pytest.skip("bridge/ is not mounted in this checkout")
    seen = {}
    for text in ("set my room back to study mode", "restore study-mode"):
        r = api.post("/api/agent/command", json={"type": "user_command", "request_id": str(uuid.uuid4()), "payload": {"text": text}}).json()
        assert r.get("error") is None and r["action"]["kind"] == "plan", (text, r.get("error"))
        seen[text] = (r["intent"]["target_state"], r["action"]["result"]["target_sha"])
    assert seen["set my room back to study mode"] == ("study", shas["study"]), "his parser drops the word `mode`…"
    assert seen["restore study-mode"] == ("study-mode", shas["study"]), "…and keeps it here: both must land on one commit"
    r = api.post("/api/agent/command", json={"type": "user_command", "request_id": str(uuid.uuid4()), "payload": {"text": "set my room back to study mode"}}).json()
    assert r["action"]["ref"] == "study", "action.ref is what was SAID (docs/31) …"
    assert r["action"]["result"]["ref_resolved"] == "study-mode", "… and ref_resolved is the ref that EXISTS"
