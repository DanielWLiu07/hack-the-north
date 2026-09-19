"""web/replay_api.py — Robot Session Replay. No network: the store boundary and Sentry are mocked.
The odometry is checked against motions whose answer is known in closed form."""
from __future__ import annotations

import asyncio
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import replay_api  # noqa: E402
import sentry_client  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402

R, B = 0.0825, 0.425                       # docs/02-hardware.md: 165 mm wheels, 425 mm wheelbase
TURN = 2 * math.pi * R


def test_straight_line_is_the_wheel_circumference_per_turn():
    enc = [[i * 0.02, i * 0.01] for i in range(101)]            # both wheels: 1 turn in 2 s
    out = replay_api.integrate(enc, enc, radius_m=R, base_m=B)
    t, x, y, yaw = out["path"][-1]
    assert t == 2.0 and x == pytest.approx(TURN, abs=1e-4) and y == 0 and yaw == 0
    assert out["info"]["distance_m"] == pytest.approx(TURN, abs=1e-3) and out["info"]["reason"] is None


def test_spinning_in_place_turns_without_travelling():
    n = B / (8 * R)                                             # wheel turns for a quarter turn of the body: (pi/2 * B/2) / (2 pi R)
    left = [[i * 0.02, -n * i / 100] for i in range(101)]
    right = [[i * 0.02, n * i / 100] for i in range(101)]
    _, x, y, yaw = replay_api.integrate(left, right, radius_m=R, base_m=B)["path"][-1]
    assert yaw == pytest.approx(math.pi / 2, abs=1e-3) and abs(x) < 1e-6 and abs(y) < 1e-6


def test_an_arc_ends_where_geometry_says():
    radius, sweep = 1.0, math.pi / 2                            # quarter circle, 1 m radius, turning left
    left = [[i * 0.02, (radius - B / 2) * sweep * (i / 200) / TURN] for i in range(201)]
    right = [[i * 0.02, (radius + B / 2) * sweep * (i / 200) / TURN] for i in range(201)]
    _, x, y, yaw = replay_api.integrate(left, right, radius_m=R, base_m=B)["path"][-1]
    assert (x, y, yaw) == pytest.approx((1.0, 1.0, math.pi / 2), abs=2e-3)


def test_no_encoders_is_no_path_and_a_counter_reset_is_not_motion():
    out = replay_api.integrate([], [])
    assert out["path"] is None and "no left_enc" in out["info"]["reason"]
    assert replay_api.integrate([[0, 1.0]], [[0.5, 1.0]])["path"] is None      # no shared timestamps
    enc = [[0.0, 10.0], [0.02, 10.01], [0.04, 0.0], [0.06, 0.01]]               # the controller rebooted at 0.04
    out = replay_api.integrate(enc, enc, radius_m=R, base_m=B)
    assert out["info"]["resets"] == 1 and out["path"][-1][1] == pytest.approx(0.02 * TURN, abs=1e-4)
    assert replay_api._level({"min": 1, "max": 3, "sum": 8, "value_count": 4}) == 2.0
    assert replay_api.nearest([[0, 5], [1, 6], [2, 7]], 1.4) == 6 and replay_api.nearest([], 1) is None


def test_pinning_moves_the_path_onto_the_stored_pose_and_measures_the_drift_at_the_others():
    enc = [[-2 + i * 0.02, i * 0.01] for i in range(101)]        # 1 turn straight ahead, ending at the shutter (t = 0)
    path = replay_api.integrate(enc, enc, radius_m=R, base_m=B)["path"]
    anchors = [{"t": 0.0, "capture_id": "cap_b", "x": 2.0, "y": 1.0, "yaw_deg": 90.0},
               {"t": -2.0, "capture_id": "cap_a", "x": 2.0, "y": 1.0 - TURN + 0.03, "yaw_deg": 90.0}]   # where the robot REALLY was 2 s earlier
    home = replay_api.pin(path, anchors)
    assert home["capture_id"] == "cap_b" and path[-1][1:] == pytest.approx([2.0, 1.0, math.pi / 2], abs=1e-4)
    assert path[0][1:3] == pytest.approx([2.0, 1.0 - TURN], abs=1e-3), "heading 90°: the drive came up the y axis"
    assert anchors[0]["gap_m"] is None and anchors[1]["gap_m"] == pytest.approx(0.03, abs=1e-3), "3 cm of integration drift, shown"
    assert replay_api.pin(path, [{"t": 0, "capture_id": "c", "x": 0, "y": 0, "yaw_deg": None}]) is None


# ── the endpoint, over a mocked store ──────────────────────────────────────────────────────────
SHUTTER = datetime(2026, 9, 19, 0, 37, 29, tzinfo=timezone.utc)
TRACE = "e6d4e9e5380b49e98c94d2a713613f6a"
iso = lambda d: d.isoformat().replace("+00:00", "Z")   # noqa: E731


