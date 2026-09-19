"""Dry run of the documents the caretaker plan adds (plan/roommate/03-interfaces.md §10, "no mapping
changes") against isolated test-shapes- copies of the real templates. Live; deleted afterwards.

§10 holds only while producers send the listed fields: the last test pins what happens otherwise.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

import ingest
import setup_elastic as S
from queries import Queries

PREFIX = "test-shapes-"
STREAMS = ("room-events", "room-observations", "robot-telemetry")
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ms(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


@pytest.fixture(scope="module")
def shapes(es):
    def teardown():
        for name in STREAMS:
            es.options(ignore_status=404).indices.delete_data_stream(name=PREFIX + name)
            es.options(ignore_status=404).indices.delete_index_template(name=PREFIX + name)
    teardown()
    specs = S.load_specs()
    for name in STREAMS:
        body = copy.deepcopy(specs[name])
        body["index_patterns"] = [f"{PREFIX}{name}*"]
        S.ensure_data_stream(es, PREFIX + name, body, recreate=True)
    yield es
    teardown()


def write(es, index: str, docs: list[dict]) -> dict:
    acts = []
    for d in docs:
        a = ingest.action(index, d)
        a["_index"] = PREFIX + index
        acts.append(a)
    return ingest.write(es, acts)[PREFIX + index]


def event(kind: str, minutes: int, **extra) -> dict:
    return {"@timestamp": ms(NOW - timedelta(minutes=minutes)), "event_type": kind, "branch": "main",
            "objects_affected": ["mug_a1b2"], "zone": ["desk"], "message": f"{kind}: the mug",
            "author": "caretaker", "outcome": "ok", **extra}


def test_new_room_event_types(shapes):
    commit = event("commit", 50, commit_sha="d" * 40, objects_moved=["mug_a1b2"])
    new = [event(k, m) for k, m in (("chore_opened", 40), ("chore_closed", 30), ("pr_opened", 20),
                                    ("pr_merged", 10), ("tidy", 5))]
    ok, dup, errors = write(shapes, "room-events", [commit, *new])
    assert (ok, errors) == (6, [])
    # none of them is a commit: time travel must still answer with the commit
    q = Queries(shapes, prefix=PREFIX)
    assert q.commit_at(NOW, branch="main")["commit_sha"] == "d" * 40


def test_bb_map_observations(shapes):
    obs = [{"@timestamp": ms(NOW - timedelta(minutes=3, milliseconds=i)), "capture_id": "cap_bb_0001",
            "object_id": oid, "camera": "bb_map", "raw_x": 0.42, "raw_y": 0.18, "raw_z": 0.76,
            "occluded": occ, "confidence": 0.8, "point_count": 900}
           for i, (oid, occ) in enumerate((("mug_a1b2", False), ("keys_7c2e", True), (None, False)))]
    ok, dup, errors = write(shapes, "room-observations", obs)
    assert (ok, errors) == (3, [])


def test_nav_telemetry(shapes):
    t0 = NOW - timedelta(minutes=2)
    docs = [{"@timestamp": ms(t0 + timedelta(milliseconds=20 * k)), "signal": sig, "value": v}
            for k in range(10) for sig, v in (("nav_x", 0.1 * k), ("nav_y", -0.05 * k), ("nav_yaw", 1.5 * k))]
    ok, dup, errors = write(shapes, "robot-telemetry", docs)
    assert (ok, errors) == (30, [])
    q = Queries(shapes, prefix=PREFIX)
    window = q.telemetry_window(t0 + timedelta(milliseconds=180), seconds=1)
    assert set(window) == {"nav_x", "nav_y", "nav_yaw"} and window["nav_yaw"]["high"] == 13.5


def test_a_field_outside_section_10_is_rejected(shapes):
    # a PR event naturally wants its number; the strict mapping refuses the WHOLE doc until it's mapped
    ok, dup, errors = write(shapes, "room-events", [event("pr_opened", 1, pr_number=7)])
    assert ok == 0 and len(errors) == 1 and errors[0].startswith("strict_dynamic_mapping_exception")
