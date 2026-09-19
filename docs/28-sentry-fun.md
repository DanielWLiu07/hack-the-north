# 28 — The fun angle: a robot that files its own bugs and closes them by tidying up

Everything in `18-sentry.md` is *correct*. None of it is **memorable**. Judges see forty
correct integrations; they remember one that made them laugh and then realise it was real.

## The line

> **"Our robot files its own bug reports — and resolves them by physically tidying up."**

That is the pitch. Everything below makes it literally true.

---

## 1. The robot resolves its own Sentry issues  ·  **the headline**

A grasp slips → `grasp_slipped` issue, with the camera frame attached. The executor retries,
the object lands where the commit says it should, the rescan comes back clean → **we mark the
issue resolved through the Sentry API**, from the robot.

```python
# after verify-by-rescan succeeds for the op that failed
requests.put(f"https://sentry.io/api/0/issues/{issue_id}/",
             headers={"Authorization": f"Bearer {TOK}"},
             json={"status": "resolved"})
obs.context("resolution", {"resolved_by": "robot", "retry_ops": 1,
                           "commit_sha": sha, "verified_by": "rescan"})
```

**Why it is not a gimmick:** an issue tracker's whole premise is that a human reads the error
and fixes the code. Here the thing that caused the error *is* the thing that fixes it, and the
fix is a physical action in a room. The issue timeline literally reads:

```
 01:42  grasp_slipped · gripper closed to 2mm · [camera frame attached]
 01:43  robot retried · rescan clean
 01:43  resolved by robot
```

Nobody else's issue feed will have that.

## 2. The room has a heartbeat, and a messy room misses it

We have **one cron monitor** on the education plan. Point it at *the room being clean*:

```python
obs.heartbeat("room-clean", status="ok" if working_tree_clean else "error")
```

A room left dirty stops checking in, and **Sentry alerts that the room failed its heartbeat.**
That is a completely legitimate use of cron monitoring — a scheduled job that did not complete
— applied to a physical space. It is also funny, which is the point.

## 3. The LED strip is the error indicator

The robot already has an addressable LED strip. Mirror Sentry's unresolved-issue count onto
it: **green = no open issues, amber = warnings, red = an unresolved error.**

Observability you can see from across the room, on the machine that produced it. Costs twenty
minutes and every judge walking past the table asks what the colours mean — which is the
question you want.

## 4. The robot says its own errors out loud

TTS is already wired. On a new issue:

> *"Sentry issue seven-seven-four-one. I fell over. Peak tilt rate two point eight."*

A robot reading out its own stack trace is the kind of detail that gets repeated to a
colleague, which is how a project gets remembered after judging.

## 5. Suspect Commits, in a project where commits are rooms

Once the repo is on GitHub, Sentry's GitHub integration links an error to the commit that
likely caused it. In **this** project that sentence has two meanings at once — code commits
and *room* commits sit side by side in the same demo.

Cheap (~2 min, and you need the repo for Devpost anyway), and the resonance is free.

---

## What this buys on their three axes

| axis | their words | what the above gives |
|---|---|---|
| **Creativity** | *"whether Sentry let you build something you couldn't have"* | an issue tracker whose issues are **closed by a robot moving an object**. Impossible without both halves |
| **Depth** | *"custom spans, tags, context, instrumented agent calls"* | already done — and now the API is used for *write*, not just read |
| **Influence** | *"how Sentry data shaped decisions during the build"* | `SENTRY_STORY.md`, auto-captured by `sentry_watch.py` as it happens |

## Build order — cheapest first
1. **LED mirrors issue count** — 20 min, visible from across the room
2. **Robot resolves its own issues** — 30 min, and it is the headline
3. **Room-clean cron heartbeat** — 15 min, we already have `obs.heartbeat()`
4. **TTS announces new issues** — 15 min, TTS is already wired
5. Suspect Commits — 2 min once the repo is pushed

None of it is more than an hour, and all of it is downstream of instrumentation that already
exists and is already sending data.

---

## Status, as built — 2026-09-19 (`robot_sentry.py`)

| # | state | how |
|---|---|---|
| **1 · robot resolves its own issues** | **LIVE.** GITSPACE-B: `grasp_slipped` (frame attached) → note *"🤖 Resolved by the robot … retried (2 attempts), rescan came back clean at commit a3f9c1"* → `set_resolved`. Heal trace carries `resolved_by=robot`. | `SelfHealingRobot` around the executor's Robot; `python robot_sentry.py demo` replays it (mock arm, synthetic). Real path: master's `cmd_apply` hook (docs/10). Actor becomes the robot once `SENTRY_ROBOT_TOKEN` is set (D36). |
| **2 · room-clean heartbeat** | built, mocked | `IssueMirror` checks in `ok`/`error` from `room.git`'s working tree. room.git is dirty right now, so its first check-in FAILS — which is the feature. |
| **3 · LED mirrors issues** | built, mocked (no Pi yet) | worst of: unresolved error → `error` (red), warning or dirty room → `dirty` (amber), else `clean` (green); written on change only. Two writers — D34. |
| **4 · TTS says new issues** | built, mocked | *"Sentry issue 12. My grasp slipped."* / *"The room failed its heartbeat."* — Pi `/say`, or `--local-say` on the laptop; ≥ 20 s apart; already-open issues aren't news. |
| 5 · Suspect Commits | not started | needs the repo on GitHub. |

Run the loop: `python robot_sentry.py mirror` (`--once` to try one tick). Tests: `telemetry/test_robot_sentry.py`.

