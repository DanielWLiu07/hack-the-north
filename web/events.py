"""events.py — the hub behind `GET /api/events` (SSE, docs/16-api.md §4b).

Server -> browser only, so it is Server-Sent Events: `EventSource` reconnects by itself
and sends `Last-Event-ID`, and we replay what it missed from a small ring buffer.

EVENTS (the `event:` name, then the JSON in `data:`)

    status     {"clean": false, "changes": 3, "conflicts": 0, "branch": "main",
                "head": "a3f9c1", "rev": "9b1c…"}     rev changes whenever the tree does;
                                                      fetch /api/status for the detail
    capture    {"commit_sha": "a3f9c1", "branch": "main", "capture_id": "cap_0912"?}
                                                      HEAD moved: a commit landed
    conflict   {"object_id": "mug_a1b2", "zone": "desk", "merging": "movie-night",
                "ours": {"zone", "pose"}, "theirs": {"zone", "pose"}}
    job        {"id": "job_9a2f", "state": "grasping", "progress": 0.4, ...}   pushed in
    telemetry  {"ts": "2026-09-19T14:22:07.400Z", "pitch": 0.021, "tilt_rate": 0.004,
                "balanced": true, "odom_residual": 0.004?}   pushed in, 2 Hz, decimated

    room_state {"clean": false, "head": "a3f9c1", "branch": "main", "confirmed": [...], "pending": [...],
                "last_verified_job": "job_9a2f"?, "blocked": null, "at": "..."}   the watch loop's verdict
    chore      {"id", "object_id", "zone", "type", "verdict", "status": "open"|"closed", ...}
    pr         {"id", "branch", "title", "status": "open"|"merged"|"closed", ...}
    nav        {"pose": {"x", "y", "yaw"}, "status", "frame": "world_z_up", ...}   <= 2 Hz

`status`, `capture` and `conflict` come from the room watcher in this process. `job` and
`telemetry` have no source here: the executor and the laptop's ingest push them through
the loopback-only `POST /api/internal/event` (server.py). `room_state`, `chore`, `pr` and `nav`
(plan/roommate/03-interfaces.md §8) come from roomctl's watch loop and the nav bridge through
`POST /api/edge/event` (roommate_api.py): local, or with the cloud bearer token.

`telemetry` and `nav` are VOLATILE: they carry no id, are never replayed, and are dropped for a
client that is behind (docs/23-telemetry.md: "a dashboard missing a frame is fine"). Replaying a
stale pitch reading, or where the robot WAS, after a reconnect would be worse than a gap.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections import deque
from typing import AsyncIterator

import room

log = logging.getLogger("gitspace.web.events")

EVENT_NAMES = ("status", "job", "capture", "conflict", "telemetry",    # the inlets' allow-list
               "room_state", "chore", "pr", "nav")                       # the roommate plan's (03 §8)
ROOMMATE_EVENTS = ("room_state", "nav", "chore", "pr")                   # what /api/edge/event accepts

# gitirl-agent's robot-side names (awzheng/gitirl@b4f3e07), mapped EXPLICITLY onto ours instead of being
# refused. All three are about a job in flight (ANDREW-HANDOFF.md §2), so all three become `job`;
# `kind` keeps his name and `state` says what happened. Anything else is still refused.
EDGE_EVENTS = {"robot_status": "job", "robot_observation": "job", "robot_action_result": "job"}


def from_edge(name: str, data: dict) -> tuple[str, dict]:
    """(our event, our data) for one of his names; ours pass through untouched."""
    if name not in EDGE_EVENTS:
        return name, data
    ident = data.get("id") or data.get("job_id") or data.get("request_id") or \
        (data.get("metadata") or {}).get("job_id") or "robot"
    if name == "robot_action_result":
        res = data.get("result") if isinstance(data.get("result"), dict) else data
        status = str(res.get("status", "")).lower()
        # his ActionStatus (planner/models.py @ b4f3e07): success | failed | retryable. `retryable` is
        # not an ending: he retries, so the job stays open rather than flashing red
        state = {"success": "done", "retryable": "retrying"}.get(status) or \
            ("done" if status in ("ok", "done", "succeeded", "restore_complete") else "failed")
        return "job", {"id": ident, "kind": name, "state": state, "result": res}
    if name == "robot_observation":
        return "job", {"id": ident, "kind": name, "state": "observed", "observation": data.get("observation", data)}
    return "job", {"id": ident, "kind": name, "state": data.get("status", "status"), "detail": data.get("message"),
                   "metadata": data.get("metadata") or {}}


VOLATILE = {"telemetry", "nav"}
HEARTBEAT_S = 15
RETRY_MS = 2000
WATCH_EVERY_S = 1.0
_CLOSE = object()


def _frame(event: str, data: dict, event_id: str | None = None) -> bytes:
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


class Hub:
    def __init__(self, keep: int = 256, queue_size: int = 128):
        # ids are "<boot>-<n>": after a restart a client's Last-Event-ID names another
        # boot, so we replay nothing rather than the wrong thing
        self.boot = secrets.token_hex(3)
        self._n = 0
        self._ring: deque[tuple[int, bytes]] = deque(maxlen=keep)
        self._clients: set[asyncio.Queue] = set()
        self._queue_size = queue_size

    @property
    def clients(self) -> int:
        return len(self._clients)

    def publish(self, event: str, data: dict) -> None:
        if event in VOLATILE:
            frame = _frame(event, data)
        else:
            self._n += 1
            frame = _frame(event, data, f"{self.boot}-{self._n}")
            self._ring.append((self._n, frame))
        for q in list(self._clients):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                if event in VOLATILE:
                    continue                 # a slow client just misses a telemetry frame
                # too far behind for durable events: end its stream; EventSource
                # reconnects and Last-Event-ID replays what it missed from the ring
                self._clients.discard(q)
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(_CLOSE)

    def close(self) -> None:
        for q in list(self._clients):
            try:
                q.put_nowait(_CLOSE)
            except asyncio.QueueFull:
                pass
        self._clients.clear()

    def _missed(self, last_event_id: str | None) -> list[bytes]:
        if not last_event_id:
            return []
        boot, _, n = last_event_id.rpartition("-")
        if boot != self.boot or not n.isdigit():
            return []
        return [frame for i, frame in self._ring if i > int(n)]

    async def stream(self, last_event_id: str | None, hello: list[bytes]) -> AsyncIterator[bytes]:
        """One client's SSE body: retry hint, what it missed, the current state, then live."""
        q: asyncio.Queue = asyncio.Queue(self._queue_size)
        self._clients.add(q)
        try:
            yield f"retry: {RETRY_MS}\n\n".encode()
            for frame in self._missed(last_event_id):
                yield frame
            for frame in hello:              # current truth last; no id, so it never
                yield frame                  # disturbs the client's Last-Event-ID
            while True:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield b": ping\n\n"      # keeps proxies from closing an idle stream
                    continue
                if frame is _CLOSE:
                    return
                yield frame
        finally:
            self._clients.discard(q)


