"""roomctl/nav_publish.py: the robot on its map, in the ROOM frame, for the dashboard (web/API-FOR-PAGES.md, nav).

The geometry is checked against the definitions, not against the module: BB yaw 0 faces +y; the page's yaw is
radians counter-clockwise from +x in the room; a grid row is a y, row 0 is ymin. The last test is the whole path:
fake/bbsim.py -> roomctl.bb_nav -> the publisher -> a REAL web server on a scratch port -> GET /api/nav/snapshot.
"""
import base64
import json
import logging
import math
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from roomctl import frames  # noqa: E402
from roomctl.frames import SE2  # noqa: E402
from roomctl.nav_publish import NavPublisher, pose_event, room_map  # noqa: E402

TS = [SE2.identity(), SE2(math.radians(30), 1.2, -0.8), SE2(math.radians(-115), -0.4, 2.0), SE2(math.pi, 0.3, 0.3)]


def state(x=0.0, y=0.0, h=0.0, **kw):
    return SimpleNamespace(**{"x": x, "y": y, "h": h, "ready": True, "status": "idle", "map_gen": 3, "path": None, "goal": None, **kw})


@pytest.mark.parametrize("T", TS)
@pytest.mark.parametrize("h", [0.0, 1.0, -2.5, math.pi / 2])
def test_yaw_is_radians_ccw_from_room_x_and_points_where_the_robot_faces(T, h):
    ev = pose_event(state(0.7, -0.2, h), T)
    fx, fy = -math.sin(h), math.cos(h)                                  # Bracket Bot: forward at yaw h
    c, s = math.cos(-T.theta), math.sin(-T.theta)                       # BB -> room is a rotation by -theta
    want = (c * fx - s * fy, s * fx + c * fy)
    assert (math.cos(ev["pose"]["yaw"]), math.sin(ev["pose"]["yaw"])) == pytest.approx(want, abs=1e-3)
    assert abs(ev["pose"]["yaw"]) <= math.pi + 1e-3 and ev["frame"] == "world_z_up" and ev["map_gen"] == 3
    assert (ev["pose"]["x"], ev["pose"]["y"]) == pytest.approx(frames.bb_to_room((0.7, -0.2, 0.0), T)[:2], abs=1e-3)


def test_with_the_identity_registration_bb_yaw_zero_is_facing_room_plus_y():
    assert pose_event(state(h=0.0), SE2.identity())["pose"]["yaw"] == pytest.approx(math.pi / 2, abs=1e-4)


def test_the_path_arrives_as_xs_and_ys_and_leaves_as_room_frame_points():
    T = TS[1]
    ev = pose_event(state(path=[[1.0, 1.5, 2.0], [0.0, 0.5, 1.0]], goal=(2.0, 1.0), status="navigating"), T)
    want = [list(frames.bb_to_room((x, y, 0.0), T)[:2]) for x, y in ((1.0, 0.0), (1.5, 0.5), (2.0, 1.0))]
    assert np.allclose(ev["path"], want, atol=1e-3) and ev["goal"] == pytest.approx(want[-1], abs=1e-3)
    assert ev["status"] == "navigating"
    assert pose_event(state(path=[[0.0, 0.0], [1.0, 1.0]]), SE2.identity())["path"] == [[0.0, 1.0], [0.0, 1.0]]   # 2 points: still xs, ys
    assert pose_event(state(), T)["path"] == []


# ── the map ─────────────────────────────────────────────────────────────────────────────

def area_map(anchor, ages=None):
    """A 2 x 2 m rectangle, all floor, with ONE obstacle block at area (X 0.5..0.8, Y 1.0..1.3): ahead and to the right."""
    nx = ny = 67
    g = np.ones((ny, nx), np.uint8)
    g[int((1.0 + 0.5) / 0.03):int((1.3 + 0.5) / 0.03), int((0.5 + 1.0) / 0.03):int((0.8 + 1.0) / 0.03)] = 2   # rows = Y, cols = X
    ages = np.full((4, 4), -1.0) if ages is None else np.asarray(ages, float)
    return {"area": {"anchor_world": dict(zip(("x", "y", "yaw"), anchor)), "bounds": {"xmin": -1.0, "xmax": 1.0, "ymin": -0.5, "ymax": 1.5}},
            "grid": {"resolution_m": 0.03, "nx": nx, "ny": ny, "cells": base64.b64encode(g.tobytes()).decode()},
            "freshness": {"block_m": 0.5, "nx": 4, "ny": 4, "age_s": ages.tolist()}, "t": 0.0}


