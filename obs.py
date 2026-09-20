"""
obs.py — the bridge between Sentry and Elasticsearch.

Used by the Pi, the laptop and the AWS web tier. One import, one init.

THE POINT OF THIS FILE
----------------------
Sentry answers "why was the system slow or broken".
Elasticsearch answers "what did the robot actually see".

Neither is useful alone when a commit produces a wrong diff. So every Sentry
span carries the `capture_id`, and every Elasticsearch document carries the
Sentry `trace_id` — a BIDIRECTIONAL link:

    slow trace in Sentry  --capture_id-->  the exact documents in Elasticsearch
    bad diff in Elastic   --trace_id--->   the exact waterfall in Sentry

That round trip is the submission for both prizes. It is also genuinely how you
debug a robot at 4am, which is why it is worth building rather than describing.
"""
from __future__ import annotations
import os, json, time, functools, contextlib, contextvars, threading
from pathlib import Path
from collections import deque
from typing import Any

try:
    import sentry_sdk
    from sentry_sdk import get_current_span
    _HAVE = True
except ImportError:          # the robot must still run with no SDK installed
    sentry_sdk = None        # type: ignore
    _HAVE = False

_ORG = os.getenv("SENTRY_ORG_SLUG", "")
_PROJ = "gitspace"


# ── init ──────────────────────────────────────────────────────────────────────
# Third-party INFO chatter: ~43 % of web's log bytes are httpx's "HTTP Request: GET … 200 OK", and Sentry
# Logs bills and drops by the byte (19 MB/h accepted, and the first thing venue wifi loses). Our own
# gitspace.* lines — the ones worth reading in an incident — stay. SENTRY_KEEP_LOGGERS un-ignores any of these.
NOISY = ("httpx", "httpcore", "uvicorn.access", "urllib3", "websockets", "elastic_transport", "elasticsearch",
         "botocore", "asyncio", "multipart")


def _quieten() -> None:
    """Two lists, deliberately: ignore_logger drops a logger's BREADCRUMBS and events,
    ignore_logger_for_sentry_logs drops its LOG RECORDS (the Logs product). A noisy library wants both."""
    try:
        from sentry_sdk.integrations.logging import ignore_logger, ignore_logger_for_sentry_logs
    except ImportError:                       # older SDK: logs are filtered by level only
        try:
            from sentry_sdk.integrations.logging import ignore_logger
        except Exception:
            return
        ignore_logger_for_sentry_logs = ignore_logger
    keep = {n.strip() for n in os.getenv("SENTRY_KEEP_LOGGERS", "").split(",") if n.strip()}
    for name in (*NOISY, *[n.strip() for n in os.getenv("SENTRY_QUIET_LOGGERS", "").split(",") if n.strip()]):
        if name not in keep:
            ignore_logger(name)
            ignore_logger_for_sentry_logs(name)


def _profiles_rate(role: str) -> float:
    """The robot balances on the same CPU its process runs on: NO profiler thread there unless asked for
    by name (SENTRY_ROBOT_PROFILES_SAMPLE_RATE). The generic SENTRY_PROFILES_SAMPLE_RATE (1.0 in a copied
    .env) is deliberately ignored for role "robot"."""
    name = "SENTRY_ROBOT_PROFILES_SAMPLE_RATE" if role == "robot" else "SENTRY_PROFILES_SAMPLE_RATE"
    try:
        return min(1.0, max(0.0, float(os.getenv(name, "0" if role == "robot" else "1.0"))))
    except ValueError:
        return 0.0


