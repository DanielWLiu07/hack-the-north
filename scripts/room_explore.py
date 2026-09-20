#!/usr/bin/env python3
"""room_explore.py — drive the robot around the room so `add` has more to add.

    python scripts/room_explore.py [name] [--minutes 3] [--every 30] [--lane 0.6] [--area XMIN XMAX YMIN YMAX | --around R]
    python scripts/room_explore.py [name] ... --go            actually move: a person in the room, and `clear` typed at the prompt

One `room_live.py add` is the robot's fused map as it stands: what it has seen from where it has been. This drives it
over the room first — bbapps/nav's own swept rectangle, then its patrol (the stalest floor block next) — and runs the add
step every --every seconds WHILE it drives, so /scene and /robot fill in as it goes (they follow the newest map within 5 s,
and the robot marker on /robot follows the pose it publishes here, at 2 Hz). When --minutes are up, or on Ctrl-C, the
robot is halted and one last add is taken. The camera's layer (dense points, floor objects) is not taken while driving —
a moving robot cannot give one and its gate would refuse it — so the adds here are map-only; a still `add` afterwards
gives the layer.

MOTION IS THE PERSON'S DECISION, EVERY TIME. Without --go this prints the plan — the area, its lanes, the minutes, the
interval, the robot's pose, its SLAM state and its bus voltage — and exits 0 with "add --go to drive, with a person in
the room". With --go it prints the plan, then asks "Is the room clear of people and pets? type clear to drive" and moves
only on `clear` typed at a terminal (a pipe is a refusal: this is a balancing machine driving a sweep pattern through a
hallway people walked all day). It still refuses, in one line, unless SLAM is localized, no job is running, the area is
at least 1 m x 1 m after the 0.3 m shrink, the robot stands inside it, and the robot's drive daemon is talking
(robot.server's /healthz bbos.power, published every 10 s: null or older than 30 s means the base is silent while its
process lives — the one state where the robot moves and we cannot see it).

WHAT STOPS IT, ALWAYS WITH halt(): --minutes; Ctrl-C / SIGTERM; a SLAM reset (map_gen changes: the area was defined in a
map that no longer exists; it is not re-defined); the HARD CEILINGS --max-minutes (10) and --max-metres (60, integrated
from pose deltas) for an operator who walked away or lost the terminal; the nav stack silent for 15 s (lost contact);
bbos.power going stale mid-run. Ceilings end the run with exit 0 and say which; lost contact and a stale drive daemon
exit 1; a failed trip is a RobotError, filed the way roomctl/bb_nav.py files every refused or failed trip, exit 1. A
failed snapshot (the robot busy, a 503) is one log line and the patrol goes on. The bus voltage is logged at the start,
on every snapshot line and at the end, and the summary prints start / min / end — a trail, not a cutoff: nobody has
stated a floor.

THE AREA. bbapps/nav takes a rectangle RELATIVE TO THE ROBOT (+X right, +Y forward, anchored where it stands when the
rectangle is defined), and sweeps it in lanes --lane apart. --area is given in the map's world frame (the frame of every
map_<stamp>.json: default = the newest one's bounds_m) and is turned into that robot frame here through roomctl/frames.py;
--around R is a 2R x 2R square centred on the robot, in its own frame, for a room with no map yet.

WHO IT TALKS TO. The nav stack through roomctl/bb_nav.py (BB_HOST, BB_API_PORT); robot.server's /healthz for the drive
daemon and the voltage (PI_HOST:PI_PORT from .env via scripts/pi_link.read_env; --healthz URL to point elsewhere); the
map only through room_live's add (scripts/bbos_map.py); the site's event inlet, $ROOM_WEB_URL/api/edge/event (default
http://127.0.0.1:8000), with the same `nav` event the watch loop's nav bridge posts — the pose in the room frame, which
for the fused map IS its own frame (bbos_map's identity registration) — plus source "explore", voltage and metres.
Tested against fake/bbsim.py only (BB_HOST=127.0.0.1:18110; --sim-no-healthz, loopback only, skips the drive-daemon
check the fake cannot answer, and the plan line says so; --snapshot-dir stands in for the map the fake does not serve);
it has never been run against the robot from here.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from roomctl import frames  # noqa: E402
from roomctl.bb_nav import BBNav, RobotError, job_error, report, status_error, world_pose  # noqa: E402
from roomctl.nav_publish import pose_event  # noqa: E402

import pi_link  # noqa: E402
import room_live  # noqa: E402

SHRINK_M = 0.3                # the area is pulled in by this much on every side: the robot needs room to turn at a lane's end
MIN_SIDE_M = 1.0              # after the shrink: smaller than this is not worth a sweep, and bbsim's own margin would eat it
SWEEP_TIMEOUT_S = 600         # the robot's own limit on a sweep (bbapps/nav default)
POWER_STALE_S = 30.0          # bbos publishes drive.status every 10 s; older than this and the base daemon is not talking
POWER_EVERY_S = 5.0           # how often /healthz is asked while driving
UNSAMPLED = "00000000000000000000000000000001-0000000000000001-0"     # sentry-trace: the robot's SDK inherits "not sampled" and files nothing
CONTACT_LOST_S = 15.0         # no /ws state and no /health answer for this long = the nav stack is gone: halt
TICK_S = 0.5                  # the control loop, and the pose publish rate (2 Hz, the nav bridge's)
JUMP_M = 1.0                  # a pose delta bigger than this in one tick is a SLAM correction, not driving: not counted
B, D, G, Y, R, X = ("\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[0m") if sys.stdout.isatty() else ("",) * 6


def say(t0: float, line: str) -> None:
    m, s = divmod(int(time.monotonic() - t0), 60)
    print(f"{D}[explore {m:02d}:{s:02d}]{X} {line}", flush=True)


def volts(v: float | None) -> str:
    return f"{v:.1f} V" if isinstance(v, (int, float)) else "? V"


# ── the area ──────────────────────────────────────────────────────────────────────
def newest_bounds(room: room_live.Room) -> tuple[list[float], list[float], str] | None:
    """bounds_m of the instance's newest map snapshot (<name>.scene/map_<stamp>.json), or None when there is no map yet."""
    for p in sorted(room.scene.glob("map_*.json"), reverse=True) if room.scene.is_dir() else []:
        try:
            b = json.loads(p.read_text()).get("bounds_m") or {}
            lo, hi = b.get("min"), b.get("max")
            if isinstance(lo, list) and isinstance(hi, list) and len(lo) >= 2 and len(hi) >= 2:
                return [float(lo[0]), float(lo[1])], [float(hi[0]), float(hi[1])], p.name
        except (OSError, ValueError, TypeError):
            continue
    return None


