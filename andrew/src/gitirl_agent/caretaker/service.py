"""Small synchronous cloud-job to robot-action service."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

from src.gitirl_agent.planner.models import ActionStatus, RobotAction
from src.gitirl_agent.protocol.daniel import (
    DanielContractError,
    point_action_from_daniel_job,
    robot_actions_from_daniel_job,
)
from src.gitirl_agent.robot.interface import RobotAdapter
from src.gitirl_agent.robot.http_contract import robot_action_to_dict


class CaretakerJobError(ValueError):
    """A cloud job is unsafe, incomplete, or unsupported."""


@dataclass(frozen=True)
class CaretakerJobResult:
    job_id: str
    status: str
    message: str
    attempts: int = 0
    actions: Tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "message": self.message,
            "attempts": self.attempts,
            "actions": [dict(action) for action in self.actions],
        }


class CaretakerService:
    """Validate, serialize, and execute one cloud job at a time.

    Completed job IDs are cached in memory so an HTTP retry during the same
    process does not repeat a physical action. Durable idempotency belongs in
    Daniel's job store once that contract exists.
    """

    def __init__(self, robot: RobotAdapter) -> None:
        self._robot = robot
        self._lock = threading.Lock()
        self._completed: Dict[str, CaretakerJobResult] = {}

    def execute(self, document: Mapping[str, Any]) -> CaretakerJobResult:
        job_id = document.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise CaretakerJobError("job_id must be a non-empty string")

        with self._lock:
            prior = self._completed.get(job_id)
            if prior is not None:
                return prior

            action = self._translate(document)
            result = self._robot.execute(action)
            action_summary = {
                "action": robot_action_to_dict(action),
                "status": result.status.value,
                "message": result.message,
            }
            if result.status is ActionStatus.SUCCESS:
                status = "succeeded"
            elif result.status is ActionStatus.RETRYABLE:
                status = "retryable_failure"
            else:
                # Daniel's current contract accepts only succeeded, failed, or
                # retryable_failure. Unknown/ambiguous completion must fail
                # closed so cloud state never records an unverified success.
                status = "failed"
            completed = CaretakerJobResult(
                job_id,
                status,
                "caretaker job completed" if status == "succeeded" else result.message,
                1,
                (action_summary,),
            )
            self._completed[job_id] = completed
            return completed

    def _translate(self, document: Mapping[str, Any]) -> RobotAction:
        command = document.get("command")
        try:
            if command == "point":
                return point_action_from_daniel_job(document)
            if command != "move":
                raise CaretakerJobError("command must be point or move")
            translated = robot_actions_from_daniel_job(document)
        except DanielContractError as error:
            raise CaretakerJobError(str(error)) from error
        if translated.unsupported:
            reasons = "; ".join(item.reason for item in translated.unsupported)
            raise CaretakerJobError(f"job contains unsupported operations: {reasons}")
        if not translated.actions:
            raise CaretakerJobError("job contains no executable actions")
        if len(translated.actions) != 1:
            raise CaretakerJobError("MVP jobs must contain exactly one action")
        return translated.actions[0]
