"""voxelize's Elasticsearch client, tested at the API boundary with a fake: no network, no
quota. Retries, auth failures, the parked-key short circuit, the offline spool and its
replay, and the request/response shapes the real _bulk API uses."""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voxelize  # noqa: E402  (puts the repo root on sys.path)
import es_sink  # noqa: E402
import obs  # noqa: E402
from voxelize import INDEX, IndexResult, VoxelGrid  # noqa: E402

CUBE = ((-4.0, -4.0, 0.0), 8.0, 7)
REPO = Path(__file__).resolve().parents[2]


class Status(Exception):
    """What elasticsearch.ApiError looks like to the client: an HTTP status_code."""
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class FakeES:
    """Plays back a script, one entry per bulk request: an exception to raise, or a callable
    (docs -> per-doc statuses). Records every request."""
    def __init__(self, *script):
        self.script, self.requests = list(script), []

    def bulk(self, operations, refresh=None):
        docs = operations[1::2]
        self.requests.append({"operations": operations, "refresh": refresh})
        step = self.script.pop(0) if self.script else (lambda ds: [201] * len(ds))
        if isinstance(step, BaseException):
            raise step
        statuses = step(docs)
        return {"errors": any(s >= 300 for s in statuses),
                "items": [{"index": {"_index": INDEX, "_id": voxelize.doc_id(d), "status": s,
                                     **({"error": {"type": "x", "reason": f"HTTP {s}"}} if s >= 300 else {})}}
                          for d, s in zip(docs, statuses)]}


def _docs(n_side=12):
    """A few hundred real room-voxels docs, built the way index_voxels builds them."""
    x, y = np.meshgrid(np.linspace(0.1, 0.9, n_side * 3), np.linspace(-0.4, 0.4, n_side * 3))
    pts = np.column_stack([x.ravel(), y.ravel(), np.full(x.size, 0.75)])
    grid = VoxelGrid.from_points(np.repeat(pts, 3, axis=0), CUBE)
    with obs.span("test"):
        return voxelize.voxel_docs(grid, "abc123", "0ff1ce", "main", "2026-09-18T21:00:00Z")


@pytest.fixture
def no_sleep():
    naps = []
    return naps.append, naps


# ── the request shape ────────────────────────────────────────────────────────

