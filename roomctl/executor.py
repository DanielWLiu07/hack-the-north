"""git gives us a target tree; this makes the room match it (docs/04, "Applying a diff").

    observed records + target records
      -> ops: MOVE / REMOVE (to the bin) / ADD (impossible: we can't conjure objects)
      -> order them: a place may never land on something that is still there
      -> cycles (A to B's spot, B to A's): STAGE one of them somewhere free first
      -> robot calls, then verify by rescanning — and say honestly what didn't happen

Ordering is a simulation, not a guess: we keep the room's occupancy as it will be at every
step and only schedule a placement whose footprint is free *at that moment*. "Never place
into an occupied spot" holds by construction, and the test checks it anyway.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Callable, Protocol

from roomctl.state import Extents, ObjectRecord, Pose

try:
    import obs  # spans are no-ops unless this process called obs.init()
except ImportError:  # pragma: no cover
    obs = None


def _span(op: str, desc: str, **data):
    """The robot span vocabulary, shared with robot_client.HttpRobot: robot.drive/pick/place,
    tagged `robot=` so a simulated pick can never read as a real one."""
    import contextlib
    return obs.span(op, desc, **data) if obs else contextlib.nullcontext()

TOUCH = 0.01        # m: boxes overlapping by less than a quantum are touching, not colliding.
                    # The committed state can have a mug against a book; we must be able to restore it.
CLEARANCE = 0.015   # m kept around a spot the planner picks itself (staging): gripper fingers
STAGING_STEP = 0.02  # m grid when searching for a free staging spot
BIN = "bin"


# ── geometry: oriented footprints on a surface ───────────────────────────────

@dataclass(frozen=True)
class Box:
    """An object's footprint where it stands: an oriented rectangle plus its height span."""
    x: float
    y: float
    yaw: float
    hx: float
    hy: float
    z0: float
    z1: float

    @classmethod
    def of(cls, pose: Pose, ext: Extents, margin: float = 0.0) -> Box:
        """`margin` grows (or, negative, shrinks) the footprint by that much in total."""
        return cls(pose.x, pose.y, pose.yaw, max(0.0, ext.x + margin) / 2, max(0.0, ext.y + margin) / 2,
                   pose.z - ext.z / 2, pose.z + ext.z / 2)

    def corners(self) -> list[tuple[float, float]]:
        c, s = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        return [(self.x + c * dx - s * dy, self.y + s * dx + c * dy)
                for dx, dy in ((self.hx, self.hy), (-self.hx, self.hy), (-self.hx, -self.hy), (self.hx, -self.hy))]

    def axes(self) -> list[tuple[float, float]]:
        c, s = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        return [(c, s), (-s, c)]

    def overlaps(self, other: Box) -> bool:
        if self.z1 <= other.z0 or other.z1 <= self.z0:
            return False  # different surfaces (desk vs shelf)
        a, b = self.corners(), other.corners()
        for ax, ay in self.axes() + other.axes():  # separating-axis test
            pa = [x * ax + y * ay for x, y in a]
            pb = [x * ax + y * ay for x, y in b]
            if max(pa) <= min(pb) or max(pb) <= min(pa):
                return False
        return True


# ── the plan ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Spot:
    zone: str
    pose: Pose
    extents: Extents

    @property
    def box(self) -> Box:
        """The collision footprint: touching is allowed, overlapping by a quantum or more isn't."""
        return Box.of(self.pose, self.extents, -TOUCH)

    def __str__(self) -> str:
        if self.zone == BIN:
            return "bin"
        p = self.pose
        return f"{self.zone} ({p.x:.2f}, {p.y:.2f}, {p.z:.2f}) yaw {p.yaw}"


@dataclass(frozen=True)
class BasePose:
    """Where the robot stands (floor plane, metres) and faces (degrees) — docs/24 A2."""
    x: float
    y: float
    yaw: float

    def __str__(self) -> str:
        return f"({self.x:.2f}, {self.y:.2f}) facing {self.yaw:.0f}°"


