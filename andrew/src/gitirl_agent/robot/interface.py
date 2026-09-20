"""Platform-facing interface for a future robot integration."""

from typing import Protocol

from src.gitirl_agent.planner.models import ActionResult, RobotAction
from src.gitirl_agent.state.models import WorldState


class RobotAdapter(Protocol):
    def observe(self) -> WorldState:
        """Return a fresh observation translated into the generic state model."""

    def execute(self, action: RobotAction) -> ActionResult:
        """Attempt one robot-independent high-level action."""


# TODO: Ryan/Sarah replace or implement this adapter using only confirmed
# BracketBot observation, execution, and completion interfaces.
