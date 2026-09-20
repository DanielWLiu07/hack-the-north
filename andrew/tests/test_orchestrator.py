import unittest

from src.gitirl_agent.commands.parser import parse_command
from src.gitirl_agent.orchestration.orchestrator import Orchestrator
from src.gitirl_agent.orchestration.results import CommandResultStatus
from src.gitirl_agent.planner.models import ActionType
from src.gitirl_agent.robot.mock import MockRobotAdapter
from src.gitirl_agent.state.models import ObjectState, WorldState
from src.gitirl_agent.state.store import InMemoryStateStore


def world_at(position: str) -> WorldState:
    return WorldState(
        objects=(ObjectState("box_A", label="box", position=position),)
    )


def configured_orchestrator(failures_before_success: int = 0):
    store = InMemoryStateStore()
    store.save("study", world_at("X"))
    robot = MockRobotAdapter(world_at("Y"), failures_before_success)
    return Orchestrator(store, robot), robot


class OrchestratorTests(unittest.TestCase):
    def test_commit_saves_current_state(self) -> None:
        store = InMemoryStateStore()
        robot = MockRobotAdapter(world_at("Y"))
        orchestrator = Orchestrator(store, robot)

        result = orchestrator.execute_command(
            parse_command("gitirl commit workshop", "request-commit")
        )

        self.assertIs(result.status, CommandResultStatus.COMMIT_COMPLETE)
        self.assertEqual(store.load("workshop"), world_at("Y"))
        self.assertEqual(robot.execute_calls, 0)

    def test_diff_returns_displayable_differences_without_executing(self) -> None:
        orchestrator, robot = configured_orchestrator()

        result = orchestrator.execute_command(
            parse_command("gitirl diff study", "request-diff")
        )

        self.assertIs(result.status, CommandResultStatus.DIFF_COMPLETE)
        self.assertEqual(result.differences[0].object_id, "box_A")
        self.assertEqual(result.differences[0].change_type.value, "MOVED")
        self.assertEqual(robot.execute_calls, 0)

    def test_mock_restore_flow(self) -> None:
        orchestrator, robot = configured_orchestrator()

        result = orchestrator.execute_command(
            parse_command("restore study", "request-1")
        )

        self.assertIs(result.status, CommandResultStatus.RESTORE_COMPLETE)
        self.assertEqual(result.attempts, 1)
        self.assertIs(
            robot.executed_actions[0].action_type,
            ActionType.MOVE_OBJECT,
        )
        self.assertEqual(robot.observe().get("box_A").position, "X")

    def test_retry_then_success(self) -> None:
        orchestrator, robot = configured_orchestrator(failures_before_success=1)

        result = orchestrator.restore("study", "request-2")

        self.assertIs(result.status, CommandResultStatus.RESTORE_COMPLETE)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(robot.execute_calls, 2)

    def test_max_retries_exceeded(self) -> None:
        store = InMemoryStateStore()
        store.save("study", world_at("X"))
        robot = MockRobotAdapter(world_at("Y"), failures_before_success=99)
        orchestrator = Orchestrator(store, robot, max_retries=2)

        result = orchestrator.restore("study", "request-3")

        self.assertIs(result.status, CommandResultStatus.FAILED)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(robot.execute_calls, 3)

    def test_unknown_saved_state(self) -> None:
        orchestrator, robot = configured_orchestrator()

        result = orchestrator.restore("unknown", "request-4")

        self.assertIs(result.status, CommandResultStatus.UNKNOWN_STATE)
        self.assertEqual(robot.observe_calls, 0)
        self.assertEqual(robot.execute_calls, 0)


if __name__ == "__main__":
    unittest.main()
