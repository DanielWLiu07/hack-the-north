"""The whole watch loop against the fake robot (plan/roommate/04, ring 2): scenarios 1, 2, 3 and 7.

Nothing here is stubbed between the robot and git: fake/bbsim.py serves a scene over loopback in a BB world
frame that is NOT the room frame; roomctl.bb_nav mirrors it; perception.bb_source turns the voxels into object
records; roomctl.watch debounces `git status` of a real room repo, files chores and reports the badge. The
registration is the simulator's own T (GET /sim/truth): estimating it is another module's job.

Scenarios 4 to 6 (a PR the robot carries out, a map reset mid-job, a nav failure) need the executor wired to
BBNavRobot, and are not here yet.
"""
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

pytest.importorskip("websockets")
from test_bbsim import Sim  # noqa: E402  the same harness: a bbsim subprocess on free loopback ports

from fake.scene_gen import FakeRoom  # noqa: E402
from roomctl import chores, watch_cli  # noqa: E402
from roomctl.bb_nav import BBNav  # noqa: E402
from roomctl.repo import Repo  # noqa: E402
from roomctl.watch import Watch  # noqa: E402

AREA = {"xmin": -1.0, "xmax": 1.0, "ymin": -0.5, "ymax": 1.5, "sweep": False}   # around home, desk and shelf ahead
GAP = 0.25                                                                        # pass_gap_s: a "pass" in test time


class Stack:
    def __init__(self, tmp_path):
        self.repo_path = tmp_path / "room.git"
        FakeRoom(self.repo_path, quiet=True).commit("clean_bench")                # `main`: the bench, tidied
        self.sim = Sim()
        self.sim.wait_ready()
        self.sim.look_at_desk()
        self.sim.post("/map/rectangle", AREA)
        self.sim.wait_job()
        self.nav = BBNav("127.0.0.1", self.sim.ws_port, self.sim.api_port).start()
        self.beats, self.events = [], []
        self.settle()

    def watch(self, tier="B", **kw):
        return Watch(Repo(self.repo_path), self.nav, watch_cli.reg_provider(self.nav), tier=tier, fresh_s=3.0,
                     pass_gap_s=GAP, heartbeat=self.beats.append,
                     publish=lambda name, data: self.events.append((name, data)), **kw)

    def settle(self):
        """The robot has seen what it can see, and OUR mirror has received it (the stream is asynchronous)."""
        self.sim.until(lambda: self.sim.get("/sim/truth")["stale_visible"] == 0, 30, "the robot to see the change")
        want = self.sim.mirror().cells
        self.sim.until(lambda: self.nav.state is not None and self.nav.area is not None
                       and dict(self.nav.mirror.cells) == want, 45, "our mirror to catch up")

    def next_pass(self, w):
        time.sleep(GAP + 0.1)
        passes = w.state.passes
        st = w.tick()
        assert st.blocked is None and st.passes == passes + 1, (st.blocked, st.passes)
        return st

    def porcelain(self):
        return subprocess.run(["git", "-C", str(self.repo_path), "status", "--porcelain", "-uall"],
                              capture_output=True, text=True, check=True).stdout

    def close(self):
        self.nav.close()
        self.sim.close()


@pytest.fixture
def stack(tmp_path, monkeypatch):
    for k, v in (("ROOM_ES", "off"), ("ROOM_EVENTS", "off"), ("ROOM_SENTRY", "off")):
        monkeypatch.setenv(k, v)
    s = Stack(tmp_path)
    yield s
    s.close()


def rows(state, which):
    return [(c["object_id"], c["type"]) for c in getattr(state, which)]


def test_1_an_untouched_desk_is_clean_after_every_one_of_five_passes(stack):
    """G2 across passes: voxels -> candidates -> records must reproduce `main` byte for byte, every time."""
    w = stack.watch()
    for i in range(5):
        st = stack.next_pass(w)
        assert stack.porcelain() == "", f"pass {i + 1}:\n{stack.porcelain()}"
        assert st.clean and not st.pending and not st.confirmed
    assert chores.list_chores(w.repo) == [] and all(b.clean for b in stack.beats)


