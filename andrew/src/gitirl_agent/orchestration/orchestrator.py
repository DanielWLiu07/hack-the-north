"""Validated orchestration across state, planning, robot, and verification."""

from __future__ import annotations

from typing import Optional

from src.gitirl_agent.commands.models import CommandType, GitIRLCommand
from src.gitirl_agent.commands.validation import validate_command
from src.gitirl_agent.orchestration.results import (
    CommandResult,
    CommandResultStatus,
)
from src.gitirl_agent.planner.models import ActionStatus
from src.gitirl_agent.planner.planner import DeterministicPlanner
from src.gitirl_agent.robot.interface import RobotAdapter
from src.gitirl_agent.state.diff import DiffEngine
from src.gitirl_agent.state.store import StateStore
from src.gitirl_agent.verification.verifier import (
    VerificationStatus,
    Verifier,
)


MAX_RETRIES = 2


class Orchestrator:
    def __init__(
        self,
        state_store: StateStore,
        robot: RobotAdapter,
        diff_engine: Optional[DiffEngine] = None,
        planner: Optional[DeterministicPlanner] = None,
        verifier: Optional[Verifier] = None,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        self._state_store = state_store
        self._robot = robot
        self._diff_engine = diff_engine or DiffEngine()
        self._planner = planner or DeterministicPlanner()
        self._verifier = verifier or Verifier(self._diff_engine)
        self._max_retries = max_retries

    def execute_command(self, command: GitIRLCommand) -> CommandResult:
        validation = validate_command(command)
        if not validation.valid:
            return CommandResult(
                request_id=command.request_id,
                status=CommandResultStatus.INVALID_COMMAND,
                message="; ".join(error.message for error in validation.errors),
            )
        if command.command is CommandType.COMMIT:
            return self.commit(command.target_state or command.message or "", command.request_id)
        if command.command is CommandType.DIFF:
            if not command.target_state:
                return CommandResult(
                    request_id=command.request_id,
                    status=CommandResultStatus.INVALID_COMMAND,
                    message="diff requires a target state in the current MVP",
                )
            return self.diff(command.target_state, command.request_id)
        if command.command is CommandType.RESTORE:
            return self.restore(command.target_state or "", command.request_id)
        return CommandResult(
            request_id=command.request_id,
            status=CommandResultStatus.UNSUPPORTED_COMMAND,
            message="Command is recognized but not orchestrated yet",
        )

    def commit(self, state_name: str, request_id: str) -> CommandResult:
        current = self._robot.observe()
        self._state_store.save(state_name, current)
        return CommandResult(
            request_id=request_id,
            status=CommandResultStatus.COMMIT_COMPLETE,
            message=f"Saved current world state as {state_name}",
        )

    def diff(self, target_state: str, request_id: str) -> CommandResult:
        desired = self._state_store.load(target_state)
        if desired is None:
            return CommandResult(
                request_id=request_id,
                status=CommandResultStatus.UNKNOWN_STATE,
                message=f"Unknown saved state: {target_state}",
            )
        differences = self._diff_engine.compare(self._robot.observe(), desired)
        return CommandResult(
            request_id=request_id,
            status=CommandResultStatus.DIFF_COMPLETE,
            message=f"Found {len(differences)} changed object(s)",
            differences=differences,
        )

    def restore(self, target_state: str, request_id: str) -> CommandResult:
        desired = self._state_store.load(target_state)
        if desired is None:
            return CommandResult(
                request_id=request_id,
                status=CommandResultStatus.UNKNOWN_STATE,
                message=f"Unknown saved state: {target_state}",
            )

        current = self._robot.observe()
        differences = self._diff_engine.compare(current, desired)
        if not differences:
            return CommandResult(
                request_id=request_id,
                status=CommandResultStatus.ALREADY_CORRECT,
                message="Current world already matches the desired state",
            )

        plan = self._planner.plan(differences, request_id)
        if plan.conflicts:
            return CommandResult(
                request_id=request_id,
                status=CommandResultStatus.CONFLICT,
                message="One or more differences have no safe generic action",
                differences=differences,
                actions=plan.actions,
            )

        total_attempts = 0
        for action in plan.actions:
            if action.object_id is None:
                return CommandResult(
                    request_id=request_id,
                    status=CommandResultStatus.CONFLICT,
                    message="Action cannot be verified without an object_id",
                    differences=differences,
                    actions=plan.actions,
                    attempts=total_attempts,
                )

            for attempt_index in range(self._max_retries + 1):
                action_result = self._robot.execute(action)
                total_attempts += 1
                observed = self._robot.observe()
                verification = self._verifier.check(
                    action.object_id,
                    observed,
                    desired,
                )

                if verification.status is VerificationStatus.VERIFIED:
                    break
                if (
                    verification.status is VerificationStatus.HARD_CONFLICT
                    or action_result.status is ActionStatus.FAILED
                ):
                    return CommandResult(
                        request_id=request_id,
                        status=CommandResultStatus.CONFLICT,
                        message=verification.message or action_result.message,
                        differences=differences,
                        actions=plan.actions,
                        attempts=total_attempts,
                    )
                if attempt_index == self._max_retries:
                    return CommandResult(
                        request_id=request_id,
                        status=CommandResultStatus.FAILED,
                        message="Verification failed after maximum retries",
                        differences=differences,
                        actions=plan.actions,
                        attempts=total_attempts,
                    )

        remaining = self._diff_engine.compare(self._robot.observe(), desired)
        if remaining:
            return CommandResult(
                request_id=request_id,
                status=CommandResultStatus.CONFLICT,
                message="World state still differs after planned actions",
                differences=remaining,
                actions=plan.actions,
                attempts=total_attempts,
            )
        return CommandResult(
            request_id=request_id,
            status=CommandResultStatus.RESTORE_COMPLETE,
            message="Desired state restored and verified",
            actions=plan.actions,
            attempts=total_attempts,
        )
