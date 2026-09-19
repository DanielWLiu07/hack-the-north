"""The one place poses cross between the room frame and Bracket Bot's world frame.

    room (ours, versioned; docs/20)   X forward from the anchor tag, Y left, Z up, floor z = 0, metres;
                                      headings in DEGREES from +X, counter-clockwise
    BB world (SLAM, bbapps/nav)       x, y on the floor, z up, metres; yaw h in RADIANS, CCW, and
                                      **at h = 0 the robot faces +y** (forward = (-sin h, cos h), +x on its right)

Both are Z-up, so the map between them is a planar rigid transform plus a height offset:

    p_bb = R(theta) . p_room + (tx, ty)        z_bb = z_room + dz

A heading measured from +x is phi_bb = h + pi/2 in BB's convention, so a room heading phi_room
(degrees) becomes h = theta + radians(phi_room) - pi/2. Get that line wrong and a correct plan
drives the robot facing the wrong way; tests/test_frames.py checks it against the geometry, not
against this formula.

T itself (T_bb<-room) is estimated elsewhere and changes whenever BB's map_gen changes
(plan/roommate/03 §2, §12). Nothing else in the codebase does sin/cos on these frames.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SE2:
    """T_bb<-room: p_bb = R(theta) p_room + (tx, ty); z_bb = z_room + dz."""
    theta: float        # radians
    tx: float
    ty: float
    dz: float = 0.0

    @classmethod
    def identity(cls) -> "SE2":
        return cls(0.0, 0.0, 0.0, 0.0)

    def inverse(self) -> "SE2":
        """T_room<-bb, as an SE2 of the same form."""
        c, s = math.cos(self.theta), math.sin(self.theta)
        return SE2(-self.theta, -(c * self.tx + s * self.ty), -(-s * self.tx + c * self.ty), -self.dz)

    def matrix(self) -> np.ndarray:
        """4x4 homogeneous room -> BB, for numpy over many points."""
        c, s = math.cos(self.theta), math.sin(self.theta)
        return np.array([[c, -s, 0.0, self.tx],
                         [s, c, 0.0, self.ty],
                         [0.0, 0.0, 1.0, self.dz],
                         [0.0, 0.0, 0.0, 1.0]])


def _wrap_rad(a: float) -> float:
    """(-pi, pi]"""
    a = math.fmod(a + math.pi, 2 * math.pi)
    return (a + 2 * math.pi if a <= 0 else a) - math.pi


def _wrap_deg(a: float) -> float:
    """(-180, 180]"""
    a = math.fmod(a + 180.0, 360.0)
    return (a + 360.0 if a <= 0 else a) - 180.0


# ── points ──────────────────────────────────────────────────────────────────────────────

def room_to_bb(p, T: SE2) -> tuple[float, float, float]:
    x, y, z = (float(v) for v in (tuple(p) + (0.0,))[:3])
    c, s = math.cos(T.theta), math.sin(T.theta)
    return (c * x - s * y + T.tx, s * x + c * y + T.ty, z + T.dz)


def bb_to_room(p, T: SE2) -> tuple[float, float, float]:
    x, y, z = (float(v) for v in (tuple(p) + (0.0,))[:3])
    c, s = math.cos(T.theta), math.sin(T.theta)
    dx, dy = x - T.tx, y - T.ty
    return (c * dx + s * dy, -s * dx + c * dy, z - T.dz)


def room_to_bb_array(points: np.ndarray, T: SE2) -> np.ndarray:
    """(N,3) room -> (N,3) BB, vectorised (the voxel mirror has ~57k cells)."""
    P = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    M = T.matrix()
    return P @ M[:3, :3].T + M[:3, 3]


def bb_to_room_array(points: np.ndarray, T: SE2) -> np.ndarray:
    """(N,3) BB -> (N,3) room, vectorised."""
    return room_to_bb_array(points, T.inverse())


# ── headings ────────────────────────────────────────────────────────────────────────────

def heading_room_to_bb_yaw(phi_room_deg: float, T: SE2) -> float:
    """A room heading (degrees from +X) -> BB yaw h (radians; h = 0 faces +y)."""
    return _wrap_rad(T.theta + math.radians(phi_room_deg) - math.pi / 2)


def bb_yaw_to_heading_room(h: float, T: SE2) -> float:
    """BB yaw (radians) -> a room heading, degrees in (-180, 180]."""
    return _wrap_deg(math.degrees(h + math.pi / 2 - T.theta))


def bb_forward(h: float) -> tuple[float, float]:
    """The unit vector the robot faces at BB yaw h (bbapps/nav docs)."""
    return (-math.sin(h), math.cos(h))


# ── BB's robot-relative frames (`area`, `robot`): +X right, +Y forward ─────────────────

def robot_rel_to_bb(X: float, Y: float, px: float, py: float, h: float) -> tuple[float, float]:
    """bbapps/nav: x = px + X cos h - Y sin h; y = py + X sin h + Y cos h."""
    c, s = math.cos(h), math.sin(h)
    return (px + X * c - Y * s, py + X * s + Y * c)


def bb_to_robot_rel(x: float, y: float, px: float, py: float, h: float) -> tuple[float, float]:
    c, s = math.cos(h), math.sin(h)
    dx, dy = x - px, y - py
    return (dx * c + dy * s, -dx * s + dy * c)


# ── what the nav API takes ──────────────────────────────────────────────────────────────

def base_pose_to_navigate(base, T: SE2) -> dict:
    """A room-frame base pose (x, y, yaw in degrees; e.g. roomctl.executor.BasePose) ->
    the body of POST :8020/navigate in BB's world frame."""
    x, y, _ = room_to_bb((base.x, base.y, 0.0), T)
    return {"x": round(x, 4), "y": round(y, 4), "heading": round(heading_room_to_bb_yaw(base.yaw, T), 5),
            "frame": "world"}
