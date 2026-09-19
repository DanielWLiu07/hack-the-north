#!/usr/bin/env python3
"""bbos_map.py — the room model the ROBOT already builds, brought to the laptop. Do not rebuild it.

Bracket Bot's `bbos` runs a `mapping` daemon: it "pairs every depth frame with its visual odometry pose, and computes a
3d map of the environment", and publishes it in shared memory as `mapping.voxels` — 3 cm voxels with world coordinates,
the camera's colour, and its OWN floor / not-floor label per voxel — plus `slam.pose`, the robot's position in that same
frame. That is a fused, multi-view, pose-registered model of the room. Our single-view stereo cloud is none of those: it
sees one direction, and with no pose it cannot survive the robot turning (measured: agreement between captures fell from
90 % to 40-60 % the moment the robot rotated). This reads theirs instead.

    python scripts/bbos_map.py pull                  one snapshot of the map -> ~/.cache/gitspace/maps/<time>/ {map.npz, map.ply,
                                                     objects.json, map.png}.  ~320 KB over ssh, ~2 s
    python scripts/bbos_map.py objects [DIR]         separate the things standing in the room (newest snapshot by default)
    python scripts/bbos_map.py diff OLD_DIR NEW_DIR  what APPEARED and what is GONE between two snapshots, as objects
    python scripts/bbos_map.py scan --repo ROOM      THE PRODUCT PATH: this map -> perception/bb_source.scan_into_bb -> the room
                                                     repo's working tree (associate, settle, serialize, voxelize, publish staging);
                                                     `room commit --no-scan` then publishes it. --frame adds the head image, so
                                                     objects get names and words
    python scripts/bbos_map.py frame-check           does the map line up with the camera? projects the map's objects into a
                                                     simultaneous head frame under each yaw convention -> a picture to LOOK at

`objects` / `diff` here are EVIDENCE tools (what is in the map, what changed). OBJECTS IN THE ROOM REPO come from
perception/bb_source, which already owns candidates -> associate -> serialize for BB's voxel map: MapSource below makes
`mapping.voxels` a second mirror for that same function (the first is bbapps/nav's /heavy stream, roomctl.bb_nav), so
there is one pipeline, not two. Registration for the demo is IDENTITY: the room frame IS the SLAM world frame of the
current map generation, recorded with it.

TRANSPORT: `GET /map/voxels` on robot/server.py (docs/16) — an .npz of coords / colors / labels + a JSON `meta` with the
robot's pose from slam.pose; read on demand on the robot and cached 2 s, so asking costs it one 36 MB shared-memory read at
most every two seconds however many ask. If that route is missing (an older robot/ on the robot), it falls back to the
first transport this had: a 20-line READ-ONLY reader piped over ssh and run with bbos's own interpreter, exactly as their
apps read the topic (`Reader("mapping.voxels", keeptime=False)`). Either way nothing of theirs is written or restarted.

HOW OBJECTS ARE SEPARATED. bbos already says which voxels are not floor (label 1). Those, at least 9 cm up (three voxels:
clear of floor speckle), are clustered in 3D with a 6.5 cm neighbourhood — two voxels — so things that do not touch come
apart. A cluster under 25 voxels (a fist) is dropped; a long thin one (> 1.6 m by < 0.45 m) is a WALL, reported but not an
object. Things that physically touch (a backpack against a table leg, people at a table) are one instance: geometry cannot
split what is not separated, and this does not pretend to.
HOW A DIFF WORKS. Both snapshots are in the robot's SLAM world frame, so they can be compared even though the robot moved
between them — the thing our own captures could not do. APPEARED = not-floor voxels of NEW with nothing of OLD within 1.5
voxels, clustered as above; GONE is the same the other way. Only where BOTH snapshots observed the space: a region the old
map had never seen is "unknown", not "appeared".
LIMITS, measured or stated by bbos: 3 cm voxels; the map is capped at 1.3 m high; a flat thing (a snack bag, 3 cm) is
below it; glass and mirrors map badly; the frame resets if SLAM re-initialises (a robot reboot) — a diff across a reset is
meaningless, and `pull` records pgo_count and the map origin so that can be noticed.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pi_link  # noqa: E402

MAPS = Path(os.getenv("BBOS_MAP_DIR", "~/.cache/gitspace/maps")).expanduser()
VOXEL_M, MIN_UP_M, EPS_M, MIN_VOXELS = 0.03, 0.09, 0.065, 25
BBOS_PY = "PYTHONPATH=/home/bracketbot/bbos timeout 40 /home/bracketbot/bbos/.venv/bin/python3 -"

READER = r'''
import sys, io, time, numpy as np
from bbos import Reader
def one(topic, wait):
    with Reader(topic, keeptime=False) as r:
        t0 = time.time()
        while not r.ready():
            if time.time() - t0 > wait: return None
            time.sleep(0.02)
        return r.data.copy()
d = one("mapping.voxels", 15)
if d is None: sys.exit("mapping.voxels: no data in 15 s - is bbos's mapping daemon running?")
n = int(d["num_voxels"]); out = dict(coords=np.array(d["coords"][:n], np.float32), colors=np.array(d["colors"][:n], np.uint8),
    labels=np.array(d["labels"][:n], np.int8), info=np.array(d["info"][:n], np.int32), origin=np.array(d["origin"]),
    robot_pos=np.array(d["robot_pos"]), robot_heading=np.float32(d["robot_heading"]),
    timestamp_ns=np.int64(np.datetime64(d["timestamp"], "ns").astype(np.int64)))
p = one("slam.pose", 3)
if p is not None: out.update(slam_pos=np.array(p["pos"]), slam_quat=np.array(p["quat"]), pgo_count=np.int32(p["pgo_count"]))
h = one("slam.health", 3)
if h is not None: out.update(localized=np.bool_(h["localized"]), vo_lost=np.bool_(h["vo_lost"]), degraded=np.bool_(h["degraded"]))
buf = io.BytesIO(); np.savez_compressed(buf, **out); sys.stdout.buffer.write(buf.getvalue())
'''


def target() -> str:
    env = pi_link.read_env()
    host = env.get("PI_HOST", "")
    if not host:
        raise SystemExit("PI_HOST is not set in .env")
    return f"{os.getenv('PI_USER', env.get('PI_USER', 'bracketbot'))}@{host}"


# ── the map as a SOURCE for perception/bb_source (quacks like roomctl.bb_nav.BBNav / VoxelMirror) ──
class MapMirror:
    """One snapshot of mapping.voxels in roomctl.bb_nav.VoxelMirror's shape: .res, .points() -> ((N,3) float32 BB-world
    metres, (N,3) uint8 rgb), len(). Extra, because bbos gives it for free: .floor — a bool per point, bbos's OWN
    floor label (-1), aligned with points()."""

    def __init__(self, m: dict):
        import numpy as np
        self.res = VOXEL_M
        self._pts = np.ascontiguousarray(m["coords"], np.float32)
        self._rgb = np.ascontiguousarray(m["colors"], np.uint8)
        self.floor = np.asarray(m["labels"]) == -1
        self.version, self.resets = 1, 0

    def __len__(self) -> int:
        return len(self._pts)

    def points(self):
        return self._pts, self._rgb

    def points_room(self, T):
        sys.path.insert(0, str(ROOT))
        from roomctl import frames
        return frames.bb_to_room_array(self._pts, T).astype("float32"), self._rgb


class MapSource:
    """What scan_into_bb(repo, nav, reg) calls `nav`: .mirror, .area (None: every block counts as fresh), .state (the
    robot's pose in the map frame — the eye for occlusion, and the pose camera_frame() needs)."""

    def __init__(self, m: dict):
        from types import SimpleNamespace
        self.mirror, self.area = MapMirror(m), None
        # changes exactly when SLAM re-initialises (a new origin, EITHER axis), never with pgo_count. The same number
        # perception/bb_source.MapSnapshot.map_gen gives for this pull, so the two can be mixed in one scan.
        import zlib
        import numpy as np
        self.map_gen = zlib.crc32(np.round(np.asarray(m["origin"], float), 3).tobytes())
        self.state = SimpleNamespace(ready=bool(m.get("localized", True)), x=float(m["robot_pos"][0]), y=float(m["robot_pos"][1]),
                                     h=float(m["robot_heading"]), status="bbos mapping.voxels", running=False, map_gen=self.map_gen,
                                     t=int(m["timestamp_ns"]) / 1e9)

    @classmethod
    def load(cls, d: Path) -> "MapSource":
        import numpy as np
        return cls(dict(np.load(d / "map.npz")))


def registration(src: MapSource):
    """IDENTITY, for the demo: the room frame is the SLAM world frame of this map generation (master's decision)."""
    from types import SimpleNamespace
    sys.path.insert(0, str(ROOT))
    from roomctl import frames
    try:                                   # bb_source's own: the same identity, plus a refusal if the map's floor is not at z ~ 0
        from perception import bb_source
        return bb_source.identity_registration(src)
    except (ImportError, AttributeError):
        return SimpleNamespace(T=frames.SE2.identity(), map_gen=src.map_gen)


# ── pull ──────────────────────────────────────────────────────────────────────────
def _over_http() -> tuple[dict, int] | None:
    """GET /map/voxels -> (the map in this module's dict shape, bytes). None if the robot has no such route."""
    import http.client
    import numpy as np
    env = pi_link.read_env()
    conn = http.client.HTTPConnection(env.get("PI_HOST", ""), int(env.get("PI_PORT", "8080") or 8080), timeout=20)
    try:
        conn.request("GET", "/map/voxels", headers={"sentry-trace": "00000000000000000000000000000001-0000000000000001-0"})
        r = conn.getresponse()
        body = r.read()
    except OSError:
        return None
    finally:
        conn.close()
    if r.status == 404:
        return None
    if r.status != 200:
        try:
            why = json.loads(body).get("detail") or json.loads(body).get("error")
        except ValueError:
            why = f"HTTP {r.status}"
        raise SystemExit(f"the robot has no map to give right now: {why}  (is bbos's mapping daemon up? has the robot looked around?)")
    z = np.load(io.BytesIO(body))
    meta = json.loads(str(z["meta"]))
    slam = meta.get("slam") or {}
    m = {"coords": z["coords"], "colors": z["colors"], "labels": z["labels"], "origin": np.array(meta["origin"], np.float32),
         "robot_pos": np.array(meta["robot_pos"], np.float32), "robot_heading": np.float32(meta["robot_heading"]),
         "timestamp_ns": np.int64(meta["stamp_ns"]), "pgo_count": np.int32(slam.get("pgo_count", -1)),
         "localized": np.bool_(slam.get("localized", False) and slam.get("ok", False)), "vo_lost": np.bool_(slam.get("vo_lost", False)),
         "degraded": np.bool_(slam.get("degraded", False))}
    return m, len(body)


def pull(out_root: Path = MAPS, say=print) -> Path:
    import numpy as np
    t0 = time.monotonic()
    got = _over_http()
    if got is not None:
        m, nbytes = got
        buf = io.BytesIO(); np.savez_compressed(buf, **m); raw = buf.getvalue(); via = "http"
    else:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ControlPath=none", target(), BBOS_PY],
                           input=READER.encode(), capture_output=True, timeout=70)
        if r.returncode or not r.stdout:
            raise SystemExit(f"could not read the robot's map: {(r.stderr.decode(errors='replace').strip().splitlines() or ['no output'])[-1]}")
        raw, nbytes, via = r.stdout, len(r.stdout), "ssh"
        m = dict(np.load(io.BytesIO(raw)))
    r = type("R", (), {"stdout": raw})()
    when = time.strftime("%Y%m%d-%H%M%S", time.gmtime(int(m["timestamp_ns"]) / 1e9))
    d = out_root / when
    d.mkdir(parents=True, exist_ok=True)
    (d / "map.npz").write_bytes(r.stdout)
    write_ply(d / "map.ply", m["coords"], m["colors"], f"bbos mapping.voxels {when} world frame (SLAM), metres, 3 cm voxels")
    objs = objects_of(m)
    meta = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(m["timestamp_ns"]) / 1e9)), "source": "bbos mapping.voxels + slam.pose",
            "voxels": int(len(m["coords"])), "voxel_m": VOXEL_M, "robot": {"x": round(float(m["robot_pos"][0]), 2), "y": round(float(m["robot_pos"][1]), 2),
            "heading_rad": round(float(m["robot_heading"]), 3)}, "slam": {"localized": bool(m.get("localized", False)), "vo_lost": bool(m.get("vo_lost", False)),
            "pgo_count": int(m.get("pgo_count", -1))}, "map_origin": [round(float(v), 2) for v in m["origin"]],
            "bounds_m": {"min": [round(float(v), 2) for v in m["coords"].min(0)], "max": [round(float(v), 2) for v in m["coords"].max(0)]},
            "objects": objs}
    (d / "objects.json").write_text(json.dumps(meta, indent=1) + "\n")
    render(d / "map.png", m, objs)
    say(f"  map {when}: {meta['voxels']:,} voxels · {len([o for o in objs if o['kind'] == 'object'])} objects, {len([o for o in objs if o['kind'] == 'wall'])} walls · "
        f"robot at ({meta['robot']['x']}, {meta['robot']['y']}) · SLAM {'localized' if meta['slam']['localized'] else 'NOT localized'} · "
        f"{nbytes / 1e3:.0f} KB over {via} in {time.monotonic() - t0:.1f} s\n  -> {d}")
    return d


