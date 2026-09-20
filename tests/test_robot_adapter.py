"""robot/frames.py + robot/adapter.py — the robot side of the edge's contract, against a simulated robot.

The conversion room -> Bracket Bot's frame lives in ONE place, and the golden tests pin its
conventions: BB is +x right, +y forward, and BB yaw 0 faces +y. The adapter's wire is the edge's
own template server's, status code for status code. Hardware refuses every motion.
"""
import math
import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from robot import adapter, frames  # noqa: E402

REG = adapter.SIM_REGISTRATION                       # theta +90°, tx 1.0, ty -0.5: not the identity, on purpose


@pytest.fixture(autouse=True)
def no_live_sentry(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "")


def near(a, b, tol=1e-9):
    return all(abs(x - y) < tol for x, y in zip(a, b))


# ── ONE conversion: roomctl/frames.py. robot/ only re-exports it ─────────────────
def test_robot_frames_is_the_projects_one_conversion_not_a_second_copy():
    """tests/test_frames.py pins the conventions (h = 0 faces +y, h = theta + phi - pi/2) against the
    geometry. Two implementations of one transform is the bug the plan was written around — so this
    fails if trigonometry on these frames ever appears under robot/ again."""
    import re
    import roomctl.frames as canonical
    for name in ("SE2", "room_to_bb", "bb_to_room", "heading_room_to_bb_yaw", "bb_yaw_to_heading_room",
                 "bb_forward", "base_pose_to_navigate"):
        assert getattr(frames, name) is getattr(canonical, name), name
    for f in ("frames.py", "adapter.py"):
        assert not re.findall(r"\b(sin|cos)\(", (ROOT / "robot" / f).read_text()), f"trigonometry in robot/{f}"


def test_an_unmeasured_registration_is_none_never_the_identity():
    assert frames.from_env({}) is None
    r = frames.from_env({"ROBOT_REGISTRATION": "1.2,-0.4,90,0.03", "ROBOT_REGISTRATION_MAP_GEN": "map-7",
                         "ROBOT_REGISTRATION_RESIDUAL_M": "0.018"})
    assert (r.T.tx, r.T.ty, r.T.dz, r.map_gen, r.residual_m) == (1.2, -0.4, 0.03, "map-7", 0.018)
    assert r.T.theta == pytest.approx(math.pi / 2) and r.source == "measured"
    with pytest.raises(ValueError):
        frames.from_env({"ROBOT_REGISTRATION": "1.2,-0.4"})


# ── the adapter, simulated ───────────────────────────────────────────────────────
def target(x, y, z=0.75, frame=frames.ROOM_FRAME, oid="keys_7c2e"):   # frames.ROOM_FRAME is "world_z_up"
    return {"object_id": oid, "label": "keys", "position": {"x": x, "y": y, "z": z}, "orientation": {"yaw": 0},
            "metadata": {"zone": "desk", "coordinate_frame": frame}}


def point(rid="req_1", **kw):
    t = kw.pop("target", target(2.0, 0.0))
    return {"version": 1, "request_id": rid, "action": {"action_type": "POINT_AT_OBJECT", "request_id": rid,
            "object_id": t.get("object_id"), "source": None, "target": t, "metadata": {}, **kw}}


@pytest.fixture
def sim():
    backend = adapter.SimBackend(time_scale=0.0)
    with TestClient(adapter.create_app(backend, token="s3cret")) as c:
        c.headers["Authorization"] = "Bearer s3cret"
        yield c, backend


def test_point_at_object_stands_off_and_faces_the_object_in_bbs_frame(sim):
    c, backend = sim
    r = c.post("/v1/actions", json=point())
    assert r.status_code == 200
    body = r.json()
    assert (body["version"], body["request_id"], body["result"]["status"]) == (1, "req_1", "success")
    assert body["result"]["message"].startswith("SIMULATED:") and body["result"]["observations"] is None
    (nav, x, y, h), (pt, ox, oy, oz) = backend.log
    assert (nav, pt) == ("navigate", "point")
    assert near((ox, oy, oz), frames.room_to_bb((2.0, 0.0, 0.75), REG.T), 1e-3)     # the object, in BB's world
    assert math.hypot(ox - x, oy - y) == pytest.approx(adapter.STANDOFF_M, abs=1e-3)   # 0.60 m short of it
    fx, fy = frames.bb_forward(h)                                         # and, by BB's own convention, FACING it
    assert near((fx, fy), ((ox - x) / adapter.STANDOFF_M, (oy - y) / adapter.STANDOFF_M), 1e-3)