def init(role: str) -> bool:
    """role: 'robot' | 'laptop' | 'web' — it becomes server_name. Safe to call when SENTRY_DSN is unset."""
    dsn = os.getenv("SENTRY_DSN", "").strip()
    # A DSN that is absent, blank, commented-out or otherwise not a URL must be a
    # no-op, NEVER an exception: sentry_sdk.init() raises BadDsn on garbage, and an
    # observability layer that can take the site down at import is worse than none.
    if not (_HAVE and dsn.startswith("http")):
        return False
    # asyncio is NOT auto-enabled and must be opted into. Without it, a task
    # spawned off the event loop loses the current scope, so its spans detach
    # from the transaction and its errors arrive untagged — which reads as
    # "tracing works except for the async parts", the worst kind of partial.
    integrations = []
    try:
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        integrations.append(AsyncioIntegration())
    except Exception:
        pass

    kw: dict[str, Any] = dict(
        dsn=dsn,
        integrations=integrations,
        environment=os.getenv("SENTRY_ENVIRONMENT", "htn2026"),
        release=os.getenv("SENTRY_RELEASE", "gitspace@0.1.0"),
        server_name=role,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "1.0")),
        profiles_sample_rate=_profiles_rate(role),
        send_default_pii=False,          # we never store people
        # Venue wifi resets idle connections: ~1 h of network_error drops (6 errors, 67 transactions,
        # 524 spans) on Sat 19 Sep. keep_alive holds the connection open, and a deeper queue rides out a
        # stall instead of discarding what arrives while the send is blocked (queue_overflow).
        keep_alive=True,
        transport_queue_size=int(os.getenv("SENTRY_TRANSPORT_QUEUE", "1000")),
    )
    try:
        sentry_sdk.init(enable_logs=True, **kw)      # Logs: newer SDKs
    except TypeError:
        sentry_sdk.init(_experiments={"enable_logs": True}, **kw)
    _quieten()
    sentry_sdk.set_tag("role", role)
    global _ROLE
    _ROLE = role
    _ship_failures_soon()                 # a previous run's failures that never reached Elasticsearch
    return True


# ── the link, in both directions ──────────────────────────────────────────────
def trace_fields() -> dict[str, str]:
    """Embed the RESULT of this in every Elasticsearch document.

    Gives the Elastic side a way back into the exact Sentry waterfall that
    produced it — without this, a bad diff is a dead end.
    """
    if not _HAVE:
        return {}
    span = get_current_span()
    if span is None:
        return {}
    ctx = span.get_trace_context() or {}
    tid, sid = ctx.get("trace_id"), ctx.get("span_id")
    if not tid:
        return {}
    out = {"sentry_trace_id": tid, "sentry_span_id": sid or ""}
    if _ORG:
        out["sentry_url"] = f"https://{_ORG}.sentry.io/performance/trace/{tid}/"
    return out


# The join ids of the capture_scope we're in, so every obs.span() opened inside carries them.
_JOIN: contextvars.ContextVar[dict] = contextvars.ContextVar("obs_join", default={})


def _stamp(sp, tags: dict) -> None:
    """Tag AND data: the tag for issue/transaction search, the data as a span attribute."""
    for k, v in tags.items():
        try:
            sp.set_tag(k, str(v))
            sp.set_data(k, v)
        except Exception:
            pass


def _tag_up(tags: dict) -> None:
    """The current scope, the current span, and the transaction that contains it. A tag set only
    on a forked scope never reaches a transaction that started outside the fork — measured live:
    capture_scope's capture_id was on no span at all (docs/10 D33)."""
    sp = get_current_span()
    if sp is not None:
        _stamp(sp, tags)
        root = getattr(sp, "containing_transaction", None)
        if root is not None and root is not sp:
            _stamp(root, tags)


@contextlib.contextmanager
def capture_scope(capture_id: str, commit_sha: str = "", branch: str = "main"):
    """Tag everything inside with the ids that join the two systems: events on this scope, the
    span and transaction we're inside, and every obs.span() opened in here."""
    if not _HAVE:
        yield {}
        return
    tags = {"capture_id": capture_id, "branch": branch, **({"commit_sha": commit_sha} if commit_sha else {})}
    token = _JOIN.set({**_JOIN.get(), **tags})
    try:
        with sentry_sdk.new_scope() as scope:
            for k, v in tags.items():
                scope.set_tag(k, v)
            _tag_up(tags)
            yield trace_fields()
    finally:
        _JOIN.reset(token)


