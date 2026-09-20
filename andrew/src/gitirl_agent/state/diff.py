"""Deterministic comparison of generic current and desired states."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from src.gitirl_agent.state.models import ObjectState, WorldState


class ChangeType(str, Enum):
    MOVED = "MOVED"
    ADDED = "ADDED"
    MISSING = "MISSING"
    RELATION_CHANGED = "RELATION_CHANGED"
    UNCHANGED = "UNCHANGED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StateDiff:
    object_id: str
    change_type: ChangeType
    current_state: Optional[ObjectState]
    desired_state: Optional[ObjectState]


class DiffEngine:
    def compare(
        self,
        current: WorldState,
        desired: WorldState,
        include_unchanged: bool = False,
    ) -> Tuple[StateDiff, ...]:
        current_by_id = {item.object_id: item for item in current.objects}
        desired_by_id = {item.object_id: item for item in desired.objects}
        entries = []

        for object_id in sorted(current_by_id.keys() | desired_by_id.keys()):
            current_object = current_by_id.get(object_id)
            desired_object = desired_by_id.get(object_id)

            if current_object is None:
                change_type = ChangeType.MISSING
            elif desired_object is None:
                change_type = ChangeType.ADDED
            elif (
                current_object.position != desired_object.position
                or current_object.orientation != desired_object.orientation
            ):
                change_type = ChangeType.MOVED
            elif current_object.relationships != desired_object.relationships:
                change_type = ChangeType.RELATION_CHANGED
            elif current_object.label != desired_object.label:
                change_type = ChangeType.UNKNOWN
            else:
                change_type = ChangeType.UNCHANGED

            if include_unchanged or change_type is not ChangeType.UNCHANGED:
                entries.append(
                    StateDiff(
                        object_id=object_id,
                        change_type=change_type,
                        current_state=current_object,
                        desired_state=desired_object,
                    )
                )

        return tuple(entries)


# TODO: Confirm equality/tolerance rules after Ryan and Sarah provide the real
# perception representation, pose semantics, and confidence behavior.
