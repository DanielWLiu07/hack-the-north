"""robot/balance_source.py — the balance loop's state, as robot/telemetry.py wants it.

    ROBOT_TELEMETRY_SOURCE=robot.balance_source:udp      (runbook: robot/RUNBOOK.md)

The telemetry tap calls source() 50 times a second from its own thread and expects a dict of the
eight signals of docs/23 §1, NOW, without waiting: it is sample-and-hold over whatever the balance
loop last said. So a source is two halves — something that RECEIVES the loop's state whenever it
arrives, and a read() that hands back the latest instantly.

`Held` is that second half, with the one rule that matters: **a value that stopped arriving
expires.** Plain sample-and-hold over a balance loop that crashed returns its last tilt_rate
forever — a constant — which reads as a perfectly still robot, and the capture gate would PASS
every capture on the evidence of a dead sensor. After STALE_S with nothing new, read() returns {}:
the tap records NaN, tel.peak() has nothing, and the gate rejects (obs.capture_quality fails None).

`udp` is a complete first half needing nothing installed: the balance loop sends its state as one
JSON datagram per tick to 127.0.0.1:8765 (five lines, in the runbook). Loopback UDP never blocks
the sender and never leaves the Pi. If BB's loop already publishes somewhere (a topic, a DORA
node), write a receiver that calls HELD.put(state) instead — read() and the rest stay as they are.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time

SIGNALS = ("pitch", "tilt_rate", "left_enc", "right_enc",
           "motor_current_l", "motor_current_r", "odom_residual", "balanced")
STALE_S = 0.1                 # five 20 ms ticks with nothing new: the loop is gone, not still
UDP_PORT = int(os.getenv("ROBOT_BALANCE_UDP_PORT", "8765"))

log = logging.getLogger("gitspace.balance_source")


class Held:
    """The latest state and when it arrived. Thread-safe; read() never blocks on the writer's I/O."""

    def __init__(self, stale_s: float = STALE_S):
        self.stale_s = stale_s
        self._state: dict = {}
        self._at = 0.0
        self._lock = threading.Lock()
        self.received = 0

    def put(self, state: dict) -> None:
        with self._lock:
            self._state, self._at = state, time.monotonic()
            self.received += 1

    def read(self) -> dict:
        with self._lock:
            fresh = time.monotonic() - self._at <= self.stale_s
            return dict(self._state) if fresh else {}

    def age(self) -> float:
        with self._lock:
            return time.monotonic() - self._at if self._at else float("inf")


HELD = Held()
_listener: threading.Thread | None = None
_start = threading.Lock()


def _listen(sock: socket.socket, held: Held) -> None:
    while True:
        try:
            data, _ = sock.recvfrom(4096)
            state = json.loads(data)
            if isinstance(state, dict):
                held.put(state)
        except OSError:
            return                                # socket closed
        except ValueError:
            pass                                  # one bad datagram is not a dead loop


def listen(port: int = UDP_PORT, held: Held = HELD) -> socket.socket:
    """Bind 127.0.0.1:<port> and feed `held` from a daemon thread. Loopback only: the balance
    loop's state is not an API."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))
    threading.Thread(target=_listen, args=(sock, held), name="balance-udp", daemon=True).start()
    log.info("balance state expected on udp://127.0.0.1:%d", sock.getsockname()[1])
    return sock


def udp() -> dict:
    """ROBOT_TELEMETRY_SOURCE=robot.balance_source:udp — the listener starts on the first call."""
    global _listener
    if _listener is None:
        with _start:
            if _listener is None:
                listen()
                _listener = threading.current_thread()
    return HELD.read()