def area_in_robot_frame(world_box: tuple[float, float, float, float] | None, around: float | None, pose: tuple[float, float, float]) -> dict:
    """The rectangle bbapps/nav gets: robot-relative (+X right, +Y forward), anchored at `pose`. A world box becomes the
    axis-aligned box around its four corners in that frame (it over-covers by the corners' rotation, never under)."""
    px, py, h = pose
    if around is not None:
        raw = (-around, around, -around, around)
    else:
        xmin, xmax, ymin, ymax = world_box
        corners = [frames.bb_to_robot_rel(x, y, px, py, h) for x in (xmin, xmax) for y in (ymin, ymax)]
        raw = (min(c[0] for c in corners), max(c[0] for c in corners), min(c[1] for c in corners), max(c[1] for c in corners))
    shrunk = (raw[0] + SHRINK_M, raw[1] - SHRINK_M, raw[2] + SHRINK_M, raw[3] - SHRINK_M)
    return {"raw": raw, "box": shrunk, "w": shrunk[1] - shrunk[0], "d": shrunk[3] - shrunk[2],
            "inside": shrunk[0] <= 0.0 <= shrunk[1] and shrunk[2] <= 0.0 <= shrunk[3]}


def lanes_of(area: dict, lane: float) -> int:
    """How many lanes the sweep will drive (bbsim's rule, 0.25 m in from the edges); the real stack's may differ by one."""
    ymin, ymax = area["box"][2] + 0.25, area["box"][3] - 0.25
    return max(1, int(math.floor((ymax - ymin) / lane + 1e-6)) + 1) if ymax > ymin else 1


