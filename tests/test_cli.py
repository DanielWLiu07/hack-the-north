"""`room` against fake scenes: the acceptance criteria in roomctl/README.md."""
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roomctl.cli import main  # noqa: E402


@pytest.fixture
def room(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ROOM_ES", "off")
    monkeypatch.setenv("ROOM_EVENTS", "off")  # never push test jobs into a running dashboard
    monkeypatch.setenv("ROOM_ROBOT", "mock")
    monkeypatch.setenv("ROOM_SENTRY", "off")
    monkeypatch.delenv("ROOM_SCANNER", raising=False)
    repo = tmp_path / "room.git"

    def run(*args: str) -> tuple[int, str]:
        code = main(["--repo", str(repo), *args])
        return code, capsys.readouterr().out
    assert run("init", "--scene", "clean_bench")[0] == 0
    run.repo = repo
    return run


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def test_unchanged_scene_is_clean(room):
    code, out = room("status", "--scene", "clean_bench", "--exit-code")
    assert code == 0 and "working tree clean" in out


def test_status_names_the_physical_change(room):
    code, out = room("status", "--scene", "messy_bench", "--exit-code")
    assert code == 1
    assert "modified:   zones/desk/mug_a1b2.yaml   (moved 0.19 m, turned 15° → 40°)" in out
    assert "deleted:    zones/desk/marker_c3d4.yaml" in out
    assert "Untracked objects:\n\tzones/desk/scissors_9f3a.yaml   (new scissors)" in out


def test_status_json_is_the_api_shape(room):
    _, out = room("status", "--scene", "messy_bench", "--json")
    d = json.loads(out)
    assert d["clean"] is False and d["branch"] == "main" and len(d["head"]) == 7
    by = {c["object_id"]: c for c in d["changes"]}
    assert by["mug_a1b2"]["type"] == "modified" and by["mug_a1b2"]["delta_m"] == 0.19
    assert by["marker_c3d4"]["type"] == "deleted"
    assert by["scissors_9f3a"]["type"] == "untracked"


def test_diff_is_literally_git_diff(room):
    room("status", "--scene", "messy_bench")
    ours = subprocess.run([sys.executable, "-m", "roomctl", "--repo", str(room.repo), "diff"],
                          capture_output=True, text=True, cwd=ROOT).stdout
    assert ours == git(room.repo, "diff")
    assert "-  x: 0.42\n+  x: 0.61\n" in ours


def test_spatial_staging_commits_only_what_was_added(room):
    room("status", "--scene", "messy_bench")
    room("add", "zones/desk/mug_a1b2.yaml")
    code, out = room("commit", "-m", "just the mug", "--no-scan")
    assert code == 0 and "1 moved" in out
    assert git(room.repo, "show", "--name-only", "--format=", "HEAD").split() == ["zones/desk/mug_a1b2.yaml"]
    assert git(room.repo, "status", "--short", "-uall").split() == [
        "D", "zones/desk/marker_c3d4.yaml", "??", "zones/desk/scissors_9f3a.yaml"]


def test_commit_records_the_room_as_it_is(room):
    code, out = room("commit", "-m", "afternoon", "--scene", "messy_bench")
    assert code == 0 and set(out.splitlines()[1].strip().split(", ")) == {"1 moved", "1 removed", "1 added"}
    assert git(room.repo, "log", "-1", "--format=%an %s").strip() == "gitspace-robot afternoon"
    code, out = room("commit", "-m", "again", "--scene", "messy_bench")
    assert code == 1 and "nothing to commit" in out


def test_write_verbs_refuse_to_move_what_they_cant_verify(room):
    before = git(room.repo, "rev-parse", "HEAD")
    assert room("reset", "--hard")[0] == 128          # no scanner: no motion, no ref change
    assert room("revert", "HEAD")[0] == 128
    for verb in ("merge", "stash", "cherry-pick"):    # not wired to the executor yet
        assert room(verb, "HEAD")[0] == 2
    assert git(room.repo, "rev-parse", "HEAD") == before


def test_reset_hard_puts_the_room_back_and_admits_what_it_couldnt(room):
    code, out = room("reset", "--hard", "--scene", "messy_bench", "--no-route")
    assert "1. remove  scissors_9f3a" in out and "2. move    mug_a1b2" in out
    assert "error: cannot apply hunk: object 'marker_c3d4' not present in room" in out
    assert "2 of 3 objects put right" in out and code == 1
    assert git(room.repo, "status", "--porcelain", "-uall").split() == ["D", "zones/desk/marker_c3d4.yaml"]


def test_revert_is_real_git_revert_and_refuses_a_dirty_room(room):
    room("commit", "-m", "afternoon", "--scene", "messy_bench")
    assert room("revert", "HEAD", "--scene", "clean_bench")[0] == 128   # the room differs from HEAD
    code, out = room("revert", "HEAD", "--scene", "messy_bench", "--no-route")
    assert git(room.repo, "log", "-1", "--format=%an|%s").strip() == 'gitspace-robot|Revert "afternoon"'
    assert "2 of 3 objects put right" in out  # the marker left the room: it can't be conjured


def scene(tmp_path: Path, name: str, extends: str, move: dict) -> str:
    """A fake scene on disk that extends another: what the room physically looks like."""
    import yaml
    p = tmp_path / f"{name}.yaml"
    p.write_text(yaml.safe_dump({"extends": extends, "message": name, "move": move}))
    return str(p)


@pytest.fixture
def two_changes(room, tmp_path):
    """c1 clean -> c2 the mug moved -> c3 the cup moved too; the room stands at c3."""
    mug = scene(tmp_path, "mug", "clean_bench", {"mug_a1b2": {"x": 0.61, "yaw": 40}})
    both = scene(tmp_path, "both", mug, {"cup_7e21": {"x": 0.18, "y": -0.12}})
    c1 = git(room.repo, "rev-parse", "HEAD").strip()
    room("commit", "-m", "mug moved", "--scene", mug)
    c2 = git(room.repo, "rev-parse", "HEAD").strip()
    room("commit", "-m", "cup moved", "--scene", both)
    return c1, c2, git(room.repo, "rev-parse", "HEAD").strip(), both


def blob(repo: Path, ref: str, path: str) -> str:
    return git(repo, "show", f"{ref}:{path}")


MUG, CUP = "zones/desk/mug_a1b2.yaml", "zones/desk/cup_7e21.yaml"


def test_restore_puts_the_whole_room_at_a_state_as_a_new_commit(room, two_changes):
    """Andrew's `restore <state>` (docs/31 §7.4): `git restore --source=<state> --staged
    --worktree -- zones` + a commit on top of HEAD, then the robot makes the room match it.
    Both objects go back; history keeps c2 and c3; HEAD stays on the branch."""
    c1, _, c3, both = two_changes
    code, out = room("restore", c1[:7], "--scene", both, "--no-route")
    assert code == 0 and f"the room matches {c1[:7]}" in out and f"Restore {c1[:7]}" in out
    assert git(room.repo, "rev-parse", "HEAD^").strip() == c3                          # on top, not instead
    assert git(room.repo, "branch", "--show-current").strip() == "main"                # not a checkout
    assert git(room.repo, "log", "-1", "--format=%an|%s|%b").strip() == (
        f"gitspace-robot|Restore {c1[:7]}|git restore --source={c1} --staged --worktree -- zones")
    assert git(room.repo, "diff", c1, "HEAD", "--", "zones") == ""                     # the tree is c1's
    assert git(room.repo, "status", "--porcelain", "-uall") == ""                      # and so is the room
    for f in (MUG, CUP):
        assert (room.repo / f).read_text() == blob(room.repo, c1, f)


def test_restore_with_no_ref_is_the_robot_alone(room):
    """`room restore` = restore HEAD: no commit, the robot puts back what moved since the last
    one — the step after the panel's Commit (docs/31 §6: the robot moves from committed state)."""
    before = git(room.repo, "rev-parse", "HEAD")
    code, out = room("restore", "--scene", "messy_bench", "--no-route")
    assert "nothing to commit, the robot puts it back" in out and "2 of 3 objects put right" in out and code == 1
    assert git(room.repo, "rev-parse", "HEAD") == before
    assert git(room.repo, "status", "--porcelain", "-uall").split() == ["D", "zones/desk/marker_c3d4.yaml"]


def test_restore_never_sweeps_untracked_objects_into_its_commit(room, two_changes):
    c1, *_ = two_changes
    code, out = room("restore", c1[:7], "--scene", "messy_bench", "--no-route")        # scissors are out
    assert "scissors_9f3a" not in git(room.repo, "show", "--name-only", "--format=", "HEAD")
    assert "1. remove  scissors_9f3a" in out                                           # the robot bins them


def test_revert_is_not_restore(room, two_changes):
    """Same history, same room, different verb: revert undoes ONE commit (the mug) as a new
    commit; the cup, moved by a later commit, stays where it is."""
    c1, c2, c3, both = two_changes
    code, out = room("revert", c2[:7], "--scene", both, "--no-route")
    assert code == 0 and "the room matches" in out
    assert git(room.repo, "rev-parse", "HEAD^").strip() == c3
    assert git(room.repo, "log", "-1", "--format=%s").strip() == 'Revert "mug moved"'
    assert (room.repo / MUG).read_text() == blob(room.repo, c1, MUG)
    assert (room.repo / CUP).read_text() == blob(room.repo, c3, CUP)


def test_restore_plan_is_the_handoff_contract(room, two_changes):
    c1, _, c3, both = two_changes
    room("status", "--scene", both)
    code, out = room("restore", "--source", c1[:7], "--plan-only", "--json", "--no-route")
    d = json.loads(out)
    assert code == 0 and d["contract"] == "gitspace.plan/1" and d["target_sha"] == c1
    assert sorted(op["object_id"] for op in d["ops"]) == ["cup_7e21", "mug_a1b2"]
    assert git(room.repo, "rev-parse", "HEAD").strip() == c3                         # plan-only writes nothing


def test_restore_of_a_ref_that_isnt_there_moves_nothing(room):
    before = git(room.repo, "rev-parse", "HEAD")
    assert room("restore", "no-such-state", "--scene", "messy_bench", "--no-route")[0] == 128
    assert git(room.repo, "rev-parse", "HEAD") == before


def test_checkout_of_a_swap_stages_and_verifies_clean(room):
    from dataclasses import replace
    from fake.scene_gen import FakeRoom, load_scene
    git(room.repo, "checkout", "-q", "-b", "swap")
    s = load_scene("clean_bench")
    mug, cup = s.objects["mug_a1b2"], s.objects["cup_7e21"]
    FakeRoom(room.repo, quiet=True).commit(replace(s, objects={
        **s.objects, "mug_a1b2": replace(mug, x=cup.x, y=cup.y), "cup_7e21": replace(cup, x=mug.x, y=mug.y)}),
        "swap mug and cup")
    git(room.repo, "checkout", "-q", "main")
    code, out = room("checkout", "swap", "--scene", "clean_bench", "--no-route")
    kinds = [line.split()[1] for line in out.splitlines() if line.strip()[:2] in ("1.", "2.", "3.")]
    assert kinds == ["stage", "move", "unstage"] and "the room matches swap" in out and code == 0
    assert git(room.repo, "status", "--porcelain", "-uall") == ""


def test_conflict_shows_in_status(room):
    git(room.repo, "checkout", "-q", "-b", "movie-night")
    room("commit", "-m", "movie night", "--scene", "movie_night")
    git(room.repo, "checkout", "-q", "main")
    room("commit", "-m", "afternoon", "--scene", "messy_bench")
    subprocess.run(["git", "-C", str(room.repo), "merge", "-q", "movie-night"], capture_output=True)
    _, out = room("status", "--no-scan")
    assert "both modified: zones/desk/mug_a1b2.yaml" in out
    assert "renamed:    zones/shelf/speaker_6b12.yaml -> zones/desk/speaker_6b12.yaml" in out
    d = json.loads(room("status", "--no-scan", "--json")[1])
    assert d["merging"] and [c["object_id"] for c in d["changes"] if c["type"] == "conflict"] == ["mug_a1b2"]


def test_reset_refuses_what_the_robot_cant_stand_close_enough_for(room):
    """With placeholder arm numbers the middle of the fake desk has nowhere to stand (docs/24 A2)
    — an unapplied hunk, reported, not retried, and the robot says how much it did rather than
    claiming success.

    The invariant is REACH, not "attempt nothing". Until the octree went 7 -> 8 levels this test
    asserted that no pick at all was attempted, but that was resolving the costmap, not the arm:
    at 6.25 cm the scissors had no stance (166 of 180 candidates rejected on base_fits), and at
    3.125 cm the planner finds one at the desk's edge, (1.18, -0.01), because the inflated
    pedestal boundary is now resolved to the nearer cell. Nothing was lost from the costmap —
    obstacle cells went 76 -> 150 over the same pedestal. So the scissors IS attempted now, and
    what must hold is that every pick is made from a stance the arm can actually reach.

    ⚠ That stance is a knife edge: 0.4800 m to the scissors against r_max 0.48, and 0.2894 m of
    body clearance against INFLATE_M 0.28. Both numbers are placeholders (roomctl.executor
    ArmModel, "MEASURE"), so a real arm wants a margin here rather than equality — owner robot/.
    """
    import re

    from roomctl.executor import ARM
    code, out = room("reset", "--hard", "--scene", "messy_bench")
    assert "nowhere to stand to pick up 'mug_a1b2'" in out and "180 base poses sampled" in out
    stance, attempted = None, 0
    for line in out.splitlines():                    # every pick comes from the drive before it
        if (m := re.search(r"\[robot\] drive\s+to \(([-\d.]+), ([-\d.]+)\)", line)):
            stance = (float(m.group(1)), float(m.group(2)))
        elif (m := re.search(r"\[robot\] pick\s+(\S+)\s+at \(([-\d.]+), ([-\d.]+),", line)):
            attempted += 1
            assert stance is not None, f"{m.group(1)} picked without driving anywhere"
            reach = math.dist(stance, (float(m.group(2)), float(m.group(3))))
            assert reach <= ARM.r_max + 1e-6, f"{m.group(1)} picked from {reach:.3f} m away, arm reaches {ARM.r_max}"
    assert attempted == 1 and "1 of 3 objects put right" in out and code == 1


class FakeIssues:
    """robot_sentry.SentryIssues at the boundary: records what the robot resolves."""
    made = 0

    def __init__(self):
        FakeIssues.made += 1
        self.resolved = []

    def resolve_failures(self, kind, capture_id, note):
        self.resolved.append((kind, capture_id, note))
        return ["GITSPACE-B"]


def test_self_heal_is_off_for_the_mock_by_default(room, monkeypatch):
    """docs/28's robot talks to Sentry's API — never by default with a robot that prints."""
    import robot_sentry
    monkeypatch.setattr(robot_sentry.SentryIssues, "from_env", classmethod(lambda cls: pytest.fail("called Sentry")))
    monkeypatch.setenv("ROOM_MOCK_SLIP_ONCE", "mug_a1b2")
    code, out = room("reset", "--hard", "--scene", "messy_bench", "--no-route")
    assert "failed: grasp_slipped" in out and "sentry:" not in out


def test_self_heal_retries_then_resolves_only_what_the_rescan_verified(room, monkeypatch):
    import robot_sentry
    issues = FakeIssues()
    monkeypatch.setattr(robot_sentry.SentryIssues, "from_env", classmethod(lambda cls: issues))
    monkeypatch.setenv("ROOM_SELF_HEAL", "1")
    monkeypatch.setenv("ROOM_MOCK_SLIP_ONCE", "mug_a1b2")
    code, out = room("reset", "--hard", "--scene", "messy_bench", "--no-route")
    assert out.count("[robot] pick   mug_a1b2") == 2, "slipped once, retried once"
    assert "2 of 3 objects put right" in out and "sentry: 1 issue resolved by the robot" in out
    (kind, capture_id, note), = issues.resolved
    assert kind == "grasp_slipped" and capture_id.startswith("cap_") and "rescan came back clean" in note
