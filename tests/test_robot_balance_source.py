"""robot/balance_source.py + robot/check_source.py — the seam Sarah and Ryan plug the balance loop into.

The failure worth a test file: sample-and-hold over a balance loop that DIED returns its last
tilt_rate forever, a constant, which reads as a perfectly still robot — and the capture gate would
pass every capture on the evidence of a dead sensor. State must expire. Loopback UDP only.
"""
import json
import math
import random
import socket
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from robot import balance_source as bs  # noqa: E402
from robot import check_source as cs  # noqa: E402
from robot.telemetry import Telemetry  # noqa: E402

GOOD = {"pitch": 0.01, "tilt_rate": 0.01, "left_enc": 1.0, "right_enc": 1.0, "motor_current_l": 0.4,
        "motor_current_r": 0.4, "odom_residual": 0.004, "balanced": 1.0}


def rows(n=250, **over):
    rng = random.Random(0)
    out = []
    for _ in range(n):
        r = {**GOOD, "pitch": rng.gauss(0, 0.003), "tilt_rate": rng.gauss(0, 0.008),
             "motor_current_l": 0.4 + rng.gauss(0, 0.02), "motor_current_r": 0.4 + rng.gauss(0, 0.02)}
        r.update({k: (v(rng) if callable(v) else v) for k, v in over.items()})
        out.append({k: v for k, v in r.items() if v is not None})
    return out


FAST = [0.05] * 250


# ── Held: state that stops arriving expires ──────────────────────────────────────
def test_held_returns_the_latest_state_and_then_lets_it_expire():
    h = bs.Held(stale_s=0.05)
    assert h.read() == {}                                   # nothing yet is not "still"
    h.put(GOOD)
    assert h.read() == GOOD
    time.sleep(0.08)
    assert h.read() == {}                                   # the loop went quiet: no evidence, not old evidence


def test_a_dead_balance_loop_leaves_the_gate_with_no_evidence():
    """Through the real tap: while state arrives tel.peak() has a number; once it stops, None —
    which obs.capture_quality FAILS. Never the last value, held."""
    h = bs.Held(stale_s=0.05)
    tel = Telemetry(source=h.read).start()
    try:
        t_end = time.monotonic() + 0.4
        while time.monotonic() < t_end:
            h.put(GOOD)
            time.sleep(0.01)
        alive_at = time.monotonic() - 0.15
        assert tel.peak("tilt_rate", alive_at - 0.1, alive_at + 0.1) == pytest.approx(0.01)
        time.sleep(0.45)                                    # the balance loop has crashed
        dead_at = time.monotonic() - 0.15
        assert tel.peak("tilt_rate", dead_at - 0.1, dead_at + 0.1) is None
    finally:
        tel.stop()


def test_the_udp_listener_feeds_held_and_shrugs_off_a_bad_datagram():
    h = bs.Held()
    sock = bs.listen(0, h)                                  # port 0: any free loopback port
    try:
        port = sock.getsockname()[1]
        assert sock.getsockname()[0] == "127.0.0.1"         # the loop's state is not an API
        out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for payload in (b"{not json", b"[1,2]", json.dumps(GOOD).encode()):
            out.sendto(payload, ("127.0.0.1", port))
        deadline = time.monotonic() + 1.0
        while not h.received and time.monotonic() < deadline:
            time.sleep(0.005)
        assert h.read() == GOOD and h.received == 1
    finally:
        sock.close()


# ── check_source: the verdicts ───────────────────────────────────────────────────
def test_a_healthy_source_is_ok_and_says_how_often_the_gate_would_pass():
    fail, warn, nums = cs.check(rows(), FAST)
    assert fail == [] and warn == [] and nums["gate_windows_passing"] > 0.9


def test_nothing_arriving_is_one_failure_not_nine():
    fail, warn, _ = cs.check([{}] * 250, FAST)
    assert len(fail) == 1 and "not sending" in fail[0] and warn == []


def test_a_frozen_tilt_rate_fails_because_it_would_pass_every_capture():
    fail, _, _ = cs.check(rows(tilt_rate=0.0), FAST)
    assert any("never changes" in f and "PASS every capture" in f for f in fail)


def test_degrees_per_second_is_caught():
    fail, _, _ = cs.check(rows(tilt_rate=lambda r: math.degrees(r.gauss(0, 0.05))), FAST)
    assert any("deg/s, not rad/s" in f for f in fail)


def test_a_rate_differentiated_from_raw_pitch_is_caught():
    """robot/telemetry.py's FakeRobot, in one line: 3 mrad of pitch noise / 20 ms = 0.2 rad/s."""
    fail, _, nums = cs.check(rows(tilt_rate=lambda r: r.gauss(0, 0.003) * math.sqrt(2) / 0.02), FAST)
    assert nums["gate_windows_passing"] == 0.0 and any("every capture would be rejected" in f for f in fail)


def test_missing_gate_and_arm_signals_fail_but_a_missing_chart_signal_only_warns():
    fail, warn, _ = cs.check(rows(balanced=None, odom_residual=None), FAST)
    assert any("`balanced`" in f and "not_balanced" in f for f in fail)
    assert any("`odom_residual`" in w for w in warn) and not any("odom_residual" in f for f in fail)
    fail, _, _ = cs.check(rows(tilt_rate=None), FAST)
    assert any("`tilt_rate`" in f and "REJECTS" in f for f in fail)


def test_a_source_that_does_io_in_the_tick_fails():
    fail, _, _ = cs.check(rows(), [0.05] * 240 + [12.0] * 10)
    assert any("doing I/O" in f for f in fail)


def test_wrong_names_and_a_non_binary_balanced_are_reported():
    fail, warn, _ = cs.check(rows(balanced=0.7, tiltRate=0.01), FAST)
    assert any("must be 0 or 1" in f for f in fail) and any("tiltRate" in w for w in warn)


def test_idle_motors_reading_exactly_zero_only_warn():
    """Found on the real robot: bbos publishes iq = 0.0 with the motors idle. Only a frozen
    tilt_rate can pass a capture it should not; nothing gates on current."""
    fail, warn, _ = cs.check(rows(motor_current_l=0.0, motor_current_r=0.0), FAST)
    assert fail == [] and sum("never changes" in w for w in warn) == 2
