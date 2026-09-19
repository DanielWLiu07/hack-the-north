# `web/` — the browser surface · LAPTOP

**Branch:** `track/web` · **Owner:** Daniel (with `elastic/` and `roomctl/`)

## What this is for — and what it deliberately is not

Rerun owns 3D. **This does not duplicate it.** `web/` covers the four things Rerun is bad at,
and all four happen to be the Elasticsearch story made visible:

1. **Search** — the hybrid query box. Type "where did I leave my hammer", see the ranked
   results with their timelines, and show *why* a result matched (the BM25 hit vs the vector
   hit vs the reranker's reordering). **Show the row BM25 alone would have missed.**
2. **History** — the room's commit log, branch graph, and per-object timeline. `git log
   --graph` for a room, in a browser, scrubabble.
3. **Analytics** — the ES|QL aggregations: zone volatility, the dirtiness histogram with its
   step change at the second the judge touched the mug, most-moved object, telemetry
   correlation when a commit looks wrong.
4. **Conflict resolution** — the merge-conflict UI. Both candidate positions, `--ours` /
   `--theirs` buttons, and the robot executes the choice.

> **A caution worth keeping:** Warp's prize explicitly says *"there's no need to add a GUI for
> the sake of adding one."* This page earns its place only by showing things the CLI and Rerun
> genuinely cannot. If a panel is just a prettier `git log`, cut it.

## Files
| file | purpose |
|---|---|
| `server.py` | Small FastAPI app. Proxies Elasticsearch (the browser must never hold the ES key), serves `roomctl` state, exposes the agent's tools over HTTP. |
| `static/index.html` | The dashboard. Four panels above. |
| `static/search.js` | Query box → `/api/search` → results with match-provenance badges. |
| `static/history.js` | Commit graph + per-object timeline. |
| `static/analytics.js` | ES\|QL results → charts. |
| `static/conflict.js` | The merge-conflict resolution UI. |
| `api.py` | Full request/response spec in [`../docs/16-api.md` §4b](../docs/16-api.md). Includes `GET /api/events` — **SSE, not a WebSocket** (one-directional, native reconnect). |

## Why a server and not a static page
**The browser must never hold the Elasticsearch API key.** `server.py` proxies every query,
which also gives you one place to shape responses and to log what the demo actually ran.

## Build order
1. `server.py` + `/api/search` against **`fake/`** data. Hour 4-ish, no robot needed.
2. `static/search.js` — the search panel. It's the highest-value one for the Elastic judges.
3. `analytics.js` — cheap, and it fills the 30-second silence while the arm works.
4. `history.js`.
5. `conflict.js` — only if the merge beat survives to T3.

## Acceptance criteria
- [ ] Searching "mug" surfaces an object whose only matching description says "ceramic cup",
      and the UI **shows that the vector leg found it and BM25 didn't**
- [ ] The analytics panel renders a real ES|QL aggregation, not a mock
- [ ] Legible on a projector from 3 m
- [ ] No Elasticsearch credentials reachable from the browser

## Gotchas
- **Don't rebuild Rerun.** No 3D here. Point cloud, boxes and ghosts stay in Rerun.
- Every panel must answer *"what could the CLI not do?"* If there's no answer, delete it.
- Keep it one page. A router and five routes is time you don't have.
