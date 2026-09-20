"""VLM free-text description per instance, PER CAMERA VIEW. Stage 5's text half.

Each view is described on its own and every description is kept. Three cameras give
three descriptions of one object -- "a blue ceramic mug", "cup with handle, chipped",
"cylindrical container, dark" -- and nothing here reconciles them. That disagreement is
the entity-resolution signal hybrid search runs on (docs/11-elastic.md, "Gap 2"). Each one
becomes the `raw_description` of its own room-observations document.

There is deliberately no function in this file that turns several descriptions into one.

The VLM is never shown the segmenter's label or another view's description. A hint would
pull the three views toward agreement and erase the signal they exist to provide.
"""
from __future__ import annotations

import base64
import contextvars
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from cluster import Instance
from keys import credential

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))    # obs.py lives at the repo root
import obs  # noqa: E402

log = logging.getLogger(__name__)

MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-5")
EFFORT = os.getenv("OPENAI_VISION_EFFORT", "minimal")   # a caption, not a proof. "" to omit
MAX_OUTPUT_TOKENS = 2000      # gpt-5 REASONS out of this budget before it answers: ~140
                              # tokens for a two-word caption. Too tight and the reply is
                              # valid but EMPTY, which reads like a refusal
ATTEMPTS = 3                  # label_attempt counts these; ours are the only retries
TIMEOUT_S = float(os.getenv("OPENAI_VISION_TIMEOUT", "30"))
BACKOFF_S = (1.0, 2.0)        # before attempts 2 and 3; a Retry-After header wins, up to MAX_WAIT_S
MAX_WAIT_S = 8.0
FATAL_STATUS = {401, 403, 404}                  # bad key, no access, unknown model: stop the capture
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}
_sleep = time.sleep           # tests patch this
WORKERS = 8                   # ~45 views per capture
PAD = 0.15                    # crop padding, fraction of the mask's bbox
MIN_SIDE, MAX_SIDE = 256, 768 # px. left_rect is 480x270, so object crops get upscaled
DIM = 0.35                    # brightness kept outside the mask: context, not subject
SMALL_PX = 2000               # a mask under this is mostly context in its own crop, so PAD (a
CONTEXT_PX = 120              # FRACTION of its bbox) gives it almost none: it gets this many px
DIM_SMALL = 0.7               # instead, and keeps more of the room around it. Measured on the
                              # hallway's crisp packets (cap_0015, 199 and 773 mask px): at
                              # PAD/DIM the model answers "unidentifiable object"; with these it
                              # answers "small wrapper" and "snack bag".

PROMPT = (
    "A robot camera is looking at a room. The bright region of this image is ONE physical "
    "object; the dimmed surroundings are only context. Describe the bright object exactly as "
    "it appears from this viewpoint.\n"
    "label: a 1-4 word noun phrase naming it.\n"
    "description: one sentence covering colour, material, shape, markings and condition.\n"
    "If you cannot tell what it is, say that plainly instead of guessing."
)
SCHEMA = {
    "type": "object",
    "properties": {"label": {"type": "string"}, "description": {"type": "string"}},
    "required": ["label", "description"],
    "additionalProperties": False,
}


@dataclass
class ViewDescription:
    """One camera's description of one instance. Becomes one room-observations document."""
    camera: str | None
    label: str | None           # the VLM's short name for it, from this view only
    text: str | None            # raw_description: unreconciled free text from this view
    model: str
    attempt: int                # label_attempt
    error: str | None = None    # set when every attempt failed; text is then None


class VLMOffline(RuntimeError):
    """No usable key (parked, unset, or garbage): describe nothing, call nothing."""


class TruncatedReply(RuntimeError):
    """A valid response with no usable answer: the output budget ran out mid-reasoning."""


