"""Captures as git nodes: GET /api/scene/{instance}/history and that commit's cloud/current.ply.

Local only — TestClient's default peer is "testclient", which is not loopback.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r.stdout.strip()


def ply(points: list[tuple]) -> bytes:
    head = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(points)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    ).encode()
    body = b"".join(struct.pack("<fffBBB", *p) for p in points)
    return head + body


def room(tmp_path: Path) -> tuple[Path, str, str, bytes]:
    rooms = tmp_path / "rooms"
    repo = rooms / "hallway"
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    (repo / "README").write_text("first\n")
    git(repo, "add", "README")
    git(repo, "commit", "-m", "first scan (cap_0007)")
    empty = git(repo, "rev-parse", "HEAD")

    blob = ply([(1.0, 0.0, 0.5, 255, 10, 10), (0.0, -0.2, 0.0, 10, 255, 10)])
    (repo / "cloud").mkdir()
    (repo / "cloud" / "current.ply").write_bytes(blob)
    (repo / "cloud" / "current.json").write_text(json.dumps({
        "capture_id": "cap_0016", "at": "2026-09-19T17:00:00Z", "points": 2,
        "pose": {"x": 0.4, "z": -0.1, "yaw": 1.57},
    }) + "\n")
    git(repo, "add", "cloud/current.ply", "cloud/current.json")
    git(repo, "commit", "-m", "items on the floor  [cap_0016]")
    head = git(repo, "rev-parse", "HEAD")
    (rooms / ".current").write_text("hallway\n")
    return rooms, empty, head, blob


def local(monkeypatch, tmp_path) -> tuple[TestClient, str, str, bytes]:
    rooms, empty, head, blob = room(tmp_path)
    monkeypatch.setenv("ROOM_LIVE_DIR", str(rooms))
    return TestClient(server.app, client=("127.0.0.1", 50000)), empty, head, blob


def test_history_is_the_instance_git_log_and_a_capture_without_a_cloud_is_still_a_node(monkeypatch, tmp_path):
    c, empty, head, blob = local(monkeypatch, tmp_path)
    doc = c.get("/api/scene/hallway/history").json()
    assert doc["instance"] == "hallway" and doc["head"] == head and [n["sha"] for n in doc["commits"]] == [head, empty]
    newest, first = doc["commits"]
    assert newest["head"] is True and newest["cloud"] is True and newest["capture_id"] == "cap_0016"
    assert newest["parents"] == [empty] and newest["points"] == 2
    assert newest["robot"]["x"] == 0.4 and newest["robot"]["y"] == -0.1
    assert first["head"] is False and first["cloud"] is False and first["capture_id"] == "cap_0007"
    assert first["subject"].startswith("first scan") and first["parents"] == []

    ply_r = c.get(f"/api/scene/hallway/history/{head}.ply")
    assert ply_r.status_code == 200 and ply_r.content == blob and ply_r.headers["cache-control"] == "no-store"
    short = c.get(f"/api/scene/hallway/history/{head[:7]}.json").json()
    assert short["capture_id"] == "cap_0016" and short["points"] == 2
    assert c.get(f"/api/scene/hallway/history/{empty}.ply").status_code == 404
    assert c.get("/api/scene/hallway/history/deadbee.ply").status_code == 404
    assert c.get("/api/scene/hallway/history/../hallway.ply").status_code == 404


def test_history_stays_on_this_laptop(monkeypatch, tmp_path):
    rooms, *_ = room(tmp_path)
    monkeypatch.setenv("ROOM_LIVE_DIR", str(rooms))
    stranger = TestClient(server.app)
    assert stranger.get("/api/scene/hallway/history").status_code == 403
    tunnel = TestClient(server.app, client=("127.0.0.1", 50000))
    assert tunnel.get("/api/scene/hallway/history", headers={"x-forwarded-for": "1.2.3.4"}).status_code == 403
    assert stranger.post("/api/scene/hallway/add").status_code == 403


def test_add_captures_current_as_a_new_git_node(monkeypatch, tmp_path):
    import scene_api
    c, _empty, head, _blob = local(monkeypatch, tmp_path)

    def run(instance: str, message: str) -> dict:
        repo = Path(os.environ["ROOM_LIVE_DIR"]) / instance
        meta = json.loads((repo / "cloud" / "current.json").read_text())
        meta["capture_id"] = "cap_0099"
        meta["at"] = "2026-09-19T20:00:00Z"
        (repo / "cloud" / "current.json").write_text(json.dumps(meta, indent=1) + "\n")
        git(repo, "add", "cloud/current.json")
        git(repo, "commit", "-m", f"{message}  [cap_0099]")
        sha = git(repo, "rev-parse", "HEAD")
        return {"instance": instance, "committed": True, "sha": sha, "head": sha, "subject": f"{message}  [cap_0099]"}

    monkeypatch.setattr(scene_api, "_run_add", run)
    r = c.post("/api/scene/hallway/add")
    assert r.status_code == 200
    body = r.json()
    assert body["committed"] is True and body["sha"] != head and body["subject"].startswith("current")
    hist = c.get("/api/scene/hallway/history").json()
    assert hist["head"] == body["sha"] and hist["commits"][0]["capture_id"] == "cap_0099"
    assert hist["commits"][0]["head"] is True and hist["commits"][1]["sha"] == head


def test_history_prefers_complete_capture_plys_as_the_time_graph(monkeypatch, tmp_path):
    """The /scene captures — not the thinned git voxels — are the nodes you walk in time."""
    rooms = tmp_path / "rooms"
    repo = rooms / "hallway-test"
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    (repo / "README").write_text("room\n")
    git(repo, "add", "README")
    git(repo, "commit", "-m", "first scan (cap_0007)")
    older_sha = git(repo, "rev-parse", "HEAD")
    (repo / "cloud").mkdir()
    (repo / "cloud" / "current.ply").write_bytes(ply([(0.0, 0.0, 0.0, 1, 1, 1)]))
    git(repo, "add", "cloud/current.ply")
    git(repo, "commit", "-m", "items on the floor  [cap_0016]")
    cap16_sha = git(repo, "rev-parse", "HEAD")

    scene = rooms / "hallway-test.scene"
    recs = rooms / "hallway-test.recordings"
    scene.mkdir()
    recs.mkdir()
    blob21 = ply([(1.0, 0.0, 0.5, 255, 10, 10), (0.0, -0.2, 0.0, 10, 255, 10), (0.5, 0.1, 0.2, 10, 10, 255)])
    blob7 = ply([(2.0, 0.0, 0.0, 200, 200, 200)])
    (scene / "cap_0007.ply").write_bytes(blob7)
    os.utime(scene / "cap_0007.ply", (1_000_000, 1_000_000))
    (scene / "cap_0016.ply").write_bytes(ply([(0.1, 0.0, 0.0, 9, 9, 9)]))
    os.utime(scene / "cap_0016.ply", (1_000_100, 1_000_100))
    (scene / "cap_0021.ply").write_bytes(blob21)
    os.utime(scene / "cap_0021.ply", (1_000_200, 1_000_200))
    (scene / "cap_0008.ply").write_bytes(b"ply\nformat binary_little_endian 1.0\nelement vertex 9\nend_header\nshort")
    os.utime(scene / "cap_0008.ply", (1_000_300, 1_000_300))
    (recs / "cap_0021").mkdir()
    (recs / "cap_0021" / "capture.json").write_text(json.dumps({
        "at": "2026-09-19T21:00:00Z",
        "pose": {"x": 0.4, "z": -0.1, "yaw": 0.0},
    }) + "\n")

    monkeypatch.setenv("ROOM_LIVE_DIR", str(rooms))
    c = TestClient(server.app, client=("127.0.0.1", 50000))
    doc = c.get("/api/scene/hallway-test/history").json()
    # `head` is the tip of the TIME graph: the newest complete capture, here one taken but never committed — the robot
    # is ahead of the repo. git HEAD (cap_0016) stays visible as head_sha and as the "HEAD -> main" ref chip on its node.
    assert doc["instance"] == "hallway-test" and doc["kind"] == "captures" and doc["head"] == "cap_0021"
    assert doc["head_sha"] == cap16_sha
    ids = [n["id"] for n in doc["commits"]]
    assert ids == ["cap_0021", "cap_0016", "cap_0007"]  # incomplete cap_0008 is not a node
    assert [n["head"] for n in doc["commits"]] == [True, False, False]     # exactly one node leads: what the page follows
    assert any(r.get("head") and r["kind"] == "branch" for r in doc["commits"][1]["refs"])   # ...and "HEAD -> main" still sits on cap_0016
    newest, mid, first = doc["commits"]
    assert newest["file"] == "cap_0021.ply" and newest["cloud"] is True and newest["points"] == 3
    assert newest["parents"] == ["cap_0016"]
    # cap_0021's recording has no pose_bb: it is NOT placed, and the API must not derive a heading from a pose that was
    # never recorded (a number here would read downstream as a measurement). The raw odometry yaw stays as recorded.
    assert newest["robot"]["placed"] is False and newest["robot"]["heading_rad"] is None
    assert newest["robot"]["source"] == "none" and newest["robot"]["yaw"] == 0.0
    assert newest["commit_sha"] is None  # captured to disk, never committed
    assert mid["commit_sha"] == cap16_sha and mid["parents"] == ["cap_0007"]
    assert first["commit_sha"] == older_sha and first["parents"] == []

    ply_r = c.get("/api/scene/hallway-test/cap_0021.ply")
    assert ply_r.status_code == 200 and ply_r.content == blob21
    assert c.get("/api/scene/hallway-test/cap_0008.ply").status_code == 200  # file exists; history still skipped it


def test_add_says_why_the_robot_could_not_be_read():
    import scene_api
    out = (
        "[1/2] read the robot's fused map\n"
        "could not read the robot's map: ssh: connect to host 10.37.101.235 port 22: No route to host\n"
        "Sentry is attempting to send 1 pending events\nWaiting up to 2 seconds\nPress Ctrl-C to quit\n"
    )
    assert "could not read the robot's map" in scene_api._add_detail(out, 1)


def test_find_names_the_capture_ply_and_a_missing_one_is_said_out_loud(monkeypatch, tmp_path):
    import scene_api
    c, _empty, _head, blob = local(monkeypatch, tmp_path)
    scene = tmp_path / "rooms" / "hallway.scene"
    scene.mkdir()
    (scene / "cap_0016.ply").write_bytes(blob)
    (scene / "cap_0016.png").write_bytes(b"\x89PNG")
    found = c.get("/api/scene/find/cap_0016").json()
    assert found["available"] is True and found["instance"] == "hallway" and found["points"] == 2
    assert found["ply"] == "/api/scene/hallway/cap_0016.ply" and found["has_png"] is True and found["local_only"] is True
    missing = c.get("/api/scene/find/cap_0007").json()
    assert missing["available"] is False and "no point cloud" in missing["reason"]
    assert c.get("/api/scene/find/not-a-cap").status_code == 404
    assert TestClient(server.app).get("/api/scene/find/cap_0016").status_code == 403
