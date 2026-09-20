"""Validation performed before orchestration or robot execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from src.gitirl_agent.commands.models import CommandType, GitIRLCommand


@dataclass(frozen=True)
class ValidationError:
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: Tuple[ValidationError, ...] = ()


def validate_command(command: GitIRLCommand) -> ValidationResult:
    errors = []

    if not isinstance(command.command, CommandType):
        errors.append(
            ValidationError("unknown_command", "Unknown or ambiguous command")
        )
    elif command.command is CommandType.RESTORE and not command.target_state:
        errors.append(
            ValidationError("missing_target_state", "restore requires target_state")
        )
    elif (
        command.command is CommandType.COMMIT
        and not command.target_state
        and not command.message
    ):
        errors.append(
            ValidationError(
                "missing_commit_identity",
                "commit requires a state name or message",
            )
        )

    return ValidationResult(valid=not errors, errors=tuple(errors))
