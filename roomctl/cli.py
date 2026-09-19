"""`room` — git for the room you're standing in.

    room init                     first scan of the room, first commit
    room status [--json]          scan, then: what has moved since the last commit
    room diff [<git diff args>]   the literal `git diff` of the physical world
    room add <zones/desk/ ...>    spatial staging
    room commit -m "msg"          scan, then record the room as it is now
    room log [<git log args>]     the room's history (default: --graph --oneline --all)
    room reset --hard [<ref>]     the robot puts the room back to <ref> (default HEAD)
    room checkout <ref>           the robot makes the room match a branch or commit
    room revert [<ref>]           git revert, then the robot applies it
    room restore [<ref>]          <ref>'s room as a new commit on HEAD, then the robot applies it
    room search "<words>"         hybrid search over the room's whole history (Elasticsearch)
    room publish [--flush]        send spooled commits to Elasticsearch
    room show|blame|branch|tag    read-only git, passed straight through

Read commands are perception; write commands are motion (docs/04). reset --hard, checkout,
revert and restore move physical objects through the executor; merge, cherry-pick and stash
will. restore is not revert: `restore <ref>` puts the WHOLE room at <ref> as a new commit
(`git restore --source=<ref> --staged --worktree -- zones` + commit); `revert <commit>` undoes
only that one commit's change. Neither moves HEAD off the branch (ANDREW-HANDOFF.md §3).
Every commit is also written to Elasticsearch (roomctl/publish.py), or spooled if it's away.

Scanning: the room is the working tree, and a scan is how we `stat` it. Until perception
is wired in, the scanner is fake/scene_gen.py — `--scene messy_bench` for one command, or
ROOM_SCANNER=fake:<scene> for all of them. With no scanner, commands use the working tree
as it was last scanned.

    alias room="$PWD/.venv/bin/python -m roomctl"
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")  # real environment variables win over the file

from roomctl.executor import (HOME, BasePose, MockRobot, RobotError, Spot, _perception,  # noqa: E402
                              execute, plan, render_plan, route)
from roomctl.repo import ROBOT_EMAIL, ROBOT_NAME, Entry, GitError, Repo, Status, init, load_room  # noqa: E402
from roomctl.robot_client import web_inlet  # noqa: E402
from roomctl.state import ObjectRecord, SchemaError, from_yaml  # noqa: E402

APPLY_VERBS = {"reset", "checkout", "revert", "restore"}  # make the room match a tree: robot acts
WRITE_VERBS = {"switch", "merge", "cherry-pick", "stash", "rebase", "pull"}  # not yet
READ_VERBS = {"show", "blame", "branch", "tag", "shortlog", "reflog", "ls-files"}


# ── scanning ─────────────────────────────────────────────────────────────────

def scanner(scene: str | None) -> str | None:
    """The fake scene to scan, or None for "use the working tree as last scanned" (or for the
    real pipeline — see recording())."""
    if scene:
        return scene
    spec = os.getenv("ROOM_SCANNER", "").strip()
    if not spec or spec == "none":
        return None
    kind, _, arg = spec.partition(":")
    if kind == "fake" and arg:
        return arg
    if kind == "perception":
        return None
    raise GitError(f"ROOM_SCANNER={spec!r}: want fake:<scene>, perception:<recording dir>, or none")


def recording() -> Path | None:
    """ROOM_SCANNER=perception:<dir> — one recorded capture, replayed through the real
    pipeline (perception/pipeline.scan_into). Live capture from the Pi comes later."""
    kind, _, arg = os.getenv("ROOM_SCANNER", "").strip().partition(":")
    if kind != "perception":
        return None
    if not arg or not Path(arg).is_dir():
        raise GitError("ROOM_SCANNER=perception:<recording dir> — a directory perception/pipeline.scan_into reads")
    return Path(arg)


def scan_real(repo: Repo, rec: Path) -> str:
    """The real chain: depth -> fuse -> segment/cluster -> merge -> associate -> serialize, then
    voxelize.stage + publish.stage_scan for the commit to pick up."""
    import sys as _sys
    d = str(ROOT / "perception")
    if d not in _sys.path:
        _sys.path.insert(0, d)
    from pipeline import scan_into
    from roomctl.publish import is_the_room
    # The capture page reads what scan_into indexes (room-clouds, room-observations) — but only
    # the room's own repository may write the shared indices (docs/10 D37).
    res = scan_into(repo.path, rec, es="env" if is_the_room(repo) and os.getenv("ROOM_ES", "auto") != "off" else None)
    return str(getattr(res, "at", "") or "")


def fake_room(repo: Repo):
    from fake.scene_gen import FakeRoom  # only when scanning: the fake stands in for perception
    return FakeRoom(repo.path, quiet=True)


def scan(repo: Repo, scene: str | None) -> str | None:
    """Look at the room and write what's there into the working tree. Returns capture time."""
    if (rec := recording()) is not None:
        return scan_real(repo, rec)
    name = scanner(scene)
    if name is None:
        return None
    room = fake_room(repo)
    res = room.scan(name)
    room.flush(res.capture_id, None, os.getenv("ROOM_ES", "auto"))  # observations -> ES, if it's up
    return res.at.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── status ───────────────────────────────────────────────────────────────────

