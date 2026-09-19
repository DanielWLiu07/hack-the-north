"""roomctl must never FORK to run git — the web server runs these calls from its worker threads.

A fork copies one thread. If another thread holds the malloc lock at that instant, the child
deadlocks between fork and exec, and on macOS it keeps its share of the server's listening
socket, so it swallows connections that never come back (measured on :8000: about one request
in nine answered). CPython takes posix_spawn instead only under the exact conditions in
Popen._execute_child; the two that are easy to lose in a later edit are an absolute git and
close_fds=False. The web's own spawn site is pinned the same way in web/tests/test_no_fork.py.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fake.scene_gen import FakeRoom  # noqa: E402
from roomctl.repo import Repo, git_bin  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    FakeRoom(tmp_path / "room.git", quiet=True).commit("clean_bench")
    return Repo(tmp_path / "room.git")


@pytest.fixture
def forks(monkeypatch):
    """Every call to the private fork_exec, which is the fork path and nothing else."""
    seen: list[str] = []
    real = subprocess._fork_exec

    def spy(args, *a, **kw):
        seen.append(" ".join(x.decode() if isinstance(x, bytes) else str(x) for x in (args or [])[:3]))
        return real(args, *a, **kw)

    monkeypatch.setattr(subprocess, "_fork_exec", spy)
    return seen


def test_the_spy_fires_on_the_settings_we_left_behind(repo, forks):
    """Without this, a green test below could mean the spy simply never works."""
    subprocess.run([git_bin(), "-C", str(repo.path), "status", "--porcelain"], capture_output=True, close_fds=True)
    assert forks, "the fork spy never fired, so the test that follows proves nothing"


def test_reading_and_writing_the_room_never_forks(repo, forks):
    repo.git("status", "--porcelain")
    repo.head()
    repo.records()
    repo.records(":")
    repo.status()
    repo.git("log", "-1", "--format=%H")
    assert forks == []
