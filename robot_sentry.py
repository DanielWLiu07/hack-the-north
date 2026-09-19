#!/usr/bin/env python3
"""robot_sentry.py — the robot's side of Sentry (docs/28): it files its own bugs and closes them by
physically tidying up.

  SentryIssues      Sentry's REST API with SENTRY_AUTH_TOKEN: find, comment, resolve.
  SelfHealingRobot  wraps the executor's Robot. A slipped grasp is retried; after the RESCAN
                    verifies the object landed, the issue it filed is resolved by the robot, and
                    the timeline reads: grasp_slipped -> robot retried, rescan clean -> resolved.
  IssueMirror       one loop: unresolved issues -> the LED (green / amber / red); every new issue
                    -> said out loud; room.git clean or not -> the `room-clean` cron check-in, so a
                    messy room FAILS its heartbeat and Sentry alerts.

    python robot_sentry.py mirror [--once] [--local-say]    # the loop (LED + TTS + heartbeat)
    python robot_sentry.py demo --send                      # fail -> retry -> verify -> resolve, LIVE
                                                            # (synthetic data into Sentry: opt-in only)

The issue a heal resolves is found by the tags every failure report carries — failure_kind +
capture_id — so it works whoever filed it: the hub (a `job failed` on /stream, with telemetry and
the camera frame) or this wrapper (report=True, when no hub is running).
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import httpx

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("gitspace.robot_sentry")

# docs/16 §2.7 codes a second attempt can fix. unreachable_pose / not_balanced / busy it can't.
RETRYABLE = {"grasp_slipped"}
ROOM_MONITOR = {"schedule": {"type": "interval", "value": 1, "unit": "minute"},
                "checkin_margin": 2, "max_runtime": 1, "timezone": "UTC"}
SAID = {"fell_over": "I fell over", "grasp_slipped": "My grasp slipped",
        "unreachable_pose": "I can't reach that", "not_balanced": "I'm not balanced",
        "job_timeout": "A job timed out", "robot_unreachable": "I lost my connection"}


class SentryIssues:
    """The REST half of Sentry (obs.py is the SDK half). Every call is one HTTP request."""

    def __init__(self, org: str, token: str, project: str = "gitspace",
                 environment: str | None = "htn2026", client: httpx.Client | None = None,
                 robot_token: str | None = None):
        self.org, self.project, self.environment = org, project, environment
        self.http = client or httpx.Client(base_url="https://us.sentry.io/api/0", timeout=15,
                                           headers={"Authorization": f"Bearer {token}"})
        # Writes (the note, the resolve) go out as the "gitspace robot" internal integration when
        # its token is set, so the issue timeline names THE ROBOT as the actor, not a person.
        self.writer = (httpx.Client(base_url="https://us.sentry.io/api/0", timeout=15,
                                    headers={"Authorization": f"Bearer {robot_token}"})
                       if robot_token and client is None else self.http)
        self.as_robot = self.writer is not self.http

    @classmethod
    def from_env(cls) -> "SentryIssues | None":
        org, tok = os.getenv("SENTRY_ORG_SLUG", "").strip(), os.getenv("SENTRY_AUTH_TOKEN", "").strip()
        return (cls(org, tok, environment=os.getenv("SENTRY_ENVIRONMENT", "htn2026"),
                    robot_token=os.getenv("SENTRY_ROBOT_TOKEN", "").strip() or None) if org and tok else None)

    def find(self, query: str, period: str = "14d", limit: int = 50) -> list[dict]:
        params = {"query": query, "statsPeriod": period, "limit": limit}
        if self.environment:
            params["environment"] = self.environment
        r = self.http.get(f"/projects/{self.org}/{self.project}/issues/", params=params)
        r.raise_for_status()
        return r.json()

    def comment(self, issue_id: str, text: str) -> None:
        self.writer.post(f"/organizations/{self.org}/issues/{issue_id}/comments/", json={"text": text}).raise_for_status()

    def resolve(self, issue_id: str) -> None:
        self.writer.put(f"/organizations/{self.org}/issues/{issue_id}/", json={"status": "resolved"}).raise_for_status()

    def resolve_failures(self, kind: str, capture_id: str, note: str) -> list[str]:
        """Every UNRESOLVED `kind` failure filed against this capture: a note on its timeline,
        then resolved. Returns the short ids. The note goes first so the timeline reads in order."""
        done = []
        for issue in self.find(f"is:unresolved failure_kind:{kind} capture_id:{capture_id}"):
            self.comment(issue["id"], note)
            self.resolve(issue["id"])
            done.append(issue.get("shortId") or issue["id"])
        return done


class SelfHealingRobot:
    """The executor's Robot, plus: a slipped grasp is retried (`retries` times), and remembered, so
    that once the rescan verifies the room the issue it filed can be resolved by the robot.
    Everything else passes straight through. Never resolves on the retry alone — only on proof."""

    def __init__(self, inner, capture_id: str, commit_sha: str = "", retries: int = 1,
                 report: bool = False, frames=None, camera: str = "cam0", **report_tags):
        self.inner, self.capture_id, self.commit_sha = inner, capture_id, commit_sha
        self.retries, self.report, self.frames, self.camera = retries, report, frames, camera
        self.report_tags = report_tags
        self.healed: list[dict] = []           # {kind, object_id, detail, attempts}

    def __getattr__(self, name):               # drive / place / say / led: untouched
        return getattr(self.inner, name)

    def pick(self, object_id: str, pose) -> None:
        import obs
        for attempt in range(self.retries + 1):
            try:
                self.inner.pick(object_id, pose)
            except Exception as e:  # noqa: BLE001 -- only RobotError-shaped errors are retried
                code = getattr(e, "code", None)
                if code not in RETRYABLE or attempt == self.retries:
                    raise
                if self.report:                 # no hub running: file it ourselves, same tags
                    frame = self.frames.get(self.capture_id, self.camera) if self.frames else None
                    obs.robot_failure(code, getattr(e, "detail", "") or str(e), frame=frame,
                                      capture_id=self.capture_id, object_id=object_id,
                                      **({"commit_sha": self.commit_sha} if self.commit_sha else {}),
                                      **self.report_tags)
                log.warning("%s on %s — retrying (%d/%d)", code, object_id, attempt + 1, self.retries)
                last = (code, getattr(e, "detail", ""))
                continue
            if attempt:
                self.healed.append({"kind": last[0], "object_id": object_id, "detail": last[1],
                                    "attempts": attempt + 1})
            return

    def resolve_verified(self, issues: SentryIssues | None, verified: set[str], how: str = "rescan") -> list[str]:
        """After the rescan: for every healed op whose object is VERIFIED where the commit says,
        resolve the issue(s) it filed, with the retry on the timeline. Returns short ids."""
        import obs
        out: list[str] = []
        for h in [h for h in self.healed if h["object_id"] in verified]:
            note = (f"🤖 Resolved by the robot. {h['kind']} on {h['object_id']}; retried "
                    f"({h['attempts']} attempts) and the {how} came back clean"
                    + (f" at commit {self.commit_sha}" if self.commit_sha else "") + ".")
            resolution = {"resolved_by": "robot", "kind": h["kind"], "object_id": h["object_id"],
                          "attempts": h["attempts"], "verified_by": how, "capture_id": self.capture_id,
                          "commit_sha": self.commit_sha}
            with obs.capture_scope(self.capture_id, self.commit_sha):
                obs.context("resolution", resolution)   # on the heal transaction, not process-wide
                with obs.transaction("robot.heal", f"robot resolves {h['kind']} on {h['object_id']}"):
                    with obs.span("sentry.resolve", h["kind"], resolved_by="robot",
                                  object_id=h["object_id"], attempts=h["attempts"]) as sp:
                        ids = issues.resolve_failures(h["kind"], self.capture_id, note) if issues else []
                        if sp is not None:
                            sp.set_data("resolved_issues", ",".join(ids))
            log.info("resolved by robot: %s", ids or "(no matching unresolved issue)")
            out += ids
        return out


def room_is_clean(room_git: Path) -> bool | None:
    """`room status` without roomctl: is the room's working tree clean? None if unreadable.
    --no-optional-locks: never contend with roomctl's own git calls."""
    r = subprocess.run(["git", "--no-optional-locks", "-C", str(room_git), "status", "--porcelain"],
                       capture_output=True, text=True, timeout=10)
    return None if r.returncode else r.stdout.strip() == ""


