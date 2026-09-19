"""`room watch`, `room chores`, `room status --live`: the watch loop's verbs (wired into roomctl/cli.py).

The robot is the one in $BB_HOST (roomctl.bb_nav.BBNav.from_env). The registration T_bb<-room comes from,
in order: roomctl.registration (when that module exists), $ROOM_BB_T="theta_deg,tx,ty[,dz]" (a taught
transform), or, against the simulator only, its own GET /sim/truth.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.request

from roomctl import chores, frames
from roomctl.repo import GitError, Repo


def connect():
    from roomctl.bb_nav import BBNav
    nav = BBNav.from_env().start()
    deadline = time.time() + float(os.getenv("BB_CONNECT_S", 15))
    while time.time() < deadline and (nav.state is None or not len(nav.mirror)):
        time.sleep(0.1)
    if nav.state is None:
        nav.close()
        raise GitError(f"no pose from the robot at {nav.host}:{nav.ws_port} (is bbapps/nav, or fake/bbsim.py, running?)")
    return nav


def reg_provider(nav):
    try:                                             # the real thing, once it lands (plan/roommate/03 §2)
        from roomctl import registration
        if hasattr(registration, "provider"):
            return registration.provider(nav)
    except ImportError:
        pass
    raw = os.getenv("ROOM_BB_T", "").strip()
    if raw:
        v = [float(x) for x in raw.split(",")]
        T = frames.SE2(math.radians(v[0]), v[1], v[2], v[3] if len(v) > 3 else 0.0)
        return lambda: (T, None)

    def from_sim():                                  # the simulator knows its own T; a real robot has no /sim/
        try:
            with urllib.request.urlopen(f"http://{nav.host}:{nav.api_port}/sim/truth", timeout=2) as f:
                d = json.loads(f.read())
        except (OSError, ValueError):
            return None
        t = d["T_bb_from_room"]
        return frames.SE2(t["theta"], t["tx"], t["ty"], t["dz"]), d["map_gen"]
    return from_sim


def web_publisher():
    """POST the loop's events to the dashboard's inlet, when $ROOM_WEB_URL says where it is."""
    base = os.getenv("ROOM_WEB_URL", "").strip().rstrip("/")
    if not base:
        return None
    token = os.getenv("GITIRL_CLOUD_TOKEN", "").strip()

    def publish(event: str, data: dict) -> None:
        req = urllib.request.Request(f"{base}/api/edge/event", data=json.dumps({"event": event, "data": data}).encode(),
                                     headers={"content-type": "application/json",
                                              **({"authorization": f"Bearer {token}"} if token else {})})
        urllib.request.urlopen(req, timeout=3).close()
    return publish


def make_watch(repo: Repo, nav, tier: str, act: bool, **kw):
    from roomctl.watch import Watch
    beat = None
    try:
        from telemetry.room_clean import RoomCleanBeat
        beat = RoomCleanBeat()
    except Exception:  # noqa: BLE001  the badge is optional; watching the room is not
        pass
    reg, publish, jobs = reg_provider(nav), web_publisher(), None
    if act and str(tier).upper() in ("A", "B"):                     # C: the robot does not move
        from roomctl.caretaker import Caretaker, SimArm
        arm = SimArm(nav.host, nav.api_port) if os.getenv("ROOM_ARM", "").strip() == "sim" else None
        jobs = Caretaker(repo, nav, reg, tier=tier, arm=arm, publish=publish)   # no arm: pick refuses, honestly
    return Watch(repo, nav, reg, tier=tier, act=act, publish=publish, heartbeat=beat, jobs=jobs, **kw)


def render(state, paint) -> str:
    badge = paint("32", "clean") if state.clean else paint("31", "dirty")
    out = [f"On branch {state.branch or '?'} @ {state.head or '-'}   room is {badge}"
           f"   (pass {state.passes}, {state.stale_blocks} stale blocks)"]
    if state.blocked:
        out.append(paint("33", f"  not looking: {state.blocked}"))
    for title, rows, color in (("Confirmed (seen on two fresh passes):", state.confirmed, "31"),
                               ("Pending (seen once; waiting for the next fresh pass):", state.pending, "33")):
        if rows:
            out.append(title)
            out += [paint(color, f"  {c['type']:<10} {c['path']}   {c['verdict']} -> {c['action']}"
                                 f"{'  ' + c['chore_id'] if c.get('chore_id') else ''}") for c in rows]
    if state.ignored:
        out.append(f"  ({state.ignored} change{'s' if state.ignored != 1 else ''} in personal zones: not our business)")
    if state.last_verified_job:
        out.append(f"  last verified by rescan: {state.last_verified_job}")
    return "\n".join(out)


def cmd_watch(repo: Repo, a, Paint) -> int:
    repo.require()
    nav = connect()
    paint = Paint(sys.stdout.isatty())
    try:
        w = make_watch(repo, nav, a.tier, not a.no_act)
        shown = None
        stop_at = time.time() + a.for_s if a.for_s else None

        def stop():
            nonlocal shown
            v = w.state.verdict()
            if v != shown:
                shown = v
                print(json.dumps(w.state.to_dict()) if a.json else render(w.state, paint), flush=True)
            return stop_at is not None and time.time() >= stop_at
        w.run(every_s=a.every, stop=stop)
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        nav.close()


def cmd_status_live(repo: Repo, a, Paint) -> int:
    """One fresh pass, then status: what `room status` means when the scanner is the robot's live map."""
    repo.require()
    nav = connect()
    try:
        w = make_watch(repo, nav, os.getenv("ROOM_TIER", "C"), act=False, debounce_passes=1, pass_gap_s=0.0)
        deadline = time.time() + float(os.getenv("ROOM_LIVE_S", 20))
        st = w.tick()
        while st.passes == 0 and time.time() < deadline:
            time.sleep(0.5)
            st = w.tick()
        if st.passes == 0:
            raise GitError(f"the robot never gave a fresh look ({st.blocked or 'every block is stale'})")
        print(json.dumps(st.to_dict(), indent=2) if a.json else render(st, Paint(sys.stdout.isatty())))
        return 1 if a.exit_code and not st.clean else 0
    finally:
        nav.close()


def cmd_chores(repo: Repo, a, Paint) -> int:
    repo.require()
    rows = chores.list_chores(repo, None if a.all else "open")
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    paint = Paint(sys.stdout.isatty())
    if not rows:
        print("no open chores: nothing the roommate could not put back itself")
    for c in rows:
        who = f"  for {c['owner']}" if c.get("owner") else ""
        line = f"{c['id']:<10} {c['type']:<10} zones/{c['zone']}/{c['object_id']}   {c['verdict']}{who}   opened {c['opened_at']}"
        print(paint("31", line) if c["status"] == "open" else f"{line}   closed {c['closed_at']} by {c['closed_by']}")
    return 0