@dataclass(frozen=True)
class Op:
    kind: str       # move | remove | stage | unstage
    object_id: str
    src: Spot
    dst: Spot
    pick_base: BasePose | None = None    # set by route(): where to stand to pick
    place_base: BasePose | None = None   # ...and to place


@dataclass
class Plan:
    ops: list[Op] = field(default_factory=list)
    unapplied: list[tuple[str, str]] = field(default_factory=list)  # (object_id, git-apply error)

    @property
    def changes(self) -> int:
        """Objects that end somewhere new (a stage + unstage pair is one object)."""
        return len({op.object_id for op in self.ops})

    def to_dict(self, ref: str = "", target_sha: str = "") -> dict:
        """THE hand-off to the edge (docs/30): ordering is ours — it needs the occupancy grid —
        and execution, verification and retry are the edge's. This is where our side STOPS:
        an ordered op list, in our frame, with its units written down."""
        def spot(sp: Spot) -> dict:
            p, e = sp.pose, sp.extents
            return {"zone": sp.zone, "pose": {"x": p.x, "y": p.y, "z": p.z, "yaw": p.yaw},
                    "extents": {"x": e.x, "y": e.y, "z": e.z}}

        def base(b: BasePose | None) -> dict | None:
            return None if b is None else {"x": b.x, "y": b.y, "yaw": b.yaw}
        return {
            "contract": "gitspace.plan/1",
            "ref": ref, "target_sha": target_sha,
            # the token bridge/contract.py asserts on every pose that crosses to the edge (docs/31 §4)
            "frame": "world_z_up",
            "frame_def": "X forward from the anchor tag, Y left, Z UP, floor z=0; metres. pose.yaw = the "
                         "AXIS of extents.x in degrees [0,180) about +Z. base.yaw = heading, degrees about "
                         "+Z. Bracket Bot is Y-DOWN: convert at the adapter (docs/20, ANDREW-HANDOFF.md §5).",
            "ops": [{"seq": i, "kind": op.kind, "object_id": op.object_id, "from": spot(op.src),
                     "to": spot(op.dst), "base": {"pick": base(op.pick_base), "place": base(op.place_base)}}
                    for i, op in enumerate(self.ops, 1)],
            "unapplied": [{"object_id": oid, "reason": why} for oid, why in self.unapplied],
        }


def spot_of(rec: ObjectRecord) -> Spot:
    return Spot(rec.zone, rec.pose, rec.extents)


def same_place(a: ObjectRecord, b: ObjectRecord) -> bool:
    return a.zone == b.zone and a.pose == b.pose  # extents are re-measured, not moved


