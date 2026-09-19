"""web/sentry_client.py against MOCKED HTTP only — nothing here may ever reach sentry.io.

    ../.venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
import pytest

WEB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB))
os.environ["SENTRY_DSN"] = ""                  # never initialise Sentry from a test

import sentry_client as sc                      # noqa: E402

TRACE = "e6d4e9e5380b49e98c94d2a713613f6a"
LIVE = {"SENTRY_DSN": "https://abc@o1.ingest.us.sentry.io/2", "SENTRY_AUTH_TOKEN": "sntrys_" + "x" * 40, "SENTRY_ORG_SLUG": "na-alh"}
PARKED = {**LIVE, "SENTRY_DSN": "# PAUSED until 01:00 — obs.init() no-ops without a DSN", "SENTRY_DSN_PARKED": LIVE["SENTRY_DSN"]}
ISSUE = [{"id": "7741490949", "shortId": "GITSPACE-3", "title": "robot: grasp_slipped", "permalink": "https://na-alh.sentry.io/issues/7741490949/"}]
run = asyncio.run


def boom(request):
    raise AssertionError(f"network call while paused: {request.url}")


def scripted(responses, seen=None):
    it = iter(responses)

    def handler(request):
        if seen is not None:
            seen.append(request)
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    return httpx.MockTransport(handler)


def client(responses, seen=None, slept=None):
    async def no_sleep(s):
        if slept is not None:
            slept.append(s)
    return sc.SentryClient(LIVE, transport=scripted(responses, seen), sleep=no_sleep)


# ── the pause ────────────────────────────────────────────────────────────────────────────────

def test_parked_makes_no_call_and_says_until():
    c = sc.SentryClient(PARKED, transport=httpx.MockTransport(boom))
    st = c.state()
    assert st["paused"] and st["until"] == "01:00" and not st["configured"]
    for call in (c.trace_summary(TRACE), c.issues_for_capture("cap_0004"), c.recent_issues()):
        with pytest.raises(sc.SentryError) as e:
            run(call)
        assert (e.value.code, e.value.status, e.value.retryable) == ("sentry_paused", 503, True)
    assert c.calls == 0


def test_a_parked_token_pauses_too_and_nothing_set_is_unconfigured():
    tok = sc.SentryClient({**LIVE, "SENTRY_AUTH_TOKEN": "   # PAUSED until 01:00", "SENTRY_AUTH_TOKEN_PARKED": "real"})
    assert tok.state()["paused"] and "SENTRY_AUTH_TOKEN" in tok.state()["reason"]
    st = sc.SentryClient({}).state()
    assert st["paused"] is False and st["configured"] is False and "not set" in st["reason"]


# ── reads ────────────────────────────────────────────────────────────────────────────────────

def _trace_handler(request):
    assert request.method == "GET" and request.headers["authorization"].startswith("Bearer sntrys_")
    assert str(request.url).startswith("https://sentry.io/api/0/")
    path = request.url.path
    if path.endswith(f"/events-trace/{TRACE}/"):
        return httpx.Response(200, json={"transactions": [{"event_id": "aa11", "project_slug": "gitspace",
            "children": [{"event_id": "bb22", "project_slug": "gitspace", "children": []}]}]})
    if path.endswith("/events/aa11/"):
        return httpx.Response(200, json={"entries": [{"type": "spans", "data": [
            {"op": "segment", "start_timestamp": 104.4, "timestamp": 107.5}, {"op": "depth", "start_timestamp": 101.6, "timestamp": 103.0},
            {"op": "depth", "start_timestamp": 103.0, "timestamp": 104.4}, {"op": "describe", "start_timestamp": 107.5, "timestamp": 108.4},
            {"op": "es.associate", "start_timestamp": 108.4, "timestamp": 108.7}, {"op": "git.commit", "start_timestamp": 108.7, "timestamp": 108.8},
            {"op": "broken", "start_timestamp": None, "timestamp": 1}]}, {"type": "message"}]})
    if path.endswith("/events/bb22/"):
        return httpx.Response(200, json={"entries": [{"type": "spans", "data": [{"op": "capture", "start_timestamp": 100.0, "timestamp": 101.6}]}]})
    if path.endswith("/issues/"):
        assert request.url.params["query"] == "capture_id:cap_0004"
        return httpx.Response(200, json=ISSUE)
    return httpx.Response(404, json={})


def test_trace_becomes_a_stage_waterfall_in_pipeline_order():
    c = sc.SentryClient(LIVE, transport=httpx.MockTransport(_trace_handler))
    w = run(c.trace_summary(TRACE))
    assert [s["stage"] for s in w["stages"]] == ["capture", "depth", "segment", "describe", "es.associate", "git.commit"]
    ms = {s["stage"]: s["ms"] for s in w["stages"]}
    assert ms["depth"] == 2800.0 and ms["capture"] == 1600.0 and ms["segment"] == 3100.0
    assert w["total_ms"] == 8800.0 and w["spans"] == 7 and w["transactions"] == 2      # the span without timestamps is skipped
    assert w["url"] == f"https://na-alh.sentry.io/performance/trace/{TRACE}/"
    assert sc.summarise([]) == {"stages": [], "total_ms": None, "spans": 0}


# the shape organizations/{org}/trace/{id}/ answered on 2026-09-19 for cap_82093 (timestamps shortened)
LIVE_TREE = [{"op": "room.status", "description": "room status [cap_82093]", "is_transaction": True, "event_id": "t1", "parent_span_id": None,
              "start_timestamp": 94.411, "end_timestamp": 94.805, "errors": [], "children": [
    {"op": "robot.capture", "description": "3x stereo grab/retrieve", "event_id": "s1", "parent_span_id": "t1", "start_timestamp": 94.412, "end_timestamp": 94.496, "children": []},
    {"op": "perception.sgbm", "description": "disparity x3", "event_id": "s2", "parent_span_id": "t1", "start_timestamp": 94.496, "end_timestamp": 94.621, "children": []},
    {"op": "no.clock", "event_id": "s3", "parent_span_id": "t1", "children": []}]}]


def test_trace_spans_come_flat_on_the_wall_clock_from_the_endpoint_that_answers_live():
    seen = []

    def http(request):
        seen.append(request.url.path)
        return httpx.Response(200, json=LIVE_TREE) if request.url.path.endswith(f"/organizations/na-alh/trace/{TRACE}/") else httpx.Response(404)
    c = sc.SentryClient(LIVE, transport=httpx.MockTransport(http))
    spans = run(c.trace_spans(TRACE))
    assert [(x["op"], x["depth"], x["is_transaction"]) for x in spans] == [("room.status", 0, True), ("robot.capture", 1, False), ("perception.sgbm", 1, False)]
    assert spans[1]["start"] == 94.412 and spans[1]["end"] == 94.496 and spans[1]["parent_span_id"] == "t1"
    w = run(c.trace_summary(TRACE))                           # the waterfall counts the LEAVES: a parent's time is its children's
    assert [(x["stage"], x["ms"]) for x in w["stages"]] == [("robot.capture", 84.0), ("perception.sgbm", 125.0)] and w["transactions"] == 1
    assert not any("events-trace" in p for p in seen), "the older endpoint is only the fallback"


def test_issues_are_found_by_the_capture_tag():
    c = sc.SentryClient(LIVE, transport=httpx.MockTransport(_trace_handler))
    iss = run(c.issues_for_capture("cap_0004"))
    assert iss[0]["short_id"] == "GITSPACE-3" and iss[0]["permalink"].endswith("/7741490949/")


def test_snapshot_issue_pulls_a_capture_id_out_of_the_title_and_intifies_count():
    row = sc.snapshot_issue({"id": 774, "shortId": "GITSPACE-12", "title": "robot: grasp_slipped — cap_82093",
                             "count": "8", "lastSeen": "2026-09-19T12:00:00Z", "permalink": "https://example/i"})
    assert row["id"] == "774" and row["count"] == 8 and row["capture_id"] == "cap_82093"
    assert sc.snapshot_issue({"id": "1", "title": "CancelledError", "count": 1})["capture_id"] is None


def test_recent_issues_are_unresolved_and_issue_tags_join_a_capture():
    seen = []

    def http(request):
        seen.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/issues/") and request.url.params.get("query") == "is:unresolved":
            return httpx.Response(200, json=[{"id": "7741817625", "shortId": "GITSPACE-9", "title": "robot: fell_over",
                                              "count": 3, "permalink": "https://na-alh.sentry.io/issues/7741817625/"}])
        if request.url.path.endswith("/issues/7741817625/events/latest/"):
            return httpx.Response(200, json={"tags": [{"key": "capture_id", "value": "cap_0912"},
                                                      {"key": "role", "value": "laptop"}]})
        return httpx.Response(404, json={})

    c = sc.SentryClient(LIVE, transport=httpx.MockTransport(http))
    iss = run(c.recent_issues())
    assert iss[0]["short_id"] == "GITSPACE-9" and iss[0]["count"] == 3 and iss[0]["capture_id"] is None
    tags = run(c.issue_tags("7741817625"))
    assert tags == {"capture_id": "cap_0912", "role": "laptop"}
    assert seen[0][2]["query"] == "is:unresolved"
    with pytest.raises(sc.SentryError) as e:
        run(c.issue_tags("nope"))
    assert e.value.code == "bad_request" and c.calls == 2


def test_bad_ids_never_reach_the_network():
    c = client([])
    for call in (c.trace_summary("../../etc"), c.issues_for_capture("cap_1; DROP")):
        with pytest.raises(sc.SentryError) as e:
            run(call)
        assert e.value.code == "bad_request" and e.value.status == 422
    assert c.calls == 0


@pytest.mark.parametrize("responses, code, status, retryable, calls", [
    ([httpx.Response(503), httpx.Response(503)], "sentry_error", 502, True, 2),
    ([httpx.Response(429), httpx.Response(429)], "sentry_rate_limited", 503, True, 2),
    ([httpx.Response(401, json={})], "sentry_auth", 502, False, 1),
    ([httpx.Response(403, json={"detail": "needs event:write"})], "sentry_forbidden", 502, False, 1),
    ([httpx.Response(404, json={})], "not_found", 404, False, 1),
    ([httpx.ReadTimeout("t"), httpx.ReadTimeout("t")], "sentry_timeout", 504, True, 2),
    ([httpx.Response(200, text="<html>")], "sentry_error", 502, False, 1),
])
def test_errors_map_to_the_2_7_shape_with_one_retry(responses, code, status, retryable, calls):
    c = client(responses)
    with pytest.raises(sc.SentryError) as e:
        run(c.issues_for_capture("cap_0004"))
    assert (e.value.code, e.value.status, e.value.retryable) == (code, status, retryable)
    assert c.calls == calls


def test_retry_after_is_honoured_but_capped():
    slept = []
    c = client([httpx.Response(429, headers={"Retry-After": "30"}), httpx.Response(200, json=[])], slept=slept)
    assert run(c.issues_for_capture("cap_0004")) == [] and c.calls == 2 and slept == [sc.RETRY_CAP_S]
    c = client([httpx.ConnectError("c"), httpx.Response(200, json=[])])
    assert run(c.issues_for_capture("cap_0004")) == [] and c.calls == 2


# ── [ask Seer]: every non-success is a first-class `stumped`; nothing is ever faked ─────────────────

def test_seer_stumped_while_parked_without_any_call():
    c = sc.SentryClient(PARKED, transport=httpx.MockTransport(boom))
    out = run(c.ask_seer("cap_0004"))
    assert out["state"] == "stumped" and out["reason"] == "Sentry is paused until 01:00 to save quota"
    assert out["verdict"] is None and out["verified"] is False and c.calls == 0
    st = c.seer_status()
    assert st["available"] is False and st["verified"] is False and st["credits"] is None and "paused" in st["credits_reason"]


def test_seer_stumped_when_no_issue_is_tagged():
    out = run(client([httpx.Response(200, json=[])]).ask_seer("cap_0004"))
    assert out["state"] == "stumped" and out["reason"] == "no Sentry issue is tagged with this capture"


# the two free GETs ask_seer makes before it may spend a run — recorded from the live API on 2026-09-19
SETUP = {"integration": {"ok": False, "reason": "integration_missing"}, "seerReposLinked": False, "autofixEnabled": True,
         "setupAcknowledgement": {"orgHasAcknowledged": True, "userHasAcknowledged": True}, "billing": {"hasAutofixQuota": True}}
NO_RUN = {"autofix": None}
PRE = [httpx.Response(200, json=ISSUE), httpx.Response(200, json=SETUP), httpx.Response(200, json=NO_RUN)]
ORG_PATH = "/api/0/organizations/na-alh/issues/7741490949/autofix/"


@pytest.mark.parametrize("post, expect", [
    (httpx.Response(404, json={}), "no autofix endpoint for this issue (HTTP 404 when trying to start the run)"),
    (httpx.Response(403, json={"detail": "You need the event:write or project:write scope"}), "event:write, project:write"),
    (httpx.Response(403, json={"detail": "You do not have permission to perform this action."}), "Sentry said: You do not have permission"),
    (httpx.ReadTimeout("t"), "Sentry did not answer in time (start)"),
    (httpx.Response(503), "sentry_error"),
])
def test_seer_stumped_on_the_start_call(post, expect):
    seen = []
    c = client(PRE + [post], seen=seen)
    out = run(c.ask_seer("cap_0004"))
    assert out["state"] == "stumped" and expect in out["reason"], out["reason"]
    assert out["issue"]["short_id"] == "GITSPACE-3" and out["verdict"] is None
    assert [r.method for r in seen] == ["GET", "GET", "GET", "POST"], "a POST starts a billed run: it must never be retried"
    # the ORG-scoped path: the bare /issues/<id>/autofix/ that docs/26 assumed answers 404 on the live API
    assert seen[1].url.path == ORG_PATH + "setup/" and seen[2].url.path == ORG_PATH and seen[3].url.path == ORG_PATH


@pytest.mark.parametrize("setup, expect", [
    ({**SETUP, "autofixEnabled": False}, "Seer is switched off for this Sentry organisation"),
    ({**SETUP, "billing": {"hasAutofixQuota": False}}, "the Sentry plan has no Seer quota left"),
])
def test_seer_never_bills_a_run_it_cannot_do(setup, expect):
    seen = []
    out = run(client([httpx.Response(200, json=ISSUE), httpx.Response(200, json=setup)], seen=seen).ask_seer("cap_0004"))
    assert out["state"] == "stumped" and out["reason"] == expect and out["setup"]["blocker"] == expect
    assert [r.method for r in seen] == ["GET", "GET"]


def test_seer_setup_reads_the_live_shape():
    got = run(client([httpx.Response(200, json=SETUP)]).seer_setup("7741490949"))
    assert got["blocker"] is None and got["enabled"] is True and got["quota"] is True
    assert got["caveat"] == "Seer cannot read our code: no code integration (integration_missing) and no repository linked to Seer"
    with pytest.raises(sc.SentryError):
        run(client([]).seer_setup("../../etc"))


def test_seer_verdict_after_polling():
    slept, seen = [], []
    done = {"autofix": {"run_id": 42, "status": "COMPLETED", "steps": [
        {"type": "root_cause_analysis", "causes": [{"title": "The robot was mid-recovery", "description": "tilt_rate peaked 200 ms before the shutter"}]},
        {"type": "solution", "description": "Wait for tilt_rate < 0.05 rad/s before latching."}]}}
    c = client(PRE + [httpx.Response(202, json={"run_id": 42}), httpx.Response(200, json=NO_RUN),      # not visible yet: keep waiting
                      httpx.Response(200, json={"autofix": {"run_id": 42, "status": "PROCESSING"}}), httpx.Response(200, json=done)],
               seen=seen, slept=slept)
    out = run(c.ask_seer("cap_0004", context_depth=20, poll_s=2.0))
    assert out["state"] == "verdict" and out["reason"] is None and out["verified"] is False
    assert "mid-recovery" in out["verdict"] and "Wait for tilt_rate" in out["verdict"]
    assert out["run"] == {"run_id": 42, "status": "COMPLETED", "reused": False} and slept == [2.0, 2.0]
    assert b"20 telemetry breadcrumbs" in seen[3].content and b'"stopping_point":"root_cause"' in seen[3].content.replace(b" ", b"")


def test_seer_reads_a_run_that_already_exists_instead_of_buying_another():
    seen = []
    rests = {"autofix": {"run_id": 7, "status": "WAITING_FOR_USER_RESPONSE", "steps": [
        {"type": "root_cause_analysis", "causes": [{"description": "the shutter latched mid-lean"}]}]}}
    out = run(client([httpx.Response(200, json=ISSUE), httpx.Response(200, json=SETUP), httpx.Response(200, json=rests)], seen=seen).ask_seer("cap_0004"))
    assert out["state"] == "verdict" and "mid-lean" in out["verdict"] and out["run"]["reused"] is True
    assert [r.method for r in seen] == ["GET", "GET", "GET"]
    # ...but a run that DIED is not an answer: asking again starts a fresh one
    seen = []
    dead = {"autofix": {"run_id": 7, "status": "ERROR"}}
    out = run(client([httpx.Response(200, json=ISSUE), httpx.Response(200, json=SETUP), httpx.Response(200, json=dead),
                      httpx.Response(202, json={"run_id": 8}), httpx.Response(200, json=rests)], seen=seen).ask_seer("cap_0004"))
    assert out["state"] == "verdict" and out["run"]["reused"] is False and [r.method for r in seen].count("POST") == 1


def test_seer_stumped_when_the_run_says_nothing_readable_or_never_finishes():
    c = client(PRE + [httpx.Response(202, json={}), httpx.Response(200, json={"autofix": {"status": "COMPLETED", "steps": []}})])
    out = run(c.ask_seer("cap_0004"))
    assert out["state"] == "stumped" and "nothing readable" in out["reason"] and "status, steps" in out["reason"]
    assert "Seer cannot read our code" in out["reason"], "the setup caveat is the likeliest why: say it"
    c = client(PRE + [httpx.Response(202, json={})] + [httpx.Response(200, json={"autofix": {"status": "PROCESSING"}})] * 4)
    out = run(c.ask_seer("cap_0004", poll_s=2.0, max_wait_s=4.0))
    assert out["state"] == "stumped" and "still processing after 4 s" in out["reason"]
    assert run(client([]).ask_seer("not a capture"))["state"] == "stumped"
