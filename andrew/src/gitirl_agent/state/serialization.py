"""JSON-compatible conversion for generic world states."""

from __future__ import annotations

from typing import Any, Dict, Mapping

from src.gitirl_agent.state.models import ObjectState, WorldState


def world_state_to_dict(state: WorldState) -> Dict[str, Any]:
    return {
        "objects": [
            {
                "object_id": item.object_id,
                "label": item.label,
                "position": item.position,
                "orientation": item.orientation,
                "relationships": dict(item.relationships),
                "confidence": item.confidence,
                "metadata": dict(item.metadata),
            }
            for item in state.objects
        ],
        "metadata": dict(state.metadata),
    }


def world_state_from_dict(value: Mapping[str, Any]) -> WorldState:
    raw_objects = value.get("objects", [])
    if not isinstance(raw_objects, list):
        raise ValueError("world state objects must be a list")

    objects = []
    for raw_object in raw_objects:
        if not isinstance(raw_object, dict):
            raise ValueError("world state object must be an object")
        object_id = raw_object.get("object_id")
        if not isinstance(object_id, str) or not object_id:
            raise ValueError("world state object requires object_id")
        objects.append(
            ObjectState(
                object_id=object_id,
                label=_optional_string(raw_object.get("label")),
                position=raw_object.get("position"),
                orientation=raw_object.get("orientation"),
                relationships=_mapping(raw_object.get("relationships")),
                confidence=_optional_number(raw_object.get("confidence")),
                metadata=_mapping(raw_object.get("metadata")),
            )
        )

    return WorldState(
        objects=tuple(objects),
        metadata=_mapping(value.get("metadata")),
    )


def _optional_string(value: Any):
    return value if isinstance(value, str) else None


def _optional_number(value: Any):
    return value if isinstance(value, (int, float)) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}
