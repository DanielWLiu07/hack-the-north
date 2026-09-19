#!/usr/bin/env python3
"""telemetry/hub.py — the laptop's ONE consumer of ws://<pi>:8080/stream. docs/23-telemetry.md.

One socket in, four sinks out. Every sink owns a task, a bounded queue with drop-oldest, and
(if it blocks) its own single thread. Fan-out is a non-blocking put, so a sink that hangs or
throws loses its own data and nobody else's:

  es      robot-telemetry TSDS, _bulk once a second. ES down -> spool to disk, backfill later.
  sentry  a 10 s laptop-side ring, PULLED (never streamed) by obs.robot_failure() and the
          capture gate; also turns balanced 1->0 into a `fell_over` issue with the lean attached.
  sse     2 Hz decimated frames POSTed to web's loopback inlet, which fans them out on
          /api/events. Web down or slow: the frame is dropped.
  rerun   every sample as scalars on the shared wall-clock timeline. Drops.

The Pi stamps samples with its monotonic clock; this is the one place they become wall-clock,
via the pairing in its hello (docs/22 §3). Reconnects back off 0.5 s -> 8 s and ask the Pi to
replay from the last sample seen, so a wifi blip leaves no hole (docs/16 §3.3).

    python -m telemetry.hub                               # PI_HOST:PI_PORT from .env
    python -m telemetry.hub --pi ws://127.0.0.1:8080/stream
    python robot/telemetry.py --fake                      # a Pi to point it at
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import math
import os
import signal
import sys
import threading
import time
from collections import OrderedDict, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
# Trust the Pi's own wall clock if it agrees with ours to within 250 ms. A Pi clock is either
# NTP'd (ms) or, with no RTC and no NTP, off by minutes to days — 250 ms separates the two while
# tolerating a hello that arrived late (one sample can't tell delay from skew). Captures and
# telemetry cannot disagree either way: both are mapped HERE (_add_wall_times). What this
# decides is alignment with laptop-stamped things (commits) — and the lag monitor checks it.
MAX_PI_CLOCK_SKEW_S = 0.25
CLOCK_LAG_WARN_S = 0.25        # live check: min(arrival - sample wall time) over recent batches
# The one cron monitor on the plan (docs/18). Check in while the Pi's watch loop is producing
# detections; if it dies — or the Pi, or the link — the check-ins stop and Sentry pages.
WATCH_SLUG = "watch-loop"
WATCH_EVERY_S = 30.0
WATCH_MONITOR = {"schedule": {"type": "interval", "value": 1, "unit": "minute"},
                 "checkin_margin": 2, "max_runtime": 1, "timezone": "UTC"}
PI_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warn": logging.WARNING,
             "warning": logging.WARNING, "error": logging.ERROR}

log = logging.getLogger("gitspace.telemetry")
pi_log = logging.getLogger("gitspace.pi")      # Pi "log" messages; enable_logs ships them to Sentry


def iso_ms(t_wall: float) -> str:
    """Epoch seconds -> '2026-09-19T14:22:07.400Z', the shape every ES doc and SSE frame uses."""
    return dt.datetime.fromtimestamp(t_wall, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Batch:
    """One telemetry message, unpacked once and shared read-only by every sink."""
    t_mono: tuple[float, ...]
    t_wall: tuple[float, ...]
    signals: dict[str, tuple]                  # name -> values, None where the Pi had none
    replay: bool

    def rows(self):
        for i, (m, w) in enumerate(zip(self.t_mono, self.t_wall)):
            yield m, w, {name: vals[i] for name, vals in self.signals.items()}


class Clock:
    """Pi monotonic -> wall seconds, one mapping per Pi boot. Reusing it across reconnects is
    what makes a replayed sample land on the @timestamp it had live, so the TSDS dedupes it."""

    def __init__(self) -> None:
        self._by_boot: dict[str, float] = {}
        self.offset: float | None = None

    def on_hello(self, h: dict, laptop_now: float) -> str | None:
        boot = h.get("boot_id", "")
        if boot not in self._by_boot:
            pi_now = h["t_wall_base"] + (h["t_mono_now"] - h["t_mono_base"])
            skew = pi_now - laptop_now               # includes the hello's one-way LAN delay (~ms)
            if abs(skew) <= MAX_PI_CLOCK_SKEW_S:
                self._by_boot[boot] = h["t_wall_base"] - h["t_mono_base"]
                log.info("Pi clock agrees with the laptop's (%+.1f ms): using the Pi's pairing", skew * 1e3)
            else:  # keep the Pi's clock for intervals, take the date from ours
                wall_base = round(laptop_now - (h["t_mono_now"] - h["t_mono_base"]), 3)  # whole ms
                self._by_boot[boot] = wall_base - h["t_mono_base"]
                self.offset = self._by_boot[boot]
                return (f"Pi wall clock is {skew:+.3f} s off the laptop's — "
                        f"mapping Pi monotonic onto the laptop clock instead")
        self.offset = self._by_boot[boot]
        return None

    def wall(self, t_mono: float) -> float:
        if self.offset is None:
            raise ValueError("no hello yet: Pi monotonic time can't be placed on the wall clock")
        return t_mono + self.offset


class Jobs:
    """`job` messages -> something synchronous code can block on. POST /arm answers with a
    job_id at once and the outcome arrives later on /stream, so the executor's Robot client does
    `job = jobs.wait(job_id, timeout)` from its own thread. Terminal states are kept (last
    `keep`), so a job that finishes before wait() is called is not lost. A job that finishes
    while the socket is DOWN is — the Pi does not replay jobs — and wait() times out."""
    TERMINAL = ("done", "failed")

    def __init__(self, keep: int = 256):
        self._cond = threading.Condition()
        self._done: OrderedDict[str, dict] = OrderedDict()
        self._keep = keep

    def update(self, msg: dict) -> None:
        if msg.get("state") not in self.TERMINAL or "id" not in msg:
            return
        with self._cond:
            self._done[msg["id"]] = msg
            self._done.move_to_end(msg["id"])
            while len(self._done) > self._keep:
                self._done.popitem(last=False)
            self._cond.notify_all()

    def wait(self, job_id: str, timeout: float) -> dict:
        """The terminal job message: {"state": "done", "result": ...} or {"state": "failed",
        "error": <docs/16 §2.7 code>, "detail": ...}. Raises TimeoutError. Never call it on the
        hub's own event loop — it blocks."""
        with self._cond:
            if not self._cond.wait_for(lambda: job_id in self._done, timeout):
                raise TimeoutError(f"job {job_id}: no done/failed within {timeout:g} s")
            return self._done[job_id]


