"""A small, fully known room for the live query tests.

Four commits, six objects, three cameras. Every number the tests assert on is derived from the
documents built here, so the expectations can't drift from the data.

    C1  -3h  main         initial scan
    C2  -2h  main         mug moved desk -> couch
    C3  -1h  main         hammer (tool_4f2a) gone, keys moved shelf -> desk   [traced in Sentry]
    C4  -30m movie-night  mug moved again (events only: a branch the snapshots don't follow)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

ROOM_ORIGIN, ROOM_SIZE, LEVELS = (-4.0, -4.0, 0.0), 8.0, 7  # .env scene constants
TRACE = "5eb1d2c3a4f5061728394a5b6c7d8e9f"
TRACE_URL = f"https://gitspace.sentry.io/performance/trace/{TRACE}/"


def octree_key(x: float, y: float, z: float) -> str:
    """docs/11's encoder: one octant digit per level, (bx<<2)|(by<<1)|bz."""
    f = [(v - o) / ROOM_SIZE for v, o in zip((x, y, z), ROOM_ORIGIN)]
    digits = []
    for _ in range(LEVELS):
        f = [c * 2 for c in f]
        bits = [int(c) for c in f]
        f = [c - b for c, b in zip(f, bits)]
        digits.append(str((bits[0] << 2) | (bits[1] << 1) | bits[2]))
    return "".join(digits)


