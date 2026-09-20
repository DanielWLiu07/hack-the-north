import unittest

from src.gitirl_agent.planner.models import ActionType
from src.gitirl_agent.planner.planner import DeterministicPlanner
from src.gitirl_agent.state.diff import ChangeType, DiffEngine
from src.gitirl_agent.state.models import ObjectState, WorldState


class PlannerTests(unittest.TestCase):
    def test_moved_object_becomes_move_action(self) -> None:
        current = WorldState(objects=(ObjectState("box_A", position="Y"),))
        desired = WorldState(objects=(ObjectState("box_A", position="X"),))
        differences = DiffEngine().compare(current, desired)

        plan = DeterministicPlanner().plan(differences, "request-1")

        self.assertEqual(differences[0].change_type, ChangeType.MOVED)
        self.assertEqual(len(plan.actions), 1)
        self.assertIs(plan.actions[0].action_type, ActionType.MOVE_OBJECT)
        self.assertEqual(plan.actions[0].request_id, "request-1")
        self.assertEqual(plan.actions[0].target.position, "X")

    def test_missing_object_remains_a_conflict(self) -> None:
        current = WorldState()
        desired = WorldState(objects=(ObjectState("box_A", position="X"),))

        plan = DeterministicPlanner().plan(
            DiffEngine().compare(current, desired),
            "request-2",
        )

        self.assertFalse(plan.actions)
        self.assertEqual(plan.conflicts[0].change_type, ChangeType.MISSING)


if __name__ == "__main__":
    unittest.main()
