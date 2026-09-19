"""robot/events.py — GET /events, the Pi's structured stream as Server-Sent Events.

What is proved: the framing a browser's EventSource and a bare `curl -N` both parse; ids that
count up by one so Last-Event-ID resumes with no gap and no duplicate; that a Pi RESTART is
detected rather than resumed across; the keepalive comment on an idle connection; and that a
client which stops reading costs nobody else anything. No socket is opened.
"""
import asyncio
import contextlib
import json
import sys
import threading
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import obs  # noqa: E402
from robot import config as C  # noqa: E402
from robot import events as sse  # noqa: E402
from robot import server  # noqa: E402

HELLO = {"t": "hello", "boot_id": "b00t", "t_mono_base": 8000.0, "t_wall_base": 1789780927.4}


@pytest.fixture(autouse=True)
def no_live_sentry(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "")


def parse(text):
    """SSE as a client reads it -> ([{id, event, data}], [comments], retry_ms)."""
    events, comments, retry = [], [], None
    for block in text.split("\n\n"):
        ev, data = {}, []
        for line in block.split("\n"):
            if line.startswith(":"):
                comments.append(line[1:].strip())
            elif line.startswith("retry:"):
                retry = int(line[6:])
            elif ":" in line:
                k, v = line.split(":", 1)
                if k == "data":
                    data.append(v[1:] if v.startswith(" ") else v)
                else:
                    ev[k] = v.strip()
        if data:
            events.append({**ev, "data": json.loads("\n".join(data))})
    return events, comments, retry


def msg(t, **kw):
    return json.dumps({"t": t, **kw}, separators=(",", ":"))


async def take(gen, n):
    out = []
    async for chunk in gen:
        out.append(chunk)
        if len(out) >= n:
            break
    await gen.aclose()
    return "".join(out)


def filled(n=10, **kw):
    log = sse.EventLog("b00t", **kw)

    async def go():
        for i in range(n):
            await log.ingest(msg("job" if i % 2 else "telemetry", i=i))
    asyncio.run(go())
    return log


# ── the framing ──────────────────────────────────────────────────────────────────
def test_one_event_is_id_event_data_and_a_blank_line():
    assert sse.frame("job", '{"id":"job_1"}', "b00t:7") == 'id: b00t:7\nevent: job\ndata: {"id":"job_1"}\n\n'
    assert sse.frame("hello", "{}") == "event: hello\ndata: {}\n\n"            # no id: not part of the log
    two = sse.frame("log", '{"msg":\n"x"}', "b:1")                               # a newline would END a data field
    assert two == 'id: b:1\nevent: log\ndata: {"msg":\ndata: "x"}\n\n' and parse(two)[0][0]["data"] == {"msg": "x"}


def test_a_connection_opens_with_retry_then_a_hello_that_has_no_id():
    log = filled(0)
    events, _, retry = parse(asyncio.run(take(log.subscribe(HELLO), 2)))
    assert retry == 2000
    (hello,) = events
    assert hello["event"] == "hello" and "id" not in hello
    assert hello["data"]["t_mono_base"] == 8000.0 and hello["data"]["transport"] == "sse"   # the clock pairing


def test_ids_count_up_by_one_and_the_event_name_is_the_messages_t():
    log = filled(6)
    events, _, _ = parse(asyncio.run(take(log.subscribe(HELLO, last_event_id="0"), 8)))
    assert [e["id"] for e in events[1:]] == [f"b00t:{n}" for n in range(1, 7)]
    assert [e["event"] for e in events[1:]] == ["telemetry", "job"] * 3
    assert all(e["data"]["t"] == e["event"] for e in events)


# ── Last-Event-ID ────────────────────────────────────────────────────────────────
def test_resuming_replays_exactly_what_was_missed_then_goes_live():
    log = filled(10)

    async def go():
        gen = log.subscribe(HELLO, last_event_id="b00t:7")
        got = [await gen.__anext__() for _ in range(5)]              # retry, hello, 8, 9, 10
        await log.ingest(msg("job", i=10))                            # and the live one joins without a seam
        got.append(await gen.__anext__())
        await gen.aclose()
        return "".join(got)
    events, _, _ = parse(asyncio.run(go()))
    assert [e.get("id") for e in events] == [None, "b00t:8", "b00t:9", "b00t:10", "b00t:11"]
    assert [e["data"].get("i") for e in events[1:]] == [7, 8, 9, 10]                       # no gap, no duplicate


