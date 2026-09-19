"""Live-cluster fixtures. Nothing here touches the real indices: every test resource is built
from the real mappings/*.json under a `test-` prefix, loaded with tests/world.py, and deleted
afterwards.

    cd elastic && .venv/bin/python -m pytest tests -v
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ELASTIC = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ELASTIC), str(ELASTIC.parent)]  # elastic/ modules, and roomctl/perception for records.py

import ingest  # noqa: E402
import setup_elastic as S  # noqa: E402
from queries import Queries  # noqa: E402
from world import World  # noqa: E402

PREFIX = "test-"


@pytest.fixture(scope="session")
def es():
    try:
        return S.connect()
    except S.SetupError as e:
        reason = str(e)
    pytest.fail(f"live cluster unavailable: {reason}", pytrace=False)


def _teardown(es) -> None:
    for name in S.DATA_STREAMS:
        es.options(ignore_status=404).indices.delete_data_stream(name=PREFIX + name)
        es.options(ignore_status=404).indices.delete_index_template(name=PREFIX + name)
    for name in S.INDICES:
        es.options(ignore_status=404).indices.delete(index=PREFIX + name)


@pytest.fixture(scope="session")
def world(es):
    """The fixture room, indexed into test- copies of all six indices. Yields (World, Queries)."""
    _teardown(es)  # a previous run that died mid-way
    for task, iid, config in S.inference_endpoints():
        S.ensure_inference(es, task, iid, config, recreate=False)
    specs = S.load_specs()
    for name in S.INDICES:
        S.ensure_index(es, PREFIX + name, specs[name], recreate=True)
    for name in S.DATA_STREAMS:
        body = copy.deepcopy(specs[name])
        body["index_patterns"] = [f"{PREFIX}{name}*"]
        S.ensure_data_stream(es, PREFIX + name, body, recreate=True)

    w = World()
    actions = []
    for index, docs in w.docs.items():
        for doc in docs:
            act = ingest.action(index, dict(doc))
            act["_index"] = PREFIX + index
            actions.append(act)
    tally = ingest.write(es.options(request_timeout=300), actions)
    errors = {i: errs for i, (_, _, errs) in tally.items() if errs}
    if errors:
        _teardown(es)
        pytest.fail(f"fixture ingest failed: {errors}", pytrace=False)

    yield w, Queries(es, prefix=PREFIX)
    _teardown(es)
