"""web/telemetry_api.py on the FIXTURE file, with Elasticsearch and Sentry mocked at the boundary:
the Elastic client handed to store.py raises `elastic_paused` (so nothing touches the network even
if a key is un-parked later), and sentry_client talks to an httpx.MockTransport.

    ../.venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

WEB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB))
os.environ["SENTRY_DSN"] = ""                  # never initialise Sentry from a test

import sentry_client                            # noqa: E402
import store                                    # noqa: E402
import telemetry_api                            # noqa: E402

PARKED = {"SENTRY_DSN": "# PAUSED until 01:00", "SENTRY_DSN_PARKED": "https://a@o1.ingest.sentry.io/2",
          "SENTRY_AUTH_TOKEN": "t" * 40, "SENTRY_ORG_SLUG": "na-alh"}
TRACE = "e6d4e9e5380b49e98c94d2a713613f6a"


class PausedElastic:
    """What server.py's Elastic client does while the key is parked: raise, never connect."""
    class Err(Exception):
        code, detail, status, retryable = "elastic_paused", "parked", 503, True

    async def search(self, *a, **k):
        raise self.Err()


def boom(request):
    raise AssertionError(f"a Sentry call was made: {request.url}")


@pytest.fixture()
def api(monkeypatch):
    telemetry_api.init(PausedElastic())
    monkeypatch.setattr(telemetry_api, "sentry", sentry_client.SentryClient(PARKED, transport=httpx.MockTransport(boom)))
    app = FastAPI()
    app.include_router(telemetry_api.router)
    with TestClient(app) as c:
        yield c
    assert telemetry_api.sentry.calls == 0 or not telemetry_api.sentry.state()["paused"]


def test_board_tells_cap_0004s_story_from_the_fixture(api):
    d = api.get("/api/telemetry/board?limit=12").json()
    assert d["source"] == "fixture"
    caps = {c["capture_id"]: c for c in d["captures"]}
    assert [c["ts"] for c in d["captures"]] == sorted((c["ts"] for c in d["captures"]), reverse=True)
    c4, c5 = caps["cap_0004"], caps["cap_0005"]
    assert c4["gate"]["pass"] is False and c4["gate"]["failing"] == ["tilt_rate_max"]
    sp = c4["telemetry"]["spike"]
    assert -260 <= sp["at_ms"] <= -140 and abs(abs(sp["value"]) - 0.134) < 0.01 and sp["in_latch_window"] is False
    assert max(abs(p[1]) for p in c4["telemetry"]["signals"]["tilt_rate"]) == abs(sp["value"])     # decimation kept it
    assert all(len(p) <= 200 for p in c4["telemetry"]["signals"].values())
    assert c4["retry"] == "cap_0005" and c5["retry_of"] == "cap_0004"
    assert c5["gate"]["pass"] is True and c5["telemetry"]["spike"] is None
    assert c4["event"]["event_type"] == "capture_rejected" and c4["event"]["moved"] == 10
    sm = d["summary"]
    assert sm["rejected"] == 1 and sm["worst_tilt_rate_max"] == {"value": 0.0825, "capture_id": "cap_0004"} and sm["p95_skew_ms"] == 3.39


def test_sentry_doorways_are_honest_about_synthetic_data_and_the_pause(api):
    d = api.get("/api/telemetry/board").json()
    stack = d["sentry_stack"]
    assert stack["paused"] is True and stack["until"] == "01:00" and stack["configured"] is False
    sn = next(c for c in d["captures"] if c["capture_id"] == "cap_0004")["sentry"]
    assert sn["search"] == "capture_id:cap_0004" and sn["tags"] == {"capture_id": "cap_0004"}
    assert sn["trace"] is None and sn["issues"] is None and sn["logs"] is None and "synthetic" in sn["why_no_links"]
    c5 = next(c for c in d["captures"] if c["capture_id"] == "cap_0005")["sentry"]
    assert c5["tags"]["commit_sha"].startswith("1a668ec")
    r = api.get("/api/telemetry/sentry/cap_0004")
    assert r.status_code == 200 and r.json()["available"] is False and "synthetic" in r.json()["reason"]
    assert api.get("/api/telemetry/sentry/nope_1").status_code == 404
    assert api.get("/api/telemetry/board?limit=0").status_code == 422


