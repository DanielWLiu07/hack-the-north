# ROTATION — replace the leaked runtime key with no outage window

**Status 2026-09-19:** stopped at step 1, old key `4J1Ot6ABHRDtKTe8PfFD` ("gitspace") still live.
Elasticsearch refuses to let an API key create a narrower key ("If the credential that is used
to authenticate this request is an API key, the derived API key cannot have any privileges"),
and this session only holds an API key. So step 1 is a person in Kibana; everything after it is
`rotate_key.py`, which never prints a key.

**Checked live 2026-09-19 ~03:00 EDT:** `.env` still authenticates as `4J1Ot6ABHRDtKTe8PfFD`
(unrestricted, not invalidated); no `ELASTIC_API_KEY_NEW` / `ELASTIC_ADMIN_API_KEY` yet. The cluster
also holds a SECOND valid, unrestricted key: `OJ06t6ABHRDtKTe8P-6I` ("HackTheNorth", created
2026-09-19T01:14Z). Nothing in `.env` uses it; whether anything else does is unknown. Decide whether
it goes too (`retire` only touches the key it is told to).

## 1. Mint two keys in Kibana (you)

Kibana → Stack Management → API keys → **Create API key** → turn on **Control security
privileges** → paste the role descriptors. No expiry is fine for the weekend.

**`gitspace-runtime`** — what every service uses (web, the publish hook, the hub, perception,
scene_gen, story_demo). Exactly what they call: search / ES|QL / msearch (`read`), `_mapping` and
`_resolve/index` (`view_index_metadata`), bulk index on the snapshot indices and create on the data
streams (`index`, `create_doc`), `GET /` from web's `/api/health` and the publish hook (`monitor`),
the semantic query + Jina rerank + semantic_text embedding (`monitor_inference`). Nothing that
creates or deletes an index, template, endpoint or key.

```json
{
  "gitspace-runtime": {
    "cluster": ["monitor", "monitor_inference"],
    "indices": [
      {
        "names": ["room-objects", "room-voxels", "room-clouds",
                  "room-observations", "robot-telemetry", "room-events"],
        "privileges": ["read", "view_index_metadata", "index", "create_doc"]
      }
    ]
  }
}
```

**`gitspace-admin`** — only `setup_elastic.py` and the live test fixtures (create/delete indices,
templates, the test- copies, inference endpoints, lifecycle). No key management.

```json
{
  "gitspace-admin": {
    "cluster": ["monitor", "monitor_inference", "manage_inference", "manage_index_templates"],
    "indices": [
      { "names": ["room-*", "robot-telemetry*", "test-*"], "privileges": ["all"] }
    ]
  }
}
```

Paste the **Encoded** values into `../.env` yourself (never into chat):

```
ELASTIC_API_KEY_NEW=<gitspace-runtime, Encoded>
ELASTIC_ADMIN_API_KEY=<gitspace-admin, Encoded>
```

## 2–4. The rest (elastic session, or you)

```zsh
cd /Users/danielwliu/Dev/projects/2026/gitspace/elastic
.venv/bin/python rotate_key.py promote        # 2. live key <- new; old kept as ELASTIC_API_KEY_OLD
# restart the long-running processes so they load the new key: the web server on :8000 (you
# restart it yourself -- web-64 won't) and telemetry/hub.py
.venv/bin/python rotate_key.py verify         # 3. identity, privileges, search, ingest (idempotent),
                                              #    publish hook, stale processes, web /api/health
.venv/bin/python rotate_key.py retire --expect-id 4J1Ot6ABHRDtKTe8PfFD
                                              # 4. re-verifies, invalidates the OLD key, proves 401,
                                              #    removes ELASTIC_API_KEY_OLD
```

Any failure before `retire`: `rotate_key.py rollback` puts the old key back — it is still valid
until step 4. `retire` refuses if the old key isn't `4J1Ot6ABHRDtKTe8PfFD` or if verify fails.
`setup_elastic.py` and `pytest tests` use `ELASTIC_ADMIN_API_KEY` when it is set.
