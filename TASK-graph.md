# TASK-graph.md — replace the Health tab on `/robot` with the room's git history graph

Owner: the `graph` session (tmux `htn:graph`). Read this whole file first.

**This supersedes any earlier version of this file. If you were making the rail HORIZONTAL: stop.
Daniel has changed his mind — it stays VERTICAL.** Keep any branch/diff/identity work; drop the
horizontal layout work.

## The change Daniel asked for, in one line

> "remove the health tab and replace it with the history graph git and make it vertical instead"

On `/robot` the workspace bar has three tabs — **Agent · Settings · Health**. The Health tab goes away,
and the room's **git history graph** takes its slot: same right-hand panel, vertical.

## What is where

### Remove: the Health tab and its panel

- `web/pages/robot.html:16` — `<button data-panel="system-status">Health</button>` in `.workspace-tabs`
- `web/pages/robot.html:21` — `<details class="system-status">` … `#connection-list` … `#refresh-connections`
- `web/pages/robot.html:12` — `<link rel="stylesheet" href="/pages/room-connections.css">`
- `web/pages/robot.html:61` — `<script type="module" src="/pages/room-connections.js">`
- `web/pages/room-chat.js:7` — the `panels` map: `'system-status': document.querySelector('.system-status')`
- `web/pages/robot.css` — `.system-status` rules live at `:19`, with mobile overrides scattered at
  `:17`, `:18`, `:22`, `:23`, `:24`; `web/pages/room-chat.css:3` also has a `body .system-status` override.
  Clean these up or repoint them at the new panel — **don't leave orphan `!important` rules behind.**

**Leave `web/pages/room-connections.js` and `.css` on disk.** Unmount them, don't delete them. They are a
working health view and someone may want them back on another page; keeping the files makes that a
one-line restore. Note in PROGRESS.md that they are now unmounted and where they went.

### Put there: the history graph — vertical, in the same right-hand slot

The graph is the **point-cloud `git log`** that today floats as a rail over the 3D canvas:
`web/pages/room-cloud.js` (the log is ~90 lines of a 540-line module — `paintLog()` `:319-363`,
`refreshHistory()` `:365-376`, `addCurrent()` `:378-402`, `pollHistory()` `:404-413`, `plyUrl()` `:423-427`,
`loadCommit()` `:429-452`, `step(delta)` `:455-464`, `[`/`]` keys `:526-532`). CSS: `web/landing/room-ink.css:22-43`.

**Every node is a full point-cloud snapshot** — clicking one re-renders the whole three.js scene from that
commit's `.ply`. That is the thing Daniel wants front and centre.

Move it into the Health tab's slot and wire it as a proper tab:
- a new tab button in `.workspace-tabs` — `<button data-panel="room-history">History</button>` where Health was
- register the panel in the `panels` map (`room-chat.js:7`) so `openPanel()` drives it like the others
- the panel sits where `.system-status` sat (`robot.css:19`: `position:fixed; right:30px; top:64px; width:260px`) —
  widen it if the graph needs it, but keep it a right-hand panel
- **vertical**: time runs top → bottom, newest at the top. It is already vertical — keep it that way and do
  not build a horizontal track.
- keep `[` / `]` stepping, and add `↑` / `↓` plus `Home` / `End`
- selecting a node must not block the scrub while its `.ply` loads
- it must still work at phone width (there are existing `@media(max-width:760px)` rules for `.git-log` at
  `room-ink.css:189` — check them, they may already fight you)

Decide whether the rail *also* stays over the canvas or lives only in the tab. Pick one, make it coherent,
and say which you chose and why. Two copies of the same graph on one page is the exact problem we are fixing.

## The data (verified — don't re-derive)

Each scene instance is its **own git repo** under `~/.cache/gitspace/rooms/<instance>/`. Verified on
`hallway-test`: 5 commits, linear. One commit tree holds **both** the cloud and the objects in it:

```
cloud/current.ply     the point cloud        zones/desk/unknown_1231.yaml   id/class/zone/pose/
cloud/current.json    capture_id, points,    zones/desk/unknown_46f8.yaml   extents/color/first_seen
                      frame, pose, bounds    room.yaml, anchors/, .roomignore
```

So the cloud diff and the object diff come from the same two shas. Nothing has to be invented to join them.

