"""The executor's Robot, over the Pi's API (docs/16 §2): HTTP starts a motion and answers
202 with a job_id at once; how it ENDED arrives later on the /stream WebSocket, which the
telemetry hub consumes. So this client posts, then blocks on a `jobs.wait(job_id, timeout)`
— `telemetry.hub.Jobs` in production, a fake in tests.

Retry rules, because a robot arm is not an idempotent API:
- A request that provably never reached the Pi (connection refused) is retried.
- `busy` (another job is running) is retried after a short wait — nothing started.
- Anything that MAY have started (a timeout after sending, a 5xx) is never retried: a second
  `pick` could close on a mug the first one already lifted. It is reported instead.
- `/say` and `/led` never raise: a missing voice line must not stop a pick.

Frames (docs/16, docs/20 Fact 3): `/drive` and `/pose` speak BB's odometry axes {x forward,
z left, yaw in RADIANS}; `/arm` speaks our world frame {x, y, z, yaw in DEGREES}. This
assumes /drive's target is anchor-registered (world), so z = world y. If robot/ says
otherwise, `to_drive_target` is the one place to change (docs/10 D15).
"""
from __future__ import annotations

import json
import logging
import math
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Callable, Protocol

from roomctl.executor import BasePose, RobotError
from roomctl.state import Pose

log = logging.getLogger("roomctl.robot")

try:
    import obs  # spans + robot_failure: no-ops unless this process called obs.init()
except ImportError:  # pragma: no cover
    obs = None

Send = Callable[[str, str, dict | None], tuple[int, dict]]


class Jobs(Protocol):
    def wait(self, job_id: str, timeout: float) -> dict: ...


class NotSent(Exception):
    """The request never left this machine — safe to retry anything."""


def to_drive_target(b: BasePose) -> dict:
    return {"x": round(b.x, 3), "z": round(b.y, 3), "yaw": round(math.radians(b.yaw), 4)}


def to_arm_pose(p: Pose) -> dict:
    return {"x": round(p.x, 3), "y": round(p.y, 3), "z": round(p.z, 3), "yaw": p.yaw}


def http_send(base_url: str, timeout: float = 5.0) -> Send:
    """JSON over urllib. Distinguishes 'never sent' (retryable) from 'sent, no answer' (not)."""
    def send(method: str, path: str, body: dict | None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{base_url.rstrip('/')}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}
        except urllib.error.URLError as e:
            if isinstance(e.reason, (ConnectionRefusedError, socket.gaierror)):
                raise NotSent(str(e.reason)) from None
            raise RobotError("robot_timeout", f"{method} {path}: {e.reason}") from None
        except (TimeoutError, socket.timeout):
            raise RobotError("robot_timeout", f"{method} {path}: no answer in {timeout:g} s") from None
    return send


