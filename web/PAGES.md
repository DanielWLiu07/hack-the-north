# Web — what pages we actually need

Seven surfaces. **Two of them are routes; the rest are sections of one scrolling page.**

A judge gets three minutes and will not learn a navigation. So the default is one page —
except where a URL is genuinely the product (something you can *send* someone).

---

## Routes (deep-linkable, because sharing them is the point)

### `/` — the hero, then the dashboard, on one scroll
The GITRL scene up top, then the live room state, search, history and analytics as sections
below it. The scene's animation loop **stops** when the hero scrolls out
([`LANDING-TASK.md`](LANDING-TASK.md) rule 2) — the styled pass must never sit behind body copy.

### `/capture/<capture_id>` — ★ THE PAGE THAT WINS BOTH PRIZES
This is the one to build carefully. It answers *"why was this diff wrong?"* on a single screen:

| block | content |
|---|---|
| the three camera views | with each one's own VLM description underneath — **visibly disagreeing** |
| per-camera disagreement | cam0 `x=0.421` · cam1 `x=0.418` · cam2 `x=0.467` → **49 mm spread, quantum is 10 mm** |
| the quality gate | `skew_ms` · `tilt_rate_max` · `coverage` → PASS / **REJECT**, with the failing value called out |
| telemetry, ±2 s around the shutter | `tilt_rate` and `odom_residual` as a strip chart, the spike marked |
| → **Open in Sentry** | a real link built from `sentry_trace_id` on the document |
| what the diff concluded | and, if it was wrong, *why* |

Both briefs are satisfied by this page and nothing else on the site does it:
**Elastic** — messy contradictory sensor data turned into something actionable.
**Sentry** — observability that changed a physical outcome, with the trace one click away.

Reachable from any anomaly: a failed op, a rejected capture, a Sentry issue.

### `/object/<object_id>` — the full life of one thing
Every appearance, all per-camera descriptions, `first_seen` / `last_seen` / `appearances`,
zone history, and the occlusion verdict if it is currently absent. This is where *"find the
hammer"* lands when the hammer is gone — the answer includes a **"drive there and point"**
button.

---

## Sections of `/`

### Status
Branch, HEAD sha, clean/dirty, and the changed objects. Mirrors the LED. Live over SSE.

### Search — the most persuasive panel on the site
The hybrid query box. It must render **`matched_by: {bm25, vector, rerank_position}`** as a
visible badge, because a result the vector leg found and BM25 missed is the single best
evidence that hybrid search is doing real work. Do not bury that in a tooltip.

### History
Commit graph with branches, and a scrubber. `git log --graph` for a room, in a browser.

### Analytics
Named ES|QL only, no arbitrary queries from the browser: zone volatility, the dirtiness
histogram (**the step change at the second the judge touched the mug**), most-moved object,
objects that have never moved, telemetry correlation.

### Conflict resolution
Appears only when there is one. Both candidate positions, `--ours` / `--theirs`, and the robot
executes the choice. Physical merge conflict, resolved in a browser.

### Telemetry strip
A thin always-visible live scope: `pitch`, `tilt_rate`, `balanced`. 2 Hz over SSE, decimated
([`../docs/23-telemetry.md`](../docs/23-telemetry.md)). It is what makes the page feel connected
to a real machine rather than to a database.

---

## Deliberately NOT building
- a login or multi-user anything
- a settings page — `.env` and query params are the settings
- a separate mobile site — one responsive page
- a 3D view anywhere but the hero. **Rerun owns 3D.** Duplicating it costs frames and buys nothing.

---

## Build order
1. `/` status + search *(search is the prize panel)*
2. **`/capture/<id>`** *(the winning page — do this before history or analytics)*
3. analytics + the telemetry strip *(both fill the 30-second silence while the arm works)*
4. `/object/<id>`
5. history
6. conflict *(only if the merge beat survives to T3)*

Note the ordering: `/capture/<id>` comes **second**, before the easier panels. It is the page a
judge should leave remembering, so it should not be the one that ran out of time.
