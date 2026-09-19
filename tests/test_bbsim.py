"""fake/bbsim.py: the fake Bracket Bot nav server, checked from the OUTSIDE (plan/roommate/06).

Everything here talks to a bbsim subprocess over loopback, the way a client does. Two receivers decode
the voxel stream: the one written out in Bracket Bot's documentation (transcribed below as DocVoxelMap,
and, when the local reference copy exists, the documentation's receiver run UNMODIFIED as a script) and
our own roomctl.bb_nav. bbsim is only useful if both agree with its ground truth.

The scene is served in a BB world frame that is NOT the room frame (30 deg, 1.2 m, -0.8 m), so every
check that compares against the scene goes through roomctl.frames with the sim's T. A client that
skips registration fails these tests by ~1 m.
"""
import base64
import json
import math
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

websockets = pytest.importorskip("websockets")
from websockets.sync.client import connect  # noqa: E402

from fake import bbsim  # noqa: E402
from roomctl import frames  # noqa: E402
from roomctl.frames import SE2  # noqa: E402

DOC = Path.home() / ".cache/gitspace/ref/bbapps-nav-server-integration.md"   # local reference, never in the repo


class DocVoxelMap:
    """The receiver in Bracket Bot's documentation, line for line (apply only)."""
    def __init__(self):
        self.cells, self.res, self.clears = {}, None, 0

    def apply(self, ptype, n, raw):
        if ptype == 4:
            bx, by, bz, res, first = struct.unpack_from("<iiifI", raw, 0)
            self.res = res
            if first:
                self.cells.clear(); self.clears += 1
            d = np.frombuffer(raw, np.uint16, n * 3, 20).reshape(n, 3).astype(np.int64) + (bx, by, bz)
            c = np.frombuffer(raw, np.uint8, n * 3, 20 + n * 6).reshape(n, 3)
            self.cells.update(zip(map(tuple, d.tolist()), map(tuple, c.tolist())))
        elif ptype == 5:
            bx, by, bz, res, nu, nr = struct.unpack_from("<iiifII", raw, 0)
            self.res = res
            p = 24
            up = np.frombuffer(raw, np.uint16, nu * 3, p).reshape(nu, 3).astype(np.int64) + (bx, by, bz); p += nu * 6
            rm = np.frombuffer(raw, np.uint16, nr * 3, p).reshape(nr, 3).astype(np.int64) + (bx, by, bz); p += nr * 6
            col = np.frombuffer(raw, np.uint8, nu * 3, p).reshape(nu, 3)
            self.cells.update(zip(map(tuple, up.tolist()), map(tuple, col.tolist())))
            for key in map(tuple, rm.tolist()):
                self.cells.pop(key, None)

    def feed(self, msg):
        ptype, n = struct.unpack_from("<II", msg, 0)
        self.apply(ptype, n, zlib.decompress(msg[8:]))
        return ptype, n


# ── the wire format, no sockets ─────────────────────────────────────────────────────────

def test_full_copy_chunks_are_at_most_40000_cells_and_only_the_first_clears():
    rng = np.random.default_rng(0)
    cells = np.unique(rng.integers(-300, 300, (95_000, 3), dtype=np.int32), axis=0)
    cols = rng.integers(0, 256, (len(cells), 3), dtype=np.uint8)
    pkts = bbsim.pack_keyframe(cells, cols)
    assert len(pkts) == math.ceil(len(cells) / 40_000)
    m = DocVoxelMap()
    m.cells[(9, 9, 9)] = (1, 2, 3)                       # stale: a full copy must clear it
    firsts = []
    for p in pkts:
        t, n = struct.unpack_from("<II", p, 0)
        assert t == 4 and n <= 40_000
        firsts.append(struct.unpack_from("<iiifI", zlib.decompress(p[8:]), 0)[4])
        m.feed(p)
    assert firsts == [1] + [0] * (len(pkts) - 1)
    assert m.res == pytest.approx(0.015)
    assert m.cells == {tuple(c): tuple(k) for c, k in zip(cells.tolist(), cols.tolist())}


