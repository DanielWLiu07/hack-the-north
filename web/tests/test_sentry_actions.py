"""sentry_actions.py — the only writes to Sentry this server makes.

Three things must hold, and none of them needs a live Sentry:
  1. a caller that is not this laptop cannot write (a tunnel arrives from 127.0.0.1 too, so the
     forwarding header is what gives it away — same rule as roommate_api.py and the event inlet);
  2. no code path anywhere in the module asks Sentry to DELETE anything;
  3. with the credentials blanked (conftest does that for every web test) not one network call is
     made, and the page is told why instead of being shown a fake green badge.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import sentry_actions  # noqa: E402
import sentry_client  # noqa: E402

FORWARDED = {"x-forwarded-for": "203.0.113.7"}          # what a tunnel or reverse proxy adds
WRITES = ("/api/sentry/issues/7742706787/resolve", "/api/sentry/issues/7742706787/remove")


@pytest.fixture()
def api():
    app = FastAPI()
    app.include_router(sentry_actions.router)
    return TestClient(app)


def test_a_forwarded_caller_cannot_write_to_sentry(api, monkeypatch):
    monkeypatch.delenv("GITIRL_CLOUD_TOKEN", raising=False)
    for path in WRITES:
        r = api.post(path, headers=FORWARDED)
        assert r.status_code == 403, (path, r.status_code, r.text)
        assert r.json()["error"] == "forbidden" and "local-only" in r.json()["detail"]


def test_with_a_cloud_token_configured_a_forwarded_caller_gets_401_and_the_bearer_works(api, monkeypatch):
    monkeypatch.setenv("GITIRL_CLOUD_TOKEN", "t" * 40)
    r = api.post(WRITES[0], headers=FORWARDED)
    assert r.status_code == 401 and r.headers["WWW-Authenticate"].startswith("Bearer")
    r = api.post(WRITES[0], headers={**FORWARDED, "authorization": "Bearer " + "t" * 40})
    # past the guard: Sentry is unconfigured in tests, so it stops there — 503, not 401/403
    assert r.status_code == 503 and r.json()["error"] == "sentry_unconfigured"


def test_a_local_caller_gets_past_the_guard_and_is_stopped_by_sentry_being_unconfigured(api):
    for path in WRITES:
        r = api.post(path)
        assert r.status_code == 503, (path, r.text)
        assert r.json()["error"] == "sentry_unconfigured"


def test_the_write_endpoints_refuse_anything_but_a_digit_id_and_a_known_status(api):
    assert api.post("/api/sentry/issues/not-an-id/resolve").status_code == 422
    assert api.post("/api/sentry/issues/not-an-id/remove").status_code == 422
    r = api.post(WRITES[0], json={"status": "deleted"})
    assert r.status_code == 422 and "resolved, ignored, unresolved" in r.json()["detail"]


def test_the_status_read_makes_no_call_and_names_the_reason_while_sentry_is_unconfigured(api):
    before = sentry_actions.sentry.calls
    r = api.get("/api/sentry/issues/status?ids=7742706787,7742706797")
    assert r.status_code == 200 and r.json()["available"] is False and r.json()["issues"] == []
    assert r.json()["reason"], "the panel prints the reason; it never shows a status nobody read"
    assert api.get("/api/sentry/issues/status?ids=abc").status_code == 422
    assert sentry_actions.sentry.calls == before, "a parked/unconfigured Sentry is never dialled"


def test_actions_says_it_cannot_write_and_never_leaks_the_token(api):
    j = api.get("/api/sentry/actions").json()
    assert j["local"] is True and j["can_write"] is False and j["deletes"] is False
    assert j["sentry"]["configured"] is False and j["sentry"]["reason"]
    assert "token" not in str(j).lower() or "AUTH_TOKEN" in j["sentry"]["reason"], \
        "the only mention of a token may be the env var NAME in the reason, never a value"


def test_nothing_in_this_module_can_ask_sentry_to_delete():
    """A guard rail, not a formality: an issue deleted in Sentry cannot be brought back, and the
    whole board is built on the event history behind these ids."""
    code = [line for line in Path(sentry_actions.__file__).read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]
    body = "\n".join(code).split('"""', 2)[-1]          # past the module docstring, which talks ABOUT deleting
    # the only way to ask httpx/SentryClient for a delete is the method token or a .delete() call
    for forbidden in ('"DELETE"', "'DELETE'", ".delete(", "method='DELETE'", 'method="DELETE"'):
        assert forbidden not in body, f"removing a card must never delete an issue in Sentry ({forbidden})"
    assert sentry_actions.WRITABLE == ("resolved", "ignored", "unresolved"), "no fourth thing a press can do"


