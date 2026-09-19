# SETUP-CHECKLIST — the moment ELASTIC_URL lands

Two lines of `.env`, one paste, and a table of what each failure means. For a fresh project,
or to rebuild this one.

**Status 2026-09-18:** all six lines green against the live project (line 4 run by
the fake's owner: 8,855 docs, 0 rejected).

## 1. Two lines in `../.env` (by hand)

```
ELASTIC_URL=https://<project>.es.<region>.aws.elastic.cloud:443
ELASTIC_API_KEY=<the Encoded value>
```

- **URL:** the project's **Elasticsearch** endpoint — `.es.` in the host. Not the Kibana URL
  (`.kb.`), not a web page link. Keep `https://`.
- **Key:** Kibana → Stack Management → API keys → Create → copy the **Encoded** value (one
  base64 string). Leave privileges unrestricted: setup creates inference endpoints,
  templates and data streams.

## 2. The paste

Stops at the first failure. Run from anywhere.

```zsh
cd /Users/danielwliu/Dev/projects/2026/gitspace/elastic \
&& .venv/bin/python setup_elastic.py \
&& .venv/bin/python setup_elastic.py \
&& .venv/bin/python ingest.py ../story_docs.json \
&& ../.venv/bin/python ../fake/scene_gen.py --index ../fake/out/demo.ndjson \
&& ../.venv/bin/python ../scripts/story_demo.py \
&& .venv/bin/python -m pytest tests -q \
&& echo "ALL GREEN"
```

Fresh checkout without `elastic/.venv`? First:
`cd elastic && uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt`

## 3. What each line should print

| # | command | green looks like | proves |
|---|---|---|---|
| 1 | `setup_elastic.py` | `elasticsearch https://… (serverless …)`; both endpoints `created`; pipeline `room-objects-rerank-text created`; `smoke test embed -> N dims · rerank('mug') -> 'ceramic cup'`; 3 indices `created`; 3 streams `template created, stream created`; `ok -- safe to run again` | cluster, EIS Jina embed + rerank, all six mappings |
| 2 | `setup_elastic.py` again | every line `exists` / `mapping in sync` / `stream exists`; nothing `created` | **acceptance: runs twice, no duplicates** |
| 3 | `ingest.py ../story_docs.json` | `room-clouds 1 written`, `room-observations 3 written` | the original h00 docs (Sentry trace `50c0ccf2…`, issue 7741490949) finally in ES. Must run before line 5, which overwrites that file |
| 4 | `scene_gen.py --index …` | per index `N indexed`, no `FAILED` | the fake room: ~8.9k docs (all six indices) through the strict mappings |
| 5 | `story_demo.py` | `read : capture_id … -> room-clouds 1, room-observations 3`, same for `sentry_trace_id`, then `joined: both lookups return exactly the 4 documents written` | **the Sentry ↔ Elastic join, read back from ES** — exit 0 only if both directions match |
| 6 | `pytest tests -q` | `146 passed` | offline invariants (mappings, records, shapes, connect, ingest, rotation) + every query in `queries.py`, live, on isolated `test-` indices (deleted after) |

Line 1 or 2 printing `FAILED` for a step exits non-zero, so the paste stops there.

## 4. If a line fails

| symptom | cause | do |
|---|---|---|
| `cannot reach …` / name not known | URL wrong: `.kb.` host, missing `https://`, or a typo | fix `ELASTIC_URL`, re-paste |
| `AuthenticationException` / 401 | not the Encoded value, or revoked | new key, re-paste |
| `jina-embed: …` or the smoke test fails | EIS doesn't offer `jina-embeddings-v3` / `jina-reranker-v2-base-multilingual` here | list what it does offer (below), put `ES_EMBED_MODEL=` / `ES_RERANK_MODEL=` in `../.env`, re-paste |
| `exists as elastic/…, config wants jinaai/…` | `ES_INFERENCE_SERVICE=jinaai` is set | EIS is the default and works: remove that line |
| `room-objects` or `room-observations` FAILED naming `fields` / multi-field on `semantic_text` | this project rejects the `raw_description.text` sub-field | **stop** — fallback in `NOTES.md` changes a query field; tell elastic |
| `room-observations` FAILED naming `semantic_text` + `time_series` | semantic_text not allowed in a TSDS | **stop** — tell elastic (fix: `text` under the same name) |
| `robot-telemetry` FAILED naming `downsampling` / `lifecycle` | Serverless refuses the rounds | delete the `"downsampling": [...]` block in `mappings/robot-telemetry.json` (keep `data_retention`), re-paste, tell elastic |
| `… exists as a plain index … --recreate …` | something wrote before setup ran | run the `--recreate <name>` it prints (deletes that index's data), re-paste |
| scene_gen `FAILED: strict_dynamic_mapping_exception` | the fake sends a field no mapping has | add it to `mappings/<index>.json`, re-paste (setup applies it in place) |
| story_demo `ELASTICSEARCH HALF NOT DONE: …` | the message says which half | fix what it names; it never claims a join it didn't read back |
| pytest failures | a query that has never met a real cluster | paste the output to elastic |

What EIS offers on this project:

```zsh
cd /Users/danielwliu/Dev/projects/2026/gitspace/elastic && .venv/bin/python -c "
import setup_elastic as S; es = S.connect()
for e in es.inference.get()['endpoints']:
    print(e['task_type'], e['inference_id'], e['service'], e['service_settings'].get('model_id'))"
```

## 5. After ALL GREEN

- Append to `../PROGRESS.md` with lines 1, 2, 5 and 6 as the `Verified:` evidence.
- Tell web `/api/health` should now be ok (it calibrates its two score thresholds then).
- Start over from empty (deletes every document): `.venv/bin/python setup_elastic.py --recreate all`
