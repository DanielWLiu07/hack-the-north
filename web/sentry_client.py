"""sentry_client.py — a small client for Sentry's web API, for the telemetry board.

Two reads, both keyed by what obs.py already writes on every span and document:

    await client.trace_summary(trace_id)        the trace as a stage waterfall: stage -> duration ms
    await client.issues_for_capture("cap_0912") recent issues tagged capture_id:<id> (obs.capture_scope
                                                tags every event; obs.robot_failure raises the issue)

and ONE action, fired only by a person pressing [ask Seer] (docs/26-seer-embodied.md):

    await client.ask_seer("cap_0912")           find the capture's issue -> start Seer's autofix run
                                                -> poll it -> {state: "verdict" | "stumped", reason, …}

ask_seer NEVER raises and NEVER fakes a verdict: parked keys, no issue, a 404 endpoint, a missing
token scope, a timeout — each is a first-class `stumped` outcome whose `reason` the panel prints
verbatim. The autofix endpoint is UNVERIFIED (`SEER_VERIFIED = False`): Sentry was parked while
this was written, and "endpoints and payloads have moved between versions". tools/verify_seer_autofix.py
is the one read-only probe to run when the pause lifts, before flipping that flag.

Rules
  * Reads are GET. The only write is the POST that starts a Seer run, and it is never retried
    (a retry could start — and bill — a second run).
  * PAUSE: keys get parked in ../.env before the demo (`SENTRY_DSN=  # PAUSED until 01:00` beside
    `SENTRY_DSN_PARKED=<the dsn>`). While the DSN or the token is parked this client makes NO
    network call at all — `state()` says so, and both reads raise SentryError("sentry_paused").
  * Timeouts on every call; ONE retry on 429 / 5xx (honouring Retry-After, capped); errors come out
    in the docs/16-api.md §2.7 shape (code / detail / status / retryable) like server.py's ApiError.
  * Nothing here is invented: a span with no timestamps is skipped, an empty trace is an empty list.

UNVERIFIED AGAINST sentry.io: written while every external API was paused, and tested only with
mocked HTTP (the response shapes below are Sentry's documented ones). First thing to do when the
pause lifts: run one trace through it and compare with the waterfall in the Sentry UI.

Endpoints used (api/0):
  GET organizations/{org}/events-trace/{trace_id}/          -> the trace's transactions (a tree)
  GET projects/{org}/{project_slug}/events/{event_id}/      -> one transaction; entries[type=spans]
  GET projects/{org}/{project}/issues/?query=capture_id:…   -> issues carrying the tag
  POST issues/{issue_id}/autofix/   GET issues/{issue_id}/autofix/     -> Seer (UNVERIFIED shape)
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Mapping

import httpx

API = "https://sentry.io/api/0"
PROJECT = "gitspace"
TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
CAPTURE_ID = re.compile(r"^[a-z]+_[0-9]+$")
# the pipeline's stages, in the order they run (docs/18-sentry.md §6); anything else sorts after, by start
STAGE_ORDER = ("capture", "depth", "sgbm", "fuse", "segment", "describe", "merge", "associate", "es", "commit", "git")
SEER_VERIFIED = False         # flip only after tools/verify_seer_autofix.py has run against the live API
ISSUE_ID = re.compile(r"^[0-9]{1,20}$")
DONE = {"COMPLETED", "ERROR", "CANCELLED", "NEED_MORE_INFORMATION", "FAILED"}
MAX_TRANSACTIONS = 4          # a capture is one or two transactions (laptop + Pi); never crawl a huge trace
RETRY_CAP_S = 2.0


class SentryError(Exception):
    """docs/16-api.md §2.7: one error shape everywhere."""

    def __init__(self, code: str, detail: str, status: int = 502, retryable: bool = False):
        super().__init__(detail)
        self.code, self.detail, self.status, self.retryable = code, detail, status, retryable


def usable(value: str | None) -> str:
    """Same rule as server.py: python-dotenv reads `KEY=# comment` as the VALUE "# comment". A secret
    never contains whitespace or starts with "#"."""
    v = (value or "").strip()
    return "" if (not v or v.startswith("#") or any(c.isspace() for c in v)) else v


def parked(env: Mapping[str, str], name: str) -> bool:
    """`name` is parked when it holds a comment / nothing while `<name>_PARKED` holds the real value —
    or when its value is plainly a parking note ("# PAUSED until 01:00")."""
    raw = (env.get(name) or "").strip()
    if usable(raw):
        return False
    return bool(usable(env.get(f"{name}_PARKED"))) or raw.startswith("#")


class SentryClient:
    def __init__(self, env: Mapping[str, str] | None = None, *, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 8.0, sleep=asyncio.sleep):
        self._env = os.environ if env is None else env
        self._transport, self._timeout, self._sleep = transport, timeout, sleep
        self.calls = 0                                   # network calls actually made (tests assert 0 while paused)

    # ── state: may we call, and if not, why ────────────────────────────────────────────────────
    def state(self) -> dict:
        env = self._env
        paused_by = [n for n in ("SENTRY_DSN", "SENTRY_AUTH_TOKEN") if parked(env, n)]
        until = None
        for n in paused_by:
            m = re.search(r"until\s+(\d{1,2}:\d{2})", env.get(n) or "", re.I)
            until = until or (m and m.group(1))
        org, token = usable(env.get("SENTRY_ORG_SLUG")), usable(env.get("SENTRY_AUTH_TOKEN"))
        org_ok = bool(re.fullmatch(r"[a-z0-9-]+", org))
        if paused_by:
            reason = f"Sentry is paused{f' until {until}' if until else ''} ({', '.join(paused_by)} parked in .env)"
        elif not (token and org_ok):
            reason = "SENTRY_AUTH_TOKEN / SENTRY_ORG_SLUG are not set"
        else:
            reason = None
        return {"paused": bool(paused_by), "until": until, "configured": bool(token and org_ok and not paused_by),
                "reason": reason, "org": org if org_ok else None, "project": PROJECT}

    def _guard(self) -> tuple[str, str]:
        st = self.state()
        if st["paused"]:
            raise SentryError("sentry_paused", st["reason"], status=503, retryable=True)
        if not st["configured"]:
            raise SentryError("sentry_unconfigured", st["reason"], status=503)
        return st["org"], usable(self._env.get("SENTRY_AUTH_TOKEN"))

    # ── one GET, with a timeout, one retry, and the §2.7 mapping ────────────────────────────────
    async def _get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> Any:
        org_token = self._guard()                        # raises BEFORE any network object exists
        attempts = (0, 1) if method == "GET" else (1,)   # a POST starts a billed Seer run: never retried
        headers = {"Authorization": f"Bearer {org_token[1]}", "Accept": "application/json"}
        last: httpx.Response | None = None
        async with httpx.AsyncClient(base_url=API, headers=headers, transport=self._transport,
                                     timeout=httpx.Timeout(self._timeout, connect=4.0)) as http:
            for attempt in attempts:
                try:
                    self.calls += 1
                    last = await http.request(method, path, params=params, json=body)
                except httpx.TimeoutException:
                    if attempt == 0:
                        continue
                    raise SentryError("sentry_timeout", "Sentry did not answer in time", 504, True) from None
                except httpx.HTTPError as e:
                    if attempt == 0:
                        continue
                    raise SentryError("sentry_unreachable", f"could not reach Sentry ({type(e).__name__})", 502, True) from None
                if last.status_code == 429 or last.status_code >= 500:
                    if attempt == 0:
                        try:
                            wait = float(last.headers.get("Retry-After", "0.5"))
                        except ValueError:
                            wait = 0.5
                        await self._sleep(min(max(wait, 0.0), RETRY_CAP_S))
                        continue
                break
        assert last is not None
        if last.status_code == 401:
            raise SentryError("sentry_auth", "Sentry rejected the auth token", 502)
        if last.status_code == 403:
            try:
                said = str(last.json().get("detail") or "")[:200]
            except ValueError:
                said = ""
            raise SentryError("sentry_forbidden", said or "Sentry answered 403 without saying why", 502)
        if last.status_code == 404:
            raise SentryError("not_found", "Sentry has nothing at that id", 404)
        if last.status_code == 429:
            raise SentryError("sentry_rate_limited", "Sentry is rate limiting this token", 503, True)
        if last.status_code >= 400:
            raise SentryError("sentry_error", f"Sentry answered HTTP {last.status_code}", 502, last.status_code >= 500)
        try:
            return last.json()
        except ValueError:
            raise SentryError("sentry_error", "Sentry answered something that is not JSON", 502) from None

    # ── reads ──────────────────────────────────────────────────────────────────────────────
    async def trace_summary(self, trace_id: str) -> dict:
        """The trace as a waterfall: [{stage, ms, start_ms, spans}] ordered as the pipeline runs."""
        if not TRACE_ID.match(trace_id or ""):
            raise SentryError("bad_request", "trace_id must be 32 hex characters", 422)
        org, _ = self._guard()
        tree = await self._get(f"/organizations/{org}/events-trace/{trace_id}/", {"statsPeriod": "14d"})
        roots = tree.get("transactions", []) if isinstance(tree, dict) else (tree or [])
        flat: list[dict] = []
        stack = list(roots)
        while stack and len(flat) < MAX_TRANSACTIONS:
            tx = stack.pop(0)
            flat.append(tx)
            stack.extend(tx.get("children") or [])
        spans: list[dict] = []
        for tx in flat:
            event_id, project = tx.get("event_id"), tx.get("project_slug") or PROJECT
            if not event_id:
                continue
            event = await self._get(f"/projects/{org}/{project}/events/{event_id}/")
            for entry in event.get("entries") or []:
                if entry.get("type") == "spans":
                    spans += [s for s in entry.get("data") or [] if isinstance(s, dict)]
        return {"trace_id": trace_id, "url": f"https://{org}.sentry.io/performance/trace/{trace_id}/",
                "transactions": len(flat), **summarise(spans)}

    async def issues_for_capture(self, capture_id: str, limit: int = 5) -> list[dict]:
        if not CAPTURE_ID.match(capture_id or ""):
            raise SentryError("bad_request", "capture_id must look like cap_0912", 422)
        org, _ = self._guard()
        found = await self._get(f"/projects/{org}/{PROJECT}/issues/",
                                {"query": f"capture_id:{capture_id}", "statsPeriod": "14d", "limit": max(1, min(limit, 25))})
        out = []
        for i in found if isinstance(found, list) else []:
            meta = i.get("metadata") or {}
            out.append({"id": i.get("id"), "short_id": i.get("shortId"), "title": i.get("title") or meta.get("title"),
                        "level": i.get("level"), "count": i.get("count"), "last_seen": i.get("lastSeen"),
                        "permalink": i.get("permalink"), "culprit": i.get("culprit")})
        return out


    # ── [ask Seer] — docs/26-seer-embodied.md. UNVERIFIED against the live API. ─────────────────
    def seer_status(self) -> dict:
        """Why the button will or will not work, BEFORE it is pressed. No network call."""
        st = self.state()
        return {"available": st["configured"], "reason": st["reason"] if not st["configured"] else
                ("the autofix endpoint has not been verified against the live API yet" if not SEER_VERIFIED else None),
                "verified": SEER_VERIFIED, "paused": st["paused"], "until": st["until"],
                # Sentry's API for the Seer budget is not known to us: say so rather than print a number
                "credits": None, "credits_reason": ("unknown — Sentry paused" + (f" until {st['until']}" if st["until"] else ""))
                if st["paused"] else "unknown — this client has no verified endpoint for the Seer budget"}

    async def ask_seer(self, capture_id: str, *, context_depth: int = 40, poll_s: float = 2.0, max_wait_s: float = 45.0) -> dict:
        """Find the capture's Sentry issue, start Seer on it, poll, return the outcome. Never raises,
        never invents: anything short of a readable answer is `stumped` with the reason."""
        out = {"capture_id": capture_id, "verified": SEER_VERIFIED, "issue": None, "run": None, "verdict": None}
        stumped = lambda reason, **more: {**out, **more, "state": "stumped", "reason": reason}   # noqa: E731
        if not CAPTURE_ID.match(capture_id or ""):
            return stumped("that is not a capture id")
        st = self.state()
        if st["paused"]:
            return stumped("Sentry is paused" + (f" until {st['until']}" if st["until"] else "") + " to save quota")
        if not st["configured"]:
            return stumped(st["reason"])
        try:
            issues = await self.issues_for_capture(capture_id, limit=1)
        except SentryError as e:
            return stumped(f"could not look the capture up in Sentry — {e.code}: {e.detail}")
        if not issues:
            return stumped("no Sentry issue is tagged with this capture")
        issue = issues[0]
        out["issue"] = {k: issue.get(k) for k in ("id", "short_id", "title", "permalink")}
        if not ISSUE_ID.match(str(issue.get("id") or "")):
            return stumped("Sentry returned an issue without a usable id", issue=out["issue"])
        path = f"/issues/{issue['id']}/autofix/"
        try:
            started = await self._request("POST", path, body={"instruction": (
                f"A physical robot capture was rejected or an operation failed (capture_id {capture_id}). "
                f"The last {int(context_depth)} telemetry breadcrumbs (tilt_rate, pitch, odom_residual) are attached "
                "to the issue. Explain the physical cause.")})
        except SentryError as e:
            return stumped(_why_stumped(e, "start"), issue=out["issue"])
        out["run"] = {"run_id": _dig(started, "run_id"), "status": None}
        waited = 0.0
        while True:
            try:
                polled = await self._get(path)
            except SentryError as e:
                return stumped(_why_stumped(e, "poll"), issue=out["issue"], run=out["run"])
            auto = polled.get("autofix") if isinstance(polled, dict) and isinstance(polled.get("autofix"), dict) else polled
            status = str((auto or {}).get("status") or "").upper() if isinstance(auto, dict) else ""
            out["run"] = {"run_id": _dig(auto, "run_id") or out["run"]["run_id"], "status": status or None}
            if status in DONE or not status:
                text = _verdict_text(auto)
                if status == "COMPLETED" and text:
                    return {**out, "state": "verdict", "reason": None, "verdict": text}
                keys = sorted(auto.keys()) if isinstance(auto, dict) else []
                return stumped(f"Seer finished with status {status or 'unknown'} and nothing readable "
                               f"(response keys: {', '.join(keys) or 'none'})", issue=out["issue"], run=out["run"])
            if waited >= max_wait_s:
                return stumped(f"Seer was still {status.lower()} after {int(max_wait_s)} s — gave up waiting",
                               issue=out["issue"], run=out["run"])
            await self._sleep(poll_s)
            waited += poll_s


def _why_stumped(e: SentryError, step: str) -> str:
    if e.code == "not_found":
        return "this Sentry plan or version has no autofix endpoint (HTTP 404)"
    if e.code == "sentry_forbidden":
        scopes = ", ".join(sorted(set(re.findall(r"\b[a-z]+:[a-z]+\b", e.detail)))) or None
        return (f"the token lacks a scope the autofix endpoint needs: {scopes}" if scopes
                else f"the token lacks a scope the autofix endpoint needs — Sentry said: {e.detail}")
    if e.code == "sentry_timeout":
        return f"Sentry did not answer in time ({step})"
    if e.code == "sentry_paused":
        return e.detail
    return f"Sentry could not {step} the run — {e.code}: {e.detail}"


def _dig(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _dig(v, key)
            if found is not None:
                return found
    return None


def _verdict_text(auto: Any) -> str | None:
    """The readable part of a finished run. Shapes seen in Sentry's autofix responses: steps[] with a
    root-cause step (`causes[].title/description`) and a solution step; else a top-level summary.
    Returns None rather than guessing when none of them is there."""
    if not isinstance(auto, dict):
        return None
    parts: list[str] = []
    for step in auto.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for cause in step.get("causes") or []:
            if isinstance(cause, dict):
                bit = " — ".join(str(cause[k]).strip() for k in ("title", "description") if cause.get(k))
                if bit:
                    parts.append(bit)
        for k in ("description", "solution_summary"):
            if step.get("type") in ("solution", "root_cause_analysis") and isinstance(step.get(k), str) and step[k].strip():
                parts.append(step[k].strip())
    for k in ("root_cause", "summary", "result"):
        if isinstance(auto.get(k), str) and auto[k].strip():
            parts.append(auto[k].strip())
    seen: list[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return "\n\n".join(seen)[:4000] or None


def summarise(spans: list[dict]) -> dict:
    """Spans -> stages. A stage is a span's `op` (obs.span(op, …)), durations summed per stage, offsets
    from the trace's first span. A span missing either timestamp is skipped, not guessed."""
    timed = [s for s in spans if isinstance(s.get("start_timestamp"), (int, float)) and isinstance(s.get("timestamp"), (int, float))]
    if not timed:
        return {"stages": [], "total_ms": None, "spans": 0}
    t0 = min(s["start_timestamp"] for s in timed)
    stages: dict[str, dict] = {}
    for s in timed:
        name = str(s.get("op") or s.get("description") or "span")
        st = stages.setdefault(name, {"stage": name, "ms": 0.0, "start_ms": None, "spans": 0})
        st["ms"] += (s["timestamp"] - s["start_timestamp"]) * 1000
        st["spans"] += 1
        start = (s["start_timestamp"] - t0) * 1000
        st["start_ms"] = start if st["start_ms"] is None else min(st["start_ms"], start)

    def rank(st: dict) -> tuple:
        head = st["stage"].split(".")[0].lower()
        return (STAGE_ORDER.index(head) if head in STAGE_ORDER else len(STAGE_ORDER), st["start_ms"])
    ordered = sorted(stages.values(), key=rank)
    for st in ordered:
        st["ms"], st["start_ms"] = round(st["ms"], 1), round(st["start_ms"], 1)
    total = (max(s["timestamp"] for s in timed) - t0) * 1000
    return {"stages": ordered, "total_ms": round(total, 1), "spans": len(timed)}
