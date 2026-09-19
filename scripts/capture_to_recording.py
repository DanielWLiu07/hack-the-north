#!/usr/bin/env python3
"""capture_to_recording.py — a LIVE capture from the robot, written as a perception Recording.

The piece that was missing between the two halves that existed: robot/server.py can take a gated
capture, perception/pipeline.py can turn a recording into the room (depth -> fuse -> segment ->
voxels -> the room repo, where `git diff` IS "what changed"). Nothing turned one into the other;
the only recordings were perception/synthetic.py's.

    python scripts/capture_to_recording.py                     # one capture -> ~/.cache/gitspace/recordings/<id>/
    python scripts/capture_to_recording.py --out DIR --n 3     # three, a few seconds apart
    python perception/pipeline.py <that dir> --repo <room repo>      # then: the room
    python scripts/capture_to_recording.py --compare DIR_A DIR_B     # how well do two captures AGREE? (no robot needed)
    python scripts/capture_to_recording.py --check-desk              # one capture: is a desk where room.yaml expects it?
    python scripts/capture_to_recording.py --check-desk DIR          # ...or the same question of an existing recording

With --n 2 or more, each capture is compared with the one before it and the agreement table is printed — for a
robot that has not moved and a scene nobody touched, that table IS the noise floor: the number everything that
calls a difference a "change" (room status, a diff, difference-based segmentation) has to stay above.

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


BANDS = ((0.0, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 6.0))      # metres from the robot, on the floor plane


def _voxels(rec_dir: Path):
    """One recording -> (capture_id, voxel centres Nx3, leaf) through the pipeline's OWN depth -> fuse -> VoxelGrid,
    so what is compared is what the room is built from, not a second opinion of it."""
    sys.path.insert(0, str(ROOT / "perception")); sys.path.insert(0, str(ROOT))
    import cv2, numpy as np
    import depth, fuse, pipeline, voxelize          # noqa: E401 -- perception's modules, imported only for --compare
    rec = pipeline.load_recording(rec_dir)
    frames = {c: cv2.imread(str(f)) for c, f in rec.frames.items()}
    rigs = {c: pipeline._rig(str(f)) for c, f in rec.calib.items()}
    out, _ = depth.depth_capture(frames, rigs, rec.skew_ms, rec.tilt_rate_max)
    _, cloud = fuse.fuse([(xyz, v, rec.mounts[c]) for c, (xyz, v, _) in out.items()], robot_pose=fuse.odom_to_world(rec.pose))
    grid = voxelize.VoxelGrid.from_points(cloud)
    return rec.capture_id, np.asarray(grid.centres()), set(grid.keys()), float(grid.leaf)


def agreement(dir_a: Path, dir_b: Path) -> dict:
    """Voxel agreement between two captures: exact, and within ONE voxel, overall and by distance band.
    `unmatched` = occupied in one scan with nothing occupied within 1.5 leaves of it in the other, both directions."""
    import numpy as np
    from scipy.spatial import cKDTree
    (ida, A, ka, leaf), (idb, B, kb, _) = _voxels(dir_a), _voxels(dir_b)
    tol = 1.5 * leaf
    da, db = cKDTree(B).query(A, k=1)[0], cKDTree(A).query(B, k=1)[0]
    P, d = np.vstack([A, B]), np.concatenate([da, db])          # every voxel of both scans, and its miss distance
    r = np.hypot(P[:, 0], P[:, 1])
    bands = []
    for lo, hi in BANDS:
        m = (r >= lo) & (r < hi)
        bands.append({"band_m": [lo, hi], "voxels": int(m.sum()), "unmatched": int((d[m] > tol).sum()),
                      "unmatched_pct": round(100 * float((d[m] > tol).mean()), 1) if m.any() else None})
    near = r < 2.0
    return {"captures": [ida, idb], "leaf_m": round(leaf, 4), "tolerance_m": round(tol, 4), "voxels": [len(ka), len(kb)],
            "exact_pct": round(100 * len(ka & kb) / max(len(ka | kb), 1), 1),
            "within_1_voxel_pct": round(100 * float((d <= tol).mean()), 1),
            "unmatched_pct_within_2m": round(100 * float((d[near] > tol).mean()), 1) if near.any() else None,
            "unmatched_pct_beyond_2m": round(100 * float((d[~near] > tol).mean()), 1) if (~near).any() else None,
            "bands": bands}


def desk_check(rec_dir: Path) -> dict:
    """Is there a desk where room.yaml's `desk` zone says? Parking feedback for a human (robot/RUNBOOK.md §7).
    The pipeline only segments objects INSIDE that zone, and with pose_source "none" the zone is relative to the ROBOT,
    so a robot parked 30 cm off gives objects=0 and no error anywhere. This measures instead of hoping: the dominant
    horizontal surface in front of the robot (its height, its near edge, how wide it reaches) against the zone."""
    sys.path.insert(0, str(ROOT / "perception")); sys.path.insert(0, str(ROOT))
    import cv2, numpy as np
    import depth, fuse, pipeline, voxelize          # noqa: E401
    rec = pipeline.load_recording(rec_dir)
    zone = (voxelize.load_room(rec.path / "room").get("zones") or {}).get("desk")
    if not zone:
        return {"ok": False, "why": "room.yaml has no `desk` zone"}
    frames = {c: cv2.imread(str(f)) for c, f in rec.frames.items()}
    rigs = {c: pipeline._rig(str(f)) for c, f in rec.calib.items()}
    out, _ = depth.depth_capture(frames, rigs, rec.skew_ms, rec.tilt_rate_max)
    _, cloud = fuse.fuse([(xyz, v, rec.mounts[c]) for c, (xyz, v, _) in out.items()], robot_pose=fuse.odom_to_world(rec.pose))
    P = np.asarray(cloud)[:, :3]
    (x0, y0, z0), (x1, y1, z1) = zone["min"], zone["max"]
    want = float(zone.get("surface", z0))
    front = P[(P[:, 0] > 0.0) & (P[:, 0] < 1.6) & (np.abs(P[:, 1]) < 0.8) & (P[:, 2] > 0.35) & (P[:, 2] < 1.25)]
    res = {"capture_id": rec.capture_id, "zone": {"x": [x0, x1], "y": [y0, y1], "z": [z0, z1], "surface": want},
           "points_in_zone": int(((P >= zone["min"]) & (P <= zone["max"])).all(axis=1).sum())}
    if len(front) < 2000:
        return {**res, "ok": False, "why": f"no surface between 0.35 and 1.25 m high within 1.6 m ahead ({len(front)} points there) — "
                                           "the robot is not facing a desk"}
    hist, edges = np.histogram(front[:, 2], bins=np.arange(0.35, 1.26, 0.02))
    k = int(hist.argmax()); top = float((edges[k] + edges[k + 1]) / 2)
    slab = front[np.abs(front[:, 2] - top) < 0.03]
    if len(slab) < 1500:
        return {**res, "ok": False, "why": f"nothing flat in front of the robot (best layer: {len(slab)} points at z={top:.2f} m)"}
    near, far = float(np.percentile(slab[:, 0], 2)), float(np.percentile(slab[:, 0], 98))
    right, left = float(np.percentile(slab[:, 1], 2)), float(np.percentile(slab[:, 1], 98))
    res.update(surface_z=round(top, 3), near_edge_x=round(near, 2), far_edge_x=round(far, 2), y_span=[round(right, 2), round(left, 2)],
               surface_points=int(len(slab)))
    problems = []
    if abs(top - want) > 0.03:
        problems.append(f"the desk top is at {top:.2f} m, room.yaml says {want:.2f} (zone z starts at {z0:.2f}): re-measure `surface`/`min.z`, or use the other desk")
    if near > x0 + 0.15:
        problems.append(f"the desk starts {near:.2f} m ahead; the zone starts at {x0:.2f}: roll the robot {near - x0:.2f} m FORWARD")
    if far < x0 + 0.30:
        problems.append("hardly any desk inside the zone's depth")
    if abs((left + right) / 2) > 0.15:
        side = "LEFT" if (left + right) / 2 > 0 else "RIGHT"
        problems.append(f"the desk is centred {abs((left + right) / 2):.2f} m to the robot's {side}: shift the robot that way, or square it up")
    return {**res, "ok": not problems, "problems": problems}


def print_desk(d: dict) -> None:
    z = d.get("zone", {})
    print(f"\n  DESK CHECK  {d.get('capture_id', '')}   room.yaml desk zone: x {z.get('x')} y {z.get('y')} z {z.get('z')} surface {z.get('surface')}  (metres, from the robot)")
    if "surface_z" in d:
        print(f"    found a flat surface at z = {d['surface_z']} m · from x = {d['near_edge_x']} to {d['far_edge_x']} m ahead · y {d['y_span']} · {d['surface_points']} points")
    print(f"    points inside the zone: {d.get('points_in_zone')}")
    if d["ok"]:
        print("    OK — the desk is where room.yaml expects it. Do not move the robot; take the captures.")
    else:
        for line in d.get("problems") or [d.get("why", "")]:
            print(f"    NOT OK — {line}")


def print_agreement(a: dict) -> None:
    print(f"\n  AGREEMENT  {a['captures'][0]} vs {a['captures'][1]}   ({a['voxels'][0]} / {a['voxels'][1]} voxels of {a['leaf_m'] * 100:.2f} cm)")
    print(f"    exact voxel match        {a['exact_pct']:5.1f} %")
    print(f"    within 1 voxel ({a['tolerance_m'] * 100:.0f} cm)    {a['within_1_voxel_pct']:5.1f} %      unmatched: {a['unmatched_pct_within_2m']} % within 2 m · {a['unmatched_pct_beyond_2m']} % beyond")
    print("    by distance from the robot:   band        voxels   unmatched after 1-voxel tolerance")
    for b in a["bands"]:
        pct = "   –  " if b["unmatched_pct"] is None else f"{b['unmatched_pct']:5.1f} %"
        print(f"                              {b['band_m'][0]:>4} – {b['band_m'][1]:<4} m  {b['voxels']:6d}   {b['unmatched']:5d} = {pct}")
    print("    (same spot, untouched scene => this is the NOISE FLOOR. A moved robot or a changed scene reads higher.)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("~/.cache/gitspace/recordings").expanduser())
    ap.add_argument("--camera", default="cam0")
    ap.add_argument("--n", type=int, default=1, help="how many captures")
    ap.add_argument("--every", type=float, default=4.0, help="seconds between them")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("DIR_A", "DIR_B"), help="agreement between two existing recordings; no capture")
    ap.add_argument("--json", action="store_true", help="print the agreement as JSON too")
    ap.add_argument("--check-desk", nargs="?", const="", metavar="DIR", help="is a desk where room.yaml's zone expects it? "
                    "With DIR: an existing recording. Without: take one capture first")
    a = ap.parse_args()
    if a.check_desk:
        d = desk_check(Path(a.check_desk).expanduser())
        print_desk(d)
        return 0 if d["ok"] else 1
    if a.compare:
        ag = agreement(a.compare[0].expanduser(), a.compare[1].expanduser())
        print_agreement(ag)
        if a.json:
            print(json.dumps(ag))
        return 0
    if not CALIB.is_file():
        print(f"no calibration at {CALIB} — copy the robot's:\n  scp <user>@<robot>:bbos/bbos/daemons/depth/cache/stereo_calibration_fisheye.yaml {CALIB}")
        return 2
    env = pi_link.read_env()
    host, port = a.host or env.get("PI_HOST", ""), a.port or int(env.get("PI_PORT", "8080") or 8080)
    made, written = 0, []
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
                written.append(rec)
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
    if a.check_desk == "" and written:
        d = desk_check(written[-1])
        print_desk(d)
    for prev, cur in zip(written, written[1:]):          # each capture against the one before it
        try:
            ag = agreement(prev, cur)
            print_agreement(ag)
            if a.json:
                print(json.dumps(ag))
        except Exception as e:  # noqa: BLE001 -- the recordings are written; a failed comparison must not lose them
            print(f"  (could not compare {prev.name} with {cur.name}: {type(e).__name__}: {e})")
    return 0 if made == a.n else 1


if __name__ == "__main__":
    sys.exit(main())
