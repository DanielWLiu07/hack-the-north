"""roomctl/bb_nav.py against bbapps/nav's documented wire format, and BBNavRobot's rules.

The encoder below is written from the robot's docs, not from bb_nav: 8-byte header <u32 type,
u32 count> + zlib(payload); type 4 = <iiifI bx,by,bz,res,first> + u16x3 offsets + u8x3 rgb;
type 5 = <iiifII bx,by,bz,res,nu,nr> + adds + removes + rgb for the adds.
"""
import base64
import json
import math
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roomctl import bb_nav, frames  # noqa: E402
from roomctl.bb_nav import AreaMap, BBNavRobot, Job, NavState, VoxelMirror, job_error, status_error  # noqa: E402
from roomctl.executor import BasePose, RobotError  # noqa: E402

RES = 0.015


def full_copy(cells, base=(0, 0, 0), first=1):
    offs = [(x - base[0], y - base[1], z - base[2]) for (x, y, z), _ in cells]
    payload = struct.pack("<iiifI", *base, RES, first)
    payload += b"".join(struct.pack("<HHH", *o) for o in offs)
    payload += b"".join(struct.pack("<BBB", *c) for _, c in cells)
    return struct.pack("<II", 4, len(cells)) + zlib.compress(payload)


def changes(adds, removes, base=(0, 0, 0)):
    payload = struct.pack("<iiifII", *base, RES, len(adds), len(removes))
    payload += b"".join(struct.pack("<HHH", *(a - b for a, b in zip(k, base))) for k, _ in adds)
    payload += b"".join(struct.pack("<HHH", *(a - b for a, b in zip(k, base))) for k in removes)
    payload += b"".join(struct.pack("<BBB", *c) for _, c in adds)
    return struct.pack("<II", 5, len(adds)) + zlib.compress(payload)


# ── /heavy ──────────────────────────────────────────────────────────────────────────────

def test_full_copy_then_changes_rebuild_the_map_exactly():
    m = VoxelMirror()
    cells = [((10, 20, 30), (255, 0, 0)), ((11, 20, 30), (0, 255, 0)), ((12, 21, 31), (0, 0, 255))]
    assert m.apply_message(full_copy(cells, base=(10, 20, 30))) == 4
    assert len(m) == 3 and m.res == pytest.approx(RES) and m.cells[(11, 20, 30)] == (0, 255, 0)
    m.apply_message(changes([((13, 22, 33), (9, 9, 9)), ((10, 20, 30), (1, 2, 3))], [(11, 20, 30)], base=(10, 20, 30)))
    assert m.cells == {(10, 20, 30): (1, 2, 3), (12, 21, 31): (0, 0, 255), (13, 22, 33): (9, 9, 9)}
    xyz, rgb = m.points()
    assert xyz.shape == (3, 3) and np.allclose(sorted(xyz[:, 0]), [10 * RES, 12 * RES, 13 * RES], atol=1e-6)


def test_first_means_clear_and_rebuild_and_a_later_chunk_appends():
    m = VoxelMirror()
    m.apply_message(full_copy([((1, 1, 1), (5, 5, 5))]))
    m.apply_message(full_copy([((2, 2, 2), (6, 6, 6))], first=1))               # a fresh full copy
    m.apply_message(full_copy([((3, 3, 3), (7, 7, 7))], first=0))               # its next chunk
    assert set(m.cells) == {(2, 2, 2), (3, 3, 3)} and m.resets == 2


def test_an_empty_full_copy_is_a_map_reset():
    seen = []
    m = VoxelMirror(on_reset=lambda: seen.append(1))
    m.apply_message(full_copy([((1, 1, 1), (5, 5, 5))]))
    m.apply_message(full_copy([], first=1))
    assert len(m) == 0 and seen == [1]


def test_ui_overlay_types_are_ignored():
    m = VoxelMirror()
    for t in (2, 3, 6, 7):
        assert m.apply_message(struct.pack("<II", t, 0) + zlib.compress(b"\x00" * 32)) == t
    assert len(m) == 0 and m.version == 0


def test_points_in_the_room_frame_go_through_frames():
    m = VoxelMirror()
    m.apply_message(full_copy([((100, 0, 50), (1, 1, 1))]))
    T = frames.SE2(math.pi / 2, 0.0, 0.0, 0.10)
    xyz, _ = m.points_room(T)
    assert np.allclose(xyz[0], frames.bb_to_room((100 * RES, 0.0, 50 * RES), T), atol=1e-6)


# ── /ws, /map, jobs ─────────────────────────────────────────────────────────────────────

def test_state_message_and_the_params_message_is_not_state():
    nav = bb_nav.BBNav("127.0.0.1")
    resets = []
    nav.on_map_reset(resets.append)
    nav._on_ws(json.dumps({"t": "params", "x": 1}))
    assert nav.state is None
    msg = {"t": "state", "ready": True, "rx": 1.5, "ry": -0.2, "rh": 0.3, "status": "navigating",
           "running": True, "waypoints": [[1, 2, None]], "wp": 0, "gx": 2.0, "gy": 1.5, "map_gen": 3}
    nav._on_ws(json.dumps(msg))
    s = nav.state
    assert (s.ready, s.x, s.y, s.h, s.goal, s.map_gen) == (True, 1.5, -0.2, 0.3, (2.0, 1.5), 3)
    nav._on_ws(json.dumps({**msg, "map_gen": 4}))
    assert resets == ["map_gen 3 -> 4"]