def cell_at(m, x, y):
    g, b = m["grid"], m["grid"]["bounds"]
    cells = np.frombuffer(base64.b64decode(g["cells_b64"]), np.uint8).reshape(g["ny"], g["nx"])   # the page's decode
    return int(cells[int((y - b["ymin"]) / g["res"]), int((x - b["xmin"]) / g["res"])])


def to_room(X, Y, anchor, T):
    return frames.bb_to_room(frames.robot_rel_to_bb(X, Y, *anchor) + (0.0,), T)[:2]


@pytest.mark.parametrize("T", TS)
@pytest.mark.parametrize("anchor", [(0.0, 0.0, 0.0), (1.4, -0.2, 1.05)])
def test_the_grid_is_resampled_into_the_room_frame(T, anchor):
    m = room_map(area_map(anchor), T)
    g, b = m["grid"], m["grid"]["bounds"]
    assert len(base64.b64decode(g["cells_b64"])) == g["nx"] * g["ny"] and g["res"] == 0.03
    assert b["xmax"] - b["xmin"] == pytest.approx(g["nx"] * g["res"]) and b["ymax"] - b["ymin"] == pytest.approx(g["ny"] * g["res"])
    assert (b["xmin"] / 0.5) == pytest.approx(round(b["xmin"] / 0.5)) and (b["ymin"] / 0.5) == pytest.approx(round(b["ymin"] / 0.5))
    assert cell_at(m, *to_room(0.65, 1.15, anchor, T)) == 2              # the obstacle is where it is, in the ROOM
    assert cell_at(m, *to_room(-0.5, 0.2, anchor, T)) == 1               # floor
    assert cell_at(m, *to_room(0.2, 1.15, anchor, T)) == 1               # beside the obstacle: not smeared
    corners = [to_room(X, Y, anchor, T) for X in (-1.0, 1.0) for Y in (-0.5, 1.5)]
    assert b["xmin"] <= min(c[0] for c in corners) and b["xmax"] >= max(c[0] for c in corners) - 0.03
    cells = np.frombuffer(base64.b64decode(g["cells_b64"]), np.uint8)
    if abs(math.degrees(T.theta + anchor[2])) % 90 > 1:                  # a rotated rectangle leaves corners nobody mapped
        assert (cells == 0).any()
    assert 0.75 < (cells > 0).sum() * g["res"] ** 2 / 4.0 < 1.05         # the mapped AREA survives the rotation: 2 x 2 m


def test_freshness_is_resampled_onto_blocks_that_start_at_the_grids_own_corner():
    T, anchor = TS[1], (1.4, -0.2, 1.05)
    ages = np.full((4, 4), -1.0)
    ages[2, 3] = 4.0                                                     # area block col 3, row 2: X 0.5..1.0, Y 0.5..1.0
    m = room_map(area_map(anchor, ages), T)
    f, b = m["freshness"], m["grid"]["bounds"]
    a = np.array(f["ages"])
    assert f["block_m"] == 0.5
    assert a.shape == (math.ceil((b["ymax"] - b["ymin"]) / 0.5 - 1e-9), math.ceil((b["xmax"] - b["xmin"]) / 0.5 - 1e-9))   # blocks tile the grid
    x, y = to_room(0.75, 0.75, anchor, T)                                # the middle of that block, in the room
    assert a[int((y - b["ymin"]) / 0.5), int((x - b["xmin"]) / 0.5)] == 4.0   # exactly how landing/livemap.js indexes it
    assert set(np.unique(a)) == {-1.0, 4.0}


def test_an_areamap_object_and_the_raw_json_give_the_same_map():
    bb_nav = pytest.importorskip("roomctl.bb_nav")
    doc = area_map((1.4, -0.2, 1.05))
    a, b = room_map(doc, TS[1]), room_map(bb_nav.AreaMap.from_doc(doc), TS[1])
    assert a == b and room_map(None, TS[1]) is None


# ── the publisher ───────────────────────────────────────────────────────────────────────

class Fixture:
    def __init__(self):
        self.nav = SimpleNamespace(state=state(), area=area_map((0.0, 0.0, 0.0)))
        self.reg, self.sent, self.down, self.now = (TS[1], 3), [], False, 100.0

    def publish(self, name, data):
        if self.down:
            raise urllib.error.URLError("connection refused")
        self.sent.append((name, data))

    def make(self, **kw):
        return NavPublisher(self.nav, lambda: self.reg, self.publish, clock=lambda: self.now, **kw)


