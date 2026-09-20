# scripts/

Small development/integration utilities. Core logic stays in `src/`.

| File | Purpose | Inputs | Outputs | Env vars |
| --- | --- | --- | --- | --- |
| `run_edge_api.py` | Run caretaker cloud→robot edge inlet | HTTP jobs | terminal job JSON | `HOUSEBOT_EDGE_TOKEN`, `HOUSEBOT_ROBOT_*` |
| `run_dev.py` | Mock CLI or JSONL edge flow | text / JSONL | JSON envelopes | `GITIRL_STATE_FILE`, `GITIRL_ROBOT_*` |
| `run_robot_api.py` | Mock robot HTTP API | HTTP | observation/result JSON | `HOUSEBOT_ROBOT_TOKEN` |
| `inspect_bracketbot.py` | Read-only BBOS smoke test | BBOS topics | JSON summary | `PYTHONPATH` |

Rules:

- No core logic, credentials, cloud infrastructure, or invented robot behavior.
- Scripts call `src/`; do not duplicate contracts.
- Update this table when scripts change.

Read `README.md`, `docs/ARCHITECTURE.md`, `docs/INTEGRATION.md`, and `docs/PROTOCOL.md` before changing integration code.