class OpenAIVLM:
    """jpeg bytes -> {"label", "description"} via the Responses API, schema-constrained.

    The SDK's own retries are off (max_retries=0) so describe_view's policy is the only one
    and label_attempt counts real attempts. `usage` totals tokens across calls (thread-safe);
    describe() puts them on its span. Raises at construction when there is no key.
    """

    def __init__(self, model: str = MODEL, effort: str = EFFORT):
        key, why = credential("OPENAI_API_KEY")
        if not key:
            raise VLMOffline(why)          # before building a client: a parked key sends nothing
        from openai import OpenAI

        self.client = OpenAI(api_key=key, timeout=TIMEOUT_S, max_retries=0)
        self.model, self.effort = model, effort
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
        self._lock = threading.Lock()

    def __call__(self, jpeg: bytes) -> dict:
        url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
        kw = {"reasoning": {"effort": self.effort}} if self.effort else {}
        # ONE gen_ai span per call. When sentry_sdk's OpenAI integration is live it already emits
        # gen_ai.responses for this call (seen live: 35/35 nested under ours, tokens counted
        # twice), so ours becomes a plain span; otherwise ours is the gen_ai.chat span.
        auto = _sdk_traces_openai()
        wrap = obs.span("perception.vlm_call", self.model, model=self.model) if auto else obs.agent_turn(PROMPT, self.model)
        with wrap as sp:
            if sp is not None and not auto:                   # the module's required/recommended fields
                for k, v in (("gen_ai.operation.name", "chat"), ("gen_ai.provider.name", "openai"),
                             ("gen_ai.request.model", self.model), ("gen_ai.pipeline.name", "perception.describe"),
                             ("gen_ai.request.max_tokens", MAX_OUTPUT_TOKENS)):
                    sp.set_data(k, v)
            r = self.client.responses.create(
                model=self.model,
                input=[{"role": "user", "content": [{"type": "input_text", "text": PROMPT},
                                                    {"type": "input_image", "image_url": url}]}],
                text={"format": {"type": "json_schema", "name": "view_description",
                                 "schema": SCHEMA, "strict": True}},
                max_output_tokens=MAX_OUTPUT_TOKENS,
                **kw,
            )
            u = self._count(getattr(r, "usage", None))
            if sp is not None and not auto:
                sp.set_data("gen_ai.response.model", getattr(r, "model", None) or self.model)
                sp.set_data("gen_ai.response.finish_reasons", [getattr(r, "status", "completed")])
                if getattr(r, "id", None):
                    sp.set_data("gen_ai.response.id", r.id)
                if u:
                    sp.set_data("gen_ai.usage.input_tokens", u["input_tokens"])
                    sp.set_data("gen_ai.usage.output_tokens", u["output_tokens"])
                    sp.set_data("gen_ai.usage.total_tokens", u["input_tokens"] + u["output_tokens"])
                    sp.set_data("gen_ai.usage.reasoning.output_tokens", u["reasoning_tokens"])
        if getattr(r, "status", "completed") == "incomplete":
            why = getattr(getattr(r, "incomplete_details", None), "reason", None)
            raise TruncatedReply(f"incomplete response ({why}); reasoning spends max_output_tokens")
        if not r.output_text:
            raise TruncatedReply("empty output_text: a truncated reply, not a refusal")
        return json.loads(r.output_text)

    def _count(self, u) -> dict | None:
        """Add one response's usage to the running totals; returns that response's own."""
        if u is None:
            return None
        details = getattr(u, "output_tokens_details", None)
        one = {"input_tokens": getattr(u, "input_tokens", 0) or 0,
               "output_tokens": getattr(u, "output_tokens", 0) or 0,
               "reasoning_tokens": getattr(details, "reasoning_tokens", 0) or 0}
        with self._lock:
            self.usage["calls"] += 1
            for k, v in one.items():
                self.usage[k] += v
        return one