def change_type(e: Entry) -> str:
    if e.untracked:
        return "untracked"
    if e.conflict:
        return "conflict"
    code = e.staged if e.staged != " " else e.unstaged
    return {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}.get(code, "modified")


def movement(before: ObjectRecord | None, after: ObjectRecord | None) -> dict:
    """What happened to the physical object — the part `git status` can't say."""
    if before is None or after is None:
        return {}
    out = {}
    d = math.dist((before.pose.x, before.pose.y, before.pose.z), (after.pose.x, after.pose.y, after.pose.z))
    if d:
        out["delta_m"] = round(d, 2)
    if before.pose.yaw != after.pose.yaw:
        out["yaw"] = [before.pose.yaw, after.pose.yaw]
    if before.zone != after.zone:
        out["zone_from"] = before.zone
    return out


def status_dict(repo: Repo, st: Status | None = None, last_capture: str | None = None) -> dict:
    """docs/16 §4b `GET /api/status`. web/ can import this instead of shelling out."""
    st = st or repo.status()
    head = repo.records()
    tree = {}
    for e in st.entries:  # only the changed files; a conflicted one simply has no reading
        f = repo.path / e.path
        if e.object_id and f.is_file():
            try:
                tree[e.object_id] = from_yaml(f.read_text())
            except SchemaError:
                pass
    changes = []
    for e in st.entries:
        c = {"type": change_type(e), "path": e.path, "object_id": e.object_id, "zone": e.zone,
             "staged": e.staged != " " and not e.untracked}
        if e.orig:
            c["from"] = e.orig
        if e.object_id:
            c.update(movement(head.get(e.object_id), tree.get(e.object_id)))
            rec = tree.get(e.object_id) or head.get(e.object_id)
            if rec:
                c["class"] = rec.cls
        changes.append(c)
    return {"branch": st.branch, "head": st.head[:7] if st.head else None, "clean": st.clean,
            "merging": st.merging, "changes": changes, "last_capture": last_capture}