@contextlib.contextmanager
def span(op: str, desc: str = "", **data):
    """A span that also records its own duration as span data."""
    if not _HAVE:
        yield None
        return
    with sentry_sdk.start_span(op=op, name=desc or op) as sp:
        _stamp(sp, _JOIN.get())                  # capture_id / commit_sha on EVERY span
        for k, v in data.items():
            sp.set_data(k, v)
        t0 = time.perf_counter()
        try:
            yield sp
        finally:
            sp.set_data("duration_ms", round((time.perf_counter() - t0) * 1000, 2))


def transaction(op: str, name: str, parent: dict | None = None):
    """A transaction; with `parent` (trace_headers() from another thread or request), it CONTINUES that
    trace instead of starting a new one, so a background job stays in the waterfall that asked for it."""
    if not _HAVE:
        return contextlib.nullcontext()
    if parent:
        try:
            return sentry_sdk.start_transaction(sentry_sdk.continue_trace(parent, op=op, name=name))
        except Exception:
            pass
    return sentry_sdk.start_transaction(op=op, name=name)


def trace_headers() -> dict[str, str]:
    """`sentry-trace` + `baggage` of where we are now: to continue this trace in a worker thread
    (transaction(parent=...)) or to send with an HTTP request by hand."""
    if not _HAVE:
        return {}
    try:
        out = {"sentry-trace": sentry_sdk.get_traceparent(), "baggage": sentry_sdk.get_baggage()}
        return {k: v for k, v in out.items() if v}
    except Exception:
        return {}


# ── robot failures become Sentry issues, with telemetry attached ──────────────
def robot_failure(kind: str, detail: str, telemetry: list[dict] | None = None,
                  frame: "bytes | Any | None" = None, *, level: str = "error",
                  context: dict | None = None, fingerprint: list[str] | None = None, **tags):
    """A failed grasp / a fall / an unreachable pose arrives as a Sentry ISSUE,
    carrying the seconds of telemetry that preceded it as breadcrumbs — and, when
    `frame` is given (JPEG bytes or a BGR array), the camera frame the target pose
    was computed from, as ONE attachment (JPEG q70, longest side <= 480 px).

    "The robot fell over" showing up in an issue feed with a tilt graph attached
    is the kind of thing a judge repeats to a colleague.

    `level` ("warning" for a map reset), `context` (structured, e.g. the nav pose/path/goal: tags are
    flat strings) and `fingerprint` (group by kind, not by the numbers in `detail`) are optional.
    """
    if not _HAVE:
        return None
    event_id, joins = None, {}
    with sentry_sdk.new_scope() as scope:
        # Everything on the FORKED scope, so it dies with this event instead of riding along
        # on the next one: fall #2 would otherwise carry fall #1's tilt graph — and its photo.
        for s in (telemetry or [])[-40:]:
            scope.add_breadcrumb(category="telemetry", level="info", message=kind, data=s)
        jpeg = small_jpeg(frame) if frame is not None else None
        why = _attachment_blocked(len(jpeg)) if jpeg else None
        if jpeg and why is None:
            scope.add_attachment(bytes=jpeg, filename=f"{kind}.jpg", content_type="image/jpeg")
        elif jpeg:
            scope.set_tag("attachment_skipped", why)
        for k, v in tags.items():
            scope.set_tag(k, str(v))
        scope.set_tag("failure_kind", kind)
        if context:
            scope.set_context(kind, context)
        if fingerprint:
            scope.fingerprint = fingerprint
        joins = trace_fields()
        if not joins.get("sentry_trace_id"):            # no span: the scope's trace, the one the event carries
            tp = (sentry_sdk.get_traceparent() or "").split("-")
            if len(tp) >= 2:
                joins = {"sentry_trace_id": tp[0], "sentry_span_id": tp[1]}
        event_id = sentry_sdk.capture_message(f"robot: {kind} — {detail}", level=level)
    _record_failure(kind, detail, level, event_id, joins, tags)
    return event_id


