"""pipeline.scan_into -> what /capture/<id> reads, with the Elasticsearch boundary faked (no network).
The input is a SYNTHETIC recording (perception/synthetic.py); everything after its JPEG is real."""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline  # noqa: E402  (puts the repo root on sys.path)
import synthetic  # noqa: E402
import voxelize  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
def _props(index):
    """An index's mapping, or a data stream's template's -- elastic/mappings/<index>.json."""
    m = json.loads((REPO / "elastic" / "mappings" / f"{index}.json").read_text())
    return m.get("template", m)["mappings"]["properties"]


MAPPING = {i: _props(i) for i in ("room-clouds", "room-observations")}
WEB_CAPTURE_ID = re.compile(r"^[a-z]+_[0-9]+$")          # web/capture_api.py CAPTURE_ID


class FakeES:
    def __init__(self):
        self.ops = []

    def bulk(self, operations, refresh=None):
        pairs = list(zip(operations[0::2], operations[1::2]))
        self.ops += pairs
        return {"errors": False, "items": [{next(iter(a)): {"status": 201}} for a, _ in pairs]}

    def docs(self, index):
        return [(a, d) for a, d in self.ops if next(iter(a.values()))["_index"] == index]


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    return synthetic.write_recordings(tmp_path_factory.mktemp("rec"), 1, seed=3,
                                      start=datetime(2026, 9, 19, 5, 0, tzinfo=timezone.utc))[0]


@pytest.fixture
def scanned(recording, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "CLOUDS", tmp_path / "clouds")
    es = FakeES()
    result = pipeline.scan_into(tmp_path / "room", recording, es=es)
    return es, result, tmp_path


def test_capture_id_is_one_the_web_accepts(recording):
    assert WEB_CAPTURE_ID.match(pipeline.load_recording(recording).capture_id)


def test_the_catalog_doc_is_what_the_capture_page_reads(scanned, recording):
    es, result, tmp = scanned
    rec = pipeline.load_recording(recording)
    [(action, doc)] = es.docs("room-clouds")
    assert action == {"index": {"_index": "room-clouds", "_id": rec.capture_id}}      # natural id: re-index overwrites
    assert set(doc) <= set(MAPPING["room-clouds"]), set(doc) - set(MAPPING["room-clouds"])
    assert doc["capture_id"] == rec.capture_id and doc["@timestamp"] == rec.at and doc["quality_ok"] is True
    assert (doc["skew_ms"], doc["tilt_rate_max"]) == (rec.skew_ms, rec.tilt_rate_max)
    assert 0.6 < doc["coverage_pct"] <= 1.0 and doc["cameras"] == ["cam0"]
    assert re.fullmatch(r"[0-9a-f]{32}", doc["sentry_trace_id"])                     # the page's Sentry link
    ply = Path(doc["cloud_uri"].removeprefix("file://"))
    assert ply.is_file() and f"element vertex {doc['point_count']}\n".encode() in ply.read_bytes()[:200]
    lo, hi = doc["bounds"]["min"], doc["bounds"]["max"]
    assert lo["z"] > -0.3 and hi["z"] < 3.0 and hi["x"] < 4.0                          # F_world metres, Z-up


def test_observation_rows_are_one_per_object_per_camera(scanned):
    es, result, tmp = scanned
    rows = es.docs("room-observations")
    kept = [(a, d) for a, d in rows if d["object_id"] is not None]
    discarded = [(a, d) for a, d in rows if d["object_id"] is None]
    committed = sorted(p.stem for p in (tmp / "room" / "zones").rglob("*.yaml"))
    assert result.ok and len(kept) == result.objects == len(committed) == 3
    assert result.rejected == len(discarded)
    [cloud] = [d for _, d in es.docs("room-clouds")]
    for action, d in kept:
        assert action == {"create": {"_index": "room-observations"}}                  # a TSDS: create only
        assert set(d) <= set(MAPPING["room-observations"]), set(d) - set(MAPPING["room-observations"])
        # a fallback-path object is seen in the fused cloud ("fused"); a masked one names its camera
        assert d["capture_id"] == cloud["capture_id"] and d["camera"] in ("fused", "cam0")
        assert d["sentry_trace_id"] == cloud["sentry_trace_id"]                        # one waterfall
        assert d["rejected_reason"] is None
        assert 0.0 < d["raw_x"] < 1.1 and -0.5 < d["raw_y"] < 0.5 and 0.68 < d["raw_z"] < 1.0   # on the desk
    assert sorted(d["object_id"] for _, d in kept) == committed
    reasons = {"too_small", "plane_fragment", "no_depth", "low_confidence", "single_frame",
               "unassociated"} | {f"roomignore:{n}" for n in ("person", "robot", "cable")}
    stamps = [d["@timestamp"] for _, d in rows]
    assert len(stamps) == len(set(stamps))                                           # TSDS identity
    for action, d in discarded:
        assert action == {"create": {"_index": "room-observations"}}
        assert set(d) <= set(MAPPING["room-observations"]), set(d) - set(MAPPING["room-observations"])
        assert d["rejected_reason"] in reasons
        assert d["capture_id"] == cloud["capture_id"]
        assert d["sentry_trace_id"] == cloud["sentry_trace_id"]


