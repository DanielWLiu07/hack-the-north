"""The properties docs/23 asks for, asserted. Run: .venv/bin/python -m pytest telemetry -q"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import socket
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("pi_telemetry", ROOT / "robot" / "telemetry.py")
pi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pi)

import obs  # noqa: E402
from telemetry.hub import Batch, Clock, ElasticSink, Hub, SentrySink, Sink, SSESink  # noqa: E402

DT = 1 / 50


def ticks(msgs: list[dict]) -> list[float]:
    return [round(m["from_mono"] + i / m["hz"], 4) for m in msgs for i in range(len(m["signals"]["pitch"]))]


def contiguous(ts: list[float]) -> bool:
    return all(abs((b - a) - DT) < 1e-4 for a, b in zip(ts, ts[1:]))


# ── robot/telemetry.py ──────────────────────────────────────────────────────────
def test_ring_fills_and_caps_with_no_consumer_attached():
    tel = pi.Telemetry(pi.FakeRobot(), ring_s=0.5).start()
    time.sleep(1.0)
    tel.stop()
    s = tel.recent(1000)
    assert len(s) == 25                                   # 0.5 s at 50 Hz, oldest evicted
    assert contiguous([x["t_mono"] for x in s])
    assert set(s[0]) == {"t_mono", *pi.SIGNALS}


def collect(tel, seconds: float, since=None) -> list[dict]:
    got: list[dict] = []

    async def send(text: str) -> None:
        got.append(json.loads(text))

    async def main() -> None:
        try:
            await asyncio.wait_for(tel.stream(send, since), seconds)
        except TimeoutError:
            pass
    asyncio.run(main())
    return got


def test_batches_are_5_samples_at_10_per_second():
    tel = pi.Telemetry(pi.FakeRobot()).start()
    live = [m for m in collect(tel, 1.05) if not m.get("replay")]
    tel.stop()
    assert 9 <= len(live) <= 11
    assert all(len(m["signals"][name]) == 5 for m in live for name in pi.SIGNALS)
    assert contiguous(ticks(live))


def test_replay_meets_live_with_no_gap_or_duplicate():
    tel = pi.Telemetry(pi.FakeRobot()).start()
    time.sleep(1.0)                                       # nobody listening for a second
    since = tel.recent(30)[0]["t_mono"]                   # laptop last saw this sample
    got = collect(tel, 0.6, since=since)
    tel.stop()
    assert any(m.get("replay") for m in got), "expected a replay of the ring"
    k = [round((t - tel.t_mono_base) * 50) for t in ticks(got)]      # exact tick indices
    assert len(k) == len(set(k)), "a sample was sent twice across the seam"
    # nothing the Pi recorded after `since` is missing. (A tick the Pi skipped under load --
    # tel.overruns -- is honestly absent from both sides; the tap never invents samples.)
    since_k = round((since - tel.t_mono_base) * 50)
    recorded = [round((s["t_mono"] - tel.t_mono_base) * 50) for s in tel.recent(10_000)]
    assert k == [x for x in recorded if since_k < x <= k[-1]], f"Pi overruns: {tel.overruns}"


def test_live_and_replayed_copies_of_a_sample_get_the_same_timestamp():
    """What makes replay idempotent in the TSDS: (signal, @timestamp) must match exactly."""
    tel = pi.Telemetry(pi.FakeRobot()).start()
    time.sleep(0.5)
    tel.stop()
    ring = list(tel._ring)
    live = [json.loads(m) for i in range(0, 20, 5) for m in tel._encode_runs(ring[i:i + 5], False)]
    replay = [json.loads(m) for m in tel._encode_runs(ring[3:18], True)]     # different grouping
    c = Clock()
    c.on_hello(tel.hello(), time.time())

    def ms(msgs):
        return {round((t + c.offset) * 1000) for t in ticks(msgs)}
    assert ms(replay) <= ms(live) and len(ms(replay)) == 15


def test_window_and_peak_answer_after_the_fact():
    tel = pi.Telemetry(pi.FakeRobot()).start()
    time.sleep(0.6)
    tel.stop()
    mid = tel.recent(1000)[10]["t_mono"]
    w = tel.window(mid - 0.1, mid + 0.1)
    assert len(w) == 11
    assert tel.peak("tilt_rate", mid - 0.1, mid + 0.1) == max(abs(x["tilt_rate"]) for x in w)


def test_publish_rides_the_same_socket():
    tel = pi.Telemetry(pi.FakeRobot()).start()

    async def main() -> list[dict]:
        got: list[dict] = []

        async def send(text: str) -> None:
            got.append(json.loads(text))
        task = asyncio.create_task(tel.stream(send))
        await asyncio.sleep(0.2)
        tel.publish({"t": "job", "id": "job_1", "state": "done"})
        await asyncio.sleep(0.2)
        task.cancel()
        return got
    got = asyncio.run(main())
    tel.stop()
    assert {"t": "job", "id": "job_1", "state": "done"} in got


# ── telemetry/hub.py ────────────────────────────────────────────────────────────
def batch(i: int, n: int = 5, balanced: float = 1.0, replay: bool = False) -> Batch:
    mono = tuple(100 + (i * n + j) * DT for j in range(n))
    sig = {"pitch": (0.01,) * n, "tilt_rate": (0.002,) * n, "balanced": (balanced,) * n}
    return Batch(mono, tuple(1.8e9 + m for m in mono), sig, replay)


class Recorder(Sink):
    name = "rec"

    def __init__(self, name):
        super().__init__(maxsize=100)
        self.name, self.handled = name, 0

    async def handle(self, b):
        self.handled += 1


class Hangs(Sink):
    name = "hangs"

    async def handle(self, b):
        await asyncio.Event().wait()                      # never returns


class Throws(Sink):
    name = "throws"

    async def handle(self, b):
        raise RuntimeError("sink on fire")


def test_a_hung_sink_and_a_throwing_sink_stall_nobody():
    a, b, hung, bad = Recorder("a"), Recorder("b"), Hangs(maxsize=10), Throws(maxsize=10)
    hub = Hub("ws://unused", [a, hung, bad, b])
    hub.clock.offset = 0.0

    async def main() -> float:
        tasks = [asyncio.create_task(s.run()) for s in hub.sinks.values()]
        t0 = time.perf_counter()
        for i in range(500):                              # 50 s of telemetry, as fast as possible
            hub._dispatch(json.dumps({"t": "telemetry", "from_mono": 100 + i * 0.1, "hz": 50,
                                      "signals": {"pitch": [0.0] * 5}}))
            if i % 20 == 0:
                await asyncio.sleep(0)
        elapsed = time.perf_counter() - t0
        await asyncio.sleep(0.2)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return elapsed
    elapsed = asyncio.run(main())
    assert a.handled == b.handled == 500                  # the healthy sinks got everything
    # every batch the broken sinks were offered is accounted for in THEIR OWN counters
    assert hung.dropped + hung.q.qsize() + 1 == 500       # +1: the one it is stuck on
    assert bad.errors + bad.dropped == 500 and bad.errors > 0
    assert elapsed < 0.5                                  # fan-out never waited on anyone


def test_es_docs_match_the_strict_mapping():
    docs = ElasticSink.docs(Batch((1.0, 1.02), (1789779300.0, 1789779300.02),
                                  {"pitch": (0.1, None), "balanced": (1.0, 1.0)}, False))
    assert all(set(d) == {"@timestamp", "signal", "value"} for d in docs)
    assert [d["@timestamp"] for d in docs if d["signal"] == "balanced"] == [1789779300000, 1789779300020]
    assert len(docs) == 3                                 # None is skipped, not sent as null


def test_clock_uses_the_pi_pairing_or_falls_back_when_the_pi_clock_is_wrong():
    now = time.time()
    good = {"boot_id": "a", "t_mono_base": 50.0, "t_wall_base": now - 10, "t_mono_now": 60.0}
    c = Clock()
    assert c.on_hello(good, now) is None and c.offset == pytest.approx(now - 60)
    stale = {"boot_id": "b", "t_mono_base": 50.0, "t_wall_base": now - 86400, "t_mono_now": 60.0}
    assert "off the laptop" in c.on_hello(stale, now)
    off = c.offset
    assert c.on_hello({**stale, "t_mono_now": 70.0}, now + 10.3) is None
    assert c.offset == off                                # same boot: same mapping on reconnect


def test_a_fall_is_reported_once_with_two_seconds_of_lean(monkeypatch):
    calls = []
    monkeypatch.setattr(obs, "robot_failure", lambda kind, detail, telemetry=None, **tags:
                        calls.append((kind, telemetry)))
    s = SentrySink(rearm_s=2.0)

    async def feed(states):
        for i, bal in enumerate(states):
            await s.handle(batch(feed.i, balanced=bal))
            feed.i += 1
    feed.i = 0
    asyncio.run(feed([1.0] * 30 + [0.0] * 5))             # 3 s upright, then down
    assert len(calls) == 1 and calls[0][0] == "fell_over"
    crumbs = calls[0][1]
    assert len(crumbs) <= 40 and crumbs[-1]["t_mono"] - crumbs[0]["t_mono"] >= 1.9
    asyncio.run(feed([1.0] * 5 + [0.0] * 5))              # 0.5 s up: not re-armed yet
    assert len(calls) == 1
    asyncio.run(feed([1.0] * 25 + [0.0] * 5))             # 2.5 s up: armed, falls again
    assert len(calls) == 2


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_end_to_end_reconnect_leaves_no_hole(tmp_path, monkeypatch):
    """Fake Pi -> hub -> spool/ring/SSE; kill the Pi's server for ~1 s mid-stream."""
    port = free_port()
    tel = pi.Telemetry(pi.FakeRobot()).start()
    es = ElasticSink(tmp_path / "spool")

    async def no_es():
        return None
    monkeypatch.setattr(es, "_client", no_es)            # ES down: everything must spool
    sentry, sse = SentrySink(), SSESink()
    hub = Hub(f"ws://127.0.0.1:{port}/stream", [es, sentry, sse])
    frames: list[dict] = []

    async def browser():
        async for f in sse.subscribe():
            frames.append(json.loads(f))

    async def main():
        pi_server = asyncio.create_task(pi.serve(tel, "127.0.0.1", port))
        await asyncio.sleep(0.3)
        hub_task = asyncio.create_task(hub.run())
        watcher = asyncio.create_task(browser())
        await asyncio.sleep(1.5)
        pi_server.cancel()                                # wifi drops
        await asyncio.gather(pi_server, return_exceptions=True)
        await asyncio.sleep(1.0)
        pi_server = asyncio.create_task(pi.serve(tel, "127.0.0.1", port))
        await asyncio.sleep(2.5)                          # reconnect (backoff) + replay
        for t in (hub_task, watcher, pi_server):
            t.cancel()
        await asyncio.gather(hub_task, watcher, pi_server, return_exceptions=True)
    asyncio.run(main())
    tel.stop()

    assert hub.connects == 2
    spooled = {json.loads(line)["@timestamp"]
               for f in (tmp_path / "spool").glob("*.ndjson")
               for line in f.read_text().splitlines()
               if json.loads(line)["signal"] == "pitch"}
    # Everything the Pi sampled reached ES, outage included. (A tick the Pi itself skipped
    # under load -- tel.overruns -- is a hole at the source, not a transport loss.)
    sampled = {round((s["t_mono"] + hub.clock.offset) * 1000)       # the hub's mapping, whichever it chose
               for s in tel.recent(10_000) if s["t_mono"] <= hub.last_mono + 1e-6}
    lost = sorted(sampled - spooled)
    assert not lost, f"{len(lost)} samples lost in transport (Pi overruns: {tel.overruns}): {lost[:3]}"
    assert max(spooled) - min(spooled) >= 4000            # spans the outage, ~5 s end to end
    assert len(sentry.ring) > 100 and 4 <= len(frames) <= 14


