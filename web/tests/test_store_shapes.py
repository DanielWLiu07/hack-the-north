"""The five web bugs from elastic-09's connection audit (docs/10-open-questions.md), pinned.
No network: pure functions, and store.py in fixture mode."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["SENTRY_DSN"] = ""

import store  # noqa: E402


def test_downsampled_telemetry_value_keeps_the_spike():
    assert store._metric(0.134) == 0.134
    assert store._metric({"min": -0.02, "max": 0.134, "sum": 1.0, "value_count": 3000}) == 0.134
    assert store._metric({"min": -0.2, "max": 0.05, "sum": 0, "value_count": 10}) == -0.2   # the extreme is signed
    assert store._metric({"sum": 1}) is None and store._metric("x") is None


def test_mixed_timestamp_formats_are_the_same_instant():
    a, b = store.when("2026-09-19T00:37:29Z"), store.when({"@timestamp": "2026-09-19T00:37:29.000Z"})
    assert a == b and not ("2026-09-19T00:37:29Z" == "2026-09-19T00:37:29.000Z")
    assert store.when("2026-09-19T00:37:29.500Z") > a                 # as strings this compares the wrong way round
    assert "2026-09-19T00:37:29.500Z" < "2026-09-19T00:37:29Z"
    assert store.when(None) == store.when("garbage")                 # unknown sorts first, never raises


def test_a_rejected_key_is_not_a_reason_to_serve_fixtures():
    assert "elastic_auth" not in store._FALLBACK_CODES
    assert {"elastic_paused", "elastic_unreachable"} <= store._FALLBACK_CODES


def test_prefix_filter_in_fixture_mode():
    store._es = None
    docs, source = asyncio.run(store._find("room-observations", {}, prefix={"capture_id": "watch_"}, size=50, label="t"))
    assert source == "fixture" and docs and all(d["capture_id"].startswith("watch_") for d in docs)
    newest, _ = asyncio.run(store._find("room-events", {}, size=2, newest_first=True, label="t"))
    assert store.when(newest[0]) >= store.when(newest[1])


def test_provenance_is_one_rule_and_a_real_trace_does_not_make_scripted_text_real():
    assert store.provenance("fake/scene_gen") == {"synthetic": True, "scripted_text": True, "rendered_input": False,
                                                  "vlm_model": "fake/scene_gen", "why": "text scripted by fake/scene_gen"}
    assert store.provenance("scripts/story_demo")["synthetic"] and store.provenance("tests/x")["synthetic"]
    missing = store.provenance(None)
    assert missing["synthetic"] and "no vlm_model recorded" in missing["why"]
    real = store.provenance("gpt-5", "cap_0912")
    assert real == {"synthetic": False, "scripted_text": False, "rendered_input": False, "vlm_model": "gpt-5", "why": None}
    rendered = store.provenance("gpt-5", "synth_00_000")          # real pipeline, real model — over rendered frames
    assert rendered["synthetic"] and not rendered["scripted_text"] and "rendered by perception/synthetic.py" in rendered["why"]
