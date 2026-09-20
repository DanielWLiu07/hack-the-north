#!/usr/bin/env python3
"""scripts/seer_sweep.py — Seer working through the open Sentry issues, bounded, and unable to touch the repo.

    ./scripts/seer_sweep.py --dry-run     what it WOULD ask about and why it skips the rest. Spends nothing.
    ./scripts/seer_sweep.py --once        one sweep, then stop
    ./scripts/seer_sweep.py               sweep, sleep, sweep — what runs in tmux window `seer-sweep`
    ./scripts/seer_sweep.py --tmux        start that window; refuses if one is already running

WHAT IT DOES. Picks unresolved issues that no Seer run has looked at, asks Seer for a ROOT CAUSE, waits,
and writes what came back to `docs/seer/` — one file per issue plus an index. A run that somebody already
paid for is READ, never re-bought, so the runs started by hand are collected here for free.

WHAT IT WILL NOT DO. It stops Seer at `root_cause` (or `solution`, if you ask for it). It will refuse to
start at all with a stopping point that makes Seer write code — `code_changes` and `open_pr` are rejected
by name, not by comment. Nothing it collects is applied to the repository; a proposal is a document you
read, and a person routes it to whoever owns the file.

SPEND IS CAPPED IN THE CODE, not in a docstring. Every cap below is read from the environment at startup
and the script REFUSES TO START if any of them is unreadable, because a cap you cannot parse is not a cap.
The per-hour budget is kept on disk, so restarting the script does not restart the budget.

    SEER_SWEEP_PER_SWEEP     6      runs this process may START in one sweep
    SEER_SWEEP_PER_HOUR     10      runs started in any rolling 60 minutes, across restarts
    SEER_SWEEP_MIN_GAP_S    90      seconds between two starts
    SEER_SWEEP_INTERVAL_S  900      seconds between sweeps
    SEER_SWEEP_MAX_WAIT_S  420      how long to wait for one run before giving up on it
    SEER_SWEEP_POLL_S        6      seconds between polls of a running Seer
    SEER_SWEEP_THIN_STREAK   3      paid runs in a row with nothing readable before it STOPS and says so
    SEER_SWEEP_STOPPING_POINT  root_cause     root_cause | solution. Nothing else is accepted.
    SEER_SWEEP_SKIP          -      extra short ids to leave alone, comma separated
    SEER_SWEEP_DIR    docs/seer     where the findings are written

WHAT IT SKIPS, and why. Connectivity notices (`robot: robot_unreachable`, `… robot_server_down`, the
`_recovered` counterparts) are link and power states: a debugging agent reading our source has nothing to
say about a robot somebody unplugged. Deploy noise (`ModuleNotFoundError`, a port already bound) is a
laptop that started a server from the wrong directory. Info-level events are smoke tests. Everything else
is a defect, and the loudest one goes first.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

from dotenv import load_dotenv                                          # noqa: E402

load_dotenv(ROOT / ".env")

import sentry_client as sc                                              # noqa: E402

STATE = Path("~/.cache/gitspace/seer-sweep").expanduser()
RAW = STATE / "raw"
SESSION, WINDOW = "htn", "seer-sweep"

# A stopping point past these two lets Seer write code. The sweep is unattended; it never asks for that.
ALLOWED_STOPS = ("root_cause", "solution")
FORBIDDEN_STOPS = ("code_changes", "open_pr", "pr", "code")

# `robot: <kind> — …`: the kinds that are a cable, a battery or a wifi router, not a defect in our code.
LINK_OR_POWER = {"robot_unreachable", "robot_server_down", "robot_forbidden", "robot_restarted"}
DEPLOY_NOISE = re.compile(r"ModuleNotFoundError|No module named|address already in use|cannot import bbos", re.I)
ROBOT_KIND = re.compile(r"^robot:\s*([a-z0-9_]+)")
# Seer writing about a fix that has already landed. Worth shouting about: the issue wants resolving, not fixing.
# Every phrasing seen in a real run so far. "Both fixes are already in the codebase" is the one that reads
# like prose and matters most: the issue wants RESOLVING in Sentry, and an edit would be a second fix.
ALREADY_FIXED = re.compile(r"already (?:been )?fixed|already (?:landed|resolved|addressed|deployed)|"
                           r"already in the (?:codebase|repo|repository|code)|has since been fixed|"
                           r"was fixed in|fixed by commit|subsequently fixed|resolved in commit|"
                           r"fix(?:es|ed)?\b[^.\n]{0,60}\balready\b|"
                           r"no longer (?:present|exists|applies)", re.I)
NAMED_FILE = re.compile(r"\b(?:[\w.-]+/)*[\w.-]+\.(?:py|js|yaml|yml|json|sh|md)\b")
NAMED_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")


def _int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip() or raw.strip().startswith("#"):
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise SystemExit(f"REFUSING TO START: {name}={raw.strip()!r} is not a whole number. A cap that "
                         f"cannot be read is not a cap.") from None
    if not lo <= value <= hi:
        raise SystemExit(f"REFUSING TO START: {name}={value} is outside {lo}..{hi}.")
    return value


def caps() -> dict:
    stop = (os.getenv("SEER_SWEEP_STOPPING_POINT") or "root_cause").strip().lower()
    if stop in FORBIDDEN_STOPS:
        raise SystemExit(f"REFUSING TO START: SEER_SWEEP_STOPPING_POINT={stop!r} would let Seer write code "
                         f"unattended. This sweep collects findings; a person routes the fix.")
    if stop not in ALLOWED_STOPS:
        raise SystemExit(f"REFUSING TO START: SEER_SWEEP_STOPPING_POINT={stop!r} is not one of {', '.join(ALLOWED_STOPS)}.")
    return {"per_sweep": _int("SEER_SWEEP_PER_SWEEP", 6, 0, 25),
            "per_hour": _int("SEER_SWEEP_PER_HOUR", 10, 0, 40),
            "min_gap_s": _int("SEER_SWEEP_MIN_GAP_S", 90, 0, 3600),
            "interval_s": _int("SEER_SWEEP_INTERVAL_S", 900, 30, 86400),
            "max_wait_s": _int("SEER_SWEEP_MAX_WAIT_S", 420, 30, 3600),
            "poll_s": _int("SEER_SWEEP_POLL_S", 6, 1, 120),
            "stop_at": stop,
            "thin_streak": _int("SEER_SWEEP_THIN_STREAK", 3, 1, 20),
            "skip": {s.strip().upper() for s in (os.getenv("SEER_SWEEP_SKIP") or "").split(",") if s.strip()},
            "dir": ROOT / (os.getenv("SEER_SWEEP_DIR") or "docs/seer")}


# ── the budget, on disk, so a restart does not restart the spend ──────────────────────────────────
def _ledger() -> list[dict]:
    try:
        rows = json.loads((STATE / "state.json").read_text())["started"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("t"), (int, float))]


def _spent(rows: list[dict], window_s: float = 3600) -> int:
    now = time.time()
    return sum(1 for r in rows if now - r["t"] < window_s)


def _record(short_id: str, run_id, rows: list[dict]) -> list[dict]:
    rows = [*rows, {"t": time.time(), "at": _now(), "issue": short_id, "run_id": run_id}][-500:]
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "state.json").write_text(json.dumps({"started": rows}, indent=1))
    return rows


def skip_file() -> set[str]:
    """Short ids in `<state>/skip.txt`, re-read EVERY sweep so a person can retire an issue from the queue
    without stopping the loop. One id per line; `#` starts a comment. The reason to reach for it: several
    issues share one cause — the head camera being off the USB bus explains GITSPACE-15, 1B, 1H, T and V —
    and buying a run per symptom fills the directory without learning anything."""
    try:
        lines = (STATE / "skip.txt").read_text().splitlines()
    except OSError:
        return set()
    return {l.split("#")[0].strip().upper() for l in lines if l.split("#")[0].strip()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clock() -> str:
    return datetime.now().strftime("%H:%M:%S")


def say(line: str = "") -> None:
    print(line, flush=True)


# ── which issues are worth a run ──────────────────────────────────────────────────────────────────
def skip_reason(issue: dict, cfg: dict) -> str | None:
    short = (issue.get("short_id") or "").upper()
    title = issue.get("title") or ""
    if short in cfg["skip"]:
        return "on the skip list"
    if str(issue.get("level") or "").lower() == "info":
        return "info level — a notice, not a defect"
    kind = ROBOT_KIND.match(title)
    if kind and (kind.group(1).endswith("_recovered") or kind.group(1) in LINK_OR_POWER):
        return "a link or power state, not our code"
    if DEPLOY_NOISE.search(title):
        return "deploy noise — a server started in the wrong place"
    if not sc.ISSUE_ID.match(str(issue.get("id") or "")):
        return "Sentry gave no usable issue id"
    return None


def finding_path(cfg: dict, short_id: str) -> Path:
    return cfg["dir"] / f"{short_id}.md"


# ── writing the artefact ──────────────────────────────────────────────────────────────────────────
def _proposed(auto: dict) -> list[str]:
    """Files a run PROPOSES to change. A root-cause run has none; a `solution` run may. They are named
    here and never opened: this process does not write to the repository."""
    out: list[str] = []
    for b in auto.get("blocks") or []:
        for key in ("file_patches", "merged_file_patches"):
            for patch in (b.get(key) if isinstance(b, dict) else None) or []:
                if isinstance(patch, dict):
                    name = patch.get("path") or patch.get("file_path") or patch.get("filename")
                    if name:
                        out.append(str(name))
                elif isinstance(patch, str):
                    out.append(patch[:120])
    return list(dict.fromkeys(out))[:12]


def _depth(auto: dict) -> str:
    """How much work the run did, for the index: turns Seer took and tools it called."""
    blocks = [b for b in auto.get("blocks") or [] if isinstance(b, dict)]
    turns = sum(1 for b in blocks if (b.get("message") or {}).get("role") == "assistant")
    tools = sum(len(b.get("tool_results") or []) for b in blocks)
    return f"{turns} turn(s), {tools} tool call(s)" if turns else "one answer"


def _named(text: str) -> tuple[list[str], list[str]]:
    found = [f for f in dict.fromkeys(NAMED_FILE.findall(text)) if "/" in f or f.endswith(".py")]
    files = [f for f in found if "/" in f or not any(o.endswith("/" + f) for o in found)]
    shas = [s for s in dict.fromkeys(NAMED_SHA.findall(text)) if not s.isdigit()]
    return files[:8], shas[:6]


def _fixed_sentence(text: str, at: re.Match) -> str:
    """The one SENTENCE that says it is already fixed, on a single line. A character window around the
    match ran across a paragraph break, and a blank line ends a markdown blockquote — the banner fell
    apart into body text that read like the finding itself."""
    line = text[text.rfind("\n", 0, at.start()) + 1: (text.find("\n", at.end()) + 1 or len(text) + 1) - 1]
    return " ".join(line.split())[:320].strip() or " ".join(text[at.start():at.end() + 160].split())


def write_finding(cfg: dict, issue: dict, run: dict, text: str, stop_at: str, reused: bool,
                  auto: dict | None = None) -> Path:
    short = issue["short_id"]
    auto = auto or {}
    files, shas = _named(text)
    fixed = ALREADY_FIXED.search(text)
    seen = f"{issue.get('count')} times"
    if issue.get("first_seen") and issue.get("last_seen"):
        seen += f", {issue['first_seen'][:16].replace('T', ' ')} → {issue['last_seen'][:16].replace('T', ' ')}"
    title = (issue.get("title") or "").strip()
    body = [f"# {short} — {title[:108] + '…' if len(title) > 110 else title}", "",
            "| | |", "|---|---|",
            f"| Sentry | {issue.get('permalink') or '—'} |",
            f"| seen | {seen} |",
            f"| level | {issue.get('level') or '—'} |",
            f"| culprit | `{issue.get('culprit') or '—'}` |",
            f"| Seer run | `{run.get('run_id') or '—'}` · {run.get('status') or '—'} · stopped at `{stop_at}` · {_depth(auto)} |",
            f"| collected | {_now()} by `scripts/seer_sweep.py`{' (read from a run that already existed — not re-bought)' if reused else ''} |",
            ""]
    if fixed:
        body += ["> ## ALREADY FIXED", ">",
                 f"> Seer says the fix has already landed — *“{_fixed_sentence(text, fixed)}”*", ">",
                 "> **Resolve the issue in Sentry rather than editing the file.** Check the commit it names first.", ""]
    if files:
        body += ["**Files Seer named:** " + ", ".join(f"`{f}`" for f in files), ""]
    if shas:
        body += ["**Commits Seer named:** " + ", ".join(f"`{s}`" for s in shas), ""]
    if proposed := _proposed(auto):
        body += ["> **Seer proposed a patch** touching " + ", ".join(f"`{f}`" for f in proposed) + ".",
                 "> **It was not applied.** Read it in the raw run and route it to whoever owns the file.", ""]
    body += [f"## Root cause, in Seer's words", "", text.strip(), "", "---", "",
             f"Raw run: `{'~/' + str((RAW / (short + '.json')).relative_to(Path.home()))}`. Nothing here was applied to the repository: the sweep stops "
             f"Seer at `{stop_at}` and never asks it for code changes or a pull request."]
    path = finding_path(cfg, short)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(body) + "\n")
    return path


def _excerpt(text: str) -> str:
    """One readable line for the index. Seer's answers open with a heading — "**Root cause identified.**"
    — so the first line is a label, not the finding; take the first line with something in it."""
    # Fenced code first, and before the backticks are stripped — otherwise a fence stops looking like a
    # fence and the index row becomes a line of Python (`n = world.n - (world.skew ...)`), which tells a
    # reader nothing about what was wrong.
    plain = re.sub(r"```.*?```", " ", text, flags=re.S)
    plain = re.sub(r"[*`>#]+", "", plain)
    lines = [" ".join(l.split()) for l in plain.splitlines() if l.strip() and not l.startswith("---")]
    # A line ending in ":" INTRODUCES the finding ("Here's the root cause analysis for GITSPACE-6:",
    # "Key findings:"). It is long enough to pass a length test and says nothing.
    pick = next((l for l in lines if len(l) > 45 and not l.endswith(":")),
                next((l for l in lines if len(l) > 45), lines[0] if lines else ""))
    return (pick[:145] + "…") if len(pick) > 146 else pick


def write_index(cfg: dict) -> Path:
    rows = []
    for md in sorted(cfg["dir"].glob("GITSPACE-*.md")):
        text = md.read_text()
        head = text.splitlines()[0].lstrip("# ").strip()
        short, _, title = head.partition(" — ")
        first = ""
        if "## Root cause, in Seer's words" in text:
            first = _excerpt(text.split("## Root cause, in Seer's words", 1)[1])
        files = re.search(r"\*\*Files Seer named:\*\* (.+)", text)
        rows.append({"short": short, "title": title[:70], "found": first,
                     "files": (files.group(1) if files else "—")[:80],
                     "fixed": "**already fixed**" if "## ALREADY FIXED" in text else "",
                     "file": md.name})
    out = ["# `docs/seer/` — what Sentry's agent found in our code", "",
           "Seer read our traces, the breadcrumbs attached to each issue, and this repository, and said what was",
           "wrong. One file per issue, written by `scripts/seer_sweep.py`.", "",
           "**Nothing here was applied.** The sweep stops Seer at a root cause; it never asks for code changes or",
           "a pull request, and it refuses to start with a stopping point that would. A finding is a document a",
           "person reads, and a person routes to whoever owns the file. Where Seer says an issue is ALREADY FIXED",
           "in a later commit, the issue wants resolving in Sentry — not another edit.", "",
           f"{len(rows)} issue(s) collected, newest sweep {_now()}.", "",
           "| issue | what it is | what Seer found | files it named |", "|---|---|---|---|"]
    for r in rows:
        out.append(f"| [{r['short']}]({r['file']}) {r['fixed']} | {r['title']} | {r['found']} | {r['files']} |")
    path = cfg["dir"] / "README.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")
    return path


# ── one issue ─────────────────────────────────────────────────────────────────────────────────────
async def look_at(client, issue: dict, cfg: dict, *, may_start: bool, write: bool, budget_line: str,
                  head=lambda: None, charge=lambda run_id: None, gate=None) -> dict:
    """Read the issue's Seer run, starting one only if `may_start`, writing a finding only if `write`.
    A dry run has no effect anywhere: it does not start a run and it does not touch `docs/seer/`.

    `charge` is called THE INSTANT the POST returns, before the first poll — the budget is spent when the
    run is bought, not when it answers, and a run that returns nothing readable cost exactly as much as one
    that named the file. Recording it after the wait lost the entry whenever the process was stopped
    mid-poll, which is a budget that quietly forgets what it spent. `paid` in the result says the same
    thing to the caller, for the patience counter."""
    paid = False
    org = client.state()["org"]
    path = f"/organizations/{org}/issues/{issue['id']}/autofix/"
    short = issue["short_id"]
    try:
        auto = sc._autofix(await client._get(path))                     # noqa: SLF001 — free, starts nothing
    except sc.SentryError as e:
        return {"outcome": "error", "why": f"could not read the run — {e.code}: {e.detail}"}
    status = str((auto or {}).get("status") or "").upper()
    reused = auto is not None and status not in (sc.DONE - sc.ANSWERED)
    if auto is not None and status in (sc.DONE - sc.ANSWERED):
        auto, reused = None, False                                      # the last run died: asking again is fair
    if auto is None:
        if not may_start:
            return {"outcome": "would_start"}
        try:
            setup = await client.seer_setup(issue["id"])
        except sc.SentryError as e:
            return {"outcome": "error", "why": f"could not check setup — {e.code}: {e.detail}"}
        if setup["blocker"]:
            return {"outcome": "blocked", "why": setup["blocker"]}
        if gate is not None:
            await gate()                                 # the spacing between PURCHASES, not between reads
        head()
        say(f"    asking Seer — {budget_line}")
        try:
            started = await client._request("POST", path, body={                        # noqa: SLF001
                "stopping_point": cfg["stop_at"],
                "instruction": ("This error comes from a robot that moves objects in a real room. Find the root "
                                "cause in this repository and name the file and the commit that introduced it. "
                                "If a later commit already fixes it, say so plainly and name that commit.")})
        except sc.SentryError as e:
            return {"outcome": "error", "why": f"could not start the run — {e.code}: {e.detail}"}
        paid = True
        run_id = sc._dig(started, "run_id")                             # noqa: SLF001
        charge(run_id)                                                  # on the ledger before we wait
        began = time.time()
        while True:
            await asyncio.sleep(cfg["poll_s"])
            try:
                auto = sc._autofix(await client._get(path))             # noqa: SLF001
            except sc.SentryError as e:
                return {"outcome": "error", "paid": paid, "why": f"could not poll the run — {e.code}: {e.detail}",
                        "run": {"run_id": run_id, "status": None}}
            status = str((auto or {}).get("status") or "").upper()
            if status in sc.DONE:
                break
            if time.time() - began > cfg["max_wait_s"]:
                return {"outcome": "slow", "paid": paid, "why": f"still {status.lower() or 'starting'} after "
                        f"{int(time.time() - began)} s — left running, the next sweep reads it",
                        "run": {"run_id": run_id, "status": status or None}}
        took = int(time.time() - began)
    else:
        took = None
    run = {"run_id": sc._dig(auto, "run_id"), "status": status}         # noqa: SLF001
    if status not in sc.DONE:
        # Somebody's earlier run is still going — this sweep, a previous one, or the Sentry UI. It is
        # already bought, so there is nothing to do but come back for it.
        return {"outcome": "running", "paid": paid, "run": run,
                "why": f"a run is still {status.lower() or 'starting'} — the next sweep reads it for free"}
    if status not in sc.ANSWERED:
        return {"outcome": "no_answer", "paid": paid, "why": f"the run ended {status or 'with no status'}", "run": run}
    text = sc._verdict_text(auto)                                       # noqa: SLF001
    if not text:
        return {"outcome": "thin", "paid": paid, "why": f"the run is {status} but carries nothing readable", "run": run}
    common = {"run": run, "text": text, "took": took, "paid": paid, "reused": reused,
              "fixed": bool(ALREADY_FIXED.search(text))}
    if not write:
        return {"outcome": "would_collect", **common}
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"{short}.json").write_text(json.dumps(auto, indent=1)[:2_000_000])
    return {"outcome": "collected", "path": write_finding(cfg, issue, run, text, cfg["stop_at"], reused, auto), **common}


# ── one sweep ─────────────────────────────────────────────────────────────────────────────────────
async def sweep(client, cfg: dict, *, dry: bool, refresh: bool, thin: list) -> dict:
    rows = _ledger()
    say(f"── sweep at {_clock()} · caps: {cfg['per_sweep']}/sweep, {cfg['per_hour']}/hour "
        f"(spent {_spent(rows)} in the last hour), {cfg['min_gap_s']} s apart, stopping at {cfg['stop_at']}")
    try:
        issues = await client.recent_issues(limit=50, stats_period="14d")
    except sc.SentryError as e:
        say(f"   Sentry would not list the issues — {e.code}: {e.detail}")
        return {"started": 0, "collected": 0, "error": e.code}
    issues.sort(key=lambda i: (-int(i.get("count") or 0), i.get("short_id") or ""))
    skipped: dict[str, int] = {}
    queue = []
    live = {**cfg, "skip": cfg["skip"] | skip_file()}        # re-read each sweep: editable while it runs
    for i in issues:
        why = skip_reason(i, live)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        if finding_path(live, i["short_id"]).exists() and not refresh:
            skipped["already collected"] = skipped.get("already collected", 0) + 1
            continue
        queue.append(i)
    say(f"   {len(issues)} unresolved · {len(queue)} worth a look · skipped: "
        + ("; ".join(f"{n} {why}" for why, n in sorted(skipped.items(), key=lambda kv: -kv[1])) or "nothing"))

    started = collected = 0
    waiting: list[str] = []
    last_start = max((r["t"] for r in rows), default=0.0)
    for issue in queue:
        short, title = issue["short_id"], (issue.get("title") or "")[:88]
        shown = []
        def head(short=short, title=title, count=issue.get("count"), shown=shown):
            if not shown:
                shown.append(1)
                say(f"  {short:<12} {count:>4}x  {title}")
        room_in_sweep = started < cfg["per_sweep"]
        room_in_hour = _spent(rows) < cfg["per_hour"]
        may_start = not dry and room_in_sweep and room_in_hour

        async def gate():
            """Waited only once we know we are BUYING. Waiting before the free read meant 90 seconds of
            nothing for every issue whose run was already paid for and only needed collecting."""
            if (g := cfg["min_gap_s"] - (time.time() - last_start)) > 0:
                head()
                say(f"    waiting {int(g)} s — runs are kept {cfg['min_gap_s']} s apart")
                await asyncio.sleep(g)

        budget = (f"run {started + 1} of {cfg['per_sweep']} this sweep, "
                  f"{_spent(rows) + 1} of {cfg['per_hour']} this hour")
        def charge(run_id, short=short):
            nonlocal rows, started, last_start
            rows = _record(short, run_id, rows)
            started += 1
            last_start = time.time()

        out = await look_at(client, issue, cfg, may_start=may_start, write=not dry,
                            budget_line=budget, head=head, charge=charge, gate=gate)
        if out["outcome"] == "would_start":
            # No run exists and we may not buy one. Say nothing per issue — keep walking the queue,
            # because every issue that ALREADY has a run is still collectable here for nothing.
            waiting.append(short)
            if dry:
                head()
                say("    no run yet · dry run — nothing started")
            continue
        head()
        if out["outcome"] == "would_collect":
            say(f"    a run already exists and is readable ({out['run']['status'].lower()}) — would write "
                f"docs/seer/{short}.md for free")
            continue
        if out["outcome"] == "collected":
            collected += 1
            files, shas = _named(out["text"])
            opening = out["text"].strip().splitlines()[0][:110]       # not `head`: that is the line printer
            how = f"in {out['took']} s" if out.get("took") is not None else "already run — read for free"
            say(f"    Seer answered ({how}) — {opening}")
            if files or shas:
                say(f"    it named {', '.join(files[:4]) or 'no file'}"
                    + (f" · commit {', '.join(shas[:2])}" if shas else ""))
            if out["fixed"]:
                say(f"    *** ALREADY FIXED in a later commit — resolve the issue, do not edit the file ***")
            say(f"    -> {out['path'].relative_to(ROOT)}")
            thin.clear()                                 # an answer worth keeping resets the patience
        else:
            say(f"    {out['outcome']}: {out.get('why', '')}")
            if out["outcome"] in ("thin", "no_answer") and out.get("paid"):
                thin.append(short)                       # we PAID for that one and got nothing readable
                if len(thin) >= cfg["thin_streak"]:
                    say(f"   STOPPING: {len(thin)} runs in a row came back with nothing readable "
                        f"({', '.join(thin)}). Seer has stopped being useful on what is left; the rest of "
                        f"the queue is not worth buying. Nothing here is a failure of the issues — tell a "
                        f"person and let them decide.")
                    return {"started": started, "collected": collected, "stop": "thin"}
            if out["outcome"] == "blocked":
                say("    (a blocker is org-wide: stopping this sweep rather than asking 20 times)")
                break
    if waiting:
        why = "dry run" if dry else f"{cfg['per_sweep']}/sweep and {cfg['per_hour']}/hour"
        say(f"   {len(waiting)} waiting for a run ({why}): {', '.join(waiting[:8])}"
            + (f" +{len(waiting) - 8} more" if len(waiting) > 8 else ""))
    if collected:
        say(f"   index -> {write_index(cfg).relative_to(ROOT)}")
    say(f"   sweep done: {started} run(s) started, {collected} finding(s) written, "
        f"{_spent(rows)}/{cfg['per_hour']} spent this hour")
    return {"started": started, "collected": collected, "queued": len(queue)}


def in_tmux() -> int:
    have = subprocess.run(["tmux", "list-windows", "-t", SESSION, "-F", "#{window_name}"],
                          capture_output=True, text=True)
    if WINDOW in have.stdout.split():
        say(f"tmux {SESSION}:{WINDOW} is already running — `tmux kill-window -t {SESSION}:{WINDOW}` first")
        return 1
    cmd = f"cd {ROOT} && ./scripts/seer_sweep.py; exec zsh"
    subprocess.run(["tmux", "new-window", "-d", "-t", SESSION, "-n", WINDOW, cmd], check=True)
    say(f"started in tmux {SESSION}:{WINDOW} — watch it with `tmux attach -t {SESSION}` then select that window")
    return 0


async def run(args) -> int:
    cfg = caps()
    client = sc.SentryClient(timeout=20.0)
    st = client.state()
    if not st["configured"]:
        say(f"REFUSING TO START: {st['reason']}")
        return 2
    say(f"Seer sweep · org {st['org']} · project {st['project']} · findings -> {cfg['dir'].relative_to(ROOT)}")
    if args.dry_run:
        say("DRY RUN: no run is started, nothing is billed, no file is written.")
    thin: list[str] = []
    while True:
        try:
            out = await sweep(client, cfg, dry=args.dry_run, refresh=args.refresh, thin=thin)
        except sc.SentryError as e:
            say(f"   sweep stopped — {e.code}: {e.detail}")
            out = {}
        if out.get("stop") == "thin":
            return 3
        if args.once or args.dry_run:
            return 0
        say(f"   sleeping {cfg['interval_s']} s\n")
        await asyncio.sleep(cfg["interval_s"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="show the queue and the skips; spend nothing")
    ap.add_argument("--once", action="store_true", help="one sweep, then exit")
    ap.add_argument("--refresh", action="store_true", help="re-collect issues that already have a finding file")
    ap.add_argument("--tmux", action="store_true", help=f"start the loop in tmux {SESSION}:{WINDOW}")
    args = ap.parse_args()
    if args.tmux:
        return in_tmux()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        say("\nstopped. The budget on disk is kept; restarting does not restart the spend.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
