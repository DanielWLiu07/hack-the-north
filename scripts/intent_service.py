#!/usr/bin/env python3
"""scripts/intent_service.py — the understanding layer: a sentence -> an Intent, with OpenAI.

    .venv/bin/python scripts/intent_service.py --port 8790        then: INTENT_URL=http://127.0.0.1:8790

    POST /v1/intent  {"text": "...", "request_id": "..."} -> an Intent (bridge/intent.schema.json)
                     422 {"error": "refused"} when it is not a household request, or the model is unsure
    GET  /health     {"ok": true, "model": "...", "service": "intent"}

This is the slot Andrew's service fills (plan/roommate/03-interfaces.md §12): same request, same answer,
same validation. `INTENT_URL` points at whichever is running, and nothing else changes.

THE RULES IT OBEYS, because an LLM in a robot's command path is only safe if they hold:
- The model NEVER writes an Intent. It fills FLAT FIELDS under a strict schema; this file assembles the
  Intent, stamps `request_id` and `source: "openai"` itself, and validates the result against OUR schema
  (bridge/intents.validate). A model cannot invent a field, an intent name, or somebody else's request.
- The model never produces a JOB. bridge/caretaker.py turns an Intent into a job, from git and Elastic.
- Unsure is an ANSWER: `understood: false` comes back as 422 `refused` with the model's reason, which the
  bridge reads as "no intent" and reports as unknown_command. Nothing is guessed into a robot command.
- Our grammar runs FIRST and this is only asked when the grammar has no match, so the deterministic path
  is never replaced — only widened to the sentences a person actually says.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bridge import intents  # noqa: E402

try:                      # repo-root obs.py: the LLM call as a gen_ai span, in the CALLER's trace
    import obs
except ImportError:  # pragma: no cover
    obs = None

MAX_BODY = 64 * 1024
OPENAI_URL = "https://api.openai.com/v1/responses"

SYSTEM = """You turn what someone says to a household caretaker robot into structured fields.

The robot lives in one room. It knows where objects are, it can point at one, tidy things back to where
they belong, and it can read the room's history (a git repository of the room).

Fill the fields. Decide `intent` from what the person WANTS:
  find    — where is something ("where are my keys", "I can't find the mug")
  point   — show me where it is ("point at the scissors", "show me the lamp")
  tidy    — put things back where they belong ("clean up the desk", "tidy the room")
  move    — put a named thing in a named place ("put the lamp on the shelf")
  status  — is the room clean / what changed
  blame   — who moved something, when did it move
  restore_time — make the room the way it was at some MOMENT ("like it was before dinner")
  why     — why was a capture, diff or commit wrong

`object_query` is the person's own words for the thing ("my keys", "the thing I cut paper with"), with
no article: "keys", "thing I cut paper with". Never invent an object id. `zone` only when they name a
place in the room (desk, shelf, bin). `when` only for a MOMENT in time, in the person's words. `ref`
only for `why`, when they name a commit.