class Paint:
    def __init__(self, on: bool):
        self.on = on

    def __call__(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[m" if self.on else s


def render_status(d: dict, paint: Paint) -> str:
    """git's layout, with the physical meaning of each line beside it."""
    def note(c: dict) -> str:
        bits = []
        if "delta_m" in c:
            bits.append(f"moved {c['delta_m']:.2f} m")
        if "yaw" in c:
            bits.append(f"turned {c['yaw'][0]}° → {c['yaw'][1]}°")
        if "zone_from" in c:
            bits.append(f"{c['zone_from']} → {c['zone']}")
        if c["type"] in ("untracked", "added") and c.get("class"):
            bits.append(f"new {c['class']}")
        if c["type"] == "deleted" and c.get("class"):
            bits.append(f"{c['class']} gone")
        return f"   ({', '.join(bits)})" if bits else ""

    def line(c: dict, color: str, label: str | None = None) -> str:
        label = label or {"added": "new object:", "renamed": "renamed:"}.get(c["type"], f"{c['type']}:")
        path = f"{c['from']} -> {c['path']}" if c.get("from") else c["path"]
        width = 15 if label.startswith("both") else 12
        return f"\t{paint(color, f'{label:<{width}}{path}')}{note(c)}"

    out = [f"On branch {d['branch']}"]
    if d["merging"]:
        out.append("You have unmerged paths — the same object was moved on both branches.")
    staged = [c for c in d["changes"] if c["staged"] and c["type"] != "conflict"]
    unstaged = [c for c in d["changes"] if not c["staged"] and c["type"] not in ("untracked", "conflict")]
    conflicts = [c for c in d["changes"] if c["type"] == "conflict"]
    untracked = [c for c in d["changes"] if c["type"] == "untracked"]
    if conflicts:
        out += ["Unmerged paths:"] + [line(c, "31", "both modified:") for c in conflicts]
    if staged:
        out += ["Changes to be committed:"] + [line(c, "32") for c in staged]
    if unstaged:
        out += ["Changes not staged for commit:"] + [line(c, "31") for c in unstaged]
    if untracked:
        out += ["Untracked objects:"] + [f"\t{paint('31', c['path'])}{note(c)}" for c in untracked]
    if d["clean"]:
        out.append("nothing to commit, working tree clean")
    return "\n".join(out)


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_init(repo: Repo, a) -> int:
    name = scanner(a.scene)
    if repo.exists:
        raise GitError(f"{repo.path} is already a room repository")
    if name:
        scan(repo, name)            # the fake inits the repo and room files on its first scan
        stage_fake_voxels(repo)
        c = repo.commit(a.message or "room init: first scan")
        print(f"Initialized room repository in {repo.path}\n[{c.branch} (root-commit) {c.sha[:7]}] "
              f"{c.message}\n {len(repo.records())} objects tracked")
        if (line := publish_line(repo, c)):
            print(line)
        return 0
    from fake.scene_gen import load_scene  # the default room.yaml until the venue's is written
    init(repo.path, load_scene(a.room_from).room)
    c = repo.commit(a.message or "room init")
    print(f"Initialized room repository in {repo.path}\n[{c.branch} (root-commit) {c.sha[:7]}] {c.message}\n"
          f" no scanner: 0 objects tracked — `room commit --scene <scene>` or set ROOM_SCANNER")
    if (line := publish_line(repo, c)):
        print(line)
    return 0


def cmd_status(repo: Repo, a) -> int:
    repo.require()
    captured = None if a.no_scan else scan(repo, a.scene)
    d = status_dict(repo, last_capture=captured)
    if a.json:
        print(json.dumps(d, indent=2))
    else:
        print(render_status(d, Paint(sys.stdout.isatty())))
        if captured is None and not a.no_scan:
            print("(not scanned: showing the room as last observed — set ROOM_SCANNER=fake:<scene>)",
                  file=sys.stderr)
    return 1 if a.exit_code and not d["clean"] else 0


def cmd_commit(repo: Repo, a) -> int:
    repo.require()
    name = None if a.no_scan else scanner(a.scene)
    if not a.no_scan and not name and (rec := recording()) is not None:
        scan_real(repo, rec)  # then commit like any non-fake scan: publish picks up its staging
    if name:  # the fake room: scanned and staged exactly like the real one, then the same hook
        scan(repo, name)
        stage_fake_voxels(repo)
    c = repo.commit(a.message)
    sha, branch, changes = (c.sha, c.branch, c.changes) if c else (None, repo.branch(), [])
    es_line = publish_line(repo, c)
    if sha is None:
        print(f"On branch {repo.branch()}\nnothing to commit, working tree clean")
        return 1
    kinds = {"A": "added", "D": "removed", "M": "moved", "R": "moved"}
    counts: dict[str, int] = {}
    for status, path in changes:
        if path.startswith("zones/"):
            counts[kinds[status]] = counts.get(kinds[status], 0) + 1
    summary = ", ".join(f"{n} {k}" for k, n in counts.items()) or "no objects changed"
    print(f"[{branch} {sha[:7]}] {a.message}\n {summary}")
    if es_line:
        print(es_line)
    return 0


# ── write verbs: git moves HEAD, the robot moves the room, a rescan checks it ──

def cmd_apply(repo: Repo, verb: str, args: list[str]) -> int:
    """`room reset --hard [ref]`, `room checkout <ref>`, `room revert [ref]`, `room restore [ref]`.

    Git goes first and decides the target tree — we never reimplement revert or checkout.
    Then the executor plans from the room as observed, the robot makes it match, and a
    rescan says honestly how much of it happened. restore commits <ref>'s objects on top of
    HEAD first (restore_commit), so like every other verb the robot works to a committed tree."""
    repo.require()
    ap = argparse.ArgumentParser(prog=f"room {verb}")
    ap.add_argument("ref", nargs="?", default="HEAD")
    ap.add_argument("--source", help="restore: git's spelling of <ref>")
    ap.add_argument("--hard", action="store_true")
    ap.add_argument("--scene", help="fake scene the room is in right now (default: $ROOM_SCANNER)")
    ap.add_argument("--plan-only", action="store_true", help="print the plan; move nothing, change no refs")
    ap.add_argument("--json", action="store_true",
                    help="with --plan-only: the ordered op list as JSON (gitspace.plan/1, docs/30) — "
                         "what the edge executes")
    ap.add_argument("--no-route", action="store_true",
                    help="skip base-pose solving (docs/24 A2): order the ops, don't ask where to stand")
    a = ap.parse_args(args)
    ref = (a.source or a.ref) if verb == "restore" else a.ref
    if verb == "checkout" and ref == "HEAD":
        raise GitError("room checkout <branch|commit>")

    name = scanner(a.scene)
    if recording() is not None and not a.plan_only:
        raise GitError(f"room {verb} with a recorded scan can't verify: a recording doesn't change when "
                       f"the robot does. Use --plan-only, or ROOM_SCANNER=fake:<scene>")
    if name is None and recording() is None and not a.plan_only:
        raise GitError(f"room {verb} moves objects and must verify by rescanning — pass --scene or set "
                       f"ROOM_SCANNER (or --plan-only)")
    if (rec := recording()) is not None:
        scan_real(repo, rec)
    world = None
    if name:
        room_fake = fake_room(repo)
        seen = room_fake.scan(name)
        room_fake.flush(seen.capture_id, None, os.getenv("ROOM_ES", "auto"))
        from fake.scene_gen import load_scene
        scene = load_scene(name)
        world = {oid: Spot(t.zone, _pose(t), _ext(t)) for oid, t in scene.objects.items()}
    current = read_worktree(repo)

    if verb == "revert" and not a.plan_only and not repo.status().clean:
        raise GitError("the room has uncommitted changes, and revert would destroy them — commit them, "
                       "or `room reset --hard` to put the room back to HEAD first")
    if a.plan_only:
        if verb == "revert":
            raise GitError("--plan-only isn't supported for revert: git computes that tree by reverting")
        room = load_room(repo.path)
        p = _routed(plan(current, repo.records(ref), room), room, current, a.no_route)
        if a.json:
            sha = repo.git("rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()
            print(json.dumps(p.to_dict(ref, sha), indent=2))
        else:
            print(render_plan(p))
        return 0

    robot = make_robot(world)  # reachable at all? — before git moves a single ref
    robot_env = {"GIT_AUTHOR_NAME": ROBOT_NAME, "GIT_AUTHOR_EMAIL": ROBOT_EMAIL,
                 "GIT_COMMITTER_NAME": ROBOT_NAME, "GIT_COMMITTER_EMAIL": ROBOT_EMAIL}
    committed = verb == "revert"
    if verb == "restore":
        committed = restore_commit(repo, ref, robot_env)
    elif verb == "reset":
        r = repo.git("reset", "--hard", ref)
    elif verb == "checkout":
        r = repo.git("checkout", "-f", ref)
    else:
        repo.git("reset", "-q", "--hard", "HEAD")  # the working tree only holds the last scan
        r = repo.git("revert", "--no-edit", ref, env=robot_env, check=False)
        if r.returncode:
            repo.git("revert", "--abort", check=False)
            raise GitError(f"git revert {ref} failed, nothing was moved: {(r.stderr or r.stdout).strip()}")
    if verb != "restore":
        print((r.stdout + r.stderr).strip() or f"HEAD is now at {repo.head()[:7]}")
    target = repo.records()

    room = load_room(repo.path)
    p = _routed(plan(current, target, room), room, current, a.no_route)
    print(render_plan(p))
    publish = None if os.getenv("ROOM_EVENTS", "web") == "off" else web_inlet()
    healing = self_heal(robot, seen.capture_id if name else "", repo.head())
    if healing is not None:
        robot = healing
    robot.led("working")
    outcome = execute(p, robot, publish)
    for op, why in outcome.failed:
        print(f"error: {op.kind} {op.object_id} failed: {why}")
    for op, why in outcome.skipped:
        print(f"skipped: {op.kind} {op.object_id} — {why}")

    # verify: the room is the working tree; look at it again
    room_fake = fake_room(repo)
    after = room_fake.scan(_moved_scene(name, world))
    room_fake.flush(after.capture_id, None, os.getenv("ROOM_ES", "auto"))
    if committed:  # revert/restore made a commit: publish it as the rescan saw the room after it
        stage_fake_voxels(repo)
        if (line := publish_line(repo, repo.commit_info())):
            print(line)
    d = status_dict(repo)
    if healing is not None and healing.healed:  # resolve only what the rescan proved
        changed = {c["object_id"] for c in d["changes"] if c.get("object_id")}
        from robot_sentry import SentryIssues
        resolved = healing.resolve_verified(SentryIssues.from_env(), verified=set(target) - changed)
        print(f"sentry: {len(resolved)} issue{'s' * (len(resolved) != 1)} resolved by the robot, after the rescan"
              if resolved else "sentry: nothing resolved (no issue found, or the rescan didn't verify it)")
    needed = len({op.object_id for op in p.ops}) + len(p.unapplied)
    landed = len({op.object_id for op in outcome.done} - {op.object_id for op, _ in outcome.failed + outcome.skipped})
    if d["clean"]:
        print(f"verify: rescanned — the room matches {ref}.")
        robot.say(f"Done. The room matches {ref}.")
        robot.led("clean")
        return 0
    print(f"verify: rescanned — {landed} of {needed} object{'s' * (needed != 1)} put right. "
          f"The room still differs from {repo.head()[:7]}:")
    print("\n".join(line for line in render_status(d, Paint(sys.stdout.isatty())).splitlines()[1:]))
    robot.say(f"I put back {landed} of {needed}. Human intervention required for the rest.")
    robot.led("dirty")
    return 1


def _routed(p, room: dict, current: dict, skip: bool):
    """Attach where-to-stand to every op. An op with nowhere to stand becomes an unapplied hunk."""
    if skip:
        return p
    home = BasePose(*room["home"]) if room.get("home") else HOME
    return route(p, fake_costmap(room, current.values()), home)


def fake_costmap(room: dict, records):
    """The fake room through perception's real path: synthetic cloud -> VoxelGrid -> Costmap."""
    from fake.scene_gen import scene_cloud
    from roomctl.repo import octree_cube
    cm = _perception()
    from voxelize import VoxelGrid
    return cm.Costmap.from_grid(VoxelGrid.from_points(scene_cloud(room, list(records)), cube=octree_cube()))


def publish_line(repo: Repo, c) -> str | None:
    """The GAP 1 hook: every commit made outside the fake goes to Elasticsearch, or the spool.
    (The fake writes its own, richer documents.) ROOM_ES=off skips it — tests and dry runs."""
    if c is None or os.getenv("ROOM_ES", "auto") == "off":
        return None
    from roomctl.publish import publish_commit
    try:
        return publish_commit(repo, c).line()
    except Exception as e:  # noqa: BLE001 — the commit happened; never let the read path undo that
        return f"es: not published ({type(e).__name__}: {e}) — the commit is safe in git"


def cmd_search(repo: Repo, args: list[str]) -> int:
    """docs/06's "where did I leave my keys" beat: elastic/queries.py's hybrid search (BM25 +
    Jina dense, RRF, Jina rerank, one hit per object) over ALL history, answered in git's terms —
    where it was last seen, in which commit, and how far back that is on this branch."""
    ap = argparse.ArgumentParser(prog="room search")
    ap.add_argument("words", nargs="+")
    ap.add_argument("-n", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all-branches", action="store_true",
                    help="search every branch's history (default: the branch you're on)")
    a = ap.parse_args(args)
    text = " ".join(a.words)
    branch = None if a.all_branches or not repo.exists else repo.branch()
    from roomctl.publish import _import, es_from_env
    es = es_from_env()
    if es is None:
        raise GitError("room search needs Elasticsearch: ELASTIC_URL / ELASTIC_API_KEY are unset or parked")
    q = _import("elastic", "queries").Queries(es)
    hits = q.search_objects(text, size=a.n, branch=branch)
    lexical = set(q.lexical_only(text, size=50, branch=branch))   # object ids BM25 matches at all
    here = set(repo.records()) if repo.exists else set()
    out = []
    for h in hits:
        last = h.get("latest") or {}
        sha = (last.get("commit_sha") or "")
        ago = None
        if sha and repo.exists:
            r = repo.git("rev-list", "--count", f"{sha}..HEAD", check=False)
            if r.returncode == 0 and repo.git("merge-base", "--is-ancestor", sha, "HEAD", check=False).returncode == 0:
                ago = int(r.stdout.strip())
        out.append({"object_id": h["object_id"], "class": h["class"], "score": h["score"],
                    "present_now": h["object_id"] in here, "bm25": h["object_id"] in lexical,
                    "last_seen": {"commit_sha": sha, "branch": last.get("branch"), "zone": last.get("zone"),
                                  "at": last.get("@timestamp"), "pose": last.get("pose")},
                    "commits_ago": ago, "descriptions": h.get("raw_description") or []})
    if a.json:
        print(json.dumps(out, indent=2))
        return 0 if out else 1
    for o in out:
        ls = o["last_seen"]
        when = (ls["at"] or "")[11:16]
        where = f"zones/{ls['zone']}" if ls["zone"] else "?"
        back = ("now" if o["present_now"] and o["commits_ago"] == 0 else
                f"{o['commits_ago']} commit{'s' * (o['commits_ago'] != 1)} ago" if o["commits_ago"] is not None
                else f"on branch {ls['branch']}")
        found = "" if o["bm25"] else "   [vector only — keyword search would have missed it]"
        print(f"{o['object_id']} — last seen {where}, {when}, commit {ls['commit_sha'][:7]} ({back}){found}")
        if o["descriptions"]:
            print(f"    \"{o['descriptions'][0]}\"")
    if not out:
        print(f"nothing in the room's history matches \"{text}\"")
    return 0 if out else 1


def cmd_publish(repo: Repo, args: list[str]) -> int:
    repo.require()
    if "--flush" not in args:
        raise GitError("room publish --flush — send spooled commits to Elasticsearch")
    from roomctl.publish import flush
    sent = flush(repo)
    for sha, res in sent:
        print(f"{sha[:7]}  {res.line()}")
    if not sent:
        print("es: nothing spooled")
    return 1 if any(r.spooled or r.rejected for _, r in sent) else 0


def self_heal(robot, capture_id: str, commit_sha: str):
    """docs/28: a slipped grasp is retried, and once the rescan verifies the object the robot
    resolves the Sentry issue it caused. It talks to Sentry's API, so it is ON by default only
    for the real robot; ROOM_SELF_HEAL=1 forces it (a deliberate demo), =0 turns it off.
    On the real path `capture_id` must be the Pi's (POST /capture), the id the hub tags its
    issues with — a laptop-minted one would never match (docs/10 D25)."""
    mode = os.getenv("ROOM_SELF_HEAL", "http")
    real = os.getenv("ROOM_ROBOT", "mock") == "http"
    if mode == "0" or (mode == "http" and not real):
        return None
    if hasattr(robot, "context"):
        robot.context["capture_id"] = capture_id
    from robot_sentry import SelfHealingRobot
    # the real robot's failures reach Sentry through the hub; only a mock needs to file its own
    return SelfHealingRobot(robot, capture_id=capture_id, commit_sha=commit_sha, report=not real)


def make_robot(world):
    """ROOM_ROBOT=mock (default): prints, and rearranges the fake room so a rescan verifies.
    ROOM_ROBOT=http: the Pi (docs/16) — needs a job stream to learn how motions end (docs/10 D30)."""
    kind = os.getenv("ROOM_ROBOT", "mock")
    if kind == "mock":
        env = lambda k: {x for x in os.getenv(k, "").split(",") if x}  # noqa: E731
        return MockRobot(world=world, fail=env("ROOM_MOCK_FAIL"), slip_once=env("ROOM_MOCK_SLIP_ONCE"))
    if kind == "http":
        from roomctl.robot_client import HttpRobot
        robot = HttpRobot.from_env(jobs=None)
        robot.pose()  # reachable at all? before anything moves
        return robot
    raise GitError(f"ROOM_ROBOT={kind!r}: want mock or http")


def stage_fake_voxels(repo: Repo) -> None:
    """The fake room through perception's real voxelize: synthetic cloud -> VoxelGrid ->
    voxelize.stage(), with each object's claim — what a real scan stages for the commit."""
    from fake.scene_gen import object_points, scene_cloud
    from roomctl.repo import octree_cube
    _perception()
    import voxelize
    records = list(read_worktree(repo).values())
    grid = voxelize.VoxelGrid.from_points(scene_cloud(load_room(repo.path), records), cube=octree_cube())
    voxelize.stage(grid, repo.path, {r.id: object_points(r) for r in records})


def read_worktree(repo: Repo) -> dict:
    from roomctl.state import read_tree
    return read_tree(repo.path)


def restore_commit(repo: Repo, ref: str, env: dict) -> bool:
    """The git half of `room restore <ref>` (docs/31 §7.4, ANDREW-HANDOFF.md §3):
    `git restore --source=<ref> --staged --worktree -- zones`, then a commit on top of HEAD if
    that changed anything. History keeps the undo; no ref but the branch tip moves. With <ref>
    = HEAD there is nothing to commit and only the robot acts: the room goes back to HEAD."""
    sha = repo.git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", check=False).stdout.strip()
    if not sha:
        raise GitError(f"could not resolve '{ref}' to a commit — nothing was moved")
    r = repo.git("restore", f"--source={sha}", "--staged", "--worktree", "--", "zones", check=False)
    if r.returncode:
        raise GitError(f"git restore --source={ref} failed, nothing was moved: {(r.stderr or r.stdout).strip()}")
    if repo.git("diff", "--cached", "--quiet", check=False).returncode == 0:
        print(f"HEAD {repo.head()[:7]} already has {ref}'s room: nothing to commit, the robot puts it back")
        return False
    # only what restore staged: never `commit -a`, which would sweep in untracked objects
    repo.git("commit", "-q", "-m", f"Restore {ref}", "-m",
             f"git restore --source={sha} --staged --worktree -- zones", env=env)
    print(f"[{repo.branch()} {repo.head()[:7]}] Restore {ref}")
    return True


def _pose(t):
    from roomctl.state import Pose
    return Pose(t.x, t.y, t.z, int(t.yaw))


def _ext(t):
    from roomctl.state import Extents
    return Extents(t.ex, t.ey, t.ez)


def _moved_scene(name: str, world: dict):
    """The fake scene after the mock robot's pick-and-places: same objects, new poses, the
    binned ones gone. Only the fake room can be rearranged by a robot that prints."""
    from dataclasses import replace
    from fake.scene_gen import load_scene
    scene = load_scene(name)
    objs = {oid: replace(t, zone=world[oid].zone, x=world[oid].pose.x, y=world[oid].pose.y,
                         z=world[oid].pose.z, yaw=world[oid].pose.yaw)
            for oid, t in scene.objects.items() if oid in world}
    return replace(scene, name=f"{name}+robot", objects=objs, occlude={})


def _traced() -> bool:
    """`room` shows up in Sentry like every other part of the system — when there is a usable
    DSN and ROOM_SENTRY isn't "off". Commands that scanned the FAKE room are tagged so."""
    from roomctl.publish import usable
    if os.getenv("ROOM_SENTRY", "on") == "off" or not usable(os.getenv("SENTRY_DSN")):
        return False
    import obs
    return obs.init("laptop")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not _traced():
        return _main(argv)
    import contextlib
    import obs
    verb = next((x for x in argv if not x.startswith("-") and x != (argv[1] if argv[:1] == ["--repo"] else None)), "help")
    try:
        with obs.transaction("room", f"room {verb}"):
            with contextlib.suppress(Exception):
                import sentry_sdk
                spec = os.getenv("ROOM_SCANNER", "")
                fake = "--scene" in argv or spec.startswith("fake:")
                sentry_sdk.get_current_scope().set_tag("scanner", "fake" if fake else (spec.split(":")[0] or "none"))
            return _main(argv)
    finally:
        obs.flush()


def _main(argv: list[str]) -> int:
    repo_path = None
    if argv[:1] == ["--repo"] and len(argv) > 1:
        repo_path, argv = Path(argv[1]), argv[2:]
    verb = argv[0] if argv else "help"
    try:
        repo = Repo(repo_path)
        if verb == "diff":
            return repo.passthrough("diff", *argv[1:])
        if verb == "log":
            return repo.passthrough("log", *(argv[1:] or ["--graph", "--oneline", "--decorate", "--all"]))
        if verb == "add":
            if not argv[1:]:
                raise GitError("room add <paths> — e.g. `room add zones/desk/`")
            return repo.passthrough("add", *argv[1:])
        if verb in READ_VERBS:
            return repo.passthrough(verb, *argv[1:])
        if verb == "reset" and "--hard" not in argv[1:]:
            return repo.passthrough(*argv)  # soft/mixed reset only moves HEAD and the index
        if verb == "checkout" and any(x in argv[1:] for x in ("-b", "-B")):
            return repo.passthrough(*argv)  # a new branch at HEAD: the room doesn't change
        if verb in APPLY_VERBS:
            return cmd_apply(repo, verb, argv[1:])
        if verb == "publish":
            return cmd_publish(repo, argv[1:])
        if verb == "search":
            return cmd_search(repo, argv[1:])
        if verb in WRITE_VERBS:
            print(f"room {verb} changes the physical room and isn't wired to the executor yet "
                  f"(reset --hard, checkout, revert and restore are).", file=sys.stderr)
            return 2

        ap = argparse.ArgumentParser(
            prog="room", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
            # diff/log/add/reset/checkout/revert/publish are dispatched above, before argparse;
            # name them here or `room --help` makes the CLI look like it only has three verbs
            usage="room [--repo PATH] {init,status,diff,add,commit,log,reset,checkout,revert,restore,search,"
                  "publish,show,blame,branch,tag} ...")
        sub = ap.add_subparsers(dest="verb", required=True)
        p = sub.add_parser("init", help="first scan, first commit")
        p.add_argument("--scene", help="fake scene to scan (default: $ROOM_SCANNER)")
        p.add_argument("-m", "--message")
        p.add_argument("--room-from", default="clean_bench",
                       help="with no scanner: take zones/anchor/roomignore from this scene")
        p = sub.add_parser("status", help="scan, then show what moved since the last commit")
        p.add_argument("--scene", help="fake scene to scan (default: $ROOM_SCANNER)")
        p.add_argument("--no-scan", action="store_true", help="don't look; show the tree as last scanned")
        p.add_argument("--json", action="store_true", help="the docs/16 /api/status shape")
        p.add_argument("--exit-code", action="store_true", help="exit 1 when the room is dirty")
        p = sub.add_parser("commit", help="scan, then record the room as it is now")
        p.add_argument("-m", "--message", required=True)
        p.add_argument("--scene", help="fake scene to scan (default: $ROOM_SCANNER)")
        p.add_argument("--no-scan", action="store_true", help="commit the tree as last scanned")
        sub.add_parser("help")
        a = ap.parse_args(argv)
        if a.verb == "help":
            ap.print_help()
            return 0
        return {"init": cmd_init, "status": cmd_status, "commit": cmd_commit}[a.verb](repo, a)
    except (GitError, SchemaError, RobotError) as e:
        print(f"fatal: {e}", file=sys.stderr)
        return 128


if __name__ == "__main__":
    sys.exit(main())