def test_resolve_writes_a_put_and_then_reads_the_issue_back(monkeypatch):
    """The whole point of the feature: the badge is Sentry's answer, not our optimism."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "PUT":
            return httpx.Response(200, json={"id": "7742706787", "status": "resolved"})
        return httpx.Response(200, json={"id": "7742706787", "shortId": "GITSPACE-Y", "status": "resolved",
                                         "substatus": None, "title": "robot: telemetry_unfed_recovered", "count": "2"})

    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "s" * 40)
    monkeypatch.setenv("SENTRY_ORG_SLUG", "na-alh")
    monkeypatch.setattr(sentry_actions, "sentry",
                        sentry_client.SentryClient(transport=httpx.MockTransport(handler)))
    app = FastAPI()
    app.include_router(sentry_actions.router)
    j = TestClient(app).post("/api/sentry/issues/7742706787/resolve").json()
    assert [m for m, _ in seen] == ["PUT", "GET"], "write, then read it back — in that order"
    assert seen[0][1] == "/api/0/organizations/na-alh/issues/7742706787/"
    assert j["status"] == "resolved" and j["resolved"] is True and j["read_back"] is True
    assert j["short_id"] == "GITSPACE-Y" and j["read_back_at"].endswith("Z")


def test_a_write_that_lands_but_cannot_be_read_back_says_so_instead_of_claiming_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}) if request.method == "PUT" else httpx.Response(500, text="nope")

    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "s" * 40)
    monkeypatch.setenv("SENTRY_ORG_SLUG", "na-alh")
    monkeypatch.setattr(sentry_actions, "sentry",
                        sentry_client.SentryClient(transport=httpx.MockTransport(handler), sleep=_nosleep))
    app = FastAPI()
    app.include_router(sentry_actions.router)
    j = TestClient(app).post("/api/sentry/issues/7742706787/resolve").json()
    assert j["read_back"] is False and j["resolved"] is False and j["status"] is None
    assert j["requested_status"] == "resolved" and j["read_back_failed_because"]


def test_remove_never_deletes_and_only_lets_the_card_go_when_sentry_says_resolved(monkeypatch):
    state = {"status": "unresolved"}
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        assert request.method != "DELETE", "remove must never delete an issue in Sentry"
        if request.method == "PUT":
            state["status"] = "resolved"
            return httpx.Response(200, json={"id": "7742706787", **state})
        return httpx.Response(200, json={"id": "7742706787", "shortId": "GITSPACE-Y", "title": "t", **state})

    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "s" * 40)
    monkeypatch.setenv("SENTRY_ORG_SLUG", "na-alh")
    monkeypatch.setattr(sentry_actions, "sentry",
                        sentry_client.SentryClient(transport=httpx.MockTransport(handler)))
    app = FastAPI()
    app.include_router(sentry_actions.router)
    api = TestClient(app)
    j = api.post("/api/sentry/issues/7742706787/remove").json()
    assert methods == ["GET", "PUT", "GET"], "look, then write only if needed, then confirm"
    assert j["may_remove"] is True and j["was_already_resolved"] is False
    assert j["deleted_in_sentry"] is False and j["kept_in_sentry"] is True

    methods.clear()
    again = api.post("/api/sentry/issues/7742706787/remove").json()
    assert methods == ["GET"], "already resolved: no second write"
    assert again["was_already_resolved"] is True and again["may_remove"] is True


def test_remove_keeps_the_card_when_sentry_will_not_say_resolved(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method != "DELETE"
        return httpx.Response(200, json={"id": "7742706787", "status": "unresolved", "title": "t"})

    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "s" * 40)
    monkeypatch.setenv("SENTRY_ORG_SLUG", "na-alh")
    monkeypatch.setattr(sentry_actions, "sentry",
                        sentry_client.SentryClient(transport=httpx.MockTransport(handler)))
    app = FastAPI()
    app.include_router(sentry_actions.router)
    j = TestClient(app).post("/api/sentry/issues/7742706787/remove").json()
    assert j["may_remove"] is False and j["removed_from_board"] is False
    assert "stays" in j["what_we_did"]


async def _nosleep(_):
    return None


def test_the_status_read_asks_sentry_ONCE_for_every_id(monkeypatch):
    """N parallel GETs is what got this token rate limited, and a 429 on the shared token makes
    every other Sentry panel on /telemetry fail at the same time (that is where the 502 on
    GET /api/telemetry/sentry/cap_0001 came from). One search, N ids."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=[
            {"id": "111", "shortId": "GITSPACE-A", "status": "resolved", "title": "a"},
            {"id": "222", "shortId": "GITSPACE-B", "status": "ignored", "title": "b"},
        ])

    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "s" * 40)
    monkeypatch.setenv("SENTRY_ORG_SLUG", "na-alh")
    monkeypatch.setattr(sentry_actions, "sentry", sentry_client.SentryClient(transport=httpx.MockTransport(handler)))
    app = FastAPI()
    app.include_router(sentry_actions.router)
    j = TestClient(app).get("/api/sentry/issues/status?ids=111,222,333").json()
    assert len(calls) == 1, f"one call for three ids, got {len(calls)}"
    assert "issue.id%3A%5B111%2C222%2C333%5D" in calls[0] or "issue.id:[111,222,333]" in calls[0]
    assert [i["id"] for i in j["issues"]] == ["111", "222"]
    assert j["issues"][0]["resolved"] is True and j["issues"][1]["ignored"] is True
    assert [u["id"] for u in j["unreadable"]] == ["333"], "an id Sentry did not return is named, not invented"


