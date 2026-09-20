"""Conservative bridge from RobotAction to BracketBot's finite NavLink API."""

from __future__ import annotations

import math
import threading
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, Tuple

from src.gitirl_agent.planner.models import (
    ActionResult,
    ActionStatus,
    ActionType,
    NavigationTarget,
    RobotAction,
)
from src.gitirl_agent.state.models import WorldState


class NavLink(Protocol):
    """The confirmed subset of ``bbapps/nav/drive_to.py`` used here."""

    def go(
        self,
        points: Sequence[Tuple[float, float, Optional[float]]],
        timeout: float = 120.0,
        global_goal: bool = True,
        cancel: Optional[threading.Event] = None,
        exact: bool = True,
    ) -> Mapping[str, Any]: ...


class ObservationSource(Protocol):
    def observe(self) -> WorldState: ...


class NavigationBackend:
    """Robot API backend that serializes and verifies SLAM navigation goals.

    It never opens a BBOS writer itself. ``NavLink`` remains responsible for
    writer ownership, stopping on timeout/cancellation, and terminal state.
    """

    def __init__(
        self,
        nav_link: NavLink,
        *,
        map_revision: str,
        observation_source: Optional[ObservationSource] = None,
        coordinate_limit_m: float = 25.0,
        max_timeout_s: float = 300.0,
    ) -> None:
        if not map_revision:
            raise ValueError("map_revision must be non-empty")
        self._nav_link = nav_link
        self._map_revision = map_revision
        self._observation_source = observation_source
        self._coordinate_limit_m = coordinate_limit_m
        self._max_timeout_s = max_timeout_s
        self._execution_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_request_id: Optional[str] = None
        self._cancel_event: Optional[threading.Event] = None
        self._completed: Dict[str, ActionResult] = {}

    def observe(self) -> WorldState:
        if self._observation_source is None:
            raise RuntimeError("semantic observation source is not configured")
        return self._observation_source.observe()

    def execute(self, action: RobotAction) -> ActionResult:
        with self._state_lock:
            prior = self._completed.get(action.request_id)
            if prior is not None:
                return prior

        if action.action_type is not ActionType.NAVIGATE_TO_POSE:
            return ActionResult(ActionStatus.FAILED, "unsupported navigation action")
        validation_error = self._validate_target(action.navigation_target)
        if validation_error is not None:
            return ActionResult(ActionStatus.FAILED, validation_error)
        target = action.navigation_target
        assert target is not None

        if not self._execution_lock.acquire(blocking=False):
            return ActionResult(ActionStatus.FAILED, "robot navigation is busy")
        cancel_event = threading.Event()
        with self._state_lock:
            self._active_request_id = action.request_id
            self._cancel_event = cancel_event
        try:
            try:
                raw_result = self._nav_link.go(
                    [(target.x, target.y, target.yaw_rad)],
                    timeout=target.timeout_s,
                    global_goal=False,
                    cancel=cancel_event,
                    exact=True,
                )
                result = self._normalize_result(raw_result, target)
            except Exception as error:  # NavLink types only exist on robot host.
                result = self._exception_result(error, cancel_event.is_set())
            with self._state_lock:
                self._completed[action.request_id] = result
            return result
        finally:
            with self._state_lock:
                self._active_request_id = None
                self._cancel_event = None
            self._execution_lock.release()

    def cancel(self, request_id: str) -> ActionResult:
        """Signal cancellation; NavLink performs the stop and terminal wait."""
        with self._state_lock:
            prior = self._completed.get(request_id)
            if prior is not None:
                return prior
            if request_id != self._active_request_id or self._cancel_event is None:
                return ActionResult(ActionStatus.FAILED, "request is not active")
            self._cancel_event.set()
        return ActionResult(ActionStatus.SUCCESS, "cancellation requested")

    def _validate_target(self, target: Optional[NavigationTarget]) -> Optional[str]:
        if target is None:
            return "NAVIGATE_TO_POSE requires navigation_target"
        if target.frame != "slam_world":
            return "navigation frame must be slam_world"
        if target.map_revision != self._map_revision:
            return "navigation map_revision does not match the loaded map"
        values = (target.x, target.y, target.yaw_rad, target.tolerance_m, target.timeout_s)
        if not all(math.isfinite(value) for value in values):
            return "navigation target values must be finite"
        if abs(target.x) > self._coordinate_limit_m or abs(target.y) > self._coordinate_limit_m:
            return "navigation target exceeds configured coordinate bounds"
        if target.yaw_rad < -math.pi or target.yaw_rad > math.pi:
            return "navigation yaw_rad must be between -pi and pi"
        if target.tolerance_m <= 0 or target.tolerance_m > 1.0:
            return "navigation tolerance_m must be in (0, 1.0]"
        if target.timeout_s <= 0 or target.timeout_s > self._max_timeout_s:
            return "navigation timeout_s exceeds configured bounds"
        return None

    @staticmethod
    def _normalize_result(
        value: Mapping[str, Any], target: NavigationTarget
    ) -> ActionResult:
        if not isinstance(value, Mapping):
            return ActionResult(ActionStatus.UNKNOWN, "NavLink returned an invalid result")
        status = value.get("status")
        distance = value.get("distance_to_goal_m")
        if status != "reached":
            return ActionResult(ActionStatus.FAILED, f"navigation ended with status {status!r}")
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            return ActionResult(ActionStatus.UNKNOWN, "navigation omitted final goal distance")
        if not math.isfinite(float(distance)):
            return ActionResult(ActionStatus.UNKNOWN, "navigation returned invalid goal distance")
        if float(distance) > target.tolerance_m:
            return ActionResult(
                ActionStatus.FAILED,
                f"navigation stopped {float(distance):.3f}m from goal",
            )
        return ActionResult(
            ActionStatus.SUCCESS,
            f"navigation reached goal within {float(distance):.3f}m",
        )

    @staticmethod
    def _exception_result(error: Exception, cancelled: bool) -> ActionResult:
        name = type(error).__name__
        message = str(error) or name
        if cancelled or name == "Cancelled":
            return ActionResult(ActionStatus.FAILED, f"navigation cancelled: {message}")
        if isinstance(error, TimeoutError) or name in {"TimeoutError", "NotReached"}:
            return ActionResult(ActionStatus.FAILED, f"navigation did not reach goal: {message}")
        # NavError includes stale/lost WebSocket state. Motion may have begun, so
        # callers must re-establish robot state and must not retry blindly.
        return ActionResult(
            ActionStatus.UNKNOWN,
            f"navigation delivery/completion is unknown: {name}: {message}",
        )