# ── the drive daemon and the voltage, from robot.server ───────────────────────────
class Power:
    """bbos.power off robot.server's /healthz: {voltage, age_s} or None. Asked every POWER_EVERY_S while driving."""

    def __init__(self, url: str, skipped: bool = False):
        self.url, self.last, self.at, self.skipped = url, None, 0.0, skipped
        self.start_v: float | None = None
        self.min_v: float | None = None
        self.end_v: float | None = None

    def read(self) -> dict | None:
        if self.skipped:
            return None
        try:
            req = urllib.request.Request(self.url, headers={"sentry-trace": UNSAMPLED})      # a probe, not a transaction on the robot
            with urllib.request.urlopen(req, timeout=3) as r:
                doc = json.loads(r.read() or b"{}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            self.last = None
        else:
            p = (doc.get("bbos") or {}).get("power") if isinstance(doc, dict) else None
            self.last = p if isinstance(p, dict) and isinstance(p.get("age_s"), (int, float)) else None
        self.at = time.monotonic()
        v = self.last.get("voltage") if self.last else None
        if isinstance(v, (int, float)):
            self.start_v = v if self.start_v is None else self.start_v
            self.min_v = v if self.min_v is None else min(self.min_v, v)
            self.end_v = v
        return self.last

    def stale(self) -> str | None:            # why the drive daemon cannot be trusted right now, or None
        if self.skipped:
            return None
        if self.last is None:
            return f"bbos.power is null on {self.url} — robot.server sees no drive.status, so the base daemon is not talking"
        if self.last["age_s"] > POWER_STALE_S:
            return f"drive.status is stale: bbos.power age {self.last['age_s']:.0f} s (over {POWER_STALE_S:.0f}) — the base daemon is silent while its process lives"
        return None

    @property
    def voltage(self) -> float | None:
        v = self.last.get("voltage") if self.last else None
        return v if isinstance(v, (int, float)) else None

    def trail(self) -> str:
        return f"{volts(self.start_v)} → {volts(self.end_v)}" + (f" (min {volts(self.min_v)})" if self.min_v is not None and self.min_v != self.end_v else "")


def healthz_url(override: str | None) -> str:
    if override:
        return override
    env = pi_link.read_env()
    host, port = env.get("PI_HOST", ""), env.get("PI_PORT", "8080") or "8080"
    return f"http://{host}:{port}/healthz" if host else ""


# ── the site: where the robot is, as the nav bridge tells it ──────────────────────
class Publisher:
    """POST {"event": "nav", "data": …} to $ROOM_WEB_URL/api/edge/event, as roomctl/watch_cli.web_publisher does, at TICK_S
    on its own thread so a slow web never holds the wheels. Says once when the site cannot be reached, once when it is back."""

    def __init__(self, base: str):
        self.base, self.token = base.rstrip("/"), os.getenv("GITIRL_CLOUD_TOKEN", "").strip()
        self.data: dict | None = None
        self.sent = self.failures = 0
        self.down = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="explore-publish", daemon=True)

    def start(self) -> Publisher:
        if self.base:
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def offer(self, data: dict) -> None:
        self.data = data

    def _post(self, data: dict) -> None:
        req = urllib.request.Request(f"{self.base}/api/edge/event", data=json.dumps({"event": "nav", "data": data}).encode(),
                                     headers={"content-type": "application/json", **({"authorization": f"Bearer {self.token}"} if self.token else {})})
        urllib.request.urlopen(req, timeout=3).close()

    def _run(self) -> None:
        while not self._stop.is_set():
            data, self.data = self.data, None
            if data is not None:
                try:
                    self._post(data)
                    self.sent += 1
                    if self.down:
                        print(f"{D}the site at {self.base} takes nav events again{X}", flush=True)
                    self.down = False
                except (urllib.error.URLError, TimeoutError, OSError) as e:
                    self.failures += 1
                    if not self.down:
                        print(f"{D}the site at {self.base} is not taking nav events ({type(e).__name__}); the drive goes on without the marker{X}", flush=True)
                    self.down = True
            self._stop.wait(TICK_S)
        if self.data is not None:              # the last word: halted
            try:
                self._post(self.data)
            except (urllib.error.URLError, TimeoutError, OSError):
                pass