def test_sse_posts_web_shaped_frames_at_2hz_and_drops_when_web_is_down():
    import httpx
    posted = []

    def inlet(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200 if len(posted) < 3 else 503, json={})
    s = SSESink(post_url="http://127.0.0.1:8000/api/internal/event")
    s._http = httpx.AsyncClient(transport=httpx.MockTransport(inlet))

    async def run():
        for i in range(30):                                   # 3 s of batches, faster than 2 Hz
            b = batch(i)
            try:
                await s.handle(Batch(b.t_mono, b.t_wall, {**b.signals, "tilt_rate": (0.002, 0.3, 0.001, 0, 0)}, False))
            except Exception as e:  # noqa: BLE001 -- what Sink.run would record
                s.fail(e)
            await asyncio.sleep(0.1)
    asyncio.run(run())
    assert 5 <= len(posted) <= 7                              # ~2 Hz, not 10
    ev = posted[0]
    assert ev["event"] == "telemetry" and ev["data"]["ts"].endswith("Z")
    assert ev["data"]["balanced"] is True and ev["data"]["tilt_rate_peak"] == 0.3
    assert s.errors == len(posted) - 2                        # 503s counted and dropped, never raised out


# ── the hub reaching a REMOTE site ────────────────────────────────────────────────────────────────
def test_the_sse_sink_carries_the_cloud_token_to_a_remote_site():
    """The loopback inlet takes anything from localhost. A remote site's /api/edge/event is guarded,
    and without this header it answers 401 — so the only door a hub on another network can reach was
    the one door it could not use."""
    import httpx
    seen = []

    def site(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={})
    s = SSESink(post_url="https://gitirl.health/api/edge/event", token="a-real-looking-token")
    s._http = httpx.AsyncClient(transport=httpx.MockTransport(site))
    asyncio.run(s._post({"ts": "2026-09-20T07:00:00.000Z"}))
    assert seen == ["Bearer a-real-looking-token"]
    assert s.stats()["authenticated"] is True


