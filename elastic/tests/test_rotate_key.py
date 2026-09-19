"""rotate_key.py's .env edits, on a temp file. Offline: no cluster, and the fake values below are
never real keys. The point is the order of operations and that no value is lost or echoed."""
from __future__ import annotations

import pytest

import rotate_key as R

BEFORE = """# comment
ELASTIC_URL=https://x.es.example
ELASTIC_API_KEY=old==
ELASTIC_API_KEY_NEW=new==
OTHER=keep
"""


@pytest.fixture
def env(tmp_path):
    p = tmp_path / ".env"
    p.write_text(BEFORE)
    return p


def values(p):
    lines = R.read_env(p)
    return {n: R.get(lines, n) for n in ("ELASTIC_API_KEY", "ELASTIC_API_KEY_NEW", "ELASTIC_API_KEY_OLD", "OTHER")}


def test_promote_keeps_the_old_key_for_rollback(env, capsys):
    msg = R.promote(env)
    assert values(env) == {"ELASTIC_API_KEY": "new==", "ELASTIC_API_KEY_NEW": None,
                           "ELASTIC_API_KEY_OLD": "old==", "OTHER": "keep"}
    assert "==" not in msg and "==" not in capsys.readouterr().out, "never echo a key"
    assert env.read_text().startswith("# comment\nELASTIC_URL=")  # everything else untouched


def test_rollback_restores_the_old_key(env):
    R.promote(env)
    R.rollback(env)
    assert values(env)["ELASTIC_API_KEY"] == "old==" and values(env)["ELASTIC_API_KEY_OLD"] is None


def test_promote_refuses_without_a_new_key(env):
    env.write_text(BEFORE.replace("ELASTIC_API_KEY_NEW=new==\n", "ELASTIC_API_KEY_NEW=   # paste here\n"))
    with pytest.raises(SystemExit, match="mint the key"):
        R.promote(env)
    assert values(env)["ELASTIC_API_KEY"] == "old==", "nothing changed"


def test_promote_refuses_the_same_key(env):
    env.write_text(BEFORE.replace("new==", "old=="))
    with pytest.raises(SystemExit, match="already live"):
        R.promote(env)
