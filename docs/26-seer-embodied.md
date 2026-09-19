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