def test_no_token_means_no_header_at_all():
    """The loopback inlet must keep working untouched for everyone who never sets a token."""
    import httpx
    seen = []

    def inlet(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={})
    s = SSESink(post_url="http://127.0.0.1:8000/api/internal/event", token="")
    s._http = httpx.AsyncClient(transport=httpx.MockTransport(inlet))
    asyncio.run(s._post({"ts": "2026-09-20T07:00:00.000Z"}))
    assert seen == [None] and s.stats()["authenticated"] is False


@pytest.mark.parametrize("raw,expect", [
    ("tok-1234567890", "tok-1234567890"),
    ("# PARKED until the venue", ""),           # dotenv reads `KEY=# comment` as the VALUE
    ("  spaced token  ", ""),                   # a token has no whitespace in it
    ("", ""),
])
def test_a_parked_or_commented_token_is_no_token(monkeypatch, raw, expect):
    from telemetry import hub
    monkeypatch.setenv("GITIRL_CLOUD_TOKEN", raw)
    assert hub._cloud_token() == expect


def test_the_token_never_reaches_a_log_or_the_stats(caplog):
    """The one property that cannot be walked back once it is wrong."""
    import logging

    import httpx
    secret = "sup3r-secret-room-token"
    s = SSESink(post_url="https://gitirl.health/api/edge/event", token=secret)
    s._http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"error": "unauthorized"})))
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            try:
                asyncio.run(s._post({"ts": "2026-09-20T07:00:00.000Z"}))
            except Exception as e:  # noqa: BLE001 -- what Sink.run records
                s.fail(e)
    burned = caplog.text + json.dumps(s.stats()) + (s.last_error or "")
    assert secret not in burned, "the token is a header, and a header is not something to print"
    assert s.stats()["authenticated"] is True, "it says a token is being SENT, never which one"