# ── sinks ────────────────────────────────────────────────────────────────────────
class Sink:
    name = "sink"

    def __init__(self, maxsize: int = 100):
        self.q: asyncio.Queue[Batch] = asyncio.Queue(maxsize)
        self.received = self.dropped = self.errors = 0
        self.last_error = ""
        self._pool: ThreadPoolExecutor | None = None

    def offer(self, b: Batch) -> None:
        """Called by the consumer for every batch. Never blocks, never raises."""
        self.received += 1
        if self.q.full():
            self.q.get_nowait()
            self.dropped += 1
        self.q.put_nowait(b)

    async def run(self) -> None:
        while True:
            b = await self.q.get()
            try:
                await self.handle(b)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 -- a sink failing is data, not a crash
                self.fail(e)

    async def handle(self, b: Batch) -> None:
        raise NotImplementedError

    def fail(self, e: BaseException | str) -> None:
        self.errors += 1
        self.last_error = (e if isinstance(e, str) else f"{type(e).__name__}: {e}")[:200]
        if self.errors in (1, 10, 100) or self.errors % 1000 == 0:
            log.warning("sink %s: %s (errors=%d)", self.name, self.last_error, self.errors)

    async def blocking(self, fn: Callable, *args):
        """Run a blocking call on THIS sink's own thread: a hung call can't starve the others."""
        if self._pool is None:
            self._pool = ThreadPoolExecutor(1, thread_name_prefix=f"sink-{self.name}")
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)

    def stats(self) -> dict:
        return {"recv": self.received, "dropped": self.dropped, "errors": self.errors,
                "queued": self.q.qsize(), **({"last_error": self.last_error} if self.last_error else {})}


