"""HTTP RobotAdapter for Ryan/Sarah's robot-side control service."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Optional
from uuid import uuid4

from src.gitirl_agent.planner.models import ActionResult, ActionStatus, RobotAction
from src.gitirl_agent.robot.http_contract import (
    RobotContractError,
    action_request,
    action_result_from_dict,
    require_envelope,
)
from src.gitirl_agent.state.models import WorldState
from src.gitirl_agent.state.serialization import world_state_from_dict


class RobotAPIError(RuntimeError):
    """The robot service was unavailable or returned an invalid response."""


class HTTPRobotAdapter:
    """Synchronous high-level adapter; never opens BBOS control writers."""

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("robot base URL must use http or https")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> Optional["HTTPRobotAdapter"]:
        base_url = (
            os.environ.get("HOUSEBOT_ROBOT_BASE_URL")
            or os.environ.get("GITIRL_ROBOT_BASE_URL", "")
        ).strip()
        if not base_url:
            return None
        return cls(
            base_url,
            token=(
                os.environ.get("HOUSEBOT_ROBOT_TOKEN")
                or os.environ.get("GITIRL_ROBOT_TOKEN")
                or None
            ),
            timeout_seconds=float(
                os.environ.get("HOUSEBOT_ROBOT_TIMEOUT_SECONDS")
                or os.environ.get("GITIRL_ROBOT_TIMEOUT_SECONDS", "60")
            ),
        )

    def observe(self) -> WorldState:
        request_id = str(uuid4())
        query = urllib.parse.urlencode({"request_id": request_id})
        value = self._request("GET", f"/v1/observation?{query}")
        try:
            envelope = require_envelope(value, request_id)
            observation = envelope.get("observation")
            if not isinstance(observation, dict):
                raise RobotContractError("response requires observation")
            return world_state_from_dict(observation)
        except (RobotContractError, ValueError) as error:
            raise RobotAPIError(str(error)) from error

    def execute(self, action: RobotAction) -> ActionResult:
        try:
            value = self._request("POST", "/v1/actions", action_request(action))
            envelope = require_envelope(value, action.request_id)
            result = envelope.get("result")
            if not isinstance(result, dict):
                raise RobotContractError("response requires result")
            return action_result_from_dict(result)
        except (RobotAPIError, RobotContractError, ValueError) as error:
            # Delivery may be ambiguous after a timeout. Never label it retryable:
            # orchestration must re-observe before deciding whether to act again.
            return ActionResult(
                ActionStatus.UNKNOWN,
                f"robot API failed; delivery status may be unknown: {error}",
            )

    def cancel(self, request_id: str) -> ActionResult:
        """Request cancellation; the execution call still carries final status."""
        if not request_id:
            raise ValueError("request_id must be non-empty")
        path_id = urllib.parse.quote(request_id, safe="")
        try:
            value = self._request("POST", f"/v1/actions/{path_id}/cancel")
            envelope = require_envelope(value, request_id)
            result = envelope.get("result")
            if not isinstance(result, dict):
                raise RobotContractError("response requires result")
            return action_result_from_dict(result)
        except (RobotAPIError, RobotContractError, ValueError) as error:
            return ActionResult(ActionStatus.UNKNOWN, f"cancellation status unknown: {error}")

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(
            self._base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout_seconds
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                detail = {"error": "http_error", "detail": str(error)}
            raise RobotAPIError(
                f"HTTP {error.code}: {detail.get('error', 'robot_error')}: "
                f"{detail.get('detail', '')}"
            ) from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RobotAPIError(str(error)) from error