# ── robot failures also land in Elasticsearch: evidence that survives a dropped Sentry send ─────
# A Sentry event lost to network_error is gone. So every robot_failure (in a process that called init())
# is ALSO a room-events document, spooled to disk FIRST and shipped in the background: one file per
# failure, sent with _create/<id> (the Sentry event id), so a retry or a second process sending the same
# file is a 409, never a duplicate. Joins to Sentry by sentry_trace_id. Only fields in room-events' strict
# mapping. capture_id and commit_sha go in the MESSAGE, not in their fields: web reads a room-events doc
# carrying those as "the capture's / the commit's event", and a failure must not stand in for one.
_ROLE: str | None = None
_SHIP_LOCK = threading.Lock()


def _failure_spool() -> Path:
    return Path(os.getenv("ROBOT_FAILURE_SPOOL") or "~/.cache/gitspace/failure-spool").expanduser()


def failure_doc(kind: str, detail: str, level: str, joins: dict, tags: dict) -> dict:
    from datetime import datetime, timezone
    extra = [f"{k} {tags[k]}" for k in ("capture_id", "commit_sha", "job_id", "action") if tags.get(k)]
    doc = {"@timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
           "event_type": "robot_failure", "outcome": level, "author": _ROLE or "unknown",
           "message": f"{kind}: {detail}" + (f" ({'; '.join(extra)})" if extra else "")}
    if tags.get("zone"):
        doc["zone"] = str(tags["zone"])
    if tags.get("object_id"):
        doc["objects_affected"] = [str(tags["object_id"])]
    doc.update({k: v for k, v in joins.items() if k in ("sentry_trace_id", "sentry_span_id", "sentry_url") and v})
    return doc


def _record_failure(kind: str, detail: str, level: str, event_id, joins: dict, tags: dict) -> None:
    if _ROLE is None:                     # never initialised (tests, scripts): no durable side effect
        return
    try:
        import uuid
        doc_id = str(event_id or uuid.uuid4().hex)
        d = _failure_spool()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{doc_id}.tmp"
        tmp.write_text(json.dumps(failure_doc(kind, detail, level, joins, tags)))
        tmp.replace(d / f"{doc_id}.json")
    except Exception:
        return
    _ship_failures_soon()


def _ship_failures_soon() -> None:
    threading.Thread(target=ship_failures, name="obs-failures", daemon=True).start()


def ship_failures(timeout: float = 5.0) -> dict:
    """Send every spooled failure to room-events. Idempotent: _create/<id>, 201 or 409 deletes the file.
    Without ELASTIC_URL / ELASTIC_API_KEY (parked, or on the robot) the files wait for a run that has them."""
    import urllib.error
    import urllib.request
    url, key = os.getenv("ELASTIC_URL", "").rstrip("/"), os.getenv("ELASTIC_API_KEY", "").strip()
    stats = {"sent": 0, "already": 0, "kept": 0}
    if not (url.startswith("http") and key) or not _SHIP_LOCK.acquire(blocking=False):
        return stats
    try:
        for f in sorted(_failure_spool().glob("*.json")):
            req = urllib.request.Request(f"{url}/room-events/_create/{f.stem}", data=f.read_bytes(), method="PUT",
                                         headers={"Content-Type": "application/json", "Authorization": f"ApiKey {key}"})
            try:
                with urllib.request.urlopen(req, timeout=timeout):
                    stats["sent"] += 1
            except urllib.error.HTTPError as e:
                if e.code != 409:
                    stats["kept"] += 1
                    continue
                stats["already"] += 1
            except Exception:
                stats["kept"] += 1
                break                             # the network is down: the rest wait for the next ship
            f.unlink(missing_ok=True)
    finally:
        _SHIP_LOCK.release()
    return stats


def breadcrumb(category: str, message: str, level: str = "info", **data):
    """A breadcrumb on the current scope: the next issue from this process carries it (BB status
    transitions, `ready` flips — plan/roommate/03-interfaces.md §9)."""
    if not _HAVE:
        return
    try:
        sentry_sdk.add_breadcrumb(category=category, message=message, level=level, data=data or None)
    except Exception:
        pass


