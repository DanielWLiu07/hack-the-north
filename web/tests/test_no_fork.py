"""The server must never fork to run git.

A forked child is copied from ONE thread. This server runs git from worker threads while other
threads serve requests, so if another thread holds the malloc lock at that instant the child
deadlocks between fork and exec — and on macOS it still holds a share of the listening socket, so
it swallows requests that never come back. Measured on :8000: about one request in nine answered.

CPython takes the posix_spawn path (no fork at all) only under the exact conditions in
`Popen._execute_child`. These tests pin the two that are easy to lose in a later edit — an
absolute executable and close_fds=False — by watching the private `_fork_exec` itself, so they
fail if the path changes for ANY reason, including a CPython upgrade that moves the goalposts.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import room  # noqa: E402


@pytest.fixture()
def forks(monkeypatch):
    """Records every call that would fork, and lets it happen (so the test still gets its answer)."""
    seen = []
    real = subprocess._fork_exec                                       # noqa: SLF001

    def spy(*a, **kw):
        seen.append(a[0] if a else None)
        return real(*a, **kw)

    monkeypatch.setattr(subprocess, "_fork_exec", spy)
    return seen


def test_reading_the_room_never_forks(forks):
    # the spy works: the SAME command with close_fds=True (the old settings) does fork
    subprocess.run([room.GIT, "--version"], capture_output=True, close_fds=True)
    assert forks, "the fork spy never fired, so this test could not fail"
    forks.clear()

    assert room.snapshot()["branch"], "sanity: git answered"
    room._git("log", "-1", "--format=%H")                              # noqa: SLF001
    assert forks == [], f"git was run by forking: {forks}"


def test_the_scene_endpoints_the_room_page_polls_never_fork(forks):
    """/robot polls /api/scene/instances and /api/scene/{i}/captures every 5 s, and both are plain `def`
    endpoints, so FastAPI runs them in a worker thread. One open tab must not mean a fork every 5 seconds."""
    import scene_api
    forks.clear()
    scene_api.instances()                                              # git under the hood, per room
    for name in ("does-not-exist", "sim-demo"):
        try:
            scene_api.captures(name)
        except Exception:                                              # noqa: BLE001 — a 404 is fine; a fork is not
            pass
    assert forks == [], f"the scene endpoints forked: {forks}"
    assert scene_api.GIT is room.GIT and scene_api.SPAWN is room.SPAWN, "one definition, not two"


def test_every_condition_for_posix_spawn_still_holds():
    import os
    assert os.path.dirname(room.GIT), "the executable needs a directory in its name, or CPython forks"
    assert os.access(room.GIT, os.X_OK), f"{room.GIT} is not executable"
    assert room.SPAWN["close_fds"] is False, "close_fds=True is exactly what forces the fork path"
    assert room.SPAWN["preexec_fn"] is None and room.SPAWN["start_new_session"] is False
    assert subprocess._USE_POSIX_SPAWN, "this platform cannot posix_spawn at all"   # noqa: SLF001


def test_close_fds_false_does_not_leak_the_servers_socket():
    """Why close_fds=False is safe: PEP 446 makes every descriptor Python opens non-inheritable, so
    the kernel closes it at exec anyway. The one thing close_fds=True would add is descriptors opened
    behind Python's back by a C extension — and it costs the fork."""
    import os
    import socket
    import stat
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    dup = os.dup(srv.fileno())                                         # a dup is non-inheritable too
    try:
        assert srv.get_inheritable() is False and os.get_inheritable(dup) is False
        probe = ("import os, stat, sys\n"
                 "def shown(fd):\n"
                 "    try: return 'socket' if stat.S_ISSOCK(os.fstat(int(fd)).st_mode) else 'other'\n"
                 "    except OSError: return 'closed'\n"
                 "print(','.join(shown(f) for f in sys.argv[1:]))\n")
        r = subprocess.run([sys.executable, "-c", probe, str(srv.fileno()), str(dup)],
                           capture_output=True, text=True, **room.SPAWN)
        assert r.stdout.strip() == "closed,closed", f"a descriptor survived exec: {r.stdout!r}"
    finally:
        os.close(dup)
        srv.close()
