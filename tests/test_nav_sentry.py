"""roomctl/bb_nav.py → Sentry (plan/roommate/03-interfaces.md §9): every refused or failed trip is ONE issue
with the robot's own view (pose, goal, path, status, map_gen), grouped by code; map_reset is a warning; status
transitions and `ready` flips are breadcrumbs. A recording stand-in for obs: nothing is sent anywhere."""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roomctl import bb_nav  # noqa: E402
from roomctl.bb_nav import BBNav, NavState  # noqa: E402
from roomctl.executor import BasePose, RobotError  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bb_protocol import FakeNav, T, robot  # noqa: E402


@pytest.fixture()
def rec(monkeypatch):
    r = SimpleNamespace(issues=[], crumbs=[], spans=[])

    @contextlib.contextmanager
    def span(op, desc="", **data):
        sp = SimpleNamespace(data=dict(data))
        sp.set_data = lambda k, v: sp.data.__setitem__(k, v)
        r.spans.append((op, sp))
        yield sp

    monkeypatch.setattr(bb_nav, "obs", SimpleNamespace(
        span=span,
        robot_failure=lambda kind, detail, **kw: r.issues.append({"kind": kind, "detail": detail, **kw}),
        breadcrumb=lambda category, message, level="info", **data: r.crumbs.append((category, message, data))))
    return r


def test_a_short_arrival_is_one_issue_with_the_robots_view(rec):
    nav = FakeNav(T, off=(0.30, 0.0))
    nav.state = NavState(ready=True, status="idle", x=1.2, y=0.4, h=0.3, goal=(1.5, 0.4), path=[[1, 0]] * 9, map_gen=3)
    with pytest.raises(RobotError):
        robot(nav, gen=3).drive(BasePose(0.3, -0.2, 0.0))
    (i,) = rec.issues
    assert (i["kind"], i["level"], i["fingerprint"]) == ("nav_short", "error", ["robot", "nav", "nav_short"])
    c = i["context"]
    assert c["pose_bb"] == {"x": 1.2, "y": 0.4, "h": 0.3} and c["goal_bb"] == [1.5, 0.4] and c["path_len"] == 9
    assert c["bb_status"] == "idle" and c["map_gen"] == 3 and c["arrive_err_m"] == pytest.approx(0.30, abs=0.01)
    assert c["target_room"] and i["action"] == "navigate" and i["robot"] == "bracketbot"
    assert rec.spans[0][0] == "nav.navigate" and rec.spans[0][1].data["arrive_err_m"] == pytest.approx(0.30, abs=0.01)


def test_a_failed_job_is_nav_failed_and_its_span_says_so(rec):
    with pytest.raises(RobotError):
        robot(FakeNav(T, error="NavError: no path")).drive(BasePose(0.3, -0.2, 0.0))
    assert [i["kind"] for i in rec.issues] == ["nav_failed"] and rec.spans[0][1].data["nav.error"] == "nav_failed"


@pytest.mark.parametrize("state,code,level", [
    (NavState(ready=False, status="idle"), "slam_not_ready", "error"),
    (NavState(ready=True, status="manual control"), "manual_override", "warning"),
])
def test_a_refusal_before_the_trip_is_filed_too(rec, state, code, level):
    nav = FakeNav(T)
    nav.state = state
    with pytest.raises(RobotError):
        robot(nav).drive(BasePose(0.3, -0.2, 0.0))
    assert [(i["kind"], i["level"]) for i in rec.issues] == [(code, level)] and nav.calls == []


def test_a_stale_registration_is_a_map_reset_warning(rec):
    with pytest.raises(RobotError):
        robot(FakeNav(T, map_gen=2), gen=1).drive(BasePose(0.3, -0.2, 0.0))
    assert [(i["kind"], i["level"]) for i in rec.issues] == [("map_reset", "warning")]


def test_a_good_trip_files_nothing(rec):
    robot(FakeNav(T)).drive(BasePose(0.3, -0.2, 0.0))
    assert rec.issues == []


def _ws(**m) -> str:
    return json.dumps({"t": "state", "ready": True, "rx": 0, "ry": 0, "rh": 0, "status": "idle", "map_gen": 1, **m})


def test_status_transitions_are_breadcrumbs_and_a_new_map_is_a_warning(rec):
    nav = BBNav("127.0.0.1")
    resets = []
    nav.on_map_reset(resets.append)
    nav._on_ws(_ws(ready=False, status="starting"))
    nav._on_ws(_ws(ready=False, status="starting"))                 # nothing changed: no crumb
    nav._on_ws(_ws(ready=True, status="idle"))
    nav._on_ws(_ws(ready=True, status="navigating"))
    nav._on_ws(json.dumps({"t": "params", "speed": 1}))              # the UI's tuning message: not state
    assert [m for _, m, _ in rec.crumbs] == ["(start) -> starting; ready False", "starting -> idle; ready True",
                                             "idle -> navigating"]
    assert rec.issues == []
    nav._on_ws(_ws(ready=True, status="idle", map_gen=2))
    assert resets == ["map_gen 1 -> 2"]
    (i,) = rec.issues
    assert (i["kind"], i["level"], i["context"]["old_map_gen"], i["map_gen"]) == ("map_reset", "warning", 1, 2)
