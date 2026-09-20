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
