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
  mount     pitch 38.1 deg down, 1.59 m up — MEASURED from the floor on this robot (see MOUNT below). bbos's own
            33 deg / 1.55 m describe ITS rectified frame; used here they tilt the floor 6 deg and fail the floor check.
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
# MEASURED, not copied. bbos's Config("depth") says pitch 33 deg / height 1.55 m — for ITS rectified frame. Through OUR
# rectification (depth.py, the yaml's R1/P1) those numbers leave the floor sloping up 6 deg and 5 cm low: the pipeline's
# floor check then passes or fails on the robot's balance wobble (cap_0006 failed it). So the mount was solved from the
# floor itself — level and zero the near floor (0.25-1.6 m ahead) — on three captures of bracketbot-0183, 2026-09-19:
#   pitch 38.06 / 37.60 / 38.57 deg  (mean 38.08, spread +-0.49 = the balance wobble) · height 1.585 / 1.586 / 1.588 m
# With it, fuse.assert_floor passes on all three at floor_z +0.2..+0.6 cm. RE-MEASURE if the head is ever re-mounted.
MOUNT = {"pitch_down_deg": 38.1, "height_m": 1.59, "yaw_left_deg": 0.0}
MOUNT_NOTE = ("measured from the floor on 3 captures (2026-09-19): pitch 38.08 +-0.49 deg, height 1.587 m. bbos's own "
              "33 deg / 1.55 m are for its rectified frame, not ours; its roll of -1 deg is not applied (measured residual roll -0.1 deg)")


def trace_headers() -> dict[str, str]:
    """`sentry-trace` + `baggage` of the transaction we are in, if any. The robot's server CONTINUES an incoming trace
    on POST /capture (docs/16 §6) and hands the id back as sentry_trace_id — but only if the caller sends one. With
    these on the request, laptop -> robot latch -> scan -> commit is ONE waterfall, and the room-clouds document carries
    the same trace id. (The 2 fps live-view poller deliberately sends the opposite: `-0`, unsampled.)"""
    try:
        sys.path.insert(0, str(ROOT))
        import obs
        return obs.trace_headers()
    except Exception:  # noqa: BLE001 -- observability never stops a capture
        return {}


def post_capture(host: str, port: int, camera: str, timeout: float = 60.0) -> tuple[int, dict]:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("POST", "/capture", body=json.dumps({"cameras": [camera], "frames": 1, "inline": True}),
                     headers={"Content-Type": "application/json", **trace_headers()})
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


LEVEL_MAX_DEG, LEVEL_MAX_M, LEVEL_MIN_PX = 6.0, 0.08, 5000


