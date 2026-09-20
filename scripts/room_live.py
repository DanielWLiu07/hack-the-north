#!/usr/bin/env python3
"""room_live.py — the front door: the LIVE robot -> a room you can `git log`.

One flow, five verbs. Every verb that looks at the room does the same three things, in this order:

    1. CAPTURE   the robot takes one gated stereo picture            (scripts/capture_to_recording.py)
    2. SCAN      depth -> the room's point cloud -> objects, voxels  (perception/pipeline.py, via `room`)
    3. GIT       the room repo's working tree now says what is there; `room status` / `room commit` do the rest

    python scripts/room_live.py new desk-demo          create an INSTANCE: a fresh room repo, first capture, first commit
    python scripts/room_live.py status                 capture + scan -> what changed since the last commit
    python scripts/room_live.py commit -m "after lunch"    capture + scan -> record the room as it is now
    python scripts/room_live.py snapshot               FREEZE THE ROOM NOW: capture -> point cloud -> one commit. The `git add`
                                                       of physical space; -m "why" is optional
    python scripts/room_live.py add                    THE ONE TO USE. `git add` for the room: reads the robot's FULL fused map (every
                                                       view it has taken, registered by its SLAM, objects separated), commits it, and
                                                       FILLS THE SCENE — http://localhost:8000/scene follows the latest one. ~3 s.
                                                       (= `snapshot --map`; `add -m "why"` to say why)
    python scripts/room_live.py snapshot --map         ...the same, but of THE ROBOT'S OWN FUSED MAP (bbos mapping.voxels): every
                                                       view it has taken, registered by its SLAM, with the objects in it separated.
                                                       2 s, no capture, and it survives the robot moving. PREFER THIS.
    python scripts/room_live.py changes [OLD] [NEW]    which OBJECTS appeared / are gone between two --map snapshots (default
                                                       HEAD~1 -> HEAD): the room's diff, as things, with a picture
    python scripts/room_live.py cloud --ref REF        the point cloud AS IT WAS at any commit (default HEAD) -> a .ply file
    python scripts/room_live.py watch --every 15       keep looking; prints what changed.  --commit: commit each change
    python scripts/room_live.py log | diff | list      history · the literal git diff · your instances

An INSTANCE is just a room repository — a normal git repo (`cd` into it, `git log`, push it anywhere):
    ~/.cache/gitspace/rooms/<name>/               the repo: room.yaml, zones/<zone>/<object>.yaml … — TEXT, so it diffs
    ~/.cache/gitspace/rooms/<name>.recordings/    every capture it was built from (the stereo frame + calibration)
    ~/.cache/gitspace/rooms/<name>.scene/         the 3D MODEL of each capture: <capture_id>.ply (coloured points,
                                                  opens in MeshLab / CloudCompare / three.js) + .png, and latest.*
THE POINT CLOUD IS IN THE COMMIT. Every commit carries `cloud/current.ply` — the room as the robot measured it at that
moment, coloured, thinned to one point per 2 cm cell (~100-150 k points, ~2 MB) so git can afford it — and
`cloud/current.json` saying which capture, when, how many points, and whether the pose was real. So history IS the room
over time: `git log -- cloud/current.ply` lists every snapshot, `git show <sha>:cloud/current.ply > then.ply` (or
`room_live.py cloud <sha>`) gives you the room as it was, `git checkout <sha>` puts the whole tree back there.
The FULL-resolution model (~500 k points, 8 MB) still goes BESIDE the repo in <name>.scene/, named by capture id — the
commit message carries that id, so a commit always leads to its full model too.

PRIVACY — A CLOUD IS A PICTURE. These points carry the camera's colours and the shapes of whoever was in view; a commit
that holds one is a 3D photograph of those people. An instance under ~/.cache has no remote and no hook, so it stays on
this laptop. A repo that CAN LEAVE the machine does not get clouds in its commits: if it has a git remote, or a
post-commit / post-merge hook (the real ./room.git has both — its hook mirrors every commit to the cloud VM, and its
origin is GitHub), the cloud is written BESIDE the repo instead and the commit carries only the text (objects, metadata).
`--cloud-in-repo` overrides that, for a room with nobody in it. Adding a remote to an instance later publishes every
cloud already in its history: that is your decision to make with open eyes, not a side effect.
The last instance you touched is remembered; name one to switch (`status desk-demo`), or `--repo PATH` for any room
repo — including the real one (`--repo ./room.git`), which is the only one that publishes to Elasticsearch.

WHAT A CHANGE IS. The scan only looks for objects inside the zones of room.yaml (a desk within 1 m — robot/RUNBOOK.md §7
says how to park; `capture_to_recording.py --check-desk` says whether you did). `status` is then the room's `git status`:
an object that moved is `modified`, a new one `untracked`, a missing one `deleted`. Positions are quantised and held with
hysteresis, so an untouched desk reads CLEAN — that is the design's acceptance test, not a given: if it reads dirty,
`capture_to_recording.py --n 2` shows how much two captures of the same scene disagree.
Until the robot's pose is real (pose_source "none"), every capture is assumed to be from the SAME spot: do not move the
robot between captures of one instance.
"""
from __future__ import annotations

