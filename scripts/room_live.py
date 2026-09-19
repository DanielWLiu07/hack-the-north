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


def stage_cloud(rec_dir: Path, repo: Path) -> dict | None:
    """Put THIS capture's point cloud in the working tree (cloud/current.ply + .json), so the commit that follows IS a
    snapshot of the room. One point per 2 cm cell, sorted, so an unchanged room writes (nearly) the same bytes."""
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
    if a.verb in ("new", "status", "commit", "snapshot", "watch"):
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
    return {"new": cmd_new, "status": cmd_status, "commit": cmd_commit, "snapshot": cmd_commit, "cloud": cmd_cloud,
            "watch": cmd_watch, "log": cmd_pass, "diff": cmd_pass, "list": cmd_list}[a.verb](a)


if __name__ == "__main__":
    sys.exit(main())
