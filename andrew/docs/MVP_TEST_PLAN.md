# Two-hour MVP test plan

Run this on the shared hackathon Wi-Fi. The home/offline baseline is the unit-test suite; do not debug absent teammate services before everyone is on the same network.

## 0–10 minutes: freeze the demo contract

- Put Daniel, Andrew, and the robot host on the same network and record their LAN IPs.
- Agree on `HOUSEBOT_EDGE_TOKEN` and `HOUSEBOT_ROBOT_TOKEN`; do not commit either value.
- Freeze one known-good `point` job for one visible object. Do not add commands during this session.

## 10–25 minutes: prove each process alone

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
HOUSEBOT_EDGE_TOKEN=dev-edge-token python3 scripts/run_edge_api.py --mock
curl http://127.0.0.1:8780/health
```

Send the example job in `docs/RUNBOOK.md`. Confirm success, then repeat the same `job_id` and confirm it is not executed twice. Also confirm an invalid command is rejected before robot execution.

## 25–45 minutes: prove the two HTTP hops with mocks

On the robot host:

```bash
HOUSEBOT_ROBOT_TOKEN=dev-robot-token \
python3 scripts/run_robot_api.py --mock --host 0.0.0.0 --port 8765
```

On Andrew's laptop:

```bash
HOUSEBOT_ROBOT_BASE_URL=http://ROBOT_IP:8765 \
HOUSEBOT_ROBOT_TOKEN=dev-robot-token \
HOUSEBOT_EDGE_TOKEN=dev-edge-token \
python3 scripts/run_edge_api.py --host 0.0.0.0 --port 8780
```

Daniel sends the frozen job to `POST http://ANDREW_IP:8780/v1/jobs`. Confirm he receives one terminal result. Test a bad token and a duplicate `job_id`.

## 45–90 minutes: replace only the robot mock

- Ryan/Sarah implement the existing `RobotAdapter` boundary using their known-good finite robot operation.
- Keep the edge and Daniel payload unchanged.
- Test observation/action/result translation without adding retries, queues, NLP, or extra commands.
- Stop on any unsafe, ambiguous, or unsupported action; never guess coordinates or object identity.

## 90–110 minutes: full demo repetition

Run the exact UI/SMS → Daniel → edge → robot flow five times. A pass requires:

- the intended object is selected;
- one physical action occurs;
- duplicate delivery does not repeat it during the same edge process;
- invalid work never reaches the robot;
- Daniel receives a clear success or failure result.

Record attempts, latency, and failure cause in `docs/TESTING.md`.

## 110–120 minutes: freeze

- Save the known-good JSON payload and terminal commands.
- Record a backup demo video.
- Revert experimental changes that are not required by the passing flow.
- Do not add features after the five consecutive passes.

## Current limitations

Idempotency is process-local, not durable. The HTTP adapters are provisional. Real robot execution and the final Daniel contract remain teammate integration work.
