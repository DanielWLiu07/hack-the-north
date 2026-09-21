"""Elasticsearch SEARCH over the operational record, for /telemetry.

    GET /api/telemetry/search    BM25 over room-events.message, highlighted, with filters
    GET /api/telemetry/facets    the filter values that actually exist, with counts
    GET /api/telemetry/signals   one ES|QL STATS across every sample in the TSDS
    GET /api/telemetry/sparkline a date histogram of one signal, for an SVG line
    GET /api/telemetry/percentiles  p50/p90/p99 of one signal

The page reads telemetry but never searched it, so the largest index in the project had no
search story on screen. Two different Elastic strengths are on show: full text with highlighting
over 165 free-text event messages, and a STATS aggregation over ~6.9M samples that answers in
tens of milliseconds.

EVERY query here is PREPARED. There is no endpoint that accepts ES|QL, and none is planned: a
free-text query box is a liability with no demo payoff, and the site is reachable through a
tunnel so loopback alone would not be a control anyway. The caller chooses a signal name from a
fixed set and supplies text that only ever reaches Elasticsearch as a bound value.

`took` in every response is the cluster's own number, not a stopwatch around the HTTP call --
wall time would include the network and flatter or slander the cluster depending on the wifi.
`source` says elasticsearch or fixture, as store.py does, so a reader never has to guess.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter()
_es = None
_queries = None

# The signals the TSDS actually carries. A request naming anything else is refused rather than
# passed through: the ES|QL interval and signal name are the only places caller input could reach
# the query text, and neither is allowed to.
SIGNALS = ("tilt_rate", "pitch", "balanced", "motor_current_l", "motor_current_r",
           "left_enc", "right_enc", "odom_residual")
SPANS = ("1 minute", "5 minutes", "1 hour", "1 day")


def init(es) -> None:  # server.py's router protocol
    global _es
    _es = es


def _q():
    """elastic/queries.py, built once against the real client.

    web/'s `es` is the async HTTP wrapper; Queries wants the elasticsearch client, so this builds
    its own from the same environment rather than reaching into the wrapper's internals.
    """
    global _queries
    if _queries is None:
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        for p in (str(root), str(root / "elastic")):
            if p not in sys.path:
                sys.path.insert(0, p)
        import setup_elastic as S
        from queries import Queries
        _queries = Queries(S.connect())
    return _queries


def _fail(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}, "source": "elasticsearch"},
                        status_code=status)


@router.get("/api/telemetry/search")
async def search(q: str = Query("", max_length=200), event_type: str | None = None,
                 zone: str | None = None, branch: str | None = None,
                 capture_id: str | None = None, size: int = Query(12, ge=1, le=50)):
    """Full text over the event log. `q` is a bound value, never concatenated into a query."""
    try:
        r = await asyncio.to_thread(lambda: _q().search_events(
            q, event_type=event_type, zone=zone, branch=branch, capture_id=capture_id, size=size))
    except Exception as e:  # noqa: BLE001 -- a dead cluster is an answer, not a stack trace
        return _fail("search_failed", f"{type(e).__name__}: {str(e)[:200]}", 503)
    req = r.pop("request")
    body = req["body"]
    return {**r, "source": "elasticsearch", "index": req["index"],
            # the query on screen beside its results: a number with the query hidden could have
            # come from anywhere, which is the opposite of showing off a search engine
            "query_shown": {"kind": "search", "index": req["index"],
                            "query": body["query"], "highlight": body.get("highlight")}}


@router.get("/api/telemetry/facets")
async def facets():
    try:
        f = await asyncio.to_thread(lambda: _q().event_facets())
    except Exception as e:  # noqa: BLE001
        return _fail("facets_failed", f"{type(e).__name__}: {str(e)[:200]}", 503)
    return {"facets": f, "source": "elasticsearch"}


@router.get("/api/telemetry/signals")
async def signals():
    """One STATS over every sample in the TSDS. This is the number worth showing."""
    try:
        s = await asyncio.to_thread(lambda: _q().telemetry_signals())
    except Exception as e:  # noqa: BLE001
        return _fail("signals_failed", f"{type(e).__name__}: {str(e)[:200]}", 503)
    total = sum(x["samples"] for x in s["signals"]) if s["signals"] else 0
    return {**s, "total_samples": total, "source": "elasticsearch",
            "query_shown": {"kind": "esql", "query": s["query"]}}


@router.get("/api/telemetry/sparkline")
async def sparkline(signal: str = Query(...), buckets: int = Query(48, ge=2, le=200),
                    span: str = Query("1 hour")):
    if signal not in SIGNALS:
        return _fail("unknown_signal", f"signal must be one of: {', '.join(SIGNALS)}")
    if span not in SPANS:
        return _fail("unknown_span", f"span must be one of: {', '.join(SPANS)}")
    try:
        s = await asyncio.to_thread(lambda: _q().telemetry_sparkline(signal, buckets=buckets, span=span))
    except Exception as e:  # noqa: BLE001
        return _fail("sparkline_failed", f"{type(e).__name__}: {str(e)[:200]}", 503)
    return {**s, "source": "elasticsearch", "query_shown": {"kind": "esql", "query": s["query"]}}


@router.get("/api/telemetry/percentiles")
async def percentiles(signal: str = Query(...)):
    """The slowest query on the page by an order of magnitude (~110 ms for one signal against
    ~30 ms for everything else), which is why it takes one signal at a time and is asked for
    last."""
    if signal not in SIGNALS:
        return _fail("unknown_signal", f"signal must be one of: {', '.join(SIGNALS)}")
    try:
        p = await asyncio.to_thread(lambda: _q().telemetry_percentiles(signal))
    except Exception as e:  # noqa: BLE001
        return _fail("percentiles_failed", f"{type(e).__name__}: {str(e)[:200]}", 503)
    return {**p, "source": "elasticsearch", "query_shown": {"kind": "esql", "query": p["query"]}}
