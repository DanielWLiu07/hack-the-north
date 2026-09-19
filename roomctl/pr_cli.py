"""`room pr ...`, `room why <commit>`, and the phrase behind `room restore --before "<phrase>"` (wired in cli.py).

    room pr open <object> --to <zone> [--title T] [--author NAME]    propose moving one object: a decision, asked for
    room pr list [--all] [--json]                                    open ones (or every one)
    room pr approve <n> [--by NAME]                                  merge it: `main` moves first, the robot second
    room pr close <n>                                                no; history keeps the branch
    room why [<commit>] [--json]                                     was that commit's picture of the room trustworthy?
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from roomctl import pr
from roomctl.repo import GitError, Repo


def _who(repo: Repo, given: str | None) -> str:
    if given:
        return given
    name = os.getenv("ROOM_AUTHOR", "").strip() or repo.git("config", "user.name", check=False).stdout.strip()
    return name or os.getenv("USER", "someone")


def _line(p, paint) -> str:
    color = {"open": "33", "merged": "32", "closed": "90"}.get(p.status, "0")
    ops = ", ".join(f"{o.get('object_id')} -> {(o.get('to') or {}).get('zone', '?')}" for o in p.ops) or "no changes"
    tail = f"  merged in {p.merged_in[:7]}" if p.merged_in else ""
    return f"#{p.id:<3} {paint(color, f'{p.status:<7}')} {p.title}   ({ops}; by {p.author or '?'}){tail}"


def cmd_pr(repo: Repo, args: list[str], Paint) -> int:
    repo.require()
    ap = argparse.ArgumentParser(prog="room pr", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="what", required=True)
    o = sub.add_parser("open", help="propose moving an object to another zone")
    o.add_argument("object_id")
    o.add_argument("--to", required=True, dest="zone", metavar="ZONE")
    o.add_argument("--title")
    o.add_argument("--author")
    o.add_argument("--json", action="store_true")
    li = sub.add_parser("list", help="pull requests")
    li.add_argument("--all", action="store_true", help="merged and closed ones too")
    li.add_argument("--json", action="store_true")
    ap_ = sub.add_parser("approve", help="merge it into the current branch")
    ap_.add_argument("id", type=int)
    ap_.add_argument("--by", help="who approves (default: $ROOM_AUTHOR, git's user.name)")
    ap_.add_argument("--json", action="store_true")
    c = sub.add_parser("close", help="decline it")
    c.add_argument("id", type=int)
    a = ap.parse_args(args)
    paint = Paint(sys.stdout.isatty())
    if a.what == "open":
        p = pr.propose(repo, a.object_id, a.zone, _who(repo, a.author), a.title)
        print(json.dumps(p.to_dict(), indent=2) if a.json else
              f"opened {_line(p, paint)}\n  approve it with: room pr approve {p.id}")
    elif a.what == "list":
        prs = [p for p in pr.list_prs(repo) if a.all or p.status == "open"]
        if a.json:
            print(json.dumps([p.to_dict() for p in prs], indent=2))
        else:
            print("\n".join(_line(p, paint) for p in prs) or "no open pull requests")
    elif a.what == "approve":
        sha = pr.approve(repo, a.id, _who(repo, a.by))
        print(json.dumps({"id": a.id, "merge_sha": sha}) if a.json else
              f"merged #{a.id} as {sha[:7]}. The room now differs from {repo.branch()}: that is the robot's job,\n"
              f"and `room status` shows it as a decision until the robot has carried it out.")
    else:
        pr.close(repo, a.id)
        print(f"closed #{a.id}")
    return 0


def cmd_why(repo: Repo, args: list[str]) -> int:
    repo.require()
    ap = argparse.ArgumentParser(prog="room why", description="was this commit's picture of the room trustworthy?")
    ap.add_argument("ref", nargs="?", default="HEAD")
    ap.add_argument("--seconds", type=float, default=2.0, help="how much telemetry before the shutter to look at")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(args)
    from roomctl import why
    from roomctl.publish import es_from_env
    es = es_from_env()
    if es is None:
        raise GitError("room why needs Elasticsearch (the capture, its gate and the telemetry live there): "
                       "ELASTIC_URL / ELASTIC_API_KEY are unset or parked")
    d = why.explain(repo, a.ref, es, a.seconds)
    print(json.dumps(d, indent=2, default=str) if a.json else why.render(d))
    return 0 if d.get("trustworthy", True) else 1


def ref_before(repo: Repo, phrase: str) -> dict:
    """"before dinner" -> the last commit on this branch before then: {sha, message, at, source}."""
    from roomctl import when
    from roomctl.publish import es_from_env
    try:
        t = when.parse_when(phrase)
    except ValueError as e:
        raise GitError(f"--before {phrase!r}: {e}") from None
    return {**when.commit_before(repo, t, es=es_from_env()), "when": t.isoformat(timespec="minutes")}
