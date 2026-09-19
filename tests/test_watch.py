"""roomctl/watch.py: the two-pass debounce, freshness, the badge, chores and "verified by rescan".

No robot and no perception here: the scanner is fake/scene_gen writing a scene into the working tree, and the
"robot" is a stand-in with the three things the loop reads (state, area, mirror). What is pinned:
a change is believed only after TWO fresh passes of ITS block; staring is not re-observing; stale is not a pass.
"""
import base64
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fake.scene_gen import FakeRoom, load_scene  # noqa: E402
from roomctl import chores, frames, pr  # noqa: E402
from roomctl.repo import Repo  # noqa: E402
from roomctl.watch import RoomState, Watch  # noqa: E402

T = frames.SE2.identity()


def area(ages):
    """GET /map's JSON: a 2 x 2 m rectangle anchored at the origin facing +y, so area (X, Y) = BB (x, y) = room
    (x, y) under the identity T. 0.5 m blocks: the desk's mug (0.42, 0.18) is block (col 2, row 1)."""
    ages = np.asarray(ages, float)
    return {"area": {"anchor_world": {"x": 0.0, "y": 0.0, "yaw": 0.0}, "bounds": {"xmin": -1.0, "xmax": 1.0, "ymin": -0.5, "ymax": 1.5}},
            "grid": {"resolution_m": 0.03, "nx": 67, "ny": 67, "cells": base64.b64encode(bytes(67 * 67)).decode()},
            "freshness": {"block_m": 0.5, "nx": 4, "ny": 4, "age_s": ages.tolist()}, "t": 0.0}


class World:
    """The room (a scene), the robot's view of it (block ages), and a clock the test owns."""
    def __init__(self, repo_path):
        self.repo_path, self.scene, self.now = repo_path, load_scene("clean_bench"), 1_000.0
        self.nav = SimpleNamespace(state=SimpleNamespace(ready=True, map_gen=1, x=0.0, y=0.0, h=0.0), area=None, mirror=None)
        self.scans, self.beats, self.events, self.jobs = 0, [], [], []
        self.boom = None                                            # set it and the next scan raises

    def scan(self, repo_dir, nav, reg):
        if self.boom:
            raise self.boom
        self.scans += 1
        FakeRoom(repo_dir, quiet=True).scan(self.scene)

    def move(self, oid, **kw):
        self.scene = replace(self.scene, objects={**self.scene.objects, oid: replace(self.scene.objects[oid], **kw)})

    def look(self, ages=None, dt=6.0):
        """Time passes, and the robot has (or has not) looked at each block."""
        self.now += dt
        if ages is not None or hasattr(self.nav, "area"):
            self.nav.area = None if ages is None else area(ages)

    def watch(self, **kw):
        kw.setdefault("heartbeat", self.beats.append)
        kw.setdefault("publish", lambda name, data: self.events.append((name, data)))
        return Watch(Repo(self.repo_path), self.nav, lambda: (T, 1), scan=self.scan, clock=lambda: self.now, **kw)


@pytest.fixture
def world(tmp_path, monkeypatch):
    for k, v in (("ROOM_ES", "off"), ("ROOM_EVENTS", "off"), ("ROOM_SENTRY", "off")):
        monkeypatch.setenv(k, v)
    path = tmp_path / "room.git"
    FakeRoom(path, quiet=True).commit("clean_bench")
    return World(path)


ALL_FRESH = np.zeros((4, 4))
ALL_STALE = np.full((4, 4), 99.0)


def only(col, row, age=0.0):
    a = np.full((4, 4), 99.0)
    a[row, col] = age
    return a


def test_an_untouched_room_stays_clean_over_five_passes(world):
    w = world.watch(tier="B")
    for _ in range(5):
        world.look(ALL_FRESH)
        st = w.tick()
        assert st.clean and not st.confirmed and not st.pending and st.blocked is None
    assert st.passes == 5 and world.scans == 5 and chores.list_chores(w.repo) == []


