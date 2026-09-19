"""EIS `.jina-clip-v2`: images and text in ONE embedding space -- what visual re-identification
(an object crop matched against every historical description) would stand on. Live: needs the
cluster. The images are synthetic, drawn here, and labelled as such.

    cd elastic && .venv/bin/python -m pytest tests/test_clip_live.py -v -s
"""
from __future__ import annotations

import base64
import math
import struct
import zlib

import numpy as np
import pytest

CLIP = ".jina-clip-v2"
TEXTS = ["a blue square", "a red circle", "a blue ceramic mug", "a claw hammer"]


def png(rgb: np.ndarray) -> str:
    """Minimal PNG encoder (no PIL/cv2 in this venv): 8-bit RGB, one IDAT, base64."""
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].astype(np.uint8).tobytes() for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))  # noqa: E731
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return base64.b64encode(data).decode()


def canvas() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    img = np.full((224, 224, 3), 255, np.uint8)
    yy, xx = np.mgrid[:224, :224]
    return img, yy, xx


def blue_square() -> np.ndarray:
    img, _, _ = canvas()
    img[62:162, 62:162] = (30, 60, 200)
    return img


def red_circle() -> np.ndarray:
    img, yy, xx = canvas()
    img[(yy - 112) ** 2 + (xx - 112) ** 2 < 55 ** 2] = (210, 30, 30)
    return img


def blue_mug() -> np.ndarray:
    """A crude side view: a blue body with a ring handle on the right."""
    img, yy, xx = canvas()
    img[60:180, 60:150] = (40, 70, 160)
    ring = ((yy - 120) ** 2 + (xx - 150) ** 2 < 38 ** 2) & ((yy - 120) ** 2 + (xx - 150) ** 2 > 24 ** 2) & (xx > 150)
    img[ring] = (40, 70, 160)
    return img


def cos(a, b) -> float:
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


@pytest.fixture(scope="module")
def vectors(es):
    images = {"blue square (drawn)": blue_square(), "red circle (drawn)": red_circle(),
              "blue mug (crude drawing)": blue_mug()}
    inputs = [{"content": {"type": "text", "value": t}} for t in TEXTS] + \
             [{"content": {"type": "image", "format": "base64",  # a DATA URL: bare base64 is a 400
                           "value": f"data:image/png;base64,{png(i)}"}} for i in images.values()]
    r = es.options(request_timeout=120).inference.inference(
        task_type="embedding", inference_id=CLIP, body={"input": inputs})
    vecs = [e["embedding"] for e in r["embeddings"]]
    return dict(zip(TEXTS, vecs[:len(TEXTS)])), dict(zip(images, vecs[len(TEXTS):]))


def test_text_and_images_share_one_space(vectors):
    text, images = vectors
    dims = {len(v) for v in [*text.values(), *images.values()]}
    assert dims == {1024}, dims
    print(f"\n  {'image':<26}" + "".join(f"{t:>20}" for t in TEXTS))
    for name, v in images.items():
        print(f"  {name:<26}" + "".join(f"{cos(v, text[t]):>20.3f}" for t in TEXTS))


@pytest.mark.parametrize("image,right,wrong", [("blue square (drawn)", "a blue square", "a red circle"),
                                               ("red circle (drawn)", "a red circle", "a blue square")])
def test_an_image_lands_nearest_its_own_description(vectors, image, right, wrong):
    text, images = vectors
    assert cos(images[image], text[right]) > cos(images[image], text[wrong])
