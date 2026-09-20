"""Translate Daniel's current HTTP payloads into stable edge-layer types.

This is intentionally the only module that knows the current shapes returned by
Daniel's ``GET /api/state`` and ``POST /api/command`` endpoints.  If those APIs
change, adapt this module instead of teaching state, planning, or robot code
about cloud-specific fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from src.gitirl_agent.planner.models import ActionType, RobotAction
from src.gitirl_agent.state.models import ObjectState, WorldState


DANIEL_FRAME = "world_z_up"
DANIEL_UNITS = {
    "position": "m",
    "yaw": "deg",
    "duration": "s",
}

# Preserve Daniel's source frame until a measured cloud-to-robot transform is
# applied. Relabeling these coordinates as SLAM/native would be unsafe.
CLOUD_POSE_METADATA = {
    "coordinate_frame": DANIEL_FRAME,
    "position_unit": "m",
    "yaw_unit": "deg",
}


class DanielContractError(ValueError):
    """A cloud document cannot be normalized without guessing."""


@dataclass(frozen=True)
class UnsupportedCloudOperation:
    object_id: Optional[str]
    operation: Optional[str]
    reason: str


@dataclass(frozen=True)
class DanielJobTranslation:
    actions: Tuple[RobotAction, ...] = ()
    unsupported: Tuple[UnsupportedCloudOperation, ...] = ()


def world_state_from_daniel(document: Mapping[str, Any]) -> WorldState:
    """Normalize Daniel's current ``GET /api/state`` response.

    The confirmed cloud pose is canonical world, Z-up, metres, with yaw as a
    180-degree object axis in degrees.  Robot-side code must still perform any
    required world-to-robot transform; the edge never relabels axes implicitly.
    """

    _require_contract(document)
    raw_objects = document.get("objects")
    if not isinstance(raw_objects, list):
        raise DanielContractError("cloud state requires an objects list")
    sha = document.get("sha")
    if sha is not None and not isinstance(sha, str):
        raise DanielContractError("cloud state sha must be a string")

    objects = tuple(_state_object(item, sha) for item in raw_objects)
    return WorldState(
        objects=objects,
        metadata={
            "source": "daniel_api_state",
            "commit_sha": sha,
            "coordinate_frame": DANIEL_FRAME,
            "position_unit": DANIEL_UNITS["position"],
            "yaw_unit": DANIEL_UNITS["yaw"],
        },
    )


def robot_actions_from_daniel_job(
    document: Mapping[str, Any],
    request_id: Optional[str] = None,
) -> DanielJobTranslation:
    """Convert a Daniel command job's physical ops to conservative actions.

    Only a confirmed ``moved`` operation has a safe generic translation today.
    Added, removed, changed, or malformed operations are returned as unsupported
    and never reach robot execution.
    """

    _require_contract(document)
    raw_ops = document.get("ops")
    if not isinstance(raw_ops, list):
        raise DanielContractError("cloud job requires an ops list")
    job_id = document.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise DanielContractError("cloud job requires job_id")
    correlation_id = request_id or job_id

    actions = []
    unsupported = []
    for raw in raw_ops:
        if not isinstance(raw, dict):
            unsupported.append(
                UnsupportedCloudOperation(None, None, "operation must be an object")
            )
            continue
        object_id = raw.get("object_id")
        operation = raw.get("op")
        if not isinstance(object_id, str) or not object_id:
            unsupported.append(
                UnsupportedCloudOperation(None, _optional_string(operation), "object_id is required")
            )
            continue
        if operation != "moved":
            unsupported.append(
                UnsupportedCloudOperation(
                    object_id,
                    _optional_string(operation),
                    "only moved operations have a confirmed robot-independent action",
                )
            )
            continue
        try:
            source = _operation_object(raw, "from", object_id)
            target = _operation_object(raw, "to", object_id)
        except DanielContractError as error:
            unsupported.append(
                UnsupportedCloudOperation(object_id, operation, str(error))
            )
            continue

        actions.append(
            RobotAction(
                action_type=ActionType.MOVE_OBJECT,
                request_id=correlation_id,
                object_id=object_id,
                source=source,
                target=target,
                metadata={
                    "source": "daniel_api_command",
                    "job_id": job_id,
                    "target_commit": document.get("target"),
                    **CLOUD_POSE_METADATA,
                },
            )
        )

    return DanielJobTranslation(tuple(actions), tuple(unsupported))


def point_action_from_daniel_job(document: Mapping[str, Any]) -> RobotAction:
    """Normalize Daniel's current ``/api/object-life/{id}/point`` job."""

    _require_contract(document)
    job_id = document.get("job_id")
    object_id = document.get("object_id")
    if not isinstance(job_id, str) or not job_id:
        raise DanielContractError("point job requires job_id")
    if document.get("command") != "point":
        raise DanielContractError("point job command must be point")
    if not isinstance(object_id, str) or not object_id:
        raise DanielContractError("point job requires object_id")
    position, orientation = _pose(document.get("target_pose"), f"{object_id}.target_pose")
    target = ObjectState(
        object_id=object_id,
        position=position,
        orientation=orientation,
        metadata={"zone": document.get("zone"), **CLOUD_POSE_METADATA},
    )
    return RobotAction(
        action_type=ActionType.POINT_AT_OBJECT,
        request_id=job_id,
        object_id=object_id,
        target=target,
        metadata={
            "source": "daniel_object_point",
            "job_id": job_id,
            "pointing_at": document.get("pointing_at"),
            **CLOUD_POSE_METADATA,
        },
    )


