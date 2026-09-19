"""The mapping files themselves, checked with no cluster.

    cd elastic && .venv/bin/python -m pytest tests/test_mappings.py -v

Every invariant here fails SILENTLY once data is flowing -- dynamic mapping guesses a type,
a TSDS drops backfilled rows, a Sentry link is missing -- and mappings can't be changed after
the first write. These read the JSON directly rather than through setup_elastic, so a bug in
setup's validator can't hide a bad file. The last block then checks that validator refuses
each of the same mistakes before it sends anything.
"""
from __future__ import annotations

import copy
import json
import re
from fnmatch import fnmatch
from pathlib import Path

import pytest

import setup_elastic as S

MAPPINGS = Path(__file__).resolve().parent.parent / "mappings"
SNAPSHOTS = ["room-objects", "room-voxels", "room-clouds"]
STREAMS = ["room-observations", "robot-telemetry", "room-events"]
ALL = SNAPSHOTS + STREAMS
TSDS_DIMENSIONS = {"room-observations": {"object_id", "camera"}, "robot-telemetry": {"signal"}}
TSDS_METRICS = {"room-observations": {"confidence", "point_count"}, "robot-telemetry": {"value"}}
TRACE_FIELDS = ("sentry_trace_id", "sentry_span_id", "sentry_url")


def load(name: str) -> dict:
    return json.loads((MAPPINGS / f"{name}.json").read_text())


def inner(name: str) -> dict:
    """The part that becomes the index: a template's "template" block, or the whole body."""
    return load(name)["template"] if name in STREAMS else load(name)


def props(name: str) -> dict:
    return inner(name)["mappings"]["properties"]


def settings(name: str) -> dict:
    """Flat "index.*" keys, whichever of ES's three spellings the file uses."""
    def flat(d: dict, prefix: str = "") -> dict:
        out = {}
        for k, v in d.items():
            out.update(flat(v, f"{prefix}{k}.") if isinstance(v, dict) else {f"{prefix}{k}": v})
        return out
    return {k if k.startswith("index.") else f"index.{k}": v
            for k, v in flat(inner(name).get("settings", {})).items()}


def walk(properties: dict, prefix: str = ""):
    """(dotted path, spec) for every field, object children and multi-fields included."""
    for name, spec in properties.items():
        yield f"{prefix}{name}", spec
        yield from walk(spec.get("properties", {}), f"{prefix}{name}.")
        yield from walk(spec.get("fields", {}), f"{prefix}{name}.")


def seconds(duration: str) -> int:
    n, unit = re.fullmatch(r"(\d+)([smhd])", duration).groups()
    return int(n) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


# ── the files ────────────────────────────────────────────────────────────────

def test_exactly_the_six_mapping_files():
    # setup_elastic reads these six by name: a seventh file would be silently ignored
    assert sorted(p.stem for p in MAPPINGS.glob("*.json")) == sorted(ALL)


@pytest.mark.parametrize("name", SNAPSHOTS)
def test_snapshot_indices_are_plain_indices(name):
    body = load(name)
    assert "index_patterns" not in body and "data_stream" not in body
    assert "mappings" in body


@pytest.mark.parametrize("name", STREAMS)
def test_stream_templates_match_their_own_stream_and_nothing_else(name):
    body = load(name)
    assert body["data_stream"] == {}, "without data_stream the first write makes a plain index"
    patterns = body["index_patterns"]
    assert any(fnmatch(name, p) for p in patterns)
    others = [n for n in ALL if n != name and any(fnmatch(n, p) for p in patterns)]
    assert not others, f"{name}'s template would also capture {others}"
    assert props(name)["@timestamp"]["type"] == "date"


@pytest.mark.parametrize("name", ALL)
def test_dynamic_mapping_is_strict(name):
    # an unmapped field must be rejected loudly, not guessed into a type we can never change
    assert inner(name)["mappings"].get("dynamic") == "strict"


# ── the room is cartesian ────────────────────────────────────────────────────

def test_voxel_cell_is_a_cartesian_point():
    assert props("room-voxels")["cell"] == {"type": "point"}


def test_object_position_is_a_cartesian_point():
    assert props("room-objects")["position"] == {"type": "point"}


@pytest.mark.parametrize("name", ALL)
def test_nothing_is_geo(name):
    geo = [path for path, spec in walk(props(name)) if spec.get("type") in ("geo_point", "geo_shape")]
    assert not geo, "a room isn't a planet: geo types do spherical maths on metres"


@pytest.mark.parametrize("name", ["room-voxels", "room-objects"])
@pytest.mark.parametrize("field", ["voxel_key", "voxel_key_l5", "voxel_key_l3"])
def test_voxel_keys_are_keyword(name, field):
    # text would tokenize "3705261" and every terms/prefix aggregation would return garbage
    assert props(name)[field] == {"type": "keyword"}


