"""The seams: every external API mocked at its boundary, every cross-module contract checked
against the other sessions' real code. Nothing here reaches OpenAI, Elasticsearch or Sentry."""
import contextlib
import contextvars
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import associate  # noqa: E402  (puts the repo root on sys.path)
import describe  # noqa: E402
import merge  # noqa: E402
import segment  # noqa: E402
from associate import ADDED, REMOVED, UNCHANGED, UNOBSERVED  # noqa: E402
from cluster import Instance  # noqa: E402
from describe import ViewDescription  # noqa: E402
from roomctl.state import Extents, ObjectRecord, Pose  # noqa: E402
from test_associate import MUG_BACK_WORDS, book, mug  # noqa: E402
from test_describe import _view  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
MAPPINGS = REPO / "elastic" / "mappings"


def _props(name):
    spec = json.loads((MAPPINGS / f"{name}.json").read_text())
    return set((spec.get("template", spec))["mappings"]["properties"])


def _contract(section):
    """The field list for one index from fake/README.md "The documents" -- the written contract."""
    text = (REPO / "fake" / "README.md").read_text()
    block = text.split(f"**`{section}`**", 1)[1].split("\n\n", 1)[0]
    fields = set()
    for code in re.findall(r"`([^`]+)`", block):
        if len(code.split()) < 3:                  # prose like `create` or `object_id: null`
            continue
        for tok in code.split():
            tok = re.sub(r"\{.*|\[\]|\(.*", "", tok)
            if re.fullmatch(r"@?[a-z_]+", tok):
                fields.add(tok)
    return fields


# ═══ OpenAI boundary (describe.py) ═════════════════════════════════════════════

openai = pytest.importorskip("openai")
httpx = pytest.importorskip("httpx")
_REQ = httpx.Request("POST", "https://api.openai.com/v1/responses")


def _status_error(cls, status, headers=None):
    return cls(f"HTTP {status}", response=httpx.Response(status, headers=headers or {}, request=_REQ), body=None)


class ScriptedVLM:
    """Raises / returns what the script says, call by call; records nothing sensitive."""
    model = "gpt-5"

    def __init__(self, *script):
        self.script, self.calls = list(script), 0

    def __call__(self, jpeg):
        self.calls += 1
        step = self.script.pop(0) if self.script else {"label": "mug", "description": "a mug"}
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(describe, "_sleep", slept.append)
    return slept


def test_auth_error_trips_the_breaker_and_stops_calling(monkeypatch, sleeps):
    monkeypatch.setattr(describe, "WORKERS", 1)            # deterministic: one call in flight
    vlm = ScriptedVLM(_status_error(openai.AuthenticationError, 401))
    out = describe.describe([_view(c) for c in ("cam0", "cam1", "cam2")] * 2, vlm=vlm)
    assert vlm.calls == 1                                    # not 6 views x 3 attempts
    assert "AuthenticationError" in out[0].error
    assert all(d.text is None and "vlm unavailable" in d.error for d in out[1:])
    assert sleeps == []                                      # fatal: no backoff either


def test_rate_limit_honours_retry_after_then_succeeds(sleeps):
    vlm = ScriptedVLM(_status_error(openai.RateLimitError, 429, {"retry-after": "3"}))
    d = describe.describe_view(*_view("cam0"), vlm)
    assert d.text == "a mug" and d.attempt == 2 and sleeps == [3.0]


def test_retry_after_is_capped(sleeps):
    vlm = ScriptedVLM(_status_error(openai.RateLimitError, 429, {"retry-after": "600"}))
    describe.describe_view(*_view("cam0"), vlm)
    assert sleeps == [describe.MAX_WAIT_S]


def test_timeouts_and_503s_back_off_then_give_up(sleeps):
    vlm = ScriptedVLM(openai.APITimeoutError(request=_REQ), _status_error(openai.InternalServerError, 503),
                      _status_error(openai.InternalServerError, 503))
    d = describe.describe_view(*_view("cam0"), vlm)
    assert vlm.calls == describe.ATTEMPTS and d.attempt == describe.ATTEMPTS
    assert d.text is None and "503" in d.error
    assert sleeps == list(describe.BACKOFF_S)


def test_bad_request_fails_this_view_only_without_retrying(sleeps):
    vlm = ScriptedVLM(_status_error(openai.BadRequestError, 400))
    d = describe.describe_view(*_view("cam0"), vlm)
    assert vlm.calls == 1 and d.attempt == 1 and "BadRequestError" in d.error and sleeps == []