class ElasticSink(Sink):
    """robot-telemetry, one doc per (signal, sample) — {@timestamp, signal, value} and nothing
    else, because the mapping is dynamic:strict. Epoch-millis timestamps, so a replayed sample
    is the same (signal, @timestamp) and the TSDS answers 409: counted as `dups`, not an error."""
    name = "es"
    INDEX = "robot-telemetry"

    def __init__(self, spool_dir: Path, flush_s: float = 1.0, spool_max_mb: float = 500,
                 maxsize: int = 600):
        super().__init__(maxsize)
        self.spool_dir, self.flush_s, self.spool_max = spool_dir, flush_s, spool_max_mb * 1e6
        self.es = None
        self._retry_at = 0.0
        self._down_reason = ""
        self.written = self.dups = self.spooled = self.backfilled = 0

    @staticmethod
    def docs(b: Batch) -> list[dict]:
        return [{"@timestamp": round(w * 1000), "signal": name, "value": v}
                for _, w, row in b.rows() for name, v in row.items() if v is not None]

    async def run(self) -> None:
        buf: list[dict] = []
        due = time.monotonic() + self.flush_s
        try:
            while True:
                try:
                    b = await asyncio.wait_for(self.q.get(), max(0.0, due - time.monotonic()))
                    buf.extend(self.docs(b))
                except TimeoutError:
                    pass
                if time.monotonic() < due:
                    continue
                due = time.monotonic() + self.flush_s
                if buf:
                    docs, buf = buf, []
                    try:
                        await self.flush(docs)
                    except asyncio.CancelledError:
                        buf = docs
                        raise
                    except Exception as e:  # noqa: BLE001
                        self.fail(e)
        finally:                             # shutting down: keep everything for backfill
            while not self.q.empty():
                buf.extend(self.docs(self.q.get_nowait()))
            if buf:
                self._spool(buf)

    async def flush(self, docs: list[dict]) -> None:
        es = await self._client()
        if es is None:
            self._spool(docs)
            return
        try:
            ok, dup, errs = await self.blocking(self._bulk, es, docs)
        except Exception as e:  # transport-level: the cluster, not the docs
            self.es, self._retry_at = None, time.monotonic() + 10
            self._spool(docs)
            self.fail(f"bulk failed, spooled {len(docs)} docs: {type(e).__name__}: {e}")
            return
        self.written, self.dups = self.written + ok, self.dups + dup
        if errs:                             # bad docs would fail again: count, don't spool
            self.fail(f"{len(errs)} docs rejected: {errs[0]}")
        await self._backfill_one(es)

    def _bulk(self, es, docs: list[dict]) -> tuple[int, int, list[str]]:
        from elasticsearch import helpers
        from ingest import action          # elastic/ingest.py: op_type create for data streams
        ok = dup = 0
        errs: list[str] = []
        for good, item in helpers.streaming_bulk(es, (action(self.INDEX, d) for d in docs),
                                                 chunk_size=1000, raise_on_error=False,
                                                 raise_on_exception=True):
            res = next(iter(item.values()))
            if good:
                ok += 1
            elif res.get("status") == 409:
                dup += 1
            else:
                err = res.get("error", {})
                errs.append(f"{err.get('type')}: {err.get('reason')}" if isinstance(err, dict) else str(err))
        return ok, dup, errs

    async def _client(self):
        if self.es is not None or time.monotonic() < self._retry_at:
            return self.es
        try:
            from setup_elastic import connect  # elastic/setup_elastic.py
            self.es = await self.blocking(connect)
            if self._down_reason:
                log.info("es: connected — backfilling the spool")
            self._down_reason = ""
        except Exception as e:  # noqa: BLE001 -- SetupError, import error, network
            self._retry_at = time.monotonic() + 30
            reason = f"{type(e).__name__}: {e}"
            if reason != self._down_reason:
                log.warning("es: unavailable, spooling to %s (%s)", self.spool_dir, reason)
                self._down_reason = reason
        return self.es

    def _spool(self, docs: list[dict]) -> None:
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        path = self.spool_dir / f"{time.time_ns()}.ndjson"
        path.write_text("".join(json.dumps(d, separators=(",", ":")) + "\n" for d in docs))
        self.spooled += len(docs)
        files = sorted(self.spool_dir.glob("*.ndjson"))
        total = sum(f.stat().st_size for f in files)
        while files and total > self.spool_max:  # drop-oldest on disk too
            total -= files[0].stat().st_size
            files.pop(0).unlink()

    async def _backfill_one(self, es) -> None:
        """One spool file per flush, oldest first — catching up never starves the live path."""
        files = sorted(self.spool_dir.glob("*.ndjson")) if self.spool_dir.exists() else []
        if not files:
            return
        docs = [json.loads(line) for line in files[0].read_text().splitlines() if line]
        ok, dup, errs = await self.blocking(self._bulk, es, docs)
        files[0].unlink()
        self.backfilled += ok + dup
        if errs:
            self.fail(f"backfill: {len(errs)} docs rejected: {errs[0]}")

    def stats(self) -> dict:
        spool = self.spool_dir.exists() and len(list(self.spool_dir.glob("*.ndjson")))
        return {**super().stats(), "written": self.written, "dups": self.dups,
                "spooled": self.spooled, "spool_files": spool or 0, "backfilled": self.backfilled}


