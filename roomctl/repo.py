"""room.git, through the real `git` binary. We never reimplement plumbing (docs/04): every
function here is a git command, or parses git's porcelain output.

room.git is a SEPARATE repository from the code. It lives at $ROOM_GIT_PATH (default
./room.git, gitignored by the code repo) and is never tracked by it.
"""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from roomctl.state import HYST, Q_POS, Q_YAW, ObjectRecord, from_yaml

ROOT = Path(__file__).resolve().parents[1]
ROBOT_NAME, ROBOT_EMAIL = "gitspace-robot", "robot@gitspace.local"  # docs/10: the robot authors
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class GitError(Exception):
    pass


@functools.cache
def git_bin() -> str:
    """On macOS /usr/bin/git is an xcrun shim that doubles the cost of every call (~29 vs
    ~14 ms). `room status` and the idempotency gate make dozens, so resolve the real one."""
    exe = os.getenv("GIT_BIN") or shutil.which("git") or "git"
    if sys.platform == "darwin" and exe == "/usr/bin/git":
        r = subprocess.run(["xcrun", "--find", "git"], capture_output=True, text=True)
        if r.returncode == 0 and Path(r.stdout.strip()).is_file():
            exe = r.stdout.strip()
    return exe


def default_path() -> Path:
    p = Path(os.getenv("ROOM_GIT_PATH", "./room.git"))
    return p if p.is_absolute() else ROOT / p


@dataclass
class Entry:
    """One line of `git status`, for one path."""
    path: str
    staged: str = " "     # index vs HEAD:  M A D R, or " "
    unstaged: str = " "   # worktree vs index: M D, or " "
    orig: str | None = None   # renames: where it came from
    untracked: bool = False
    conflict: bool = False

    @property
    def object_id(self) -> str | None:
        parts = self.path.split("/")
        return parts[2].removesuffix(".yaml") if len(parts) == 3 and parts[0] == "zones" else None

    @property
    def zone(self) -> str | None:
        return self.path.split("/")[1] if self.object_id else None


@dataclass
class Status:
    branch: str
    head: str | None
    entries: list[Entry] = field(default_factory=list)
    merging: bool = False

    @property
    def clean(self) -> bool:
        return not self.entries


@dataclass
class Commit:
    sha: str
    parent: str | None
    branch: str
    message: str
    changes: list[tuple[str, str]]  # (A|M|D|R, path) — for R, the new path


