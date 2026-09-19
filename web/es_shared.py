"""One search, one implementation (docs/10-open-questions.md GAP 3).

The hybrid retriever is the centrepiece of the Elastic submission, and it lives in
`elastic/queries.py`, where every query has a live-cluster test. web does NOT rebuild it: this
module hands that same `Queries` class the official client (behind a proxy that logs, times,
traces and maps errors the way the rest of web does) and dash_api.py shapes what comes back.
Anything web needs from Elasticsearch that `Queries` cannot answer is a request to elastic/,
not a second query here.

`Queries` is synchronous; call it with `asyncio.to_thread`.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("gitspace.web")

ELASTIC_DIR = Path(__file__).resolve().parent.parent / "elastic"
if str(ELASTIC_DIR) not in sys.path:
    sys.path.insert(0, str(ELASTIC_DIR))

try:                                   # needs the `elasticsearch` package: run web from the repo-root .venv
    import queries as shared           # elastic/queries.py
    import elasticsearch
    IMPORT_ERROR: str | None = None
except ImportError as e:               # web still serves everything that does not search
    shared = elasticsearch = None      # type: ignore[assignment]
    IMPORT_ERROR = f"{type(e).__name__}: {e}"

_server: Any = None                    # the module that owns ApiError and the sanitised ES settings
_queries: Any = None
_lock = threading.Lock()


def init(es: Any) -> None:
    """`es` is web's own Elastic client; its module (server, or __main__ when run as a script)
    carries ApiError, obs, and the URL / key AFTER parked values have been discarded."""
    global _server, _queries
    _server, _queries = sys.modules[type(es).__module__], None


class _Esql:
    def __init__(self, proxy: "_Logged"):
        self._p = proxy

    def query(self, **kw: Any) -> Any:
        return self._p._call("esql", self._p._es.esql.query, kw)


class _Logged:
    """The official client, with web's house rules applied to every call: one log line saying
    exactly what the demo ran, an obs span, and the §2.7 error shape. No translation of the
    request: what `Queries` sends is what Elasticsearch gets."""

    def __init__(self, es: Any):
        self._es = es
        self.esql = _Esql(self)

    def search(self, **kw: Any) -> Any:
        return self._call("search", self._es.search, kw)

    def msearch(self, **kw: Any) -> Any:
        return self._call("msearch", self._es.msearch, kw)

    def _call(self, label: str, fn: Any, kw: dict) -> Any:
        S, t0 = _server, time.perf_counter()
        index = kw.get("index", "")
        try:
            with S.obs.span("es.query", f"shared.{label}", index=str(index)):
                r = fn(**kw)
        except elasticsearch.ConnectionTimeout:
            raise S.ApiError("elastic_timeout", "Elasticsearch did not answer in time", status=504, retryable=True) from None
        except elasticsearch.ConnectionError as e:
            raise S.ApiError("elastic_unreachable", f"could not reach Elasticsearch ({type(e).__name__})",
                             status=502, retryable=True) from None
        except (elasticsearch.AuthenticationException, elasticsearch.AuthorizationException):
            raise S.ApiError("elastic_auth", "Elasticsearch rejected the API key", status=502) from None
        except elasticsearch.ApiError as e:
            reason = ""
            try:
                root = ((e.body or {}).get("error", {}).get("root_cause") or [{}])[0]
                reason = f"{root.get('type', 'error')}: {root.get('reason', '')}"
            except AttributeError:
                reason = str(e)[:200]
            raise S.ApiError("elastic_error", reason.strip(": "), status=502,
                             retryable=e.status_code in (429, 502, 503, 504)) from None
        body = {k: v for k, v in kw.items() if k != "index"}
        log.info("es shared.%s %s -> ok in %.0f ms  %s", label, index, (time.perf_counter() - t0) * 1000,
                 json.dumps(body, separators=(",", ":"), default=str)[:1500])
        return r


def queries() -> Any:
    """The shared `Queries`, bound to the live cluster — or the reason there is not one.
    Never constructs a client, and so never makes a call, while the key is parked or missing."""
    global _queries
    S = _server
    if S is None:
        raise RuntimeError("es_shared.init() was not called")
    if not (S.ELASTIC_URL and S.ELASTIC_API_KEY):
        if S.ELASTIC_PAUSED:
            raise S.ApiError("elastic_paused", "Elasticsearch is parked in ../.env (ELASTIC_API_KEY_PARKED) "
                             "to save quota; no calls are being made", status=503)
        raise S.ApiError("elastic_unconfigured", "ELASTIC_URL / ELASTIC_API_KEY are not set in ../.env", status=503)
    if IMPORT_ERROR:
        raise S.ApiError("search_unavailable", "elastic/queries.py could not be imported "
                         f"({IMPORT_ERROR}); run web with the repo-root .venv", status=503)
    with _lock:
        if _queries is None:
            client = elasticsearch.Elasticsearch(S.ELASTIC_URL, api_key=S.ELASTIC_API_KEY, request_timeout=10)
            _queries = shared.Queries(_Logged(client))
    return _queries


def lexical_fields(q: Any) -> list[str]:
    """Which fields the shared BM25 leg reads, read off the shared query itself (never restated here)."""
    for clause in q._lexical("x")["bool"]["should"]:
        if "multi_match" in clause:
            return list(clause["multi_match"]["fields"])
    return []


def semantic_scores(q: Any, text: str, commit_sha: str | None = None, size: int = 50) -> list[tuple[str, float]]:
    """(object_id, best score) of the SEMANTIC leg alone, best first: `Queries.semantic_only`, the
    vector twin of `lexical_only`. elastic/tests/test_query_shapes.py asserts both probes send exactly
    the clause of the matching leg in `search_objects`, so provenance cannot drift from the fused search.
    The CUT that turns scores into "the vector leg found it" is dash_api's (it is calibrated on scores)."""
    return [(oid, float(score)) for oid, score in q.semantic_only(text, commit_sha, size)]