hub = Hub()


def status_frames() -> list[bytes]:
    """What a client is told the moment it connects: the state right now."""
    try:
        snap = room.snapshot()
    except room.RoomError as e:
        return [_frame("status", {"error": "room_unavailable", "detail": str(e)})]
    frames = [_frame("status", room.summary(snap))]
    frames += [_frame("conflict", _conflict(c, snap)) for c in snap["conflicts"]]
    return frames


def _conflict(c: dict, snap: dict) -> dict:
    return {"object_id": c["object_id"], "zone": c["zone"], "merging": snap["merging"],
            "ours": c["ours"], "theirs": c["theirs"]}


async def watch_room() -> None:
    """Poll the room ~1/s (git runs in a thread, never on the loop); publish on change only."""
    prev: dict | None = None
    last_error = None
    while True:
        try:
            snap = await asyncio.to_thread(room.snapshot)
            last_error = None
        except room.RoomError as e:
            if str(e) != last_error:
                last_error = str(e)
                log.warning("room watcher: %s", e)
            await asyncio.sleep(WATCH_EVERY_S * 3)
            continue
        except Exception:                    # never let the watcher die
            log.exception("room watcher failed; retrying")
            await asyncio.sleep(WATCH_EVERY_S * 3)
            continue
        if prev is not None:
            if snap["head"] != prev["head"] and snap["head"]:
                hub.publish("capture", {"commit_sha": snap["head"], "branch": snap["branch"]})
            if snap["rev"] != prev["rev"] or snap["head"] != prev["head"] or snap["branch"] != prev["branch"]:
                hub.publish("status", room.summary(snap))
            known = {c["path"] for c in prev["conflicts"]}
            for c in snap["conflicts"]:
                if c["path"] not in known:
                    hub.publish("conflict", _conflict(c, snap))
        prev = snap
        await asyncio.sleep(WATCH_EVERY_S)