def test_the_fallback_path_names_the_fused_cloud(recording, tmp_path, monkeypatch):
    """The test above accepts either path, because which one runs depends on whether the YOLO
    weights are installed. This one pins the fallback: with the mask path forced off, no row may
    claim a camera it was never attributed to."""
    monkeypatch.setattr(pipeline, "CLOUDS", tmp_path / "clouds")
    monkeypatch.setenv("GITSPACE_SEGMENTER", "off")
    es = FakeES()
    assert pipeline.scan_into(tmp_path / "room", recording, es=es).ok
    kept = [d for _, d in es.docs("room-observations") if d["object_id"] is not None]
    assert len(kept) == 3 and {d["camera"] for d in kept} == {"fused"}


def test_forcing_the_floor_path_does_not_eat_the_desk(recording, tmp_path, monkeypatch):
    """The floor finder used to treat the desk as a 70 cm 'large' object and withhold every
    mug on it. It now only looks below FLOOR_Z_MAX, so GITSPACE_FLOOR=1 on a table scene
    still commits the three desk objects."""
    monkeypatch.setenv("GITSPACE_SEGMENTER", "off")
    monkeypatch.setenv("GITSPACE_FLOOR", "1")
    r = pipeline.scan_into(tmp_path / "room", recording, describe=False)
    desk = list((tmp_path / "room" / "zones" / "desk").glob("*.yaml"))
    assert r.ok and len(desk) == 3


def test_discarded_clusters_are_indexed_as_the_discard_pile(recording):
    """docs/11 Gap 1 / D14: a rejected Instance is a room-observations row with object_id
    null, so /capture shows the honest mess. Each row gets its own millisecond: TSDS
    identity is (object_id, camera, @timestamp) and every discard shares object_id null."""
    from cluster import Instance
    rec = pipeline.load_recording(recording)
    specks = Instance(points=np.array([[0.10, 0.0, 0.80]] * 20), camera="cam0",
                      rejected_reason="too_small")
    table = Instance(points=np.array([[0.50, 0.0, 0.75]] * 80), camera="fused",
                     rejected_reason="plane_fragment")
    _, rows = pipeline.capture_docs(rec, {"cam0": (None, np.ones((2, 2), bool), None)}, {}, True,
                                    rejects=[specks, table])
    assert [(r["object_id"], r["rejected_reason"], r["camera"]) for r in rows] == [
        (None, "too_small", "cam0"), (None, "plane_fragment", "fused")]
    assert len({r["@timestamp"] for r in rows}) == 2
    for r in rows:
        assert set(r) <= set(MAPPING["room-observations"]), set(r) - set(MAPPING["room-observations"])


def test_a_rejected_capture_is_catalogued_and_changes_nothing(recording, tmp_path, monkeypatch):
    rec_dir = tmp_path / "rejected"
    import shutil
    shutil.copytree(recording, rec_dir)
    meta = json.loads((rec_dir / "capture.json").read_text())
    meta["tilt_rate_max"] = 0.2                                                        # mid-correction
    (rec_dir / "capture.json").write_text(json.dumps(meta))
    es = FakeES()
    r = pipeline.scan_into(tmp_path / "room", rec_dir, es=es)
    assert not r.ok and not (tmp_path / "room" / "zones").exists()
    [(_, doc)] = es.docs("room-clouds")
    assert doc["quality_ok"] is False and doc["tilt_rate_max"] == 0.2 and "cloud_uri" not in doc
    assert es.docs("room-observations") == []


def test_off_by_default_no_network(recording, tmp_path, monkeypatch):
    monkeypatch.delenv("GITSPACE_INDEX_CAPTURES", raising=False)
    monkeypatch.setenv("GITSPACE_SPOOL", str(tmp_path / "spool"))
    sys.modules.pop("setup_elastic", None)
    pipeline.scan_into(tmp_path / "room", recording)
    assert "setup_elastic" not in sys.modules and not (tmp_path / "spool").exists()


