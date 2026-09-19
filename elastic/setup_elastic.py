#!/usr/bin/env python3
"""Create every Elasticsearch resource GITSPACE needs. Idempotent: run it as often as you like.

    python setup_elastic.py                          # create what's missing, leave the rest alone
    python setup_elastic.py --check                  # validate mappings/*.json offline, no network
    python setup_elastic.py --recreate room-objects  # DELETE that index and its data, then recreate
    python setup_elastic.py --recreate all           # every index + data stream (not inference)
    python setup_elastic.py --recreate jina-embed    # replace an inference endpoint (EIS -> jinaai)

Order matters: inference endpoints first (semantic_text mappings reference them), then the
snapshot indices, then the index templates and the data streams they back. Mapping bodies
live in mappings/<name>.json as verbatim REST bodies (docs/13-ingest.md).

Re-running is safe: existing indices get an additive put_mapping, templates are re-put, and
nothing is ever deleted without --recreate. A type change can't be applied in place
(mappings are immutable) and is reported with the --recreate command that fixes it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from elasticsearch import ApiError, BadRequestError, Elasticsearch, NotFoundError, TransportError

HERE = Path(__file__).resolve().parent
MAPPINGS = HERE / "mappings"
PIPELINES = HERE / "pipelines"  # ingest pipelines an index names as its index.default_pipeline
load_dotenv(HERE.parent / ".env")  # real env vars win over the file

EMBED_ID = os.getenv("ES_INFERENCE_EMBED", "jina-embed")
RERANK_ID = os.getenv("ES_INFERENCE_RERANK", "jina-rerank")

# Snapshot-per-commit state: explicit _id, overwritten on re-ingest.
INDICES = ["room-objects", "room-voxels", "room-clouds"]
# Append-only: an index template, and the data stream it backs.
DATA_STREAMS = ["room-observations", "robot-telemetry", "room-events"]
TSDS_LOOK_BACK = "7d"  # the max ES allows; anything less and replayed sessions silently drop
# obs.trace_fields() output: every doc links back to the Sentry waterfall that wrote it.
TRACE_FIELDS = ("sentry_trace_id", "sentry_span_id", "sentry_url")
# Data stream lifecycle refuses finer downsampling than this (DataStreamLifecycle.DownsamplingRound).
MIN_DOWNSAMPLE_S = 300


class SetupError(Exception):
    pass


class CredentialsError(SetupError):
    """No usable ELASTIC_URL / key: unset, parked, or a URL where a key goes. Raised before any
    network call. The live tests skip on this; any other SetupError is a real failure."""


# ── inference ────────────────────────────────────────────────────────────────

def inference_endpoints() -> list[tuple[str, str, dict]]:
    """EIS (service "elastic") serves Jina with no extra key and is the default. Going direct to
    Jina's API is an explicit choice, ES_INFERENCE_SERVICE=jinaai, not a side effect of a
    JINA_API_KEY that happens to be in .env."""
    service = os.getenv("ES_INFERENCE_SERVICE", "elastic")
    if service not in ("elastic", "jinaai"):
        raise SetupError(f"ES_INFERENCE_SERVICE={service!r}: expected 'elastic' or 'jinaai'")

    def config(model: str) -> dict:
        if service == "jinaai":
            key = os.getenv("JINA_API_KEY")
            if not key:
                raise SetupError("ES_INFERENCE_SERVICE=jinaai needs JINA_API_KEY")
            return {"service": "jinaai", "service_settings": {"api_key": key, "model_id": model}}
        return {"service": "elastic", "service_settings": {"model_id": model}}

    return [
        ("text_embedding", EMBED_ID, config(os.getenv("ES_EMBED_MODEL", "jina-embeddings-v3"))),
        ("rerank", RERANK_ID, config(os.getenv("ES_RERANK_MODEL", "jina-reranker-v2-base-multilingual"))),
    ]


def describe(config: dict) -> str:
    return f"{config['service']}/{config['service_settings'].get('model_id')}"


def ensure_inference(es: Elasticsearch, task: str, iid: str, config: dict, recreate: bool) -> str:
    try:
        existing = es.inference.get(task_type=task, inference_id=iid)["endpoints"][0]
    except NotFoundError:
        existing = None

    if existing and recreate:
        # force: semantic_text fields still reference it; they pick up the new one by id
        es.inference.delete(task_type=task, inference_id=iid, force=True)
        existing = None

    if existing:
        if describe(existing) != describe(config):
            return (f"exists as {describe(existing)}, config wants {describe(config)} "
                    f"-- run --recreate {iid} to switch")
        return "exists"

    try:
        es.options(request_timeout=120).inference.put(
            task_type=task, inference_id=iid, inference_config=config)
    except ApiError as e:
        hint = ("or set ES_INFERENCE_SERVICE=jinaai + JINA_API_KEY to use Jina directly"
                if config["service"] == "elastic" else "check JINA_API_KEY")
        raise SetupError(f"{iid}: {reason(e)} ({hint})") from e
    return "created"


def smoke_test(es: Elasticsearch) -> str:
    """One embed + one rerank: proves EIS/Jina actually answers, not just that the ids exist."""
    emb = es.inference.inference(task_type="text_embedding", inference_id=EMBED_ID,
                                 input=["ceramic cup"])
    dims = len(emb["text_embedding"][0]["embedding"])
    docs = ["soldering iron", "ceramic cup"]
    ranked = es.inference.inference(task_type="rerank", inference_id=RERANK_ID,
                                    query="mug", input=docs)["rerank"]
    best = docs[max(ranked, key=lambda r: r["relevance_score"])["index"]]
    return f"embed -> {dims} dims · rerank('mug') -> {best!r}"


# ── indices and data streams ─────────────────────────────────────────────────

def resolve(es: Elasticsearch, name: str) -> str | None:
    """What currently owns this name: 'index', 'data stream', 'alias', or None."""
    try:
        r = es.indices.resolve_index(name=name, expand_wildcards="all")
    except NotFoundError:
        return None
    if r["data_streams"]:
        return "data stream"
    if r["indices"]:
        return "index"
    if r["aliases"]:
        return "alias"
    return None


def delete(es: Elasticsearch, name: str, kind: str | None) -> None:
    if kind == "data stream":
        es.indices.delete_data_stream(name=name)
    elif kind == "index":
        es.indices.delete(index=name)
    elif kind is not None:
        raise SetupError(f"{name} is an {kind}; refusing to delete it -- remove it by hand")


def put_mapping(es: Elasticsearch, name: str, mappings: dict) -> list[str]:
    """Additive mapping update. Returns the top-level fields that are new."""
    live = es.indices.get_mapping(index=name)
    have: set[str] = set()
    for idx in live.values():  # one entry per backing index for a data stream
        have |= idx["mappings"].get("properties", {}).keys()
    try:
        es.indices.put_mapping(index=name, body=mappings)
    except BadRequestError as e:
        raise SetupError(f"{name}: live mapping conflicts with mappings/{name}.json -- "
                         f"{reason(e)}. Mappings are immutable: --recreate {name}") from e
    return sorted(mappings.get("properties", {}).keys() - have)


def load_pipelines() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in sorted(PIPELINES.glob("*.json"))}


def ensure_pipeline(es: Elasticsearch, pid: str, body: dict) -> str:
    """PUT is idempotent; put it before any index that names it as its default pipeline."""
    try:
        es.ingest.get_pipeline(id=pid)
        existed = True
    except NotFoundError:
        existed = False
    es.ingest.put_pipeline(id=pid, body=body)
    return "updated" if existed else "created"


def ensure_index(es: Elasticsearch, name: str, body: dict, recreate: bool) -> str:
    kind = resolve(es, name)
    deleted = bool(kind and recreate)
    if deleted:
        delete(es, name, kind)
        kind = None
    if kind is None:
        es.indices.create(index=name, body=body)
        return "recreated" if deleted else "created"
    if kind != "index":
        raise SetupError(f"{name} exists as a {kind}, expected a plain index -- --recreate {name}")
    added = put_mapping(es, name, body["mappings"])
    if body.get("settings"):  # dynamic ones (index.default_pipeline) apply in place; static ones raise
        es.indices.put_settings(index=name, settings=body["settings"])
    return f"exists, added {', '.join(added)}" if added else "exists, mapping in sync"


def stream_drift(es: Elasticsearch, name: str, body: dict) -> list[str]:
    """Settings that only take effect when a stream is born. Re-putting the template can't fix
    them on an existing stream, so say so instead of letting ingest fail silently later."""
    want = flatten(body["template"].get("settings", {}))
    stream = es.indices.get_data_stream(name=name)["data_streams"][0]
    problems = []
    want_mode = want.get("index.mode", "standard")
    if "index_mode" in stream and stream["index_mode"] != want_mode:
        problems.append(f"index_mode is {stream['index_mode']}, template says {want_mode}")
    if "index.look_back_time" in want:
        first = stream["indices"][0]["index_name"]  # look_back_time only shapes the first one
        s = es.indices.get_settings(index=first, name="index.look_back_time",
                                    flat_settings=True, include_defaults=True)[first]
        have = s.get("settings", {}).get("index.look_back_time") or \
            s.get("defaults", {}).get("index.look_back_time")
        if have != want["index.look_back_time"]:
            problems.append(f"look_back_time is {have}, template says {want['index.look_back_time']}")
    return problems


def ensure_data_stream(es: Elasticsearch, name: str, body: dict, recreate: bool) -> str:
    kind = resolve(es, name)
    deleted = bool(kind and recreate)
    if deleted:
        delete(es, name, kind)
        kind = None
    if kind not in (None, "data stream"):
        raise SetupError(f"{name} exists as a plain {kind}: something wrote to it before this "
                         f"script ran, so dynamic mapping guessed every type -- --recreate {name}")

    had_template = es.indices.exists_index_template(name=name)
    es.indices.put_index_template(name=name, body=body)
    template = "template updated" if had_template else "template created"

    if kind is None:
        # Created eagerly so ES|QL `FROM <name>` works before the first document arrives.
        es.indices.create_data_stream(name=name)
        return f"{template}, stream {'recreated' if deleted else 'created'}"

    added = put_mapping(es, name, body["template"]["mappings"])
    if "lifecycle" in body["template"]:
        # The template's lifecycle only reaches streams created after it; push it to this one.
        es.indices.put_data_lifecycle(name=name, body=body["template"]["lifecycle"])
    drift = stream_drift(es, name, body)
    if drift:
        raise SetupError(f"{name}: {'; '.join(drift)}. Only a new stream picks that up -- "
                         f"--recreate {name}")
    return f"{template}, stream exists" + (f", added {', '.join(added)}" if added else "")


# ── mapping files ────────────────────────────────────────────────────────────

def flatten(settings: dict, prefix: str = "") -> dict:
    """{"index": {"mode": "x"}}, {"index.mode": "x"} and {"mode": "x"} all -> {"index.mode": "x"}
    (ES accepts all three spellings)."""
    out = {}
    for k, v in settings.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, f"{key}."))
        else:
            out[key if key.startswith("index.") else f"index.{key}"] = v
    return out


def seconds(duration: str) -> float:
    m = re.fullmatch(r"(\d+)(ms|s|m|h|d)", duration)
    if not m:
        raise ValueError(f"bad duration {duration!r}")
    return int(m[1]) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}[m[2]]


def lifecycle_problems(name: str, lifecycle: dict, tsds: bool) -> list[str]:
    """The rules ES applies to downsampling rounds, checked before anything is sent."""
    rounds = lifecycle.get("downsampling") or []
    problems = []
    if rounds and not tsds:
        problems.append(f"{name}: downsampling needs index.mode=time_series")
    if len(rounds) > 10:
        problems.append(f"{name}: at most 10 downsampling rounds, found {len(rounds)}")
    prev = None
    for r in rounds:
        try:
            after, every = seconds(r["after"]), seconds(r["fixed_interval"])
        except (KeyError, ValueError) as e:
            problems.append(f"{name}: downsampling round {r}: {e}")
            continue
        if every < MIN_DOWNSAMPLE_S:
            problems.append(f"{name}: downsampling fixed_interval {r['fixed_interval']} is under 5m, "
                            f"the data stream lifecycle minimum")
        if prev:
            if after < prev[0]:
                problems.append(f"{name}: downsampling after {r['after']} is before {prev[2]['after']}")
            if every <= prev[1] or every % prev[1]:
                problems.append(f"{name}: fixed_interval {r['fixed_interval']} must be a coarser "
                                f"multiple of {prev[2]['fixed_interval']}")
        prev = (after, every, r)
    return problems


def fields(properties: dict, prefix: str = ""):
    """Yield (dotted_path, spec) for every field, including object and multi-field children."""
    for name, spec in properties.items():
        path = f"{prefix}{name}"
        yield path, spec
        yield from fields(spec.get("properties", {}), f"{path}.")
        yield from fields(spec.get("fields", {}), f"{path}.")


def load_specs() -> dict[str, dict]:
    specs = {}
    for name in INDICES + DATA_STREAMS:
        path = MAPPINGS / f"{name}.json"
        try:
            specs[name] = json.loads(path.read_text())
        except FileNotFoundError:
            raise SetupError(f"missing mappings/{path.name}")
        except json.JSONDecodeError as e:
            raise SetupError(f"mappings/{path.name}: invalid JSON -- {e}")
    return specs


def validate(specs: dict[str, dict]) -> list[str]:
    """The mistakes that cost an hour of delete-and-reindex, caught before any write."""
    problems = []
    for name, body in specs.items():
        is_stream = name in DATA_STREAMS
        if is_stream and ("index_patterns" not in body or "data_stream" not in body):
            problems.append(f"{name}: data stream template needs index_patterns and data_stream")
            continue
        if not is_stream and "index_patterns" in body:
            problems.append(f"{name}: is a template, but {name} should be a plain index")
            continue
        inner = body["template"] if is_stream else body
        settings = flatten(inner.get("settings", {}))
        props = inner.get("mappings", {}).get("properties", {})
        if not props:
            problems.append(f"{name}: no mappings.properties")
        for f in TRACE_FIELDS:
            if props.get(f, {}).get("type") != "keyword":
                problems.append(f"{name}: {f} must be mapped as keyword (the Sentry join)")

        pipeline = settings.get("index.default_pipeline")
        if pipeline and not (PIPELINES / f"{pipeline}.json").exists():
            problems.append(f"{name}: index.default_pipeline {pipeline!r} has no pipelines/{pipeline}.json")
        for key in ("index.number_of_shards", "index.number_of_replicas"):
            if key in settings:
                problems.append(f"{name}: {key} is rejected on Serverless")
        if settings.get("index.mode") == "time_series":
            if settings.get("index.look_back_time") != TSDS_LOOK_BACK:
                problems.append(f"{name}: TSDS needs index.look_back_time={TSDS_LOOK_BACK}")
            if not any(s.get("time_series_dimension") for _, s in fields(props)):
                problems.append(f"{name}: TSDS with no time_series_dimension field")
        if is_stream and "lifecycle" in inner:
            problems += lifecycle_problems(name, inner["lifecycle"],
                                           settings.get("index.mode") == "time_series")
        if is_stream and props.get("@timestamp", {}).get("type") not in ("date", "date_nanos"):
            problems.append(f"{name}: data streams need @timestamp mapped as date")

        for path, spec in fields(props):
            leaf, kind = path.rsplit(".", 1)[-1], spec.get("type")
            if kind in ("geo_point", "geo_shape"):
                problems.append(f"{name}.{path}: {kind} -- the room is cartesian, use point/shape")
            if leaf == "cell" and kind != "point":
                problems.append(f"{name}.{path}: must be type point, got {kind}")
            if leaf.startswith("voxel_key") and kind != "keyword":
                problems.append(f"{name}.{path}: must be keyword, got {kind}")
            if kind == "semantic_text" and spec.get("inference_id") != EMBED_ID:
                problems.append(f"{name}.{path}: inference_id {spec.get('inference_id')!r} "
                                f"!= ES_INFERENCE_EMBED {EMBED_ID!r}")
            if kind == "semantic_text" and not any(
                    s.get("type") == "text" for s in spec.get("fields", {}).values()):
                # a match on semantic_text is rewritten to a semantic query: without a text
                # sub-field, "BM25 missed it" would only mean BM25 never looked
                problems.append(f"{name}.{path}: semantic_text needs a text sub-field for BM25")
    return problems


# ── main ─────────────────────────────────────────────────────────────────────

def reason(e: ApiError) -> str:
    try:
        err = e.body["error"]
        root = (err.get("root_cause") or [err])[0]
        return f"{root.get('type')}: {root.get('reason')}"
    except (TypeError, KeyError, IndexError, AttributeError):
        return str(e)


def env(name: str, source=None) -> str:
    """os.getenv (or `source`, any mapping), except that `KEY=    # comment` counts as unset:
    python-dotenv returns the comment text as the value when nothing precedes it (how keys get
    parked in .env)."""
    value = ((os.environ if source is None else source).get(name) or "").strip()
    return "" if value.startswith("#") else value


def connect(admin: bool = False, source=None) -> Elasticsearch:
    """A checked client, or SetupError -- raised BEFORE any network call when the credentials
    are missing, parked or malformed.

    Two keys, least privilege: ELASTIC_API_KEY is the runtime key every service uses (read +
    write documents on the six indices, run inference -- elastic/ROTATION.md). admin=True is for
    what creates and deletes indices, templates and endpoints (this script, the live test
    fixtures) and uses ELASTIC_ADMIN_API_KEY when it is set, the runtime key otherwise.
    `source` (a mapping) replaces os.environ -- the live tests pass one built from the repo-root
    .env, because a whole-repo pytest shares one process with suites that blank these vars."""
    url = env("ELASTIC_URL", source)
    key = (admin and env("ELASTIC_ADMIN_API_KEY", source)) or env("ELASTIC_API_KEY", source)
    if not key and env("ELASTIC_API_KEY_PARKED", source):
        raise CredentialsError("ELASTIC_API_KEY is parked (ELASTIC_API_KEY_PARKED is set): Elasticsearch "
                               "calls are paused on purpose -- nothing was sent")
    if not url or not key:
        raise CredentialsError(f"ELASTIC_URL and ELASTIC_API_KEY must be set ({HERE.parent / '.env'})")
    if key.startswith(("http://", "https://")):
        raise CredentialsError("ELASTIC_API_KEY is a URL, not an API key. Create one in Kibana -> "
                               "Stack Management -> API keys and paste the 'Encoded' value")
    # Retrying a timed-out request is safe for every write here: snapshot docs carry a natural
    # _id and TSDS/commit docs 409 on a repeat. (A non-commit room-event could double.)
    es = Elasticsearch(url, api_key=key, request_timeout=30, retry_on_timeout=True, max_retries=3)
    try:
        info = es.info()
    except (ApiError, TransportError) as e:
        raise SetupError(f"cannot reach {url}: {e}") from e
    flavor = info["version"].get("build_flavor", "default")
    print(f"elasticsearch  {url}  ({flavor} {info['version']['number']})")
    return es


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="validate mapping files and exit, no network")
    ap.add_argument("--recreate", nargs="+", default=[], metavar="NAME",
                    help="delete and recreate these (index, data stream, inference id, or 'all'). "
                         "Deletes their data.")
    args = ap.parse_args()

    try:
        endpoints = inference_endpoints()
    except SetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    known = set(INDICES + DATA_STREAMS) | {iid for _, iid, _ in endpoints} | {"all"}
    unknown = set(args.recreate) - known
    if unknown:
        ap.error(f"--recreate: unknown {', '.join(sorted(unknown))} (choose from {', '.join(sorted(known))})")
    recreate = set(args.recreate)
    if "all" in recreate:
        recreate |= set(INDICES + DATA_STREAMS)

    try:
        specs = load_specs()
        problems = validate(specs)
        if problems:
            print("mappings/ has problems -- nothing was sent:", *problems, sep="\n  ")
            return 1
        print(f"mappings       {len(specs)} files valid")
        if args.check:
            return 0
        es = connect(admin=True)
    except SetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    failures = 0

    def step(label: str, fn, *a) -> None:
        nonlocal failures
        try:
            print(f"  {label:<19} {fn(*a)}")
        except (SetupError, ApiError, TransportError) as e:
            failures += 1
            msg = reason(e) if isinstance(e, ApiError) else e
            print(f"  {label:<19} FAILED  {msg}")

    print("inference")
    for task, iid, config in endpoints:
        step(iid, ensure_inference, es, task, iid, config, iid in recreate)
    if not failures:
        step("smoke test", smoke_test, es)

    print("pipelines")
    for pid, body in load_pipelines().items():
        step(pid, ensure_pipeline, es, pid, body)

    print("indices")
    for name in INDICES:
        step(name, ensure_index, es, name, specs[name], name in recreate)

    print("data streams")
    for name in DATA_STREAMS:
        step(name, ensure_data_stream, es, name, specs[name], name in recreate)

    print(f"\n{'FAILED: ' + str(failures) + ' step(s)' if failures else 'ok -- safe to run again'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
