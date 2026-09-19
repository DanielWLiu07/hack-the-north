#!/usr/bin/env python3
"""floor_objects.py — things standing on the floor, segmented in IMAGE SPACE on the aligned height map.

DRAFT: docstring is filled in with measured numbers once the method is settled.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]

# ───────────────── thresholds: each one has a physical reason; see the module docstring ─────────────────
MAX_RANGE_M = 2.2        # ground range from the robot. Past it the floor's own height noise is > 6.5 cm (measured)
FLOOR_BAND_M = 0.15      # |z| inside this is "could be floor" for the floor fit and the noise model
K_SEED = 2.5             # a component needs pixels this many floor-noise sigmas above the floor ...
K_BODY = 1.0             # ... and grows through textured pixels down to this many
K_FREE = 5.0             # above this many sigmas the height alone is evidence: no texture asked for
GRAD_MIN = 6.0           # grey levels / px: image gradient that counts as texture the matcher can lock onto
BLOCK_R = 3              # px: half of depth.py's SGBM block (WINDOW_SIZE 7) — texture supports depth this far away
MIN_SEED_M2 = 0.0008     # apparent area of the pixels above K_SEED: a 2 cm x 4 cm patch
MIN_AREA_M2 = 0.0032     # apparent area of the whole component: 4 cm x 8 cm, the smallest thing worth reporting
UNDER_FLOOR_MIN = 0.5    # share of the strip under its base that must read as floor
LARGE_M = 0.60           # wider or taller than this is "large" (a person, a chair), reported apart
STRUCT_W_M = 1.50        # wider than this is the building
STRUCT_H_M = 1.90        # taller than this is the building
PART_OF_M = 0.25         # a small component this close (3-D) to a large one is its extremity: a foot, a chair leg


def _design(x, y):
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=-1)


def fit_floor(W: np.ndarray, near: np.ndarray):
    """Robust quadratic floor z = f(x, y) through the near-field pixels that could be floor."""
    x, y, z = W[..., 0], W[..., 1], W[..., 2]
    m = near & (np.abs(z) < FLOOR_BAND_M)
    if m.sum() < 5000:
        return np.zeros(6), float("nan")
    A, b = _design(x[m], y[m])[::4], z[m][::4]
    keep = np.ones(len(b), bool)
    coef, s = np.zeros(6), float("nan")
    for _ in range(8):
        coef, *_ = np.linalg.lstsq(A[keep], b[keep], rcond=None)
        res = b - A @ coef
        s = 1.4826 * float(np.median(np.abs(res[keep])))
        keep = np.abs(res) < 2.0 * s
    return coef, s


def noise_model(h, r, flat, max_range):
    """sigma of the floor's own height by ground range, from the floor itself: [(range, sigma)]."""
    edges = np.arange(0.0, max_range + 1e-6, 0.2)
    bins = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = flat & (r >= lo) & (r < hi)
        if m.sum() < 800:
            continue
        v = h[m]
        bins.append(((lo + hi) / 2, 1.4826 * float(np.median(np.abs(v - np.median(v))))))
    return bins or [(1.0, 0.04)]


