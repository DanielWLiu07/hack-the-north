#!/usr/bin/env python3
"""scripts/check_dispatch.py — is the chain that carries a job to the robot ALIVE, hop by hop.

    ./scripts/check_dispatch.py            exit 0 when every hop answers, 1 when one does not

WHY THIS EXISTS. On 2026-09-20 nothing was running `robot/adapter.py`, so :8765 refused every
connection. The web tier was healthy. Andrew's edge was healthy. Every job died one hop short, for six
hours and thirty-four commands, and the only place it showed was a Sentry issue nobody was reading
(GITSPACE-1E, "robot API failed; delivery status may be unknown"). A judge pressing "point at the mug"
would have hit it. The pre-flight checked the site and the simulator and never checked the chain.

IT MOVES NOTHING. Every request here is a GET of a health endpoint; a pre-flight that dispatches a
motion is not a pre-flight. That means it proves each hop is ALIVE, not that a job flows end to end —
for that, press the button and watch the adapter log `POST /v1/actions`.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
from dotenv import load_dotenv                                          # noqa: E402

load_dotenv(ROOT / ".env")

GREEN, RED, AMBER, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
TIMEOUT = 4.0


def get(url: str) -> tuple[int, dict | str | None]:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            raw = r.read().decode(errors="replace")
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw[:200]
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return 0, str(getattr(e, "reason", e))[:120]


def say(ok: bool | None, label: str, detail: str) -> None:
    mark = f"{GREEN}  ok  {OFF}" if ok else (f"{AMBER} warn {OFF}" if ok is None else f"{RED} DOWN {OFF}")
    print(f"{mark} {label:<34} {detail}")


def main() -> int:
    web = os.getenv("WEB_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    edge = (os.getenv("HOUSEBOT_EDGE_URL") or "").rstrip("/")
    robot = (os.getenv("HOUSEBOT_ROBOT_BASE_URL") or "http://127.0.0.1:8765").rstrip("/")
    bad = 0

    print("the chain a job travels, nearest to furthest:\n")

    code, body = get(f"{web}/api/health")
    say(code == 200, f"web tier {web}", "healthy" if code == 200 else f"no answer ({body})")
    bad += code != 200

    code, disp = get(f"{web}/api/housebot")
    if code == 200 and isinstance(disp, dict):
        on, points_at = disp.get("enabled"), disp.get("edge")
        kinds = ", ".join(k for k, v in (disp.get("kinds") or {}).items() if v) or "nothing"
        say(bool(on), "dispatcher", f"{'on' if on else 'OFF: ' + str(disp.get('why_off'))}"
                                    f" · points at {points_at} · may send: {kinds}"
                                    f" · token {'set' if disp.get('token') else 'MISSING'}")
        bad += not on
        if on and points_at and edge and not edge.startswith(str(points_at)):
            say(None, "dispatcher vs .env", f"web says {points_at}, .env says {edge}")
    else:
        say(False, "dispatcher", f"/api/housebot did not answer ({code})")
        bad += 1

    if edge:
        code, body = get(f"{edge}/health")
        ok = code == 200
        say(ok, f"housebot edge {edge}", "healthy" if ok else f"no answer ({body})")
        bad += not ok
    else:
        say(False, "housebot edge", "HOUSEBOT_EDGE_URL is not set")
        bad += 1

    code, body = get(f"{robot}/health")
    if code == 200 and isinstance(body, dict):
        sim = body.get("simulated")
        say(True, f"robot adapter {robot}",
            ("SIMULATED — nothing physical moves" if sim else "HARDWARE — this drives the real robot"))
        if not sim:
            print(f"       {AMBER}the adapter is in hardware mode: a dispatched job moves a real machine.{OFF}")
    else:
        say(False, f"robot adapter {robot}", f"no answer ({body}) — THIS is the hop that broke on 2026-09-20")
        print(f"       start it: tmux new-window -d -t htn -n sim-adapter "
              f"\"cd {ROOT} && SENTRY_DSN= .venv/bin/python -m robot.adapter --sim --port 8765; exec zsh\"")
        bad += 1

    print()
    if bad:
        print(f"{RED}{bad} hop(s) down: a job would not reach the robot.{OFF} Nothing was sent to find out.")
        return 1
    print(f"{GREEN}every hop answers.{OFF} This says each one is ALIVE, not that a job flows: press the "
          f"button and watch the adapter log POST /v1/actions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
