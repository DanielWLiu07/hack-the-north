"""Structured orchestration outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from src.gitirl_agent.planner.models import RobotAction
from src.gitirl_agent.state.diff import StateDiff


class CommandResultStatus(str, Enum):
    COMMIT_COMPLETE = "COMMIT_COMPLETE"
    DIFF_COMPLETE = "DIFF_COMPLETE"
    RESTORE_COMPLETE = "RESTORE_COMPLETE"
    ALREADY_CORRECT = "ALREADY_CORRECT"
    CONFLICT = "CONFLICT"
    FAILED = "FAILED"
    INVALID_COMMAND = "INVALID_COMMAND"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    UNSUPPORTED_COMMAND = "UNSUPPORTED_COMMAND"


@dataclass(frozen=True)
class CommandResult:
    request_id: str
    status: CommandResultStatus
    message: str = ""
    differences: Tuple[StateDiff, ...] = ()
    actions: Tuple[RobotAction, ...] = ()
    attempts: int = 0