def test_a_change_is_pending_after_one_fresh_pass_and_confirmed_after_two(world):
    w = world.watch(tier="B")
    world.look(ALL_FRESH); assert w.tick().clean
    world.move("mug_a1b2", x=0.70, y=-0.30)
    world.look(ALL_FRESH)
    st = w.tick()
    assert st.clean and [c["object_id"] for c in st.pending] == ["mug_a1b2"] and st.pending[0]["passes"] == 1
    assert chores.list_chores(w.repo) == []                       # one glance opens nothing
    world.look(ALL_FRESH)
    st = w.tick()
    assert not st.clean and st.pending == []
    (c,) = st.confirmed
    assert (c["object_id"], c["type"], c["verdict"], c["action"], c["passes"]) == ("mug_a1b2", "modified", "mess", "chore", 2)


def test_staring_is_not_re_observing(world):
    """Eight ticks a second at a desk the robot never looked away from is ONE pass until pass_gap_s has gone by."""
    w = world.watch(tier="B", fresh_s=10.0)                         # pass_gap_s = 5
    world.look(ALL_FRESH); w.tick()
    world.move("mug_a1b2", x=0.70, y=-0.30)
    world.look(ALL_FRESH); assert w.tick().pending[0]["passes"] == 1
    scans = world.scans
    for _ in range(8):
        world.look(ALL_FRESH, dt=0.125)
        st = w.tick()
    assert st.clean and st.pending[0]["passes"] == 1 and world.scans == scans    # no new pass: not even a rescan
    world.look(ALL_FRESH, dt=5.0)
    assert not w.tick().clean


def test_a_block_that_went_stale_and_came_back_is_a_new_pass_at_once(world):
    w = world.watch(tier="B")
    world.look(ALL_FRESH); w.tick()
    world.move("mug_a1b2", x=0.70, y=-0.30)
    world.look(ALL_FRESH); w.tick()
    world.look(ALL_STALE, dt=0.5); assert w.tick().clean            # the robot looked away
    world.look(ALL_FRESH, dt=0.5)                                   # and back, well inside pass_gap_s
    assert not w.tick().clean


def test_only_a_pass_of_the_changes_own_block_counts(world):
    w = world.watch(tier="B")
    world.look(ALL_FRESH); w.tick()
    world.move("mug_a1b2", x=0.70, y=-0.30)                         # now in block (col 3, row 0)
    world.look(only(3, 0)); assert w.tick().pending[0]["passes"] == 1
    for _ in range(4):                                              # the robot patrols the OTHER side of the room
        world.look(only(0, 3))
        st = w.tick()
        assert st.clean and st.pending[0]["passes"] == 1
    assert st.stale_blocks == 15
    world.look(only(3, 0))
    assert not w.tick().clean


def test_nothing_fresh_means_no_scan_and_no_conclusion(world):
    w = world.watch(tier="B")
    world.move("mug_a1b2", x=0.70, y=-0.30)
    for _ in range(5):
        world.look(ALL_STALE)
        st = w.tick()
    assert world.scans == 0 and st.clean and st.passes == 0 and st.stale_blocks == 16
    never = np.full((4, 4), -1.0)
    world.look(never)
    assert w.tick().passes == 0                                     # -1 is "never seen", not "fresh"


def test_a_change_that_changes_again_starts_the_count_over(world):
    w = world.watch(tier="B")
    world.look(ALL_FRESH); w.tick()
    world.move("mug_a1b2", x=0.70, y=-0.30)
    world.look(ALL_FRESH); w.tick()
    world.move("mug_a1b2", x=0.60, y=0.30)                          # a hand is still moving it around
    world.look(ALL_FRESH)
    st = w.tick()
    assert st.clean and st.pending[0]["passes"] == 1
    world.move("mug_a1b2", x=0.42, y=0.18)                          # ...and puts it back: it never was a mess
    world.look(ALL_FRESH)
    st = w.tick()
    assert st.clean and not st.pending and chores.list_chores(w.repo) == []


def test_tier_b_files_one_chore_and_a_fix_closes_it_and_verifies_it(world):
    w = world.watch(tier="B")
    world.look(ALL_FRESH); w.tick()
    world.move("mug_a1b2", x=0.70, y=-0.30)
    for _ in range(4):
        world.look(ALL_FRESH); st = w.tick()
    (ch,) = chores.list_chores(w.repo)                              # four dirty ticks, ONE chore
    assert ch["status"] == "open" and ch["object_id"] == "mug_a1b2" and ch["verdict"] == "mess"
    assert st.confirmed[0]["chore_id"] == ch["id"] and st.last_verified_job is None
    assert [d["id"] for n, d in world.events if n == "chore"] == [ch["id"]]
    world.move("mug_a1b2", x=0.42, y=0.18)                          # a person put it back
    world.look(ALL_FRESH)
    st = w.tick()
    assert st.clean and st.last_verified_job == ch["id"]           # the fresh pass that showed it fixed IS the proof
    (ch,) = chores.list_chores(w.repo)
    assert ch["status"] == "closed" and ch["closed_by"] == "rescan"
    assert [d["status"] for n, d in world.events if n == "chore"] == ["open", "closed"]