def fake_store(monkeypatch, *, encoders: bool, synthetic: bool):
    async def capture(capture_id):
        if capture_id != "cap_0004":
            raise store.NotFound(capture_id)
        return {"capture_id": "cap_0004", "ts": iso(SHUTTER), "synthetic": synthetic, "gate": {"pass": False, "failing": ["tilt_rate_max"]},
                "sentry": {"trace_id": TRACE, "url": None if synthetic else f"https://na-alh.sentry.io/performance/trace/{TRACE}/",
                           "why_no_link": "synthetic trace — not recorded in Sentry" if synthetic else None}}

    async def find(index, filters, *, time_range=None, size=500, newest_first=False, label="", prefix=None):
        lo, hi = (store.when(x) for x in time_range)
        if index == "robot-telemetry":
            if label.startswith("replay.enc_"):
                return ([{"@timestamp": iso(SHUTTER - timedelta(seconds=41)), "signal": "left_enc", "value": 1.0}] if "before" in label else []), "elasticsearch"
            docs = []
            for i in range(-100, 51):                           # 50 Hz, -2 s … +1 s
                t = SHUTTER + timedelta(milliseconds=20 * i)
                if not lo <= t <= hi:
                    continue
                docs.append({"@timestamp": iso(t), "signal": "tilt_rate", "value": 0.134 if i == -10 else 0.004})
                docs.append({"@timestamp": iso(t), "signal": "pitch", "value": 0.02})
                docs.append({"@timestamp": iso(t), "signal": "odom_residual", "value": 0.006})
                if encoders:
                    docs.append({"@timestamp": iso(t), "signal": "left_enc", "value": (i + 100) * 0.001})
                    docs.append({"@timestamp": iso(t), "signal": "right_enc", "value": (i + 100) * 0.001})
            return docs, "elasticsearch"
        if index == "room-clouds":
            return [{"capture_id": "cap_0004", "@timestamp": iso(SHUTTER), "quality_ok": False}], "elasticsearch"
        return [{"@timestamp": iso(SHUTTER + timedelta(seconds=1)), "event_type": "capture_rejected", "capture_id": "cap_0004",
                 "outcome": "rejected", "message": "tilt_rate_max 0.083 rad/s > 0.05"}], "elasticsearch"
    monkeypatch.setattr(store, "capture", capture)
    monkeypatch.setattr(store, "_find", find)


@pytest.fixture()
def api():
    return TestClient(server.app)


def test_a_synthetic_capture_replays_what_exists_and_says_what_does_not(api, monkeypatch):
    fake_store(monkeypatch, encoders=False, synthetic=True)
    monkeypatch.setattr(replay_api, "sentry", sentry_client.SentryClient({}, transport=httpx.MockTransport(lambda r: pytest.fail("no Sentry call for a synthetic trace"))))
    j = api.get("/api/replay/cap_0004?before=2&after=1").json()
    assert j["path"] is None and "no left_enc / right_enc samples in this window — the nearest encoder sample is 41 s before" == j["path_info"]["reason"]
    assert len(j["pitch"]) == 151 and j["pitch"][0][0] == -2.0 and j["recorded"]["left_enc"] == 0
    assert j["spike"] == {"t": -0.2, "value": 0.134, "threshold": 0.05}
    assert [(e["type"], e["t"], e["derived"]) for e in j["events"]] == [("tilt_spike", -0.2, True), ("shutter", 0.0, False), ("capture_rejected", 1.0, False)]
    assert j["spans"] == [] and "synthetic" in j["spans_reason"] and j["trace_url"] is None
    assert j["anchors"] == [] and "room-clouds stores no capture pose" in j["anchors_reason"] and j["downsampled"] is None


def test_a_real_capture_gets_its_path_and_its_spans_on_the_shutters_clock(api, monkeypatch):
    fake_store(monkeypatch, encoders=True, synthetic=False)
    epoch = SHUTTER.timestamp()
    tree = [{"op": "room.status", "description": "room status [cap_0004]", "is_transaction": True, "event_id": "t1", "start_timestamp": epoch - 0.4,
             "end_timestamp": epoch + 0.3, "children": [
                 {"op": "robot.capture", "description": "3x stereo grab", "event_id": "s1", "parent_span_id": "t1", "start_timestamp": epoch - 0.39, "end_timestamp": epoch - 0.1},
                 {"op": "arm.pick", "description": "pick mug_a1b2", "event_id": "s2", "parent_span_id": "t1", "start_timestamp": epoch, "end_timestamp": epoch + 0.3}]}]
    live = {"SENTRY_DSN": "https://a@o1.ingest.sentry.io/2", "SENTRY_AUTH_TOKEN": "t" * 40, "SENTRY_ORG_SLUG": "na-alh"}
    monkeypatch.setattr(replay_api, "sentry", sentry_client.SentryClient(live, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=tree))))
    j = api.get("/api/replay/cap_0004?before=2&after=1").json()
    assert len(j["path"]) == 151 and j["path"][-1][1] == pytest.approx(0.150 * TURN, abs=1e-3) and j["path_info"]["reason"] is None
    ops = {s["op"]: (s["t0"], s["t1"]) for s in j["spans"]}
    assert ops["robot.capture"] == (-0.39, -0.1), "the tilt spike at -0.2 s falls INSIDE robot.capture: one event, two views"
    assert [a["op"] for a in j["arm_spans"]] == ["arm.pick"] and j["trace_url"].endswith(f"/trace/{TRACE}/")


def test_guards(api, monkeypatch):
    fake_store(monkeypatch, encoders=False, synthetic=True)
    assert api.get("/api/replay/..%2Fetc").status_code in (404, 422)
    assert api.get("/api/replay/cap_9999").json()["error"] == "not_found"
    assert api.get("/api/replay/cap_0004?before=500").status_code == 422
    assert api.get("/api/replay/window?from=2026-09-19T00:00:00Z&to=2026-09-19T01:00:00Z").json()["error"] == "bad_request"
    w = api.get("/api/replay/window", params={"from": iso(SHUTTER - timedelta(seconds=2)), "to": iso(SHUTTER)}).json()
    assert w["capture_id"] is None and w["t0_is"] == "the start of the window" and w["pitch"][0][0] == 0.0 and w["spans"] == []