import argparse
import json
import yaml
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import capture_to_recording as c2r  # noqa: E402
import pi_link  # noqa: E402

ROOMS = Path(os.getenv("ROOM_LIVE_DIR", "~/.cache/gitspace/rooms")).expanduser()
CURRENT = ROOMS / ".current"
PY = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable
B, D, G, Y, R, X = ("\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[0m") if sys.stdout.isatty() else ("",) * 6


def step(n: int, of: int, what: str) -> None:
    print(f"{B}[{n}/{of}] {what}{X}", flush=True)


# ── which room ────────────────────────────────────────────────────────────────────
class Room:
    def __init__(self, name: str | None, repo: Path | None):
        if repo is not None:
            self.repo, self.name, self.instance = repo.expanduser().resolve(), repo.name, False
        else:
            name = name or (CURRENT.read_text().strip() if CURRENT.exists() else "")
            if not name:
                raise SystemExit(f"no instance yet.  python scripts/room_live.py new <name>")
            self.repo, self.name, self.instance = ROOMS / name, name, True
        base = self.repo.parent / self.repo.name
        self.recordings, self.scene = Path(f"{base}.recordings"), Path(f"{base}.scene")

    def remember(self) -> None:
        if self.instance:
            ROOMS.mkdir(parents=True, exist_ok=True)
            CURRENT.write_text(self.name)

    def room(self, *args: str, scanner: Path | None = None, capture: bool = False) -> subprocess.CompletedProcess:
        """The project's own `room` CLI on this repo. With `scanner`, its scan reads THAT recording through the real
        pipeline (ROOM_SCANNER=perception:<dir> — roomctl/cli.py)."""
        env = dict(os.environ)
        env.pop("ROOM_SCANNER", None)
        if scanner is not None:
            env["ROOM_SCANNER"] = f"perception:{scanner}"
            env.setdefault("GITSPACE_INDEX_CAPTURES", "1")   # a capture from the real robot is evidence: always indexed
        if self.instance:
            env["ROOM_ES"] = "off"                       # only the real room publishes COMMITS to the shared indices
        for k, v in c2r.trace_headers().items():         # `room` opens its own transaction: make it a child of OURS, so
            env["SENTRY_TRACE" if k == "sentry-trace" else "SENTRY_BAGGAGE"] = v   # capture -> scan -> commit is one waterfall
        return subprocess.run([PY, "-m", "roomctl", "--repo", str(self.repo), *args], cwd=ROOT, env=env,
                              text=True, capture_output=capture)


def robot() -> tuple[str, int]:
    env = pi_link.read_env()
    return env.get("PI_HOST", ""), int(env.get("PI_PORT", "8080") or 8080)


def capture(room: Room) -> Path:
    host, port = robot()
    rec = c2r.capture_once(host, port, "cam0", room.recordings)
    if rec is None:
        raise SystemExit(1)
    return rec


# ── the 3D model of one capture ───────────────────────────────────────────────────
_CLOUDS: dict[str, tuple] = {}            # recording dir -> (pts, rgb, rec): depth is computed ONCE per capture
CELL_M = 0.02                             # the in-repo cloud keeps one point per 2 cm cell


def build_cloud(rec_dir: Path):
    """One capture -> (points Nx3 float32 in the room frame, colours Nx3 uint8, the recording). Same depth -> fuse the scan uses."""
    key = str(rec_dir)
    if key not in _CLOUDS:
        sys.path.insert(0, str(ROOT / "perception")); sys.path.insert(0, str(ROOT))
        import cv2, numpy as np
        import depth, fuse, pipeline                       # noqa: E401
        rec = pipeline.load_recording(rec_dir)
        cam = next(iter(rec.frames))
        frames = {c: cv2.imread(str(f)) for c, f in rec.frames.items()}
        rigs = {c: pipeline._rig(str(f)) for c, f in rec.calib.items()}
        out, _ = depth.depth_capture(frames, rigs, rec.skew_ms, rec.tilt_rate_max)
        xyz, valid, left = out[cam]
        pts = fuse.rect_to_world(xyz[valid], rec.mounts[cam], fuse.odom_to_world(rec.pose)).astype("<f4")
        rgb = cv2.cvtColor(left, cv2.COLOR_BGR2RGB)[valid]
        keep = (np.hypot(pts[:, 0], pts[:, 1]) < 5.0) & (pts[:, 2] > -0.25) & (pts[:, 2] < 3.2)
        _CLOUDS[key] = (pts[keep], rgb[keep], rec)
    return _CLOUDS[key]