def test_tier_a_asks_for_a_tidy_job_once_and_only_a_clean_fresh_pass_verifies_it(world):
    asked = []
    w = world.watch(tier="A", jobs=lambda action, c: asked.append((action, c["object_id"])) or "job-7")
    world.look(ALL_FRESH); w.tick()
    world.move("mug_a1b2", x=0.70, y=-0.30)
    for _ in range(3):
        world.look(ALL_FRESH); st = w.tick()
    assert asked == [("tidy", "mug_a1b2")] and st.confirmed[0]["job_id"] == "job-7"
    assert st.last_verified_job is None and chores.list_chores(w.repo) == []
    world.move("mug_a1b2", x=0.42, y=0.18)                          # the robot put it back...
    world.look(ALL_STALE)
    assert w.tick().last_verified_job is None                       # ...but nobody has LOOKED yet
    world.look(ALL_FRESH)
    assert w.tick().last_verified_job == "job-7"


def test_no_act_reports_and_does_nothing(world):
    asked = []
    w = world.watch(tier="B", act=False, jobs=lambda *a: asked.append(a))
    world.look(ALL_FRESH); w.tick()
    world.move("mug_a1b2", x=0.70, y=-0.30)
    for _ in range(3):
        world.look(ALL_FRESH); st = w.tick()
    assert not st.clean and asked == [] and chores.list_chores(w.repo) == []


def test_an_untracked_object_goes_to_lost_and_found_or_becomes_a_chore(world):
    thing = replace(world.scene.objects["cup_7e21"], id="sock_0001", x=0.75, y=0.35)
    asked = []
    w = world.watch(tier="A", jobs=lambda action, c: asked.append((action, c["verdict"])) or "job-1")
    world.look(ALL_FRESH); w.tick()
    world.scene = replace(world.scene, objects={**world.scene.objects, "sock_0001": thing})
    for _ in range(2):
        world.look(ALL_FRESH); st = w.tick()
    (c,) = st.confirmed
    assert c["type"] == "untracked" and asked == [("lost_and_found", "untracked_shared")]


def test_a_personal_zone_is_never_dirty(world):
    y = (world.repo_path / "room.yaml").read_text().replace("  shelf:\n", "  shelf:\n    policy: personal\n    owner: daniel\n")
    (world.repo_path / "room.yaml").write_text(y)
    Repo(world.repo_path).git("commit", "-qam", "the shelf is daniel's")
    w = world.watch(tier="B")
    world.look(ALL_FRESH); w.tick()
    shelf = next(o for o in world.scene.objects.values() if o.zone == "shelf")
    world.move(shelf.id, x=shelf.x + 0.2)
    for _ in range(3):
        world.look(ALL_FRESH); st = w.tick()
    assert st.clean and st.ignored == 1 and not st.pending and chores.list_chores(w.repo) == []


def test_an_approved_pull_request_is_a_decision_the_robot_owes(world):
    w = world.watch(tier="A", jobs=lambda a, c: "job-9")
    repo = Repo(world.repo_path)
    pr.approve(repo, pr.propose(repo, "mug_a1b2", "shelf", "daniel").id, "andrew")
    for _ in range(2):
        world.look(ALL_FRESH); st = w.tick()
    (mug,) = [c for c in st.confirmed if c["object_id"] == "mug_a1b2"]      # deleted there + untracked here = ONE change
    assert (mug["type"], mug["verdict"], mug["action"], mug["job_id"]) == ("moved", "decision", "tidy", "job-9")
    assert mug["zone"] == "shelf" and mug["to_zone"] == "desk"              # main says shelf; the room still says desk


