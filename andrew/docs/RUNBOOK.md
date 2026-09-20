# Caretaker MVP runbook

## Mock edge

```bash
HOUSEBOT_EDGE_TOKEN=dev-token \
python3 scripts/run_edge_api.py --mock
```

Health:

```bash
curl http://127.0.0.1:8780/health
```

Point job:

```bash
curl -X POST http://127.0.0.1:8780/v1/jobs \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"job_id":"demo-1","command":"point","object_id":"keys_7c2e","target_pose":{"x":0.8,"y":0.3,"z":0.9},"zone":"shelf"}'
```

Repeat the same request: it must return the cached result without executing again.

## Separate robot API

Terminal 1:

```bash
HOUSEBOT_ROBOT_TOKEN=dev-robot-token \
python3 scripts/run_robot_api.py --mock --host 0.0.0.0 --port 8765
```

Terminal 2:

```bash
HOUSEBOT_ROBOT_BASE_URL=http://127.0.0.1:8765 \
HOUSEBOT_ROBOT_TOKEN=dev-robot-token \
python3 scripts/run_edge_api.py
```

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

## Safety

- Only one `point` or one-object `move` job is accepted.
- Unsupported jobs are rejected before robot execution.
- Do not expose the edge port publicly.
- Do not connect the real adapter until Ryan/Sarah confirm writer ownership and finite completion.
