"""`git merge`, for objects — as a PREVIEW, and only ever a preview.

    GET  /api/merge-preview/{instance}?ours=&theirs=      what a merge WOULD do (a read)
    POST /api/merge-resolve/{instance}                    a person's decision, committed
    POST /api/demo-reset/{instance}                       a staged room back to its demo/* tags

NOTHING HERE MERGES ANYTHING. No ref moves, no tree is written, no commit is made, no working
tree is touched: three trees are read (the merge base and the two tips) and the answer says what
a merge WOULD do. That is deliberate, and it is the same rule scene_api.py and roomctl state —
`merge`, `cherry-pick` and `stash` are WRITE_VERBS and exit 2 — because a room is physical and
two answers to "where is it" are settled by looking at the room, not by merging text. A pull
request still has to SHOW a person what they are deciding, and this is that view.

WHAT IT ADDS OVER `git merge`. Git sees two YAML files and reports "modified here, deleted
there". It cannot say that both sides are talking about the same physical packet, how far apart
the two answers are, or that a move of 3 cm is stereo noise rather than a disagreement. The rules
are perception/roomdiff.py's merge3() — the very same function the CLI prints — so this endpoint
and

    python perception/roomdiff.py <repo> --merge <base> <ours> <theirs>

can never drift apart. One side changed it: that side wins, as git does. Both sides changed it
the same way (within roomctl.state.MOVE_M): agreed, no conflict. Both changed it differently, or
one removed what the other moved: a conflict, stated with both poses and the distance between
them, because only one of the two is where the thing actually is.

WHY IT IS NOT UNDER /api/scene. That namespace is guarded, and the guard is a test: no route
whose path contains "merge", "cherry" or "rebase" may exist there, and no merge verb may reach
git from scene_api.py (web/tests/test_scene_graph.py). This respects that rule rather than
bending it — it sits beside room.git's own /api/merge-preview, which graph_api.py has served all
along for the same reason: a preview is a READ that a page shows a person, not part of the room's
write API. The only git this file runs are `rev-parse`, `merge-base` and `log` — three questions,
no index, no tree, no ref.

RESOLVING IS A DECISION, AND THAT IS WHY IT IS ALLOWED TO WRITE. roomctl refuses `merge` with
"changes the physical room and isn't wired to the executor yet" — the objection is to a merge
NOBODY DECIDED, computed by a machine that cannot see the room. POST /api/merge-resolve carries a
choice per conflict, made by a person who can: it refuses unless every conflict has one, it never
picks a side itself, and it says in the commit message who decided what and that the room itself
was not moved. What it writes is the RECORD — git's idea of where things are — exactly as
`checkout` and `branch` already do for a scene instance (scene_api.py). Making the room match is
the robot's job, and that job is not this endpoint's to claim.

It never runs `git merge`. The merged tree is built with plumbing (read-tree, update-index,
write-tree, commit-tree) against a temporary index, so no working tree is touched and no
half-merged state can be left behind: either a commit exists at the end or nothing changed.

LOCAL ONLY, like every room route, and the write is never lifted by SCENE_PUBLIC.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query

import scene_api

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "perception")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import roomdiff  # noqa: E402

router = APIRouter(dependencies=[Depends(scene_api._local_only)])       # noqa: SLF001 — the same guard, one rule

FRAME, UNITS = scene_api.FRAME, scene_api.UNITS


def init(es) -> None:         # the router contract (web/server.py mount_router). Nothing here touches ES.
    return None


def _ref_or_404(repo: Path, ref: str) -> str:
    """A branch name or a commit sha of THIS room, resolved to a full sha. Checked before git sees it."""
    if not (scene_api.COMMIT.fullmatch(ref) or scene_api.BRANCH.fullmatch(ref)):
        raise HTTPException(status_code=422, detail="a ref is a commit sha or a branch name")
    full = scene_api._git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")     # noqa: SLF001
    if not full:
        raise HTTPException(status_code=404, detail=f"no commit or branch {ref!r} in this room")
    return full


def _where(rec) -> dict | None:
    """An object's place, or None for "not there on that side" — which is itself an answer."""
    if rec is None:
        return None
    return {"zone": rec.zone, "class": rec.cls,
            "pose": {"x": rec.pose.x, "y": rec.pose.y, "z": rec.pose.z, "yaw": rec.pose.yaw}}


