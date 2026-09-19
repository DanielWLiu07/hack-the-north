"""roomctl/frames.py against the GEOMETRY, not against its own formulas.

The trap: at BB yaw 0 the robot faces +y. A plan computed in the room frame is only right if the
robot, told `heading = h`, physically faces the direction the room meant. So every heading case is
checked by rotating the room's intended direction into BB with R(theta) and comparing it with the
direction BB's own convention says h faces: (-sin h, cos h).
"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roomctl import frames  # noqa: E402
from roomctl.frames import SE2  # noqa: E402

THETAS = [0.0, math.pi / 2, math.pi, -math.pi / 2, 0.7]
PHIS = [0.0, 90.0, 180.0, -90.0, 33.0]


def close(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_bb_yaw_zero_faces_plus_y_and_plus_x_is_right():
    assert close(frames.bb_forward(0.0), (0.0, 1.0))
    # robot-relative +Y is forward, +X is right (bbapps/nav)
    assert close(frames.robot_rel_to_bb(0, 1, 0, 0, 0.0), (0.0, 1.0))
    assert close(frames.robot_rel_to_bb(1, 0, 0, 0, 0.0), (1.0, 0.0))


@pytest.mark.parametrize("theta", THETAS)
@pytest.mark.parametrize("phi", PHIS)
def test_a_room_heading_faces_the_same_way_in_bb(theta, phi):
    T = SE2(theta, 0.3, -1.2, 0.05)
    h = frames.heading_room_to_bb_yaw(phi, T)
    want = (math.cos(theta + math.radians(phi)), math.sin(theta + math.radians(phi)))   # R(theta) (cos phi, sin phi)
    assert close(frames.bb_forward(h), want, 1e-12)
    back = frames.bb_yaw_to_heading_room(h, T)
    assert abs((back - phi + 180.0) % 360.0 - 180.0) < 1e-9          # the same heading, mod 360
    assert -180.0 < back <= 180.0


def test_identity_room_forward_is_bb_plus_x_so_yaw_is_minus_pi_over_2():
    """Room +X forward, with no rotation between frames, is BB +x: h = -pi/2, not 0."""
    assert abs(frames.heading_room_to_bb_yaw(0.0, SE2.identity()) - (-math.pi / 2)) < 1e-12


@pytest.mark.parametrize("theta", THETAS)
def test_points_round_trip_and_the_inverse_is_the_inverse(theta):
    T = SE2(theta, 1.5, -0.25, 0.7)
    rng = np.random.default_rng(0)
    for p in rng.uniform(-3, 3, (50, 3)):
        assert close(frames.bb_to_room(frames.room_to_bb(p, T), T), p)
        assert close(frames.room_to_bb(p, T.inverse()), frames.bb_to_room(p, T))
    P = rng.uniform(-3, 3, (1000, 3))
    Q = frames.room_to_bb_array(P, T)
    assert np.allclose(Q[7], frames.room_to_bb(P[7], T)) and np.allclose(frames.bb_to_room_array(Q, T), P)


def test_rotation_moves_points_the_right_way():
    """theta = +90 deg: room +X lands on BB +y, room +Y lands on BB -x; dz lifts z."""
    T = SE2(math.pi / 2, 0.0, 0.0, 0.1)
    assert close(frames.room_to_bb((1, 0, 0), T), (0.0, 1.0, 0.1))
    assert close(frames.room_to_bb((0, 1, 0), T), (-1.0, 0.0, 0.1))


@pytest.mark.parametrize("h", [0.0, 0.5, -2.0, math.pi])
def test_robot_relative_round_trip(h):
    for X, Y in [(0.3, 1.1), (-0.4, 0.0), (2.0, -1.5)]:
        x, y = frames.robot_rel_to_bb(X, Y, 1.0, -2.0, h)
        assert close(frames.bb_to_robot_rel(x, y, 1.0, -2.0, h), (X, Y))
    # +Y in the robot frame is always along the robot's forward vector
    fx, fy = frames.robot_rel_to_bb(0, 1, 0, 0, h)
    assert close((fx, fy), frames.bb_forward(h))


def test_base_pose_to_navigate_body():
    class Base:  # roomctl.executor.BasePose is (x, y, yaw degrees)
        x, y, yaw = 0.42, 0.18, 90.0
    T = SE2(math.pi / 2, 1.0, 2.0)
    body = frames.base_pose_to_navigate(Base, T)
    assert body["frame"] == "world"
    assert close((body["x"], body["y"]), (1.0 - 0.18, 2.0 + 0.42), 1e-4)
    # room heading 90 deg rotated by +90 -> facing BB -x -> h = pi/2
    assert close(frames.bb_forward(body["heading"]), (-1.0, 0.0), 1e-4)