Set `understood: false` when it is not a request to this robot about this room (a joke, a question about
the weather, code, anything you would have to guess at), and say why in one short clause. Guessing is
worse than refusing: a wrong guess moves a real robot."""

# OpenAI's strict structured outputs support a narrow subset of JSON Schema (no minLength / maxLength /
# pattern, and every property must be required), so the model's schema is DERIVED from ours here and the
# answer is validated against the real one afterwards. Ours stays the authority.
FIELDS = ("intent", "object_query", "object_id", "zone", "when", "ref")


def model_schema() -> dict:
    ours = intents.schema()["properties"]
    return {
        "type": "object", "additionalProperties": False,
        "required": ["understood", "reason", "confidence", *FIELDS],
        "properties": {
            "understood": {"type": "boolean", "description": "false when this is not a request to the robot"},
            "reason": {"type": ["string", "null"], "description": "why, in one clause, when understood is false"},
            "confidence": {"type": "number", "description": "0 to 1: how sure the reading is"},
            "intent": {"type": ["string", "null"], "enum": [*ours["intent"]["enum"], None]},
            "object_query": {"type": ["string", "null"], "description": "the person's words for the thing"},
            "object_id": {"type": ["string", "null"], "description": "only if they said an id like mug_a1b2"},
            "zone": {"type": ["string", "null"], "description": "a named place in the room"},
            "when": {"type": ["string", "null"], "description": "a moment, in their words"},
            "ref": {"type": ["string", "null"], "description": "a commit, only for why"},
        },
    }


class Refused(Exception):
    pass


def ask_openai(text: str, model: str, key: str, timeout: float) -> dict:
    """The one LLM call. Wrapped as a gen_ai.chat span with the model and its token usage, so the
    sentence a person typed and the tokens it cost sit in ONE waterfall with the Elastic search and
    the robot's motion. sdk_visible=False: this is raw HTTP, so sentry_sdk's OpenAI integration cannot
    see it and would not double-count it (docs/10 D31)."""
    span_cm = obs.agent_turn(text, model=model, sdk_visible=False) if obs else None
    with (span_cm if span_cm is not None else _Null()) as sp:
        answer = _post_openai(text, model, key, timeout)
        usage = answer.get("usage") or {}
        if sp is not None:
            sp.set_data("gen_ai.response.model", answer.get("model") or model)
            for ours, theirs in (("gen_ai.usage.input_tokens", "input_tokens"),
                                 ("gen_ai.usage.output_tokens", "output_tokens"),
                                 ("gen_ai.usage.total_tokens", "total_tokens")):
                if usage.get(theirs) is not None:
                    sp.set_data(ours, usage[theirs])
        fields = _fields_of(answer)
        if sp is not None:
            sp.set_data("gen_ai.response.text", json.dumps(fields)[:500])
        return fields


class _Null:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def _post_openai(text: str, model: str, key: str, timeout: float) -> dict:
    body = {
        "model": model,
        "input": [{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": text}],
        "text": {"format": {"type": "json_schema", "name": "intent_fields",
                            "schema": model_schema(), "strict": True}},
    }
    req = urllib.request.Request(OPENAI_URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            answer = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"openai HTTP {e.code}: {(e.read() or b'')[:200].decode('utf-8', 'replace')}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"openai unreachable: {e}") from None
    return answer


def _fields_of(answer: dict) -> dict:
    for item in answer.get("output") or []:
        for part in item.get("content") or []:
            if part.get("type") == "output_text" and part.get("text"):
                return json.loads(part["text"])
    raise RuntimeError("openai returned no structured answer")


def intent_from(fields: dict, text: str, request_id: str) -> dict:
    """OUR Intent, assembled here from the model's fields and validated against our schema. The model
    does not get to set request_id or source, and anything it fills that our schema refuses is refused."""
    if not fields.get("understood") or not fields.get("intent"):
        raise Refused(fields.get("reason") or "not a request this robot can act on")
    out = {"request_id": request_id, "intent": fields["intent"],
           "object_query": fields.get("object_query") or None, "object_id": fields.get("object_id") or None,
           "zone": fields.get("zone") or None, "when": fields.get("when") or None,
           "raw_text": text.strip()[:500],
           "confidence": max(0.0, min(1.0, float(fields.get("confidence") or 0.0))),
           "source": "openai"}
    if fields.get("ref"):
        out["ref"] = fields["ref"]
    return intents.validate(out)              # our schema, not the model's: the last word


def handler(model: str, key: str, token: str | None, timeout: float):
    class H(BaseHTTPRequestHandler):
        server_version = "gitspace-intent/1"

        def do_GET(self):  # noqa: N802
            if self.path.split("?")[0] != "/health":
                return self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            self._send(HTTPStatus.OK, {"ok": True, "service": "intent", "model": model})

        def do_POST(self):  # noqa: N802
            if token and self.headers.get("Authorization") != f"Bearer {token}":
                return self._send(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            if self.path.split("?")[0] != "/v1/intent":
                return self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 < n <= MAX_BODY:
                    raise ValueError("body size")
                doc = json.loads(self.rfile.read(n))
                text, rid = doc["text"], doc["request_id"]
                if not isinstance(text, str) or not text.strip() or not isinstance(rid, str) or not rid:
                    raise ValueError("text and request_id are required")
            except (ValueError, KeyError, TypeError) as e:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": str(e)})
            parent = {k: v for k, v in (("sentry-trace", self.headers.get("sentry-trace")),
                                        ("baggage", self.headers.get("baggage"))) if v}
            tx = obs.transaction("intent.understand", f"intent {model}", parent=parent) if obs else _Null()
            try:
                with tx:
                    fields = ask_openai(text, model, key, timeout)
            except RuntimeError as e:
                return self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "upstream", "detail": str(e),
                                                                   "retryable": True})
            try:
                out = intent_from(fields, text, rid)
            except Refused as e:
                return self._send(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "refused", "detail": str(e)})
            except intents.IntentError as e:
                return self._send(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": e.code, "detail": e.message})
            self._send(HTTPStatus.OK, out)

        def _send(self, status, doc):
            raw = json.dumps(doc).encode()
            self.send_response(status.value if hasattr(status, "value") else status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            return
    return H


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--model", default=os.getenv("INTENT_MODEL", "gpt-5-mini"))
    ap.add_argument("--timeout", type=float, default=float(os.getenv("INTENT_TIMEOUT_S", "25")))
    a = ap.parse_args()
    if obs is not None:
        obs.init("intent")                     # its own role: the understanding layer is its own process
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise SystemExit("OPENAI_API_KEY is not set (it may be parked in .env as OPENAI_API_KEY_PARKED)")
    if a.host not in ("127.0.0.1", "localhost", "::1") and not os.getenv("INTENT_TOKEN"):
        raise SystemExit("INTENT_TOKEN is required to listen off-host: this spends OpenAI credit per request")
    srv = ThreadingHTTPServer((a.host, a.port), handler(a.model, key, os.getenv("INTENT_TOKEN"), a.timeout))
    print(f"intent service: http://{a.host}:{a.port} · model {a.model} · POST /v1/intent · GET /health", flush=True)
    print(f"point the bridge at it:  INTENT_URL=http://{a.host}:{a.port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