def find_floor_objects(xyz_world, valid, image, *, cam_origin=(0.0, 0.0, 1.59), focal_px=246.0,
                       max_range=MAX_RANGE_M, k_seed=K_SEED, k_body=K_BODY, k_free=K_FREE, grad_min=GRAD_MIN,
                       min_seed_m2=MIN_SEED_M2, min_area_m2=MIN_AREA_M2, under_floor_min=UNDER_FLOOR_MIN,
                       block_r=BLOCK_R, large_m=LARGE_M, part_of_m=PART_OF_M, keep_rejected=False, debug=None):
    W = np.asarray(xyz_world, np.float32)
    H_, W_ = valid.shape
    x, y, z = W[..., 0], W[..., 1], W[..., 2]
    r = np.where(valid, np.hypot(x, y), np.inf)
    near = valid & (r < max_range)

    coef, floor_s = fit_floor(W, near)
    fit = (_design(np.nan_to_num(x), np.nan_to_num(y)) @ coef).astype(np.float32)
    hz = np.where(valid, z - np.maximum(fit, 0.0), 0.0).astype(np.float32)         # above the HIGHER of the two floors
    hfit = np.where(valid, z - fit, 0.0)

    grey = cv2.GaussianBlur(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 1.0)
    grad = np.hypot(cv2.Sobel(grey, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(grey, cv2.CV_32F, 0, 1, ksize=3)) / 8.0
    k = 2 * block_r + 1
    textured = cv2.dilate((grad >= grad_min).astype(np.uint8), np.ones((k, k), np.uint8)) > 0

    tall = cv2.dilate((valid & (hz > 0.30)).astype(np.uint8), np.ones((31, 31), np.uint8)) > 0
    flat = near & (np.abs(hfit) < FLOOR_BAND_M) & ~textured & ~tall
    bins = noise_model(hfit, r, flat, max_range)
    sigma = np.interp(np.where(np.isfinite(r), r, 0.0), [b[0] for b in bins], [b[1] for b in bins]).astype(np.float32)

    free = near & (hz > k_free * sigma)
    seed = (near & textured & (hz > k_seed * sigma)) | free
    cand = (near & textured & (hz > k_body * sigma)) | free
    cand = cv2.morphologyEx(cand.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)) > 0
    cand &= valid

    beyond = valid & ~near & (hz > k_body * sigma)                   # the same evidence, past the range limit
    beyond = cv2.dilate(beyond.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    dead = ~valid.any(axis=0)                                         # columns the matcher can never fill
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cand.astype(np.uint8), connectivity=8)
    C = np.asarray(cam_origin, np.float32)
    out = []
    for i in range(1, n):
        x0, y0, w, hh_, area = (int(v) for v in stats[i])
        sl = (slice(y0, y0 + hh_), slice(x0, x0 + w))
        m = lab[sl] == i
        P, hv = W[sl][m], hz[sl][m]
        cx, cy = float(np.median(P[:, 0])), float(np.median(P[:, 1]))
        top, base = float(np.percentile(hv, 95)), float(np.percentile(hv, 2))
        rho = float(np.linalg.norm([cx - C[0], cy - C[1], top / 2 - C[2]]))
        px_m2 = (rho / focal_px) ** 2                                  # what one pixel covers, facing the camera
        nseed = int((m & seed[sl]).sum())
        if nseed * px_m2 < min_seed_m2:
            continue
        sg = float(np.median(sigma[sl][m]))
        ray = np.array([cx - C[0], cy - C[1]]); ray /= max(np.linalg.norm(ray), 1e-6)
        lat = np.array([-ray[1], ray[0]])
        across, along = (P[:, :2] - [cx, cy]) @ lat, (P[:, :2] - [cx, cy]) @ ray
        width = float(np.percentile(across, 95) - np.percentile(across, 5))
        depth_m = float(np.percentile(along, 95) - np.percentile(along, 5))

        # the strip under its base, and the strip over its top, column by column, clear of the matcher's halo
        under, over = [], []
        for c in np.where(m.any(axis=0))[0]:
            rows = np.where(m[:, c])[0]
            lo, hi = y0 + rows.max() + block_r + 1, max(y0 + rows.min() - block_r - 1, 0)
            under.append(hfit[lo: lo + 6, x0 + c][valid[lo: lo + 6, x0 + c]])
            over.append(hz[max(hi - 6, 0): hi, x0 + c][valid[max(hi - 6, 0): hi, x0 + c]])
        under, over = np.concatenate(under), np.concatenate(over)
        under_floor = float((np.abs(under) < 2 * sg).mean()) if len(under) else 0.0
        over_h = float(np.median(over)) if len(over) else 0.0

        border = bool(x0 <= 1 or y0 <= 1 or x0 + w >= W_ - 1 or y0 + hh_ >= H_ - 1 or dead[max(x0 - 2, 0): x0 + w + 2].any())
        tests = {
            "area": area * px_m2 >= min_area_m2,
            "base_on_floor": base <= k_seed * sg,
            "floor_under": under_floor >= under_floor_min,
            "free_standing": over_h <= k_seed * sg,
            "not_border": not border,
            "not_range_cut": not bool((m & beyond[sl]).any()),
        }
        if width > STRUCT_W_M or top > STRUCT_H_M or (top > large_m and not tests["base_on_floor"]):
            kind = "structure"
        elif width > large_m or top > large_m:
            kind = "large"
        else:
            kind = "object" if all(tests.values()) else "rejected"
        out.append({"kind": kind, "centre": [round(cx, 3), round(cy, 3), round(top / 2, 3)], "height_m": round(top, 3), "base_m": round(base, 3),
                    "width_m": round(width, 3), "depth_m": round(depth_m, 3), "pixels": area, "seed_pixels": nseed,
                    "range_m": round(float(np.hypot(cx, cy)), 3), "cam_range_m": round(rho, 3), "area_m2": round(area * px_m2, 5),
                    "sigma_m": round(sg, 4), "under_floor": round(under_floor, 2), "over_h_m": round(over_h, 3),
                    "bbox": [x0, y0, w, hh_], "tests": {k_: bool(v_) for k_, v_ in tests.items()}, "_label": i})

    # a small component within PART_OF_M of a large one is that thing's extremity, not an object of its own
    big = [o for o in out if o["kind"] in ("large", "structure")]
    for o in out:
        if o["kind"] in ("large", "structure"):
            continue
        x0, y0, w, hh_ = o["bbox"]
        g = int(np.ceil(part_of_m / (o["cam_range_m"] / focal_px)))
        sl = (slice(max(y0 - g, 0), y0 + hh_ + g), slice(max(x0 - g, 0), x0 + w + g))
        for b in big:
            nb = lab[sl] == b["_label"]
            if nb.any() and np.min(np.hypot(W[sl][nb][:, 0] - o["centre"][0], W[sl][nb][:, 1] - o["centre"][1])) < part_of_m:
                o["part_of"] = b["_label"]
                o["tests"]["not_part_of_large"] = False
                o["kind"] = "rejected"
                break
        else:
            o["tests"]["not_part_of_large"] = True
    if debug is not None:
        debug.update(h=hz, hfit=hfit, sigma=sigma, textured=textured, cand=cand, seed=seed, labels=lab, floor_coef=coef, floor_s=floor_s, noise=bins)
    out = [o for o in out if keep_rejected or o["kind"] in ("object", "large")]
    for o in out:
        o["mask"] = lab == o.pop("_label")
    return out