def test_a_throttled_status_read_is_a_well_formed_answer_not_a_5xx(monkeypatch):
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "s" * 40)
    monkeypatch.setenv("SENTRY_ORG_SLUG", "na-alh")
    monkeypatch.setattr(sentry_actions, "sentry", sentry_client.SentryClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(429)), sleep=_nosleep))
    app = FastAPI()
    app.include_router(sentry_actions.router)
    r = TestClient(app).get("/api/sentry/issues/status?ids=111")
    assert r.status_code == 200, "a rate limit must not put a 5xx in the demo's console"
    j = r.json()
    assert j["available"] is False and "rate limiting" in j["reason"] and j["retryable"] is True
    assert j["issues"] == [] and j["unreadable"][0]["id"] == "111"


# ── the capture panel's honesty, which the same rate limit broke ────────────────────────────
# These cover web/telemetry_api.py's capture_in_sentry, not this module. They live here because
# that file belongs to another session and this is the change that made them necessary. The store
# is stubbed the way that session's own suite does it: a REAL capture, sourced from Elasticsearch,
# so the endpoint actually reaches Sentry instead of short-circuiting on "no trace".

TRACE = "017976011bd54f10ac401b6d081bffc9"


def _real_capture(monkeypatch):
    import store

    async def find(index, filters, **kw):
        if index == "room-clouds":
            return [{"capture_id": "cap_1003", "@timestamp": "2026-09-19T01:41:34.805Z", "sentry_trace_id": TRACE,
                     "sentry_url": f"https://na-alh.sentry.io/performance/trace/{TRACE}/"}], "elasticsearch"
        return [{"vlm_model": "scripts/story_demo"}], "elasticsearch"
    monkeypatch.setattr(store, "_find", find)


