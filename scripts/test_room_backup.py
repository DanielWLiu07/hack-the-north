"""room_backup.sh against a throwaway repo and a local bare remote — never the real ones."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "room_backup.sh"


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()


def sync(room, remote):
    env = {**os.environ, "ROOM_GIT_PATH": str(room), "ROOM_REPO": "x/y", "ROOM_REMOTE_URL": str(remote), "VERBOSE": "1"}
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, check=True).stdout


def test_sync_mirrors_and_a_rewritten_branch_never_loses_history(tmp_path):
    room, remote = tmp_path / "room.git", tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(tmp_path, "init", "-q", "-b", "main", str(room))
    git(room, "config", "user.email", "t@t"); git(room, "config", "user.name", "t")
    for i in range(3):
        (room / "mug.yaml").write_text(f"x: {i}\n"); git(room, "add", "-A"); git(room, "commit", "-qm", f"c{i}")
    git(room, "tag", "study", "HEAD~1")
    assert "synced 1 branch" in sync(room, remote)
    assert git(remote, "rev-parse", "main") == git(room, "rev-parse", "main")
    assert git(remote, "rev-parse", "study") == git(room, "rev-parse", "study")
    old_tip = git(room, "rev-parse", "main")
    git(room, "reset", "-q", "--hard", "HEAD~2")                         # `room reset --hard`
    (room / "mug.yaml").write_text("x: 9\n"); git(room, "commit", "-qam", "after reset")
    out = sync(room, remote)
    assert f"kept as backup/main/{old_tip[:7]}" in out
    assert git(remote, "rev-parse", "main") == git(room, "rev-parse", "main")   # remote follows local...
    assert git(remote, "rev-parse", f"backup/main/{old_tip[:7]}") == old_tip    # ...and nothing is lost
    assert "synced 0 branch" in sync(room, remote)                        # idempotent: nothing to push
    git(room, "branch", "-q", "scratch"); git(room, "branch", "-q", "-D", "scratch")
    git(room, "tag", "-f", "study", "HEAD")                                # a MOVED tag is never forced
    sync(room, remote)
    assert git(remote, "rev-parse", "study") != git(room, "rev-parse", "study")


def test_offline_exits_quietly(tmp_path):
    room = tmp_path / "room.git"
    git(tmp_path, "init", "-q", "-b", "main", str(room))
    out = sync(room, tmp_path / "does-not-exist.git")
    assert "offline or unreachable" in out