def ms(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


OBJECTS = {  # id: class, zone-by-commit, position-by-commit, three disagreeing VLM descriptions
    "mug_a1b2": ("mug", ["a blue ceramic mug, handle facing left", "navy coffee mug",
                         "cup with handle, chipped rim"]),
    "cup_7e21": ("cup", ["white ceramic cup, no handle", "off-white porcelain cup, empty",
                         "small ceramic cup, cream coloured"]),
    "lamp_9c01": ("lamp", ["brass desk lamp with a green shade", "a lamp, switched off",
                           "tall metal lamp"]),
    "book_e5f6": ("book", ["green hardcover book standing upright", "a closed book",
                           "thick paperback, green spine"]),
    "keys_7c2e": ("keys", ["a ring of house keys", "small metal keys on a keyring",
                           "keys with a red tag"]),
    "tool_4f2a": ("tool", ["a claw hammer with a wooden handle", "mallet",
                           "tool with a wooden handle"]),
}
DESK, SHELF, COUCH, BENCH = "desk", "shelf", "couch", "workbench"
AT_DESK_MUG, AT_COUCH = (0.42, 0.18, 0.76), (-1.20, 1.10, 0.45)
AT_SHELF_KEYS, AT_DESK_KEYS = (1.55, 0.85, 1.10), (0.55, -0.40, 0.75)
FIXED = {"cup_7e21": (DESK, (0.30, -0.22, 0.74)), "lamp_9c01": (DESK, (0.80, 0.10, 0.95)),
         "book_e5f6": (SHELF, (1.40, 0.95, 1.10)), "tool_4f2a": (BENCH, (2.20, -1.10, 0.90))}
EMPTY_DESK_SPOT = (0.10, 0.40)  # desk surface voxels only


class World:
    def __init__(self, now: datetime | None = None):
        self.now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
        n = self.now
        self.c1, self.c2, self.c3, self.c4 = ("c1" * 20, "c2" * 20, "c3" * 20, "c4" * 20)
        self.t = {self.c1: n - timedelta(hours=3), self.c2: n - timedelta(hours=2),
                  self.c3: n - timedelta(hours=1), self.c4: n - timedelta(minutes=30)}
        self.capture = {self.c1: "cap_0001", self.c2: "cap_0002", self.c3: "cap_0003"}
        self.docs: dict[str, list[dict]] = {k: [] for k in (
            "room-objects", "room-voxels", "room-clouds", "room-observations", "robot-telemetry",
            "room-events")}
        self._build()

    # where each object is at each main-branch commit (absent = not in that snapshot)
    def placement(self, sha: str) -> dict[str, tuple[str, tuple]]:
        p = dict(FIXED)
        p["mug_a1b2"] = (DESK, AT_DESK_MUG) if sha == self.c1 else (COUCH, AT_COUCH)
        p["keys_7c2e"] = (DESK, AT_DESK_KEYS) if sha == self.c3 else (SHELF, AT_SHELF_KEYS)
        if sha == self.c3:
            del p["tool_4f2a"]
        return p

    def traced(self, sha: str) -> dict:
        if sha != self.c3:
            return {}
        return {"sentry_trace_id": TRACE, "sentry_span_id": "a9b4702ab42c5901", "sentry_url": TRACE_URL}

    def cells(self, x: float, y: float, z: float) -> dict[str, tuple]:
        """A 2x2 footprint of 6.25 cm voxels around a point, keyed by voxel_key."""
        pts = [(x + dx, y + dy, z) for dx in (-0.03, 0.03) for dy in (-0.03, 0.03)]
        return {octree_key(*p): p for p in pts}

    def _build(self) -> None:
        main = [self.c1, self.c2, self.c3]
        for i, sha in enumerate(main):
            ts, cap, parent = self.t[sha], self.capture[sha], main[i - 1] if i else None
            here = self.placement(sha)
            for oid, (zone, (x, y, z)) in here.items():
                cls, desc = OBJECTS[oid]
                key = octree_key(x, y, z)
                self.docs["room-objects"].append({
                    "@timestamp": ms(ts), "commit_sha": sha, "parent_sha": parent, "branch": "main",
                    "author": "gitspace-robot", "capture_id": cap, "object_id": oid, "class": cls,
                    "zone": zone, "pose": {"x": x, "y": y, "z": z, "yaw": 90},
                    "position": {"x": x, "y": y}, "extents": {"x": 0.08, "y": 0.08, "z": 0.1},
                    "color": "#808080", "first_seen": ms(self.t[self.c1]), "confidence": 0.9,
                    "point_count": 3000, "observed_by": ["cam0", "cam1", "cam2"],
                    "raw_description": desc, "voxel_key": key, "voxel_key_l5": key[:5],
                    "voxel_key_l3": key[:3], **self.traced(sha)})
                for vk, (vx, vy, vz) in self.cells(x, y, z).items():
                    self.docs["room-voxels"].append(self.voxel(sha, parent, ts, vk, vx, vy, vz, zone, oid))
            for vk, (vx, vy, vz) in self.cells(*EMPTY_DESK_SPOT, 0.72).items():
                self.docs["room-voxels"].append(self.voxel(sha, parent, ts, vk, vx, vy, vz, DESK, None))
            self._observations(sha, cap, ts - timedelta(seconds=9), here)
            self.docs["room-clouds"].append({
                "@timestamp": ms(ts - timedelta(seconds=9)), "capture_id": cap, "commit_sha": sha,
                "cloud_uri": None, "point_count": 1_800_000, "cameras": ["cam0", "cam1", "cam2"],
                "bounds": {"min": {"x": -2, "y": -2, "z": 0}, "max": {"x": 3, "y": 2, "z": 1.5}},
                "coverage_pct": 0.84, "icp_residual_mm": 4.1, "skew_ms": 3.1,
                "tilt_rate_max": 0.2395 if sha == self.c3 else 0.01, "quality_ok": sha != self.c3,
                **self.traced(sha)})

        self.events = [
            (self.c1, None, "main", "initial scan: the bench as found", [DESK, SHELF, BENCH], []),
            (self.c2, self.c1, "main", "afternoon: mug moved to the couch", [DESK, COUCH], ["mug_a1b2"]),
            (self.c3, self.c2, "main", "evening: hammer gone, keys moved", [SHELF, DESK, BENCH], ["keys_7c2e"]),
            (self.c4, self.c2, "movie-night", "movie night: mug by the couch again", [COUCH], ["mug_a1b2"]),
        ]
        for sha, parent, branch, msg, zones, moved in self.events:
            self.docs["room-events"].append({
                "@timestamp": ms(self.t[sha]), "event_type": "commit", "commit_sha": sha,
                "parent_sha": parent, "branch": branch, "message": msg, "author": "gitspace-robot",
                "capture_id": self.capture.get(sha), "outcome": "ok", "zone": zones,
                "objects_moved": moved, "objects_added": [],
                "objects_removed": ["tool_4f2a"] if sha == self.c3 else [],
                "objects_affected": moved + (["tool_4f2a"] if sha == self.c3 else []),
                **self.traced(sha)})
        # an action after C3: commit_at must only ever answer with commits
        self.docs["room-events"].append({
            "@timestamp": ms(self.t[self.c3] + timedelta(minutes=5)), "event_type": "pick",
            "commit_sha": self.c3, "branch": "main", "message": "pick mug_a1b2", "outcome": "ok",
            "objects_affected": ["mug_a1b2"], "zone": [COUCH]})

        # 3 s of 50 Hz telemetry up to the C3 shutter, with the tilt spike 200 ms before it
        self.shutter = self.t[self.c3] - timedelta(seconds=9)
        for k in range(150):
            ts = ms(self.shutter - timedelta(milliseconds=20 * k))
            self.docs["robot-telemetry"] += [
                {"@timestamp": ts, "signal": "tilt_rate", "value": 0.2395 if k == 10 else 0.004},
                {"@timestamp": ts, "signal": "pitch", "value": -0.08 if k == 12 else 0.02},
                {"@timestamp": ts, "signal": "odom_residual", "value": 0.004},
            ]

    def voxel(self, sha, parent, ts, key, x, y, z, zone, oid) -> dict:
        return {"@timestamp": ms(ts), "commit_sha": sha, "parent_sha": parent, "branch": "main",
                "voxel_key": key, "voxel_key_l5": key[:5], "voxel_key_l3": key[:3],
                "cell": {"x": round(x, 4), "y": round(y, 4)}, "z_min": z - 0.03, "z_max": z + 0.03,
                "density": 40, "zone": zone, "object_id": oid}

    def _observations(self, sha: str, cap: str, t0: datetime, here: dict) -> None:
        """Three cameras per object, disagreeing on x; keys occluded from cam1 at C3; two
        rejected clusters per capture. Every doc gets its own millisecond (TSDS identity)."""
        tick = 0
        for oid, (_, (x, y, z)) in here.items():
            for c, cam in enumerate(("cam0", "cam1", "cam2")):
                tick += 1
                occluded = oid == "keys_7c2e" and sha == self.c3 and cam == "cam1"
                self.docs["room-observations"].append({
                    "@timestamp": ms(t0 + timedelta(milliseconds=tick)), "capture_id": cap,
                    "object_id": oid, "camera": cam, "confidence": 0.9 - 0.2 * c,
                    "point_count": 1400 - 300 * c, "raw_x": round(x + 0.025 * c, 4), "raw_y": y,
                    "raw_z": z, "occluded": occluded, "rejected_reason": None,
                    "raw_description": OBJECTS[oid][1][c], "raw_label": OBJECTS[oid][0],
                    "vlm_model": "tests/world", "label_attempt": 1, **self.traced(sha)})
        for reason, cam in (("too_small", "cam0"), ("roomignore:person", "cam1")):
            tick += 1
            self.docs["room-observations"].append({
                "@timestamp": ms(t0 + timedelta(milliseconds=tick)), "capture_id": cap,
                "object_id": None, "camera": cam, "confidence": 0.2, "point_count": 40,
                "raw_x": 0.0, "raw_y": 0.0, "raw_z": 0.0, "occluded": False,
                "rejected_reason": reason, "vlm_model": "tests/world", **self.traced(sha)})

    # ── expectations, derived from the docs above ────────────────────────────

    def voxel_keys(self, sha: str, field: str) -> set[str]:
        return {v[field] for v in self.docs["room-voxels"] if v["commit_sha"] == sha}

    def count(self, index: str, **match) -> int:
        return sum(all(d.get(k) == v for k, v in match.items()) for d in self.docs[index])