def write_ply(path: Path, pts, rgb, comment: str) -> None:
    import numpy as np
    vert = np.empty(len(pts), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
    vert["x"], vert["y"], vert["z"] = pts[:, 0], pts[:, 1], pts[:, 2]
    vert["r"], vert["g"], vert["b"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    with open(path, "wb") as f:
        f.write((f"ply\nformat binary_little_endian 1.0\ncomment {comment}\nelement vertex {len(vert)}\n"
                 "property float x\nproperty float y\nproperty float z\n"
                 "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n").encode())
        f.write(vert.tobytes())


def can_leave(repo: Path) -> str:
    """'' if this repo stays on this machine; otherwise WHY it does not (a remote, or a hook that ships commits)."""
    remotes = subprocess.run(["git", "-C", str(repo), "remote"], capture_output=True, text=True).stdout.split()
    if remotes:
        return f"it has a git remote ({', '.join(remotes)})"
    hooks = repo / ".git" / "hooks"
    live = [h for h in ("post-commit", "post-merge", "pre-push") if (hooks / h).is_file() and os.access(hooks / h, os.X_OK)]
    return f"it has a {live[0]} hook that may ship commits elsewhere" if live else ""


CLOUD_IN_REPO = False          # set by --cloud-in-repo


def cloud_allowed(repo: Path) -> bool:
    why = can_leave(repo) if (repo / ".git").exists() else ""
    if why and not CLOUD_IN_REPO:
        print(f"  {Y}point cloud NOT committed:{X} {why}, and a cloud is a 3D picture of whoever was in view. It is written beside "
              f"the repo instead. (--cloud-in-repo overrides, for a room with nobody in it.)")
        return False
    return True


def stage_cloud(rec_dir: Path, repo: Path) -> dict | None:
    """Put THIS capture's point cloud in the working tree (cloud/current.ply + .json), so the commit that follows IS a
    snapshot of the room. One point per 2 cm cell, sorted, so an unchanged room writes (nearly) the same bytes."""
    if not cloud_allowed(repo):
        return None
    try:
        import numpy as np
        pts, rgb, rec = build_cloud(rec_dir)
        cell = np.floor(pts / CELL_M).astype(np.int32)
        _, first = np.unique(cell, axis=0, return_index=True)          # np.unique sorts the cells: a stable order
        (repo / "cloud").mkdir(exist_ok=True)
        write_ply(repo / "cloud" / "current.ply", pts[first], rgb[first],
                  f"gitspace {rec.capture_id} room frame: x forward y left z up, metres; one point per {CELL_M * 100:.0f} cm cell")
        meta = {"capture_id": rec.capture_id, "at": rec.at, "points": int(len(first)), "points_measured": int(len(pts)),
                "cell_m": CELL_M, "frame": "x forward, y left, z up, floor at z=0, metres; origin = the floor under the robot's camera",
                "pose": rec.pose, "pose_source": json.loads((rec_dir / "capture.json").read_text()).get("pose_source"),
                "skew_ms": rec.skew_ms, "tilt_rate_max": rec.tilt_rate_max,
                "bounds_m": {"min": [round(float(v), 2) for v in pts.min(0)], "max": [round(float(v), 2) for v in pts.max(0)]}}
        (repo / "cloud" / "current.json").write_text(json.dumps(meta, indent=1) + "\n")
        return meta
    except Exception as e:  # noqa: BLE001 -- say so; the scan can still land without its cloud
        print(f"  {Y}(could not stage the point cloud: {type(e).__name__}: {e}){X}")
        return None


def write_scene(rec_dir: Path, scene_dir: Path) -> Path | None:
    """<capture_id>.ply — every measured point, in the room frame (x forward, y left, z up, floor at 0), with the colour
    of the pixel it came from — and a .png to look at without a viewer. Same depth -> fuse as the scan used."""
    try:
        import numpy as np
        pts, rgb, rec = build_cloud(rec_dir)
        scene_dir.mkdir(parents=True, exist_ok=True)
        ply = scene_dir / f"{rec.capture_id}.ply"
        write_ply(ply, pts, rgb, f"gitspace {rec.capture_id} room frame: x forward y left z up, metres")
        vert = pts
        shutil.copyfile(ply, scene_dir / "latest.ply")
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            i = np.random.default_rng(0).choice(len(pts), min(len(pts), 150000), replace=False)
            P, C = pts[i], rgb[i] / 255.0
            fig = plt.figure(figsize=(15, 6.5), facecolor="#0b0c0e")
            for k, (a, b, la, lb, t) in enumerate([(1, 0, "y left (m)", "x forward (m)", "from above"), (0, 2, "x forward (m)", "z up (m)", "from the side")]):
                ax = fig.add_subplot(1, 2, k + 1, facecolor="#0b0c0e")
                ax.scatter(P[:, a], P[:, b], c=C, s=0.4, linewidths=0)
                ax.set_xlabel(la, color="w"); ax.set_ylabel(lb, color="w"); ax.set_title(f"{rec.capture_id} · {t}", color="w")
                ax.tick_params(colors="#aaa"); ax.set_aspect("equal"); ax.grid(color="#222")
                if k == 0:
                    ax.invert_xaxis()
            plt.tight_layout(); plt.savefig(scene_dir / f"{rec.capture_id}.png", dpi=100, facecolor=fig.get_facecolor()); plt.close(fig)
            shutil.copyfile(scene_dir / f"{rec.capture_id}.png", scene_dir / "latest.png")
        except Exception:  # noqa: BLE001 -- the picture is a convenience; the .ply is the model
            pass
        print(f"  3D model: {ply}  ({len(vert):,} coloured points, {ply.stat().st_size / 1e6:.1f} MB) · latest.ply / latest.png beside it")
        return ply
    except Exception as e:  # noqa: BLE001 -- a failed render must not lose a scan that already landed in git
        print(f"  {Y}(no 3D model for this capture: {type(e).__name__}: {e}){X}")
        return None


# ── verbs ─────────────────────────────────────────────────────────────────────────
def cmd_new(a) -> int:
    room = Room(a.name, a.repo)
    if (room.repo / ".git").exists():
        raise SystemExit(f"{room.repo} is already a room.  `status {a.name}` to use it, or pick another name.")
    step(1, 4, f"capture — the robot at {robot()[0]} takes the first picture")
    rec = capture(room)
    step(2, 4, f"create the room repository  {room.repo}")
    room.repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(room.repo)], check=True)
    shutil.copytree(rec / "room", room.repo, dirs_exist_ok=True)          # room.yaml (the pinned cube + zones), anchors/, .roomignore
    step(3, 4, "scan it and make the first commit   (depth -> point cloud -> objects in room.yaml's zones -> git)")
    stage_cloud(rec, room.repo)
    r = room.room("commit", "-m", a.message or f"first scan ({rec.name})", scanner=rec)
    head = subprocess.run(["git", "-C", str(room.repo), "rev-parse", "-q", "--verify", "HEAD"], capture_output=True).returncode == 0
    if not head:
        # `room commit` exits 1 for "nothing to commit" AND for a crashed scan; only a commit proves it worked.
        shutil.rmtree(room.repo, ignore_errors=True)
        print(f"\n{R}the first scan did not produce a commit (exit {r.returncode}) — the error is above. No instance was created.{X}\n"
              f"  the capture is kept: {rec}\n  check the view: python scripts/capture_to_recording.py --check-desk {rec}")
        return 1
    step(4, 4, "the 3D model of what it saw")
    if not a.no_scene:
        write_scene(rec, room.scene)
    room.remember()
    print(f"\n{G}instance `{room.name}` is live.{X}  It is a normal git repo: {room.repo}\n"
          f"  next:  python scripts/room_live.py status          # move something on the desk first, then look again\n"
          f"         python scripts/room_live.py commit -m \"…\"   # record the room as it is now\n"
          f"         python scripts/room_live.py watch --every 15 # keep looking\n"
          f"  {D}0 objects? The scan only looks inside room.yaml's zones: python scripts/capture_to_recording.py --check-desk{X}")
    return 0