class SentrySink(Sink):
    """Sentry is PULLED, not pushed: 400 events/s would be absurd (docs/23 §4). This keeps the
    last 10 s so laptop code can attach it — recent() to obs.robot_failure(), peak() to the
    capture gate — and reports a fall itself, with the 2 s before it as breadcrumbs."""
    name = "sentry"

    def __init__(self, ring_s: float = 10.0, hz: int = 50, rearm_s: float = 2.0, maxsize: int = 100):
        super().__init__(maxsize)
        self.ring: deque[dict] = deque(maxlen=int(ring_s * hz))
        self.rearm_s = rearm_s
        self._armed, self._balanced_since = True, None
        self.falls = 0
        self.pi: dict[str, str] = {}             # fw + boot_id from the hello, tagged on reports

    async def handle(self, b: Batch) -> None:
        for m, w, row in b.rows():
            self.ring.append({"t_mono": m, "t_wall": w, **row})
            if not b.replay:
                self._watch_balance(m, row.get("balanced"))

    def _watch_balance(self, t_mono: float, balanced) -> None:
        if balanced == 1:
            self._balanced_since = self._balanced_since or t_mono
            if not self._armed and t_mono - self._balanced_since >= self.rearm_s:
                self._armed = True               # upright long enough: the next fall counts
        elif balanced == 0:
            self._balanced_since = None
            if self._armed:
                self._armed = False
                self.falls += 1
                self._report_fall()

    def _report_fall(self) -> None:
        before = self.span(2.0, max_points=40)
        peak = max((abs(s["tilt_rate"]) for s in before if s.get("tilt_rate") is not None), default=None)
        pitch = before[-1].get("pitch") if before else None
        self.report("fell_over", f"balanced went 0; pitch {pitch}, peak tilt_rate {peak}")

    def report(self, kind: str, detail: str, frame: bytes | None = None, **tags) -> None:
        """One Sentry issue per robot failure, carrying the 2 s before it (obs keeps the last 40
        breadcrumbs, so the 2 s is decimated to 40) and, if there is one, the camera frame the
        target was computed from. The hub is the ONLY reporter: the Pi holds no credentials and
        the executor only sees the error code."""
        import obs
        obs.robot_failure(kind, detail, telemetry=self.span(2.0, max_points=40), frame=frame,
                          source="telemetry.hub", **self.pi, **tags)
        log.warning("robot failure %s: %s — reported to Sentry", kind, detail)

    # pull API
    def recent(self, n: int = 40) -> list[dict]:
        return list(self.ring)[-n:]

    def span(self, seconds: float, max_points: int = 40) -> list[dict]:
        """The last `seconds`, evenly decimated to at most max_points samples."""
        rows = list(self.ring)
        if not rows:
            return []
        rows = [r for r in rows if r["t_mono"] >= rows[-1]["t_mono"] - seconds]
        step = max(1, math.ceil(len(rows) / max_points))
        return rows[::-1][::step][::-1]

    def window(self, t0_mono: float, t1_mono: float) -> list[dict]:
        return [r for r in self.ring if t0_mono <= r["t_mono"] <= t1_mono]

    def peak(self, signal: str, t0_mono: float, t1_mono: float) -> float | None:
        vals = [abs(r[signal]) for r in self.window(t0_mono, t1_mono) if r.get(signal) is not None]
        return max(vals) if vals else None

    def stats(self) -> dict:
        return {**super().stats(), "ring": len(self.ring), "falls": self.falls}


