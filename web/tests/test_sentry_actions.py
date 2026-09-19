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
