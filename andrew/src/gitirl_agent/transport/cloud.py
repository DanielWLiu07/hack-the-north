"""Outbound-only HTTP client for Daniel's gitspace cloud service."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Optional


class CloudAPIError(RuntimeError):
    """The cloud service was unavailable or returned an invalid response."""


class CloudConfigurationError(CloudAPIError):
    """A required local cloud-client setting is missing or invalid."""


class CloudJobDiscoveryUnavailable(CloudAPIError):
    """No verified read/claim stream currently exists for discovering jobs."""


class CloudAPIClient:
    """Call gitspace over outbound HTTP(S); never expose an inbound edge port."""

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("cloud base URL must use http or https")
        if parsed.params or parsed.query or parsed.fragment:
            raise ValueError("cloud base URL must not contain params, query, or fragment")
        if timeout_seconds <= 0:
            raise ValueError("cloud timeout must be positive")
        self._base_url = base_url.rstrip("/")
        self._token = token.strip() if token and token.strip() else None
        self._timeout_seconds = float(timeout_seconds)

    @classmethod
    def from_environment(cls) -> Optional["CloudAPIClient"]:
        """Build a client when configured, otherwise leave cloud integration off."""

        base_url = os.environ.get("GITIRL_CLOUD_BASE_URL", "").strip()
        if not base_url:
            return None
        raw_timeout = os.environ.get("GITIRL_CLOUD_TIMEOUT_SECONDS", "30")
        try:
            timeout = float(raw_timeout)
        except ValueError as error:
            raise CloudConfigurationError(
                "GITIRL_CLOUD_TIMEOUT_SECONDS must be a number"
            ) from error
        return cls(
            base_url,
            token=os.environ.get("GITIRL_CLOUD_TOKEN"),
            timeout_seconds=timeout,
        )

    def state(self, ref: str = "HEAD") -> Mapping[str, Any]:
        if not ref:
            raise ValueError("state ref must be non-empty")
        query = urllib.parse.urlencode({"ref": ref})
        return self._request("GET", f"/api/state?{query}")

    def search(
        self,
        query: str,
        limit: int = 20,
        all_time: bool = True,
    ) -> Mapping[str, Any]:
        if not query or len(query) > 200:
            raise ValueError("search query must contain 1 to 200 characters")
        if not 1 <= limit <= 50:
            raise ValueError("search limit must be between 1 and 50")
        encoded = urllib.parse.urlencode(
            {
                "q": query,
                "limit": limit,
                "all_time": str(all_time).lower(),
            }
        )
        return self._request("GET", f"/api/search?{encoded}")

    def plan_command(
        self,
        command: str,
        args: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Create or replay Daniel's deterministic planned job.

        This is intentionally an explicit POST method, not a health probe.
        """

        if not command:
            raise ValueError("command must be non-empty")
        if not isinstance(args, Mapping):
            raise TypeError("command args must be a mapping")
        return self._request(
            "POST", "/api/command", {"command": command, "args": dict(args)}
        )

    def get_job(self, job_id: str) -> Mapping[str, Any]:
        if not job_id:
            raise ValueError("job_id must be non-empty")
        encoded_id = urllib.parse.quote(job_id, safe="")
        return self._request("GET", f"/api/jobs/{encoded_id}")

    def report(
        self,
        job_id: str,
        result: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Submit one terminal result; fail locally when the token is absent."""

        if not job_id:
            raise ValueError("job_id must be non-empty")
        if not isinstance(result, Mapping):
            raise TypeError("result must be a mapping")
        if not self._token:
            raise CloudConfigurationError(
                "GITIRL_CLOUD_TOKEN is required to report a job result"
            )
        encoded_id = urllib.parse.quote(job_id, safe="")
        return self._request(
            "POST",
            f"/api/jobs/{encoded_id}/result",
            dict(result),
            authenticated=True,
        )

    def discover_jobs(self) -> None:
        """Fail explicitly until Daniel publishes a verified discovery contract."""

        raise CloudJobDiscoveryUnavailable(
            "cloud job discovery is not specified: GET /api/events was verified "
            "only for status events, not complete or claimable robot jobs"
        )

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Mapping[str, Any]] = None,
        authenticated: bool = False,
    ) -> Mapping[str, Any]:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        if authenticated:
            if not self._token:
                raise CloudConfigurationError(
                    "GITIRL_CLOUD_TOKEN is required for authenticated cloud writes"
                )
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
                document = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                detail = {"error": "http_error", "detail": str(error)}
            if not isinstance(detail, Mapping):
                detail = {"error": "cloud_error", "detail": str(detail)}
            raise CloudAPIError(
                f"HTTP {error.code}: {detail.get('error', 'cloud_error')}: "
                f"{detail.get('detail', '')}"
            ) from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise CloudAPIError(str(error)) from error
        if not isinstance(document, Mapping):
            raise CloudAPIError("cloud response must be a JSON object")
        return document
