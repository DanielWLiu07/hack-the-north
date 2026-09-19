#!/usr/bin/env python3
"""camera_ingest.py — the laptop-side RECEIVER for camera captures. It did not exist: the repo had a
complete sender (robot/server.py, docs/16-api.md) and one consumer of /stream (telemetry/hub.py), but
nothing that ever took the pixels. This is a STANDALONE process — web/server.py on :8000 is not
touched and never needs a restart: it already serves web/landing/**, so what is written to
web/landing/live/ is immediately at /live/… (and through the public tunnel — see PRIVACY).

    # 1. a machine with the RealSenses runs the EXISTING sender (nothing new to write there):
    #        ROBOT_CAMERAS="cam1=realsense:816612060665:D415,cam2=realsense:938422076694:D435" \\
    #        python -m robot.server --hardware --host 0.0.0.0 --port 8080
    # 2. this machine pulls from it, with the existing protocol (POST /capture {"inline": true}):
    ../.venv/bin/python camera_ingest.py --robot http://<sender-host>:8080             # one capture
    ../.venv/bin/python camera_ingest.py --robot http://<sender-host>:8080 --every 5   # keep pulling
    # or, with no network at all, a folder from Sarah's collector (docs/27):
    ../.venv/bin/python camera_ingest.py --session ~/Downloads/session_20260919_0142

What it writes (default web/landing/live/, served as /live/…):
    latest.json                       the manifest the page reads: what arrived, from where, what it IS
    <capture_id>/<camera>_color.jpg   the colour frame as the sender encoded it
    <capture_id>/<camera>.glb         a POINT CLOUD (glTF POINTS, per-point colour) — three's GLTFLoader reads it

WHAT THIS IS, AND IS NOT. The cloud is depth back-projected through the camera's own intrinsics
(x = (u-ppx)·z/fx, y = (v-ppy)·z/fy): real geometry from a real sensor, in the CAMERA frame. It is
not a gaussian splat, it is not registered to the room (camera->robot extrinsics are Sarah's
calibration, docs/27), and two cameras are two clouds, not one. The manifest says all of that in
`is` / `is_not`, so no page has to guess — and a colour frame alone produces NO cloud, ever.
No intrinsics from the sender (sim / replay) -> no cloud and the reason, unless the operator passes
--hfov-deg knowingly; the manifest then says the intrinsics were assumed.

PRIVACY. live/ holds real frames of a real room, so web/server.py serves /live/* to THIS laptop only: a
loopback peer AND no forwarding header (localonly.py), 403 for everyone else, Cache-Control: no-store. A viewer
on a tunnel or the public site gets no pixels. The deploy scripts do not ship live/.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import math
import shutil
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import httpx
import numpy as np

log = logging.getLogger("gitspace.camera_ingest")
HERE = Path(__file__).resolve().parent
OUT = HERE / "landing" / "live"
MAX_POINTS = 150_000            # per camera: a phone renders this; the full frame is ~300k-900k
MAX_RANGE_M = 6.0               # past this a D4xx is noise (perception/depth.py uses the same idea)
KEEP = 12                       # captures kept on disk
IS = "a point cloud: RealSense depth back-projected through the camera's intrinsics, coloured per point, in the CAMERA frame"
IS_NOT = ["a gaussian splat", "registered to the room (no camera->robot extrinsics applied)", "fused: each camera is its own cloud"]


# ── geometry ───────────────────────────────────────────────────────────────────────────────────
def backproject(depth_mm: np.ndarray, color_bgr: np.ndarray, k: dict) -> tuple[np.ndarray, np.ndarray]:
    """uint16 MILLIMETRES (aligned to colour) + intrinsics -> (xyz float32 metres, camera frame; rgb uint8)."""
    h, w = depth_mm.shape
    if color_bgr.shape[:2] != (h, w):
        color_bgr = cv2.resize(color_bgr, (w, h), interpolation=cv2.INTER_AREA)
    sx, sy = w / float(k.get("w") or w), h / float(k.get("h") or h)        # intrinsics are for the sender's colour size
    z = depth_mm.astype(np.float32) / 1000.0
    ok = (z > 0) & (z < MAX_RANGE_M)
    v, u = np.nonzero(ok)
    z = z[ok]
    x = (u - k["ppx"] * sx) * z / (k["fx"] * sx)
    y = (v - k["ppy"] * sy) * z / (k["fy"] * sy)
    return np.stack([x, y, z], axis=1).astype(np.float32), color_bgr[ok][:, ::-1].copy()


def assumed_intrinsics(w: int, h: int, hfov_deg: float) -> dict:
    f = (w / 2) / math.tan(math.radians(hfov_deg) / 2)
    return {"fx": f, "fy": f, "ppx": w / 2, "ppy": h / 2, "w": w, "h": h, "assumed_from_hfov_deg": hfov_deg}


def thin(xyz: np.ndarray, rgb: np.ndarray, limit: int = MAX_POINTS) -> tuple[np.ndarray, np.ndarray]:
    if len(xyz) <= limit:
        return xyz, rgb
    step = math.ceil(len(xyz) / limit)                  # a stride, not a random sample: same cloud every run
    return xyz[::step], rgb[::step]


def write_glb(path: Path, xyz_cam: np.ndarray, rgb: np.ndarray) -> int:
    """glTF 2.0 binary, one POINTS primitive, POSITION f32 + COLOR_0 u8 RGBA (normalised).
    Camera frame (x right, y DOWN, z forward) -> glTF axes (x right, y UP, -z forward)."""
    pos = (xyz_cam * np.array([1, -1, -1], np.float32)).astype("<f4")
    col = np.concatenate([rgb.astype(np.uint8), np.full((len(rgb), 1), 255, np.uint8)], axis=1)
    blob = pos.tobytes() + col.tobytes()
    blob += b"\0" * (-len(blob) % 4)
    n = len(pos)
    doc = {"asset": {"version": "2.0", "generator": "gitspace web/camera_ingest.py"},
           "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": path.stem}],
           "meshes": [{"primitives": [{"mode": 0, "attributes": {"POSITION": 0, "COLOR_0": 1}}]}],
           "buffers": [{"byteLength": len(blob)}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": n * 12, "target": 34962},
                           {"buffer": 0, "byteOffset": n * 12, "byteLength": n * 4, "target": 34962}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": n, "type": "VEC3",
                          "min": pos.min(axis=0).tolist() if n else [0, 0, 0], "max": pos.max(axis=0).tolist() if n else [0, 0, 0]},
                         {"bufferView": 1, "componentType": 5121, "normalized": True, "count": n, "type": "VEC4"}]}
    head = json.dumps(doc, separators=(",", ":")).encode()
    head += b" " * (-len(head) % 4)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(head) + 8 + len(blob))
                     + struct.pack("<I4s", len(head), b"JSON") + head + struct.pack("<I4s", len(blob), b"BIN\0") + blob)
    return n


# ── one capture from the existing sender ───────────────────────────────────────────────────────
def pull(robot: str, timeout: float = 30.0, client: httpx.Client | None = None) -> dict:
    """POST /capture {"inline": true} (docs/16 §3). Raises RuntimeError with the sender's own words."""
    own = client or httpx.Client(timeout=timeout)
    try:
        r = own.post(f"{robot.rstrip('/')}/capture", json={"inline": True})
    except httpx.HTTPError as e:
        raise RuntimeError(f"could not reach the sender at {robot} ({type(e).__name__})") from None
    finally:
        if client is None:
            own.close()
    try:
        body = r.json()
    except ValueError:
        raise RuntimeError(f"the sender answered HTTP {r.status_code} with something that is not JSON") from None
    if r.status_code >= 400:
        raise RuntimeError(f"{body.get('error', r.status_code)}: {body.get('detail', '')}"[:300])
    try:                                                # sim | replay | hardware — the page must be able to say which
        body["sender_mode"] = httpx.get(f"{robot.rstrip('/')}/healthz", timeout=5).json().get("mode")
    except (httpx.HTTPError, ValueError, AttributeError):
        body["sender_mode"] = None
    return body