def test_area_map_decodes_the_documented_payload():
    grid = np.zeros((4, 5), np.uint8)
    grid[0, 0], grid[3, 4] = 1, 2
    doc = {"area": {"anchor_world": {"x": 1.451, "y": -0.176, "yaw": 1.055},
                    "bounds": {"xmin": -1.5, "xmax": 1.5, "ymin": -1.0, "ymax": 2.0}},
           "grid": {"resolution_m": 0.03, "nx": 5, "ny": 4, "cells": base64.b64encode(grid.tobytes()).decode()},
           "freshness": {"block_m": 0.5, "nx": 2, "ny": 2, "age_s": [[12.4, -1.0], [0.5, 3.0]]}, "t": 1789790000.0}
    a = AreaMap.from_doc(doc)
    assert a.grid.shape == (4, 5) and a.grid[0, 0] == 1 and a.grid[3, 4] == 2
    assert a.freshness[0, 1] == -1.0 and a.anchor_world["yaw"] == 1.055 and a.resolution_m == 0.03


@pytest.mark.parametrize("job,code", [
    (Job("navigate", False, None, "reached"), None),
    (Job("navigate", False, "cancelled"), "nav_cancelled"),
    (Job("navigate", False, "TimeoutError: 120 s"), "nav_timeout"),
    (Job("navigate", False, "NavError: no path"), "nav_failed"),
    (Job("navigate", False, None, "failed: blocked"), "nav_failed"),
    (Job("patrol", True), None),
])
def test_job_outcomes_map_to_our_codes(job, code):
    e = job_error(job)
    assert (e.code if e else None) == code


@pytest.mark.parametrize("state,code", [
    (None, "robot_unreachable"),
    (NavState(ready=False), "slam_not_ready"),
    (NavState(ready=True, status="manual"), "manual_override"),
    (NavState(ready=True, status="waiting_for_drive"), "drive_busy"),
    (NavState(ready=True, status="idle"), None),
])
def test_the_robot_refuses_before_a_job_starts(state, code):
    e = status_error(state)
    assert (e.code if e else None) == code


# ── BBNavRobot: the executor's Robot over the nav stack ─────────────────────────────────

class FakeNav:
    """Records calls; 'drives' instantly; the arrival error is configurable."""

    def __init__(self, T, error=None, off=(0.0, 0.0), patrolling=False, map_gen=1):
        self.T, self.error, self.off = T, error, off
        self.state = NavState(ready=True, status="idle", map_gen=map_gen)
        self.calls, self._job = [], Job("patrol", True) if patrolling else None
        self._target = None

    def job(self):
        return self._job

    def navigate(self, x, y, heading=None, frame="world", timeout=120):
        self.calls.append(("navigate", round(x, 4), round(y, 4), heading, frame))
        self._target = (x, y, heading)
        self._job = Job("navigate", True)

    def wait(self, timeout, sleep=None):
        self._job = Job("navigate", False, self.error, None if self.error else "reached")
        return self._job

    def pose(self):
        x, y, h = self._target
        return {"world": {"x": x + self.off[0], "y": y + self.off[1], "yaw": h}}

    def patrol(self, goal_timeout=90):
        self.calls.append(("patrol",))
        self._job = Job("patrol", True)


T = frames.SE2(0.6, 1.2, -0.4, 0.0)


def robot(nav, gen=1, **kw):
    return BBNavRobot(nav, lambda: (T, gen), **kw)


def test_drive_sends_the_bb_frame_target_and_checks_the_actual_pose():
    nav = FakeNav(T)
    robot(nav).drive(BasePose(0.30, -0.20, 90.0))
    (_, x, y, h, frame), = nav.calls
    assert frame == "world"
    assert (x, y) == pytest.approx(frames.room_to_bb((0.30, -0.20, 0.0), T)[:2], abs=1e-4)
    assert h == pytest.approx(frames.heading_room_to_bb_yaw(90.0, T), abs=1e-5)


def test_arriving_too_far_away_is_nav_short_not_success():
    with pytest.raises(RobotError) as e:
        robot(FakeNav(T, off=(0.30, 0.0))).drive(BasePose(0.3, -0.2, 0.0))
    assert e.value.code == "nav_short"


def test_a_failed_job_raises_its_code_and_patrol_is_resumed_anyway():
    nav = FakeNav(T, error="NavError: no path", patrolling=True)
    with pytest.raises(RobotError) as e:
        robot(nav).drive(BasePose(0.3, -0.2, 0.0))
    assert e.value.code == "nav_failed" and nav.calls[-1] == ("patrol",)


def test_patrol_is_resumed_after_a_trip_only_if_it_was_running():
    nav = FakeNav(T, patrolling=True)
    robot(nav).drive(BasePose(0.3, -0.2, 0.0))
    assert nav.calls[-1] == ("patrol",)
    nav2 = FakeNav(T)
    robot(nav2).drive(BasePose(0.3, -0.2, 0.0))
    assert ("patrol",) not in nav2.calls


def test_a_stale_registration_refuses_to_move():
    with pytest.raises(RobotError) as e:
        robot(FakeNav(T, map_gen=2), gen=1).drive(BasePose(0.3, -0.2, 0.0))
    assert e.value.code == "map_reset"


def test_tier_b_has_no_arm():
    with pytest.raises(RobotError) as e:
        robot(FakeNav(T)).pick("mug_a1b2", None)
    assert e.value.code == "arm_unavailable"


def test_pose_comes_back_in_the_room_frame():
    nav = FakeNav(T)
    r = robot(nav)
    r.drive(BasePose(0.5, 0.25, 45.0))
    p = r.pose()
    assert (p.x, p.y) == pytest.approx((0.5, 0.25), abs=1e-3) and p.yaw == pytest.approx(45.0, abs=0.01)
