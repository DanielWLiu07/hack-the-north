"""Who deciphers the panel's text: Andrew's live gitirl-agent, or our stub — and it always says which.

  WsHub        his agent connected to OUR /ws/gitirl-agent -> "andrew:ws". RETIRED on his side:
               awzheng/gitirl@b4f3e07 deleted transport/websocket_client.py for HTTP+SSE, so today
               nothing dials in — kept only so an old agent still works.
  JsonlBridge  his repo at ANDREW_REPO, `scripts/run_dev.py --jsonl`, one long-lived process -> "andrew:jsonl"
               (what actually serves). Spawned with every GITIRL_* variable REMOVED: since b4f3e07
               his dev app drives a real robot when GITIRL_ROBOT_BASE_URL is set, and it executes
               what it parses — deciphering text must never be able to move anything.
  stub_parse   our copy of his parser grammar (unchanged from 35f4c96 through b4f3e07) -> "stub"

ANDREW_BRIDGE=auto (default: ws > jsonl > stub) | ws | jsonl | stub.
Each returns his envelopes for the request, untouched; deciding what to DO with them is not here.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from bridge.contract import ContractError, envelope

TIMEOUT_S = 5.0
# ANDREW-HANDOFF.md §2: a user_command is answered by parsed_command OR error, and nothing else —
# command_result comes only in reply to a robot_action. Waiting for one would time out every
# decipher the moment he follows the contract. Anything trailing for the same request inside
# GRACE_S (his dev orchestrator's mock result, today) is still collected so it shows as `ignored`.
TERMINAL = ("parsed_command", "error")
GRACE_S = 0.15


# ── the stub: his DeterministicIntentParser's grammar (awzheng/gitirl@35f4c96 commands/parser.py) ──
_RESTORE = (re.compile(r"^restore\s+(?P<state>[\w-]+)$"),
            re.compile(r"^set my room back to\s+(?P<state>[\w-]+?)(?:\s+mode)?$"))
_COMMIT = (re.compile(r"^commit\s+(?P<state>[\w-]+)$"), re.compile(r"^save this as\s+(?P<state>[\w-]+)$"))
_DIFF = re.compile(r"^(?:show(?: me)? (?:the )?)?diff(?:\s+(?P<state>[\w-]+))?$")
_EXACT = {"add": "add", "status": "status", "diff": "diff", "show diff": "diff",
          "show me the diff": "diff", "log": "log", "show log": "log"}


def stub_parse(text: str, request_id: str) -> list[dict]:
    n = " ".join(text.strip().lower().split())
    n = n[len("gitirl "):] if n.startswith("gitirl ") else n
    cmd, state = _EXACT.get(n), None
    if cmd is None and (m := _DIFF.fullmatch(n)):
        cmd, state = "diff", m.group("state")
    for kind, pats in (("restore", _RESTORE), ("commit", _COMMIT)):
        for p in pats if cmd is None else ():
            if m := p.fullmatch(n):
                cmd, state = kind, m.group("state")
                break
    if cmd is None:
        return [envelope("error", request_id, {"code": "unknown_command",
                                               "message": "Unknown or ambiguous command", "details": {}})]
    return [envelope("parsed_command", request_id, {"command": cmd, "target_state": state})]


# ── his real parser, as a subprocess speaking his JSON-lines protocol ─────────────────────────
class JsonlBridge:
    def __init__(self, repo: str | None = None):
        self.repo = Path(os.path.expanduser(repo or os.getenv("ANDREW_REPO", "~/.cache/gitspace/gitirl-agent")))
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return (self.repo / "scripts" / "run_dev.py").is_file()

    @staticmethod
    def env() -> dict:
        """Ours minus every GITIRL_* (robot, cloud, camera, state file): his mock adapter, always."""
        return {k: v for k, v in os.environ.items() if not k.startswith("GITIRL_")}

    def revision(self) -> str | None:
        r = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True)
        return r.stdout.strip() or None

    def _start(self) -> subprocess.Popen:
        if self._proc is None or self._proc.poll() is not None:
            # unbuffered bytes: select() on the pipe then tells the truth about what is waiting
            self._proc = subprocess.Popen([sys.executable, "scripts/run_dev.py", "--jsonl"], cwd=self.repo,
                                          env=self.env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, bufsize=0)
            self._buf = b""
        return self._proc

    def _line(self, timeout: float):
        """The next line, or None if nothing arrives within `timeout`, or "" at EOF. Our own buffer:
        a text wrapper would read ahead and hide a waiting line from select()."""
        import select
        fd = self._proc.stdout.fileno()
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buf:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                return None
            chunk = os.read(fd, 65536)
            if not chunk:
                return ""
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode("utf-8", "replace")

    def decipher(self, env: dict) -> list[dict]:
        """Blocking: one request in, his envelopes for it out — through the answer (parsed_command |
        error), then whatever else for the same request arrives within GRACE_S."""
        with self._lock:
            proc = self._start()
            proc.stdin.write((json.dumps(env) + "\n").encode())
            proc.stdin.flush()
            out: list[dict] = []
            answered = False
            deadline = time.monotonic() + TIMEOUT_S
            while True:
                line = self._line(GRACE_S if answered else max(0.0, deadline - time.monotonic()))
                if line is None:
                    if answered:
                        return out
                    self.close()                        # a wedged process is restarted next time
                    raise ContractError("bridge_unavailable", f"gitirl-agent (jsonl) did not answer in {TIMEOUT_S:g} s", 504)
                if line == "":
                    self.close()
                    if answered:
                        return out
                    raise ContractError("bridge_unavailable", "gitirl-agent (jsonl) exited", 503)
                try:
                    msg = json.loads(line)
                except ValueError:                      # a human-readable print() of his: not protocol
                    continue
                if isinstance(msg, dict) and msg.get("request_id") == env["request_id"]:
                    out.append(msg)                     # stale lines of earlier requests are skipped
                    answered = answered or msg.get("type") in TERMINAL

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
        self._proc = None


# ── his agent as a WebSocket CLIENT of ours: he connects to /ws/gitirl-agent ──────────────────
class WsHub:
    """He dials in (GITIRL_WS_URL); we send user_command down that socket and collect his replies by
    request_id. One agent at a time: a new connection replaces the old."""

    def __init__(self):
        self.socket = None
        self._waiting: dict[str, tuple[asyncio.Queue, asyncio.AbstractEventLoop]] = {}

    def connected(self) -> bool:
        return self.socket is not None

    def deliver(self, msg: dict) -> None:
        """Called by the socket reader for every message he sends."""
        w = self._waiting.get(msg.get("request_id"))
        if w:
            w[0].put_nowait(msg)

    async def decipher(self, env: dict) -> list[dict]:
        if self.socket is None:
            raise ContractError("bridge_unavailable", "no gitirl-agent connected to /ws/gitirl-agent", 503)
        q: asyncio.Queue = asyncio.Queue()
        self._waiting[env["request_id"]] = (q, asyncio.get_running_loop())
        try:
            await self.socket.send_text(json.dumps(env))
            out: list[dict] = []
            while True:
                msg = await asyncio.wait_for(q.get(), TIMEOUT_S)
                out.append(msg)
                if msg.get("type") in TERMINAL:
                    break
            while True:                                 # the grace window: trailing messages, if any
                try:
                    out.append(await asyncio.wait_for(q.get(), GRACE_S))
                except (asyncio.TimeoutError, TimeoutError):
                    return out
        except (asyncio.TimeoutError, TimeoutError):
            raise ContractError("bridge_unavailable", f"gitirl-agent (ws) did not answer in {TIMEOUT_S:g} s", 504)
        finally:
            self._waiting.pop(env["request_id"], None)


HUB = WsHub()
JSONL = JsonlBridge()


def will_serve(mode: str | None = None) -> str:
    """Since the Sat 13:00 split (plan/roommate/03-interfaces.md §12) the six verbs are OUR grammar
    (`stub_parse`, served as "gitspace:grammar"). His jsonl / ws parsers are test doubles, used only
    when ANDREW_BRIDGE names them; `stub` keeps the old label for the tests that pin it."""
    mode = mode or os.getenv("ANDREW_BRIDGE", "ours")
    if mode == "ours":
        return "gitspace:grammar"
    if mode == "stub":
        return "stub"
    if mode == "ws" or (mode == "auto" and HUB.connected()):
        return "andrew:ws"
    if mode == "jsonl" or (mode == "auto" and JSONL.available()):
        return "andrew:jsonl"
    return "stub"


async def decipher(env: dict, mode: str | None = None) -> tuple[str, list[dict]]:
    """(served_by, his envelopes). A forced live mode that can't answer is an error, never a quiet
    fall back to the stub — the panel must not show a stub answer as his."""
    who = will_serve(mode)
    if who == "andrew:ws":
        return who, await HUB.decipher(env)
    if who == "andrew:jsonl":
        if not JSONL.available():
            raise ContractError("bridge_unavailable", f"ANDREW_REPO has no scripts/run_dev.py ({JSONL.repo})", 503)
        return who, await asyncio.to_thread(JSONL.decipher, env)
    return (who if who == "gitspace:grammar" else "stub"), stub_parse(env["payload"]["text"], env["request_id"])
