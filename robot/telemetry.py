#!/usr/bin/env python3
"""robot/telemetry.py — the Pi's telemetry tap. Design: docs/23-telemetry.md.

  tap     a 50 Hz sampler THREAD reading `source()`, sample-and-hold over whatever the
          balance loop publishes. It runs whether or not anything is listening.
  ring    the last 10 s, always. recent() is what obs.robot_failure() attaches; window() and
          peak() answer "what was tilt_rate around the shutter?" after the fact; stream()
          replays it so a reconnecting laptop gets no hole in the time series.
  batch   one message per 100 ms carrying 5 samples per signal: 400 msg/s becomes 10.
  push    stream(send) is the ONE writer for a /stream client. Every client gets its own
          bounded queue with drop-oldest, so a stalled laptop can never back-pressure the
          sampler or the balance loop. publish() puts job/log/detection on the same socket.

Timestamps are Pi monotonic, always (docs/22 §3). Sample k is stamped at its scheduled tick,
t_mono_base + k/hz, so a batch is exactly `from_mono + i/hz` however batches are grouped. A
replayed sample therefore maps to the same @timestamp it had live and dedupes in the TSDS.

Wiring into robot/server.py (FastAPI), once the balance-loop source exists:

    tel = Telemetry(source=read_balance_state).start()      # at import, before any client

    @app.websocket("/stream")
    async def stream(ws: WebSocket, since: float | None = None, boot: str = ""):
        await ws.accept()
        await ws.send_text(json.dumps(tel.hello(cameras=[...], arm=True, fw=FW)))
        await tel.stream(ws.send_text, since if boot == tel.boot_id else None)

    obs.robot_failure("grasp_slipped", detail, telemetry=tel.recent(40))

The capture gate's tilt_rate_max is the peak |tilt_rate| within ±100 ms of the shutter
(web/telemetry_api.py LATCH_MS), and half of that window is in the future when the shutter
fires. So robot/capture.py waits for it, and publishes the LATCH time with it — the laptop
maps t_capture_mono onto the same clock as the telemetry (hub `ts`), never its own:

    t = time.monotonic(); [c.grab() for c in cams]
    tel.publish({"t": "capture_begin", "capture_id": cid, "t_capture_mono": t,
                 "tilt_rate_max": tel.peak("tilt_rate", t - 0.1, t + 0.1, wait_s=0.3),  # None = not covered
                 "skew_ms": skew, "cameras": [...], "frames_expected": n})

Standalone, no robot needed:

    python robot/telemetry.py --fake               # ws://0.0.0.0:8080/stream, synthetic robot
    python robot/telemetry.py --fake --fall-every 30
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import math
import random
import signal
import threading
import time
import uuid
from collections import deque
from typing import Awaitable, Callable

# What the balance loop already computes (docs/23 §1). Order is the wire order.
SIGNALS = ("pitch", "tilt_rate", "left_enc", "right_enc",
           "motor_current_l", "motor_current_r", "odom_residual", "balanced")
HZ = 50
BATCH = 5            # samples per message -> 10 msg/s
RING_S = 10.0
REPLAY_CHUNK = 25    # samples per replayed message: same format, fewer frames while catching up
CLIENT_QUEUE = 100   # ~10 s of live messages per client; past that the oldest are dropped

log = logging.getLogger("gitspace.telemetry")


def _num(x: float):
    return None if x != x else round(x, 6)          # NaN is not JSON; None is


class Telemetry:
    def __init__(self, source: Callable[[], dict], hz: int = HZ, ring_s: float = RING_S):
        self.source, self.hz, self.period = source, hz, 1.0 / hz
        self.boot_id = uuid.uuid4().hex[:12]
        # Paired once, back to back, and never re-paired: every reconnect maps identically.
        # Wall base on a whole millisecond (and mono on a whole microsecond) puts every 20 ms
        # sample on an exact integer-ms @timestamp, so live and replayed copies round the same.
        self.t_mono_base = round(time.monotonic(), 6)
        self.t_wall_base = round(time.time(), 3)
        self._ring: deque[tuple[int, tuple]] = deque(maxlen=int(ring_s * hz))
        self._pending: list[tuple[int, tuple]] = []
        self._published_k = -1               # last tick that has gone out as a live batch
        self._clients: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.overruns = 0                    # ticks skipped because the Pi was too busy
        self.source_errors = 0
        self.dropped = 0                     # messages a slow client never got

    # ── tap ──────────────────────────────────────────────────────────────────────
    def start(self) -> "Telemetry":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="telemetry-tap", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _run(self) -> None:
        k = 0
        while not self._stop.is_set():
            late = time.monotonic() - (self.t_mono_base + k * self.period)
            if late < 0:
                self._stop.wait(-late)
            elif late > self.period:         # fell behind: skip ticks rather than burst
                skipped = int(late / self.period)
                self.overruns += skipped
                k += skipped
            try:
                s = self.source()
                values = tuple(float(s.get(name, math.nan)) for name in SIGNALS)
            except Exception:                # a broken source must not kill the tap
                self.source_errors += 1
                values = (math.nan,) * len(SIGNALS)
            self._record(k, values)
            k += 1

    def _record(self, k: int, values: tuple) -> None:
        with self._lock:
            self._ring.append((k, values))
            self._pending.append((k, values))
            if len(self._pending) < BATCH:
                return
            msgs = self._encode_runs(self._pending, replay=False)
            self._pending = []
            self._published_k = k
            clients = list(self._clients.items())
        for q, loop in clients:              # no clients: the ring still has it all
            for m in msgs:
                self._offer(q, loop, m)

    # ── push ─────────────────────────────────────────────────────────────────────
    def hello(self, **extra) -> dict:
        """The first message on every connection. The pairing lets the laptop map Pi
        monotonic to wall-clock without ever timestamping sensor data itself."""
        return {"t": "hello", "boot_id": self.boot_id, "hz": self.hz, "signals": list(SIGNALS),
                "t_mono_base": self.t_mono_base, "t_wall_base": self.t_wall_base,
                "t_mono_now": time.monotonic(), **extra}

    def publish(self, msg: dict) -> None:
        """job / log / detection / capture_* onto every connected /stream client. Thread-safe."""
        text = json.dumps(msg, separators=(",", ":"))
        with self._lock:
            clients = list(self._clients.items())
        for q, loop in clients:
            self._offer(q, loop, text)

    async def stream(self, send: Callable[[str], Awaitable], since_mono: float | None = None) -> None:
        """Be the writer for one client until send() raises. First replays the ring: samples
        after `since_mono`, or all of it when None. Replay and live meet at one tick, under
        one lock, so the laptop sees no gap and no duplicate at the seam."""
        q: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE)
        with self._lock:
            since_k = -1 if since_mono is None else round((since_mono - self.t_mono_base) * self.hz)
            backlog = [s for s in self._ring if since_k < s[0] <= self._published_k]
            self._clients[q] = asyncio.get_running_loop()
        try:
            for i in range(0, len(backlog), REPLAY_CHUNK):
                for m in self._encode_runs(backlog[i:i + REPLAY_CHUNK], replay=True):
                    await send(m)
            while True:
                await send(await q.get())
        finally:
            with self._lock:
                self._clients.pop(q, None)

    def _offer(self, q: asyncio.Queue, loop: asyncio.AbstractEventLoop, msg: str) -> None:
        def put() -> None:
            if q.full():
                q.get_nowait()
                self.dropped += 1
            q.put_nowait(msg)
        try:
            loop.call_soon_threadsafe(put)
        except RuntimeError:                 # that client's loop is gone
            with self._lock:
                self._clients.pop(q, None)

    def _encode_runs(self, samples: list[tuple[int, tuple]], replay: bool) -> list[str]:
        """One message per run of CONTIGUOUS ticks, so `from_mono + i/hz` is always exact."""
        out, run = [], []
        for s in samples:
            if run and s[0] != run[-1][0] + 1:
                out.append(self._encode(run, replay))
                run = []
            run.append(s)
        if run:
            out.append(self._encode(run, replay))
        return out

    def _encode(self, run: list[tuple[int, tuple]], replay: bool) -> str:
        from_mono = self.t_mono_base + run[0][0] * self.period
        cols = zip(*(v for _, v in run))
        msg = {"t": "telemetry", "from_mono": round(from_mono, 6), "hz": self.hz,
               # informational only (docs/16 §3.1); consumers must use from_mono + hello
               "from": dt.datetime.fromtimestamp(self.t_wall_base + from_mono - self.t_mono_base,
                                                 dt.timezone.utc).isoformat(timespec="milliseconds"),
               "signals": {name: [_num(x) for x in col] for name, col in zip(SIGNALS, cols)}}
        if replay:
            msg["replay"] = True
        return json.dumps(msg, separators=(",", ":"))

    # ── pull: the reason the ring buffer exists ───────────────────────────────────
    def _samples(self, keep=lambda k: True) -> list[dict]:
        with self._lock:
            snap = [s for s in self._ring if keep(s[0])]
        return [{"t_mono": round(self.t_mono_base + k * self.period, 6),
                 **{name: _num(x) for name, x in zip(SIGNALS, v)}} for k, v in snap]

    def recent(self, n: int = 40) -> list[dict]:
        """The last n samples, oldest first. What obs.robot_failure(telemetry=...) takes."""
        return self._samples()[-n:]

    def wait_until(self, t_mono: float, timeout: float) -> bool:
        """Block until the ring holds a sample at or after t_mono. False on timeout."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                last = self._ring[-1][0] if self._ring else None
            if last is not None and self.t_mono_base + last * self.period >= t_mono - 1e-6:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.period / 2)

    def covers(self, t0_mono: float, t1_mono: float) -> bool:
        """Does the ring hold the WHOLE window — first sample at or before t0, last at or after t1?"""
        with self._lock:
            if not self._ring:
                return False
            first, last = self._ring[0][0], self._ring[-1][0]
        t = lambda k: self.t_mono_base + k * self.period  # noqa: E731
        return t(first) <= t0_mono + 1e-6 and t(last) >= t1_mono - 1e-6

    def window(self, t0_mono: float, t1_mono: float) -> list[dict]:
        """Samples with t0 <= t_mono <= t1, e.g. around a shutter instant, after the fact."""
        # callers pass t_mono as published (rounded to 1 us): allow that much at the edges
        k0 = math.ceil((t0_mono - self.t_mono_base) * self.hz - 1e-3)
        k1 = math.floor((t1_mono - self.t_mono_base) * self.hz + 1e-3)
        return self._samples(lambda k: k0 <= k <= k1)

    def peak(self, signal: str, t0_mono: float, t1_mono: float, wait_s: float = 0.0) -> float | None:
        """max |signal| over the window: tilt_rate_max for the capture quality gate. Waits up to
        wait_s for the window's end to be sampled. None if the window is not fully covered —
        a half window under-reads the peak and passes the capture the gate exists to reject
        (obs.capture_quality fails a None)."""
        if wait_s > 0:
            self.wait_until(t1_mono, wait_s)
        if not self.covers(t0_mono, t1_mono):
            return None
        vals = [abs(s[signal]) for s in self.window(t0_mono, t1_mono) if s[signal] is not None]
        return max(vals) if vals else None