def spoken(issue: dict) -> str:
    """'Sentry issue 12. My grasp slipped.' — short enough to say while the arm moves."""
    n = str(issue.get("shortId", "")).rsplit("-", 1)[-1]
    title = issue.get("title", "")
    m = re.match(r"robot: (\w+)", title)
    if m:
        what = SAID.get(m.group(1), m.group(1).replace("_", " "))
        peak = re.search(r"peak tilt_rate ([0-9.]+)", title)
        extra = f" Peak tilt rate {float(peak.group(1)):.1f}." if peak else ""
    elif title.lower().startswith("cron failure: room-clean"):
        what, extra = "The room failed its heartbeat", ""
    else:
        what, extra = f"New error: {title[:60]}", ""
    return f"Sentry issue {n}. {what}.{extra}"


class IssueMirror:
    """Sentry, made physical. Each tick: the LED shows the worst of (unresolved Sentry issues, the
    room's own clean/dirty); each issue that appeared since start is said once; and the room's
    cleanliness is the `room-clean` cron check-in — `ok` when clean, `error` when not, so a messy
    room fails its heartbeat (and a dead mirror misses it)."""

    def __init__(self, issues: SentryIssues, led: Callable[[str], None], say: Callable[[str], None],
                 room_git: Path, heartbeat: Callable[..., None] | None = None, beat_every: float = 30.0,
                 say_gap: float = 20.0):
        self.issues, self.led, self.say, self.room_git = issues, led, say, room_git
        self.heartbeat, self.beat_every, self.say_gap = heartbeat, beat_every, say_gap
        self.started = time.time()
        self.seen: set[str] = set()
        self.state: str | None = None
        self._beat_at = self._said_at = 0.0

    def tick(self) -> dict:
        open_ = self.issues.find("is:unresolved", period="24h")
        clean = room_is_clean(self.room_git)
        errors = [i for i in open_ if i.get("level") in ("error", "fatal")]
        state = "error" if errors else ("dirty" if open_ or clean is False else "clean")
        if state != self.state:                       # the LED is written on CHANGE only
            self.led(state)
            self.state = state
        if not self.seen and not self._said_at:       # first tick: what's already open is not news
            self.seen = {i["id"] for i in open_}
            self._said_at = 1e-9
        for i in open_:
            if i["id"] not in self.seen and time.monotonic() - self._said_at >= self.say_gap:
                self.say(spoken(i))
                self.seen.add(i["id"])
                self._said_at = time.monotonic()
        if self.heartbeat and clean is not None and time.monotonic() - self._beat_at >= self.beat_every:
            self.heartbeat("room-clean", "ok" if clean else "error", monitor_config=ROOM_MONITOR)
            self._beat_at = time.monotonic()
        return {"state": state, "unresolved": len(open_), "errors": len(errors), "room_clean": clean}


