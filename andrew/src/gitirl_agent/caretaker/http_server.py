"""Dependency-free HTTP inlet for Daniel's caretaker jobs."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse

from src.gitirl_agent.caretaker.service import CaretakerJobError, CaretakerService


MAX_REQUEST_BYTES = 1024 * 1024


class CaretakerHTTPServer:
    def __init__(
        self,
        service: CaretakerService,
        host: str = "127.0.0.1",
        port: int = 8780,
        token: Optional[str] = None,
    ) -> None:
        self._server = ThreadingHTTPServer((host, port), _handler_for(service, token))

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


def _handler_for(service: CaretakerService, token: Optional[str]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "housebot-edge/1"

        def do_GET(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/health":
                self._send(HTTPStatus.NOT_FOUND, _error("not_found", "unknown endpoint"))
                return
            self._send(HTTPStatus.OK, {"ok": True, "service": "housebot-edge", "version": 1})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized(token):
                return
            if urlparse(self.path).path != "/v1/jobs":
                self._send(HTTPStatus.NOT_FOUND, _error("not_found", "unknown endpoint"))
                return
            try:
                result = service.execute(self._read_json())
            except CaretakerJobError as error:
                self._send(HTTPStatus.UNPROCESSABLE_ENTITY, _error("invalid_job", str(error)))
                return
            except ValueError as error:
                self._send(HTTPStatus.BAD_REQUEST, _error("bad_json", str(error)))
                return
            except Exception as error:
                self._send(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    _error("robot_unavailable", str(error), retryable=True),
                )
                return
            self._send(HTTPStatus.OK, result.to_dict())

        def _authorized(self, expected: Optional[str]) -> bool:
            if expected is None or self.headers.get("Authorization") == f"Bearer {expected}":
                return True
            self._send(HTTPStatus.UNAUTHORIZED, _error("unauthorized", "invalid token"))
            return False

        def _read_json(self) -> Mapping[str, Any]:
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise ValueError("invalid Content-Length") from error
            if size <= 0 or size > MAX_REQUEST_BYTES:
                raise ValueError("request body size is invalid")
            try:
                value = json.loads(self.rfile.read(size).decode("utf-8"))
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


def _error(code: str, detail: str, retryable: bool = False) -> Dict[str, Any]:
    return {"error": code, "detail": detail, "retryable": retryable}