def test_failure_rows_carry_the_three_doorways(api):
    rows = api.get("/api/telemetry/board").json()["failures"]
    assert [r["capture_id"] for r in rows] == ["cap_0004"]
    r = rows[0]
    assert r["kind"] == "capture_rejected" and r["detail"].startswith("tilt_rate_max 0.0825 rad/s — the gate needs < 0.05")
    assert r["capture_url"] == "/capture/cap_0004" and r["from"] == "room-events"
    assert r["trace"]["url"] is None and "synthetic" in r["trace"]["why_no_link"] and r["trace"]["trace_id"]


def test_ask_seer_is_stumped_out_loud_while_sentry_is_parked(api):
    st = api.get("/api/seer/status").json()
    assert st["available"] is False and st["verified"] is False and "paused until 01:00" in st["reason"]
    assert st["credits"] is None and st["credits_reason"] == "unknown — Sentry paused until 01:00"
    r = api.post("/api/seer/ask", json={"capture_id": "cap_0004"})
    assert r.status_code == 200, "a stumped Seer is an ANSWER, not an error"
    out = r.json()
    assert out["state"] == "stumped" and out["reason"] == "Sentry is paused until 01:00 to save quota" and out["verdict"] is None
    for bad in ({"capture_id": "../x"}, {"capture_id": "cap_0004", "context_depth": 999}, {"capture_id": "cap_0004", "context_depth": True}, {}):
        e = api.post("/api/seer/ask", json=bad)
        assert e.status_code == 422 and e.json()["error"] == "bad_request"


def test_a_real_capture_gets_its_waterfall_and_a_verdict_when_sentry_answers(api, monkeypatch):
    async def find(index, filters, **kw):                      # the store boundary: a REAL capture, read from "elasticsearch"
        if index == "room-clouds":
            return [{"capture_id": "cap_82093", "@timestamp": "2026-09-19T01:41:34.805Z", "sentry_trace_id": TRACE,
                     "sentry_url": f"https://na-alh.sentry.io/performance/trace/{TRACE}/"}], "elasticsearch"
        return [{"vlm_model": "scripts/story_demo"}], "elasticsearch"

    def http(request):
        p = request.url.path
        if "/events-trace/" in p:
            return httpx.Response(200, json={"transactions": [{"event_id": "aa11", "project_slug": "gitspace", "children": []}]})
        if p.endswith("/events/aa11/"):
            return httpx.Response(200, json={"entries": [{"type": "spans", "data": [{"op": "depth", "start_timestamp": 1.0, "timestamp": 3.8}]}]})
        if p.endswith("/autofix/setup/"):
            return httpx.Response(200, json={"integration": {"ok": True}, "seerReposLinked": True, "autofixEnabled": True,
                                             "billing": {"hasAutofixQuota": True}})
        if p.endswith("/autofix/"):
            assert p.startswith("/api/0/organizations/na-alh/issues/"), "the bare /issues/<id>/autofix/ path 404s on the live API"
            return httpx.Response(200, json={"autofix": {"status": "COMPLETED", "steps": [{"type": "root_cause_analysis",
                                  "causes": [{"title": "captured mid-lean", "description": "the robot was recovering from a tilt"}]}]}})
        return httpx.Response(200, json=[{"id": "7741490949", "shortId": "GITSPACE-3", "title": "robot: capture_rejected"}])
    monkeypatch.setattr(store, "_find", find)
    live = {"SENTRY_DSN": "https://a@o1.ingest.sentry.io/2", "SENTRY_AUTH_TOKEN": "t" * 40, "SENTRY_ORG_SLUG": "na-alh"}
    monkeypatch.setattr(telemetry_api, "sentry", sentry_client.SentryClient(live, transport=httpx.MockTransport(http)))
    j = api.get("/api/telemetry/sentry/cap_82093").json()
    assert j["available"] is True and j["waterfall"]["stages"] == [{"stage": "depth", "ms": 2800.0, "start_ms": 0.0, "spans": 1}]
    out = api.post("/api/seer/ask", json={"capture_id": "cap_82093", "context_depth": 10}).json()
    assert out["state"] == "verdict" and "mid-lean" in out["verdict"] and out["verified"] is False
    monkeypatch.setattr(telemetry_api, "sentry", sentry_client.SentryClient(PARKED))
    r = api.get("/api/telemetry/sentry/cap_82093")
    assert r.status_code == 503 and r.json()["error"] == "sentry_paused" and r.json()["retryable"] is True