def cmd_status(a) -> int:
    room = Room(a.name, a.repo); room.remember()
    step(1, 3, "capture"); rec = capture(room)
    step(2, 3, f"scan + status   ({room.repo})")
    r = room.room("status", "--exit-code", scanner=rec, capture=True)
    sys.stdout.write(r.stdout)
    if "Traceback" in (r.stderr or ""):
        sys.stderr.write(r.stderr)
        print(f"{R}the scan crashed — the room's state was NOT updated; the capture is kept: {rec}{X}")
        return 2
    sys.stderr.write(r.stderr or "")
    step(3, 3, "3D model")
    if not a.no_scene:
        write_scene(rec, room.scene)
    return r.returncode


def cmd_commit(a) -> int:
    room = Room(a.name, a.repo); room.remember()
    step(1, 3, "capture"); rec = capture(room)
    step(2, 3, f"point cloud + scan + commit   ({room.repo})")
    meta = stage_cloud(rec, room.repo)
    message = a.message or f"snapshot {time.strftime('%Y-%m-%d %H:%M:%S')}"
    before = _head(room.repo)
    r = room.room("commit", "-m", f"{message}  [{rec.name}]", scanner=rec, capture=True)
    sys.stdout.write(r.stdout)
    after = _head(room.repo)
    if after == before:                                   # a crashed scan and "nothing to commit" both exit 1
        sys.stderr.write(r.stderr or "")
        subprocess.run(["git", "-C", str(room.repo), "checkout", "-q", "--", "cloud"], capture_output=True)
        print(f"{R}no commit was made — the scan's error is above. The capture is kept: {rec}{X}")
        return 2
    if meta:
        mb = (room.repo / "cloud" / "current.ply").stat().st_size / 1e6
        print(f"  {G}frozen:{X} commit {after[:7]} holds the room at {meta['at']} — {meta['points']:,} points ({mb:.1f} MB) in cloud/current.ply\n"
              f"          get it back any time:  python scripts/room_live.py cloud --ref {after[:7]}")
    step(3, 3, "full-resolution 3D model")
    if not a.no_scene:
        write_scene(rec, room.scene)
    return 0


