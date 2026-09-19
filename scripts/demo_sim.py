#!/usr/bin/env python3
"""The MVP on the simulator, in one command (plan/roommate/tasks/MVP-NOW.md, beats 1 to 3).

    python scripts/demo_sim.py up [--scene clean_bench] [--speed 0.5] [--reseed]
    python scripts/demo_sim.py mess <object> [--to X Y]        a roommate moves it (bbsim); the loop tidies it
    python scripts/demo_sim.py decide <object> [--to X Y]      moves it, then "I meant that": an as-seen PR, approved
    python scripts/demo_sim.py status                          what is running, and the sim site's verdict
    python scripts/demo_sim.py check [--timeout 900]           beats 1 to 3, asserted end to end, in one scripted run
    python scripts/demo_sim.py reset                           the scene back to `main`; the sim room re-seeded
    python scripts/demo_sim.py down

`up` starts, each in its own tmux window of session `htn` (as `<cmd>; exec zsh`, so a crash leaves the pane open):

    sim-bbsim   fake/bbsim.py on 127.0.0.1:18010 (ws) / :18020 (api), then a swept rectangle so the loop can patrol
    sim-watch   room watch --tier A on the sim room, ROOM_ARM=sim, ROOM_WEB_URL=http://127.0.0.1:8001
    sim-web     web/server.py on 127.0.0.1:8001 with ROOM_GIT_PATH = the sim room

The sim room is ~/.cache/gitspace/rooms/sim-demo, seeded from the scene: NEVER room.git. Its site is :8001; :8000 (the
real room) is not touched. Logs: ~/.cache/gitspace/logs/sim-*.log. Nothing simulated reaches the shared Elastic
indices or Sentry: every sim process runs with ELASTIC_URL / ELASTIC_API_KEY / SENTRY_DSN empty (real environment
variables win over .env), ROOM_ES=off and ROOM_SENTRY=off, and `check` verifies on :8001/api/health that Elastic is
not configured there. Loopback only.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import threading
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PY = str(ROOT / ".venv/bin/python") if (ROOT / ".venv/bin/python").exists() else sys.executable
CACHE = Path("~/.cache/gitspace").expanduser()
ROOM = CACHE / "rooms/sim-demo"
LOGS = CACHE / "logs"
SESSION, WINDOWS = "htn", ("sim-bbsim", "sim-watch", "sim-web")
WS_PORT, API_PORT, WEB_PORT = 18010, 18020, 8001
SIM = f"http://127.0.0.1:{API_PORT}"
WEB = f"http://127.0.0.1:{WEB_PORT}"
AREA = {"xmin": -1.2, "xmax": 1.2, "ymin": -0.6, "ymax": 1.6, "sweep": True, "lane_spacing": 0.6}
DEFAULT_SPOTS = {"mug_a1b2": (0.25, 0.34), "lamp_2d9b": (0.62, 0.35), "cup_7e21": (0.18, -0.40)}

# what keeps a simulated room out of the real room's indices and monitors, whatever ../.env says
QUARANTINE = {"ROOM_ES": "off", "ROOM_EVENTS": "off", "ROOM_SENTRY": "off", "ROOM_CLEAN_CRON": "0",
              "ELASTIC_URL": "", "ELASTIC_API_KEY": "", "ELASTIC_API_KEY_PARKED": "", "SENTRY_DSN": "", "SENTRY_DSN_WEB": "",
              "ROOM_CLEAN_STATE": str(CACHE / "rooms/sim-demo-room-clean.json"),
              "ROOM_STATE_FILE": str(CACHE / "rooms/sim-demo-room-state.json")}
WATCH_ENV = {**QUARANTINE, "BB_HOST": f"127.0.0.1:{WS_PORT}", "BB_API_PORT": str(API_PORT), "ROOM_ARM": "sim",
             "ROOM_WEB_URL": WEB, "ROOM_TIER": "A"}
WEB_ENV = {**QUARANTINE, "WEB_BIND": f"127.0.0.1:{WEB_PORT}", "ROOM_GIT_PATH": str(ROOM)}


LOCK = CACHE / "rooms/.sim-demo.lock"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


@contextmanager
def driving(what: str, force: bool = False):
    """One driver at a time. Three sessions share this sim, and two runs interleaving is indistinguishable from a
    bug: a mess one run makes, the other's tidy undoes, and each waits forever for a state the other changed."""
    if LOCK.exists():
        try:
            held = json.loads(LOCK.read_text())
        except (OSError, ValueError):
            held = {}
        pid = held.get("pid")
        if pid and pid != os.getpid() and _alive(pid) and not force:
            raise SystemExit(f"someone else is driving the sim: `{held.get('what')}` (pid {pid}, since "
                             f"{held.get('at')}). Wait for it, or pass --force if you know it is dead.")
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps({"pid": os.getpid(), "what": what,
                                "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}))
    try:
        yield
    finally:
        try:
            if json.loads(LOCK.read_text()).get("pid") == os.getpid():
                LOCK.unlink()
        except (OSError, ValueError):
            pass


# ── small tools ─────────────────────────────────────────────────────────────────────────

def http(method: str, url: str, body: dict | None = None, timeout: float = 5.0):
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def up_at(url: str) -> bool:
    try:
        http("GET", url, timeout=2)
        return True
    except (OSError, ValueError):
        return False


def until(fn, timeout: float, what: str, every: float = 0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            v = fn()
        except (OSError, ValueError, KeyError):
            v = None
        if v:
            return v
        time.sleep(every)
    raise SystemExit(f"timed out after {timeout:.0f} s waiting for {what}")


def pids(pattern: str) -> list[int]:
    """The real processes matching `pattern`, without the tmux shell that wraps them (its command line contains
    the same words, and counting it once cost an afternoon)."""
    out = []
    ps = subprocess.run(["ps", "-Ao", "pid=,command="], capture_output=True, text=True).stdout
    for line in ps.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if (pattern in cmd and pid.isdigit() and int(pid) != os.getpid()
                and not cmd.lstrip().startswith(("zsh", "-zsh", "/bin/zsh", "sh "))
                and " -c " not in cmd):                  # not the tmux wrapper, not us, not someone's -c one-liner
            out.append(int(pid))
    return out


def kill_pattern(pattern: str) -> None:
    """pkill refuses a pattern that starts with a dash ("illegal option -- m") and silently leaves the process
    running, which is how two watch loops ended up on one repo. Signal the pids instead."""
    for pid in pids(pattern):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True, check=check)


def window_exists(name: str) -> bool:
    r = tmux("list-windows", "-t", SESSION, "-F", "#{window_name}", check=False)
    return name in r.stdout.split()


def marker(cmd: list[str]) -> str:
    """The part of a command that identifies it in `ps`, starting at its first non-flag word."""
    rest = cmd[1:]
    first = next((i for i, t in enumerate(rest) if not t.startswith("-")), 0)
    return " ".join(rest[first:])


def start_window(name: str, env: dict, cmd: list[str], log: Path) -> None:
    """One process per window, `<cmd>; exec zsh`: a crash leaves the pane (and its log) for reading."""
    if window_exists(name):
        tmux("kill-window", "-t", f"{SESSION}:{name}", check=False)
    # killing the window does not always take the process with it, and two watch loops on one repo race each
    # other's scans and mint two sets of job ids: make sure the old one is really gone
    kill_pattern(marker(cmd))
    time.sleep(0.4)
    envs = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    shell = f"cd {shlex.quote(str(ROOT))} && env {envs} {' '.join(shlex.quote(c) for c in cmd)} 2>&1 | tee -a {shlex.quote(str(log))}; exec zsh"
    if tmux("has-session", "-t", SESSION, check=False).returncode != 0:
        tmux("new-session", "-d", "-s", SESSION)
    tmux("new-window", "-d", "-t", SESSION, "-n", name, shell)


def room(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([PY, "-m", "roomctl", "--repo", str(ROOM), *args], capture_output=True, text=True,
                          cwd=ROOT, env={**os.environ, **QUARANTINE, **(env or {})})


def truth() -> dict:
    return http("GET", f"{SIM}/sim/truth")[1]


def ci() -> dict:
    return http("GET", f"{WEB}/api/room/ci")[1]


def watch_verdict() -> dict:
    return (ci().get("watch") or {})


def free_spot(oid: str, to: tuple[float, float] | None) -> tuple[float, float]:
    """Somewhere on the same surface that nothing else stands on (a roommate does not stack things)."""
    objs = truth()["objects"]
    if oid not in objs:
        raise SystemExit(f"no object {oid!r} in the simulated room; try: {', '.join(sorted(objs))}")
    me = objs[oid]
    if to is not None:
        return to
    ex, ey = me["extents"][0] / 2 + 0.01, me["extents"][1] / 2 + 0.01
    cands = [DEFAULT_SPOTS.get(oid)] + [(me["x"] + dx, me["y"] + dy) for dx, dy in
                                         ((-0.2, 0.15), (0.2, -0.15), (-0.2, -0.15), (0.2, 0.15), (0.0, 0.25), (0.0, -0.25))]
    for c in cands:
        if c is None:
            continue
        clear = all(abs(c[0] - o["x"]) > ex + o["extents"][0] / 2 or abs(c[1] - o["y"]) > ey + o["extents"][1] / 2
                    for k, o in objs.items() if k != oid and abs(o["z"] - me["z"]) < 0.3)
        if clear:
            return c
    raise SystemExit(f"no free spot near {oid} to move it to; give one with --to X Y")


# ── verbs ───────────────────────────────────────────────────────────────────────────────

def seed(scene: str, reseed: bool) -> None:
    if ROOM.exists() and not reseed:
        print(f"sim room: {ROOM} (kept; --reseed to start it over)")
        return
    if ROOM.exists():
        shutil.rmtree(ROOM)
    ROOM.parent.mkdir(parents=True, exist_ok=True)
    for k, v in QUARANTINE.items():
        os.environ[k] = v
    from fake.scene_gen import FakeRoom
    FakeRoom(ROOM, quiet=True).commit(scene, "the bench, tidied")
    for f in ("sim-demo-room-clean.json", "sim-demo-room-state.json"):
        (CACHE / "rooms" / f).unlink(missing_ok=True)
    print(f"sim room: {ROOM} seeded from {scene}, HEAD is `main`")


def cmd_up(a) -> int:
    return _locked(a, "up", _cmd_up)


def _cmd_up(a) -> int:
    if str(ROOM) == str(Path(os.getenv("ROOM_GIT_PATH", "./room.git")).expanduser().resolve()):
        raise SystemExit("refusing: the sim room would be $ROOM_GIT_PATH itself")
    LOGS.mkdir(parents=True, exist_ok=True)
    seed(a.scene, a.reseed)
    if not up_at(f"{SIM}/health"):
        start_window("sim-bbsim", QUARANTINE, [PY, "fake/bbsim.py", "--scene", a.scene, "--ws-port", str(WS_PORT), "--api-port", str(API_PORT),
                                               "--speed", str(a.speed), "--fast", "--vox-interval", "0.2"], LOGS / "sim-bbsim.log")
        until(lambda: up_at(f"{SIM}/health"), 60, "bbsim to listen")
    until(lambda: http("GET", f"{SIM}/pose")[0] == 200, 30, "the simulated SLAM")
    print(f"bbsim: ws://127.0.0.1:{WS_PORT}  {SIM}  scene {a.scene}  speed {a.speed} m/s")
    if not (http("GET", f"{SIM}/health")[1].get("area")):
        http("POST", f"{SIM}/map/rectangle", AREA)
        until(lambda: not (http("GET", f"{SIM}/health")[1].get("job") or {}).get("running"), 300, "the sweep")
        print("bbsim: rectangle swept: the loop can patrol")
    if not up_at(f"{WEB}/api/health"):
        start_window("sim-web", WEB_ENV, [PY, "web/server.py"], LOGS / "sim-web.log")
        until(lambda: up_at(f"{WEB}/api/health"), 90, "the sim site")
    h = http("GET", f"{WEB}/api/health")[1]
    if (h.get("elastic") or {}).get("configured"):
        cmd_down(a)
        raise SystemExit("refusing: the sim site came up with Elasticsearch configured; it must not (D45)")
    print(f"sim site: {WEB}  room {ROOM}  elastic configured: False")
    if not window_exists("sim-watch"):
        start_window("sim-watch", WATCH_ENV, [PY, "-m", "roomctl", "--repo", str(ROOM), "watch", "--tier", "A", "--every", "1"],
                     LOGS / "sim-watch.log")
    until(lambda: watch_verdict().get("passes", 0) >= 1, 120, "the watch loop's first fresh pass")
    live = pids(f"roomctl --repo {ROOM} watch")
    if len(live) != 1:
        raise SystemExit(f"refusing: {len(live)} watch loops on {ROOM} ({live}); two would race each other's scans "
                         f"and mint two sets of job ids. Run `down` first.")
    print(f"watch: room watch --tier A (ROOM_ARM=sim) is up: {json.dumps(watch_verdict())[:160]}")
    if not settle_baseline():
        print("  WARNING: the room and its baseline still disagree; `check` will say so")
    print(f"logs: {LOGS}/sim-*.log   tmux windows: {', '.join(WINDOWS)} in session {SESSION}")
    return 0


def cmd_down(a) -> int:
    return _locked(a, "down", _cmd_down)


def _cmd_down(a) -> int:
    for name in WINDOWS:
        tmux("kill-window", "-t", f"{SESSION}:{name}", check=False)
    for port in (WS_PORT, API_PORT, WEB_PORT):
        for pid in subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                                  capture_output=True, text=True).stdout.split():
            subprocess.run(["kill", pid], check=False)
    kill_pattern(f"roomctl --repo {ROOM} watch")
    kill_pattern(f"fake/bbsim.py --scene clean_bench --ws-port {WS_PORT}")
    for _ in range(20):                                  # they are asked to stop; make sure they did
        if not (pids(f"roomctl --repo {ROOM} watch") or pids(f"bbsim.py --scene clean_bench --ws-port {WS_PORT}")):
            break
        time.sleep(0.25)
    left = pids(f"roomctl --repo {ROOM} watch") + pids(f"bbsim.py --scene clean_bench --ws-port {WS_PORT}")
    print("down: sim-bbsim, sim-watch, sim-web" + (f"  (still up: {left})" if left else ""))
    return 0


def cmd_mess(a) -> int:
    return _locked(a, "mess", _cmd_mess)


def _cmd_mess(a) -> int:
    x, y = free_spot(a.object, tuple(a.to) if a.to else None)
    http("POST", f"{SIM}/sim/move", {"object_id": a.object, "x": x, "y": y})
    print(f"a roommate moved {a.object} to ({x:.2f}, {y:.2f}). Watch {WEB}: pending, then confirmed, then tidied and verified.")
    return 0


def cmd_decide(a) -> int:
    return _locked(a, "decide", _cmd_decide)


def _cmd_decide(a) -> int:
    x, y = free_spot(a.object, tuple(a.to) if a.to else None)
    http("POST", f"{SIM}/sim/move", {"object_id": a.object, "x": x, "y": y})
    print(f"{a.object} moved to ({x:.2f}, {y:.2f}); waiting for the room to SEE it there...")
    until(lambda: any(c["object_id"] == a.object for c in (watch_verdict().get("pending") or []) + (watch_verdict().get("confirmed") or [])),
          180, f"the loop to notice {a.object}")
    r = room("pr", "open", a.object, "--as-seen", "--json")
    if r.returncode:
        raise SystemExit(f"pr open failed: {r.stderr.strip()}")
    pr_id = json.loads(r.stdout)["id"]
    r = room("pr", "approve", str(pr_id), "--json")
    if r.returncode:
        raise SystemExit(f"pr approve failed: {r.stderr.strip()}")
    print(f"\"I meant that\": PR #{pr_id} opened as seen and approved ({json.loads(r.stdout)['merge_sha'][:7]}). "
          f"`main` now has {a.object} there; the robot leaves it alone.")
    return 0


def cmd_reset(a) -> int:
    return _locked(a, "reset", _cmd_reset)


def _cmd_reset(a) -> int:
    scene = a.scene
    http("POST", f"{SIM}/sim/scene", {"name": scene})
    http("POST", f"{SIM}/stop")
    tmux("kill-window", "-t", f"{SESSION}:sim-watch", check=False)
    subprocess.run(["pkill", "-f", f"roomctl --repo {ROOM} watch"], check=False)
    time.sleep(1.0)
    seed(scene, reseed=True)
    start_window("sim-watch", WATCH_ENV, [PY, "-m", "roomctl", "--repo", str(ROOM), "watch", "--tier", "A", "--every", "1"],
                 LOGS / "sim-watch.log")
    until(lambda: watch_verdict().get("passes", 0) >= 1, 180, "a first pass")
    settle_baseline()
    print(f"reset: the scene is {scene} again, the sim room is re-seeded, the loop is watching")
    return 0


def settle_baseline(timeout: float = 240) -> bool:
    """Make `main` what the ROBOT sees, not what the scene file says.

    The room is seeded from the scene's exact truth poses, but the loop measures them from 1.5 cm voxels, so the
    first scan can land a quantum away on an object or two. That leaves the room permanently dirty against its own
    baseline: the badge starts red, the loop never reaches a clean pass, and so nothing is ever "verified by
    rescan" - which is what made beat 2 time out about one run in four. Commit what the robot sees, and the story
    starts from a room that agrees with itself."""
    deadline, stable = time.time() + timeout, 0
    while time.time() < deadline:
        if ci().get("state") == "clean":
            stable += 1
            if stable >= 2:
                return True
        else:
            stable = 0
            r = room("commit", "-m", "the bench, as the robot sees it", "--no-scan")
            if r.returncode:
                print(f"  (could not commit the baseline: {r.stderr.strip()[:120]})")
                return False
            print("  baseline: committed the room as the robot sees it")
        time.sleep(4)
    return False


def event_tap() -> list:
    """Everything the site fans out, kept so a failed run can say what the robot was doing when it stopped."""
    rows: list = []

    def run():
        while True:
            try:
                with urllib.request.urlopen(f"{WEB}/api/events", timeout=3600) as f:
                    name = None
                    for raw in f:
                        line = raw.decode("utf8", "replace").strip()
                        if line.startswith("event:"):
                            name = line[6:].strip()
                        elif line.startswith("data:") and name in ("job", "chore"):
                            rows.append((time.strftime("%H:%M:%S"), name, line[5:].strip()[:220]))
            except Exception:  # noqa: BLE001
                time.sleep(1)
    threading.Thread(target=run, daemon=True).start()
    return rows


def diagnose(rows: list) -> None:
    """What was true when it stopped. A stall that prints nothing costs an hour; this costs ten lines."""
    print("--- what the robot was doing:")
    for t, name, data in rows[-8:]:
        print(f"    {t} {name} {data}")
    try:
        d = ci()
        w = d.get("watch") or {}
        print(f"    site: {d.get('state')} lvj={d.get('last_verified_job')} clean={w.get('clean')} passes={w.get('passes')} "
              f"blocked={w.get('blocked')} carried={w.get('carried')}")
        print(f"    confirmed={[(c['object_id'], c['type'], c.get('job_id')) for c in w.get('confirmed') or []]} "
              f"pending={[(c['object_id'], c['type']) for c in w.get('pending') or []]}")
        t = truth()
        print(f"    bbsim: held={t.get('held')} stale_visible={t.get('stale_visible')} "
              f"job={(http('GET', f'{SIM}/health')[1] or {}).get('job')}")
        print(f"    mug={tree_xy('mug_a1b2')} (main {head_xy('mug_a1b2')})  lamp={tree_xy('lamp_2d9b')} (main {head_xy('lamp_2d9b')})")
    except Exception as e:  # noqa: BLE001
        print(f"    (could not read the state: {e})")


def cmd_status(a) -> int:
    print(f"bbsim   {'up' if up_at(f'{SIM}/health') else 'down'}   {SIM}")
    print(f"site    {'up' if up_at(f'{WEB}/api/health') else 'down'}   {WEB}")
    print(f"watch   {'window up' if window_exists('sim-watch') else 'down'}")
    if up_at(f"{WEB}/api/health"):
        c = ci()
        print(json.dumps({"state": c.get("state"), "last_verified_job": c.get("last_verified_job"), "watch": c.get("watch")}, indent=1))
    return 0


# ── the scripted run: beats 1 to 3 ─────────────────────────────────────────────────────

def _xy(text: str) -> tuple[float, float] | None:
    """(x, y) from an object record's POSE. A record carries x/y twice, under `pose:` and again under `extents:`,
    and reading the second pair compares a lamp's position with its width."""
    in_pose = False
    got: dict[str, float] = {}
    for line in text.splitlines():
        if not line.startswith(" "):
            in_pose = line.strip() == "pose:"
            continue
        if not in_pose:
            continue
        key, _, val = line.partition(":")
        if key.strip() in ("x", "y"):
            try:
                got[key.strip()] = float(val)
            except ValueError:
                return None
    return (got["x"], got["y"]) if len(got) == 2 else None


def tree_xy(oid: str) -> tuple[float, float] | None:
    """Where the room's working tree (what the robot last saw) puts the object."""
    for f in ROOM.glob(f"zones/*/{oid}.yaml"):
        return _xy(f.read_text())
    return None


def head_xy(oid: str) -> tuple[float, float] | None:
    """Where `main` says it lives."""
    for f in ROOM.glob(f"zones/*/{oid}.yaml"):
        zone = f.parent.name
        return _xy(room("show", f"HEAD:zones/{zone}/{oid}.yaml").stdout)
    return None


def seen_at(oid: str, x: float, y: float, tol: float = 0.04) -> bool:
    p = tree_xy(oid)
    return p is not None and abs(p[0] - x) < tol and abs(p[1] - y) < tol


def at_home(oid: str, tol: float = 0.05) -> bool:
    """Is the object where `main` says it lives? (bbsim's truth against the committed record.)"""
    want, o = head_xy(oid), truth()["objects"].get(oid)
    return bool(want and o) and abs(o["x"] - want[0]) < tol and abs(o["y"] - want[1]) < tol


def ready_to_run() -> str | None:
    """Why the room is not at a clean start, or None. `check` asserts a story from the beginning, so it has to
    begin at the beginning: a leftover mess or an approved PR from a previous run changes what beat 3 means."""
    if not ci().get("state") == "clean":
        return f"the room is {ci().get('state')}"
    if json.loads(room("pr", "list", "--json").stdout or "[]"):
        return "a pull request is still open"
    for oid in ("mug_a1b2", "lamp_2d9b"):
        if not at_home(oid):
            return f"{oid} is not where `main` says it lives"
    return None


def cmd_check(a) -> int:
    return _locked(a, "check", _cmd_check)


def _cmd_check(a) -> int:
    rows = event_tap()
    try:
        return _check_beats(a, rows)
    except SystemExit as e:
        print(f"{e}")
        diagnose(rows)
        raise SystemExit(1) from None


def _check_beats(a, rows) -> int:
    t0 = time.time()
    fails = []
    # A scripted run asserts a story from the beginning, so it starts at the beginning, every time. Judging
    # "is this a clean start?" by whether the room AGREES with `main` is not enough: after a previous run `main`
    # itself says the lamp lives where beat 3 put it, and beat 3 then has nothing to move.
    if a.no_reset:
        why = ready_to_run()
        if why:
            raise SystemExit(f"not at a clean start ({why}); drop --no-reset, or run `demo_sim.py reset`")
    else:
        print("starting from a fresh room")
        _cmd_reset(argparse.Namespace(scene="clean_bench", force=True))

    def ok(cond, what):
        print(("  ok   " if cond else "  FAIL ") + what)
        if not cond:
            fails.append(what)

    if not (up_at(f"{SIM}/health") and up_at(f"{WEB}/api/health") and window_exists("sim-watch")):
        raise SystemExit("not up: run `python scripts/demo_sim.py up` first")
    print("beat 1: the room is `main`")
    h = http("GET", f"{WEB}/api/health")[1]
    ok(not (h.get("elastic") or {}).get("configured"), "the sim site has no Elasticsearch (D45: nothing simulated reaches the indices)")
    until(lambda: watch_verdict().get("clean") and watch_verdict().get("passes", 0) >= 1, a.timeout / 3, "a clean pass")
    c = ci()
    ok(c["state"] == "clean" and c["watch"]["clean"] is True, f"badge green: state {c['state']}, loop clean after {c['watch']['passes']} passes")
    ok(c["watch"]["blocked"] is None, f"the loop is looking (blocked: {c['watch']['blocked']})")
    ok(http("GET", f"{WEB}/api/chores")[1] == [], "no chores")
    s, snap = http("GET", f"{WEB}/api/nav/snapshot")
    ok(s == 200 and snap.get("pose") and snap.get("grid"), f"live map: pose ({snap.get('pose', {}).get('x')}, {snap.get('pose', {}).get('y')}), grid {bool(snap.get('grid'))}")
    ok((truth().get("stats") or {}).get("deltas", 0) >= 0 and (http("GET", f"{SIM}/health")[1].get("job") or {}).get("kind") == "patrol",
       "the robot is patrolling")

    print("beat 2: a roommate makes a mess")
    mug = "mug_a1b2"
    home = truth()["objects"][mug]
    x, y = free_spot(mug, None)
    http("POST", f"{SIM}/sim/move", {"object_id": mug, "x": x, "y": y})
    print(f"  moved {mug} to ({x:.2f}, {y:.2f})")
    st = until(lambda: (lambda w: w if any(r["object_id"] == mug for r in w.get("pending") or []) else None)(watch_verdict()),
               a.timeout / 3, "the move to be seen once (pending)")
    ok(st.get("clean") is True, "one glance: pending, and the badge is STILL green")
    st = until(lambda: (lambda w: w if any(r["object_id"] == mug and r.get("job_id") for r in w.get("confirmed") or []) else None)(watch_verdict()),
               a.timeout / 3, "the second fresh pass (confirmed, tidy job started)")
    row = next(r for r in st["confirmed"] if r["object_id"] == mug)
    job = row["job_id"]
    ok(st["clean"] is False and ci()["state"] == "dirty", f"confirmed on pass {row['passes']}: the badge is red; {job} started ({row['verdict']} -> {row['action']})")
    st = until(lambda: (lambda c: c if c.get("last_verified_job") == job else None)(ci()), a.timeout / 2, f"{job} to be verified by a clean fresh pass")
    o = until(lambda: truth()["objects"].get(mug), 60, f"{mug} to be out of the gripper and back in the room")
    ok(abs(o["x"] - home["x"]) < 0.02 and abs(o["y"] - home["y"]) < 0.02, f"the sim arm put {mug} back at ({o['x']:.2f}, {o['y']:.2f})")
    ok(st["state"] == "clean" and st["watch"]["clean"] is True, f"badge green again; last_verified_job = {st['last_verified_job']} (verified by rescan)")
    ok(http("GET", f"{WEB}/api/chores")[1] == [], "still no chores (Tier A did it itself)")

    print("beat 3: \"I meant that\"")
    lamp = "lamp_2d9b"
    x, y = free_spot(lamp, None)
    if seen_at(lamp, x, y, tol=0.06):                    # never "move" it to where it already is: that is not a story
        x, y = round(x - 0.18, 3), y
    http("POST", f"{SIM}/sim/move", {"object_id": lamp, "x": x, "y": y})
    print(f"  moved {lamp} to ({x:.2f}, {y:.2f})")
    # "I meant that" is pressed when the ROOM SHOWS the thing where you put it: opening an as-seen PR before that
    # commits the pose the room still believes, which is the old one, and the drift never clears
    # this one waits for the PATROL to come round to the lamp, which is the slowest step in the story: the mug in
    # beat 2 sits near where the robot parks, the lamp does not
    until(lambda: seen_at(lamp, x, y), a.timeout, f"the room's own record of {lamp} to move to where it now is")
    r = room("pr", "open", lamp, "--as-seen", "--json")
    ok(r.returncode == 0, f"pr open --as-seen: {r.stderr.strip() or 'opened'}")
    pr_id = json.loads(r.stdout)["id"] if r.returncode == 0 else None
    r2 = room("pr", "approve", str(pr_id), "--json") if pr_id else None
    ok(r2 is not None and r2.returncode == 0, f"pr approve {pr_id}: {(r2.stderr.strip() if r2 else '') or 'merged'}")
    st = until(lambda: (lambda w: w if w.get("clean") and not any(r["object_id"] == lamp for r in (w.get("pending") or []) + (w.get("confirmed") or []))
                        else None)(watch_verdict()), a.timeout / 3, "the loop to agree with the new main")
    o = until(lambda: truth()["objects"].get(lamp), 60, f"{lamp} to be in the room")
    assert o
    ok(abs(o["x"] - x) < 0.02 and abs(o["y"] - y) < 0.02, f"the robot left {lamp} where it was put ({o['x']:.2f}, {o['y']:.2f})")
    head = head_xy(lamp)
    ok(bool(head) and abs(head[0] - o["x"]) < 0.05 and abs(head[1] - o["y"]) < 0.05,
       f"`main` now says the lamp lives there: {head} vs the sim's ({o['x']:.2f}, {o['y']:.2f})")
    green = until(lambda: ci()["state"] == "clean" and (ci().get("watch") or {}).get("clean"), a.timeout / 4,
                  "the badge to go green on the new main", every=1.0)
    ok(bool(green), "badge green: a decision is not a mess")
    ok(http("GET", f"{WEB}/api/chores")[1] == [], "no chores")
    ok(not (http("GET", f"{WEB}/api/health")[1].get("elastic") or {}).get("configured"), "still no Elasticsearch on the sim site")
    print(f"{'PASS' if not fails else 'FAIL'}: beats 1 to 3 in {time.time() - t0:.0f} s" + ("" if not fails else f"; failed: {fails}"))
    return 0 if not fails else 1


def _locked(a, what: str, fn):
    with driving(what, getattr(a, "force", False)):
        return fn(a)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("up"); p.add_argument("--scene", default="clean_bench"); p.add_argument("--speed", type=float, default=0.5)
    p.add_argument("--reseed", action="store_true", help="start the sim room over from the scene")
    sub.add_parser("down"); sub.add_parser("status")
    for v in ("mess", "decide"):
        p = sub.add_parser(v); p.add_argument("object"); p.add_argument("--to", nargs=2, type=float, metavar=("X", "Y"))
    p = sub.add_parser("reset"); p.add_argument("--scene", default="clean_bench")
    p = sub.add_parser("check"); p.add_argument("--timeout", type=float, default=900)
    p.add_argument("--no-reset", action="store_true", help="run against the room as it is, instead of a clean start")
    for name, sp in sub.choices.items():                                  # one driver at a time; --force breaks a dead lock
        sp.add_argument("--force", action="store_true", help="take the sim even if another session holds it")
    a = ap.parse_args(argv)
    return {"up": cmd_up, "down": cmd_down, "mess": cmd_mess, "decide": cmd_decide, "reset": cmd_reset,
            "status": cmd_status, "check": cmd_check}[a.verb](a)


if __name__ == "__main__":
    sys.exit(main())
