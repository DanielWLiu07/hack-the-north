"""room.py — read the room's git state. READ-ONLY.

room.git belongs to roomctl; this process never writes to it. Every call is
`git --no-optional-locks`: a plain `git status` refreshes the index on disk, which is a
write into someone else's repository (and a lock roomctl can trip over mid-commit).

Objects live at zones/<zone>/<object_id>.yaml. Shapes follow docs/16-api.md §4b.
"""
from __future__ import annotations

import hashlib
import math
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
GIT_TIMEOUT_S = 5

# NEVER fork: this server runs git from worker threads (asyncio.to_thread) while other threads are
# serving requests. fork() copies only the calling thread, so if any other thread happens to hold the
# malloc lock at that instant, the child deadlocks between fork and exec — inside malloc, before it can
# exec git. On macOS every process holding the listening socket gets a share of new connections, so each
# stuck child silently swallows requests (measured: the site answered about 1 in 9).
#
# CPython 3.11 takes the posix_spawn path — which never forks — only when ALL of these hold
# (Popen._execute_child): the executable has a directory in its name, preexec_fn is None, close_fds is
# FALSE, no pass_fds, cwd is None, no start_new_session, and the pipe fds are above 2. So: an absolute
# git, `-C <path>` instead of cwd, and close_fds=False.
#
# close_fds=False is safe here, and is not the same as leaking: since PEP 446 (Python 3.4) every file
# descriptor Python creates is non-inheritable, so it is closed by the kernel at exec anyway — verified
# for this server's own listening socket. close_fds=True only matters for descriptors opened by C
# extensions behind Python's back, and it is precisely what forces the fork path.
def _git_bin() -> str:
    """The REAL git. On macOS /usr/bin/git is an xcrun shim that doubles the cost of every call, and
    this server makes dozens per dashboard refresh — roomctl resolves the one behind it, so reuse that
    rather than keeping a second answer to the same question."""
    try:
        from roomctl.repo import git_bin
        return git_bin()
    except Exception:  # noqa: BLE001 — a checkout without roomctl still reads the room
        return shutil.which("git") or "/usr/bin/git"


GIT = _git_bin()
SPAWN = {"close_fds": False, "preexec_fn": None, "start_new_session": False}


class RoomError(Exception):
    """The room repository is missing, or git failed."""


def room_path() -> Path:
    raw = os.getenv("ROOM_GIT_PATH", "./room.git")
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p).resolve()


