import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from src.gitirl_agent.planner.models import ActionStatus, ActionType, NavigationTarget, RobotAction
from src.gitirl_agent.robot.http_adapter import HTTPRobotAdapter
from src.gitirl_agent.robot.http_contract import (
    action_response,
    action_result_from_dict,
    action_result_to_dict,
    observation_response,
    robot_action_from_dict,
    robot_action_to_dict,
)
from src.gitirl_agent.robot.mock import MockRobotAdapter
from src.gitirl_agent.state.models import ObjectState, WorldState


def world_at(position):
    return WorldState(
        objects=(ObjectState("box_A", label="box", position=position),)
    )


class FakeResponse:
    def __init__(self, document):
        self._payload = json.dumps(document).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def read(self):
        return self._payload


class RobotHTTPTests(unittest.TestCase):
    def test_observe_execute_reobserve(self):
        backend = MockRobotAdapter(world_at("Y"))

        def robot_api(request, timeout):
            request_id = request.full_url.split("request_id=", 1)[-1]
            if request.method == "GET":
                return FakeResponse(observation_response(request_id, backend.observe()))
            document = json.loads(request.data.decode("utf-8"))
            action = robot_action_from_dict(document["action"])
            return FakeResponse(action_response(action.request_id, backend.execute(action)))

        client = HTTPRobotAdapter("http://robot.test", timeout_seconds=2)
        with patch(
            "src.gitirl_agent.robot.http_adapter.urllib.request.urlopen",
            side_effect=robot_api,
        ):
            self.assertEqual(client.observe().get("box_A").position, "Y")
            action = RobotAction(
                action_type=ActionType.MOVE_OBJECT,
                request_id="request-1",
                object_id="box_A",
                source=world_at("Y").get("box_A"),
                target=world_at("X").get("box_A"),
            )
            result = client.execute(action)
            observed = client.observe()

        self.assertTrue(result.success)
        self.assertEqual(observed.get("box_A").position, "X")

    def test_http_failure_fails_closed(self):
        client = HTTPRobotAdapter("http://robot.test", timeout_seconds=2)
        body = io.BytesIO(
            b'{"error":"busy","detail":"another action is running"}'
        )
        error = urllib.error.HTTPError(
            "http://robot.test/v1/actions", 409, "Conflict", {}, body
        )
        action = RobotAction(
            action_type=ActionType.MOVE_OBJECT,
            request_id="request-2",
            object_id="box_A",
            target=world_at("X").get("box_A"),
        )

        with patch(
            "src.gitirl_agent.robot.http_adapter.urllib.request.urlopen",
            side_effect=error,
        ):
            result = client.execute(action)

        self.assertIs(result.status, ActionStatus.UNKNOWN)
        self.assertFalse(result.retryable)
        self.assertIn("busy", result.message)

    def test_contract_round_trip(self):
        action = RobotAction(
            action_type=ActionType.VERIFY_OBJECT,
            request_id="request-3",
            object_id="box_A",
            target=world_at("X").get("box_A"),
            metadata={"source": "test"},
        )
        self.assertEqual(
            robot_action_from_dict(robot_action_to_dict(action)), action
        )
        backend = MockRobotAdapter(world_at("Y"))
        result = backend.execute(
            RobotAction(
                action_type=ActionType.MOVE_OBJECT,
                request_id="request-4",
                object_id="box_A",
                target=world_at("X").get("box_A"),
            )
        )
        self.assertEqual(
            action_result_from_dict(action_result_to_dict(result)), result
        )

    def test_navigation_contract_round_trip(self):
        action = RobotAction(
            action_type=ActionType.NAVIGATE_TO_POSE,
            request_id="nav-1",
            navigation_target=NavigationTarget(
                frame="slam_world",
                x=1.25,
                y=-0.5,
                yaw_rad=1.2,
                map_revision="map-2026-09-19",
                tolerance_m=0.3,
                timeout_s=90,
            ),
        )

        self.assertEqual(robot_action_from_dict(robot_action_to_dict(action)), action)

if __name__ == "__main__":
    unittest.main()
