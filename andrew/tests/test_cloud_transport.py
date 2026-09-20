import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from src.gitirl_agent.transport.cloud import (
    CloudAPIClient,
    CloudAPIError,
    CloudConfigurationError,
    CloudJobDiscoveryUnavailable,
)


class FakeResponse:
    def __init__(self, document):
        self._payload = json.dumps(document).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def read(self):
        return self._payload


class CloudTransportTests(unittest.TestCase):
    def test_reads_use_expected_paths_without_token(self):
        requests = []

        def cloud(request, timeout):
            requests.append((request, timeout))
            return FakeResponse({"ok": True})

        client = CloudAPIClient(
            "https://cloud.test/", token="secret", timeout_seconds=7
        )
        with patch(
            "src.gitirl_agent.transport.cloud.urllib.request.urlopen",
            side_effect=cloud,
        ):
            client.state("feature branch")
            client.search("something to write with", limit=3, all_time=False)
            client.get_job("job/one")

        self.assertEqual(requests[0][1], 7)
        self.assertEqual(
            requests[0][0].full_url,
            "https://cloud.test/api/state?ref=feature+branch",
        )
        self.assertIn("q=something+to+write+with", requests[1][0].full_url)
        self.assertIn("limit=3", requests[1][0].full_url)
        self.assertIn("all_time=false", requests[1][0].full_url)
        self.assertEqual(
            requests[2][0].full_url,
            "https://cloud.test/api/jobs/job%2Fone",
        )
        self.assertTrue(all(r.headers.get("Authorization") is None for r, _ in requests))

    def test_plan_command_posts_document_without_authentication(self):
        seen = {}

        def cloud(request, timeout):
            seen["request"] = request
            return FakeResponse({"job_id": "job_1"})

        client = CloudAPIClient("https://cloud.test", token="secret")
        with patch(
            "src.gitirl_agent.transport.cloud.urllib.request.urlopen",
            side_effect=cloud,
        ):
            response = client.plan_command("checkout", {"ref": "abc123"})

        request = seen["request"]
        self.assertEqual(response["job_id"], "job_1")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.full_url, "https://cloud.test/api/command")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"command": "checkout", "args": {"ref": "abc123"}},
        )
        self.assertIsNone(request.headers.get("Authorization"))

    def test_report_requires_token_before_network(self):
        client = CloudAPIClient("https://cloud.test")
        with patch(
            "src.gitirl_agent.transport.cloud.urllib.request.urlopen"
        ) as urlopen:
            with self.assertRaises(CloudConfigurationError):
                client.report("job_1", {"run_id": "run_1", "status": "failed"})
        urlopen.assert_not_called()

    def test_report_uses_bearer_token(self):
        seen = {}

        def cloud(request, timeout):
            seen["request"] = request
            return FakeResponse({"accepted": True})

        client = CloudAPIClient("https://cloud.test", token="secret")
        with patch(
            "src.gitirl_agent.transport.cloud.urllib.request.urlopen",
            side_effect=cloud,
        ):
            client.report("job_1", {"run_id": "run_1", "status": "success"})

        request = seen["request"]
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        self.assertEqual(request.full_url, "https://cloud.test/api/jobs/job_1/result")

    def test_environment_is_optional_and_validated(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(CloudAPIClient.from_environment())
        with patch.dict(
            os.environ,
            {
                "GITIRL_CLOUD_BASE_URL": "https://cloud.test",
                "GITIRL_CLOUD_TOKEN": "secret",
                "GITIRL_CLOUD_TIMEOUT_SECONDS": "12.5",
            },
            clear=True,
        ):
            self.assertIsInstance(CloudAPIClient.from_environment(), CloudAPIClient)
        with patch.dict(
            os.environ,
            {
                "GITIRL_CLOUD_BASE_URL": "https://cloud.test",
                "GITIRL_CLOUD_TIMEOUT_SECONDS": "soon",
            },
            clear=True,
        ):
            with self.assertRaises(CloudConfigurationError):
                CloudAPIClient.from_environment()

    def test_http_and_invalid_json_fail_closed(self):
        client = CloudAPIClient("https://cloud.test")
        error = urllib.error.HTTPError(
            "https://cloud.test/api/state",
            503,
            "Unavailable",
            {},
            io.BytesIO(b'{"error":"offline","detail":"try later"}'),
        )
        with patch(
            "src.gitirl_agent.transport.cloud.urllib.request.urlopen",
            side_effect=error,
        ):
            with self.assertRaisesRegex(CloudAPIError, "offline: try later"):
                client.state()
        with patch(
            "src.gitirl_agent.transport.cloud.urllib.request.urlopen",
            return_value=FakeResponse(["not", "an", "object"]),
        ):
            with self.assertRaisesRegex(CloudAPIError, "JSON object"):
                client.state()

    def test_job_discovery_is_explicitly_unavailable(self):
        client = CloudAPIClient("https://cloud.test")
        with self.assertRaisesRegex(CloudJobDiscoveryUnavailable, "status events"):
            client.discover_jobs()

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            CloudAPIClient("cloud.test")
        client = CloudAPIClient("https://cloud.test")
        with self.assertRaises(ValueError):
            client.search("")
        with self.assertRaises(ValueError):
            client.search("valid", limit=0)


if __name__ == "__main__":
    unittest.main()