def crop(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """The mask's padded bbox, with everything outside the mask dimmed, sized for a VLM.

    A SMALL mask (under SMALL_PX, e.g. a crisp packet 8 x 19 px on the floor 1.3 m out) is
    treated differently on both counts: PAD is a fraction of its own bbox, which gives a tiny
    object almost no context, and DIM throws away the little texture upscaling left it. Under
    CONTEXT_PX of real image and DIM_SMALL it stops being a blob -- measured against the live
    model on cap_0015's two packets, which go from "unidentifiable object" to "small wrapper"
    and "snack bag". Bigger objects keep the tighter crop: there, dimming is what says WHICH
    object of several the answer is about.
    """
    ys, xs = np.nonzero(mask)
    if not len(ys):
        raise ValueError("empty mask")
    h, w = mask.shape
    small = int(mask.sum()) < SMALL_PX
    pad, dim = (CONTEXT_PX, DIM_SMALL) if small else (0, DIM)
    py = max(int((ys.max() - ys.min()) * PAD) + 4, pad)
    px = max(int((xs.max() - xs.min()) * PAD) + 4, pad)
    y0, y1 = max(0, ys.min() - py), min(h, ys.max() + 1 + py)
    x0, x1 = max(0, xs.min() - px), min(w, xs.max() + 1 + px)
    c = image[y0:y1, x0:x1].astype(np.float32)
    c[~mask[y0:y1, x0:x1]] *= dim
    c = c.astype(np.uint8)
    short, long = min(c.shape[:2]), max(c.shape[:2])
    s = min(max(1.0, MIN_SIDE / short), MAX_SIDE / long)
    if s != 1.0:
        c = cv2.resize(c, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
    return c


def _sdk_traces_openai() -> bool:
    """True when sentry_sdk's OpenAI integration is live and will trace the call itself.
    Reads client state only; never initialises anything."""
    try:
        import sentry_sdk
        from sentry_sdk.integrations.openai import OpenAIIntegration

        return sentry_sdk.get_client().get_integration(OpenAIIntegration) is not None
    except Exception:  # noqa: BLE001 -- no SDK, no integration: we trace it ourselves
        return False


class _Breaker:
    """Trips on a fatal error, so the other ~44 views of a capture don't each fail the same way."""

    def __init__(self):
        self.reason: str | None = None
        self._lock = threading.Lock()

    def trip(self, reason: str) -> None:
        with self._lock:
            if self.reason is None:
                self.reason = reason
                log.error("describe: VLM unavailable, skipping the rest of this capture: %s", reason)


def classify(e: Exception) -> str:
    """"fatal" (stop the capture), "retry" (transient) or "fail" (this view only: the same
    input fails the same way). Keyed on HTTP status, so it holds across openai SDK versions."""
    status = getattr(e, "status_code", None)
    if status in FATAL_STATUS:
        return "fatal"
    if status in RETRY_STATUS:
        return "retry"
    if status is not None or isinstance(e, TruncatedReply):
        return "fail"
    return "retry"            # timeouts, dropped connections, malformed model output


def _wait(e: Exception, attempt: int) -> float:
    headers = getattr(getattr(e, "response", None), "headers", None) or {}
    try:
        return min(MAX_WAIT_S, max(0.0, float(headers.get("retry-after"))))
    except (TypeError, ValueError):
        return BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)]