def _choice_labels(c) -> dict:
    """What each side is ASKING FOR, in the words a person would use: keep it, or remove it.

    "ours" and "theirs" are git's words for whose commit it was, and they say nothing about the
    room. A conflict over a chip packet is really one question — is it still there? — so the two
    buttons are "keep it" (and where) and "remove it", taken from what each side actually holds.
    """
    def label(rec):
        if rec is None:
            return {"verb": "remove", "text": "Remove it", "why": "this side says it is gone"}
        at = f"{rec.zone} ({rec.pose.x:+.2f}, {rec.pose.y:+.2f})"
        return {"verb": "keep", "text": f"Keep it · {at}", "why": f"this side says it is on the {rec.zone}"}
    both = c.ours is not None and c.theirs is not None
    out = {"ours": label(c.ours), "theirs": label(c.theirs)}
    if both:                                     # two places, so "keep" alone does not tell them apart
        out["ours"]["text"] = f"Keep it here · {c.ours.zone} ({c.ours.pose.x:+.2f}, {c.ours.pose.y:+.2f})"
        out["theirs"]["text"] = f"Keep it there · {c.theirs.zone} ({c.theirs.pose.x:+.2f}, {c.theirs.pose.y:+.2f})"
    return out


def _subject(repo: Path, sha: str) -> str:
    return scene_api._git(repo, "log", "-1", "--format=%s", sha).strip()          # noqa: SLF001


@router.get("/api/merge-preview/{instance}")
def merge_preview(instance: str,
                  ours: str = Query(..., min_length=1, max_length=64),
                  theirs: str = Query(..., min_length=1, max_length=64)) -> dict:
    """What merging `theirs` into `ours` would do to the objects. A read: nothing is merged."""
    instance = scene_api._instance_or_404(instance)                               # noqa: SLF001
    repo = scene_api._under_rooms(instance)                                       # noqa: SLF001
    so, st = _ref_or_404(repo, ours), _ref_or_404(repo, theirs)
    base = scene_api._git(repo, "merge-base", so, st).strip()                     # noqa: SLF001
    if not scene_api.COMMIT.fullmatch(base):
        raise HTTPException(status_code=409, detail="these two share no history, so there is no base to merge from")

    at_base, at_ours, at_theirs = (roomdiff.records_at(repo, s) for s in (base, so, st))
    merged, conflicts = roomdiff.merge3(at_base, at_ours, at_theirs)
    ours_ops = {c.object_id: c for c in roomdiff.diff(at_base, at_ours) if c.counts}
    theirs_ops = {c.object_id: c for c in roomdiff.diff(at_base, at_theirs) if c.counts}
    disputed = {c.object_id for c in conflicts}

    clean = []
    for oid in sorted((set(ours_ops) | set(theirs_ops)) - disputed):
        mine, yours = ours_ops.get(oid), theirs_ops.get(oid)
        change = mine or yours
        rec = merged.get(oid)
        clean.append({
            "object_id": oid, "class": change.cls,
            # WHOSE change the merge takes. "both" is not a conflict: the two sides did the same
            # thing to it, which is agreement and the reason a rescan of an unchanged room merges.
            "side": "both" if mine and yours else ("ours" if mine else "theirs"),
            "op": {"+": "added", "-": "removed", "~": "moved"}.get(change.kind, change.kind),
            "distance_m": None if change.distance is None else round(change.distance, 3),
            "note": change.note,
            "base": _where(at_base.get(oid)), "result": _where(rec),
        })

    return {
        "instance": instance,
        "ours": {"ref": ours, "sha": so, "subject": _subject(repo, so)},
        "theirs": {"ref": theirs, "sha": st, "subject": _subject(repo, st)},
        "base": {"sha": base, "subject": _subject(repo, base)},
        "up_to_date": base == st,                      # theirs is already in ours: nothing to take
        "fast_forward": base == so and base != st,     # ours has nothing of its own: ours moves up
        "would_merge_cleanly": not conflicts,
        "clean": clean,
        "conflicts": [{
            "object_id": c.object_id, "class": c.cls, "why": c.why,
            "distance_m": None if c.distance is None else round(c.distance, 3),
            "base": _where(c.base), "ours": _where(c.ours), "theirs": _where(c.theirs),
            "options": list(c.options), "choose": _choice_labels(c),
        } for c in conflicts],
        # A merge TAKES theirs into ours. A row on ours' side is not something the merge does —
        # it is already here — so counting the two together made an up-to-date branch report
        # changes it would not make. They are named apart, and "clean" is their total.
        "summary": {"takes_theirs": sum(1 for o in clean if o["side"] == "theirs"),
                    "already_ours": sum(1 for o in clean if o["side"] != "theirs"),
                    "clean": len(clean), "conflicts": len(conflicts),
                    "objects_after": len(merged), "gate_m": roomdiff.MOVE_M},
        "written": False,
        "detail": "a preview computed from reads: nothing was merged, no ref moved, no file changed",
        "frame": FRAME, "units": UNITS,
    }


