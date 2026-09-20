#!/usr/bin/env python3
"""Run the authenticated robot API with a mock or BracketBot NavLink backend."""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gitirl_agent.robot.http_server import RobotHTTPServer
from src.gitirl_agent.robot.mock import MockRobotAdapter
from src.gitirl_agent.robot.navigation import NavigationBackend
from src.gitirl_agent.state.models import ObjectState, WorldState


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    backend_group = parser.add_mutually_exclusive_group(required=True)
    backend_group.add_argument("--mock", action="store_true")
    backend_group.add_argument(
        "--navigation",
        action="store_true",
        help="wrap the robot host's bbapps.nav.drive_to.NavLink",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--nav-python-path",
        default="/home/bracketbot/bbapps/nav",
        help="directory containing drive_to.py on the robot host",
    )
    parser.add_argument(
        "--map-revision",
        help="required immutable identifier for the currently loaded SLAM map",
    )
    arguments = parser.parse_args()
    token = (
        os.environ.get("HOUSEBOT_ROBOT_TOKEN")
        or os.environ.get("GITIRL_ROBOT_TOKEN")
        or None
    )
    if arguments.host not in {"127.0.0.1", "localhost", "::1"} and token is None:
        raise SystemExit("HOUSEBOT_ROBOT_TOKEN is required when listening off-host")
    if arguments.mock:
        current = WorldState(
            objects=(ObjectState("box_A", label="box", position="Y"),)
        )
        backend = MockRobotAdapter(current)
        backend_name = "mock"
    else:
        if not arguments.map_revision:
            raise SystemExit("--map-revision is required with --navigation")
        sys.path.insert(0, arguments.nav_python_path)
        try:
            nav_link_type = getattr(importlib.import_module("drive_to"), "NavLink")
            nav_link = nav_link_type()
        except (ImportError, AttributeError, TypeError) as error:
            raise SystemExit(f"could not construct drive_to.NavLink: {error}") from error
        backend = NavigationBackend(nav_link, map_revision=arguments.map_revision)
        backend_name = "navigation"

    server = RobotHTTPServer(
        backend,
        host=arguments.host,
        port=arguments.port,
        token=token,
    )
    print(f"{backend_name} robot API: {server.base_url}")
    print("Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
