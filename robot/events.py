"""robot/events.py — GET /events: the Pi's structured stream as Server-Sent Events.

Split by PAYLOAD TYPE, the way docs/16 §3b splits by latency class:

    /events   SSE         everything that is JSON — hello · telemetry · job · log · capture_* .
                          Plain HTTP, reconnects by itself, resumes from Last-Event-ID, and
                          `curl -N` is a complete client.
    /frames   WebSocket   the pixels. SSE is UTF-8 only; base64 would cost +33% on 5 MB.
    /stream   WebSocket   unchanged — telemetry/hub.py's connection, with its own ?since=&boot= replay.

One consumer taps the telemetry ONCE (Telemetry.stream, its public seam — telemetry.py is not
touched), gives every message the next id, keeps the last EVENT_LOG of them, and fans out. All of
it runs on the event loop: the 50 Hz sampler thread does exactly what it did before — one
call_soon_threadsafe per batch — so nothing here can reach the balance loop, and nothing here
opens a socket of its own.

Ids are `<boot_id>:<n>`, n counting up by one from 1. Boot-scoped on purpose: after a Pi restart n
starts again, and a bare integer would let a client resume at "1234" of a DIFFERENT run and
silently skip what it never saw. Resumption, by what Last-Event-ID says:

    this boot, n still in the log   events n+1.. then live: no gap, no duplicate
    this boot, n older than the log everything kept, after a `gap` event naming the ids that are gone
    another boot (the Pi restarted) EVERYTHING this boot has logged — nothing of the new run is lost —
                                    after a `gap` with reason "boot_changed"; what the old run sent
                                    after the client's last id cannot be recovered, and it says so
    absent                          live only. (`Last-Event-ID: 0` asks for the whole log.)

The fresh `hello` that opens every connection carries the new boot_id, as on /stream.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import deque
from pathlib import Path
from typing import AsyncIterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import obs  # noqa: E402

EVENT_LOG = 1000              # events kept for resumption: about a minute at 10 telemetry msg/s
CLIENT_QUEUE = 200            # per client; past that the OLDEST is dropped — a stalled curl holds nobody up
HEARTBEAT_S = 15.0            # a comment line, so an idle proxy does not close a quiet connection
RETRY_MS = 2000               # what EventSource waits before reconnecting

HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no",      # nginx: do not buffer a stream
           "Access-Control-Allow-Origin": "*"}                           # a page on the laptop may read it


def frame(event: str, data: str, event_id: str | None = None) -> str:
    """One SSE event. `data` is one JSON document; a newline in it would END the field, so each
    line gets its own `data:` and the client joins them back (the spec's rule, not ours)."""
    head = f"id: {event_id}\n" if event_id else ""
    return head + f"event: {event}\n" + "".join(f"data: {line}\n" for line in data.split("\n")) + "\n"


class EventLog:
    def __init__(self, boot_id: str, keep: int = EVENT_LOG, client_queue: int = CLIENT_QUEUE):
        self.boot_id, self.client_queue = boot_id, client_queue
        self.n = 0
        self._ring: deque[tuple[int, str, str]] = deque(maxlen=keep)       # (n, type, json)
        self._clients: dict[asyncio.Queue, frozenset[str] | None] = {}
        self.dropped = self.served = 0

    # ── in: the one tap ──────────────────────────────────────────────────────────
    async def ingest(self, text: str) -> None:
        """Telemetry.stream()'s `send`. Runs on the event loop; never awaits anything slow."""
        try:
            kind = str(json.loads(text).get("t") or "message")
        except (ValueError, AttributeError):
            kind = "message"
        self.n += 1
        item = (self.n, kind, text)
        self._ring.append(item)
        for q, types in self._clients.items():
            if types is None or kind in types:
                if q.full():
                    q.get_nowait()
                    self.dropped += 1
                q.put_nowait(item)

    async def run(self, tel) -> None:
        """Be one /stream client of our own, for as long as the server lives."""
        await tel.stream(self.ingest, None)

    # ── out: one connection ──────────────────────────────────────────────────────
    def resume_from(self, last_event_id: str) -> tuple[list[tuple[int, str, str]], dict | None]:
        """-> (events to replay, a `gap` notice or None). See the module docstring's table."""
        if not last_event_id:
            return [], None
        if last_event_id == "0":            # "from the beginning": the whole log, and no restart to report
            return list(self._ring), None
        boot, _, n = last_event_id.rpartition(":")
        if boot != self.boot_id or not n.isdigit():
            return list(self._ring), {"reason": "boot_changed", "boot_id": self.boot_id,
                                      "last_event_id": last_event_id[:80]}
        last = int(n)
        backlog = [e for e in self._ring if e[0] > last]
        oldest = self._ring[0][0] if self._ring else self.n + 1
        gap = ({"reason": "log_overrun", "missed_from": last + 1, "missed_to": oldest - 1}
               if oldest > last + 1 else None)
        return backlog, gap                 # a hole the log no longer covers is SAID, never papered over

    async def subscribe(self, hello: dict, last_event_id: str = "", types: frozenset[str] | None = None,
                        limit: int = 0, heartbeat_s: float = HEARTBEAT_S) -> AsyncIterator[str]:
        """The body of one GET /events. `limit` ends the stream after that many events — what makes
        a curl proof (and a test) terminate by itself; 0 = until the client leaves."""
        q: asyncio.Queue = asyncio.Queue(maxsize=self.client_queue)
        backlog, gap = self.resume_from(last_event_id)
        self._clients[q] = types             # registered in the same loop turn the backlog was cut: no gap, no duplicate
        sent = beats = 0
        t0 = time.monotonic()
        with obs.span("robot.sse.open", "GET /events", resumed=bool(backlog or gap), replayed=len(backlog),
                      types=",".join(sorted(types)) if types else "*", clients=len(self._clients)):
            obs.measure(sse_clients=len(self._clients))
        try:
            yield f"retry: {RETRY_MS}\n\n"
            if types is None or "hello" in types:      # no id: a hello is this connection's, not the log's
                yield frame("hello", json.dumps({**hello, "transport": "sse"}, separators=(",", ":")))
            if gap:
                yield frame("gap", json.dumps(gap))
            pending = [e for e in backlog if types is None or e[1] in types]
            while True:
                if pending:
                    n, kind, text = pending.pop(0)
                else:
                    try:
                        n, kind, text = await asyncio.wait_for(q.get(), heartbeat_s)
                    except asyncio.TimeoutError:
                        beats += 1
                        yield ": keepalive\n\n"
                        continue
                sent += 1                   # BEFORE the yield: a client that leaves never resumes us,
                self.served += 1            # and the event it was just handed would go uncounted
                yield frame(kind, text, f"{self.boot_id}:{n}")
                if limit and sent >= limit:
                    return
        finally:
            self._clients.pop(q, None)
            with obs.span("robot.sse.close", "GET /events", events=sent, heartbeats=beats,
                          seconds=round(time.monotonic() - t0, 2), dropped_total=self.dropped):
                pass

    def stats(self) -> dict:
        return {"clients": len(self._clients), "last_id": f"{self.boot_id}:{self.n}", "kept": len(self._ring),
                "served": self.served, "dropped": self.dropped}
