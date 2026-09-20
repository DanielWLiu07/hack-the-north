"""JSON serialization for the provisional message contract."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Mapping, Optional

from src.gitirl_agent.commands.models import CommandType
from src.gitirl_agent.planner.models import ActionResult, ActionStatus
from src.gitirl_agent.protocol.messages import (
    CommandResultPayload,
    CommandStatusPayload,
    ErrorPayload,
    MessageEnvelope,
    MessageType,
    ParsedCommandPayload,
    RobotActionResultPayload,
    RobotObservationPayload,
    RobotStatusPayload,
    UserCommandPayload,
)
from src.gitirl_agent.state.serialization import world_state_from_dict


class ProtocolMessageError(ValueError):
    """A malformed or unsupported provisional message."""


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def serialize_message(message: MessageEnvelope) -> str:
    return json.dumps(_to_json_value(message), separators=(",", ":"))


def deserialize_message(raw: str) -> MessageEnvelope:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise ProtocolMessageError("Message must be valid JSON") from error
    if not isinstance(value, dict):
        raise ProtocolMessageError("Message must be a JSON object")
    return message_from_dict(value)


def message_from_dict(value: Mapping[str, Any]) -> MessageEnvelope:
    raw_type = value.get("type")
    request_id = value.get("request_id")
    payload = value.get("payload")

    try:
        message_type = MessageType(raw_type)
    except (TypeError, ValueError) as error:
        raise ProtocolMessageError("Unknown message type") from error
    if not isinstance(request_id, str) or not request_id:
        raise ProtocolMessageError("request_id must be a non-empty string")
    if not isinstance(payload, dict):
        raise ProtocolMessageError("payload must be a JSON object")

    typed_payload = _payload_from_dict(message_type, payload)
    timestamp = value.get("timestamp")
    if timestamp is not None and not isinstance(timestamp, str):
        raise ProtocolMessageError("timestamp must be a string when supplied")
    return MessageEnvelope(message_type, request_id, typed_payload, timestamp)


def _payload_from_dict(
    message_type: MessageType,
    payload: Mapping[str, Any],
) -> Any:
    if message_type is MessageType.USER_COMMAND:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ProtocolMessageError("user_command payload requires text")
        return UserCommandPayload(text=text)

    if message_type is MessageType.PARSED_COMMAND:
        try:
            command = CommandType(payload.get("command"))
        except (TypeError, ValueError) as error:
            raise ProtocolMessageError("parsed_command requires a command") from error
        return ParsedCommandPayload(
            command=command,
            target_state=_optional_string(payload, "target_state"),
            message=_optional_string(payload, "message"),
        )

    if message_type is MessageType.ROBOT_STATUS:
        status = payload.get("status")
        if not isinstance(status, str):
            raise ProtocolMessageError("robot_status requires status")
        return RobotStatusPayload(
            status=status,
            message=_optional_string(payload, "message"),
            metadata=_mapping(payload.get("metadata")),
        )

    if message_type is MessageType.ROBOT_OBSERVATION:
        observation = payload.get("observation")
        if not isinstance(observation, dict):
            raise ProtocolMessageError("robot_observation requires observation")
        return RobotObservationPayload(world_state_from_dict(observation))

    if message_type is MessageType.ROBOT_ACTION_RESULT:
        raw_result = payload.get("result")
        if not isinstance(raw_result, dict):
            raise ProtocolMessageError("robot_action_result requires result")
        try:
            status = ActionStatus(raw_result.get("status"))
        except (TypeError, ValueError) as error:
            raise ProtocolMessageError("action result requires valid status") from error
        observations = raw_result.get("observations")
        return RobotActionResultPayload(
            ActionResult(
                status=status,
                message=str(raw_result.get("message", "")),
                observations=(
                    world_state_from_dict(observations)
                    if isinstance(observations, dict)
                    else None
                ),
            )
        )

    if message_type is MessageType.COMMAND_STATUS:
        return CommandStatusPayload(
            status=str(payload.get("status", "")),
            message=_optional_string(payload, "message"),
        )
    if message_type is MessageType.COMMAND_RESULT:
        return CommandResultPayload(
            status=str(payload.get("status", "")),
            message=str(payload.get("message", "")),
            attempts=int(payload.get("attempts", 0)),
        )
    if message_type is MessageType.ERROR:
        return ErrorPayload(
            code=str(payload.get("code", "protocol_error")),
            message=str(payload.get("message", "")),
            details=_mapping(payload.get("details")),
        )

    # Robot-action deserialization is intentionally deferred until Daniel and
    # Ryan/Sarah finalize ownership and the authoritative action wire schema.
    return dict(payload)


def _optional_string(value: Mapping[str, Any], key: str) -> Optional[str]:
    item = value.get(key)
    return item if isinstance(item, str) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _to_json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            item.name: _to_json_value(getattr(value, item.name))
            for item in fields(value)
            if getattr(value, item.name) is not None
        }
    if isinstance(value, Mapping):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_to_json_value(item) for item in value]
    return value