def from_robot(cap: dict, out: Path, sender: str, hfov_deg: float | None = None) -> dict:
    cid = cap["capture_id"]
    folder = out / cid
    folder.mkdir(parents=True, exist_ok=True)
    rig = {r.get("camera"): r for r in cap.get("rig") or [] if isinstance(r, dict)}
    shutter: dict[tuple[str, str], dict] = {}
    for f in cap.get("frames") or []:                   # seq 0 is the latch — the shutter instant (docs/22)
        key = (f["camera"], f["kind"])
        if key not in shutter or f["seq"] < shutter[key]["seq"]:
            shutter[key] = f
    cameras = []
    for cam in cap.get("cameras") or sorted({c for c, _ in shutter}):
        info, entry = rig.get(cam) or {}, {"camera": cam, "model": (rig.get(cam) or {}).get("model") or None,
                                           "intrinsics": None, "color": None, "cloud": None, "points": 0, "cloud_reason": None}
        color_f, depth_f = shutter.get((cam, "color")), shutter.get((cam, "depth"))
        color = None
        if color_f and color_f.get("jpeg_b64"):
            raw = base64.b64decode(color_f["jpeg_b64"])
            (folder / f"{cam}_color.jpg").write_bytes(raw)
            entry["color"] = f"{cid}/{cam}_color.jpg"
            color = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if not (depth_f and depth_f.get("png_b64")):
            entry["cloud_reason"] = "the sender sent no depth for this camera — a colour frame alone is a picture, not geometry"
        elif color is None:
            entry["cloud_reason"] = "depth arrived without a colour frame to colour it"
        else:
            depth = cv2.imdecode(np.frombuffer(base64.b64decode(depth_f["png_b64"]), np.uint8), cv2.IMREAD_UNCHANGED)
            k = info.get("intrinsics") or (assumed_intrinsics(depth.shape[1], depth.shape[0], hfov_deg) if hfov_deg else None)
            if depth is None or depth.dtype != np.uint16:
                entry["cloud_reason"] = "the depth payload is not a 16-bit PNG in millimetres (docs/16)"
            elif not k:
                entry["cloud_reason"] = ("the sender reported no intrinsics for this camera (sim / replay do not have them), "
                                         "and depth cannot be back-projected without them")
            else:
                xyz, rgb = thin(*backproject(depth, color, k))
                entry.update(intrinsics=k, cloud=f"{cid}/{cam}.glb", points=write_glb(folder / f"{cam}.glb", xyz, rgb))
        cameras.append(entry)
    return {"capture_id": cid, "at": cap.get("started_at"), "source": "robot", "sender": sender,
            "sender_mode": cap.get("sender_mode"),      # "hardware" is the only value that means real cameras
            "pose": cap.get("pose"), "pose_source": cap.get("pose_source"), "quality_ok": cap.get("quality_ok"),
            "skew_ms": cap.get("skew_ms"), "tilt_rate_max": cap.get("tilt_rate_max"), "coverage": cap.get("coverage"),
            "sentry_trace_id": cap.get("sentry_trace_id"), "cameras": cameras}