def test_a_target_in_any_other_frame_is_refused_not_guessed(sim):
    c, backend = sim
    for frame in ("bracketbot_map", None, "world", "world_y_down"):       # a genuinely different frame still refuses
        res = c.post("/v1/actions", json=point(target=target(2, 0, frame=frame))).json()["result"]
        assert res["status"] == "failed" and "never guessed" in res["message"]
    bad = target(2, 0)
    bad["position"] = {"x": 2.0, "y": "left"}
    assert c.post("/v1/actions", json=point(target=bad)).json()["result"]["status"] == "failed"
    assert backend.log == []                                              # nothing moved for any of them


def test_the_wire_token_is_the_projects_and_the_older_name_is_a_legacy_alias(sim):
    """`world_z_up` is what bridge/contract.py, gitspace.plan/1, web/* and roomctl/nav_publish.py
    all say; this file first used docs/20's prose heading instead. The older name is accepted on
    input so anything still sending it keeps working, and is never emitted."""
    c, backend = sim
    assert frames.ROOM_FRAME == "world_z_up" and "canonical_world_z_up" in frames.ROOM_FRAME_ALIASES
    for frame in ("world_z_up", "canonical_world_z_up"):
        assert c.post("/v1/actions", json=point(target=target(2, 0, frame=frame))).json()["result"]["status"] == "success"
    assert len(backend.log) == 4                                          # both moved the simulated base
    assert c.get("/registration").json()["frame_from"] == "world_z_up"    # emitted: only the project's token
    assert c.get("/v1/observation?request_id=o").json()["observation"]["metadata"]["robot_pose_room"][
        "coordinate_frame"] == "world_z_up"


def test_the_wire_is_the_edges_template_status_for_status(sim):
    c, _ = sim
    assert c.get("/health", headers={"Authorization": ""}).json()["ok"] is True          # health needs no token
    assert c.post("/v1/actions", json=point(), headers={"Authorization": "Bearer wrong"}).status_code == 401
    for doc, code in (({**point(), "version": 2}, "unsupported robot API version"),
                      ({"version": 1, "request_id": "", "action": {}}, "response requires request_id"),
                      ({"version": 1, "request_id": "r", "action": {"action_type": "FLY", "request_id": "r"}}, "action_type is invalid"),
                      ({"version": 1, "request_id": "r", "action": {"action_type": "NO_OP", "request_id": "other"}}, "does not match envelope")):
        r = c.post("/v1/actions", json=doc)
        assert r.status_code == 422 and r.json() == {"error": "bad_request", "detail": r.json()["detail"], "retryable": False}
        assert code in r.json()["detail"]
    assert c.post("/v1/actions", content=b"{nope", headers={"content-type": "application/json"}).status_code == 400
    assert c.get("/v1/observation").status_code == 422
    obs_doc = c.get("/v1/observation?request_id=o1").json()
    assert obs_doc["request_id"] == "o1" and obs_doc["observation"]["metadata"]["simulated"] is True
    assert c.post("/v1/actions", json={**point("n1"), "action": {"action_type": "NO_OP", "request_id": "n1"}}).json()["result"]["status"] == "success"
    moved = c.post("/v1/actions", json={**point("m1"), "action": {"action_type": "MOVE_OBJECT", "request_id": "m1"}}).json()["result"]
    assert moved["status"] == "failed" and "not implemented" in moved["message"]


