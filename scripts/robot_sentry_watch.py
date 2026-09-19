#!/usr/bin/env python3
"""robot_sentry_watch.py — Sentry, watching the robot FROM OUTSIDE.

The robot's own process reports to Sentry (obs.init("robot"): unhandled exceptions, ERROR logs, every
request as a trace). That covers everything except the failures that matter most on demo day — the ones
a process cannot report about itself: it is dead, it never started, the wifi dropped, the robot was
switched off, Bracket Bot's camera daemon stopped publishing, the IMU feed went null. From the robot's
side those are silence. From here they are a condition with a start time.

    python scripts/robot_sentry_watch.py            # watch PI_HOST:PI_PORT from .env, forever
    python scripts/robot_sentry_watch.py --once     # one check, print it, exit 0 healthy / 1 not
    python scripts/robot_sentry_watch.py --dry-run  # watch, print what WOULD be filed, send nothing

What becomes a Sentry ISSUE (obs.robot_failure: tagged, with the last health snapshots as breadcrumbs
and — when we have one — the last picture the robot's camera sent, i.e. what it saw before it went dark):

    robot_unreachable      nothing answers at its address (wifi, power, or the link — docs/33)
    robot_server_down      the machine pings but :8080 is closed: robot.server crashed or never started
    camera_unavailable     robot.server is up but a camera is not (bbos's camera daemon, or its topic went stale)
    telemetry_unfed        tilt_rate is null in every sample: the IMU reader died. EVERY capture is rejected
    telemetry_stalled      the robot's event counter stopped advancing: the tap thread is wedged
    telemetry_starved      the 50 Hz tap is skipping ticks fast: the robot's CPU is starved (the balance loop's too)
    telemetry_source_errors  the telemetry source is raising
    stream_dropping        the robot is dropping events/frames for a slow client (us)
    clock_skew             robot and laptop clocks disagree by > 2 s: every mapped @timestamp is suspect
    robot_restarted        (warning) boot_id changed — a restart nobody may have noticed

Each fires ONCE when it has held for its debounce, and files `… recovered after N s` (info) when it clears —
an issue feed that says how long the robot was dark is a timeline, not a pile. No cron monitor is used: the
plan has one, and it belongs to the watch loop (obs.heartbeat).

Probes carry `sentry-trace: …-0` so watching the robot does not itself become 17k transactions a day on it.
State is written to ~/.cache/gitspace/robot-watch.json every check; the telemetry page reads it
(web/robot_view_api.py) so "is Sentry watching the robot" is a fact on the screen, not a belief.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import pi_link  # noqa: E402

STATE_FILE = Path(os.getenv("ROBOT_WATCH_STATE", "~/.cache/gitspace/robot-watch.json")).expanduser()
EVERY_S = 5.0
FRAME_EVERY_S = 30.0          # one preview frame per 30 s, kept to attach to the NEXT failure
SKEW_LIMIT_S = 2.0            # docs/23 §8: past this the hub stops trusting the Pi's wall clock

# condition -> (seconds it must hold before it is an issue, level)
RULES = {
    "robot_unreachable": (15, "error"), "robot_server_down": (15, "error"), "camera_unavailable": (10, "error"),
    "telemetry_unfed": (15, "error"), "telemetry_stalled": (15, "error"), "telemetry_starved": (20, "warning"),
    "telemetry_source_errors": (10, "error"), "stream_dropping": (20, "warning"), "clock_skew": (10, "warning"),
}


def unsampled() -> dict[str, str]:
    return {"sentry-trace": f"{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-0"}


def get(host: str, port: int, path: str, timeout: float = 4.0) -> tuple[int, dict, bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", path, headers=unsampled())
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        conn.close()


def sample_telemetry(host: str, port: int) -> dict:
    """One telemetry event + the hello, off /events. -> {"tilt": [...], "skew_s": float|None}"""
    conn = http.client.HTTPConnection(host, port, timeout=5)
    out: dict = {"tilt": None, "skew_s": None}
    try:
        conn.request("GET", "/events?types=hello,telemetry&limit=1", headers=unsampled())
        r = conn.getresponse()
        event = ""
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                doc = json.loads(line[5:])
                if event == "hello":
                    out["skew_s"] = round(doc["t_wall_base"] + (doc["t_mono_now"] - doc["t_mono_base"]) - time.time(), 3)
                elif event == "telemetry":
                    out["tilt"] = (doc.get("signals") or {}).get("tilt_rate")
                    break
    except (OSError, ValueError, KeyError, http.client.HTTPException):
        pass
    finally:
        conn.close()
    return out


def pings(host: str) -> bool:
    try:
        return subprocess.run(["ping", "-c", "1", "-W", "1000", host], capture_output=True, timeout=4).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class Watch:
    def __init__(self, dry: bool):
        self.dry = dry
        self.since: dict[str, float] = {}          # condition -> when it was first seen
        self.fired: dict[str, float] = {}          # condition -> when its issue was filed
        self.history: deque[dict] = deque(maxlen=40)
        self.prev: dict = {}
        self.frame: bytes | None = None
        self.frame_at = 0.0
        self.preview_error = ""
        self.boot_id = ""
        self.filed: list[dict] = []
        self.live = False
        if not dry:
            import obs
            self.obs = obs
            self.live = bool(obs.init("link"))

    # -- one look at the robot ----------------------------------------------------------------------
    def check(self) -> dict:
        env = pi_link.read_env()
        host, port = env.get("PI_HOST", ""), int(env.get("PI_PORT", "8080") or 8080)
        snap: dict = {"at": time.strftime("%H:%M:%S"), "t": time.time(), "robot": f"{host}:{port}",
                      "link": env.get("PI_LINK", "?"), "bad": {}}
        bad: dict[str, str] = snap["bad"]
        if not host:
            bad["robot_unreachable"] = "PI_HOST is not set in .env"
            return snap
        t0 = time.monotonic()
        try:
            status, _, body = get(host, port, "/healthz")
            hz = json.loads(body) if status == 200 else {}
        except (OSError, ValueError, http.client.HTTPException) as e:
            hz, why = {}, (getattr(e, "strerror", None) or str(e) or type(e).__name__)
            if pings(host):
                bad["robot_server_down"] = f"{host} answers ping but :{port} does not ({why}) — robot.server is not running"
            else:
                bad["robot_unreachable"] = f"nothing answers at {host} ({why}) — power, wifi, or the link (PI_LINK={snap['link']})"
            return snap
        snap["rtt_ms"] = round((time.monotonic() - t0) * 1000, 1)
        if "boot_id" not in hz:
            bad["robot_server_down"] = f":{port} answers but it is not robot.server (HTTP {status})"
            return snap
        tel, ev = hz.get("telemetry") or {}, hz.get("events") or {}
        last_n = int(str(ev.get("last_id", ":0")).rpartition(":")[2] or 0)
        snap.update(mode=hz.get("mode"), boot_id=hz["boot_id"], cameras=hz.get("cameras"), unavailable=hz.get("unavailable"),
                    overruns=tel.get("overruns"), source_errors=tel.get("source_errors"), dropped=tel.get("dropped"),
                    events_n=last_n, events_dropped=ev.get("dropped"), frames_dropped=hz.get("frames_dropped"),
                    preview=hz.get("preview"), last_capture=hz.get("last_capture"), fw=hz.get("fw"))
        if hz.get("unavailable") or not hz.get("cameras"):
            bad["camera_unavailable"] = f"cameras up: {hz.get('cameras')} · unavailable: {hz.get('unavailable')}"
        p, dt = self.prev, max(time.time() - self.prev.get("t", 0), 1e-6)
        if p.get("boot_id") == hz["boot_id"]:                      # rates only mean something within one boot
            if last_n <= p.get("events_n", -1):
                bad["telemetry_stalled"] = f"the robot's event log has not advanced past {last_n} in {dt:.0f} s"
            d_over = (tel.get("overruns") or 0) - (p.get("overruns") or 0)
            if d_over / dt > 5:                                    # 50 Hz tap: >10% of ticks skipped
                bad["telemetry_starved"] = f"{d_over} telemetry ticks skipped in {dt:.0f} s — the robot's CPU is starved"
            if (tel.get("source_errors") or 0) > (p.get("source_errors") or 0):
                bad["telemetry_source_errors"] = f"telemetry source raised {tel['source_errors'] - p['source_errors']}× in {dt:.0f} s"
            d_drop = ((ev.get("dropped") or 0) + (hz.get("frames_dropped") or 0)) - ((p.get("events_dropped") or 0) + (p.get("frames_dropped") or 0))
            if d_drop > 0:
                bad["stream_dropping"] = f"the robot dropped {d_drop} events/frames for a slow client in {dt:.0f} s"
        s = sample_telemetry(host, port)
        snap["skew_s"] = s["skew_s"]
        if s["tilt"] is not None:
            snap["tilt_rate"] = next((v for v in reversed(s["tilt"]) if v is not None), None)
            if s["tilt"] and all(v is None for v in s["tilt"]):
                bad["telemetry_unfed"] = ("tilt_rate is null in every sample — the IMU reader (ROBOT_TELEMETRY_SOURCE) is not "
                                          "feeding the robot; the quality gate rejects every capture until it does")
        if s["skew_s"] is not None and abs(s["skew_s"]) > SKEW_LIMIT_S:
            bad["clock_skew"] = f"robot clock is {s['skew_s']:+.1f} s from the laptop's"
        if time.monotonic() - self.frame_at > FRAME_EVERY_S and hz.get("cameras"):
            camera = hz["cameras"][0]
            try:
                st, headers, jpeg = get(host, port, f"/camera/{camera}.jpg")
                age = next((v for k, v in headers.items() if k.lower() == "x-frame-age-ms"), None)
                stale = age is not None and float(age) > 1000
                if st == 200 and jpeg[:2] == b"\xff\xd8" and not stale:
                    self.frame, self.frame_at = jpeg, time.monotonic()
                    self.preview_error = ""
                else:
                    self.preview_error = f"{camera}: preview unavailable (HTTP {st}" + (
                        f", frame age {age} ms)" if stale else ", no valid JPEG)"
                    )
            except (OSError, ValueError, http.client.HTTPException) as e:
                self.preview_error = f"{camera}: preview read failed ({type(e).__name__})"
        if self.preview_error:
            bad["camera_unavailable"] = "; ".join(filter(None, [bad.get("camera_unavailable"), self.preview_error]))
        return snap

    # -- conditions -> issues -----------------------------------------------------------------------
    def file(self, kind: str, detail: str, level: str, snap: dict, **extra) -> None:
        rec = {"kind": kind, "detail": detail, "level": level, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        self.filed.append(rec)
        self.filed = self.filed[-12:]
        print(f"  [{'DRY' if self.dry else 'SENTRY'}] {level.upper():7s} {kind}: {detail}", flush=True)
        if self.dry or not self.live:
            return
        tags = {"link": snap.get("link"), "robot": snap.get("robot"), "boot_id": snap.get("boot_id") or self.boot_id,
                "robot_mode": snap.get("mode"), "watcher": "robot_sentry_watch", **extra}
        crumbs = [{k: v for k, v in h.items() if k not in ("t", "bad") and v is not None} | {"bad": ",".join(h["bad"]) or "ok"}
                  for h in self.history]
        if level == "error":
            self.obs.robot_failure(kind, detail, telemetry=crumbs, frame=self.frame, **{k: v for k, v in tags.items() if v})
        else:
            import sentry_sdk
            with sentry_sdk.new_scope() as scope:
                for k, v in tags.items():
                    if v:
                        scope.set_tag(k, str(v))
                scope.set_tag("failure_kind", kind)
                for c in crumbs[-20:]:
                    scope.add_breadcrumb(category="robot-health", level="info", message=kind, data=c)
                sentry_sdk.capture_message(f"robot: {kind} — {detail}", level=level)

    def step(self) -> dict:
        snap = self.check()
        self.history.append(snap)
        now = time.time()
        if snap.get("boot_id"):
            if self.boot_id and snap["boot_id"] != self.boot_id:
                self.file("robot_restarted", f"boot_id {self.boot_id} -> {snap['boot_id']}: robot.server restarted", "warning", snap)
            self.boot_id = snap["boot_id"]
        for kind, detail in snap["bad"].items():
            self.since.setdefault(kind, now)
            hold, level = RULES[kind]
            if kind not in self.fired and now - self.since[kind] >= hold:
                self.fired[kind] = now
                self.file(kind, detail, level, snap)
        for kind in [k for k in self.since if k not in snap["bad"]]:
            if kind in self.fired:
                self.file(f"{kind}_recovered", f"{kind} cleared after {now - self.since[kind]:.0f} s", "info", snap, recovered=kind)
                del self.fired[kind]
            del self.since[kind]
        if snap.get("boot_id"):
            self.prev = snap
        state = {"watching": True, "sentry_live": self.live, "dry_run": self.dry, "checked_at": now, "every_s": EVERY_S,
                 "robot": snap.get("robot"), "healthy": not snap["bad"], "pending": {k: round(now - t) for k, t in self.since.items() if k not in self.fired},
                 "open": {k: snap["bad"].get(k, "") for k in self.fired}, "filed": self.filed[-6:], "pid": os.getpid(),
                 "snapshot": {k: v for k, v in snap.items() if k not in ("t", "bad")}}
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(state))
            tmp.replace(STATE_FILE)
        except OSError:
            pass
        return state


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print what would be filed; send nothing to Sentry")
    a = ap.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    socket.setdefaulttimeout(6)
    w = Watch(dry=a.dry_run or a.once)
    print(f"  robot watch · every {EVERY_S:g} s · Sentry {'LIVE' if w.live else ('dry run' if w.dry else 'NOT CONFIGURED (no SENTRY_DSN)')} · state -> {STATE_FILE}", flush=True)
    if a.once:
        st = w.step()
        print(json.dumps({"healthy": st["healthy"], "snapshot": st["snapshot"], "bad": w.history[-1]["bad"]}, indent=2))
        return 0 if st["healthy"] else 1
    last_line = ""
    try:
        while True:
            t0 = time.monotonic()
            st = w.step()
            s = st["snapshot"]
            line = (f"ok · {s.get('mode')} · rtt {s.get('rtt_ms')} ms · events {s.get('events_n')} · tilt {s.get('tilt_rate')}"
                    if st["healthy"] else "NOT OK · " + " · ".join(list(st["open"]) + [f"{k} ({v}s)" for k, v in st["pending"].items()]))
            if line.split(" · ")[0:2] != last_line.split(" · ")[0:2]:      # print on change of state, not every 5 s
                print(f"  {s.get('at')}  {line}", flush=True)
            last_line = line
            time.sleep(max(0.5, EVERY_S - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        try:
            STATE_FILE.write_text(json.dumps({"watching": False, "checked_at": time.time(), "stopped": True}))
        except OSError:
            pass
        if w.live:
            w.obs.flush(3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
