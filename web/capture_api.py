"""capture_api.py — /capture/<capture_id>: "why was this diff wrong?" on one screen.

    GET /api/captures?limit=50        commit-path captures, newest first
    GET /api/capture/{capture_id}     everything the page draws, in one response
    GET /capture                      -> the newest capture
    GET /capture/{capture_id}         the page (pages/capture.html)
    GET /pages/{file}                 the page's own js / css

server.py mounts it:  capture_api.init(es); app.include_router(capture_api.router)
The data rules live in store.py: Elasticsearch first, fixture file as a labelled
fallback, and a field that was not recorded is null — never a made-up number.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import store

try:                      # repo-root obs.py; optional, the page works without Sentry
    import obs
except ImportError:       # pragma: no cover
    obs = None

PAGES = Path(__file__).resolve().parent / "pages"
CAPTURE_ID = re.compile(r"^[a-z]+_[0-9]+$")          # cap_0912, watch_00041
ASSET = re.compile(r"^[a-z0-9_-]+\.(js|css)$")

router = APIRouter()


def init(es) -> None:
    store.init(es)


def _error(code: str, detail: str, status: int, retryable: bool = False) -> JSONResponse:
    """docs/16-api.md §2.7 — one error shape everywhere."""
    return JSONResponse({"error": code, "detail": detail, "retryable": retryable}, status_code=status)


def _upstream(e: Exception) -> JSONResponse:
    # server.py's ApiError (code / detail / status / retryable), without importing server.py
    return _error(getattr(e, "code", "internal_error"), str(getattr(e, "detail", e)),
                  int(getattr(e, "status", 500)), bool(getattr(e, "retryable", False)))


@router.get("/api/captures")
async def list_captures(limit: int = Query(50, ge=1, le=500)):
    try:
        return await store.captures(limit)
    except Exception as e:  # noqa: BLE001
        return _upstream(e)


@router.get("/api/capture/{capture_id}")
async def get_capture(capture_id: str):
    if not CAPTURE_ID.match(capture_id):
        return _error("bad_request", "capture_id must look like cap_0912", 422)
    try:
        if obs is not None:
            with obs.capture_scope(capture_id), obs.span("capture.load", capture_id, capture_id=capture_id):
                payload = await store.capture(capture_id)
        else:
            payload = await store.capture(capture_id)
        return payload
    except store.NotFound:
        return _error("not_found", f"no capture {capture_id}", 404)
    except Exception as e:  # noqa: BLE001
        return _upstream(e)


@router.get("/capture", include_in_schema=False)
async def latest_capture():
    try:
        found = (await store.captures(1))["captures"]
    except Exception as e:  # noqa: BLE001
        return _upstream(e)
    if not found:
        return _error("not_found", "no captures recorded yet", 404)
    return RedirectResponse(f"/capture/{found[0]['capture_id']}", status_code=307)


@router.get("/capture/{capture_id}", include_in_schema=False)
async def capture_page(capture_id: str):
    if not CAPTURE_ID.match(capture_id):
        return _error("not_found", "no such page", 404)
    return FileResponse(PAGES / "capture.html", headers={"Cache-Control": "no-cache"})


@router.get("/pages/{name}", include_in_schema=False)
async def page_asset(name: str):
    path = PAGES / name
    if not ASSET.match(name) or not path.is_file():
        return _error("not_found", "Not Found", 404)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})
