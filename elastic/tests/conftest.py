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
from dotenv import dotenv_values

ELASTIC = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ELASTIC), str(ELASTIC.parent)]  # elastic/ modules, and roomctl/perception for records.py

import ingest  # noqa: E402
import setup_elastic as S  # noqa: E402
from queries import Queries  # noqa: E402
from world import World  # noqa: E402

PREFIX = "test-"


LIVE_VARS = ("ELASTIC_URL", "ELASTIC_API_KEY", "ELASTIC_ADMIN_API_KEY", "ELASTIC_API_KEY_PARKED")


def live_credentials() -> dict[str, str]:
    """The live suite's credentials, the same from any cwd and any mix of suites: a usable process
    variable wins, else the repo-root .env FILE. Not os.environ alone -- a whole-repo `pytest` is one
    process, and web/tests/conftest.py blanks ELASTIC_API_KEY at import to keep web's tests offline."""
    file = dotenv_values(ELASTIC.parent / ".env")
    return {name: S.env(name) or S.env(name, file) for name in LIVE_VARS}


@pytest.fixture(scope="session")
def es():
    try:
        return S.connect(admin=True, source=live_credentials())  # creates/deletes test- indices
    except S.CredentialsError as e:
        pytest.skip(f"live cluster: {e}")  # like the other suites' opt-in live tests


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
    for pid, body in S.load_pipelines().items():  # before any index that names it as its default
        S.ensure_pipeline(es, pid, body)
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


# ── added centrally on 2026-09-20: no test suite may reach the live Sentry project ──────────────
# `tests/` was found filing REAL issues into it — a test raised a failure on purpose, the code
# answered with obs.robot_failure() as designed, and those sat in the issue list looking like real
# ones. A per-test monkeypatch.setenv("SENTRY_DSN", "") does not stop it: roomctl/cli.py and
# web/server.py call load_dotenv() AT IMPORT (so the real DSN is in os.environ before fixtures
# run), and blanking the variable does not un-initialise an SDK that is already active. Blanking
# here, at conftest import, happens before any test module — and load_dotenv never overrides an
# existing variable, even an empty one. ONLY the Sentry variables are touched.
import os as _os

for _name in ("SENTRY_DSN", "SENTRY_DSN_PARKED", "SENTRY_DSN_WEB", "SENTRY_AUTH_TOKEN",
              "SENTRY_AUTH_TOKEN_PARKED", "SENTRY_ROBOT_TOKEN"):
    _os.environ[_name] = ""

import sentry_sdk as _sentry_sdk

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def _live_sentry_host():
    """The host of the ACTIVE client's DSN when it is somewhere real; None when nothing can send."""
    try:
        client = _sentry_sdk.get_client()
        if not client.is_active():
            return None
        host = getattr(getattr(client.transport, "parsed_dsn", None), "host", None)
        if host is None:
            dsn = str(getattr(client, "dsn", "") or "")
            host = dsn.split("@", 1)[1].split("/", 1)[0].split(":", 1)[0] if "@" in dsn else None
        return None if host is None or host in _LOCAL_HOSTS else host
    except Exception:  # noqa: BLE001 -- a guard must not become the failure
        return None


@pytest.fixture(autouse=True)
def _no_live_sentry(request):
    before = _live_sentry_host()
    if before:
        pytest.fail(f"a live Sentry client ({before}) was active before {request.node.name} — an earlier test "
                    "initialised it and did not reset it; nothing in this suite may send", pytrace=False)
    yield
    after = _live_sentry_host()
    if after:
        _sentry_sdk.get_global_scope().set_client(None)
        pytest.fail(f"{request.node.name} left a live Sentry client active ({after}). Init with a localhost DSN "
                    "and an in-memory transport, and reset the client afterwards", pytrace=False)
