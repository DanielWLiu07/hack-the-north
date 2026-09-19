#!/usr/bin/env python3
"""Hand-authored scene -> object YAML -> a real commit in room.git + Elasticsearch documents.

The unblocker (README.md here). It plays the whole perception pipeline from a scene file,
so roomctl, elastic/ and web/ build against the real record shapes before a camera works —
and it fakes the MESS, not just the answer:

  - three disagreeing VLM descriptions per object per capture, one per camera view
  - per-camera coordinate conflicts (cam2's extrinsics are ~4.5 cm off)
  - rejected clusters with a rejected_reason: specks, cables, the robot's own arm, people
  - confidence that wobbles across a watch-loop sequence; one chronically flaky object
  - occlusion, partial (one camera blocked) and total (carried forward, not deleted)

Git gets the clean truth: jittered measurements go through quantization + hysteresis
(docs/20 Part 5), so an unchanged room is byte-identical. Elasticsearch gets the mess.

    python fake/scene_gen.py --commit clean_bench          scan + commit
    python fake/scene_gen.py --scan messy_bench            scan only: a dirty tree for `git status`
    python fake/scene_gen.py --commit movie_night --branch movie-night
    python fake/scene_gen.py --demo --reset                the whole history: 4 commits, 2 branches
    python fake/scene_gen.py --index fake/out/demo.ndjson  (re)send a saved run to Elasticsearch
    python fake/scene_gen.py --list

Every run writes its documents to fake/out/<run>.ndjson in _bulk format, and indexes them
when ELASTIC_URL / ELASTIC_API_KEY work and elastic/setup_elastic.py has created the targets.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import math
import os
import random
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from roomctl.repo import ROBOT_NAME, GitError, Repo, init, write_room_files  # noqa: E402
import obs  # noqa: E402  — no-ops unless this process called obs.init()
from roomctl.state import (HYST, ID_RE, Q_POS, Q_YAW, YAW_PERIOD, Extents, ObjectRecord, Pose,  # noqa: E402
                           iso_utc, settle, to_yaml, write_tree)

load_dotenv(ROOT / ".env")  # real environment variables win over the file

SCENES = HERE / "scenes"
OUT = HERE / "out"
STATE_FILE = "gitspace-fake.json"  # lives in <repo>/.git/: travels with the repo, never tracked
VLM_MODEL = "fake/scene_gen"  # every fake description in ES is attributable to this file

# Jitter on the FUSED measurement: (sigma, clamp). The clamps stay under half a quantum so
# an authored move lands exactly on its authored value, and inside the hysteresis deadband
# so a still object never changes. Per-camera readings are far noisier — those go to ES.
JIT_POS, JIT_YAW, JIT_EXT = (0.0015, 0.004), (0.8, 2.0), (0.0012, 0.003)

# The octree cube (docs/11). perception/voxelize.py owns the real encoder; this is its twin.
ORIGIN = tuple(float(os.getenv(f"ROOM_ORIGIN_{a}", d)) for a, d in (("X", -4.0), ("Y", -4.0), ("Z", 0.0)))
CUBE = float(os.getenv("ROOM_CUBE_SIZE", 8.0))
LEVELS = int(os.getenv("OCTREE_LEVELS", 7))
CELL = CUBE / 2 ** LEVELS
FLOOR_MARGIN = 1.2   # bbsim.py's --floor-margin default: the floor it knows reaches this far past the furniture
FLOOR_THICK = 0.02   # m. Must keep the floor's z_mid under costmap.py's Z_FLOOR or it becomes an obstacle

SNAPSHOT_INDICES = ("room-objects", "room-voxels", "room-clouds")


def _mapped(index: str, field: str) -> bool:
    """Is `field` in elastic/'s mapping? Every mapping is dynamic: strict — one unmapped field
    and the whole document is rejected."""
    try:
        return field in json.loads((ROOT / "elastic" / "mappings" / f"{index}.json").read_text())["mappings"]["properties"]
    except (OSError, KeyError, ValueError):
        return False


CLOUD_POSE = _mapped("room-clouds", "pose")  # the capture pose, room frame, yaw in degrees
DATA_STREAMS = ("room-observations", "room-events", "robot-telemetry")

# docs/22: the three rigs latch within milliseconds (grab() all, then retrieve() all) — not
# round-robin, whose ~0.5 s skew is 50x the quantum on a balancing robot.
LATCH_SKEW_MS = (0.6, 3.5)
GATE_SKEW_MS, GATE_TILT_RATE, GATE_COVERAGE = 25.0, 0.05, 0.60  # docs/22 §4, the quality gate
LATCH_WINDOW_S = 0.1      # tilt_rate_max is the peak |tilt_rate| within ±100 ms of the latch
TELEMETRY_HZ, TELEMETRY_S = 50, 2.0   # docs/23: the ring buffer's window; 2 s after the shutter
TELEMETRY_PRE_S = 8.0     # ...and 8 s before it: the drive to the capture pose (web /replay, docs/29)
WHEEL_CIRC, WHEELBASE = 0.518, 0.425  # m: 165 mm wheels (1 m = 1.93 turns), docs/02
DRIVE_V, DRIVE_RAMP = 0.25, 0.5       # m/s cruise, s to reach it
RETRY_S = 5               # a rejected capture waits for a quiet window and tries again
RIG = (-0.10, 0.0, 0.50)  # the camera rig in the world frame: a lean rotates the cloud about it
PIPELINE_S = 9        # capture -> commit latency (the Sentry waterfall in docs/16 §6)
WATCH_CAMERA = "cam0"  # the watch loop is the forward camera only (docs/11 "Watch mode")
WATCH_GATE_M = 0.10   # the watch loop's cheap nearest-neighbour association gate
ROUND_ASPECT = 1.2    # perception/cluster.py: rounder footprints report yaw 0

# The discard pile: (rejected_reason, what the VLM said, where it sits, points, confidence, YOLO label)
REJECTS = [
    ("too_small", ["small dark speck on the tablecloth", "a crumb or a bit of lint",
                   "tiny bright dot near the edge"], "desk", (8, 40), (0.10, 0.35), None),
    ("plane_fragment", ["edge of the table surface", "strip of wall behind the shelf",
                        "a sliver of the shelf board"], "edge", (2500, 9000), (0.20, 0.50), "dining table"),
    ("roomignore:cable", ["black USB cable coiled near the lamp", "thin wire running off the desk",
                          "white charging cable"], "desk", (90, 400), (0.30, 0.60), None),
    ("roomignore:robot", ["part of the robot's own arm, orange and black",
                          "a gripper at the corner of the frame", "robot bracket, out of focus"],
     "robot", (500, 3000), (0.40, 0.80), None),
    ("low_confidence", ["blurry reflective object, possibly a spoon", "glare on something metal",
                        "unclear shape near the shelf edge"], "shelf", (60, 300), (0.08, 0.28), None),
    ("single_frame", ["flickering bright patch", "a shadow that moved between frames",
                      "something visible in one frame only"], "desk", (30, 200), (0.20, 0.50), None),
    ("no_depth", ["white textureless region, no stereo match", "shiny surface, depth dropout"],
     "desk", (0, 15), (0.10, 0.30), None),
]
BYSTANDER = ("roomignore:person", ["a person standing behind the table, out of focus",
                                   "someone's torso in a grey hoodie"],
             "person", (4000, 15000), (0.70, 0.95), "person")
HAND = ("roomignore:person", ["a hand reaching toward the table", "forearm in a grey sleeve",
                              "fingers holding something"], "hand", (800, 3000), (0.60, 0.92), "person")


class SceneError(Exception):
    pass


# ── scenes ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Truth:
    """Where an object really is. The fake knows; perception would have to find out."""
    id: str
    cls: str
    zone: str
    x: float
    y: float
    z: float
    yaw: float
    ex: float
    ey: float
    ez: float
    color: str
    label: str
    confidence: float
    descriptions: tuple[str, ...]
    flaky: bool = False
    hidden_from: tuple[str, ...] = ()

    @classmethod
    def parse(cls, d: dict) -> Truth:
        try:
            x, y, z, yaw = d["pose"]
            ex, ey, ez = d["extents"]
            return cls(id=d["id"], cls=d["class"], zone=d["zone"], x=x, y=y, z=z, yaw=yaw,
                       ex=ex, ey=ey, ez=ez, color=d["color"], label=d.get("label") or d["class"],
                       confidence=float(d.get("confidence", 0.8)),
                       descriptions=tuple(dict.fromkeys(d["descriptions"])), flaky=bool(d.get("flaky")),
                       hidden_from=tuple(d.get("hidden_from") or ()))
        except (KeyError, TypeError, ValueError) as e:
            raise SceneError(f"object {d.get('id', '?')}: {type(e).__name__} {e}") from None

    def footprint(self) -> tuple[float, float, float, float]:
        """Axis-aligned bounds of the rotated box on the floor plane: x0, x1, y0, y1."""
        c, s = abs(math.cos(math.radians(self.yaw))), abs(math.sin(math.radians(self.yaw)))
        hx, hy = (self.ex * c + self.ey * s) / 2, (self.ex * s + self.ey * c) / 2
        return self.x - hx, self.x + hx, self.y - hy, self.y + hy


@dataclass
class Scene:
    name: str
    room: dict
    cameras: dict[str, dict]
    objects: dict[str, Truth]
    occlude: dict[str, list[str]] = field(default_factory=dict)
    message: str | None = None


def scene_path(name: str) -> Path:
    p = Path(name)
    return p if p.suffix == ".yaml" else SCENES / f"{name}.yaml"


_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_cache: dict[Path, tuple[float, dict]] = {}


def _read(path: Path) -> dict:
    """Parsed scene file, cached until it changes on disk (tests rescan hundreds of times)."""
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in SCENES.glob("*.yaml")))
        raise SceneError(f"no scene {path.stem!r} (have: {known})")
    mtime = path.stat().st_mtime
    hit = _cache.get(path)
    if hit is None or hit[0] != mtime:
        _cache[path] = hit = (mtime, yaml.load(path.read_text(), Loader=_LOADER) or {})
    return copy.deepcopy(hit[1])


def _resolve(path: Path, seen: tuple = ()) -> dict:
    """Follow `extends`, applying move/remove/add. Returns plain dicts."""
    if path in seen:
        raise SceneError(f"extends cycle through {path.stem}")
    raw = _read(path)
    if "extends" not in raw:
        return {"room": raw["room"], "cameras": raw["cameras"],
                "objects": {o["id"]: dict(o) for o in raw["objects"]}}
    base = _resolve(scene_path(raw["extends"]), seen + (path,))
    objs = base["objects"]
    for oid, change in (raw.get("move") or {}).items():
        if oid not in objs:
            raise SceneError(f"{path.stem}: move {oid}: not in {raw['extends']}")
        o = objs[oid] = dict(objs[oid])
        pose = list(o["pose"])
        for i, k in enumerate(("x", "y", "z", "yaw")):
            if k in change:
                pose[i] = change[k]
        o["pose"] = pose
        o["zone"] = change.get("zone", o["zone"])
    for oid in raw.get("remove") or []:
        if objs.pop(oid, None) is None:
            raise SceneError(f"{path.stem}: remove {oid}: not in {raw['extends']}")
    for o in raw.get("add") or []:
        if o["id"] in objs:
            raise SceneError(f"{path.stem}: add {o['id']}: already in {raw['extends']}")
        objs[o["id"]] = dict(o)
    return {**base, "objects": objs}


def load_scene(name: str) -> Scene:
    path = scene_path(name)
    raw = _read(path)
    resolved = _resolve(path)
    scene = Scene(name=path.stem, room=resolved["room"], cameras=resolved["cameras"],
                  objects={oid: Truth.parse(o) for oid, o in sorted(resolved["objects"].items())},
                  occlude={k: list(v) for k, v in (raw.get("occlude") or {}).items()},
                  message=raw.get("message"))
    check_scene(scene)
    return scene


def check_scene(s: Scene) -> None:
    """Authoring mistakes, caught before they become a mysterious diff."""
    zones, cams = s.room["zones"], set(s.cameras)
    problems = []
    for t in s.objects.values():
        if not ID_RE.match(t.id):
            problems.append(f"{t.id}: id must be <class_slug>_<4 hex>")
        if t.zone not in zones:
            problems.append(f"{t.id}: unknown zone {t.zone!r}")
            continue
        z = zones[t.zone]
        if not all(lo <= v <= hi for v, lo, hi in zip((t.x, t.y, t.z), z["min"], z["max"])):
            problems.append(f"{t.id}: centre {(t.x, t.y, t.z)} is outside zone {t.zone}")
        if abs(t.z - t.ez / 2 - z["surface"]) > 0.01:
            problems.append(f"{t.id}: bottom at z={t.z - t.ez / 2:.3f}, but {t.zone} surface is {z['surface']}")
        # Authored the way perception reports it (perception/cluster.py Instance.box()):
        # yaw is the axis of extents.x in [0, 180), x is the long side, round footprints are 0.
        aspect = max(t.ex, t.ey) / min(t.ex, t.ey)
        if not 0 <= t.yaw < YAW_PERIOD:
            problems.append(f"{t.id}: yaw {t.yaw} must be in [0, {YAW_PERIOD}) — it's an axis")
        elif aspect < ROUND_ASPECT and t.yaw != 0:
            problems.append(f"{t.id}: footprint aspect {aspect:.2f} < {ROUND_ASPECT} has no axis; yaw must be 0")
        elif aspect >= ROUND_ASPECT and t.ex < t.ey:
            problems.append(f"{t.id}: extents x must be the long side (swap x/y and add 90 to yaw)")
        if len(set(t.descriptions)) < 3:
            problems.append(f"{t.id}: needs at least 3 distinct descriptions")
        if set(t.hidden_from) - cams:
            problems.append(f"{t.id}: hidden_from names unknown cameras")
    for oid, oc in s.occlude.items():
        if oid not in s.objects:
            problems.append(f"occlude: {oid} is not in the scene")
        if set(oc) - cams:
            problems.append(f"occlude: {oid} names unknown cameras")
    objs = list(s.objects.values())
    for i, a in enumerate(objs):
        for b in objs[i + 1:]:
            if a.zone == b.zone:
                ax0, ax1, ay0, ay1 = a.footprint()
                bx0, bx1, by0, by1 = b.footprint()
                if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                    problems.append(f"{a.id} and {b.id} overlap on the {a.zone}")
    if problems:
        raise SceneError(f"scene {s.name}:\n  " + "\n  ".join(problems))


def catalog() -> dict[str, Truth]:
    """Every object any scene knows about — metadata for HEAD objects the current scene lacks."""
    out: dict[str, Truth] = {}
    for p in sorted(SCENES.glob("*.yaml")):
        for oid, o in _resolve(p)["objects"].items():
            out.setdefault(oid, Truth.parse(o))
    return out


# ── helpers ──────────────────────────────────────────────────────────────────

def ts(t: datetime) -> str:
    """ES timestamp, millisecond precision — TSDS identity is (dimensions, @timestamp)."""
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def hash32(s: str) -> int:
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16)


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def jitter(rng: random.Random, sigma_clamp: tuple[float, float]) -> float:
    sigma, lim = sigma_clamp
    return clamp(rng.gauss(0, sigma), -lim, lim)


def stabilize(new: float, committed: float | None, q: float = Q_POS, hyst: float = HYST) -> float:
    """docs/20 Part 5, verbatim: quantize, but keep the committed value inside the deadband.
    perception/serialize.py owns the real one; this twin uses the same roomctl.state constants."""
    if committed is not None and abs(new - committed) < q * hyst:
        return committed
    return round(new / q) * q


def yaw_diff(a: float, b: float) -> float:
    """Smallest turn between two AXES: 175 vs 5 is 10, not 170."""
    h = YAW_PERIOD / 2
    return (a - b + h) % YAW_PERIOD - h


def stabilize_yaw(new: float, committed: int | None) -> int:
    if committed is not None and abs(yaw_diff(new, committed)) < Q_YAW * HYST:
        return committed
    return int(round(new / Q_YAW) * Q_YAW) % YAW_PERIOD


def octree_key(x: float, y: float, z: float, origin=ORIGIN, size=CUBE, levels=LEVELS) -> str | None:
    """docs/11-elastic.md encoder: one base-8 digit per level, (bx<<2)|(by<<1)|bz."""
    f = [(v - o) / size for v, o in zip((x, y, z), origin)]
    if any(c < 0.0 or c >= 1.0 for c in f):
        return None
    digits = []
    for _ in range(levels):
        f = [c * 2 for c in f]
        bits = [int(c) for c in f]
        f = [c - b for c, b in zip(f, bits)]
        digits.append(str((bits[0] << 2) | (bits[1] << 1) | bits[2]))
    return "".join(digits)


def wobble(oid: str, t: datetime, flaky: bool) -> float:
    """Slow confidence drift per object. Flaky objects swing hard and fast."""
    phase = (hash32(oid) % 1000) / 1000 * 2 * math.pi
    period, amp = (180.0, 0.22) if flaky else (420.0, 0.04)
    return amp * math.sin(2 * math.pi * t.timestamp() / period + phase)


def area(t: Truth) -> float:
    return t.ex * t.ey + t.ex * t.ez + t.ey * t.ez


def spot(kind: str, zones: dict, rng: random.Random, near: Truth | None = None) -> tuple[float, float, float]:
    if kind == "hand" and near is not None:
        return near.x - 0.06 + rng.uniform(-0.03, 0.03), near.y + rng.uniform(-0.05, 0.05), near.z + 0.12
    if kind in zones:
        z = zones[kind]
        return (rng.uniform(z["min"][0], z["max"][0]), rng.uniform(z["min"][1], z["max"][1]),
                z["surface"] + rng.uniform(0.0, 0.02))
    if kind == "edge":
        z = zones["desk"]
        return z["min"][0] + rng.uniform(0, 0.03), rng.uniform(z["min"][1], z["max"][1]), z["surface"]
    if kind == "robot":
        return rng.uniform(-0.05, 0.05), rng.uniform(-0.12, 0.12), rng.uniform(0.45, 0.65)
    return 1.45 + rng.uniform(0, 0.2), rng.uniform(-0.5, 0.5), rng.uniform(1.0, 1.5)  # person


def rnd(v: float | None, n: int = 4) -> float | None:
    return None if v is None else round(v, n)


# ── the fake room ────────────────────────────────────────────────────────────

@dataclass
class Result:
    capture_id: str
    at: datetime
    verdicts: dict[str, tuple[str, str]]  # object_id -> (verdict, detail)
    paths: dict[str, str]                 # object_id -> file path in this scan (or HEAD, if removed)
    sha: str | None = None
    parent: str | None = None
    branch: str | None = None
    message: str | None = None
    changes: list[tuple[str, str]] = field(default_factory=list)  # (git status letter, path)


class DocClock:
    """Each camera's docs start at its latch instant (all within a few ms, docs/22). Every
    further doc from that camera steps 1 ms: in a TSDS the same dimensions + @timestamp
    overwrite each other, and rejected rows all share object_id=null."""

    def __init__(self, t0: datetime, latch_ms: dict[str, float]):
        self.t0, self.offset, self.n = t0, latch_ms, {c: 0 for c in latch_ms}

    def __call__(self, cam: str) -> datetime:
        t = self.t0 + timedelta(milliseconds=self.offset[cam] + self.n[cam])
        self.n[cam] += 1
        return t


def trace(seed: str, capture_id: str) -> dict:
    """Synthetic Sentry ids: every doc of one capture shares a trace, as obs.trace_fields()
    would give it. No sentry_url on purpose — a link to a trace that never existed is dead."""
    h = hashlib.sha1(f"{seed}:{capture_id}:trace".encode()).hexdigest()
    return {"sentry_trace_id": h[:32], "sentry_span_id": hashlib.sha1(h.encode()).hexdigest()[:16]}


def _tag(key: str, value: str) -> None:
    try:
        import sentry_sdk
        sentry_sdk.get_current_scope().set_tag(key, value)
    except Exception:  # noqa: BLE001
        pass


def _sentry_live() -> bool:
    try:
        import sentry_sdk
        return sentry_sdk.get_client().is_active()
    except Exception:  # noqa: BLE001 — no SDK, or an SDK without get_client: not live
        return False


def capture_ok(skew_ms: float, tilt_rate_max: float, coverage_pct: float) -> bool:
    """docs/22 §4: cameras latched together, robot genuinely settled, enough valid depth."""
    return skew_ms < GATE_SKEW_MS and tilt_rate_max < GATE_TILT_RATE and coverage_pct > GATE_COVERAGE


def leaned(t: Truth, lean: tuple[float, float, float]) -> Truth:
    """Where a capture taken mid-lean puts an object: the cloud pitched about the rig, then
    registered with a stale pose (the odometry residual). Every object shifts at once."""
    pitch, dx, dy = lean
    x, z = t.x - RIG[0], t.z - RIG[2]
    c, s = math.cos(pitch), math.sin(pitch)
    return replace(t, x=RIG[0] + c * x + s * z + dx, y=t.y + dy, z=RIG[2] - s * x + c * z)


class FakeRoom:
    def __init__(self, repo: Path, *, seed: str = "gitspace", quiet: bool = False, sentry: bool = False):
        self.git = Repo(repo)  # refuses a path that contains the code repo
        self.repo = self.git.path
        self.seed, self.quiet = seed, quiet
        self.sentry = sentry  # run each commit-path capture as a real Sentry transaction (--sentry)
        self.actions: list[tuple[dict, dict]] = []  # (bulk action line, source)
        self._state: dict | None = None
        self._catalog: dict[str, Truth] | None = None
        self._trace: dict = {}  # Sentry ids of the capture being emitted
        self._traces: dict[str, dict] = {}
        self._tel_until: datetime | None = None   # last telemetry sample emitted: never twice
        self._odo: list[float] | None = None       # cumulative wheel turns, left/right
        self._pose: list[float] | None = None      # the robot in the room frame: x, y, yaw (deg)

    # -- persistent fake state: capture counter, clock, what has ever been seen ----

    @property
    def state(self) -> dict:
        if self._state is None:
            p = self.repo / ".git" / STATE_FILE
            self._state = json.loads(p.read_text()) if p.is_file() else \
                {"captures": 0, "watch": 0, "clock": None, "seen": {}}
        return self._state

    def save_state(self) -> None:
        (self.repo / ".git" / STATE_FILE).write_text(json.dumps(self.state, indent=1, sort_keys=True))

    def clock(self) -> datetime | None:
        c = self.state["clock"]
        return datetime.fromisoformat(c) if c else None

    def advance(self, t: datetime) -> None:
        c = self.clock()
        if c is None or t > c:
            self.state["clock"] = t.isoformat()

    def next_time(self, at: datetime | None, lead: timedelta = timedelta(0)) -> datetime:
        """A capture time after everything already emitted, leaving `lead` for a watch window."""
        t = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
        c = self.clock()
        if c is not None and t - lead <= c:
            t = (c + lead + timedelta(seconds=2)).replace(microsecond=0)
        return t

    def log(self, *a) -> None:
        if not self.quiet:
            print(*a)

    def trace_ids(self, cap: str) -> dict:
        """Every doc of one capture shares one trace. obs.trace_fields() inside the capture's
        scope when a Sentry client is live in this process — the real join. Otherwise the
        synthetic ids (web labels them "synthetic trace"): with no client the SDK still makes
        span ids, and those would be links to traces Sentry never received (docs/10 GAP 6)."""
        if cap not in self._traces:
            fields = {}
            with obs.capture_scope(cap) as tf:
                if tf and _sentry_live():
                    fields = dict(tf)
            self._traces[cap] = fields or trace(self.seed, cap)
        return self._traces[cap]

    # -- repo ----------------------------------------------------------------

    def reset(self) -> None:
        if not self.repo.exists():
            return
        if not (self.repo / ".git" / STATE_FILE).is_file() and any(self.repo.iterdir()):
            raise SystemExit(f"refusing to delete {self.repo}: scene_gen did not create it "
                             f"(no .git/{STATE_FILE})")
        shutil.rmtree(self.repo)
        self._state = None

    def ensure_repo(self, scene: Scene) -> None:
        if not self.git.exists:
            init(self.repo, scene.room)
            self.save_state()
            self.log(f"init      {self.repo}")
        write_room_files(self.repo, scene.room)

    def switch(self, branch: str) -> None:
        """Teleport: the fake room simply IS the branch. The real thing goes through the executor."""
        if not self.git.exists or self.git.branch() == branch:
            return
        new = not self.git.branch_exists(branch)
        self.git.switch(branch, create=new)
        if self.git.has_head():
            self.log(f"branch    {branch}{' (new)' if new else ''}")

    # -- the commit-path capture: 3 cameras, VLM descriptions, fusion, stabilize ---

    def capture(self, scene: Scene, t0: datetime,
                lean: tuple[float, float, float] | None = None) -> tuple[Result, list[ObjectRecord]]:
        head = self.git.records()
        self.state["captures"] += 1
        cap = f"cap_{self.state['captures']:04d}"
        self._trace = self.trace_ids(cap)
        cams = sorted(scene.cameras)
        lrng = random.Random(f"{self.seed}:{cap}:latch")
        skew = lrng.uniform(*LATCH_SKEW_MS)
        offsets = [0.0] + sorted(lrng.uniform(0, skew) for _ in cams[1:])
        offsets[-1] = skew if len(cams) > 1 else 0.0
        clock = DocClock(t0, dict(zip(cams, offsets)))
        zones = scene.room["zones"]
        seen_before = self.state["seen"]
        records, verdicts, paths, meta = [], {}, {}, {}

        for t in scene.objects.values():
            rng = random.Random(f"{self.seed}:{cap}:{t.id}")
            if lean:
                t = leaned(t, lean)
            visible = [c for c in cams if c not in t.hidden_from]
            blocked = [c for c in visible if c in scene.occlude.get(t.id, ())]
            seeing = [c for c in visible if c not in blocked]
            if t.flaky and seeing:  # small and shiny: cameras drop it, the capture burst keeps one
                seeing = [c for c in seeing if rng.random() > 0.3] or seeing[:1]
            readings = {}  # camera -> (confidence, raw xyz, point count): uncalibrated, unquantized
            for cam in seeing:
                cc = scene.cameras[cam]
                conf = clamp(t.confidence + cc["quality"] + wobble(t.id, t0, t.flaky)
                             + rng.gauss(0, 0.08 if t.flaky else 0.03), 0.03, 0.99)
                noise = cc["noise"] * (2 if t.flaky else 1)
                raw = [v + b + rng.gauss(0, noise) for v, b in zip((t.x, t.y, t.z), cc["bias"])]
                readings[cam] = (conf, raw, int(area(t) * 60000 * (0.55 + 0.45 * conf) * (1 + cc["quality"])))
            # One VLM call per view. Short of three views, the first camera gets re-asked, so
            # every object always carries three descriptions — and they never agree.
            descs = rng.sample(t.descriptions, 3)
            attempts = [(c, 1) for c in seeing] + [(seeing[0], n) for n in range(2, 5 - len(seeing))] \
                if seeing else []
            for desc, (cam, attempt) in zip(descs, attempts):
                conf, raw, pts = readings[cam]
                self.observation(clock(cam), cap, cam, t.id, conf, pts, raw,
                                 description=desc, label=t.label, attempt=attempt)
            for cam in blocked:  # the occlusion event: this camera should have seen it, and didn't
                pts = rng.randint(0, 25)
                raw = None if pts < 20 else [v + b + rng.uniform(-0.04, 0.04) for v, b in
                                             zip((t.x, t.y, t.z), scene.cameras[cam]["bias"])]
                self.observation(clock(cam), cap, cam, t.id, rng.uniform(0.02, 0.2), pts, raw,
                                 occluded=True)

            prev = head.get(t.id)
            if not seeing:
                if prev:  # in HEAD, absent, occluder in the way: carried forward unchanged
                    records.append(prev)
                    verdicts[t.id] = ("unobserved", f"occluded from {' '.join(blocked)}")
                    paths[t.id] = prev.path
                    meta[t.id] = {"observed_by": [], "confidence": None, "point_count": 0,
                                  "descriptions": seen_before.get(t.id, {}).get("descriptions", [])}
                continue
            rec = settle(prev, self.measure(t, prev, rng,
                                            seen_before.get(t.id, {}).get("first_seen") or iso_utc(t0)))
            records.append(rec)
            paths[t.id] = rec.path
            meta[t.id] = {"observed_by": sorted(readings), "descriptions": descs,
                          "point_count": sum(r[2] for r in readings.values()),
                          "confidence": round(sum(r[0] for r in readings.values()) / len(readings), 3)}
            if prev is None:  # "returned" = committed before, absent from HEAD, back (docs/20 Part 4)
                verdicts[t.id] = ("returned", "id reused from history") \
                    if seen_before.get(t.id, {}).get("committed") else ("added", "")
            elif to_yaml(prev) == to_yaml(rec):
                verdicts[t.id] = ("unchanged", "")
            else:
                d = math.dist((prev.pose.x, prev.pose.y, prev.pose.z), (rec.pose.x, rec.pose.y, rec.pose.z))
                how = [f"{d:.2f} m"] if d else []
                how += [f"yaw {prev.pose.yaw} -> {rec.pose.yaw}"] if prev.pose.yaw != rec.pose.yaw else []
                how += [f"{prev.zone} -> {rec.zone}"] if prev.zone != rec.zone else []
                verdicts[t.id] = ("moved", ", ".join(how) or "extents changed")
            seen_before.setdefault(t.id, {})["first_seen"] = rec.first_seen
            seen_before[t.id]["descriptions"] = descs

        for oid, prev in head.items():
            if oid not in scene.objects:  # every camera had line of sight, nothing there
                verdicts[oid] = ("removed", "")
                paths[oid] = prev.path

        rng = random.Random(f"{self.seed}:{cap}:rejects")
        for cam in cams:
            for _ in range(rng.choice((0, 1, 1, 2, 2, 3))):
                self.reject(clock(cam), cap, cam, rng.choice(REJECTS), zones, rng)
            if rng.random() < 0.2:
                self.reject(clock(cam), cap, cam, BYSTANDER, zones, rng)

        self._last_capture = {"capture_id": cap, "at": t0, "cams": cams, "meta": meta, "rng": rng,
                              "skew_ms": round(skew, 2)}
        return Result(cap, t0, verdicts, paths), records

    def measure(self, t: Truth, prev: ObjectRecord | None, rng: random.Random, first_seen: str) -> ObjectRecord:
        """The fused estimate after merge: truth + sub-quantum jitter, then stabilize vs HEAD."""
        p, e = (prev.pose, prev.extents) if prev else (None, None)
        x, y, z = (v + jitter(rng, JIT_POS) for v in (t.x, t.y, t.z))
        ex, ey, ez = (v + jitter(rng, JIT_EXT) for v in (t.ex, t.ey, t.ez))
        return ObjectRecord(
            id=t.id, cls=prev.cls if prev else t.cls, zone=t.zone,
            pose=Pose(stabilize(x, p and p.x), stabilize(y, p and p.y), stabilize(z, p and p.z),
                      stabilize_yaw(t.yaw + jitter(rng, JIT_YAW), p and p.yaw)),
            extents=Extents(*(max(Q_POS, stabilize(v, c)) for v, c in
                              zip((ex, ey, ez), (e and e.x, e and e.y, e and e.z)))),
            color=prev.color if prev else t.color,
            first_seen=prev.first_seen if prev else first_seen,
        )

    # -- the watch loop: cam0 only, stock detector, no VLM, cheap association ---

    def watch(self, before: Scene, after: Scene, t_from: datetime, t_to: datetime,
              t_change: datetime | None, every: timedelta) -> int:
        head = self.git.records()
        zones = before.room["zones"]
        touched = {oid for oid in before.objects.keys() | after.objects.keys()
                   if moved(before.objects.get(oid), after.objects.get(oid))
                   or before.occlude.get(oid) != after.occlude.get(oid)}
        n, t = 0, t_from
        while t < t_to:
            self.state["watch"] += 1
            cap = f"watch_{self.state['watch']:05d}"
            rng = random.Random(f"{self.seed}:{cap}")
            self._trace = self.trace_ids(cap)
            clock = DocClock(t, {WATCH_CAMERA: 0.0})
            scene = after if t_change is not None and t >= t_change else before
            hand = t_change is not None and abs((t - t_change).total_seconds()) <= 6
            cam = scene.cameras[WATCH_CAMERA]
            for o in scene.objects.values():
                if WATCH_CAMERA in o.hidden_from:
                    continue
                if (hand and o.id in touched) or WATCH_CAMERA in scene.occlude.get(o.id, ()):
                    if o.id in head:
                        self.observation(clock(WATCH_CAMERA), cap, WATCH_CAMERA, o.id,
                                         rng.uniform(0.02, 0.15), rng.randint(0, 12), None, occluded=True)
                    continue
                if rng.random() > (0.6 if o.flaky else 0.97):
                    continue  # the detector simply missed it this frame
                raw = [v + b + rng.gauss(0, 0.006 * (2 if o.flaky else 1))
                       for v, b in zip((o.x, o.y, o.z), cam["bias"])]
                conf = clamp(o.confidence - 0.05 + wobble(o.id, t, o.flaky)
                             + rng.gauss(0, 0.08 if o.flaky else 0.03), 0.03, 0.99)
                oid = nearest(head, raw)
                self.observation(clock(WATCH_CAMERA), cap, WATCH_CAMERA, oid, conf,
                                 int(area(o) * 30000 * (0.55 + 0.45 * conf)), raw, label=o.label,
                                 rejected=None if oid else "unassociated")
            if hand:
                near = [o for oid in sorted(touched) for o in (before.objects.get(oid), after.objects.get(oid)) if o]
                for o in near[:2]:
                    self.reject(clock(WATCH_CAMERA), cap, WATCH_CAMERA, HAND, zones, rng, near=o, vlm=False)
            elif rng.random() < 0.15:
                self.reject(clock(WATCH_CAMERA), cap, WATCH_CAMERA,
                            rng.choice([r for r in REJECTS if r[0] != "plane_fragment"]), zones, rng, vlm=False)
            self.advance(t + timedelta(seconds=1))
            n += 1
            t += every
        return n

    # -- documents ------------------------------------------------------------

    def observation(self, at: datetime, cap: str, cam: str, oid: str | None, conf: float, pts: int,
                    raw: list[float] | None, *, occluded: bool = False, rejected: str | None = None,
                    description: str | None = None, label: str | None = None, attempt: int | None = None):
        self.actions.append(({"create": {"_index": "room-observations"}}, {
            "@timestamp": ts(at), "capture_id": cap, "object_id": oid, "camera": cam,
            "confidence": round(conf, 3), "point_count": pts,
            "raw_x": rnd(raw and raw[0]), "raw_y": rnd(raw and raw[1]), "raw_z": rnd(raw and raw[2]),
            "occluded": occluded, "rejected_reason": rejected,
            "raw_description": description, "raw_label": label,
            "vlm_model": VLM_MODEL if description else None,
            "label_attempt": attempt if description else None, **self._trace,
        }))
        self.advance(at)

    def telemetry(self, t0: datetime, bump: bool, home=(-0.40, 0.0, 0.0)) -> dict:
        """50 Hz telemetry from 8 s before the shutter to 2 s after — the ring buffer is what makes
        the window around the shutter knowable after the fact (docs/23 §2). The robot drives a
        short arc to its capture pose and stops; the encoders, motor currents and pose integrate
        that one drive, so web's /replay can draw the path (docs/29). A bump hits ~200 ms before
        the shutter; the balance controller is still ringing when the cameras latch, and the pose
        estimate hasn't caught up (the residual spikes).

        The ±2 s samples draw from the same random stream they always did, so a regenerated
        capture is byte-identical there; the extra 6 s and the drive have streams of their own."""
        rng = random.Random(f"{self.seed}:{ts(t0)}:telemetry")
        pre = random.Random(f"{self.seed}:{ts(t0)}:telemetry-pre")
        drv = random.Random(f"{self.seed}:{ts(t0)}:drive")
        phase = rng.uniform(0, 2 * math.pi)
        pitch0, resid0 = rng.uniform(0.012, 0.024), rng.uniform(0.003, 0.005)

        if self._odo is None:
            self._odo = [drv.uniform(80, 400), drv.uniform(80, 400)]
            self._pose = [home[0], home[1], home[2]]
        dist = 0.40 if bump else drv.uniform(0.20, 0.55)
        dist *= 1 if self.state["captures"] % 2 == 0 else -1   # there and back: stay near home
        arc = drv.uniform(-0.12, 0.12)                          # left/right split: a gentle curve
        stop = -1.0 if bump else drv.uniform(-1.8, -1.2)
        start = stop - (abs(dist) / DRIVE_V + DRIVE_RAMP)

        def travelled(dt: float) -> tuple[float, float, float]:
            """(metres so far, speed, |acceleration|) of a trapezoidal drive start -> stop."""
            if dt <= start:
                return 0.0, 0.0, 0.0
            if dt >= stop:
                return abs(dist), 0.0, 0.0
            a, t, T = DRIVE_V / DRIVE_RAMP, dt - start, stop - start
            if t < DRIVE_RAMP:
                return a * t * t / 2, a * t, a
            if t > T - DRIVE_RAMP:
                u = T - t
                return abs(dist) - a * u * u / 2, a * u, a
            return DRIVE_V * (t - DRIVE_RAMP / 2), DRIVE_V, 0.0

        sign = 1 if dist >= 0 else -1
        odo0 = list(self._odo)
        first = -TELEMETRY_PRE_S
        if self._tel_until is not None:  # a retry 5 s later must not re-emit the last window
            first = max(first, math.ceil(((self._tel_until - t0).total_seconds() + 1e-6) * TELEMETRY_HZ)
                        / TELEMETRY_HZ)
        n0, n1 = round(first * TELEMETRY_HZ), round(TELEMETRY_S * TELEMETRY_HZ)
        peak, shutter, held = 0.0, {}, []
        for i in range(-round(TELEMETRY_S * TELEMETRY_HZ) if first > -TELEMETRY_S else n0, n1 + 1):
            dt = i / TELEMETRY_HZ
            r = rng if dt >= -TELEMETRY_S - 1e-9 else pre
            tilt = 0.006 * math.sin(2 * math.pi * 1.3 * dt + phase) + r.gauss(0, 0.004)
            pitch = pitch0 + 0.003 * math.sin(2 * math.pi * 0.4 * dt + phase) + r.gauss(0, 0.0008)
            resid = resid0 + 0.0006 * math.sin(2 * math.pi * 0.2 * dt) + r.gauss(0, 0.0003)
            s_m, v, acc = travelled(dt)
            current = [0.30 + 0.4 * v + 0.9 * acc + drv.gauss(0, 0.02) for _ in range(2)]
            if bump and dt >= -0.2:
                s, w = dt + 0.2, 2 * math.pi * 2.0
                ring = math.exp(-s / 0.45)
                tilt += 0.14 * ring * math.cos(w * s)
                pitch += 0.14 / w * ring * math.sin(w * s)
                # the pose estimate lags the knock by several cm — past settle()'s 5 cm, which is
                # exactly why the gate has to reject this capture rather than trust hysteresis
                resid += 0.07 * math.exp(-s / 0.8) * (1 - math.exp(-s / 0.05))
                current = [c + 0.8 * ring for c in current]      # the controller fighting the knock
            if dt < first - 1e-9:
                continue  # already emitted by the previous capture's window
            left = odo0[0] + sign * s_m * (1 - arc) / WHEEL_CIRC
            right = odo0[1] + sign * s_m * (1 + arc) / WHEEL_CIRC
            at = t0 + timedelta(seconds=dt)
            for signal, val in (("pitch", pitch), ("tilt_rate", tilt), ("odom_residual", resid),
                                ("left_enc", left), ("right_enc", right),
                                ("motor_current_l", current[0]), ("motor_current_r", current[1]),
                                ("balanced", 1)):
                self.actions.append(({"create": {"_index": "robot-telemetry"}},
                                     {"@timestamp": ts(at), "signal": signal, "value": round(val, 5),
                                      **self._trace}))
            if abs(dt) <= LATCH_WINDOW_S + 1e-9:
                peak = max(peak, abs(tilt))
            if dt <= 0:  # what the Pi's ring buffer held when the shutter fired
                held = (held + [{"t": ts(at), "pitch": round(pitch, 5), "tilt_rate": round(tilt, 5),
                                 "odom_residual": round(resid, 5)}])[-40:]
            if abs(dt) < 1e-9:
                shutter = {"pitch": pitch - pitch0, "odom_residual": resid}
        # the drive, integrated once: cumulative turns and the pose it left the robot in
        dl, dr = sign * abs(dist) * (1 - arc), sign * abs(dist) * (1 + arc)
        self._odo = [odo0[0] + dl / WHEEL_CIRC, odo0[1] + dr / WHEEL_CIRC]
        x, y, yaw = self._pose
        dyaw = math.degrees((dr - dl) / WHEELBASE)
        mid = math.radians(yaw + dyaw / 2)
        self._pose = [x + (dl + dr) / 2 * math.cos(mid), y + (dl + dr) / 2 * math.sin(mid), (yaw + dyaw) % 360]
        self._tel_until = t0 + timedelta(seconds=TELEMETRY_S)
        self.advance(self._tel_until)
        return {"tilt_rate_max": round(peak, 4), "ring": held, **shutter,
                "pose": {"x": round(self._pose[0], 3), "y": round(self._pose[1], 3), "yaw": round(self._pose[2], 1)}}

    def reject(self, at, cap, cam, kind, zones, rng, near: Truth | None = None, vlm: bool = True):
        reason, texts, where, (p0, p1), (c0, c1), label = kind
        pts = rng.randint(p0, p1)
        raw = None if pts < 20 else spot(where, zones, rng, near)
        self.observation(at, cap, cam, None, rng.uniform(c0, c1), pts, raw, rejected=reason,
                         description=rng.choice(texts) if vlm else None, label=label,
                         attempt=1 if vlm else None)

    def cloud(self, res: Result, scene: Scene, sha: str | None) -> None:
        self.actions.append(({"index": {"_index": "room-clouds", "_id": res.capture_id}},
                             self.cloud_doc(res, scene, sha)))

    def cloud_doc(self, res: Result, scene: Scene, sha: str | None) -> dict:
        lc = self._last_capture
        rng, gate = random.Random(f"{self.seed}:{res.capture_id}:cloud"), lc["gate"]
        pts = sum(m["point_count"] for m in lc["meta"].values())
        zmin = [min(z["min"][i] for z in scene.room["zones"].values()) for i in range(3)]
        zmax = [max(z["max"][i] for z in scene.room["zones"].values()) for i in range(3)]
        return {
            "@timestamp": ts(res.at), "capture_id": res.capture_id, "commit_sha": sha,
            "cloud_uri": None,  # synthetic: there is no .ply behind a fake capture
            "point_count": pts + rng.randint(1_200_000, 2_600_000),  # + table, walls, floor
            "bounds": {"min": dict(zip("xyz", zmin)), "max": dict(zip("xyz", zmax))},
            "cameras": lc["cams"], "icp_residual_mm": round(rng.uniform(3.0, 6.5), 2),
            **gate, **self.trace_ids(res.capture_id),
        }

    def rejection(self, res: Result, gate: dict, retry: str) -> None:
        """The capture the gate threw away, and the diff it would have committed."""
        why = [f"tilt_rate_max {gate['tilt_rate_max']:.3f} rad/s > {GATE_TILT_RATE}"] \
            if gate["tilt_rate_max"] >= GATE_TILT_RATE else []
        why += [f"skew_ms {gate['skew_ms']} > {GATE_SKEW_MS:g}"] if gate["skew_ms"] >= GATE_SKEW_MS else []
        why += [f"coverage {gate['coverage_pct']} < {GATE_COVERAGE}"] if gate["coverage_pct"] <= GATE_COVERAGE else []
        by = {"moved": "objects_moved", "added": "objects_added", "returned": "objects_added",
              "removed": "objects_removed"}
        ev = {k: [] for k in ("objects_added", "objects_removed", "objects_moved")}
        for oid, (verdict, _) in res.verdicts.items():
            if verdict in by:
                ev[by[verdict]].append(oid)
        affected = sorted({o for v in ev.values() for o in v})
        self.actions.append(({"create": {"_index": "room-events", "_id": f"{res.capture_id}:rejected"}}, {
            "@timestamp": ts(res.at + timedelta(seconds=1)), "event_type": "capture_rejected",
            "commit_sha": None, "parent_sha": None, "branch": self.git.branch(),
            "message": f"quality gate rejected {res.capture_id} ({'; '.join(why)}), retried as {retry}. "
                       f"Committed, it would have moved {len(ev['objects_moved'])} objects.",
            "author": ROBOT_NAME, "capture_id": res.capture_id, "outcome": "rejected: " + "; ".join(why),
            "objects_affected": affected, **{k: sorted(v) for k, v in ev.items()},
            "zone": sorted({res.paths[o].split("/")[1] for o in affected if o in res.paths}),
            **self.trace_ids(res.capture_id),
        }))

    def snapshot(self, res: Result, records: list[ObjectRecord], scene: Scene, at: datetime) -> None:
        """room-objects (full snapshot, every object), room-voxels, room-events: one commit."""
        base = {"@timestamp": ts(at), "commit_sha": res.sha, "parent_sha": res.parent, "branch": res.branch,
                **self.trace_ids(res.capture_id)}
        meta = self._last_capture["meta"]
        for r in records:
            m = meta.get(r.id, {})
            key = octree_key(r.pose.x, r.pose.y, r.pose.z) or ""
            self.actions.append(({"index": {"_index": "room-objects", "_id": f"{res.sha}:{r.id}"}}, {
                # No commit message here: it's commit-level text, and copied onto every object it
                # would make all of them match "mug moved". It lives on room-events, by commit_sha.
                **base, "author": ROBOT_NAME, "capture_id": res.capture_id,
                "object_id": r.id, "class": r.cls, "zone": r.zone,
                "pose": {"x": round(r.pose.x, 2), "y": round(r.pose.y, 2), "z": round(r.pose.z, 2),
                         "yaw": r.pose.yaw},
                "position": {"x": round(r.pose.x, 2), "y": round(r.pose.y, 2)},
                "extents": {"x": round(r.extents.x, 2), "y": round(r.extents.y, 2), "z": round(r.extents.z, 2)},
                "color": r.color, "first_seen": r.first_seen,
                "confidence": m.get("confidence"), "point_count": m.get("point_count", 0),
                "observed_by": m.get("observed_by", []), "raw_description": m.get("descriptions", []),
                "vlm_model": VLM_MODEL,
                "voxel_key": key, "voxel_key_l5": key[:5], "voxel_key_l3": key[:3],
            }))
        for v in voxels(records, scene, random.Random(f"{self.seed}:{res.sha}:voxels")):
            self.actions.append(({"index": {"_index": "room-voxels", "_id": f"{res.sha}:{v['voxel_key']}"}},
                                 {**base, **v}))
        by = {"A": "objects_added", "D": "objects_removed", "M": "objects_moved", "R": "objects_moved"}
        ev = {k: [] for k in by.values()}
        zones = set()
        for status, path in res.changes:
            parts = path.split("/")
            if parts[0] == "zones" and len(parts) == 3:
                ev[by[status]].append(parts[2].removesuffix(".yaml"))
                zones.add(parts[1])
        self.actions.append(({"create": {"_index": "room-events", "_id": f"{res.sha}:commit"}}, {
            **base, "event_type": "commit", "message": res.message, "author": ROBOT_NAME,
            "capture_id": res.capture_id, "outcome": "ok",
            "objects_affected": sorted({o for v in ev.values() for o in v}),
            **{k: sorted(v) for k, v in ev.items()}, "zone": sorted(zones),
        }))

    # -- the verbs ------------------------------------------------------------

    def _attempt(self, scene: Scene, t0: datetime, bump: bool) -> tuple[Result, list[ObjectRecord]]:
        """One shutter: telemetry around it, the capture, and the quality gate's verdict."""
        cap = f"cap_{self.state['captures'] + 1:04d}"
        with contextlib.ExitStack() as stack:
            if self.sentry:  # a real waterfall for this capture, visibly labelled synthetic
                stack.enter_context(obs.transaction("capture", f"{cap} (fake/scene_gen)"))
                stack.enter_context(obs.capture_scope(cap))
                stack.enter_context(obs.span("capture.fake", cap, synthetic=VLM_MODEL))
                _tag("synthetic", VLM_MODEL)
            return self._attempt_in_scope(scene, t0, bump, cap)

    def _attempt_in_scope(self, scene: Scene, t0: datetime, bump: bool, cap: str):
        self._trace = self.trace_ids(cap)
        tel = self.telemetry(t0, bump)
        lean = (tel["pitch"], 0.8 * tel["odom_residual"], -0.6 * tel["odom_residual"]) if bump else None
        res, records = self.capture(scene, t0, lean)
        rng = random.Random(f"{self.seed}:{res.capture_id}:gate")
        gate = {"skew_ms": self._last_capture["skew_ms"], "tilt_rate_max": tel["tilt_rate_max"],
                "coverage_pct": round(rng.uniform(0.74, 0.91), 3)}
        gate["quality_ok"] = capture_ok(**gate)
        if CLOUD_POSE:
            gate["pose"] = tel["pose"]
        if self.sentry:
            obs.capture_quality(gate["skew_ms"], gate["tilt_rate_max"], gate["coverage_pct"])
            if not gate["quality_ok"]:  # an ISSUE tagged capture_id: what web's [ask Seer] looks up
                obs.robot_failure("capture_rejected",
                                  f"tilt_rate_max {gate['tilt_rate_max']:.3f} rad/s > {GATE_TILT_RATE}",
                                  telemetry=tel["ring"], capture_id=res.capture_id, synthetic=VLM_MODEL)
        self._last_capture["gate"] = gate
        return res, records

    def _look(self, scene: Scene, at: datetime | None, watch: int, every: timedelta,
              bump: bool = False) -> Result:
        lead = every * watch + timedelta(seconds=TELEMETRY_PRE_S + TELEMETRY_S + (RETRY_S if bump else 0))
        t0 = self.next_time(at, lead)
        if watch and self.git.has_head():  # the room changing under the watch loop, then the scan
            start = t0 - every * watch
            self.watch(self.head_scene(scene), scene, start, t0, start + every * (watch // 2), every)
        if bump:  # the robot got knocked just before the shutter: the gate rejects, we retry
            bad, _ = self._attempt(scene, t0, bump=True)
            gate = self._last_capture["gate"]
            self.cloud(bad, scene, None)
            retry = f"cap_{self.state['captures'] + 1:04d}"
            self.rejection(bad, gate, retry)
            moved = sum(v == "moved" for v, _ in bad.verdicts.values())
            self.log(f"{bad.capture_id}  REJECTED by the quality gate: tilt_rate_max {gate['tilt_rate_max']:.3f} "
                     f"rad/s > {GATE_TILT_RATE} — it would have moved {moved} objects. Retrying.")
            t0 += timedelta(seconds=RETRY_S)
        res, records = self._attempt(scene, t0, bump=False)
        write_tree(self.repo, records)
        self.advance(t0 + timedelta(seconds=2))
        return res

    def scan(self, name: str | Scene, *, at: datetime | None = None, watch: int = 0,
             every: timedelta = timedelta(seconds=2), bump: bool = False) -> Result:
        """Look at the room and write what's there into the working tree. No commit."""
        scene = name if isinstance(name, Scene) else load_scene(name)
        self.ensure_repo(scene)
        res = self._look(scene, at, watch, every, bump)
        self.cloud(res, scene, None)
        self.stage(res, scene)
        return res

    def stage(self, res: Result, scene: Scene) -> None:
        """What a real scan stages for the commit (roomctl/publish.py): the per-object mess the
        YAML leaves out, the capture's trace, its catalog doc. `room commit` then publishes all
        of it through the same hook the real pipeline uses."""
        from roomctl.publish import stage_scan
        meta = {oid: {"confidence": m.get("confidence"), "point_count": m.get("point_count", 0),
                      "observed_by": m.get("observed_by", []), "raw_description": m.get("descriptions", []),
                      "vlm_model": VLM_MODEL}   # provenance: these words are scripted, not seen
                for oid, m in self._last_capture["meta"].items()}
        doc = self.cloud_doc(res, scene, None)
        trace = {k: doc.pop(k) for k in list(doc) if k.startswith("sentry_")}
        stage_scan(self.git, res.capture_id, ts(res.at), meta, trace=trace, cloud=doc)

    def commit(self, name: str | Scene, message: str | None = None, *, branch: str | None = None,
               at: datetime | None = None, watch: int = 0,
               every: timedelta = timedelta(seconds=2), bump: bool = False) -> Result:
        """scan, then `git add -A && git commit` as the robot, dated at capture time + pipeline."""
        scene = name if isinstance(name, Scene) else load_scene(name)
        self.ensure_repo(scene)
        if branch:
            self.switch(branch)
        res = self._look(scene, at, watch, every, bump)
        res.message = message or scene.message or f"scan: {scene.name}"
        when = res.at + timedelta(seconds=PIPELINE_S)
        c = self.git.commit(res.message, when)
        res.branch = self.git.branch()
        if c is None:
            self.cloud(res, scene, None)
            return res
        res.sha, res.parent, res.changes = c.sha, c.parent, c.changes
        self.cloud(res, scene, res.sha)
        committed = list(self.git.records().values())
        for r in committed:
            self.state["seen"].setdefault(r.id, {})["committed"] = True
        self.snapshot(res, committed, scene, when)
        self.advance(when)
        return res

    def head_scene(self, like: Scene) -> Scene:
        """HEAD as a scene: committed poses, with metadata borrowed from the scene catalog."""
        if self._catalog is None:
            self._catalog = catalog()
        objs = {}
        for oid, r in self.git.records().items():
            base = like.objects.get(oid) or self._catalog.get(oid) or Truth(
                oid, r.cls, r.zone, 0, 0, 0, 0, 0, 0, 0, r.color, r.cls, 0.8, (r.cls,) * 3)
            objs[oid] = replace(base, zone=r.zone, x=r.pose.x, y=r.pose.y, z=r.pose.z, yaw=r.pose.yaw,
                                ex=r.extents.x, ey=r.extents.y, ez=r.extents.z)
        return Scene(f"HEAD:{like.name}", like.room, like.cameras, objs)

    def demo(self, start: datetime | None = None, every: timedelta = timedelta(seconds=30),
             on_commit=lambda res: None) -> list[Result]:
        """The whole story, ending now: a hammer that leaves, a movie-night branch, a judge's
        mess on main (so `git merge movie-night` conflicts on the mug) whose first capture is
        knocked and REJECTED by the quality gate, a laptop that hides the glasses case for ten
        minutes. Leaves main checked out and clean."""
        if self.git.exists and self.git.has_head():
            raise SystemExit(f"{self.repo} already has history; use --reset for --demo")
        s = {n: load_scene(n) for n in ("bench_with_hammer", "clean_bench", "movie_night",
                                        "messy_bench", "occluded_bench")}
        # The laptop from occluded_bench, opened on top of the messy desk rather than the clean one.
        occ = s["occluded_bench"]
        laptop = replace(s["messy_bench"], name="messy_bench+laptop", occlude=occ.occlude,
                         objects={**s["messy_bench"].objects, "laptop_8e4b": occ.objects["laptop_8e4b"]})
        t = (start or datetime.now(timezone.utc) - timedelta(minutes=125)).replace(microsecond=0)
        m = lambda k: t + timedelta(minutes=k)  # noqa: E731
        out = [self.commit(s["bench_with_hammer"], at=m(0))]
        on_commit(out[-1])
        self.watch(s["bench_with_hammer"], s["clean_bench"], m(0.5), m(30), m(22), every)
        out.append(self.commit(s["clean_bench"], at=m(30)))
        on_commit(out[-1])
        self.watch(s["clean_bench"], s["movie_night"], m(30.5), m(60), m(52), every)
        out.append(self.commit(s["movie_night"], branch="movie-night", at=m(60)))
        on_commit(out[-1])
        self.switch("main")
        self.watch(s["clean_bench"], s["messy_bench"], m(60.5), m(95), m(88), every)
        out.append(self.commit(s["messy_bench"], at=m(95), bump=True))  # first shutter fails the gate
        on_commit(out[-1])
        self.watch(s["messy_bench"], laptop, m(95.5), m(110), m(103), every)
        self.watch(laptop, s["messy_bench"], m(110), m(122), m(113), every)
        return out

    # -- output ---------------------------------------------------------------

    def flush(self, label: str, out_dir: Path | None, es_mode: str) -> int:
        """Write the run's _bulk file and (maybe) index it. Returns a process exit code."""
        self.save_state()
        acts = self.actions
        counts: dict[str, int] = {}
        for a, _ in acts:
            idx = next(iter(a.values()))["_index"]
            counts[idx] = counts.get(idx, 0) + 1
        self.log("es docs   " + (" · ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "none"))
        if out_dir is not None and acts:
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{label}.ndjson"
            path.write_text("".join(json.dumps(a) + "\n" + json.dumps(s) + "\n" for a, s in acts))
            self.log(f"          -> {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
        self.actions = []
        if es_mode != "off":
            from roomctl.publish import is_the_room, not_the_room
            if not is_the_room(self.git):
                # a scratch copy's captures reuse the real room's ids (cap_0015 …): writing them
                # would overwrite the demo's history (docs/10 D37, D45)
                self.log(f"          es: {not_the_room(self.git)}")
                return 1 if es_mode == "on" else 0
        return index_actions(acts, es_mode, self.log)


def moved(a: Truth | None, b: Truth | None) -> bool:
    """Did a human touch it? HEAD poses are quantized, so compare with a tolerance."""
    if a is None or b is None:
        return a is not b
    return a.zone != b.zone or math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) > 0.03 \
        or abs(yaw_diff(a.yaw, b.yaw)) > 10


def object_points(r, spacing: float = 0.015):
    """The surface points of one committed object's box — what a stereo rig would return."""
    return scene_cloud({"zones": {}}, [r], spacing)


def scene_cloud(room: dict, records, spacing: float = 0.015):
    """A synthetic fused point cloud of the room, for perception's voxelize -> costmap path:
    the surfaces a stereo rig would see of each zone's top and its pedestal (inset by the
    zone's `overhang`, default 10 cm), and of every object's box. Returns an (N,3) array."""
    import numpy as np

    def box(lo, hi):
        axes = [np.arange(lo[a], hi[a] + 1e-9, spacing) if hi[a] - lo[a] > spacing else np.array([lo[a], hi[a]])
                for a in range(3)]
        faces = []
        for a in range(3):
            for v in (lo[a], hi[a]):
                g = np.meshgrid(*[axes[b] if b != a else np.array([v]) for b in range(3)], indexing="ij")
                faces.append(np.stack([m.ravel() for m in g], axis=1))
        return np.concatenate(faces)

    parts = []
    for z in room.get("zones", {}).values():
        (x0, y0, _), (x1, y1, _) = z["min"], z["max"]
        s, o = z["surface"], z.get("overhang", 0.10)
        parts.append(box((x0, y0, s - 0.02), (x1, y1, s)))
        if x1 - x0 > 2 * o and y1 - y0 > 2 * o:
            parts.append(box((x0 + o, y0 + o, 0.02), (x1 - o, y1 - o, s - 0.02)))
    for r in records:
        c, sn = abs(math.cos(math.radians(r.pose.yaw))), abs(math.sin(math.radians(r.pose.yaw)))
        hx, hy = (r.extents.x * c + r.extents.y * sn) / 2, (r.extents.x * sn + r.extents.y * c) / 2
        parts.append(box((r.pose.x - hx, r.pose.y - hy, r.pose.z - r.extents.z / 2),
                         (r.pose.x + hx, r.pose.y + hy, r.pose.z + r.extents.z / 2)))
    return np.concatenate(parts) if parts else np.zeros((0, 3))


def nearest(head: dict[str, ObjectRecord], raw: list[float]) -> str | None:
    best, best_d = None, WATCH_GATE_M
    for oid, r in head.items():
        d = math.dist(raw, (r.pose.x, r.pose.y, r.pose.z))
        if d < best_d:
            best, best_d = oid, d
    return best


def voxels(records: list[ObjectRecord], scene: Scene, rng: random.Random) -> list[dict]:
    """Occupied cells of the committed scene: support surfaces plus every object's box.
    One doc per cell; the object with the most points in a cell claims it. Cells on an
    object's boundary flicker between commits — voxel diff is noisy by construction."""
    cells: dict[str, dict] = {}

    def fill(lo, hi, zone, oid, density, flicker):
        idx = [range(math.floor((a - o) / CELL), math.floor((b - o - 1e-9) / CELL) + 1)
               for a, b, o in zip(lo, hi, ORIGIN)]
        for i in idx[0]:
            for j in idx[1]:
                for k in idx[2]:
                    c = [o + (n + 0.5) * CELL for n, o in zip((i, j, k), ORIGIN)]
                    key = octree_key(*c)
                    if key is None:
                        continue
                    edge = i in (idx[0][0], idx[0][-1]) or j in (idx[1][0], idx[1][-1])
                    if flicker and edge and rng.random() < 0.12:
                        continue
                    z0 = max(lo[2], ORIGIN[2] + k * CELL)
                    z1 = min(hi[2], ORIGIN[2] + (k + 1) * CELL)
                    n = max(1, int(density * (0.7 + 0.6 * rng.random())))
                    v = cells.setdefault(key, {"voxel_key": key, "voxel_key_l5": key[:5],
                                               "voxel_key_l3": key[:3],
                                               "cell": {"x": round(c[0], 4), "y": round(c[1], 4)},
                                               "z_min": z0, "z_max": z1, "density": 0, "zone": zone,
                                               "object_id": None, "_claims": {}})
                    v["z_min"], v["z_max"] = min(v["z_min"], z0), max(v["z_max"], z1)
                    v["density"] += n
                    if oid:
                        v["_claims"][oid] = v["_claims"].get(oid, 0) + n

    # The floor. bbsim already has one -- rebuild_truth() fills floor_bounds(), the furniture
    # extents seeded with +-0.6 m and grown by --floor-margin -- but neither scene_cloud() nor
    # this function ever emitted it, so room-voxels held two slabs floating in 8 m of nothing:
    # 6 occupied cells at 1 m, 0.3% of the cube. Same bounds rule, so the cubes agree with the
    # grid the robot drives on rather than being a second invented room.
    xs = [-0.6, 0.6] + [z[e][0] for z in scene.room["zones"].values() for e in ("min", "max")]
    ys = [-0.6, 0.6] + [z[e][1] for z in scene.room["zones"].values() for e in ("min", "max")]
    # 2 cm thick, like every zone surface -- NOT one cell thick. from_docs takes z_mid as
    # (z_min + z_max) / 2, and costmap.py's body band is z_mid > Z_FLOOR (0.02): a cell-thick
    # floor reads back as z_mid 0.031, so every floor cell becomes an obstacle and the costmap
    # walls off the room it is supposed to drive across. At 2 cm, z_mid is 0.01 and it stays free.
    fill((min(xs) - FLOOR_MARGIN, min(ys) - FLOOR_MARGIN, 0.0),
         (max(xs) + FLOOR_MARGIN, max(ys) + FLOOR_MARGIN, FLOOR_THICK), "floor", None, 30, False)
    for zname, z in scene.room["zones"].items():
        fill((z["min"][0], z["min"][1], z["surface"] - 0.02), (z["max"][0], z["max"][1], z["surface"]),
             zname, None, 60, False)
        # the pedestal's walls, inset by the overhang: what blocks the BASE (docs/24 A1). Without
        # them a costmap rebuilt from these docs has no obstacles at all.
        (x0, y0, _), (x1, y1, _) = z["min"], z["max"]
        o = z.get("overhang", 0.10)
        if x1 - x0 > 2 * o and y1 - y0 > 2 * o:
            lo_z, hi_z = 0.02, z["surface"] - 0.02
            for lo, hi in (((x0 + o, y0 + o), (x1 - o, y0 + o + CELL)), ((x0 + o, y1 - o - CELL), (x1 - o, y1 - o)),
                           ((x0 + o, y0 + o), (x0 + o + CELL, y1 - o)), ((x1 - o - CELL, y0 + o), (x1 - o, y1 - o))):
                fill((lo[0], lo[1], lo_z), (hi[0], hi[1], hi_z), zname, None, 40, False)
    for r in records:
        c, s = abs(math.cos(math.radians(r.pose.yaw))), abs(math.sin(math.radians(r.pose.yaw)))
        hx = (r.extents.x * c + r.extents.y * s) / 2
        hy = (r.extents.x * s + r.extents.y * c) / 2
        hz = r.extents.z / 2
        fill((r.pose.x - hx, r.pose.y - hy, r.pose.z - hz), (r.pose.x + hx, r.pose.y + hy, r.pose.z + hz),
             r.zone, r.id, 140, True)
    out = []
    for v in sorted(cells.values(), key=lambda v: v["voxel_key"]):
        claims = v.pop("_claims")
        if claims:
            v["object_id"] = max(sorted(claims), key=claims.get)
        v["z_min"], v["z_max"] = round(v["z_min"], 4), round(v["z_max"], 4)
        out.append(v)
    return out


# ── Elasticsearch: plain _bulk over urllib, so the fake needs no client library ──

def es_target() -> tuple[str, str] | str:
    url, key = os.getenv("ELASTIC_URL", "").rstrip("/"), os.getenv("ELASTIC_API_KEY", "")
    if not url or not key:
        return "ELASTIC_URL / ELASTIC_API_KEY not set"
    if key.startswith(("http://", "https://")):
        return "ELASTIC_API_KEY is a URL, not an API key (see elastic/NOTES.md)"
    if "xxxxx" in url:
        return "ELASTIC_URL is still the .env.example placeholder"
    return url, key


def es_call(url: str, key: str, method: str, path: str, body: str | None = None, timeout: float = 120):
    req = urllib.request.Request(f"{url}{path}", method=method, data=body.encode() if body else None,
                                 headers={"Authorization": f"ApiKey {key}",
                                          "Content-Type": "application/x-ndjson" if body else "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e).get("error", {})
            root = (err.get("root_cause") or [err])[0] if isinstance(err, dict) else {"reason": err}
            msg = f"{root.get('type', '')}: {root.get('reason', '')}".strip(": ")
        except ValueError:
            msg = ""
        raise RuntimeError(f"HTTP {e.code} {method} {path.split('?')[0]} {msg}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"cannot reach Elasticsearch: {getattr(e, 'reason', e)}") from None


def index_actions(acts: list[tuple[dict, dict]], mode: str, log=print) -> int:
    if mode == "off" or not acts:
        return 0
    target = es_target()
    if isinstance(target, str):
        log(f"          not indexed: {target}")
        return 1 if mode == "on" else 0
    url, key = target
    try:
        # Rule zero (docs/13): never let a write create an index. Dynamic mapping would turn
        # `position` into two floats and a data stream into a plain index.
        r = es_call(url, key, "GET", "/_resolve/index/" + ",".join(SNAPSHOT_INDICES + DATA_STREAMS)
                    + "?expand_wildcards=all&ignore_unavailable=true")
        have = {i["name"] for i in r.get("indices", []) + r.get("aliases", [])}
        streams = {d["name"] for d in r.get("data_streams", [])}
        missing = [n for n in SNAPSHOT_INDICES if n not in have] + \
                  [n for n in DATA_STREAMS if n not in streams]
        if missing:
            log(f"          not indexed: {', '.join(missing)} missing — run elastic/setup_elastic.py first")
            return 1 if mode == "on" else 0
        tally: dict[str, list] = {}
        for i in range(0, len(acts), 500):
            chunk = acts[i:i + 500]
            body = "".join(json.dumps(a) + "\n" + json.dumps(s) + "\n" for a, s in chunk)
            last = i + 500 >= len(acts)  # docs/13 gotcha 1: searchable when we return, but refresh once
            resp = es_call(url, key, "POST", "/_bulk" + ("?refresh=wait_for" if last else ""), body)
            for (a, _), item in zip(chunk, resp["items"]):
                name = next(iter(a.values()))["_index"]
                res = next(iter(item.values()))
                t = tally.setdefault(name, [0, 0, []])
                if res.get("status", 500) < 300:
                    t[0] += 1
                elif res.get("status") == 409:
                    t[1] += 1  # already there: a re-sent run is idempotent
                else:
                    err = res.get("error", {})
                    t[2].append(f"{err.get('type')}: {err.get('reason')}" if isinstance(err, dict) else str(err))
    except RuntimeError as e:
        log(f"          not indexed: {e}")
        return 1 if mode == "on" else 0
    failed = 0
    for name, (ok, dup, errs) in sorted(tally.items()):
        failed += len(errs)
        log(f"          {name:<18} {ok} indexed" + (f", {dup} already there" if dup else "")
            + (f", {len(errs)} FAILED: {errs[0]}" if errs else ""))
    return 1 if failed else 0


def read_bulk(path: Path) -> list[tuple[dict, dict]]:
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return list(zip(lines[0::2], lines[1::2]))


# ── CLI ──────────────────────────────────────────────────────────────────────

def report(room: FakeRoom, res: Result, committed: bool) -> None:
    order = {"moved": 0, "removed": 1, "added": 2, "returned": 3, "unobserved": 4}
    shown = sorted(((v, oid) for oid, v in res.verdicts.items() if v[0] != "unchanged"),
                   key=lambda x: (order[x[0][0]], res.paths[x[1]]))
    unchanged = sum(1 for v in res.verdicts.values() if v[0] == "unchanged")
    room.log(f"{res.capture_id}  {res.at.strftime('%Y-%m-%d %H:%M:%S')}Z  "
             f"{unchanged} unchanged" + ("" if shown else ", nothing moved"))
    for (verdict, detail), oid in shown:
        room.log(f"  {verdict:<11}{res.paths[oid]}" + (f"   ({detail})" if detail else ""))
    if committed and res.sha:
        room.log(f"commit    {res.sha[:7]}  [{res.branch}] {res.message}")
    elif committed:
        room.log("commit    nothing to commit, working tree clean")
    else:
        status = room.git.git("status", "--short", "-uall").stdout.rstrip()
        room.log("status    " + (status.replace("\n", "\n          ") if status else "working tree clean"))


def parse_at(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    verb = ap.add_mutually_exclusive_group(required=True)
    verb.add_argument("--commit", metavar="SCENE", help="scan SCENE and commit it")
    verb.add_argument("--scan", metavar="SCENE", help="scan SCENE into the working tree, no commit")
    verb.add_argument("--demo", action="store_true", help="build the whole demo history (needs an empty repo)")
    verb.add_argument("--index", metavar="FILE", type=Path, help="send a saved .ndjson run to Elasticsearch")
    verb.add_argument("--list", action="store_true", help="list scenes")
    ap.add_argument("-m", "--message", help="commit message (default: the scene's own)")
    ap.add_argument("--branch", help="commit on this branch, creating it from HEAD if needed")
    ap.add_argument("--repo", type=Path, help="room repository (default: $ROOM_GIT_PATH, ./room.git)")
    ap.add_argument("--reset", action="store_true", help="delete the room repo first (only one scene_gen made)")
    ap.add_argument("--watch", type=int, default=20, metavar="N",
                    help="watch-loop captures to emit before a scan, 2 s apart (default 20; 0 = none)")
    ap.add_argument("--at", type=parse_at, help="capture time, ISO-8601 (default: now)")
    ap.add_argument("--sentry", action="store_true",
                    help="run each commit-path capture as a REAL Sentry transaction (tagged synthetic), "
                         "and a gate rejection as a Sentry issue. Sends to Sentry: needs SENTRY_DSN")
    ap.add_argument("--bump", action="store_true",
                    help="knock the robot just before the shutter: the quality gate rejects, then a retry")
    ap.add_argument("--seed", default="gitspace", help="noise seed (default: gitspace)")
    ap.add_argument("--es", choices=("auto", "on", "off"), default="auto",
                    help="index into Elasticsearch: auto = if configured, on = fail if not, off = files only")
    ap.add_argument("--out", type=Path, default=OUT, help="where the .ndjson goes (default fake/out/)")
    args = ap.parse_args(argv)

    if args.index:
        acts = read_bulk(args.index)
        print(f"es docs   {len(acts)} from {args.index}")
        return index_actions(acts, "on")

    if args.sentry and not obs.init("laptop"):
        print("error: --sentry needs a usable SENTRY_DSN in .env — nothing was sent", file=sys.stderr)
        return 2
    repo = args.repo or Path(os.getenv("ROOM_GIT_PATH", "./room.git"))
    room = FakeRoom(repo if repo.is_absolute() else ROOT / repo, seed=args.seed, sentry=args.sentry)
    try:
        if args.list:
            for p in sorted(SCENES.glob("*.yaml")):
                s = load_scene(p.stem)
                print(f"{p.stem:<18} {len(s.objects):>2} objects  {s.message or ''}")
            return 0
        if args.reset:
            room.reset()
        if args.demo:
            room.demo(start=args.at, on_commit=lambda res: report(room, res, committed=True))
            label = "demo"
        elif args.commit:
            res = room.commit(args.commit, args.message, branch=args.branch, at=args.at, watch=args.watch,
                              bump=args.bump)
            report(room, res, committed=True)
            label = res.capture_id
        else:
            res = room.scan(args.scan, at=args.at, watch=args.watch, bump=args.bump)
            report(room, res, committed=False)
            label = res.capture_id
    except (SceneError, GitError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    code = room.flush(label, args.out, args.es)
    if args.sentry:
        obs.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