class HttpRobot:
    def __init__(self, send: Send, jobs: Jobs | None, *, drive_timeout_s: float = 45.0,
                 arm_timeout_s: float = 60.0, retries: int = 3, backoff_s: float = 0.5,
                 sleep: Callable[[float], None] = time.sleep, voice: str = "elevenlabs"):
        self.send, self.jobs = send, jobs
        self.drive_timeout_s, self.arm_timeout_s = drive_timeout_s, arm_timeout_s
        self.retries, self.backoff_s, self.sleep, self.voice = retries, backoff_s, sleep, voice
        self.dropped = 0  # fire-and-forget calls that failed
        self.context: dict = {}  # e.g. {"capture_id": ...}: tags on the issues THIS client files

    @classmethod
    def from_env(cls, jobs: Jobs | None) -> HttpRobot:
        host, port = os.getenv("PI_HOST", "").strip(), os.getenv("PI_PORT", "8080").strip()
        if not host:
            raise RobotError("robot_unconfigured", "PI_HOST is not set in .env")
        return cls(http_send(f"http://{host}:{port}"), jobs)

    # -- the Robot protocol (roomctl/executor.py) -----------------------------

    def drive(self, base: BasePose) -> None:
        body = {"target": to_drive_target(base), "speed": 0.25, "timeout_s": self.drive_timeout_s}
        self._job("drive", "/drive", body, self.drive_timeout_s)

    def pick(self, object_id: str, pose: Pose) -> None:
        body = {"action": "pick", "pose": to_arm_pose(pose), "approach": "top_down", "speed": 0.15}
        self._job("pick", "/arm", body, self.arm_timeout_s, object_id=object_id)

    def place(self, object_id: str, pose: Pose, zone: str) -> None:
        body = {"action": "place", "pose": to_arm_pose(pose), "speed": 0.15}
        self._job("place", "/arm", body, self.arm_timeout_s, object_id=object_id, zone=zone)

    def say(self, text: str) -> None:
        self._fire("/say", {"text": text, "voice": self.voice})

    def led(self, state: str) -> None:
        self._fire("/led", {"state": state})

    def pose(self) -> dict:
        """GET /pose — the cheap reachability check before any plan moves anything."""
        try:
            status, body = self.send("GET", "/pose", None)
        except NotSent as e:
            raise RobotError("robot_unreachable", str(e)) from None
        if status != 200:
            raise RobotError(body.get("error", f"http_{status}"), body.get("detail", ""))
        return body

    # -- mechanics ----------------------------------------------------------

    def _post(self, path: str, body: dict) -> dict:
        """POST until 202, retrying only what provably didn't start."""
        for attempt in range(self.retries + 1):
            try:
                status, resp = self.send("POST", path, body)
            except NotSent as e:
                if attempt == self.retries:
                    raise RobotError("robot_unreachable", str(e)) from None
                self.sleep(self.backoff_s * 2 ** attempt)
                continue
            if status == 202 and resp.get("job_id"):
                return resp
            code = resp.get("error") or f"http_{status}"
            if code == "busy" and attempt < self.retries:
                self.sleep(self.backoff_s * 2 ** attempt)
                continue
            raise RobotError(code, resp.get("detail", ""))
        raise RobotError("busy", f"still busy after {self.retries} retries")  # pragma: no cover

    def _job(self, kind: str, path: str, body: dict, timeout: float, **tags) -> dict:
        desc = f"arm.{kind} {tags['object_id']}" if "object_id" in tags else f"{kind} pi"
        span = obs.span(f"robot.{kind}", desc, robot="pi", **tags) if obs else _null()
        with span:
            try:
                job_id = self._post(path, body)["job_id"]
                if self.jobs is None:
                    raise RobotError("no_job_stream", f"{kind} {job_id} started, but nothing is reading "
                                                      f"/stream to say how it ended")
                try:
                    msg = self.jobs.wait(job_id, timeout)
                except TimeoutError:
                    raise RobotError("job_timeout", f"{kind} {job_id}: no done/failed within {timeout:g} s") from None
                if msg.get("state") == "done":
                    return msg.get("result") or {}
                # A failure the Pi reported on /stream: the telemetry hub files THAT issue, with
                # the lean and the camera frame. Filing it again here makes a stray duplicate
                # nobody resolves (docs/10 D25).
                err = RobotError(msg.get("error") or "failed", msg.get("detail", ""))
                err.from_stream = True
                raise err
            except RobotError as e:
                if obs and not getattr(e, "from_stream", False):  # only what the hub never sees
                    obs.robot_failure(e.code, e.detail or kind, action=kind, **self.context, **tags)
                raise

    def _fire(self, path: str, body: dict) -> None:
        try:
            status, resp = self.send("POST", path, body)
            if status >= 400:
                raise RobotError(resp.get("error", f"http_{status}"), resp.get("detail", ""))
        except (NotSent, RobotError) as e:
            self.dropped += 1
            log.warning("robot %s dropped: %s", path, e)


class _null:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


# ── job progress to the dashboard: web's loopback inlet (web/server.py) ───────

def web_inlet(bind: str | None = None, timeout: float = 0.3) -> Callable[[str, dict], None]:
    """`publish("job", {...})` to POST /api/internal/event. Best effort, never raises, never
    waits long: the dashboard missing a frame must not stop the robot."""
    host, _, port = (bind or os.getenv("WEB_BIND", "127.0.0.1:8000")).rpartition(":")
    host = host.strip("[]") or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"  # the inlet only accepts loopback peers
    url = f"http://{host}:{port}/api/internal/event"

    def publish(event: str, data: dict) -> None:
        req = urllib.request.Request(url, data=json.dumps({"event": event, "data": data}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=timeout).close()
        except (urllib.error.URLError, OSError, ValueError):
            pass
    return publish
