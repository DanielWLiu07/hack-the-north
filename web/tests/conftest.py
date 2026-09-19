"""Two rules for every test in web/: no live API, and no live room.

1. CREDENTIALS. server.py loads ../.env WITHOUT override, so a variable that already exists (even
   empty) wins. Blanking the credentials here, before any test module imports `server`, puts
   Elasticsearch into `elastic_unconfigured` (store.py serves fake/out/demo.ndjson), keeps Sentry
   uninitialised, and leaves nothing to send to OpenAI. Tests that need an answer mock the transport.

2. THE ROOM. ../room.git is a SHARED, LIVE repository: roomctl and the integration session commit to
   it, branch in it (`live-check`), and leave its working tree dirty for an hour at a time. Tests that
   read it directly pass or fail depending on what somebody else was doing that minute — 20 errors
   ("room.git must be clean"), then `6 == 4` commits, from the same code on the same night. So the
   graph tests run against a SNAPSHOT: a throwaway clone pinned to the four-commit demo story, found
   by commit SUBJECT (the SHAs change whenever fake/scene_gen.py regenerates the room; the story does
   not). The clone shares the story's SHAs with fake/out/demo.ndjson, so the fixture enrichment still
   lines up, and the tests' before/after fingerprint still proves graph_api never writes — on a repo
   that nobody else is writing to at the same time. The live repo is only ever READ (git clone).
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

BLANKED = ("ELASTIC_API_KEY", "ELASTIC_API_KEY_PARKED", "OPENAI_API_KEY", "SENTRY_DSN", "SENTRY_DSN_WEB",
           "SENTRY_AUTH_TOKEN", "SENTRY_AUTH_TOKEN_PARKED", "SENTRY_DSN_PARKED",
           # the understanding layer (scripts/intent_service.py or Andrew's): .env points it at a RUNNING
           # service that spends OpenAI credit. A test that wants one starts its own fake and sets these.
           "INTENT_URL", "ANDREW_INTENT_URL")
# CONTAINED to web/. A repo-root `pytest` is ONE process for every suite: blanking os.environ here for good
# turned elastic's 25 live tests into errors. So: blank while web's test modules are being IMPORTED (server.py
# reads the environment at import), put the real values back when collection ends, and blank again around each
# web test with the fixture below. Nobody else's tests ever run with web's blanks.
_REAL = {name: os.environ.get(name) for name in (*BLANKED, "ROOM_GIT_PATH")}
for name in BLANKED:
    os.environ[name] = ""

REPO_ROOT = Path(__file__).resolve().parents[2]
STORY_MAIN = "afternoon: mug moved"          # the tip of main in the demo story (fake/scene_gen.py --demo)
STORY_BRANCH = ("movie-night", "movie night:")
ROOM_TESTS = ("test_graph_api.py", "test_graph_previews.py")


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *args], capture_output=True, text=True,
                       env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}, timeout=30)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args[:2])}: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def _live_room() -> Path:
    raw = os.getenv("ROOM_GIT_PATH")
    if not raw:
        try:
            from dotenv import dotenv_values
            raw = dotenv_values(REPO_ROOT / ".env").get("ROOM_GIT_PATH")
        except ImportError:
            raw = None
    p = Path(raw or "./room.git").expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p).resolve()


def _snapshot() -> Path:
    live = _live_room()
    if not (live / ".git").exists() and not (live / "HEAD").exists():
        raise RuntimeError(f"no room repository at {live}")
    log = [line.split("\t", 1) for line in _git(live, "log", "--all", "--date-order", "--format=%H%x09%s").splitlines()]
    tip = next((sha for sha, subject in log if subject.startswith(STORY_MAIN)), None)
    side = next((sha for sha, subject in log if subject.startswith(STORY_BRANCH[1])), None)
    if not tip or not side:
        raise RuntimeError(f"the demo story is not in {live.name}: no commit titled '{STORY_MAIN}…' / '{STORY_BRANCH[1]}…'")
    home = Path(tempfile.mkdtemp(prefix="web-room-"))
    atexit.register(shutil.rmtree, home, ignore_errors=True)
    clone = home / "room.git"
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout", str(live), str(clone)],
                   check=True, capture_output=True, timeout=60, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    _git(clone, "checkout", "--quiet", "-B", "main", tip)
    _git(clone, "branch", "--force", STORY_BRANCH[0], side)
    _git(clone, "remote", "remove", "origin")
    for ref in _git(clone, "for-each-ref", "--format=%(refname)").splitlines():      # exactly the story, nothing else
        if ref not in ("refs/heads/main", f"refs/heads/{STORY_BRANCH[0]}"):
            _git(clone, "update-ref", "-d", ref)
    return clone


# jobs.py persists its ledger (JOBS_DIR): tests keep theirs in a throwaway directory, never ~/.cache/gitspace/jobs
JOBS_HOME = tempfile.mkdtemp(prefix="web-jobs-")
atexit.register(shutil.rmtree, JOBS_HOME, ignore_errors=True)

STORY_ERROR: str | None = None
STORY_ROOM: str | None = None
try:                                          # at import: the test modules read room.room_path() when THEY import
    STORY_ROOM = os.environ["ROOM_GIT_PATH"] = str(_snapshot())
except Exception as e:  # noqa: BLE001 — no room, no story: those tests are skipped with the reason, not errored
    STORY_ERROR = f"{type(e).__name__}: {e}"


def _put_back() -> None:
    for name, value in _REAL.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def pytest_collection_finish(session):
    _put_back()                                   # every web test module is imported by now


@pytest.fixture(autouse=True)
def _web_tests_touch_nothing_live(monkeypatch):
    """Around each test in web/tests only: no credentials, and the snapshot of the room (see the docstring)."""
    for name in BLANKED:
        monkeypatch.setenv(name, "")
    if STORY_ROOM:
        monkeypatch.setenv("ROOM_GIT_PATH", STORY_ROOM)
    monkeypatch.setenv("JOBS_DIR", JOBS_HOME)
    monkeypatch.setenv("ROOM_CLEAN_STATE", os.path.join(JOBS_HOME, "room-clean.json"))   # never this Mac's real badge
    monkeypatch.setenv("ROOM_STATE_FILE", os.path.join(JOBS_HOME, "room-state.json"))    # nor the watch loop's last verdict
    yield


def pytest_collection_modifyitems(items):
    if STORY_ERROR:
        skip = pytest.mark.skip(reason=f"no snapshot of the room story — {STORY_ERROR}")
        for item in items:
            if Path(str(item.fspath)).name in ROOM_TESTS:
                item.add_marker(skip)