def capture_quality(skew_ms: float | None, tilt_rate_max: float | None,
                    coverage: float | None) -> bool:
    """The gate from docs/22-camera-sync.md, recorded as span data AND as measurements
    either way — data to find a capture, measurements to chart every capture over time.

    Recording the numbers on a REJECTED capture matters as much as on an
    accepted one — that is what turns "the diff looked wrong" into an answer.

    A value that was not recorded (None) FAILS its check and is not measured. The Pi
    returns tilt_rate_max=None when its ring did not cover the whole latch window; a
    gate that passes on missing evidence passes the capture it exists to reject.
    """
    vals = {"skew_ms": skew_ms, "tilt_rate_max": tilt_rate_max, "coverage": coverage}
    ok = (skew_ms is not None and skew_ms < 25 and tilt_rate_max is not None
          and tilt_rate_max < 0.05 and coverage is not None and coverage > 0.60)
    if _HAVE:
        sp = get_current_span()
        if sp is not None:
            for k, v in vals.items():
                if v is not None:
                    sp.set_data(k, v)
            sp.set_data("quality_ok", ok)
        measure(**{k: v for k, v in vals.items() if v is not None})
        if not ok:
            # The CURRENT scope (capture_scope's fork), not sentry_sdk.set_tag's process-wide
            # isolation scope — otherwise every later event is tagged rejected too. And the span
            # + its transaction, which a forked scope's tags never reach.
            sentry_sdk.get_current_scope().set_tag("capture_rejected", "true")
            _tag_up({"capture_rejected": "true"})
    return ok


def flush(timeout: float = 5.0):
    if _HAVE:
        sentry_sdk.flush(timeout=timeout)

# ── measurements: numbers you can CHART across every capture ─────────────────
def measure(**kv):
    """Attach numeric measurements to the current transaction.

    A tag is a string you filter by; a MEASUREMENT is a number Sentry charts and
    alerts on. `tilt_rate_max` as a tag answers "show me that capture";
    as a measurement it answers "is the robot getting less stable over the
    weekend, and did it correlate with the failed grasps?" — which is a question
    only observability can answer and is exactly the Influence axis.
    """
    if not _HAVE:
        return
    tx = sentry_sdk.get_current_scope().transaction
    if tx is None:
        return
    for k, v in kv.items():
        try:
            tx.set_measurement(k, float(v))
        except Exception:
            pass


# The attachment budget: 1 GB/mo on the education plan, and the photos have to still be arriving
# at judging. Three limits, whichever bites first, over a rolling hour:
#     20 attachments   ·   1 MB   ·   100 KB each
# 1 MB/h is <= 720 MB/month even if the robot fails nonstop; a 480 px q70 frame is ~8 KB.
# The hour is LAPTOP-WIDE, not per process: the hub, the CLI, web and perception all import obs,
# so the ledger is a locked file (per-process memory only if the file can't be used). Over a
# limit the ISSUE still goes out — tagged attachment_skipped=<why> — just without the file.
MAX_ATTACHMENTS_PER_HOUR = int(os.getenv("OBS_MAX_ATTACHMENTS_PER_HOUR", "20"))
MAX_ATTACHMENT_BYTES_PER_HOUR = int(os.getenv("OBS_MAX_ATTACHMENT_BYTES_PER_HOUR", str(1_000_000)))
MAX_ATTACHMENT_BYTES = int(os.getenv("OBS_MAX_ATTACHMENT_BYTES", str(100_000)))
_LEDGER = Path(os.path.expanduser(os.getenv("OBS_ATTACH_LEDGER", "~/.cache/gitspace/attach-ledger.json")))
_ATTACHED: deque = deque()                 # (time, bytes) — the fallback when the ledger can't be used
_ATTACH_LOCK = threading.Lock()


