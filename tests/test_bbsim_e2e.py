"""The whole watch loop against the fake robot (plan/roommate/04, ring 2): scenarios 1, 2, 3 and 7.

Nothing here is stubbed between the robot and git: fake/bbsim.py serves a scene over loopback in a BB world
frame that is NOT the room frame; roomctl.bb_nav mirrors it; perception.bb_source turns the voxels into object
records; roomctl.watch debounces `git status` of a real room repo, files chores and reports the badge. The
registration is the simulator's own T (GET /sim/truth): estimating it is another module's job.

Scenarios 4 to 6 close the loop: roomctl.caretaker runs plan -> route (on the robot's own floor map) -> execute
through roomctl.bb_nav.BBNavRobot, with the simulator's /sim/arm as the arm. The simulated arm brings its own
reach (0.85 m): with the real placeholder (0.48 m) nothing deeper than ~0.20 m into a table can be stood in
front of, which the planner says honestly ("nowhere to stand") and is a hardware number, not a software one.
"""
import math
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
from roomctl import chores, pr, watch_cli  # noqa: E402
from roomctl.caretaker import Caretaker, SimArm  # noqa: E402
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


# ── closing the loop: the robot does something about it ─────────────────────────────────

SWEPT = {"xmin": -1.2, "xmax": 1.2, "ymin": -0.6, "ymax": 1.6, "sweep": True}     # the floor map a base pose needs


class Voice:
    def __init__(self):
        self.said, self.leds = [], []

    def say(self, text):
        self.said.append(text)

    def led(self, state):
        self.leds.append(state)


class HeldRegistration:
    """A registration as the real one behaves: estimated once for one map, and WRONG (not silently refreshed)
    after a reset until somebody estimates it again."""
    def __init__(self, nav):
        self._fresh = watch_cli.reg_provider(nav)
        self.reregister()

    def reregister(self):
        self.value = self._fresh()

    def __call__(self):
        return self.value


@pytest.fixture
def swept(stack):
    stack.sim.post("/map/rectangle", SWEPT)
    assert stack.sim.wait_job(180)["error"] is None
    stack.sim.look_at_desk()
    stack.settle()
    stack.voice, stack.job_events = Voice(), []
    return stack


def caretaker(stack, tier="A", reg=None, on_event=None):
    def publish(name, data):
        stack.job_events.append((name, data))
        if on_event:
            on_event(name, data)
    return Caretaker(Repo(stack.repo_path), stack.nav, reg or watch_cli.reg_provider(stack.nav), tier=tier,
                     arm=SimArm("127.0.0.1", stack.sim.api_port) if tier == "A" else None, voice=stack.voice,
                     publish=publish, reregister_s=20.0)


def finish(stack, ct, timeout=180):
    """Let the job end, then let the robot LOOK again (on the real robot the patrol does this)."""
    ct.join(timeout)
    assert not ct.busy(), "the job is still running"
    stack.sim.post("/stop")
    stack.sim.look_at_desk()
    stack.settle()


def where(stack, oid):
    o = stack.sim.get("/sim/truth")["objects"][oid]
    return o["x"], o["y"], o["z"]


def test_4_an_approved_pull_request_is_carried_out_and_a_knock_is_restored_to_the_new_spot(swept):
    repo = Repo(swept.repo_path)
    p = pr.propose(repo, "mug_a1b2", "shelf", "daniel", "the mug lives on the shelf")
    pr.approve(repo, p.id, "andrew")
    new = repo.records()["mug_a1b2"].pose                                          # where `main` now says it lives
    assert repo.records()["mug_a1b2"].zone == "shelf"
    ct = caretaker(swept)
    w = swept.watch(tier="A", jobs=ct)
    st = swept.next_pass(w)
    assert [(c["type"], c["verdict"]) for c in st.pending] == [("moved", "decision")]   # approved, not yet done: NOT a mess
    st = swept.next_pass(w)
    (c,) = st.confirmed
    assert c["action"] == "tidy" and c["job_id"] == "tidy-1" and st.last_verified_job is None
    finish(swept, ct)
    assert ct.results["tidy-1"]["state"] == "done" and ct.results["tidy-1"]["summary"] == "1 of 1"
    assert where(swept, "mug_a1b2") == pytest.approx((new.x, new.y, new.z), abs=0.011)   # the robot moved it
    st = swept.next_pass(w)
    assert st.clean and swept.porcelain() == "" and st.last_verified_job == "tidy-1"      # verified by rescan

    swept.sim.post("/sim/move", {"object_id": "mug_a1b2", "x": 0.25, "y": 0.34, "z": 0.755})   # knocked back onto the desk
    swept.settle()
    st = swept.next_pass(w)
    st = swept.next_pass(w)
    (c,) = st.confirmed
    assert (c["verdict"], c["action"], c["job_id"]) == ("mess", "tidy", "tidy-2")        # now it IS a mess
    finish(swept, ct)
    assert where(swept, "mug_a1b2") == pytest.approx((new.x, new.y, new.z), abs=0.011)   # back to the SHELF, the new home
    st = swept.next_pass(w)
    assert st.clean and st.last_verified_job == "tidy-2"
    assert swept.voice.said == ["Tidied.", "Tidied."]