def plan(current: dict[str, ObjectRecord], target: dict[str, ObjectRecord], room: dict) -> Plan:
    """Observed room -> target tree, as an ordered list of pick-and-place ops."""
    out = Plan()
    occupied = {oid: spot_of(r) for oid, r in current.items()}  # the room, as it will be
    pending: dict[str, Spot] = {}                                # where things still have to go
    bin_spot = room_bin(room)

    for oid in sorted(target.keys() - current.keys()):
        out.unapplied.append((oid, f"cannot apply hunk: object '{oid}' not present in room"))
    for oid in sorted(current):
        if oid in target:
            if not same_place(current[oid], target[oid]):
                pending[oid] = spot_of(target[oid])
        elif bin_spot is not None:
            pending[oid] = replace_extents(bin_spot, current[oid].extents)
        else:
            out.unapplied.append((oid, f"cannot apply hunk: nowhere to put '{oid}' (no bin in room.yaml)"))

    staged: set[str] = set()
    while pending:
        def free(oid: str) -> bool:
            dst = pending[oid]
            return dst.zone == BIN or not any(
                other != oid and s.zone != BIN and s.box.overlaps(dst.box) for other, s in occupied.items())

        ready = [oid for oid in pending if free(oid)]
        if ready:  # removals first (they only free space), then by id, so plans are deterministic
            oid = min(ready, key=lambda o: (pending[o].zone != BIN, o))
            dst = pending.pop(oid)
            kind = "remove" if dst.zone == BIN else "unstage" if oid in staged else "move"
            out.ops.append(Op(kind, oid, occupied[oid], dst))
            if dst.zone == BIN:
                del occupied[oid]
            else:
                occupied[oid] = dst
            continue

        blockers = {oid: sorted(o for o, s in occupied.items()
                                if o != oid and s.zone != BIN and s.box.overlaps(dst.box))
                    for oid, dst in pending.items()}
        stuck = {oid: [b for b in bs if b not in pending] for oid, bs in blockers.items()}
        stuck = {oid: bs for oid, bs in stuck.items() if bs}
        if stuck:  # something that isn't going to move is sitting where this has to go
            for oid, bs in sorted(stuck.items()):
                settled = [b for b in bs if b in target and occupied[b] == spot_of(target[b])]
                why = ("the target tree puts them on top of each other" if len(settled) == len(bs)
                       else "which isn't moving")
                out.unapplied.append((oid, f"cannot apply hunk: target of '{oid}' is occupied by "
                                           f"{', '.join(repr(b) for b in bs)}, {why}"))
                del pending[oid]
            continue

        # Every pending placement waits on another pending object: a cycle. Stage the object
        # that blocks the most others, somewhere free of everything now and everything to come.
        candidates = [o for o in pending if o not in staged]
        if not candidates:
            for oid in sorted(pending):
                out.unapplied.append((oid, f"cannot apply hunk: '{oid}' is in a cycle that staging didn't break"))
            break
        oid = max(sorted(candidates), key=lambda o: sum(o in bs for bs in blockers.values()))
        spot = staging_spot(oid, occupied, pending, room)
        if spot is None:
            for o in sorted(pending):
                out.unapplied.append((o, f"cannot apply hunk: '{o}' is in a cycle and there is no free "
                                         f"staging spot"))
            break
        out.ops.append(Op("stage", oid, occupied[oid], spot))
        occupied[oid] = spot
        staged.add(oid)
    return out


def replace_extents(s: Spot, e: Extents) -> Spot:
    return Spot(s.zone, s.pose, e)


def room_bin(room: dict) -> Spot | None:
    b = room.get("bin")
    if not b:
        return None
    x, y, z = b["pose"]
    return Spot(BIN, Pose(x, y, z, 0), Extents(0.01, 0.01, 0.01))


def staging_spot(oid: str, occupied: dict[str, Spot], pending: dict[str, Spot], room: dict) -> Spot | None:
    """The free spot nearest the object: on a zone surface, clear of everything in the room now
    and of every placement still to come, so parking here can't create a new conflict."""
    here = occupied[oid]
    ext = here.extents
    avoid = [Box.of(s.pose, s.extents) for o, s in occupied.items() if o != oid and s.zone != BIN] + \
            [Box.of(s.pose, s.extents) for o, s in pending.items() if s.zone != BIN]
    best, best_d = None, math.inf
    zones = room.get("zones", {})
    for zname in [here.zone] + sorted(z for z in zones if z != here.zone):
        z = zones.get(zname)
        if not z:
            continue
        pz = round(z["surface"] + ext.z / 2, 2)
        nx = int((z["max"][0] - z["min"][0]) / STAGING_STEP) + 1
        ny = int((z["max"][1] - z["min"][1]) / STAGING_STEP) + 1
        for i in range(nx):
            for j in range(ny):
                pose = Pose(round(z["min"][0] + i * STAGING_STEP, 2), round(z["min"][1] + j * STAGING_STEP, 2),
                            pz, here.pose.yaw)
                box = Box.of(pose, ext, 2 * CLEARANCE)
                if not all(z["min"][0] <= cx <= z["max"][0] and z["min"][1] <= cy <= z["max"][1]
                           for cx, cy in box.corners()):
                    continue
                d = math.dist((pose.x, pose.y), (here.pose.x, here.pose.y))
                if d < best_d and not any(box.overlaps(a) for a in avoid):
                    best, best_d = Spot(zname, pose, ext), d
        if best is not None:
            return best  # prefer staying on the same surface
    return best


