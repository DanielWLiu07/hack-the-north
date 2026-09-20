#!/usr/bin/env python3
"""Run the housebot edge HTTP inlet with a mock or configured robot adapter."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gitirl_agent.caretaker.http_server import CaretakerHTTPServer
from src.gitirl_agent.caretaker.service import CaretakerService
from src.gitirl_agent.robot.http_adapter import HTTPRobotAdapter
from src.gitirl_agent.robot.mock import MockRobotAdapter
from src.gitirl_agent.state.models import ObjectState, WorldState


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()

    if args.mock:
        robot = MockRobotAdapter(
            WorldState(objects=(ObjectState("box_A", label="box", position="Y"),))
        )
    else:
        robot = HTTPRobotAdapter.from_environment()
        if robot is None:
            raise SystemExit("Set HOUSEBOT_ROBOT_BASE_URL (or GITIRL_ROBOT_BASE_URL), or use --mock")

    token = os.environ.get("HOUSEBOT_EDGE_TOKEN") or None
    if args.host not in {"127.0.0.1", "localhost", "::1"} and token is None:
        raise SystemExit("HOUSEBOT_EDGE_TOKEN is required when listening off-host")
    server = CaretakerHTTPServer(
        CaretakerService(robot), host=args.host, port=args.port, token=token
    )
    print(f"housebot edge API: {server.base_url}")
    print("GET /health; POST /v1/jobs; Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
