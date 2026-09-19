#!/usr/bin/env python3
"""Prove the laptop's critical path runs with NO internet.

Blocks every non-loopback socket in this process, then imports the stack, loads each
pre-downloaded YOLO weight by bare name (exactly how BB's examples do it), runs
stereo -> cloud -> plane removal on synthetic data, and writes a Rerun .rrd.

Anything that quietly reaches for the network -- a lazy weight download, a runtime
`pip install`, a font fetch -- fails here, tonight, instead of at the venue.

    .venv/bin/python scripts/check_offline.py

The block is Python-level. Native code with its own sockets can slip past it, so the
real test is still: unplug the router's WAN cable and run the demo.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BB_WEIGHTS = ("yolo11n.pt", "yolo11s-seg.pt")  # what BB's example_yolo/_segmentation load

# ── block the network ──────────────────────────────────────────────────────────
attempts: list[str] = []


def _is_local(host) -> bool:
    if host in (None, "", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect


def _getaddrinfo(host, *args, **kwargs):
    host = host.decode() if isinstance(host, bytes) else host
    if not _is_local(host):
        attempts.append(host)
        raise socket.gaierror(socket.EAI_NONAME, f"blocked by check_offline: {host}")
    return _real_getaddrinfo(host, *args, **kwargs)


def _connect(self, address):
    if isinstance(address, tuple) and not _is_local(address[0]):
        attempts.append(address[0])
        raise OSError(f"blocked by check_offline: {address[0]}")
    return _real_connect(self, address)


socket.getaddrinfo = _getaddrinfo
socket.socket.connect = _connect
os.environ.update(YOLO_OFFLINE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")

# ── checks ─────────────────────────────────────────────────────────────────────
results: list[tuple[str, str, str, float]] = []


def run(name, fn):
    n0, t0 = len(attempts), time.perf_counter()
    try:
        status, detail = "ok", fn() or ""
    except Exception as e:  # noqa: BLE001 -- report every failure, keep going
        status, detail = "FAIL", f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
    tried = sorted(set(attempts[n0:]))
    if tried and status == "ok":
        status = "warn"
        detail += f"  [tried network: {', '.join(tried)}]"
    results.append((status, name, detail, time.perf_counter() - t0))


def check_config():
    cfg = os.environ.get("YOLO_CONFIG_DIR")
    if not cfg:
        raise RuntimeError("YOLO_CONFIG_DIR unset -- run with the bootstrap venv (.venv/bin/python)")
    from ultralytics.utils import SETTINGS

    weights = Path(SETTINGS["weights_dir"])
    if not weights.is_absolute() or not weights.is_dir():
        raise RuntimeError(f"ultralytics weights_dir is not an existing absolute dir: {weights}")
    return f"weights_dir={weights}"


def check_imports():
    import cv2, numpy, scipy, open3d, rerun, torch, ultralytics, sentry_sdk  # noqa: E401,F401
    import elasticsearch, fastapi, yaml, dotenv, boto3, openai, websockets  # noqa: E401,F401

    mps = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    return (f"cv2 {cv2.__version__} · open3d {open3d.__version__} · rerun {rerun.__version__} · "
            f"torch {torch.__version__} ({mps}) · ultralytics {ultralytics.__version__} · "
            f"sentry {sentry_sdk.VERSION}")


def check_stereo():
    import cv2
    import numpy as np

    if int(cv2.__version__.split(".")[0]) != 4:
        raise RuntimeError(f"OpenCV {cv2.__version__}; BB's depth code expects 4.x")
    assert callable(cv2.fisheye.initUndistortRectifyMap)
    rng = np.random.default_rng(0)
    left = rng.integers(0, 255, (240, 320), dtype=np.uint8)
    right = np.roll(left, -8, axis=1)  # every pixel at disparity 8
    sgbm = cv2.StereoSGBM_create(minDisparity=0, numDisparities=32, blockSize=5)
    disp = sgbm.compute(left, right).astype(np.float32) / 16.0
    valid = disp > 0
    med = float(np.median(disp[valid]))
    if abs(med - 8) > 0.5:
        raise RuntimeError(f"SGBM median disparity {med:.2f}, expected 8")
    Q = np.float32([[1, 0, 0, -160], [0, 1, 0, -120], [0, 0, 0, 200], [0, 0, 1 / 60, 0]])
    xyz = cv2.reprojectImageTo3D(disp, Q)
    assert xyz.shape == (240, 320, 3), xyz.shape
    return f"SGBM disparity {med:.2f} px on synthetic pair, xyz {xyz.shape}"


def check_open3d():
    import numpy as np
    import open3d as o3d

    rng = np.random.default_rng(0)
    table = np.c_[rng.uniform(-1, 1, (4000, 2)), rng.normal(0, 0.002, 4000)]
    mug = rng.normal([0.3, 0.2, 0.06], 0.02, (600, 3))
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.r_[table, mug]))
    _, inliers = pcd.segment_plane(distance_threshold=0.01, ransac_n=3, num_iterations=200)
    objects = pcd.select_by_index(inliers, invert=True)
    labels = np.asarray(objects.cluster_dbscan(eps=0.05, min_points=20))
    n = int(labels.max()) + 1 if labels.size else 0
    if not (len(inliers) > 3800 and n == 1):
        raise RuntimeError(f"plane inliers {len(inliers)}, clusters {n}; expected >3800 and 1")
    return f"plane {len(inliers)} pts removed, {n} object cluster"


def check_rerun(tmp: Path):
    import numpy as np
    import rerun as rr

    path = tmp / "check.rrd"
    rec = rr.RecordingStream("gitspace_check")
    rec.save(path)
    rec.log("points", rr.Points3D(np.random.default_rng(0).uniform(-1, 1, (1000, 3))))
    rec.flush()
    rec.disconnect()
    size = path.stat().st_size
    if size < 1000:
        raise RuntimeError(f".rrd is only {size} bytes")
    return f"wrote {size // 1024} KB .rrd"


def check_yolo(name: str):
    from ultralytics import YOLO
    from ultralytics.utils import ASSETS

    model = YOLO(name)  # bare name, cwd is an empty temp dir -> must resolve from weights_dir
    t0 = time.perf_counter()
    r = model.predict(ASSETS / "bus.jpg", verbose=False)[0]
    ms = (time.perf_counter() - t0) * 1000
    r.plot()
    n = len(r.boxes)
    if n == 0:
        raise RuntimeError("no detections on bus.jpg")
    masks = f", {len(r.masks)} masks" if "-seg" in name and r.masks is not None else ""
    return f"{n} detections{masks} on bus.jpg, {ms:.0f} ms (first call)"


def check_sam3():
    import clip  # noqa: F401 -- ultralytics pip-installs this at runtime if it's missing

    return "sam3.pt present, CLIP tokenizer importable"


def check_git():
    out = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="gitspace-offline-") as d:
        tmp = Path(d)
        os.chdir(tmp)
        run("config", check_config)
        run("imports", check_imports)
        run("opencv stereo", check_stereo)
        run("open3d plane+dbscan", check_open3d)
        run("rerun .rrd", lambda: check_rerun(tmp))
        run("git", check_git)

        weights_dir = None
        try:
            from ultralytics.utils import SETTINGS

            weights_dir = Path(SETTINGS["weights_dir"])
        except Exception:  # noqa: BLE001 -- config check already reported it
            pass
        present = sorted(p.name for p in weights_dir.glob("yolo*.pt")) if weights_dir else []
        for name in BB_WEIGHTS:
            if name not in present:
                results.append(("FAIL", name, "missing -- BB's examples load this by bare name", 0.0))
        for name in present:
            run(name, lambda n=name: check_yolo(n))
        if weights_dir and (weights_dir / "sam3.pt").exists():
            run("sam3", check_sam3)

    width = max(len(r[1]) for r in results)
    for status, name, detail, secs in results:
        mark = {"ok": "\033[32m ok \033[0m", "warn": "\033[33mwarn\033[0m"}.get(status, "\033[31mFAIL\033[0m")
        print(f"  [{mark}] {name:<{width}}  {detail}  ({secs:.1f}s)")
    failed = sum(r[0] == "FAIL" for r in results)
    warned = sum(r[0] == "warn" for r in results)
    print(f"\n  offline check: {len(results) - failed - warned} ok, {warned} warn, {failed} FAIL"
          + (f"  (blocked hosts: {', '.join(sorted(set(attempts)))})" if attempts else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
