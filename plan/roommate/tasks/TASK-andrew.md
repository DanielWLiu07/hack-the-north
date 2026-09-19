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
