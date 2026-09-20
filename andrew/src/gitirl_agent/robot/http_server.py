"""Small robot-side HTTP server template for Ryan/Sarah's implementation."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping, Optional, Protocol
from urllib.parse import parse_qs, unquote, urlparse

from src.gitirl_agent.planner.models import ActionResult, RobotAction
from src.gitirl_agent.robot.http_contract import (
    ROBOT_API_VERSION,
    RobotContractError,
    action_response,
    error_document,
    observation_response,
    require_envelope,
    robot_action_from_dict,
)
from src.gitirl_agent.state.models import WorldState


MAX_REQUEST_BYTES = 1024 * 1024


class RobotAPIBackend(Protocol):
    """The only methods Ryan/Sarah need to connect to their robot stack."""

    def observe(self) -> WorldState:
        """Return a fresh semantic observation with stable object IDs."""

    def execute(self, action: RobotAction) -> ActionResult:
        """Return only after terminal success, failure, or retryable failure."""

    def cancel(self, request_id: str) -> ActionResult:
        """Signal cancellation of an active request when supported."""


class RobotHTTPServer:
    def __init__(
        self,
        backend: RobotAPIBackend,
        host: str = "127.0.0.1",
        port: int = 8765,
        token: Optional[str] = None,
    ) -> None:
        handler = _handler_for(backend, token)
        self._server = ThreadingHTTPServer((host, port), handler)

    @property
    def address(self) -> tuple:
        return self._server.server_address

    @property
    def base_url(self) -> str:
        host, port = self.address[:2]
        return f"http://{host}:{port}"

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _handler_for(backend: RobotAPIBackend, token: Optional[str]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "gitirl-robot-api/1"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send(HTTPStatus.OK, {"ok": True, "version": ROBOT_API_VERSION})
                return
            if not self._authorized(token):
                return
            if parsed.path != "/v1/observation":
                self._send(HTTPStatus.NOT_FOUND, error_document("not_found", "unknown endpoint"))
                return
            request_id = (parse_qs(parsed.query).get("request_id") or [""])[0]
            if not request_id:
                self._send(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    error_document("bad_request", "request_id is required"),
                )
                return
            try:
                state = backend.observe()
                self._send(HTTPStatus.OK, observation_response(request_id, state))
            except Exception as error:  # robot backend errors become explicit API errors
                self._send(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    error_document("observation_failed", str(error), retryable=True),
                )

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if not self._authorized(token):
                return
            path = urlparse(self.path).path
            if path.startswith("/v1/actions/") and path.endswith("/cancel"):
                request_id = unquote(
                    path[len("/v1/actions/") : -len("/cancel")].strip("/")
                )
                if not request_id:
                    self._send(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        error_document("bad_request", "request_id is required"),
                    )
                    return
                cancel = getattr(backend, "cancel", None)
                if cancel is None:
                    self._send(
                        HTTPStatus.NOT_IMPLEMENTED,
                        error_document("unsupported", "backend does not support cancellation"),
                    )
                    return
                try:
                    self._send(HTTPStatus.OK, action_response(request_id, cancel(request_id)))
                except Exception as error:
                    self._send(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        error_document("cancellation_failed", str(error), retryable=False),
                    )
                return
            if path != "/v1/actions":
                self._send(HTTPStatus.NOT_FOUND, error_document("not_found", "unknown endpoint"))
                return
            try:
                document = self._read_json()
                envelope = require_envelope(document)
                raw_action = envelope.get("action")
                if not isinstance(raw_action, dict):
                    raise RobotContractError("request requires action")
                action = robot_action_from_dict(raw_action)
                if action.request_id != envelope["request_id"]:
                    raise RobotContractError("action request_id does not match envelope")
            except RobotContractError as error:
                self._send(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    error_document("bad_request", str(error)),
                )
                return
            except ValueError as error:
                self._send(HTTPStatus.BAD_REQUEST, error_document("bad_json", str(error)))
                return

            try:
                result = backend.execute(action)
                self._send(HTTPStatus.OK, action_response(action.request_id, result))
            except Exception as error:
                self._send(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    error_document("execution_failed", str(error), retryable=False),
                )

        def _authorized(self, expected: Optional[str]) -> bool:
            if expected is None:
                return True
            if self.headers.get("Authorization") == f"Bearer {expected}":
                return True
            self._send(HTTPStatus.UNAUTHORIZED, error_document("unauthorized", "invalid token"))
            return False

        def _read_json(self) -> Mapping[str, Any]:
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length or "0")
            except ValueError as error:
                raise ValueError("invalid Content-Length") from error
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("request body size is invalid")
            try:
                value = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("body must be valid JSON") from error
            if not isinstance(value, dict):
                raise ValueError("body must be a JSON object")
            return value

        def _send(self, status: HTTPStatus, document: Mapping[str, Any]) -> None:
            payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler
