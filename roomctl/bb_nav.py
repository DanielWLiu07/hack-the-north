"""roomctl/bb_nav.py — the client for Bracket Bot's navigation stack (bbapps/nav, on the robot).

We are "the server": we connect in, the robot never connects out (no auth there, so the robot
network only). What it gives us, and what this module turns it into:

    ws :8010/heavy    colour voxel map, 1.5 cm cells   -> VoxelMirror (dict keyed by integer cell)
    ws :8010/ws       pose + status ~8 Hz, map_gen     -> NavState, and map-reset callbacks
    ws :8020/stream   2D area grid + freshness, job    -> AreaMap
    http :8020        /health /pose /map /map/rectangle /navigate /patrol /stop

Rules the robot's docs make hard requirements, enforced here:
- keep up with /heavy: it queues 8 messages per connection, then drops you and sends a fresh full
  copy with first = 1, which always means "clear and rebuild";
- one job at a time: /navigate ends a patrol, and patrol does NOT resume by itself;
- a go-to "arrives" within 0.25 m, and an unreachable target still reports "reached" (at the
  nearest mapped floor), so the ACTUAL pose is always checked;
- map_gen changes on a map reset or re-anchor: everything derived from the old map is dropped.

Every conversion to or from the room frame goes through roomctl/frames.py.
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import struct
import threading
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from roomctl import frames
from roomctl.executor import BasePose, RobotError

try:
    import obs  # spans: no-ops unless the process called obs.init()
except ImportError:  # pragma: no cover
    obs = None

HEADER = struct.Struct("<II")        # u32 type, u32 count
FULL = struct.Struct("<iiifI")       # type 4: bx, by, bz, res, first
DELTA = struct.Struct("<iiifII")     # type 5: bx, by, bz, res, nu, nr
ARRIVE_TOL_M = 0.25


# ── the 3D voxel map ────────────────────────────────────────────────────────────────────

class VoxelMirror:
    """Mirror of the robot's coloured voxel cloud, keyed by integer cell (BB world frame)."""

    def __init__(self, on_reset: Callable[[], None] | None = None):
        self.res: float | None = None
        self.cells: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        self.version = 0          # +1 per applied type-4/5 message
        self.resets = 0           # full copies that started with first = 1
        self._on_reset = on_reset

    def __len__(self) -> int:
        return len(self.cells)

    def apply_message(self, msg: bytes) -> int:
        """One WebSocket message: 8-byte header + zlib payload. Types 2/3/6/7 are UI overlays."""
        ptype, count = HEADER.unpack_from(msg, 0)
        if ptype in (4, 5):
            self.apply(ptype, count, zlib.decompress(msg[8:]))
        return ptype

    def apply(self, ptype: int, count: int, raw: bytes) -> None:
        if ptype == 4:
            bx, by, bz, res, first = FULL.unpack_from(raw, 0)
            self.res = res
            if first:
                self.cells.clear()
                self.resets += 1
                if count == 0 and self._on_reset:     # an EMPTY full copy: the robot reset its map
                    self._on_reset()
            off = np.frombuffer(raw, np.uint16, count * 3, FULL.size).reshape(count, 3).astype(np.int64)
            rgb = np.frombuffer(raw, np.uint8, count * 3, FULL.size + count * 6).reshape(count, 3)
            self.cells.update(zip(map(tuple, (off + (bx, by, bz)).tolist()), map(tuple, rgb.tolist())))
        elif ptype == 5:
            bx, by, bz, res, nu, nr = DELTA.unpack_from(raw, 0)
            self.res = res
            p = DELTA.size
            up = np.frombuffer(raw, np.uint16, nu * 3, p).reshape(nu, 3).astype(np.int64) + (bx, by, bz)
            p += nu * 6
            rm = np.frombuffer(raw, np.uint16, nr * 3, p).reshape(nr, 3).astype(np.int64) + (bx, by, bz)
            p += nr * 6
            rgb = np.frombuffer(raw, np.uint8, nu * 3, p).reshape(nu, 3)
            self.cells.update(zip(map(tuple, up.tolist()), map(tuple, rgb.tolist())))
            for key in map(tuple, rm.tolist()):
                self.cells.pop(key, None)
        else:
            return
        self.version += 1

    def points(self) -> tuple[np.ndarray, np.ndarray]:
        """(N,3) float32 BB-world metres, (N,3) uint8 rgb."""
        if not self.cells or not self.res:
            return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.uint8)
        keys = np.fromiter((c for k in self.cells for c in k), np.int64, len(self.cells) * 3).reshape(-1, 3)
        rgb = np.fromiter((c for v in self.cells.values() for c in v), np.uint8, len(self.cells) * 3).reshape(-1, 3)
        return (keys * self.res).astype(np.float32), rgb

    def points_room(self, T: frames.SE2) -> tuple[np.ndarray, np.ndarray]:
        xyz, rgb = self.points()
        return frames.bb_to_room_array(xyz, T).astype(np.float32), rgb


# ── pose, status, area, jobs ────────────────────────────────────────────────────────────

@dataclass
class NavState:
    ready: bool = False
    x: float = 0.0
    y: float = 0.0
    h: float = 0.0
    status: str = ""
    running: bool = False
    waypoints: list = field(default_factory=list)
    wp: int = -1
    goal: tuple[float, float] | None = None
    path: list | None = None
    map_gen: int = 0
    t: float = 0.0

    @classmethod
    def from_msg(cls, m: dict) -> NavState:
        return cls(ready=bool(m.get("ready")), x=float(m.get("rx", 0.0)), y=float(m.get("ry", 0.0)),
                   h=float(m.get("rh", 0.0)), status=str(m.get("status", "")), running=bool(m.get("running")),
                   waypoints=list(m.get("waypoints") or []), wp=int(m.get("wp", -1)),
                   goal=(float(m["gx"]), float(m["gy"])) if "gx" in m and "gy" in m else None,
                   path=m.get("path"), map_gen=int(m.get("map_gen", 0)), t=time.time())


@dataclass
class AreaMap:
    anchor_world: dict            # {"x", "y", "yaw"}: the robot's BB pose when the area was defined
    bounds: dict                  # {"xmin", "xmax", "ymin", "ymax"} in the area frame (+X right, +Y forward)
    grid: np.ndarray              # (ny, nx) uint8: 1 floor, 2 obstacle, 0 unknown; row 0 = ymin, col 0 = xmin
    resolution_m: float
    freshness: np.ndarray         # (nby, nbx) seconds since seen; -1 = never
    block_m: float
    t: float

    @classmethod
    def from_doc(cls, d: dict) -> AreaMap:
        g, f = d["grid"], d.get("freshness") or {}
        cells = np.frombuffer(base64.b64decode(g["cells"]), np.uint8).reshape(int(g["ny"]), int(g["nx"]))
        ages = np.asarray(f.get("age_s") or [[]], dtype=np.float64)
        return cls(anchor_world=dict(d["area"]["anchor_world"]), bounds=dict(d["area"]["bounds"]), grid=cells.copy(),
                   resolution_m=float(g["resolution_m"]), freshness=ages, block_m=float(f.get("block_m", 0.5)),
                   t=float(d.get("t", time.time())))


@dataclass
class Job:
    kind: str
    running: bool
    error: str | None = None
    result: str | None = None
    progress: dict | None = None

    @classmethod
    def from_doc(cls, d: dict | None) -> Job | None:
        if not d:
            return None
        d = d.get("job", d) if isinstance(d, dict) and "kind" not in d else d
        if not d or "kind" not in d:
            return None
        return cls(kind=str(d["kind"]), running=bool(d.get("running")), error=d.get("error"),
                   result=d.get("result"), progress=d.get("progress"))


def job_error(job: Job | None) -> RobotError | None:
    """bbapps/nav job outcome -> our RobotError (plan/roommate/03 §3), or None when it succeeded."""
    if job is None or job.running:
        return None
    e = job.error
    if e:
        if e == "cancelled":
            return RobotError("nav_cancelled", e)
        if e.startswith("TimeoutError"):
            return RobotError("nav_timeout", e)
        return RobotError("nav_failed", e)
    if job.kind == "navigate" and job.result and job.result != "reached":
        return RobotError("nav_failed", f"finished {job.result!r}")
    return None


