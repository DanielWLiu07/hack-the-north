#!/usr/bin/env python3
"""scripts/demo_state.py — save the room as it is now, and put it back between demos.

    demo_state.py save tidy -n "everything where it belongs"    snapshot the room under a name
    demo_state.py list                                          every snapshot, newest first
    demo_state.py restore tidy                                  put the room back, ready to run again
    demo_state.py status                                        what the room is right now

WHAT A SNAPSHOT HOLDS: every ref in room.git (branches and tags), which branch HEAD was on, and the
WORKING TREE — the room as last scanned, dirty or clean, because "the mug is out of place" lives there
and not in a commit.

WHAT RESTORE ALSO CLEARS, and the reason this script exists rather than `git reset`: the job ledger.
A job id is a hash of what it would DO (command + target + HEAD + the room as scanned), so running the
same demo twice returns the FIRST job, replayed, and the robot does not move the second time. Restoring
a room without clearing its ledger gives you a demo that works once. Elasticsearch is never touched:
it is append-only history, and the demo reads it.

NOTHING IS LOST BY RESTORING. The room as it was is auto-saved first (`before-<name>-<time>`), and every
branch tip is kept as `refs/demo-backup/<time>/<branch>` so no commit becomes unreachable.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATES = Path(os.getenv("DEMO_STATES_DIR") or "~/.cache/gitspace/demo-states").expanduser()
NAME_OK = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


def room() -> Path:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    raw = os.getenv("ROOM_GIT_PATH") or "./room.git"
    p = Path(raw).expanduser()
    p = p if p.is_absolute() else (ROOT / p).resolve()
    if not (p / ".git").exists() and not (p / "HEAD").exists():
        raise SystemExit(f"no room repository at {p}")
    return p


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=60)
    if check and r.returncode:
        raise SystemExit(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def ledger_dir(repo: Path) -> Path:
    import hashlib
    base = Path(os.getenv("JOBS_DIR") or "~/.cache/gitspace/jobs").expanduser()
    return base / hashlib.sha1(str(repo.resolve()).encode()).hexdigest()[:12]


def local(iso: str) -> str:
    """Saved times are stored UTC and read back in YOUR clock: a demo is reverted by a person
    standing at a table, and "05:06" for one in the morning is a state you distrust."""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%b %d %H:%M")
    except (TypeError, ValueError):
        return iso[5:16].replace("T", " ")


def describe(repo: Path) -> dict:
    dirty = [l for l in git(repo, "status", "--porcelain=v1", "--untracked-files=all").splitlines() if l]
    return {"head": git(repo, "rev-parse", "HEAD")[:7], "subject": git(repo, "log", "-1", "--format=%s"),
            "branch": git(repo, "symbolic-ref", "--short", "HEAD", check=False) or "(detached)",
            "dirty": len(dirty),
            # branches, tags and remotes — NOT refs/demo-backup, which restore deliberately leaves behind.
            # Counting those made a restored room read as drifted from the state it had just been set to.
            "refs": len(git(repo, "for-each-ref", "refs/heads", "refs/tags", "refs/remotes").splitlines())}


def do_save(name: str, note: str, quiet: bool = False) -> Path:
    repo = room()
    into = STATES / name
    if into.exists():
        shutil.rmtree(into)
    into.mkdir(parents=True)
    (into / "refs.txt").write_text(git(repo, "for-each-ref", "--format=%(refname) %(objectname)"))
    with tarfile.open(into / "worktree.tar", "w") as tar:      # the room as scanned, dirty or not
        for item in sorted(repo.iterdir()):
            if item.name != ".git":
                tar.add(item, arcname=item.name)
    meta = {"name": name, "note": note, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "room": str(repo), "head_full": git(repo, "rev-parse", "HEAD"), **describe(repo)}
    (into / "state.json").write_text(json.dumps(meta, indent=1))
    if not quiet:
        print(f"saved '{name}': {meta['head']} {meta['subject']!r} on {meta['branch']}, "
              f"{meta['dirty']} uncommitted change(s), {meta['refs']} refs"
              + (f"\n  note: {note}" if note else ""))
    return into


def do_list() -> int:
    if not STATES.exists() or not any(STATES.iterdir()):
        print(f"no saved states yet ({STATES}). Save one: demo_state.py save tidy -n 'the room at rest'")
        return 0
    rows = []
    for d in STATES.iterdir():
        try:
            rows.append(json.loads((d / "state.json").read_text()))
        except (OSError, ValueError):
            continue
    now = describe(room())
    wide = max([len(m["name"]) for m in rows] + [len("name")]) + 2
    print(f"{'name':{wide}} {'saved':14} {'head':8} {'branch':12} dirty  note")
    for m in sorted(rows, key=lambda m: m["at"], reverse=True):
        here = "  <- the room is here now" if (m["head"] == now["head"] and m["dirty"] == now["dirty"]) else ""
        print(f"{m['name']:{wide}} {local(m['at']):14} {m['head']:8} {m['branch']:12} "
              f"{m['dirty']:>5}  {m.get('note', '')}{here}")
    print(f"\nthe room right now: {now['head']} {now['subject']!r}, {now['dirty']} uncommitted change(s)")
    return 0


def do_restore(name: str, keep_ledger: bool = False) -> int:
    repo, into = room(), STATES / name
    if not (into / "state.json").exists():
        raise SystemExit(f"no saved state called {name!r} — demo_state.py list")
    meta = json.loads((into / "state.json").read_text())
    stamp = time.strftime("%m%d-%H%M%S")
    do_save(f"before-{name}-{stamp}", f"auto-saved before restoring {name!r}", quiet=True)
    for line in git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads").splitlines():
        ref, sha = line.split()
        git(repo, "update-ref", f"refs/demo-backup/{stamp}/{ref.split('/')[-1]}", sha, check=False)

    saved = [l.split() for l in (into / "refs.txt").read_text().splitlines() if l.strip()]
    for ref, sha in saved:                                     # every branch and tag back to its sha
        git(repo, "update-ref", ref, sha, check=False)
    for line in git(repo, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags").splitlines():
        if line and line not in {r for r, _ in saved}:          # refs made since the save
            git(repo, "update-ref", "-d", line, check=False)
    branch = meta.get("branch", "main")
    if branch != "(detached)":
        git(repo, "symbolic-ref", "HEAD", f"refs/heads/{branch}", check=False)
    git(repo, "reset", "--hard", meta["head_full"], check=False)

    for item in repo.iterdir():                                 # the working tree, exactly as saved
        if item.name != ".git":
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    with tarfile.open(into / "worktree.tar") as tar:
        tar.extractall(repo)
    git(repo, "add", "-A", check=False)                         # so `git status` reads like it did
    git(repo, "reset", check=False)

    cleared = 0
    led = ledger_dir(repo)
    if not keep_ledger and led.exists():
        cleared = len(list(led.rglob("*.json")))
        shutil.rmtree(led)
    now = describe(repo)
    print(f"restored '{name}': {now['head']} {now['subject']!r} on {now['branch']}, "
          f"{now['dirty']} uncommitted change(s)")
    print(f"  job ledger: {'kept' if keep_ledger else f'cleared ({cleared} job(s)) — the next demo dispatches fresh'}")
    print(f"  the room as it was: saved as 'before-{name}-{stamp}', branch tips at refs/demo-backup/{stamp}/")
    print("  Elasticsearch untouched (append-only history; the demo reads it)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("save", help="snapshot the room under a name")
    s.add_argument("name")
    s.add_argument("-n", "--note", default="")
    sub.add_parser("list", help="every snapshot, newest first")
    r = sub.add_parser("restore", help="put the room back and clear the job ledger")
    r.add_argument("name")
    r.add_argument("--keep-ledger", action="store_true", help="do NOT clear jobs (a repeat will replay them)")
    sub.add_parser("status", help="what the room is right now")
    a = ap.parse_args()
    if a.cmd == "save":
        if not a.name or set(a.name) - NAME_OK:
            raise SystemExit("a name is lowercase letters, digits, - and _")
        do_save(a.name, a.note)
        return 0
    if a.cmd == "list":
        return do_list()
    if a.cmd == "restore":
        return do_restore(a.name, a.keep_ledger)
    d = describe(room())
    print(f"{d['head']} {d['subject']!r} on {d['branch']} · {d['dirty']} uncommitted change(s) · {d['refs']} refs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
