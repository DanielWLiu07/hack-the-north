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
    rescanned: nothing changed                    the same floor again, and the room says so

and then two branches that disagree about the packet:

    eaten    someone took the chip packet         the object is gone
    kicked   the chip packet was kicked           the object is 44 cm away

Merging them is the point: git can only say "a file was deleted here and modified there", while
perception/roomdiff.py says WHICH object, where each branch puts it, and that the deciding
evidence is in the room. The small box merges cleanly either way, so the merge is doing real
work rather than simply failing.

RESET exists because a demo is run more than once. The three branch tips are tagged at seed
time; `reset` moves main, eaten and kicked back to those tags and cleans the tree, in under a
second, without rescanning anything.

The two divergent commits are staged by hand: nobody ate or kicked anything. The objects, their
ids, their poses and the 44 cm are real measurements from real scans.
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
MOVE = (0.38, -0.22)                             # m, how far "kicked" moves the packet
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
    print("\n  page     http://127.0.0.1:8000/robot?instance=chips")
    print(f"  merge    .venv/bin/python perception/roomdiff.py {ROOM} "
          f"--merge {TAGS['main']} {TAGS['kicked']} {TAGS['eaten']}")
    return 0


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
    _cloud(SECOND)
    room("commit", "-m", f"rescanned: nothing changed  [{SECOND}]", capture=SECOND)
    if git("status", "--porcelain") or "nothing changed" not in git("log", "-1", "--format=%s"):
        git("add", "-A")
        git(*WHO, "commit", "-q", "--allow-empty", "-m",
            f"rescanned: nothing changed  [{SECOND}]")

    _pin_names()
    base = git("rev-parse", "HEAD")
    packet = _packet_path()
    git("checkout", "-q", "-B", "eaten", base)
    gone = _cloud_without(ANCHOR, packet)          # the picture agrees with the record
    git("rm", "-q", packet)
    git("add", "-A")
    git(*WHO, "commit", "-qm", f"someone took the chip packet  [{ANCHOR}]")
    print(f"  eaten: {gone} points of the packet taken out of that branch's cloud")

    git("checkout", "-q", "-B", "kicked", base)
    _move_packet(packet, *MOVE)
    git("add", "-A")
    git(*WHO, "commit", "-qm", f"the chip packet was kicked across the floor  [{ANCHOR}]")

    git("checkout", "-q", "main")
    for branch, tag in TAGS.items():
        git("tag", "-f", tag, branch if branch != "main" else "HEAD")
    _scene_files()
    print()
    return state()


def _cloud(capture: str) -> None:
    """The commit's own cloud. With one of these per commit the History graph is a graph of
    COMMITS — branches and all — instead of one node per capture file, and the viewer still has
    something to draw at every node."""
    import shutil

    src = ROOMS / "hallway-test.scene" / f"{capture}.ply"
    if not src.is_file():
        return
    (ROOM / "cloud").mkdir(exist_ok=True)
    shutil.copyfile(src, ROOM / "cloud" / "current.ply")
    meta = ROOM / "cloud" / "current.json"
    meta.write_text(json.dumps({"capture_id": capture, "frame": "x forward, y left, z up, floor at z=0, metres"}, indent=1))
    git("add", "-A")


def _cloud_without(capture: str, rec_path: str, pad: float = 0.0) -> int:
    """The capture's cloud with THE OBJECT'S OWN POINTS taken out, as this commit's cloud.

    Not a box: the detector already says which pixels are the packet, and those pixels are the
    points to drop. A box cannot do this job here — the floor in this frame spans -3 to +5 cm
    while the packet is 8 cm tall, so any height cut through the box takes floor with it and
    leaves a void where the ground should be. The mask takes the packet and nothing else.
    """
    import sys as _sys

    _sys.path[:0] = [str(ROOT), str(ROOT / "perception")]
    import numpy as np
    import difference, fuse, roomdiff, segment
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
    # and those pixels are the packet too. Grown a little, they go with it.
    import cv2

    wider = cv2.dilate(inst.mask.astype("uint8"), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))).astype(bool)
    keep = view.valid.copy()
    keep[wider] = False
    pts = fuse.rect_to_world(view.xyz[keep], view.mount, pose)
    rgb = view.image[keep][:, ::-1]
    vert = np.empty(len(pts), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
    vert["x"], vert["y"], vert["z"] = pts[:, 0], pts[:, 1], pts[:, 2]
    vert["r"], vert["g"], vert["b"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    with open(ROOM / "cloud" / "current.ply", "wb") as f:
        f.write((f"ply\nformat binary_little_endian 1.0\ncomment {capture} with {rec.id}'s own points removed\n"
                 f"element vertex {len(vert)}\n"
                 "property float x\nproperty float y\nproperty float z\n"
                 "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n").encode())
        f.write(vert.tobytes())
    return int((wider & view.valid).sum())


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
    """One commit that fixes the two labels, so the demo reads the same every time it is seeded."""
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