class SSESink(Sink):
    """2 Hz frames for the dashboard, in web/events.py's `telemetry` shape, POSTed to web's
    loopback inlet (`POST /api/internal/event`). Each frame carries the latest values plus
    the PEAK tilt_rate since the last one — decimating by sampling would hide exactly the
    spike that matters. subscribe() serves the same frames in-process."""
    name = "sse"

    def __init__(self, hz: float = 2.0, post_url: str | None = None, maxsize: int = 50):
        super().__init__(maxsize)
        self.period, self.post_url = 1.0 / hz, post_url
        self._next = 0.0
        self._peak = 0.0
        self._subs: set[asyncio.Queue] = set()
        self._http = None
        self.sent = 0

    async def handle(self, b: Batch) -> None:
        if b.replay:
            return                               # volatile: history is ES's job, not the live view's
        tr = [abs(v) for v in b.signals.get("tilt_rate", ()) if v is not None]
        self._peak = max([self._peak, *tr])
        if not (self._subs or self.post_url) or time.monotonic() < self._next:
            return
        self._next = time.monotonic() + self.period
        latest = {k: v[-1] for k, v in b.signals.items()}
        if latest.get("balanced") is not None:
            latest["balanced"] = bool(latest["balanced"])
        data = {"ts": iso_ms(b.t_wall[-1]), **latest, "tilt_rate_peak": round(self._peak, 4)}
        self._peak = 0.0
        frame = json.dumps(data, separators=(",", ":"))
        for q in list(self._subs):
            if q.full():
                q.get_nowait()
            q.put_nowait(frame)
        if self.post_url:
            await self._post(data)               # raises -> Sink.fail(): counted, frame dropped
        self.sent += 1

    async def _post(self, data: dict) -> None:
        if self._http is None:
            import httpx
            self._http = httpx.AsyncClient(timeout=0.5)
        r = await self._http.post(self.post_url, json={"event": "telemetry", "data": data})
        r.raise_for_status()

    async def subscribe(self):
        """Async iterator of JSON frames for one browser. Small queue: stale frames are worthless."""
        q: asyncio.Queue[str] = asyncio.Queue(4)
        self._subs.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subs.discard(q)

    def stats(self) -> dict:
        return {**super().stats(), "subscribers": len(self._subs), "frames": self.sent}


class RerunSink(Sink):
    """Every sample as a scalar under telemetry/<signal>, on the `time` timeline (wall-clock)."""
    name = "rerun"

    def __init__(self, addr: str, maxsize: int = 100):
        super().__init__(maxsize)
        import rerun as rr
        self.rr = rr
        self.rec = rr.RecordingStream("gitspace", recording_id=os.getenv("RERUN_RECORDING_ID"))
        self.rec.connect_grpc(addr)

    async def handle(self, b: Batch) -> None:
        await self.blocking(self._log, b)

    def _log(self, b: Batch) -> None:
        rr = self.rr
        times = rr.TimeColumn("time", timestamp=b.t_wall)
        for name, vals in b.signals.items():
            rr.send_columns(f"telemetry/{name}", indexes=[times],
                            columns=rr.Scalars.columns(scalars=[math.nan if v is None else v for v in vals]),
                            recording=self.rec)