def from_session(session: Path, out: Path, which: str | None = None) -> dict:
    """Sarah's collector (docs/27): capture_NNNN/{d415,d435}_{color.png,pointcloud.npy,depth_raw.npy}.
    Her pointcloud.npy is METRES already, one vertex per colour pixel — no intrinsics needed."""
    caps = sorted(p for p in session.glob("capture_*") if p.is_dir()) or ([session] if list(session.glob("*_color.png")) else [])
    if which:
        caps = [p for p in caps if p.name == which]
    if not caps:
        raise RuntimeError(f"no capture_* folder with *_color.png in {session}")
    src = caps[-1]
    cid = f"{session.name}_{src.name}".replace(" ", "_") if src != session else session.name
    folder = out / cid
    folder.mkdir(parents=True, exist_ok=True)
    cameras = []
    for color_png in sorted(src.glob("*_color.png")):
        cam = color_png.name[:-len("_color.png")]
        entry = {"camera": cam, "model": cam.upper(), "intrinsics": None, "color": None, "cloud": None, "points": 0, "cloud_reason": None}
        color = cv2.imread(str(color_png), cv2.IMREAD_COLOR)
        cv2.imwrite(str(folder / f"{cam}_color.jpg"), color, [cv2.IMWRITE_JPEG_QUALITY, 88])
        entry["color"] = f"{cid}/{cam}_color.jpg"
        cloud = src / f"{cam}_pointcloud.npy"
        if not cloud.is_file():
            entry["cloud_reason"] = f"{cloud.name} is not in the folder — a colour frame alone is a picture, not geometry"
        else:
            pts = np.load(cloud).astype(np.float32).reshape(-1, 3)
            h, w = color.shape[:2]
            if len(pts) != h * w:
                entry["cloud_reason"] = f"{cloud.name} has {len(pts)} vertices, not one per {w}x{h} colour pixel, so it cannot be coloured"
            elif np.nanmax(np.abs(pts)) > 50:
                entry["cloud_reason"] = f"{cloud.name} is not in metres (max {np.nanmax(np.abs(pts)):.0f}) — docs/27's unit trap"
            else:
                ok = np.isfinite(pts).all(axis=1) & (pts[:, 2] > 0) & (pts[:, 2] < MAX_RANGE_M)
                xyz, rgb = thin(pts[ok], color.reshape(-1, 3)[ok][:, ::-1].copy())
                entry.update(cloud=f"{cid}/{cam}.glb", points=write_glb(folder / f"{cam}.glb", xyz, rgb))
        cameras.append(entry)
    meta = next((p for p in (src / "metadata.json", session / "metadata.json") if p.is_file()), None)
    at = None
    if meta:
        try:
            at = json.loads(meta.read_text()).get("timestamp")
        except (ValueError, AttributeError):
            at = None
    return {"capture_id": cid, "at": at, "source": "session_folder", "sender": str(session), "sender_mode": "recorded", "pose": None, "pose_source": None,
            "quality_ok": None, "skew_ms": None, "tilt_rate_max": None, "coverage": None, "sentry_trace_id": None, "cameras": cameras}


