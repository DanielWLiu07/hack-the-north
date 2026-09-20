"""Minimal stateful robot adapter for local development and tests only."""

from __future__ import annotations

from typing import Dict

from src.gitirl_agent.planner.models import (
    ActionResult,
    ActionStatus,
    ActionType,
    RobotAction,
)
from src.gitirl_agent.state.models import ObjectState, WorldState


class MockRobotAdapter:
    def __init__(
        self,
        initial_state: WorldState,
        failures_before_success: int = 0,
    ) -> None:
        self._state = initial_state
        self._remaining_failures = failures_before_success
        self.observe_calls = 0
        self.execute_calls = 0
        self.executed_actions = []

    def observe(self) -> WorldState:
        self.observe_calls += 1
        return self._state

    def execute(self, action: RobotAction) -> ActionResult:
        self.execute_calls += 1
        self.executed_actions.append(action)

        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            return ActionResult(ActionStatus.RETRYABLE, "simulated action failure")

        if (
            action.action_type is ActionType.POINT_AT_OBJECT
            and action.object_id is not None
            and action.target is not None
        ):
            return ActionResult(ActionStatus.SUCCESS, "simulated point complete")

        if (
            action.action_type is not ActionType.MOVE_OBJECT
            or action.object_id is None
            or action.target is None
        ):
            return ActionResult(ActionStatus.FAILED, "unsupported mock action")

        objects_by_id: Dict[str, ObjectState] = {
            item.object_id: item for item in self._state.objects
        }
        objects_by_id[action.object_id] = action.target
        self._state = WorldState(
            objects=tuple(objects_by_id.values()),
            metadata=self._state.metadata,
        )
        return ActionResult(ActionStatus.SUCCESS, "simulated action complete")
