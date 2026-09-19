"""The caretaker: what the watch loop's `jobs(action, change)` hook does about a confirmed change.

    Tier A   tidy / lost_and_found   plan (this one object, the rest of the room as seen) -> route on Bracket Bot's
                                     own floor map -> execute: drive, pick, place. Honest about how far it got:
                                     "2 of 3" is a result, not a success.
    Tier B   chore                   the robot cannot put it back, so it goes and STANDS where it can see the mess,
                                     says what is wrong, and the chore (roomctl/chores.py) waits for a person.

A job runs on its own thread (a tidy is minutes at 0.14 m/s; the room must stay watched meanwhile) and one at a
time: `busy()` is how the watch loop knows not to start another, not to send the robot patrolling mid-pick, and
not to call a job verified while it is still running. The hook returns the job id at once; nothing here decides
that a job WORKED. Only a clean fresh pass does (roomctl/watch.py).

A map reset mid-job (Bracket Bot's map_gen changed: every pose the plan was routed on is void) is not a failure
of the robot: the job waits for the registration to catch up, redefines the area the reset wiped, and retries
what is left ONCE. Anything else that fails is filed once, where it happens (roomctl.bb_nav.report).
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from types import SimpleNamespace
from typing import Any, Callable

from roomctl import executor
from roomctl.executor import BasePose, RobotError
from roomctl.repo import Repo, load_room
from roomctl.state import read_tree

RETRY_CODES = ("map_reset", "not_registered", "slam_not_ready")


class SimArm:
    """The arm, in fake/bbsim.py: POST /sim/arm moves the object in the simulated room, so the next fresh pass
    really does verify it. The room repo's ids are the scene's ids; an object the repo has never named (an
    untracked `unknown_*`) is matched to the simulated object standing where the robot was told to pick."""

    def __init__(self, host: str, api_port: int, timeout: float = 5.0, reach: float = 0.85):
        self.base, self.timeout, self._alias = f"http://{host}:{api_port}", timeout, {}
        # The simulated arm has no kinematics, so it brings its own reach model. With the real placeholder
        # (executor.ARM: 0.48 m) and the base's 0.28 m inflation, only what lies within ~0.20 m of a table's
        # edge can be stood in front of at all: a fact about the hardware numbers, not about this file.
        self.model = executor.ArmModel(r_max=reach, z_max=1.20)

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as f:
                return json.loads(f.read())
        except OSError as e:
            raise RobotError("arm_failed", f"{path}: {e}") from None

    def _sim_id(self, object_id: str, pose) -> str:
        objs = self._call("GET", "/sim/truth")["objects"]
        if object_id in objs:
            return object_id
        near = min(objs.items(), key=lambda kv: math.hypot(kv[1]["x"] - pose.x, kv[1]["y"] - pose.y), default=None)
        if near is None or math.hypot(near[1]["x"] - pose.x, near[1]["y"] - pose.y) > 0.15:
            raise RobotError("grasp_failed", f"nothing to pick up at ({pose.x:.2f}, {pose.y:.2f})")
        return near[0]

    def pick(self, object_id, pose) -> None:
        sid = self._alias[object_id] = self._sim_id(object_id, pose)
        self._call("POST", "/sim/arm", {"op": "pick", "object_id": sid})

    def place(self, object_id, pose, zone) -> None:
        sid = self._alias.pop(object_id, object_id)
        self._call("POST", "/sim/arm", {"op": "place", "object_id": sid, "pose": [pose.x, pose.y, pose.z, pose.yaw]})


class Caretaker:
    def __init__(self, repo: Repo, nav: Any, reg_provider: Callable[[], tuple | None], *, tier: str = "A", arm=None,
                 voice=None, publish: Callable[[str, dict], Any] | None = None, threaded: bool = True,
                 reregister_s: float = 30.0, nav_timeout: float = 120.0, sleep=time.sleep):
        self.repo, self.nav, self.reg_provider, self.tier = repo, nav, reg_provider, str(tier).upper()
        self.arm, self.voice, self.publish, self.threaded = arm, voice, publish, threaded
        self.reregister_s, self.nav_timeout, self.sleep = reregister_s, nav_timeout, sleep
        self.results: dict[str, dict] = {}              # job id -> how it ended (or {"state": "running"})
        self._n, self._lock, self._thread = 0, threading.Lock(), None
        self._bounds: dict | None = None

    # ── what the watch loop asks ────────────────────────────────────────────────────────
    def busy(self) -> bool:
        return any(r.get("state") == "running" for r in self.results.values())

    def running(self, job_id: str) -> bool:
        return self.results.get(job_id, {}).get("state") == "running"

    def failed(self, job_id: str) -> bool:
        return self.results.get(job_id, {}).get("state") == "failed"

    def __call__(self, action: str, change: dict) -> str | None:
        """Start a job for one confirmed change. None = not now (one at a time): ask again on a later pass."""
        return self._start("visit" if action == "chore" else action, self._visit if action == "chore" else self._tidy,
                           change, [change["object_id"]])

    def batch(self, changes: list[dict]) -> str | None:
        """Every confirmed tidy / lost-and-found at once, as ONE job, so "2 of 3" means what it says."""
        return self._start("tidy", self._tidy, {"object_id": changes[0]["object_id"], "objects": [c["object_id"] for c in changes]},
                           [c["object_id"] for c in changes]) if changes else None

    def _start(self, kind: str, run, change: dict, objects: list[str]) -> str | None:
        with self._lock:
            if self.busy():
                return None
            self._n += 1
            job_id = f"{kind}-{self._n}"
            self.results[job_id] = {"state": "running", "action": kind, "object_id": change["object_id"], "objects": objects}
        if self.threaded:
            self._thread = threading.Thread(target=self._guard, args=(run, job_id, change), daemon=True, name=job_id)
            self._thread.start()
        else:
            self._guard(run, job_id, change)
        return job_id

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # ── running one ─────────────────────────────────────────────────────────────────────
    def _guard(self, run, job_id: str, change: dict) -> None:
        try:
            result = run(job_id, change)
        except RobotError as e:
            result = {"ok": False, "error": e.code, "detail": e.detail, "done": 0, "total": 0}
        except Exception as e:  # noqa: BLE001  a bug here must end the job, not the watching
            result = {"ok": False, "error": "caretaker_bug", "detail": f"{type(e).__name__}: {e}", "done": 0, "total": 0}
        result.setdefault("summary", f"{result.get('done', 0)} of {result.get('total', 0)}")
        self.results[job_id] = {**self.results[job_id], **result, "state": "done" if result.get("ok") else "failed"}
        if self.publish:
            try:
                self.publish("job", {"id": job_id, "kind": self.results[job_id]["action"], "state": self.results[job_id]["state"],
                                     "object_id": change["object_id"], "result": result})
            except Exception:  # noqa: BLE001
                pass

    def _robot(self):
        from roomctl.bb_nav import BBNavRobot
        return BBNavRobot(self.nav, self.reg_provider, arm=self.arm, voice=self.voice, resume_patrol=False,
                          timeout=self.nav_timeout, sleep=self.sleep)

    def _reg(self) -> SimpleNamespace:
        got = self.reg_provider()
        if not got:
            raise RobotError("not_registered", "no room<->robot transform yet")
        T, gen = got if isinstance(got, tuple) else (got, None)
        st = getattr(self.nav, "state", None)
        if gen is not None and st is not None and getattr(st, "map_gen", gen) != gen:
            raise RobotError("map_reset", f"the robot's map is gen {st.map_gen}, the registration is for gen {gen}")
        return SimpleNamespace(T=T, map_gen=gen)

    def _map_changed(self) -> bool:
        got, st = self.reg_provider(), getattr(self.nav, "state", None)
        gen = got[1] if isinstance(got, tuple) else None
        return gen is not None and st is not None and getattr(st, "map_gen", gen) != gen

    def _costmap(self, reg, room: dict):
        """Where the robot may stand: Bracket Bot's own 2-D floor map, in the room frame, with the live voxel
        map to ray-cast through. None when the robot has no area map (then nothing is routed, only ordered)."""
        area = getattr(self.nav, "area", None)
        if area is None:
            return None
        from perception import bb_source
        from perception.costmap import Costmap
        bounds = area["area"]["bounds"] if isinstance(area, dict) else area.bounds
        self._bounds = dict(bounds)                      # remembered: a map reset wipes the area, and we put it back
        grid = None
        if getattr(self.nav, "mirror", None) is not None and len(self.nav.mirror):
            pts, _ = bb_source.room_points(self.nav.mirror, reg)
            grid = bb_source.visibility_grid(pts, room.get("zones"))
        return Costmap.from_bb_grid(area, reg, grid=grid)

    def _plan_for(self, oids: list[str], room: dict, done: set[str] = frozenset()) -> executor.Plan:
        """Only THESE objects move; everything else is an obstacle standing where it was last seen."""
        current, head = read_tree(self.repo.path), self.repo.records()
        for d in done:                                   # already carried out this job: it is where main says
            if d in head:
                current[d] = head[d]
            else:
                current.pop(d, None)
        target = dict(current)
        for oid in oids:
            if oid in head:
                target[oid] = head[oid]
            else:
                target.pop(oid, None)                    # main has never heard of it: lost and found
        return executor.plan(current, target, room)

    def _tidy(self, job_id: str, change: dict) -> dict:
        oids, room = list(change.get("objects") or [change["object_id"]]), load_room(self.repo.path)
        robot, done, failed, tries = self._robot(), set(), [], 0
        total, retried_after = None, None
        while True:
            tries += 1
            p = self._plan_for(oids, room, done)
            total = total if total is not None else len(p.ops) + len(p.unapplied)
            retry = None
            try:
                reg = self._reg()
                cm = self._costmap(reg, room)
                if cm is not None:
                    p = executor.route(p, cm, robot.pose(), arm=getattr(self.arm, "model", None) or executor.ARM)
                out = executor.execute(p, robot, self.publish, job_id)
            except RobotError as e:
                if e.code not in RETRY_CODES:
                    raise
                out, retry = executor.Outcome(), e.code
            done |= {op.object_id for op in out.done}
            failed = [(op.object_id, why) for op, why in out.failed + out.skipped] + list(p.unapplied)
            retry = retry or next((c for c in RETRY_CODES if any(c in why for _, why in failed)), None)
            if retry and self._map_changed():
                retry = "map_reset"                      # "SLAM not ready" is only the first second of a reset
            if retry and tries == 1 and self._recover():
                retried_after = retry
                continue                                 # once: a second reset mid-job is somebody pressing buttons
            break
        n_done = len(done)
        ok = not failed and n_done == total
        robot.say(f"I put {n_done} of {total} back." if total and not ok else "Tidied.")
        self._resume_patrol()
        return {"ok": ok, "done": n_done, "total": total, "summary": f"{n_done} of {total}", "tries": tries,
                "failed": [{"object_id": o, "why": w} for o, w in failed], "retried_after": retried_after}

    def _recover(self) -> bool:
        """After a map reset: wait for a registration that belongs to the new map, then give the robot its area
        back (the reset wiped it: no area, no floor map, nowhere to stand)."""
        deadline = time.monotonic() + self.reregister_s
        while time.monotonic() < deadline:
            try:
                self._reg()
                break
            except RobotError:
                self.sleep(0.25)
        else:
            return False
        if getattr(self.nav, "area", None) is None and hasattr(self.nav, "define_area"):
            b = ((load_room(self.repo.path).get("nav") or {}).get("area")) or self._bounds
            if b:
                try:
                    self.nav.define_area(b["xmin"], b["xmax"], b["ymin"], b["ymax"], sweep=True)   # the floor map went too
                    job = self.nav.wait(600, sleep=self.sleep)
                    if job is not None and job.error:
                        return False
                    end = time.monotonic() + 10
                    while getattr(self.nav, "area", None) is None and time.monotonic() < end:
                        self.sleep(0.25)
                except RobotError:
                    return False
        return True

    def _visit(self, job_id: str, change: dict) -> dict:
        """Tier B: go and look at it, say so. The chore itself is already filed; this can fail without harm."""
        oid, room = change["object_id"], load_room(self.repo.path)
        rec = read_tree(self.repo.path).get(oid) or self.repo.records().get(oid)
        if rec is None:
            return {"ok": False, "error": "unknown_object", "done": 0, "total": 1}
        robot, reg = self._robot(), self._reg()
        cm = self._costmap(reg, room)
        if cm is None:
            return {"ok": False, "error": "no_area_map", "done": 0, "total": 1}
        from perception.costmap import solve_viewpoint
        here = robot.pose()
        xyz = (rec.pose.x, rec.pose.y, rec.pose.z)
        ignore = math.hypot(rec.extents.x, rec.extents.y, rec.extents.z) / 2 + cm.grid.leaf
        got = solve_viewpoint(xyz, cm, (here.x, here.y), (here.x, here.y, math.radians(here.yaw)), executor.ROBOT.eye_h,
                              r_min=0.6, r_max=1.4, min_sep_deg=0.0, ignore_end=ignore)
        if got is None:
            return {"ok": False, "error": "no_viewpoint", "done": 0, "total": 1}
        stand = BasePose(round(got[0], 3), round(got[1], 3), round(math.degrees(got[2]) % 360, 1))
        try:
            robot.drive(stand)
        finally:
            self._resume_patrol()
        what = {"modified": "is not where it belongs", "moved": "is not where it belongs", "deleted": "is missing",
                "untracked": "does not belong here"}.get(change.get("type"), "is out of place")
        robot.led("attention")
        robot.say(f"The {rec.cls} {what}. I can't move it myself, so I've filed a chore.")
        return {"ok": True, "done": 1, "total": 1, "stood_at": [stand.x, stand.y, stand.yaw], "chore_id": change.get("chore_id")}

    def _resume_patrol(self) -> None:
        if getattr(self.nav, "area", None) is not None and hasattr(self.nav, "patrol"):
            try:
                self.nav.patrol()
            except Exception:  # noqa: BLE001
                pass
