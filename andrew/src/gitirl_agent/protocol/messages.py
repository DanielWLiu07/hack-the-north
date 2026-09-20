"""Development-default messages exchanged with the central backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Tuple, Union

from src.gitirl_agent.commands.models import CommandType
from src.gitirl_agent.planner.models import ActionResult, RobotAction
from src.gitirl_agent.state.diff import StateDiff
from src.gitirl_agent.state.models import WorldState


class MessageType(str, Enum):
    USER_COMMAND = "user_command"
    ROBOT_STATUS = "robot_status"
    ROBOT_OBSERVATION = "robot_observation"
    ROBOT_ACTION_RESULT = "robot_action_result"
    PARSED_COMMAND = "parsed_command"
    ROBOT_ACTION = "robot_action"
    COMMAND_STATUS = "command_status"
    COMMAND_RESULT = "command_result"
    ERROR = "error"


@dataclass(frozen=True)
class UserCommandPayload:
    text: str


@dataclass(frozen=True)
class RobotStatusPayload:
    status: str
    message: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RobotObservationPayload:
    observation: WorldState


@dataclass(frozen=True)
class RobotActionResultPayload:
    result: ActionResult


@dataclass(frozen=True)
class ParsedCommandPayload:
    command: CommandType
    target_state: Optional[str] = None
    message: Optional[str] = None


@dataclass(frozen=True)
class RobotActionPayload:
    action: RobotAction


@dataclass(frozen=True)
class CommandStatusPayload:
    status: str
    message: Optional[str] = None


@dataclass(frozen=True)
class CommandResultPayload:
    status: str
    message: str = ""
    attempts: int = 0
    differences: Tuple[StateDiff, ...] = ()


@dataclass(frozen=True)
class ErrorPayload:
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


MessagePayload = Union[
    UserCommandPayload,
    RobotStatusPayload,
    RobotObservationPayload,
    RobotActionResultPayload,
    ParsedCommandPayload,
    RobotActionPayload,
    CommandStatusPayload,
    CommandResultPayload,
    ErrorPayload,
    Mapping[str, Any],
]


@dataclass(frozen=True)
class MessageEnvelope:
    type: MessageType
    request_id: str
    payload: MessagePayload
    timestamp: Optional[str] = None


# DEVELOPMENT DEFAULTS ONLY. Daniel's authoritative backend contract should be
# accommodated here and in serialization.py, not throughout the service.
