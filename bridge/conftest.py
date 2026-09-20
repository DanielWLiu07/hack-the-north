"""No bridge test may reach a real understanding service.

`INTENT_URL` takes precedence over `ANDREW_INTENT_URL`, and a developer's .env points the
first one at a live OpenAI-backed service. A test that starts its own fake and sets the
second name was therefore asking the REAL one: it spent credit, it made the suite depend on
a process being up, and it failed with the live service's answer instead of the fixture's.
web/tests/conftest.py blanks both for the same reason. A test that wants a service starts
one and sets the variable itself.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_live_intent_service(monkeypatch):
    monkeypatch.delenv("INTENT_URL", raising=False)
    monkeypatch.delenv("ANDREW_INTENT_URL", raising=False)


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