def status_error(state: NavState | None) -> RobotError | None:
    """Refuse to start a job while the robot can't take one."""
    if state is None:
        return RobotError("robot_unreachable", "no /ws state yet")
    if not state.ready:
        return RobotError("slam_not_ready", "the SLAM pose is not live")
    s = state.status
    if s.startswith("manual"):
        return RobotError("manual_override", "someone is driving from the robot's browser UI")
    if s.startswith("waiting_for_drive"):
        return RobotError("drive_busy", "another program holds the wheels")
    if s.startswith("resetting map") or s.startswith("waiting for nav daemon"):
        return RobotError("nav_unavailable", s)
    return None


# ── Sentry: every refused or failed trip is ONE issue, grouped by its code (03-interfaces §9) ────────

WARN = {"map_reset", "manual_override", "drive_busy", "nav_cancelled"}   # the robot is fine; something changed


def nav_context(state: NavState | None, target=None, **extra) -> dict:
    """What the robot said about itself when a trip failed: pose, goal and path in BB's world frame,
    its status and map generation. Structured context on the issue (tags are flat strings)."""
    if state is None:
        return {"bb_status": None, "why": "no /ws state received yet", "target_room": str(target) if target else None, **extra}
    path = state.path or []
    return {"pose_bb": {"x": round(state.x, 3), "y": round(state.y, 3), "h": round(state.h, 3)},
            "goal_bb": list(state.goal) if state.goal else None, "path_len": len(path), "path_head": path[:5],
            "bb_status": state.status, "ready": state.ready, "running": state.running, "map_gen": state.map_gen,
            "target_room": str(target) if target else None, **extra}


def report(err: RobotError, state: NavState | None, target=None, **extra) -> None:
    """File `err` as a Sentry issue: nav_failed / nav_short / nav_timeout / slam_not_ready / … as errors,
    map_reset / manual_override / drive_busy / nav_cancelled as warnings. No-op without obs.init()."""
    if obs is None:
        return
    try:
        obs.robot_failure(err.code, err.detail or err.code, level="warning" if err.code in WARN else "error",
                          context=nav_context(state, target, **extra), fingerprint=["robot", "nav", err.code],
                          action="navigate", robot="bracketbot",
                          map_gen=getattr(state, "map_gen", None) if state is not None else None)
    except Exception:  # noqa: BLE001 — reporting a failure must never become the failure
        pass


def world_pose(d: dict) -> tuple[float, float, float]:
    """GET /pose -> (x, y, yaw) in BB world, tolerant of {"world": {...}} vs flat keys."""
    w = d.get("world", d)
    x = w.get("x", w.get("rx"))
    y = w.get("y", w.get("ry"))
    h = w.get("yaw", w.get("h", w.get("rh")))
    if x is None or y is None or h is None:
        raise RobotError("bad_pose", f"unrecognised /pose body keys {sorted(d)}")
    return float(x), float(y), float(h)


# ── the client ──────────────────────────────────────────────────────────────────────────

