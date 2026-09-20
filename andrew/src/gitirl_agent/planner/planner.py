"""Conservative state-diff to high-level-action translation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

from src.gitirl_agent.planner.models import ActionType, RobotAction
from src.gitirl_agent.state.diff import ChangeType, StateDiff


@dataclass(frozen=True)
class PlanningResult:
    actions: Tuple[RobotAction, ...] = ()
    conflicts: Tuple[StateDiff, ...] = ()


class DeterministicPlanner:
    def plan(
        self,
        differences: Sequence[StateDiff],
        request_id: str,
    ) -> PlanningResult:
        actions = []
        conflicts = []

        for difference in differences:
            if difference.change_type is ChangeType.MOVED:
                actions.append(
                    RobotAction(
                        action_type=ActionType.MOVE_OBJECT,
                        request_id=request_id,
                        object_id=difference.object_id,
                        source=difference.current_state,
                        target=difference.desired_state,
                    )
                )
            elif difference.change_type is not ChangeType.UNCHANGED:
                # No safe generic action exists for this discrepancy without
                # confirmed robot and perception capabilities.
                conflicts.append(difference)

        return PlanningResult(tuple(actions), tuple(conflicts))