def _board(monkeypatch, client):
    import telemetry_api
    _real_capture(monkeypatch)
    monkeypatch.setattr(telemetry_api, "sentry", client)
    app = FastAPI()
    app.include_router(telemetry_api.router)
    return TestClient(app)


LIVE = {"SENTRY_DSN": "https://a@o1.ingest.sentry.io/2", "SENTRY_AUTH_TOKEN": "t" * 40, "SENTRY_ORG_SLUG": "na-alh"}


def test_a_real_trace_with_nothing_under_it_says_so_instead_of_drawing_a_blank(monkeypatch):
    """cap_1003 on the live board is exactly this: a capture whose trace id is real but which
    Sentry has no spans and no issues for. Tracing sampled nothing, or nothing failed during it —
    a normal state, and the panel has to say which rather than print an empty waterfall."""
    def http(request):
        if "/events-trace/" in request.url.path:
            return httpx.Response(200, json={"transactions": []})
        return httpx.Response(200, json=[])
    api = _board(monkeypatch, sentry_client.SentryClient(LIVE, transport=httpx.MockTransport(http)))
    j = api.get("/api/telemetry/sentry/cap_1003").json()
    assert j["available"] is True and j["issues"] == [] and j["waterfall"]["spans"] == 0
    assert "nothing in Sentry carries this capture's tags" in j["reason"]


def test_a_trace_that_does_have_spans_still_reports_no_reason(monkeypatch):
    def http(request):
        p = request.url.path
        if "/events-trace/" in p:
            return httpx.Response(200, json={"transactions": [{"event_id": "aa11", "project_slug": "gitspace", "children": []}]})
        if p.endswith("/events/aa11/"):
            return httpx.Response(200, json={"entries": [{"type": "spans", "data": [{"op": "depth", "start_timestamp": 1.0, "timestamp": 3.8}]}]})
        return httpx.Response(200, json=[])
    api = _board(monkeypatch, sentry_client.SentryClient(LIVE, transport=httpx.MockTransport(http)))
    j = api.get("/api/telemetry/sentry/cap_1003").json()
    assert j["available"] is True and j["reason"] is None and j["waterfall"]["spans"] == 1


def test_sentry_throttling_does_not_502_the_capture_panel(monkeypatch):
    api = _board(monkeypatch, sentry_client.SentryClient(LIVE, transport=httpx.MockTransport(
        lambda r: httpx.Response(429)), sleep=_nosleep))
    r = api.get("/api/telemetry/sentry/cap_1003")
    assert r.status_code == 200, "a throttled token says nothing about this capture"
    j = r.json()
    assert j["available"] is False and j["retryable"] is True and "rate limiting" in j["reason"]
    assert j["waterfall"] is None and j["issues"] == []


def test_a_paused_sentry_is_still_a_503_because_that_is_configuration_not_weather(monkeypatch):
    """The §2.7 contract the board session's own suite asserts: parked credentials are a state of
    THIS server, they do not pass on their own, and narrowing the catch above must not change it."""
    parked = {"SENTRY_DSN": "# PAUSED until 01:00", "SENTRY_DSN_PARKED": "https://a@o1.ingest.sentry.io/2",
              "SENTRY_AUTH_TOKEN": "t" * 40, "SENTRY_ORG_SLUG": "na-alh"}
    api = _board(monkeypatch, sentry_client.SentryClient(parked))
    r = api.get("/api/telemetry/sentry/cap_1003")
    assert r.status_code == 503 and r.json()["error"] == "sentry_paused" and r.json()["retryable"] is True