IDENTITY = frames.SE2.identity()              # the fused map's frame is the room frame for `add` (bbos_map.identity_registration)


def nav_data(state, phase: str, power: Power, metres: float) -> dict:
    d = pose_event(state, IDENTITY)
    d.update({"source": "explore", "phase": phase, "voltage": power.voltage, "metres": round(metres, 2)})
    return d


# ── the add step, while driving ───────────────────────────────────────────────────
def newest_map(room: room_live.Room) -> tuple[str, int | None]:
    """(stamp, voxels) of the instance's newest map snapshot, from its sidecar: what the last add wrote."""
    for p in sorted(room.scene.glob("map_*.json"), reverse=True) if room.scene.is_dir() else []:
        try:
            v = json.loads(p.read_text()).get("voxels")
            return p.stem[4:], int(v) if isinstance(v, (int, float)) else None
        except (OSError, ValueError, TypeError):
            continue
    return "", None


def snapshot(room: room_live.Room, message: str, snapshot_dir: Path | None) -> tuple[str, str]:
    """room_live's add, map only. Returns (what happened, commit sha or ''). Never raises: a busy robot or a 503 is a
    line in the log and the patrol goes on. Its own prints go to stdout as they always do."""
    before = room_live._head(room.repo) if (room.repo / ".git").exists() else ""
    was_stamp, was_voxels = newest_map(room)
    ns = SimpleNamespace(name=room.name, repo=None, message=message, no_capture=True, recording=None, dir=str(snapshot_dir) if snapshot_dir else None)
    said = io.StringIO()                      # cmd_mapshot narrates for a person at a terminal; here its words become one line of ours
    try:
        with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
            rc = room_live.cmd_mapshot(ns)
    except SystemExit as e:                   # bbos_map.pull says why the robot had no map to give
        return f"snapshot failed: {str(e.code) if e.code not in (None, 0, 1) else 'the robot had no map to give'}", ""
    except Exception as e:  # noqa: BLE001 — anything else in the add path is a line here, not the end of the drive
        return f"snapshot failed: {type(e).__name__}: {e}", ""
    after = room_live._head(room.repo)
    stamp, voxels = newest_map(room)
    grew = (f"{was_voxels:,} → {voxels:,} voxels ({voxels - was_voxels:+,})" if isinstance(was_voxels, int) and isinstance(voxels, int)
            else f"{voxels:,} voxels" if isinstance(voxels, int) else "voxels ?")
    if rc == 0 and after and after != before:
        return f"snapshot {stamp}: {grew} · commit {after[:7]}", after
    return f"snapshot {stamp or '?'}: {grew} · nothing new, no commit", after