def test_2_mess_is_pending_then_confirmed_red_a_chore_then_fixed_and_green(stack):
    w = stack.watch(tier="B")
    assert stack.next_pass(w).clean
    stack.sim.post("/sim/move", {"object_id": "mug_a1b2", "x": 0.62, "y": 0.10})   # a roommate moved the mug
    stack.settle()
    st = stack.next_pass(w)
    assert st.clean and rows(st, "pending") == [("mug_a1b2", "modified")]          # one glance: not a mess yet
    assert chores.list_chores(w.repo) == []
    st = stack.next_pass(w)
    assert not st.clean and rows(st, "confirmed") == [("mug_a1b2", "modified")]    # two fresh passes: red
    (c,) = st.confirmed
    assert (c["verdict"], c["action"]) == ("mess", "chore") and c["block_age_s"] is not None and c["block_age_s"] <= 3.0
    (ch,) = chores.list_chores(w.repo, "open")
    assert ch["object_id"] == "mug_a1b2" and c["chore_id"] == ch["id"]
    assert stack.beats[-1].clean is False                                          # what turns the room's badge red
    assert "mug_a1b2" in stack.porcelain()
    st = stack.next_pass(w)
    assert len(chores.list_chores(w.repo)) == 1 and st.last_verified_job is None   # still red; still ONE chore

    stack.sim.post("/sim/move", {"object_id": "mug_a1b2", "x": 0.42, "y": 0.18})   # a person put it back
    stack.settle()
    st = stack.next_pass(w)
    assert st.clean and stack.porcelain() == ""
    assert st.last_verified_job == ch["id"]                                        # verified by rescan, by nothing else
    assert chores.list_chores(w.repo, "open") == [] and chores.list_chores(w.repo)[0]["closed_by"] == "rescan"
    assert stack.beats[-1].clean is True
    states = [(d["clean"], len(d["pending"]), len(d["confirmed"])) for n, d in stack.events if n == "room_state"]
    assert states == [(True, 0, 0), (True, 1, 0), (False, 0, 1), (True, 0, 0)]      # subscribers saw exactly the story


def test_3_an_occluded_object_is_never_reported_deleted(stack):
    w = stack.watch(tier="B")
    assert stack.next_pass(w).clean
    stack.sim.post("/sim/occlude", {"object_id": "tape_measure_91be", "by": "box"})  # a box lands in front of it...
    stack.settle()
    stack.sim.post("/sim/arm", {"op": "pick", "object_id": "tape_measure_91be"})    # ...and behind it, it is taken away
    time.sleep(0.5)
    assert stack.sim.get("/sim/truth")["stale_cells"] > 0                           # the robot cannot know: hidden
    for _ in range(4):
        st = stack.next_pass(w)
        seen = rows(st, "pending") + rows(st, "confirmed")
        assert ("tape_measure_91be", "deleted") not in seen, seen
        assert "zones/desk/tape_measure_91be.yaml" not in stack.porcelain()

    stack.sim.post("/sim/occlude", {"object_id": "tape_measure_91be", "clear": True})  # the control: take the box away
    stack.settle()
    st = stack.next_pass(w)
    st = stack.next_pass(w)
    st = stack.next_pass(w)
    assert ("tape_measure_91be", "deleted") in rows(st, "confirmed") + rows(st, "pending")   # NOW it is really gone


def test_7_after_falling_behind_the_stream_the_mirror_is_rebuilt_and_the_room_still_reads_clean(stack):
    w = stack.watch(tier="B")
    assert stack.next_pass(w).clean
    before = stack.sim.get("/sim/truth")["stats"]
    stack.sim.post("/sim/link", {"bytes_per_s": 30_000})                            # the robot's wifi goes bad
    stack.nav.close()                                                               # and we reconnect into it
    stack.nav = BBNav("127.0.0.1", stack.sim.ws_port, stack.sim.api_port).start()
    try:
        for i in range(14):                                                         # the room keeps changing meanwhile:
            x, y = ((0.62, 0.10), (0.42, 0.18))[i % 2]                              # a mug, moved and put back, in plain view
            stack.sim.post("/sim/move", {"object_id": "mug_a1b2", "x": x, "y": y})
            time.sleep(0.12)
        stack.sim.until(lambda: stack.sim.get("/sim/truth")["stats"]["fell_behind"] > before["fell_behind"], 30,
                        "the robot to drop our queue")
    finally:
        stack.sim.post("/sim/link", {"bytes_per_s": None})
    stack.settle()                                                                  # a fresh full copy: first=1, rebuild
    assert stack.sim.get("/sim/truth")["stats"]["resyncs"] >= before["resyncs"] + 2
    w = stack.watch(tier="B")
    for _ in range(2):
        st = stack.next_pass(w)
        assert st.clean and stack.porcelain() == ""                                 # no ghost objects from stale changes