def test_an_empty_map_is_one_clearing_chunk_with_count_zero():
    (p,) = bbsim.pack_keyframe(np.zeros((0, 3), np.int32), np.zeros((0, 3), np.uint8))
    assert struct.unpack_from("<II", p, 0) == (4, 0)
    assert struct.unpack_from("<iiifI", zlib.decompress(p[8:]), 0)[4] == 1
    m = DocVoxelMap(); m.cells[(1, 1, 1)] = (0, 0, 0); m.feed(p)
    assert m.cells == {}


def test_changes_add_recolour_and_remove_with_negative_cells():
    m = DocVoxelMap()
    base = np.array([[-5, -7, 0], [-4, -7, 0], [3, 2, 9]], np.int32)
    for p in bbsim.pack_keyframe(base, np.full((3, 3), 10, np.uint8)):
        m.feed(p)
    p = bbsim.pack_delta(np.array([[-4, -7, 0], [6, 6, 6]], np.int32), np.array([[1, 2, 3], [4, 5, 6]], np.uint8),
                         np.array([[3, 2, 9]], np.int32))
    assert struct.unpack_from("<II", p, 0) == (5, 3)     # count = adds + removes
    m.feed(p)
    assert m.cells == {(-5, -7, 0): (10, 10, 10), (-4, -7, 0): (1, 2, 3), (6, 6, 6): (4, 5, 6)}
    assert bbsim.pack_delta(base[:0], np.zeros((0, 3), np.uint8), base[:0]) is None


def test_our_client_decodes_the_same_bytes_as_the_documented_receiver():
    bb_nav = pytest.importorskip("roomctl.bb_nav")
    cells = np.array([[0, 0, 0], [-2, 5, 1], [7, -3, 4]], np.int32)
    cols = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], np.uint8)
    doc, ours = DocVoxelMap(), bb_nav.VoxelMirror()
    for p in bbsim.pack_keyframe(cells, cols) + [bbsim.pack_delta(cells[:1] + 1, cols[:1], cells[1:2])] + bbsim.empty_overlays():
        doc.feed(p); ours.apply_message(p)
    assert dict(ours.cells) == doc.cells