def publish(manifest: dict, out: Path) -> Path:
    """latest.json, written atomically (the page polls it), and the old captures pruned."""
    manifest = {**manifest, "received_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "is": ("colour frames only — no geometry arrived, so there is no cloud" if not any(c["cloud"] for c in manifest["cameras"])
                       else IS + (" — through ASSUMED pinhole intrinsics (--hfov-deg), so its proportions are approximate"
                                  if any((c["intrinsics"] or {}).get("assumed_from_hfov_deg") for c in manifest["cameras"]) else "")),
                "is_not": IS_NOT, "axes": "glTF: x right, y up, -z forward (camera frame, flipped from x right / y down / z forward)",
                "units": "metres"}
    tmp = out / "latest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=1))
    tmp.replace(out / "latest.json")
    folders = sorted((p for p in out.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
    for old in folders[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    return out / "latest.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--robot", metavar="URL", help="the sender: http://<host>:8080 (robot/server.py)")
    src.add_argument("--session", type=Path, metavar="DIR", help="a folder from Sarah's collector (docs/27)")
    ap.add_argument("--capture", help="with --session: which capture_NNNN (default: the last)")
    ap.add_argument("--every", type=float, metavar="S", help="with --robot: keep pulling, one capture every S seconds")
    ap.add_argument("--hfov-deg", type=float, help="ASSUME pinhole intrinsics from this horizontal FOV when the sender "
                    "reports none (D435 colour ~69, D415 ~65). The manifest records that they were assumed.")
    ap.add_argument("--out", type=Path, default=OUT, help=f"default {OUT.relative_to(HERE)} — served by :8000 at /live/, to this laptop only")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s  %(message)s")
    a.out.mkdir(parents=True, exist_ok=True)
    if a.out.resolve() == OUT.resolve():
        log.info("writing to %s: served at /live/ by :8000, to this laptop only", a.out)
    while True:
        try:
            m = from_session(a.session.expanduser(), a.out, a.capture) if a.session else from_robot(pull(a.robot), a.out, a.robot, a.hfov_deg)
            publish(m, a.out)
            for c in m["cameras"]:
                log.info("%s %s: %s", m["capture_id"], c["camera"], f"{c['points']} points -> {c['cloud']}" if c["cloud"] else f"NO CLOUD — {c['cloud_reason']}")
        except RuntimeError as e:
            log.error("%s", e)
            if not a.every:
                return 1
        if not a.every or a.session:
            return 0
        time.sleep(max(1.0, a.every))


if __name__ == "__main__":
    sys.exit(main())