def level(rec_dir: Path, say=print) -> dict | None:
    """SELF-LEVEL one capture: fit the floor just ahead of the robot (0.25-1.6 m, +-0.9 m) and correct THIS capture's
    mount pitch and height so that floor is level and at z = 0. Why per capture: the robot balances, so its body pitch at
    the shutter is never quite the nominal one. Measured: captures taken while it rocked (tilt_rate ~0.05 rad/s) carry
    +2.3 deg of pitch error; at 2.3 m that lifts the floor 9 cm — enough to read as an object, to dirty a diff, and at
    ~5 deg to fail the pipeline's floor check outright. The near floor is always in view (the camera looks 38 deg down),
    it is the best-measured surface there is (11 mm flat), and it is the definition of z = 0.
    Bounded: a correction past +-6 deg / +-8 cm is not wobble, it is something wrong (an axis mistake is 90 deg) — then
    nothing is changed and the nominal mount stands. Roll is measured and reported but not applied (fuse.Mount has none;
    it has stayed under 1.4 deg). Writes the levelled mount into capture.json, keeping the nominal one beside it."""
    sys.path.insert(0, str(ROOT / "perception")); sys.path.insert(0, str(ROOT))
    import math
    import cv2, numpy as np
    import depth, fuse, pipeline          # noqa: E401
    rec = pipeline.load_recording(rec_dir)
    cam = next(iter(rec.frames))
    frames = {c: cv2.imread(str(f)) for c, f in rec.frames.items()}
    rigs = {c: pipeline._rig(str(f)) for c, f in rec.calib.items()}
    out, _ = depth.depth_capture(frames, rigs, rec.skew_ms, rec.tilt_rate_max)
    xyz, valid, _ = out[cam]
    C = xyz[valid]
    nominal = rec.mounts[cam]
    pitch, height = float(nominal.pitch_down_deg), float(nominal.height_m)

    def near_floor(P):
        Q = P[(P[:, 0] > 0.25) & (P[:, 0] < 1.6) & (np.abs(P[:, 1]) < 0.9) & (np.abs(P[:, 2]) < 0.45)]
        if len(Q) < LEVEL_MIN_PX:
            return None
        A = np.c_[Q[:, 0], Q[:, 1], np.ones(len(Q))]
        for _ in range(4):                                  # trim to the plane: objects standing on it must not tilt it
            coef, *_ = np.linalg.lstsq(A, Q[:, 2], rcond=None)
            keep = np.abs(Q[:, 2] - A @ coef) < 0.025
            if keep.sum() < LEVEL_MIN_PX:
                return None
            A, Q = A[keep], Q[keep]
        return math.degrees(math.atan(coef[0])), math.degrees(math.atan(coef[1])), float(coef[2]), int(len(Q)), float(np.std(Q[:, 2] - A @ coef))

    fit = None
    for _ in range(8):
        fit = near_floor(fuse.rect_to_world(C, fuse.Mount(pitch, height, nominal.yaw_left_deg)))
        if fit is None:
            break
        pitch, height = pitch + fit[0], height - fit[2]
    if fit is None:
        say(f"  level: not enough floor in view just ahead of the robot — the nominal mount stands")
        return None
    dp, dh = pitch - nominal.pitch_down_deg, height - nominal.height_m
    if abs(dp) > LEVEL_MAX_DEG or abs(dh) > LEVEL_MAX_M:
        say(f"  level: the floor asks for {dp:+.1f} deg / {dh * 100:+.1f} cm — that is not balance wobble; NOT applied (check the mount, or what the robot is standing on)")
        return None
    # Levelling must never make a capture UNSCANNABLE. The pipeline asserts on a plane fitted over the WHOLE cloud,
    # far floor included, where stereo depth is biased (glossy tile, reflections): a mount that is right for the near
    # floor can put that global plane past its 5 cm tolerance (seen: -5.5 cm). So the levelled mount is kept only if
    # fuse.assert_floor accepts it; otherwise the nominal mount stands — it is the one the check was passing with.
    try:
        fuse.assert_floor(fuse.rect_to_world(C, fuse.Mount(pitch, height, nominal.yaw_left_deg)))
    except AssertionError as e:
        say(f"  level: near floor asks for {dp:+.2f} deg / {dh * 100:+.1f} cm, but the pipeline's whole-cloud floor check rejects it "
            f"({str(e)[:60]}…) — nominal mount kept")
        return None
    cj = rec_dir / "capture.json"
    d = json.loads(cj.read_text())
    d["rig"][cam]["mount_nominal"] = d["rig"][cam].get("mount_nominal") or dict(d["rig"][cam]["mount"])
    d["rig"][cam]["mount"] = {"pitch_down_deg": round(pitch, 3), "height_m": round(height, 4), "yaw_left_deg": nominal.yaw_left_deg}
    d["levelled"] = {"pitch_delta_deg": round(dp, 2), "height_delta_cm": round(dh * 100, 1), "residual_roll_deg": round(fit[1], 2),
                     "floor_pixels": fit[3], "floor_flatness_mm": round(fit[4] * 1000, 1)}
    cj.write_text(json.dumps(d, indent=1))
    say(f"  levelled: pitch {dp:+.2f} deg, height {dh * 100:+.1f} cm (roll {fit[1]:+.2f} deg left as is) · floor flat to {fit[4] * 1000:.0f} mm over {fit[3]} px")
    return d["levelled"]


CLOUD_FIELDS = ("sentry_trace_id", "sentry_span_id", "sentry_url")      # room-clouds is `dynamic: strict`: only what it maps


def rejected_docs(doc: dict) -> list[dict]:
    """A 409 capture_rejected -> one room-clouds document per attempt the robot threw away (each attempt has its own
    capture id and its own trace). quality_ok false, the numbers that failed the gate, no points. Without these the
    catalog holds only the captures that worked, and "why did the room not update at 14:02" has no answer in it."""
    out = []
    for a in doc.get("attempts") or []:
        if not isinstance(a, dict) or not a.get("capture_id"):
            continue
        d = {"@timestamp": a.get("finished_at") or a.get("started_at"), "capture_id": a["capture_id"],
             "cameras": sorted(a.get("cameras") or []), "skew_ms": a.get("skew_ms"), "tilt_rate_max": a.get("tilt_rate_max"),
             "coverage_pct": a.get("coverage"), "quality_ok": False, "point_count": 0,
             **{k: a[k] for k in CLOUD_FIELDS if a.get(k)}}
        out.append({k: v for k, v in d.items() if v is not None})
    return out


def index_rejected(doc: dict, say=print) -> int:
    """Write them; spooled like any capture if Elasticsearch is away. Never raises: evidence must not break a capture loop."""
    docs = rejected_docs(doc)
    if not docs:
        return 0
    try:
        sys.path.insert(0, str(ROOT / "perception")); sys.path.insert(0, str(ROOT))
        import es_sink
        r = es_sink.deliver("room-clouds", docs, docs[-1]["capture_id"])
        why = "; ".join(f"{a.get('capture_id')}: {', '.join(a.get('rejected_by') or ['gate'])}" for a in doc.get("attempts") or [])
        say(f"  recorded {len(docs)} rejected attempt(s) in room-clouds, quality_ok=false ({why})" + (f"  (SPOOLED: {r.reason})" if r.spooled else ""))
        return len(docs)
    except Exception as e:  # noqa: BLE001
        say(f"  rejected attempts not recorded ({type(e).__name__}: {e})")
        return 0


