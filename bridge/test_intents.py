"""bridge/intents.py: our caretaker grammar, the strict Intent schema, and Andrew's intent service as a
fallback that is validated and never trusted (plan/roommate/03-interfaces.md §12). Loopback only."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bridge import intents
from bridge.intents import IntentError, parse, validate

DEMO = [
    ("Where are my keys?", "find", {"object_query": "keys"}),
    ("where did I leave my keys", "find", {"object_query": "keys"}),
    ("Hey robot, where's the mug?", "find", {"object_query": "mug"}),
    ("have you seen my wallet", "find", {"object_query": "wallet"}),
    ("point at mug_a1b2", "point", {"object_id": "mug_a1b2", "object_query": None}),
    ("show me the scissors", "point", {"object_query": "scissors"}),
    ("clean up the desk", "tidy", {"zone": "desk"}),
    ("tidy up", "tidy", {"zone": None}),
    ("put everything back", "tidy", {"zone": None}),
    ("put the lamp on the shelf", "move", {"object_query": "lamp", "zone": "shelf"}),
    ("is the room clean?", "status", {}),
    ("what changed", "status", {}),
    ("who moved my mug?", "blame", {"object_query": "mug"}),
    ("put the room back like it was before dinner", "restore_time", {"when": "before dinner"}),
]
NOT_OURS = ["set my room back to study mode", "restore study-mode", "revert HEAD", "make me a sandwich",
            "put it back", "commit morning", "log", ""]


@pytest.mark.parametrize("text,intent,fields", DEMO)
def test_the_caretaker_grammar(text, intent, fields):
    i = parse(text, "r1")
    assert i is not None and i["intent"] == intent and i["source"] == "grammar" and i["confidence"] == 1.0
    assert i["raw_text"] == text.strip() and i["request_id"] == "r1"
    for k, v in fields.items():
        assert i[k] == v, (text, k, i[k])


@pytest.mark.parametrize("text", NOT_OURS)
def test_what_is_not_ours_is_left_alone(text):
    assert parse(text, "r1") is None, "named states, graph verbs and nonsense go elsewhere"


def good(**kw) -> dict:
    return {"request_id": "r1", "intent": "find", "object_query": "keys", "object_id": None, "zone": None,
            "when": None, "raw_text": "where are my keys", "confidence": 0.9, "source": "openai", **kw}


@pytest.mark.parametrize("bad", [
    good(extra="nope"),                                   # strict: no extra keys
    {k: v for k, v in good().items() if k != "zone"},     # every key present, null or not
    good(intent="dance"), good(source="gpt"), good(confidence=1.5),
    good(object_query=None),                              # find needs something to find
    good(intent="move", zone=None),                       # move needs a destination
    good(intent="restore_time", object_query=None),       # restore_time needs a time
    good(object_id="Not An Id"), good(zone="the desk"),
])
def test_the_schema_is_strict(bad):
    with pytest.raises(IntentError) as e:
        validate(bad)
    assert e.value.code == "intent_invalid"


class _Service:
    def __init__(self, answer, code=200):
        svc = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                svc.got = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                raw = json.dumps(answer(svc.got) if callable(answer) else answer).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                return
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def test_his_intent_service_is_asked_only_by_url_and_validated(monkeypatch):
    monkeypatch.delenv("ANDREW_INTENT_URL", raising=False)
    assert intents.from_service("the thing I cut paper with", "r9") is None, "not configured: not asked"
    svc = _Service(lambda got: good(request_id=got["request_id"], object_query="scissors",
                                    raw_text=got["text"]))
    try:
        monkeypatch.setenv("ANDREW_INTENT_URL", svc.url)
        i = intents.from_service("the thing I cut paper with", "r9")
        assert i["intent"] == "find" and i["object_query"] == "scissors" and svc.got == {"text": "the thing I cut paper with", "request_id": "r9"}
    finally:
        svc.close()


@pytest.mark.parametrize("answer,code,expect", [
    (good(request_id="someone-else"), 200, "intent_invalid"),       # an answer for another request
    (good(source="grammar"), 200, "intent_invalid"),                 # it cannot claim to be our grammar
    (good(extra=1), 200, "intent_invalid"),
    ({"error": "boom"}, 500, "intent_unavailable"),
])
def test_a_bad_answer_is_refused_never_repaired(monkeypatch, answer, code, expect):
    svc = _Service(answer, code)
    try:
        monkeypatch.setenv("ANDREW_INTENT_URL", svc.url)
        with pytest.raises(IntentError) as e:
            intents.from_service("where are my keys", "r1")
        assert e.value.code == expect
    finally:
        svc.close()


def test_his_refusal_is_no_intent(monkeypatch):
    svc = _Service({"error": "refused", "detail": "not a household request"}, 422)
    try:
        monkeypatch.setenv("ANDREW_INTENT_URL", svc.url)
        assert intents.from_service("write me a poem", "r1") is None
    finally:
        svc.close()


def test_an_unreachable_service_is_an_outage(monkeypatch):
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    monkeypatch.setenv("ANDREW_INTENT_URL", f"http://127.0.0.1:{dead}")
    with pytest.raises(IntentError) as e:
        intents.from_service("where are my keys", "r1")
    assert e.value.code == "intent_unavailable"


@pytest.mark.parametrize("text,fields", [
    ("why was this diff wrong", {"ref": None}),
    ("Why is that capture bad?", {"ref": None}),
    ("why was 1a668ec wrong", {"ref": "1a668ec"}),
])
def test_why_is_a_caretaker_question_too(text, fields):
    i = parse(text, "r1")
    assert i is not None and i["intent"] == "why" and i["object_query"] is None
    assert i.get("ref") == fields["ref"]


def test_a_time_phrase_is_restore_time_and_a_state_name_is_not():
    i = parse("put the room back the way it was 2 hours ago", "r1")
    assert i["intent"] == "restore_time" and i["when"] == "2 hours ago"
    assert parse("set my room back to study mode", "r1") is None, "a named state is not a moment"


@pytest.mark.parametrize("text", [
    "put it back the way it was before dinner",          # the literal sentence in MVP-NOW.md beat 4
    "put it back the way it was 2 hours ago",
    "put things back the way it was this morning",
    "put the room back the way it was 2 hours ago",
])
def test_the_runbook_sentences_are_moments(text):
    i = parse(text, "r1")
    assert i is not None and i["intent"] == "restore_time" and i["when"]


def test_put_it_back_somewhere_is_still_a_move():
    i = parse("put it back on the shelf", "r1")
    assert i["intent"] == "move" and i["zone"] == "shelf" and i["when"] is None


def test_without_jsonschema_our_own_grammar_still_answers_and_a_foreign_intent_is_refused(monkeypatch):
    """The cloud tier had no `jsonschema`, so every sentence in the panel answered HTTP 500. A missing
    dependency is a deployment fact: our own Intents (built here, field by field) are checked structurally
    and go through; one from another service cannot be fully checked, so it is refused, never trusted."""
    monkeypatch.setattr(intents, "_validator", lambda: None)
    i = parse("where are my keys", "r1")
    assert i is not None and i["intent"] == "find" and i["object_query"] == "keys"
    with pytest.raises(IntentError) as e:                      # the structural check still bites
        intents.validate({**good(source="grammar"), "extra": 1})
    assert e.value.code == "intent_invalid"
    with pytest.raises(IntentError) as e:
        intents.validate({k: v for k, v in good().items() if k != "zone"})
    assert "required" in e.value.message
    svc = _Service(lambda got: good(request_id=got["request_id"]))
    try:
        monkeypatch.setenv("INTENT_URL", svc.url)
        with pytest.raises(IntentError) as e:
            intents.from_service("the thing I cut paper with", "r1")
        assert e.value.code == "intent_unavailable" and "jsonschema" in e.value.message
    finally:
        svc.close()


# ── the gate on the model path: the only thing between a public endpoint and a live key ──────────

@pytest.fixture()
def gate(monkeypatch):
    """A fresh gate, and a service that FAILS if it is ever reached — so 'blocked' means 'not called'."""
    intents._recent.clear()
    intents._day[0] = intents._day[1] = 0
    for k in intents._blocked:
        intents._blocked[k] = 0
    for name in ("INTENT_OFF", "INTENT_DAILY_CAP", "INTENT_PER_IP_PER_HOUR", "INTENT_MAX_CHARS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("INTENT_URL", "http://127.0.0.1:1")      # nothing listens: a call would raise
    return intents


def test_the_kill_switch_stops_the_model_path_and_nothing_else(gate, monkeypatch):
    monkeypatch.setenv("INTENT_OFF", "1")
    assert gate.from_service("something only a model could read", "r1") is None, "no call, no error"
    assert gate.gate_stats()["blocked"]["off"] == 1 and gate.gate_stats()["off"] is True
    assert parse("where are my keys", "r1")["intent"] == "find", "the grammar is untouched by the switch"


def test_the_daily_cap_is_a_money_guard_and_holds_when_spent(gate, monkeypatch):
    monkeypatch.setenv("INTENT_DAILY_CAP", "2")
    for _ in range(2):
        with pytest.raises(IntentError):                        # allowed through: the fake URL then fails
            gate.from_service("a sentence", "r1", who="1.2.3.4")
    assert gate.gate_stats()["calls_today"] == 2
    assert gate.from_service("a sentence", "r1", who="1.2.3.4") is None, "spent: declined, not an error"
    assert gate.from_service("a sentence", "r2", who="9.9.9.9") is None, "the cap is for everyone"
    assert gate.gate_stats()["blocked"]["daily"] == 2


def test_one_caller_cannot_use_the_whole_cap(gate, monkeypatch):
    monkeypatch.setenv("INTENT_PER_IP_PER_HOUR", "1")
    with pytest.raises(IntentError):
        gate.from_service("a sentence", "r1", who="1.2.3.4")
    assert gate.from_service("a sentence", "r2", who="1.2.3.4") is None
    assert gate.gate_stats()["blocked"]["per_ip"] == 1
    with pytest.raises(IntentError):                            # a different caller still gets through
        gate.from_service("a sentence", "r3", who="5.6.7.8")


def test_a_paragraph_is_refused_before_it_is_paid_for(gate, monkeypatch):
    monkeypatch.setenv("INTENT_MAX_CHARS", "40")
    assert gate.from_service("x" * 41, "r1") is None
    assert gate.gate_stats()["blocked"]["too_long"] == 1 and gate.gate_stats()["calls_today"] == 0


def test_the_counters_are_countable_and_carry_no_text(gate, monkeypatch, caplog):
    monkeypatch.setenv("INTENT_OFF", "1")
    with caplog.at_level("WARNING"):
        gate.from_service("my coffee cup has gone missing", "r1", who="1.2.3.4")
    line = caplog.text
    assert "intent gate" in line and "coffee cup" not in line, "a log line must never carry the sentence"
    st = gate.gate_stats()
    assert set(st) >= {"off", "calls_last_hour", "calls_today", "daily_cap", "per_ip_per_hour", "blocked"}