def test_a_site_that_went_away_is_said_once_and_then_quietly(caplog):
    """At 2 Hz a site that has gone away would otherwise narrate its own outage. Say it once, keep
    going, and say one line when it comes back — the same manners roomctl/nav_publish.py has."""
    import logging

    import httpx
    up = [False]

    def site(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if up[0] else 503, json={})
    s = SSESink(post_url="https://gitirl.health/api/edge/event", token="t0ken-value-here")
    s._http = httpx.AsyncClient(transport=httpx.MockTransport(site))

    with caplog.at_level(logging.WARNING):
        for _ in range(40):                                  # 20 s of a dead site
            try:
                asyncio.run(s._post({"ts": "2026-09-20T07:00:00.000Z"}))
            except Exception as e:  # noqa: BLE001
                s.fail(e)
        lost = [r for r in caplog.records if "not reachable" in r.message]
        assert len(lost) == 1, f"said it {len(lost)} times over 40 failures"
        assert s.errors == 40 and s.failed_sends == 40, "quiet is not the same as uncounted"
        assert s.stats()["reachable"] is False

        up[0] = True
        asyncio.run(s._post({"ts": "2026-09-20T07:00:20.000Z"}))
        back = [r for r in caplog.records if "reachable again" in r.message]
        assert len(back) == 1 and "40 failed sends" in back[0].getMessage()
        assert s.stats()["reachable"] is True
