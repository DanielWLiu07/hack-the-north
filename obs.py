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
import os, time, functools, contextlib
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
def init(role: str) -> bool:
    """role: 'pi' | 'laptop' | 'web'. Safe to call when SENTRY_DSN is unset."""
    dsn = os.getenv("SENTRY_DSN", "").strip()
    # A DSN that is absent, blank, commented-out or otherwise not a URL must be a
    # no-op, NEVER an exception: sentry_sdk.init() raises BadDsn on garbage, and an
    # observability layer that can take the site down at import is worse than none.
    if not (_HAVE and dsn.startswith("http")):
        return False
    kw: dict[str, Any] = dict(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT", "htn2026"),
        release=os.getenv("SENTRY_RELEASE", "gitspace@0.1.0"),
        server_name=role,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "1.0")),
        profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "1.0")),
        send_default_pii=False,          # we never store people
    )
    try:
        sentry_sdk.init(enable_logs=True, **kw)      # Logs: newer SDKs
    except TypeError:
        sentry_sdk.init(_experiments={"enable_logs": True}, **kw)
    sentry_sdk.set_tag("role", role)
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


@contextlib.contextmanager
def capture_scope(capture_id: str, commit_sha: str = "", branch: str = "main"):
    """Tag everything inside with the ids that join the two systems."""
    if not _HAVE:
        yield {}
        return
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("capture_id", capture_id)
        scope.set_tag("branch", branch)
        if commit_sha:
            scope.set_tag("commit_sha", commit_sha)
        yield trace_fields()


@contextlib.contextmanager
def span(op: str, desc: str = "", **data):
    """A span that also records its own duration as span data."""
    if not _HAVE:
        yield None
        return
    with sentry_sdk.start_span(op=op, name=desc or op) as sp:
        for k, v in data.items():
            sp.set_data(k, v)
        t0 = time.perf_counter()
        try:
            yield sp
        finally:
            sp.set_data("duration_ms", round((time.perf_counter() - t0) * 1000, 2))


def transaction(op: str, name: str):
    if not _HAVE:
        return contextlib.nullcontext()
    return sentry_sdk.start_transaction(op=op, name=name)


# ── robot failures become Sentry issues, with telemetry attached ──────────────
def robot_failure(kind: str, detail: str, telemetry: list[dict] | None = None,
                  frame: "bytes | Any | None" = None, **tags):
    """A failed grasp / a fall / an unreachable pose arrives as a Sentry ISSUE,
    carrying the seconds of telemetry that preceded it as breadcrumbs — and, when
    `frame` is given (JPEG bytes or a BGR array), the camera frame the target pose
    was computed from, as ONE attachment (JPEG q70, longest side <= 640 px).

    "The robot fell over" showing up in an issue feed with a tilt graph attached
    is the kind of thing a judge repeats to a colleague.
    """
    if not _HAVE:
        return None
    with sentry_sdk.new_scope() as scope:
        # Everything on the FORKED scope, so it dies with this event instead of riding along
        # on the next one: fall #2 would otherwise carry fall #1's tilt graph — and its photo.
        for s in (telemetry or [])[-40:]:
            scope.add_breadcrumb(category="telemetry", level="info", message=kind, data=s)
        jpeg = small_jpeg(frame) if frame is not None else None
        if jpeg:
            scope.add_attachment(bytes=jpeg, filename=f"{kind}.jpg", content_type="image/jpeg")
        for k, v in tags.items():
            scope.set_tag(k, str(v))
        scope.set_tag("failure_kind", kind)
        return sentry_sdk.capture_message(f"robot: {kind} — {detail}", level="error")


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
            # isolation scope — otherwise every later event is tagged rejected too.
            sentry_sdk.get_current_scope().set_tag("capture_rejected", "true")
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


def small_jpeg(frame, max_side: int = 640, quality: int = 70) -> bytes | None:
    """JPEG bytes or a BGR array -> JPEG q70 with the longest side <= max_side, or None.
    Attachments are for failures, not captures: a few tens of KB each keeps 1 GB/mo roomy."""
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
        sentry_sdk.set_context(name, data)


# ── attachments: the thing nobody else will have ─────────────────────────────
def attach(filename: str, data: bytes, content_type: str = "application/octet-stream"):
    """Attach a FILE to the current event — 1 GB/mo on the education plan.

    This is the creative use: a failed grasp arrives as a Sentry issue with
    the actual camera frame the target pose was computed from. You can SEE
    why the robot reached into empty space. Nobody debugging a web app has
    a reason to do this; a robot does.

    Keep frames small (JPEG q70, downscaled) — an attachment per failure is
    fine, an attachment per capture is not.
    """
    if not _HAVE:
        return
    try:
        sentry_sdk.get_current_scope().add_attachment(
            bytes=data, filename=filename, content_type=content_type)
    except Exception:
        pass


# ── AI agent monitoring: their sheet says "Trace your MCP & LLM calls" ────────
@contextlib.contextmanager
def agent_tool(name: str, kind: str = "mcp", **args):
    """Wrap ONE agent tool call so it becomes a span with its inputs/outputs.

    We are an MCP project on both sides — bracketbot-mcp for actions, Elastic
    Agent Builder for retrieval — so a single trace reads:

        agent.tool_call → es.search → agent.decide → mcp.room_revert → arm.pick

    which is Creativity and Depth of integration in one screenshot.
    """
    if not _HAVE:
        yield None
        return
    with sentry_sdk.start_span(op=f"gen_ai.{kind}.tool", name=name) as sp:
        sp.set_data("gen_ai.tool.name", name)
        for k, v in args.items():
            sp.set_data(f"gen_ai.tool.input.{k}", str(v)[:200])
        t0 = time.perf_counter()
        try:
            yield sp
        except Exception:
            sp.set_status("internal_error")
            raise
        finally:
            sp.set_data("duration_ms", round((time.perf_counter() - t0) * 1000, 2))


@contextlib.contextmanager
def agent_turn(prompt: str, model: str = "gpt-5"):
    """The LLM call itself, as a gen_ai span — the other half of agent monitoring."""
    if not _HAVE:
        yield None
        return
    with sentry_sdk.start_span(op="gen_ai.chat", name=model) as sp:
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

