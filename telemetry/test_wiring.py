"""The CONNECTIONS, with every external boundary mocked: no OpenAI, no Elasticsearch, no Sentry.
    .venv/bin/python -m pytest telemetry -q

  obs  -> sentry_sdk        a fake scope/span/transaction records what WOULD be sent
  hub  -> Elasticsearch     a fake client at client.bulk() returns 201/409/400 or raises 401
  hub  -> executor          Jobs.wait() across threads; JobWatcher over a local /stream
  hub  -> perception        *_mono -> *_wall on the telemetry clock; frames for failure photos
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "elastic")]

import obs  # noqa: E402
from telemetry import hub as hubmod  # noqa: E402
from telemetry.frames import FrameCache  # noqa: E402
from telemetry.hub import Batch, Clock, ElasticSink, Hub, JobWatcher, Jobs, SentrySink  # noqa: E402
from telemetry.test_telemetry import free_port, pi  # noqa: E402


# ── a fake Sentry: records, never sends ─────────────────────────────────────────
class FakeScope:
    def __init__(self):
        self.crumbs, self.attachments, self.tags = [], [], {}
        self.transaction = None

    def add_breadcrumb(self, **kw):
        self.crumbs.append(kw)

    def add_attachment(self, **kw):
        self.attachments.append(kw)

    def set_tag(self, k, v):
        self.tags[k] = v


class FakeSpan:
    def __init__(self, containing_transaction=None):
        self.data, self.tags = {}, {}
        self.containing_transaction = containing_transaction

    def set_data(self, k, v):
        self.data[k] = v

    def set_tag(self, k, v):
        self.tags[k] = v

    def get_trace_context(self):
        return {"trace_id": "0" * 32, "span_id": "1" * 16}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeTx(FakeSpan):
    def __init__(self):
        super().__init__()
        self.measurements = {}
        self.containing_transaction = self

    def set_measurement(self, name, value, unit=""):
        self.measurements[name] = value


@pytest.fixture
def sentry(monkeypatch, tmp_path):
    """Every forked scope, every message, and a trap on the process-wide calls that leak."""
    monkeypatch.setattr(obs, "_ATTACHED", __import__("collections").deque())
    monkeypatch.setattr(obs, "_LEDGER", tmp_path / "attach-ledger.json")   # never the real budget
    rec = SimpleNamespace(scopes=[], messages=[], current=FakeScope(), started=[])
    rec.current.transaction = FakeTx()
    rec.span = FakeSpan(containing_transaction=rec.current.transaction)

    def start_span(op=None, name=None, **kw):
        sp = FakeSpan(containing_transaction=rec.current.transaction)
        rec.started.append((op, sp))
        return sp

    @contextlib.contextmanager
    def new_scope():
        sc = FakeScope()
        rec.scopes.append(sc)
        yield sc

    def leak(*a, **k):
        raise AssertionError("process-wide scope call: this would leak onto every later event")
    monkeypatch.setattr(obs.sentry_sdk, "new_scope", new_scope)
    monkeypatch.setattr(obs.sentry_sdk, "capture_message", lambda m, level=None: rec.messages.append(m) or "evt")
    monkeypatch.setattr(obs.sentry_sdk, "get_current_scope", lambda: rec.current)
    monkeypatch.setattr(obs, "get_current_span", lambda: rec.span)
    monkeypatch.setattr(obs.sentry_sdk, "start_span", start_span)
    for name in ("add_breadcrumb", "set_tag", "add_attachment"):
        monkeypatch.setattr(obs.sentry_sdk, name, leak)
    return rec


def test_robot_failure_puts_crumbs_and_one_small_photo_on_the_forked_scope_only(sentry):
    frame = np.random.default_rng(0).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    for fall in range(2):
        obs.robot_failure("grasp_slipped", f"#{fall}", frame=frame if fall == 0 else None,
                          telemetry=[{"t_mono": fall * 100 + i} for i in range(60)], job_id=f"j{fall}")
    a, b = sentry.scopes
    assert [len(a.crumbs), len(b.crumbs)] == [40, 40]              # each event: its own last 40
    assert all(c["data"]["t_mono"] >= 100 for c in b.crumbs)       # nothing from failure #1
    assert len(a.attachments) == 1 and b.attachments == []         # one photo, on its own event
    att = a.attachments[0]
    assert att["filename"] == "grasp_slipped.jpg" and att["content_type"] == "image/jpeg"
    img = cv2.imdecode(np.frombuffer(att["bytes"], np.uint8), cv2.IMREAD_COLOR)
    assert max(img.shape[:2]) == 480                               # downscaled to ~480 px, aspect kept
    assert a.tags["failure_kind"] == "grasp_slipped" and a.tags["job_id"] == "j0"


def test_photos_stop_at_the_hourly_cap_but_the_issues_keep_coming(sentry, monkeypatch):
    monkeypatch.setattr(obs, "MAX_ATTACHMENTS_PER_HOUR", 2)
    frame = np.zeros((720, 1280, 3), np.uint8)
    for i in range(4):
        obs.robot_failure("grasp_slipped", f"#{i}", frame=frame)
    assert [len(sc.attachments) for sc in sentry.scopes] == [1, 1, 0, 0]
    assert [sc.tags.get("attachment_skipped") for sc in sentry.scopes] == [None, None, "hourly_count", "hourly_count"]
    assert len(sentry.messages) == 4                               # every failure still reported
    assert len(sentry.scopes[0].attachments[0]["bytes"]) < 15_000  # ~480 px q70


def test_capture_quality_measures_and_fails_on_missing_evidence(sentry):
    assert obs.capture_quality(3.1, 0.02, 0.71) is True
    assert sentry.current.transaction.measurements == {"skew_ms": 3.1, "tilt_rate_max": 0.02, "coverage": 0.71}
    sentry.current.transaction.measurements.clear()
    sentry.span.data.clear()
    assert obs.capture_quality(3.1, None, 0.71) is False           # the Pi's window wasn't covered
    assert "tilt_rate_max" not in sentry.current.transaction.measurements   # nothing invented
    assert "tilt_rate_max" not in sentry.span.data and sentry.span.data["quality_ok"] is False
    assert sentry.current.tags == {"capture_rejected": "true"}    # on capture_scope's fork, not global
    assert sentry.span.tags["capture_rejected"] == "true"          # and on the span + its transaction,
    assert sentry.current.transaction.tags["capture_rejected"] == "true"   # which the fork never reaches


def test_capture_id_reaches_every_span_and_the_transaction_whatever_the_nesting(sentry):
    """Live, 2026-09-19: capture_scope nested INSIDE a transaction put capture_id on no span at all,
    so Sentry could not be searched by capture — the Sentry->Elasticsearch half of the join."""
    with obs.capture_scope("cap_42", "a3f9c1"):
        with obs.span("perception.sgbm", "disparity"):
            pass
        with obs.span("perception.merge"):
            pass
    tx, enclosing = sentry.current.transaction, sentry.span
    assert tx.tags["capture_id"] == "cap_42" and tx.tags["commit_sha"] == "a3f9c1"
    assert enclosing.tags["capture_id"] == "cap_42"
    assert [op for op, _ in sentry.started] == ["perception.sgbm", "perception.merge"]
    assert all(sp.tags["capture_id"] == "cap_42" and sp.data["capture_id"] == "cap_42" for _, sp in sentry.started)
    with obs.span("after"):                                        # outside: no stale ids
        pass
    assert "capture_id" not in sentry.started[-1][1].tags


def test_small_jpeg_downscales_passes_small_jpegs_and_refuses_garbage():
    img = np.zeros((720, 2560, 3), np.uint8)
    out = obs.small_jpeg(img)
    assert cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (135, 480)
    assert obs.small_jpeg(out) and obs.small_jpeg(b"not a jpeg") is None


# ── frames ─────────────────────────────────────────────────────────────────────
def test_frame_cache_prefers_the_camera_asked_for_and_never_escapes_its_dir(tmp_path):
    fc = FrameCache(tmp_path, keep=2)
    img = np.full((720, 1280, 3), 90, np.uint8)
    fc.put("cap_1", "cam1", img)
    fc.put("cap_1", "cam0", img)
    assert fc.get("cap_1", "cam1") == (tmp_path / "cap_1" / "cam1.jpg").read_bytes()
    assert fc.get("cap_1", "cam2") == (tmp_path / "cap_1" / "cam0.jpg").read_bytes()  # falls back
    assert fc.get("cap_nope") is None and fc.get(None) is None
    fc.put("../../etc", "../passwd", img)                          # ids arrive off the wire
    assert not (tmp_path.parent / "etc").exists() and all(p.parent == tmp_path for p in tmp_path.iterdir())
    time.sleep(0.01)
    fc.put("cap_3", "cam0", img)
    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 2   # keeps the newest 2


# ── hub -> executor: jobs ─────────────────────────────────────────────────────────
def test_jobs_wait_from_another_thread_before_and_after_the_outcome():
    jobs = Jobs()
    jobs.update({"t": "job", "id": "early", "state": "done", "result": {"grasped": True}})
    assert jobs.wait("early", 0.1)["result"] == {"grasped": True}  # finished before anyone waited
    got = {}
    t = threading.Thread(target=lambda: got.setdefault("j", jobs.wait("late", 2)))
    t.start()
    time.sleep(0.05)
    jobs.update({"t": "job", "id": "late", "state": "moving_to_pick", "progress": 0.3})  # not terminal
    assert "j" not in got
    jobs.update({"t": "job", "id": "late", "state": "failed", "error": "grasp_slipped", "detail": "2mm"})
    t.join(1)
    assert got["j"]["error"] == "grasp_slipped"
    with pytest.raises(TimeoutError):
        jobs.wait("never", 0.05)


def hub_after_hello(sinks, frames=None):
    h = Hub("ws://unused", sinks, frames=frames)
    h._dispatch(json.dumps(pi.Telemetry(pi.FakeRobot()).hello(fw="test")))
    return h


def test_a_failed_job_is_reported_once_with_the_frame_of_the_capture_before_it(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(obs, "robot_failure", lambda kind, detail, telemetry=None, frame=None, **tags:
                        calls.append((kind, frame, tags)))
    fc = FrameCache(tmp_path)
    fc.put("cap_7", "cam0", np.full((720, 1280, 3), 50, np.uint8))
    h = hub_after_hello([SentrySink()], frames=fc)
    h._dispatch(json.dumps({"t": "capture_begin", "capture_id": "cap_7", "t_capture_mono": 10.0}))
    h._dispatch(json.dumps({"t": "job", "id": "job_1", "state": "moving_to_pick", "progress": 0.1}))
    h._dispatch(json.dumps({"t": "capture_begin", "capture_id": "cap_8", "t_capture_mono": 20.0}))  # later
    h._dispatch(json.dumps({"t": "job", "id": "job_1", "state": "failed", "error": "grasp_slipped",
                            "detail": "gripper closed to 2mm"}))
    assert len(calls) == 1
    kind, frame, tags = calls[0]
    assert kind == "grasp_slipped" and frame == fc.get("cap_7", "cam0")   # the capture it was PLANNED from
    assert tags["job_id"] == "job_1" and tags["capture_id"] == "cap_7"
    assert h.jobs.wait("job_1", 0.01)["state"] == "failed"


def test_detections_beat_the_watch_monitor_at_most_every_30s(monkeypatch):
    beats = []
    monkeypatch.setattr(obs, "heartbeat", lambda slug, status="ok", duration=None, monitor_config=None:
                        beats.append((slug, status, monitor_config)))
    h = hub_after_hello([SentrySink()])
    h._dispatch(json.dumps({"t": "detection", "camera": "cam0", "objects": []}))
    assert beats == []                                             # opt-in: room-clean owns the one monitor
    h.watch_heartbeat = True
    for _ in range(5):
        h._dispatch(json.dumps({"t": "detection", "camera": "cam0", "objects": []}))
    assert len(beats) == 1 and beats[0][:2] == ("watch-loop", "ok")
    assert beats[0][2]["schedule"] == {"type": "interval", "value": 1, "unit": "minute"}  # upserts itself
    h._beat_at -= hubmod.WATCH_EVERY_S
    h._dispatch(json.dumps({"t": "detection", "camera": "cam0", "objects": []}))
    assert len(beats) == 2


def test_a_job_watcher_never_reports_or_beats(monkeypatch):
    monkeypatch.setattr(obs, "robot_failure", lambda *a, **k: pytest.fail("watcher reported a failure"))
    monkeypatch.setattr(obs, "heartbeat", lambda *a, **k: pytest.fail("watcher beat the monitor"))
    w = JobWatcher("ws://unused")
    w.hub._dispatch(json.dumps(pi.Telemetry(pi.FakeRobot()).hello()))
    w.hub._dispatch(json.dumps({"t": "detection", "objects": []}))
    w.hub._dispatch(json.dumps({"t": "job", "id": "j", "state": "failed", "error": "busy"}))
    assert w.jobs.wait("j", 0.01)["error"] == "busy"


def test_job_watcher_over_a_real_local_stream():
    """The executor's path end to end, minus the arm: Pi publishes a job, a watcher in another
    thread (as `room revert` would run it) unblocks on it."""
    port = free_port()
    tel = pi.Telemetry(pi.FakeRobot()).start()
    loop = asyncio.new_event_loop()
    server = threading.Thread(target=lambda: loop.run_until_complete(pi.serve(tel, "127.0.0.1", port)), daemon=True)
    server.start()
    time.sleep(0.3)
    w = JobWatcher(f"ws://127.0.0.1:{port}/stream").start()
    for _ in range(50):
        if w.hub.connected:
            break
        time.sleep(0.05)
    tel.publish({"t": "job", "id": "job_42", "state": "done", "result": {"grasped": True, "duration_s": 26.4}})
    assert w.jobs.wait("job_42", 2)["result"]["grasped"] is True
    tel.stop()


# ── hub -> perception: one clock ───────────────────────────────────────────────
def test_every_pi_mono_timestamp_gets_a_wall_twin_on_the_telemetry_clock():
    h = hub_after_hello([])
    h._dispatch(json.dumps({"t": "telemetry", "from_mono": 100.0, "hz": 50, "signals": {"pitch": [0.0]}}))
    msg = {"t": "capture_begin", "capture_id": "c", "t_capture_mono": 100.0,
           "frames": [{"camera": "cam0", "t_mono": 100.0005}]}
    got = []
    h.on("capture_begin", got.append)
    h._dispatch(json.dumps(msg))
    m = got[0]
    assert m["t_capture_wall"] == round(100.0 + h.clock.offset, 3)   # the SAME offset as telemetry
    assert m["ts"].endswith("Z") and m["frames"][0]["t_wall"] == round(100.0005 + h.clock.offset, 3)
    fresh = Hub("ws://unused", [])
    fresh._dispatch(json.dumps(msg))                              # no hello: nothing is invented
    assert "ts" not in msg


def test_the_pi_clock_survives_a_late_hello_but_not_a_missing_ntp():
    now = time.time()
    c = Clock()
    pi_off = lambda boot, err: {"boot_id": boot, "t_mono_base": 5.0, "t_wall_base": now - 5 + err, "t_mono_now": 10.0}
    assert c.on_hello(pi_off("a", -0.12), now) is None             # a hello 120 ms late: keep the Pi's pairing
    assert c.offset == pytest.approx(now - 5 - 0.12 - 5.0)
    assert "off the laptop" in c.on_hello(pi_off("b", -3600), now)   # no NTP: an hour off -> our clock
    assert c.wall(10.0) == pytest.approx(now, abs=1e-3)


def test_the_lag_monitor_warns_when_the_mapping_is_wrong(caplog):
    h = hub_after_hello([])               # the Pi here shares this process's clocks: mapping ~exact
    h.clock.offset += 2.0                 # now pretend we picked a mapping 2 s off
    for _ in range(60):                   # live batches whose newest sample is "now" on the Pi
        h._dispatch(json.dumps({"t": "telemetry", "from_mono": time.monotonic() - 0.08, "hz": 50,
                                "signals": {"pitch": [0.0] * 5}}))
    assert h.clock_lag_s == pytest.approx(-2.0, abs=0.05)         # samples look 2 s in the future
    assert any("clock mapping is off" in r.message for r in caplog.records)


# ── hub -> Elasticsearch, at client.bulk() ───────────────────────────────────────
class FakeES:
    """Parses the real _bulk body helpers.streaming_bulk sends and answers per doc."""

    def __init__(self, status_for=lambda doc: 201, raise_=None):
        from elastic_transport import JsonSerializer
        self.status_for, self.raise_, self.received = status_for, raise_, []
        self.transport = SimpleNamespace(serializers=SimpleNamespace(get_serializer=lambda mt: JsonSerializer()))
        null = contextlib.nullcontext                       # the client's OpenTelemetry hooks, inert
        self._otel = SimpleNamespace(helpers_span=lambda name: null(SimpleNamespace()), use_span=lambda sp: null())

    def options(self, **kw):
        return self

    def bulk(self, operations, **kw):
        if self.raise_:
            raise self.raise_
        lines = [json.loads(x) for x in operations]
        items = []
        for action, doc in zip(lines[::2], lines[1::2]):
            self.received.append(doc)
            st = self.status_for(doc)
            item = {"_index": action["create"]["_index"], "status": st}
            if st >= 300:
                item["error"] = {"type": "version_conflict_engine_exception" if st == 409 else
                                 "strict_dynamic_mapping_exception", "reason": "…"}
            items.append({"create": item})
        return SimpleNamespace(body={"errors": any(i["create"]["status"] >= 300 for i in items), "items": items})


def auth_error():
    from elastic_transport import ApiResponseMeta, HttpHeaders, NodeConfig
    from elasticsearch import AuthenticationException
    meta = ApiResponseMeta(status=401, http_version="1.1", headers=HttpHeaders(), duration=0.0,
                           node=NodeConfig("https", "es", 443))
    return AuthenticationException("unable to authenticate", meta, {"error": "invalid api key"})


def docs(n, t0=1_789_780_000_000):
    return [{"@timestamp": t0 + 20 * i, "signal": "pitch", "value": 0.01} for i in range(n)]


def run(coro):
    return asyncio.run(coro)


def test_bulk_counts_written_dups_and_rejects_without_spooling_rejects(tmp_path):
    sink = ElasticSink(tmp_path / "spool")
    es = FakeES(status_for=lambda d: {0: 201, 1: 409, 2: 400}[(d["@timestamp"] // 20) % 3])

    async def go():
        sink.es = es
        await sink.flush(docs(9))
    run(go())
    assert (sink.written, sink.dups, sink.errors) == (3, 3, 1)      # 3 bad docs -> one failure record
    assert "strict_dynamic_mapping_exception" in sink.last_error
    assert not (tmp_path / "spool").exists()                      # a doc ES REJECTS would fail again


def test_an_auth_error_mid_run_spools_every_doc_then_backfills_oldest_first(tmp_path):
    sink = ElasticSink(tmp_path / "spool")
    down = FakeES(raise_=auth_error())

    async def go():
        sink.es = down
        await sink.flush(docs(5, t0=1000))                         # key revoked: nothing may be lost
        await asyncio.sleep(0.002)
        sink.es, sink._retry_at = down, 0
        await sink.flush(docs(5, t0=9000))
    run(go())
    files = sorted((tmp_path / "spool").glob("*.ndjson"))
    assert sink.spooled == 10 and len(files) == 2 and sink.es is None and sink.written == 0

    up = FakeES()

    async def recover():
        sink.es = up
        await sink.flush(docs(1, t0=50_000))                       # first good flush: live doc + ONE file
    run(recover())
    assert [d["@timestamp"] for d in up.received] == [50_000] + [1000 + 20 * i for i in range(5)]
    assert len(list((tmp_path / "spool").glob("*.ndjson"))) == 1 and sink.backfilled == 5


def test_no_client_spools_and_the_spool_is_capped_drop_oldest(tmp_path):
    sink = ElasticSink(tmp_path / "spool", spool_max_mb=0.0005)   # 500 bytes
    for i in range(6):
        sink._spool(docs(3, t0=i * 1000))
        time.sleep(0.002)
    files = sorted((tmp_path / "spool").glob("*.ndjson"))
    assert sum(f.stat().st_size for f in files) <= 500
    assert json.loads(files[-1].read_text().splitlines()[0])["@timestamp"] == 5000   # newest kept


# ── the Pi's side of the capture gate ────────────────────────────────────────────
def test_the_latch_peak_waits_for_the_window_and_refuses_a_partial_one():
    tel = pi.Telemetry(pi.FakeRobot()).start()
    time.sleep(0.3)
    t = time.monotonic()
    t0 = time.perf_counter()
    peak = tel.peak("tilt_rate", t - 0.1, t + 0.1, wait_s=0.5)    # half the window is in the future
    waited = time.perf_counter() - t0
    assert peak is not None and 0.08 <= waited <= 0.35
    assert tel.peak("tilt_rate", t + 5, t + 5.2) is None           # not covered, no wait: None
    assert tel.peak("tilt_rate", t - 60, t) is None                # start evicted from the ring: None
    tel.stop()


# ── AI agent monitoring conventions (docs/10 D31) ────────────────────────────────
def test_agent_tool_speaks_sentrys_execute_tool_convention(sentry):
    with obs.capture_scope("cap_9"):
        with obs.agent_tool("search_objects", kind="elastic", query="mug") as sp:
            sp.set_data("gen_ai.tool.call.result", json.dumps({"ok": True}))
    op, sp = sentry.started[-1]
    assert op == "gen_ai.execute_tool"
    assert sp.data["gen_ai.operation.name"] == "execute_tool" and sp.data["gen_ai.tool.name"] == "search_objects"
    assert sp.data["gen_ai.tool.type"] == "elastic" and json.loads(sp.data["gen_ai.tool.call.arguments"]) == {"query": "mug"}
    assert sp.tags["capture_id"] == "cap_9"


def test_agent_turn_never_double_counts_an_instrumented_call(sentry, monkeypatch):
    monkeypatch.setattr(obs, "_openai_instrumented", lambda: True)
    with obs.agent_turn("describe this", "gpt-5"):
        pass
    assert sentry.started[-1][0] == "agent.turn"                   # the integration owns the gen_ai span
    with obs.agent_turn("realtime voice", "gpt-realtime", sdk_visible=False):
        pass
    op, sp = sentry.started[-1]
    assert op == "gen_ai.chat" and sp.data["gen_ai.operation.name"] == "chat"
    assert sp.data["gen_ai.request.model"] == "gpt-realtime"


# ── the attachment budget: 1 GB/month, one ledger for the whole laptop ─────────────
def test_the_byte_budget_and_the_size_cap(sentry, monkeypatch):
    monkeypatch.setattr(obs, "MAX_ATTACHMENT_BYTES_PER_HOUR", 25_000)
    assert obs._attachment_blocked(200_000) == "too_large"           # > 100 KB: never, whatever the hour
    assert [obs._attachment_blocked(10_000) for _ in range(3)] == [None, None, "hourly_bytes"]
    assert json.loads(obs._LEDGER.read_text()) and len(json.loads(obs._LEDGER.read_text())) == 2


def test_the_budget_is_shared_by_every_process_on_the_laptop(sentry, monkeypatch):
    """Two processes (the hub and the CLI, say) draw on ONE hourly budget, not one each."""
    monkeypatch.setattr(obs, "MAX_ATTACHMENTS_PER_HOUR", 3)
    assert obs._attachment_blocked(1000) is None and obs._attachment_blocked(1000) is None
    other = subprocess.run([sys.executable, "-c",
        "import sys, obs; from pathlib import Path; obs._LEDGER = Path(sys.argv[1]); "
        "obs.MAX_ATTACHMENTS_PER_HOUR = 3; print([obs._attachment_blocked(1000) for _ in range(2)])",
        str(obs._LEDGER)], capture_output=True, text=True, cwd=ROOT, check=True)
    assert other.stdout.strip() == "[None, 'hourly_count']"          # it got the 3rd slot, not a fresh 3
    assert obs._attachment_blocked(1000) == "hourly_count"


def test_attach_is_refused_outside_a_capture_scope(sentry):
    assert obs.attach("frame.jpg", b"x" * 100, "image/jpeg") is False  # would ride on every later event
    assert sentry.current.attachments == []
    with obs.capture_scope("cap_1"):
        assert obs.attach("frame.jpg", b"x" * 100, "image/jpeg") is True
    assert len(sentry.current.attachments) == 1