def cmd_mapshot(a) -> int:
    """Snapshot the robot's own fused room model (scripts/bbos_map.py) into the repo: cloud/current.ply (3 cm coloured
    voxels, SLAM world frame), cloud/map.npz (the same with bbos's labels — what `changes` compares) and cloud/objects.json
    — the separated objects as TEXT, sorted by position, so `git diff` between two snapshots reads as things moving."""
    import bbos_map
    room = Room(a.name, a.repo); room.remember()
    if not (room.repo / ".git").exists():
        step(0, 2, f"create the room repository  {room.repo}")
        room.repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(room.repo)], check=True)
        for rel in ("room.yaml", ".roomignore"):
            if (ROOT / "room.git" / rel).is_file():
                shutil.copyfile(ROOT / "room.git" / rel, room.repo / rel)
    step(1, 2, "read the robot's fused map (bbos mapping.voxels + slam.pose, read-only)")
    d = Path(a.dir).expanduser() if getattr(a, "dir", None) else bbos_map.pull()
    # THE CAMERA'S LAYER on top of the map (what 3 cm voxels cannot hold): one gated stereo capture, placed in the map's
    # frame through the verified chain, gives dense 1 cm colour within 3 m and the things standing on the floor that the
    # map's floor label absorbs (a can, a crisp bag). Skipped, not faked, when the robot is moving or the capture does not
    # line up with the map. --no-capture skips it; --recording DIR uses a saved capture taken from where this map says
    # the robot stood (the offline path).
    layer = None
    if not getattr(a, "no_capture", False):
        rec = getattr(a, "recording", None)
        if rec is None:
            host, port = robot()
            rec = c2r.capture_once(host, port, "cam0", room.recordings, say=print)
        if rec is not None:
            layer = bbos_map.capture_layer(d, recording=Path(rec).expanduser(), say=print)
    (room.repo / "cloud").mkdir(exist_ok=True)
    keep = cloud_allowed(room.repo)
    for src, dst in (("map.ply", "current.ply"), ("map.npz", "map.npz"), ("objects.json", "objects.json")):
        if keep or dst == "objects.json":                 # positions and sizes are text about THINGS; the voxels are a picture
            shutil.copyfile(d / src, room.repo / "cloud" / dst)
    meta = json.loads((d / "objects.json").read_text())
    (room.repo / "cloud" / "current.json").write_text(json.dumps({k: v for k, v in meta.items() if k != "objects"} | {"points": meta["voxels"]}, indent=1) + "\n")
    # FILL THE SCENE: the viewer (web/scene_api.py, /scene) lists <instance>.scene/map_<YYYYMMDDHHMMSS>.{ply,json,png} and
    # follows latest.*. The .json is what lets it draw more than points: each object's box, and where the robot stood and
    # which way it faced (heading h: it faces (-sin h, cos h) — measured, docs/20 Fact 3).
    room.scene.mkdir(parents=True, exist_ok=True)
    sid = "map_" + d.name.replace("-", "")
    shutil.copyfile(d / "map.png", room.scene / f"map-{d.name}.png")
    for src, ext in (("map.ply", "ply"), ("objects.json", "json"), ("map.png", "png")):
        shutil.copyfile(d / src, room.scene / f"{sid}.{ext}")
        shutil.copyfile(d / src, room.scene / f"latest.{ext}")
    if layer is not None and rec is not None and not getattr(a, "no_scene", False):
        # the capture's own full-resolution model, cap_NNNN.ply, beside the map: /scene and /robot place it by the pose
        # recorded at the shutter (pose_bb), so a turn through six headings reads as six views of one room
        write_scene(Path(rec).expanduser(), room.scene)
    if layer is not None:                                          # the camera's layer: dense points + floor objects
        for name in (f"{sid}.dense.ply", "latest.dense.ply"):
            shutil.copyfile(d / "dense.ply", room.scene / name)
        for name in (f"{sid}.json", "latest.json"):
            side = json.loads((room.scene / name).read_text())
            side["floor_objects"] = layer["floor_objects"]; side["dense_points"] = layer["dense_points"]; side["capture"] = layer["capture"]
            (room.scene / name).write_text(json.dumps(side, indent=1))
        (room.repo / "cloud" / "floor_objects.json").write_text(json.dumps(layer["floor_objects"], indent=1) + "\n")   # text about things: goes in the commit
    n_obj = sum(o["kind"] == "object" for o in meta["objects"])
    # NAMES (--name): perception's own path, bb_source.scan_into_bb — the map's objects inside room.yaml's zones become
    # zones/<zone>/<name>.yaml in the repo, and the head frame from the capture we just took (placed by its own SLAM pose)
    # gives them names where the segmenter recognises them (laptop_…, else unknown_…). Needs zones: `room_live.py zone`.
    if getattr(a, "name_objects", False):
        zones = (yaml.safe_load((room.repo / "room.yaml").read_text()) if (room.repo / "room.yaml").is_file() else {}) or {}
        if not zones.get("zones"):
            print(f"  {Y}no zones in room.yaml: nothing to name.{X}  python scripts/room_live.py zone {room.name}   measures the table's zone from this map")
        else:
            os.environ.setdefault("ROOM_ES", "off")
            bbos_map.scan(room.repo, d, recording=Path(rec).expanduser() if (layer is not None and rec is not None) else None)
    step(2, 2, f"commit   ({room.repo})")
    before = _head(room.repo)
    message = a.message or f"map snapshot {time.strftime('%Y-%m-%d %H:%M:%S')}"
    r = room.room("commit", "--no-scan", "-m", f"{message}  [{n_obj} objects, {meta['voxels']} voxels]", capture=True)
    sys.stdout.write(r.stdout)
    after = _head(room.repo)
    if after == before:
        sys.stderr.write(r.stderr or "")
        print(f"{Y}nothing was committed (the map has not changed, or the commit failed — see above){X}")
        return 1
    print(f"  {G}frozen:{X} commit {after[:7]} holds the room as the robot has mapped it — {meta['voxels']:,} voxels, {n_obj} objects, SLAM "
          f"{'localized' if meta['slam']['localized'] else 'NOT localized'}\n          picture: {room.scene / ('map-' + d.name + '.png')}\n"
          f"          what changed since the last one:  python scripts/room_live.py changes\n"
          f"          see it in 3D (this laptop only):   http://localhost:8000/scene")
    return 0