def test_a_fresh_client_gets_live_only():
    log = filled(10)

    async def go():
        gen = log.subscribe(HELLO)
        head = [await gen.__anext__() for _ in range(2)]
        await log.ingest(msg("log", msg="now"))
        head.append(await gen.__anext__())
        await gen.aclose()
        return "".join(head)
    events, _, _ = parse(asyncio.run(go()))
    assert [e.get("id") for e in events] == [None, "b00t:11"]


def test_a_pi_restart_is_detected_not_resumed_across():
    """The client last saw 5000 of the OLD boot. A bare integer would resume at 5001 of a run that
    has only reached 10 — or, worse, of one that has passed it — and skip what it never saw."""
    log = filled(10)
    events, _, _ = parse(asyncio.run(take(log.subscribe(HELLO, last_event_id="0ldb00t:5000"), 13)))
    assert events[0]["event"] == "hello" and events[0]["data"]["boot_id"] == "b00t"        # the NEW boot, said first
    assert events[1]["event"] == "gap" and events[1]["data"]["reason"] == "boot_changed"
    assert [e["id"] for e in events[2:]] == [f"b00t:{n}" for n in range(1, 11)]            # all of the new run
    for garbage in ("5000", "b00t:", "b00t:x", ":::"):
        ev, _, _ = parse(asyncio.run(take(log.subscribe(HELLO, last_event_id=garbage), 3)))
        assert ev[1]["event"] == "gap"                                                     # never a wrong resume


def test_a_hole_the_log_no_longer_covers_is_said():
    log = filled(10, keep=4)                                          # only 7..10 are still kept
    events, _, _ = parse(asyncio.run(take(log.subscribe(HELLO, last_event_id="b00t:2"), 7)))
    assert events[1]["event"] == "gap"
    assert events[1]["data"] == {"reason": "log_overrun", "missed_from": 3, "missed_to": 6}
    assert [e["id"] for e in events[2:]] == ["b00t:7", "b00t:8", "b00t:9", "b00t:10"]


# ── filter, heartbeat, a client that stops reading ───────────────────────────────
def test_types_filters_events_and_ids_keep_their_true_numbers():
    log = filled(10)
    want = frozenset({"job"})
    events, _, _ = parse(asyncio.run(take(log.subscribe(HELLO, "0", want, limit=5), 99)))
    assert [e["event"] for e in events] == ["job"] * 5                # no hello either: it was not asked for
    assert [e["id"] for e in events] == ["b00t:2", "b00t:4", "b00t:6", "b00t:8", "b00t:10"]


def test_an_idle_connection_gets_a_keepalive_comment():
    log = filled(0)
    text = asyncio.run(take(log.subscribe(HELLO, heartbeat_s=0.05), 5))
    events, comments, _ = parse(text)
    assert comments == ["keepalive"] * 3 and len(events) == 1         # comments carry no id and no event
    assert ": keepalive\n\n" in text


def test_a_client_that_stops_reading_costs_nobody_else_anything():
    log = sse.EventLog("b00t", client_queue=5)

    async def go():
        stalled, reader = log.subscribe(HELLO), log.subscribe(HELLO)
        for g in (stalled, reader):
            await g.__anext__()                                       # both connected ...
            await g.__anext__()
        first = asyncio.ensure_future(reader.__anext__())
        await asyncio.sleep(0)
        t0 = time.perf_counter()
        for i in range(100):                                          # ... and only `reader` ever reads again
            await log.ingest(msg("telemetry", i=i))
            if i == 0:
                got = [await first]
            else:
                got.append(await reader.__anext__())
        took = time.perf_counter() - t0
        await stalled.aclose()
        await reader.aclose()
        return "".join(got), took
    text, took = asyncio.run(go())
    assert [e["data"]["i"] for e in parse(text)[0]] == list(range(100))      # the reader missed nothing
    assert log.dropped == 95 and took < 1.0                                   # the stalled one dropped its oldest


def test_open_and_close_are_obs_spans_with_what_happened(monkeypatch):
    spans = []

    @contextlib.contextmanager
    def span(op, desc="", **data):
        spans.append((op, data))
        yield None
    monkeypatch.setattr(obs, "span", span)
    log = filled(10)
    # retry + events 9 and 10. (No hello: it was not among the types asked for.)
    asyncio.run(take(log.subscribe(HELLO, last_event_id="b00t:8", types=frozenset({"job", "telemetry"})), 3))
    (op1, opened), (op2, closed) = spans
    assert op1 == "robot.sse.open" and opened["resumed"] is True and opened["replayed"] == 2
    assert opened["types"] == "job,telemetry" and opened["clients"] == 1
    assert op2 == "robot.sse.close" and closed["events"] == 2 and closed["heartbeats"] == 0
    assert log.stats()["clients"] == 0                                # and the client is gone from the fan-out


