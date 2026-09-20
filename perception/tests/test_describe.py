"""describe.py with a fake VLM: every view gets its own description and none is reconciled."""
import json
import sys
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import describe  # noqa: E402
from cluster import Instance  # noqa: E402

# what three cameras might plausibly say about one mug (docs/11 "Gap 2")
SAID = {"cam0": "a blue ceramic mug", "cam1": "cup with handle, chipped", "cam2": "cylindrical container, dark"}
TINT = {"cam0": (220, 60, 20), "cam1": (60, 220, 20), "cam2": (20, 60, 220)}   # BGR, one per camera


def _view(cam):
    """A 270x480 left_rect with a tinted disc, and the instance whose mask is that disc."""
    img = np.full((270, 480, 3), 90, np.uint8)
    mask = np.zeros((270, 480), np.uint8)
    cv2.circle(mask, (240, 135), 30, 1, -1)
    img[mask.astype(bool)] = TINT[cam]
    inst = Instance(points=np.zeros((200, 3)), label="cup", source="segment", camera=cam,
                    mask=mask.astype(bool), score=0.9)
    return inst, img


class FakeVLM:
    """Says whatever SAID says for the camera whose tint is at the crop's centre."""
    model = "fake-vlm"

    def __init__(self, fail_first=0):
        self.fail_first, self.calls, self.lock = fail_first, 0, threading.Lock()

    def __call__(self, jpeg: bytes) -> dict:
        with self.lock:
            self.calls += 1
            if self.calls <= self.fail_first:
                raise TimeoutError("vlm timed out")
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        px = img[img.shape[0] // 2, img.shape[1] // 2].astype(int)
        cam = min(TINT, key=lambda c: np.abs(px - TINT[c]).sum())
        return {"label": SAID[cam].split(",")[0], "description": SAID[cam]}


def test_three_views_keep_three_different_descriptions():
    views = [_view(c) for c in ("cam0", "cam1", "cam2")]
    out = describe.describe(views, vlm=FakeVLM())
    assert [d.camera for d in out] == ["cam0", "cam1", "cam2"]
    assert [d.text for d in out] == [SAID["cam0"], SAID["cam1"], SAID["cam2"]]   # all three, disagreeing
    assert len({d.text for d in out}) == 3
    assert all(d.model == "fake-vlm" and d.attempt == 1 and d.error is None for d in out)
    for (inst, _), d in zip(views, out):
        assert inst.description is d                  # travels with its own view


def test_a_failed_attempt_is_retried_and_counted():
    out = describe.describe([_view("cam1")], vlm=FakeVLM(fail_first=1))
    assert out[0].text == SAID["cam1"] and out[0].attempt == 2


def test_a_dead_vlm_records_the_failure_and_the_capture_goes_on():
    views = [_view(c) for c in ("cam0", "cam1")]
    out = describe.describe(views, vlm=FakeVLM(fail_first=99))
    assert len(out) == 2
    assert all(d.text is None and "TimeoutError" in d.error for d in out)


def test_cluster_path_instances_have_no_crop_to_describe():
    d = describe.describe_view(Instance(points=np.zeros((50, 3))), np.zeros((10, 10, 3), np.uint8), FakeVLM())
    assert d.text is None and "no mask" in d.error


def test_crop_dims_everything_but_the_object_and_upscales_small_ones():
    inst, img = _view("cam0")
    c = describe.crop(img, inst.mask)
    assert min(c.shape[:2]) >= describe.MIN_SIDE
    assert tuple(c[c.shape[0] // 2, c.shape[1] // 2]) == TINT["cam0"]
    assert c[2, 2].max() < 90 * describe.DIM + 2                       # the corner is dimmed


def test_openai_request_carries_only_prompt_and_image(monkeypatch):
    """The VLM must not be told the segmenter's label: a hint would make the views agree."""
    sent = {}

    class Responses:
        def create(self, **kw):
            sent.update(kw)
            return type("R", (), {"output_text": json.dumps({"label": "mug", "description": "a mug"})})()

    vlm = describe.OpenAIVLM.__new__(describe.OpenAIVLM)
    vlm.client = type("C", (), {"responses": Responses()})()
    vlm.model, vlm.effort = "gpt-5", "minimal"
    inst, img = _view("cam0")
    d = describe.describe_view(inst, img, vlm)

    assert d.text == "a mug" and d.model == "gpt-5"
    content = sent["input"][0]["content"]
    assert [c["type"] for c in content] == ["input_text", "input_image"]
    assert content[0]["text"] == describe.PROMPT and "cup" not in describe.PROMPT
    assert content[1]["image_url"].startswith("data:image/jpeg;base64,")
    assert sent["text"]["format"]["type"] == "json_schema"
    assert sent["reasoning"] == {"effort": "minimal"}


def _fake_openai(**response):
    """An OpenAIVLM whose client returns `response` as the Responses API object."""
    class Responses:
        def create(self, **kw):
            self.kw = kw
            return type("R", (), response)()

    vlm = describe.OpenAIVLM.__new__(describe.OpenAIVLM)
    vlm.client = type("C", (), {"responses": Responses()})()
    vlm.model, vlm.effort = "gpt-5", "minimal"
    vlm.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    vlm._lock = threading.Lock()
    return vlm


def test_reasoning_budget_is_generous_and_truncation_is_not_a_refusal():
    """gpt-5 spends ~140 reasoning tokens before a two-word caption; at a tight cap the reply is
    valid and EMPTY. That must be recorded as a truncation, never as a description."""
    usage = type("U", (), {"input_tokens": 700, "output_tokens": 40,
                           "output_tokens_details": type("D", (), {"reasoning_tokens": 40})()})()
    vlm = _fake_openai(status="incomplete", output_text="", usage=usage,
                       incomplete_details=type("I", (), {"reason": "max_output_tokens"})())
    inst, img = _view("cam0")
    d = describe.describe_view(inst, img, vlm)
    assert d.text is None and "max_output_tokens" in d.error
    assert vlm.client.responses.kw["max_output_tokens"] >= 1000
    # not retried: the same budget truncates the same way, it would only burn tokens
    assert d.attempt == 1
    assert vlm.usage == {"calls": 1, "input_tokens": 700, "output_tokens": 40, "reasoning_tokens": 40}

    empty = _fake_openai(output_text="")
    d = describe.describe_view(inst, img, empty)
    assert d.text is None and "empty output_text" in d.error


def test_a_small_thing_gets_real_context_and_keeps_it_visible():
    """A crisp packet on the floor 1.3 m out is ~8 x 19 px. PAD is a fraction of the mask's own
    bbox, so it gave that 4 px of surroundings, and DIM took the texture out of what was left:
    the live model answered "unidentifiable object". With CONTEXT_PX of real image and
    DIM_SMALL it answers "small wrapper" (cap_0015, measured)."""
    img = np.full((270, 480, 3), 200, np.uint8)
    mask = np.zeros((270, 480), bool)
    mask[130:140, 240:248] = True                              # 80 px
    c = describe.crop(img, mask)
    around = c[c < 190]
    assert around.size, "the crop is all subject: no context came with it"
    assert abs(float(around.mean()) - 200 * describe.DIM_SMALL) < 8      # dimmed gently
    assert (c >= 190).sum() / c.size < 0.10                              # mostly room, as intended


def test_a_big_thing_keeps_the_tight_crop_and_the_dimming():
    """Dimming is what says WHICH object the answer is about when a crop holds several, so it
    stays for anything big enough to be recognised without help."""
    img = np.full((270, 480, 3), 200, np.uint8)
    mask = np.zeros((270, 480), bool)
    mask[60:200, 120:380] = True                               # 36k px
    c = describe.crop(img, mask)
    around = c[c < 190]
    assert abs(float(around.mean()) - 200 * describe.DIM) < 8
    assert (c >= 190).sum() / c.size > 0.5                     # the subject fills its own crop
