# YOUR JOB — master / integration

You are the integration session. Five other sessions are working in parallel:

| window | owns |
|---|---|
| htn:3 | `elastic/` |
| htn:4 | `web/` (except `web/landing/`, which is done) |
| htn:5 | `perception/` — depth, fuse, voxelize, serialize |
| htn:6 | `perception/` — segment, cluster, describe, merge, associate |
| htn:7 | `scripts/`, deployment, Sentry |

**Do NOT edit those folders.** You own the parts that tie them together:
`roomctl/` · `fake/` · `tests/` · `docs/` · `README.md` · `TEAM.md` · `BUILD_ORDER.md`

Read `README.md`, then `docs/01-concept.md`, `docs/06-demo.md`, `docs/04-git-semantics.md`,
`docs/20-perception-logic.md`.

## Build, in this order — the first item unblocks two other sessions

1. **`fake/scene_gen.py`** — HIGHEST PRIORITY. Hand-authored scene → object YAML → a real
   git commit + Elasticsearch documents. It must fake the MESS, not clean data:
   three disagreeing VLM descriptions per object, per-camera coordinate conflicts,
   rejected clusters with reasons, wobbling confidence, occasional occlusion.
   The elastic and web sessions are testing against hand-written stubs until this lands.
2. **`roomctl/state.py`** — freeze the object-record schema. One YAML shape, one field per
   line. Tell the other sessions the moment it is frozen; several are guessing at it.
3. **`roomctl/repo.py` + `cli.py`** — `room init/status/diff/add/commit/log`. Real git via
   subprocess. We do NOT reimplement git plumbing.
4. **`tests/test_idempotent_scan.py`** — scan twice, `git diff --exit-code` clean. This is
   the project's most important gate; write it before there is anything to regress.
5. **`roomctl/executor.py`** — diff → ops → dependency graph → topological sort → robot
   calls → verify by rescan. Build it against a MOCK robot that prints ops. Get the
   ordering right before motion exists.

## Your standing duties
- When a session finishes a numbered step, check its work against the acceptance criteria
  in its `TASK.md` and note drift in `docs/10-open-questions.md`.
- Keep `TEAM.md` accurate — ownership has already shifted once.
- `room.git` is a SEPARATE repository from the code. Never nest it.