# ── a synthetic robot, so everything downstream works before the kit arrives ───────
class FakeRobot:
    """A gentle wobble, encoders creeping forward, a lean-and-recover every 20 s (what the
    quality gate exists to reject) and, with fall_every, a fall: balanced drops for 3 s."""

    def __init__(self, fall_every: float = 0.0, seed: int = 0):
        self.t0, self.fall_every = time.monotonic(), fall_every
        self.rng = random.Random(seed)
        self.prev_pitch, self.prev_t = 0.0, 0.0

    def __call__(self) -> dict:
        t = time.monotonic() - self.t0
        lean = 0.25 * math.exp(-((t % 20.0) - 10.0) ** 2 / 0.3)
        falling = self.fall_every > 0 and (t % self.fall_every) > self.fall_every - 3.0
        pitch = (1.2 if falling else 0.02 * math.sin(2 * math.pi * 0.7 * t) + lean)
        pitch += self.rng.gauss(0, 0.003)
        dt_ = max(t - self.prev_t, 1e-3)
        tilt_rate = (pitch - self.prev_pitch) / dt_
        self.prev_pitch, self.prev_t = pitch, t
        cur = 0.45 + 3.0 * abs(tilt_rate) * (not falling) + self.rng.gauss(0, 0.02)
        return {"pitch": pitch, "tilt_rate": tilt_rate,
                "left_enc": 0.05 * t, "right_enc": 0.049 * t,
                "motor_current_l": cur, "motor_current_r": cur * 0.97,
                "odom_residual": 0.004 + 0.0002 * t % 0.01, "balanced": 0.0 if falling else 1.0}


