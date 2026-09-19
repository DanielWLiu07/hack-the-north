"""robot/frames.py — NOT a second conversion. `roomctl/frames.py` is the one place poses cross
between the room frame and Bracket Bot's (tests/test_frames.py pins it against the geometry).
This re-exports it, and adds only what is not a conversion: WHERE the transform came from.

    Registration   an SE2 (T_bb<-room) plus its provenance — which BB map it was measured against
                   (`map_gen`: a remap invalidates it), the fit's residual, measured or simulated.
    from_env       ROBOT_REGISTRATION="tx,ty,theta_deg[,dz]". Unset -> None: NOT MEASURED. There is
                   no default on purpose: the identity would send the robot, confidently, to the
                   wrong place, and everything that would move it refuses on None instead.

No sin/cos lives here or in robot/adapter.py. The robot needs roomctl/frames.py shipped beside
robot/ (math + numpy only); without it this import fails loudly rather than falling back to a copy.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

from roomctl.frames import (SE2, base_pose_to_navigate, bb_forward, bb_to_room, bb_yaw_to_heading_room,  # noqa: F401
                            heading_room_to_bb_yaw, room_to_bb)

ROOM_FRAME = "canonical_world_z_up"


@dataclass(frozen=True)
class Registration:
    T: SE2
    map_gen: str = ""
    residual_m: float | None = None
    source: str = "measured"          # "measured" | "simulated"

    def document(self) -> dict:
        """GET :8765/registration — what perception must use too, instead of estimating its own."""
        return {"frame_from": ROOM_FRAME, "frame_to": "bracketbot_world(x, y floor, z up; yaw h rad CCW, h=0 faces +y)",
                "T_bb_room": {"theta_rad": self.T.theta, "tx": self.T.tx, "ty": self.T.ty, "dz": self.T.dz},
                "matrix": [[float(v) for v in row] for row in self.T.matrix()],
                "map_gen": self.map_gen, "residual_m": self.residual_m, "source": self.source,
                "simulated": self.source == "simulated"}


def from_env(env: dict | None = None) -> Registration | None:
    e = os.environ if env is None else env
    raw = e.get("ROBOT_REGISTRATION", "").strip()
    if not raw:
        return None
    parts = [float(v) for v in raw.split(",")]
    if len(parts) not in (3, 4) or not all(math.isfinite(v) for v in parts):
        raise ValueError(f"ROBOT_REGISTRATION={raw!r}: want tx,ty,theta_deg[,dz]")
    res = e.get("ROBOT_REGISTRATION_RESIDUAL_M", "").strip()
    return Registration(SE2(math.radians(parts[2]), parts[0], parts[1], parts[3] if len(parts) == 4 else 0.0),
                        e.get("ROBOT_REGISTRATION_MAP_GEN", "").strip(), float(res) if res else None)
