"""`git diff` and `git merge`, for objects.

The room's objects are files, so git already diffs and merges them as TEXT. Three things text
cannot do, and this does:

  ALIGN.     Two scans taken from different robot poses disagree about every object in the room
             until they are brought into one frame. The pose is not tracked (`pose_source: none`)
             and a balancing robot does not stand still — 24 cm and 2.6 deg between two captures
             called "the same spot" — so a raw comparison reports the whole room as moved. The
             fit is scripts/floor_objects.register: ORB matches lifted to 3-D by each capture's
             own depth, then a robust planar (x, y, yaw) solve against the BUILDING.
  THRESHOLD. A change under MOVE_M (roomctl.state, 5 cm) is not a change: it is quantization and
             stereo noise. That is the same number `settle` uses to decide a commit, so a diff
             never claims a move the commit would not record. Aligning adds its own error, so an
             aligned diff widens the gate by ALIGN_SLACK.
  RESOLVE.   Two branches that both moved the mug is a conflict whose answer is in the ROOM, not
             in the file. A textual merge either takes a side silently or leaves `<<<<<<<` in a
             YAML nobody reads. This states both poses, how far apart they are, and what would
             settle it.

    python perception/roomdiff.py <repo> <ref_a> <ref_b>                 # what changed
    python perception/roomdiff.py <repo> --merge <base> <ours> <theirs>  # and what conflicts
"""
from __future__ import annotations

import math
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from roomctl.state import MOVE_M, ObjectRecord, from_yaml  # noqa: E402

ALIGN_SLACK = 0.03       # m added to the gate when the two scans had to be registered: the fit's
                         # own error. Measured on the hallway pairs: inlier residuals sit under 2 cm
MIN_INLIERS = 30         # a registration with fewer matches than this is not trusted; say so instead
ACCEPT_NN_M = 0.05       # ...and inliers alone are not enough: after the fit, the two clouds must LIE ON
ACCEPT_OVERLAP = 0.60    # each other. Measured on the hallway set: a good fit puts the median point 0.6-0.7
                         # cm from its neighbour with 88-96% inside 10 cm, while a fit built on 38 matches
                         # claimed 114 deg and left the median 57 cm out, with 33% inside. Nothing in the
                         # inlier count separates those two; this does
ADDED, REMOVED, MOVED, SAME = "+", "-", "~", "="


@dataclass
class Change:
    kind: str                      # ADDED / REMOVED / MOVED / SAME
    object_id: str
    cls: str
    distance: float | None = None  # m the centre moved, when both sides have it
    before: ObjectRecord | None = None
    after: ObjectRecord | None = None
    note: str = ""

    @property
    def counts(self) -> bool:
        """A change git would record. SAME is reported so a diff can say what it CHOSE to ignore."""
        return self.kind != SAME


@dataclass
class Conflict:
    object_id: str
    cls: str
    ours: ObjectRecord | None
    theirs: ObjectRecord | None
    base: ObjectRecord | None
    why: str
    distance: float | None = None
    options: tuple[str, ...] = field(default_factory=tuple)


def centre(rec: ObjectRecord) -> np.ndarray:
    return np.array([rec.pose.x, rec.pose.y, rec.pose.z], float)


def moved_by(a: ObjectRecord, b: ObjectRecord) -> float:
    return float(np.linalg.norm(centre(a) - centre(b)))


def records_at(repo: Path, ref: str) -> dict[str, ObjectRecord]:
    """The room's objects at any ref, read the way git stores them."""
    repo = Path(repo)
    names = _git(repo, "ls-tree", "-r", "--name-only", ref, "--", "zones").splitlines()
    out = {}
    for path in names:
        if not path.endswith(".yaml"):
            continue
        rec = from_yaml(_git(repo, "show", f"{ref}:{path}"))
        out[rec.id] = rec
    return out


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def diff(before: dict[str, ObjectRecord], after: dict[str, ObjectRecord],
         gate: float = MOVE_M) -> list[Change]:
    """What changed between two states of the room. `gate` is how far a centre must move to count."""
    out = []
    for oid in sorted(before.keys() | after.keys()):
        a, b = before.get(oid), after.get(oid)
        if a is None:
            out.append(Change(ADDED, oid, b.cls, None, None, b, f"new in {b.zone}"))
        elif b is None:
            out.append(Change(REMOVED, oid, a.cls, None, a, None, f"gone from {a.zone}"))
        else:
            d = moved_by(a, b)
            if a.zone != b.zone:
                out.append(Change(MOVED, oid, b.cls, d, a, b, f"{a.zone} -> {b.zone}"))
            elif d >= gate:
                out.append(Change(MOVED, oid, b.cls, d, a, b, f"in {b.zone}"))
            else:
                out.append(Change(SAME, oid, b.cls, d, a, b,
                                  f"{d * 100:.1f} cm, under the {gate * 100:.0f} cm threshold"))
    return out


