"""telemetry/room_clean.py: the room-clean badge sends nothing unless ROOM_CLEAN_CRON=1, sends a flip at
once and otherwise every 30 s, and always leaves its verdict for the dashboard. No Sentry: a fake heartbeat."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from telemetry import room_clean
from telemetry.room_clean import RoomCleanBeat


class Clock:
    def __init__(self):
        self.t = 1_000.0

    def __call__(self):
        return self.t


@pytest.fixture()
def beat(tmp_path, monkeypatch):
    monkeypatch.setenv("ROOM_CLEAN_STATE", str(tmp_path / "room-clean.json"))
    monkeypatch.setenv("ROOM_CLEAN_CRON", "1")
    sent, clock = [], Clock()
    b = RoomCleanBeat(lambda slug, status, monitor_config=None: sent.append((slug, status, monitor_config)),
                      clock=clock, mono=clock)
    b.sent, b.t = sent, clock
    return b


def test_off_by_default_but_the_verdict_is_still_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("ROOM_CLEAN_STATE", str(tmp_path / "s.json"))
    monkeypatch.delenv("ROOM_CLEAN_CRON", raising=False)
    sent = []
    doc = RoomCleanBeat(lambda *a, **k: sent.append(a))(False, source="test")
    assert sent == [], "the cron seat is watch-loop's until the user frees it"
    assert (doc["enabled"], doc["last"], doc["at"], doc["sent"]) == (False, "error", None, False)
    assert "ROOM_CLEAN_CRON" in doc["why_off"] and room_clean.read_state()["last"] == "error"


def test_a_flip_goes_at_once_and_a_steady_state_every_30_s(beat):
    beat(True)
    assert beat.sent[-1][:2] == ("room-clean", "ok") and beat.sent[-1][2]["schedule"]["value"] == 1
    beat.t.t += 5
    beat(True)
    assert len(beat.sent) == 1, "steady, and not due yet"
    beat.t.t += 1
    beat(False)
    assert beat.sent[-1][1] == "error" and len(beat.sent) == 2, "a mess is reported at once, not in 30 s"
    beat.t.t += 30
    beat(False)
    assert len(beat.sent) == 3, "still messy: re-sent on the interval, so a dead feeder is noticed"
    doc = beat(True)
    assert doc["last"] == "ok" and doc["since"] == room_clean._iso(beat.t.t) and doc["sent"] is True


def test_since_survives_a_restart(beat, monkeypatch):
    beat(False)
    first = room_clean.read_state()["since"]
    beat.t.t += 100
    again = RoomCleanBeat(lambda *a, **k: None, clock=beat.t, mono=beat.t)
    assert again(False)["since"] == first, "the mess didn't start when the process did"


@dataclass
class RoomState:                       # the shape roomctl/watch.py hands over (03-interfaces §6)
    clean: bool
    confirmed: list = field(default_factory=list)
    pending: list = field(default_factory=list)


def test_red_on_the_pass_that_confirms_the_mess_green_after_the_tidy(beat):
    beat(RoomState(clean=True))
    beat.t.t += 1
    beat(RoomState(clean=True, pending=[{"object_id": "mug_a1b2"}]))            # pass 1: seen once
    assert [s[1] for s in beat.sent] == ["ok"]
    beat.t.t += 1
    doc = beat(RoomState(clean=False, confirmed=[{"object_id": "mug_a1b2"}]))    # pass 2: confirmed
    assert [s[1] for s in beat.sent] == ["ok", "error"] and doc["confirmed"] == 1
    beat.t.t += 1
    beat(RoomState(clean=True))                                                  # tidied
    assert [s[1] for s in beat.sent] == ["ok", "error", "ok"]


def test_a_failed_check_in_is_just_missed(tmp_path, monkeypatch):
    monkeypatch.setenv("ROOM_CLEAN_STATE", str(tmp_path / "s.json"))
    monkeypatch.setenv("ROOM_CLEAN_CRON", "1")

    def boom(*a, **k):
        raise RuntimeError("sentry down")
    doc = RoomCleanBeat(boom)(True)
    assert doc["sent"] is False and doc["at"] is None and doc["last"] == "ok"