def cmd_zone(a) -> int:
    """Measure the table: the largest horizontal surface between 0.45 and 1.15 m in the robot's map (bbos_map.measure_surface)
    -> room.yaml `zones: {table: …}` of the instance, so `add --name` and the scan have somewhere to look for objects."""
    import bbos_map
    import numpy as np
    room = Room(a.name, a.repo); room.remember()
    d = Path(a.dir).expanduser() if getattr(a, "dir", None) else bbos_map.pull()
    r = bbos_map.measure_surface(dict(np.load(d / "map.npz")))
    if not r:
        raise SystemExit("no horizontal surface between 0.45 and 1.15 m in this map: is the table in view? (bbos_map.py surface)")
    ry = room.repo / "room.yaml"
    doc = (yaml.safe_load(ry.read_text()) if ry.is_file() else {}) or {}
    # REPLACE the instance's zones, do not add to them. A room.yaml copied from room.git carries THAT room's `desk` and
    # `shelf`; here they overlap the table just measured, and candidates are formed per zone with each cell going to the
    # first zone that claims it — so a laptop on the boundary is cut in half and names nothing (measured 2026-09-20:
    # 60 candidates, no name; the same map and frame with the table alone: 51 candidates, laptop_73b0). The zones of a
    # different room have no business in an instance of this one. --keep adds to what is there instead.
    before = dict(doc.get("zones") or {})
    if getattr(a, "keep", False):
        doc.setdefault("zones", {})[a.zone] = r["zone"]
        dropped = []
    else:
        dropped = [k for k in before if k != a.zone]
        doc["zones"] = {a.zone: r["zone"]}
        for k in dropped:                                   # and the objects a scan once put in those zones: gone with them
            if (room.repo / "zones" / k).is_dir():
                shutil.rmtree(room.repo / "zones" / k)
    ry.write_text(yaml.safe_dump(doc, sort_keys=False))
    z = r["zone"]
    print(f"  zone {G}{a.zone}{X}: surface at {r['surface_z']} m, {r['area_m2']} m² · x {z['min'][0]}..{z['max'][0]}  y {z['min'][1]}..{z['max'][1]}  -> {ry}")
    print(f"  room.yaml zones now: {', '.join(doc['zones'])}" + (f"   (dropped, inherited from another room, with their zones/ objects: {', '.join(dropped)})" if dropped
          else ("   (kept what was there: --keep)" if getattr(a, "keep", False) and len(before) > 1 else "")))
    return 0


def cmd_place(a) -> int:
    """Say which ROOM an instance is of — `place: <text>` in its room.yaml — so the viewers can label and group instances by
    place instead of leaving a judge to work out that two names are the same hallway on different days."""
    room = Room(a.name, a.repo)
    ry = room.repo / "room.yaml"
    doc = (yaml.safe_load(ry.read_text()) if ry.is_file() else {}) or {}
    if a.text is None:
        print(f"  {room.name}: place = {doc.get('place') or '(not set)'}"); return 0
    doc["place"] = a.text
    ry.write_text(yaml.safe_dump(doc, sort_keys=False))
    print(f"  {room.name}: place = {a.text}  -> {ry}")
    return 0


def cmd_changes(a) -> int:
    """OLD -> NEW as OBJECTS: both maps come out of git (cloud/map.npz at each commit) and are compared in the robot's
    world frame. The textual twin is `git diff OLD NEW -- cloud/objects.json`."""
    import io
    import numpy as np
    import bbos_map
    room = Room(a.name, a.repo)
    old_ref, new_ref = a.old or "HEAD~1", a.new or "HEAD"
    maps = []
    for ref in (old_ref, new_ref):
        blob = subprocess.run(["git", "-C", str(room.repo), "show", f"{ref}:cloud/map.npz"], capture_output=True)
        if blob.returncode:
            raise SystemExit(f"`{ref}` has no cloud/map.npz — it is not a --map snapshot.  python scripts/room_live.py log")
        maps.append(dict(np.load(io.BytesIO(blob.stdout))))
    ch = bbos_map.diff(*maps)
    short = [subprocess.run(["git", "-C", str(room.repo), "log", "-1", "--format=%h %ci", r], capture_output=True, text=True).stdout.strip() for r in (old_ref, new_ref)]
    print(f"  {short[0]}  ->  {short[1]}")
    if not ch["same_frame"]:
        print(f"  {Y}the map's frame differs between these (a robot reboot resets SLAM): positions may not be comparable{X}")
    for kind, col in (("appeared", G), ("gone", R)):
        print(f"  {col}{kind.upper()}: {len(ch[kind])}{X}")
        for o in ch[kind]:
            print(f"      at ({o['centre_m'][0]:+.2f}, {o['centre_m'][1]:+.2f})  {o['size_m'][0] * 100:.0f} x {o['size_m'][1] * 100:.0f} x {o['size_m'][2] * 100:.0f} cm  top {o['top_m']:.2f} m  {o['voxels']} voxels")
    room.scene.mkdir(parents=True, exist_ok=True)
    pic = room.scene / f"changes-{short[0].split()[0]}-{short[1].split()[0]}.png"
    bbos_map.render(pic, maps[1], bbos_map.objects_of(maps[1]), ch)
    print(f"  picture: {pic}")
    return 0