def merge3(base: dict[str, ObjectRecord], ours: dict[str, ObjectRecord], theirs: dict[str, ObjectRecord],
           gate: float = MOVE_M) -> tuple[dict[str, ObjectRecord], list[Conflict]]:
    """Three-way merge of two rooms that diverged. -> (merged objects, conflicts).

    One side changed it: that side wins, as git does. BOTH sides changed it differently: the file
    cannot decide, because the object is somewhere in particular and only one of the two is where
    it is — a conflict, with both poses and the distance between them.
    """
    merged, conflicts = {}, []
    for oid in sorted(base.keys() | ours.keys() | theirs.keys()):
        b, o, t = base.get(oid), ours.get(oid), theirs.get(oid)
        ours_changed = _changed(b, o, gate)
        theirs_changed = _changed(b, t, gate)
        if not ours_changed and not theirs_changed:
            if o is not None:
                merged[oid] = o
        elif ours_changed and not theirs_changed:
            if o is not None:
                merged[oid] = o
        elif theirs_changed and not ours_changed:
            if t is not None:
                merged[oid] = t
        elif o is None and t is None:
            continue                                            # both removed it: agreed
        elif o is None or t is None:
            gone, kept = ("ours", t) if o is None else ("theirs", o)
            conflicts.append(Conflict(oid, kept.cls, o, t, b,
                                      f"{gone} removed it, the other moved it",
                                      None, ("keep it where the other side put it", "confirm it is gone",
                                             "rescan the room")))
        else:
            d = moved_by(o, t)
            if d < gate and o.zone == t.zone:
                merged[oid] = o                                 # both moved it to the same place
            else:
                conflicts.append(Conflict(oid, o.cls, o, t, b, "both moved it, to different places", d,
                                          ("take ours", "take theirs", "rescan the room")))
    return merged, conflicts


def _changed(base: ObjectRecord | None, side: ObjectRecord | None, gate: float) -> bool:
    if base is None and side is None:
        return False
    if base is None or side is None:
        return True
    return side.zone != base.zone or moved_by(base, side) >= gate


# ── alignment: two captures are not in one frame until this says so ────────────────────────

def align(capture_a, capture_b):
    """(dx, dy, dyaw_deg, inliers, matches) taking capture A's frame into B's, or None.

    Reads both recordings, runs scripts/floor_objects.register on the building, and reports how
    many matches held it up: a registration nobody can check is worse than none.
    """
    import importlib.util

    import difference

    spec = importlib.util.spec_from_file_location("floor_objects", ROOT / "scripts" / "floor_objects.py")
    fo = sys.modules.get("floor_objects")
    if fo is None:
        fo = importlib.util.module_from_spec(spec)
        sys.modules["floor_objects"] = fo
        spec.loader.exec_module(fo)
    _, a = difference.load_view(capture_a)
    _, b = difference.load_view(capture_b)
    from fuse import rect_to_world

    wa = rect_to_world(a.xyz, a.mount, a.pose)
    wb = rect_to_world(b.xyz, b.mount, b.pose)
    got = fo.register(wa, a.valid, a.image, wb, b.valid, b.image)
    if not got:
        return None
    (dx, dy, dyaw), inliers, matches = got
    if inliers < MIN_INLIERS:
        return None
    return float(dx), float(dy), float(math.degrees(dyaw)), int(inliers), int(matches)


def agreement(view_a, view_b, se2) -> tuple[float, float]:
    """Does capture A, moved by `se2`, lie on capture B? -> (median nearest-neighbour m, share within 10 cm).

    The check a registration cannot do for itself: ORB can match 38 features consistently and still be
    describing a motion the room never made.
    """
    from scipy.spatial import cKDTree

    pa, pb = _floor_cloud(view_a), _floor_cloud(view_b)
    if len(pa) < 500 or len(pb) < 500:
        return float("inf"), 0.0
    c, s = math.cos(math.radians(se2[2])), math.sin(math.radians(se2[2]))
    xy = np.c_[pa[:, 0] * c - pa[:, 1] * s + se2[0], pa[:, 0] * s + pa[:, 1] * c + se2[1]]
    d, _ = cKDTree(pb[:, :2]).query(xy, k=1)
    return float(np.median(d)), float((d < 0.10).mean())