# ── where to stand: docs/24 Part A ───────────────────────────────────────────
#
# perception/costmap.py owns the costmap, solve_base_pose and solve_viewpoint, and
# perception/raycast.py the one line-of-sight both it and the occlusion verdict use (A4).
# This file only asks them, per op, and turns "nowhere to stand" into an unapplied hunk.
# We compute WHERE; Bracket Bot's nav computes HOW (A0) — nothing here plans a path.

def _perception():
    """perception/ uses flat imports (`from voxelize import VoxelGrid`)."""
    import sys
    from pathlib import Path
    d = str(Path(__file__).resolve().parents[1] / "perception")
    if d not in sys.path:
        sys.path.insert(0, d)
    import costmap
    return costmap


@dataclass(frozen=True)
class ArmModel:
    """perception.costmap.Arm: the SO-101's reach annulus around the BASE centre. Every number
    is a PLACEHOLDER until robot/arm.py exists with measured ones and real IK."""
    r_min: float = 0.18          # m, floor plane, base centre -> target: closer, the arm folds on itself
    r_max: float = 0.48          # m: mount 8 cm ahead of the base + ~40 cm of SO-101
    z_min: float = 0.45          # m, world: lowest graspable height
    z_max: float = 1.05          # m, world: highest
    yaw_limit: float = 60.0      # degrees either side of straight ahead

    def reachable(self, target, base_pose) -> bool:
        """IK-exists stand-in. base_pose is (x, y, yaw) with yaw in RADIANS (perception's frame)."""
        x, y, yaw = base_pose
        r = math.dist((x, y), (target[0], target[1]))
        bearing = math.atan2(target[1] - y, target[0] - x) - yaw
        off = abs(math.degrees((bearing + math.pi) % (2 * math.pi) - math.pi))
        return self.r_min <= r <= self.r_max and self.z_min <= target[2] <= self.z_max and off <= self.yaw_limit


@dataclass(frozen=True)
class RobotModel:
    eye_h: float = 0.95   # m: where line of sight starts (the head cameras). MEASURE.


ARM, ROBOT = ArmModel(), RobotModel()
HOME = BasePose(-0.40, 0.0, 0.0)   # default start: behind the anchor tag, facing +X


def base_pose_for(target: tuple[float, float, float], extents: Extents, costmap, here: BasePose,
                  arm=ARM, eye_h: float = ROBOT.eye_h) -> tuple[BasePose | None, str]:
    """perception.costmap.solve_base_pose_why, in our units (yaw in degrees on BasePose), plus
    which of docs/24 A2's filters rejected the candidates — the half of "nowhere to stand"
    that says whether to blame the costmap, the arm, the view, or the route."""
    cm = _perception()
    # raycast.py: the last stretch of the ray is the object itself and what it stands on
    ignore = math.hypot(extents.x, extents.y, extents.z) / 2 + costmap.grid.leaf
    got, why = cm.solve_base_pose_why(target, costmap, arm, (here.x, here.y, math.radians(here.yaw)),
                                      eye_h, ignore)
    n = sum(why.values())
    reason = f"{n} base poses sampled: " + ", ".join(f"{c} {k.replace('_', ' ')}" for k, c in why.items() if c)
    pose = BasePose(round(got[0], 3), round(got[1], 3), round(math.degrees(got[2]) % 360, 1)) if got else None
    return pose, reason


