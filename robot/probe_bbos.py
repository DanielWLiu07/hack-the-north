#!/usr/bin/env python3
"""robot/probe_bbos.py — MEASURE what bbos publishes, before anything is built on it. Read-only.

Self-contained on purpose (numpy + bbos only), so it runs on the robot without deploying anything:

    ssh bracketbot@<robot> 'PYTHONPATH=/home/bracketbot/bbos /home/bracketbot/bbos/.venv/bin/python3 -' < robot/probe_bbos.py
    ... python3 - camera.depth camera.rect camera.points camera.head.jpeg slam.pose     # or name the topics

It exists because bbos's own comments cannot be taken on trust — registry.py documents imu rpy as
radians and the daemon publishes degrees (robot/bbos.py) — and because the questions it answers
are the ones that fail SILENTLY when guessed (docs/20 Fact 1: a depth unit wrong by 1000x throws
nothing):

    camera.depth   dtype, shape, UNITS — derived by comparing against camera.points' own z, not
                   read off a name; the share of valid pixels (that is `coverage`)
    alignment      which image depth is pixel-aligned with: its shape against camera.rect and
                   against HALF of the side-by-side head frame
    pairing        how far each topic's timestamp lags camera.head.jpeg — depth is computed from a
                   frame, so it is older than the colour published beside it
    intrinsics     whatever Config("camera") / Config("depth") expose (fx fy ppx ppy, Q, baseline)

Reads with keeptime=False (bbos's global Loop is never entered); each Reader claims one slot of
bbos's timing table and frees it on exit. Commands nothing, writes nothing.
"""
import sys
import time

import numpy as np

DEFAULT = ["camera.head.jpeg", "camera.rect", "camera.depth", "camera.points", "slam.pose", "slam.health"]
SECONDS = 2.0


def depth_units(depth: np.ndarray, points_z: np.ndarray | None) -> str:
    """What one count of `depth` is, in metres — from the data. With camera.points available the
    answer is the ratio of medians (points are believed to be metres ONLY if their own range is
    room-sized); without it, only what the magnitudes allow, and it says so."""
    d = np.asarray(depth, dtype=np.float64)
    d = d[np.isfinite(d) & (d > 0)]
    if d.size == 0:
        return "no valid depth pixels: cannot tell"
    med = float(np.median(d))
    if points_z is not None:
        z = np.asarray(points_z, dtype=np.float64)
        z = z[np.isfinite(z) & (z > 0)]
        if z.size and 0.05 < float(np.median(z)) < 50:
            k = float(np.median(z)) / med
            name = {1.0: "METRES", 0.001: "MILLIMETRES", 0.01: "CENTIMETRES"}.get(
                min((1.0, 0.001, 0.01), key=lambda u: abs(np.log(k / u))), "?")
            return (f"1 count = {k:.6g} m  => {name}  (median depth {med:.4g}, median points z {np.median(z):.4g} m; "
                    f"MEASURED against camera.points)")
    guess = "METRES" if 0.05 < med < 50 else "MILLIMETRES" if 50 < med < 50000 else "unknown (disparity?)"
    return f"median {med:.4g} -> probably {guess}; NOT cross-checked (no usable camera.points). Do not build on this"


def describe(name: str, value) -> str:
    a = np.asarray(value)
    if a.dtype.kind in "fiu" and a.size > 1:
        f = a.astype(np.float64)
        ok = np.isfinite(f)
        nz = f[ok & (f != 0)]
        rng = f"nonzero {nz.size / a.size:.1%}, min {nz.min():.4g} median {np.median(nz):.4g} max {nz.max():.4g}" if nz.size else "all zero"
        return f"{name}: {a.dtype} {a.shape}  {rng}"
    return f"{name}: {a.dtype} {a.shape}  = {a.tolist() if a.size <= 12 else '...'}"


def main(topics: list[str]) -> int:
    from bbos import Reader
    readers = {t: Reader(t, keeptime=False) for t in topics}
    last, stamps = {}, {t: [] for t in topics}
    end = time.monotonic() + SECONDS
    while time.monotonic() < end:
        for t, r in readers.items():
            if r.ready():
                last[t] = r.data
                stamps[t].append(int(r.data["timestamp"].view("i8")))
        time.sleep(0.004)
    ref = stamps.get("camera.head.jpeg") or []
    for t in topics:
        if t not in last:
            print(f"\n[{t}]  NO WRITER / nothing new in {SECONDS:g} s")
            continue
        d = last[t]
        lag = f", newest is {(ref[-1] - stamps[t][-1]) / 1e6:+.0f} ms behind camera.head.jpeg" if ref and t != "camera.head.jpeg" else ""
        print(f"\n[{t}]  {len(stamps[t]) / SECONDS:.0f} Hz{lag}")
        for k in d.dtype.names:
            if k != "timestamp":
                print("   " + describe(k, d[k]))
    if "camera.depth" in last:
        dd = last["camera.depth"]
        field = next((k for k in dd.dtype.names if k != "timestamp" and np.asarray(dd[k]).ndim >= 2), None)
        if field:
            depth = np.asarray(dd[field])
            z = None
            if "camera.points" in last:
                pd = last["camera.points"]
                pf = next((k for k in pd.dtype.names if k != "timestamp" and np.asarray(pd[k]).shape[-1:] == (3,)), None)
                z = np.asarray(pd[pf])[..., 2] if pf else None
            print(f"\nDEPTH UNITS   {depth_units(depth, z)}")
            print(f"DEPTH SHAPE   {depth.shape} {depth.dtype}; valid (finite, >0): {float((np.isfinite(depth.astype(float)) & (depth > 0)).mean()):.1%}  <- this is `coverage`")
            for other in ("camera.rect", "camera.head.rgb"):
                if other in last:
                    shapes = {k: np.asarray(last[other][k]).shape for k in last[other].dtype.names if k != "timestamp"}
                    print(f"ALIGNMENT     {other} fields {shapes}  — aligned with depth iff an image here is {depth.shape[:2]}")
    for r in readers.values():
        r.__exit__(None, None, None)
    print("\nINTRINSICS    (whatever bbos exposes; read-only)")
    try:
        from bbos import Config
        for daemon in ("camera", "depth"):
            try:
                c = Config(daemon)
                keys = [k for k in dir(c) if not k.startswith("_")]
                print(f"   Config({daemon!r}): {keys}")
                for k in keys:
                    v = getattr(c, k)
                    if any(s in k.lower() for s in ("fx", "fy", "cx", "cy", "pp", "focal", "baseline", "width", "height", "calib", "k", "q")) and not callable(v):
                        print(f"      {k} = {np.round(v, 4).tolist() if isinstance(v, np.ndarray) else v}")
            except Exception as e:  # noqa: BLE001
                print(f"   Config({daemon!r}): {type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        print(f"   bbos.Config unavailable: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or DEFAULT))