def _floor_cloud(view) -> np.ndarray:
    """The structure worth matching: standing points within 4 m, thinned."""
    world, valid, _ = view
    w = world[valid]
    keep = (np.hypot(w[:, 0], w[:, 1]) < 4.0) & (w[:, 2] > 0.05) & (w[:, 2] < 2.0)
    return w[keep][::7]


def invert(se2):
    """The same motion, read the other way. Registration is not symmetric — matching A into B can
    hold with hundreds of inliers while B into A finds nothing, because the residuals are weighted
    in the target's frame — so a chain that cannot step forwards is often free to step backwards."""
    x, y, ang = se2
    c, s = math.cos(math.radians(-ang)), math.sin(math.radians(-ang))
    return (-(x * c - y * s), -(x * s + y * c), -ang)


def compose(first, second):
    """Apply `first`, then `second`. SE2 as (dx, dy, dyaw_deg), the frame convention register uses."""
    x1, y1, a1 = first
    x2, y2, a2 = second
    c, s = math.cos(math.radians(a2)), math.sin(math.radians(a2))
    return (x1 * c - y1 * s + x2, x1 * s + y1 * c + y2, (a1 + a2))


def align_path(capture_a, capture_b, recordings=None):
    """A's frame into B's, THROUGH other captures when they don't match directly.

    Two scans taken minutes apart can be unmatchable head-on — the robot turned too far for the
    same features to be in both — while each still matches a capture taken in between. Measured
    on the hallway set: cap_0018 -> cap_0020 finds nothing at all, but 0018 -> 0019 -> 0020 holds
    with 38 and 353 matches. The chain is only as good as its weakest hop, so that number is
    reported: a diff standing on 38 matches should be read differently from one standing on 833.

    -> (se2, path, weakest_inliers) or None when no chain of registrations connects them.
    """
    import difference
    from fuse import rect_to_world

    pool = [Path(capture_a), Path(capture_b)]
    if recordings:
        pool += [p for p in sorted(Path(recordings).iterdir())
                 if (p / "capture.json").is_file() and p.name not in {pool[0].name, pool[1].name}]
    views: dict[str, tuple] = {}

    def view(d: Path):
        if d.name not in views:
            _, v = difference.load_view(d)
            views[d.name] = (rect_to_world(v.xyz, v.mount, v.pose), v.valid, v.image)
        return views[d.name]

    fo = _floor_objects()
    seen, queue = {pool[0].name}, [(pool[0], (0.0, 0.0, 0.0), [pool[0].name], 10 ** 9)]
    while queue:
        here, se2, path, weakest = queue.pop(0)
        if here.name == Path(capture_b).name:
            return se2, path, weakest
        for nxt in pool:
            if nxt.name in seen:
                continue
            got, back = fo.register(*view(here), *view(nxt)), None
            if not got or got[1] < MIN_INLIERS:
                back = fo.register(*view(nxt), *view(here))          # the same hop, measured the other way
                if not back or back[1] < MIN_INLIERS:
                    continue
            (dx, dy, dyaw), inliers, _ = got if back is None else back
            hop = (float(dx), float(dy), math.degrees(float(dyaw)))
            hop = hop if back is None else invert(hop)
            near, overlap = agreement(view(here), view(nxt), hop)
            if near > ACCEPT_NN_M or overlap < ACCEPT_OVERLAP:
                continue                       # matched features, wrong motion: the clouds don't lie on each other
            seen.add(nxt.name)
            queue.append((nxt, compose(se2, hop), [*path, nxt.name], min(weakest, int(inliers))))
    return None


def _floor_objects():
    import importlib.util

    fo = sys.modules.get("floor_objects")
    if fo is None:
        spec = importlib.util.spec_from_file_location("floor_objects", ROOT / "scripts" / "floor_objects.py")
        fo = importlib.util.module_from_spec(spec)
        sys.modules["floor_objects"] = fo
        spec.loader.exec_module(fo)
    return fo


def apply_se2(records: dict[str, ObjectRecord], se2) -> dict[str, ObjectRecord]:
    """Objects measured in capture A's frame, restated in B's."""
    from dataclasses import replace

    dx, dy, dyaw = se2[0], se2[1], math.radians(se2[2])
    c, s = math.cos(dyaw), math.sin(dyaw)
    out = {}
    for oid, r in records.items():
        x, y = r.pose.x * c - r.pose.y * s + dx, r.pose.x * s + r.pose.y * c + dy
        pose = replace(r.pose, x=round(x, 3), y=round(y, 3), yaw=(r.pose.yaw + se2[2]) % 180)
        out[oid] = replace(r, pose=pose)
    return out


# ── saying it ─────────────────────────────────────────────────────────────────────────────

