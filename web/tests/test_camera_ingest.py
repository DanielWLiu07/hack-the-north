"""web/camera_ingest.py — the receiver. No network, no camera: captures are built here in the sender's
own shape (robot/server.py POST /capture inline) and in Sarah's collector's folder shape (docs/27)."""
from __future__ import annotations

import base64
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

import camera_ingest as ci  # noqa: E402

K = {"fx": 100.0, "fy": 100.0, "ppx": 4.0, "ppy": 3.0, "w": 8, "h": 6}


def read_glb(path: Path):
    raw = path.read_bytes()
    magic, version, total = struct.unpack_from("<4sII", raw)
    assert magic == b"glTF" and version == 2 and total == len(raw)
    n, kind = struct.unpack_from("<I4s", raw, 12)
    assert kind == b"JSON"
    doc = json.loads(raw[20:20 + n])
    m, kind = struct.unpack_from("<I4s", raw, 20 + n)
    assert kind == b"BIN\0" and (20 + n) % 4 == 0 and m % 4 == 0
    blob = raw[28 + n:28 + n + m]
    count = doc["accessors"][0]["count"]
    pos = np.frombuffer(blob, "<f4", count * 3).reshape(-1, 3)
    col = np.frombuffer(blob, np.uint8, count * 4, offset=count * 12).reshape(-1, 4)
    return doc, pos, col


def test_backprojection_is_the_pinhole_model_in_metres_and_drops_what_has_no_depth():
    depth = np.zeros((6, 8), np.uint16)
    depth[3, 4] = 2000          # the principal point, 2 m away -> straight ahead
    depth[3, 6] = 1000          # 2 px right of it, 1 m away    -> x = 2 * 1 / 100
    depth[0, 0] = 9000          # past MAX_RANGE_M: dropped
    color = np.zeros((6, 8, 3), np.uint8)
    color[3, 6] = (10, 20, 30)  # BGR
    xyz, rgb = ci.backproject(depth, color, K)
    assert xyz.tolist() == [[0.0, 0.0, 2.0], [pytest.approx(0.02), 0.0, 1.0]] and rgb[1].tolist() == [30, 20, 10]


def test_glb_is_a_valid_points_primitive_with_y_up(tmp_path):
    xyz = np.array([[0.1, 0.2, 1.5], [-0.3, -0.1, 2.0]], np.float32)
    n = ci.write_glb(tmp_path / "c.glb", xyz, np.array([[255, 0, 0], [0, 255, 0]], np.uint8))
    doc, pos, col = read_glb(tmp_path / "c.glb")
    assert n == 2 and doc["meshes"][0]["primitives"][0] == {"mode": 0, "attributes": {"POSITION": 0, "COLOR_0": 1}}
    assert np.allclose(pos, [[0.1, -0.2, -1.5], [-0.3, 0.1, -2.0]]), "camera y-down/z-forward -> glTF y-up/-z-forward"
    assert col.tolist() == [[255, 0, 0, 255], [0, 255, 0, 255]] and doc["accessors"][1]["normalized"] is True
    assert doc["accessors"][0]["min"] == pytest.approx([-0.3, -0.2, -2.0])


def capture(*, intrinsics: bool, depth: bool = True) -> dict:
    img = np.full((6, 8, 3), 128, np.uint8)
    d = np.full((6, 8), 1500, np.uint16)
    jpg = base64.b64encode(cv2.imencode(".jpg", img)[1].tobytes()).decode()
    png = base64.b64encode(cv2.imencode(".png", d)[1].tobytes()).decode()
    frames = [{"camera": "cam1", "seq": s, "kind": "color", "fmt": "mjpg", "jpeg_b64": jpg} for s in (1, 0)]
    if depth:
        frames += [{"camera": "cam1", "seq": 0, "kind": "depth", "fmt": "png16", "png_b64": png}]
    return {"capture_id": "cap_0007", "started_at": "2026-09-19T07:00:00.000Z", "cameras": ["cam1"], "quality_ok": True,
            "pose": {"x": 0, "z": 0, "yaw": 0}, "pose_source": "odometry", "sender_mode": "hardware",
            "rig": [{"camera": "cam1", "kind": "realsense", "model": "D435", **({"intrinsics": K} if intrinsics else {})}], "frames": frames}


def test_a_hardware_capture_becomes_a_cloud_and_a_manifest_that_says_what_it_is(tmp_path):
    m = ci.from_robot(capture(intrinsics=True), tmp_path, "http://sarah:8080")
    ci.publish(m, tmp_path)
    latest = json.loads((tmp_path / "latest.json").read_text())
    cam = latest["cameras"][0]
    assert cam["cloud"] == "cap_0007/cam1.glb" and cam["points"] == 48 and cam["color"] == "cap_0007/cam1_color.jpg"
    assert read_glb(tmp_path / cam["cloud"])[1][:, 2].tolist() == pytest.approx([-1.5] * 48)
    assert latest["sender_mode"] == "hardware" and "point cloud" in latest["is"] and "a gaussian splat" in latest["is_not"]


def test_no_intrinsics_or_no_depth_is_no_cloud_and_the_reason_never_a_guess(tmp_path):
    cam = ci.from_robot(capture(intrinsics=False), tmp_path, "http://x")["cameras"][0]
    assert cam["cloud"] is None and "no intrinsics" in cam["cloud_reason"] and cam["color"]
    m = ci.from_robot(capture(intrinsics=True, depth=False), tmp_path, "http://x")
    assert m["cameras"][0]["cloud"] is None and "a picture, not geometry" in m["cameras"][0]["cloud_reason"]
    ci.publish(m, tmp_path)
    assert json.loads((tmp_path / "latest.json").read_text())["is"].startswith("colour frames only")
    assumed = ci.from_robot(capture(intrinsics=False), tmp_path, "http://x", hfov_deg=69.0)["cameras"][0]   # the operator said so, and it is recorded
    assert assumed["cloud"] and assumed["intrinsics"]["assumed_from_hfov_deg"] == 69.0


def test_sarahs_folder_needs_no_intrinsics_and_the_unit_trap_is_caught(tmp_path):
    cap = tmp_path / "session_x" / "capture_0003"
    cap.mkdir(parents=True)
    cv2.imwrite(str(cap / "d435_color.png"), np.full((6, 8, 3), 200, np.uint8))
    pts = np.zeros((48, 3), np.float32)
    pts[:, 2] = 1.2
    pts[0] = 0                                                     # (0,0,0) = no depth
    np.save(cap / "d435_pointcloud.npy", pts)
    out = tmp_path / "out"
    out.mkdir()
    m = ci.from_session(tmp_path / "session_x", out)
    assert m["capture_id"] == "session_x_capture_0003" and m["cameras"][0]["points"] == 47 and m["sender_mode"] == "recorded"
    np.save(cap / "d435_pointcloud.npy", pts * 1000)               # millimetres passed off as metres
    assert "not in metres" in ci.from_session(tmp_path / "session_x", out)["cameras"][0]["cloud_reason"]
    with pytest.raises(RuntimeError):
        ci.from_session(tmp_path / "nothing_here", out)