def _head(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "-q", "--verify", "HEAD"], capture_output=True, text=True).stdout.strip()


def cmd_cloud(a) -> int:
    """The point cloud as it was at REF -> a .ply on disk. `git show REF:cloud/current.ply`, with the metadata printed."""
    room = Room(a.name, a.repo)
    ref = a.ref or "HEAD"
    sha = subprocess.run(["git", "-C", str(room.repo), "rev-parse", "--short", f"{ref}^{{commit}}"], capture_output=True, text=True)
    if sha.returncode:
        raise SystemExit(f"`{ref}` is not a commit in {room.repo}.  python scripts/room_live.py log")
    blob = subprocess.run(["git", "-C", str(room.repo), "show", f"{ref}:cloud/current.ply"], capture_output=True)
    if blob.returncode:
        raise SystemExit(f"commit {sha.stdout.strip()} has no cloud/current.ply (it predates snapshots).")
    meta = subprocess.run(["git", "-C", str(room.repo), "show", f"{ref}:cloud/current.json"], capture_output=True, text=True).stdout
    out = Path(a.out).expanduser() if a.out else room.scene / f"at-{sha.stdout.strip()}.ply"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob.stdout)
    when = subprocess.run(["git", "-C", str(room.repo), "log", "-1", "--format=%ci · %s", ref], capture_output=True, text=True).stdout.strip()
    m = json.loads(meta) if meta.strip() else {}
    print(f"the room at {sha.stdout.strip()}  ({when})\n  {m.get('points', '?'):,} points · capture {m.get('capture_id')} · pose_source {m.get('pose_source')}\n  -> {out}")
    return 0