def capture_once(host: str, port: int, camera: str, out_root: Path, say=print, record_rejected: bool | None = None) -> Path | None:
    """One gated capture from the robot, written as a recording. None (and the reason, said) if it could not be had.
    A 409 capture_rejected / busy is the robot saying it is still moving: waited out up to three times, never faked —
    and every attempt it threw away is recorded (record_rejected; default: on unless GITSPACE_INDEX_CAPTURES=0)."""
    import os
    if record_rejected is None:
        record_rejected = os.getenv("GITSPACE_INDEX_CAPTURES", "1") != "0"
    for attempt in range(1, 5):
        try:
            status, doc = post_capture(host, port, camera)
        except OSError as e:
            say(f"  robot unreachable at {host}:{port} — {e}.  python scripts/pi_link.py status")
            return None
        if status == 200:
            rec = write_recording(doc, camera, out_root)
            kb = (rec / f"{camera}.jpg").stat().st_size // 1024
            say(f"  {doc['capture_id']}  ->  {rec}   ({kb} KB · tilt {doc.get('tilt_rate_max')} · pose_source {doc.get('pose_source')})")
            try:
                level(rec, say)
            except Exception as e:  # noqa: BLE001 -- a capture that cannot be levelled is still a capture
                say(f"  level: skipped ({type(e).__name__}: {e})")
            return rec
        why = f"{doc.get('error')}: {str(doc.get('detail', ''))[:110]}"
        if status == 403:
            say(f"  the robot REFUSES this laptop ({why}): its address is not in ROBOT_ALLOW — ./scripts/push_to_pi.sh <user>@<robot> --start refreshes it")
            return None
        if status == 409 and doc.get("error") == "capture_rejected" and record_rejected:
            index_rejected(doc, say)
        if status == 409 and doc.get("error") in ("capture_rejected", "busy") and attempt < 4:
            say(f"  {why} — the robot is still settling; retrying ({attempt}/3)")
            time.sleep(1.5)
            continue
        say(f"  HTTP {status} {why}")
        return None
    return None


def index_recording(rec_dir: Path, say=print) -> dict | None:
    """Record this capture in Elasticsearch: its room-clouds catalog document (quality_ok, skew_ms, tilt_rate_max,
    coverage, point count, the Sentry trace) through perception's OWN capture_docs + index_capture, so the shape is the
    one /capture/<id> reads. A capture from the real robot is evidence whether or not anything is ever committed from
    it. (When room_live.py scans the capture, the scan indexes it — with the objects' observations — and this is not
    called; a second write of the same capture_id is a harmless duplicate.)"""
    try:
        sys.path.insert(0, str(ROOT / "perception")); sys.path.insert(0, str(ROOT))
        import cv2
        import depth, pipeline          # noqa: E401
        rec = pipeline.load_recording(rec_dir)
        frames = {c: cv2.imread(str(f)) for c, f in rec.frames.items()}
        rigs = {c: pipeline._rig(str(f)) for c, f in rec.calib.items()}
        out, ok = depth.depth_capture(frames, rigs, rec.skew_ms, rec.tilt_rate_max)
        cov = {c: rigs[c].coverage(v) for c, (_, v, _) in out.items()}
        res = pipeline.index_capture(pipeline.capture_docs(rec, out, cov, ok), rec.capture_id, "env")
        r = res["room-clouds"]
        say(f"  indexed: room-clouds {rec.capture_id} quality_ok={bool(ok)} coverage={sum(cov.values()) / max(len(cov), 1):.2f}"
            + (f"  (SPOOLED, Elasticsearch away: {r.reason})" if r.spooled else ""))
        return {"indexed": r.indexed, "spooled": r.spooled}
    except Exception as e:  # noqa: BLE001 -- the recording is on disk; indexing can be redone from it
        say(f"  not indexed ({type(e).__name__}: {e}) — the recording is kept: {rec_dir}")
        return None


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
    ap.add_argument("--no-index", action="store_true", help="do not record the capture in Elasticsearch (room-clouds)")
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
    try:                                                   # ES + Sentry settings live in .env; both are optional
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        sys.path.insert(0, str(ROOT))
        import obs
        obs.init("link")
    except Exception:  # noqa: BLE001
        obs = None
    import contextlib
    made, written = 0, []
    for i in range(a.n):
        tx = obs.transaction("capture", "capture_to_recording") if obs else contextlib.nullcontext()
        with tx:                                           # capture + index in ONE trace, continued on the robot
            rec = capture_once(host, port, a.camera, a.out, record_rejected=not a.no_index)
            if rec is not None and not a.no_index:
                index_recording(rec)
        if rec is None:
            break
        made += 1
        written.append(rec)
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
    if obs:
        obs.flush(3)
    return 0 if made == a.n else 1


if __name__ == "__main__":
    sys.exit(main())
