"""to_es_doc: the one translation from a git-side record (`id`) to a room-objects document
(`object_id`) -- docs/10 GAP 2. Offline: no cluster, no network.

    cd elastic && .venv/bin/python -m pytest tests/test_records.py -v
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import ingest
from perception.voxelize import octree_key, pinned_cube
from records import SERVER_FIELDS, to_es_doc
from roomctl.repo import Commit
from roomctl.state import Pose, SchemaError, from_yaml

MAPPING = json.loads((Path(__file__).resolve().parent.parent / "mappings" / "room-objects.json")
                     .read_text())["mappings"]["properties"]
SENT = set(MAPPING) - set(SERVER_FIELDS)  # what a writer sends; the default pipeline adds the rest
PIPELINE = (Path(__file__).resolve().parent.parent / "pipelines" / "room-objects-rerank-text.json").read_text()

# roomctl/state.py's own example record, byte for byte
RECORD = from_yaml("""id: mug_a1b2
class: mug
zone: desk
pose:
  x: 0.42
  y: 0.18
  z: 0.76
  yaw: 15
extents:
  x: 0.12
  y: 0.09
  z: 0.11
color: "#2b4c7e"
first_seen: "2026-09-18T14:12:33Z"
""")
COMMIT = Commit(sha="a3f9c1e" + "0" * 33, parent="9b17e0" + "0" * 34, branch="main",
                message="afternoon: mug moved", changes=[("M", "zones/desk/mug_a1b2.yaml")])
AT = datetime(2026, 9, 19, 14, 22, 7, tzinfo=timezone.utc)
META = {"confidence": 0.87, "point_count": 1420, "observed_by": ["cam0", "cam2"],
        "raw_description": ["a blue ceramic mug", "cup with handle, chipped", "cylindrical container, dark"],
        "vlm_model": "gpt-5-vision"}
TRACE = {"sentry_trace_id": "50c0ccf229c0430fae541a7eab3cb17c", "sentry_span_id": "a9b4702ab42c5901",
         "sentry_url": "https://example.sentry.io/performance/trace/50c0ccf229c0430fae541a7eab3cb17c/"}


def doc(record=RECORD, **kw) -> dict:
    return to_es_doc(record, COMMIT, **{"at": AT, "capture_id": "cap_0912", "meta": META, "trace": TRACE, **kw})


# ── the key set IS the mapping ───────────────────────────────────────────────

@pytest.mark.parametrize("meta,trace", [(META, TRACE), (None, None)], ids=["full", "bare"])
def test_key_set_matches_the_mapping_exactly(meta, trace):
    assert set(doc(meta=meta, trace=trace)) == SENT


@pytest.mark.parametrize("field", ["pose", "position", "extents"])
def test_nested_key_sets_match_the_mapping(field):
    sub = MAPPING[field].get("properties") or {"x": None, "y": None}  # position is a point: {x, y}
    assert set(doc()[field]) == set(sub)


def test_server_fields_are_exactly_what_the_pipeline_sets():
    for f in SERVER_FIELDS:
        assert f"ctx['{f}']" in PIPELINE and f in MAPPING


def test_values_fit_the_mapped_types():
    d = doc()
    for name, spec in ((n, s) for n, s in MAPPING.items() if n in SENT):
        value, kind = d[name], spec.get("type")
        if value is None:
            continue
        if kind in ("keyword", "text", "semantic_text"):
            assert isinstance(value, str) or (isinstance(value, list) and all(isinstance(v, str) for v in value)), name
        elif kind == "date":
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif kind == "float":
            assert isinstance(value, (int, float)) and not isinstance(value, bool), name
        elif kind == "integer":
            assert isinstance(value, int) and not isinstance(value, bool), name
        elif kind == "point":
            assert set(value) == {"x", "y"} and all(isinstance(v, float) for v in value.values()), name
        else:  # objects
            for k, v in value.items():
                assert isinstance(v, (int, float)) and not isinstance(v, bool), f"{name}.{k}"


# ── id -> object_id, and the _id it produces ─────────────────────────────────

def test_id_becomes_object_id():
    d = doc()
    assert d["object_id"] == RECORD.id == "mug_a1b2"
    assert "id" not in d, "dynamic:strict would reject the whole doc"
    assert d["class"] == RECORD.cls == "mug"


def test_ingest_id_is_commit_sha_and_object_id_and_stable():
    first, again = ingest.action("room-objects", doc()), ingest.action("room-objects", doc())
    assert first["_id"] == again["_id"] == f"{COMMIT.sha}:{RECORD.id}", "re-ingest must overwrite"
    assert first["_op_type"] == "index" and "_index" not in first["_source"]


def test_ingest_refuses_a_git_side_record():
    git_shaped = {"id": "mug_a1b2", "class": "mug", "commit_sha": COMMIT.sha}
    with pytest.raises(ValueError, match="to_es_doc"):
        ingest.action("room-objects", git_shaped)


@pytest.mark.parametrize("field", ["commit_sha", "object_id"])
@pytest.mark.parametrize("value", [None, ""])
def test_ingest_refuses_an_id_it_would_have_to_guess(field, value):
    with pytest.raises(ValueError, match=field):  # never "None:mug_a1b2"
        ingest.action("room-objects", {**doc(), field: value})


# ── voxel keys: the same cube and encoder as room-voxels ─────────────────────

def test_voxel_key_is_perceptions_key_for_that_point():
    d, p = doc(), RECORD.pose
    origin, size, levels = pinned_cube()
    key = octree_key(p.x, p.y, p.z, origin, size, levels)
    # one digit per level, from the cube -- not a hardcoded 7. This test pinned the depth and
    # failed the moment OCTREE_LEVELS went 7 -> 8, which is the whole point: a key is as deep as
    # the cube says, and anything asserting otherwise breaks on a legitimate change.
    assert d["voxel_key"] == key and len(key) == levels
    assert (d["voxel_key_l5"], d["voxel_key_l3"]) == (key[:5], key[:3])


def test_outside_the_cube_has_no_voxel_key():
    far = replace(RECORD, pose=Pose(5.0, 0.18, 0.76, 15))  # origin -4 m, side 8 m: x must be < 4
    d = doc(far)
    assert d["voxel_key"] is d["voxel_key_l5"] is d["voxel_key_l3"] is None
    assert set(d) == SENT


# ── inputs it must refuse or normalise ───────────────────────────────────────

def test_an_off_schema_record_is_refused():
    with pytest.raises(SchemaError, match="yaw"):
        doc(replace(RECORD, pose=Pose(0.42, 0.18, 0.76, 195)))  # yaw is an axis: [0, 180)


@pytest.mark.parametrize("arg,bad", [("meta", {"descriptions": ["a mug"]}),  # the fake's internal name
                                     ("trace", {"trace_id": "abc"})])
def test_unknown_keys_are_refused_not_dropped(arg, bad):
    with pytest.raises(ValueError, match="unknown"):
        doc(**{arg: bad})


def test_one_description_becomes_an_array():
    assert doc(meta={"raw_description": "a blue ceramic mug"})["raw_description"] == ["a blue ceramic mug"]


def test_no_trace_means_null_links_not_invented_ones():
    d = doc(trace=None)
    assert d["sentry_trace_id"] is d["sentry_span_id"] is d["sentry_url"] is None


def test_timestamps():
    d = doc()
    assert d["@timestamp"] == "2026-09-19T14:22:07Z"
    assert d["first_seen"] == RECORD.first_seen
    assert doc(at="2026-09-19T14:22:07.412Z")["@timestamp"] == "2026-09-19T14:22:07.412Z"


# ── one commit's whole snapshot: what the roomctl -> elastic hook (docs/10 GAP 1) sends ──

EVENTS_MAPPING = json.loads((Path(__file__).resolve().parent.parent / "mappings" / "room-events.json")
                            .read_text())["template"]["mappings"]["properties"]
CHANGES = [("A", "zones/desk/cup_7e21.yaml"), ("M", "zones/couch/mug_a1b2.yaml"),
           ("D", "zones/workbench/tool_4f2a.yaml"), ("R", "zones/desk/keys_7c2e.yaml"),
           ("M", "room.yaml")]  # not an object: ignored
COMMIT2 = replace(COMMIT, changes=CHANGES)


def test_commit_event_key_set_matches_the_mapping_exactly():
    from records import commit_event
    assert set(commit_event(COMMIT2, at=AT, capture_id="cap_0912")) == set(EVENTS_MAPPING)


def test_commit_event_lists_come_from_the_diff():
    from records import commit_event
    ev = commit_event(COMMIT2, at=AT, capture_id="cap_0912", trace=TRACE)
    assert ev["objects_added"] == ["cup_7e21"]
    assert ev["objects_moved"] == ["keys_7c2e", "mug_a1b2"]  # R = changed zone: a move
    assert ev["objects_removed"] == ["tool_4f2a"]
    assert ev["objects_affected"] == ["cup_7e21", "keys_7c2e", "mug_a1b2", "tool_4f2a"]
    assert ev["zone"] == ["couch", "desk", "workbench"]
    assert (ev["event_type"], ev["message"], ev["sentry_trace_id"]) == ("commit", COMMIT.message, TRACE["sentry_trace_id"])


def test_commit_actions_is_the_full_snapshot_plus_the_event():
    cup = replace(RECORD, id="cup_7e21", cls="cup", color="#e8e4da")
    acts = ingest.commit_actions(COMMIT2, [RECORD, cup], at=AT, capture_id="cap_0912",
                                 meta_by_id={"cup_7e21": {"raw_description": ["white ceramic cup"]}},
                                 trace=TRACE)
    assert [(a["_index"], a.get("_id")) for a in acts] == [
        ("room-objects", f"{COMMIT.sha}:mug_a1b2"), ("room-objects", f"{COMMIT.sha}:cup_7e21"),
        ("room-events", f"{COMMIT.sha}:commit")]
    by_id = {a["_source"].get("object_id"): a["_source"] for a in acts[:2]}
    assert by_id["cup_7e21"]["raw_description"] == ["white ceramic cup"]
    assert by_id["mug_a1b2"]["raw_description"] is None, "no meta for it: null, not someone else's"
    assert all(set(a["_source"]) == SENT for a in acts[:2])
    assert acts[2]["_op_type"] == "create"