def test_registration_is_published_and_the_robots_pose_comes_back_in_the_room_frame(sim):
    c, _ = sim
    reg = c.get("/registration").json()
    assert reg["T_bb_room"] == {"theta_rad": pytest.approx(math.pi / 2), "tx": 1.0, "ty": -0.5, "dz": 0.0}
    assert reg["simulated"] is True and reg["map_gen"] == "sim-map-1" and reg["frame_from"] == frames.ROOM_FRAME
    m = reg["matrix"]                                                     # the published matrix IS the conversion
    assert near([sum(m[i][j] * v for j, v in enumerate((2, 0, 0.75, 1))) for i in range(3)],
                frames.room_to_bb((2.0, 0.0, 0.75), REG.T), 1e-9)
    c.post("/v1/actions", json=point())
    pose = c.get("/v1/observation?request_id=o2").json()["observation"]["metadata"]["robot_pose_room"]
    assert pose["coordinate_frame"] == frames.ROOM_FRAME
    assert math.hypot(2.0 - pose["x"], 0.0 - pose["y"]) == pytest.approx(adapter.STANDOFF_M, abs=1e-3)
    start = frames.bb_to_room((0.0, 0.0, 0.0), REG.T)                     # BB's origin is NOT the room's under this registration
    assert pose["yaw_deg"] == pytest.approx(math.degrees(math.atan2(0.0 - start[1], 2.0 - start[0])), abs=0.01)


def test_on_hardware_every_motion_is_refused_and_an_unmeasured_registration_says_so(monkeypatch):
    monkeypatch.delenv("ROBOT_REGISTRATION", raising=False)
    with TestClient(adapter.create_app()) as c:                           # HardwareBackend, nothing measured
        assert c.get("/health").json()["registration"] == "unmeasured"
        r = c.get("/registration")
        assert r.status_code == 503 and r.json()["error"] == "registration_unmeasured"
        res = c.post("/v1/actions", json=point()).json()["result"]
        # both are true here; it says the TERMINAL one. The unmeasured registration is not lost —
        # it is what /registration and /health report, above.
        assert res["status"] == "failed" and "hardware motion is not enabled" in res["message"]
    with TestClient(adapter.create_app(registration=frames.Registration(frames.SE2(0.3, 0.2, 0.1), map_gen="m1"))) as c:
        res = c.post("/v1/actions", json=point()).json()["result"]        # measured, but motion is still a human's call
        assert res["status"] == "failed" and "hardware motion is not enabled" in res["message"]


def test_the_edges_trace_is_continued_through_the_adapter(monkeypatch):
    import sentry_sdk
    from sentry_sdk.transport import Transport
    import obs
    envelopes = []

    class InMemory(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope)
    monkeypatch.setattr(obs, "init", lambda role: sentry_sdk.init(dsn="http://k@localhost:9/1", transport=InMemory,
                                                                   traces_sample_rate=1.0, server_name=role) or True)
    try:
        with TestClient(adapter.create_app(adapter.SimBackend(time_scale=0.0))) as c:
            c.post("/v1/actions", json=point(), headers={"sentry-trace": f"{'a' * 32}-{'b' * 16}-1"})
        (tx,) = [i.payload.json for e in envelopes for i in e.items if i.type == "transaction"]
    finally:
        sentry_sdk.get_global_scope().set_client(None)
    assert tx["contexts"]["trace"]["trace_id"] == "a" * 32 and tx["server_name"] == "robot-adapter"
    assert [s["op"] for s in tx["spans"] if s["op"].startswith("adapter.")] == [
        "adapter.action", "adapter.map_gen", "adapter.transform", "adapter.navigate", "adapter.point"]


def test_off_localhost_the_adapter_wants_a_token_and_an_allowlist_and_strangers_get_403(monkeypatch):
    with TestClient(adapter.create_app(adapter.SimBackend(time_scale=0.0), token="t", allow=("10.37.20.56",)),
                    client=("10.37.99.1", 4000)) as stranger:
        assert stranger.get("/health").status_code == 403                  # not even /health: no fingerprinting
        assert stranger.post("/v1/actions", json=point(), headers={"Authorization": "Bearer t"}).status_code == 403
    with TestClient(adapter.create_app(adapter.SimBackend(time_scale=0.0), token="t", allow=("10.37.20.56",)),
                    client=("10.37.20.56", 4000)) as edge:
        assert edge.post("/v1/actions", json=point(), headers={"Authorization": "Bearer t"}).json()["result"]["status"] == "success"
    monkeypatch.setattr(sys, "argv", ["adapter", "--host", "0.0.0.0"])
    monkeypatch.delenv("ROBOT_ALLOW", raising=False)
    monkeypatch.setenv("HOUSEBOT_ROBOT_TOKEN", "t")
    assert adapter.main() == 2                                             # a token alone, in clear text on a wifi, is not enough