def explain(changes: list[Change], gate: float = MOVE_M, quiet: bool = False) -> str:
    """The diff as a person reads it. Unchanged objects are listed last, and say why they didn't
    count — the number under the threshold is the interesting part when someone disagrees."""
    lines, counted = [], [c for c in changes if c.counts]
    for c in counted:
        if c.kind == ADDED:
            lines.append(f"+ {c.object_id:<28} {c.cls:<22} {c.note} at {_at(c.after)}")
        elif c.kind == REMOVED:
            lines.append(f"- {c.object_id:<28} {c.cls:<22} {c.note}, last at {_at(c.before)}")
        else:
            lines.append(f"~ {c.object_id:<28} {c.cls:<22} moved {c.distance * 100:.0f} cm  "
                         f"{_at(c.before)} -> {_at(c.after)}  ({c.note})")
    same = [c for c in changes if not c.counts]
    if same and not quiet:
        lines.append("")
        for c in same:
            lines.append(f"= {c.object_id:<28} {c.cls:<22} {c.note}")
    head = f"{len(counted)} change{'' if len(counted) == 1 else 's'}, {len(same)} under the "
    head += f"{gate * 100:.0f} cm threshold"
    return "\n".join([head, ""] + lines) if lines else head


def explain_conflicts(conflicts: list[Conflict]) -> str:
    if not conflicts:
        return "no conflicts"
    out = [f"{len(conflicts)} conflict{'' if len(conflicts) == 1 else 's'}", ""]
    for c in conflicts:
        out.append(f"CONFLICT ({c.why}): {c.object_id}  [{c.cls}]")
        out.append(f"   ours    {_at(c.ours) if c.ours else 'removed'}")
        out.append(f"   theirs  {_at(c.theirs) if c.theirs else 'removed'}")
        if c.base:
            out.append(f"   base    {_at(c.base)}")
        if c.distance is not None:
            out.append(f"   {c.distance * 100:.0f} cm apart")
        out.append("   resolve: " + " · ".join(c.options))
        out.append("")
    return "\n".join(out)


def _at(rec: ObjectRecord | None) -> str:
    return "—" if rec is None else f"{rec.zone} ({rec.pose.x:+.2f}, {rec.pose.y:+.2f}, {rec.pose.z:.2f})"


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="git diff and git merge, for the room's objects.")
    ap.add_argument("repo")
    ap.add_argument("refs", nargs="*", help="<ref_a> <ref_b>: what changed between them")
    ap.add_argument("--merge", nargs=3, metavar=("BASE", "OURS", "THEIRS"),
                    help="three-way merge of two rooms that diverged")
    ap.add_argument("--gate", type=float, default=MOVE_M, help="metres a centre must move to count")
    ap.add_argument("--align", nargs=2, metavar=("CAP_A", "CAP_B"),
                    help="two recordings: register them and restate the first room in the second's frame")
    ap.add_argument("--via", metavar="RECORDINGS_DIR",
                    help="other captures to chain the registration through when the two don't match directly")
    ap.add_argument("--anyway", action="store_true",
                    help="diff even when the scans could not be aligned (the numbers are then two frames apart)")
    a = ap.parse_args(argv)
    repo, gate = Path(a.repo), a.gate

    shift = None
    if a.align:
        got = align_path(a.align[0], a.align[1], a.via)
        if got is None:
            print("NOT ALIGNED: no chain of registrations connects those two scans.")
            print("An object's pose is measured from where the robot stood, so without a common frame a")
            print("diff reports the ROBOT's movement as the room's. Pass --via <recordings dir> to chain")
            print("through other captures, or --anyway to see the numbers knowing they are two frames apart.")
            if not a.anyway:
                return 2
        else:
            shift, path, weakest = got
            gate += ALIGN_SLACK
            print(f"aligned: {shift[0] * 100:+.0f} cm, {shift[1] * 100:+.0f} cm, {shift[2]:+.1f} deg"
                  + ("" if len(path) == 2 else "   via " + " -> ".join(p[-4:] for p in path[1:-1]))
                  + f"   (weakest hop {weakest} matches)   gate widened to {gate * 100:.0f} cm\n")

    if a.merge:
        base, ours, theirs = (records_at(repo, r) for r in a.merge)
        merged, conflicts = merge3(base, ours, theirs, gate)
        print(f"merging {a.merge[1][:8]} and {a.merge[2][:8]} on {a.merge[0][:8]}: "
              f"{len(merged)} object{'' if len(merged) == 1 else 's'} merged cleanly\n")
        print(explain_conflicts(conflicts))
        return 1 if conflicts else 0

    before, after = (records_at(repo, r) for r in a.refs[:2])
    if shift is not None:
        before = apply_se2(before, shift)
    print(explain(diff(before, after, gate), gate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