def test_the_map_goes_once_and_the_pose_every_time():
    fx = Fixture()
    p = fx.make(fresh_every_s=0)
    for _ in range(5):
        fx.now += 0.5
        assert p.once()
    assert [n for n, _ in fx.sent] == ["nav"] * 5
    assert ["grid" in d for _, d in fx.sent] == [True, False, False, False, False]
    assert all({"pose", "status", "path", "map_gen", "frame", "at"} <= set(d) for _, d in fx.sent)
    assert "freshness" in fx.sent[0][1] and "_sig" not in fx.sent[0][1] and p.maps_sent == 1
    json.dumps(fx.sent[0][1])                                            # it is what goes on the wire


def test_the_map_goes_again_when_the_map_gen_the_cells_the_rectangle_or_the_registration_change():
    fx = Fixture()
    p = fx.make(fresh_every_s=0)
    p.once()
    fx.nav.state.map_gen, fx.reg = 4, (TS[1], 4); p.once()               # a reset (and a registration for the new map)
    doc = area_map((0.0, 0.0, 0.0))
    g = np.frombuffer(base64.b64decode(doc["grid"]["cells"]), np.uint8).copy(); g[:200] = 0
    doc["grid"]["cells"] = base64.b64encode(g.tobytes()).decode()
    fx.nav.area = doc; p.once()                                          # different cells: floor newly (un)mapped
    fx.nav.area = area_map((0.5, 0.5, 0.3)); p.once()                    # a new rectangle
    fx.reg = (TS[2], 4); p.once()                                        # a better registration moves everything
    p.once()
    assert ["grid" in d for _, d in fx.sent] == [True, True, True, True, True, False]


def test_ages_alone_are_not_a_change_but_ride_along_every_fresh_every_s():
    fx = Fixture()
    p = fx.make(fresh_every_s=10.0)
    for i in range(30):                                                  # 15 s at 2 Hz, the ages ticking all the while
        fx.now += 0.5
        fx.nav.area = area_map((0.0, 0.0, 0.0), np.full((4, 4), float(i)))
        p.once()
    assert [i for i, (_, d) in enumerate(fx.sent) if "grid" in d] == [0, 20]


def test_nothing_is_sent_without_a_pose_or_with_a_registration_for_another_map():
    fx = Fixture()
    p = fx.make()
    fx.reg = None; assert p.once() is False
    fx.reg = (TS[1], 2); assert p.once() is False                        # the registration is for map 2, the robot is on 3
    fx.reg, fx.nav.state = (TS[1], 3), None; assert p.once() is False
    assert fx.sent == []
    fx.nav.state, fx.nav.area = state(), None
    assert p.once() and "grid" not in fx.sent[0][1]                      # no rectangle yet: the pose alone is still true


def test_an_unreachable_dashboard_is_said_once_and_the_map_is_sent_again_when_it_is_back(caplog):
    fx = Fixture()
    p = fx.make(fresh_every_s=0)
    fx.down = True
    with caplog.at_level(logging.WARNING, logger="roomctl.nav_publish"):
        for _ in range(6):
            assert p.once() is False                                     # and nothing raises into the caller
        assert [r.message for r in caplog.records if "not reachable" in r.message] != [] and len(caplog.records) == 1
        assert p.maps_sent == 0                                          # a map that did not arrive was not "sent"
        fx.down = False
        assert p.once() and "grid" in fx.sent[0][1]
        assert p.once() and "grid" in fx.sent[1][1]                      # once more: the dashboard may have restarted empty
        assert p.once() and "grid" not in fx.sent[2][1]
    assert [("reachable again" in r.message) for r in caplog.records] == [False, True] and p.failures == 6


def test_the_thread_never_blocks_its_owner_and_stops_when_told():
    fx = Fixture()

    def slow(name, data):
        time.sleep(0.3); fx.sent.append((name, data))
    p = NavPublisher(fx.nav, lambda: fx.reg, slow, hz=20)
    t0 = time.monotonic(); p.start()
    assert time.monotonic() - t0 < 0.1                                   # start() returns at once
    time.sleep(0.8); p.stop()
    n = len(fx.sent)
    assert 1 <= n <= 4                                                   # a slow web slows the publisher, nobody else
    time.sleep(0.5)
    assert len(fx.sent) == n