def test_decimation_never_loses_a_one_sample_spike():
    pts = [[i * 1.0, 0.001 * ((i * 7) % 5)] for i in range(5000)]
    pts[3333][1], pts[1200][1] = 9.87, -4.2
    out = telemetry_api.decimate(pts, 200)
    assert len(out) <= 200 and [3333.0, 9.87] in out and [1200.0, -4.2] in out and out == sorted(out, key=lambda p: p[0])
    assert telemetry_api.decimate(pts[:150], 200) == pts[:150]


def test_seer_assets_are_served_narrowly(api, tmp_path, monkeypatch):
    seer = tmp_path / "seer"
    for rel, data in {"models/hand.glb": b"glTF", "seer.js": b"//", "tools/gen.js": b"//", "reference/art.png": b"x", "notes.py": b"#"}.items():
        (seer / rel).parent.mkdir(parents=True, exist_ok=True)
        (seer / rel).write_bytes(data)
    (tmp_path / "secret.js").write_bytes(b"//")
    monkeypatch.setattr(telemetry_api, "SEER", seer.resolve())
    r = api.get("/pages/seer/models/hand.glb")
    assert r.status_code == 200 and r.headers["content-type"] == "model/gltf-binary" and r.headers["cache-control"] == "no-cache"
    assert api.get("/pages/seer/seer.js").status_code == 200
    for bad in ("tools/gen.js", "reference/art.png", "notes.py", "../secret.js", "%2e%2e/secret.js", "..%2Fsecret.js",
                "models/../../secret.js", "nope.js", ""):
        assert api.get(f"/pages/seer/{bad}").status_code == 404, bad


def test_a_story_demo_capture_is_labelled_scripted_even_though_its_trace_is_real(api, monkeypatch):
    """cap_82093 / cap_78072: scripts/story_demo ran them through Sentry for real, with scripted numbers and no
    (or a scripts/) vlm_model. `synthetic` (never ran -> no link) is False; `provenance.synthetic` must be True."""
    async def find(index, filters, **kw):
        if index == "room-clouds":
            return [{"capture_id": "cap_82093", "@timestamp": "2026-09-19T01:41:34.805528+00:00", "quality_ok": False,
                     "sentry_trace_id": TRACE, "sentry_url": f"https://na-alh.sentry.io/performance/trace/{TRACE}/"}], "elasticsearch"
        if index == "room-observations":
            return [{"capture_id": "cap_82093", "camera": "cam0"}], "elasticsearch"          # no vlm_model at all
        return [], "elasticsearch"
    monkeypatch.setattr(store, "_find", find)
    card = api.get("/api/telemetry/board").json()["captures"][0]
    assert card["synthetic"] is False and card["sentry"]["url"], "it DID run: the real trace link stays"
    assert card["provenance"]["synthetic"] is True and "no vlm_model recorded" in card["provenance"]["why"]