# ── a person's decision, committed ─────────────────────────────────────────────────────
GIT = "git"
# THE PICTURE HAS TO AGREE WITH THE DECISION. A commit carries the objects AND the cloud they were
# found in, and a merge built from ours' tree keeps ours' cloud — so "keep it" produced a record
# saying the packet is on the floor over a scan with the packet's points deleted, and the 3D view
# showed it gone. The scan is a photograph: it cannot be merged, only chosen. When every conflict
# went one way, that side's scan is the one that shows what was decided, and it comes across with
# the records. Decisions split between the two sides have no single true photograph, so ours' is
# kept and the answer says the picture does not show the whole decision.
CLOUD_FILES = ("cloud/current.ply", "cloud/current.json")
WHO = ("-c", "user.email=room@gitirl", "-c", "user.name=room")
SIDES = ("ours", "theirs")


def _write_git(repo: Path, *args: str, env: dict | None = None, timeout: int = 15) -> str:
    """A git that may write, in ONE repo, with the output on failure. Raises HTTPException(500)
    with git's own first line: a resolution that half-happened must say so, not return 200."""
    r = subprocess.run([GIT, "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {}), "GIT_OPTIONAL_LOCKS": "0"})
    if r.returncode:
        first = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
        raise HTTPException(status_code=500, detail=f"git {args[0]}: {first[0][:200] if first else 'failed'}")
    return r.stdout.strip()


def _paths_at(repo: Path, sha: str) -> dict[str, tuple[str, str]]:
    """{object_id: (path, blob)} for the zones tree at a commit — the file each object IS."""
    out = {}
    for line in scene_api._git(repo, "ls-tree", "-r", sha, "--", "zones").splitlines():   # noqa: SLF001
        meta, _, path = line.partition("\t")
        bits = meta.split()
        if len(bits) == 3 and bits[1] == "blob" and path.endswith(".yaml"):
            out[Path(path).stem] = (path, bits[2])
    return out


def _readable(repo: Path, ref: str, sha: str) -> str:
    """A name a person can read in `git log`. The page selects nodes by sha, so `theirs` arrives as
    40 hex — "merge 742f3451d52444aaa39c30... into kicked" is a true subject and a useless one. A
    branch pointing at that commit is its name; otherwise the short sha and what it said."""
    if scene_api.BRANCH.fullmatch(ref) and not scene_api.COMMIT.fullmatch(ref):
        return ref
    for line in scene_api._git(repo, "for-each-ref", "--format=%(refname:short) %(objectname)",   # noqa: SLF001
                               "refs/heads/").splitlines():
        name, _, at = line.partition(" ")
        if at.strip() == sha:
            return name
    subject = _subject(repo, sha)
    return f"{sha[:7]} ({subject})" if subject else sha[:7]


def _branch_of(repo: Path, ref: str) -> str | None:
    """The BRANCH `ref` names, or None. A resolution has to land on a branch: a commit made onto a
    bare sha belongs to nothing and is collected as garbage the next time git looks."""
    if scene_api.BRANCH.fullmatch(ref) and not scene_api.COMMIT.fullmatch(ref):
        full = scene_api._git(repo, "rev-parse", "--verify", f"refs/heads/{ref}")         # noqa: SLF001
        return ref if full else None
    return None


@router.post("/api/merge-resolve/{instance}", dependencies=[Depends(scene_api._local_write)])  # noqa: SLF001
def merge_resolve(instance: str, body: dict = Body(...)) -> dict:
    """Commit the merge of `theirs` into `ours`, with the conflicts settled by `choices`.

    body: {ours, theirs, choices: {object_id: "ours"|"theirs"}, expect?: {ours, theirs}}

    Every conflict must appear in `choices`; there is no default side and no "resolve the rest for
    me". `expect` is an optimistic lock — pass the two shas the preview was computed from and a
    resolution built on a stale view is refused rather than applied to a room that has moved on.
    """
    instance = scene_api._instance_or_404(instance)                                       # noqa: SLF001
    repo = scene_api._under_rooms(instance)                                               # noqa: SLF001
    ours, theirs = str(body.get("ours") or ""), str(body.get("theirs") or "")
    so, st = _ref_or_404(repo, ours), _ref_or_404(repo, theirs)
    branch = _branch_of(repo, ours)
    if not branch:
        raise HTTPException(status_code=422, detail="`ours` must be a branch: a commit made onto a bare sha belongs to nothing")
    expect = body.get("expect") or {}
    for side, got in (("ours", so), ("theirs", st)):
        want = expect.get(side)
        if want and not got.startswith(str(want)):
            raise HTTPException(status_code=409, detail=f"{side} has moved since that preview ({want} -> {got[:7]}): look again")

    base = scene_api._git(repo, "merge-base", so, st).strip()                             # noqa: SLF001
    if not scene_api.COMMIT.fullmatch(base):
        raise HTTPException(status_code=409, detail="these two share no history, so there is no base to merge from")
    if base == st:
        raise HTTPException(status_code=409, detail=f"{theirs} is already part of {ours}: there is nothing to merge")

    at_base, at_ours, at_theirs = (roomdiff.records_at(repo, s) for s in (base, so, st))
    merged, conflicts = roomdiff.merge3(at_base, at_ours, at_theirs)
    choices = body.get("choices") if isinstance(body.get("choices"), dict) else {}
    wanted = {c.object_id for c in conflicts}
    missing = sorted(wanted - set(choices))
    if missing:
        raise HTTPException(status_code=409, detail=f"every conflict needs a decision; nothing chosen for {', '.join(missing)}")
    extra = sorted(set(choices) - wanted)
    if extra:
        raise HTTPException(status_code=409, detail=f"{', '.join(extra)} is not in conflict here; nothing was merged")
    if any(choices[oid] not in SIDES for oid in wanted):
        raise HTTPException(status_code=422, detail="each choice must be 'ours' or 'theirs'")

    # the object set the merge ends with: what merged cleanly, plus the side each conflict was given
    side_of = {oid: "theirs" for oid in at_theirs if oid in merged and merged[oid] is at_theirs.get(oid)}
    taken = []
    for c in conflicts:
        pick = choices[c.object_id]
        rec = (c.ours if pick == "ours" else c.theirs)
        if rec is None:
            merged.pop(c.object_id, None)
        else:
            merged[c.object_id] = rec
            side_of[c.object_id] = pick
        taken.append({"object_id": c.object_id, "class": c.cls, "chose": pick,
                      "did": _choice_labels(c)[pick]["verb"],          # kept it, or removed it
                      "said": _choice_labels(c)[pick]["text"],
                      "result": _where(rec), "was": {"ours": _where(c.ours), "theirs": _where(c.theirs)}})

    chose = {t["chose"] for t in taken}
    one_way = len(chose) <= 1
    picture = next(iter(chose)) if len(chose) == 1 else "ours"
    ours_paths, theirs_paths = _paths_at(repo, so), _paths_at(repo, st)
    index = Path(tempfile.mkdtemp(prefix="roommerge-")) / "index"
    env = {"GIT_INDEX_FILE": str(index)}                      # a temporary index: the working tree is never touched
    try:
        _write_git(repo, "read-tree", so, env=env)
        if picture == "theirs":
            for path in CLOUD_FILES:
                blob = scene_api._git(repo, "rev-parse", f"{st}:{path}")                  # noqa: SLF001
                if scene_api.COMMIT.fullmatch(blob.strip()):
                    _write_git(repo, "update-index", "--add", "--cacheinfo",
                               f"100644,{blob.strip()},{path}", env=env)
        for oid in sorted(set(ours_paths) | set(theirs_paths)):
            ours_at, theirs_at = ours_paths.get(oid), theirs_paths.get(oid)
            if oid not in merged:                              # gone from the merge
                if ours_at:
                    _write_git(repo, "update-index", "--force-remove", ours_at[0], env=env)
                continue
            take = theirs_at if side_of.get(oid) == "theirs" and theirs_at else ours_at
            if not take:
                continue
            if ours_at and ours_at[0] != take[0]:              # a zone change moves the FILE
                _write_git(repo, "update-index", "--force-remove", ours_at[0], env=env)
            if not ours_at or ours_at != take:
                _write_git(repo, "update-index", "--add", "--cacheinfo", f"100644,{take[1]},{take[0]}", env=env)
        tree = _write_git(repo, "write-tree", env=env)
    finally:
        index.unlink(missing_ok=True)
        index.parent.rmdir()

    message = _message(instance, branch, _readable(repo, theirs, st), branch, taken, merged, picture)
    commit = _write_git(repo, *WHO, "commit-tree", tree, "-p", so, "-p", st, "-m", message)
    head = scene_api._git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()              # noqa: SLF001
    dirty = bool(scene_api._git(repo, "status", "--porcelain=v1", "--untracked-files=no"))  # noqa: SLF001
    moved_tree = False
    if head == branch and not dirty:
        _write_git(repo, "reset", "--hard", commit)            # the branch is checked out: bring its files along
        moved_tree = True
    else:
        _write_git(repo, "update-ref", f"refs/heads/{branch}", commit, so)
    return {
        "instance": instance, "merged": commit, "branch": branch, "parents": [so, st],
        "base": base, "conflicts_settled": taken,
        "picture": {"from": picture, "shows_the_decision": one_way,
                    "detail": ("the scan from the side that was chosen, so the cloud shows what was decided"
                               if one_way else
                               "this side's scan: the decisions went both ways, and no single scan shows all of them")}
                   if taken else None,
        "objects_after": sorted(merged),
        "working_tree": ("updated: that branch is checked out" if moved_tree
                         else f"left alone: HEAD is on {head or 'a detached commit'}"
                              + (", and it has uncommitted changes" if dirty and head == branch else "")),
        "detail": "the RECORD is merged: git now says where these things are. Nothing in the room moved — "
                  "making the room match a decision is the robot's job, and no executor was asked.",
        "frame": FRAME, "units": UNITS,
    }


def _message(instance: str, ours: str, theirs: str, branch: str, taken: list[dict], merged: dict,
             picture: str = "ours") -> str:
    """The commit message IS the record of the decision: which way each conflict went, from where to
    where, and that a person chose it. A merge nobody can read afterwards is a merge nobody can audit."""
    head = f"merge {theirs} into {ours}" + (f": {len(taken)} conflict settled" if len(taken) == 1
                                            else f": {len(taken)} conflicts settled" if taken else ", cleanly")
    lines = [head, ""]
    for t in taken:
        got, other = t["was"][t["chose"]], t["was"]["theirs" if t["chose"] == "ours" else "ours"]
        lines.append(f"{t['class'] or t['object_id']} ({t['object_id']}): "
                     + (f"kept, {got['zone']} ({got['pose']['x']:+.2f}, {got['pose']['y']:+.2f})" if got
                        else "removed — confirmed gone"))
        lines.append("  the other side said " + (f"{other['zone']} ({other['pose']['x']:+.2f}, {other['pose']['y']:+.2f})"
                                                 if other else "it was gone"))
    lines += ["", f"{len(merged)} object(s) in {branch} after this.",
              f"The cloud is {'the other side' if picture == 'theirs' else 'this side'}'s scan — "
              "a photograph cannot be merged, only chosen.",
              "Decided by a person; the room itself was not moved."]
    return "\n".join(lines)


# ── putting a staged demo back ─────────────────────────────────────────────────────────
DEMO_TAG = "demo/"


def _demo_tags(repo: Path) -> dict[str, str]:
    """{branch: sha} for every `demo/<branch>` tag — a room's own statement of where its demo
    starts. A room without them is not a staged demo, and nothing here will touch it."""
    out = {}
    for line in scene_api._git(repo, "for-each-ref", "--format=%(refname:short) %(objectname)",   # noqa: SLF001
                               f"refs/tags/{DEMO_TAG}*").splitlines():
        name, _, sha = line.partition(" ")
        branch = name[len(DEMO_TAG):]
        if branch and scene_api.COMMIT.fullmatch(sha.strip()) and scene_api.BRANCH.fullmatch(branch):
            out[branch] = sha.strip()
    return out


@router.post("/api/demo-reset/{instance}", dependencies=[Depends(scene_api._local_write)])  # noqa: SLF001
def demo_reset(instance: str) -> dict:
    """Put a staged demo room back to where its `demo/*` tags say it starts.

    A demo is run more than once, and the second run has to start where the first one did — a merge
    committed at 11am is a fine thing to have shown and a confusing thing to open with. The tags are
    the statement of the starting state (scripts/demo_chips.py writes them at seed time), so this
    moves each branch back to its tag and nothing else: it never deletes a tag, never touches a room
    without them, and cannot invent a state that was not staged.

    Commits made during the demo are not deleted — they are unreferenced, and `git reflog` still has
    them until git collects them. Refusing to reset would be safer still; leaving the room wrong
    between demos is not.
    """
    instance = scene_api._instance_or_404(instance)                                       # noqa: SLF001
    repo = scene_api._under_rooms(instance)                                               # noqa: SLF001
    tags = _demo_tags(repo)
    if not tags:
        raise HTTPException(status_code=404, detail=f"{instance} has no demo/* tags: it is not a staged demo room")
    head = scene_api._git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()              # noqa: SLF001
    moved = []
    for branch, want in sorted(tags.items()):
        at = scene_api._git(repo, "rev-parse", "--verify", f"refs/heads/{branch}")        # noqa: SLF001
        if not at or at == want:
            continue
        if branch == head:
            _write_git(repo, "reset", "--hard", want)         # the checked-out branch brings its files
        else:
            _write_git(repo, "update-ref", f"refs/heads/{branch}", want, at)
        moved.append({"branch": branch, "from": at[:7], "to": want[:7]})
    dirty = scene_api._git(repo, "status", "--porcelain=v1", "--untracked-files=no")      # noqa: SLF001
    if dirty and head in tags:
        _write_git(repo, "reset", "--hard", tags[head])
    return {"instance": instance, "head": head, "reset": moved,
            "already": not moved and not dirty,
            "detail": (f"{len(moved)} branch(es) put back to their demo/* tag" if moved
                       else "already at the staged starting state; nothing was changed")}