# ── the drive ─────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="the room instance (default: the last one used, like room_live.py)")
    ap.add_argument("--minutes", type=float, default=3.0, help="how long to drive (default 3)")
    ap.add_argument("--every", type=float, default=30.0, help="seconds between adds while driving (default 30)")
    ap.add_argument("--lane", type=float, default=0.6, help="lane spacing of the sweep, metres (default 0.6, the caretaker's)")
    ap.add_argument("--area", type=float, nargs=4, metavar=("XMIN", "XMAX", "YMIN", "YMAX"),
                    help="the rectangle to cover, in the map's world frame (default: the newest map snapshot's bounds_m)")
    ap.add_argument("--around", type=float, metavar="R", help="instead: a 2R x 2R square centred on the robot, in its own frame")
    ap.add_argument("--max-minutes", type=float, default=10.0, help="hard ceiling on the drive, whatever --minutes says (default 10)")
    ap.add_argument("--max-metres", type=float, default=60.0, help="hard ceiling on distance driven, from pose deltas (default 60)")
    ap.add_argument("--go", action="store_true", help="MOVE — after `clear` is typed at the prompt. Without it the plan is printed and nothing drives.")
    ap.add_argument("-m", "--message", help="commit message for each add (default: 'explore <mm:ss>')")
    ap.add_argument("--snapshot-dir", type=Path, help="an existing map snapshot to add instead of pulling the robot's (the fake robot serves none)")
    ap.add_argument("--healthz", help="robot.server's /healthz URL (default: PI_HOST:PI_PORT from .env)")
    ap.add_argument("--sim-no-healthz", action="store_true", help="SIM ONLY: skip the drive-daemon check (fake/bbsim.py has no robot.server); the plan says so")
    ap.add_argument("--web", default=os.getenv("ROOM_WEB_URL", "http://127.0.0.1:8000"), help="the site to post the pose to as `nav` events ($ROOM_WEB_URL; '' = none)")
    a = ap.parse_args()
    if a.sim_no_healthz and "127.0.0.1" not in os.getenv("BB_HOST", "") and "localhost" not in os.getenv("BB_HOST", ""):
        ap.error("--sim-no-healthz is for fake/bbsim.py on loopback; BB_HOST is not loopback")
    if a.every < 5:
        ap.error("--every under 5 s would spend the drive pulling maps")
    if a.max_minutes <= 0 or a.max_metres <= 0:
        ap.error("--max-minutes and --max-metres must be positive: they are the ceilings")

    try:
        import obs
        obs.init("link")                      # so a refused or failed trip is one Sentry issue, as bb_nav files them
    except Exception:  # noqa: BLE001 — observability must never keep the robot from being asked
        pass

    room = room_live.Room(a.name, None)
    t0 = time.monotonic()
    try:
        nav = BBNav.from_env().start()
    except RobotError as e:
        print(f"{R}cannot drive: {e.code}: {e.detail}{X}")
        return 1
    power = Power(healthz_url(a.healthz), skipped=a.sim_no_healthz)
    try:
        return drive(a, room, nav, power, t0)
    finally:
        nav.close()