def route(p: Plan, costmap, start: BasePose, arm=ARM, eye_h: float = ROBOT.eye_h) -> Plan:
    """Give every op a base pose to pick from and one to place from, in order, the robot
    ending each op where it placed. An op nobody can stand close enough for is an unapplied
    hunk (docs/24 A2: None is a real answer) — and so is anything that needed the spot it
    would have vacated."""
    out = Plan(unapplied=list(p.unapplied))
    here, stuck = start, {}
    for op in p.ops:
        blocker = next((o for o, s in stuck.items() if op.dst.zone != BIN and s.box.overlaps(op.dst.box)), None)
        if op.object_id in stuck or blocker:
            why = f"'{blocker}' can't be moved out of its way" if blocker else "an earlier step for it failed"
            out.unapplied.append((op.object_id, f"cannot apply hunk: '{op.object_id}' — {why}"))
            stuck.setdefault(op.object_id, op.src)
            continue
        src, dst = op.src.pose, op.dst.pose
        pick, why = base_pose_for((src.x, src.y, src.z), op.src.extents, costmap, here, arm, eye_h)
        if pick is None:
            out.unapplied.append((op.object_id, f"cannot apply hunk: nowhere to stand to pick up "
                                                f"'{op.object_id}' at {op.src} — {why}"))
            stuck[op.object_id] = op.src
            continue
        # the bin is dropped into from above: aim at the lowest height the arm works at
        dz = max(dst.z, arm.z_min) if op.dst.zone == BIN else dst.z
        place, why = base_pose_for((dst.x, dst.y, dz), op.dst.extents, costmap, pick, arm, eye_h)
        if place is None:
            out.unapplied.append((op.object_id, f"cannot apply hunk: nowhere to stand to put "
                                                f"'{op.object_id}' at {op.dst} — {why}"))
            stuck[op.object_id] = op.src
            continue
        out.ops.append(replace(op, pick_base=pick, place_base=place))
        here = place
    return out


# ── doing it ─────────────────────────────────────────────────────────────────