# ── the hub ──────────────────────────────────────────────────────────────────────
class Hub:
    def __init__(self, url: str, sinks: list[Sink], frames=None, watch_heartbeat: bool | None = None):
        self.url = url
        # The plan has ONE cron monitor and docs/28 gives it to `room-clean` (robot_sentry.py).
        # The watch-loop beat is opt-in: WATCH_HEARTBEAT=1.
        self.watch_heartbeat = (os.getenv("WATCH_HEARTBEAT", "0") == "1") if watch_heartbeat is None else watch_heartbeat
        self.sinks = {s.name: s for s in sinks}
        self.frames = frames                     # telemetry.frames.FrameCache: failure photos
        self.last_capture: str | None = None     # newest capture_begin seen
        self._job_capture: OrderedDict[str, str | None] = OrderedDict()   # job -> capture before it
        self._beat_at = 0.0
        self.clock = Clock()
        self.boot: str | None = None
        self.last_mono: float | None = None
        self.connected = False
        self.batches = self.gaps = self.bad_messages = self.connects = 0
        self.jobs = Jobs()
        self.clock_lag_s: float | None = None     # min(arrival - mapped time of newest sample)
        self._lags: deque[float] = deque(maxlen=50)
        self._lag_warned = False
        self._handlers: dict[str, list[Callable[[dict], None]]] = defaultdict(list)

    def on(self, t: str, fn: Callable[[dict], None]) -> None:
        """Subscribe to other /stream traffic (job, detection, capture_begin, ...) without a
        second socket. Handlers must be quick; they run on the consumer."""
        self._handlers[t].append(fn)

    # pull API, for obs.robot_failure(telemetry=hub.recent()) and the capture gate
    def recent(self, n: int = 40) -> list[dict]:
        return self.sinks["sentry"].recent(n) if "sentry" in self.sinks else []

    def peak(self, signal: str, t0_mono: float, t1_mono: float) -> float | None:
        return self.sinks["sentry"].peak(signal, t0_mono, t1_mono) if "sentry" in self.sinks else None

    async def run(self, stats_every: float = 0) -> None:
        tasks = [asyncio.create_task(s.run(), name=f"sink:{n}") for n, s in self.sinks.items()]
        if stats_every:
            tasks.append(asyncio.create_task(self._report(stats_every), name="stats"))
        try:
            await self._consume_forever()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _consume_forever(self) -> None:
        from websockets.asyncio.client import connect
        backoff = 0.5
        while True:
            url = self.url
            if self.boot and self.last_mono is not None:
                url += f"?since={self.last_mono:.6f}&boot={self.boot}"
            try:
                async with connect(url, open_timeout=5, ping_interval=5, ping_timeout=5,
                                   max_size=2 ** 22) as ws:
                    self.connected, backoff = True, 0.5
                    self.connects += 1
                    log.info("connected to %s", self.url)
                    async for raw in ws:
                        for line in str(raw).splitlines():   # NDJSON: tolerate >1 per frame
                            if line.strip():
                                self._dispatch(line)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 -- refused, reset, timeout, bad handshake
                if self.connected or backoff == 0.5:
                    log.warning("stream: %s: %s — reconnecting", type(e).__name__, e)
            finally:
                self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 8.0)

    def _dispatch(self, line: str) -> None:
        try:
            msg = json.loads(line)
            t = msg.get("t")
            if t == "telemetry":
                self._telemetry(msg)
            elif t == "hello":
                if msg.get("boot_id") != self.boot:   # Pi restarted: nothing to replay from
                    self.boot, self.last_mono = msg.get("boot_id"), None
                if "sentry" in self.sinks:
                    self.sinks["sentry"].pi = {k: str(msg[k]) for k in ("fw", "boot_id") if k in msg}
                warn = self.clock.on_hello(msg, time.time())
                if warn:
                    log.warning(warn)
            elif t == "log":
                pi_log.log(PI_LEVELS.get(str(msg.get("level")).lower(), logging.INFO), msg.get("msg", ""))
            else:
                self._add_wall_times(msg)
                if t == "capture_begin":
                    self.last_capture = msg.get("capture_id") or self.last_capture
                elif t == "job":
                    self._job(msg)
                elif t == "detection":
                    self._watch_beat()
        except Exception as e:  # noqa: BLE001 -- one bad message must not drop the socket
            self.bad_messages += 1
            if self.bad_messages in (1, 10, 100):
                log.warning("bad message (%s: %s): %.120s", type(e).__name__, e, line)
            return
        for fn in self._handlers.get(t, ()):
            try:
                fn(msg)
            except Exception as e:  # noqa: BLE001
                log.warning("handler for %r failed: %s", t, e)

    def _job(self, msg: dict) -> None:
        jid = str(msg.get("id", ""))
        if jid and jid not in self._job_capture:     # the capture the job was planned from
            self._job_capture[jid] = self.last_capture
            while len(self._job_capture) > 256:
                self._job_capture.popitem(last=False)
        self.jobs.update(msg)
        if msg.get("state") == "failed" and "sentry" in self.sinks:
            cap = self._job_capture.get(jid)
            frame = self.frames.get(cap, msg.get("camera")) if (self.frames and cap) else None
            self.sinks["sentry"].report(str(msg.get("error") or "job_failed"), str(msg.get("detail", "")),
                                        frame=frame, job_id=jid, capture_id=cap or "")

    def _watch_beat(self) -> None:
        """Only the daemon hub (the one with the Sentry sink) beats: one monitor, one source."""
        if not self.watch_heartbeat or "sentry" not in self.sinks or time.monotonic() - self._beat_at < WATCH_EVERY_S:
            return
        self._beat_at = time.monotonic()
        import obs
        obs.heartbeat(WATCH_SLUG, "ok", monitor_config=WATCH_MONITOR)

    def _add_wall_times(self, msg: dict) -> None:
        """Every Pi `*_mono` timestamp gets a `*_wall` twin (epoch s) from the SAME mapping the
        telemetry uses, so a capture's @timestamp and the telemetry around its shutter cannot
        disagree about when it happened. A capture_begin also gets `ts` (ISO): that, and nothing
        stamped on the laptop, is what room-clouds' @timestamp must be (docs/22 §3)."""
        if self.clock.offset is None:
            return
        for k in [k for k, v in msg.items() if k.endswith("_mono") and isinstance(v, (int, float))]:
            msg[k[:-5] + "_wall"] = round(self.clock.wall(msg[k]), 3)
        for f in msg.get("frames") or ():
            if isinstance(f, dict) and isinstance(f.get("t_mono"), (int, float)):
                f["t_wall"] = round(self.clock.wall(f["t_mono"]), 3)
        if "t_capture_wall" in msg:
            msg["ts"] = iso_ms(msg["t_capture_wall"])

    def _telemetry(self, msg: dict) -> None:
        if self.clock.offset is None:
            raise ValueError("telemetry before hello")
        hz, m0 = msg["hz"], msg["from_mono"]
        signals = {k: tuple(v) for k, v in msg["signals"].items()}
        n = len(next(iter(signals.values())))
        mono = tuple(m0 + i / hz for i in range(n))
        replay = bool(msg.get("replay"))
        if not replay and self.last_mono is not None and mono[0] - self.last_mono > 1.5 / hz:
            self.gaps += 1                        # a hole the replay did not cover
        self.last_mono = max(self.last_mono or -math.inf, mono[-1])
        b = Batch(mono, tuple(m + self.clock.offset for m in mono), signals, replay)
        if not replay:
            self._check_lag(b.t_wall[-1])
        self.batches += 1
        for s in self.sinks.values():
            s.offer(b)

    def _check_lag(self, newest_wall: float) -> None:
        """A live batch leaves the Pi right after its newest sample, so arrival minus that sample's
        mapped wall time is mapping error + network delay; its MINIMUM over 5 s is ~ the mapping
        error. Large either way means Pi-clock timestamps are off against the laptop's."""
        self._lags.append(time.time() - newest_wall)
        if len(self._lags) == self._lags.maxlen:
            self.clock_lag_s = min(self._lags, key=abs)
            if abs(self.clock_lag_s) > CLOCK_LAG_WARN_S and not self._lag_warned:
                self._lag_warned = True
                log.warning("telemetry clock mapping is off by ~%+.0f ms against the laptop clock",
                            self.clock_lag_s * 1e3)

    def stats(self) -> dict:
        return {"connected": self.connected, "connects": self.connects, "batches": self.batches,
                "clock_lag_ms": None if self.clock_lag_s is None else round(self.clock_lag_s * 1e3, 1),
                "gaps": self.gaps, "bad": self.bad_messages,
                **{n: s.stats() for n, s in self.sinks.items()}}

    async def _report(self, every: float) -> None:
        while True:
            await asyncio.sleep(every)
            log.info("stats %s", json.dumps(self.stats(), separators=(",", ":")))


