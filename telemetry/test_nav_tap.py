"""telemetry/hub.py NavTap: Bracket Bot's SLAM pose as telemetry signals, in the room frame only when a
registration for the robot's CURRENT map exists. A fake nav; nothing is contacted."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from roomctl import frames
from roomctl.bb_nav import NavState
from telemetry.hub import ElasticSink, NavTap

T = frames.SE2(0.6, 1.2, -0.4, 0.0)


def state(x, y, h, gen=1, ready=True, t=None):
    s = NavState(ready=ready, x=x, y=y, h=h, map_gen=gen)
    s.t = t if t is not None else time.time()
    return s


def test_without_a_registration_it_is_bbs_own_frame_and_says_so():
    nav, out = SimpleNamespace(state=None), []
    tap = NavTap(nav, out.append, flush_s=0)
    assert tap.poll() is None, "no state yet: nothing"
    nav.state = state(1.0, 2.0, 0.5)
    b = tap.poll()
    assert set(b.signals) == {"nav_bb_x", "nav_bb_y", "nav_bb_h", "nav_ready", "nav_map_gen"}
    assert (b.signals["nav_bb_x"], b.signals["nav_ready"]) == ((1.0,), (1.0,)) and b.t_wall == (nav.state.t,)


def test_with_a_registration_it_is_the_room_frame_through_frames():
    nav, out = SimpleNamespace(state=state(1.0, 2.0, 0.5, gen=3)), []
    b = NavTap(nav, out.append, registration=lambda: (T, 3), flush_s=0).poll()
    x, y, _ = frames.bb_to_room((1.0, 2.0, 0.0), T)
    assert b.signals["nav_x"] == (round(x, 4),) and b.signals["nav_y"] == (round(y, 4),)
    assert b.signals["nav_yaw"] == (round(frames.bb_yaw_to_heading_room(0.5, T), 2),)
    assert "nav_bb_x" not in b.signals


def test_a_registration_for_an_old_map_is_not_used():
    nav, out = SimpleNamespace(state=state(1.0, 2.0, 0.5, gen=4)), []
    b = NavTap(nav, out.append, registration=lambda: (T, 3), flush_s=0).poll()
    assert "nav_x" not in b.signals and b.signals["nav_map_gen"] == (4.0,), "map_gen moved on: BB frame, never a wrong room pose"


def test_only_new_states_are_sampled_and_batches_go_to_every_sink_as_signal_docs():
    t0 = time.time()
    nav, out = SimpleNamespace(state=state(1.0, 2.0, 0.5, t=t0)), []
    tap = NavTap(nav, out.append, flush_s=3600)
    tap.poll()
    tap.poll()                                            # the same state again: not a new sample
    nav.state = state(1.1, 2.0, 0.5, t=t0 + 0.125)
    tap.poll()
    b = tap.flush()
    assert tap.samples == 2 and len(b.t_wall) == 2 and out == [b]
    docs = ElasticSink.docs(b)
    assert {d["signal"] for d in docs} >= {"nav_bb_x", "nav_ready"} and all(d["value"] is not None for d in docs)
    assert sorted({d["@timestamp"] for d in docs}) == [round(t0 * 1000), round((t0 + 0.125) * 1000)]
