#!/usr/bin/env python3
"""robot/check_source.py — is this balance-state source good enough to gate captures on?

    python -m robot.check_source robot.balance_source:udp            # the real loop must be running
    python -m robot.check_source robot.balance_source:udp --fake-sender   # prove the plumbing first
    python -m robot.check_source mymodule:read_state --seconds 10

Calls the source exactly as the telemetry tap does — 50 Hz, from a thread that must never wait —
and answers the questions that otherwise surface as "every capture is rejected" (or, worse, as
captures that pass when they should not). Exit 0 = wire it in. Runbook: robot/RUNBOOK.md.
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import socket
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robot import config as C  # noqa: E402
from robot.balance_source import SIGNALS, UDP_PORT  # noqa: E402

HZ = 50
GATE_NEEDS = ("tilt_rate",)               # without it the capture gate rejects everything
ARM_NEEDS = ("balanced",)                 # without it /arm answers not_balanced forever
MAX_CALL_MS = 5.0                         # p99. The tap has 20 ms per tick and shares the Pi with the balance loop


def load(spec: str):
    mod, _, name = spec.partition(":")
    if not name:
        raise SystemExit(f"{spec!r}: want module:callable, e.g. robot.balance_source:udp")
    return getattr(importlib.import_module(mod), name)


def sample(source, seconds: float) -> tuple[list[dict], list[float], int]:
    rows, call_ms, errors = [], [], 0
    t0 = time.monotonic()
    for k in range(int(seconds * HZ)):
        wait = t0 + k / HZ - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        t = time.perf_counter()
        try:
            s = source()
            rows.append(s if isinstance(s, dict) else {})
        except Exception as e:  # noqa: BLE001 -- the tap survives this; count it as the tap would
            errors += 1
            rows.append({})
            last = f"{type(e).__name__}: {e}"
        call_ms.append((time.perf_counter() - t) * 1000)
    if errors:
        print(f"  source() raised {errors}x, last: {last}")
    return rows, call_ms, errors


def column(rows: list[dict], name: str) -> list[float]:
    out = []
    for r in rows:
        try:
            v = float(r.get(name, math.nan))
        except (TypeError, ValueError):
            v = math.nan
        out.append(v)
    return out


def check(rows: list[dict], call_ms: list[float], errors: int = 0) -> tuple[list[str], list[str], dict]:
    """-> (failures, warnings, numbers). Failures mean: do not gate captures on this yet."""
    fail, warn, n = [], [], len(rows)
    nums: dict = {"samples": n}
    p99 = sorted(call_ms)[max(0, int(len(call_ms) * 0.99) - 1)] if call_ms else 0.0
    nums["call_ms_p99"] = round(p99, 3)
    if errors:
        fail.append(f"source() raised on {errors}/{n} calls — the tap records NaN for each")
    if p99 > MAX_CALL_MS:
        fail.append(f"source() takes {p99:.1f} ms at p99 (limit {MAX_CALL_MS}): it is doing I/O. It must return the "
                    "LATEST state instantly; receive in a background thread (balance_source.Held)")
    empty = sum(1 for r in rows if not r) / max(n, 1)
    nums["empty_share"] = round(empty, 3)
    if empty > 0.05:
        fail.append(f"{empty:.0%} of reads returned nothing: the balance loop is not sending, is slower than "
                    f"{1 / C.LATCH_WINDOW_S:.0f} Hz, or stalls (Held expires state after 100 ms — on purpose)")
    extra = sorted({k for r in rows for k in r} - set(SIGNALS))
    if extra:
        warn.append(f"unknown keys are ignored: {extra}. The eight names are {list(SIGNALS)}")
    for name in SIGNALS if empty <= 0.95 else ():      # nothing arrived at all: one failure says it
        col = column(rows, name)
        good = [v for v in col if v == v]
        share = len(good) / max(n, 1)
        if share < 0.95:
            msg = f"`{name}` present in only {share:.0%} of samples"
            if name in GATE_NEEDS:
                fail.append(msg + " — the capture gate reads this; missing evidence REJECTS the capture")
            elif name in ARM_NEEDS:
                fail.append(msg + " — /arm is refused (`not_balanced`) whenever this is missing")
            else:
                warn.append(msg + " (charted only; nothing gates on it)")
            continue
        if len(set(good)) == 1 and name == "tilt_rate":
            fail.append(f"`tilt_rate` never changes ({good[0]!r}) over {n / HZ:.0f} s: a frozen value reads as a perfectly "
                        "still robot and would PASS every capture. A live gyro always has noise")
        elif len(set(good)) == 1 and name in ("pitch", "motor_current_l", "motor_current_r"):
            # found on the real robot: idle motors publish iq = 0.0 exactly. Nothing gates on these.
            warn.append(f"`{name}` never changes ({good[0]!r}) over {n / HZ:.0f} s — fine for idle motors; "
                        "if the robot was moving, that field is not wired")
    tilt = [abs(v) for v in column(rows, "tilt_rate") if v == v]
    pitch = [abs(v) for v in column(rows, "pitch") if v == v]
    if tilt:
        w = int(2 * C.LATCH_WINDOW_S * HZ) + 1                # the gate's window: +-100 ms = 11 samples
        peaks = [max(tilt[i:i + w]) for i in range(0, max(1, len(tilt) - w + 1))]
        ok = sum(p < C.MAX_TILT_RATE for p in peaks) / len(peaks)
        nums.update(tilt_rate_median=round(statistics.median(tilt), 4), tilt_rate_max=round(max(tilt), 4),
                    gate_windows_passing=round(ok, 3))
        if statistics.median(tilt) > 1.0:
            fail.append(f"median |tilt_rate| is {statistics.median(tilt):.2f}: that is deg/s, not rad/s "
                        "(multiply by pi/180). The gate's limit is 0.05 rad/s = 2.9 deg/s")
        elif ok == 0.0:
            fail.append(f"no +-100 ms window has peak |tilt_rate| under {C.MAX_TILT_RATE} rad/s (median peak "
                        f"{statistics.median(peaks):.3f}): every capture would be rejected. If the robot was standing "
                        "still, the signal is too noisy — differentiate a FILTERED pitch, or use the gyro rate "
                        "directly, never the difference of raw pitch samples")
        elif ok < 0.2:
            warn.append(f"only {ok:.0%} of windows would pass the gate: captures will retry a lot. Settle the robot, "
                        "or look at the noise on tilt_rate")
    if pitch and statistics.median(pitch) > 0.6 and statistics.median(column(rows, "balanced") or [0]) >= 0.5:
        warn.append(f"median |pitch| is {statistics.median(pitch):.2f} while balanced=1: degrees, not radians?")
    bal = [v for v in column(rows, "balanced") if v == v]
    if bal and not set(bal) <= {0.0, 1.0}:
        fail.append(f"`balanced` must be 0 or 1, saw {sorted(set(bal))[:4]}")
    return fail, warn, nums


def fake_sender(port: int, stop: threading.Event) -> None:
    """robot/sim.py's settled robot, sent the way the real loop should send: one datagram a tick."""
    from robot import sim
    balance, sock = sim.SimBalance(), socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    while not stop.wait(1 / HZ):
        sock.sendto(json.dumps(balance()).encode(), ("127.0.0.1", port))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="module:callable — what ROBOT_TELEMETRY_SOURCE will be set to")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--fake-sender", action="store_true",
                    help=f"also send a SIMULATED robot to udp://127.0.0.1:{UDP_PORT}, to test the receiving half alone")
    a = ap.parse_args()
    source, stop = load(a.source), threading.Event()
    if a.fake_sender:
        source()                                              # bind the listener before anything is sent
        threading.Thread(target=fake_sender, args=(UDP_PORT, stop), daemon=True).start()
        print("  --fake-sender: the numbers below are a SIMULATED robot, not yours")
        time.sleep(0.2)
    print(f"  sampling {a.source} at {HZ} Hz for {a.seconds:g} s ...")
    rows, call_ms, errors = sample(source, a.seconds)
    stop.set()
    fail, warn, nums = check(rows, call_ms, errors)
    print("  " + json.dumps(nums))
    for w in warn:
        print(f"  warn  {w}")
    for f in fail:
        print(f"  FAIL  {f}")
    print(f"  {'NOT READY' if fail else 'OK'} — {len(fail)} failure(s), {len(warn)} warning(s)"
          + ("" if fail else f". Set ROBOT_TELEMETRY_SOURCE={a.source}"))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