def _say_local(text: str) -> None:
    subprocess.run(["say", text], timeout=30, check=False)


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    sys.path.insert(0, str(ROOT))
    import obs
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mirror")
    m.add_argument("--once", action="store_true")
    m.add_argument("--interval", type=float, default=15.0)
    m.add_argument("--local-say", action="store_true", help="speak on this laptop, not the robot")
    d = sub.add_parser("demo", help="mock arm slips once -> the robot resolves the issue it filed")
    d.add_argument("--send", action="store_true",
                   help="really send it: files a SYNTHETIC issue (tagged synthetic=true) and resolves it")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s  %(message)s")
    obs.init("laptop")
    issues = SentryIssues.from_env()
    if issues is None:
        print("SENTRY_ORG_SLUG / SENTRY_AUTH_TOKEN missing")
        return 1
    if not issues.as_robot:
        log.warning("SENTRY_ROBOT_TOKEN unset: resolutions will show YOUR name as the actor, not the "
                    "robot's (Settings > Developer Settings > Custom Integrations > gitspace robot)")
    if a.cmd == "demo":
        if not a.send:     # synthetic data in Sentry is a deliberate act, never a default
            print("demo (dry): would file ONE synthetic grasp_slipped issue (fw=mock-arm, synthetic=true, "
                  "one 480 px demo frame), retry, verify, then resolve it as the robot. Add --send to do it.")
            return 0
        return _demo(issues)
    from roomctl.robot_client import HttpRobot
    robot = HttpRobot.from_env(None)                  # /led and /say are fire-and-forget
    mirror = IssueMirror(issues, robot.led, _say_local if a.local_say else robot.say,
                         Path(os.getenv("ROOM_GIT_PATH", str(ROOT / "room.git"))), heartbeat=obs.heartbeat)
    while True:
        try:
            print(mirror.tick(), flush=True)
        except Exception as e:  # noqa: BLE001 -- a Sentry hiccup must not stop the LED
            log.warning("tick failed: %s", e)
        if a.once:
            return 0
        time.sleep(a.interval)


