"""Pull requests for the room (roomctl/pr.py): a decision becomes main, and the room then reads as
drifted from it until the caretaker moves the object. Real git, a temporary room."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fake.scene_gen import FakeRoom  # noqa: E402
from roomctl import pr  # noqa: E402
from roomctl.executor import Box  # noqa: E402
from roomctl.repo import GitError, Repo, load_room  # noqa: E402

LAMP = "lamp_2d9b"     # 0.18 m square: no gap on the fake shelf is wide enough
MUG = "mug_a1b2"       # fits the shelf's free front band


@pytest.fixture
def repo(tmp_path):
    FakeRoom(tmp_path / "room.git", quiet=True).commit("clean_bench")
    return Repo(tmp_path / "room.git")


def test_propose_builds_a_branch_and_leaves_main_and_the_room_alone(repo):
    head, status_before = repo.head(), repo.git("status", "--porcelain", "-uall").stdout
    p = pr.propose(repo, MUG, "shelf", author="daniel", title="Move the mug to the shelf")
    assert p.id == 1 and p.branch == "pr/1-move-the-mug-to-the-shelf" and p.status == "open"
    assert repo.head() == head and repo.git("status", "--porcelain", "-uall").stdout == status_before
    tip = repo.records(p.branch)
    assert tip[MUG].zone == "shelf" and repo.records(head)[MUG].zone == "desk"
    shelf = load_room(repo.path)["zones"]["shelf"]
    mug = tip[MUG]
    assert shelf["min"][0] <= mug.pose.x <= shelf["max"][0] and shelf["min"][1] <= mug.pose.y <= shelf["max"][1]
    box = Box.of(mug.pose, mug.extents)
    assert not any(box.overlaps(Box.of(r.pose, r.extents)) for oid, r in tip.items() if oid != MUG and r.zone == "shelf")
    assert "Proposed-by: daniel" in repo.git("log", "-1", "--format=%B", p.branch).stdout
    assert [o["op"] for o in p.ops] == ["moved"] and p.ops[0]["to"]["zone"] == "shelf"


def test_approve_merges_into_main_and_the_room_now_reads_as_drift(repo):
    p = pr.propose(repo, MUG, "shelf", author="daniel")
    merge = pr.approve(repo, p.id, approver="sam")
    assert repo.head() == merge and repo.branch() == "main"
    parents = repo.git("log", "-1", "--format=%P", merge).stdout.split()
    assert len(parents) == 2 and parents[1] == p.head_sha                       # a real --no-ff merge
    msg = repo.git("log", "-1", "--format=%B", merge).stdout
    assert msg.startswith(f"Merge pull request #{p.id}: ") and "Approved-by: sam" in msg
    assert repo.records()[MUG].zone == "shelf"                                   # main now SAYS shelf
    st = repo.status()
    assert not st.clean and MUG in {e.object_id for e in st.entries}             # the room still has it on the desk
    (merged,) = pr.list_prs(repo)
    assert merged.status == "merged" and merged.merged_in == merge
    with pytest.raises(GitError, match="already merged"):
        pr.approve(repo, p.id, approver="sam")


def test_close_keeps_the_branch_in_history_and_numbers_keep_counting(repo):
    a = pr.propose(repo, MUG, "shelf", author="daniel")
    pr.close(repo, a.id)
    b = pr.propose(repo, "keys_7c2e", "desk", author="sam")
    assert b.id == 2
    by_id = {x.id: x for x in pr.list_prs(repo)}
    assert by_id[1].status == "closed" and by_id[2].status == "open"
    with pytest.raises(GitError, match="closed"):
        pr.approve(repo, 1, approver="sam")


@pytest.mark.parametrize("oid,zone,err", [("nope_0000", "shelf", "no object"), (LAMP, "attic", "no zone"),
                                          (LAMP, "desk", "already in"),
                                          (LAMP, "shelf", "no free spot")])   # never stacked onto something
def test_propose_refuses_what_it_cant_do(repo, oid, zone, err):
    with pytest.raises(GitError, match=err):
        pr.propose(repo, oid, zone, author="daniel")


def _drift(repo, oid, zone, dx=0.0, dy=0.0):
    """Move `oid` in the working tree only, the way a scan that saw it somewhere else would."""
    from dataclasses import replace
    from roomctl.state import to_yaml
    rec = repo.records()[oid]
    moved = replace(rec, zone=zone, pose=replace(rec.pose, x=rec.pose.x + dx, y=rec.pose.y + dy))
    (repo.path / rec.path).unlink()
    (repo.path / moved.path).parent.mkdir(parents=True, exist_ok=True)
    (repo.path / moved.path).write_text(to_yaml(moved))
    return moved


def test_i_meant_that_keeps_the_object_exactly_where_it_was_seen(repo):
    seen = _drift(repo, MUG, "shelf", dx=0.02)
    p = pr.propose(repo, MUG, None, author="daniel", as_seen=True)
    assert p.title == f"keep {MUG} in shelf" and repo.records(p.branch)[MUG] == seen
    pr.approve(repo, p.id, approver="sam")
    assert MUG not in {e.object_id for e in repo.status().entries}               # decided, so not drift


def test_i_meant_that_refuses_what_it_cant_do(repo):
    with pytest.raises(GitError, match="already in main exactly"):
        pr.propose(repo, MUG, None, author="daniel", as_seen=True)
    _drift(repo, MUG, "shelf")
    with pytest.raises(GitError, match="was seen in shelf"):
        pr.propose(repo, MUG, "desk", author="daniel", as_seen=True)
    with pytest.raises(GitError, match="not in the room"):
        pr.propose(repo, "nope_0000", None, author="daniel", as_seen=True)