class RobotError(Exception):
    """docs/16 §2.7 codes: grasp_slipped, unreachable_pose, not_balanced, busy..."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


class Robot(Protocol):
    def drive(self, base: BasePose) -> None: ...  # POST /drive: BB's nav decides HOW (docs/24 A0)
    def pick(self, object_id: str, pose: Pose) -> None: ...
    def place(self, object_id: str, pose: Pose, zone: str) -> None: ...  # zone "bin": drop it there
    def say(self, text: str) -> None: ...
    def led(self, state: str) -> None: ...


class MockRobot:
    """Prints every call instead of moving. Optionally keeps a `world` (object_id -> Spot) up to
    date, so a simulated rescan can verify the plan; `fail` makes those objects slip."""

    def __init__(self, world: dict[str, Spot] | None = None, fail: set[str] = frozenset(),
                 out: Callable[[str], None] = print, slip_once: set[str] = frozenset()):
        self.world, self.fail, self.out = world, set(fail), out
        self.slip_once = set(slip_once)  # these slip on the first grasp only
        self.calls: list[tuple[str, str]] = []
        self._holding: str | None = None

    def drive(self, base: BasePose) -> None:
        with _span("robot.drive", "drive mock", robot="mock"):
            self.calls.append(("drive", str(base)))
            self.out(f"  [robot] drive  to {base}")

    def pick(self, object_id: str, pose: Pose) -> None:
        with _span("robot.pick", f"arm.pick {object_id}", object_id=object_id, robot="mock"):
            self.calls.append(("pick", object_id))
            self.out(f"  [robot] pick   {object_id:<18} at ({pose.x:.2f}, {pose.y:.2f}, {pose.z:.2f}) yaw {pose.yaw}")
            if object_id in self.fail or object_id in self.slip_once:
                self.slip_once.discard(object_id)
                raise RobotError("grasp_slipped", f"gripper closed on nothing at {object_id}")
            self._holding = object_id

    def place(self, object_id: str, pose: Pose, zone: str) -> None:
        with _span("robot.place", f"arm.place {object_id}", object_id=object_id, zone=zone, robot="mock"):
            self.calls.append(("place", object_id))
            where = "bin" if zone == BIN else f"({pose.x:.2f}, {pose.y:.2f}, {pose.z:.2f}) yaw {pose.yaw}"
            self.out(f"  [robot] place  {object_id:<18} at {where}")
            if self.world is not None:
                if zone == BIN:
                    self.world.pop(object_id, None)
                else:
                    old = self.world[object_id]
                    self.world[object_id] = Spot(zone, pose, old.extents)
            self._holding = None

    def say(self, text: str) -> None:
        self.out(f"  [robot] say    \"{text}\"")

    def led(self, state: str) -> None:
        self.out(f"  [robot] led    {state}")


@dataclass
class Outcome:
    done: list[Op] = field(default_factory=list)
    failed: list[tuple[Op, str]] = field(default_factory=list)
    skipped: list[tuple[Op, str]] = field(default_factory=list)


def execute(p: Plan, robot: Robot, publish: Callable[[str, dict], None] | None = None,
            job_id: str | None = None) -> Outcome:
    """Run the ops in order. A failed pick leaves that object where it was, so any later op
    that needs its spot is skipped rather than attempted — never place onto a stuck object.

    `publish("job", {...})` narrates it for the dashboard (docs/16 §3.1 job states); the
    robot's voice narrates the same thing in the room."""
    import secrets
    out = Outcome()
    stuck: dict[str, Spot] = {}
    jid = job_id or f"job_{secrets.token_hex(2)}"
    n = len(p.ops)

    def tell(state: str, i: int, **data) -> None:
        if publish:
            publish("job", {"id": jid, "state": state, "progress": round(i / n, 3) if n else 1.0, **data})

    tell("planned", 0, ops=n, unapplied=len(p.unapplied))
    for i, op in enumerate(p.ops):
        if op.object_id in stuck:
            out.skipped.append((op, f"'{op.object_id}' didn't move earlier"))
            tell("op_skipped", i + 1, object_id=op.object_id, reason=out.skipped[-1][1])
            continue
        blocker = next((o for o, s in stuck.items() if op.dst.zone != BIN and s.box.overlaps(op.dst.box)), None)
        if blocker:
            out.skipped.append((op, f"its target is still occupied by '{blocker}'"))
            stuck[op.object_id] = op.src
            tell("op_skipped", i + 1, object_id=op.object_id, reason=out.skipped[-1][1])
            continue
        try:
            tell("moving_to_pick", i, op=op.kind, object_id=op.object_id)
            if op.pick_base:
                robot.drive(op.pick_base)
            robot.pick(op.object_id, op.src.pose)
            tell("placing", i + 0.5, op=op.kind, object_id=op.object_id)
            if op.place_base and op.place_base != op.pick_base:
                robot.drive(op.place_base)
            robot.place(op.object_id, op.dst.pose, op.dst.zone)
        except RobotError as e:
            out.failed.append((op, str(e)))
            stuck[op.object_id] = op.src
            tell("op_failed", i + 1, object_id=op.object_id, error=e.code, detail=e.detail)
            if e.code == "not_balanced":  # the robot is recovering: nothing else is safe
                for rest in p.ops[i + 1:]:
                    out.skipped.append((rest, "robot not balanced"))
                break
            continue
        out.done.append(op)
    result = {"done": len(out.done), "failed": len(out.failed), "skipped": len(out.skipped),
              "unapplied": len(p.unapplied)}
    tell("done" if not (out.failed or out.skipped or p.unapplied) else "failed", n, result=result)
    return out


def render_plan(p: Plan) -> str:
    lines = []
    if p.ops:
        lines.append(f"plan: {len(p.ops)} operation{'s' * (len(p.ops) != 1)}, dependency-ordered")
        for i, op in enumerate(p.ops, 1):
            lines.append(f"  {i}. {op.kind:<8}{op.object_id:<20}{op.src}  ->  {op.dst}")
            if op.pick_base:
                lines.append(f"     {'':<28}stand at {op.pick_base}, then {op.place_base}")
    elif not p.unapplied:
        lines.append("plan: nothing to do — the room already matches")
    for _, msg in p.unapplied:
        lines.append(f"error: {msg}")
    if p.unapplied:
        n = len(p.unapplied)
        lines.append(f"hint: {n} hunk{'s' * (n != 1)} could not be applied. Human intervention required.")
    return "\n".join(lines)
