"""roomctl/policy.py: mess or decision, and whose business it is (plan/roommate/03 §5)."""
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fake.scene_gen import FakeRoom, load_scene  # noqa: E402
from roomctl import policy, pr  # noqa: E402
from roomctl.repo import Entry, Repo, load_room, room_yaml  # noqa: E402

ROOM = {"zones": {"desk": {"surface": 0.70},                                   # today's room: no policy at all
                  "shelf": {"surface": 0.90, "policy": "shared"},
                  "my_side": {"surface": 0.70, "policy": "personal", "owner": "daniel"}}}
TRACKED = Entry("zones/desk/mug_a1b2.yaml", unstaged="M")
DELETED = Entry("zones/shelf/lamp_1.yaml", unstaged="D")
NEW = Entry("zones/desk/thing_9.yaml", untracked=True)
MINE = Entry("zones/my_side/book_2.yaml", unstaged="M")
MINE_NEW = Entry("zones/my_side/sock_3.yaml", untracked=True)


@pytest.mark.parametrize("entry, tier, want", [
    (TRACKED, "A", ("mess", "tidy")), (TRACKED, "B", ("mess", "chore")), (TRACKED, "C", ("mess", "chore")),
    (DELETED, "A", ("mess", "tidy")), (DELETED, "B", ("mess", "chore")),
    (NEW, "A", ("untracked_shared", "lost_and_found")), (NEW, "B", ("untracked_shared", "chore")),
    (MINE, "A", ("personal", "ignore")), (MINE, "B", ("personal", "ignore")),
    (MINE_NEW, "A", ("untracked_personal", "ignore")), (MINE_NEW, "C", ("untracked_personal", "ignore")),
])
def test_every_verdict_and_action(entry, tier, want):
    assert policy.classify(entry, ROOM, "abc1234", tier) == want


def test_an_approved_but_unexecuted_change_is_a_decision_not_a_mess():
    assert policy.classify(TRACKED, ROOM, "abc", "A", decided={"mug_a1b2"}) == ("decision", "tidy")
    assert policy.classify(TRACKED, ROOM, "abc", "B", decided={"mug_a1b2"}) == ("decision", "chore")
    assert policy.classify(MINE, ROOM, "abc", "A", decided={"book_2"}) == ("personal", "ignore")   # still not ours
    # approval moves `main` first: the object, still in its OLD zone, now reads as untracked there. Not lost property.
    assert policy.classify(NEW, ROOM, "abc", "A", decided={"thing_9"}) == ("decision", "tidy")
    assert policy.classify(NEW, ROOM, "abc", "A", decided={"other"}) == ("untracked_shared", "lost_and_found")


def test_a_zone_with_no_policy_is_shared_and_tier_is_case_insensitive():
    assert policy.zone_policy(ROOM, "desk") == "shared" and policy.zone_policy(ROOM, "nowhere") == "shared"
    assert policy.classify(TRACKED, ROOM, None, "b") == ("mess", "chore")
    assert policy.zone_owner(ROOM, "my_side") == "daniel" and policy.zone_owner(ROOM, "desk") is None


def test_files_that_are_not_objects_are_nobodys_mess():
    for path in ("room.yaml", "anchors/tag_0.yaml", ".roomignore", "zones/desk/nested/x.yaml"):
        assert policy.classify(Entry(path, unstaged="M"), ROOM, "abc", "A") == ("personal", "ignore")


def test_bad_input_is_refused_not_guessed():
    with pytest.raises(ValueError):
        policy.classify(TRACKED, ROOM, "abc", "D")
    with pytest.raises(ValueError):
        policy.classify(TRACKED, {"zones": {"desk": {"policy": "communal"}}}, "abc", "A")


# ── room.yaml v2: optional, and yesterday's rooms still load ────────────────────────────

def test_todays_room_yaml_loads_unchanged_and_v2_keys_round_trip(tmp_path):
    scene = load_scene("clean_bench")
    old = room_yaml(scene.room)
    assert "policy" not in old and "owner" not in old                              # nothing new is written unasked
    (tmp_path / "room.yaml").write_text(old)
    r = load_room(tmp_path)
    assert set(r["zones"]) == {"desk", "shelf"} and r["bin"] == r["lost_and_found"] == {"pose": [0.30, -0.75, 0.45]}
    assert r["nav"] is None and policy.zone_policy(r, "desk") == "shared"

    v2 = {**scene.room, "zones": {**scene.room["zones"],
                                  "my_side": {"min": [0.1, -1.2, 0.68], "max": [0.9, -0.7, 1.3], "surface": 0.70,
                                              "policy": "personal", "owner": "daniel"}}}
    (tmp_path / "room.yaml").write_text(room_yaml(v2) + "nav:\n  area: {xmin: -1.0, xmax: 1.0, ymin: -0.5, ymax: 1.5}\n")
    r = load_room(tmp_path)
    assert r["zones"]["my_side"]["policy"] == "personal" and r["zones"]["my_side"]["owner"] == "daniel"
    assert r["nav"]["area"]["ymax"] == 1.5
    assert policy.classify(MINE, r, "abc", "A") == ("personal", "ignore")


def test_lost_and_found_is_an_alias_of_bin(tmp_path):
    (tmp_path / "room.yaml").write_text("zones: {}\nlost_and_found: {pose: [1.0, 2.0, 0.4]}\n")
    r = load_room(tmp_path)
    assert r["bin"] == r["lost_and_found"] == {"pose": [1.0, 2.0, 0.4]}


# ── decisions, from real git history ────────────────────────────────────────────────────

@pytest.fixture
def repo(tmp_path, monkeypatch):
    for k, v in (("ROOM_ES", "off"), ("ROOM_EVENTS", "off"), ("ROOM_SENTRY", "off")):
        monkeypatch.setenv(k, v)
    path = tmp_path / "room.git"
    FakeRoom(path, quiet=True).commit("clean_bench")
    return Repo(path)


def test_after_an_approval_the_drift_is_a_decision_until_the_room_catches_up(repo):
    assert policy.decided_objects(repo) == set()
    p = pr.propose(repo, "mug_a1b2", "shelf", "daniel", "mug lives on the shelf")
    assert policy.decided_objects(repo) == set()                                  # proposing decides nothing
    pr.approve(repo, p.id, "andrew")
    st = repo.status()
    drifted = {e.object_id for e in st.entries if e.object_id}
    assert "mug_a1b2" in drifted                                                   # main moved; the mug has not
    decided = policy.decided_objects(repo)
    assert decided == {"mug_a1b2"}
    for e in st.entries:
        if e.object_id == "mug_a1b2" and not e.untracked:
            assert policy.classify(e, load_room(repo.path), st.head, "A", decided) == ("decision", "tidy")
    subprocess.run(["git", "-C", str(repo.path), "checkout", "-q", "HEAD", "--", "."], check=True)   # the robot moved it
    subprocess.run(["git", "-C", str(repo.path), "clean", "-fdq", "zones"], check=True)
    assert repo.status().clean and policy.decided_objects(repo) == set()


def test_a_roommate_moving_the_decided_object_again_is_a_mess(repo):
    p = pr.propose(repo, "mug_a1b2", "shelf", "daniel")
    pr.approve(repo, p.id, "andrew")
    scene = load_scene("clean_bench")
    mug = scene.objects["mug_a1b2"]
    FakeRoom(repo.path, quiet=True).scan(replace(scene, objects={**scene.objects, "mug_a1b2": replace(mug, x=0.80, y=-0.30)}))
    assert "mug_a1b2" not in policy.decided_objects(repo)       # not where it was before the merge either: drift
