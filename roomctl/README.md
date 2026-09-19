# `roomctl/` — the `room` CLI · LAPTOP

**Branch:** `track/data` · **Owner:** Daniel. **Unblocked from hour zero** via `fake/`.

## What this module owns
The user-facing command surface, the room's git repository, and the executor that turns a diff
into ordered robot operations. Semantics: [`../docs/04-git-semantics.md`](../docs/04-git-semantics.md)

## Files

| file | purpose |
|---|---|
| `cli.py` | `room init/status/diff/add/commit/log/checkout/revert/merge/search/blame/stash`. The whole product surface. |
| `repo.py` | Thin wrapper over `git` **subprocess calls**. We use real git — never reimplement plumbing. |
| `state.py` | **FROZEN.** The object-record schema: `ObjectRecord`, `to_yaml`/`from_yaml`, `read_tree`/`write_tree`, `new_id`, the quantization constants. `tests/test_state.py` pins the bytes. |
| `zones.py` | Zone definitions and assignment. A zone is a **directory**: `zones/desk/`, `zones/shelf/`. |
| `executor.py` | diff → `MOVE`/`ADD`/`REMOVE` ops → **dependency graph** → topological sort → robot calls → verify by rescan. |
| `hooks.py` | Inbound webhook / SQS consumer: GitHub PR merge, Agent Builder callback. Allow-listed tool names only. |

## Use it

```bash
alias room="$PWD/.venv/bin/python -m roomctl"     # or: python roomctl/cli.py, from anywhere
room init --scene clean_bench                     # first scan, first commit, into $ROOM_GIT_PATH
room status --scene messy_bench                   # scan (a judge has moved things), then git status
room diff                                         # literally `git diff`
room add zones/desk/ && room commit -m "desk"     # spatial staging
room log                                          # --graph --oneline --decorate --all
room status --json                                # the docs/16 /api/status shape (or import roomctl.cli.status_dict)
```

Scanner: `--scene <fake scene>` per command, or `ROOM_SCANNER=fake:<scene>`; unset means "the
tree as last scanned". `ROOM_ES=off` keeps the fake's observations out of Elasticsearch.
`room reset --hard [ref]`, `room checkout <ref>` and `room revert [ref]` let git move HEAD, then
plan and run the robot (`MockRobot` for now: it prints, and moves the fake room so a rescan can
verify), then rescan and say how much landed. They need a scanner; `--plan-only` just prints.
`ROOM_MOCK_FAIL=mug_a1b2` makes a grasp slip. `merge cherry-pick stash` still exit 2.
`show blame branch tag` pass straight through to git.

## Build order
1. ~~`repo.py` + `cli.py` against **`fake/` scenes**. Real git, fake objects.~~ Done 2026-09-18: `init status diff add commit log`.
2. ~~`state.py` — freeze the object YAML schema and tell the other tracks.~~ Done 2026-09-18.
3. `zones.py` — directory-per-zone, so `git add zones/desk/` works.
4. ~~`executor.py` with a **mock robot** (prints ops).~~ Done 2026-09-18: `room reset --hard`, `checkout`, `revert`.
5. Wire the executor to the real `robot/` API.
6. `hooks.py` — stretch, only for the GitHub PR beat.

## Acceptance criteria
- [x] `room status` on an unchanged fake scene prints "working tree clean" (`tests/test_cli.py`)
- [x] `room diff` output is *literally* `git diff` — line-oriented, per-object files (byte-compared in `tests/test_cli.py`)
- [x] The executor never plans a `place` into a cell another object still occupies (300 random layouts, `tests/test_executor_order.py`)
- [x] A cyclic swap (A→B's spot, B→A's spot) produces a staging move — and `room checkout` of one verifies clean
- [ ] `room merge` on two branches that moved the same object produces a real git conflict — the git
      conflict is real and `room status` shows it; `room merge` itself waits for the executor

## Gotchas
- **Keep `room.git` separate from the code repo.** Two histories. Conflating them costs an
  hour at exactly the wrong time.
- One object per file, one field per line — that's what makes `git diff` readable on a projector.
- The executor must **verify by rescanning** and report honestly. "I moved 2 of 3" beats a
  false success.
