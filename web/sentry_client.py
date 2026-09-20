"""sentry_client.py — a small client for Sentry's web API, for the telemetry board.

Two reads, both keyed by what obs.py already writes on every span and document:

    await client.trace_summary(trace_id)        the trace as a stage waterfall: stage -> duration ms
    await client.issues_for_capture("cap_0912") recent issues tagged capture_id:<id> (obs.capture_scope
                                                tags every event; obs.robot_failure raises the issue)

and ONE action, fired only by a person pressing [ask Seer] (docs/26-seer-embodied.md):

    await client.ask_seer("cap_0912")           find the capture's issue -> start Seer's autofix run
                                                -> poll it -> {state: "verdict" | "stumped", reason, …}

ask_seer NEVER raises and NEVER fakes a verdict: parked keys, no issue, a 404 endpoint, a missing
token scope, Seer not set up, a timeout — each is a first-class `stumped` outcome whose `reason`
the panel prints verbatim.

VERIFIED LIVE 2026-09-19 (tools/verify_seer_autofix.py, read-only):
  * the path docs/26 assumed, `issues/{id}/autofix/`, is GONE — HTTP 404 with an empty body;
  * the ORG-SCOPED path answers: GET organizations/{org}/issues/{id}/autofix/ -> 200 {"autofix": null}
    for an issue nobody has run Seer on;
  * GET …/autofix/setup/ -> 200 {integration{ok,reason}, seerReposLinked, autofixEnabled,
    billing{hasAutofixQuota}, setupAcknowledgement{…}} — what ask_seer reads BEFORE it spends a run.
VERIFIED LIVE 2026-09-20 against a COMPLETED run (16890654, GITSPACE-1J), read by scripts/seer_sweep.py:
  * a finished run has NO `steps` key. It is a conversation: `blocks[]`, each `{id, timestamp, message:
    {role, content, thinking_content, tool_calls}, tool_links, tool_results, file_patches, todos}`;
  * the root cause is the CLOSING ASSISTANT TURN's `content` — markdown, naming the file, the commit that
    introduced the bug, and (for 1J) the later commit that had already fixed it;
  * `status` comes back lower-case ("completed"); every comparison here upper-cases it first.
  `_verdict_text` reads the conversation first and keeps the steps[] reader for older runs.
NOT verified: the POST that starts a run from THIS client (the runs read above were started by hand).
`SEER_VERIFIED` stays False until one run has been started from here and read back.

Rules
  * Reads are GET. The only write is the POST that starts a Seer run, and it is never retried
    (a retry could start — and bill — a second run).
  * PAUSE: keys get parked in ../.env before the demo (`SENTRY_DSN=  # PAUSED until 01:00` beside
    `SENTRY_DSN_PARKED=<the dsn>`). While the DSN or the token is parked this client makes NO
    network call at all — `state()` says so, and both reads raise SentryError("sentry_paused").
  * Timeouts on every call; ONE retry on 429 / 5xx (honouring Retry-After, capped); errors come out
    in the docs/16-api.md §2.7 shape (code / detail / status / retryable) like server.py's ApiError.
  * Nothing here is invented: a span with no timestamps is skipped, an empty trace is an empty list.

The two reads were written while every external API was paused and tested with mocked HTTP (the
response shapes below are Sentry's documented ones); compare one trace with the waterfall in the
Sentry UI before trusting the stage numbers.

Endpoints used (api/0):
  GET organizations/{org}/trace/{trace_id}/                 -> the trace's spans, a tree (verified live)
  GET organizations/{org}/events-trace/{trace_id}/          -> older shape; the fallback when the first is empty
  GET projects/{org}/{project_slug}/events/{event_id}/      -> one transaction; entries[type=spans]
  GET projects/{org}/{project}/issues/?query=capture_id:…   -> issues carrying the tag
  GET  organizations/{org}/issues/{issue_id}/autofix/setup/ -> is Seer set up (verified live)
  GET  organizations/{org}/issues/{issue_id}/autofix/       -> {"autofix": run | null} (verified live)
  POST organizations/{org}/issues/{issue_id}/autofix/       -> starts a run (NOT yet pressed live)
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
CAPTURE_IN = re.compile(r"\b([a-z]+_[0-9]+)\b")   # a capture id buried in an issue title
# the pipeline's stages, in the order they run (docs/18-sentry.md §6); anything else sorts after, by start
STAGE_ORDER = ("capture", "depth", "sgbm", "fuse", "segment", "describe", "merge", "associate", "es", "commit", "git")
SEER_VERIFIED = False         # True only once ONE real run has been started and read back (a press bills a run)
SEER_VERIFIED_DETAIL = ("read path verified live 2026-09-19 (org-scoped; the path in docs/26 answers 404); "
                        "the POST that starts a run has not been pressed yet")
ISSUE_ID = re.compile(r"^[0-9]{1,20}$")
# a run that has stopped moving. WAITING_FOR_USER_RESPONSE is where a root-cause-only run rests.
DONE = {"COMPLETED", "ERROR", "CANCELLED", "NEED_MORE_INFORMATION", "WAITING_FOR_USER_RESPONSE", "FAILED"}
ANSWERED = {"COMPLETED", "NEED_MORE_INFORMATION", "WAITING_FOR_USER_RESPONSE"}   # may carry a root cause
MAX_SPANS = 400               # a capture is a few dozen spans; never walk a runaway trace
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
    async def trace_spans(self, trace_id: str) -> list[dict]:
        """Every span of the trace, flat, oldest first, on the WALL CLOCK: [{op, description, start, end
        (epoch seconds), span_id, parent_span_id, depth, is_transaction, errors}]. One GET.
        Verified live 2026-09-19: organizations/{org}/trace/{id}/ answers a tree of spans; the older
        events-trace/ endpoint answers {"transactions": []} for the very same traces."""
        if not TRACE_ID.match(trace_id or ""):
            raise SentryError("bad_request", "trace_id must be 32 hex characters", 422)
        org, _ = self._guard()
        tree = await self._get(f"/organizations/{org}/trace/{trace_id}/", {"statsPeriod": "14d"})
        out: list[dict] = []
        stack = [(n, 0) for n in (tree if isinstance(tree, list) else [])]
        while stack and len(out) < MAX_SPANS:
            node, depth = stack.pop(0)
            if not isinstance(node, dict):
                continue
            start, end = node.get("start_timestamp"), node.get("end_timestamp", node.get("timestamp"))
            if isinstance(start, (int, float)) and isinstance(end, (int, float)):      # no timestamps = not drawn, not guessed
                out.append({"op": node.get("op") or None, "description": node.get("description") or node.get("name") or None,
                            "start": float(start), "end": float(end), "span_id": node.get("event_id") or node.get("span_id"),
                            "parent_span_id": node.get("parent_span_id"), "depth": depth,
                            "is_transaction": bool(node.get("is_transaction")), "errors": len(node.get("errors") or [])})
            stack = [(c, depth + 1) for c in (node.get("children") or [])] + stack
        return sorted(out, key=lambda x: (x["start"], x["depth"]))

    async def trace_summary(self, trace_id: str) -> dict:
        """The trace as a waterfall: [{stage, ms, start_ms, spans}] ordered as the pipeline runs."""
        org, _ = self._guard()
        try:
            found = await self.trace_spans(trace_id)
        except SentryError as e:
            if e.code != "not_found":                    # an install without the newer endpoint still has the older one
                raise
            found = []
        if found:                                        # leaves only: a parent's time is its children's, counted once
            parents = {x["parent_span_id"] for x in found if x["parent_span_id"]}
            leaves = [x for x in found if x["span_id"] not in parents] or found
            spans = [{"op": x["op"], "description": x["description"], "start_timestamp": x["start"], "timestamp": x["end"]} for x in leaves]
            return {"trace_id": trace_id, "url": f"https://{org}.sentry.io/performance/trace/{trace_id}/",
                    "transactions": sum(1 for x in found if x["is_transaction"]), **summarise(spans)}
        # nothing there: ask the older endpoint before saying the trace is empty
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
        return [snapshot_issue(i) for i in found if isinstance(i, dict)] if isinstance(found, list) else []

    async def recent_issues(self, limit: int = 25, stats_period: str = "24h",
                            state: str = "unresolved") -> list[dict]:
        """Issues on this project in one state, newest lastSeen first. One GET. Tags such as capture_id
        are not on the list payload — call issue_tags() for a new issue when you need the join.

        `state` is "unresolved" (the live panel) or "resolved" (what the room has already dealt with).
        It is looked up, never interpolated: a query string is a search language, and the only two
        searches this project makes are these two."""
        org, _ = self._guard()
        query = {"unresolved": "is:unresolved", "resolved": "is:resolved"}.get(state)
        if query is None:
            raise SentryError("bad_request", "state must be 'unresolved' or 'resolved'", 422)
        found = await self._get(f"/projects/{org}/{PROJECT}/issues/",
                                {"query": query, "statsPeriod": stats_period,
                                 "limit": max(1, min(int(limit), 50)), "sort": "date"})
        return [snapshot_issue(i) for i in found if isinstance(i, dict)] if isinstance(found, list) else []

    async def issue_tags(self, issue_id: str) -> dict:
        """Tags on the issue's latest event (capture_id, commit_sha, camera, role). Empty dict if
        Sentry has no event yet. One GET."""
        if not ISSUE_ID.match(str(issue_id or "")):
            raise SentryError("bad_request", "issue id must be digits", 422)
        org, _ = self._guard()
        try:
            ev = await self._get(f"/issues/{issue_id}/events/latest/")
        except SentryError as e:
            if e.code == "not_found":
                return {}
            raise
        tags = {t["key"]: t["value"] for t in (ev.get("tags") or [])
                if isinstance(ev, dict) and isinstance(t, dict) and t.get("key") and t.get("value") is not None}
        out = {k: tags[k] for k in ("capture_id", "commit_sha", "camera", "role") if k in tags}
        cap = out.get("capture_id")
        if cap and not CAPTURE_ID.match(str(cap)):
            out.pop("capture_id", None)
        return out


    # ── [ask Seer] — docs/26-seer-embodied.md ───────────────────────────────────────────────────
    def seer_status(self) -> dict:
        """Why the button will or will not work, BEFORE it is pressed. No network call."""
        st = self.state()
        return {"available": st["configured"], "reason": st["reason"] if not st["configured"] else
                (None if SEER_VERIFIED else SEER_VERIFIED_DETAIL),
                "verified": SEER_VERIFIED, "verified_detail": SEER_VERIFIED_DETAIL,
                "paused": st["paused"], "until": st["until"],
                # Sentry's API for the Seer budget is not known to us: say so rather than print a number
                "credits": None, "credits_reason": ("unknown — Sentry paused" + (f" until {st['until']}" if st["until"] else ""))
                if st["paused"] else "unknown — this client has no verified endpoint for the Seer budget"}

    async def seer_setup(self, issue_id: str) -> dict:
        """Is Seer able to run on this issue? One GET, no run started, nothing billed.
        `blocker` is the reason a run must NOT be started; `caveat` is what will limit a run that is."""
        if not ISSUE_ID.match(str(issue_id or "")):
            raise SentryError("bad_request", "issue id must be digits", 422)
        org, _ = self._guard()
        raw = await self._get(f"/organizations/{org}/issues/{issue_id}/autofix/setup/")
        raw = raw if isinstance(raw, dict) else {}
        integ = raw.get("integration") if isinstance(raw.get("integration"), dict) else {}
        quota = (raw.get("billing") or {}).get("hasAutofixQuota") if isinstance(raw.get("billing"), dict) else None
        enabled, repos = raw.get("autofixEnabled"), raw.get("seerReposLinked")
        blocker = ("Seer is switched off for this Sentry organisation" if enabled is False else
                   "the Sentry plan has no Seer quota left" if quota is False else None)
        caveats = []
        if integ.get("ok") is False:
            caveats.append(f"no code integration ({integ.get('reason') or 'unknown reason'})")
        if repos is False:
            caveats.append("no repository linked to Seer")
        return {"enabled": enabled, "quota": quota, "integration_ok": integ.get("ok"),
                "integration_reason": integ.get("reason"), "repos_linked": repos, "blocker": blocker,
                "caveat": ("Seer cannot read our code: " + " and ".join(caveats)) if caveats else None}

    async def ask_seer(self, capture_id: str, *, context_depth: int = 40, poll_s: float = 2.0, max_wait_s: float = 45.0,
                       may_start: bool = True) -> dict:
        """Find the capture's Sentry issue, start Seer on it, poll, return the outcome. Never raises,
        never invents: anything short of a readable answer is `stumped` with the reason."""
        out = {"capture_id": capture_id, "verified": SEER_VERIFIED, "issue": None, "setup": None, "run": None, "verdict": None}
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
        # the org-scoped path: `/issues/{id}/autofix/` (what docs/26 assumed) answers 404 on the live API
        path = f"/organizations/{st['org']}/issues/{issue['id']}/autofix/"
        try:                                             # a free GET first: never bill a run Seer cannot do
            out["setup"] = await self.seer_setup(issue["id"])
        except SentryError as e:
            return stumped(_why_stumped(e, "check the setup of"), issue=out["issue"])
        if out["setup"]["blocker"]:
            return stumped(out["setup"]["blocker"], issue=out["issue"], setup=out["setup"])
        try:                                             # a run somebody already paid for is read, not re-bought
            existing = _autofix(await self._get(path))
        except SentryError as e:
            return stumped(_why_stumped(e, "read"), issue=out["issue"], setup=out["setup"])
        if existing is not None and str(existing.get("status") or "").upper() in DONE - ANSWERED:
            existing = None                              # the last run died (ERROR / CANCELLED / FAILED): asking again is fair
        if existing is None and not may_start:           # a visitor on the public link may READ a run, never BUY one
            return stumped("Seer has not looked at this issue yet, and a run can only be started from the robot's own "
                           "laptop — each one is billed", issue=out["issue"], setup=out["setup"])
        if existing is None:
            try:
                started = await self._request("POST", path, body={"stopping_point": "root_cause", "instruction": (
                    f"A physical robot capture was rejected or an operation failed (capture_id {capture_id}). "
                    f"The last {int(context_depth)} telemetry breadcrumbs (tilt_rate, pitch, odom_residual) are attached "
                    "to the issue. Explain the physical cause.")})
            except SentryError as e:
                return stumped(_why_stumped(e, "start"), issue=out["issue"], setup=out["setup"])
            out["run"] = {"run_id": _dig(started, "run_id"), "status": None, "reused": False}
        else:
            out["run"] = {"run_id": _dig(existing, "run_id"), "status": None, "reused": True}
        waited = 0.0
        while True:
            try:
                auto = existing if existing is not None else _autofix(await self._get(path))
            except SentryError as e:
                return stumped(_why_stumped(e, "poll"), issue=out["issue"], setup=out["setup"], run=out["run"])
            existing = None
            status = str((auto or {}).get("status") or "").upper()
            out["run"] = {**out["run"], "run_id": _dig(auto, "run_id") or out["run"]["run_id"], "status": status or None}
            if status in DONE:
                text = _verdict_text(auto)
                if status in ANSWERED and text:
                    return {**out, "state": "verdict", "reason": None, "verdict": text}
                keys = sorted(auto.keys()) if isinstance(auto, dict) else []
                why = f"Seer stopped with status {status} and nothing readable (response keys: {', '.join(keys) or 'none'})"
                if out["setup"]["caveat"]:
                    why += f". {out['setup']['caveat']}"
                return stumped(why, issue=out["issue"], setup=out["setup"], run=out["run"])
            if waited >= max_wait_s:                      # no status yet = the run has not appeared; same patience
                return stumped(f"Seer was still {status.lower() or 'starting'} after {int(max_wait_s)} s — gave up waiting",
                               issue=out["issue"], setup=out["setup"], run=out["run"])
            await self._sleep(poll_s)
            waited += poll_s


def _autofix(polled: Any) -> dict | None:
    """The run inside GET …/autofix/: `{"autofix": {...}}`, or None for `{"autofix": null}` (no run yet)."""
    auto = polled.get("autofix") if isinstance(polled, dict) else None
    return auto if isinstance(auto, dict) else None


def _why_stumped(e: SentryError, step: str) -> str:
    if e.code == "not_found":
        return f"Sentry has no autofix endpoint for this issue (HTTP 404 when trying to {step} the run)"
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


def _assistant_says(auto: dict) -> list[str]:
    """Seer's own words, oldest first, from the CONVERSATION shape — `blocks[]`, each a chat turn
    `{message: {role, content}, tool_calls, file_patches}`. Recorded live 2026-09-20 from a completed
    run: there is no `steps` key at all any more, and the root cause is the last assistant turn."""
    out = []
    for b in auto.get("blocks") or []:
        msg = b.get("message") if isinstance(b, dict) else None
        if isinstance(msg, dict) and msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
            if msg["content"].strip():
                out.append(msg["content"].strip())
    return out


def _verdict_text(auto: Any) -> str | None:
    """The readable part of a finished run. Shapes seen in Sentry's autofix responses: the CONVERSATION
    (`blocks[].message`, live since 2026-09-20 — the answer is the closing assistant turn); the older
    steps[] with a root-cause step (`causes[].title/description`) and a solution step; else a top-level
    summary. Returns None rather than guessing when none of them is there."""
    if not isinstance(auto, dict):
        return None
    parts: list[str] = []
    said = _assistant_says(auto)
    if said:
        parts.append(said[-1])
        if len(said[-1]) < 120 and len(said) > 1:      # a terse closer: carry the longest turn before it
            parts.insert(0, max(said[:-1], key=len))
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


def snapshot_issue(i: dict) -> dict:
    """One Sentry issue as the telemetry live feed draws it. Count is an int (the API sometimes
    sends it as a string). capture_id is filled later from the latest event's tags, or from the
    title when obs.robot_failure put it there."""
    meta = i.get("metadata") if isinstance(i.get("metadata"), dict) else {}
    title = i.get("title") or meta.get("title") or meta.get("value") or ""
    try:
        count = int(i.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    found = CAPTURE_IN.search(str(title))
    return {"id": str(i.get("id") or ""), "short_id": i.get("shortId") or i.get("short_id"),
            "title": title, "level": i.get("level"), "count": count,
            "first_seen": i.get("firstSeen") or i.get("first_seen"),
            "last_seen": i.get("lastSeen") or i.get("last_seen"),
            "permalink": i.get("permalink"), "culprit": i.get("culprit"),
            "status": i.get("substatus") or i.get("status"),
            "capture_id": found.group(1) if found else None}


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
