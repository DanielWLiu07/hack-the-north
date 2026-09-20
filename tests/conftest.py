"""No test in `tests/` may reach a live Sentry, whatever `../.env` holds today.

This file exists because they did. `tests/test_robot_capture.py` raises
`CameraUnavailable("cam2", "/dev/v4l/by-path/x did not open")` on purpose, `CaptureRig.open()`
answers that with `obs.robot_failure(...)` as it should, and those became **real issues in the
live project** — six of them, indistinguishable on the issue page from a camera that really
failed. An issue list that is partly fiction is worse than a short one.

Two things let it happen, and a per-test `monkeypatch.setenv("SENTRY_DSN", "")` stops neither:

1. **`.env` arrives at IMPORT time.** `roomctl/cli.py` and `web/server.py` call `load_dotenv()`
   while being imported, so importing either one — which several tests here do — puts the real
   DSN into `os.environ` for the whole pytest process, before most fixtures run.
2. **Blanking the variable does not un-initialise the SDK.** `obs.robot_failure` checks that
   `sentry_sdk` imported, not that a DSN is configured; once any client is active it sends. A
   fixture that edits the environment after `sentry_sdk.init()` has run changes nothing.

So: the variables are blanked HERE, at conftest import — before any test module is imported, and
therefore before any `load_dotenv()` inside one. `load_dotenv` does not override a variable that
already exists (even when it is empty), so `.env` cannot put the real DSN back. That is the same
trick `web/tests/conftest.py` uses.

And because "nothing sends" is a property worth proving rather than assuming, `no_live_sentry`
FAILS any test that runs with a client pointed at a real host — before it runs, so an earlier
test's leak is attributed to the test that leaked rather than to its victim, and after, so a test
that initialises one and walks away is caught at once.
"""
from __future__ import annotations

import os

# Before any test module — and so before any load_dotenv() inside one.
for _name in ("SENTRY_DSN", "SENTRY_DSN_PARKED", "SENTRY_DSN_WEB", "SENTRY_AUTH_TOKEN",
              "SENTRY_AUTH_TOKEN_PARKED", "SENTRY_ROBOT_TOKEN", "ELASTIC_API_KEY",
              "ELASTIC_API_KEY_PARKED", "OPENAI_API_KEY", "HOUSEBOT_EDGE_TOKEN"):
    os.environ[_name] = ""

import pytest  # noqa: E402
import sentry_sdk  # noqa: E402

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def live_sentry_host() -> str | None:
    """The host of the ACTIVE client's DSN, when it is somewhere real. None when nothing can send.

    A test may legitimately init the SDK against a fake DSN on localhost with an in-memory
    transport — that is how the robot's Sentry behaviour is tested — so the check is "can this
    reach the internet", not "is a client present"."""
    try:
        client = sentry_sdk.get_client()
        if not client.is_active():
            return None
        host = getattr(getattr(client.transport, "parsed_dsn", None), "host", None)
        if host is None:
            dsn = str(getattr(client, "dsn", "") or "")
            host = dsn.split("@", 1)[1].split("/", 1)[0].split(":", 1)[0] if "@" in dsn else None
        return None if host is None or host in LOCAL_HOSTS else host
    except Exception:  # noqa: BLE001 -- a guard must not become the failure
        return None


@pytest.fixture(autouse=True)
def no_live_sentry(request):
    """Fail loudly rather than file an issue into the project a judge is reading."""
    before = live_sentry_host()
    if before:
        pytest.fail(f"a live Sentry client ({before}) was already active before {request.node.name} — "
                    "some earlier test initialised it and did not reset it; nothing here may send", pytrace=False)
    yield
    after = live_sentry_host()
    if after:
        sentry_sdk.get_global_scope().set_client(None)      # contain it: the next test is not its fault
        pytest.fail(f"{request.node.name} left a live Sentry client active ({after}). Tests must init with a "
                    "localhost DSN and an in-memory transport, and reset the client afterwards", pytrace=False)


def pytest_report_header(config):
    return "sentry: credentials blanked in tests/conftest.py; a live client fails the test that opens it"