API — `web/scene_api.py`: `GET /api/scene/instances` `:302` · `GET /api/scene/{instance}/captures` `:379` ·
`GET /api/scene/{instance}/history` `:501` (`_git_commits()` `:440-459`, `_capture_nodes()` `:470-498`) ·
`GET /api/scene/{instance}/history/{sha}.ply` `:527` and `.json` `:532` ·
`POST /api/scene/{instance}/add` `:572` (behind the existing `git add · current` button, `room-cloud.js:330-336`).

## Still wanted (unchanged from before)

### Branches — lanes, and branching by command. Never merging.

`/api/scene/{instance}/history` **already returns `parents` per node** (`scene_api.py:453`, `:492`) and
`room-cloud.js` never reads it. That is branch support already paid for, sitting unused. Read it and draw
lanes. **The correct ~20-line railroad lane walk already exists at `web/landing/graph.js:57-75`** — port it,
vertical, no transposition needed now. Distinguish lanes by stroke dash, not colour
(`graph.js:76` `DASH = ['', '7 5', '2 5', '11 4 2 4']`) — this page has one accent and that keeps it.

Branching must be reachable as a command. `POST /api/scene/{instance}/add` is the only write today, so add
the branch/checkout writes to `scene_api.py` in that file's existing style.

**No merge. Anywhere.** Not a button, not an endpoint, not a code path. `roomctl` already enforces it —
`merge`, `cherry-pick`, `stash` are in `WRITE_VERBS` and exit 2. The graph must agree with the CLI.

Keep **preview before execute**: selecting a node previews; running is a second control that arms ~600 ms
later and ignores the second click of a double-click.

### Object diffs — substantial, and identity-aware

There is no object diff on this page today. Build it for any two selected nodes.

The shape is already solved one repo over: `_ops(a, b)` at **`web/graph_api.py:227-266`** diffs two commits'
`zones/**.yaml` into per-object `moved` (with `delta_m`, `delta_yaw_deg`) / `changed` / `added` / `removed`,
and re-fuses the delete+add git reports when an object changes zone (path is `zones/<zone>/<id>.yaml`) back
into **one** move carrying `from_zone`. The instance repos have the same `zones/` layout, so it applies
directly. Reuse it — extract it if that's cleanest — don't write a second one.

Identity — "is this the same object?" — **is already solved. Do not re-solve it:**
- `perception/associate.py` — header `:1-27` has the decision table: `unchanged` / `moved` / `returned` /
  `added` / `removed` / `unobserved`. `_match_head()` `:362` is Hungarian on distance hard-gated at 1.5 m;
  `_reidentify_all()` `:386` is the Elasticsearch search that recognises an object that **left and came
  back**; `_extents_ok()` `:426` and `delta_e()` `:411` (colour distance) are the gates.
- `roomctl/state.py` — `class`, `color`, `first_seen`, `extents` are identity **carried forward**, set at
  first sight, never re-derived.
- So a stable `object_id` across two commits *means* the same physical object; a new id means genuinely new.
  **Surface that, don't recompute it.**

For cloud-vs-cloud, `perception/difference.py` answers "what APPEARED or went AWAY between two captures of
one room" — `difference(baseline, current, camera)` `:113`, free-space-along-ray geometry, thresholds
documented at `:1-40`. It complements the per-object YAML diff rather than replacing it.

Per object show: class, colour swatch, zone, before → after pose, metres moved, degrees turned, zone change —
and **"new to the room" vs "seen before"**, with `first_seen`, and *returned* called out when it left and came
back. Group by zone, order by how much changed. "Nothing changed" must read as nothing changed, not as an
empty box.

**Honest warning.** In `hallway-test` the objects are `unknown_1231` / `unknown_46f8` — `class: unknown`,
`color: "#808080"`. Until segmentation labels them a real diff there will look thin; that is the data, not
the panel. Build against it truthfully — do **not** invent classes or colours. Say in PROGRESS.md if you
want a richer instance to develop against.

## Rules

- Git is the source; Elasticsearch is enrichment only. Missing enrichment is `null`, never guessed.
- No graph library. The railroad walk is ~20 lines and correct.
- Text only ever enters the DOM as text.
- Don't touch `room.git` or the instance repos' history.
- Verify in the **real page** at `/robot`: the tab opens, the old Health tab is gone with no dead CSS or
  console errors, the graph renders, stepping works. Say exactly what you checked.
- Write it up in `PROGRESS.md`. Don't commit unless asked.
