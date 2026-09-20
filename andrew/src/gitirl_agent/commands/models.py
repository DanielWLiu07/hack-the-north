"""Typed platform-command models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
from uuid import uuid4


class CommandType(str, Enum):
    ADD = "add"
    COMMIT = "commit"
    STATUS = "status"
    DIFF = "diff"
    RESTORE = "restore"
    LOG = "log"


@dataclass(frozen=True)
class GitIRLCommand:
    """A normalized request accepted by gitirl-agent."""

    command: Optional[CommandType]
    target_state: Optional[str] = None
    message: Optional[str] = None
    raw_text: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid4()))
    metadata: Mapping[str, Any] = field(default_factory=dict)
