"""The roommate's verbs: `room pr`, `room why`, `room restore --before`, `room chores` (plan/roommate/03 §6, §7, §12)."""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fake.scene_gen import FakeRoom  # noqa: E402
from roomctl import chores, policy  # noqa: E402
from roomctl.cli import main  # noqa: E402
from roomctl.repo import Repo  # noqa: E402

T0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=6)


@pytest.fixture
def room(tmp_path, monkeypatch, capsys):
    for k, v in (("ROOM_ES", "off"), ("ROOM_EVENTS", "off"), ("ROOM_ROBOT", "mock"), ("ROOM_SENTRY", "off"),
                 ("ROOM_AUTHOR", "daniel")):
        monkeypatch.setenv(k, v)
    for k in ("ROOM_SCANNER", "ELASTIC_URL", "ELASTIC_API_KEY", "BB_HOST"):
        monkeypatch.delenv(k, raising=False)
    repo = tmp_path / "room.git"
    FakeRoom(repo, quiet=True).commit("clean_bench", "the bench, tidied", at=T0)

    def run(*args: str):
        code = main(["--repo", str(repo), *args])
        cap = capsys.readouterr()
        return code, cap.out, cap.err
    run.repo = Repo(repo)
    return run


def test_pr_open_list_approve_is_a_decision_the_room_has_not_caught_up_with(room):
    code, out, _ = room("pr", "list")
    assert code == 0 and "no open pull requests" in out
    code, out, _ = room("pr", "open", "mug_a1b2", "--to", "shelf", "--title", "the mug lives on the shelf")
    assert code == 0 and "opened #1" in out and "mug_a1b2 -> shelf" in out and "by daniel" in out
    assert room.repo.status().clean                                   # proposing touches neither main nor the room
    code, out, _ = room("pr", "list", "--json")
    (p,) = json.loads(out)
    assert (p["id"], p["status"], p["author"]) == (1, "open", "daniel") and p["ops"][0]["to"]["zone"] == "shelf"
    code, out, _ = room("pr", "approve", "1", "--by", "andrew")
    assert code == 0 and "merged #1" in out and "decision" in out
    assert room.repo.records()["mug_a1b2"].zone == "shelf"            # main moved...
    assert policy.decided_objects(room.repo) == {"mug_a1b2"}          # ...the room has not: a decision, not a mess
    msg = subprocess.run(["git", "-C", str(room.repo.path), "log", "-1", "--format=%B"], capture_output=True, text=True).stdout
    assert "Approved-by: andrew" in msg
    assert "no open pull requests" in room("pr", "list")[1]
    assert "merged" in room("pr", "list", "--all")[1]


def test_pr_close_declines_and_bad_requests_are_fatal_not_tracebacks(room):
    room("pr", "open", "cup_7e21", "--to", "shelf")
    code, out, _ = room("pr", "close", "1")
    assert code == 0 and "closed #1" in out and "no open pull requests" in room("pr", "list")[1]
    assert room.repo.records()["cup_7e21"].zone == "desk"
    for args in (("pr", "approve", "1"), ("pr", "approve", "9"), ("pr", "open", "nothing_0000", "--to", "shelf")):
        code, _, err = room(*args)
        assert code == 128 and err.startswith("fatal:"), (args, err)


def test_why_says_what_it_needs_when_elasticsearch_is_not_there(room):
    code, _, err = room("why", "HEAD")
    assert code == 128 and "needs Elasticsearch" in err


def test_why_renders_the_explanation_and_exits_1_for_an_untrustworthy_commit(room, monkeypatch):
    from roomctl import publish, why
    monkeypatch.setattr(publish, "es_from_env", lambda: object())
    seen = {}

    def explain(repo, ref, es, seconds):
        seen.update(ref=ref, seconds=seconds)
        return {"commit": "a3f9c1d0", "message": "scan", "capture_id": "cap_0012", "trustworthy": False,
                "gate": {"passed": False, "skew_ms": 41.0, "max_skew_ms": 25.0, "tilt_rate_max": 0.2, "max_tilt_rate": 0.05},
                "telemetry": {"tilt_rate": {"peak": 0.21}}, "findings": ["the robot was knocked 0.4 s before the shutter"],
                "trace": {"id": "234f3164", "url": None}}
    monkeypatch.setattr(why, "explain", explain)
    code, out, _ = room("why", "HEAD~0", "--seconds", "3")
    assert code == 1 and seen == {"ref": "HEAD~0", "seconds": 3.0}
    assert "quality gate REJECTED" in out and "knocked" in out and "don't trust" in out
    assert json.loads(room("why", "--json")[1])["capture_id"] == "cap_0012"


def test_restore_before_a_time_finds_the_commit_and_plans_back_to_it(room):
    FakeRoom(room.repo.path, quiet=True).commit("messy_bench", "after dinner", at=T0 + timedelta(hours=3))
    tidy = subprocess.run(["git", "-C", str(room.repo.path), "rev-parse", "HEAD~1"], capture_output=True, text=True).stdout.strip()
    phrase = (T0 + timedelta(hours=2)).isoformat()                    # between the two commits
    code, out, err = room("restore", "--before", phrase, "--plan-only", "--scene", "messy_bench", "--no-route")
    assert code == 0, err
    assert tidy[:7] in err and "the bench, tidied" in err and "found by git" in err     # says which commit, and how it knew
    assert "mug_a1b2" in out                                          # the plan goes back to the tidy bench
    code, _, err = room("restore", "--before", (T0 - timedelta(hours=1)).isoformat(), "--plan-only", "--scene", "messy_bench")
    assert code == 128 and "no commit" in err
    code, _, err = room("restore", "--before", "when the cows come home", "--plan-only", "--scene", "messy_bench")
    assert code == 128 and "--before" in err
    code, _, err = room("reset", "--hard", "--before", "dinner", "--plan-only", "--scene", "messy_bench")
    assert code == 128 and "room restore" in err


def test_chores_lists_open_ones_and_all(room):
    assert "no open chores" in room("chores")[1]
    c, _ = chores.open_chore(room.repo, {"object_id": "mug_a1b2", "zone": "desk", "type": "modified", "verdict": "mess", "owner": None})
    assert c["id"] in room("chores")[1]
    chores.close_chore(room.repo, c["id"])
    assert "no open chores" in room("chores")[1] and "closed" in room("chores", "--all")[1]
    assert json.loads(room("chores", "--all", "--json")[1])[0]["closed_by"] == "rescan"
