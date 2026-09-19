# TASK: Andrew · the words and the edge
> **ACTIVE since Sat 12:45 EDT.** The caretaker-roommate plan is the team's goal (`../../../PLAN.md` §0 first).
> **Shared rules:** develop and test on **localhost only** (web http://localhost:8000, landing :8124, devgraph :8125,
> bbsim on loopback ports); bind servers to 127.0.0.1; don't point work at the Vercel/GCP/Tailscale URLs (master deploys).
> Don't commit or push (master batches commits). Don't put assistant or tool names in any file.
> **Open localhost pages in Chrome, never Safari**: `open -a "Google Chrome" http://localhost:8000`; browser tooling uses Chrome/Chromium.

**Goal (updated Sat 13:00): you own the AI layer. Language understanding with OpenAI, and semantic
retrieval on Elasticsearch as our vector store, plus your Housebot Edge executing our jobs.**
Daniel's side now does the deterministic command parsing, all the logic and the job building, then sends
you complete jobs. Contract: `plan/roommate/03-interfaces.md` §12.

| # | task | done when |
|---|---|---|
| 1 | **Intent service:** `POST /v1/intent {text, request_id}` → an Intent matching our schema (`bridge/intent.schema.json`), via OpenAI structured output; low confidence → refuse, never guess | 20 phrasings of find/tidy/move/status/blame/"before dinner" map correctly; unknown text is refused |
| 2 | **Resolver:** `resolve(object_query)` → ranked object ids, built on `elastic/queries.py` hybrid search (Jina vectors + BM25 + rerank); an LLM tie-break only when the top two are close | "where are my keys", "the thing I cut paper with", "my blue mug" → the right object |
| 3 | **Descriptions:** `perception/describe.py` (OpenAI vision) produces the words the vector leg searches; keep `vlm_model` provenance | new objects are findable by description |
| 4 | **The agent** (`agent/`): it uses the Intent + resolver; it never builds jobs (our logic does) | a spoken or typed request goes through one path |
| 5 | **Your edge:** keep executing our `point` / `move` jobs (§12 shapes); the point demo 5× is the first gate | "Where are my keys?" 5 in a row, end to end |

**No separate vector database.** Elasticsearch *is* the vector store (Jina `semantic_text`). A second
store would split the memory and the Elastic prize story. Coordinate query changes with the elastic
workstream, which owns `elastic/queries.py` and the mappings.

**Note (Sat 13:30, from the elastic workstream):** the production reranker (`jina-rerank`) is now jina-reranker-v3.5, which
has a different score scale. Calibrate the resolver's "top two are close" tie-break on scores measured after 13:00. "Where are my
keys" wins by more than 0.3 on every phrasing tested (12/12). PR/chore events must use only the fields in 03 §10: a
`pr_number` field is rejected by the strict mapping (the PR number lives in the branch name `pr/<n>-<slug>`).

**Sentry (for the Sentry prize: one trace across every process).** Add the Python SDK to Housebot Edge (and your intent
service): `sentry_sdk.init(dsn=<our SENTRY_DSN, the gitspace project>, environment="htn2026", server_name="housebot-edge",
traces_sample_rate=1.0, keep_alive=True)`, with the OpenAI integration on for the intent calls (gen_ai spans). In your HTTP
server, CONTINUE the incoming trace: read the `sentry-trace` and `baggage` headers on `POST /v1/jobs` and start the job's
transaction with `sentry_sdk.continue_trace(headers)`. Our dispatcher sends them. Then forward them on your call to the robot
adapter. Result: one trace from the dashboard click → our parser → your edge → the robot adapter → the robot.