def describe_view(inst: Instance, image: np.ndarray, vlm, breaker: _Breaker | None = None) -> ViewDescription:
    """One instance, one camera. Never raises: a failed view is recorded, not dropped."""
    model = getattr(vlm, "model", type(vlm).__name__)
    breaker = breaker or _Breaker()
    if inst.mask is None:
        return ViewDescription(inst.camera, None, None, model, 0, "no mask (cluster-path instance)")
    ok, jpeg = cv2.imencode(".jpg", crop(image, inst.mask), [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        return ViewDescription(inst.camera, None, None, model, 0, "jpeg encode failed")
    err, attempt = "", 0
    for attempt in range(1, ATTEMPTS + 1):
        if breaker.reason:
            return ViewDescription(inst.camera, None, None, model, attempt - 1, f"vlm unavailable: {breaker.reason}")
        try:
            out = vlm(jpeg.tobytes())
            label, text = str(out["label"]).strip(), str(out["description"]).strip()
            if text:
                return ViewDescription(inst.camera, label or None, text, model, attempt)
            err = "empty description"
            continue
        except Exception as e:  # noqa: BLE001 -- one bad view must not sink the capture
            err = f"{type(e).__name__}: {e}"
            kind = classify(e)
            log.warning("describe %s attempt %d (%s): %s", inst.camera, attempt, kind, err)
            if kind == "fatal":
                breaker.trip(err)
                break
            if kind == "fail":
                break
            if attempt < ATTEMPTS:
                _sleep(_wait(e, attempt))
    return ViewDescription(inst.camera, None, None, model, attempt, err)


def describe(views: list[tuple[Instance, np.ndarray]], vlm=None) -> list[ViewDescription]:
    """(instance, that camera's left_rect) pairs -> one ViewDescription each, in order.

    Also sets `inst.description`, so the description travels with its own view through
    fuse and merge. Offline (no key, or the client won't build) every view gets an error
    and a null description, and the capture carries on: the geometry and the ids don't
    depend on words.
    """
    if not views:
        return []
    offline = None
    if vlm is None:
        try:
            vlm = OpenAIVLM()
        except Exception as e:  # noqa: BLE001 -- e.g. OPENAI_API_KEY parked
            offline = f"vlm offline: {type(e).__name__}: {e}"
            log.warning("describe: %s", offline)
    model = getattr(vlm, "model", MODEL)
    with obs.span("perception.describe", f"VLM x{len(views)}", views=len(views), model=model) as sp:
        t0 = time.perf_counter()
        if offline:
            out = [ViewDescription(inst.camera, None, None, model, 0, offline) for inst, _ in views]
        else:
            breaker = _Breaker()
            # Each task runs in a COPY of this context, so the Sentry span current here (and
            # anything else in contextvars) is the parent of the worker's gen_ai.chat span.
            # A bare pool.map would start every call in an empty context: orphaned spans.
            with ThreadPoolExecutor(WORKERS) as pool:
                futures = [pool.submit(contextvars.copy_context().run, describe_view, inst, img, vlm, breaker)
                           for inst, img in views]
                out = [f.result() for f in futures]
        for (inst, _), d in zip(views, out):
            inst.description = d
        if sp is not None:
            sp.set_data("failed", sum(d.error is not None for d in out))
            sp.set_data("retried", sum(d.attempt > 1 and d.error is None for d in out))
            sp.set_data("offline", bool(offline))
            sp.set_data("wall_ms", round((time.perf_counter() - t0) * 1000, 1))
            for k, v in getattr(vlm, "usage", {}).items():
                sp.set_data(k, v)
    return out


def describe_added(assocs, image: np.ndarray, vlm=None) -> list[ViewDescription]:
    """Roommate plan task 3: words for what this pass ADDED, from the frame its masks are on
    (segment.label_map_objects put them there). Everything else keeps the words it has --
    roomctl/publish carries an object's last raw_description and vlm_model forward -- so a
    quiet room costs no VLM call. A view already described, or with nothing to crop, is
    skipped. Sets each view's description (merge.object_fields then reports the words and
    the model that wrote them)."""
    from associate import ADDED

    views = [(v, image) for a in assocs if a.verdict == ADDED and a.obj is not None
             for v in a.obj.views if v.mask is not None and v.description is None]
    return describe(views, vlm)


def describe_capture(capture_dir, segmenter=None, vlm=None, cams=None) -> dict:
    """One RealSense capture folder (Sarah's collector, docs/27): every camera in it through the
    real chain -- depth.RealSenseDepth -> segment.run -> describe -- and back per camera:
    {cam: [(Instance, ViewDescription)]}. Camera frame: no cross-camera merge without extrinsics."""
    import depth
    import segment

    d = Path(capture_dir)
    cams = cams or sorted(p.name[: -len("_color.png")] for p in d.glob("*_color.png"))
    segmenter = segmenter or segment.YoloSegmenter()
    per_cam, views = {}, []
    for cam in cams:
        xyz, valid, img = depth.RealSenseDepth(cam).observe(depth.RealSenseFrame.load(d, cam))
        instances, _ = segment.run(xyz, valid, img, cam, segmenter=segmenter)
        per_cam[cam] = instances
        views += [(i, img) for i in instances]
    describe(views, vlm=vlm)
    return {cam: [(i, i.description) for i in insts] for cam, insts in per_cam.items()}


def main() -> int:
    import argparse

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description="VLM descriptions for a RealSense capture folder, per camera.")
    ap.add_argument("capture_dir", help="session_*/capture_NNNN from depth-camera/data_collect.py")
    ap.add_argument("--cam", action="append", help="d415 / d435 (default: every *_color.png in the folder)")
    a = ap.parse_args()
    obs.init("laptop")
    with obs.transaction("perception.describe", f"describe {Path(a.capture_dir).name}"):
        with obs.capture_scope(Path(a.capture_dir).name) as link:
            out = describe_capture(a.capture_dir, cams=a.cam)
    obs.flush(10)
    for cam, pairs in out.items():
        print(f"== {cam}: {len(pairs)} instances")
        for inst, d in pairs:
            c = inst.centroid
            print(f"  {inst.label:<14} {len(inst.points):>6} pts  camera-frame ({c[0]:+.2f}, {c[1]:+.2f}, {c[2]:.2f}) m"
                  f"  colour {inst.color}")
            print(f"      {d.text!r}" if d.text else f"      (no description: {d.error})")
    if link.get("sentry_trace_id"):
        print("trace:", link["sentry_trace_id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