# ── a live simulator ────────────────────────────────────────────────────────────────────

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Sim:
    def __init__(self, *extra, ws_port=None, api_port=None):
        self.ws_port, self.api_port = ws_port or _free_port(), api_port or _free_port()
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "fake/bbsim.py"), "--scene", "clean_bench", "--ws-port", str(self.ws_port),
             "--api-port", str(self.api_port), "--speed", "1.5", "--fast", "--vox-interval", "0.05", *extra],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.first_seen = None
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                self.first_seen = self.req("GET", "/pose")
                break
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError(self.proc.stdout.read())
                time.sleep(0.05)
        else:
            raise RuntimeError("bbsim did not start")

    def req(self, method, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.api_port}{path}", method=method,
                                   data=None if body is None else json.dumps(body).encode(),
                                   headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=20) as f:
                return f.status, json.loads(f.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get(self, path):
        return self.req("GET", path)[1]

    def post(self, path, body=None, expect=(200, 202)):
        status, out = self.req("POST", path, body or {})
        assert status in expect, (path, status, out)
        return out

    @property
    def T(self):
        t = self.get("/sim/truth")["T_bb_from_room"]
        return SE2(t["theta"], t["tx"], t["ty"], t["dz"])

    def wait_ready(self):
        self.until(lambda: self.req("GET", "/pose")[0] == 200, 30, "SLAM ready")

    def wait_job(self, timeout=90):
        self.until(lambda: not (self.get("/health")["job"] or {"running": False})["running"], timeout, "job to end")
        return self.get("/health")["job"]

    def until(self, fn, timeout, what):
        deadline = time.time() + timeout
        while time.time() < deadline:
            v = fn()
            if v:
                return v
            time.sleep(0.03)
        raise AssertionError(f"timed out waiting for {what}")

    def restore(self):
        self.post("/stop")
        for k in ("nav", "drive_busy", "manual"):
            self.post("/sim/fail", {"kind": k, "clear": True})
        t = self.get("/sim/truth")
        for oid, o in t["objects"].items():
            if o["kind"] == "occluder":
                self.post("/sim/occlude", {"object_id": oid.split(":", 1)[1], "clear": True})
        self.post("/sim/scene", {"name": "clean_bench"})
        self.wait_ready()
        self.look_at_desk()
        self.until(lambda: self.get("/sim/truth")["stale_visible"] == 0, 30, "the map to catch up")

    def look_at_desk(self):
        self.post("/sim/teleport", {"x": -0.4, "y": 0.0, "yaw": 0})      # home: behind the tag, facing the desk

    def heavy(self, **kw):
        return connect(f"ws://127.0.0.1:{self.ws_port}/heavy", max_size=None, open_timeout=20, **kw)

    def mirror(self, settle=0.6, m=None, ws=None):
        """Read /heavy until it has been quiet for `settle` seconds."""
        m = m or DocVoxelMap()
        if ws is None:
            with self.heavy() as own:
                return self.mirror(settle, m, own)
        while True:
            try:
                m.feed(ws.recv(timeout=settle))
            except TimeoutError:
                return m

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture(scope="module")
def sim():
    s = Sim()
    yield s
    s.close()


@pytest.fixture
def fresh(sim):
    sim.restore()
    return sim


def truth_cells(sim):
    """The scene's cells, computed HERE from the scene file and the sim's T: an independent expectation."""
    args = bbsim.parser().parse_args(["--scene", sim.get("/sim/truth")["scene"]])
    args.T = sim.T
    local = bbsim.Sim(args)
    return {tuple(c): tuple(k) for c, k in zip(local.t_cells.tolist(), local.t_cols.tolist())}


def test_pose_is_503_until_slam_is_ready_and_health_says_connected(sim):
    status, body = sim.first_seen                        # the very first request after the port opened
    assert status == 503 and "detail" in body
    sim.wait_ready()
    h = sim.get("/health")
    assert h["main_py"]["connected"] is True and h["job"] is None and h["area"] is None
    p = sim.get("/pose")
    assert set(p["world"]) == {"x", "y", "yaw"} and p["area"] is None


def test_the_documented_receiver_rebuilds_the_whole_scene(fresh):
    m = fresh.mirror()
    assert m.clears == 1 and m.res == pytest.approx(0.015)
    assert m.cells == truth_cells(fresh)
    assert len(m.cells) > 50_000                         # one room; the robot's own figure is ~57k


def test_the_map_is_in_bb_world_not_the_room_frame(fresh):
    """Registration is really exercised: the mug is where T says, and ~1 m from where the room says."""
    m, T = fresh.mirror(), fresh.T
    blue = np.array([c for c, rgb in m.cells.items() if rgb == (0x2b, 0x4c, 0x7e)], float) * m.res
    mug = fresh.get("/sim/truth")["objects"]["mug_a1b2"]
    centroid_room = frames.bb_to_room_array(blue, T).mean(axis=0)
    assert np.allclose(centroid_room[:2], (mug["x"], mug["y"]), atol=0.01)
    assert centroid_room[2] == pytest.approx(mug["z"], abs=0.01)
    assert np.hypot(*(blue.mean(axis=0)[:2] - (mug["x"], mug["y"]))) > 0.5


def test_an_oriented_box_keeps_its_yaw_in_the_voxels(fresh):
    fresh.post("/sim/move", {"object_id": "book_e5f6", "yaw": 40})
    fresh.until(lambda: fresh.get("/sim/truth")["stale_visible"] == 0, 30, "the book to be re-seen")
    book = fresh.get("/sim/truth")["objects"]["book_e5f6"]
    m = fresh.mirror()
    P = np.array([c for c, rgb in m.cells.items() if "#%02x%02x%02x" % rgb == book["color"]], float) * m.res
    R = frames.bb_to_room_array(P, fresh.T)[:, :2]
    w, v = np.linalg.eigh(np.cov((R - R.mean(axis=0)).T))
    axis = math.degrees(math.atan2(v[1, 1], v[0, 1])) % 180
    assert abs((axis - 40 + 90) % 180 - 90) < 5


def test_state_stream_is_params_then_state_at_about_8hz(fresh):
    with connect(f"ws://127.0.0.1:{fresh.ws_port}/ws", open_timeout=20) as ws:
        first = json.loads(ws.recv(timeout=10))
        assert first["t"] == "params"
        t0, msgs = time.time(), []
        while time.time() - t0 < 1.5:
            msgs.append(json.loads(ws.recv(timeout=10)))
    assert all(m["t"] == "state" for m in msgs)
    assert 8 <= len(msgs) <= 16                          # ~8 Hz over 1.5 s
    s = msgs[-1]
    assert {"ready", "rx", "ry", "rh", "status", "running", "waypoints", "wp", "map_gen"} <= set(s)
    assert s["status"] == "idle" and s["wp"] == -1 and "gx" not in s and "path" not in s


def test_navigate_returns_202_at_once_and_arrives_within_a_quarter_metre(fresh):
    T = fresh.T
    tx, ty, _ = frames.room_to_bb((-0.9, -0.6, 0.0), T)
    heading = frames.heading_room_to_bb_yaw(90.0, T)
    status, out = fresh.req("POST", "/navigate", {"x": tx, "y": ty, "heading": heading, "frame": "world"})
    assert status == 202 and out["job"]["kind"] == "navigate" and out["job"]["running"] is True
    with connect(f"ws://127.0.0.1:{fresh.ws_port}/ws", open_timeout=20) as ws:
        seen = [json.loads(ws.recv(timeout=10)) for _ in range(4)]
    driving = [m for m in seen if m.get("t") == "state" and m["running"]]
    assert driving and driving[0]["status"] == "navigating" and "gx" in driving[0] and len(driving[0]["path"]) == 2
    job = fresh.wait_job()
    assert job["error"] is None and job["result"] == "reached"
    p = fresh.get("/pose")["world"]
    assert math.hypot(p["x"] - tx, p["y"] - ty) <= 0.25
    assert abs(bbsim.wrap(p["yaw"] - heading)) < 0.05
    room = fresh.get("/sim/truth")["robot_room"]        # BB yaw 0 faces +y: the room heading came out right
    assert abs(bbsim.wrap(math.radians(room["yaw_deg"] - 90.0))) < 0.05


def test_a_target_inside_an_obstacle_still_reports_reached_somewhere_else(fresh):
    tx, ty, _ = frames.room_to_bb((0.5, 0.0, 0.0), fresh.T)            # the middle of the desk
    fresh.post("/navigate", {"x": tx, "y": ty})
    job = fresh.wait_job()
    assert job["error"] is None and job["result"] == "reached"         # as documented. So: always check /pose
    p = fresh.get("/pose")["world"]
    assert math.hypot(p["x"] - tx, p["y"] - ty) > 0.25


def test_robot_relative_frames_follow_the_documented_formula(fresh):
    p0 = fresh.get("/pose")["world"]
    fresh.post("/navigate", {"x": -0.3, "y": -0.5, "frame": "robot"})  # 0.3 m to its left, 0.5 m behind
    assert fresh.wait_job()["error"] is None
    ex, ey = frames.robot_rel_to_bb(-0.3, -0.5, p0["x"], p0["y"], p0["yaw"])
    p = fresh.get("/pose")["world"]
    assert math.hypot(p["x"] - ex, p["y"] - ey) <= 0.25
    assert fresh.req("POST", "/navigate", {"x": 0, "y": 0, "frame": "area"})[0] == 400   # no rectangle yet
    assert fresh.req("POST", "/navigate", {"x": 0})[0] == 400


def test_one_job_at_a_time_and_stop_cancels(fresh):
    a = frames.room_to_bb((-1.0, -0.9, 0.0), fresh.T)
    first = fresh.post("/navigate", {"x": a[0], "y": a[1]})["job"]
    second = fresh.post("/navigate", {"x": a[0] + 0.1, "y": a[1]})["job"]
    assert second["id"] != first["id"] and fresh.get("/health")["job"]["id"] == second["id"]
    out = fresh.post("/stop")
    assert out["job"]["running"] is False and out["job"]["error"] == "cancelled"
    with connect(f"ws://127.0.0.1:{fresh.ws_port}/ws", open_timeout=20) as ws:
        ws.recv(timeout=10)
        s = json.loads(ws.recv(timeout=10))
    assert s["status"] == "idle" and s["running"] is False


def test_rectangle_sweep_builds_the_2d_map_and_freshness(fresh):
    assert fresh.req("POST", "/patrol", {})[0] == 409                 # patrol needs a rectangle
    fresh.post("/sim/teleport", {"x": -0.9, "y": 0.0, "yaw": 0})
    anchor = fresh.get("/pose")["world"]
    job = fresh.post("/map/rectangle", {"xmin": -1.0, "xmax": 1.0, "ymin": -0.5, "ymax": 1.5, "lane_spacing": 0.6})["job"]
    assert job["kind"] == "rectangle"
    done = fresh.wait_job(180)
    assert done["error"] is None and done["progress"]["waypoint"] == done["progress"]["waypoints"]
    d = fresh.get("/map")
    assert d["area"]["anchor_world"] == pytest.approx(anchor, abs=1e-3)
    assert d["area"]["bounds"] == {"xmin": -1.0, "xmax": 1.0, "ymin": -0.5, "ymax": 1.5}
    g = d["grid"]
    assert (g["resolution_m"], g["nx"], g["ny"]) == (0.03, 67, 67)
    cells = np.frombuffer(base64.b64decode(g["cells"]), np.uint8).reshape(g["ny"], g["nx"])   # the documented decode
    assert set(np.unique(cells)) <= {0, 1, 2} and (cells == 1).any() and (cells == 2).any()
    # the desk is AHEAD of the anchor pose (area +y), so obstacles sit in the high rows: row 0 is ymin
    assert (cells[g["ny"] // 2:] == 2).sum() > (cells[: g["ny"] // 2] == 2).sum()
    f = d["freshness"]
    age = np.array(f["age_s"])
    assert (f["block_m"], f["nx"], f["ny"]) == (0.5, 4, 4) and age.shape == (4, 4)
    assert (age >= 0).sum() >= 12 and age.max() < 60
    area = fresh.get("/pose")["area"]
    assert area is not None and -1.3 < area["x"] < 1.3 and -0.8 < area["y"] < 1.8
    assert fresh.get("/health")["area"]["bounds"]["ymax"] == 1.5


def test_patrol_refreshes_stale_blocks_until_navigate_ends_it_for_good(fresh):
    fresh.post("/sim/teleport", {"x": -0.9, "y": 0.0, "yaw": 0})
    fresh.post("/map/rectangle", {"xmin": -1.0, "xmax": 1.0, "ymin": -0.5, "ymax": 1.5, "sweep": False})
    assert fresh.wait_job()["error"] is None
    age0 = np.array(fresh.get("/map")["freshness"]["age_s"])
    assert (age0 == -1).any()                                          # a new rectangle: blocks never seen
    job = fresh.post("/patrol", {})["job"]
    assert job["kind"] == "patrol" and job["goals"] == 0
    j = fresh.until(lambda: (lambda x: x if x["goals"] >= 3 else None)(fresh.get("/health")["job"]), 180, "3 patrol goals")
    assert j["running"] and set(j["target_world"]) == {"x", "y"} and set(j["target_area"]) == {"x", "y"}
    assert (np.array(fresh.get("/map")["freshness"]["age_s"]) == -1).sum() < (age0 == -1).sum()
    p = fresh.get("/pose")["world"]
    fresh.post("/navigate", {"x": p["x"], "y": p["y"]})
    assert fresh.wait_job()["kind"] == "navigate"
    time.sleep(0.5)
    assert fresh.get("/health")["job"]["kind"] == "navigate"            # patrol does NOT resume by itself


def test_stream_pushes_the_map_and_the_job(fresh):
    fresh.post("/map/rectangle", {"xmin": -0.5, "xmax": 0.5, "ymin": -0.5, "ymax": 0.5, "sweep": False})
    with connect(f"ws://127.0.0.1:{fresh.api_port}/stream?period=0.1", open_timeout=20) as ws:
        t0 = time.time()
        msgs = [json.loads(ws.recv(timeout=10)) for _ in range(4)]
    assert time.time() - t0 < 2.0                                      # ?period= is honoured (default is 1 s)
    assert all({"area", "grid", "freshness", "t", "job"} <= set(m) for m in msgs)
    assert msgs[-1]["job"]["kind"] == "rectangle" and msgs[-1]["grid"]["nx"] == 33


def test_a_moved_object_is_stale_until_the_robot_looks(fresh):
    before = fresh.mirror().cells
    fresh.post("/sim/teleport", {"x": -0.4, "y": 0.0, "yaw": 180})     # back to the desk
    fresh.post("/sim/move", {"object_id": "mug_a1b2", "x": 0.70, "y": -0.30})
    time.sleep(0.6)
    assert fresh.get("/sim/truth")["stale_cells"] > 0
    assert fresh.mirror().cells == before                              # not looking: the map has NOT changed
    with fresh.heavy() as ws:
        m = fresh.mirror(ws=ws)
        fresh.look_at_desk()
        fresh.until(lambda: fresh.get("/sim/truth")["stale_visible"] == 0, 30, "the desk to be re-seen")
        m = fresh.mirror(m=m, ws=ws)
    assert m.clears == 1                                               # it arrived as CHANGES, not a new full copy
    assert m.cells == fresh.mirror().cells                             # changes applied == a fresh full copy
    c, n = colour_centroid_room(fresh, m.cells, m.res, (0x2b, 0x4c, 0x7e))
    assert n > 50 and np.allclose(c[:2], (0.70, -0.30), atol=0.015)    # the mug is where it was put, and only there


def colour_centroid_room(sim, cells, res, rgb):
    P = np.array([c for c, k in cells.items() if k == rgb], float) * res
    return frames.bb_to_room_array(P, sim.T).mean(axis=0), len(P)


def test_an_occluder_hides_a_change_behind_it(fresh):
    fresh.post("/sim/occlude", {"object_id": "mug_a1b2", "by": "box"})
    assert any(o["kind"] == "occluder" for o in fresh.get("/sim/truth")["objects"].values())
    fresh.until(lambda: fresh.get("/sim/truth")["stale_visible"] == 0, 30, "the occluder to be mapped")
    fresh.post("/sim/arm", {"op": "pick", "object_id": "mug_a1b2"})    # the mug is GONE, behind the box
    time.sleep(0.6)                                                    # a dozen change ticks
    blue = [c for c, rgb in fresh.mirror().cells.items() if rgb == (0x2b, 0x4c, 0x7e)]
    assert len(blue) > 20                                              # still in the map: hidden is not gone
    assert fresh.get("/sim/truth")["stale_cells"] > 0
    fresh.post("/sim/occlude", {"object_id": "mug_a1b2", "clear": True})
    fresh.until(lambda: fresh.get("/sim/truth")["stale_visible"] == 0, 30, "the empty spot to be seen")
    assert not [c for c, rgb in fresh.mirror().cells.items() if rgb == (0x2b, 0x4c, 0x7e)]   # now it IS gone


def test_the_arm_stand_in_moves_the_object_in_the_scene(fresh):
    fresh.post("/sim/arm", {"op": "pick", "object_id": "cup_7e21"})
    assert "cup_7e21" in fresh.get("/sim/truth")["held"]
    fresh.post("/sim/arm", {"op": "place", "object_id": "cup_7e21", "pose": [0.55, 0.30, 0.745, 0]})
    o = fresh.get("/sim/truth")["objects"]["cup_7e21"]
    assert (o["x"], o["y"]) == (0.55, 0.30) and fresh.get("/sim/truth")["held"] == []
    assert fresh.req("POST", "/sim/move", {"object_id": "nope", "x": 0})[0] == 400


def test_a_map_reset_bumps_map_gen_sends_an_empty_full_copy_then_rebuilds(fresh):
    gen = fresh.get("/health")["main_py"]["map_gen"]
    with fresh.heavy() as ws:
        m = fresh.mirror(ws=ws)
        assert len(m.cells) > 50_000
        fresh.post("/sim/reset_map")
        kinds = []
        emptied = False
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                t, n = m.feed(ws.recv(timeout=0.5))
            except TimeoutError:
                if emptied and len(m.cells) > 50_000:
                    break
                continue
            kinds.append(t)
            emptied = emptied or (t == 4 and n == 0 and not m.cells)
    assert emptied, "an empty full copy (first=1, count 0) must arrive"
    assert {2, 3} <= set(kinds)                                        # the UI overlays, which a receiver ignores
    assert m.cells == truth_cells(fresh)
    assert fresh.get("/health")["main_py"]["map_gen"] == gen + 1
    assert fresh.get("/health")["area"] is None                        # "define the rectangle again afterwards"


def test_a_reanchor_moves_the_bb_frame_so_the_old_registration_is_wrong(fresh):
    T0 = fresh.T
    room0 = fresh.get("/sim/truth")["robot_room"]
    fresh.post("/sim/reset_map", {"reanchor": True})
    fresh.wait_ready()
    T1, room1 = fresh.T, fresh.get("/sim/truth")["robot_room"]
    assert abs(T1.theta - T0.theta) > 0.2
    assert math.hypot(room1["x"] - room0["x"], room1["y"] - room0["y"]) < 1e-6    # the robot did not move
    p = fresh.get("/pose")["world"]
    stale = frames.bb_to_room((p["x"], p["y"], 0.0), T0)               # what a client that kept the old T believes
    assert math.hypot(stale[0] - room1["x"], stale[1] - room1["y"]) > 0.2


@pytest.mark.parametrize("kind, status", [("drive_busy", "waiting_for_drive"), ("manual", "manual")])
def test_injected_wheel_contention_shows_in_status(fresh, kind, status):
    fresh.post("/sim/fail", {"kind": kind, "once": True, "hold_s": 0.6})
    p = fresh.get("/pose")["world"]
    fresh.post("/navigate", {"x": p["x"] + 0.1, "y": p["y"]})
    with connect(f"ws://127.0.0.1:{fresh.ws_port}/ws", open_timeout=20) as ws:
        seen = {json.loads(ws.recv(timeout=10)).get("status") for _ in range(4)}
    assert status in seen
    assert fresh.wait_job()["error"] is None                           # once: it clears, and the drive goes ahead


def test_injected_nav_failure_is_a_naverror_and_a_failed_status(fresh):
    fresh.post("/sim/fail", {"kind": "nav", "once": True})
    p = fresh.get("/pose")["world"]
    fresh.post("/navigate", {"x": p["x"] + 0.5, "y": p["y"]})
    job = fresh.wait_job()
    assert job["error"] == "NavError: no path" and job["result"] is None
    with connect(f"ws://127.0.0.1:{fresh.ws_port}/ws", open_timeout=20) as ws:
        ws.recv(timeout=10)
        assert json.loads(ws.recv(timeout=10))["status"] == "failed: no path"
    fresh.post("/navigate", {"x": p["x"] + 0.3, "y": p["y"]})
    assert fresh.wait_job()["error"] is None                           # once means once


class RawReader:
    """A websocket reader that really can be slow: a plain socket with a small receive buffer, set BEFORE the
    connection exists, and nothing reading in the background (a library client would)."""
    def __init__(self, port, path):
        self.s = socket.socket()
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        self.s.connect(("127.0.0.1", port))
        key = base64.b64encode(b"bbsim-slow-readr").decode()            # 16 bytes, as the protocol requires
        self.s.sendall((f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            self.buf += self.s.recv(4096)
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        assert b" 101 " in head.split(b"\r\n")[0]

    def _need(self, n, timeout):
        self.s.settimeout(timeout)
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self.buf += chunk

    def recv(self, timeout):
        """One binary message (the server never fragments or masks), or TimeoutError."""
        try:
            self._need(2, timeout)
            n, p = self.buf[1] & 0x7F, 2
            if n == 126:
                self._need(4, timeout); n, p = struct.unpack(">H", self.buf[2:4])[0], 4
            elif n == 127:
                self._need(10, timeout); n, p = struct.unpack(">Q", self.buf[2:10])[0], 10
            self._need(p + n, timeout)
        except socket.timeout:
            raise TimeoutError from None
        op, data, self.buf = self.buf[0] & 0x0F, self.buf[p:p + n], self.buf[p + n:]
        return data if op == 2 else self.recv(timeout)

    def close(self):
        self.s.close()


def test_a_slow_reader_gets_a_fresh_full_copy_and_ends_up_correct(fresh):
    """The robot queues 8 messages per connection. A reader that stops reading must not be fed changes
    against a map it no longer has: it gets first=1 again, and the result is still exactly the scene."""
    before = fresh.get("/sim/truth")["stats"]["fell_behind"]
    fresh.post("/sim/link", {"bytes_per_s": 40_000})                   # the robot's wifi, not loopback: the full copy crawls
    rx = RawReader(fresh.ws_port, "/heavy")
    try:
        for i in range(14):                                            # 14 changes pile up behind it (the queue is 8)
            fresh.post("/sim/scene", {"name": ("messy_bench", "clean_bench")[i % 2]})
            time.sleep(0.12)
        fresh.post("/sim/move", {"object_id": "mug_a1b2", "x": 0.69, "y": 0.18})
        fresh.until(lambda: fresh.get("/sim/truth")["stats"]["fell_behind"] > before, 30, "the sim to drop the queue")
        fresh.until(lambda: fresh.get("/sim/truth")["stale_visible"] == 0, 30, "the last change to be mapped")
        fresh.post("/sim/link", {"bytes_per_s": None})
        m = DocVoxelMap()
        while True:                                                    # read everything: the abandoned copy, then the fresh one
            try:
                m.feed(rx.recv(1.5))
            except TimeoutError:
                break
    finally:
        fresh.post("/sim/link", {"bytes_per_s": None})
        rx.close()
    assert m.clears >= 2                                               # the second first=1: clear and rebuild
    assert m.cells == fresh.mirror().cells                             # and it ends exactly where a new reader does
    c, n = colour_centroid_room(fresh, m.cells, m.res, (0x2b, 0x4c, 0x7e))
    assert n > 50 and np.allclose(c[:2], (0.69, 0.18), atol=0.015)


def test_our_client_runs_against_the_sim(fresh):
    bb_nav = pytest.importorskip("roomctl.bb_nav")
    nav = bb_nav.BBNav("127.0.0.1", fresh.ws_port, fresh.api_port).start()
    try:
        want = fresh.mirror().cells
        fresh.until(lambda: nav.state is not None and nav.state.ready and len(nav.mirror) == len(want), 45, "BBNav to sync")
        assert dict(nav.mirror.cells) == want                               # our client == the documented receiver
        p = fresh.get("/pose")["world"]
        job = nav.navigate(p["x"] + 0.3, p["y"])
        assert job.kind == "navigate"
        done = nav.wait(30, poll=0.05)
        assert done.error is None and done.result == "reached"
        x, y, _ = bb_nav.world_pose(nav.pose())
        assert math.hypot(x - (p["x"] + 0.3), y - p["y"]) <= 0.25
    finally:
        nav.close()


@pytest.mark.skipif(not DOC.exists(), reason="the local copy of Bracket Bot's documentation is not on this machine")
def test_the_receiver_from_the_robots_own_docs_runs_unmodified(tmp_path):
    """The acceptance check. The script is cut out of the documentation byte for byte and run as it is; it
    hard-codes ports 8010/8020, so this sim listens there (loopback). Skips if something else holds them."""
    for port in (8010, 8020):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                pytest.skip(f"port {port} is in use")
    text = DOC.read_text()
    a, end = text.index("import asyncio, json, struct, sys, zlib"), "asyncio.run(main())"
    script = tmp_path / "receiver.py"
    script.write_text(text[a:text.index(end, a) + len(end)] + "\n")
    s = Sim(ws_port=8010, api_port=8020)
    try:
        s.wait_ready()
        n = len(truth_cells(s))
        rx = subprocess.Popen([sys.executable, "-u", str(script), "127.0.0.1"], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True)
        try:
            line = ""
            deadline = time.time() + 20
            while time.time() < deadline and f"voxels={n}" not in line:
                line = rx.stdout.readline()
        finally:
            rx.kill()
        p = s.get("/pose")["world"]
        assert f"voxels={n}" in line, line                              # it rebuilt the whole map...
        assert f"pose x={p['x']:+.2f} y={p['y']:+.2f} yaw={p['yaw']:+.2f}" in line and "status='idle'" in line   # ...and printed the pose
    finally:
        s.close()
