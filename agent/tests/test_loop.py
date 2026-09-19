"""agent/loop.py with a scripted gpt-5 and a real throwaway room repo: the tools really run
(git revert -> executor.plan -> execute on the mock arm), no API is called, and the span
order is the one the Sentry screenshot needs."""
import contextlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))
import loop  # noqa: E402
import tools  # noqa: E402
from roomctl.state import Extents, ObjectRecord, Pose, write_tree  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def room(tmp_path):
    """A room repo: commit 1 the mug on the left of the desk, commit 2 it moved right."""
    repo = tmp_path / "room"
    repo.mkdir()
    shutil.copy(ROOT / "room.git" / "room.yaml", repo / "room.yaml")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@t")
    mug = lambda x: ObjectRecord("mug_a1b2", "mug", "desk", Pose(x, 0.0, 0.75, 0), Extents(0.09, 0.09, 0.10),
                                 "#2b4c7e", "2026-09-19T10:00:00Z")
    write_tree(repo, [mug(0.30)])
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "morning")
    write_tree(repo, [mug(0.80)])
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "afternoon: mug moved")
    return repo, _git(repo, "rev-parse", "--short", "HEAD")


class Step:
    def __init__(self, rid, calls=(), text=""):
        self.id, self.output_text = rid, text
        self.output = [type("FC", (), {"type": "function_call", "name": n, "arguments": json.dumps(a), "call_id": f"c{k}"})()
                       for k, (n, a) in enumerate(calls)]


class ScriptedGPT:
    def __init__(self, *steps):
        self.steps, self.sent = list(steps), []
        self.responses = self

    def create(self, **kw):
        self.sent.append(kw)
        return self.steps.pop(0)


@pytest.fixture
def spans(monkeypatch):
    seen = []

    def recorder(label):
        @contextlib.contextmanager
        def cm(*a, **kw):
            seen.append(label(*a, **kw))
            yield None
        return cm

    monkeypatch.setattr(loop.obs, "transaction", recorder(lambda op, name: op))
    monkeypatch.setattr(loop.obs, "span", recorder(lambda op, desc="", **kw: op))
    monkeypatch.setattr(tools.obs, "span", recorder(lambda op, desc="", **kw: op))
    monkeypatch.setattr(tools.obs, "agent_tool", recorder(lambda name, kind="mcp", **kw: f"tool:{name}"))
    return seen


class CannedSearch(tools.Toolbox):
    def search_objects(self, query):
        return {"ok": True, "objects": [{"object_id": "mug_a1b2", "class": "mug", "timeline": [
            {"commit": self.afternoon, "zone": "desk", "pose": {"x": 0.8}}, {"commit": "morning", "pose": {"x": 0.3}}]}]}


def test_one_turn_retrieves_decides_reverts_and_picks_in_that_order(room, spans):
    repo, afternoon = room
    box = CannedSearch(repo)
    box.afternoon = afternoon
    gpt = ScriptedGPT(Step("r1", [("search_objects", {"query": "mug"})]),
                      Step("r2", [("room_revert", {"ref": afternoon, "reason": "that commit moved the mug"})]),
                      Step("r3", text="Reverted the afternoon commit; the (simulated) arm moved the mug back."))
    res = loop.run_turn("put my mug back", box, gpt)

    assert res["answer"].startswith("Reverted")
    assert spans == ["gen_ai.invoke_agent", "agent.decide", "tool:search_objects", "agent.decide",
                     "tool:room_revert", "robot.pick", "robot.place", "agent.decide"]
    revert = res["steps"][1]["result"]
    assert revert["ok"] and revert["moved"] == ["mug_a1b2"] and revert["robot"] == "mock"
    assert _git(repo, "log", "-1", "--format=%s").startswith("Revert")        # git decided the target
    assert gpt.sent[1]["previous_response_id"] == "r1"                           # chained, not re-sent
    assert gpt.sent[1]["input"][0]["type"] == "function_call_output"


def test_unknown_tool_names_are_refused_not_dispatched(room):
    out = tools.Toolbox(room[0]).dispatch("rm_rf", {"path": "/"})
    assert out["ok"] is False and "unknown tool" in out["error"]


def test_a_bad_ref_moves_nothing(room):
    repo, _ = room
    out = tools.Toolbox(repo).dispatch("room_revert", {"ref": "deadbeef", "reason": "x"})
    assert out["ok"] is False and "nothing moved" in out["error"]
    assert _git(repo, "log", "-1", "--format=%s") == "afternoon: mug moved"
