"""Generic physical-world state models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple


@dataclass(frozen=True)
class ObjectState:
    object_id: str
    label: Optional[str] = None
    position: Any = None
    orientation: Any = None
    relationships: Mapping[str, Any] = field(default_factory=dict)
    confidence: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorldState:
    objects: Tuple[ObjectState, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def get(self, object_id: str) -> Optional[ObjectState]:
        return next(
            (item for item in self.objects if item.object_id == object_id),
            None,
        )
