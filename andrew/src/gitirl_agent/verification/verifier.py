"""Verification against a fresh observation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.gitirl_agent.state.diff import DiffEngine
from src.gitirl_agent.state.models import WorldState


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    RETRYABLE_FAILURE = "retryable_failure"
    HARD_CONFLICT = "hard_conflict"


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    message: str = ""


class Verifier:
    def __init__(self, diff_engine: DiffEngine) -> None:
        self._diff_engine = diff_engine

    def check(
        self,
        object_id: str,
        current: WorldState,
        desired: WorldState,
    ) -> VerificationResult:
        desired_object = desired.get(object_id)
        current_object = current.get(object_id)

        if desired_object is None:
            return VerificationResult(
                VerificationStatus.HARD_CONFLICT,
                "desired object is unavailable",
            )
        if current_object is None:
            return VerificationResult(
                VerificationStatus.HARD_CONFLICT,
                "object is missing after action",
            )

        differences = self._diff_engine.compare(
            WorldState(objects=(current_object,)),
            WorldState(objects=(desired_object,)),
        )
        if not differences:
            return VerificationResult(VerificationStatus.VERIFIED)
        return VerificationResult(
            VerificationStatus.RETRYABLE_FAILURE,
            "object does not yet match desired state",
        )


# TODO: Confirm verification tolerances and hard-conflict rules after the real
# observation and execution-result semantics are known.