def test_5_a_map_reset_mid_job_waits_for_a_new_registration_and_retries_once(swept):
    reg = HeldRegistration(swept.nav)
    gen0 = swept.nav.state.map_gen
    fired = []

    def reset_once(name, data):
        if data.get("state") == "moving_to_pick" and not fired:
            fired.append(True)
            swept.sim.post("/sim/reset_map")                                       # somebody wiped the robot's map
            swept.sim.until(lambda: swept.nav.state.map_gen != gen0, 20, "the new map_gen to reach us")
    ct = caretaker(swept, reg=reg, on_event=reset_once)
    w = swept.watch(tier="A", jobs=ct)
    w.reg_provider = reg
    assert swept.next_pass(w).clean
    swept.sim.post("/sim/move", {"object_id": "mug_a1b2", "x": 0.25, "y": 0.34})
    swept.settle()
    swept.next_pass(w)
    assert swept.next_pass(w).confirmed[0]["job_id"] == "tidy-1"
    swept.sim.until(lambda: fired and swept.nav.state.map_gen != gen0, 30, "the reset")
    assert w.tick().blocked in ("slam_not_ready", "map_reset")                     # the loop concludes nothing meanwhile:
    swept.sim.until(lambda: w.tick().blocked == "map_reset", 20, "SLAM to come back")  # first no pose, then the wrong map
    assert ct.busy()                                                               # the job is WAITING, not failed
    time.sleep(1.0)
    reg.reregister()                                                               # the registration catches up
    ct.join(240)
    r = ct.results["tidy-1"]
    assert (r["state"], r["tries"], r["retried_after"], r["summary"]) == ("done", 2, "map_reset", "1 of 1")
    assert where(swept, "mug_a1b2")[:2] == pytest.approx((0.42, 0.18), abs=0.011)
    assert swept.nav.area is not None                                              # the area the reset wiped is back


def test_6_a_nav_failure_is_an_honest_two_of_three_and_one_filed_issue(swept, monkeypatch):
    from roomctl import bb_nav
    filed = []
    monkeypatch.setattr(bb_nav.obs, "robot_failure", lambda kind, detail, **kw: filed.append((kind, kw.get("level"))))
    drives = []

    def fail_second_drive(name, data):
        if data.get("state") == "moving_to_pick":
            drives.append(data["object_id"])
            if len(drives) == 2:
                swept.sim.post("/sim/fail", {"kind": "nav", "once": True})
    ct = caretaker(swept, on_event=fail_second_drive)
    w = swept.watch(tier="A", jobs=ct)
    assert swept.next_pass(w).clean
    # Every spot here must be REACHABLE, so the only thing that fails is the failure this test injects. The arm's
    # reach is a placeholder (executor.ARM 0.48 m) times perception's reach margin (0.9), and the base is kept
    # INFLATE_M (0.28) off the desk: with the simulated arm's 0.85 that is 0.765 m from a stance just off the front
    # edge at x = -0.20, so an object much past x = 0.56 cannot be stood in front of at all. (0.60, 0.30) used to
    # work and now comes back "3 reach margin": real, and not what this scenario is about.
    for oid, x, y in (("mug_a1b2", 0.25, 0.34), ("cup_7e21", 0.18, -0.40), ("glasses_case_d04f", 0.30, 0.15)):
        swept.sim.post("/sim/move", {"object_id": oid, "x": x, "y": y})
    swept.settle()
    swept.next_pass(w)
    st = swept.next_pass(w)
    assert {c["job_id"] for c in st.confirmed} == {"tidy-1"} and len(st.confirmed) == 3     # three messes, ONE job
    finish(swept, ct, 300)
    r = ct.results["tidy-1"]
    assert (r["state"], r["summary"], len(r["failed"])) == ("failed", "2 of 3", 1), r["failed"]
    assert "nav_failed" in r["failed"][0]["why"] and r["failed"][0]["object_id"] == drives[1], r["failed"]
    assert filed == [("nav_failed", "error")]                                      # one failure, one issue: not three
    assert swept.voice.said == ["I put 2 of 3 back."]
    st = swept.next_pass(w)
    assert not st.clean and [c["object_id"] for c in st.confirmed] == [drives[1]]   # the room says the same thing
    assert st.last_verified_job is None                                            # and nothing is called verified


def test_tier_b_drives_up_faces_the_mess_says_so_and_a_person_fixing_it_is_the_verification(swept):
    ct = caretaker(swept, tier="B")
    w = swept.watch(tier="B", jobs=ct)
    assert swept.next_pass(w).clean
    swept.sim.post("/sim/move", {"object_id": "mug_a1b2", "x": 0.25, "y": 0.34})
    swept.settle()
    swept.next_pass(w)
    (c,) = swept.next_pass(w).confirmed
    assert c["action"] == "chore" and c["chore_id"] == "chore-1" and c["visit_id"] == "visit-1"
    ct.join(180)
    r = ct.results["visit-1"]
    assert r["state"] == "done", r
    me = swept.sim.get("/sim/truth")["robot_room"]
    d = math.hypot(0.25 - me["x"], 0.34 - me["y"])
    bearing = math.degrees(math.atan2(0.34 - me["y"], 0.25 - me["x"]))
    assert 0.35 <= d <= 1.65 and abs((bearing - me["yaw_deg"] + 180) % 360 - 180) < 12   # standing off, facing it
    assert swept.voice.said == ["The mug is not where it belongs. I can't move it myself, so I've filed a chore."]
    assert swept.voice.leds == ["attention"] and where(swept, "mug_a1b2")[:2] == pytest.approx((0.25, 0.34))  # it moved nothing
    swept.sim.post("/sim/move", {"object_id": "mug_a1b2", "x": 0.42, "y": 0.18})       # a person puts it back
    finish(swept, ct)
    st = swept.next_pass(w)
    assert st.clean and st.last_verified_job == "chore-1" and chores.list_chores(w.repo, "open") == []
