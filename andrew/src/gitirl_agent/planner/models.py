"""Robot-independent high-level action contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional

from src.gitirl_agent.state.models import ObjectState, WorldState


class ActionType(str, Enum):
    OBSERVE = "OBSERVE"
    MOVE_OBJECT = "MOVE_OBJECT"
    PICK_OBJECT = "PICK_OBJECT"
    PLACE_OBJECT = "PLACE_OBJECT"
    POINT_AT_OBJECT = "POINT_AT_OBJECT"
    NAVIGATE_TO_POSE = "NAVIGATE_TO_POSE"
    VERIFY_OBJECT = "VERIFY_OBJECT"
    WAIT = "WAIT"
    NO_OP = "NO_OP"


@dataclass(frozen=True)
class NavigationTarget:
    """A planar goal in the robot's persistent SLAM world frame.

    This is deliberately separate from an object's 3D pose. Converting a
    cloud/world position into this frame is a calibrated, deterministic step;
    it must not be inferred by an LLM or by the navigation backend.
    """

    x: float
    y: float
    yaw_rad: float
    map_revision: str
    frame: str = "slam_world"
    tolerance_m: float = 0.35
    timeout_s: float = 120.0


@dataclass(frozen=True)
class RobotAction:
    action_type: ActionType
    request_id: str
    object_id: Optional[str] = None
    source: Optional[ObjectState] = None
    target: Optional[ObjectState] = None
    navigation_target: Optional[NavigationTarget] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ActionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    RETRYABLE = "retryable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ActionResult:
    status: ActionStatus
    message: str = ""
    observations: Optional[WorldState] = None

    @property
    def success(self) -> bool:
        return self.status is ActionStatus.SUCCESS

    @property
    def failed(self) -> bool:
        return self.status is ActionStatus.FAILED

    @property
    def retryable(self) -> bool:
        return self.status is ActionStatus.RETRYABLE

    @property
    def unknown(self) -> bool:
        return self.status is ActionStatus.UNKNOWN