def test_every_result_of_a_simulated_backend_says_so(sim):
    c, _ = sim
    for doc in (point("a"), {**point("b"), "action": {"action_type": "NO_OP", "request_id": "b"}},
                {**point("c"), "action": {"action_type": "MOVE_OBJECT", "request_id": "c"}},
                point("d", target=target(2, 0, frame="wrong"))):
        assert c.post("/v1/actions", json=doc).json()["result"]["simulated"] is True, doc["request_id"]
    assert c.get("/health").json()["simulated"] is True
    with TestClient(adapter.create_app(registration=frames.Registration(frames.SE2(0.3, 0.2, 0.1)))) as hw:
        assert hw.post("/v1/actions", json=point()).json()["result"]["simulated"] is False


# ── the map generation: a registration belongs to ONE of them ────────────────────
GEN_1751 = 619536401                                     # crc32 of the robot's 17:51 map origin


def test_the_map_generation_is_the_projects_one_formula():
    """`perception.bb_source.MapSnapshot.map_gen` and `scripts/bbos_map.py` compute this inline;
    pinned here against a real measured origin so a fourth copy cannot drift from them."""
    assert frames.map_gen([-21.119998931884766, -21.119998931884766]) == GEN_1751    # float32, as bbos publishes it
    assert frames.map_gen([-21.12, -21.12]) == GEN_1751                              # and rounded: the same generation
    assert frames.map_gen([-21.12, -20.88]) != GEN_1751                              # a SLAM reset: a different one


def hw(gen_reg, gen_live, measured=True):
    reg = frames.Registration(frames.SE2(0.3, 0.2, 0.1), map_gen=gen_reg, source="measured" if measured else "simulated")
    live = None if gen_live is None else (lambda: gen_live)
    if gen_live == "unreadable":
        def live():
            raise adapter.Stale("cannot read the live map generation from http://127.0.0.1:8080/map/gen")
    return TestClient(adapter.create_app(adapter.SimBackend(time_scale=0.0), registration=reg, map_gen_now=live))


def test_a_motion_is_refused_when_slam_has_re_initialised_since_the_registration():
    with hw(str(GEN_1751), 186401604) as c:
        res = c.post("/v1/actions", json=point()).json()["result"]
        assert res["status"] == "retryable"                      # not "failed": re-measuring fixes it
        assert "186401604" in res["message"] and str(GEN_1751) in res["message"] and "re-measure" in res["message"]
        assert c.get("/health").json()["map_gen"]["ok"] is False


def test_the_same_generation_goes_ahead(sim):
    with hw(str(GEN_1751), GEN_1751) as c:
        assert c.post("/v1/actions", json=point()).json()["result"]["status"] == "success"
        assert c.get("/health").json()["map_gen"] == {"registration": str(GEN_1751), "live": str(GEN_1751), "ok": True}


def test_a_generation_that_cannot_be_read_refuses_too():
    """A registration that cannot be checked is not a registration to drive on."""
    with hw(str(GEN_1751), "unreadable") as c:
        res = c.post("/v1/actions", json=point()).json()["result"]
        assert res["status"] == "retryable" and "cannot read the live map generation" in res["message"]


def test_without_a_measured_generation_nothing_new_is_enforced(sim):
    c, backend = sim                                             # the simulated adapter d2 builds against
    assert c.post("/v1/actions", json=point()).json()["result"]["status"] == "success"
    assert "map_gen" not in c.get("/health").json() or c.get("/health").json()["map_gen"]["ok"] is True
    with hw("", GEN_1751) as c2:                                 # measured registration, no generation recorded
        assert c2.post("/v1/actions", json=point()).json()["result"]["status"] == "success"


def test_no_map_at_all_refuses_a_motion_rather_than_matching_nothing():
    """`/map/gen` answers map_gen: null while SLAM is lost. A registration cannot be checked against
    no map, so the adapter refuses — retryable, because localizing fixes it."""
    def no_map():
        raise adapter.Stale("bbos has no map yet — origin (0,0), no voxels, SLAM not localized")
    reg = frames.Registration(frames.SE2(0.3, 0.2, 0.1), map_gen="619536401")
    with TestClient(adapter.create_app(adapter.SimBackend(time_scale=0.0), registration=reg, map_gen_now=no_map)) as c:
        res = c.post("/v1/actions", json=point()).json()["result"]
    assert res["status"] == "retryable" and "no map yet" in res["message"]