class BBNav:
    def __init__(self, host: str, ws_port: int = 8010, api_port: int = 8020, timeout: float = 5.0):
        self.host, self.ws_port, self.api_port, self.timeout = host, ws_port, api_port, timeout
        self.state: NavState | None = None
        self.area: AreaMap | None = None
        self.last_job: Job | None = None
        self._reset_cbs: list[Callable[[str], None]] = []
        self.mirror = VoxelMirror(on_reset=lambda: self._reset("empty full copy on /heavy"))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats = {"heavy_msgs": 0, "ws_msgs": 0, "stream_msgs": 0, "reconnects": 0, "last_error": None}

    @classmethod
    def from_env(cls) -> BBNav:
        """BB_HOST=<ip or name>[:ws_port]; BB_API_PORT optional."""
        raw = os.getenv("BB_HOST", "").strip()
        if not raw:
            raise RobotError("robot_unconfigured", "BB_HOST is not set")
        host, _, port = raw.partition(":")
        return cls(host, int(port or 8010), int(os.getenv("BB_API_PORT", "8020")))

    # -- map resets -----------------------------------------------------------------------
    def on_map_reset(self, fn: Callable[[str], None]) -> None:
        self._reset_cbs.append(fn)

    def _reset(self, why: str) -> None:
        self.area = None
        for fn in list(self._reset_cbs):
            try:
                fn(why)
            except Exception:  # noqa: BLE001 — a callback must not kill the reader
                pass

    # -- background readers ---------------------------------------------------------------
    def start(self) -> BBNav:
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=lambda: asyncio.run(self._run()), name="bb-nav", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()

    async def _run(self) -> None:
        await asyncio.gather(self._loop(f"ws://{self.host}:{self.ws_port}/heavy", self._on_heavy, binary=True),
                             self._loop(f"ws://{self.host}:{self.ws_port}/ws", self._on_ws),
                             self._loop(f"ws://{self.host}:{self.api_port}/stream", self._on_stream))

    async def _loop(self, url: str, handle, binary: bool = False) -> None:
        import websockets
        while not self._stop.is_set():
            try:
                async with websockets.connect(url, max_size=None, open_timeout=self.timeout) as ws:
                    async for msg in ws:
                        if self._stop.is_set():
                            return
                        handle(msg)
            except Exception as e:  # noqa: BLE001 — both robot programs can restart: reconnect
                self.stats["reconnects"] += 1
                self.stats["last_error"] = f"{url}: {type(e).__name__}: {e}"
                await asyncio.sleep(1.0)

    def _on_heavy(self, msg) -> None:
        if isinstance(msg, (bytes, bytearray)):
            self.mirror.apply_message(bytes(msg))
            self.stats["heavy_msgs"] += 1

    def _on_ws(self, msg) -> None:
        m = json.loads(msg)
        if m.get("t") != "state":
            return                                   # "params": tuning values for the browser UI
        new = NavState.from_msg(m)
        old, self.state = self.state, new
        self.stats["ws_msgs"] += 1
        if obs is not None and (old is None or new.status != old.status or new.ready != old.ready):
            obs.breadcrumb("nav.status", f"{old.status if old else '(start)'} -> {new.status}"
                           + ("" if old is not None and new.ready == old.ready else f"; ready {new.ready}"),
                           ready=new.ready, map_gen=new.map_gen)
        if old is not None and new.map_gen != old.map_gen:
            why = f"map_gen {old.map_gen} -> {new.map_gen}"
            self._reset(why)
            report(RobotError("map_reset", f"{why}: the robot rebuilt its map; everything derived from the old one "
                                           "is dropped until it is registered again"), new, old_map_gen=old.map_gen)

    def _on_stream(self, msg) -> None:
        d = json.loads(msg)
        if "grid" in d and "area" in d:
            self.area = AreaMap.from_doc(d)
        if "job" in d:
            self.last_job = Job.from_doc(d["job"])
        self.stats["stream_msgs"] += 1

    # -- http -----------------------------------------------------------------------------
    def _http(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://{self.host}:{self.api_port}{path}", data=data, method=method,
                                     headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RobotError("robot_unreachable", f"{method} {path}: {e}") from None

    def health(self) -> dict:
        return self._http("GET", "/health")[1]

    def job(self) -> Job | None:
        return Job.from_doc(self.health().get("job"))

    def pose(self) -> dict:
        status, d = self._http("GET", "/pose")
        if status == 503:
            raise RobotError("slam_not_ready", "GET /pose answered 503")
        return d

    def area_map(self) -> AreaMap:
        status, d = self._http("GET", "/map")
        if status != 200:
            raise RobotError("no_area", f"GET /map answered {status}: define a rectangle first")
        self.area = AreaMap.from_doc(d)
        return self.area

    def _start(self, path: str, body: dict) -> Job:
        status, d = self._http("POST", path, body)
        if status not in (200, 202):
            raise RobotError("nav_rejected", f"POST {path} answered {status}: {d}")
        return Job.from_doc(d) or Job(kind=path.strip("/").split("/")[0], running=True)

    def define_area(self, xmin, xmax, ymin, ymax, sweep=True, lane_spacing=0.6, timeout=600) -> Job:
        return self._start("/map/rectangle", {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax,
                                              "sweep": sweep, "lane_spacing": lane_spacing, "timeout": timeout})

    def navigate(self, x: float, y: float, heading: float | None = None, frame: str = "world",
                 timeout: float = 120) -> Job:
        return self._start("/navigate", {"x": x, "y": y, "heading": heading, "frame": frame, "timeout": timeout})

    def patrol(self, goal_timeout: float = 90) -> Job:
        return self._start("/patrol", {"goal_timeout": goal_timeout})

    def halt(self) -> None:
        """POST /stop: halt the robot and end the current job."""
        self._http("POST", "/stop", {})

    def wait(self, timeout: float, poll: float = 0.5, sleep=time.sleep) -> Job:
        """Until the current job has ended. A timeout here is ours, not the robot's."""
        end = time.monotonic() + timeout
        while True:
            job = self.job()
            if job is None or not job.running:
                return job or Job(kind="?", running=False)
            if time.monotonic() >= end:
                raise RobotError("nav_timeout", f"job still running after {timeout:.0f} s")
            sleep(poll)


# ── the executor's Robot, over the nav stack ────────────────────────────────────────────

Registration = Callable[[], "tuple[frames.SE2, int | None] | None"]


class BBNavRobot:
    """roomctl.executor.Robot for Bracket Bot. drive() goes through /navigate; the arm and the voice
    are separate adapters (arm=None means Tier B: pick/place refuse with `arm_unavailable`)."""

    def __init__(self, nav: BBNav, registration: Registration, *, arm=None, voice=None,
                 resume_patrol: bool = True, arrive_tol: float = ARRIVE_TOL_M, timeout: float = 120.0,
                 sleep=time.sleep):
        self.nav, self.registration, self.arm, self.voice = nav, registration, arm, voice
        self.resume_patrol, self.arrive_tol, self.timeout, self.sleep = resume_patrol, arrive_tol, timeout, sleep

    def _T(self) -> frames.SE2:
        reg = self.registration()
        if reg is None:
            raise RobotError("not_registered", "no room<->robot transform yet (registration)")
        T, gen = reg
        st = self.nav.state
        if gen is not None and st is not None and st.map_gen != gen:
            raise RobotError("map_reset", f"the robot's map is gen {st.map_gen}, the registration is for gen {gen}")
        return T

    def drive(self, base: BasePose) -> None:
        """One trip. Any refusal or failure is filed once, here, with the robot's own view of it."""
        self.last_arrive_err_m = None
        try:
            self._drive(base)
        except RobotError as e:
            report(e, self.nav.state, base, arrive_err_m=self.last_arrive_err_m, arrive_tol_m=self.arrive_tol)
            raise

    def _drive(self, base: BasePose) -> None:
        if (err := status_error(self.nav.state)) is not None:
            raise err
        T = self._T()
        before = self.nav.job()
        was_patrolling = bool(before and before.kind == "patrol" and before.running)
        body = frames.base_pose_to_navigate(base, T)
        span = obs.span("nav.navigate", f"navigate to {base}", target=str(base)) if obs else _Null()
        try:
            with span as sp:
                self.nav.navigate(body["x"], body["y"], body["heading"], "world", self.timeout)
                job = self.nav.wait(self.timeout + 10, sleep=self.sleep)
                if (err := job_error(job)) is not None:
                    if sp is not None:
                        sp.set_data("nav.error", err.code)
                    raise err
                here = self.pose()
                off = math.hypot(here.x - base.x, here.y - base.y)
                self.last_arrive_err_m = round(off, 3)
                if sp is not None:
                    sp.set_data("arrive_err_m", round(off, 3))
                if off > self.arrive_tol:
                    raise RobotError("nav_short", f"arrived {off:.2f} m from {base} (tolerance {self.arrive_tol} m)")
        finally:
            if self.resume_patrol and was_patrolling:
                try:
                    self.nav.patrol()
                except RobotError:
                    pass

    def pose(self) -> BasePose:
        x, y, h = world_pose(self.nav.pose())
        T = self._T()
        rx, ry, _ = frames.bb_to_room((x, y, 0.0), T)
        return BasePose(round(rx, 4), round(ry, 4), round(frames.bb_yaw_to_heading_room(h, T), 2))

    def pick(self, object_id, pose) -> None:
        if self.arm is None:
            raise RobotError("arm_unavailable", "no arm adapter (Tier B)")
        self.arm.pick(object_id, pose)

    def place(self, object_id, pose, zone) -> None:
        if self.arm is None:
            raise RobotError("arm_unavailable", "no arm adapter (Tier B)")
        self.arm.place(object_id, pose, zone)

    def say(self, text: str) -> None:
        if self.voice is not None:
            self.voice.say(text)

    def led(self, state: str) -> None:
        if self.voice is not None and hasattr(self.voice, "led"):
            self.voice.led(state)


class _Null:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False