def _within_budget(entries: list, nbytes: int, now: float) -> tuple[list, str | None]:
    entries = [e for e in entries if now - e[0] <= 3600]
    if len(entries) >= MAX_ATTACHMENTS_PER_HOUR:
        return entries, "hourly_count"
    if sum(e[1] for e in entries) + nbytes > MAX_ATTACHMENT_BYTES_PER_HOUR:
        return entries, "hourly_bytes"
    return entries + [[now, nbytes]], None


def _attachment_blocked(nbytes: int) -> str | None:
    """None if this attachment fits the budget (and it is now counted), else why not."""
    if nbytes > MAX_ATTACHMENT_BYTES:
        return "too_large"
    now = time.time()
    with _ATTACH_LOCK:
        try:
            import fcntl
            _LEDGER.parent.mkdir(parents=True, exist_ok=True)
            with open(_LEDGER, "a+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)          # one budget across every process on the laptop
                f.seek(0)
                try:
                    entries = json.loads(f.read() or "[]")
                except ValueError:
                    entries = []
                entries, why = _within_budget(entries, nbytes, now)
                f.seek(0)
                f.truncate()
                f.write(json.dumps(entries))
                return why
        except (ImportError, OSError):
            entries, why = _within_budget(list(_ATTACHED), nbytes, now)
            _ATTACHED.clear()
            _ATTACHED.extend(entries)
            return why


def small_jpeg(frame, max_side: int = 480, quality: int = 70) -> bytes | None:
    """JPEG bytes or a BGR array -> JPEG q70 with the longest side <= max_side, or None.
    Attachments are for failures, not captures: ~8 KB each keeps 1 GB/mo roomy."""
    try:
        import cv2
        import numpy as np
    except ImportError:          # the web tier has no OpenCV: pass small JPEGs through as-is
        return bytes(frame) if isinstance(frame, (bytes, bytearray)) and len(frame) <= 300_000 else None
    img = (cv2.imdecode(np.frombuffer(bytes(frame), np.uint8), cv2.IMREAD_COLOR)
           if isinstance(frame, (bytes, bytearray)) else frame)
    if img is None or getattr(img, "ndim", 0) not in (2, 3):
        return None
    h, w = img.shape[:2]
    if max(h, w) > max_side:
        k = max_side / max(h, w)
        img = cv2.resize(img, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


# ── context: structured blobs, not flat strings ──────────────────────────────
def context(name: str, data: dict):
    """A tag is one string. A context is a whole table on the issue page.

    The capture-quality block belongs here: skew, tilt, coverage, per-camera
    point counts, all visible at once on the issue rather than flattened into
    a dozen tags.
    """
    if _HAVE:
        # the CURRENT scope (a capture_scope's fork, say), not set_context's process-wide
        # isolation scope — which would stamp this table on every later event too
        sentry_sdk.get_current_scope().set_context(name, data)


# ── attachments: the thing nobody else will have ─────────────────────────────
def attach(filename: str, data: bytes, content_type: str = "application/octet-stream"):
    """Attach a FILE to the current event — 1 GB/mo on the education plan.

    This is the creative use: a failed grasp arrives as a Sentry issue with
    the actual camera frame the target pose was computed from. You can SEE
    why the robot reached into empty space. Nobody debugging a web app has
    a reason to do this; a robot does.

    Keep frames small (JPEG q70, downscaled) — an attachment per failure is
    fine, an attachment per capture is not.

    Only inside obs.capture_scope(): a scope keeps its attachments and re-sends them with EVERY
    later event, so outside a fork one call would ride on everything this process ever reports.
    Inside, the fork dies at the scope's end. Counts against the laptop-wide budget; returns
    whether it was attached.
    """
    if not _HAVE:
        return False
    if not _JOIN.get():
        import logging
        logging.getLogger("obs").warning("obs.attach(%s) outside capture_scope: refused — it would "
                                         "be re-sent with every later event", filename)
        return False
    why = _attachment_blocked(len(data))
    if why:
        sentry_sdk.get_current_scope().set_tag("attachment_skipped", why)
        return False
    try:
        sentry_sdk.get_current_scope().add_attachment(
            bytes=data, filename=filename, content_type=content_type)
        return True
    except Exception:
        return False


# ── AI agent monitoring: their sheet says "Trace your MCP & LLM calls" ────────
@contextlib.contextmanager
def agent_tool(name: str, kind: str = "mcp", **args):
    """Wrap ONE agent tool call so it becomes a span with its inputs/outputs.

    We are an MCP project on both sides — bracketbot-mcp for actions, Elastic
    Agent Builder for retrieval — so a single trace reads:

        gen_ai.invoke_agent → gen_ai.execute_tool (elastic) → es.search
                            → gen_ai.execute_tool (mcp)     → robot.arm

    which is Creativity and Depth of integration in one screenshot. The op and
    attributes are sentry_sdk's own (consts OP.GEN_AI_EXECUTE_TOOL / SPANDATA), so
    Sentry's AI Agents view counts these as tool calls (docs/10 D31). The caller
    sets `gen_ai.tool.call.result` (a JSON string) on the yielded span.
    """
    if not _HAVE:
        yield None
        return
    with sentry_sdk.start_span(op="gen_ai.execute_tool", name=f"execute_tool {name}") as sp:
        sp.set_data("gen_ai.operation.name", "execute_tool")
        sp.set_data("gen_ai.tool.name", name)
        sp.set_data("gen_ai.tool.type", kind)
        sp.set_data("gen_ai.tool.call.arguments", json.dumps(args, default=str)[:2000])
        _stamp(sp, _JOIN.get())
        t0 = time.perf_counter()
        try:
            yield sp
        except Exception:
            sp.set_status("internal_error")
            raise
        finally:
            sp.set_data("duration_ms", round((time.perf_counter() - t0) * 1000, 2))


def _openai_instrumented() -> bool:
    try:
        from sentry_sdk.integrations.openai import OpenAIIntegration
        return sentry_sdk.get_client().get_integration(OpenAIIntegration) is not None
    except Exception:
        return False


@contextlib.contextmanager
def agent_turn(prompt: str, model: str = "gpt-5", sdk_visible: bool | None = None):
    """An LLM call as a gen_ai.chat span — for the calls the SDK CAN'T see (the Realtime/WebRTC
    voice path). A call through the OpenAI client is already a gen_ai span, with tokens, from
    sentry_sdk's OpenAI integration; wrapping it again double-counts (live, docs/10 D31: 35 calls
    -> 70 LLM spans, 13,020 input tokens counted as 26,040). So while that integration is active
    this is a plain `agent.turn` span, unless sdk_visible=False says the SDK can't see the call.
    On a gen_ai.chat span the caller sets gen_ai.response.model and gen_ai.usage.* once known."""
    if not _HAVE:
        yield None
        return
    visible = _openai_instrumented() if sdk_visible is None else sdk_visible
    if visible:
        with sentry_sdk.start_span(op="agent.turn", name=f"turn {model}") as sp:
            sp.set_data("model", model)
            yield sp
        return
    with sentry_sdk.start_span(op="gen_ai.chat", name=f"chat {model}") as sp:
        sp.set_data("gen_ai.operation.name", "chat")
        sp.set_data("gen_ai.system", "openai")
        sp.set_data("gen_ai.request.model", model)
        sp.set_data("gen_ai.request.prompt_chars", len(prompt))
        yield sp


# ── cron monitor: the watch loop has a heartbeat, so a dead one pages us ─────
def heartbeat(slug: str = "watch-loop", status: str = "ok", duration: float | None = None,
              monitor_config: dict | None = None):
    """One cron monitor is on the plan. Point it at the watch loop.

    A perception loop that dies silently keeps serving STALE commits, which is
    worse than crashing — the room looks clean because nothing is looking at it.
    """
    if not _HAVE:
        return
    try:
        from sentry_sdk.crons import capture_checkin
        capture_checkin(monitor_slug=slug, status=status, duration=duration,
                        monitor_config=monitor_config)   # given: the monitor upserts itself
    except Exception:
        pass

