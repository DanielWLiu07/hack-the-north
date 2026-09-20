"""Saved-state boundary with in-memory and development JSON stores."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol

from src.gitirl_agent.state.models import WorldState
from src.gitirl_agent.state.serialization import (
    world_state_from_dict,
    world_state_to_dict,
)


class StateStore(Protocol):
    def load(self, name: str) -> Optional[WorldState]:
        """Return a state, or None when the name is unknown."""

    def save(self, name: str, state: WorldState) -> None:
        """Save a state under a name."""


class InMemoryStateStore:
    """Process-local implementation for development and tests."""

    def __init__(self) -> None:
        self._states: Dict[str, WorldState] = {}

    def load(self, name: str) -> Optional[WorldState]:
        return self._states.get(name)

    def save(self, name: str, state: WorldState) -> None:
        self._states[name] = state


class JsonFileStateStore:
    """Small local JSON store; replaceable by the final persistence adapter."""

    FORMAT_VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self, name: str) -> Optional[WorldState]:
        states = self._read_states()
        raw_state = states.get(name)
        if raw_state is None:
            return None
        if not isinstance(raw_state, dict):
            raise ValueError(f"Saved state {name!r} must be a JSON object")
        return world_state_from_dict(raw_state)

    def save(self, name: str, state: WorldState) -> None:
        states = dict(self._read_states())
        states[name] = world_state_to_dict(state)
        document = {"version": self.FORMAT_VERSION, "states": states}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self._path)

    def _read_states(self) -> Mapping[str, Any]:
        if not self._path.exists():
            return {}
        document = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("State file must contain a JSON object")
        if document.get("version") != self.FORMAT_VERSION:
            raise ValueError("Unsupported state-file version")
        states = document.get("states")
        if not isinstance(states, dict):
            raise ValueError("State file must contain a states object")
        return states