async def serve(tel: Telemetry, host: str, port: int, **hello_extra) -> None:
    """A minimal /stream server on `websockets`, for bench tests and for the laptop to develop
    against. robot/server.py replaces it with a FastAPI route calling the same stream()."""
    from urllib.parse import parse_qs, urlparse

    from websockets.asyncio.server import serve as ws_serve
    from websockets.exceptions import ConnectionClosed

    async def handler(ws) -> None:
        url = urlparse(ws.request.path)
        if url.path != "/stream":
            await ws.close(1008, "only /stream")
            return
        qs = parse_qs(url.query)
        same_boot = qs.get("boot", [""])[0] == tel.boot_id
        since = float(qs["since"][0]) if same_boot and "since" in qs else None
        log.info("client %s connected (since=%s)", ws.remote_address, since)
        await ws.send(json.dumps(tel.hello(**hello_extra)))
        try:
            await tel.stream(ws.send, since)
        except ConnectionClosed:
            log.info("client %s gone", ws.remote_address)

    async with ws_serve(handler, host, port) as server:
        log.info("telemetry on ws://%s:%d/stream  boot=%s", host, port, tel.boot_id)
        await server.serve_forever()


async def until_signalled(coro) -> None:
    """Run coro; SIGINT or SIGTERM (what systemd sends) cancels it so its cleanup runs."""
    task = asyncio.current_task()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, task.cancel)
    await coro


def main() -> None:
    ap = argparse.ArgumentParser(description="Serve /stream from a synthetic robot.")
    ap.add_argument("--fake", action="store_true", required=True,
                    help="synthetic balance loop (the real source is wired in robot/server.py)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--fall-every", type=float, default=0.0, metavar="S",
                    help="fall over every S seconds (0 = never)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s  %(message)s")
    tel = Telemetry(FakeRobot(fall_every=args.fall_every)).start()
    try:
        asyncio.run(until_signalled(serve(tel, args.host, args.port, fw="fake-robot", cameras=[], arm=False)))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        tel.stop()


if __name__ == "__main__":
    main()