# ── the traversal's ES path: room-voxels docs back into a grid ─────────────────

CUBE = ((-4.0, -4.0, 0.0), 8.0, 7)


def test_voxel_docs_round_trip_to_the_grid():
    import obs
    rng = np.random.default_rng(0)
    g = voxelize.VoxelGrid.from_points(np.repeat(rng.uniform([-2, -2, 0], [2, 2, 1.5], (400, 3)), 3, axis=0), CUBE)
    with obs.span("test"):
        docs = voxelize.voxel_docs(g, "abc123", None, "main", "2026-09-19T05:00:00Z")
    back = voxelize.VoxelGrid.from_docs(docs, CUBE)
    assert np.array_equal(back.ijk, g.ijk) and np.array_equal(back.occ, g.occ)
    assert np.allclose(back.z_lo, g.z_lo, atol=1e-4) and np.allclose(back.z_hi, g.z_hi, atol=1e-4)
    with pytest.raises(ValueError, match="different cube"):
        voxelize.VoxelGrid.from_docs(docs, ((-3.0, -4.0, 0.0), 8.0, 7))              # a moved cube
    with pytest.raises(ValueError, match="levels"):
        voxelize.VoxelGrid.from_docs(docs, ((-4.0, -4.0, 0.0), 8.0, 6))


def test_voxels_for_commit_pages_with_search_after():
    docs = [{"voxel_key": f"{i:07o}", "commit_sha": "c"} for i in range(12)]

    class Pager:
        calls = []

        def search(self, index, size, query, sort, search_after=None):
            Pager.calls.append(search_after)
            start = 0 if search_after is None else int(search_after[0], 8) + 1
            hits = [{"_source": d, "sort": [d["voxel_key"]]} for d in docs[start:start + size]]
            return {"hits": {"hits": hits}}

    assert voxelize.voxels_for_commit(Pager(), "c", page=5) == docs
    assert Pager.calls == [None, ["0000004"], ["0000011"]]


def test_traversal_runs_on_an_es_shaped_grid():
    """Costmap + base pose from room-voxels documents, the path docs/24 A5 allows planning."""
    import costmap
    import obs
    from test_costmap import Arm, MUG, _room
    g = voxelize.VoxelGrid.from_points(_room(np.random.default_rng(0)), CUBE)
    with obs.span("test"):
        docs = voxelize.voxel_docs(g, "abc123", None, "main", "2026-09-19T05:00:00Z")
    cm = costmap.Costmap.from_grid(voxelize.VoxelGrid.from_docs(docs, CUBE), robot_h=0.60)
    pose, why = costmap.solve_base_pose_why(MUG, cm, Arm(), (0.0, 0.0, 0.0), 1.0)
    assert pose is not None and cm.obstacle.sum() > 0                                 # the pedestal is in it


def test_a_capture_nobody_scanned_reads_differently_from_one_that_found_nothing(recording):
    """A cloud doc with no observations was read as a broken writer and cost an investigation
    (elastic-09, 18:30Z: cap_0016/0018/0020/0021). It wasn't: a hallway holds nothing inside
    the demo bench's zones, so those scans were honest. `objects` says which happened --
    absent when nobody scanned the capture, 0 when a scan looked and found nothing."""
    import associate
    import merge
    from cluster import Instance

    rec = pipeline.load_recording(recording)
    out = {"cam0": (None, np.ones((2, 2), bool), None)}
    catalogued, _ = pipeline.capture_docs(rec, out, {}, True)               # index_recording's call
    assert "objects" not in catalogued

    nothing, rows = pipeline.capture_docs(rec, out, {}, True, assocs=[])
    assert nothing["objects"] == 0 and rows == []

    obj = merge.MergedObject([Instance(points=np.array([[0.5, 0.0, 0.8]] * 30), camera="cam0",
                                       label="cup", score=0.9)])
    a = associate.Association(associate.ADDED, "cup_1a2b", "cup", "#2b4c7e", rec.at, "desk", obj)
    seen, rows = pipeline.capture_docs(rec, out, {}, True, assocs=[a])
    assert seen["objects"] == 1 and len(rows) == 1                          # the row count, explained
    assert set(seen) <= set(MAPPING["room-clouds"]), set(seen) - set(MAPPING["room-clouds"])