def _state_object(value: Any, sha: Optional[str]) -> ObjectState:
    if not isinstance(value, dict):
        raise DanielContractError("cloud state object must be an object")
    object_id = value.get("object_id")
    if not isinstance(object_id, str) or not object_id:
        raise DanielContractError("cloud state object requires object_id")
    position, orientation = _pose(value.get("pose"), f"{object_id}.pose")
    return ObjectState(
        object_id=object_id,
        label=_optional_string(value.get("class")),
        position=position,
        orientation=orientation,
        metadata={
            "zone": value.get("zone"),
            "color": value.get("color"),
            "extents": value.get("extents"),
            "commit_sha": sha,
            **CLOUD_POSE_METADATA,
        },
    )


def _operation_object(
    operation: Mapping[str, Any], field: str, object_id: str
) -> ObjectState:
    position, orientation = _pose(operation.get(field), f"{object_id}.{field}")
    return ObjectState(
        object_id=object_id,
        label=_optional_string(operation.get("class")),
        position=position,
        orientation=orientation,
        metadata={
            "zone": operation.get("zone"),
            **CLOUD_POSE_METADATA,
        },
    )


def _require_contract(document: Mapping[str, Any]) -> None:
    """Reject coordinate data whose meaning is not the confirmed contract."""

    frame = document.get("frame")
    if frame != DANIEL_FRAME:
        raise DanielContractError(f"cloud document frame must be {DANIEL_FRAME}")
    units = document.get("units")
    if not isinstance(units, Mapping):
        raise DanielContractError("cloud document requires units")
    for quantity, expected in DANIEL_UNITS.items():
        if units.get(quantity) != expected:
            raise DanielContractError(
                f"cloud document units.{quantity} must be {expected}"
            )


def _pose(value: Any, field: str) -> tuple[Mapping[str, float], Mapping[str, float]]:
    if not isinstance(value, dict):
        raise DanielContractError(f"{field} requires a pose")
    position = {}
    for axis in ("x", "y", "z"):
        coordinate = value.get(axis)
        if not _number(coordinate):
            raise DanielContractError(f"{field}.{axis} must be a number")
        position[axis] = float(coordinate)
    orientation = {}
    if "yaw" in value:
        yaw = value["yaw"]
        if not _number(yaw):
            raise DanielContractError(f"{field}.yaw must be a number")
        orientation["yaw"] = float(yaw)
    return position, orientation


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _optional_string(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None
