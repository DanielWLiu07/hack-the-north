#!/usr/bin/env python3
"""bbsim: a fake of Bracket Bot's nav server, on loopback, driven by fake/scene_gen scenes.

    python fake/bbsim.py --scene clean_bench [--ws-port 18010 --api-port 18020] [--speed 0.14]
                         [--fail-nav 0.0] [--seed 0] [--fast] [--T 30,1.2,-0.8,0]
    BB_HOST=127.0.0.1:18010 ROOM_NAV=bb ROOM_SOURCE=bb room watch

It speaks what bbapps/nav speaks (plan/roommate/06, PLAN.md §3), so every track tests the robot
loop with no hardware:

    ws   :ws-port/heavy     the colour voxel map. 8-byte header <II type, count> + zlib payload.
                            type 4 full-copy chunks of <= 40,000 cells, first=1 on the first;
                            type 5 changes (adds, removes, rgb); res 0.015; a per-client queue of 8,
                            and a reader that falls behind gets a fresh full copy.
    ws   :ws-port/ws        {"t": "params"} once, then {"t": "state"} at 8 Hz.
    http :api-port          GET /health /pose /map · ws /stream · POST /map/rectangle /navigate
                            /patrol /stop · one job at a time, 202 + the job's state.
    http :api-port/sim/*    test controls; the real robot has none of these.

The scene lives in the ROOM frame; everything served is in a BB WORLD frame that differs from it by
a configurable SE2 (--T), so a client that skips registration is wrong by a visible amount. Every
room <-> BB conversion goes through roomctl/frames.py.

What the sim decides for itself, where the robot's documentation is silent, is marked SIM below.
Where a recorded real-robot fixture differs from this file, the robot wins and this file is fixed.

Loopback only, by construction: the listeners bind 127.0.0.1 and there is no option to change it.
Stdlib + websockets + numpy (+ pyyaml through scene_gen).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import heapq
import json
import math
import random
import struct
import sys
import time
import zlib
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fake import scene_gen  # noqa: E402
from roomctl import frames  # noqa: E402
from roomctl.frames import SE2  # noqa: E402

HOST = "127.0.0.1"

# ── the wire (bbapps/nav main.py) ───────────────────────────────────────────────────────
RES = 0.015            # metres per cell; world metres = integer cell * res
KF_CHUNK = 40_000      # cells per full-copy chunk
QMAX = 8               # messages queued per /heavy connection
VOX_INTERVAL = 0.5     # seconds between change packets
STATE_HZ = 8.0
Z_MAX = 1.5            # the robot filters the ceiling out

# ── the nav API (nav_api.py) ────────────────────────────────────────────────────────────
GRID_RES = 0.03
ARRIVE_M = 0.25
SEE_CONE = math.radians(120.0)
TURN_RATE = 0.9        # SIM rad/s, turning in place
ROBOT_RADIUS = 0.22    # SIM planning inflation
EYE_Z = 1.0            # SIM camera height, for what the depth camera can see
PLAN_RES = 0.05        # SIM planning grid
SLAM_READY_S = 1.0     # SIM /pose is 503 for this long after start (and after a map reset)


def pack_keyframe(cells: np.ndarray, cols: np.ndarray) -> list[bytes]:
    """Type 4. An empty map is ONE chunk with count 0 and first=1: "clear"."""
    n = len(cells)
    if n == 0:
        return [struct.pack("<II", 4, 0) + zlib.compress(struct.pack("<iiifI", 0, 0, 0, RES, 1), 1)]
    out = []
    for ci, s in enumerate(range(0, n, KF_CHUNK)):
        c, k = cells[s:s + KF_CHUNK], cols[s:s + KF_CHUNK]
        base = c.min(axis=0)
        payload = (struct.pack("<iiifI", int(base[0]), int(base[1]), int(base[2]), RES, 1 if ci == 0 else 0)
                   + (c - base).astype(np.uint16).tobytes() + k.astype(np.uint8).tobytes())
        out.append(struct.pack("<II", 4, len(c)) + zlib.compress(payload, 1))
    return out


def pack_delta(up_cells: np.ndarray, up_cols: np.ndarray, rm_cells: np.ndarray) -> bytes | None:
    """Type 5: adds/recolours, then removes, then the rgb of the adds."""
    nu, nr = len(up_cells), len(rm_cells)
    if nu == 0 and nr == 0:
        return None
    base = np.vstack([a for a in (up_cells, rm_cells) if len(a)]).min(axis=0)
    payload = (struct.pack("<iiifII", int(base[0]), int(base[1]), int(base[2]), RES, nu, nr)
               + (up_cells - base).astype(np.uint16).tobytes() + (rm_cells - base).astype(np.uint16).tobytes()
               + up_cols.astype(np.uint8).tobytes())
    return struct.pack("<II", 5, nu + nr) + zlib.compress(payload, 1)


def empty_overlays() -> list[bytes]:
    """Types 2 and 3 (floor, gradient): UI overlays a receiver must ignore. A map wipe sends them empty."""
    z = zlib.compress(b"", 1)
    return [struct.pack("<II", 2, 0) + z, struct.pack("<II", 3, 0) + z]


_OFF = 1 << 20


def cell_keys(cells: np.ndarray) -> np.ndarray:
    c = cells.astype(np.int64) + _OFF
    return (c[:, 0] << 42) | (c[:, 1] << 21) | c[:, 2]


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


# ── the scene, as boxes in the room frame ───────────────────────────────────────────────

@dataclass
class Box:
    id: str
    x: float
    y: float
    z: float                 # centre
    yaw: float               # degrees, the axis of ex
    ex: float
    ey: float
    ez: float
    rgb: tuple[int, int, int]
    kind: str = "object"     # object | furniture | occluder
    cls: str = ""
    zone: str = ""

    def local(self, P: np.ndarray) -> np.ndarray:
        """Room points -> this box's own axes, centred."""
        c, s = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        d = P - (self.x, self.y, self.z)
        return np.stack([c * d[:, 0] + s * d[:, 1], -s * d[:, 0] + c * d[:, 1], d[:, 2]], axis=1)

    def surface(self, spacing: float) -> np.ndarray:
        """Points on the six faces of the ORIENTED box, in the room frame. (scene_gen.scene_cloud uses
        the axis-aligned bounds of the rotated box, which erases the yaw that bb_source must recover.)"""
        h = (self.ex / 2, self.ey / 2, self.ez / 2)
        ax = [np.linspace(-h[a], h[a], max(2, int(math.ceil(2 * h[a] / spacing)) + 1)) for a in range(3)]
        faces = []
        for a in range(3):
            for v in (-h[a], h[a]):
                g = np.meshgrid(*[ax[b] if b != a else np.array([v]) for b in range(3)], indexing="ij")
                faces.append(np.stack([m.ravel() for m in g], axis=1))
        L = np.concatenate(faces)
        c, s = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        return np.stack([c * L[:, 0] - s * L[:, 1] + self.x, s * L[:, 0] + c * L[:, 1] + self.y, L[:, 2] + self.z], 1)

    def blocks(self, eye: np.ndarray, P: np.ndarray, shrink: float = 0.01) -> np.ndarray:
        """Which segments eye -> P pass through this box (slab test in the box's axes, vectorised)."""
        o = self.local(eye[None, :])[0]
        d = self.local(P) - o
        h = np.maximum(np.array([self.ex, self.ey, self.ez]) / 2 - shrink, 1e-4)
        with np.errstate(divide="ignore", invalid="ignore"):
            t1, t2 = (-h - o) / d, (h - o) / d
        lo, hi = np.minimum(t1, t2), np.maximum(t1, t2)
        par = np.abs(d) < 1e-12                       # parallel to a slab: inside it, or never
        inside = np.abs(o) <= h
        lo = np.where(par, np.where(inside, -np.inf, np.inf), lo)
        hi = np.where(par, np.where(inside, np.inf, -np.inf), hi)
        t0, t1m = lo.max(axis=1), hi.min(axis=1)
        return (t0 <= t1m) & (t1m > 1e-6) & (t0 < 1.0 - 1e-6)


