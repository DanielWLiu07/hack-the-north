#!/usr/bin/env python3
"""capture_to_recording.py — a LIVE capture from the robot, written as a perception Recording.

The piece that was missing between the two halves that existed: robot/server.py can take a gated
capture, perception/pipeline.py can turn a recording into the room (depth -> fuse -> segment ->
voxels -> the room repo, where `git diff` IS "what changed"). Nothing turned one into the other;
the only recordings were perception/synthetic.py's.

    python scripts/capture_to_recording.py                     # one capture -> ~/.cache/gitspace/recordings/<id>/
    python scripts/capture_to_recording.py --out DIR --n 3     # three, a few seconds apart
    python perception/pipeline.py <that dir> --repo <room repo>      # then: the room

What it writes — exactly what perception.pipeline.load_recording reads (perception/synthetic.py's layout):
    capture.json   capture_id · at · pose {x,z,yaw} · skew_ms · tilt_rate_max · source "robot"
                   frames [{camera, file}] · rig {camera: {calib, mount}}   (+ extras, see below)
    cam0.jpg       the head camera's side-by-side stereo pair, THE ROBOT'S BYTES (no re-encode)
    cam0.yaml      this robot's stereo calibration (perception/calib/, copied from the robot's bbos depth daemon)
    room/          room.yaml · .roomignore · anchors/  — so a fresh room repo can be initialised from it

WHAT IS MEASURED AND WHAT IS NOT — written into capture.json so nobody downstream has to guess:
  mount     pitch 33 deg down, 1.55 m up: bbos's own Config("depth") for THIS robot (pitch_deg, height_m), not
            BB's example placeholders. Their roll of -1 deg has no field in fuse.Mount and is NOT applied.
  frame     2560x960 on this robot (bbos cam_head), not the 2560x720 the docs assume.
  pose      `pose_source` is copied from the robot. "none" = the robot did not measure a pose and {0,0,0} is a
            placeholder: captures taken from DIFFERENT spots must not be fused until that is "odometry"/"anchor".
A rejected capture (409: the robot was moving) is retried a few times, then reported — never faked.
"""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pi_link  # noqa: E402

CALIB = ROOT / "perception" / "calib" / "stereo_calibration_fisheye.yaml"
MOUNT = {"pitch_down_deg": 33.0, "height_m": 1.55, "yaw_left_deg": 0.0}      # bbos Config("depth") on bracketbot-0183
MOUNT_NOTE = "bbos Config('depth'): pitch_deg 33, height_m 1.55, roll_deg -1 (roll NOT applied: fuse.Mount has no roll)"


def post_capture(host: str, port: int, camera: str, timeout: float = 60.0) -> tuple[int, dict]:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("POST", "/capture", body=json.dumps({"cameras": [camera], "frames": 1, "inline": True}),
                     headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    finally:
        conn.close()


def write_recording(doc: dict, camera: str, out_root: Path) -> Path:
    frame = next((f for f in doc["frames"] if f["camera"] == camera and f["kind"] == "color" and f.get("jpeg_b64")), None)
    if frame is None:
        raise SystemExit(f"the capture carries no inline colour frame for {camera}: {[(f['camera'], f['kind']) for f in doc['frames']]}")
    jpeg = base64.b64decode(frame["jpeg_b64"])
    if jpeg[:2] != b"\xff\xd8":
        raise SystemExit("the frame is not a JPEG")
    rec = out_root / doc["capture_id"]
    rec.mkdir(parents=True, exist_ok=True)
    (rec / f"{camera}.jpg").write_bytes(jpeg)
    shutil.copyfile(CALIB, rec / f"{camera}.yaml")
    room = rec / "room"
    if not room.exists():
        room.mkdir()
        for rel in ("room.yaml", ".roomignore"):
            if (ROOT / "room.git" / rel).is_file():
                shutil.copyfile(ROOT / "room.git" / rel, room / rel)
        if (ROOT / "room.git" / "anchors").is_dir():
            shutil.copytree(ROOT / "room.git" / "anchors", room / "anchors")
    at = (doc.get("finished_at") or doc.get("started_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))[:19] + "Z"
    pose = doc.get("pose") or {}
    (rec / "capture.json").write_text(json.dumps({
        "capture_id": doc["capture_id"], "at": at,
        "pose": {"x": pose.get("x", 0.0), "z": pose.get("z", 0.0), "yaw": pose.get("yaw", 0.0)},
        "skew_ms": doc.get("skew_ms"), "tilt_rate_max": doc.get("tilt_rate_max"), "source": "robot",
        "frames": [{"camera": camera, "file": f"{camera}.jpg"}],
        "rig": {camera: {"calib": f"{camera}.yaml", "mount": MOUNT}},
        # extras — load_recording ignores them; they are here so the numbers above can be trusted or not
        "pose_source": doc.get("pose_source"), "mount_source": MOUNT_NOTE,
        "frame_size": [frame.get("width"), frame.get("height")], "gate": doc.get("gate"), "attempt": doc.get("attempt"),
        "robot_rig": doc.get("rig"), "sentry_trace_id": doc.get("sentry_trace_id"), "t_capture_mono": doc.get("t_capture_mono"),
    }, indent=1))
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("~/.cache/gitspace/recordings").expanduser())
    ap.add_argument("--camera", default="cam0")
    ap.add_argument("--n", type=int, default=1, help="how many captures")
    ap.add_argument("--every", type=float, default=4.0, help="seconds between them")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    a = ap.parse_args()
    if not CALIB.is_file():
        print(f"no calibration at {CALIB} — copy the robot's:\n  scp <user>@<robot>:bbos/bbos/daemons/depth/cache/stereo_calibration_fisheye.yaml {CALIB}")
        return 2
    env = pi_link.read_env()
    host, port = a.host or env.get("PI_HOST", ""), a.port or int(env.get("PI_PORT", "8080") or 8080)
    made = 0
    for i in range(a.n):
        for attempt in range(1, 5):
            try:
                status, doc = post_capture(host, port, a.camera)
            except OSError as e:
                print(f"  robot unreachable at {host}:{port} — {e}.  python scripts/pi_link.py status")
                return 1
            if status == 200:
                rec = write_recording(doc, a.camera, a.out)
                kb = (rec / f"{a.camera}.jpg").stat().st_size // 1024
                print(f"  {doc['capture_id']}  ->  {rec}   ({kb} KB · tilt {doc.get('tilt_rate_max')} · pose_source {doc.get('pose_source')})")
                made += 1
                break
            why = f"{doc.get('error')}: {str(doc.get('detail', ''))[:110]}"
            if status == 409 and doc.get("error") in ("capture_rejected", "busy") and attempt < 4:
                print(f"  {why} — retrying ({attempt}/3)")
                time.sleep(1.5)
                continue
            print(f"  HTTP {status} {why}")
            break
        if i + 1 < a.n:
            time.sleep(a.every)
    return 0 if made == a.n else 1


if __name__ == "__main__":
    sys.exit(main())