def _demo(issues: SentryIssues) -> int:
    """The headline, end to end through the REAL executor with a mock arm that slips once:
    grasp_slipped (camera frame attached) -> retried -> verified -> resolved by the robot."""
    import cv2
    import numpy as np
    import obs
    from roomctl.executor import MockRobot, Op, Plan, RobotError, Spot, execute
    from roomctl.state import Extents, Pose
    from telemetry.frames import FrameCache

    cap = f"cap_heal_{int(time.time())}"
    frames = FrameCache()
    img = np.full((720, 1280, 3), 235, np.uint8)
    cv2.rectangle(img, (560, 330), (700, 500), (160, 90, 40), -1)
    cv2.drawMarker(img, (630, 415), (0, 0, 255), cv2.MARKER_CROSS, 60, 3)
    for k, line in enumerate(["SYNTHETIC DEMO FRAME", "mock arm - slips once, then holds", cap]):
        cv2.putText(img, line, (40, 70 + 55 * k), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (20, 20, 20), 3)
    frames.put(cap, "cam0", img)

    spot = lambda x: Spot("desk", Pose(x, 0.0, 0.75, 0), Extents(0.08, 0.08, 0.10))  # noqa: E731
    world = {"mug_a1b2": spot(0.30)}

    class SlipsOnce(MockRobot):
        slipped = False

        def pick(self, object_id, pose):
            if not SlipsOnce.slipped:
                SlipsOnce.slipped = True
                raise RobotError("grasp_slipped", "gripper closed to 2mm, expected 78mm (demo: mock arm)")
            super().pick(object_id, pose)

    robot = SelfHealingRobot(SlipsOnce(world=world), cap, commit_sha="a3f9c1", report=True,
                             frames=frames, fw="mock-arm", synthetic="true")
    out = execute(Plan(ops=[Op("move", "mug_a1b2", spot(0.30), spot(0.60))]), robot)
    print(f"executor: done={len(out.done)} failed={len(out.failed)} healed={robot.healed}")
    obs.flush(10)
    time.sleep(20)                                    # let Sentry ingest the issue before we look it up
    verified = {o for o, s in world.items() if abs(s.pose.x - 0.60) < 0.01}   # the "rescan" (mock world)
    ids = robot.resolve_verified(issues, verified, how="rescan (mock world)")
    obs.flush(10)
    print(f"verified={sorted(verified)} resolved_by_robot={ids}")
    return 0 if ids else 1


if __name__ == "__main__":
    sys.exit(main())