def hex_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


FLOOR_RGB, TOP_RGB, LEG_RGB, OCC_RGB = (138, 141, 145), (176, 141, 91), (110, 110, 115), (70, 60, 50)


@dataclass
class Job:
    kind: str
    running: bool = True
    error: str | None = None
    result: str | None = None
    progress: dict | None = None
    goals: int | None = None
    target_world: dict | None = None
    target_area: dict | None = None
    id: int = 0

    def json(self) -> dict:
        d = {"kind": self.kind, "running": self.running, "error": self.error, "result": self.result,
             "progress": self.progress, "id": self.id}
        if self.kind == "patrol":
            d.update(goals=self.goals or 0, target_world=self.target_world, target_area=self.target_area)
        return d


class NavError(Exception):
    pass


class Sim:
    def __init__(self, args):
        self.args = args
        self.rng = random.Random(args.seed)
        self.T = args.T
        self.t0 = time.time()
        self.map_gen = 0
        self.ready_at = self.t0 + SLAM_READY_S
        self.status = "idle"
        self.job: Job | None = None
        self.job_task: asyncio.Task | None = None
        self._job_id = 0
        self.fail: dict[str, dict] = {}          # kind -> {"once": bool, "hold_s": float}
        self.held: dict[str, Box] = {}           # objects the arm stand-in is holding
        self.heavy_clients: list[dict] = []
        self.ws_clients: list[asyncio.Queue] = []
        self.stream_clients = 0
        self.vox_lock = asyncio.Lock()      # the voxel tick, /sim/truth and the /sim/* writers all touch the map
        self.stats = {"resyncs": 0, "fell_behind": 0, "deltas": 0, "keyframes_built": 0}
        self.area: dict | None = None
        self.route: list[tuple[float, float, float | None]] = []   # BB world waypoints
        self.wp = -1
        self.goal: tuple[float, float] | None = None
        self.final_heading: float | None = None
        self.stop_radius = 0.1
        self.route_done: asyncio.Event | None = None
        self.route_error: str | None = None
        self.time_scale = 1.0
        self.link_bps = float(args.link_bps or 0)
        self.load_scene(args.scene)
        home = list(self.scene.room.get("home") or [-0.4, 0.0, 0]) + [0, 0, 0]
        self.px, self.py, _ = frames.room_to_bb((home[0], home[1], 0.0), self.T)
        self.ph = frames.heading_room_to_bb_yaw(float(home[2]), self.T)
        self.rebuild_truth()
        # SIM the area is "pre-mapped" (PLAN §3): the voxel map starts complete. After that, only what
        # the camera can see is updated, so a changed scene is stale until the robot looks at it.
        self.m_keys, self.m_cells, self.m_cols = self.t_keys.copy(), self.t_cells.copy(), self.t_cols.copy()
        if args.empty_map:
            self.m_keys, self.m_cells, self.m_cols = self.t_keys[:0], self.t_cells[:0], self.t_cols[:0]
        self.kf_cache: list[bytes] | None = None
        self._remap_at: float | None = None

    # ── scene -> truth ──────────────────────────────────────────────────────────────────
    def load_scene(self, name: str) -> None:
        self.scene = scene_gen.load_scene(name)
        self.scene_name = name
        self.boxes: dict[str, Box] = {}
        for zname, z in self.scene.room.get("zones", {}).items():
            (x0, y0, _), (x1, y1, _) = z["min"], z["max"]
            s, o = z["surface"], z.get("overhang", 0.10)
            self.boxes[f"zone:{zname}:top"] = Box(f"zone:{zname}:top", (x0 + x1) / 2, (y0 + y1) / 2, s - 0.01, 0,
                                                  x1 - x0, y1 - y0, 0.02, TOP_RGB, "furniture", zone=zname)
            if x1 - x0 > 2 * o and y1 - y0 > 2 * o:
                self.boxes[f"zone:{zname}:leg"] = Box(f"zone:{zname}:leg", (x0 + x1) / 2, (y0 + y1) / 2,
                                                      (s - 0.02 + 0.02) / 2, 0, x1 - x0 - 2 * o, y1 - y0 - 2 * o,
                                                      s - 0.04, LEG_RGB, "furniture", zone=zname)
        for t in self.scene.objects.values():
            self.boxes[t.id] = Box(t.id, t.x, t.y, t.z, t.yaw, t.ex, t.ey, t.ez, hex_rgb(t.color), "object",
                                   cls=t.cls, zone=t.zone)
        for oid in self.held:
            self.boxes.pop(oid, None)

    def floor_bounds(self) -> tuple[float, float, float, float]:
        """Room-frame bounds of the floor the sim knows about: the furniture plus a margin."""
        m = self.args.floor_margin
        xs, ys = [-0.6, 0.6], [-0.6, 0.6]
        for b in self.boxes.values():
            if b.kind == "furniture":
                xs += [b.x - b.ex / 2, b.x + b.ex / 2]
                ys += [b.y - b.ey / 2, b.y + b.ey / 2]
        return min(xs) - m, max(xs) + m, min(ys) - m, max(ys) + m

    def rebuild_truth(self) -> None:
        """Every cell the depth camera could ever report, in BB cells, with its colour and owner."""
        sp = RES / 2                                   # denser than a cell, so a rotated surface has no holes
        pts, cols, own = [], [], []
        self.box_list = list(self.boxes.values())
        for bi, b in enumerate(self.box_list):
            P = b.surface(sp)
            pts.append(P); cols.append(np.tile(np.array(b.rgb, np.uint8), (len(P), 1))); own.append(np.full(len(P), bi))
        if not self.args.no_floor:
            x0, x1, y0, y1 = self.floor_bounds()
            gx, gy = np.meshgrid(np.arange(x0, x1, sp), np.arange(y0, y1, sp), indexing="ij")
            P = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], 1)
            pts.append(P); cols.append(np.tile(np.array(FLOOR_RGB, np.uint8), (len(P), 1))); own.append(np.full(len(P), -1))
        P, C, O = np.concatenate(pts), np.concatenate(cols), np.concatenate(own)
        keep = P[:, 2] + self.T.dz < Z_MAX
        P, C, O = P[keep], C[keep], O[keep]
        B = frames.room_to_bb_array(P, self.T)
        cells = np.round(B / RES).astype(np.int32)     # bbapps/nav: cell = round(coord / res)
        keys = cell_keys(cells)
        # one colour per cell: objects win over furniture, furniture over the floor (the highest owner index)
        order = np.lexsort((O, keys))
        keys, cells, C, O = keys[order], cells[order], C[order], O[order]
        last = np.r_[keys[1:] != keys[:-1], True]
        self.t_keys, self.t_cells, self.t_cols, self.t_own = keys[last], cells[last], C[last], O[last]
        self.build_plan_grid()

    # ── planning grid (BB world) ────────────────────────────────────────────────────────
    def build_plan_grid(self) -> None:
        z = self.t_cells[:, 2] * RES - self.T.dz
        body = (z > 0.05) & (z < 1.3)                  # whatever the robot's body would hit
        xy = self.t_cells[:, :2] * RES
        lo, hi = xy.min(axis=0) - 0.3, xy.max(axis=0) + 0.3
        self.pg_lo = lo
        nx, ny = (np.ceil((hi - lo) / PLAN_RES).astype(int) + 1)
        occ = np.zeros((nx, ny), bool)
        ij = np.floor((xy[body] - lo) / PLAN_RES).astype(int)
        occ[ij[:, 0], ij[:, 1]] = True
        self.pg_occ = occ
        r = int(math.ceil(ROBOT_RADIUS / PLAN_RES))
        infl = occ.copy()
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r:
                    sh = np.zeros_like(occ)
                    xs = slice(max(0, dx), nx + min(0, dx)); xs0 = slice(max(0, -dx), nx + min(0, -dx))
                    ys = slice(max(0, dy), ny + min(0, dy)); ys0 = slice(max(0, -dy), ny + min(0, -dy))
                    sh[xs, ys] = occ[xs0, ys0]
                    infl |= sh
        known = np.zeros((nx, ny), bool)               # floor the map actually covers
        fl = np.floor((xy[~body] - lo) / PLAN_RES).astype(int)
        known[fl[:, 0], fl[:, 1]] = True
        self.pg_free = known & ~infl if not self.args.no_floor else ~infl
        self.pg_free_idx = np.argwhere(self.pg_free)

    def pg_cell(self, x: float, y: float) -> tuple[int, int]:
        i, j = np.floor((np.array([x, y]) - self.pg_lo) / PLAN_RES).astype(int)
        return int(np.clip(i, 0, self.pg_free.shape[0] - 1)), int(np.clip(j, 0, self.pg_free.shape[1] - 1))

    def pg_xy(self, i: int, j: int) -> tuple[float, float]:
        return (float(self.pg_lo[0] + (i + 0.5) * PLAN_RES), float(self.pg_lo[1] + (j + 0.5) * PLAN_RES))

    def nearest_free(self, x: float, y: float) -> tuple[float, float]:
        i, j = self.pg_cell(x, y)
        if self.pg_free[i, j]:
            return x, y
        if not len(self.pg_free_idx):
            raise NavError("no mapped floor")
        d = ((self.pg_free_idx - (i, j)) ** 2).sum(axis=1)
        return self.pg_xy(*self.pg_free_idx[int(d.argmin())])

    def plan(self, x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
        """A* on the inflated grid, 8-connected; then a line-of-sight shortcut pass."""
        s, g = self.pg_cell(*self.nearest_free(x0, y0)), self.pg_cell(x1, y1)
        free = self.pg_free
        if s == g:
            return [(x1, y1)]
        openq, came, cost = [(0.0, s)], {s: None}, {s: 0.0}
        nbr = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0), (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]
        while openq:
            _, cur = heapq.heappop(openq)
            if cur == g:
                break
            for dx, dy, w in nbr:
                n = (cur[0] + dx, cur[1] + dy)
                if not (0 <= n[0] < free.shape[0] and 0 <= n[1] < free.shape[1]) or not free[n]:
                    continue
                c = cost[cur] + w
                if c < cost.get(n, 1e18):
                    cost[n] = c; came[n] = cur
                    heapq.heappush(openq, (c + math.hypot(g[0] - n[0], g[1] - n[1]), n))
        if g not in came:
            raise NavError("no path")
        cells = []
        n = g
        while n is not None:
            cells.append(n); n = came[n]
        cells.reverse()

        def clear(a, b):
            k = int(max(abs(b[0] - a[0]), abs(b[1] - a[1]))) + 1
            ii = np.round(np.linspace(a[0], b[0], k + 1)).astype(int); jj = np.round(np.linspace(a[1], b[1], k + 1)).astype(int)
            return bool(free[ii, jj].all())
        out, i = [cells[0]], 0
        while i < len(cells) - 1:
            j = len(cells) - 1
            while j > i + 1 and not clear(cells[i], cells[j]):
                j -= 1
            out.append(cells[j]); i = j
        pts = [self.pg_xy(*c) for c in out[1:]]
        pts[-1] = (x1, y1)
        return pts

    # ── what the camera sees ────────────────────────────────────────────────────────────
    def in_view_xy(self, xy: np.ndarray, see_range: float) -> np.ndarray:
        d = xy - (self.px, self.py)
        dist = np.hypot(d[:, 0], d[:, 1])
        fx, fy = frames.bb_forward(self.ph)
        with np.errstate(invalid="ignore", divide="ignore"):
            cosang = (d[:, 0] * fx + d[:, 1] * fy) / dist
        return (dist <= see_range) & ((cosang >= math.cos(SEE_CONE / 2)) | (dist < 1e-6))

    def visible(self, cells: np.ndarray, owners: np.ndarray) -> np.ndarray:
        """In range, in the cone, and no OTHER box between the camera and the cell. An object does not
        hide itself (SIM: the real robot sees every side over a patrol; one viewpoint would bias extents)."""
        B = cells * RES
        vis = self.in_view_xy(B[:, :2], self.args.see_range)
        if not vis.any():
            return vis
        idx = np.flatnonzero(vis)
        P = frames.bb_to_room_array(B[idx], self.T)
        ex, ey, _ = frames.bb_to_room((self.px, self.py, 0.0), self.T)
        eye = np.array([ex, ey, EYE_Z])
        blocked = np.zeros(len(idx), bool)
        for bi, b in enumerate(self.box_list):
            # a cell INSIDE a box is not hidden by it: something solid stands where the map says there is a
            # free-standing voxel, and the camera seeing that solid is what clears it
            h = np.array([b.ex, b.ey, b.ez]) / 2 + RES
            inside = (np.abs(b.local(P)) <= h).all(axis=1)
            blocked |= b.blocks(eye, P) & (owners[idx] != bi) & ~inside
        vis[idx[blocked]] = False
        return vis

    def diff(self) -> tuple[np.ndarray, np.ndarray]:
        """Where the served map differs from the truth AND the camera can see it now:
        (indices into the truth to add/recolour, indices into the served map to remove)."""
        add = ~np.isin(self.t_keys, self.m_keys, assume_unique=True)
        both_t = np.flatnonzero(~add)
        pos = np.searchsorted(self.m_keys, self.t_keys[both_t])
        recol = (self.m_cols[pos] != self.t_cols[both_t]).any(axis=1)
        up_idx = np.r_[np.flatnonzero(add), both_t[recol]]
        rm_idx = np.flatnonzero(~np.isin(self.m_keys, self.t_keys, assume_unique=True))
        if len(up_idx):
            up_idx = up_idx[self.visible(self.t_cells[up_idx], self.t_own[up_idx])]
        if len(rm_idx):
            rm_idx = rm_idx[self.visible(self.m_cells[rm_idx], np.full(len(rm_idx), -9))]
        return up_idx, rm_idx

    def vox_tick(self) -> bytes | None:
        """Bring the served map up to the truth, but only where the camera is looking."""
        if time.time() < self.ready_at:
            return None
        up_idx, rm_idx = self.diff()
        if not len(up_idx) and not len(rm_idx):
            return None
        pkt = pack_delta(self.t_cells[up_idx], self.t_cols[up_idx], self.m_cells[rm_idx])
        keep = ~np.isin(self.m_keys, self.t_keys[up_idx])   # a recoloured cell is replaced, not duplicated
        keep[rm_idx] = False
        keys = np.r_[self.m_keys[keep], self.t_keys[up_idx]]
        cells = np.vstack([self.m_cells[keep], self.t_cells[up_idx]]); cols = np.vstack([self.m_cols[keep], self.t_cols[up_idx]])
        o = np.argsort(keys, kind="stable")
        self.m_keys, self.m_cells, self.m_cols = keys[o], cells[o], cols[o]
        self.kf_cache = None
        self.stats["deltas"] += 1
        return pkt

    def broadcast_heavy(self, pkt: bytes | None) -> None:
        if pkt is None:
            return
        for c in self.heavy_clients:
            try:
                c["q"].put_nowait(pkt)
            except asyncio.QueueFull:                  # fell behind: drop the queue, send a fresh full copy
                if not c["resync"]:
                    self.stats["fell_behind"] += 1
                c["resync"] = True
                while not c["q"].empty():
                    c["q"].get_nowait()

    def keyframe(self) -> list[bytes]:
        if self.kf_cache is None:
            self.kf_cache = pack_keyframe(self.m_cells, self.m_cols)
            self.stats["keyframes_built"] += 1
        return self.kf_cache

    def reset_map(self, reanchor: bool = False) -> None:
        self.map_gen += 1
        if reanchor:                                   # a re-anchor: BB's world frame itself moves
            old = self.T
            rx, ry, _ = frames.bb_to_room((self.px, self.py, 0.0), old)
            rh = frames.bb_yaw_to_heading_room(self.ph, old)
            self.T = SE2(wrap(old.theta + self.rng.uniform(0.3, 1.2)), old.tx + self.rng.uniform(-1, 1),
                         old.ty + self.rng.uniform(-1, 1), old.dz)
            self.px, self.py, _ = frames.room_to_bb((rx, ry, 0.0), self.T)
            self.ph = frames.heading_room_to_bb_yaw(rh, self.T)
            self.rebuild_truth()
        self.m_keys, self.m_cells, self.m_cols = self.t_keys[:0], self.t_cells[:0], self.t_cols[:0]
        self.kf_cache = None
        self.area = None
        self.ready_at = time.time() + SLAM_READY_S
        for c in self.heavy_clients:                     # each connection sends its own empty full copy
            c["resync"] = c["wiped"] = True               # (queued, it would be dropped with the stale changes)
        self._remap_at = time.time() + SLAM_READY_S      # checked by physics_loop: /sim/* runs in a worker thread,
                                                         # and there is no running loop to call_later on there

    def _remap(self) -> None:
        """SIM the rebuild after a reset: the pre-mapped area comes back in one step."""
        self.m_keys, self.m_cells, self.m_cols = self.t_keys.copy(), self.t_cells.copy(), self.t_cols.copy()
        self.kf_cache = None
        for c in self.heavy_clients:
            c["resync"] = True

    # ── state ───────────────────────────────────────────────────────────────────────────
    @property
    def ready(self) -> bool:
        return time.time() >= self.ready_at

    def state_msg(self) -> dict:
        m = {"t": "state", "ready": self.ready, "rx": round(self.px, 3), "ry": round(self.py, 3), "rh": round(self.ph, 4),
             "status": self.status, "wp": self.wp, "running": bool(self.route), "loop": False, "global": True,
             "manual": self.status == "manual", "map_gen": self.map_gen,
             "waypoints": [[round(x, 3), round(y, 3), None if h is None else round(h, 4)] for x, y, h in self.route]}
        if self.route and self.goal is not None:
            m["gx"], m["gy"] = round(self.goal[0], 3), round(self.goal[1], 3)
            pts = [(self.px, self.py)] + [(x, y) for x, y, _ in self.route[max(self.wp, 0):]]
            m["path"] = [[round(p[0], 3) for p in pts], [round(p[1], 3) for p in pts]]
        return m

    # ── motion ──────────────────────────────────────────────────────────────────────────
    def step(self, dt: float) -> None:
        if self.area is not None and self.ready:
            self.mark_seen()
        if not self.route or self.status in ("waiting_for_drive", "manual"):
            return
        dt *= self.time_scale
        x, y, _ = self.route[self.wp]
        last = self.wp == len(self.route) - 1
        dx, dy = x - self.px, y - self.py
        dist = math.hypot(dx, dy)
        if dist <= (self.stop_radius if last else 0.06):
            if not last:
                self.wp += 1
                return
            if self.final_heading is not None:
                err = wrap(self.final_heading - self.ph)
                if abs(err) > 0.02:
                    self.ph = wrap(self.ph + math.copysign(min(abs(err), TURN_RATE * dt), err))
                    return
            self._finish(None)
            return
        err = wrap(math.atan2(-dx, dy) - self.ph)      # forward = (-sin h, cos h)
        if abs(err) > 0.12:
            self.ph = wrap(self.ph + math.copysign(min(abs(err), TURN_RATE * dt), err))
            return
        self.ph = wrap(self.ph + err * min(1.0, 4 * dt))
        fx, fy = frames.bb_forward(self.ph)
        adv = min(self.args.speed * dt, dist)
        self.px += fx * adv; self.py += fy * adv

    def _finish(self, error: str | None) -> None:
        self.route, self.wp, self.goal = [], -1, None
        self.route_error = error
        self.status = "reached" if error is None else f"failed: {error}"
        if self.route_done is not None:
            self.route_done.set()

    async def drive(self, x: float, y: float, heading: float | None, timeout: float) -> tuple[float, float]:
        """Plan and drive to (x, y) in BB world. An unreachable point becomes the nearest free floor, and
        that still ends `reached` (as the robot's docs say): the caller must read the actual pose."""
        if not self.ready:
            raise NavError("SLAM not ready")
        kind = next((k for k in ("drive_busy", "manual") if k in self.fail), None)
        if kind:
            f = self.fail[kind]
            if f.get("once", True):
                self.fail.pop(kind)
            self.status = "waiting_for_drive" if kind == "drive_busy" else "manual"
            await asyncio.sleep(float(f.get("hold_s", 3.0)))
        if "nav" in self.fail or self.rng.random() < self.args.fail_nav:
            if self.fail.get("nav", {}).get("once", True):
                self.fail.pop("nav", None)
            self.status = "failed: no path"
            raise NavError("no path")
        tx, ty = self.nearest_free(x, y)
        try:
            pts = self.plan(self.px, self.py, tx, ty)
        except NavError as e:
            self.status = f"failed: {e}"
            raise
        self.route = [(px, py, None) for px, py in pts[:-1]] + [(pts[-1][0], pts[-1][1], heading)]
        self.wp, self.goal, self.final_heading = 0, (tx, ty), heading
        self.stop_radius = self.rng.uniform(0.03, 0.20)  # "arrived" is anywhere within 0.25 m
        self.route_done, self.route_error = asyncio.Event(), None
        self.status = "navigating"
        try:
            await asyncio.wait_for(self.route_done.wait(), timeout / max(self.time_scale, 1e-6))
        except asyncio.TimeoutError:
            self._finish("timeout")
            raise TimeoutError(f"navigate exceeded {timeout:g}s") from None
        if self.route_error:
            raise NavError(self.route_error)
        return tx, ty

    # ── jobs: one at a time ─────────────────────────────────────────────────────────────
    async def start_job(self, kind: str, coro_fn) -> Job:
        await self.cancel_job()
        self._job_id += 1
        job = Job(kind, id=self._job_id)
        self.job = job

        async def run():
            try:
                job.result = await coro_fn(job)
            except asyncio.CancelledError:
                job.error = "cancelled"
                self.route, self.wp, self.goal = [], -1, None
                self.status = "idle"
            except NavError as e:
                job.error = f"NavError: {e}"
            except TimeoutError as e:
                job.error = f"TimeoutError: {e}"
            except Exception as e:                      # a bug in the sim must not look like a success
                job.error = f"NavError: sim {type(e).__name__}: {e}"
            finally:
                job.running = False
                self.time_scale = 1.0
        self.job_task = asyncio.create_task(run())
        return job

    async def cancel_job(self) -> None:
        t = self.job_task
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self.job_task = None

    def to_world(self, x: float, y: float, heading: float | None, frame: str):
        if frame == "world":
            return x, y, heading
        if frame == "robot":
            ax, ay, ah = self.px, self.py, self.ph
        elif frame == "area":
            if self.area is None:
                raise ValueError("no area: POST /map/rectangle first")
            a = self.area["anchor_world"]; ax, ay, ah = a["x"], a["y"], a["yaw"]
        else:
            raise ValueError(f"frame must be world, area or robot, not {frame!r}")
        wx, wy = frames.robot_rel_to_bb(x, y, ax, ay, ah)
        return wx, wy, None if heading is None else wrap(heading + ah)

    # ── the rectangle, its 2D grid and freshness ────────────────────────────────────────
    def define_area(self, b: dict) -> None:
        bounds = {k: float(b[k]) for k in ("xmin", "xmax", "ymin", "ymax")}
        if not (bounds["xmax"] > bounds["xmin"] and bounds["ymax"] > bounds["ymin"]):
            raise ValueError("bounds: xmax > xmin and ymax > ymin")
        block, see = float(b.get("block", 0.5)), float(b.get("see_range", 2.0))
        nx = int(round((bounds["xmax"] - bounds["xmin"]) / GRID_RES)); ny = int(round((bounds["ymax"] - bounds["ymin"]) / GRID_RES))
        bx = int(math.ceil((bounds["xmax"] - bounds["xmin"]) / block - 1e-9)); by = int(math.ceil((bounds["ymax"] - bounds["ymin"]) / block - 1e-9))
        a = {"anchor_world": {"x": round(self.px, 4), "y": round(self.py, 4), "yaw": round(self.ph, 5)}, "bounds": bounds,
             "block": block, "see_range": see, "nx": nx, "ny": ny, "bnx": bx, "bny": by}
        ah = a["anchor_world"]
        X = bounds["xmin"] + (np.arange(nx) + 0.5) * GRID_RES; Y = bounds["ymin"] + (np.arange(ny) + 0.5) * GRID_RES
        gx, gy = np.meshgrid(X, Y)                       # rows = y (row 0 is ymin), columns = x
        c, s = math.cos(ah["yaw"]), math.sin(ah["yaw"])
        wx, wy = ah["x"] + gx * c - gy * s, ah["y"] + gx * s + gy * c
        ij = np.floor((np.stack([wx, wy], -1) - self.pg_lo) / PLAN_RES).astype(int)
        inside = (ij[..., 0] >= 0) & (ij[..., 0] < self.pg_occ.shape[0]) & (ij[..., 1] >= 0) & (ij[..., 1] < self.pg_occ.shape[1])
        ic = np.clip(ij, 0, np.array(self.pg_occ.shape) - 1)
        a["truth"] = np.where(inside & self.pg_occ[ic[..., 0], ic[..., 1]], 2, 1).astype(np.uint8)
        a["cell_block"] = (np.minimum((gx - bounds["xmin"]) // block, bx - 1).astype(int), np.minimum((gy - bounds["ymin"]) // block, by - 1).astype(int))
        BX = bounds["xmin"] + (np.arange(bx) + 0.5) * block; BY = bounds["ymin"] + (np.arange(by) + 0.5) * block
        bgx, bgy = np.meshgrid(np.minimum(BX, bounds["xmax"]), np.minimum(BY, bounds["ymax"]))
        a["block_world"] = np.stack([ah["x"] + bgx * c - bgy * s, ah["y"] + bgx * s + bgy * c], -1)
        a["block_area"] = np.stack([bgx, bgy], -1)
        a["seen_t"] = np.full((by, bx), -1.0)             # a new rectangle resets patrol history
        a["mapped"] = np.zeros((by, bx), bool)
        a["solid"] = np.array([[(a["truth"][(a["cell_block"][0] == i) & (a["cell_block"][1] == j)] == 2).all()
                                for i in range(bx)] for j in range(by)])
        a["skip_until"] = np.zeros((by, bx))
        self.area = a
        self.mark_seen()

    def mark_seen(self) -> None:
        a = self.area
        bw = a["block_world"].reshape(-1, 2)
        seen = self.in_view_xy(bw, a["see_range"]).reshape(a["seen_t"].shape)   # walls are NOT taken into account
        a["seen_t"][seen] = time.time()
        a["mapped"] |= seen

    def map_json(self) -> dict:
        a = self.area
        if a is None:
            return {"area": None, "grid": None, "freshness": None, "t": round(time.time(), 3)}
        cells = np.where(a["mapped"][a["cell_block"][1], a["cell_block"][0]], a["truth"], 0).astype(np.uint8)
        now = time.time()
        age = np.where(a["seen_t"] < 0, -1.0, np.round(now - a["seen_t"], 1))
        return {"area": {"anchor_world": a["anchor_world"], "bounds": a["bounds"]},
                "grid": {"resolution_m": GRID_RES, "nx": a["nx"], "ny": a["ny"], "cells": base64.b64encode(cells.tobytes()).decode()},
                "freshness": {"block_m": a["block"], "nx": a["bnx"], "ny": a["bny"], "age_s": age.tolist()},
                "t": round(now, 3)}

    async def job_rectangle(self, job: Job, body: dict) -> str | None:
        self.define_area(body)
        if not body.get("sweep", True):
            return "defined"
        a, b = self.area, self.area["bounds"]
        ah = a["anchor_world"]
        sp = float(body.get("lane_spacing", 0.6))
        m = 0.25
        ys = list(np.arange(b["ymin"] + m, b["ymax"] - m + 1e-6, sp)) or [(b["ymin"] + b["ymax"]) / 2]
        wps = []
        for k, Y in enumerate(ys):                        # lanes run left-right, spaced front to back
            xs = (b["xmin"] + m, b["xmax"] - m) if k % 2 == 0 else (b["xmax"] - m, b["xmin"] + m)
            wps += [(xs[0], float(Y)), (xs[1], float(Y))]
        if self.args.fast:
            self.time_scale = 20.0
        deadline = time.time() + float(body.get("timeout", 600)) / self.time_scale
        for i, (X, Y) in enumerate(wps):
            job.progress = {"waypoint": i, "waypoints": len(wps), "lane": i // 2, "lanes": len(ys)}
            wx, wy = frames.robot_rel_to_bb(X, Y, ah["x"], ah["y"], ah["yaw"])
            left = (deadline - time.time()) * self.time_scale
            if left <= 0:
                raise TimeoutError("sweep ran out of time")
            await self.drive(wx, wy, None, left)
        job.progress = {"waypoint": len(wps), "waypoints": len(wps), "lane": len(ys), "lanes": len(ys)}
        self.status = "idle"
        return "swept"

    async def job_navigate(self, job: Job, body: dict) -> str:
        x, y, h = self.to_world(float(body["x"]), float(body["y"]), body.get("heading"), body.get("frame", "world"))
        await self.drive(x, y, None if h is None else float(h), float(body.get("timeout", 120)))
        return self.status                                # "reached", even at the NEAREST free floor

    async def job_patrol(self, job: Job, body: dict) -> None:
        if self.area is None:
            raise NavError("no area: POST /map/rectangle first")
        job.goals = 0
        leg = float(body.get("goal_timeout", 90))
        while True:
            a, now = self.area, time.time()
            age = np.where(a["seen_t"] < 0, np.inf, now - a["seen_t"])
            age[a["solid"]] = -np.inf                     # blocks that are entirely obstacle are skipped
            age[a["skip_until"] > now] = -1e9             # "couldn't bring it into view": back of the queue
            best = age.max()
            if not np.isfinite(best) and best < 0:
                await asyncio.sleep(0.5); continue
            cand = np.argwhere(age == best)
            d = [math.hypot(*(a["block_world"][j, i] - (self.px, self.py))) for j, i in cand]
            j, i = cand[int(np.argmin(d))]                # the stalest; nearest on a tie
            bx, by = a["block_world"][j, i]
            job.target_world = {"x": round(float(bx), 3), "y": round(float(by), 3)}
            job.target_area = {"x": round(float(a["block_area"][j, i][0]), 3), "y": round(float(a["block_area"][j, i][1]), 3)}
            try:
                sx, sy = self.nearest_free(float(bx), float(by))
                face = math.atan2(-(bx - sx), by - sy) if math.hypot(bx - sx, by - sy) > 0.05 else None
                await self.drive(sx, sy, face, leg)       # stand on free floor, facing the block
            except (NavError, TimeoutError):
                pass
            if a is self.area and a["seen_t"][j, i] < now:  # couldn't bring it into view
                a["skip_until"][j, i] = time.time() + 30
            job.goals += 1
            await asyncio.sleep(0.05)

    # ── /sim/* ──────────────────────────────────────────────────────────────────────────
    def sim_truth(self) -> dict:
        rx, ry, _ = frames.bb_to_room((self.px, self.py, 0.0), self.T)
        return {"scene": self.scene_name, "map_gen": self.map_gen, "ready": self.ready,
                "T_bb_from_room": {"theta": self.T.theta, "tx": self.T.tx, "ty": self.T.ty, "dz": self.T.dz},
                "robot_bb": {"x": self.px, "y": self.py, "yaw": self.ph},
                "robot_room": {"x": rx, "y": ry, "yaw_deg": frames.bb_yaw_to_heading_room(self.ph, self.T)},
                "objects": {b.id: {"x": b.x, "y": b.y, "z": b.z, "yaw": b.yaw, "extents": [b.ex, b.ey, b.ez],
                                   "color": "#%02x%02x%02x" % b.rgb, "kind": b.kind, "class": b.cls, "zone": b.zone}
                            for b in self.boxes.values() if b.kind != "furniture"},
                "held": sorted(self.held), "cells_truth": int(len(self.t_keys)), "cells_served": int(len(self.m_keys)),
                "stale_cells": int((~np.isin(self.t_keys, self.m_keys)).sum() + (~np.isin(self.m_keys, self.t_keys)).sum()),
                "stale_visible": int(sum(len(i) for i in self.diff())) if self.ready else -1,
                "heavy_clients": len(self.heavy_clients), "stats": dict(self.stats), "fail": self.fail}

    def sim(self, path: str, b: dict) -> dict:
        if path == "/sim/scene":
            self.held.clear()                              # a new ground truth: nothing is in the gripper
            self.load_scene(b["name"])
        elif path == "/sim/move":
            o = self._obj(b["object_id"])
            self.boxes[o.id] = replace(o, x=float(b.get("x", o.x)), y=float(b.get("y", o.y)), z=float(b.get("z", o.z)),
                                       yaw=float(b.get("yaw", o.yaw)))
        elif path == "/sim/occlude":
            oid = f"occluder:{b['object_id']}"
            if b.get("clear"):
                self.boxes.pop(oid, None)
            else:
                o = self._obj(b["object_id"])
                by = b.get("by") if isinstance(b.get("by"), dict) else {}
                ex_, ey_, ez_ = by.get("extents", [0.30, 0.04, 0.30])
                rx, ry, _ = frames.bb_to_room((self.px, self.py, 0.0), self.T)
                dx, dy = o.x - rx, o.y - ry
                d = math.hypot(dx, dy) or 1.0
                k = max(0.0, d - max(o.ex, o.ey) / 2 - 0.12) / d     # just in front of it, on the camera's side
                self.boxes[oid] = Box(oid, rx + dx * k, ry + dy * k, o.z - o.ez / 2 + ez_ / 2,
                                      math.degrees(math.atan2(dy, dx)) + 90.0, ex_, ey_, ez_,
                                      hex_rgb(by["color"]) if "color" in by else OCC_RGB, "occluder", cls=str(b.get("by", "box")))
        elif path == "/sim/reset_map":
            self.reset_map(bool(b.get("reanchor", False)))
            return {"ok": True, "map_gen": self.map_gen}
        elif path == "/sim/fail":
            kind = b["kind"]
            if kind not in ("nav", "drive_busy", "manual"):
                raise ValueError("kind: nav | drive_busy | manual")
            if b.get("clear"):
                self.fail.pop(kind, None)
            else:
                self.fail[kind] = {"once": bool(b.get("once", True)), "hold_s": float(b.get("hold_s", 3.0))}
            return {"ok": True, "fail": self.fail}
        elif path == "/sim/arm":
            op = b.get("op") or ("pick" if "pick" in b else "place")
            oid = b.get("object_id") or b.get(op)
            if op == "pick":
                self.held[oid] = self._obj(oid)
                self.boxes.pop(oid)
            elif op == "place":
                o = self.held.pop(oid, None) or self._obj(oid)
                x, y, z, yaw = (list(b["pose"]) + [o.yaw])[:4]
                self.boxes[oid] = replace(o, x=float(x), y=float(y), z=float(z), yaw=float(yaw))
            else:
                raise ValueError("op: pick | place")
        elif path == "/sim/link":                           # SIM {"bytes_per_s": 50000} a slow /heavy link; null = loopback
            self.link_bps = float(b.get("bytes_per_s") or 0)
            return {"ok": True, "bytes_per_s": self.link_bps or None}
        elif path == "/sim/teleport":                       # SIM put the robot somewhere (room frame), for tests
            self.px, self.py, _ = frames.room_to_bb((float(b["x"]), float(b["y"]), 0.0), self.T)
            if "yaw" in b:
                self.ph = frames.heading_room_to_bb_yaw(float(b["yaw"]), self.T)
            return {"ok": True}
        else:
            raise KeyError(path)
        self.rebuild_truth()
        return {"ok": True}

    def _obj(self, oid: str) -> Box:
        if oid not in self.boxes or self.boxes[oid].kind == "furniture":
            raise ValueError(f"no object {oid!r}")
        return self.boxes[oid]


# ── :ws-port  /heavy and /ws ────────────────────────────────────────────────────────────

async def heavy_handler(sim: Sim, conn) -> None:
    client = {"q": asyncio.Queue(maxsize=QMAX), "resync": True, "wiped": False}
    sim.heavy_clients.append(client)

    async def send(pkt: bytes) -> None:
        await conn.send(pkt)
        sent = 0.0                                       # SIM /sim/link: a slow link. Loopback never backs up by
        while sim.link_bps and sent < len(pkt):          # itself (the kernel buffers a whole map); a robot's wifi does
            await asyncio.sleep(0.02)
            sent += 0.02 * sim.link_bps

    try:
        while True:
            if client["wiped"]:                            # a map reset: an EMPTY full copy (first=1, count 0),
                client["wiped"] = False                    # then the empty UI overlays, as the robot does
                for pkt in pack_keyframe(sim.t_cells[:0], sim.t_cols[:0]) + empty_overlays():
                    await send(pkt)
            if client["resync"]:
                if not sim.ready:
                    await asyncio.sleep(0.05); continue    # streams when the SLAM pose is live
                while not client["q"].empty():             # stale changes must not land on the fresh copy
                    client["q"].get_nowait()
                client["resync"] = False
                sim.stats["resyncs"] += 1
                for pkt in sim.keyframe():
                    await send(pkt)
                    if client["resync"]:
                        break
                continue
            try:
                pkt = await asyncio.wait_for(client["q"].get(), 0.25)
            except asyncio.TimeoutError:
                continue
            await send(pkt)
    finally:
        sim.heavy_clients.remove(client)


async def ws_handler(sim: Sim, conn) -> None:
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    sim.ws_clients.append(q)

    async def tx():
        await conn.send(json.dumps({"t": "params", "params": {"sim": True}}))
        while True:
            await conn.send(await q.get())

    async def rx():
        async for raw in conn:                             # the browser UI's commands; a server uses nav_api instead
            try:
                if json.loads(raw).get("type") == "wipe_map":
                    sim.reset_map()
            except (ValueError, AttributeError):
                pass
    try:
        done, pending = await asyncio.wait([asyncio.create_task(tx()), asyncio.create_task(rx())],
                                           return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    finally:
        sim.ws_clients.remove(q)


async def ws_router(sim: Sim, conn) -> None:
    path = conn.request.path.split("?")[0]
    try:
        if path == "/heavy":
            await heavy_handler(sim, conn)
        elif path == "/ws":
            await ws_handler(sim, conn)
        else:
            await conn.close(1008, "no such socket")
    except Exception:
        pass


# ── :api-port  HTTP + ws /stream, by hand (stdlib): POST bodies and a websocket on one port ──

def _resp(status: int, body: dict) -> bytes:
    text = {200: "OK", 202: "Accepted", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed",
            409: "Conflict", 503: "Service Unavailable"}[status]
    data = json.dumps(body).encode()
    return (f"HTTP/1.1 {status} {text}\r\ncontent-type: application/json\r\ncontent-length: {len(data)}\r\n"
            f"connection: close\r\n\r\n").encode() + data


def _ws_frame(text: str) -> bytes:
    data = text.encode()
    n = len(data)
    head = bytes([0x81, n]) if n < 126 else bytes([0x81, 126]) + struct.pack(">H", n) if n < 65536 \
        else bytes([0x81, 127]) + struct.pack(">Q", n)
    return head + data


async def stream_socket(sim: Sim, reader, writer, headers: dict, query: str) -> None:
    key = headers.get("sec-websocket-key", "")
    accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    writer.write((f"HTTP/1.1 101 Switching Protocols\r\nupgrade: websocket\r\nconnection: Upgrade\r\n"
                  f"sec-websocket-accept: {accept}\r\n\r\n").encode())
    period = 1.0
    for kv in query.split("&"):
        if kv.startswith("period="):
            try:
                period = max(0.05, float(kv[7:]))
            except ValueError:
                pass
    closed = asyncio.Event()

    async def rx():                                        # only close and ping matter
        try:
            while True:
                h = await reader.readexactly(2)
                op, n = h[0] & 0x0F, h[1] & 0x7F
                if n == 126:
                    n = struct.unpack(">H", await reader.readexactly(2))[0]
                elif n == 127:
                    n = struct.unpack(">Q", await reader.readexactly(8))[0]
                mask = await reader.readexactly(4) if h[1] & 0x80 else b"\0\0\0\0"
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(await reader.readexactly(n)))
                if op == 8:
                    writer.write(bytes([0x88, 0])); break
                if op == 9:
                    writer.write(bytes([0x8A, len(data)]) + data)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        closed.set()
    task = asyncio.create_task(rx())
    sim.stream_clients += 1
    try:
        while not closed.is_set():
            writer.write(_ws_frame(json.dumps({**sim.map_json(), "job": sim.job.json() if sim.job else None})))
            await writer.drain()
            try:
                await asyncio.wait_for(closed.wait(), period)
            except asyncio.TimeoutError:
                pass
    finally:
        sim.stream_clients -= 1
        task.cancel()


async def api_route(sim: Sim, method: str, path: str, body: dict) -> tuple[int, dict]:
    job = lambda: sim.job.json() if sim.job else None      # noqa: E731
    if method == "GET":
        if path == "/health":
            a = sim.area
            return 200, {"main_py": {"connected": True, "ready": sim.ready, "map_gen": sim.map_gen}, "job": job(),
                         "area": None if a is None else {"anchor_world": a["anchor_world"], "bounds": a["bounds"]}, "sim": True}
        if path == "/pose":
            if not sim.ready:
                return 503, {"detail": "SLAM pose not ready"}
            out = {"world": {"x": round(sim.px, 4), "y": round(sim.py, 4), "yaw": round(sim.ph, 5)}, "area": None,
                   "ready": True, "map_gen": sim.map_gen, "t": round(time.time(), 3)}
            if sim.area is not None:
                a = sim.area["anchor_world"]
                X, Y = frames.bb_to_robot_rel(sim.px, sim.py, a["x"], a["y"], a["yaw"])
                out["area"] = {"x": round(X, 4), "y": round(Y, 4), "yaw": round(wrap(sim.ph - a["yaw"]), 5)}
            return 200, out
        if path == "/map":
            return 200, sim.map_json()
        if path == "/sim/truth":
            async with sim.vox_lock:                      # it diffs the whole map: same reason
                return 200, await asyncio.to_thread(sim.sim_truth)
        return 404, {"detail": "not found"}
    if method != "POST":
        return 405, {"detail": "GET or POST"}
    try:
        if path == "/map/rectangle":
            for k in ("xmin", "xmax", "ymin", "ymax"):
                float(body[k])
            if not sim.ready:
                return 503, {"detail": "SLAM pose not ready"}
            return 202, {"job": (await sim.start_job("rectangle", lambda j: sim.job_rectangle(j, body))).json()}
        if path == "/navigate":
            float(body["x"]); float(body["y"])
            sim.to_world(0.0, 0.0, None, body.get("frame", "world"))
            return 202, {"job": (await sim.start_job("navigate", lambda j: sim.job_navigate(j, body))).json()}
        if path == "/patrol":
            if sim.area is None:
                return 409, {"detail": "no area: POST /map/rectangle first"}
            return 202, {"job": (await sim.start_job("patrol", lambda j: sim.job_patrol(j, body))).json()}
        if path == "/stop":
            await sim.cancel_job()
            sim.route, sim.wp, sim.goal, sim.status = [], -1, None, "idle"
            return 200, {"job": job()}
        if path.startswith("/sim/"):
            async with sim.vox_lock:                      # it rebuilds the truth the voxel tick reads
                return 200, await asyncio.to_thread(sim.sim, path, body)
    except KeyError as e:
        return (404, {"detail": "not found"}) if str(e).strip("'") == path else (400, {"detail": f"missing field {e}"})
    except (ValueError, TypeError) as e:
        return 400, {"detail": str(e)}
    return 404, {"detail": "not found"}


async def api_conn(sim: Sim, reader, writer) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        lines = head.decode("latin1").split("\r\n")
        method, target, _ = lines[0].split(" ", 2)
        headers = {k.strip().lower(): v.strip() for k, v in (ln.split(":", 1) for ln in lines[1:] if ":" in ln)}
        path, _, query = target.partition("?")
        if headers.get("upgrade", "").lower() == "websocket":
            if path != "/stream":
                writer.write(_resp(404, {"detail": "not found"}))
            else:
                await stream_socket(sim, reader, writer, headers, query)
            return
        n = int(headers.get("content-length") or 0)
        raw = await reader.readexactly(n) if n else b""
        try:
            body = json.loads(raw) if raw.strip() else {}
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            writer.write(_resp(400, {"detail": "body must be a JSON object"}))
            return
        status, out = await api_route(sim, method.upper(), path, body)
        writer.write(_resp(status, out))
        await writer.drain()
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, ValueError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


# ── loops ───────────────────────────────────────────────────────────────────────────────

async def physics_loop(sim: Sim) -> None:
    last, next_state = time.monotonic(), 0.0
    while True:
        await asyncio.sleep(0.02)
        now = time.monotonic()
        sim.step(min(now - last, 0.1)); last = now
        if sim._remap_at is not None and time.time() >= sim._remap_at:
            sim._remap_at = None
            sim._remap()                                  # the pre-mapped area comes back a second after a reset
        if now >= next_state:
            next_state = now + 1.0 / STATE_HZ
            msg = json.dumps(sim.state_msg())
            for q in sim.ws_clients:
                if q.full():
                    q.get_nowait()                          # latest state only
                q.put_nowait(msg)


async def voxel_loop(sim: Sim) -> None:
    while True:
        await asyncio.sleep(sim.args.vox_interval)
        async with sim.vox_lock:                          # numpy over ~100k cells: in a thread, or every HTTP
            pkt = await asyncio.to_thread(sim.vox_tick)   # request (and the client's registration) waits behind it
        sim.broadcast_heavy(pkt)


async def serve(args, started: asyncio.Event | None = None) -> None:
    from websockets.asyncio.server import serve as ws_serve
    sim = Sim(args)
    args.sim = sim
    async with ws_serve(lambda c: ws_router(sim, c), HOST, args.ws_port, max_size=None, compression=None):
        api = await asyncio.start_server(lambda r, w: api_conn(sim, r, w), HOST, args.api_port)
        print(f"bbsim  scene {args.scene}  {len(sim.t_keys):,} cells  ws://{HOST}:{args.ws_port}/heavy /ws  "
              f"http://{HOST}:{args.api_port}  T_bb<-room theta={math.degrees(sim.T.theta):.1f}deg "
              f"t=({sim.T.tx:+.2f}, {sim.T.ty:+.2f}) dz={sim.T.dz:+.2f}", flush=True)
        sim.ready_at = time.time() + SLAM_READY_S        # "SLAM" comes up ~1 s after the sockets do
        if started is not None:
            started.set()
        async with api:
            await asyncio.gather(physics_loop(sim), voxel_loop(sim))


def parse_T(s: str) -> SE2:
    v = [float(x) for x in s.split(",")]
    if len(v) not in (3, 4):
        raise argparse.ArgumentTypeError("--T theta_deg,tx,ty[,dz]")
    return SE2(math.radians(v[0]), v[1], v[2], v[3] if len(v) == 4 else 0.0)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="a fake Bracket Bot nav server on loopback")
    ap.add_argument("--scene", default="clean_bench")
    ap.add_argument("--ws-port", type=int, default=18010)
    ap.add_argument("--api-port", type=int, default=18020)
    ap.add_argument("--speed", type=float, default=0.14, help="m/s; the robot cruises at about 0.14")
    ap.add_argument("--fast", action="store_true", help="sweeps run 20x faster")
    ap.add_argument("--fail-nav", type=float, default=0.0, help="probability a drive fails with 'no path'")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--T", type=parse_T, default=parse_T("30,1.2,-0.8,0"), help="T_bb<-room: theta_deg,tx,ty[,dz]")
    ap.add_argument("--see-range", type=float, default=2.0)
    ap.add_argument("--floor-margin", type=float, default=1.2)
    ap.add_argument("--no-floor", action="store_true")
    ap.add_argument("--empty-map", action="store_true", help="start with nothing mapped; the map fills as the robot looks")
    ap.add_argument("--vox-interval", type=float, default=VOX_INTERVAL)
    ap.add_argument("--link-bps", type=float, default=0, help="emulate a slow /heavy link, bytes per second (0 = loopback speed)")
    return ap


def main(argv=None) -> None:
    args = parser().parse_args(argv)
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