class JobWatcher:
    """Hub.jobs for a process that is not the hub daemon — `room revert`, the web server's
    /api/command, the agent. Runs a SINKLESS hub on its own thread: it reads /stream for `job`
    messages and writes nothing anywhere, so the daemon stays the only writer and the only
    Sentry reporter. Start it BEFORE the POST that creates the job.

        watcher = JobWatcher(PI_STREAM_URL).start()
        job_id = post_arm(...)["job_id"]
        job = watcher.jobs.wait(job_id, timeout=60)
        if job["state"] == "failed": raise RobotError(job["error"], job.get("detail", ""))
    """

    def __init__(self, url: str):
        self.hub = Hub(url, [])
        self.jobs = self.hub.jobs
        self._thread = threading.Thread(target=lambda: asyncio.run(self.hub.run()), daemon=True,
                                        name="job-watcher")

    def start(self) -> "JobWatcher":
        self._thread.start()
        return self


async def until_signalled(coro) -> None:
    """Run coro; SIGINT or SIGTERM (what systemd sends) cancels it so its cleanup runs."""
    task = asyncio.current_task()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, task.cancel)
    await coro


def build_sinks(no_es: bool = False, no_rerun: bool = False) -> list[Sink]:
    sinks: list[Sink] = []
    if not no_es:
        sys.path.insert(0, str(ROOT / "elastic"))
        spool = Path(os.path.expanduser(os.getenv("TELEMETRY_SPOOL", "~/.cache/gitspace/telemetry-spool")))
        sinks.append(ElasticSink(spool))
    port = os.getenv("WEB_BIND", "127.0.0.1:8000").rpartition(":")[2]
    sinks += [SentrySink(),
              SSESink(post_url=os.getenv("WEB_EVENTS_URL", f"http://127.0.0.1:{port}/api/internal/event"))]
    if not no_rerun:
        try:
            sinks.append(RerunSink(os.getenv("RERUN_ADDR", "rerun+http://127.0.0.1:9876/proxy")))
        except Exception as e:  # noqa: BLE001 -- no rerun installed: three sinks still run
            log.warning("rerun sink disabled: %s", e)
    return sinks


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    sys.path.insert(0, str(ROOT))
    import obs
    ap = argparse.ArgumentParser(description="Consume the Pi's /stream and fan out to four sinks.")
    ap.add_argument("--pi", default=f"ws://{os.getenv('PI_HOST', '127.0.0.1')}:{os.getenv('PI_PORT', '8080')}/stream")
    ap.add_argument("--no-es", action="store_true")
    ap.add_argument("--no-rerun", action="store_true")
    ap.add_argument("--stats-every", type=float, default=10.0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s  %(message)s")
    obs.init("laptop")
    from telemetry.frames import FrameCache
    hub = Hub(args.pi, build_sinks(args.no_es, args.no_rerun), frames=FrameCache())
    try:
        asyncio.run(until_signalled(hub.run(stats_every=args.stats_every)))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass                                  # the sinks already spooled what they held
    finally:
        obs.flush()


if __name__ == "__main__":
    main()