def cmd_watch(a) -> int:
    room = Room(a.name, a.repo); room.remember()
    print(f"watching `{room.name}` every {a.every:g} s{' and committing each change' if a.commit else ''} — Ctrl-C stops.  {room.repo}")
    try:
        while True:
            t0 = time.monotonic()
            host, port = robot()
            rec = c2r.capture_once(host, port, "cam0", room.recordings, say=lambda *_: None)
            stamp = time.strftime("%H:%M:%S")
            if rec is None:
                print(f"  {stamp}  {Y}no capture (robot moving, or unreachable) — trying again{X}")
            else:
                r = room.room("status", "--json", scanner=rec, capture=True)
                try:
                    st = json.loads(r.stdout)
                except ValueError:
                    print(f"  {stamp}  {rec.name}  {R}scan failed{X}: {(r.stderr or r.stdout).strip().splitlines()[-1][:160] if (r.stderr or r.stdout).strip() else ''}")
                    st = None
                if st is not None:
                    changes = st.get("changes") or []
                    if not changes:
                        print(f"  {stamp}  {rec.name}  {G}clean{X}")
                    else:
                        what = ", ".join(f"{c.get('type')} {c.get('object_id')}" + (f" ({c['delta_m']} m)" if c.get("delta_m") else "") for c in changes[:6])
                        print(f"  {stamp}  {rec.name}  {Y}{len(changes)} change{'s' if len(changes) != 1 else ''}{X}: {what}")
                        if a.commit:
                            stage_cloud(rec, room.repo)
                            c = room.room("commit", "--no-scan", "-m", f"watch: {len(changes)} change{'s' if len(changes) != 1 else ''}  [{rec.name}]", capture=True)
                            print(f"            {(c.stdout or c.stderr).strip().splitlines()[0] if (c.stdout or c.stderr).strip() else ''}")
                    if not a.no_scene:
                        write_scene(rec, room.scene)
            time.sleep(max(1.0, a.every - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0


def cmd_pass(a) -> int:
    room = Room(a.name, a.repo)
    return room.room(a.verb, *a.rest).returncode


def cmd_list(_a) -> int:
    cur = CURRENT.read_text().strip() if CURRENT.exists() else ""
    rooms = sorted(p for p in ROOMS.glob("*") if (p / ".git").is_dir()) if ROOMS.exists() else []
    if not rooms:
        print("no instances yet.  python scripts/room_live.py new <name>")
    for p in rooms:
        n = subprocess.run(["git", "-C", str(p), "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout.strip() or "0"
        last = subprocess.run(["git", "-C", str(p), "log", "-1", "--format=%cr · %s"], capture_output=True, text=True).stdout.strip()
        print(f"  {'*' if p.name == cur else ' '} {p.name:20s} {n:>3} commits · {last}\n      {D}{p}{X}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="verb", required=True)

    def common(p, name_required=False):
        p.add_argument("--cloud-in-repo", action="store_true", help="commit point clouds even into a repo that can leave this "
                       "machine (a remote, or a shipping hook). A cloud is a 3D picture of whoever was in view")
        p.add_argument("name", nargs=None if name_required else "?", help="the instance (default: the last one used)")
        p.add_argument("--repo", type=Path, help="any room repository instead of an instance, e.g. ./room.git (the real room)")
        p.add_argument("--no-scene", action="store_true", help="skip writing the 3D model")

    p = sub.add_parser("new", help="create an instance: fresh room repo, first capture, first commit"); common(p, True)
    p.add_argument("-m", "--message")
    p = sub.add_parser("status", help="capture + scan -> what changed since the last commit"); common(p)
    p = sub.add_parser("commit", help="capture + scan -> record the room as it is now"); common(p)
    p.add_argument("-m", "--message", required=True)
    p = sub.add_parser("snapshot", help="FREEZE the room now: capture -> point cloud -> one commit (message optional)"); common(p)
    p.add_argument("-m", "--message")
    p.add_argument("--map", action="store_true", help="snapshot the robot's own FUSED map (bbos) instead of one stereo capture")
    p = sub.add_parser("add", help="`git add` for the room: the robot's full fused map -> one commit -> fills the scene"); common(p)
    p.add_argument("--no-capture", action="store_true", help="the map only: no stereo capture, so no dense layer and no floor objects")
    p.add_argument("--recording", type=Path, help="a saved capture (offline): its dense layer + floor objects, placed by where THIS map says the robot stood")
    p.add_argument("--dir", type=Path, help="an already-pulled map snapshot dir (offline, with --recording) instead of pulling one from the robot")
    p.add_argument("--name", dest="name_objects", action="store_true", help="also NAME the zone objects into the repo (perception's scan_into_bb + the head frame); needs `zone` first")
    p.add_argument("-m", "--message")
    p = sub.add_parser("zone", help="measure the table's zone from the robot's map into room.yaml (needed by `add --name`)"); common(p)
    p.add_argument("--zone", default="table"); p.add_argument("--dir", type=Path, help="an already-pulled map snapshot dir instead of pulling one")
    p.add_argument("--keep", action="store_true", help="add this zone to the ones already in room.yaml instead of replacing them (default: replace — inherited zones cut objects in half)")
    p = sub.add_parser("place", help="which room this instance is of: `place NAME \"HTN venue hallway\"` (shown by /scene and /robot)"); common(p)
    p.add_argument("text", nargs="?", default=None)
    p = sub.add_parser("changes", help="objects that appeared / are gone between two --map snapshots"); common(p)
    p.add_argument("--old", help="default HEAD~1"); p.add_argument("--new", help="default HEAD")
    p = sub.add_parser("explore", help="drive around so `add` has more to add: scripts/room_explore.py (plan only without --go)")
    p.add_argument("rest", nargs=argparse.REMAINDER, help="room_explore.py's own arguments, e.g. --go --minutes 3 --every 30")
    p = sub.add_parser("cloud", help="the point cloud as it was at a commit (default HEAD) -> a .ply file"); common(p)
    p.add_argument("--ref", help="a commit: a sha, HEAD~2, a tag (default HEAD)")
    p.add_argument("--out", help="where to write it (default: <instance>.scene/at-<sha>.ply)")
    p = sub.add_parser("watch", help="keep looking; print (and optionally commit) every change"); common(p)
    p.add_argument("--every", type=float, default=15.0)
    p.add_argument("--commit", action="store_true", help="commit whenever something changed")
    for v in ("log", "diff"):
        p = sub.add_parser(v, help=f"`room {v}` on the instance"); common(p)
        p.add_argument("rest", nargs=argparse.REMAINDER)
    sub.add_parser("list", help="your instances")
    a = ap.parse_args()
    import contextlib
    tx = contextlib.nullcontext()
    if a.verb in ("new", "status", "commit", "snapshot", "add", "watch"):
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
            sys.path.insert(0, str(ROOT))
            import obs
            if obs.init("link") and a.verb != "watch":     # watch opens none: a transaction that lasts for hours is one endless span
                tx = obs.transaction("room_live", f"room_live {a.verb}")
        except Exception:  # noqa: BLE001 -- no Sentry, same tool
            obs = None
    with tx:
        with contextlib.suppress(Exception):
            import sentry_sdk
            tp = sentry_sdk.get_traceparent()
            if tp and a.verb != "watch":
                print(f"{D}sentry trace {tp.split('-')[0]}  (the robot's latch continues it; find the waterfall by this id){X}")
        rc = _dispatch(a)
    with contextlib.suppress(Exception):
        obs.flush(3)
    return rc


def _dispatch(a) -> int:
    global CLOUD_IN_REPO
    CLOUD_IN_REPO = bool(getattr(a, "cloud_in_repo", False))
    if a.verb == "add" or (a.verb == "snapshot" and getattr(a, "map", False)):
        return cmd_mapshot(a)
    if a.verb == "place":
        return cmd_place(a)
    if a.verb == "zone":
        return cmd_zone(a)
    if a.verb == "explore":                            # a thin call: the guards, the prompt and the log all live in room_explore.py
        import room_explore
        sys.argv = ["room_explore.py", *a.rest]
        return room_explore.main()
    if a.verb == "changes":
        return cmd_changes(a)
    return {"new": cmd_new, "status": cmd_status, "commit": cmd_commit, "snapshot": cmd_commit, "cloud": cmd_cloud,
            "watch": cmd_watch, "log": cmd_pass, "diff": cmd_pass, "list": cmd_list}[a.verb](a)


if __name__ == "__main__":
    sys.exit(main())