def test_the_loop_concludes_nothing_without_a_registration_or_slam(world):
    w = Watch(Repo(world.repo_path), world.nav, lambda: None, scan=world.scan, clock=lambda: world.now)
    world.look(ALL_FRESH)
    assert w.tick().blocked == "no_registration" and world.scans == 0
    w = world.watch()
    world.nav.state.ready = False
    assert w.tick().blocked == "slam_not_ready"
    world.nav.state.ready, world.nav.state.map_gen = True, 2        # the registration is for map 1
    assert w.tick().blocked == "map_reset" and world.scans == 0


def test_a_map_reset_forgets_every_count(world):
    gen = [1]
    w = Watch(Repo(world.repo_path), world.nav, lambda: (T, gen[0]), tier="B", scan=world.scan, clock=lambda: world.now)
    world.look(ALL_FRESH); w.tick()
    world.move("mug_a1b2", x=0.70, y=-0.30)
    world.look(ALL_FRESH); assert w.tick().pending[0]["passes"] == 1
    world.nav.state.map_gen = gen[0] = 2                            # reset, and re-registered
    world.look(ALL_FRESH)
    st = w.tick()
    assert st.clean and st.pending[0]["passes"] == 1                # one pass on the NEW map: not two
    world.look(ALL_FRESH)
    assert not w.tick().clean


def test_the_heartbeat_gets_every_tick_and_subscribers_only_get_changes(world):
    w = world.watch(tier="B", republish_s=0)                        # 0: only changes, which is what this is about
    for _ in range(3):
        world.look(ALL_FRESH); w.tick()
    assert len(world.beats) == 3 and all(isinstance(b, RoomState) and b.clean for b in world.beats)
    assert [n for n, _ in world.events] == ["room_state"]           # three identical ticks, one event
    world.move("mug_a1b2", x=0.70, y=-0.30)
    for _ in range(2):
        world.look(ALL_FRESH); w.tick()
    states = [d for n, d in world.events if n == "room_state"]
    assert [(d["clean"], len(d["pending"]), len(d["confirmed"])) for d in states] == [(True, 0, 0), (True, 1, 0), (False, 0, 1)]
    assert world.beats[-1].clean is False                           # what the room's CI badge turns red on


def test_an_output_that_throws_never_stops_the_watching(world):
    def boom(*a):
        raise RuntimeError("the dashboard is down")
    w = world.watch(tier="B", heartbeat=boom, publish=boom)
    world.look(ALL_FRESH)
    assert w.tick().clean


def test_with_no_area_map_the_room_is_one_block(world):
    w = world.watch(tier="B")
    world.look(None); w.tick()
    world.move("mug_a1b2", x=0.70, y=-0.30)
    world.look(None); assert w.tick().clean
    world.look(None)
    st = w.tick()
    assert not st.clean and st.stale_blocks == 0 and st.confirmed[0]["block_age_s"] is None


def test_the_badge_module_accepts_the_state_as_is(world, tmp_path, monkeypatch):
    room_clean = pytest.importorskip("telemetry.room_clean")
    monkeypatch.setenv("ROOM_CLEAN_STATE", str(tmp_path / "badge.json"))
    sent = []
    beat = room_clean.RoomCleanBeat(heartbeat=lambda slug, status, **kw: sent.append(status), switch=lambda: True)
    w = world.watch(tier="B", heartbeat=beat)
    world.look(ALL_FRESH); w.tick()
    world.move("mug_a1b2", x=0.70, y=-0.30)
    world.look(ALL_FRESH); w.tick()                                 # pending: still ok
    assert sent == ["ok"]
    world.look(ALL_FRESH); w.tick()
    assert sent == ["ok", "error"] and room_clean.read_state()["confirmed"] == 1


def test_any_source_with_a_mirror_will_do_and_no_freshness_grid_means_every_scan_is_fresh(world):
    """The voxels may come from another daemon entirely: no .state, no .area, no patrol, no map-reset hook.
    Then the room is one block, every scan is a fresh pass (pass_gap_s apart), and the debounce still holds."""
    world.nav = SimpleNamespace(mirror=object())
    w = world.watch(tier="B")
    world.look(None); assert w.tick().blocked is None
    world.move("mug_a1b2", x=0.70, y=-0.30)
    world.look(None); st = w.tick()
    assert st.clean and st.pending[0]["passes"] == 1
    world.look(None, dt=0.1); assert w.tick().clean                  # too soon to be a second look
    world.look(None); st = w.tick()
    assert not st.clean and st.stale_blocks == 0
    w.run(every_s=0, stop=lambda: True)                              # run() needs nothing more from the source either


