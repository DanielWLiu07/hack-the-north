"""Confirmed BracketBot read boundary and safe integration composition.

This module deliberately does not open BBOS control or torque writers. The
installed robot stack already owns those single-writer topics. Ryan/Sarah's
integration supplies semantic perception and completed-action execution while
this module keeps BBOS details out of orchestration.
"""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Tuple

from src.gitirl_agent.planner.models import ActionResult, RobotAction
from src.gitirl_agent.state.models import WorldState


CAMERA_TOPICS: Mapping[str, str] = {
    "head": "camera.head.jpeg",
    "left": "camera.left.jpeg",
    "right": "camera.right.jpeg",
}
CAMERA_STATUS_TOPICS: Mapping[str, str] = {
    "head": "camera.head.status",
    "left": "camera.left.status",
    "right": "camera.right.status",
}
ARM_STATE_TOPICS: Mapping[str, str] = {
    "left": "arm_left.state",
    "right": "arm_right.state",
}


class BracketBotUnavailableError(RuntimeError):
    """Raised when confirmed BBOS observation topics cannot be read in time."""


@dataclass(frozen=True)
class BracketBotSnapshot:
    """Raw, internal snapshot of confirmed BBOS observation topics.

    JPEG bytes are intentionally not JSON-encoded here. A perception component
    owned by the robot team translates this snapshot into ``WorldState``.
    """

    captured_at: str
    camera_jpegs: Mapping[str, bytes] = field(default_factory=dict)
    camera_status: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    arm_state: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    topic_timestamps: Mapping[str, str] = field(default_factory=dict)

    def summary(self) -> Dict[str, Any]:
        """Return JSON-safe diagnostics without copying raw image payloads."""

        return {
            "captured_at": self.captured_at,
            "cameras": {
                name: {
                    "topic": CAMERA_TOPICS[name],
                    "jpeg_bytes": len(payload),
                    "status": dict(self.camera_status.get(name, {})),
                }
                for name, payload in self.camera_jpegs.items()
            },
            "arms": {
                name: dict(values) for name, values in self.arm_state.items()
            },
            "topic_timestamps": dict(self.topic_timestamps),
        }


class SnapshotInterpreter(Protocol):
    """Ryan/Sarah boundary: turn robot perception into semantic world state."""

    def interpret(self, snapshot: BracketBotSnapshot) -> WorldState:
        """Return stable object identities and generic physical state."""


class RobotActionExecutor(Protocol):
    """Ryan/Sarah boundary: execute one supported action through their stack."""

    def execute(self, action: RobotAction) -> ActionResult:
        """Block until the action has a normalized terminal result."""


class BBOSObservationSource:
    """Read confirmed BracketBot camera/status/arm topics through ``bbos.Reader``.

    ``bbos`` is imported lazily so the rest of gitirl-agent and its tests run on
    non-robot machines without installing robot software.
    """

    def __init__(
        self,
        timeout_seconds: float = 1.0,
        poll_interval_seconds: float = 0.01,
        reader_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds cannot be negative")
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._reader_factory = reader_factory
        self._readers: Dict[str, Any] = {}

    def __enter__(self) -> "BBOSObservationSource":
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def open(self) -> None:
        if self._readers:
            return
        factory = self._reader_factory
        if factory is None:
            try:
                factory = importlib.import_module("bbos").Reader
            except (ImportError, AttributeError) as error:
                raise BracketBotUnavailableError(
                    "BBOS is unavailable; run this adapter on BracketBot with "
                    "/home/bracketbot/bbos on PYTHONPATH"
                ) from error

        topics = tuple(CAMERA_TOPICS.values()) + tuple(
            CAMERA_STATUS_TOPICS.values()
        ) + tuple(ARM_STATE_TOPICS.values())
        opened: Dict[str, Any] = {}
        try:
            for topic in topics:
                opened[topic] = factory(topic, keeptime=False).__enter__()
        except Exception:
            for reader in opened.values():
                reader.__exit__(None, None, None)
            raise
        self._readers = opened

    def close(self) -> None:
        for reader in self._readers.values():
            reader.__exit__(None, None, None)
        self._readers.clear()

    def read_snapshot(self) -> BracketBotSnapshot:
        self.open()
        pending = set(self._readers)
        records: Dict[str, Any] = {}
        deadline = time.monotonic() + self._timeout_seconds

        while pending and time.monotonic() < deadline:
            for topic in tuple(pending):
                reader = self._readers[topic]
                if reader.ready():
                    records[topic] = _copy_record(reader.data)
                    pending.remove(topic)
            if pending and self._poll_interval_seconds:
                time.sleep(self._poll_interval_seconds)

        if pending:
            missing = ", ".join(sorted(pending))
            raise BracketBotUnavailableError(
                f"Timed out waiting for BBOS topics: {missing}"
            )

        timestamps = {
            topic: str(record["timestamp"])
            for topic, record in records.items()
            if "timestamp" in record
        }
        return BracketBotSnapshot(
            captured_at=datetime.now(timezone.utc).isoformat(),
            camera_jpegs={
                name: _jpeg_bytes(records[topic])
                for name, topic in CAMERA_TOPICS.items()
            },
            camera_status={
                name: _without_timestamp(records[topic])
                for name, topic in CAMERA_STATUS_TOPICS.items()
            },
            arm_state={
                name: _without_timestamp(records[topic])
                for name, topic in ARM_STATE_TOPICS.items()
            },
            topic_timestamps=timestamps,
        )


class BracketBotAdapter:
    """Compose confirmed BBOS reads with team-owned semantics and execution.

    The executor must report a terminal result. Merely calling the installed
    ``LiveControls.set_task`` is not completion and must not be reported as
    success; the real executor needs Ryan/Sarah's completion/failure signal.
    """

    def __init__(
        self,
        observations: BBOSObservationSource,
        interpreter: SnapshotInterpreter,
        executor: RobotActionExecutor,
    ) -> None:
        self._observations = observations
        self._interpreter = interpreter
        self._executor = executor

    def observe(self) -> WorldState:
        return self._interpreter.interpret(self._observations.read_snapshot())

    def execute(self, action: RobotAction) -> ActionResult:
        return self._executor.execute(action)

    def close(self) -> None:
        self._observations.close()


def _copy_record(record: Any) -> Dict[str, Any]:
    if isinstance(record, Mapping):
        return {
            str(key): _copy_field(str(key), value)
            for key, value in record.items()
        }
    names: Tuple[str, ...] = tuple(getattr(record.dtype, "names", ()) or ())
    return {name: _copy_field(name, record[name]) for name in names}


def _copy_field(name: str, value: Any) -> Any:
    # Avoid expanding a multi-megabyte uint8 JPEG capacity buffer into a Python
    # list. Preserve a private copy until `_jpeg_bytes` trims it to jpeg_len.
    if name == "jpeg" and hasattr(value, "copy"):
        return value.copy()
    return _to_python(value)


def _to_python(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    return value


def _jpeg_bytes(record: Mapping[str, Any]) -> bytes:
    length = int(record["jpeg_len"])
    return bytes(record["jpeg"][:length])


def _without_timestamp(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: value for key, value in record.items() if key != "timestamp"
    }
