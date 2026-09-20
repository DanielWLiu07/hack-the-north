"""No test in `agent/tests/` may reach the live Sentry project.

Added centrally on 2026-09-20 (coordinated, across folder boundaries) after `tests/` was found to
be filing REAL issues into the project: a test raised a camera failure on purpose, the code
answered with `obs.robot_failure()` as designed, and six of those sat in the issue list
indistinguishable from a camera that had really failed. An issue list that is partly fiction is
worse than a short one, and a judge may be reading it.

A per-test `monkeypatch.setenv("SENTRY_DSN", "")` does NOT prevent this, for two reasons:
  1. `roomctl/cli.py` and `web/server.py` call `load_dotenv()` AT IMPORT, so importing either one
     puts the real DSN into `os.environ` for the whole pytest process before most fixtures run.
  2. Blanking the variable does not un-initialise the SDK — `obs.robot_failure` checks that
     `sentry_sdk` imported, not that a DSN is configured; once a client is active, it sends.

So the variables are blanked HERE, at conftest import: before any test module, and therefore
before any `load_dotenv()` inside one. `load_dotenv` never overrides a variable that already
exists, even an empty one, so `.env` cannot put the real DSN back.

**Only the Sentry variables are touched.** Nothing here changes this suite's own credentials or
its live tests — `OPENAI_API_KEY` is untouched.
"""
from __future__ import annotations

import os

for _name in ("SENTRY_DSN", "SENTRY_DSN_PARKED", "SENTRY_DSN_WEB", "SENTRY_AUTH_TOKEN",
              "SENTRY_AUTH_TOKEN_PARKED", "SENTRY_ROBOT_TOKEN"):
    os.environ[_name] = ""

import pytest  # noqa: E402
import sentry_sdk  # noqa: E402

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def live_sentry_host() -> str | None:
    """The host of the ACTIVE client's DSN when it is somewhere real; None when nothing can send.
    A fake DSN on localhost with an in-memory transport is how Sentry behaviour is legitimately
    tested, so the question is "can this reach the internet", not "is a client present"."""
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
def _no_live_sentry(request):
    """Fail loudly rather than file an issue into the project a judge is reading."""
    before = live_sentry_host()
    if before:
        pytest.fail(f"a live Sentry client ({before}) was active before {request.node.name} — an earlier "
                    "test initialised it and did not reset it; nothing in this suite may send", pytrace=False)
    yield
    after = live_sentry_host()
    if after:
        sentry_sdk.get_global_scope().set_client(None)      # contain it: the next test is not its fault
        pytest.fail(f"{request.node.name} left a live Sentry client active ({after}). Init with a localhost "
                    "DSN and an in-memory transport, and reset the client afterwards", pytrace=False)