def test_bulk_request_shape_and_chunking(monkeypatch, tmp_path, no_sleep):
    monkeypatch.setattr(es_sink, "CHUNK", 50)
    docs = _docs()
    es = FakeES()
    assert voxelize.deliver(docs, "abc123", es, tmp_path, no_sleep[0]) == IndexResult(len(docs))
    assert len(es.requests) == -(-len(docs) // 50)                                  # ceil
    ops = [op for r in es.requests for op in r["operations"]]
    assert ops[0::2] == [{"index": {"_index": INDEX, "_id": f"abc123:{d['voxel_key']}"}} for d in docs]
    assert ops[1::2] == docs and all(r["refresh"] == "wait_for" for r in es.requests)
    assert not (tmp_path / INDEX).exists()                                        # nothing spooled


@pytest.mark.skipif(importlib.util.find_spec("elasticsearch") is None, reason="elastic/ingest.py needs the client")
def test_id_rule_is_elastic_ingests():
    """Through ingest.action(), its public door, so a refactor inside it can't fool this."""
    sys.path.insert(0, str(REPO / "elastic"))
    from ingest import action
    for d in _docs(4):
        assert voxelize.doc_id(d) == action(INDEX, d)["_id"]


def test_no_commit_sha_no_document():
    grid = VoxelGrid.from_points(np.repeat([[0.5, 0.0, 0.75]], 3, axis=0), CUBE)
    for bad in (None, ""):
        with obs.span("test"), pytest.raises(ValueError, match="commit_sha"):
            voxelize.voxel_docs(grid, bad, None, "main", "2026-09-18T21:00:00Z")


# ── retries ──────────────────────────────────────────────────────────────────

def test_flaky_connection_is_retried_with_backoff(tmp_path, no_sleep):
    docs = _docs(4)
    es = FakeES(ConnectionError("reset"), TimeoutError("slow"))
    assert voxelize.deliver(docs, "abc123", es, tmp_path, no_sleep[0]) == IndexResult(len(docs))
    assert no_sleep[1] == [0.5, 1.0] and len(es.requests) == 3


def test_only_the_refused_docs_are_resent(tmp_path, no_sleep):
    docs = _docs(4)
    es = FakeES(lambda ds: [429 if i % 3 == 0 else 201 for i in range(len(ds))])
    assert voxelize.deliver(docs, "abc123", es, tmp_path, no_sleep[0]) == IndexResult(len(docs))
    assert len(es.requests[1]["operations"]) // 2 == len(docs[0::3])            # just the 429s


def test_persistent_503_spools_after_the_retries(tmp_path, no_sleep):
    docs = _docs(4)
    es = FakeES(*[Status(503)] * es_sink.RETRIES)
    r = voxelize.deliver(docs, "abc123", es, tmp_path, no_sleep[0])
    assert (r.indexed, r.spooled) == (0, len(docs)) and r.reason.startswith("transient")
    assert len(es.requests) == es_sink.RETRIES


# ── offline: spool instead of losing or stalling ─────────────────────────────

def test_auth_failure_spools_at_once(tmp_path, no_sleep):
    docs = _docs(4)
    es = FakeES(Status(401))
    r = voxelize.deliver(docs, "abc123", es, tmp_path, no_sleep[0])
    assert r == IndexResult(0, len(docs), r.reason) and r.reason.startswith("auth")
    assert len(es.requests) == 1 and no_sleep[1] == []                           # no retry on 401
    spooled = tmp_path / INDEX / "abc123.jsonl"
    assert [json.loads(line) for line in spooled.read_text().splitlines()] == docs
    assert (tmp_path / ".gitignore").read_text() == "*\n"                        # never committed


def test_parked_key_spools_without_a_network_call(tmp_path, monkeypatch, no_sleep):
    """docs: the key is parked as `# parked...` beside ELASTIC_API_KEY_PARKED. No client is
    even built -- the setup_elastic module is never imported."""
    monkeypatch.setenv("ELASTIC_API_KEY", "# parked until 01:00")
    monkeypatch.setenv("ELASTIC_URL", "https://example.invalid")
    sys.modules.pop("setup_elastic", None)
    r = voxelize.deliver(_docs(4), "abc123", None, tmp_path, no_sleep[0])
    assert r.indexed == 0 and r.spooled > 0 and "parked" in r.reason
    assert "setup_elastic" not in sys.modules


def test_a_rejected_doc_is_a_bug_not_weather(tmp_path, no_sleep):
    es = FakeES(lambda ds: [201] * (len(ds) - 1) + [400])
    with pytest.raises(RuntimeError, match="rejected"):
        voxelize.deliver(_docs(4), "abc123", es, tmp_path, no_sleep[0])
    assert not (tmp_path / INDEX).exists()                                       # retrying can't fix it
    with pytest.raises(Status):
        voxelize.deliver(_docs(4), "abc123", FakeES(Status(400)), tmp_path, no_sleep[0])


def test_flush_replays_the_spool_and_keeps_what_fails(tmp_path, no_sleep):
    a, b = _docs(4), _docs(5)
    voxelize.deliver(a, "sha_a", FakeES(Status(401)), tmp_path, no_sleep[0])
    voxelize.deliver(b, "sha_b", FakeES(Status(401)), tmp_path, no_sleep[0])
    assert voxelize.flush_spool(FakeES(Status(403)), tmp_path, no_sleep[0])["sha_a"].spooled == len(a)
    assert sorted(p.stem for p in (tmp_path / INDEX).glob("*.jsonl")) == ["sha_a", "sha_b"]  # kept
    es = FakeES()
    out = voxelize.flush_spool(es, tmp_path, no_sleep[0])
    assert out == {"sha_a": IndexResult(len(a)), "sha_b": IndexResult(len(b))}
    assert [op for r in es.requests for op in r["operations"]][1::2] == a + b      # same docs, same ids
    assert list((tmp_path / INDEX).glob("*.jsonl")) == []


def test_index_voxels_end_to_end_offline(tmp_path, no_sleep):
    """The pipeline's call, with the cluster refusing the key: the commit is spooled, the
    span says so, and nothing raises."""
    grid = VoxelGrid.from_points(np.repeat([[0.5, 0.0, 0.75], [0.6, 0.1, 0.75]], 3, axis=0))
    r = voxelize.index_voxels(grid, "c0ffee", None, "main", "2026-09-18T21:00:00Z", es=FakeES(Status(401)),
                              room_root=REPO / "room.git", spool_dir=tmp_path, sleep=no_sleep[0])
    assert (r.indexed, r.spooled) == (0, 2)
    [d] = [json.loads(line) for line in (tmp_path / INDEX / "c0ffee.jsonl").read_text().splitlines()][:1]
    assert d["zone"] == "desk" and d["sentry_trace_id"]                           # room.yaml's zones


# ── the real client's exceptions classify the same way ───────────────────────

@pytest.mark.skipif(importlib.util.find_spec("elasticsearch") is None, reason="needs the elasticsearch package")
def test_real_elasticsearch_exceptions():
    import elasticsearch as es_mod
    from elastic_transport import ApiResponseMeta, HttpHeaders, NodeConfig

    def api(cls, status):
        meta = ApiResponseMeta(status=status, http_version="1.1", headers=HttpHeaders(), duration=0.0,
                               node=NodeConfig("https", "localhost", 9200))
        return cls(message=f"HTTP {status}", meta=meta, body={})

    assert es_sink._failure(api(es_mod.AuthenticationException, 401)) == "auth"
    assert es_sink._failure(api(es_mod.AuthorizationException, 403)) == "auth"
    assert es_sink._failure(api(es_mod.ApiError, 429)) == "transient"
    assert es_sink._failure(api(es_mod.ApiError, 503)) == "transient"
    assert es_sink._failure(api(es_mod.BadRequestError, 400)) is None
    assert es_sink._failure(es_mod.ConnectionError("refused")) == "transient"
    assert es_sink._failure(es_mod.ConnectionTimeout("slow")) == "transient"


# ── scan time -> commit time (docs/10 GAP 1) ─────────────────────────────────

def _room_repo(tmp_path):
    """A room repo: room.yaml (the pinned cube, the desk zone) and a .git directory."""
    root = tmp_path / "room"
    (root / ".git").mkdir(parents=True)
    (root / "room.yaml").write_text((REPO / "room.git" / "room.yaml").read_text())
    return root


def test_scan_stages_commit_indexes(tmp_path, no_sleep):
    """The scan has voxels and no sha; the commit has a sha and no voxels. stage() then
    index_staged() carries the grid across, owners resolved at scan time."""
    room = _room_repo(tmp_path)
    rng = np.random.default_rng(0)
    mug = np.column_stack([rng.uniform(0.48, 0.52, 300), rng.uniform(-0.02, 0.02, 300), rng.uniform(0.76, 0.86, 300)])
    top = np.column_stack([rng.uniform(0.2, 0.8, 3000), rng.uniform(-0.3, 0.3, 3000), rng.uniform(0.74, 0.76, 3000)])
    grid = VoxelGrid.from_points(np.vstack([top, mug]))
    path = voxelize.stage(grid, room, claims={"mug_a1b2": mug})
    assert path == room / ".git" / "gitspace" / "voxels.npz" and path.is_file()

    es = FakeES()
    r = voxelize.index_staged(room, "c0ffee", "0ff1ce", "main", "2026-09-18T22:30:00Z", es=es,
                              spool_dir=tmp_path / "spool", sleep=no_sleep[0])
    docs = [op for req in es.requests for op in req["operations"]][1::2]
    assert r == IndexResult(len(grid.ijk)) and len(docs) == len(grid.ijk)
    assert {d["commit_sha"] for d in docs} == {"c0ffee"} and {d["parent_sha"] for d in docs} == {"0ff1ce"}
    with obs.span("test"):
        direct = voxelize.voxel_docs(grid, "c0ffee", "0ff1ce", "main", "2026-09-18T22:30:00Z",
                                     voxelize.load_room(room)["zones"], {"mug_a1b2": mug})
    strip = lambda ds: [{k: v for k, v in d.items() if not k.startswith("sentry_")} for d in ds]
    assert strip(docs) == strip(direct)                          # same documents as indexing directly
    assert any(d["object_id"] == "mug_a1b2" for d in docs)
    assert not path.exists()                                      # consumed
    assert voxelize.index_staged(room, "c0ffee", None, "main", "t", es=FakeES()).reason == "nothing staged"


def test_staged_voxels_survive_an_offline_commit(tmp_path, no_sleep):
    room = _room_repo(tmp_path)
    voxelize.stage(VoxelGrid.from_points(np.repeat([[0.5, 0.0, 0.75]], 3, axis=0)), room)
    r = voxelize.index_staged(room, "c0ffee", None, "main", "2026-09-18T22:30:00Z", es=FakeES(Status(401)),
                              spool_dir=tmp_path / "spool", sleep=no_sleep[0])
    assert (r.indexed, r.spooled) == (0, 1)
    assert (tmp_path / "spool" / INDEX / "c0ffee.jsonl").is_file()          # the spool has it now