# ── the whole path: simulator -> client -> publisher -> a real web server ───────────────

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as f:
            return f.status, json.loads(f.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_end_to_end_the_dashboard_shows_the_simulated_robot_in_the_room_frame(tmp_path, monkeypatch):
    pytest.importorskip("fastapi"); pytest.importorskip("uvicorn"); pytest.importorskip("websockets")
    from test_bbsim import Sim
    from fake.scene_gen import FakeRoom
    from roomctl import watch_cli
    from roomctl.bb_nav import BBNav
    for k, v in (("ROOM_ES", "off"), ("ROOM_EVENTS", "off"), ("ROOM_SENTRY", "off")):
        monkeypatch.setenv(k, v)
    FakeRoom(tmp_path / "room.git", quiet=True).commit("clean_bench")
    port = _free_port()
    env = {**os.environ, "WEB_BIND": f"127.0.0.1:{port}", "ROOM_GIT_PATH": str(tmp_path / "room.git"), "ROOM_STATE_FILE": str(tmp_path / "rs.json"),
           "ROOM_CLEAN_STATE": str(tmp_path / "rc.json"), "GITIRL_CLOUD_TOKEN": "", "ELASTIC_URL": "", "ELASTIC_API_KEY": "", "SENTRY_DSN": ""}
    web = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT / "web", env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sim = nav = pub = None
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                status, body = _get(base + "/api/nav/snapshot")
                break
            except OSError:
                assert web.poll() is None, web.stdout.read()[-2000:]
                time.sleep(0.2)
        else:
            pytest.fail("the web server did not come up")
        assert status == 503                                             # nothing has published yet: it says so
        sim = Sim()
        sim.wait_ready(); sim.look_at_desk()
        sim.post("/map/rectangle", {"xmin": -1.0, "xmax": 1.0, "ymin": -0.5, "ymax": 1.5, "sweep": False}); sim.wait_job()
        nav = BBNav("127.0.0.1", sim.ws_port, sim.api_port).start()
        sim.until(lambda: nav.state is not None and nav.state.ready and nav.area is not None, 30, "the client to sync")
        monkeypatch.setenv("ROOM_WEB_URL", base)
        pub = NavPublisher(nav, watch_cli.reg_provider(nav), watch_cli.web_publisher(), hz=5).start()
        sim.until(lambda: _get(base + "/api/nav/snapshot")[0] == 200, 20, "the first nav event to arrive")
        snap = _get(base + "/api/nav/snapshot")[1]
        me = sim.get("/sim/truth")["robot_room"]                         # home: (-0.4, 0), facing +X = yaw 0
        assert (snap["pose"]["x"], snap["pose"]["y"]) == pytest.approx((me["x"], me["y"]), abs=0.01)
        assert math.degrees(snap["pose"]["yaw"]) == pytest.approx(me["yaw_deg"], abs=0.5) and abs(snap["pose"]["yaw"]) < 0.02
        assert snap["frame"] == "world_z_up" and snap["map_gen"] == 0 and snap["stale"] is False and snap["status"] == "idle"
        g = snap["grid"]
        assert len(base64.b64decode(g["cells_b64"])) == g["nx"] * g["ny"]
        assert cell_at(snap, 0.5, 0.0) == 2 and cell_at(snap, -0.15, 0.0) == 1   # the desk, and the floor in front of it: ROOM coords
        assert cell_at(snap, -0.9, -0.9) == 0                            # behind the robot: never seen, and it says so
        a, b = np.array(snap["freshness"]["ages"]), g["bounds"]
        assert 0 <= a[int((0.0 - b["ymin"]) / 0.5), int((0.5 - b["xmin"]) / 0.5)] < 5   # the desk block is being looked at
        tx, ty, _ = frames.room_to_bb((-0.9, -0.5, 0.0), SE2(**{k: v for k, v in sim.get("/sim/truth")["T_bb_from_room"].items()}))
        sim.post("/navigate", {"x": tx, "y": ty})
        driving = sim.until(lambda: (lambda s: s if s.get("status") == "navigating" and len(s.get("path") or []) >= 2 else None)(
            _get(base + "/api/nav/snapshot")[1]), 20, "a navigating snapshot")
        assert driving["path"][-1] == pytest.approx([-0.9, -0.5], abs=0.26)      # the path ends at the goal, in the room frame
        assert "grid" in driving                                         # a pose-only event keeps the map it belongs to
        sim.wait_job()
        sim.until(lambda: math.hypot(_get(base + "/api/nav/snapshot")[1]["pose"]["x"] + 0.9,
                                     _get(base + "/api/nav/snapshot")[1]["pose"]["y"] + 0.5) <= 0.26, 20, "the pose to follow the robot")
        assert pub.maps_sent < pub.sent / 3                              # the pose is the traffic; the map is the exception
    finally:
        for closer in (lambda: pub and pub.stop(), lambda: nav and nav.close(), lambda: sim and sim.close()):
            closer()
        web.terminate()
        try:
            web.wait(10)
        except subprocess.TimeoutExpired:
            web.kill()