def _git(*args: str, check: bool = True) -> str:
    path = room_path()
    if not (path / ".git").exists() and not (path / "HEAD").exists():
        raise RoomError(f"no room repository at ROOM_GIT_PATH ({path.name})")
    try:
        r = subprocess.run([GIT, "--no-optional-locks", "-C", str(path), *args],   # absolute, and no cwd=: see SPAWN
                           capture_output=True, timeout=GIT_TIMEOUT_S, **SPAWN,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
    except subprocess.TimeoutExpired:
        raise RoomError(f"git {args[0]} timed out") from None
    if check and r.returncode != 0:
        raise RoomError(f"git {args[0]}: {r.stderr.decode(errors='replace').strip()[:200]}")
    return r.stdout.decode(errors="replace")


def _object_of(path: str) -> tuple[str | None, str | None]:
    """zones/<zone>/<object_id>.yaml -> (object_id, zone); anything else -> (None, None)."""
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "zones" and parts[2].endswith(".yaml"):
        return parts[2][:-5], parts[1]
    return None, None


def _record(text: str) -> dict:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _pose(doc: dict) -> dict | None:
    pose = doc.get("pose")
    if not isinstance(pose, dict):
        return None
    try:
        return {k: float(pose[k]) for k in ("x", "y", "z")} | (
            {"yaw": float(pose["yaw"])} if "yaw" in pose else {})
    except (KeyError, TypeError, ValueError):
        return None


_blob_cache: dict[tuple[str, str], str] = {}   # (rev-qualified spec) -> text; blobs are immutable


def _show(spec: str, head: str) -> str | None:
    """`git show <spec>`; cached per HEAD because `HEAD:path` / `:2:path` move with it."""
    key = (head, spec)
    if key not in _blob_cache:
        if len(_blob_cache) > 512:
            _blob_cache.clear()
        try:
            _blob_cache[key] = _git("show", spec)
        except RoomError:
            return None
    return _blob_cache[key]


def _parse_status(raw: str) -> tuple[dict, list[tuple[str, str]], list[str]]:
    """porcelain v2 -z -> (branch info, [(XY, path)] incl. untracked as '??', [unmerged paths])."""
    info: dict = {}
    entries: list[tuple[str, str]] = []
    unmerged: list[str] = []
    fields = raw.split("\0")
    i = 0
    while i < len(fields):
        f = fields[i]
        i += 1
        if not f:
            continue
        if f.startswith("# branch.oid "):
            info["oid"] = f[13:]
        elif f.startswith("# branch.head "):
            info["head"] = f[14:]
        elif f[0] == "1":
            xy, path = f.split(" ", 8)[1], f.split(" ", 8)[8]
            entries.append((xy, path))
        elif f[0] == "2":                     # rename: the ORIGINAL path is the next NUL field
            xy, path = f.split(" ", 9)[1], f.split(" ", 9)[9]
            entries.append((xy, path))
            i += 1
        elif f[0] == "u":
            unmerged.append(f.split(" ", 10)[10])
        elif f[0] == "?":
            entries.append(("??", f[2:]))
    return info, entries, unmerged


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def snapshot() -> dict:
    """Everything the dashboard needs about the working tree, in one read.

    Returns the /api/status body plus `conflicts`, `merging` and `rev` — a hash that
    changes whenever anything about the tree changes (so a second move of an already
    modified object is still news).
    """
    root = room_path()
    raw = _git("status", "--porcelain=v2", "--branch", "--untracked-files=all", "-z")
    info, entries, unmerged = _parse_status(raw)
    oid = info.get("oid", "")
    head = oid[:7] if oid and oid != "(initial)" else None
    branch = info.get("head")
    if branch == "(detached)":
        branch = None

    sig = hashlib.sha1(raw.encode())
    changes: list[dict] = []
    newest = 0.0
    for xy, path in entries:
        object_id, zone = _object_of(path)
        if xy == "??":
            kind = "untracked"
        elif "D" in xy:
            kind = "deleted"
        elif xy[0] == "A":
            kind = "added"                    # staged but never committed; not in the spec's three
        else:
            kind = "modified"
        change: dict = {"type": kind, "object_id": object_id, "zone": zone, "path": path}
        file = root / path
        if file.is_file():
            st = file.stat()
            sig.update(f"{path}:{st.st_mtime_ns}:{st.st_size}".encode())
            if object_id and kind == "modified":
                before = _pose(_record(_show(f"HEAD:{path}", oid) or ""))
                after = _pose(_record(file.read_text(errors="replace")))
                if before and after:
                    change["delta_m"] = round(math.dist(
                        (before["x"], before["y"], before["z"]),
                        (after["x"], after["y"], after["z"])), 3)
        changes.append(change)

    zones = root / "zones"
    if zones.is_dir():
        for f in zones.rglob("*.yaml"):
            newest = max(newest, f.stat().st_mtime)
    if not newest and head:
        try:
            newest = float(_git("log", "-1", "--format=%ct").strip() or 0)
        except (RoomError, ValueError):
            newest = 0.0

    conflicts = []
    for path in unmerged:
        object_id, zone = _object_of(path)
        ours = _record(_show(f":2:{path}", oid + ":merge") or "")
        theirs = _record(_show(f":3:{path}", oid + ":merge") or "")
        conflicts.append({"object_id": object_id, "zone": zone, "path": path,
                          "ours": {"zone": ours.get("zone"), "pose": _pose(ours)},
                          "theirs": {"zone": theirs.get("zone"), "pose": _pose(theirs)}})
        sig.update(path.encode())
    merging = None
    if (root / ".git" / "MERGE_HEAD").exists():
        name = _git("name-rev", "--name-only", "--refs=refs/heads/*", "MERGE_HEAD", check=False).strip()
        merging = name if name and name != "undefined" else None

    return {"branch": branch, "head": head, "clean": not changes and not conflicts,
            "changes": changes, "last_capture": _iso(newest) if newest else None,
            "conflicts": conflicts, "merging": merging, "rev": sig.hexdigest()[:12]}


def summary(snap: dict) -> dict:
    """The SSE `status` event: small, and different whenever the tree is."""
    return {"clean": snap["clean"], "changes": len(snap["changes"]), "conflicts": len(snap["conflicts"]),
            "branch": snap["branch"], "head": snap["head"], "rev": snap["rev"]}