# ── over HTTP, the whole server in sim ───────────────────────────────────────────
@pytest.fixture
def client(tmp_path):
    app = server.create_app(C.Config(mode="sim", state_dir=tmp_path), time_scale=0.0, sse_heartbeat_s=0.1)
    with TestClient(app) as c:
        time.sleep(0.25)
        yield c


def test_events_is_text_event_stream_and_curl_can_read_it(client):
    r = client.get("/events?types=hello,telemetry&limit=3")           # curl -sN '<pi>:8080/events?...&limit=3'
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["access-control-allow-origin"] == "*"
    events, _, retry = parse(r.text)
    assert retry == 2000 and [e["event"] for e in events] == ["hello", "telemetry", "telemetry", "telemetry"]
    assert events[0]["data"]["cameras"] == ["cam1", "cam2"] and events[0]["data"]["mode"] == "sim"
    boot = events[0]["data"]["boot_id"]
    ns = [int(e["id"].rpartition(":")[2]) for e in events[1:]]
    assert all(e["id"].startswith(boot + ":") for e in events[1:]) and ns == list(range(ns[0], ns[0] + 3))
    assert len(events[1]["data"]["signals"]["pitch"]) == 5            # the same batch /stream carries


def test_last_event_id_resumes_over_http_by_header_and_by_query(client):
    first, _, _ = parse(client.get("/events?types=telemetry&limit=3").text)
    last = first[-1]["id"]
    n = int(last.rpartition(":")[2])
    time.sleep(0.3)                                                   # the link was down; telemetry kept coming
    again, _, _ = parse(client.get("/events?types=telemetry&limit=2", headers={"Last-Event-ID": last}).text)
    assert [int(e["id"].rpartition(":")[2]) for e in again] == [n + 1, n + 2]
    assert again[0]["data"]["from_mono"] == pytest.approx(first[-1]["data"]["from_mono"] + 0.1, abs=1e-4)
    by_query, _, _ = parse(client.get(f"/events?types=telemetry&limit=1&last_event_id={last}").text)
    assert by_query[0]["id"] == again[0]["id"]


def test_a_capture_and_a_job_arrive_as_named_events(client):
    got = {}
    t = threading.Thread(target=lambda: got.update(
        r=client.get("/events?types=capture_begin,capture_end,job&limit=3")))
    t.start()
    while client.get("/healthz").json()["events"]["clients"] < 1:
        time.sleep(0.01)
    cap = client.post("/capture", json={"frames": 1}).json()
    client.post("/say", json={"text": "hello"})
    t.join(timeout=10)
    events, _, _ = parse(got["r"].text)
    assert [e["event"] for e in events[:2]] == ["capture_begin", "capture_end"] and events[2]["event"] == "job"
    assert events[0]["data"]["capture_id"] == cap["capture_id"] and events[0]["data"]["skew_ms"] == cap["skew_ms"]
    assert "jpeg_b64" not in json.dumps(events[0]["data"])            # pixels never ride the text stream


def test_a_quiet_filtered_stream_is_kept_alive(client):
    got = {}
    t = threading.Thread(target=lambda: got.update(r=client.get("/events?types=job&limit=1")))
    t.start()
    time.sleep(0.45)                                                  # nothing matches `job`: only keepalives
    client.post("/say", json={"text": "hi"})
    t.join(timeout=10)
    events, comments, _ = parse(got["r"].text)
    assert len(comments) >= 3 and set(comments) == {"keepalive"} and events[0]["event"] == "job"


def test_healthz_counts_the_stream(client):
    parse(client.get("/events?limit=2").text)
    e = client.get("/healthz").json()["events"]
    assert e["clients"] == 0 and e["served"] >= 2 and e["dropped"] == 0 and e["last_id"].count(":") == 1


def test_the_websockets_are_still_there(client):
    """SSE is ALONGSIDE: /stream stays telemetry/hub.py's link, /frames stays the binary path."""
    routes = {getattr(r, "path", "") for r in client.app.routes}
    assert {"/events", "/stream", "/frames"} <= routes
    with client.websocket_connect("/stream") as ws:
        assert ws.receive_json()["t"] == "hello"
