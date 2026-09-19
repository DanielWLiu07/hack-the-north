# `viz/` — LAPTOP · the demo screen

**Branch:** `track/data` · **Owner:** Daniel. Rerun owns 3D; [`web/`](../web/) owns search, history and analytics.

Judges score **design**, and for a CLI-first robotics project this screen *is* the design.
Treat it as a designed artifact, not debug output.

## Files
| file | purpose |
|---|---|
| `blueprint.py` | The Rerun blueprint — panel layout, saved and version-controlled. |
| `logging.py` | Helpers: log a cloud, boxes coloured by diff status, ghosts, the diff text panel. |

## The five panels
1. **Big 3D view** — fused cloud + object boxes. White = unchanged, **amber = moved** (with an
   arrow from old pose to new), **green = added**, **red dashed ghost = removed**, grey = unobserved.
2. **Diff text** — the actual `git diff`, monospace.
3. **Timeline** — Rerun's native timeline scrubbed **by commit**, so you can drag backwards
   through the room's history. Nearly free, looks extraordinary.
4. **Camera strip** — three rectified left images, so people can see it's real.
5. **Status** — branch, clean/dirty, HEAD short sha. Mirrored on the LED strip.

## Acceptance criteria
- [ ] Legible from 2 m away on a laptop screen
- [ ] A moved object is obvious to someone who has never seen the project
- [ ] Conflict view shows both candidate positions as ghosts
- [ ] Nothing flickers or re-lays-out between captures

## Gotchas
- Build it early and **freeze it**. Redesigning the demo screen at hour 30 is how teams lose.
- The Pi and the laptop can both log to the same viewer — `rr.connect_grpc(...)`.
