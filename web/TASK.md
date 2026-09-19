# YOUR JOB — web/

You own **only** `gitspace/web/`. Do not edit any other folder.

Read first:
- `README.md` here — what this is and deliberately is not
- `../docs/16-api.md` §4b — full request/response spec for every endpoint
- `../docs/11-elastic.md` — what the queries mean

Build, in this order:
1. `server.py` — FastAPI. **The browser must never hold the Elasticsearch key**; proxy
   every query here. Bind per `WEB_BIND` in `../.env`.
2. `GET /api/search` — the money endpoint. Returns `matched_by: {bm25, vector,
   rerank_position}` so the UI can show WHY a result matched.
3. `static/index.html` + `search.js` — one page, four panels: search, history,
   analytics, conflict. Dark. No 3D — Rerun owns that.
4. `GET /api/events` — **SSE, not a WebSocket**. Browser needs server→client only and
   `EventSource` reconnects for free.

Acceptance: searching "mug" surfaces an object whose only matching description says
"ceramic cup", AND the UI visibly shows the vector leg found it while BM25 did not.
That badge is the single most persuasive thing on the page.

Rule: every panel must answer "what could the CLI not do?". If there is no answer,
delete the panel.
