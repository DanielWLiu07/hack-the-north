"""robot/jobs.py — /drive /arm /say /led: answer at once with a job id, narrate on /stream.

docs/16 §3.2: a 30 s pick must not be a 30 s HTTP request. The POST returns 202 + job_id in
milliseconds; every state change is a `job` message on the socket; `done` or `failed` ends it.

The rules that belong to the API rather than to any one actuator live here (docs/16 §2.7):
    not_balanced     the arm is refused while the robot is recovering or down
    busy             never two arm jobs at once
    job_superseded   a newer /drive replaces the one still running

The actuators themselves are SIMULATED. robot/nav.py, arm.py, audio.py and led.py are not built
(robot/README.md: nav wraps BB's daemon, arm is LeRobot on the STS3215 bus), so outside sim/replay
every one of these answers 503 `backend_unavailable` rather than pretending to have moved.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from typing import Callable

ARM_STATES = {"pick": ("moving_to_pick", "grasping", "lifting"), "place": ("placing", "releasing"),
              "point": ("pointing",), "stow": ("stowing",)}
ARM_SECONDS = {"pick": 28.0, "place": 22.0, "point": 8.0, "stow": 6.0}      # at the default speed
ARM_SPEED = 0.15              # low on purpose: arm ACCELERATION is what the balance loop must reject
LED_STATES = ("clean", "dirty", "conflict", "working", "error")


class JobError(Exception):
    def __init__(self, code: str, detail: str, status: int = 400, retryable: bool = False):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.status, self.retryable = code, detail, status, retryable


def _num(d: dict, key: str, what: str) -> float:
    v = d.get(key) if isinstance(d, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise JobError("bad_request", f"{what}.{key} must be a finite number")
    return float(v)


class Jobs:
    def __init__(self, publish: Callable[[dict], None], base, balanced: Callable[[], bool], *,
                 sim: bool, time_scale: float = 1.0):
        self.publish, self.base, self.balanced = publish, base, balanced
        self.sim, self.time_scale = sim, time_scale
        self.led_state = "clean"
        self._arm = threading.Lock()
        self._drive_cancel: threading.Event | None = None
        self._lock = threading.Lock()

    # ── the endpoints ────────────────────────────────────────────────────────────
    def drive(self, body: dict) -> dict:
        target = body.get("target")
        x, z, yaw = (_num(target, k, "target") for k in ("x", "z", "yaw"))     # BB's axes, yaw rad
        speed = float(body.get("speed", 0.25))
        if not 0 < speed <= 1.0:
            raise JobError("bad_request", "speed must be in (0, 1] m/s")
        self._need_backend("nav.py")
        cancel = threading.Event()
        with self._lock:                                   # the newer command wins
            if self._drive_cancel is not None:
                self._drive_cancel.set()
            self._drive_cancel = cancel
        job_id = self._start(self._run_drive, (x, z, yaw), speed, float(body.get("timeout_s", 30)), cancel)
        return {"job_id": job_id, "accepted": True}

    def arm(self, body: dict) -> dict:
        action = body.get("action")
        if action not in ARM_STATES:
            raise JobError("bad_request", f"action must be one of {sorted(ARM_STATES)}")
        if action != "stow":
            for k in ("x", "y", "z"):                      # F_world, metres; yaw degrees (docs/16 §2.4)
                _num(body.get("pose"), k, "pose")
        speed = float(body.get("speed", ARM_SPEED))
        if not 0 < speed <= 1.0:
            raise JobError("bad_request", "speed must be in (0, 1]")
        self._need_backend("arm.py")
        if not self.balanced():
            raise JobError("not_balanced", "the robot is recovering or down: arm refused", 409)
        if not self._arm.acquire(blocking=False):
            raise JobError("busy", "an arm job is already running", 409, retryable=True)
        est = round(ARM_SECONDS[action] * ARM_SPEED / speed, 1)
        try:
            job_id = self._start(self._run_arm, action, est)
        except BaseException:
            self._arm.release()
            raise
        return {"job_id": job_id, "accepted": True, "estimated_s": est}

    def say(self, body: dict) -> dict:
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise JobError("bad_request", "text must be a non-empty string")
        self._need_backend("audio.py")
        duration = round(max(0.5, 0.38 * len(text.split())), 1)
        return {"job_id": self._start(self._run_say, duration), "duration_s": duration}

    def led(self, body: dict) -> dict:
        state = body.get("state")
        if state not in LED_STATES:
            raise JobError("bad_request", f"state must be one of {list(LED_STATES)}")
        self._need_backend("led.py")
        self.led_state = state
        return {"state": state}

    # ── the simulated actuators ──────────────────────────────────────────────────
    def _need_backend(self, module: str) -> None:
        if not self.sim:
            raise JobError("backend_unavailable", f"robot/{module} is not built yet (robot/README.md); "
                           "run with ROBOT_MODE=sim for a simulated one", 503)

    def _start(self, run: Callable, *args) -> str:
        job_id = f"job_{uuid.uuid4().hex[:4]}"
        threading.Thread(target=self._guard, args=(run, job_id, *args), name=job_id, daemon=True).start()
        return job_id

    def _guard(self, run: Callable, job_id: str, *args) -> None:
        try:
            run(job_id, *args)
        except Exception as e:  # noqa: BLE001 -- a job that dies must still END on the socket
            self._say(job_id, "failed", error="internal", detail=f"{type(e).__name__}: {e}")

    def _say(self, job_id: str, state: str, **extra) -> None:
        self.publish({"t": "job", "id": job_id, "state": state, **extra})

    def _wait(self, seconds: float, cancel: threading.Event | None = None) -> bool:
        """False if cancelled. time_scale 0 makes every job instant, for tests."""
        s = seconds * self.time_scale
        if cancel is not None:
            return not cancel.wait(s) if s > 0 else not cancel.is_set()
        if s > 0:
            time.sleep(s)
        return True

    def _run_drive(self, job_id: str, target: tuple, speed: float, timeout_s: float, cancel: threading.Event) -> None:
        p = self.base.read()
        x0, z0, yaw0 = p["x"], p["z"], p["yaw"]
        x1, z1, yaw1 = target
        turn = (yaw1 - yaw0 + math.pi) % (2 * math.pi) - math.pi
        seconds = max(0.2, math.hypot(x1 - x0, z1 - z0) / speed + abs(turn) / 1.0)     # 1 rad/s of turn
        n = max(1, int(seconds * 5))                       # five progress messages a second
        t0 = time.monotonic()
        for i in range(1, n + 1):
            if i * seconds / n > timeout_s:                # stops where it got to, and says so
                return self._say(job_id, "failed", error="drive_timeout",
                                 detail=f"needs {seconds:.1f} s at {speed} m/s; timeout_s is {timeout_s}")
            if not self._wait(seconds / n, cancel):
                return self._say(job_id, "failed", error="job_superseded", detail="a newer /drive replaced this one")
            k = i / n
            self.base.set(x0 + (x1 - x0) * k, z0 + (z1 - z0) * k, yaw0 + turn * k)
            if i < n:
                self._say(job_id, "driving", progress=round(k, 2))
        self._say(job_id, "done", progress=1.0, result={"final_pose": self.base.read(),
                                                        "duration_s": round(time.monotonic() - t0, 2)})

    def _run_arm(self, job_id: str, action: str, est: float) -> None:
        try:
            states, t0 = ARM_STATES[action], time.monotonic()
            for i, state in enumerate(states):
                if not self.balanced():
                    return self._say(job_id, "failed", error="not_balanced", detail=f"lost balance while {state}")
                self._say(job_id, state, progress=round(i / len(states), 2))
                self._wait(est / len(states))
            result = {"duration_s": round(time.monotonic() - t0, 2), "simulated": True}
            if action == "pick":
                result["grasped"] = True
            self._say(job_id, "done", progress=1.0, result=result)
        finally:
            self._arm.release()

    def _run_say(self, job_id: str, duration: float) -> None:
        self._say(job_id, "speaking", progress=0.0)
        self._wait(duration)
        self._say(job_id, "done", progress=1.0, result={"duration_s": duration, "simulated": True})
