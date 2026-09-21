#!/usr/bin/env python3
"""The chip-packet demo room: a history, two branches that disagree, and a way back.

    python scripts/demo_chips.py state     # what the room looks like now
    python scripts/demo_chips.py reset     # put it back to the demo state, between runs
    python scripts/demo_chips.py seed      # build it from the recordings (slow: it scans)

WHAT THE DEMO IS. `~/.cache/gitspace/rooms/chips` is a room built from two real captures of the
same patch of hallway floor — a crisp packet and a small box. Its history:

    room init                                     an empty room
    first scan: the room's frame is set           the anchor capture defines the coordinates
    the floor as cap_0018 sees it                 the packet and the box are committed
    named the two things on the floor             the labels, pinned
    rescanned: nothing changed                    the same floor again: an EMPTY commit

and then two branches that disagree about the packet:

    eaten    someone took the chip packet         the object is gone
    kicked   the chip packet was kicked           the object is 39 cm away

Merging them is the point: git can only say "a file was deleted here and modified there", while
perception/roomdiff.py says WHICH object, where each branch puts it, and that the deciding
evidence is in the room. The small box merges cleanly either way, so the merge is doing real
work rather than simply failing.

RESET exists because a demo is run more than once. The three branch tips are tagged at seed
time; `reset` moves main, eaten and kicked back to those tags and cleans the tree, in under a
second, without rescanning anything.

The two divergent commits are staged by hand: nobody ate or kicked anything. The objects, their
ids, their poses and the 39 cm are real measurements from real scans.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOMS = Path.home() / ".cache/gitspace/rooms"
ROOM, RECORDINGS = ROOMS / "chips", ROOMS / "hallway-test.recordings"
ANCHOR, SECOND = "cap_0018", "cap_0018"          # the anchor defines the room's frame; the second
                                                 # pass is the SAME capture: a rescan that changes nothing,
                                                 # which is the room saying so rather than id churn
PACKET_AT = (0.82, -0.58)        # m: where cap_0018 sees the chip packet. Picked by PLACE, not by name:
                                 # the describing model calls it a snack bag, a crumpled packet or a paper
                                 # scrap depending on the run, and a demo cannot be hostage to that
NAMES = {"packet": "chip packet", "other": "small box"}   # pinned for the same reason
TAGS = {"main": "demo/main", "eaten": "demo/eaten", "kicked": "demo/kicked"}
CELL_M = None                                    # None = every measured point. room_live thins to one per
                                                 # 2 cm cell for a repo it commits often; here detail wins —
                                                 # at 2 cm a crisp packet is a dozen cells and you cannot
                                                 # tell what it is
MOVE = (0.30, 0.25)                              # m, how far "kicked" moves the packet: 39 cm, onto
                                                 # OPEN floor. The packet's points travel with the
                                                 # record, so the spot has to be one the camera saw
                                                 # bare ground in — under the chair it reads as debris
WHO = ("-c", "user.email=room@gitirl", "-c", "user.name=room")


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(ROOM), *args], capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def state() -> int:
    if not (ROOM / ".git").exists():
        print(f"no demo room at {ROOM} — run: python scripts/demo_chips.py seed")
        return 1
    print(git("log", "--oneline", "--graph", "--all", "-8"))
    print(f"\non {git('rev-parse', '--abbrev-ref', 'HEAD')}, "
          f"{len(list((ROOM / 'zones').rglob('*.yaml')))} object(s) in the tree")
    dirty = git("status", "--porcelain")
    print("working tree: " + ("clean" if not dirty else f"DIRTY\n{dirty}"))
    cur = (ROOMS / ".current").read_text().strip() if (ROOMS / ".current").exists() else ""
    print(f"rooms/.current: {cur or 'unset'}" + ("" if cur == ROOM.name else f"  (a page with no ?instance= opens {cur or 'whichever room has a model'})"))
    print("\n  page     http://127.0.0.1:8000/robot?instance=chips")
    print(f"  merge    .venv/bin/python perception/roomdiff.py {ROOM} "
          f"--merge {TAGS['main']} {TAGS['kicked']} {TAGS['eaten']}")
    return 0


def _make_current() -> None:
    """Point rooms/.current at this room, so a page opened with no ?instance= is the demo.

    `.current` is what scene_api and room_live.py read for "the room being worked in", and the
    /robot page opens it when nothing else is asked for. It is one line of text and `room_live.py`
    rewrites it whenever it adds to another room: this is a demo pointer, not a claim on the disk.
    """
    (ROOMS / ".current").write_text(ROOM.name + "\n")


def reset() -> int:
    """Back to the state `seed` left, without rescanning: the tags are the demo."""
    missing = [t for t in TAGS.values() if not git("tag", "-l", t)]
    if missing:
        print(f"missing tag(s) {missing} — run: python scripts/demo_chips.py seed")
        return 1
    git("checkout", "-q", "main")
    git("reset", "-q", "--hard", TAGS["main"])
    git("clean", "-qfd")
    for branch, tag in TAGS.items():
        if branch != "main":
            git("branch", "-f", branch, tag)
    _make_current()
    print("the chip demo is back:")
    return state()


def seed() -> int:
    """Build the room from the recordings. Scans, so it takes a minute."""
    import shutil

    if not (RECORDINGS / ANCHOR / "capture.json").is_file():
        print(f"no recording {RECORDINGS / ANCHOR}")
        return 1
    shutil.rmtree(ROOM, ignore_errors=True)
    env_scan = {"ROOM_ES": "off", "GITSPACE_DESCRIBE": "1"}

    def room(*args: str, capture: str | None = None) -> None:
        import os

        env = dict(os.environ, **env_scan)
        if capture:
            env["ROOM_SCANNER"] = f"perception:{RECORDINGS / capture}"
        r = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "roomctl", "--repo", str(ROOM), *args],
                           cwd=ROOT, env=env, capture_output=True, text=True)
        print("  " + (r.stdout or r.stderr).strip().splitlines()[-1][:88])

    print("scanning (the room's frame is whichever capture lands first):")
    room("init", capture=ANCHOR)
    _cloud(ANCHOR)
    room("commit", "-m", f"first scan: the room's frame is set  [{ANCHOR}]", capture=ANCHOR)
    _cloud(SECOND)
    room("commit", "-m", f"the floor as {SECOND} sees it  [{SECOND}]", capture=SECOND)
    _pin_names()                                   # before the rescans, and that ORDER is the point
    _cloud(SECOND)
    room("commit", "-m", f"rescanned: nothing changed  [{SECOND}]", capture=SECOND)
    if git("status", "--porcelain") or "nothing changed" not in git("log", "-1", "--format=%s"):
        git("add", "-A")
        git(*WHO, "commit", "-q", "--allow-empty", "-m",
            f"rescanned: nothing changed  [{SECOND}]")

    base = git("rev-parse", "HEAD")
    packet = _packet_path()
    git("checkout", "-q", "-B", "eaten", base)
    gone = _cloud_without(ANCHOR, packet)          # the picture agrees with the record
    git("rm", "-q", packet)
    git("add", "-A")
    git(*WHO, "commit", "-qm", f"someone took the chip packet  [{ANCHOR}]")
    print(f"  eaten: {gone} points of the packet taken out of that branch's cloud")

    git("checkout", "-q", "-B", "kicked", base)
    moved = _cloud_moved(ANCHOR, packet, *MOVE)    # the packet's points go where the record says
    _move_packet(packet, *MOVE)
    git("add", "-A")
    git(*WHO, "commit", "-qm", f"the chip packet was kicked across the floor  [{ANCHOR}]")
    print(f"  kicked: {moved} points of the packet carried {MOVE[0] * 100:.0f} cm forward and "
          f"{abs(MOVE[1]) * 100:.0f} cm {'left' if MOVE[1] > 0 else 'right'} in that branch's cloud")

    git("checkout", "-q", "main")
    for branch, tag in TAGS.items():
        git("tag", "-f", tag, branch if branch != "main" else "HEAD")
    _scene_files()
    _make_current()
    print()
    return state()


def _cloud(capture: str, drop=None, shift=None) -> int:
    """The commit's own cloud, written the way scripts/room_live.py writes one.

    Per-commit clouds are what make the History graph a graph of COMMITS — branches and all —
    rather than one node per capture file. The metadata beside it is not decoration: the page
    reads `pose` to stand the robot in the scene and `bounds_m` to frame it, so a cloud with a
    two-line sidecar draws as a bare drift of points with nothing to judge it by.

    `drop` is a pixel mask to leave out (the packet, on the branch where it is gone), and `shift`
    is (mask, dx, dy): the same pixels carried across the floor, for the branch where it was
    kicked. A record that says the packet moved over a cloud that still shows it where it was
    puts the box — and the octree cell under it — a whole packet-length from the thing it names.
    """
    import sys as _sys

    _sys.path[:0] = [str(ROOT), str(ROOT / "perception")]
    import cv2
    import numpy as np
    import depth, fuse, pipeline

    rec = pipeline.load_recording(RECORDINGS / capture)
    cam = next(iter(rec.frames))
    out, _ = depth.depth_capture({c: cv2.imread(str(f)) for c, f in rec.frames.items()},
                                 {c: pipeline._rig(str(f)) for c, f in rec.calib.items()},
                                 rec.skew_ms, rec.tilt_rate_max)
    xyz, valid, left = out[cam]
    if drop is not None:
        valid = valid & ~drop
    import roomdiff

    pose, _ = roomdiff.pose_for(ROOM, RECORDINGS / capture)
    pts = fuse.rect_to_world(xyz[valid], rec.mounts[cam], pose).astype("<f4")
    if shift is not None:                                      # the packet's own pixels, carried
        mask, dx, dy = shift
        sel = mask[valid]
        pts[sel, 0] += dx
        pts[sel, 1] += dy
    rgb = cv2.cvtColor(left, cv2.COLOR_BGR2RGB)[valid]
    keep = (np.hypot(pts[:, 0], pts[:, 1]) < 5.0) & (pts[:, 2] > -0.25) & (pts[:, 2] < 3.2)
    pts, rgb = pts[keep], rgb[keep]
    if CELL_M:
        cell = np.floor(pts / CELL_M).astype(np.int32)
        _, first = np.unique(cell, axis=0, return_index=True)  # one point per cell, sorted: stable bytes
    else:
        first = np.arange(len(pts))                            # every point the camera measured
    (ROOM / "cloud").mkdir(exist_ok=True)
    vert = np.empty(len(first), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
    vert["x"], vert["y"], vert["z"] = pts[first, 0], pts[first, 1], pts[first, 2]
    vert["r"], vert["g"], vert["b"] = rgb[first, 0], rgb[first, 1], rgb[first, 2]
    with open(ROOM / "cloud" / "current.ply", "wb") as f:
        detail = f"one point per {CELL_M * 100:.0f} cm cell" if CELL_M else "every measured point"
        f.write((f"ply\nformat binary_little_endian 1.0\ncomment gitspace {capture} room frame: x forward y left "
                 f"z up, metres; {detail}\nelement vertex {len(vert)}\n"
                 "property float x\nproperty float y\nproperty float z\n"
                 "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n").encode())
        f.write(vert.tobytes())
    (ROOM / "cloud" / "current.json").write_text(json.dumps({
        "capture_id": capture, "at": rec.at, "points": int(len(first)), "points_measured": int(len(pts)),
        "cell_m": CELL_M,   # null: nothing thinned
        "frame": "x forward, y left, z up, floor at z=0, metres; origin = the floor under the robot's camera",
        "pose": fuse.world_to_odom(pose[0], pose[1], pose[2]), "pose_source": "registered to this room's anchor",
        "skew_ms": rec.skew_ms, "tilt_rate_max": rec.tilt_rate_max,
        "bounds_m": {"min": [round(float(v), 2) for v in pts.min(0)], "max": [round(float(v), 2) for v in pts.max(0)]},
    }, indent=1) + "\n")
    git("add", "-A")
    return int(len(first))


def _packet_pixels(capture: str, rec_path: str):
    """Which pixels of `capture` ARE the committed packet, as the detector sees them.

    Not a box: the floor in this frame spans -3 to +5 cm while the packet is 8 cm tall, so any
    height cut through a box takes floor with it and leaves a void where the ground should be.
    """
    import sys as _sys

    _sys.path[:0] = [str(ROOT), str(ROOT / "perception")]
    import cv2
    import numpy as np
    import difference, roomdiff, segment
    from roomctl.state import from_yaml

    rec = from_yaml((ROOM / rec_path).read_text())
    pose, _ = roomdiff.pose_for(ROOM, RECORDINGS / capture)
    cam, view = difference.load_view(RECORDINGS / capture)
    found, _ = segment.run(view.xyz, view.valid, view.image, cam, segmenter=lambda im: [],
                           mount=view.mount, robot_pose=pose, floor=True)
    if not found:
        raise SystemExit(f"the floor detector found nothing in {capture}: nothing to take out")
    want = np.array([rec.pose.x, rec.pose.y])
    inst = min(found, key=lambda i: float(np.linalg.norm(i.box()[0][:2] - want)))
    away = float(np.linalg.norm(inst.box()[0][:2] - want))
    if away > 0.25:
        raise SystemExit(f"nearest detection is {away * 100:.0f} cm from the committed packet — wrong object")
    # the mask is the packet's CORE; stereo smears a bright object a few pixels wider than it is,
    # and those pixels are the packet too
    wider = cv2.dilate(inst.mask.astype("uint8"), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))).astype(bool)
    return wider, int((wider & view.valid).sum())


def _cloud_without(capture: str, rec_path: str) -> int:
    """This commit's cloud with the packet's own points left out: the branch where it is gone."""
    mask, n = _packet_pixels(capture, rec_path)
    _cloud(capture, drop=mask)
    return n


def _cloud_moved(capture: str, rec_path: str, dx: float, dy: float) -> int:
    """This commit's cloud with the packet's own points MOVED, so the picture and the record
    agree about where it is. Without this the branch commits a pose 39 cm from the only points
    that show a packet, and every reader of that pose — the 3D box, the octree cell the Objects
    tab drills, a diff against another branch — inherits the lie."""
    mask, n = _packet_pixels(capture, rec_path)
    _cloud(capture, shift=(mask, dx, dy))
    return n


def _packet_path() -> str:
    """The chip packet: the committed object nearest where cap_0018 sees it."""
    import re

    best, best_d = None, 1e9
    for f in sorted((ROOM / "zones").rglob("*.yaml")):
        t = f.read_text()
        x = float(re.search(r"^  x: (\S+)", t, re.M).group(1))
        y = float(re.search(r"^  y: (\S+)", t, re.M).group(1))
        d = ((x - PACKET_AT[0]) ** 2 + (y - PACKET_AT[1]) ** 2) ** 0.5
        if d < best_d:
            best, best_d = f, d
    if best is None or best_d > 0.40:
        raise SystemExit(f"no object near {PACKET_AT} among {[p.name for p in (ROOM / 'zones').rglob('*.yaml')]}")
    return str(best.relative_to(ROOM))


def _pin_names() -> None:
    """One commit that fixes the two labels, so the demo reads the same every time it is seeded.

    It runs BEFORE the rescans, and that is not cosmetic. An object's id is minted from its class
    at first sight, so while the label is still whatever the describing model said this run, the
    next scan of the SAME capture calls it something else and the room commits two renames under
    the subject "nothing changed". With the label pinned first, that scan has nothing to say and
    the commit is genuinely empty — which is the claim the demo is making.
    """
    import re

    packet = _packet_path()
    for f in sorted((ROOM / "zones").rglob("*.yaml")):
        want = NAMES["packet"] if str(f.relative_to(ROOM)) == packet else NAMES["other"]
        t = f.read_text()
        if re.search(r"^class: .*$", t, re.M).group(0) != f"class: {want}":
            f.write_text(re.sub(r"^class: .*$", f"class: {want}", t, count=1, flags=re.M))
    git("add", "-A")
    if git("status", "--porcelain"):
        git(*WHO, "commit", "-qm", "named the two things on the floor")


def _move_packet(rel: str, dx: float, dy: float) -> None:
    import re

    p = ROOM / rel
    s = p.read_text()
    x = float(re.search(r"^  x: (\S+)", s, re.M).group(1))
    y = float(re.search(r"^  y: (\S+)", s, re.M).group(1))
    s = re.sub(r"^  x: \S+", f"  x: {x + dx:.2f}", s, count=1, flags=re.M)
    s = re.sub(r"^  y: \S+", f"  y: {y + dy:.2f}", s, count=1, flags=re.M)
    p.write_text(s)


def _scene_files() -> None:
    """The per-capture detections the boxes read, and the recordings the poses come from.

    No <capture>.ply here on purpose: those turn the History graph into one node per capture
    FILE, which cannot show a branch. The clouds live in the commits instead (_cloud).
    """
    import shutil

    src = ROOMS / "hallway-test.scene"
    (ROOMS / "chips.scene").mkdir(exist_ok=True)
    (ROOMS / "chips.recordings").mkdir(exist_ok=True)
    for cap in (ANCHOR, SECOND):
        if (src / f"{cap}.json").is_file():
            shutil.copyfile(src / f"{cap}.json", ROOMS / "chips.scene" / f"{cap}.json")
    for stale in list((ROOMS / "chips.scene").glob("*.ply")) + list((ROOMS / "chips.scene").glob("*.png")):
        stale.unlink()                      # any capture cloud here makes the graph per-FILE, not per-commit
        if (RECORDINGS / cap).is_dir() and not (ROOMS / "chips.recordings" / cap).exists():
            shutil.copytree(RECORDINGS / cap, ROOMS / "chips.recordings" / cap)


if __name__ == "__main__":
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "state").lower()
    raise SystemExit({"state": state, "reset": reset, "seed": seed}.get(cmd, state)())