def test_offline_without_a_key_describes_nothing_and_carries_on(monkeypatch):
    """The state right now: OPENAI_API_KEY parked. The capture must survive it."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    views = [_view(c) for c in ("cam0", "cam1")]
    out = describe.describe(views)                           # vlm=None -> tries the real client
    assert [d.text for d in out] == [None, None]
    assert all(d.error.startswith("vlm offline") and d.attempt == 0 for d in out)
    assert all(inst.description is d for (inst, _), d in zip(views, out))


def test_client_has_our_timeout_and_no_sdk_retries(monkeypatch):
    seen = {}

    class Recorder:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(openai, "OpenAI", Recorder)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    describe.OpenAIVLM()
    assert seen == {"api_key": "sk-test-not-real", "timeout": describe.TIMEOUT_S, "max_retries": 0}


@pytest.mark.parametrize("value", ["# parked until 01:00", "", "sk live but has spaces"])
def test_a_parked_or_garbage_key_never_builds_a_client(monkeypatch, value):
    """docs/10 GAP 4: dotenv reads `KEY=# parked` as a VALUE. It must not reach OpenAI."""
    built = []
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: built.append(kw))
    monkeypatch.setenv("OPENAI_API_KEY", value)
    monkeypatch.setenv("OPENAI_API_KEY_PARKED", "sk-the-real-one")
    out = describe.describe([_view("cam0")])
    assert built == []
    assert out[0].text is None and out[0].error.startswith("vlm offline")
    assert "parked" in out[0].error


def test_worker_threads_inherit_the_callers_context():
    """How the Sentry parent span reaches the gen_ai.chat spans: contextvars, copied per task."""
    parent = contextvars.ContextVar("parent", default=None)
    seen = []

    class Peeking(ScriptedVLM):
        def __call__(self, jpeg):
            seen.append(parent.get())
            return super().__call__(jpeg)

    parent.set("perception.describe")
    describe.describe([_view(c) for c in ("cam0", "cam1", "cam2")], vlm=Peeking())
    assert seen == ["perception.describe"] * 3


def test_every_vlm_call_is_an_agent_turn_with_its_token_usage(monkeypatch):
    turns = []

    class Span(dict):
        def set_data(self, k, v):
            self[k] = v

    @contextlib.contextmanager
    def agent_turn(prompt, model):
        sp = Span(prompt=prompt, model=model)
        turns.append(sp)
        yield sp

    monkeypatch.setattr(describe.obs, "agent_turn", agent_turn)
    monkeypatch.setattr(describe, "_sdk_traces_openai", lambda: False)   # no SDK integration: ours is the gen_ai span
    usage = type("U", (), {"input_tokens": 350, "output_tokens": 60,
                           "output_tokens_details": type("D", (), {"reasoning_tokens": 0})()})()

    class Responses:
        def create(self, **kw):
            return type("R", (), {"status": "completed", "usage": usage,
                                  "output_text": json.dumps({"label": "mug", "description": "a mug"})})()

    vlm = describe.OpenAIVLM.__new__(describe.OpenAIVLM)
    vlm.client = type("C", (), {"responses": Responses()})()
    vlm.model, vlm.effort = "gpt-5", "minimal"
    vlm.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    vlm._lock = __import__("threading").Lock()
    describe.describe([_view("cam0"), _view("cam1")], vlm=vlm)
    assert len(turns) == 2
    assert all(t["prompt"] == describe.PROMPT and t["model"] == "gpt-5" for t in turns)
    # Sentry's AI Agents module: required gen_ai.operation.name + gen_ai.response.model, standard usage keys
    for t in turns:
        assert t["gen_ai.operation.name"] == "chat" and t["gen_ai.response.model"] == "gpt-5"
        assert t["gen_ai.provider.name"] == "openai" and t["gen_ai.pipeline.name"] == "perception.describe"
        assert (t["gen_ai.usage.input_tokens"], t["gen_ai.usage.output_tokens"], t["gen_ai.usage.total_tokens"],
                t["gen_ai.usage.reasoning.output_tokens"]) == (350, 60, 410, 0)


# ═══ Elasticsearch boundary (associate.ESHistory) ══════════════════════════════
# elasticsearch-py isn't installed where pytest runs; these stand-ins have the same shape
# (ApiError carries .meta.status; TransportError for the network) and associate
# classifies by exactly that. A venv check against the real classes is below.

class ApiError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.meta = type("Meta", (), {"status": status})()


class AuthenticationException(ApiError):
    pass


class TransportError(Exception):
    pass


class ConnectionError(TransportError):  # noqa: A001 -- the client's own name
    pass


class FakeES:
    def __init__(self, fail=None, hits=()):
        self.fail, self.hits, self.searches, self.opts = fail, list(hits), 0, None

    def options(self, **kw):
        self.opts = kw
        return self

    def search(self, **kw):
        self.searches += 1
        if self.fail:
            raise self.fail
        return {"hits": {"hits": self.hits}}


def _hit(oid, score, **over):
    src = {"object_id": oid, "class": "cup", "zone": "desk", "color": "#2b4c7e",
           "first_seen": "2026-09-19T14:00:00Z", "commit_sha": "c0",
           "pose": {"x": 0.4, "y": 0.2, "z": 0.8, "yaw": 0.0}, "extents": {"x": 0.09, "y": 0.09, "z": 0.1}}
    src.update(over)
    return {"_score": score, "_source": src}


BOOK = ObjectRecord("book_0001", "book", "desk", Pose(0.0, -0.30, 0.77, 0), Extents(0.20, 0.15, 0.04),
                    "#a01818", "2026-09-19T14:00:00Z")


def test_client_retries_transient_failures_itself():
    es = FakeES()
    associate.ESHistory(es)
    assert es.opts["max_retries"] >= 1 and es.opts["retry_on_timeout"] is True
    assert {429, 503}.issubset(es.opts["retry_on_status"])


@pytest.mark.parametrize("error, reason", [
    (AuthenticationException("bad key", 401), "unauthorised"),
    (ApiError("no index", 404), "index missing"),
    (ApiError("parse", 400), "query rejected"),
    (ApiError("overloaded", 503), "HTTP 503 after client retries"),
    (ConnectionError("refused"), "unreachable (ConnectionError)"),
])
def test_es_failures_become_history_unavailable(error, reason):
    with pytest.raises(associate.HistoryUnavailable, match=re.escape(reason)):
        associate.ESHistory(FakeES(fail=error)).reidentify(mug(), exclude=set())


def test_our_own_bug_is_not_disguised_as_an_outage():
    with pytest.raises(TypeError):
        associate.ESHistory(FakeES(fail=TypeError("bad kwarg"))).reidentify(mug(), exclude=set())


def test_a_malformed_history_doc_is_skipped_not_fatal():
    broken = _hit("cup_dead", 1.9)
    del broken["_source"]["extents"]
    got = associate.ESHistory(FakeES(hits=[broken, _hit("cup_1a2b", 1.7)])).reidentify(mug(), exclude=set())
    assert [c.object_id for c in got] == ["cup_1a2b"]


def test_history_outage_degrades_the_scan_and_says_so():
    """ES down mid-commit: HEAD matching still works, nothing can be `returned`, and every new
    object says it might be a returning one -- roomctl decides whether to commit that."""
    es = FakeES(fail=ConnectionError("refused"))
    back = mug(at=(1.10, 0.60, 0.80), words=MUG_BACK_WORDS, seed=9)
    other = mug(at=(0.10, 0.60, 0.80), words=["a green vase"], seed=3)
    out = {a.cls + str(i): a for i, a in enumerate(
        associate.associate([book(), back, other], {BOOK.id: BOOK}, "cap_9",
                            history=associate.ESHistory(es), occluded=lambda r: False))}
    verdicts = sorted(a.verdict for a in out.values())
    assert verdicts == [ADDED, ADDED, UNCHANGED]
    assert es.searches == 1                                   # disabled after the first failure
    for a in out.values():
        if a.verdict == ADDED:
            assert a.note and "history unavailable" in a.note and "unreachable" in a.note
        else:
            assert a.note is None


def test_real_elasticsearch_exceptions_classify_the_same():
    es_mod = pytest.importorskip("elasticsearch")          # the venv check covers this when skipped
    et = pytest.importorskip("elastic_transport")
    meta = et.ApiResponseMeta(401, "1.1", et.HttpHeaders(), 0.0, et.NodeConfig("https", "es", 443))
    assert associate._is_es_error(es_mod.AuthenticationException("x", meta, None))
    assert "unauthorised" in associate._es_reason(es_mod.AuthenticationException("x", meta, None))
    assert "unreachable" in associate._es_reason(es_mod.ConnectionError("x"))


# ═══ Contracts with the other modules ══════════════════════════════════════════

def _two_view_object(with_words=True):
    views = [Instance(points=np.random.default_rng(k).uniform(-.05, .05, (80, 3)) + [0.4, 0.2, 0.8],
                      label="cup", source="segment", camera=f"cam{k}", score=0.8 + k / 10, color="#2b4c7e",
                      description=ViewDescription(f"cam{k}", "mug", f"words {k}", "gpt-5", 1) if with_words else None)
             for k in range(2)]
    views.append(Instance(points=np.zeros((50, 3)) + [0.41, 0.2, 0.8]))       # fallback-path view
    return merge.MergedObject(views)


def test_observation_docs_match_the_contract_and_the_mapping():
    obj = _two_view_object()
    a = associate.Association(ADDED, "cup_1a2b", "cup", "#2b4c7e", "2026-09-19T14:00:00Z", "desk", obj)
    at = datetime(2026, 9, 19, 14, 22, 7, 412000, tzinfo=timezone.utc)
    trace = {"sentry_trace_id": "t", "sentry_span_id": "s"}
    docs = associate.observation_docs([a], "cap_0912", at, trace)

    contract, mapping = _contract("room-observations"), _props("room-observations")
    for d in docs:
        assert set(d) - set(trace) == contract                 # every key, no extras
        assert set(d) <= mapping                                # dynamic: strict would reject extras
        assert d["object_id"] == "cup_1a2b" and d["capture_id"] == "cap_0912"
    assert [d["@timestamp"] for d in docs] == ["2026-09-19T14:22:07.412Z", "2026-09-19T14:22:07.413Z",
                                               "2026-09-19T14:22:07.414Z"]   # the fake's format, distinct
    fused = docs[2]                                              # fallback-path view: explicit nulls
    assert fused["camera"] == "fused" and fused["confidence"] is None and "confidence" in fused
    assert fused["raw_description"] is None and fused["vlm_model"] is None and fused["label_attempt"] is None
    assert docs[0]["vlm_model"] == "gpt-5" and docs[0]["label_attempt"] == 1


def test_web_object_api_can_read_every_doc_we_write():
    """web/object_api.py indexes sightings with s["confidence"], s["camera"], s["occluded"]."""
    docs = associate.observation_docs(
        [associate.Association(ADDED, "cup_1a2b", "cup", "#2b4c7e", "t", "desk", _two_view_object(False))],
        "cap_1", datetime(2026, 9, 19, tzinfo=timezone.utc))
    for d in docs:
        d["confidence"], d["camera"], d["occluded"]          # KeyError here = a 500 on /object/<id>


def test_object_fields_are_what_room_objects_needs_from_perception():
    f = _two_view_object().object_fields()
    assert set(f) <= _props("room-objects") and set(f) <= _contract("room-objects")
    assert f["observed_by"] == ["cam0", "cam1", "fused"]
    assert f["raw_description"] == ["words 0", "words 1"]       # every view's words, unreconciled
    assert f["confidence"] == 0.85 and f["point_count"] == 210


def test_object_fields_carry_the_describing_models_name():
    """elastic/records.py reads meta["vlm_model"] for room-objects: without it real words land
    with no provenance, indistinguishable from fake/scene_gen's scripted ones. Every META_FIELDS
    key is ours to send, and nothing else."""
    sys.path.insert(0, str(REPO / "elastic"))
    import records

    f = _two_view_object().object_fields()
    assert set(f) == set(records.META_FIELDS)
    assert f["vlm_model"] == "gpt-5"
    bare = _two_view_object(False).object_fields()
    assert bare["raw_description"] == [] and bare["vlm_model"] is None


ZONES = {"desk": {"min": [0.08, -0.50, 0.68], "max": [1.00, 0.50, 1.30]},
         "shelf": {"min": [0.10, 0.60, 0.88], "max": [0.95, 1.00, 1.40]},
         "annex": {"min": [0.90, -0.50, 0.68], "max": [1.50, 0.50, 1.30]}}   # overlaps desk at x 0.90-1.00


def test_zone_rule_matches_voxelize(monkeypatch):
    """voxelize.voxel_docs gives an overlap to the zone first by name; objects must agree, or an
    object and its own voxels sit in different zones. Checked voxel by voxel against the real
    voxel_docs (its Sentry-trace requirement is stubbed locally: nothing is sent)."""
    import voxelize

    monkeypatch.setattr(voxelize.obs, "trace_fields", lambda: {"sentry_trace_id": "stub", "sentry_span_id": "stub"})
    rng = np.random.default_rng(0)
    pts = np.vstack([rng.uniform([0.0, -0.6, 0.6], [1.6, 1.1, 1.4], (400, 3)), [[3.0, 3.0, 0.1]]])
    grid = voxelize.VoxelGrid.from_points(np.repeat(pts, voxelize.MIN_PTS, axis=0))
    docs = voxelize.voxel_docs(grid, "sha", None, "main", "2026-09-19T14:00:00Z", ZONES, {})
    assert len(docs) == len(grid.centres()) > 100
    for c, d in zip(grid.centres(), docs):
        assert associate.zone_of(c, ZONES) == d["zone"], (c, d["zone"])
    assert {"annex", "desk", "shelf", None} <= {d["zone"] for d in docs}
    assert associate.zone_of([0.95, 0.0, 0.80], ZONES) == "annex"   # overlap: first by name


def test_zone_hysteresis_stops_boundary_renames():
    edge = [1.02, 0.0, 0.8]                                   # 2 cm past the desk's +x face
    assert associate.zone_of(edge, ZONES, previous="desk") == "desk"
    assert associate.zone_of([1.06, 0.0, 0.8], ZONES, previous="desk") == "annex"   # really moved
    assert associate.zone_of([3.0, 3.0, 0.1], ZONES) is None


def test_for_serialize_puts_a_moved_object_in_its_new_zone():
    obj = _two_view_object()
    a = associate.Association("moved", "cup_1a2b", "cup", "#2b4c7e", "2026-09-19T14:00:00Z", "shelf", obj,
                              centre=np.array([0.5, 0.0, 0.8]), extents=np.array([.1, .1, .1]), yaw=0.0)
    (m,), _ = associate.for_serialize([a], zones=ZONES)
    assert m.zone == "desk"                                  # was shelf in HEAD: a rename, on purpose
    a.centre = np.array([3.0, 3.0, 0.1])
    with pytest.raises(ValueError, match="no zone"):
        associate.for_serialize([a], zones=ZONES)
    assert associate.for_serialize([a], zones=ZONES, default_zone="floor")[0][0].zone == "floor"


def test_real_room_yaml_zones_load_and_assign():
    import voxelize

    zones = voxelize.load_room(REPO / "room.git").get("zones")
    if not zones:
        pytest.skip("room.git/room.yaml has no zones")
    lo, hi = np.array(zones["desk"]["min"]), np.array(zones["desk"]["max"])
    assert associate.zone_of((lo + hi) / 2, zones) == "desk"


def test_segment_run_with_a_mount_returns_world_frame_through_fuse():
    import fuse
    from test_segment import _masks, _render

    xyz, valid, img, lab = _render()
    mount = fuse.Mount(pitch_down_deg=30.0, height_m=1.2)
    pose = fuse.odom_to_world({"x": 1.5, "z": -0.4, "yaw": 0.6})       # BB's {x, z, yaw}: z -> y
    seg = lambda im: _masks(lab)                                         # noqa: E731
    rect, rest_rect = segment.run(xyz, valid, img, "cam0", segmenter=seg)
    world, rest_world = segment.run(xyz, valid, img, "cam0", segmenter=seg, mount=mount, robot_pose=pose)
    for r, w in zip(rect, world):
        assert np.allclose(w.points, fuse.rect_to_world(r.points, mount, pose))   # same pose as fuse()
        assert w.mask is not None and w.mask.shape == valid.shape       # pixels, for describe
    assert np.allclose(rest_world, fuse.rect_to_world(rest_rect, mount, pose))
    with pytest.raises(ValueError, match="robot_pose"):                  # no silent origin default
        segment.run(xyz, valid, img, "cam0", segmenter=seg, mount=mount)


def test_raycast_occlusion_check_plugs_into_associate():
    """The other session's raycast.occlusion_check is associate's `occluded`, unchanged."""
    import raycast
    import voxelize

    head_mug = ObjectRecord("cup_1a2b", "cup", "desk", Pose(2.0, 0.0, 0.81, 0), Extents(0.08, 0.08, 0.10),
                            "#2b4c7e", "2026-09-19T14:00:00Z")
    wall = np.array([[1.0, y, z] for y in np.arange(-0.3, 0.31, 0.02) for z in np.arange(0.6, 1.2, 0.02)])
    grid = voxelize.VoxelGrid.from_points(np.repeat(wall, 4, axis=0))
    def verdict(*cams):
        (a,) = associate.associate([], {head_mug.id: head_mug}, "cap_1",
                                   occluded=raycast.occlusion_check(list(cams), grid))
        return a.verdict

    assert verdict(raycast.Camera((0.0, 0.0, 0.9), (1.0, 0.0, -0.05), 40)) == UNOBSERVED   # wall in the way
    assert verdict(raycast.Camera((2.0, 2.0, 0.9), (0.0, -1.0, -0.05), 40)) == REMOVED     # clear view, not there
    assert verdict(raycast.Camera((2.0, 2.0, 0.9), (0.0, 1.0, -0.05), 40)) == UNOBSERVED   # facing away: no view
    with pytest.raises(TypeError):                                        # bare positions are refused now
        associate.associate([], {head_mug.id: head_mug}, "cap_1",
                            occluded=raycast.occlusion_check([(0.0, 0.0, 0.9)], grid))


def test_parked_elastic_key_means_offline_history_and_noted_new_objects(monkeypatch):
    """ELASTIC_API_KEY parked: no client is built (elasticsearch isn't even importable here), no
    call is made, and every new object says it might be a returning one."""
    monkeypatch.setenv("ELASTIC_URL", "https://es.example:443")
    monkeypatch.setenv("ELASTIC_API_KEY", "# parked until 01:00")
    monkeypatch.setenv("ELASTIC_API_KEY_PARKED", "real-key")
    h = associate.history_from_env()
    assert isinstance(h, associate.OfflineHistory) and h.reason == "ELASTIC_API_KEY parked"
    (a,) = associate.associate([mug()], {}, "cap_1", history=h, occluded=lambda r: False)
    assert a.verdict == ADDED and "ELASTIC_API_KEY parked" in a.note


def test_keys_follow_web_servers_rule():
    import keys

    assert keys.usable("  sk-abc  ") == "sk-abc"
    assert keys.usable("# parked") == "" and keys.usable("a b") == "" and keys.usable(None) == ""


def test_one_gen_ai_span_per_call_when_the_sdk_already_traces_openai(monkeypatch):
    """Live, sentry_sdk's OpenAI integration nested its own gen_ai.responses span under every
    one of ours and both carried tokens: 35 calls showed as 70, 13,020 tokens as 26,040."""
    turns, plain = [], []

    @contextlib.contextmanager
    def agent_turn(prompt, model):
        turns.append(model)
        yield None

    @contextlib.contextmanager
    def span(op, desc="", **data):
        plain.append(op)
        yield None

    monkeypatch.setattr(describe.obs, "agent_turn", agent_turn)
    monkeypatch.setattr(describe.obs, "span", span)
    monkeypatch.setattr(describe, "_sdk_traces_openai", lambda: True)

    class Responses:
        def create(self, **kw):
            return type("R", (), {"status": "completed", "output_text": json.dumps({"label": "mug", "description": "a mug"})})()

    vlm = describe.OpenAIVLM.__new__(describe.OpenAIVLM)
    vlm.client = type("C", (), {"responses": Responses()})()
    vlm.model, vlm.effort = "gpt-5", "minimal"
    vlm.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    vlm._lock = __import__("threading").Lock()
    describe.describe_view(*_view("cam0"), vlm)
    assert turns == [] and plain == ["perception.vlm_call"]


def test_erosion_scales_with_image_width():
    """depth.DOWNSAMPLE went 0.375 -> 0.75 (stereo left_rect 960 wide); RealSense colour is 640."""
    assert [segment.erode_px(w) for w in (480, 640, 960, 1280)] == [2, 2, 4, 5]


def test_a_realsense_capture_folder_runs_the_real_chain(tmp_path):
    """Sarah's collector's on-disk contract (docs/27): <cam>_color.png, <cam>_pointcloud.npy (H*W x 3
    METRES, camera frame, zeros = no depth), <cam>_depth_raw.npy (uint16 MILLIMETRES). Loaded by
    depth.RealSenseDepth, segmented, described per camera -- two cameras, two sets of words."""
    import cv2
    from test_describe import FakeVLM
    from test_segment import _masks, _render

    xyz, valid, img, lab = _render()
    cap = tmp_path / "session_20260919_120000" / "capture_0003"
    cap.mkdir(parents=True)
    for cam in ("d415", "d435"):
        pts = np.where(valid[..., None], xyz, 0).reshape(-1, 3).astype(np.float32)
        cv2.imwrite(str(cap / f"{cam}_color.png"), img)
        np.save(cap / f"{cam}_pointcloud.npy", pts)                              # METRES
        np.save(cap / f"{cam}_depth_raw.npy", np.round(xyz[..., 2] * 1000).astype(np.uint16))   # MILLIMETRES
    (cap / "metadata.json").write_text(json.dumps({"capture_index": 3}))

    out = describe.describe_capture(cap, segmenter=lambda im: _masks(lab), vlm=FakeVLM())
    assert sorted(out) == ["d415", "d435"]
    for cam, pairs in out.items():
        assert sorted(i.label for i, _ in pairs) == ["book", "cup"]
        assert all(i.camera == cam and d.camera == cam and d.text for i, d in pairs)
        book = next(i for i, _ in pairs if i.label == "book")
        assert abs(book.centroid[2] - 0.80) < 0.01                               # metres, not mm


def test_a_millimetre_point_cloud_is_refused_not_described(tmp_path):
    """The unit trap docs/27 warns about, caught on load rather than as objects 1000x too far."""
    import cv2
    from test_describe import FakeVLM
    from test_segment import _masks, _render

    xyz, valid, img, lab = _render()
    cap = tmp_path / "capture_0001"
    cap.mkdir()
    cv2.imwrite(str(cap / "d415_color.png"), img)
    np.save(cap / "d415_pointcloud.npy", (xyz.reshape(-1, 3) * 1000).astype(np.float32))   # WRONG: mm
    with pytest.raises(AssertionError, match="metres"):
        describe.describe_capture(cap, segmenter=lambda im: _masks(lab), vlm=FakeVLM())


def test_a_rejected_mask_gives_its_pixels_back_to_the_fallback():
    """pipeline.segment_then_cluster (the mask path, wired): YOLO's "dining table" mask on the
    synthetic desk covers everything standing on it. It is no object (cluster's size window), and
    it must not hide what it covers: those pixels go to the fallback, which still finds them."""
    import fuse
    import pipeline
    from test_segment import _render

    xyz, valid, img, lab = _render()
    table = segment.Mask(np.ones_like(valid), "dining table", 0.6)          # the whole view
    mount, pose = fuse.Mount(pitch_down_deg=0.0, height_m=1.0), (0.0, 0.0, 0.0)
    zones = {"desk": {"min": [0.6, -0.3, 0.8], "max": [0.95, 0.3, 1.2]}}    # around book + mug; not the wall
    found = pipeline.segment_then_cluster({"cam0": (xyz, valid, img)}, {"cam0": mount}, pose, zones,
                                          lambda im: [table], segment.IGNORE_LABELS)
    assert "dining table" not in {i.label for i in found}
    assert any(i.label == "unknown" and i.source == "cluster" for i in found)      # still found, by the fallback

    # the old rule -- every mask claims its pixels -- would have lost them
    _, rest = segment.run(xyz, valid, img, "cam0", lambda im: [table], mount=mount, robot_pose=pose)
    assert len(rest) == 0


# ── bb_source candidates named from the robot's frame (roommate plan, task 1) ─────────────

from dataclasses import dataclass as _dc  # noqa: E402


@_dc
class Cand:                                   # the fields of plan/roommate/03-interfaces.md §4's Candidate we use
    centroid: tuple
    extents: tuple
    yaw_axis_deg: int = 0


ROOM_TO_CAM = np.array([[0, -1, 0, 0], [0, 0, -1, 0], [1, 0, 0, 0], [0, 0, 0, 1]], float)   # room (X fwd, Z up) -> optical
K_RENDER = np.array([[300.0, 0, 240.0], [0, 300.0, 135.0], [0, 0, 1]])


def _render_candidates():
    """test_segment's scene in the room frame: book, mug, a thing hidden BEHIND the book, and a
    patch of bare wall."""
    return [Cand((0.815, 0.025, -0.025), (0.03, 0.15, 0.15)),       # book face, 0.80 m ahead
            Cand((0.80, -0.09, -0.04), (0.08, 0.08, 0.12)),         # mug
            Cand((1.63, 0.025, -0.025), (0.06, 0.30, 0.30)),        # behind the book, twice as far and twice
                                                                     # the size: it projects over ALL of it
            Cand((1.99, 0.40, 0.00), (0.05, 0.10, 0.10))]           # wall, no mask there


def test_candidates_take_their_names_from_the_masks_they_project_onto():
    from test_segment import _masks, _render

    xyz, valid, img, lab = _render()
    got = segment.label_candidates(_render_candidates(), img, K_RENDER, ROOM_TO_CAM, lambda im: _masks(lab))
    assert [g[0] for g in got] == ["book", "cup", "unknown", "unknown"]
    assert got[0][1] == 0.91 and got[0][2].label == "book"           # score and mask ride along (for describe)


def test_an_ignored_mask_names_nothing():
    from test_segment import _render

    xyz, valid, img, lab = _render()
    got = segment.label_candidates(_render_candidates(), img, K_RENDER, ROOM_TO_CAM,
                                   lambda im: [segment.Mask(lab == 2, "person", 0.99)])
    assert [g[0] for g in got] == ["unknown"] * 4


def test_labels_do_not_depend_on_where_the_robot_stands():
    """Move and turn the whole room and the camera together: the same names."""
    from test_segment import _masks, _render

    xyz, valid, img, lab = _render()
    yaw = np.radians(35)
    move = np.array([[np.cos(yaw), -np.sin(yaw), 0, 1.7], [np.sin(yaw), np.cos(yaw), 0, -0.4], [0, 0, 1, 0.9], [0, 0, 0, 1]])
    moved = [Cand(tuple((move @ np.r_[c.centroid, 1])[:3]), c.extents, int(np.degrees(yaw)) % 180) for c in _render_candidates()]
    got = segment.label_candidates(moved, img, K_RENDER, ROOM_TO_CAM @ np.linalg.inv(move), lambda im: _masks(lab))
    assert [g[0] for g in got] == ["book", "cup", "unknown", "unknown"]


def test_bb_map_observations_say_seen_hidden_or_nothing():
    """Roommate plan task 4: rows as camera "bb_map"; a HIDDEN object gets an occluded row at its
    last pose (web/object_api's hidden-vs-gone rule reads it); a STALE one gets no row at all."""
    head = {oid: ObjectRecord(oid, "cup", "desk", Pose(x, 0.2, 0.8, 0), Extents(0.09, 0.09, 0.10), "#2b4c7e",
                              "2026-09-19T14:00:00Z") for oid, x in (("cup_aaaa", 0.3), ("cup_bbbb", 0.6), ("cup_cccc", 0.9))}
    seen = _two_view_object()                                       # stands where cup_aaaa was (0.4, 0.2, 0.8)
    assocs = associate.associate([seen], head, "cap_bb1", zones=None, misses={},
                                 fresh=lambda r: r.id != "cup_cccc",              # cccc's block is stale
                                 occluded=lambda r: r.id == "cup_bbbb")          # bbbb is hidden
    by = {a.object_id: a for a in assocs}
    assert by["cup_bbbb"].verdict == UNOBSERVED and by["cup_bbbb"].occluded
    assert by["cup_cccc"].verdict == UNOBSERVED and not by["cup_cccc"].occluded
    docs = associate.observation_docs(assocs, "cap_bb1", datetime(2026, 9, 19, tzinfo=timezone.utc), camera="bb_map")
    rows = {d["object_id"]: d for d in docs}
    assert set(rows) == {"cup_aaaa", "cup_bbbb"}                      # no row for the stale one
    assert {d["camera"] for d in docs} == {"bb_map"}
    assert rows["cup_aaaa"]["occluded"] is False
    hid = rows["cup_bbbb"]
    assert hid["occluded"] is True and (hid["raw_x"], hid["raw_y"], hid["raw_z"]) == (0.6, 0.2, 0.8)
    contract, mapping = _contract("room-observations"), _props("room-observations")
    for d in docs:
        assert set(d) == contract and set(d) <= mapping
        d["confidence"], d["camera"], d["occluded"]                   # what web/object_api indexes


# ── map objects: names and crops from the frame (task 1, applied), words for new ones (task 3) ──

def _map_objects(cands):
    return [merge.MergedObject([Instance(points=np.zeros((5, 3)) + c.centroid, camera="bb_map")]) for c in cands]


def test_map_objects_take_the_frames_names_and_a_crop_of_what_it_sees():
    """scan_into_bb's step 2: one call puts label, score and a pixel mask on each map object's
    view. A named object's mask is the segmenter's; one the segmenter can't name but the
    camera sees gets the visible part of its box, so describe.py can still crop it; one the
    frame doesn't see at all gets nothing to crop."""
    from test_segment import _masks, _render

    xyz, valid, img, lab = _render()
    cands = _render_candidates()
    objs = _map_objects(cands)
    segment.label_map_objects(objs, cands, img, K_RENDER, ROOM_TO_CAM, lambda im: _masks(lab))
    v = [o.views[0] for o in objs]
    assert [x.label for x in v] == ["book", "cup", "unknown", "unknown"]
    assert v[0].score == 0.91 and (v[0].mask == (lab == 1)).all()
    assert v[2].mask is None and v[2].score is None                 # all of it hidden behind the book
    assert v[3].mask is not None and v[3].mask.sum() >= segment.MIN_CROP_PX and v[3].score is None
    assert all(x.camera == "bb_map" for x in v)                        # still the map's row, not a camera's


def test_only_what_this_pass_added_is_described_and_the_model_rides_along():
    """Roommate task 3: new objects reach Elastic with words and with WHO wrote them. An object
    already in HEAD keeps its words (publish carries them forward), so a quiet room costs no
    VLM call; describing twice is a no-op."""
    from test_describe import FakeVLM, _view

    new, img = _view("cam0")
    old, _ = _view("cam1")
    unseen = Instance(points=np.zeros((5, 3)), camera="bb_map")      # added, but no crop
    assocs = [associate.Association(ADDED, "cup_1a2b", "cup", "#2b4c7e", "t", "desk", merge.MergedObject([new])),
              associate.Association(UNCHANGED, "cup_9f9f", "cup", "#2b4c7e", "t", "desk", merge.MergedObject([old])),
              associate.Association(ADDED, "box_0c0c", "box", "#806040", "t", "desk", merge.MergedObject([unseen]))]
    vlm = FakeVLM()
    out = describe.describe_added(assocs, img, vlm)
    assert vlm.calls == 1 and len(out) == 1
    assert new.description.text and old.description is None and unseen.description is None
    f = assocs[0].obj.object_fields()
    assert f["raw_description"] == [new.description.text] and f["vlm_model"] == "fake-vlm"
    assert describe.describe_added(assocs, img, vlm) == [] and vlm.calls == 1


def test_a_piece_of_an_object_is_named_by_the_smallest_mask_it_sits_inside():
    """Overlapping zones cut one object into pieces (measured on the live map: a laptop became 5
    candidates, and every piece's IoU with the laptop mask fell under LABEL_MIN_IOU, so the room
    named nothing). A piece is still that object, so an unnamed candidate takes the label of the
    smallest mask CONTAINING it — specificity, not a lower bar. The smallest matters: a mask over
    the whole table contains everything on the table and must never outbid the thing's own mask."""
    from test_segment import _masks, _render

    xyz, valid, img, lab = _render()
    book, mug = _render_candidates()[0], _render_candidates()[1]
    half = Cand((book.centroid[0], book.centroid[1] + 0.05, book.centroid[2]), (0.03, 0.05, 0.15))

    table = segment.Mask((lab >= 0), "dining table", 0.8)      # everything, as YOLO's table mask is
    got = segment.label_candidates([half, mug], img, K_RENDER, ROOM_TO_CAM,
                                   lambda im: [*_masks(lab), table])
    assert got[0][0] == "book"          # the sliver is a piece of the book, not of the table
    assert got[1][0] == "cup"           # and the mug keeps its own name

    only_table = segment.label_candidates([half], img, K_RENDER, ROOM_TO_CAM, lambda im: [table])
    assert only_table[0][0] == "dining table"   # with nothing better, the containing mask is the answer