def test_a_quiet_clean_loop_still_says_so_now_and_then(world):
    """A dashboard cannot tell "clean since an hour ago" from "dead an hour ago" without hearing it again."""
    w = world.watch(tier="B", republish_s=30.0)
    for _ in range(9):                                              # 54 s of an untouched room, 6 s a tick
        world.look(ALL_FRESH)
        w.tick()
    states = [d for n, d in world.events if n == "room_state"]
    assert len(states) == 2 and all(d["clean"] for d in states)     # the first, then one ~30 s later
    assert states[1]["passes"] > states[0]["passes"] and states[1]["at"] > states[0]["at"]


def test_a_dashboard_that_refuses_job_events_cannot_fail_the_job(world, caplog):
    """The roommate inlet takes room_state / nav / chore / pr, so the executor's "job" narration is a 400. A
    dashboard that refuses (or is down) must never end a job before the robot has moved."""
    import logging
    from roomctl.caretaker import Caretaker
    calls = []

    def refuses(name, data):
        calls.append(name)
        raise RuntimeError("HTTP Error 400: event must be one of room_state, nav, chore, pr")
    ct = Caretaker(Repo(world.repo_path), world.nav, lambda: (T, None), tier="A", threaded=False, publish=refuses)
    with caplog.at_level(logging.WARNING, logger="roomctl.caretaker"):
        job = ct("tidy", {"object_id": "mug_a1b2", "action": "tidy", "type": "modified", "verdict": "mess", "zone": "desk"})
    assert ct.results[job]["state"] == "done", ct.results[job]
    assert calls and len(caplog.records) == 1 and "did not take" in caplog.records[0].message   # said once, not per step


def test_a_scan_that_cannot_finish_never_takes_the_loop_down(world, caplog):
    """A full disk once killed this loop mid-write, leaving the room frozen with a green badge and stale blocks.
    A pass that raises must leave the loop up, the last verdict standing, and the reason visible."""
    w = world.watch(tier="B")
    world.look(ALL_FRESH); w.tick()
    world.move("mug_a1b2", x=0.70, y=-0.30)
    for _ in range(2):
        world.look(ALL_FRESH); w.tick()
    assert not w.state.clean                                        # a real verdict, before the disk fills
    world.boom = OSError(28, "No space left on device")
    for _ in range(3):
        world.look(ALL_FRESH)
        st = w.tick()
    assert st.blocked == "scan_failed: OSError" and st.clean is False   # still up, still saying the room is dirty
    assert st.confirmed and st.passes == 3                          # the last verdict stands; no pass was counted
    assert len(world.beats) == 6                                    # the badge kept hearing from it every tick
    assert len([r for r in caplog.records if "could not be completed" in r.message]) <= 1
    world.boom = None                                               # the disk is cleared
    world.look(ALL_FRESH)
    st = w.tick()
    assert st.blocked is None and st.passes == 4


def test_an_object_in_the_robots_hand_is_not_a_mess(world):
    """A held object is invisible to the map, so the room reads it as deleted. Acting on that starts a second job
    for something the robot is already carrying, and the room can never reach a clean pass."""
    class Jobs:
        def __init__(self):
            self.asked, self.holding = [], set()

        def busy(self):
            return bool(self.holding)

        def in_flight(self):
            return set(self.holding)

        def __call__(self, action, change):
            self.asked.append((action, change["object_id"]))
            self.holding.add(change["object_id"])
            return f"tidy-{len(self.asked)}"

        def running(self, job_id):
            return bool(self.holding)
    jobs = Jobs()
    w = world.watch(tier="A", jobs=jobs)
    world.look(ALL_FRESH); w.tick()
    world.move("mug_a1b2", x=0.70, y=-0.30)
    for _ in range(2):
        world.look(ALL_FRESH); st = w.tick()
    assert jobs.asked == [("tidy", "mug_a1b2")] and not st.clean

    del world.scene.objects["mug_a1b2"]                              # the arm picks it up: gone from the map
    for _ in range(3):
        world.look(ALL_FRESH); st = w.tick()
    assert st.clean and st.carried == 1 and not st.confirmed and not st.pending
    assert jobs.asked == [("tidy", "mug_a1b2")]                      # no second job for the mug in its own gripper
    assert st.last_verified_job is None                              # and a job still running is never "verified"
