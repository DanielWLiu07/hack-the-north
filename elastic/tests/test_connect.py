"""connect(): the one door to the cluster. Offline -- the client class is replaced, so a test
that reached the network would fail instead of calling out.

The failure modes that matter are the ones .env produces: a parked key (`KEY=   # PAUSED ...`,
which python-dotenv returns as the comment text), a URL pasted where a key goes, nothing set.
Each must stop BEFORE a client exists, so a paused cluster is never sent a request.
"""
from __future__ import annotations

import pytest

import setup_elastic as S

URL = "https://project.es.us-east4.gcp.elastic.cloud"


class NoNetwork:
    def __init__(self, *a, **kw):
        raise AssertionError("connect() built a client -- it would have called the cluster")


class FakeClient:
    made: list[dict] = []

    def __init__(self, url, **kw):
        FakeClient.made.append({"url": url, **kw})

    def info(self):
        return {"version": {"number": "9.6.0", "build_flavor": "serverless"}}


def use_env(monkeypatch, **values):
    for name in ("ELASTIC_URL", "ELASTIC_API_KEY", "ELASTIC_API_KEY_PARKED", "ELASTIC_ADMIN_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("env,message", [
    ({"ELASTIC_URL": URL, "ELASTIC_API_KEY": "# PAUSED until 01:00", "ELASTIC_API_KEY_PARKED": "k"}, "parked"),
    ({"ELASTIC_URL": URL, "ELASTIC_API_KEY_PARKED": "k"}, "parked"),
    ({"ELASTIC_URL": URL, "ELASTIC_API_KEY": "# just a comment"}, "must be set"),
    ({"ELASTIC_URL": "# PAUSED", "ELASTIC_API_KEY": "abc=="}, "must be set"),
    ({}, "must be set"),
    ({"ELASTIC_URL": URL, "ELASTIC_API_KEY": "https://claude.ai/artifact/x"}, "is a URL"),
], ids=["parked-comment", "parked-empty", "comment-only", "url-parked", "nothing", "url-as-key"])
def test_refuses_before_any_client_exists(monkeypatch, env, message):
    use_env(monkeypatch, **env)
    monkeypatch.setattr(S, "Elasticsearch", NoNetwork)
    with pytest.raises(S.SetupError, match=message):
        S.connect()


def test_good_credentials_get_a_retrying_client(monkeypatch, capsys):
    use_env(monkeypatch, ELASTIC_URL=URL, ELASTIC_API_KEY="abc==")
    FakeClient.made.clear()
    monkeypatch.setattr(S, "Elasticsearch", FakeClient)
    S.connect()
    (made,) = FakeClient.made
    assert made == {"url": URL, "api_key": "abc==", "request_timeout": 30,
                    "retry_on_timeout": True, "max_retries": 3}
    assert "serverless 9.6.0" in capsys.readouterr().out


@pytest.mark.parametrize("raw,read", [("# PAUSED until 01:00", ""), ("  abc==  ", "abc=="),
                                      ("", ""), ("value # not a comment", "value # not a comment")])
def test_env_treats_a_leading_hash_as_unset(monkeypatch, raw, read):
    monkeypatch.setenv("SOME_KEY", raw)
    assert S.env("SOME_KEY") == read


@pytest.mark.parametrize("admin_key,admin,used", [
    ("adm==", True, "adm=="),    # setup / fixtures take the admin key when there is one
    ("adm==", False, "run=="),   # services never do
    (None, True, "run=="),       # no admin key yet: the runtime key, exactly as before
])
def test_admin_key_only_for_admin_callers(monkeypatch, admin_key, admin, used):
    use_env(monkeypatch, ELASTIC_URL=URL, ELASTIC_API_KEY="run==", **({"ELASTIC_ADMIN_API_KEY": admin_key} if admin_key else {}))
    FakeClient.made.clear()
    monkeypatch.setattr(S, "Elasticsearch", FakeClient)
    S.connect(admin=admin)
    assert FakeClient.made[0]["api_key"] == used


def test_credential_problems_are_a_distinct_error(monkeypatch):
    use_env(monkeypatch, ELASTIC_URL=URL)
    monkeypatch.setattr(S, "Elasticsearch", NoNetwork)
    with pytest.raises(S.CredentialsError):  # the live tests skip on exactly this
        S.connect()
    assert issubclass(S.CredentialsError, S.SetupError)


def test_an_explicit_source_replaces_the_process_environment(monkeypatch):
    use_env(monkeypatch, ELASTIC_URL=URL, ELASTIC_API_KEY="")  # blanked, as web/tests/conftest.py does
    FakeClient.made.clear()
    monkeypatch.setattr(S, "Elasticsearch", FakeClient)
    S.connect(source={"ELASTIC_URL": URL, "ELASTIC_API_KEY": "from-file=="})
    assert FakeClient.made[0]["api_key"] == "from-file=="
