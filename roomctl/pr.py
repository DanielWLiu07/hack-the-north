"""Pull requests for the room: how a DECISION becomes `main` (plan/roommate/03 §7).

A change you mean goes through a pull request; everything else is drift, and the caretaker puts
it back. PRs are plain git, with no side database:

    propose   a commit on refs/heads/pr/<n>-<slug>, built with plumbing on a temporary index. The
              working tree (which IS the physical room, as last scanned) is never touched.
    approve   a --no-ff merge commit onto the current branch (a 3-way `merge-tree --write-tree`,
              so main may have moved on). The branch ref is updated and the INDEX is reset to it,
              but the working tree is left alone. So right after approval the room reads as
              drifted from the new main ("the lamp is not on the shelf yet"), and reconciling that
              drift is exactly the caretaker's job. The decision is recorded before the robot moves.
    close     the branch moves to refs/closed/pr/<n>-<slug>; history keeps it.

Commits are authored by the robot (like every room commit); who proposed and who approved are
git trailers (`Proposed-by:`, `Approved-by:`).
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, replace

from roomctl.executor import Spot, staging_spot
from roomctl.repo import ROBOT_EMAIL, ROBOT_NAME, GitError, Repo, load_room
from roomctl.state import object_path, to_yaml

PR_REF = re.compile(r"^refs/(heads|closed)/pr/(\d+)-([a-z0-9-]+)$")


@dataclass
class PR:
    id: int
    branch: str
    title: str
    author: str | None
    base_sha: str
    head_sha: str
    status: str                      # open | merged | closed
    ops: list[dict] = field(default_factory=list)
    merged_in: str | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "branch": self.branch, "title": self.title, "author": self.author,
                "base_sha": self.base_sha, "head_sha": self.head_sha, "status": self.status, "ops": self.ops,
                "merged_in": self.merged_in}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40].rstrip("-") or "change"


def _robot_env(extra: dict | None = None) -> dict:
    return {"GIT_AUTHOR_NAME": ROBOT_NAME, "GIT_AUTHOR_EMAIL": ROBOT_EMAIL,
            "GIT_COMMITTER_NAME": ROBOT_NAME, "GIT_COMMITTER_EMAIL": ROBOT_EMAIL, **(extra or {})}


def _refs(repo: Repo) -> list[tuple[str, str, int, str]]:
    """(ref, kind heads|closed, n, slug) for every PR ref."""
    out = repo.git("for-each-ref", "--format=%(refname)", "refs/heads/pr/", "refs/closed/pr/").stdout.split()
    found = []
    for ref in out:
        m = PR_REF.match(ref)
        if m:
            found.append((ref, m.group(1), int(m.group(2)), m.group(3)))
    return found


def _trailer(message: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", message, re.M)
    return m.group(1).strip() if m else None


def _ops(repo: Repo, a: str, b: str) -> list[dict]:
    """Object-level changes from tree a to tree b (the preview a reviewer reads)."""
    ra, rb = repo.records(a), repo.records(b)
    ops = []
    for oid in sorted(ra.keys() | rb.keys()):
        x, y = ra.get(oid), rb.get(oid)
        if x and y and (x.zone, x.pose) != (y.zone, y.pose):
            ops.append({"op": "moved", "object_id": oid, "class": y.cls, "from": {"zone": x.zone, **asdict(x.pose)},
                        "to": {"zone": y.zone, **asdict(y.pose)}})
        elif x and not y:
            ops.append({"op": "removed", "object_id": oid, "class": x.cls, "from": {"zone": x.zone, **asdict(x.pose)}})
        elif y and not x:
            ops.append({"op": "added", "object_id": oid, "class": y.cls, "to": {"zone": y.zone, **asdict(y.pose)}})
    return ops


def propose(repo: Repo, object_id: str, zone: str, author: str, title: str | None = None) -> PR:
    """Move one object to a free spot in `zone`, as a commit on a new pr/<n>-<slug> branch."""
    repo.require()
    base = repo.head()
    if not base:
        raise GitError("the room has no commits yet")
    records = repo.records(base)
    if object_id not in records:
        raise GitError(f"no object {object_id!r} at {base[:7]}")
    room = load_room(repo.path)
    if zone not in room.get("zones", {}):
        raise GitError(f"no zone {zone!r} in room.yaml (zones: {', '.join(room.get('zones', {}))})")
    rec = records[object_id]
    if rec.zone == zone:
        raise GitError(f"{object_id} is already in {zone}")
    occupied = {oid: Spot(r.zone, r.pose, r.extents) for oid, r in records.items()}
    only_target = {**room, "zones": {zone: room["zones"][zone]}}      # search that surface alone
    spot = staging_spot(object_id, occupied, {}, only_target)
    if spot is None:
        raise GitError(f"no free spot for {object_id} in {zone}")
    moved = replace(rec, zone=zone, pose=spot.pose)

    n = 1 + max((t[2] for t in _refs(repo)), default=0)
    title = title or f"move {object_id} to {zone}"
    branch = f"pr/{n}-{_slug(title)}"
    blob = repo.git("hash-object", "-w", "--stdin", stdin=to_yaml(moved)).stdout.strip()
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
        repo.git("read-tree", base, env=env)
        repo.git("update-index", "--remove", "--force-remove", object_path(rec.zone, object_id), env=env)
        repo.git("update-index", "--add", "--cacheinfo", f"100644,{blob},{object_path(zone, object_id)}", env=env)
        tree = repo.git("write-tree", env=env).stdout.strip()
    msg = f"{title}\n\nProposed-by: {author}\n"
    commit = repo.git("commit-tree", tree, "-p", base, "-m", msg, env=_robot_env()).stdout.strip()
    repo.git("update-ref", f"refs/heads/{branch}", commit)
    return PR(n, branch, title, author, base, commit, "open", _ops(repo, base, commit))


def list_prs(repo: Repo) -> list[PR]:
    head = repo.head()
    prs = []
    for ref, kind, n, slug in sorted(_refs(repo), key=lambda t: t[2]):
        sha = repo.git("rev-parse", ref).stdout.strip()
        msg = repo.git("log", "-1", "--format=%B", sha).stdout
        base = repo.git("rev-parse", f"{sha}^").stdout.strip()
        status, merged_in = "open", None
        if kind == "closed":
            status = "closed"
        elif head and repo.git("merge-base", "--is-ancestor", sha, head, check=False).returncode == 0:
            status = "merged"
            merged_in = repo.git("log", "--merges", "--format=%H", "--grep", f"Merge pull request #{n}:", "-1",
                                 head).stdout.strip() or None
        prs.append(PR(n, ref.removeprefix("refs/heads/").removeprefix("refs/closed/"), msg.splitlines()[0],
                      _trailer(msg, "Proposed-by"), base, sha, status, _ops(repo, base, sha), merged_in))
    return prs


def _find(repo: Repo, pr_id: int) -> tuple[str, str]:
    for ref, kind, n, _ in _refs(repo):
        if n == pr_id:
            return ref, kind
    raise GitError(f"no pull request #{pr_id}")


def approve(repo: Repo, pr_id: int, approver: str) -> str:
    """Merge PR #n into the current branch; returns the merge sha. The working tree (the room as
    last seen) is untouched, so what the robot still has to do shows up as drift."""
    ref, kind = _find(repo, pr_id)
    if kind == "closed":
        raise GitError(f"pull request #{pr_id} is closed")
    head, branch = repo.head(), repo.branch()
    if branch in ("", "HEAD"):
        raise GitError("HEAD is detached: check out the branch to merge into")
    tip = repo.git("rev-parse", ref).stdout.strip()
    if repo.git("merge-base", "--is-ancestor", tip, head, check=False).returncode == 0:
        raise GitError(f"pull request #{pr_id} is already merged")
    r = repo.git("merge-tree", "--write-tree", "--messages", head, tip, check=False)
    if r.returncode != 0:
        raise GitError(f"pull request #{pr_id} conflicts with {branch}: {r.stdout.strip()[:300]}")
    tree = r.stdout.split()[0]
    title = repo.git("log", "-1", "--format=%s", tip).stdout.strip()
    msg = f"Merge pull request #{pr_id}: {title}\n\nApproved-by: {approver}\n"
    merge = repo.git("commit-tree", tree, "-p", head, "-p", tip, "-m", msg, env=_robot_env()).stdout.strip()
    repo.git("update-ref", f"refs/heads/{branch}", merge, head)       # fails if the branch moved meanwhile
    repo.git("read-tree", "HEAD")                                        # index = the new main; the room stays as seen
    return merge


def close(repo: Repo, pr_id: int) -> None:
    ref, kind = _find(repo, pr_id)
    if kind == "closed":
        return
    sha = repo.git("rev-parse", ref).stdout.strip()
    repo.git("update-ref", ref.replace("refs/heads/", "refs/closed/", 1), sha)
    repo.git("update-ref", "-d", ref, sha)