# ── time series ──────────────────────────────────────────────────────────────

def test_exactly_the_two_telemetry_streams_are_tsds():
    assert {n for n in ALL if settings(n).get("index.mode") == "time_series"} == set(TSDS_DIMENSIONS)


@pytest.mark.parametrize("name", TSDS_DIMENSIONS)
def test_tsds_look_back_is_7d(name):
    # the default 2h silently drops every replayed or backfilled document older than that
    assert settings(name)["index.look_back_time"] == "7d"


@pytest.mark.parametrize("name,dims", TSDS_DIMENSIONS.items())
def test_tsds_dimensions_are_declared(name, dims):
    declared = {path for path, spec in walk(props(name)) if spec.get("time_series_dimension")}
    assert declared == dims  # capture_id must NOT be one: unique per capture, it defeats series
    assert all(props(name)[d]["type"] == "keyword" for d in dims)
    assert set(settings(name).get("index.routing_path", dims)) == dims


@pytest.mark.parametrize("name,metrics", TSDS_METRICS.items())
def test_tsds_metrics_are_gauges(name, metrics):
    declared = {path: spec["time_series_metric"] for path, spec in walk(props(name))
                if "time_series_metric" in spec}
    assert declared == {m: "gauge" for m in metrics}


def test_telemetry_downsampling_is_declared_and_legal():
    lifecycle = load("robot-telemetry")["template"]["lifecycle"]
    rounds = lifecycle["downsampling"]
    assert 1 <= len(rounds) <= 10
    for prev, cur in zip(rounds, rounds[1:]):
        assert seconds(cur["after"]) >= seconds(prev["after"])
        assert seconds(cur["fixed_interval"]) % seconds(prev["fixed_interval"]) == 0
        assert seconds(cur["fixed_interval"]) > seconds(prev["fixed_interval"])
    assert all(seconds(r["fixed_interval"]) >= 300 for r in rounds), "lifecycle minimum is 5m"
    assert "data_retention" in lifecycle, "no retention -> 30-day rollover -> nothing downsamples"


# ── the Sentry join and the search fields ────────────────────────────────────

@pytest.mark.parametrize("name", ALL)
@pytest.mark.parametrize("field", TRACE_FIELDS)
def test_sentry_join_field_on_every_index(name, field):
    assert props(name).get(field) == {"type": "keyword"}


@pytest.mark.parametrize("name", ["room-objects", "room-observations"])
def test_descriptions_are_searchable_both_ways(name):
    desc = props(name)["raw_description"]
    assert desc["type"] == "semantic_text" and desc["inference_id"] == S.EMBED_ID
    # a match on semantic_text is rewritten to a semantic query: BM25 needs this sub-field
    assert desc["fields"]["text"]["type"] == "text"


# ── setup_elastic refuses the same mistakes before sending anything ──────────

def test_setup_validator_accepts_the_real_files():
    assert S.validate(S.load_specs()) == []


def _props(specs: dict, name: str) -> dict:
    return (specs[name]["template"] if name in STREAMS else specs[name])["mappings"]["properties"]


MISTAKES = {
    "cell as geo_point": (lambda s: _props(s, "room-voxels")["cell"].update(type="geo_point"),
                          "cell: must be type point"),
    "voxel_key as text": (lambda s: _props(s, "room-voxels")["voxel_key_l3"].update(type="text"),
                          "voxel_key_l3: must be keyword"),
    "look_back_time 2h": (lambda s: s["room-observations"]["template"]["settings"].update(
                              {"index.look_back_time": "2h"}), "look_back_time=7d"),
    "sentry field missing": (lambda s: _props(s, "room-clouds").pop("sentry_trace_id"),
                             "sentry_trace_id must be mapped as keyword"),
    "no TSDS dimension": (lambda s: _props(s, "robot-telemetry")["signal"].pop("time_series_dimension"),
                          "no time_series_dimension"),
    "downsampling at 1s": (lambda s: s["robot-telemetry"]["template"]["lifecycle"]["downsampling"][0].update(
                               fixed_interval="1s"), "under 5m"),
    "semantic_text without BM25": (lambda s: _props(s, "room-objects")["raw_description"].pop("fields"),
                                   "needs a text sub-field"),
    "shard count on Serverless": (lambda s: s["room-clouds"].setdefault("settings", {}).update(
                                      number_of_shards=1), "rejected on Serverless"),
}


@pytest.mark.parametrize("mistake", MISTAKES)
def test_setup_refuses(mistake):
    mutate, message = MISTAKES[mistake]
    specs = copy.deepcopy(S.load_specs())
    mutate(specs)
    problems = S.validate(specs)
    assert any(message in p for p in problems), problems