class Repo:
    def __init__(self, path: Path | str | None = None):
        path = Path(path) if path is not None else default_path()
        self.path = path.resolve()
        self._blobs: dict[str, ObjectRecord] = {}  # blob sha -> parsed record: blobs never change
        if self.path == ROOT or self.path in ROOT.parents:
            raise GitError(f"{self.path} contains the code repo — room.git must be a SEPARATE "
                           f"repository (set ROOM_GIT_PATH)")

    # -- running git ---------------------------------------------------------

    def git(self, *args: str, check: bool = True, env: dict | None = None,
            stdin: str | None = None) -> subprocess.CompletedProcess:
        r = subprocess.run([git_bin(), "-C", str(self.path), "-c", "commit.gpgsign=false", *args],
                           capture_output=True, text=True, input=stdin,
                           env={**os.environ, **(env or {})})
        if check and r.returncode:
            raise GitError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
        return r

    def passthrough(self, *args: str) -> int:
        """Run git with the terminal attached: `room diff` IS `git diff`, byte for byte."""
        self.require()
        return subprocess.run([git_bin(), "-C", str(self.path), "-c", "color.ui=auto", *args]).returncode

    # -- facts ---------------------------------------------------------------

    @property
    def exists(self) -> bool:
        return (self.path / ".git").is_dir()

    def require(self) -> None:
        if not self.exists:
            raise GitError(f"no room repository at {self.path} — run `room init`")

    def has_head(self) -> bool:
        return self.git("rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0

    def head(self) -> str | None:
        r = self.git("rev-parse", "--verify", "-q", "HEAD", check=False)
        return r.stdout.strip() or None

    def branch(self) -> str:
        return self.git("symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip() or "HEAD"

    def branch_exists(self, name: str) -> bool:
        return self.git("rev-parse", "--verify", "-q", f"refs/heads/{name}", check=False).returncode == 0

    def is_dirty(self) -> bool:
        return bool(self.git("status", "--porcelain").stdout.strip())

    def records(self, ref: str = "HEAD") -> dict[str, ObjectRecord]:
        """Every object at `ref` (a commit, or ":" for the index), by id. One git call when
        nothing changed since the last read — blobs are content-addressed, so parsed records
        are cached by blob sha."""
        if ref == ":":
            r = self.git("ls-files", "-s", "-z", "--", "zones", check=False)  # mode sha stage\tpath
            rows = [(f.split()[1], p) for f, p in (e.split("\t", 1) for e in r.stdout.split("\0") if e)
                    if f.split()[2] == "0"]
        else:
            r = self.git("ls-tree", "-r", "-z", ref, "--", "zones", check=False)  # mode type sha\tpath
            if r.returncode:
                return {}  # unborn branch, or no such ref
            rows = [(f.split()[2], p) for f, p in (e.split("\t", 1) for e in r.stdout.split("\0") if e)]
        rows = [(sha, p) for sha, p in rows if p.endswith(".yaml") and p.count("/") == 2]
        missing = [sha for sha, _ in rows if sha not in self._blobs]
        if missing:
            out = subprocess.run([git_bin(), "-C", str(self.path), "cat-file", "--batch"], capture_output=True,
                                 input="".join(f"{sha}\n" for sha in missing).encode(), check=True).stdout
            i = 0
            for sha in missing:
                nl = out.index(b"\n", i)
                size = int(out[i:nl].split()[2])
                self._blobs[sha] = from_yaml(out[nl + 1:nl + 1 + size].decode())
                i = nl + 1 + size + 1
        return {self._blobs[sha].id: self._blobs[sha] for sha, _ in rows}

    def status(self) -> Status:
        self.require()
        raw = self.git("status", "--porcelain=v2", "-z", "-uall", "--branch").stdout
        st = Status(branch="HEAD", head=None, merging=(self.path / ".git" / "MERGE_HEAD").exists())
        items = raw.split("\0")
        i = 0
        while i < len(items):
            line = items[i]
            i += 1
            if not line:
                continue
            kind = line[0]
            if kind == "#":
                key, _, val = line[2:].partition(" ")
                if key == "branch.head":
                    st.branch = val
                elif key == "branch.oid" and val != "(initial)":
                    st.head = val
            elif kind == "?":
                st.entries.append(Entry(line[2:], untracked=True))
            elif kind == "1":
                f = line.split(" ", 8)
                st.entries.append(Entry(f[8], staged=f[1][0].replace(".", " "),
                                        unstaged=f[1][1].replace(".", " ")))
            elif kind == "2":
                f = line.split(" ", 9)
                st.entries.append(Entry(f[9], staged=f[1][0].replace(".", " "),
                                        unstaged=f[1][1].replace(".", " "), orig=items[i]))
                i += 1  # -z puts the rename source in its own field
            elif kind == "u":
                f = line.split(" ", 10)
                st.entries.append(Entry(f[10], conflict=True))
        return st

    # -- writes --------------------------------------------------------------

    def commit(self, message: str, when: datetime | None = None) -> Commit | None:
        """`git commit` as the robot. If nothing is staged, stage everything first — `room
        commit` records the room as it is now. If something IS staged (`room add zones/desk/`),
        commit exactly that: spatial staging. None when there's nothing to commit."""
        self.require()
        staged = lambda: self.git("diff", "--cached", "--quiet", check=False).returncode != 0  # noqa: E731
        if not staged():
            self.git("add", "-A")
            if not staged():
                return None
        base = "HEAD" if self.has_head() else EMPTY_TREE
        changes = []
        for line in self.git("diff", "--cached", "--name-status", "-M", base).stdout.splitlines():
            status, *paths = line.split("\t")
            changes.append((status[0], paths[-1]))
        date = (when or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
        self.git("commit", "-q", "-m", message, env={
            "GIT_AUTHOR_NAME": ROBOT_NAME, "GIT_AUTHOR_EMAIL": ROBOT_EMAIL, "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_NAME": ROBOT_NAME, "GIT_COMMITTER_EMAIL": ROBOT_EMAIL,
            "GIT_COMMITTER_DATE": date})
        parent = self.git("rev-parse", "-q", "--verify", "HEAD~1", check=False).stdout.strip() or None
        return Commit(self.head(), parent, self.branch(), message, changes)

    def commit_info(self, ref: str = "HEAD") -> Commit:
        """An existing commit, read back from git: what the post-commit hook needs."""
        sha = self.git("rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()
        parent = self.git("rev-parse", "-q", "--verify", f"{sha}~1", check=False).stdout.strip() or None
        diff = self.git("diff", "--name-status", "-M", parent or EMPTY_TREE, sha).stdout.splitlines()
        changes = [(line.split("\t")[0][0], line.split("\t")[-1]) for line in diff]
        return Commit(sha, parent, self.branch(), self.git("log", "-1", "--format=%s", sha).stdout.strip(), changes)

    def switch(self, branch: str, create: bool = False) -> None:
        """Move HEAD. This does NOT move objects — callers that change reality go through the
        executor. Used by the fake (which teleports) and for the unborn branch at init."""
        if not self.has_head():
            self.git("symbolic-ref", "HEAD", f"refs/heads/{branch}")
            return
        if self.is_dirty():
            raise GitError(f"the room has uncommitted changes; commit them before switching to {branch}")
        self.git("checkout", "-q", *(["-b", branch] if create else [branch]))


# ── room init ────────────────────────────────────────────────────────────────

def init(path: Path | str | None, room: dict) -> Repo:
    """A new room.git: `git init -b main`, plus room.yaml, the anchor and .roomignore. The
    first commit is the caller's job (it needs a scan)."""
    repo = Repo(path)
    if repo.exists:
        raise GitError(f"{repo.path} is already a room repository")
    repo.path.mkdir(parents=True, exist_ok=True)
    repo.git("init", "-q", "-b", "main")
    write_room_files(repo.path, room)
    return repo


def write_room_files(root: Path, room: dict) -> None:
    """room.yaml, anchors/<tag>.yaml, .roomignore — written only if absent: pinned once."""
    files = {
        "room.yaml": room_yaml(room),
        f"anchors/{room['anchor']['id']}.yaml": anchor_yaml(room["anchor"]),
        ".roomignore": "".join(f"{p}\n" for p in room["roomignore"]),
    }
    for rel, text in files.items():
        p = Path(root) / rel
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)


def octree_cube() -> tuple[tuple[float, float, float], float, int]:
    origin = tuple(float(os.getenv(f"ROOM_ORIGIN_{a}", d)) for a, d in (("X", -4.0), ("Y", -4.0), ("Z", 0.0)))
    return origin, float(os.getenv("ROOM_CUBE_SIZE", 8.0)), int(os.getenv("OCTREE_LEVELS", 7))


def room_yaml(room: dict) -> str:
    origin, size, levels = octree_cube()
    lines = [
        "# The room's constants. Pin once: every voxel key in Elasticsearch is relative",
        "# to this cube, so changing it silently invalidates all history.",
        "units: metres",
        "frame: world  # X forward from the anchor tag, Y left, Z up, floor at z = 0",
        f"anchor: {room['anchor']['id']}",
        "quantization:",
        f"  position_m: {Q_POS}",
        f"  yaw_deg: {Q_YAW}",
        f"  hysteresis_quanta: {HYST}",
        "octree:",
        f"  origin: [{', '.join(f'{v:.1f}' for v in origin)}]",
        f"  size_m: {size:.1f}",
        f"  levels: {levels}",
        "zones:",
    ]
    for name, z in room["zones"].items():
        lines += [f"  {name}:",
                  f"    min: [{', '.join(f'{v:.2f}' for v in z['min'])}]",
                  f"    max: [{', '.join(f'{v:.2f}' for v in z['max'])}]",
                  f"    surface: {z['surface']:.2f}"]
        if z.get("policy"):                                 # v2, optional: shared (the default) | personal
            lines += [f"    policy: {z['policy']}"]
        if z.get("owner"):
            lines += [f"    owner: {z['owner']}"]
    if room.get("home"):
        lines += [f"home: [{', '.join(f'{v:.2f}' for v in room['home'])}]  # the robot's parking pose: x, y, yaw"]
    if room.get("bin"):
        lines += ["bin:  # outside every zone: where removed objects go, untracked",
                  f"  pose: [{', '.join(f'{v:.2f}' for v in room['bin']['pose'])}]"]
    return "\n".join(lines) + "\n"


def load_room(root: Path) -> dict:
    """room.yaml -> {"zones": {name: {min, max, surface[, policy, owner]}}, "bin": {"pose": [x, y, z]} | None,
    "home", "lost_and_found" (v2 name; falls back to `bin`), "nav"}. Every v2 key is optional: a room
    written before them loads exactly as it did."""
    import yaml
    p = Path(root) / "room.yaml"
    if not p.is_file():
        raise GitError(f"{p} is missing — run `room init`")
    d = yaml.safe_load(p.read_text()) or {}
    return {"zones": d.get("zones") or {}, "bin": d.get("bin") or d.get("lost_and_found"), "home": d.get("home"),
            "lost_and_found": d.get("lost_and_found") or d.get("bin"), "nav": d.get("nav")}


def anchor_yaml(a: dict) -> str:
    x, y, z, yaw = a["pose"]
    return (f"id: {a['id']}\nfamily: {a['family']}\nsize_m: {a['size_m']:.2f}\n"
            f"pose:\n  x: {x:.2f}\n  y: {y:.2f}\n  z: {z:.2f}\n  yaw: {int(yaw)}\n")
