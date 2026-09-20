# Housebot Edge

> Thin integration service for a roommate/caretaker robot.

The repository was originally `gitirl-agent`. The current MVP is simpler: Daniel's app finds a person/object or plans one caretaker job, this edge validates and translates it, and Ryan/Sarah's robot adapter performs the supported physical action.

```text
Daniel web/cloud (laptop or AWS)
       │ HTTP caretaker job
       ▼
Housebot Edge (Andrew's laptop)
       │ high-level RobotAction over HTTP
       ▼
Ryan/Sarah robot adapter (BracketBot host)
```

The edge currently accepts:

- Daniel's existing `point` job from `/api/object-life/{object_id}/point`.
- A job containing confirmed `moved` operations.

It serializes physical jobs, rejects unsupported work, and caches completed job IDs in memory so an HTTP retry does not repeat a physical action. It never opens BBOS motor/control writers.

## Run all three apps

Use the hackathon LAN for the physical loop. AWS is optional for Daniel's public/cloud components, not required for robot control.

Robot-side API, mock for now:

```bash
HOUSEBOT_ROBOT_TOKEN=shared-robot-token \
python3 scripts/run_robot_api.py --mock --host 0.0.0.0 --port 8765
```

Andrew's edge:

```bash
HOUSEBOT_ROBOT_BASE_URL=http://ROBOT_IP:8765 \
HOUSEBOT_ROBOT_TOKEN=shared-robot-token \
HOUSEBOT_EDGE_TOKEN=shared-development-token \
python3 scripts/run_edge_api.py --host 0.0.0.0 --port 8780
```

Daniel sends a complete job to:

```http
POST http://ANDREW_IP:8780/v1/jobs
Authorization: Bearer shared-development-token
Content-Type: application/json
```

Health checks:

```bash
curl http://ANDREW_IP:8780/health
curl http://ROBOT_IP:8765/health
```

## Example caretaker job

```json
{
  "job_id": "job_demo_1",
  "command": "point",
  "object_id": "keys_7c2e",
  "target_pose": {"x": 0.8, "y": 0.3, "z": 0.9},
  "zone": "shelf"
}
```

This becomes `POINT_AT_OBJECT`; Ryan/Sarah still need to implement that capability behind `RobotAdapter`.

## Development

Mock everything in one process:

```bash
python3 scripts/run_edge_api.py --mock
```

Tests:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

For the first shared-Wi-Fi integration session, follow [MVP_TEST_PLAN.md](docs/MVP_TEST_PLAN.md).

## Naming

`Housebot Edge` is the working product/service name. The repository and Python namespace remain `gitirl` / `gitirl_agent` temporarily to avoid breaking imports and teammate links during the hackathon. Rename them only after the team confirms the pivot; see [ARCHITECTURE.md](docs/ARCHITECTURE.md).