def drive(a, room: room_live.Room, nav: BBNav, power: Power, t0: float) -> int:
    # ── the plan: pose and SLAM state first, the area is relative to them
    deadline_state = time.monotonic() + 6.0
    while nav.state is None and time.monotonic() < deadline_state:
        time.sleep(0.2)
    try:
        px, py, ph = world_pose(nav.pose())
    except RobotError as e:
        print(f"{R}cannot drive: {e.code}: {e.detail}{X}")
        report(e, nav.state)
        return 1
    st = nav.state
    source = ""
    if a.around is not None:
        world_box = None
    elif a.area:
        world_box = tuple(a.area); source = "--area"
    else:
        nb = newest_bounds(room)
        if nb is None:
            print(f"{R}cannot drive: {room.name} has no map snapshot yet, so no bounds to cover — give --area or --around R{X}")
            return 1
        world_box = (nb[0][0], nb[1][0], nb[0][1], nb[1][1]); source = nb[2]
    area = area_in_robot_frame(world_box, a.around, (px, py, ph))
    lanes = lanes_of(area, a.lane)
    job = nav.job()
    p = power.read() if power.url else None
    print(f"{B}explore {room.name}{X}   {a.minutes:g} min (ceilings {a.max_minutes:g} min, {a.max_metres:g} m) · add every {a.every:g} s · lanes {a.lane} m apart")
    if a.around is not None:
        print(f"  area    {2 * a.around:g} x {2 * a.around:g} m around the robot, in its own frame")
    else:
        print(f"  area    world x {world_box[0]:.2f}..{world_box[1]:.2f}  y {world_box[2]:.2f}..{world_box[3]:.2f}   (from {source})")
    bx = area["box"]
    print(f"          robot frame, {SHRINK_M} m in: right {bx[0]:+.2f}..{bx[1]:+.2f}  forward {bx[2]:+.2f}..{bx[3]:+.2f}  "
          f"= {area['w']:.2f} x {area['d']:.2f} m · {lanes} lane{'s' if lanes != 1 else ''}")
    print(f"  robot   at ({px:.2f}, {py:.2f}) heading {ph:.3f} rad, facing ({-math.sin(ph):+.2f}, {math.cos(ph):+.2f}) · "
          f"{'inside the area' if area['inside'] else 'OUTSIDE the area'}")
    print(f"  SLAM    {'localized' if st and st.ready else 'NOT localized'} · status {st.status if st else '?'!r} · map_gen {st.map_gen if st else '?'} · "
          f"job {'none' if job is None or not job.running else job.kind + ' running'}")
    age = f"age {p['age_s']:.0f} s" if p else "null"
    if power.skipped:
        print(f"  power   {Y}check SKIPPED (--sim-no-healthz): no drive-daemon watch, no voltage — the fake robot only{X}")
    else:
        print(f"  power   {volts(power.voltage)} · bbos.power {age} · {power.url or 'no /healthz: PI_HOST is not set in .env'}")
    print(f"  site    {a.web or 'not posting the pose'}")
    if not a.go:
        print(f"\n{Y}add --go to drive, with a person in the room{X}")
        return 0

    # ── the gate: one line says which condition failed
    refuse = None
    err = status_error(st)
    if err is not None:
        refuse = f"{err.code}: {err.detail}"
    elif job is not None and job.running:
        refuse = f"a job is already running on the robot: {job.kind}"
    elif area["w"] < MIN_SIDE_M or area["d"] < MIN_SIDE_M:
        refuse = f"the area is {area['w']:.2f} x {area['d']:.2f} m after the {SHRINK_M} m shrink; at least {MIN_SIDE_M:.0f} x {MIN_SIDE_M:.0f} m is needed"
    elif not area["inside"]:
        refuse = "the robot stands outside the area (it must start inside what it is to sweep)"
    elif not power.url and not power.skipped:
        refuse = "no /healthz to watch the drive daemon on: PI_HOST is not set in .env (or give --healthz URL)"
    elif power.stale():
        refuse = power.stale()
    if refuse:
        print(f"{R}refused: {refuse}{X}")
        if err is not None:
            report(err, st)
        return 1
    if not sys.stdin.isatty():
        print(f"{R}refused: stdin is not a terminal — the clear-room confirmation needs a person at the keyboard{X}")
        return 1
    try:
        answer = input(f"\n{B}Is the room clear of people and pets? type clear to drive:{X} ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer.strip().lower() != "clear":
        print(f"{Y}not driving: the answer was not `clear`{X}")
        return 1

    # ── drive
    stop_why: list[str] = []
    def stop(sig, _frame):
        stop_why.append(signal.Signals(sig).name)
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, stop)
    nav.on_map_reset(lambda why: stop_why.append(f"map reset: {why}"))
    pub = Publisher(a.web).start()
    gen0 = st.map_gen
    started = time.monotonic()
    deadline = started + a.minutes * 60
    ceiling = started + a.max_minutes * 60
    next_add = started + a.every
    next_power = started + POWER_EVERY_S
    next_line = started + 5.0
    last_seen = time.monotonic()              # the nav stack's last answer, /ws or /health
    metres, last_xy = 0.0, (st.x, st.y) if st else None
    adds: list[str] = []
    rc = 0
    phase = "sweep"
    say(t0, f"{volts(power.voltage)} · sweep: rectangle right {bx[0]:+.2f}..{bx[1]:+.2f} forward {bx[2]:+.2f}..{bx[3]:+.2f}, {lanes} lanes")
    try:
        nav.define_area(bx[0], bx[1], bx[2], bx[3], sweep=True, lane_spacing=a.lane, timeout=SWEEP_TIMEOUT_S)
        while not stop_why:
            now = time.monotonic()
            st = nav.state
            # ── the distance, from pose deltas; the ceilings; contact
            if st is not None:
                if last_xy is not None:
                    step = math.hypot(st.x - last_xy[0], st.y - last_xy[1])
                    if step < JUMP_M:
                        metres += step
                last_xy = (st.x, st.y)
                last_seen = max(last_seen, now - (time.time() - st.t))       # the /ws message's own time, not "still fresh"
            if now >= ceiling:
                stop_why.append(f"max-minutes reached ({a.max_minutes:g})"); break
            if metres >= a.max_metres:
                stop_why.append(f"max-metres reached ({metres:.1f} of {a.max_metres:g} m)"); break
            if now >= deadline:
                stop_why.append(f"{a.minutes:g} minutes are up"); break
            if st is not None and st.map_gen != gen0:
                stop_why.append(f"map reset: map_gen {gen0} -> {st.map_gen}"); break
            try:
                job, answered = nav.job(), True
                last_seen = now
            except RobotError as e:
                if e.code != "robot_unreachable":
                    raise
                job, answered = None, False          # no answer is not "no job": nothing is started on a silent stack
            if now - last_seen > CONTACT_LOST_S:
                raise RobotError("lost_contact", f"the nav stack has not answered for {CONTACT_LOST_S:.0f} s (no /ws state, no /health)")
            if answered:
                if job is None or not job.running:
                    if (e := job_error(job)) is not None:
                        raise e
                    if phase == "sweep":
                        say(t0, f"swept · pose ({st.x:.2f}, {st.y:.2f}, {st.h:.3f}) · patrol: the stalest floor next" if st else "swept · patrol")
                    else:
                        say(t0, "the patrol ended on its own; starting it again")
                    nav.patrol(goal_timeout=90)
                    phase = "patrol"
            # ── the drive daemon
            if now >= next_power:
                next_power = now + POWER_EVERY_S
                power.read()
                if (why := power.stale()):
                    raise RobotError("drive_status_stale", why)
            # ── the add
            if now >= next_add:
                next_add = now + a.every
                m, s = divmod(int(now - t0), 60)
                what, sha = snapshot(room, a.message or f"explore {m:02d}:{s:02d}", a.snapshot_dir)
                if " · commit " in what:
                    adds.append(sha)
                st = nav.state
                say(t0, f"{what} · pose ({st.x:.2f}, {st.y:.2f}, {st.h:.3f}) · {metres:.1f} m · {volts(power.voltage)}" if st else f"{what} · {volts(power.voltage)}")
                next_line = now + 5.0
            elif now >= next_line and st is not None:
                next_line = now + 5.0
                goal = f"driving to ({st.goal[0]:.2f}, {st.goal[1]:.2f})" if st.goal else f"{phase}: {st.status or 'idle'}"
                say(t0, f"{goal} · pose ({st.x:.2f}, {st.y:.2f}, {st.h:.3f}) · {metres:.1f} m · {volts(power.voltage)}")
            if st is not None:
                pub.offer(nav_data(st, phase, power, metres))
            time.sleep(TICK_S)
    except RobotError as e:
        say(t0, f"{R}{e.code}: {e.detail}{X}")
        report(e, nav.state, target=f"explore {room.name}", metres=round(metres, 2), voltage=power.voltage)
        rc = 1
    finally:
        try:
            nav.halt()
            say(t0, f"halted{': ' + stop_why[0] if stop_why else ''} · {metres:.1f} m driven · {power.trail()}")
        except RobotError as e:
            say(t0, f"{R}halt failed: {e.code}: {e.detail} — the robot may still be moving: stop it at the robot{X}")
            report(e, nav.state, target=f"explore {room.name}")
            rc = 1
        st = nav.state
        if st is not None:
            pub.offer({**nav_data(st, "halted", power, metres), "status": "halted"})
        pub.stop()
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, signal.SIG_DFL)
    time.sleep(1.0)                           # let the wheels stop before the last map is read
    m, s = divmod(int(time.monotonic() - t0), 60)
    what, sha = snapshot(room, a.message or f"explore {m:02d}:{s:02d} (final)", a.snapshot_dir)
    if " · commit " in what:
        adds.append(sha)
    if power.url and not power.skipped:
        power.read()
    st = nav.state
    say(t0, f"final {what}" + (f" · pose ({st.x:.2f}, {st.y:.2f}, {st.h:.3f})" if st else "") + f" · {volts(power.voltage)}")
    print(f"{G if rc == 0 else Y}done:{X} {stop_why[0] if stop_why else 'stopped on an error'} · {metres:.1f} m driven · {power.trail()} · "
          f"{len(adds)} commit{'s' if len(adds) != 1 else ''} {' '.join(c[:7] for c in adds)} in {room.repo} · {pub.sent} nav events posted"
          + ("" if rc == 0 else " — the drive ended on an error, see above"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
