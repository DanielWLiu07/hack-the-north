"""Export the repository's labelled fixture for verify-offline.mjs; no listening server."""
import os
import sys
import json
from pathlib import Path

os.environ['SENTRY_DSN'] = ''
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
import telemetry_api
import sentry_client


class PausedElastic:
    class Err(Exception):
        code, detail, status, retryable = 'elastic_paused', 'local visual test', 503, True

    async def search(self, *args, **kwargs):
        raise self.Err()


def block(request):
    raise AssertionError('Network is disabled for this fixture export')


telemetry_api.init(PausedElastic())
telemetry_api.sentry = sentry_client.SentryClient(
    {'SENTRY_DSN': '# PAUSED until 01:00'}, transport=httpx.MockTransport(block))
app = FastAPI()
app.include_router(telemetry_api.router)
with TestClient(app) as client:
    response = client.get('/api/telemetry/board?limit=12')
    response.raise_for_status()
    Path('/tmp/seer-board-fixture.json').write_text(json.dumps(response.json()))
    print({'source': response.json()['source'], 'captures': len(response.json()['captures'])})