def write_ply(path: Path, pts, rgb, comment: str) -> None:
    import numpy as np
    vert = np.empty(len(pts), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
    vert["x"], vert["y"], vert["z"] = pts[:, 0], pts[:, 1], pts[:, 2]
    vert["r"], vert["g"], vert["b"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    with open(path, "wb") as f:
        f.write((f"ply\nformat binary_little_endian 1.0\ncomment {comment}\nelement vertex {len(vert)}\nproperty float x\nproperty float y\n"
                 "property float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n").encode())
        f.write(vert.tobytes())


# ── objects ───────────────────────────────────────────────────────────────────────
def _cluster(q):
    sys.path.insert(0, str(ROOT / "perception"))
    import numpy as np
    import cluster                       # perception's own DBSCAN — numpy/scipy only
    return cluster.dbscan(np.asarray(q, np.float64), EPS_M, 5) if len(q) else np.empty(0, int)


def _describe(q, robot_xy) -> dict:
    import numpy as np
    lo, hi = q.min(0), q.max(0)
    size = hi - lo + VOXEL_M
    # a WALL is long and THIN — along ITS OWN axes, not the map's: the glass wall here runs diagonally, so its
    # axis-aligned box is 2.3 x 2.8 m and looks like a room-sized object. Principal axes of the footprint say what it is.
    xy = q[:, :2] - q[:, :2].mean(0)
    ev = np.sqrt(np.maximum(np.linalg.eigvalsh(np.cov(xy.T)) if len(q) > 2 else np.zeros(2), 0)) * 4      # ~full extent (4 sigma)
    wall = ev.max() > 1.6 and ev.min() < 0.45 * max(1.0, ev.max() / 2.5)
    c = q.mean(0)
    return {"kind": "wall" if wall else "object", "centre_m": [round(float(v), 2) for v in c], "size_m": [round(float(v), 2) for v in size],
            "top_m": round(float(hi[2]), 2), "voxels": int(len(q)), "from_robot_m": round(float(np.hypot(c[0] - robot_xy[0], c[1] - robot_xy[1])), 2)}


def objects_of(m: dict, mask=None) -> list[dict]:
    """Not-floor voxels, clear of the floor, clustered in 3D. Sorted by position so the list is stable between snapshots."""
    import numpy as np
    P, L = m["coords"], m["labels"]
    sel = (L == 1) & (P[:, 2] > MIN_UP_M)
    if mask is not None:
        sel &= mask
    Q = P[sel]
    lab = _cluster(Q)
    out = [_describe(Q[lab == k], m["robot_pos"]) for k in sorted(set(lab.tolist()) - {-1}) if (lab == k).sum() >= MIN_VOXELS]
    return sorted(out, key=lambda o: (o["kind"] != "object", round(o["centre_m"][0], 1), round(o["centre_m"][1], 1)))


# ── diff ──────────────────────────────────────────────────────────────────────────
def diff(old: dict, new: dict) -> dict:
    import numpy as np
    from scipy.spatial import cKDTree
    tol = 1.5 * VOXEL_M
    out = {}
    for name, a, b in (("appeared", new, old), ("gone", old, new)):
        A, B = a["coords"], b["coords"]
        seen_by_b = cKDTree(B[:, :2]).query(A[:, :2], k=1)[0] < 0.15            # the OTHER snapshot observed this patch of floor plan at all
        far = cKDTree(B).query(A, k=1)[0] > tol
        out[name] = [o for o in objects_of(a, mask=far & seen_by_b) if o["kind"] == "object"]
        out[f"{name}_unknown_voxels"] = int((far & ~seen_by_b & (a["labels"] == 1)).sum())
    same_frame = np.allclose(old["origin"], new["origin"]) and int(new.get("pgo_count", 0)) >= int(old.get("pgo_count", 0))
    out["same_frame"] = bool(same_frame)
    return out


# ── picture ───────────────────────────────────────────────────────────────────────
def render(path: Path, m: dict, objs: list[dict], changes: dict | None = None) -> None:
    try:
        import numpy as np
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        P, C = m["coords"], m["colors"] / 255.0
        rp, hd = m["robot_pos"], float(m["robot_heading"])
        fig, axs = plt.subplots(1, 2, figsize=(17, 8), facecolor="#0b0c0e")
        for ax in axs:
            ax.set_facecolor("#0b0c0e"); ax.set_aspect("equal"); ax.tick_params(colors="#aaa"); ax.grid(color="#222")
        o = np.argsort(P[:, 2])
        axs[0].scatter(P[o, 0], P[o, 1], c=C[o], s=2.4, linewidths=0)
        axs[0].set_title("the robot's fused map, from above (camera colours)", color="w")
        axs[1].scatter(P[:, 0], P[:, 1], c="#2a2e36", s=2, linewidths=0)
        pal = plt.cm.tab20(np.linspace(0, 1, 20))
        for n, ob in enumerate(objs):
            c, s = ob["centre_m"], ob["size_m"]
            col = "#777777" if ob["kind"] == "wall" else pal[n % 20]
            axs[1].add_patch(plt.Rectangle((c[0] - s[0] / 2, c[1] - s[1] / 2), s[0], s[1], fill=False, ec=col, lw=1.6))
            if ob["kind"] == "object":
                axs[1].annotate(f"{s[0] * 100:.0f}x{s[1] * 100:.0f}x{s[2] * 100:.0f} cm", (c[0], c[1]), color="w", fontsize=7.5, ha="center")
        for kind, col in (("appeared", "#3ddc84"), ("gone", "#ff5d5d")):
            for ob in (changes or {}).get(kind, []):
                c, s = ob["centre_m"], ob["size_m"]
                axs[1].add_patch(plt.Rectangle((c[0] - s[0] / 2 - .05, c[1] - s[1] / 2 - .05), s[0] + .1, s[1] + .1, fill=False, ec=col, lw=3))
                axs[1].annotate(kind.upper(), (c[0], c[1] + s[1] / 2 + .12), color=col, fontsize=10, ha="center", weight="bold")
        for ax in axs:
            ax.plot(rp[0], rp[1], "o", color="#f2a03c", ms=9)
            ax.arrow(rp[0], rp[1], 0.45 * math.cos(hd), 0.45 * math.sin(hd), color="#f2a03c", width=0.03)
        axs[1].set_title("things standing in the room (one box each; grey = wall)" + ("  ·  green = appeared, red = gone" if changes else ""), color="w")
        plt.tight_layout(); plt.savefig(path, dpi=100, facecolor=fig.get_facecolor()); plt.close(fig)
    except Exception as e:  # noqa: BLE001 -- the picture is a convenience
        print(f"  (no picture: {type(e).__name__}: {e})")


def measure_surface(m: dict, box=None) -> dict | None:
    """Find the dominant HORIZONTAL surface above the floor (a table top) in the map, and the box it spans — what
    room.yaml's zone should be for THIS room, measured rather than assumed. box: optional ((x0,y0),(x1,y1)) to look in."""
    import numpy as np
    P = m["coords"][(m["labels"] == 1)]
    P = P[(P[:, 2] > 0.45) & (P[:, 2] < 1.15)]
    if box is not None:
        (x0, y0), (x1, y1) = box
        P = P[(P[:, 0] >= x0) & (P[:, 0] <= x1) & (P[:, 1] >= y0) & (P[:, 1] <= y1)]
    if len(P) < 150:
        return None
    # A table top is an AREA. A wall sliced at any height is a LINE with just as many voxels (the first version of this
    # found the glass wall). So every height layer is split into connected patches, and a patch only counts if it is wide
    # in BOTH of its own principal directions; the winner is the largest such patch over all layers.
    best = None
    for z0 in np.arange(0.45, 1.15, VOXEL_M):
        slab = P[np.abs(P[:, 2] - (z0 + VOXEL_M / 2)) <= VOXEL_M]
        if len(slab) < 80:
            continue
        lab = _cluster(np.c_[slab[:, :2], np.zeros(len(slab))])
        for q in set(lab.tolist()) - {-1}:
            S = slab[lab == q]
            if len(S) < 80:
                continue
            xy = S[:, :2] - S[:, :2].mean(0)
            ext = np.sqrt(np.maximum(np.linalg.eigvalsh(np.cov(xy.T)), 0)) * 4
            if ext.min() < 0.35:                                       # thinner than 35 cm: a wall slice or an edge
                continue
            if best is None or len(S) > len(best[1]):
                best = (float(z0 + VOXEL_M / 2), S)
    if best is None:
        return None
    top, S = best
    lo, hi = S[:, :2].min(0), S[:, :2].max(0)
    return {"surface_z": round(top, 2), "cells": int(len(S)), "area_m2": round(len(S) * VOXEL_M ** 2, 2),
            "zone": {"min": [round(float(lo[0]) - 0.05, 2), round(float(lo[1]) - 0.05, 2), round(top - 0.04, 2)],
                     "max": [round(float(hi[0]) + 0.05, 2), round(float(hi[1]) + 0.05, 2), round(top + 0.55, 2)], "surface": round(top, 2)}}


UNSAMPLED = {"sentry-trace": "00000000000000000000000000000001-0000000000000001-0"}     # a probe is not a product trace
STILL_M, STILL_RAD = 0.05, math.radians(3.0)     # the robot between the two pose reads either side of the shutter


def _robot_get(path: str, timeout: float = 15.0) -> tuple[int, dict, bytes]:
    import http.client
    env = pi_link.read_env()
    conn = http.client.HTTPConnection(env.get("PI_HOST", ""), int(env.get("PI_PORT", "8080") or 8080), timeout=timeout)
    try:
        conn.request("GET", path, headers=UNSAMPLED)
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    except OSError as e:
        return 0, {"error": str(e)}, b""
    finally:
        conn.close()


def _pose_bb() -> dict | None:
    """The robot in bbos's world frame (GET /pose -> pose_bb, from slam.pose). None while SLAM has no pose to give."""
    st, _, body = _robot_get("/pose")
    try:
        bb = json.loads(body).get("pose_bb") if st == 200 else None
    except ValueError:
        bb = None
    return bb if bb and bb.get("ok") else None


def head_frame(reg, camera: str = "cam0", say=print):
    """What the head camera sees NOW, placed in the map's frame: bb_source.camera_frame(image, mount, pose_bb, reg, (f, cx,
    cy)) — the thing that turns `unknown_…` into `mug_…` on the map path. The pose is read on BOTH sides of the frame and
    the frame is refused if the robot moved between them: a label drawn from the wrong pose lands on the wrong object,
    silently. A preview frame, not a capture — no capture id is spent and nothing is recorded. -> (frame, facts) | None."""
    import cv2
    import numpy as np
    sys.path.insert(0, str(ROOT / "perception")); sys.path.insert(0, str(ROOT))
    import bb_source, depth, difference, fuse
    import capture_to_recording as c2r
    p0 = _pose_bb()
    st, hd, jpeg = _robot_get(f"/camera/{camera}.jpg")
    p1 = _pose_bb()
    if st != 200 or jpeg[:2] != b"\xff\xd8":
        say(f"  no head frame (HTTP {st}): objects keep the names they have"); return None
    if p0 is None or p1 is None:
        say("  SLAM has no pose right now (lost, stalled or just restarted): objects keep the names they have"); return None
    moved = math.hypot(p1["x"] - p0["x"], p1["y"] - p0["y"])
    turned = abs(math.atan2(math.sin(p1["heading"] - p0["heading"]), math.cos(p1["heading"] - p0["heading"])))
    if moved > STILL_M or turned > STILL_RAD:
        say(f"  the robot moved {moved * 100:.0f} cm / {math.degrees(turned):.1f} deg around the shutter: frame refused"); return None
    sbs = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    rig = depth.StereoDepth(str(c2r.CALIB))
    xyz, valid, image = rig.observe(sbs)                       # image: the rectified left eye (BGR), the size xyz is
    f, cx, cy = difference.intrinsics(xyz, valid)
    h = math.atan2(math.sin(p0["heading"]) + math.sin(p1["heading"]), math.cos(p0["heading"]) + math.cos(p1["heading"]))
    pose = ((p0["x"] + p1["x"]) / 2, (p0["y"] + p1["y"]) / 2, h)
    frame = bb_source.camera_frame(image, fuse.Mount(**c2r.MOUNT), pose, reg, (f, cx, cy))
    facts = {"pose_bb": {"x": round(pose[0], 3), "y": round(pose[1], 3), "heading": round(h, 4)}, "moved_m": round(moved, 3),
             "turned_deg": round(math.degrees(turned), 2), "intrinsics": [round(f, 1), round(cx, 1), round(cy, 1)],
             "image": list(image.shape[1::-1]), "frame_age_ms": int(hd.get("x-frame-age-ms", -1)), "mount": c2r.MOUNT,
             "stereo": (xyz, valid)}
    return frame, facts


def project(frame, pts):
    """Room points -> (u, v, z) in the frame's image; z <= 0 is behind the lens."""
    import numpy as np
    P = (frame.room_to_cam @ np.c_[pts, np.ones(len(pts))].T).T[:, :3]
    z = P[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = frame.K[0, 0] * P[:, 0] / z + frame.K[0, 2]; v = frame.K[1, 1] * P[:, 1] / z + frame.K[1, 2]
    return u, v, z


AGREE_MIN, AGREE_PX = 0.60, 2000      # a frame names objects only if >= 60 % of >= 2000 shared pixels range within 15 cm


def alignment(frame, src, xyz, valid):
    """The map drawn from the pose we CLAIM the camera has, against what the stereo pair measured from where it really
    is. A wrong heading convention (90 deg), a wrong mount or a stale pose shows here as a gross mismatch — and a frame
    that fails this must not name anything: a label from the wrong pose lands on the wrong object without any error.
    -> (facts, the map as the camera would see it, BGR)."""
    import numpy as np
    pts, cols = src.mirror.points()
    H, W = frame.image.shape[:2]
    u, v, z = project(frame, pts)
    ok = (z > 0.25) & (u >= 0) & (u < W - 1) & (v >= 0) & (v < H - 1)
    ui, vi, zi, ci = u[ok].astype(int), v[ok].astype(int), z[ok], cols[ok]
    order = np.argsort(-zi)                                     # far first, so near cells are painted over them
    drawn, zmap = np.zeros((H, W, 3), np.uint8), np.full((H, W), np.nan, np.float32)
    for x, y, zz, c in zip(ui[order], vi[order], zi[order], ci[order]):
        r = max(1, int(round(frame.K[0, 0] * VOXEL_M / zz / 2)))
        drawn[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1] = c[::-1]      # the map's colours are RGB; the picture is BGR
        zmap[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1] = zz
    both = valid & np.isfinite(zmap)
    err = np.abs(xyz[..., 2][both] - zmap[both]) if both.any() else np.array([])
    within = float((err < 0.15).mean()) if len(err) else None
    return {"map_cells_in_view": int(ok.sum()), "pixels_with_both": int(both.sum()),
            "range_gap_median_m": round(float(np.median(err)), 3) if len(err) else None,
            "range_within_15cm": round(within, 3) if within is not None else None,
            "aligned": bool(within is not None and both.sum() >= AGREE_PX and within >= AGREE_MIN)}, drawn


def frame_check(d: Path | None = None, say=print) -> int:
    """Does the map line up with the camera? Left: what the head camera sees. Right: the robot's MAP, drawn from the pose
    and mount we claim the camera has. If pose, heading convention and mount are right, the two look like the same room.
    And a number, not only a look: where both have depth, how far apart are the map's range and the stereo range."""
    import cv2
    import numpy as np
    d = d or pull(say=say)
    src = MapSource.load(d); reg = registration(src)
    got = head_frame(reg, say=say)
    if got is None:
        return 1
    frame, facts = got
    xyz, valid = facts.pop("stereo")
    got, drawn = alignment(frame, src, xyz, valid)
    facts.update(got)
    W = frame.image.shape[1]
    pic = np.hstack([frame.image, drawn])
    cv2.putText(pic, "head camera", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.putText(pic, "the robot's map, from the claimed pose", (W + 8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    out = d / "frame_check.jpg"
    cv2.imwrite(str(out), pic); (d / "frame_check.json").write_text(json.dumps(facts, indent=1))
    say(f"  frame-check: {facts['map_cells_in_view']:,} map cells in view · where both have depth ({facts['pixels_with_both']:,} px) the "
        f"ranges differ by {facts['range_gap_median_m']} m median, {facts['range_within_15cm']} within 15 cm  =>  "
        f"{'ALIGNED: this frame may name objects' if facts['aligned'] else 'NOT ALIGNED: no names from this frame'}\n  -> {out}")
    return 0 if facts["aligned"] else 1


def scan(repo: Path, d: Path | None = None, with_frame: bool = False) -> int:
    """This map -> perception/bb_source.scan_into_bb -> the room repo's working tree. One pipeline: theirs."""
    sys.path.insert(0, str(ROOT / "perception")); sys.path.insert(0, str(ROOT))
    import bb_source
    d = d or pull()
    src = MapSource.load(d); reg = registration(src)
    got = head_frame(reg) if with_frame else None
    frame = None
    if got:
        facts = got[1]
        xyz, valid = facts.pop("stereo")
        facts.update(alignment(got[0], src, xyz, valid)[0])
        (d / "frame.json").write_text(json.dumps(facts, indent=1))
        if facts["aligned"]:
            frame = got[0]
        else:
            print(f"  the head frame does NOT line up with the map ({facts['range_within_15cm']} of {facts['pixels_with_both']:,} shared "
                  f"pixels within 15 cm; need {AGREE_MIN} of {AGREE_PX:,}): no names taken from it.  bbos_map.py frame-check shows why")
    res = bb_source.scan_into_bb(repo, src, reg, frame=frame, capture_id=f"bbmap_{d.name.replace('-', '')}")
    print(f"  scan_into_bb: {res}" + ("" if frame is not None or not with_frame else "   (no frame: names unchanged)"))
    return 0


def newest() -> Path:
    ds = sorted(p for p in MAPS.glob("*") if (p / "map.npz").is_file()) if MAPS.exists() else []
    if not ds:
        raise SystemExit("no map snapshot yet.  python scripts/bbos_map.py pull")
    return ds[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("pull")
    p = sub.add_parser("objects"); p.add_argument("dir", nargs="?", type=Path)
    p = sub.add_parser("diff"); p.add_argument("old", type=Path); p.add_argument("new", type=Path)
    p = sub.add_parser("scan"); p.add_argument("--repo", type=Path, required=True); p.add_argument("--dir", type=Path, help="an existing snapshot (default: pull a new one)")
    p.add_argument("--frame", action="store_true", help="add the head camera's view, so new objects get names (needs a SLAM pose; only meaningful with a map pulled NOW)")
    p = sub.add_parser("frame-check"); p.add_argument("dir", nargs="?", type=Path)
    p = sub.add_parser("surface"); p.add_argument("dir", nargs="?", type=Path)
    a = ap.parse_args()
    if a.verb == "scan":
        return scan(a.repo.expanduser(), a.dir.expanduser() if a.dir else None, with_frame=a.frame)
    if a.verb == "frame-check":
        return frame_check(a.dir.expanduser() if a.dir else None)
    if a.verb == "surface":
        import numpy as np
        d = (a.dir or newest()).expanduser()
        r = measure_surface(dict(np.load(d / "map.npz")))
        print(json.dumps(r, indent=1) if r else "no horizontal surface between 0.45 and 1.15 m in this map")
        return 0 if r else 1
    import numpy as np
    if a.verb == "pull":
        d = pull()
        a.verb, a.dir = "objects", d
    if a.verb == "objects":
        d = (a.dir or newest()).expanduser()
        meta = json.loads((d / "objects.json").read_text())
        for n, o in enumerate(meta["objects"]):
            print(f"   {n:>2} {o['kind']:6s} at ({o['centre_m'][0]:+.2f}, {o['centre_m'][1]:+.2f})  {o['size_m'][0] * 100:>4.0f} x {o['size_m'][1] * 100:>4.0f} x {o['size_m'][2] * 100:>4.0f} cm"
                  f"  top {o['top_m']:.2f} m  {o['voxels']:>5} voxels  {o['from_robot_m']:.1f} m from the robot")
        print(f"  picture: {d / 'map.png'}   model: {d / 'map.ply'}")
        return 0
    old, new = dict(np.load(a.old.expanduser() / "map.npz")), dict(np.load(a.new.expanduser() / "map.npz"))
    ch = diff(old, new)
    if not ch["same_frame"]:
        print("  WARNING: the map's origin or SLAM history differs between these snapshots (a robot reboot?) — positions may not be comparable")
    for kind in ("appeared", "gone"):
        print(f"  {kind.upper()}: {len(ch[kind])}" + (f"   (+{ch[kind + '_unknown_voxels']} voxels in space the other snapshot never saw: unknown, not counted)" if ch[kind + "_unknown_voxels"] else ""))
        for o in ch[kind]:
            print(f"      at ({o['centre_m'][0]:+.2f}, {o['centre_m'][1]:+.2f})  {o['size_m'][0] * 100:.0f} x {o['size_m'][1] * 100:.0f} x {o['size_m'][2] * 100:.0f} cm  top {o['top_m']:.2f} m  {o['voxels']} voxels")
    out = a.new.expanduser() / f"diff-from-{a.old.expanduser().name}.png"
    render(out, new, objects_of(new), ch)
    (a.new.expanduser() / f"diff-from-{a.old.expanduser().name}.json").write_text(json.dumps(ch, indent=1) + "\n")
    print(f"  picture: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
