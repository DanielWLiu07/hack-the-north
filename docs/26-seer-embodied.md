# 26 — Seer, embodied

Sentry's AI debugger as an entity **in the room**, and a control surface in the telemetry
panel. Not a mascot.

---

## The rule this has to pass

The git graph earned its place because **clicking a node moves a robot**. A 3D Seer earns its
place the same way or not at all:

> **You point it at a real failure and it answers.** If it only animates, it is a costume and
> it should be cut.

Judges can tell the difference in about four seconds, and a decorative AI mascot reads worse
than no mascot.

---

## What it is

A **watcher** — it fits the scene's existing vocabulary (`watchers.js`, `heads.js`, the hooded
camera pod). Not a robot arm; something that *looks*. One large lens, a slow idle scan, and a
posture that changes with what it is doing.

```
 idle         slow sweep, lens dim            nothing to look at
 summoned     turns toward the failure        an issue was selected
 thinking     lens brightens, tighter arcs    the Seer call is in flight
 verdict      holds still, lens steady        the answer renders beside it
 stumped      lens dims, turns away           Seer returned nothing useful — SHOW THIS
```

That last state is deliberate. A system that only animates success is untrustworthy; one that
visibly gives up is the opposite. **Build the stumped pose before the verdict pose.**

---

## Where the control lives — the telemetry panel

The telemetry strip already carries live robot state, so it is the natural home for
"something is wrong, look into it".

```
┌─ TELEMETRY ────────────────────────────────────────────────┐
│  pitch ▁▂▅▃▁   tilt_rate ▁▁█▂▁   balanced ●                │
│                                                             │
│  ⚠ grasp_slipped · cap_78072 · 2m ago                      │
│     [ open capture ]  [ open trace ]  [ ask Seer ]   ←──────┤ the control
└─────────────────────────────────────────────────────────────┘
```

**The three buttons are the whole integration story on one row**: the Elastic evidence, the
Sentry trace, and the AI verdict, all reachable from the moment the robot failed.

### Config, per their "configure it in telemetry" idea

| control | why it is a control and not a constant |
|---|---|
| **auto-summon on failure** | off by default. Seer costs credits ($20/mo) — do not fire on every warning |
| **severity threshold** | only summon for `failed_op` / `fell` / `grasp_slipped`, not camera dropouts |
| **context depth** | how many telemetry breadcrumbs to attach. More context, better answer, more tokens |
| **credits remaining** | show it. A control that can silently exhaust a budget should display the budget |

---

## The API path — verify this at 01:00

Sentry exposes autofix/root-cause on an issue. The shape to confirm:

```
POST /api/0/issues/<issue_id>/autofix/      → start a run
GET  /api/0/issues/<issue_id>/autofix/      → poll state + result
```

Endpoints and payloads have moved between versions — **check against the live API before
building the UI on top**, and degrade gracefully: if the endpoint 404s, the watcher goes to
`stumped` and the panel says why. Never fake a verdict.

Our `SENTRY_AUTH_TOKEN` already works for issue reads, so the same token is the starting
point. If autofix needs a scope we lack, that is a five-minute token change, not a redesign.

---

## What makes this genuinely good rather than cute

Three things, in order of how much they matter:

1. **It is the only place the two systems visibly meet.** The panel shows robot telemetry
   (ours), the failure (Sentry), and the evidence (Elastic) in one frame — then asks an AI to
   reconcile them. That is the cross-system story *as an interface* rather than as a claim.
2. **Seer is reasoning about a physical failure.** Everyone else points it at a stack trace.
   Ours gets 40 telemetry samples and a camera frame, and has to explain why an arm closed on
   nothing. Whatever it answers is interesting — including if it misses.
3. **The honest-failure state is part of the design.** `stumped` is a first-class pose.

---

## Cost and order

| | |
|---|---|
| the watcher model + four poses | reuse `watchers.js` — it already has the vocabulary |
| the telemetry row + three buttons | small, and the first two buttons work today |
| the Seer call + polling | **verify the endpoint first** |
| config controls | last — defaults are fine for a demo |

**Build the row before the model.** `[open capture]` and `[open trace]` work right now with no
Seer at all, and they are already useful. The watcher is the part that makes it memorable, but
the row is the part that makes it true.

**Do not build this before `ai_agent_monitoring`.** That one is a *scored* product; this is
creativity on top. Order matters if the hours run out.

---

## As built, 2026-09-20: the sweep

The panel asks Seer about ONE capture, when a person presses the button. `scripts/seer_sweep.py` is the
other half: Seer working through the open issues on its own, writing what it finds to `docs/seer/` — one
file per issue and an index. See `scripts/README.md` for the flags.

**What the live API actually returns**, which is not what this document assumed above:

* `/issues/{id}/autofix/` is gone; the org-scoped path answers (verified 2026-09-19).
* A finished run has **no `steps` key at all**. It is a conversation — `blocks[]`, each one
  `{message: {role, content}, tool_calls, tool_results, file_patches}` — and the root cause is the
  **closing assistant turn**, markdown, naming the file and the commit (verified 2026-09-20 on run
  16890654). `status` comes back lower-case. `sentry_client._verdict_text` reads the conversation first
  and keeps the old `steps` reader for runs made before the change.
* Seven turns and nine tool calls is a normal run. They take 40–80 s.

**The two rules the sweep is built on.** Spend is capped IN THE CODE — per sweep, per rolling hour (kept
on disk, so restarting does not restart the budget), and a minimum gap — and the script refuses to start
if a cap cannot be parsed. And it stops Seer at `root_cause`, refusing `code_changes` and `open_pr` by
name: nothing it collects is applied, because an unattended agent that edits the repository is a
different product from one that explains it.

**Several issues are usually one fault.** `GITSPACE-15`, `1B`, `1H`, `T` and `V` are five tracebacks for
one head camera that is off the USB bus. The sweep buys a run for the loudest of them and
`~/.cache/gitspace/seer-sweep/skip.txt` retires the rest — re-read every sweep, so a person can edit it
while the loop runs. Spending five runs to write the same sentence five times is how a findings directory
gets big and stops being worth reading.

**The answer worth having is often not a patch.** For `GITSPACE-15` Seer's conclusion was *"the head
stereo camera is physically off the USB bus — reseat the cable"*, reached from breadcrumbs and our source
together. For `GITSPACE-1J` it named the commit that introduced the bug AND the commit that had already
fixed it 52 minutes later, which is an issue to RESOLVE, not a file to edit — so a finding that says so
gets a banner instead of a suggestion.
